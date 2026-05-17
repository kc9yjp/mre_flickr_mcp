#!/usr/bin/env python3
"""Sync Flickr albums (photosets) to the local SQLite database."""

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import load_env, load_credentials, oauth_params, sign_request, init_db, DB_FILE, API_URL

import requests

if not os.path.exists(DB_FILE):
    print(f"Database not found: {DB_FILE}\nVisit http://localhost:8000/sync to run a sync", file=sys.stderr)
    sys.exit(1)

conn = sqlite3.connect(DB_FILE)
init_db(conn)
api_key, api_secret = load_env()
creds = load_credentials()

print("Syncing albums...")
page, pages, total = 1, 1, 0
synced_at = int(time.time())

while page <= pages:
    params = oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": "flickr.photosets.getList",
        "user_id": creds["user_nsid"],
        "per_page": "500",
        "page": str(page),
        "format": "json",
        "nojsoncallback": "1",
    })
    params["oauth_signature"] = sign_request("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
    resp = requests.get(API_URL, params=params)
    data = resp.json()
    if data.get("stat") != "ok":
        print(f"Error: {data.get('message')}", file=sys.stderr)
        sys.exit(1)

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
conn.close()
print(f"Done. {total} albums synced.")
