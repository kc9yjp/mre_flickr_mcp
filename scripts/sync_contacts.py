#!/usr/bin/env python3
"""Sync Flickr contacts (people you follow) to the local SQLite database."""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flickr_sync import load_env, load_credentials, oauth_params, sign_request, init_db, DB_FILE, API_URL, HTTP_TIMEOUT

import requests


def main():
    parser = argparse.ArgumentParser(prog="sync-contacts", description="Sync Flickr contacts to SQLite")
    parser.add_argument("--full", action="store_true", help="Full sync (contacts API is always full)")
    args = parser.parse_args()

    if not os.path.exists(DB_FILE):
        print(f"Database not found: {DB_FILE}\nRun: bin/flickr-sync --create", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)
    api_key, api_secret = load_env()
    creds = load_credentials()

    print("Syncing contacts...")
    page, pages, total = 1, 1, 0
    synced_at = int(time.time())

    while page <= pages:
        params = oauth_params(api_key, {
            "oauth_token": creds["oauth_token"],
            "method": "flickr.contacts.getList",
            "per_page": "1000",
            "page": str(page),
            "format": "json",
            "nojsoncallback": "1",
        })
        params["oauth_signature"] = sign_request("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
        try:
            resp = requests.get(API_URL, params=params, timeout=HTTP_TIMEOUT)
        except requests.exceptions.Timeout:
            print(f"Error fetching contacts: timed out after {HTTP_TIMEOUT}s", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            print(f"Error fetching contacts: {e}", file=sys.stderr)
            sys.exit(1)
        if resp.status_code == 429:
            print("Error fetching contacts: rate limited (HTTP 429)", file=sys.stderr)
            sys.exit(1)
        if not resp.ok:
            print(f"Error fetching contacts: HTTP {resp.status_code}", file=sys.stderr)
            sys.exit(1)
        data = resp.json()
        if data.get("stat") != "ok":
            print(f"Error: {data.get('message')}", file=sys.stderr)
            sys.exit(1)

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
