"""Tests for scripts/db.py's per-user directory ownership guard.

Flickr usernames are mutable (renameable) and can be reused after being
released, unlike the immutable NSID. Since per-user data lives at
data/{username}/flickr.db, these tests cover the guard that stops a
renamed/reused username from silently resolving to a different Flickr
account's cached data (see check_dir_ownership / seed_dir_ownership).
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import db


# --- _validate_username ---

@pytest.mark.parametrize("username", [
    "plainuser", "user.name", "user-name", "user_name123",
    "Jane Doe", "caféuser", "a" * 128,
])
def test_validate_username_accepts_realistic_flickr_names(username):
    assert db._validate_username(username) == username


@pytest.mark.parametrize("username", [
    "", ".", "..", "a/b", "a\\b", "../escape", "null\x00byte", "a" * 129,
])
def test_validate_username_rejects_unsafe_or_invalid(username):
    with pytest.raises(ValueError):
        db._validate_username(username)


def test_db_file_rejects_unsafe_username(tmp_path):
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        with pytest.raises(ValueError):
            db.db_file("../escape")


# --- check_dir_ownership / seed_dir_ownership ---

def test_check_dir_ownership_noop_when_nsid_unknown(tmp_path):
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        db.check_dir_ownership("someuser", None)  # must not raise


def test_check_dir_ownership_noop_when_no_marker_yet(tmp_path):
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        (tmp_path / "someuser").mkdir()
        db.check_dir_ownership("someuser", "111@N00")  # must not raise


def test_seed_then_check_same_nsid_passes(tmp_path):
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        (tmp_path / "someuser").mkdir()
        db.seed_dir_ownership("someuser", "111@N00")
        db.check_dir_ownership("someuser", "111@N00")  # must not raise


def test_seed_is_idempotent_first_writer_wins(tmp_path):
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        (tmp_path / "someuser").mkdir()
        db.seed_dir_ownership("someuser", "111@N00")
        db.seed_dir_ownership("someuser", "222@N00")  # later seed does not overwrite
        db.check_dir_ownership("someuser", "111@N00")
        with pytest.raises(RuntimeError):
            db.check_dir_ownership("someuser", "222@N00")


def test_check_dir_ownership_rejects_different_nsid(tmp_path):
    """A username reused/renamed across two different Flickr accounts must
    not silently resolve to the first account's data directory."""
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        (tmp_path / "someuser").mkdir()
        db.seed_dir_ownership("someuser", "111@N00")
        with pytest.raises(RuntimeError, match="different"):
            db.check_dir_ownership("someuser", "222@N00")


# --- get_db_for_user integration ---

def test_get_db_for_user_seeds_ownership_on_first_use(tmp_path):
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        with db.get_db_for_user("newuser", "111@N00") as conn:
            conn.execute("SELECT 1")
        marker = tmp_path / "newuser" / ".owner_nsid"
        assert marker.read_text().strip() == "111@N00"


def test_get_db_for_user_refuses_colliding_nsid(tmp_path):
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        with db.get_db_for_user("newuser", "111@N00") as conn:
            conn.execute("SELECT 1")
        with pytest.raises(RuntimeError):
            with db.get_db_for_user("newuser", "222@N00"):
                pass


def test_get_db_for_user_falls_back_to_current_user_context(tmp_path):
    """When nsid isn't passed explicitly, it's read from _current_user if
    that context's username matches — closing the gap for call sites not
    yet threading nsid through explicitly."""
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        token = db._current_user.set({"nsid": "111@N00", "username": "ctxuser"})
        try:
            with db.get_db_for_user("ctxuser") as conn:
                conn.execute("SELECT 1")
        finally:
            db._current_user.reset(token)
        marker = tmp_path / "ctxuser" / ".owner_nsid"
        assert marker.read_text().strip() == "111@N00"

        token = db._current_user.set({"nsid": "222@N00", "username": "ctxuser"})
        try:
            with pytest.raises(RuntimeError):
                with db.get_db_for_user("ctxuser"):
                    pass
        finally:
            db._current_user.reset(token)


def test_get_db_for_user_without_nsid_context_is_unchecked(tmp_path):
    """No nsid available anywhere (legacy/test call sites): behaves exactly
    as before this guard existed — no exception, no marker written."""
    with patch.object(db, "_DATA_DIR", str(tmp_path)):
        with db.get_db_for_user("plainuser") as conn:
            conn.execute("SELECT 1")
        with db.get_db_for_user("plainuser") as conn:
            conn.execute("SELECT 1")
        assert not (tmp_path / "plainuser" / ".owner_nsid").exists()
