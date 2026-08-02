"""Tests for flickr_api._resolve_api_key (stdio transport's MCP_API_KEY lookup)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import flickr_api


def _write_creds(creds_base: Path, nsid: str, username: str, mcp_api_key: str) -> None:
    user_dir = creds_base / nsid
    user_dir.mkdir(parents=True)
    (user_dir / "credentials.json").write_text(json.dumps({
        "oauth_token": "tok",
        "oauth_token_secret": "sec",
        "user_nsid": nsid,
        "username": username,
        "mcp_api_key": mcp_api_key,
    }))


def test_resolve_api_key_matches_correct_key(tmp_path):
    _write_creds(tmp_path, "111@N00", "alice", "correct-key")
    with patch.object(flickr_api, "_CREDS_BASE", str(tmp_path)):
        user = flickr_api._resolve_api_key("correct-key")
    assert user == {"nsid": "111@N00", "username": "alice"}


def test_resolve_api_key_rejects_wrong_key(tmp_path):
    _write_creds(tmp_path, "111@N00", "alice", "correct-key")
    with patch.object(flickr_api, "_CREDS_BASE", str(tmp_path)):
        assert flickr_api._resolve_api_key("wrong-key") is None


def test_resolve_api_key_rejects_empty_key(tmp_path):
    """An empty MCP_API_KEY must never match a user with no key set (or an
    empty-string stored key) — secrets.compare_digest requires matching
    types/non-empty comparands to be meaningful, not just any falsy match."""
    _write_creds(tmp_path, "111@N00", "alice", "")
    with patch.object(flickr_api, "_CREDS_BASE", str(tmp_path)):
        assert flickr_api._resolve_api_key("") is None


def test_resolve_api_key_picks_right_user_among_several(tmp_path):
    _write_creds(tmp_path, "111@N00", "alice", "alice-key")
    _write_creds(tmp_path, "222@N00", "bob", "bob-key")
    with patch.object(flickr_api, "_CREDS_BASE", str(tmp_path)):
        assert flickr_api._resolve_api_key("bob-key") == {"nsid": "222@N00", "username": "bob"}
        assert flickr_api._resolve_api_key("alice-key") == {"nsid": "111@N00", "username": "alice"}
