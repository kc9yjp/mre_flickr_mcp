"""Tests for the agent's /api/llm-* connection routes (scripts/agent/routes.py)."""

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
USERNAME = "agentroutesuser"
NSID = "12345@N00"

HEADERS = {"X-CSRF-Token": CSRF}


@pytest.fixture(autouse=True)
def _creds_dir(tmp_path):
    creds_dir = str(tmp_path / "flickr_mcp_creds")
    with patch("agent.settings._CREDS_BASE", creds_dir):
        yield


@pytest.fixture()
def client():
    import web
    from agent import routes as agent_routes

    async def seed_session(request):
        request.session["csrf_token"] = CSRF
        request.session["user_nsid"] = NSID
        request.session["username"] = USERNAME
        request.session["fullname"] = "Agent Routes User"
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[Route("/seed", endpoint=seed_session), *agent_routes.api_routes()],
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

def test_llm_settings_unauthenticated_returns_401(client):
    assert client.get("/api/llm-settings").status_code == 401


def test_llm_connections_create_unauthenticated_returns_403():
    """No session -> no CSRF token in session, so CSRFMiddleware rejects the
    POST before the route's own auth check even runs."""
    import web
    from agent import routes as agent_routes

    app = Starlette(
        routes=list(agent_routes.api_routes()),
        middleware=[
            Middleware(SessionMiddleware, secret_key=SECRET),
            Middleware(web.CSRFMiddleware),
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/llm-connections", json={"name": "x", "kind": "ollama", "base_url": "http://x/v1"})
    assert resp.status_code == 403


# --- chat_inject ---

@pytest.fixture()
def chat_data_dir(tmp_path):
    """agent.store's chat.db lives under db._DATA_DIR/{username}/chat.db."""
    with patch("db._DATA_DIR", str(tmp_path / "agentdata")):
        yield str(tmp_path / "agentdata")


def test_chat_inject_unknown_conversation_returns_404(client, chat_data_dir):
    _login(client)
    resp = client.post(
        "/api/chat/inject", headers=HEADERS,
        json={"message": "hi", "conversation_id": "doesnotexist"},
    )
    assert resp.status_code == 404


def test_chat_inject_rejects_conversation_owned_by_another_user(client, chat_data_dir):
    """A conversation_id created under a different username must be refused
    even though injection only checked the caller's OWN turn lock, not who
    the conversation_id actually belongs to."""
    from agent import store

    other_conv = store.create_conversation("someoneelse", "hello", "", "")
    _login(client)
    resp = client.post(
        "/api/chat/inject", headers=HEADERS,
        json={"message": "hi", "conversation_id": other_conv},
    )
    assert resp.status_code == 404


def test_chat_inject_known_conversation_no_running_turn_returns_409(client, chat_data_dir):
    from agent import store

    conv = store.create_conversation(USERNAME, "hello", "", "")
    _login(client)
    resp = client.post(
        "/api/chat/inject", headers=HEADERS,
        json={"message": "hi", "conversation_id": conv},
    )
    assert resp.status_code == 409


# --- per-browser-window session isolation ---

def test_chat_cancel_is_scoped_to_the_requesting_session(client):
    """/api/chat/cancel only tears down the turn belonging to the window that
    sent the request (matched by its X-Session-Id), never a sibling window's."""
    from agent import loop

    _login(client)

    class _FakeTask:
        def __init__(self):
            self.cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    task_a, task_b = _FakeTask(), _FakeTask()
    loop.register_task(USERNAME, "sess-a", task_a)
    loop.register_task(USERNAME, "sess-b", task_b)
    try:
        resp = client.post(
            "/api/chat/cancel",
            headers={**HEADERS, "X-Session-Id": "sess-a"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert task_a.cancelled is True
        assert task_b.cancelled is False
    finally:
        loop.unregister_task(USERNAME, "sess-a", task_a)
        loop.unregister_task(USERNAME, "sess-b", task_b)


def test_chat_stream_lock_is_keyed_by_conversation(client, chat_data_dir, monkeypatch):
    """A second turn on the SAME conversation — even from a different window —
    is refused (409) while one is in flight, and the running check is keyed by
    the conversation id, not the session id."""
    from agent import loop, settings, store

    conv = store.create_conversation(USERNAME, "hello", "", "")
    _login(client)

    monkeypatch.setattr(
        settings, "resolve_cfg",
        lambda *a, **k: {"model": "m", "base_url": "http://x/v1"},
    )

    calls = []

    def fake_is_turn_running(username, conversation_id=""):
        calls.append((username, conversation_id))
        return True

    monkeypatch.setattr(loop, "is_turn_running", fake_is_turn_running)

    resp = client.post(
        "/api/chat/stream",
        headers={**HEADERS, "X-Session-Id": "some-other-window"},
        json={"message": "hi", "conversation_id": conv},
    )
    assert resp.status_code == 409
    # Keyed by the conversation, not by the window's session id.
    assert calls == [(USERNAME, conv)]


def test_chat_stream_requires_message_or_images(client, chat_data_dir, monkeypatch):
    from agent import settings

    _login(client)
    monkeypatch.setattr(
        settings, "resolve_cfg",
        lambda *a, **k: {"model": "m", "base_url": "http://x/v1"},
    )
    resp = client.post("/api/chat/stream", headers=HEADERS, json={})
    assert resp.status_code == 400
    assert "message is required" in resp.json()["error"]


def test_chat_stream_image_only_message_reaches_run_turn(client, chat_data_dir, monkeypatch):
    """No typed text, just a pasted image: the request must not be rejected
    for "missing message", the invalid second entry must be filtered out
    before reaching run_turn, and the new conversation's title must fall
    back to something other than an empty string."""
    from agent import loop, settings, store

    _login(client)
    monkeypatch.setattr(
        settings, "resolve_cfg",
        lambda *a, **k: {"model": "m", "base_url": "http://x/v1"},
    )

    captured = {}

    async def fake_run_turn(user, conversation_id, message, cfg, focused_photo_id=None,
                             session_id="", resume=False, images=None):
        captured["message"] = message
        captured["images"] = images
        yield {"type": "done"}

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    data_url = "data:image/png;base64,aGVsbG8="
    resp = client.post(
        "/api/chat/stream", headers=HEADERS,
        json={"images": [data_url, "not-a-data-url"]},
    )
    assert resp.status_code == 200
    assert captured["message"] == ""
    assert captured["images"] == [data_url]

    conv = store.list_conversations(USERNAME)[0]
    assert conv["title"] == "1 image"


# --- presets ---

def test_llm_connection_presets(client):
    _login(client)
    resp = client.get("/api/llm-connection-presets")
    assert resp.status_code == 200
    presets = resp.json()["presets"]
    assert {"ollama", "zen", "lmstudio", "custom"} <= set(presets)
    assert presets["lmstudio"]["base_url"] == "http://host.docker.internal:1234/v1"


# --- create / update / delete ---

def test_create_connection_then_appears_in_settings(client):
    _login(client)
    resp = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "LM Studio", "kind": "openai_compatible",
              "base_url": "http://host.docker.internal:1234/v1", "api_mode": "chat_completions"},
    )
    assert resp.status_code == 200
    body = resp.json()
    cid = body["id"]
    assert cid == "lm-studio"
    assert body["connections"][cid]["base_url"] == "http://host.docker.internal:1234/v1"

    listed = client.get("/api/llm-settings").json()
    assert cid in listed["connections"]


def test_create_connection_missing_fields_400(client):
    _login(client)
    resp = client.post("/api/llm-connections", headers=HEADERS, json={"name": "", "kind": "", "base_url": ""})
    assert resp.status_code == 400


def test_update_connection_rename_and_disable_models(client):
    _login(client)
    created = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "Ollama 2", "kind": "ollama", "base_url": "http://host2:11434/v1"},
    ).json()
    cid = created["id"]

    updated = client.post(
        f"/api/llm-connections/{cid}/update", headers=HEADERS,
        json={"name": "Ollama Renamed", "disabled_models": ["llama2"]},
    )
    assert updated.status_code == 200
    conn = updated.json()["connections"][cid]
    assert conn["name"] == "Ollama Renamed"
    assert conn["disabled_models"] == ["llama2"]


