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


# ---------------------------------------------------------------------------
# _api_replace — Flickr's non-REST replace endpoint (XML, multipart upload)
# ---------------------------------------------------------------------------

def _replace_env(tmp_path, monkeypatch):
    """Point flickr_api at throwaway credentials/env for a replace call."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({
        "oauth_token": "tok", "oauth_token_secret": "sec",
        "user_nsid": "111@N00", "username": "alice",
    }))
    env = tmp_path / ".env"
    env.write_text("FLICKR_API_KEY=key\nFLICKR_API_SECRET=secret\n")
    monkeypatch.setattr(flickr_api, "CREDENTIALS_FILE", str(creds))
    monkeypatch.setattr(flickr_api, "ENV_FILE", str(env))


class _FakeResponse:
    def __init__(self, text, ok=True, status_code=200):
        self.text, self.ok, self.status_code = text, ok, status_code


def test_api_replace_signs_form_fields_and_returns_photo_id(tmp_path, monkeypatch):
    _replace_env(tmp_path, monkeypatch)
    captured = {}

    def _post(url, data=None, files=None, timeout=None):
        captured.update(url=url, data=data, files=files)
        return _FakeResponse('<rsp stat="ok"><photoid>12345</photoid></rsp>')

    monkeypatch.setattr(flickr_api.requests, "post", _post)
    photo_id = flickr_api._api_replace("12345", b"jpegbytes", "enhanced.jpg")

    assert photo_id == "12345"
    assert captured["url"] == flickr_api.REPLACE_URL
    # The binary part is excluded from the OAuth base string; only the form
    # fields are signed, and photo_id must be among them.
    assert captured["data"]["photo_id"] == "12345"
    assert captured["data"]["oauth_token"] == "tok"
    assert captured["data"]["oauth_signature"]
    assert "photo" not in captured["data"]
    assert captured["files"]["photo"][0] == "enhanced.jpg"
    assert captured["files"]["photo"][1] == b"jpegbytes"


def test_api_replace_raises_flickr_error_on_err_response(tmp_path, monkeypatch):
    import pytest

    _replace_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        flickr_api.requests, "post",
        lambda *a, **k: _FakeResponse('<rsp stat="fail"><err code="5" msg="Filetype was not recognised" /></rsp>'),
    )
    with pytest.raises(flickr_api.FlickrAPIError) as excinfo:
        flickr_api._api_replace("12345", b"notanimage")
    assert excinfo.value.code == 5
    assert excinfo.value.flickr_message == "Filetype was not recognised"


def test_api_replace_purges_cached_reads_for_that_photo(tmp_path, monkeypatch):
    """A replace rotates the photo's URL secret, so cached getSizes/getInfo
    responses for it must not survive the call."""
    _replace_env(tmp_path, monkeypatch)
    flickr_api.clear_cache()
    key = flickr_api._cache_key("111@N00", "flickr.photos.getSizes", {"photo_id": "12345"})
    flickr_api._cache_store(key, {"sizes": {"size": []}})
    assert flickr_api._cache_get(key) is not None

    monkeypatch.setattr(
        flickr_api.requests, "post",
        lambda *a, **k: _FakeResponse('<rsp stat="ok"><photoid>12345</photoid></rsp>'),
    )
    flickr_api._api_replace("12345", b"jpegbytes")
    assert flickr_api._cache_get(key) is None


def test_api_replace_rejects_unparseable_response(tmp_path, monkeypatch):
    import pytest

    _replace_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        flickr_api.requests, "post",
        lambda *a, **k: _FakeResponse("502 Bad Gateway"),
    )
    with pytest.raises(RuntimeError, match="unparseable"):
        flickr_api._api_replace("12345", b"jpegbytes")


def test_api_replace_rejects_a_non_rsp_envelope(tmp_path, monkeypatch):
    """A gateway's HTML error page can parse as XML — it must be reported as a
    transport failure, not as a Flickr error with a made-up code 0."""
    import pytest

    _replace_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        flickr_api.requests, "post",
        lambda *a, **k: _FakeResponse("<html>502 Bad Gateway</html>"),
    )
    with pytest.raises(RuntimeError, match="unexpected response"):
        flickr_api._api_replace("12345", b"jpegbytes")
