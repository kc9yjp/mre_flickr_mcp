#!/usr/bin/env python3
"""Sync Flickr group membership to the local SQLite database.

Usage (single-user):
    python scripts/sync_groups.py

Usage (multi-user):
    python scripts/sync_groups.py --nsid 12345@N00 --username jdoe

Backfill group vectors after first enabling semantic search (no Flickr calls):
    python scripts/sync_groups.py --rebuild-vectors --nsid 12345@N00 --username jdoe
"""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import (
    sync_groups, sync_group_descriptions, sync_group_summaries, sync_photo_groups,
    init_db, DB_FILE,
)
from db import db_file as _db_file, check_dir_ownership, seed_dir_ownership
import flickr_api
import vector_search


def main():
    """Parse CLI arguments and sync group membership for the resolved user."""
    parser = argparse.ArgumentParser(prog="sync-groups", description="Sync Flickr groups to SQLite")
    parser.add_argument("--full",     action="store_true", help="Full sync (groups API is always full)")
    parser.add_argument("--nsid",     help="Flickr user NSID for multi-user mode")
    parser.add_argument("--username", help="Username for per-user DB path (multi-user mode)")
    parser.add_argument("--rebuild-vectors", action="store_true",
                        help="Re-embed every group into the vector store and exit "
                             "(no Flickr calls; requires WORKBENCH_VECTOR_SEARCH_ENABLED)")
    args = parser.parse_args()

    target_db = _db_file(args.username) if args.username else DB_FILE

    if args.username:
        try:
            check_dir_ownership(args.username, args.nsid)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    if not os.path.exists(target_db):
        print(f"Database not found: {target_db}\nVisit http://localhost:8000/sync to run a sync", file=sys.stderr)
        sys.exit(1)

    if args.username:
        seed_dir_ownership(args.username, args.nsid)

    if args.nsid:
        from db import _current_user
        _current_user.set({"nsid": args.nsid, "username": args.username or ""})

    nsid = args.nsid or flickr_api._load_credentials().get("user_nsid", "")

    if args.rebuild_vectors:
        if not vector_search.enabled():
            print("Vector search is off. Set WORKBENCH_VECTOR_SEARCH_ENABLED=true and retry.", file=sys.stderr)
            sys.exit(1)
        with sqlite3.connect(target_db) as conn:
            init_db(conn)
            print("Rebuilding group vectors...")
            stats = vector_search.sync_group_vectors(conn, nsid, args.username, rebuild=True)
        print("Done.")
        # Unlike the sync path — where a failed embedding must never fail the
        # run — this is an explicit one-shot command, so report the failure.
        sys.exit(1 if stats["failed"] or (stats["total"] and not stats["embedded"]) else 0)

    with sqlite3.connect(target_db) as conn:
        init_db(conn)

        print("Syncing groups...")
        synced_at = int(time.time())
        total = sync_groups(conn)

        print("Fetching group descriptions...")
        sync_group_descriptions(conn)

        print("Syncing photo-group memberships...")
        sync_photo_groups(conn)

        print("Generating AI group summaries...")
        sync_group_summaries(conn, nsid)

        # Optional final phase — skipped entirely (not even announced in the
        # log) unless WORKBENCH_VECTOR_SEARCH_ENABLED is on. Runs after the
        # summaries so the embedded text includes each group's freshly
        # generated summary/keywords.
        if vector_search.enabled():
            print("Building group vectors...")
            vector_search.sync_group_vectors(conn, nsid, args.username)

        conn.execute(
            "INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?, 'full', ?, 'groups')",
            (synced_at, total or 0),
        )
        conn.commit()
    print("Done.")


if __name__ == "__main__":
    main()
