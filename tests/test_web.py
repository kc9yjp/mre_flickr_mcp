import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

SECRET = "testsecretkey"


def _load_web_with_log_dir(log_dir: str):
    web = importlib.import_module("web")
    return importlib.reload(web)


def _make_app(web, routes):
    middleware = [
        Middleware(SessionMiddleware, secret_key=SECRET),
        Middleware(web.CSRFMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware)



def test_csrf_rejects_post_without_token(tmp_path):
    web = _load_web_with_log_dir(str(tmp_path))
    app = _make_app(web, [Route("/logout", endpoint=web.route_logout, methods=["POST"])])

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/logout", data={})

    assert response.status_code == 403
    assert "CSRF" in response.text


def test_csrf_rejects_post_with_wrong_token(tmp_path):
    web = _load_web_with_log_dir(str(tmp_path))
    app = _make_app(web, [Route("/logout", endpoint=web.route_logout, methods=["POST"])])

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/logout", data={"csrf_token": "not-the-right-token"})

    assert response.status_code == 403


def test_csrf_form_path_with_real_session_token(tmp_path):
    """Covers the form-based CSRF branch (not just the header-based /api/
    branch) with an actual session token present: a matching token succeeds,
    a mismatched token is rejected, and a form submitted with no csrf_token
    field at all is rejected without raising (the str() coercion — form
    values can be None/UploadFile — is what secrets.compare_digest needs)."""
    from starlette.responses import PlainTextResponse

    web = _load_web_with_log_dir(str(tmp_path))

    async def seed_session(request):
        request.session["csrf_token"] = "the-real-token"
        return PlainTextResponse("ok")

    app = _make_app(web, [
        Route("/seed", endpoint=seed_session),
        Route("/logout", endpoint=web.route_logout, methods=["POST"]),
    ])

    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/seed")

        wrong = client.post("/logout", data={"csrf_token": "not-the-right-token"})
        assert wrong.status_code == 403

        missing = client.post("/logout", data={})
        assert missing.status_code == 403

        ok = client.post("/logout", data={"csrf_token": "the-real-token"}, follow_redirects=False)
        assert ok.status_code == 303


def test_logout_clears_session_without_deleting_credentials(tmp_path):
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(json.dumps({"oauth_token": "tok", "oauth_token_secret": "sec"}))

    web = _load_web_with_log_dir(str(tmp_path))

    CSRF = "valid-csrf-token"

    async def seed_session(request):
        from starlette.responses import PlainTextResponse
        request.session["csrf_token"] = CSRF
        request.session["user_nsid"] = "12345@N00"
        return PlainTextResponse("ok")

    app = _make_app(web, [
        Route("/seed", endpoint=seed_session),
        Route("/logout", endpoint=web.route_logout, methods=["POST"]),
    ])

    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/seed")
        response = client.post("/logout", data={"csrf_token": CSRF}, follow_redirects=False)

    assert response.status_code in (302, 303)
    assert creds_file.exists()


def test_regen_key_rotates_api_key(tmp_path):
    import importlib
    old_key = "old-api-key-value"
    new_creds = {}

    web = _load_web_with_log_dir(str(tmp_path))
    web._api_key_registry[old_key] = "12345@N00"

    import webapi as _webapi
    webapi = importlib.reload(_webapi)

    CSRF = "valid-csrf-token"

    async def seed_session(request):
        from starlette.responses import PlainTextResponse
        request.session["csrf_token"] = CSRF
        request.session["user_nsid"] = "12345@N00"
        return PlainTextResponse("ok")

    app = _make_app(web, [
        Route("/seed", endpoint=seed_session),
        Route("/api/regen-key", endpoint=webapi.api_regen_key, methods=["POST"]),
    ])

    def fake_load_credentials(nsid=None):
        return {"mcp_api_key": old_key, "oauth_token": "tok", "oauth_token_secret": "sec"}

    def fake_save_credentials(data, nsid):
        new_creds.update(data)

    with patch("web._load_credentials", side_effect=fake_load_credentials), \
         patch("web._save_credentials", side_effect=fake_save_credentials):
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/seed")
            response = client.post(
                "/api/regen-key",
                json={},
                headers={"X-CSRF-Token": CSRF},
                follow_redirects=False,
            )

    assert response.status_code == 200
    assert response.json().get("ok") is True
    assert old_key not in web._api_key_registry
    saved_key = new_creds.get("mcp_api_key", "")
    assert saved_key and saved_key != old_key
    assert web._api_key_registry.get(saved_key) == "12345@N00"
