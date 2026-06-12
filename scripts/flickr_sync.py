#!/usr/bin/env python3
"""Sync public Flickr photo metadata to a local SQLite database.

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
import html
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

EXTRAS = "description,date_upload,date_taken,last_update,tags,views,count_faves,count_comments,url_o,url_l,path_alias,media"

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

    conn.execute("""
        INSERT INTO photos
            (id, title, description, date_taken, date_uploaded, last_updated,
             url_photopage, url_original, tags, views, favorites, comments, synced_at, is_public)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            date_taken=excluded.date_taken,
            date_uploaded=excluded.date_uploaded,
            last_updated=excluded.last_updated,
            url_photopage=excluded.url_photopage,
            url_original=excluded.url_original,
            tags=excluded.tags,
            views=excluded.views,
            favorites=excluded.favorites,
            comments=excluded.comments,
            synced_at=excluded.synced_at,
            is_public=1
    """, (
        p["id"],
        p.get("title", ""),
        description,
        p.get("datetaken", ""),
        int(p.get("dateupload", 0) or 0),
        int(p.get("lastupdate", 0) or 0),
        url_photopage,
        url_original,
        tags,
        int(p.get("views", 0) or 0),
        int(p.get("count_faves", 0) or 0),
        int(p.get("count_comments", 0) or 0),
        synced_at,
    ))


# --- Fetch iterators ---

def fetch_all_public(user_nsid):
    """Yield every public photo for the authenticated user, paginated."""
    page, pages = 1, 1
    while page <= pages:
        data = api_get("flickr.people.getPublicPhotos", {
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


def fetch_updated(since):
    """Yield public photos updated after `since` (unix timestamp), paginated."""
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
        public = [p for p in result["photo"] if int(p.get("ispublic", 0)) == 1]
        print(f"  page {page}/{pages} ({len(public)} public of {len(result['photo'])} updated)")
        yield from public
        page += 1
        if page <= pages:
            time.sleep(0.5)


# --- Groups ---

_KW_STOP = {
    "a", "an", "the", "and", "or", "of", "in", "for", "to", "is", "are",
    "be", "as", "at", "by", "it", "its", "on", "no", "not", "all", "any",
    "our", "my", "your", "we", "you", "me", "us", "am", "was", "do", "go",
    "only", "more", "from", "with", "that", "this", "can", "will", "has",
    "have", "had", "just", "been", "also", "if", "but", "so", "up", "out",
    "into", "than", "then", "when", "where", "who", "what", "how", "here",
    "there", "some", "such", "even", "very", "too", "most", "well", "new",
    "like", "one", "two", "per", "now", "use", "yes", "may",
    # flickr-specific noise
    "flickr", "photo", "photos", "picture", "pictures", "image", "images",
    "pic", "pics", "group", "pool", "please", "welcome", "add", "post",
    "feel", "free", "share", "join", "member", "members", "rule", "rules",
}


def generate_group_keywords(name: str, description: str = "") -> str:
    """Derive searchable keywords from a group name and description."""
    name_text = html.unescape(name or "")
    desc_text = html.unescape(description or "")[:600]

    # Split on hyphens/underscores so "wabi-sabi" → ["wabi", "sabi"]
    combined = re.sub(r"[-_]", " ", name_text + " " + desc_text)
    # Extract lowercase alpha words of 3+ chars
    words = re.findall(r"[a-z]{3,}", combined.lower())

    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        if w not in _KW_STOP and w not in seen:
            seen.add(w)
            result.append(w)

    return " ".join(result[:60])


def populate_group_keywords(conn) -> int:
    """Regenerate auto_keywords for all groups from name + description."""
    rows = conn.execute("SELECT id, name, description FROM groups").fetchall()
    updated = 0
    for row in rows:
        kw = generate_group_keywords(row["name"] or "", row["description"] or "")
        conn.execute("UPDATE groups SET auto_keywords=? WHERE id=?", (kw, row["id"]))
        updated += 1
    conn.commit()
    return updated


def sync_groups(conn):
    creds = flickr_api._load_credentials()
    page, pages = 1, 1
    synced_at = int(time.time())
    total = 0
    while page <= pages:
        data = api_get("flickr.people.getGroups", {
            "user_id": creds["user_nsid"],
        })
        groups = data["groups"]["group"]
        for g in groups:
            conn.execute("""
                INSERT INTO groups (id, name, members, pool_count, synced_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, members=excluded.members,
                    pool_count=excluded.pool_count, synced_at=excluded.synced_at
            """, (
                g["nsid"], g["name"],
                int(g.get("members", 0) or 0),
                int(g.get("pool_count", 0) or 0),
                synced_at,
            ))
            total += 1
        pages = int(data["groups"].get("pages", 1))
        page += 1
    conn.commit()
    populate_group_keywords(conn)
    print(f"  {total} groups synced.")
    return total


def sync_group_descriptions(conn):
    """Fetch descriptions from flickr.groups.getInfo for groups missing them."""
    rows = conn.execute("SELECT id FROM groups WHERE description IS NULL").fetchall()
    if not rows:
        return 0
    updated = 0
    for (group_id,) in rows:
        try:
            data = flickr_api._api_get("flickr.groups.getInfo", {"group_id": group_id})
        except RuntimeError:
            continue
        group = data.get("group", {})
        description = (group.get("description") or {}).get("_content", "") or ""
        conn.execute(
            "UPDATE groups SET description=? WHERE id=?",
            (description[:2000], group_id),
        )
        updated += 1
        time.sleep(0.15)
    conn.commit()
    if updated:
        populate_group_keywords(conn)
    print(f"  {updated} group descriptions fetched.")
    return updated


def sync_photo_groups(conn):
    """Populate photo_groups by fetching the user's photos from each group pool."""
    creds = flickr_api._load_credentials()
    user_nsid = creds["user_nsid"]
    groups = conn.execute("SELECT id FROM groups").fetchall()

    # Full clear before re-population; a mid-sync failure leaves the table empty
    # until the next successful sync (get_photo_contexts falls back to the API).
    conn.execute("DELETE FROM photo_groups")
    conn.commit()

    total = 0
    for (group_id,) in groups:
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
                print(f"  Warning: skipping group {group_id} ({e})")
                break
            container = data.get("photos", {})
            pages = int(container.get("pages", 1))
            for p in container.get("photo", []):
                conn.execute(
                    "INSERT OR IGNORE INTO photo_groups (photo_id, group_id) VALUES (?, ?)",
                    (p["id"], group_id),
                )
                total += 1
            conn.commit()
            page += 1
            if page <= pages:
                time.sleep(0.15)

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
        since = last_sync_time(conn)

        if args.full or since is None:
            if not args.full:
                print("No previous sync found — running full sync.")
            mode = "full"
            photos = fetch_all_public(creds["user_nsid"])
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
    parser.add_argument("--create",   action="store_true", help="Create the database if it does not exist")
    parser.add_argument("--nsid",     help="Flickr user NSID for multi-user mode")
    parser.add_argument("--username", help="Username for per-user DB path (multi-user mode)")
    cmd_sync(parser.parse_args())


if __name__ == "__main__":
    main()
