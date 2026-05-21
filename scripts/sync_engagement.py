#!/usr/bin/env python3
"""Sync per-contact engagement (faves + comments on your photos) to the local database.

Usage (single-user):
    python scripts/sync_engagement.py

Usage (multi-user):
    python scripts/sync_engagement.py --nsid 12345@N00 --username jdoe
"""

import argparse
import os
import sqlite3
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flickr_api
from flickr_sync import init_db, DB_FILE
from db import db_file as _db_file


def upsert_engagement(conn, contact_id, faves=0, comments=0):
    """Increment fave/comment counts for *contact_id*, or insert if new."""
    last_updated = int(time.time())
    conn.execute("""
        INSERT INTO contact_engagement (contact_id, faves, comments, last_updated)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(contact_id) DO UPDATE SET
            faves = faves + excluded.faves,
            comments = comments + excluded.comments,
            last_updated = excluded.last_updated
    """, (contact_id, faves, comments, last_updated))


def api_get(method, extra):
    """Call ``flickr_api._api_get``, letting its built-in retry handle transient errors."""
    try:
        return flickr_api._api_get(method, extra)
    except RuntimeError as e:
        print(f"  API error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Parse CLI arguments and sync engagement data for the resolved user."""
    parser = argparse.ArgumentParser(
        prog="sync-engagement",
        description="Sync per-contact engagement (faves + comments) to SQLite",
    )
    parser.add_argument("--nsid",     help="Flickr user NSID for multi-user mode")
    parser.add_argument("--username", help="Username for per-user DB path (multi-user mode)")
    args = parser.parse_args()

    target_db = _db_file(args.username) if args.username else DB_FILE

    if not os.path.exists(target_db):
        print(f"Database not found: {target_db}\nVisit http://localhost:8000/sync to run a sync", file=sys.stderr)
        sys.exit(1)

    with sqlite3.connect(target_db) as conn:
        init_db(conn)

        # Clear existing engagement so we start fresh (avoids double-counting on resume)
        conn.execute("DELETE FROM contact_engagement")
        conn.commit()

        # --- Faves ---
        fave_photos = conn.execute("SELECT id FROM photos WHERE favorites > 0").fetchall()
        print(f"Fetching faves for {len(fave_photos)} photos...")
        batch: defaultdict[str, int] = defaultdict(int)
        for i, (photo_id,) in enumerate(fave_photos, 1):
            page, pages = 1, 1
            while page <= pages:
                data = api_get("flickr.photos.getFavorites", {"photo_id": photo_id, "per_page": "50", "page": str(page)})
                result = data["photo"]
                pages = int(result.get("pages", 1))
                for person in result.get("person", []):
                    batch[person["nsid"]] += 1
                page += 1
                if page <= pages:
                    time.sleep(0.5)
            if i % 100 == 0:
                for contact_id, count in batch.items():
                    upsert_engagement(conn, contact_id, faves=count)
                conn.commit()
                batch.clear()
                print(f"  {i}/{len(fave_photos)} photos processed for faves")
            time.sleep(0.5)
        # flush remaining faves
        for contact_id, count in batch.items():
            upsert_engagement(conn, contact_id, faves=count)
        conn.commit()
        print(f"  {len(fave_photos)}/{len(fave_photos)} photos processed for faves")

        # --- Comments ---
        comment_photos = conn.execute("SELECT id FROM photos WHERE comments > 0").fetchall()
        print(f"Fetching comments for {len(comment_photos)} photos...")
        batch = defaultdict(int)
        for i, (photo_id,) in enumerate(comment_photos, 1):
            data = api_get("flickr.photos.comments.getList", {"photo_id": photo_id})
            for comment in data.get("comments", {}).get("comment", []):
                batch[comment["author"]] += 1
            if i % 100 == 0:
                for contact_id, count in batch.items():
                    upsert_engagement(conn, contact_id, comments=count)
                conn.commit()
                batch.clear()
                print(f"  {i}/{len(comment_photos)} photos processed for comments")
            time.sleep(0.5)
        # flush remaining comments
        for contact_id, count in batch.items():
            upsert_engagement(conn, contact_id, comments=count)
        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM contact_engagement").fetchone()[0]
    print(f"Done. Engagement recorded for {total} contacts.")


if __name__ == "__main__":
    main()
