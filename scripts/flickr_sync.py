#!/usr/bin/env python3
"""Sync Flickr photo metadata (public and private) to a local SQLite database.

Usage (single-user):
    python scripts/flickr_sync.py          # incremental (since last sync)
    python scripts/flickr_sync.py --full   # fetch all photos

Usage (multi-user):
    python scripts/flickr_sync.py --nsid 12345@N00 --username jdoe
    python scripts/flickr_sync.py --nsid 12345@N00 --username jdoe --full --create

When ``--nsid`` / ``--username`` are provided the script resolves credentials
and the database path per-user; otherwise it falls back to the single-user
defaults (``~/.flickr_mcp/credentials.json`` and ``data/flickr.db``).
"""

import argparse
import asyncio
import html
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flickr_api
from db import DB_FILE, db_file as _db_file
API_URL = flickr_api.API_URL
HTTP_TIMEOUT = flickr_api.HTTP_TIMEOUT
PER_PAGE = 500

EXTRAS = "description,date_upload,date_taken,last_update,tags,views,count_faves,count_comments,url_o,url_l,url_m,url_z,path_alias,media,ispublic"

# Aliases re-exported for sync_*.py backward compatibility
load_env = flickr_api._load_env
load_credentials = flickr_api._load_credentials


def api_get(method, extra=None):
    """Thin wrapper: translate RuntimeError from flickr_api into sys.exit(1) for script use."""
    try:
        return flickr_api._api_get(method, extra)
    except RuntimeError as e:
        print(f"API error: {e}", file=sys.stderr)
        sys.exit(1)


# --- Database ---

_MIGRATIONS = [
    "ALTER TABLE photos ADD COLUMN favorites    INTEGER DEFAULT 0",
    "ALTER TABLE photos ADD COLUMN comments     INTEGER DEFAULT 0",
    "ALTER TABLE photos ADD COLUMN reviewed_at  INTEGER DEFAULT NULL",
    "ALTER TABLE photos ADD COLUMN is_public    INTEGER DEFAULT 1",
    "ALTER TABLE sync_log ADD COLUMN type       TEXT DEFAULT 'photos'",
    "ALTER TABLE groups ADD COLUMN description  TEXT",
    "ALTER TABLE groups ADD COLUMN keywords     TEXT",
    "ALTER TABLE sync_log ADD COLUMN duration_seconds INTEGER",
    """CREATE TABLE IF NOT EXISTS pending_group_adds (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id     TEXT NOT NULL,
        group_id     TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'waiting',
        error_msg    TEXT,
        retry_after  INTEGER,
        queued_at    INTEGER NOT NULL,
        completed_at INTEGER
    )""",
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "ALTER TABLE groups ADD COLUMN auto_keywords TEXT",
    """CREATE TABLE IF NOT EXISTS keeper_list (
        photo_id TEXT PRIMARY KEY,
        note     TEXT,
        added_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS never_follow (
        contact_id TEXT PRIMARY KEY,
        reason     TEXT,
        added_at   INTEGER
    )""",
    "ALTER TABLE photos ADD COLUMN url_medium TEXT",
    # Groups rework: drop the old manual/auto keyword fields — search now uses
    # the AI-generated summary + keywords produced by sync_group_summaries().
    "ALTER TABLE groups DROP COLUMN keywords",
    "ALTER TABLE groups DROP COLUMN auto_keywords",
    "ALTER TABLE groups ADD COLUMN needs_summary INTEGER DEFAULT 1",
    "ALTER TABLE groups ADD COLUMN summary_md TEXT",
    "ALTER TABLE groups ADD COLUMN is_milestone INTEGER DEFAULT 0",
    "ALTER TABLE groups ADD COLUMN fave_min INTEGER",
    "ALTER TABLE groups ADD COLUMN view_min INTEGER",
    "ALTER TABLE groups ADD COLUMN ai_keywords TEXT",
    "ALTER TABLE groups ADD COLUMN summary_generated_at INTEGER",
    # User-editable per-group note (replaces the old manual `keywords` field)
    # — incorporated into the prompt the next time the AI summary regenerates.
    "ALTER TABLE groups ADD COLUMN user_note TEXT",
    "ALTER TABLE groups ADD COLUMN open_subject INTEGER",
]

SCHEMA_VERSION = len(_MIGRATIONS)


