#!/usr/bin/env python3
"""Sync Flickr albums (photosets) to the local SQLite database.

Usage (single-user):
    python scripts/sync_albums.py

Usage (multi-user):
    python scripts/sync_albums.py --nsid 12345@N00 --username jdoe
"""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flickr_api
from flickr_sync import api_get, init_db, DB_FILE
from db import db_file as _db_file


def main():
    """Parse CLI arguments and sync albums for the resolved user."""
    parser = argparse.ArgumentParser(prog="sync-albums", description="Sync Flickr albums to SQLite")
    parser.add_argument("--nsid",     help="Flickr user NSID for multi-user mode")
    parser.add_argument("--username", help="Username for per-user DB path (multi-user mode)")
    args = parser.parse_args()

    target_db = _db_file(args.username) if args.username else DB_FILE
    nsid_arg = args.nsid if args.nsid else None

    if nsid_arg:
        from db import _current_user
        _current_user.set({"nsid": nsid_arg, "username": args.username or ""})

    if not os.path.exists(target_db):
        print(f"Database not found: {target_db}\nVisit http://localhost:8000/sync to run a sync", file=sys.stderr)
        sys.exit(1)

    try:
        creds = flickr_api._load_credentials(nsid=nsid_arg)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with sqlite3.connect(target_db) as conn:
        init_db(conn)

        print("Syncing albums...")
        page, pages, total = 1, 1, 0
        synced_at = int(time.time())

        while page <= pages:
            data = api_get("flickr.photosets.getList", {
                "user_id": creds["user_nsid"],
                "per_page": "500",
                "page": str(page),
            })

            result = data["photosets"]
            pages = int(result.get("pages", 1))
            for a in result.get("photoset", []):
                title = a.get("title", {}).get("_content", "") if isinstance(a.get("title"), dict) else a.get("title", "")
                description = a.get("description", {}).get("_content", "") if isinstance(a.get("description"), dict) else a.get("description", "")
                conn.execute("""
                    INSERT INTO albums (id, title, description, primary_photo_id, count_photos, count_views, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title, description=excluded.description,
                        primary_photo_id=excluded.primary_photo_id,
                        count_photos=excluded.count_photos, count_views=excluded.count_views,
                        synced_at=excluded.synced_at
                """, (
                    a["id"], title, description,
                    a.get("primary", ""),
                    int(a.get("photos", 0) or 0),
                    int(a.get("count_views", 0) or 0),
                    synced_at,
                ))
                total += 1
            conn.commit()
            page += 1

        conn.execute(
            "INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?, 'full', ?, 'albums')",
            (synced_at, total),
        )
        conn.commit()
    print(f"Done. {total} albums synced.")


if __name__ == "__main__":
    main()