def test_update_unknown_connection_404(client):
    _login(client)
    resp = client.post("/api/llm-connections/does-not-exist/update", headers=HEADERS, json={"name": "x"})
    assert resp.status_code == 404


def test_delete_connection_clears_active_connection(client):
    _login(client)
    created = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "Temp", "kind": "openai_compatible", "base_url": "http://x/v1"},
    ).json()
    cid = created["id"]

    saved = client.get("/api/llm-settings").json()
    saved["active_connection"] = cid
    client.post("/api/llm-settings", headers=HEADERS, json=saved)

    deleted = client.post(f"/api/llm-connections/{cid}/delete", headers=HEADERS)
    assert deleted.status_code == 200
    body = deleted.json()
    assert cid not in body["connections"]
    assert body["active_connection"] == ""


def test_delete_unknown_connection_404(client):
    _login(client)
    resp = client.post("/api/llm-connections/does-not-exist/delete", headers=HEADERS)
    assert resp.status_code == 404


# --- /api/llm-connections/{id}/model-settings ---

def test_update_model_settings_then_reads_back(client):
    _login(client)
    created = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "LM Studio", "kind": "openai_compatible", "base_url": "http://host.docker.internal:1234/v1"},
    ).json()
    cid = created["id"]

    resp = client.post(
        f"/api/llm-connections/{cid}/model-settings", headers=HEADERS,
        json={"model": "qwen/qwen3.5-9b", "vision": True, "max_tokens": 2048},
    )
    assert resp.status_code == 200
    entry = resp.json()["connections"][cid]["models"]["qwen/qwen3.5-9b"]
    assert entry["vision"] is True
    assert entry["max_tokens"] == 2048

    listed = client.get("/api/llm-settings").json()
    assert listed["connections"][cid]["models"]["qwen/qwen3.5-9b"]["vision"] is True


