"""Tests for oauth2.py — the OAuth 2.1 authorization server that lets
Desktop/claude.ai-style connectors authorize themselves against /sse and
/mcp, alongside (not instead of) the static mcp_api_key path covered in
test_web.py."""

import base64
import hashlib
import importlib
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import web  # noqa: E402  (CSRFMiddleware / oauth2.NO_CSRF_PATHS reuse)

SECRET = "testsecretkey"
REDIRECT_URI = "https://client.example/callback"


def _load_oauth2(tmp_path):
    """Fresh oauth2 module instance, storage redirected under tmp_path."""
    import oauth2
    oauth2 = importlib.reload(oauth2)
    oauth2._CREDS_BASE = str(tmp_path)
    oauth2._CLIENTS_FILE = str(tmp_path / "oauth_clients.json")
    return oauth2


def _register_client(oauth2, redirect_uri=REDIRECT_URI, auth_method="none", name="Test Client"):
    client_id = "client-" + secrets.token_hex(4)
    record = {
        "client_id": client_id,
        "client_name": name,
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": auth_method,
        "created_at": time.time(),
    }
    if auth_method == "client_secret_post":
        record["client_secret"] = "shh"
    clients = oauth2._load_clients()
    clients[client_id] = record
    oauth2._save_clients(clients)
    return client_id


def _pkce_pair():
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def _seed_session(request):
    request.session["csrf_token"] = "test-csrf"
    request.session["user_nsid"] = "111@N00"
    request.session["username"] = "alice"
    return PlainTextResponse("ok")


async def _check_session(request):
    return JSONResponse(dict(request.session))


def _make_app(oauth2):
    middleware = [
        Middleware(SessionMiddleware, secret_key=SECRET),
        Middleware(web.CSRFMiddleware),
    ]
    routes = [
        Route("/seed", endpoint=_seed_session),
        Route("/check-session", endpoint=_check_session),
        *oauth2.oauth_routes(),
    ]
    return Starlette(routes=routes, middleware=middleware)


@pytest.fixture()
def oauth_env(tmp_path):
    oauth2 = _load_oauth2(tmp_path)
    app = _make_app(oauth2)
    client_id = _register_client(oauth2)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield oauth2, client, client_id


def _get_code(client, client_id, challenge, state="xyz"):
    resp = client.post("/oauth/authorize", data={
        "csrf_token": "test-csrf",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "",
        "decision": "allow",
    }, follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith(REDIRECT_URI)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    assert qs["state"] == [state]
    return qs["code"][0]


def _exchange_code(client, client_id, code, verifier):
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })


# --- PKCE ---------------------------------------------------------------

def test_verify_pkce():
    import oauth2 as oauth2_mod
    verifier, challenge = _pkce_pair()
    assert oauth2_mod._verify_pkce(verifier, challenge) is True
    assert oauth2_mod._verify_pkce("wrong-verifier", challenge) is False
    assert oauth2_mod._verify_pkce("", challenge) is False
    assert oauth2_mod._verify_pkce(verifier, "") is False


# --- Discovery ------------------------------------------------------------

def test_protected_resource_metadata(oauth_env):
    _oauth2, client, _client_id = oauth_env
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorization_servers"] == [body["resource"]]


def test_authorization_server_metadata(oauth_env):
    _oauth2, client, _client_id = oauth_env
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert body["token_endpoint"].endswith("/oauth/token")
    assert body["registration_endpoint"].endswith("/oauth/register")
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]


# --- Dynamic client registration ------------------------------------------

