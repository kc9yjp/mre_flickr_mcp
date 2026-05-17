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
    app = Starlette(routes=[Route("/logs", endpoint=web.route_logs)])

    with TestClient(app) as client:
        response = client.get("/logs")

    assert response.status_code == 200
    assert "Logs" in response.text
    assert "No log file found yet" in response.text
