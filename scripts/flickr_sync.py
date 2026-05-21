#!/usr/bin/env python3
"""Sync public Flickr photo metadata to a local SQLite database.

Usage:
    python scripts/flickr_sync.py          # incremental (since last sync)
    python scripts/flickr_sync.py --full   # fetch all public photos
"""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flickr_api

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "flickr.db")
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
    """)
    conn.commit()

    # Migrations: safely add columns that may be missing from older databases
    migrations = [
        "ALTER TABLE photos ADD COLUMN favorites    INTEGER DEFAULT 0",
        "ALTER TABLE photos ADD COLUMN comments     INTEGER DEFAULT 0",
        "ALTER TABLE photos ADD COLUMN reviewed_at  INTEGER DEFAULT NULL",
        "ALTER TABLE photos ADD COLUMN is_public    INTEGER DEFAULT 1",
        "ALTER TABLE sync_log ADD COLUMN type       TEXT DEFAULT 'photos'",
        "ALTER TABLE groups ADD COLUMN description  TEXT",
        "ALTER TABLE groups ADD COLUMN keywords     TEXT",
        "ALTER TABLE sync_log ADD COLUMN duration_seconds INTEGER",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # column already exists


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
    print(f"  {updated} group descriptions fetched.")
    return updated


# --- Command ---

def cmd_sync(args):
    try:
        flickr_api._load_env()
        creds = flickr_api._load_credentials()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(DB_FILE):
        if not args.create:
            print(f"Database not found: {DB_FILE}\nRun with --create to initialise it.", file=sys.stderr)
            sys.exit(1)
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        print(f"Creating database at {DB_FILE}")

    conn = sqlite3.connect(DB_FILE)
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
    conn.close()
    print(f"Done. {total} photos synced ({mode}).")


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(prog="flickr-sync", description="Sync Flickr photo metadata to SQLite")
    parser.add_argument("--full", action="store_true", help="Full sync (ignore last sync timestamp)")
    parser.add_argument("--create", action="store_true", help="Create the database if it does not exist")
    cmd_sync(parser.parse_args())


if __name__ == "__main__":
    main()