def test_register_client_success(oauth_env):
    _oauth2, client, _existing_client_id = oauth_env
    resp = client.post("/oauth/register", json={
        "redirect_uris": ["https://new-client.example/cb"],
        "client_name": "New Client",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["client_id"]
    assert body["redirect_uris"] == ["https://new-client.example/cb"]
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body


def test_register_rejects_missing_redirect_uris(oauth_env):
    _oauth2, client, _client_id = oauth_env
    resp = client.post("/oauth/register", json={"client_name": "Bad"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"


def test_register_confidential_client_gets_secret(oauth_env):
    _oauth2, client, _client_id = oauth_env
    resp = client.post("/oauth/register", json={
        "redirect_uris": ["https://confidential.example/cb"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    assert resp.status_code == 201
    assert resp.json()["client_secret"]


def test_register_enforces_capacity_limit(oauth_env):
    oauth2, client, _client_id = oauth_env
    oauth2._MAX_CLIENTS = 1  # the fixture already registered one client
    resp = client.post("/oauth/register", json={"redirect_uris": ["https://x.example/cb"]})
    assert resp.status_code == 429


# --- Authorize + consent ---------------------------------------------------

def test_authorize_requires_pkce(oauth_env):
    _oauth2, client, client_id = oauth_env
    client.get("/seed")
    resp = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


def test_authorize_rejects_unknown_client(oauth_env):
    _oauth2, client, _client_id = oauth_env
    client.get("/seed")
    _verifier, challenge = _pkce_pair()
    resp = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": "no-such-client", "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client"


def test_authorize_rejects_unregistered_redirect_uri(oauth_env):
    _oauth2, client, client_id = oauth_env
    client.get("/seed")
    _verifier, challenge = _pkce_pair()
    resp = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id,
        "redirect_uri": "https://attacker.example/cb",
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


def test_authorize_redirects_to_login_when_not_authenticated(oauth_env):
    _oauth2, client, client_id = oauth_env
    _verifier, challenge = _pkce_pair()
    resp = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"

    session = client.get("/check-session").json()
    assert session["post_login_redirect"].startswith("/oauth/authorize?")
    assert f"client_id={client_id}" in session["post_login_redirect"]


def test_authorize_shows_consent_page_when_logged_in(oauth_env):
    _oauth2, client, client_id = oauth_env
    client.get("/seed")
    _verifier, challenge = _pkce_pair()
    resp = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
    })
    assert resp.status_code == 200
    assert "Test Client" in resp.text
    assert "alice" in resp.text


def test_authorize_deny_redirects_with_error(oauth_env):
    _oauth2, client, client_id = oauth_env
    client.get("/seed")
    _verifier, challenge = _pkce_pair()
    resp = client.post("/oauth/authorize", data={
        "csrf_token": "test-csrf", "client_id": client_id, "redirect_uri": REDIRECT_URI,
        "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256",
        "scope": "", "decision": "deny",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith(REDIRECT_URI)
    assert "error=access_denied" in resp.headers["location"]


def test_authorize_allow_requires_csrf_token(oauth_env):
    _oauth2, client, client_id = oauth_env
    client.get("/seed")
    _verifier, challenge = _pkce_pair()
    resp = client.post("/oauth/authorize", data={
        "csrf_token": "wrong-token", "client_id": client_id, "redirect_uri": REDIRECT_URI,
        "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256",
        "scope": "", "decision": "allow",
    })
    assert resp.status_code == 403


# --- Token exchange ---------------------------------------------------------

def test_token_exchange_issues_working_access_token(oauth_env):
    oauth2, client, client_id = oauth_env
    client.get("/seed")
    verifier, challenge = _pkce_pair()
    code = _get_code(client, client_id, challenge)

    resp = _exchange_code(client, client_id, code, verifier)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert oauth2.resolve_access_token(body["access_token"]) == "111@N00"


def test_token_exchange_code_is_single_use(oauth_env):
    _oauth2, client, client_id = oauth_env
    client.get("/seed")
    verifier, challenge = _pkce_pair()
    code = _get_code(client, client_id, challenge)

    first = _exchange_code(client, client_id, code, verifier)
    assert first.status_code == 200
    second = _exchange_code(client, client_id, code, verifier)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_token_exchange_rejects_wrong_code_verifier(oauth_env):
    _oauth2, client, client_id = oauth_env
    client.get("/seed")
    verifier, challenge = _pkce_pair()
    code = _get_code(client, client_id, challenge)

    resp = _exchange_code(client, client_id, code, "not-" + verifier)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_refresh_token_issues_new_access_token(oauth_env):
    oauth2, client, client_id = oauth_env
    client.get("/seed")
    verifier, challenge = _pkce_pair()
    code = _get_code(client, client_id, challenge)
    tokens = _exchange_code(client, client_id, code, verifier).json()

    resp = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_id,
    })
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["access_token"] != tokens["access_token"]
    assert oauth2.resolve_access_token(tokens["access_token"]) is None
    assert oauth2.resolve_access_token(new_tokens["access_token"]) == "111@N00"


def test_confidential_client_requires_correct_secret(oauth_env):
    oauth2, client, _default_client_id = oauth_env
    client.get("/seed")
    client_id = _register_client(oauth2, redirect_uri=REDIRECT_URI, auth_method="client_secret_post")
    verifier, challenge = _pkce_pair()
    code = _get_code(client, client_id, challenge)

    wrong = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": REDIRECT_URI, "code_verifier": verifier, "client_secret": "nope",
    })
    assert wrong.status_code == 401


# --- Revocation --------------------------------------------------------------

def test_revoke_invalidates_access_token(oauth_env):
    oauth2, client, client_id = oauth_env
    client.get("/seed")
    verifier, challenge = _pkce_pair()
    code = _get_code(client, client_id, challenge)
    tokens = _exchange_code(client, client_id, code, verifier).json()

    assert oauth2.resolve_access_token(tokens["access_token"]) == "111@N00"
    resp = client.post("/oauth/revoke", data={"token": tokens["access_token"]})
    assert resp.status_code == 200
    assert oauth2.resolve_access_token(tokens["access_token"]) is None


def test_revoke_unknown_token_still_returns_200(oauth_env):
    _oauth2, client, _client_id = oauth_env
    resp = client.post("/oauth/revoke", data={"token": "never-issued"})
    assert resp.status_code == 200


# --- ApiKeyMiddleware integration --------------------------------------------

def test_apikey_middleware_accepts_oauth_access_token(tmp_path):
    oauth2 = _load_oauth2(tmp_path)
    web_mod = importlib.reload(web)
    web_mod._api_key_registry.clear()
    oauth2._token_registry["good-oauth-token"] = ("222@N00", time.time() + 3600)

    async def stub_mcp(request):
        return PlainTextResponse(f"user={request.state.user_nsid}")

    app = Starlette(
        routes=[Route("/mcp", endpoint=stub_mcp)],
        middleware=[Middleware(web_mod.ApiKeyMiddleware)],
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        ok = client.get("/mcp", headers={"Authorization": "Bearer good-oauth-token"})
        assert ok.status_code == 200
        assert ok.text == "user=222@N00"

        bad = client.get("/mcp", headers={"Authorization": "Bearer bad-token"})
        assert bad.status_code == 401
        assert "resource_metadata" in bad.headers.get("www-authenticate", "")


def test_apikey_middleware_rejects_expired_oauth_token(tmp_path):
    oauth2 = _load_oauth2(tmp_path)
    web_mod = importlib.reload(web)
    web_mod._api_key_registry.clear()
    oauth2._token_registry["stale-token"] = ("222@N00", time.time() - 1)

    async def stub_mcp(request):
        return PlainTextResponse(f"user={request.state.user_nsid}")

    app = Starlette(
        routes=[Route("/mcp", endpoint=stub_mcp)],
        middleware=[Middleware(web_mod.ApiKeyMiddleware)],
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/mcp", headers={"Authorization": "Bearer stale-token"})
        assert resp.status_code == 401
