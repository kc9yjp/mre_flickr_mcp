"""Tests for flickr_sync helpers — no Flickr API or file-system required."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import flickr_sync


def _groups_db(row_factory=None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute(
        "CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT, "
        "description TEXT, auto_keywords TEXT)"
    )
    conn.execute(
        "INSERT INTO groups (id, name, description) VALUES "
        "('1@N', 'TREE PICS', 'Photos of trees and forests')"
    )
    return conn


class TestPopulateGroupKeywords:
    def test_works_on_bare_connection(self):
        """Regression: the sync-script path uses a connection without
        row_factory; populate_group_keywords must not index rows by name."""
        conn = _groups_db(row_factory=None)
        updated = flickr_sync.populate_group_keywords(conn)
        assert updated == 1
        kw = conn.execute("SELECT auto_keywords FROM groups WHERE id='1@N'").fetchone()[0]
        assert "tree" in kw
        assert "forests" in kw

    def test_works_with_row_factory(self):
        conn = _groups_db(row_factory=sqlite3.Row)
        updated = flickr_sync.populate_group_keywords(conn)
        assert updated == 1


class TestGenerateGroupKeywords:
    def test_splits_hyphens_and_drops_stopwords(self):
        kw = flickr_sync.generate_group_keywords("Wabi-Sabi", "The art of trees")
        words = kw.split()
        assert "wabi" in words and "sabi" in words
        assert "the art of" not in kw  # stopwords stripped
        assert "art" in words and "trees" in words
