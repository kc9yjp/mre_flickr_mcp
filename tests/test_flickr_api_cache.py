"""Tests for the per-user GET response cache in flickr_api.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import flickr_api

FAKE_ENV = ("fake_api_key", "fake_api_secret")


def _creds(nsid="11111@N00"):
    return {"oauth_token": "tok", "oauth_token_secret": "toksecret", "user_nsid": nsid}


def _fake_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = payload
    return resp


@pytest.fixture(autouse=True)
def _clear_cache():
    flickr_api.clear_cache()
    yield
    flickr_api.clear_cache()


@pytest.fixture(autouse=True)
def _env_and_creds():
    with (
        patch("flickr_api._load_env", return_value=FAKE_ENV),
        patch("flickr_api._load_credentials", return_value=_creds()),
    ):
        yield


class TestGetCaching:
    def test_second_identical_get_hits_cache(self):
        payload = {"stat": "ok", "photo": {"id": "123"}}
        with patch("requests.get", return_value=_fake_response(payload)) as mock_get:
            first = flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
            second = flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
        assert first == payload
        assert second == payload
        mock_get.assert_called_once()

    def test_different_params_are_not_cached_together(self):
        payload = {"stat": "ok", "photo": {"id": "123"}}
        with patch("requests.get", return_value=_fake_response(payload)) as mock_get:
            flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
            flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "456"})
        assert mock_get.call_count == 2

    def test_cache_expires_after_ttl(self):
        payload = {"stat": "ok", "photo": {"id": "123"}}
        with patch("requests.get", return_value=_fake_response(payload)) as mock_get:
            flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
            with patch("time.time", return_value=__import__("time").time() + flickr_api.CACHE_TTL + 1):
                flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
        assert mock_get.call_count == 2

    def test_separate_users_do_not_share_cache(self):
        payload = {"stat": "ok", "photo": {"id": "123"}}
        with patch("requests.get", return_value=_fake_response(payload)) as mock_get:
            with patch("flickr_api._load_credentials", return_value=_creds("11111@N00")):
                flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
            with patch("flickr_api._load_credentials", return_value=_creds("22222@N00")):
                flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
        assert mock_get.call_count == 2


class TestPostPurgesCache:
    def test_write_purges_cached_get_for_same_photo_id(self):
        get_payload = {"stat": "ok", "photo": {"id": "123", "title": "old"}}
        post_payload = {"stat": "ok"}
        with (
            patch("requests.get", return_value=_fake_response(get_payload)) as mock_get,
            patch("requests.post", return_value=_fake_response(post_payload)),
        ):
            flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
            flickr_api._api_post("flickr.photos.setMeta", {"photo_id": "123", "title": "new"})
            flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "123"})
        assert mock_get.call_count == 2

    def test_write_does_not_purge_unrelated_photo(self):
        get_payload = {"stat": "ok", "photo": {"id": "456"}}
        post_payload = {"stat": "ok"}
        with (
            patch("requests.get", return_value=_fake_response(get_payload)) as mock_get,
            patch("requests.post", return_value=_fake_response(post_payload)),
        ):
            flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "456"})
            flickr_api._api_post("flickr.photos.setMeta", {"photo_id": "123", "title": "new"})
            flickr_api._api_get("flickr.photos.getInfo", {"photo_id": "456"})
        assert mock_get.call_count == 1
