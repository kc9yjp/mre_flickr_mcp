"""Tests for the /api JSON endpoints (scripts/webapi.py)."""

import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

SECRET = "testsecretkey"
CSRF = "valid-csrf-token"
USERNAME = "apitestuser"
NSID = "12345@N00"


@pytest.fixture()
def user_db(mem_db, tmp_path):
    """Place the seeded test DB at the per-user path and point db._DATA_DIR at it."""
    user_dir = tmp_path / "userdata" / USERNAME
    user_dir.mkdir(parents=True)
    shutil.copy(mem_db, user_dir / "flickr.db")

    import sqlite3
    conn = sqlite3.connect(user_dir / "flickr.db")
    conn.execute("INSERT INTO photo_groups VALUES ('photo1', 'group1@N00')")
    conn.commit()
    conn.close()

    with patch("db._DATA_DIR", str(tmp_path / "userdata")):
        yield


@pytest.fixture()
def client(user_db):
    """TestClient over the /api routes with session + CSRF middleware."""
    import web
    import webapi

    async def seed_session(request):
        request.session["csrf_token"] = CSRF
        request.session["user_nsid"] = NSID
        request.session["username"] = USERNAME
        request.session["fullname"] = "API Test User"
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[Route("/seed", endpoint=seed_session), *webapi.api_routes()],
        middleware=[
            Middleware(SessionMiddleware, secret_key=SECRET),
            Middleware(web.CSRFMiddleware),
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _login(client):
    client.get("/seed")


# --- auth ---

def test_api_me_unauthenticated_returns_401(client):
    assert client.get("/api/me").status_code == 401


def test_api_photos_unauthenticated_returns_401(client):
    assert client.get("/api/photos").status_code == 401


def test_api_me_returns_session_user(client):
    _login(client)
    data = client.get("/api/me").json()
    assert data["username"] == USERNAME
    assert data["nsid"] == NSID
    assert data["csrf_token"] == CSRF


# --- CSRF ---

def test_api_post_without_csrf_header_rejected(client):
    _login(client)
    response = client.post("/api/sync/photos")
    assert response.status_code == 403
    assert "CSRF" in response.json()["error"]


def test_api_post_with_wrong_csrf_header_rejected(client):
    _login(client)
    response = client.post("/api/sync/photos", headers={"X-CSRF-Token": "wrong"})
    assert response.status_code == 403


# --- photos ---

def test_api_photos_lists_seeded_photo(client):
    _login(client)
    data = client.get("/api/photos").json()
    assert data["total"] == 1
    assert data["photos"][0]["id"] == "photo1"
    assert data["photos"][0]["url_medium"].endswith("photo1_z.jpg")


def test_api_photos_query_filter(client):
    _login(client)
    assert client.get("/api/photos", params={"query": "Test"}).json()["total"] == 1
    assert client.get("/api/photos", params={"query": "nomatch"}).json()["total"] == 0


def test_api_photos_tag_filter(client):
    _login(client)
    assert client.get("/api/photos", params={"tags": "sunset"}).json()["total"] == 1
    assert client.get("/api/photos", params={"tags": "portrait"}).json()["total"] == 0


def test_api_photos_pagination_offset_past_end(client):
    _login(client)
    data = client.get("/api/photos", params={"offset": 5}).json()
    assert data["total"] == 1
    assert data["photos"] == []


def test_api_photos_bad_limit_returns_400(client):
    _login(client)
    assert client.get("/api/photos", params={"limit": "abc"}).status_code == 400


def test_api_photos_by_ids_returns_matching_photos_in_order(client):
    _login(client)
    data = client.get("/api/photos", params={"ids": "photo1,doesnotexist"}).json()
    assert data["total"] == 1
    assert [p["id"] for p in data["photos"]] == ["photo1"]


def test_api_photos_by_ids_ignores_other_filters(client):
    _login(client)
    # "ids" takes an early-return path — other filter params should be ignored.
    data = client.get("/api/photos", params={"ids": "photo1", "query": "nomatch"}).json()
    assert data["total"] == 1


def test_api_photo_detail_includes_groups_and_keeper_flag(client):
    _login(client)
    data = client.get("/api/photos/photo1").json()
    assert data["id"] == "photo1"
    assert data["groups"] == [{"id": "group1@N00", "name": "Landscape Lovers"}]
    assert data["in_keeper_list"] is False


def test_api_photo_detail_missing_returns_404(client):
    _login(client)
    import flickr_api
    with patch("webapi.flickr_api._api_get", side_effect=flickr_api.FlickrAPIError(1, "Photo not found")):
        assert client.get("/api/photos/doesnotexist").status_code == 404


def _fake_external_api_get(method, extra=None):
    if method == "flickr.photos.getInfo":
        return {
            "photo": {
                "id": "9999",
                "owner": {"nsid": "55@N00", "username": "someoneelse", "realname": "Some One"},
                "title": {"_content": "Cool Photo"},
                "description": {"_content": "A description"},
                "dates": {"taken": "2020-01-01 00:00:00", "posted": "1600000000", "lastupdate": "1600000001"},
                "views": "42",
                "comments": {"_content": "3"},
                "visibility": {"ispublic": 1},
                "tags": {"tag": [{"raw": "sunset"}, {"raw": "lake"}]},
            }
        }
    if method == "flickr.photos.getSizes":
        return {"sizes": {"size": [
            {"label": "Medium", "source": "https://example.com/medium.jpg"},
            {"label": "Large", "source": "https://example.com/large.jpg"},
        ]}}
    if method == "flickr.photos.getFavorites":
        return {"photo": {"total": "7"}}
    raise AssertionError(f"unexpected method {method}")


def test_api_photo_detail_external_photo_fetched_from_flickr(client):
    _login(client)
    with patch("webapi.flickr_api._api_get", side_effect=_fake_external_api_get):
        resp = client.get("/api/photos/9999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_own"] is False
    assert data["title"] == "Cool Photo"
    assert data["favorites"] == 7
    assert data["comments"] == 3
    assert data["tags"] == "sunset lake"
    assert data["owner"]["username"] == "someoneelse"
    assert data["url_original"] == "https://example.com/large.jpg"
    assert data["url_photopage"] == "https://www.flickr.com/photos/55@N00/9999/"


def test_api_photo_fave_posts_to_flickr(client):
    _login(client)
    with patch("webapi.flickr_api._api_post") as mock_post:
        mock_post.return_value = {"stat": "ok"}
        resp = client.post("/api/photos/9999/fave", headers={"X-CSRF-Token": CSRF})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_post.assert_called_once_with("flickr.favorites.add", {"photo_id": "9999"})


def test_api_photo_comment_posts_to_flickr(client):
    _login(client)
    with patch("webapi.flickr_api._api_post") as mock_post:
        mock_post.return_value = {"comment": {"id": "abc123"}}
        resp = client.post(
            "/api/photos/9999/comment",
            json={"comment_text": "Nice shot!"},
            headers={"X-CSRF-Token": CSRF},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["comment_id"] == "abc123"
    mock_post.assert_called_once_with(
        "flickr.photos.comments.addComment",
        {"photo_id": "9999", "comment_text": "Nice shot!"},
    )


def test_api_photo_comment_requires_text(client):
    _login(client)
    resp = client.post("/api/photos/9999/comment", json={}, headers={"X-CSRF-Token": CSRF})
    assert resp.status_code == 400


# --- stats ---

def test_api_stats_returns_counts(client):
    _login(client)
    data = client.get("/api/stats").json()
    assert data["total_photos"] == 1
    assert data["public_photos"] == 1
    assert data["total_views"] == 100
    assert data["total_groups"] == 1
    assert data["total_albums"] == 1
    assert data["total_contacts"] == 1
    assert {"tag": "sunset", "count": 1} in data["top_tags"]


# --- sync ---

def test_api_sync_status_reports_last_sync(client):
    _login(client)
    data = client.get("/api/sync/status").json()
    assert data["running"] is False
    types = {r["type"] for r in data["rows"]}
    assert "photos" in types


def test_api_sync_trigger_starts_sync(client):
    _login(client)
    with patch("web._start_sync", return_value=True) as start:
        response = client.post("/api/sync/photos", headers={"X-CSRF-Token": CSRF})
    assert response.status_code == 200
    assert response.json()["started"] is True
    start.assert_called_once_with("photos", USERNAME, NSID, full=False)


def test_api_sync_trigger_full_flag(client):
    _login(client)
    with patch("web._start_sync", return_value=True) as start:
        client.post("/api/sync/photos?full=1", headers={"X-CSRF-Token": CSRF})
    start.assert_called_once_with("photos", USERNAME, NSID, full=True)


def test_api_sync_trigger_unknown_type_returns_400(client):
    _login(client)
    response = client.post("/api/sync/bogus", headers={"X-CSRF-Token": CSRF})
    assert response.status_code == 400


def test_api_sync_trigger_busy_returns_409(client):
    _login(client)
    import webapi

    class FakeLock:
        def locked(self):
            return True

    with patch.object(webapi, "_get_user_lock", return_value=FakeLock()):
        response = client.post("/api/sync/photos", headers={"X-CSRF-Token": CSRF})
    assert response.status_code == 409
    assert response.json()["started"] is False


# --- no database yet ---

def test_api_photos_no_db_returns_404(client, tmp_path):
    _login(client)
    with patch("db._DATA_DIR", str(tmp_path / "empty")):
        response = client.get("/api/photos")
    assert response.status_code == 404
    assert "sync" in response.json()["error"]
