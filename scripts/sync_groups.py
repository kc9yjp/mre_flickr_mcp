#!/usr/bin/env python3
"""Sync Flickr group membership to the local SQLite database."""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import load_env, load_credentials, sync_groups, sync_group_descriptions, init_db, DB_FILE


def main():
    parser = argparse.ArgumentParser(prog="sync-groups", description="Sync Flickr groups to SQLite")
    parser.add_argument("--full", action="store_true", help="Full sync (groups API is always full)")
    args = parser.parse_args()

    if not os.path.exists(DB_FILE):
        print(f"Database not found: {DB_FILE}\nRun: bin/flickr-sync --create", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)
    api_key, api_secret = load_env()
    creds = load_credentials()

    print("Syncing groups...")
    synced_at = int(time.time())
    total = sync_groups(api_key, api_secret, creds, conn)

    print("Fetching group descriptions...")
    sync_group_descriptions(api_key, api_secret, creds, conn)

    conn.execute(
        "INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?, 'full', ?, 'groups')",
        (synced_at, total or 0),
    )
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
