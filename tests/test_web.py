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
    os.environ["FLICKR_LOG_DIR"] = log_dir
    web = importlib.import_module("web")
    return importlib.reload(web)


def _make_app(web, routes):
    middleware = [
        Middleware(SessionMiddleware, secret_key=SECRET),
        Middleware(web.CSRFMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware)


def test_logs_route_returns_page(tmp_path):
    web = _load_web_with_log_dir(str(tmp_path))
    app = _make_app(web, [Route("/logs", endpoint=web.route_logs)])

    with TestClient(app) as client:
        response = client.get("/logs")

    assert response.status_code == 200
    assert "Logs" in response.text
    assert "No log file found yet" in response.text


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
