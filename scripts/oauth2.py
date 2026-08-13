"""OAuth 2.1 authorization server for MCP client connectors.

This is a *second*, separate auth layer from ``flickr_api.py``'s OAuth 1.0a
(which authorizes this server to call the Flickr API on a user's behalf).
This one authorizes MCP *clients* — Claude Desktop, claude.ai's "Add custom
connector" dialog — to call this server's own ``/sse``/``/mcp`` endpoints,
which today only understand the static per-user ``mcp_api_key`` bearer token
pasted into ``.mcp.json`` (see ``web.py``'s ``ApiKeyMiddleware``). Desktop and
claude.ai don't support pasting a token; they expect the server to run the
standard discovery -> dynamic registration -> browser consent -> token dance.

Hand-rolled (opaque bearer tokens, no JWT/JWK) to match this project's
existing "no third-party OAuth library" convention (see ``flickr_api.py``'s
OAuth 1.0a signing). PKCE (S256) is mandatory on every authorization request,
per the MCP authorization spec / OAuth 2.1 — plain PKCE and PKCE-less
authorization-code requests are both rejected.

The static ``mcp_api_key`` path is untouched: this is purely additive. A
connector authorized here gets its own access/refresh token pair, independent
of that key and of any other connector the same user has authorized.

Storage
-------
* ``~/.flickr_mcp/oauth_clients.json`` — server-wide, one entry per
  dynamically-registered client (RFC 7591). Not tied to any Flickr user;
  registration happens before any login, from the connector itself.
* ``~/.flickr_mcp/{nsid}/oauth_tokens.json`` — per Flickr user, one entry per
  authorized client. A user can independently authorize several clients
  (Desktop, claude.ai web, ...) without disturbing each other or the static
  ``mcp_api_key``.
* ``_pending_codes`` (in-memory) — authorization codes, short TTL, same
  expiry-sweep pattern as ``web._pending_oauth``.
* ``_token_registry`` (in-memory) — access_token -> (nsid, expires_at),
  mirroring ``web._api_key_registry`` so ``ApiKeyMiddleware`` can resolve an
  OAuth bearer token as cheaply as a static one. Rebuilt at startup and kept
  in sync on every issuance/refresh/revocation.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from flickr_api import _CREDS_BASE

_CLIENTS_FILE = os.path.join(_CREDS_BASE, "oauth_clients.json")

_ACCESS_TOKEN_TTL = 3600              # 1 hour
_REFRESH_TOKEN_TTL = 30 * 24 * 3600   # 30 days, matches the session cookie's max_age
_AUTH_CODE_TTL = 60                   # seconds an authorization code stays redeemable
_PENDING_CODE_MAX = 200               # hard cap on concurrent in-flight authorize requests
_MAX_CLIENTS = 200                    # hard cap on registered clients

_clients_lock = threading.Lock()
_tokens_lock = threading.Lock()

_pending_codes: dict[str, dict] = {}   # code -> {client_id, nsid, redirect_uri, code_challenge, scope, created_at}
_token_registry: dict[str, tuple[str, float]] = {}   # access_token -> (nsid, expires_at)


# ---------------------------------------------------------------------------
# Client storage (RFC 7591 registrations)
# ---------------------------------------------------------------------------

def _load_clients() -> dict:
    if not os.path.exists(_CLIENTS_FILE):
        return {}
    try:
        with open(_CLIENTS_FILE) as f:
            return json.load(f)
    except Exception as e:
        logging.error("oauth2: failed to read %s: %s", _CLIENTS_FILE, e)
        return {}


def _save_clients(clients: dict) -> None:
    os.makedirs(_CREDS_BASE, mode=0o700, exist_ok=True)
    with os.fdopen(os.open(_CLIENTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(clients, f, indent=2)


# ---------------------------------------------------------------------------
# Token storage (per Flickr user)
# ---------------------------------------------------------------------------

def _tokens_file(nsid: str) -> str:
    return os.path.join(_CREDS_BASE, nsid, "oauth_tokens.json")


def _load_tokens(nsid: str) -> list[dict]:
    path = _tokens_file(nsid)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logging.error("oauth2: failed to read %s: %s", path, e)
        return []


def _save_tokens(nsid: str, tokens: list[dict]) -> None:
    path = _tokens_file(nsid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(tokens, f, indent=2)


def load_token_registry() -> None:
    """Populate the in-memory access-token registry from every user's
    ``oauth_tokens.json``. Called at startup and after each issuance/refresh/
    revocation so concurrent SSE connections never see a stale view — mirrors
    ``web._load_api_key_registry``."""
    new_registry: dict[str, tuple[str, float]] = {}
    if os.path.isdir(_CREDS_BASE):
        for entry in os.scandir(_CREDS_BASE):
            if not entry.is_dir():
                continue
            for tok in _load_tokens(entry.name):
                at = tok.get("access_token")
                exp = tok.get("access_expires_at")
                if at and exp:
                    new_registry[at] = (entry.name, exp)
    _token_registry.clear()
    _token_registry.update(new_registry)
    logging.debug("OAuth token registry: %d token(s) loaded", len(_token_registry))


def resolve_access_token(token: str) -> str | None:
    """Return the owning Flickr NSID for a valid, non-expired OAuth access
    token, or None. Used by ``web.ApiKeyMiddleware`` as a fallback when the
    bearer value isn't a static ``mcp_api_key``."""
    entry = _token_registry.get(token)
    if not entry:
        return None
    nsid, expires_at = entry
    if time.time() >= expires_at:
        return None
    return nsid


