#!/usr/bin/env python3
"""Sync Flickr group membership to the local SQLite database.

Usage (single-user):
    python scripts/sync_groups.py

Usage (multi-user):
    python scripts/sync_groups.py --nsid 12345@N00 --username jdoe
"""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import sync_groups, sync_group_descriptions, init_db, DB_FILE
from db import db_file as _db_file


def main():
    """Parse CLI arguments and sync group membership for the resolved user."""
    parser = argparse.ArgumentParser(prog="sync-groups", description="Sync Flickr groups to SQLite")
    parser.add_argument("--full",     action="store_true", help="Full sync (groups API is always full)")
    parser.add_argument("--nsid",     help="Flickr user NSID for multi-user mode")
    parser.add_argument("--username", help="Username for per-user DB path (multi-user mode)")
    args = parser.parse_args()

    target_db = _db_file(args.username) if args.username else DB_FILE

    if not os.path.exists(target_db):
        print(f"Database not found: {target_db}\nVisit http://localhost:8000/sync to run a sync", file=sys.stderr)
        sys.exit(1)

    with sqlite3.connect(target_db) as conn:
        init_db(conn)

        print("Syncing groups...")
        synced_at = int(time.time())
        total = sync_groups(conn)

        print("Fetching group descriptions...")
        sync_group_descriptions(conn)

        conn.execute(
            "INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?, 'full', ?, 'groups')",
            (synced_at, total or 0),
        )
        conn.commit()
    print("Done.")


if __name__ == "__main__":
    main()