def test_update_model_settings_missing_model_400(client):
    _login(client)
    created = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "LM Studio", "kind": "openai_compatible", "base_url": "http://x/v1"},
    ).json()
    resp = client.post(f"/api/llm-connections/{created['id']}/model-settings", headers=HEADERS, json={"vision": True})
    assert resp.status_code == 400


def test_update_model_settings_unknown_connection_404(client):
    _login(client)
    resp = client.post(
        "/api/llm-connections/does-not-exist/model-settings", headers=HEADERS,
        json={"model": "m", "vision": True},
    )
    assert resp.status_code == 404


def test_reset_model_settings(client):
    _login(client)
    created = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "LM Studio", "kind": "openai_compatible", "base_url": "http://x/v1"},
    ).json()
    cid = created["id"]
    client.post(
        f"/api/llm-connections/{cid}/model-settings", headers=HEADERS,
        json={"model": "m", "vision": True},
    )

    resp = client.post(f"/api/llm-connections/{cid}/model-settings/reset", headers=HEADERS, json={"model": "m"})
    assert resp.status_code == 200
    assert "m" not in resp.json()["connections"][cid]["models"]


def test_reset_model_settings_unknown_connection_404(client):
    _login(client)
    resp = client.post(
        "/api/llm-connections/does-not-exist/model-settings/reset", headers=HEADERS, json={"model": "m"},
    )
    assert resp.status_code == 404


# --- /api/llm-models filtering ---

def test_llm_models_filters_disabled_models(client, monkeypatch):
    _login(client)
    created = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "Fake Ollama", "kind": "ollama", "base_url": "http://fake/v1"},
    ).json()
    cid = created["id"]
    client.post(f"/api/llm-connections/{cid}/update", headers=HEADERS, json={"disabled_models": ["b"]})

    async def fake_list_models(base_url, api_key="", client=None):
        return ["a", "b", "c"]

    monkeypatch.setattr("agent.routes.llm.list_models", fake_list_models)

    resp = client.get(f"/api/llm-models?connection={cid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"] == ["a", "c"]
    assert body["all_models"] == ["a", "b", "c"]


def test_llm_models_unknown_connection_404(client):
    _login(client)
    resp = client.get("/api/llm-models?connection=does-not-exist")
    assert resp.status_code == 404


def test_llm_models_accepts_legacy_provider_param(client, monkeypatch):
    _login(client)
    created = client.post(
        "/api/llm-connections", headers=HEADERS,
        json={"name": "Fake", "kind": "ollama", "base_url": "http://fake/v1"},
    ).json()
    cid = created["id"]

    async def fake_list_models(base_url, api_key="", client=None):
        return ["a"]

    monkeypatch.setattr("agent.routes.llm.list_models", fake_list_models)

    resp = client.get(f"/api/llm-models?provider={cid}")
    assert resp.status_code == 200
    assert resp.json()["models"] == ["a"]