def _find_token_owner(token_value: str, field: str) -> tuple[str | None, dict | None]:
    """Scan every user's token store for an entry whose *field* matches
    *token_value* (constant-time compare). Used for refresh-token lookups and
    revocation, where — unlike an access token — we don't have an in-memory
    index to short-circuit the scan."""
    if not os.path.isdir(_CREDS_BASE):
        return None, None
    for entry in os.scandir(_CREDS_BASE):
        if not entry.is_dir():
            continue
        for tok in _load_tokens(entry.name):
            stored = tok.get(field) or ""
            if stored and secrets.compare_digest(stored, token_value):
                return entry.name, tok
    return None, None


def _issue_tokens(nsid: str, client_id: str, scope: str) -> JSONResponse:
    """Mint a fresh access/refresh token pair for (nsid, client_id), replacing
    any prior grant that same client held for that user."""
    now = time.time()
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    access_expires_at = now + _ACCESS_TOKEN_TTL
    refresh_expires_at = now + _REFRESH_TOKEN_TTL

    with _tokens_lock:
        tokens = _load_tokens(nsid)
        old = [t for t in tokens if t.get("client_id") == client_id]
        tokens = [t for t in tokens if t.get("client_id") != client_id]
        tokens.append({
            "client_id": client_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "access_expires_at": access_expires_at,
            "refresh_expires_at": refresh_expires_at,
            "scope": scope,
            "issued_at": now,
        })
        _save_tokens(nsid, tokens)

    for t in old:
        _token_registry.pop(t.get("access_token"), None)
    _token_registry[access_token] = (nsid, access_expires_at)

    logging.info("oauth2: issued access token for client %s, user %s", client_id, nsid)
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": _ACCESS_TOKEN_TTL,
        "refresh_token": refresh_token,
        "scope": scope,
    })


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    if not code_verifier or not code_challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, code_challenge)


def _sweep_pending_codes() -> None:
    cutoff = time.time() - _AUTH_CODE_TTL
    stale = [c for c, v in _pending_codes.items() if v["created_at"] < cutoff]
    for c in stale:
        del _pending_codes[c]


def _check_client_auth(client: dict, body: dict) -> bool:
    """Confidential clients (``token_endpoint_auth_method: client_secret_post``)
    must present their secret at the token endpoint; public clients (the
    expected case for Desktop/claude.ai — PKCE stands in for client auth) are
    always accepted here."""
    if client.get("token_endpoint_auth_method") != "client_secret_post":
        return True
    provided = body.get("client_secret", "")
    expected = client.get("client_secret", "")
    return bool(expected) and bool(provided) and secrets.compare_digest(provided, expected)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _issuer(request: Request) -> str:
    return str(request.base_url).rstrip("/")


async def route_protected_resource_metadata(request: Request):
    """GET /.well-known/oauth-protected-resource — RFC 9728. Tells a client
    which authorization server(s) protect this resource; this server acts as
    both, at the same origin."""
    issuer = _issuer(request)
    return JSONResponse({
        "resource": issuer,
        "authorization_servers": [issuer],
    })


async def route_authorization_server_metadata(request: Request):
    """GET /.well-known/oauth-authorization-server — RFC 8414."""
    issuer = _issuer(request)
    return JSONResponse({
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["mcp"],
    })


