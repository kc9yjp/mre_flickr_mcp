"""Tests for web._build_sync_rows: the progress/ETA fields surfaced during
the AI group-summary phase of a groups sync (see mcp_tools._sync_progress)."""

import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

USERNAME = "syncrowuser"


@pytest.fixture()
def user_db(mem_db, tmp_path):
    user_dir = tmp_path / "userdata" / USERNAME
    user_dir.mkdir(parents=True)
    shutil.copy(mem_db, user_dir / "flickr.db")

    import sqlite3
    conn = sqlite3.connect(user_dir / "flickr.db")
    conn.execute(
        "INSERT INTO sync_log (synced_at, mode, photos_fetched, type, duration_seconds) VALUES (?,?,?,?,?)",
        (int(time.time()), "incremental", 1, "groups", 12),
    )
    conn.commit()
    conn.close()

    with patch("db._DATA_DIR", str(tmp_path / "userdata")):
        yield


@pytest.fixture(autouse=True)
def _clear_sync_state():
    import mcp_tools
    mcp_tools._active_syncs.clear()
    mcp_tools._sync_phase.clear()
    mcp_tools._sync_progress.clear()
    yield
    mcp_tools._active_syncs.clear()
    mcp_tools._sync_phase.clear()
    mcp_tools._sync_progress.clear()


def _groups_row(rows):
    return next(r for r in rows if r["type"] == "groups")


def test_no_progress_fields_when_idle(user_db):
    import web
    row = _groups_row(web._build_sync_rows(USERNAME))
    assert row["running"] is False
    assert row["progress_done"] is None
    assert row["progress_total"] is None
    assert row["eta_seconds"] is None


def test_no_eta_before_first_group_completes(user_db):
    """Phase has flipped to "model" but no "Summary i/n" line has been
    parsed yet — _sync_progress has no entry for the label in this window."""
    import mcp_tools
    mcp_tools._active_syncs["groups"] = time.time()
    mcp_tools._sync_phase["groups"] = "model"

    import web
    row = _groups_row(web._build_sync_rows(USERNAME))
    assert row["running"] is True
    assert row["phase"] == "model"
    assert row["progress_done"] is None
    assert row["eta_seconds"] is None


def test_eta_computed_from_observed_pace(user_db):
    import mcp_tools
    started = time.time() - 10  # 2 groups done in ~10s -> ~5s/group
    mcp_tools._active_syncs["groups"] = started
    mcp_tools._sync_phase["groups"] = "model"
    mcp_tools._sync_progress["groups"] = {"done": 2, "total": 8, "started": started}

    import web
    row = _groups_row(web._build_sync_rows(USERNAME))
    assert row["progress_done"] == 2
    assert row["progress_total"] == 8
    # 6 groups left at ~5s/group -> ~30s, generously bounded for timing slack.
    assert row["eta_seconds"] is not None
    assert 15 <= row["eta_seconds"] <= 45


def test_eta_zero_when_all_groups_done(user_db):
    import mcp_tools
    started = time.time() - 10
    mcp_tools._active_syncs["groups"] = started
    mcp_tools._sync_phase["groups"] = "model"
    mcp_tools._sync_progress["groups"] = {"done": 5, "total": 5, "started": started}

    import web
    row = _groups_row(web._build_sync_rows(USERNAME))
    assert row["eta_seconds"] == 0
