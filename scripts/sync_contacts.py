#!/usr/bin/env python3
"""Sync Flickr contacts (people you follow) to the local SQLite database."""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import api_get, init_db, DB_FILE


def main():
    parser = argparse.ArgumentParser(prog="sync-contacts", description="Sync Flickr contacts to SQLite")
    parser.add_argument("--full", action="store_true", help="Full sync (contacts API is always full)")
    args = parser.parse_args()

    if not os.path.exists(DB_FILE):
        print(f"Database not found: {DB_FILE}\nVisit http://localhost:8000/sync to run a sync", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    print("Syncing contacts...")
    page, pages, total = 1, 1, 0
    synced_at = int(time.time())

    while page <= pages:
        data = api_get("flickr.contacts.getList", {
            "per_page": "1000",
            "page": str(page),
        })

        contacts = data["contacts"]
        pages = int(contacts.get("pages", 1))
        for c in contacts.get("contact", []):
            conn.execute("""
                INSERT INTO contacts (id, username, realname, is_friend, is_family, synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username=excluded.username, realname=excluded.realname,
                    is_friend=excluded.is_friend, is_family=excluded.is_family,
                    synced_at=excluded.synced_at
            """, (
                c["nsid"], c.get("username", ""), c.get("realname", ""),
                int(c.get("friend", 0)), int(c.get("family", 0)),
                synced_at,
            ))
            total += 1
        conn.commit()
        page += 1

    conn.execute(
        "INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?, 'full', ?, 'contacts')",
        (synced_at, total),
    )
    conn.commit()
    conn.close()
    print(f"Done. {total} contacts synced.")


if __name__ == "__main__":
    main()
