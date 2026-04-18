#!/usr/bin/env python3
"""Sync per-contact engagement (faves + comments on your photos) to the local database."""

import os
import sqlite3
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import load_env, load_credentials, oauth_params, sign_request, init_db, DB_FILE, API_URL

import requests

if not os.path.exists(DB_FILE):
    print(f"Database not found: {DB_FILE}\nRun: bin/flickr-sync --create", file=sys.stderr)
    sys.exit(1)

conn = sqlite3.connect(DB_FILE)
init_db(conn)
api_key, api_secret = load_env()
creds = load_credentials()

engagement = defaultdict(lambda: {"faves": 0, "comments": 0})


def api_get(method, extra):
    params = oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": method,
        "format": "json",
        "nojsoncallback": "1",
    })
    params.update(extra)
    params["oauth_signature"] = sign_request("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
    resp = requests.get(API_URL, params=params)
    return resp.json()


# --- Faves ---
fave_photos = conn.execute("SELECT id FROM photos WHERE favorites > 0").fetchall()
print(f"Fetching faves for {len(fave_photos)} photos...")
for i, (photo_id,) in enumerate(fave_photos, 1):
    page, pages = 1, 1
    while page <= pages:
        data = api_get("flickr.photos.getFavorites", {"photo_id": photo_id, "per_page": "50", "page": str(page)})
        if data.get("stat") != "ok":
            break
        result = data["photo"]
        pages = int(result.get("pages", 1))
        for person in result.get("person", []):
            engagement[person["nsid"]]["faves"] += 1
        page += 1
        if page <= pages:
            time.sleep(0.5)
    if i % 50 == 0:
        print(f"  {i}/{len(fave_photos)} photos processed for faves")
    time.sleep(0.5)

# --- Comments ---
comment_photos = conn.execute("SELECT id FROM photos WHERE comments > 0").fetchall()
print(f"Fetching comments for {len(comment_photos)} photos...")
for i, (photo_id,) in enumerate(comment_photos, 1):
    data = api_get("flickr.photos.comments.getList", {"photo_id": photo_id})
    if data.get("stat") != "ok":
        continue
    for comment in data.get("comments", {}).get("comment", []):
        engagement[comment["author"]]["comments"] += 1
    if i % 50 == 0:
        print(f"  {i}/{len(comment_photos)} photos processed for comments")
    time.sleep(0.5)

# --- Upsert ---
last_updated = int(time.time())
for contact_id, counts in engagement.items():
    conn.execute("""
        INSERT INTO contact_engagement (contact_id, faves, comments, last_updated)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(contact_id) DO UPDATE SET
            faves=excluded.faves, comments=excluded.comments, last_updated=excluded.last_updated
    """, (contact_id, counts["faves"], counts["comments"], last_updated))
conn.commit()
conn.close()
print(f"Done. Engagement recorded for {len(engagement)} contacts.")
