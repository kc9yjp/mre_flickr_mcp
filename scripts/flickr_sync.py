#!/usr/bin/env python3
"""Sync public Flickr photo metadata to a local SQLite database.

Usage:
    python scripts/flickr_sync.py          # incremental (since last sync)
    python scripts/flickr_sync.py --full   # fetch all public photos
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.parse

import requests

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "flickr.db")
CREDENTIALS_FILE = os.path.expanduser("~/.flickr_mcp/credentials.json")
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
API_URL = "https://api.flickr.com/services/rest/"
PER_PAGE = 500

EXTRAS = "description,date_upload,date_taken,last_update,tags,views,count_faves,count_comments,url_o,url_l,path_alias,media"


# --- Auth (mirrors flickr.py) ---

def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    api_key = env.get("FLICKR_API_KEY") or os.environ.get("FLICKR_API_KEY")
    api_secret = env.get("FLICKR_API_SECRET") or os.environ.get("FLICKR_API_SECRET")
    if not api_key or not api_secret:
        print("Error: FLICKR_API_KEY and FLICKR_API_SECRET must be set in .env", file=sys.stderr)
        sys.exit(1)
    return api_key, api_secret


def sign_request(method, url, params, api_secret, token_secret=""):
    sorted_params = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    base_string = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    signing_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    sig = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1)
    return base64.b64encode(sig.digest()).decode()


def load_credentials():
    if not os.path.exists(CREDENTIALS_FILE):
        print("Not logged in. Run: bin/flickr login", file=sys.stderr)
        sys.exit(1)
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def oauth_params(api_key, extra=None):
    params = {
        "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
        "oauth_timestamp": str(int(time.time())),
        "oauth_consumer_key": api_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }
    if extra:
        params.update(extra)
    return params


def api_get(api_key, api_secret, creds, method, extra=None):
    params = oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": method,
        "format": "json",
        "nojsoncallback": "1",
    })
    if extra:
        params.update(extra)
    params["oauth_signature"] = sign_request("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
    resp = requests.get(API_URL, params=params)
    data = resp.json()
    if data.get("stat") != "ok":
        print(f"API error ({method}): {data.get('message', 'unknown')}", file=sys.stderr)
        sys.exit(1)
    return data


# --- Database ---

def init_db(conn):
    conn.executescript("""
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
            photos_fetched INTEGER
        );
    """)
    conn.commit()

    # Migrations: safely add columns that may be missing from older databases
    migrations = [
        "ALTER TABLE photos ADD COLUMN favorites    INTEGER DEFAULT 0",
        "ALTER TABLE photos ADD COLUMN comments     INTEGER DEFAULT 0",
        "ALTER TABLE photos ADD COLUMN reviewed_at  INTEGER DEFAULT NULL",
        "ALTER TABLE photos ADD COLUMN is_public    INTEGER DEFAULT 1",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # column already exists


def last_sync_time(conn):
    row = conn.execute("SELECT MAX(synced_at) FROM sync_log").fetchone()
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

def fetch_all_public(api_key, api_secret, creds):
    """Yield every public photo for the authenticated user, paginated."""
    page, pages = 1, 1
    while page <= pages:
        data = api_get(api_key, api_secret, creds, "flickr.people.getPublicPhotos", {
            "user_id": creds["user_nsid"],
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


def fetch_updated(api_key, api_secret, creds, since):
    """Yield public photos updated after `since` (unix timestamp), paginated."""
    page, pages = 1, 1
    while page <= pages:
        data = api_get(api_key, api_secret, creds, "flickr.photos.recentlyUpdated", {
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

def sync_groups(api_key, api_secret, creds, conn):
    page, pages = 1, 1
    synced_at = int(time.time())
    total = 0
    while page <= pages:
        params = oauth_params(api_key, {
            "oauth_token": creds["oauth_token"],
            "method": "flickr.people.getGroups",
            "user_id": creds["user_nsid"],
            "format": "json",
            "nojsoncallback": "1",
        })
        params["oauth_signature"] = sign_request("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
        resp = requests.get(API_URL, params=params)
        data = resp.json()
        if data.get("stat") != "ok":
            print(f"Error fetching groups: {data.get('message')}", file=sys.stderr)
            return
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
        # getGroups doesn't paginate, but handle it if it does
        pages = int(data["groups"].get("pages", 1))
        page += 1
    conn.commit()
    print(f"  {total} groups synced.")


# --- Command ---

def cmd_sync(args):
    api_key, api_secret = load_env()
    creds = load_credentials()

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
        photos = fetch_all_public(api_key, api_secret, creds)
    else:
        mode = "update"
        print(f"Incremental sync since {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))}")
        photos = fetch_updated(api_key, api_secret, creds, since)

    total = 0
    for photo in photos:
        upsert_photo(conn, photo, creds["user_nsid"], synced_at)
        total += 1
        if total % 100 == 0:
            conn.commit()

    conn.commit()
    conn.execute(
        "INSERT INTO sync_log (synced_at, mode, photos_fetched) VALUES (?, ?, ?)",
        (synced_at, mode, total),
    )
    conn.commit()

    print("Syncing groups...")
    sync_groups(api_key, api_secret, creds, conn)
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
