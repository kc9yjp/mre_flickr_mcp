"""Shared fixtures for MCP server tests."""

import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY, title TEXT, description TEXT,
    date_taken TEXT, date_uploaded INTEGER, last_updated INTEGER,
    url_photopage TEXT, url_original TEXT, tags TEXT,
    views INTEGER DEFAULT 0, favorites INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0, synced_at INTEGER,
    reviewed_at INTEGER DEFAULT NULL, is_public INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY, name TEXT, members INTEGER,
    pool_count INTEGER, synced_at INTEGER,
    description TEXT, keywords TEXT
);
CREATE TABLE IF NOT EXISTS albums (
    id TEXT PRIMARY KEY, title TEXT, description TEXT,
    primary_photo_id TEXT, count_photos INTEGER,
    count_views INTEGER, synced_at INTEGER
);
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY, username TEXT, realname TEXT,
    is_friend INTEGER DEFAULT 0, is_family INTEGER DEFAULT 0,
    synced_at INTEGER
);
CREATE TABLE IF NOT EXISTS contact_engagement (
    contact_id TEXT PRIMARY KEY, faves INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0, last_updated INTEGER
);
CREATE TABLE IF NOT EXISTS do_not_unfollow (
    contact_id TEXT PRIMARY KEY, reason TEXT, added_at INTEGER
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, synced_at INTEGER,
    mode TEXT, photos_fetched INTEGER, type TEXT DEFAULT 'photos'
);
"""

FAKE_CREDS = {
    "oauth_token": "fake_token",
    "oauth_token_secret": "fake_secret",
    "user_nsid": "99999999@N00",
}

FAKE_ENV = ("fake_api_key", "fake_api_secret")


def make_db():
    """Return an in-memory SQLite connection with the full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@pytest.fixture()
def mem_db(tmp_path) -> str:
    """Populate a temp file DB and return its path.
    File-based so handlers can close() and re-open freely without losing data."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    now = int(time.time())
    conn.execute(
        "INSERT INTO photos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("photo1", "Test Photo", "A description", "2024-01-15 12:00:00",
         now - 86400, now - 86400, "https://flickr.com/photos/x/photo1/",
         "https://live.staticflickr.com/x/photo1_orig.jpg",
         "sunset landscape", 100, 5, 2, now, None, 1),
    )
    conn.execute(
        "INSERT INTO albums VALUES (?,?,?,?,?,?,?)",
        ("album1", "My Album", "An album", "photo1", 1, 50, now),
    )
    conn.execute(
        "INSERT INTO groups VALUES (?,?,?,?,?,?,?)",
        ("group1@N00", "Landscape Lovers", 500, 1000, now, "Landscape photography", "nature"),
    )
    conn.execute(
        "INSERT INTO contacts VALUES (?,?,?,?,?,?)",
        ("contact1@N00", "jsmith", "John Smith", 0, 0, now),
    )
    conn.execute(
        "INSERT INTO sync_log VALUES (?,?,?,?,?)",
        (None, now, "incremental", 10, "photos"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def patched_server(mem_db, tmp_path):
    """
    Import mcp_tools with all external dependencies patched out.
    """
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(json.dumps(FAKE_CREDS))

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"FLICKR_API_KEY={FAKE_ENV[0]}\nFLICKR_API_SECRET={FAKE_ENV[1]}\n"
    )

    def _make_conn():
        c = sqlite3.connect(mem_db)
        c.row_factory = sqlite3.Row
        return c

    with (
        patch("flickr_api.CREDENTIALS_FILE", str(creds_file)),
        patch("flickr_api.ENV_FILE", str(env_file)),
        patch("mcp_tools.db", side_effect=_make_conn),
        patch("mcp_tools._load_credentials", return_value=FAKE_CREDS),
        patch("mcp_tools._load_env", return_value=FAKE_ENV),
    ):
        import mcp_tools as mcp
        api_get = MagicMock()
        api_post = MagicMock()
        with (
            patch.object(mcp, "_api_get", api_get),
            patch.object(mcp, "_api_post", api_post),
        ):
            yield mcp, api_get, api_post