def _apply_migrations(conn):
    """Run pending schema migrations using PRAGMA user_version as a cursor.

    Each migration has a 1-based index. Only migrations whose index exceeds the
    stored user_version are executed. The version is incremented after each
    migration so partial failures leave the DB in a consistent state.
    Existing databases with user_version=0 (pre-versioning) run all migrations;
    duplicate-column errors are silently skipped (columns already exist from the
    old try/except approach). Any other error propagates so the DB is not
    silently left in a partially-migrated state.
    """
    import sqlite3 as _sqlite3
    cur = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(_MIGRATIONS, 1):
        if i <= cur:
            continue
        try:
            conn.execute(sql)
            conn.commit()
        except _sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        conn.execute(f"PRAGMA user_version = {i}")
        conn.commit()


def init_db(conn):
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=5000;

        CREATE TABLE IF NOT EXISTS photos (
            id            TEXT PRIMARY KEY,
            title         TEXT,
            description   TEXT,
            date_taken    TEXT,
            date_uploaded INTEGER,
            last_updated  INTEGER,
            url_photopage TEXT,
            url_original  TEXT,
            tags          TEXT,
            views         INTEGER,
            favorites     INTEGER,
            comments      INTEGER,
            synced_at     INTEGER
        );

        CREATE TABLE IF NOT EXISTS groups (
            id         TEXT PRIMARY KEY,
            name       TEXT,
            members    INTEGER,
            pool_count INTEGER,
            synced_at  INTEGER
        );

        CREATE TABLE IF NOT EXISTS albums (
            id               TEXT PRIMARY KEY,
            title            TEXT,
            description      TEXT,
            primary_photo_id TEXT,
            count_photos     INTEGER,
            count_views      INTEGER,
            synced_at        INTEGER
        );

        CREATE TABLE IF NOT EXISTS photo_groups (
            photo_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            PRIMARY KEY (photo_id, group_id)
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id        TEXT PRIMARY KEY,
            username  TEXT,
            realname  TEXT,
            is_friend INTEGER DEFAULT 0,
            is_family INTEGER DEFAULT 0,
            synced_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS contact_engagement (
            contact_id   TEXT PRIMARY KEY,
            faves        INTEGER DEFAULT 0,
            comments     INTEGER DEFAULT 0,
            last_updated INTEGER
        );

        CREATE TABLE IF NOT EXISTS do_not_unfollow (
            contact_id TEXT PRIMARY KEY,
            reason     TEXT,
            added_at   INTEGER
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at      INTEGER,
            mode           TEXT,
            photos_fetched INTEGER,
            type           TEXT DEFAULT 'photos'
        );

        CREATE TABLE IF NOT EXISTS pending_group_adds (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id     TEXT NOT NULL,
            group_id     TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'waiting',
            error_msg    TEXT,
            retry_after  INTEGER,
            queued_at    INTEGER NOT NULL,
            completed_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS keeper_list (
            photo_id TEXT PRIMARY KEY,
            note     TEXT,
            added_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS never_follow (
            contact_id TEXT PRIMARY KEY,
            reason     TEXT,
            added_at   INTEGER
        );
    """)
    conn.commit()
    _apply_migrations(conn)


def last_sync_time(conn):
    row = conn.execute("SELECT MAX(synced_at) FROM sync_log WHERE type = 'photos'").fetchone()
    return row[0] if row and row[0] else None


def upsert_photo(conn, p, owner_nsid, synced_at):
    if isinstance(p.get("tags"), dict):
        tags = " ".join(t["raw"] for t in p["tags"].get("tag", []))
    else:
        tags = p.get("tags", "")

    if isinstance(p.get("description"), dict):
        description = p["description"].get("_content", "")
    else:
        description = p.get("description", "")

    path_alias = p.get("pathalias") or owner_nsid
    url_photopage = f"https://www.flickr.com/photos/{path_alias}/{p['id']}/"
    url_original = p.get("url_o") or p.get("url_l", "")
    url_medium = p.get("url_z") or p.get("url_m", "")

    is_public = int(p.get("ispublic", 1))

    conn.execute("""
        INSERT INTO photos
            (id, title, description, date_taken, date_uploaded, last_updated,
             url_photopage, url_original, url_medium, tags, views, favorites, comments, synced_at, is_public)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            date_taken=excluded.date_taken,
            date_uploaded=excluded.date_uploaded,
            last_updated=excluded.last_updated,
            url_photopage=excluded.url_photopage,
            url_original=excluded.url_original,
            url_medium=excluded.url_medium,
            tags=excluded.tags,
            views=excluded.views,
            favorites=excluded.favorites,
            comments=excluded.comments,
            synced_at=excluded.synced_at,
            is_public=excluded.is_public
    """, (
        p["id"],
        p.get("title", ""),
        description,
        p.get("datetaken", ""),
        int(p.get("dateupload", 0) or 0),
        int(p.get("lastupdate", 0) or 0),
        url_photopage,
        url_original,
        url_medium,
        tags,
        int(p.get("views", 0) or 0),
        int(p.get("count_faves", 0) or 0),
        int(p.get("count_comments", 0) or 0),
        synced_at,
        is_public,
    ))


BACKFILL_WINDOW_DAYS = 90  # each date-range chunk; 90 days ≈ safe under Flickr's ~4k cap


# --- Fetch iterators ---

def fetch_all_photos(user_nsid):
    """Yield every photo (public and private) for the authenticated user, paginated."""
    page, pages = 1, 1
    while page <= pages:
        data = api_get("flickr.people.getPhotos", {
            "user_id": user_nsid,
            "extras": EXTRAS,
            "per_page": str(PER_PAGE),
            "page": str(page),
        })
        result = data["photos"]
        pages = int(result["pages"])
        print(f"  page {page}/{pages} ({len(result['photo'])} photos)")
        yield from result["photo"]
        page += 1
        if page <= pages:
            time.sleep(0.5)  # stay within rate limits


# Keep old name as an alias so any external callers aren't broken
fetch_all_public = fetch_all_photos


def fetch_updated(since):
    """Yield all photos (public and private) updated after `since` (unix timestamp), paginated."""
    page, pages = 1, 1
    while page <= pages:
        data = api_get("flickr.photos.recentlyUpdated", {
            "min_date": str(since),
            "extras": EXTRAS,
            "per_page": str(PER_PAGE),
            "page": str(page),
        })
        result = data["photos"]
        pages = int(result["pages"])
        photos = result["photo"]
        public_count = sum(1 for p in photos if int(p.get("ispublic", 0)) == 1)
        print(f"  page {page}/{pages} ({public_count} public, {len(photos) - public_count} private of {len(photos)} updated)")
        yield from photos
        page += 1
        if page <= pages:
            time.sleep(0.5)


def fetch_backfill(user_nsid, conn):
    """Walk the full upload history in 90-day windows to bypass Flickr's page cap.

    Uses flickr.photos.search with date-range filters so each window stays well
    under the ~4000-result ceiling.  Progress is checkpointed in the settings
    table after each window so an interrupted run can resume mid-stream.

    Yields (photo_dict, window_end_timestamp) for each photo.
    """
    from db import get_setting, set_setting

    checkpoint = get_setting(conn, "backfill_checkpoint")
    start_ts = int(checkpoint) if checkpoint else 0  # 0 = beginning of Flickr time

    now = int(time.time())
    window_seconds = BACKFILL_WINDOW_DAYS * 86400

    t = start_ts
    while t < now:
        t_end = min(t + window_seconds, now)
        page, pages = 1, 1
        window_count = 0
        while page <= pages:
            data = api_get("flickr.photos.search", {
                "user_id":        user_nsid,
                "min_upload_date": str(t),
                "max_upload_date": str(t_end),
                "extras":         EXTRAS,
                "per_page":       str(PER_PAGE),
                "page":           str(page),
                "sort":           "date-posted-asc",
            })
            result = data["photos"]
            pages = int(result["pages"])
            photos = result["photo"]
            for p in photos:
                yield p, t_end
                window_count += 1
            page += 1
            if page <= pages:
                time.sleep(0.5)

        from_str = time.strftime("%Y-%m-%d", time.localtime(t))
        to_str   = time.strftime("%Y-%m-%d", time.localtime(t_end))
        print(f"  {from_str} → {to_str}: {window_count} photos")

        set_setting(conn, "backfill_checkpoint", str(t_end + 1))
        conn.commit()
        t = t_end + 1

    # Clear checkpoint on successful completion
    conn.execute("DELETE FROM settings WHERE key='backfill_checkpoint'")
    conn.commit()


# --- Groups ---
#
# Sync happens in two phases:
#   1. sync_groups() + sync_group_descriptions() — incremental DB update from
#      Flickr. Detects new groups, groups the user has left (deleted), and
#      name/description changes. Any change flags needs_summary=1.
#   2. sync_group_summaries() — for groups flagged needs_summary, calls the
#      user's configured LLM to (re)generate an AI markdown summary, milestone
#      thresholds (min faves/views), and search keywords/synonyms.


def sync_groups(conn):
    """Sync the joined-groups list from Flickr, detecting new/renamed/left groups.

    Compares the fetched group list against the local DB: new groups are
    inserted (flagged needs_summary=1), existing groups have members/
    pool_count refreshed, and groups the user has left are deleted (cascading
    their photo_groups rows). A renamed group is flagged needs_summary=1 so
    phase two regenerates its AI summary.
    """
    creds = flickr_api._load_credentials()
    synced_at = int(time.time())

    fetched: dict[str, dict] = {}
    page, pages = 1, 1
    while page <= pages:
        data = api_get("flickr.people.getGroups", {
            "user_id": creds["user_nsid"],
        })
        for g in data["groups"]["group"]:
            fetched[g["nsid"]] = g
        pages = int(data["groups"].get("pages", 1))
        page += 1

    existing = {row[0]: row[1] for row in conn.execute("SELECT id, name FROM groups")}

    new_count = renamed_count = 0
    for gid, g in fetched.items():
        name = g["name"]
        members = int(g.get("members", 0) or 0)
        pool_count = int(g.get("pool_count", 0) or 0)
        if gid not in existing:
            conn.execute("""
                INSERT INTO groups (id, name, members, pool_count, synced_at, needs_summary)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (gid, name, members, pool_count, synced_at))
            new_count += 1
        else:
            renamed = existing[gid] != name
            conn.execute("""
                UPDATE groups SET name=?, members=?, pool_count=?, synced_at=?,
                    needs_summary = CASE WHEN ? THEN 1 ELSE needs_summary END
                WHERE id=?
            """, (name, members, pool_count, synced_at, renamed, gid))
            if renamed:
                renamed_count += 1

    left_ids = [gid for gid in existing if gid not in fetched]
    for gid in left_ids:
        conn.execute("DELETE FROM photo_groups WHERE group_id=?", (gid,))
        conn.execute("DELETE FROM groups WHERE id=?", (gid,))

    conn.commit()
    print(f"  {len(fetched)} groups synced ({new_count} new, {renamed_count} renamed, {len(left_ids)} left).")
    return len(fetched)


def sync_group_descriptions(conn):
    """Fetch each group's description from flickr.groups.getInfo and diff it.

    Every joined group is re-fetched (Flickr exposes no last-modified marker
    for group info), so a changed description is detected and flags
    needs_summary=1 for phase two to regenerate the AI summary.
    """
    rows = conn.execute("SELECT id, description FROM groups").fetchall()
    if not rows:
        return 0
    changed = 0
    for group_id, prev_description in rows:
        try:
            data = flickr_api._api_get("flickr.groups.getInfo", {"group_id": group_id})
        except RuntimeError:
            continue
        group = data.get("group", {})
        description = ((group.get("description") or {}).get("_content", "") or "")[:2000]
        if description != (prev_description or ""):
            conn.execute(
                "UPDATE groups SET description=?, needs_summary=1 WHERE id=?",
                (description, group_id),
            )
            changed += 1
        time.sleep(0.15)
    conn.commit()
    print(f"  {changed} group descriptions changed.")
    return changed


def _parse_group_summary_json(text: str) -> dict:
    """Best-effort parse of the LLM's JSON reply, tolerating markdown code fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    return json.loads(text)


def _build_group_summary_prompt(nsid: str, name: str, description: str, user_note: str) -> str:
    """Fill the user-editable group-summary prompt template (see
    agent.prompts_store) with this group's own name/description/note.

    Uses ``.replace()`` on literal ``{token}`` placeholders rather than
    ``str.format()`` — a group description is freeform text that may itself
    contain stray ``{``/``}`` characters, which would break ``.format()``.
    """
    from agent.prompts_store import GROUP_SUMMARY_PROMPT_DEFAULT, get_prompt_by_code

    prompt = get_prompt_by_code(nsid, "group-summary")
    template = prompt["text"] if prompt else GROUP_SUMMARY_PROMPT_DEFAULT

    desc_text = html.unescape(re.sub(r"<[^>]+>", " ", description or ""))[:3000].strip()
    return (
        template
        .replace("{group_name}", name or "")
        .replace("{group_description}", desc_text or "(no description provided)")
        .replace("{group_user_note}", (user_note or "").strip() or "(none)")
    )


async def _sync_group_summaries_async(conn, nsid: str, rows) -> int:
    import httpx
    from agent import llm
    from agent.settings import DEFAULT_SYNC_THROTTLE_SECONDS, resolve_sync_cfg

    total = len(rows)
    updated = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
        for i, (group_id, name, description, user_note) in enumerate(rows):
            # Re-resolved every iteration (instead of once up front) so a
            # model/connection/throttle change made on the Sync page while
            # this loop is running takes effect on the very next group,
            # rather than only on the next sync run.
            cfg = resolve_sync_cfg(nsid)
            if not cfg.get("base_url") or not cfg.get("model"):
                print(f"  Stopping: no LLM connection/model configured ({total - i} group(s) left for next sync).")
                break
            throttle_seconds = cfg.get("sync_throttle_seconds", DEFAULT_SYNC_THROTTLE_SECONDS)
            stream = llm.stream_responses if cfg.get("api_mode") == "responses" else llm.stream_chat

            if i > 0 and throttle_seconds > 0:
                # Paced to be gentle on a local LLM (the common case here) —
                # one request every throttle_seconds rather than back-to-back
                # calls for every flagged group. Configurable on the Sync page.
                await asyncio.sleep(throttle_seconds)

            # One-time call, no tools, no history: each group gets a fresh
            # session containing only the (editable) prompt + this group's
            # own table data — nothing else.
            prompt = _build_group_summary_prompt(nsid, name, description, user_note)
            messages = [{"role": "user", "content": prompt}]
            try:
                content = ""
                async for event in stream(cfg, messages, client=client):
                    if event["type"] == "message":
                        content = event["content"]
                result = _parse_group_summary_json(content)
            except Exception as e:
                print(f"  Warning: summary generation failed for group {group_id} ({name}): {e}")
            else:
                keywords = result.get("keywords") or []
                conn.execute("""
                    UPDATE groups SET summary_md=?, is_milestone=?, fave_min=?, view_min=?,
                        open_subject=?, ai_keywords=?, needs_summary=0, summary_generated_at=?
                    WHERE id=?
                """, (
                    result.get("summary", ""),
                    int(bool(result.get("is_milestone"))),
                    result.get("fave_min"),
                    result.get("view_min"),
                    None if result.get("open_subject") is None else int(bool(result.get("open_subject"))),
                    " ".join(str(k) for k in keywords),
                    int(time.time()),
                    group_id,
                ))
                conn.commit()
                updated += 1
            finally:
                # Machine-parsed by _run_sync_script (tools/sync.py) to drive the
                # Sync page's progress bar and estimated-time-remaining display.
                print(f"  Summary {i + 1}/{total}: {name}", flush=True)
    return updated


def sync_group_summaries(conn, nsid: str) -> int:
    """Generate AI summaries (rules, milestone thresholds, open-subject
    flag, keywords) for groups flagged needs_summary=1 by the earlier
    DB-update phase, or by a user_note edit via set_group_note."""
    rows = conn.execute(
        "SELECT id, name, description, user_note FROM groups WHERE needs_summary=1"
    ).fetchall()
    if not rows:
        print("  No groups need a summary refresh.")
        return 0

    from agent.settings import resolve_sync_cfg
    cfg = resolve_sync_cfg(nsid)
    if not cfg.get("base_url") or not cfg.get("model"):
        print("  Skipping AI summaries: no LLM connection/model configured (set one on the Sync page).")
        return 0

    updated = asyncio.run(_sync_group_summaries_async(conn, nsid, rows))
    print(f"  {updated}/{len(rows)} group summaries generated.")
    return updated


def sync_photo_groups(conn):
    """Populate photo_groups by fetching the user's photos from each group pool.

    The whole crawl happens in memory first, then the table is updated in one
    short write transaction (committed by the caller), so the write lock is
    never held across network calls.  Rows are replaced per group: a group
    whose pool fetch fails keeps its previous memberships instead of being
    wiped by a partial sync.
    """
    creds = flickr_api._load_credentials()
    user_nsid = creds["user_nsid"]
    groups = conn.execute("SELECT id FROM groups").fetchall()

    fetched = []   # (group_id, [(photo_id, group_id), ...]) for each group fetched in full
    failed = 0
    for (group_id,) in groups:
        rows = []
        ok = True
        page, pages = 1, 1
        while page <= pages:
            try:
                data = flickr_api._api_get("flickr.groups.pools.getPhotos", {
                    "group_id": group_id,
                    "user_id":  user_nsid,
                    "per_page": "500",
                    "page":     str(page),
                })
            except RuntimeError as e:
                print(f"  Warning: keeping existing memberships for group {group_id} ({e})")
                failed += 1
                ok = False
                break
            container = data.get("photos", {})
            pages = int(container.get("pages", 1))
            for p in container.get("photo", []):
                rows.append((p["id"], group_id))
            page += 1
            if page <= pages:
                time.sleep(0.15)
        if ok:
            fetched.append((group_id, rows))

    conn.execute("DELETE FROM photo_groups WHERE group_id NOT IN (SELECT id FROM groups)")
    total = 0
    for group_id, rows in fetched:
        conn.execute("DELETE FROM photo_groups WHERE group_id = ?", (group_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO photo_groups (photo_id, group_id) VALUES (?, ?)",
            rows,
        )
        total += len(rows)

    if failed:
        print(f"  Warning: {failed} of {len(groups)} group pools could not be fetched; "
              f"their previous memberships were kept.")
    print(f"  {total} photo-group memberships synced.")
    return total


# --- Command ---

def cmd_sync(args):
    """Run the photo sync.  Resolves per-user paths from CLI args when provided."""
    target_db = _db_file(args.username) if args.username else DB_FILE
    nsid_arg = args.nsid if args.nsid else None

    if nsid_arg:
        from db import _current_user
        _current_user.set({"nsid": nsid_arg, "username": args.username or ""})

    try:
        flickr_api._load_env()
        creds = flickr_api._load_credentials(nsid=nsid_arg)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(target_db):
        if not args.create:
            print(f"Database not found: {target_db}\nRun with --create to initialise it.", file=sys.stderr)
            sys.exit(1)
        os.makedirs(os.path.dirname(target_db), exist_ok=True)
        print(f"Creating database at {target_db}")

    with sqlite3.connect(target_db) as conn:
        init_db(conn)

        synced_at = int(time.time())

        if args.backfill:
            mode = "backfill"
            print("Backfill sync: walking full upload history in 90-day windows…")
            total = 0
            for photo, window_end in fetch_backfill(creds["user_nsid"], conn):
                upsert_photo(conn, photo, creds["user_nsid"], synced_at)
                total += 1
                if total % 500 == 0:
                    conn.commit()
                    print(f"  {total} photos upserted so far…")
        else:
            since = last_sync_time(conn)
            if args.full or since is None:
                if not args.full:
                    print("No previous sync found — running full sync.")
                mode = "full"
                photos = fetch_all_photos(creds["user_nsid"])
            else:
                mode = "update"
                print(f"Incremental sync since {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))}")
                photos = fetch_updated(since)

            total = 0
            for photo in photos:
                upsert_photo(conn, photo, creds["user_nsid"], synced_at)
                total += 1
                if total % 100 == 0:
                    conn.commit()

        conn.commit()
        conn.execute(
            "INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?, ?, ?, 'photos')",
            (synced_at, mode, total),
        )
        conn.commit()
    print(f"Done. {total} photos synced ({mode}) to {target_db}.")


# --- Entry point ---

def main():
    """Parse CLI arguments and run the photo sync."""
    parser = argparse.ArgumentParser(prog="flickr-sync", description="Sync Flickr photo metadata to SQLite")
    parser.add_argument("--full",     action="store_true", help="Full sync (ignore last sync timestamp)")
    parser.add_argument("--backfill", action="store_true", help="Walk full upload history in date-range windows (bypasses Flickr page cap)")
    parser.add_argument("--create",   action="store_true", help="Create the database if it does not exist")
    parser.add_argument("--nsid",     help="Flickr user NSID for multi-user mode")
    parser.add_argument("--username", help="Username for per-user DB path (multi-user mode)")
    cmd_sync(parser.parse_args())


if __name__ == "__main__":
    main()