async def route_register(request: Request):
    """POST /oauth/register — RFC 7591 Dynamic Client Registration.

    Deliberately unauthenticated (per spec): Desktop/claude.ai call this once,
    the first time a user adds this server as a connector, before any Flickr
    login has happened. Every subsequent ``/oauth/authorize`` call uses the
    ``client_id`` handed back here.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "Request body must be JSON"},
            status_code=400,
        )

    redirect_uris = body.get("redirect_uris")
    if not redirect_uris or not isinstance(redirect_uris, list) or not all(
        isinstance(u, str) and u for u in redirect_uris
    ):
        return JSONResponse(
            {"error": "invalid_redirect_uri", "error_description": "redirect_uris must be a non-empty array of strings"},
            status_code=400,
        )

    client_name = str(body.get("client_name") or "MCP client")[:200]
    token_endpoint_auth_method = body.get("token_endpoint_auth_method", "none")
    if token_endpoint_auth_method not in ("none", "client_secret_post"):
        token_endpoint_auth_method = "none"

    client_id = secrets.token_urlsafe(24)
    record = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": token_endpoint_auth_method,
        "created_at": time.time(),
    }
    response = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": token_endpoint_auth_method,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    if token_endpoint_auth_method == "client_secret_post":
        client_secret = secrets.token_urlsafe(32)
        record["client_secret"] = client_secret
        response["client_secret"] = client_secret

    with _clients_lock:
        clients = _load_clients()
        if len(clients) >= _MAX_CLIENTS:
            logging.warning("oauth2: rejected registration — client store at capacity (%d)", _MAX_CLIENTS)
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Too many registered clients"},
                status_code=429,
            )
        clients[client_id] = record
        _save_clients(clients)

    logging.info("oauth2: registered client %s (%s)", client_id, client_name)
    return JSONResponse(response, status_code=201)


async def route_authorize(request: Request):
    """GET /oauth/authorize — browser-facing. Requires an existing Flickr
    login session; if absent, parks the request in the session and bounces
    through /login first. Otherwise renders the consent page."""
    from web import _base_ctx, templates  # deferred: avoids a circular import at module load

    params = request.query_params
    response_type = params.get("response_type", "")
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "")
    scope = params.get("scope", "")

    if response_type != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if not code_challenge or code_challenge_method != "S256":
        return JSONResponse(
            {"error": "invalid_request", "error_description": "PKCE (S256) is required"}, status_code=400
        )

    with _clients_lock:
        client = _load_clients().get(client_id)
    if not client:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if redirect_uri not in client.get("redirect_uris", []):
        return JSONResponse(
            {"error": "invalid_request", "error_description": "redirect_uri not registered"}, status_code=400
        )

    user_nsid = request.session.get("user_nsid", "")
    if not user_nsid:
        # Stash the whole authorize request and send the user through the
        # existing Flickr login flow; route_oauth_callback resumes it here.
        request.session["post_login_redirect"] = "/oauth/authorize?" + str(request.url.query)
        return RedirectResponse("/login", status_code=302)

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)

    ctx = _base_ctx(request, "Authorize")
    ctx.update({
        "client_name": client.get("client_name", "MCP client"),
        "username": request.session.get("username", user_nsid),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
    })
    return templates.TemplateResponse(request, "oauth_authorize.html", ctx)


async def route_authorize_submit(request: Request):
    """POST /oauth/authorize — consent decision. CSRF-validated by
    ``CSRFMiddleware`` before this runs; the parsed form is cached on
    ``request.state.form``."""
    form = request.state.form
    user_nsid = request.session.get("user_nsid", "")
    if not user_nsid:
        return RedirectResponse("/login", status_code=303)

    client_id = str(form.get("client_id") or "")
    redirect_uri = str(form.get("redirect_uri") or "")
    state = str(form.get("state") or "")
    code_challenge = str(form.get("code_challenge") or "")
    scope = str(form.get("scope") or "")
    decision = str(form.get("decision") or "")

    with _clients_lock:
        client = _load_clients().get(client_id)
    if not client or redirect_uri not in client.get("redirect_uris", []):
        return Response("Invalid client or redirect_uri", status_code=400)

    sep = "&" if "?" in redirect_uri else "?"

    if decision != "allow":
        dest = f"{redirect_uri}{sep}error=access_denied"
        if state:
            dest += f"&state={urllib.parse.quote(state)}"
        return RedirectResponse(dest, status_code=303)

    _sweep_pending_codes()
    if len(_pending_codes) >= _PENDING_CODE_MAX:
        logging.warning("oauth2: rejected authorize — pending codes at capacity (%d)", _PENDING_CODE_MAX)
        return Response("Too many pending authorizations. Try again shortly.", status_code=429)

    code = secrets.token_urlsafe(32)
    _pending_codes[code] = {
        "client_id": client_id,
        "nsid": user_nsid,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "scope": scope,
        "created_at": time.time(),
    }
    logging.info("oauth2: issued authorization code for client %s, user %s", client_id, user_nsid)

    dest = f"{redirect_uri}{sep}code={urllib.parse.quote(code)}"
    if state:
        dest += f"&state={urllib.parse.quote(state)}"
    return RedirectResponse(dest, status_code=303)


def _handle_auth_code_grant(body: dict) -> JSONResponse:
    code = body.get("code", "")
    client_id = body.get("client_id", "")
    redirect_uri = body.get("redirect_uri", "")
    code_verifier = body.get("code_verifier", "")

    _sweep_pending_codes()
    entry = _pending_codes.pop(code, None)
    if not entry:
        return JSONResponse({"error": "invalid_grant", "error_description": "Unknown or expired code"}, status_code=400)
    if entry["client_id"] != client_id or entry["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if not _verify_pkce(code_verifier, entry["code_challenge"]):
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

    with _clients_lock:
        client = _load_clients().get(client_id)
    if not client:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if not _check_client_auth(client, body):
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    return _issue_tokens(entry["nsid"], client_id, entry.get("scope", ""))


def _handle_refresh_grant(body: dict) -> JSONResponse:
    refresh_token = body.get("refresh_token", "")
    client_id = body.get("client_id", "")
    if not refresh_token or not client_id:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    with _clients_lock:
        client = _load_clients().get(client_id)
    if not client:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if not _check_client_auth(client, body):
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    nsid, entry = _find_token_owner(refresh_token, "refresh_token")
    if not nsid or entry.get("client_id") != client_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry.get("refresh_expires_at", 0) < time.time():
        return JSONResponse({"error": "invalid_grant", "error_description": "Refresh token expired"}, status_code=400)

    return _issue_tokens(nsid, client_id, entry.get("scope", ""))


async def route_token(request: Request):
    """POST /oauth/token — authorization_code and refresh_token grants.
    Not a browser request (no session, no CSRF token), so ``CSRFMiddleware``
    excludes this path entirely."""
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
        else:
            body = dict(await request.form())
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    grant_type = body.get("grant_type", "")
    if grant_type == "authorization_code":
        return _handle_auth_code_grant(body)
    if grant_type == "refresh_token":
        return _handle_refresh_grant(body)
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


async def route_revoke(request: Request):
    """POST /oauth/revoke — RFC 7009. Accepts either an access or a refresh
    token; always returns 200 (per spec, even for an unknown token) so a
    client can't probe token validity through this endpoint."""
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
        else:
            body = dict(await request.form())
    except Exception:
        return Response(status_code=200)

    token = body.get("token", "")
    if not token:
        return Response(status_code=200)

    nsid, entry = _find_token_owner(token, "access_token")
    if not entry:
        nsid, entry = _find_token_owner(token, "refresh_token")
    if entry and nsid:
        # entry came from a separate _load_tokens() read than the one we're
        # about to take under the lock, so match by access_token value
        # (unique per issuance), not object identity.
        target_access = entry.get("access_token")
        with _tokens_lock:
            tokens = _load_tokens(nsid)
            remaining = [t for t in tokens if t.get("access_token") != target_access]
            _save_tokens(nsid, remaining)
        _token_registry.pop(target_access, None)
        logging.info("oauth2: revoked token for client %s, user %s", entry.get("client_id"), nsid)

    return Response(status_code=200)


def oauth_routes() -> list[Route]:
    return [
        Route("/.well-known/oauth-protected-resource", endpoint=route_protected_resource_metadata),
        Route("/.well-known/oauth-authorization-server", endpoint=route_authorization_server_metadata),
        Route("/oauth/register", endpoint=route_register, methods=["POST"]),
        Route("/oauth/authorize", endpoint=route_authorize, methods=["GET"]),
        Route("/oauth/authorize", endpoint=route_authorize_submit, methods=["POST"]),
        Route("/oauth/token", endpoint=route_token, methods=["POST"]),
        Route("/oauth/revoke", endpoint=route_revoke, methods=["POST"]),
    ]


# Paths CSRFMiddleware and ApiKeyMiddleware need to special-case in web.py.
NO_CSRF_PATHS = ("/oauth/register", "/oauth/token", "/oauth/revoke")
