import importlib
import os
import sys
from pathlib import Path
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def _load_web_with_log_dir(log_dir: str):
    os.environ["FLICKR_LOG_DIR"] = log_dir
    web = importlib.import_module("web")
    return importlib.reload(web)


def test_logs_route_returns_page(tmp_path):
    web = _load_web_with_log_dir(str(tmp_path))
    from starlette.middleware import Middleware
    from starlette.middleware.sessions import SessionMiddleware
    from scripts.web import CSRFMiddleware, SESSION_SECRET_KEY

    middleware = [
        Middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY),
        Middleware(CSRFMiddleware),
    ]
    app = Starlette(
        routes=[Route("/logs", endpoint=web.route_logs)],
        middleware=middleware
    )

    with TestClient(app) as client:
        response = client.get("/logs")

    assert response.status_code == 200
    assert "Logs" in response.text
    assert "No log file found yet" in response.text
