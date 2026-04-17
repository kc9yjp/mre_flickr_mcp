#!/usr/bin/env python3
"""Sync Flickr group membership to the local SQLite database."""

import os
import sqlite3
import sys

# reuse auth and DB utilities from flickr_sync
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import load_env, load_credentials, sync_groups, init_db, DB_FILE

if not os.path.exists(DB_FILE):
    print(f"Database not found: {DB_FILE}\nRun: bin/flickr-sync --create", file=sys.stderr)
    sys.exit(1)

conn = sqlite3.connect(DB_FILE)
init_db(conn)
api_key, api_secret = load_env()
creds = load_credentials()
print("Syncing groups...")
sync_groups(api_key, api_secret, creds, conn)
conn.close()
print("Done.")
