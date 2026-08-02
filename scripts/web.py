"""Web UI, OAuth flow, and SSE/uvicorn server setup.

Multi-user design
-----------------
Each Flickr account that completes the OAuth flow gets:
  * A credentials file at ``~/.flickr_mcp/{nsid}/credentials.json`` containing
    their OAuth tokens and a randomly-generated ``mcp_api_key``.
  * A personal SQLite database at ``data/{username}/flickr.db``.

The web session stores ``user_nsid``, ``username``, and ``fullname`` after a
successful login.  Sessions last 30 days.  Logging out only clears the session
— credentials and the database are preserved so the user can re-authenticate
without a full re-sync.

The ``ApiKeyMiddleware`` maps incoming MCP API keys to their owner's NSID via
the in-memory ``_api_key_registry``.  The ``_SSEHandler`` then sets the
``db._current_user`` ContextVar so all tool handlers and ``_api_get``/``_api_post``
resolve the correct per-user paths transparently.
"""

import asyncio
import datetime
import json
import logging
import os
import pathlib
import time
import urllib.parse
import uuid
import anyio
import requests
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from db import _current_user as _db_current_user
from flickr_api import (
    _CREDS_BASE, CREDENTIALS_FILE,
    credentials_file, _load_credentials, _save_credentials,
    _load_env, _oauth_params, _sign,
)
from mcp_tools import (
    SYNC_SCRIPT, _active_syncs, _background_refresh, _get_user_lock, _run_sync_script,
    _sync_phase, _sync_progress, cancel_sync, server,
)

import secrets
from starlette.middleware.sessions import SessionMiddleware

MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

# Defaults to False to match the documented default workflow (plain
# http://localhost:8000, whether run directly or via `docker compose up`).
# Set SESSION_COOKIE_SECURE=true when running behind something that
# terminates TLS for the browser (a reverse proxy, or the tailscale sidecar
# in docker-compose.yml) — the Secure flag is about the scheme the browser
# connected with, not the scheme the app itself sees, so it's safe to
# enable even though the app is reached over plain HTTP internally.
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes")

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(_PROJECT_ROOT / "templates"))

_SESSION_KEY_FILE = os.path.join(_CREDS_BASE, "session_secret.key")

def _load_or_create_session_key() -> str:
    """Load the session signing key from env, file, or generate a new one."""
    if key := os.environ.get("SESSION_SECRET_KEY"):
        return key
    if os.path.exists(_SESSION_KEY_FILE):
        with open(_SESSION_KEY_FILE) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    os.makedirs(_CREDS_BASE, exist_ok=True)
    with os.fdopen(os.open(_SESSION_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(key)
    return key

SESSION_SECRET_KEY = _load_or_create_session_key()

# ---------------------------------------------------------------------------
# Per-user API key registry
# ---------------------------------------------------------------------------

_api_key_registry: dict[str, str] = {}  # mcp_api_key -> user_nsid


def _load_api_key_registry() -> None:
    """Populate ``_api_key_registry`` by scanning all per-user credential files.

    Called once at startup and again after each successful OAuth login so that
    newly-registered users are immediately usable without a restart.
    Builds into a temporary dict and swaps atomically to avoid a window where
    concurrent SSE connections see an empty registry.
    """
    new_registry: dict[str, str] = {}
    if not os.path.isdir(_CREDS_BASE):
        _api_key_registry.clear()
        return
    for entry in os.scandir(_CREDS_BASE):
        if not entry.is_dir():
            continue
        cpath = os.path.join(entry.path, "credentials.json")
        if not os.path.exists(cpath):
            continue
        try:
            with open(cpath) as f:
                creds = json.load(f)
            key = creds.get("mcp_api_key")
            nsid = creds.get("user_nsid")
            if key and nsid:
                new_registry[key] = nsid
        except Exception as e:
            logging.warning("Failed to load API key from %s: %s", cpath, type(e).__name__)
    _api_key_registry.clear()
    _api_key_registry.update(new_registry)
    logging.debug("API key registry: %d user(s) loaded", len(_api_key_registry))


_pending_oauth: dict[str, tuple[str, float, str]] = {}  # token -> (secret, created_at, nonce)
_PENDING_OAUTH_TTL = 600   # seconds before an unused request token is discarded
_PENDING_OAUTH_MAX = 100   # hard cap on concurrent in-flight OAuth flows

_FLICKR_REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
_FLICKR_ACCESS_TOKEN_URL  = "https://www.flickr.com/services/oauth/access_token"
_FLICKR_AUTHORIZE_URL     = "https://www.flickr.com/services/oauth/authorize"

_SITE_TITLE = "Mr E Flickr MCP"
_GITHUB_URL = "https://github.com/kc9yjp/mre_flickr_mcp"
_FLICKR_URL = "https://www.flickr.com/photos/ejwettstein/"


def _fmt_dur(secs) -> str | None:
    """Format a duration in seconds for display in the sync table."""
    if secs is None:
        return None
    if secs == 0:
        return "< 1s"
    if secs < 60:
        return f"{secs}s"
    return f"{round(secs / 60)} min"


def _base_ctx(request: Request, title: str, logged_in: bool | None = None) -> dict:
    """Build the template context shared by every page."""
    if logged_in is None:
        logged_in = bool(request.session.get("user_nsid"))
    return {
        "request": request,
        "title": title,
        "site_title": _SITE_TITLE,
        "github_url": _GITHUB_URL,
        "flickr_url": _FLICKR_URL,
        "year": datetime.date.today().year,
        "logged_in": logged_in,
        "csrf_token": request.session.get("csrf_token", ""),
    }


# --- Route handlers ---

async def route_root(request: Request):
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)

    user_nsid = request.session.get("user_nsid", "")
    if not user_nsid:
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/app", status_code=302)


async def route_login(request: Request):
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)

    msg = request.query_params.get("msg", "")
    logged_in = bool(request.session.get("user_nsid"))

    if logged_in and msg not in ("ok", "err"):
        return RedirectResponse("/", status_code=303)

    ctx = _base_ctx(request, "Login", logged_in=False)
    ctx.update({
        "alert_ok": "Login successful! You are now connected to Flickr." if msg == "ok" else None,
        "alert_err": "Login failed. Please try again." if msg == "err" else None,
    })
    return templates.TemplateResponse(request, "login.html", ctx)



def _login_error(request: Request, message: str, status_code: int = 500):
    ctx = _base_ctx(request, "Login", logged_in=False)
    ctx["alert_err"] = message
    return templates.TemplateResponse(request, "login.html", ctx, status_code=status_code)


async def route_login_start(request: Request):
    try:
        api_key, api_secret = _load_env()
    except Exception as e:
        return _login_error(request, f"Config error: {e}")

    callback_url = str(request.base_url).rstrip("/") + "/oauth/callback"
    params = _oauth_params(api_key, {"oauth_callback": callback_url})
    params["oauth_signature"] = _sign("GET", _FLICKR_REQUEST_TOKEN_URL, params, api_secret)

    try:
        resp = requests.get(_FLICKR_REQUEST_TOKEN_URL, params=params, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return _login_error(request, "Failed to get request token. Check your Flickr API credentials.")

    token_data = dict(urllib.parse.parse_qsl(resp.text))
    oauth_token = token_data.get("oauth_token")
    oauth_token_secret = token_data.get("oauth_token_secret")

    if not oauth_token:
        return _login_error(request, "Flickr returned no token in the request token response.")

    cutoff = time.time() - _PENDING_OAUTH_TTL
    stale = [t for t, (_, ts, _n) in _pending_oauth.items() if ts < cutoff]
    for t in stale:
        del _pending_oauth[t]

    if len(_pending_oauth) >= _PENDING_OAUTH_MAX:
        logging.warning("Rejected OAuth start: pending dict at capacity (%d)", _PENDING_OAUTH_MAX)
        return _login_error(request, "Too many login attempts in progress. Try again shortly.", status_code=429)

    # Bind this pending request token to the browser that started the flow, so a
    # crafted /oauth/callback link (built from an attacker's own oauth_token/verifier)
    # can't be used to fixate a victim's session onto the attacker's Flickr account.
    nonce = secrets.token_urlsafe(32)
    request.session["oauth_nonce"] = nonce

    _pending_oauth[oauth_token] = (oauth_token_secret, time.time(), nonce)
    authorize_url = f"{_FLICKR_AUTHORIZE_URL}?oauth_token={oauth_token}&perms=write"
    return RedirectResponse(authorize_url)


async def route_oauth_callback(request: Request):
    oauth_token    = request.query_params.get("oauth_token", "")
    oauth_verifier = request.query_params.get("oauth_verifier", "")

    if not oauth_token or not oauth_verifier:
        return RedirectResponse("/login?msg=err")

    entry = _pending_oauth.pop(oauth_token, None)
    token_secret = entry[0] if entry is not None else None
    expected_nonce = entry[2] if entry is not None else None
    if token_secret is None:
        return RedirectResponse("/login?msg=err")

    session_nonce = request.session.pop("oauth_nonce", None)
    if not session_nonce or not expected_nonce or not secrets.compare_digest(session_nonce, expected_nonce):
        logging.warning("OAuth callback: nonce mismatch (possible session-fixation attempt) for token %s", oauth_token)
        return RedirectResponse("/login?msg=err")

    try:
        api_key, api_secret = _load_env()
    except Exception as e:
        logging.error("OAuth callback: failed to load env: %s", e)
        return RedirectResponse("/login?msg=err")

    params = _oauth_params(api_key, {
        "oauth_token":    oauth_token,
        "oauth_verifier": oauth_verifier,
    })
    params["oauth_signature"] = _sign("POST", _FLICKR_ACCESS_TOKEN_URL, params, api_secret, token_secret)

    try:
        resp = requests.post(_FLICKR_ACCESS_TOKEN_URL, data=params, timeout=15)
        resp.raise_for_status()
    except Exception:
        logging.exception("OAuth access token exchange failed")
        return RedirectResponse("/login?msg=err")

    token_data = dict(urllib.parse.parse_qsl(resp.text))
    access_token        = token_data.get("oauth_token")
    access_token_secret = token_data.get("oauth_token_secret")
    user_nsid           = token_data.get("user_nsid", "")
    username            = token_data.get("username", "")
    fullname            = token_data.get("fullname", "")

    if not access_token:
        logging.error("No access token in Flickr response")
        return RedirectResponse("/login?msg=err")

    from db import _validate_username
    try:
        _validate_username(username)
    except ValueError:
        logging.error("OAuth callback: unsafe/invalid username %r for nsid %s", username, user_nsid)
        return RedirectResponse("/login?msg=err")

    # Preserve an existing API key so MCP clients don't break on re-login.
    mcp_api_key = None
    try:
        existing = _load_credentials(nsid=user_nsid)
        mcp_api_key = existing.get("mcp_api_key")
    except Exception as e:
        logging.debug("No existing credentials for %s (first login): %s", user_nsid, type(e).__name__)
    if not mcp_api_key:
        mcp_api_key = str(uuid.uuid4())

    creds = {
        "oauth_token":        access_token,
        "oauth_token_secret": access_token_secret,
        "user_nsid":          user_nsid,
        "username":           username,
        "fullname":           fullname,
        "mcp_api_key":        mcp_api_key,
    }

    _save_credentials(creds, user_nsid)
    _api_key_registry[mcp_api_key] = user_nsid

    request.session["user_nsid"] = user_nsid
    request.session["username"]  = username
    request.session["fullname"]  = fullname

    logging.info("OAuth login complete for user %s (%s)", username, user_nsid)

    scripts_dir = os.path.dirname(SYNC_SCRIPT)
    user_args   = ["--nsid", user_nsid, "--username", username]

    async def _post_login_sync():
        async with _get_user_lock(username):
            await _run_sync_script(SYNC_SCRIPT, "photos",
                                   extra_args=["--create"] + user_args,
                                   username=username, nsid=user_nsid)
            await _run_sync_script(os.path.join(scripts_dir, "sync_contacts.py"), "contacts",
                                   extra_args=user_args, username=username, nsid=user_nsid)
            await _run_sync_script(os.path.join(scripts_dir, "sync_groups.py"),   "groups",
                                   extra_args=user_args, username=username, nsid=user_nsid)
            await _run_sync_script(os.path.join(scripts_dir, "sync_albums.py"),   "albums",
                                   extra_args=user_args, username=username, nsid=user_nsid)

    asyncio.create_task(_post_login_sync())
    return RedirectResponse("/?msg=ok", status_code=303)


async def route_logout(request: Request):
    """Clear the session cookie.  Credentials and database are preserved."""
    request.session.clear()
    return RedirectResponse("/", status_code=303)


async def _trigger_full_sync(username: str, user_nsid: str, user_args: list[str], scripts_dir: str) -> None:
    """Run a full sync cycle for *username* in the background."""
    lock = _get_user_lock(username)
    if lock.locked():
        return
    async with lock:
        await _run_sync_script(SYNC_SCRIPT, f"photos/{username}", extra_args=user_args, username=username, nsid=user_nsid)
        await asyncio.gather(
            _run_sync_script(os.path.join(scripts_dir, "sync_contacts.py"), f"contacts/{username}", extra_args=user_args, username=username, nsid=user_nsid),
            _run_sync_script(os.path.join(scripts_dir, "sync_groups.py"),   f"groups/{username}",   extra_args=user_args, username=username, nsid=user_nsid),
            _run_sync_script(os.path.join(scripts_dir, "sync_albums.py"),   f"albums/{username}",   extra_args=user_args, username=username, nsid=user_nsid),
        )
        await _run_sync_script(os.path.join(scripts_dir, "sync_engagement.py"), f"engagement/{username}", extra_args=user_args, username=username, nsid=user_nsid)


_SYNC_TABLE_MAP = {
    "photos":     "photos",
    "contacts":   "contacts",
    "groups":     "groups",
    "albums":     "albums",
    "engagement": "contact_engagement",
}


def _build_sync_rows(db_username: str, nsid: str | None = None) -> list[dict]:
    """Query sync_log and return rows enriched with active-sync status,
    total record counts, and pending group-summary counts."""
    import random
    from db import get_db_for_user
    from tools.sync import MIN_REFRESH_INTERVAL, REFRESH_INTERVAL

    raw_rows = []
    totals: dict[str, int] = {}
    pending_summaries = 0
    try:
        with get_db_for_user(db_username, nsid) as conn:
            raw_rows = conn.execute(
                "SELECT s.type, s.synced_at AS last, s.duration_seconds"
                " FROM sync_log s"
                " JOIN (SELECT type, MAX(synced_at) AS ts FROM sync_log GROUP BY type) m"
                " ON s.type = m.type AND s.synced_at = m.ts"
            ).fetchall()
            for stype, table in _SYNC_TABLE_MAP.items():
                try:
                    totals[stype] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except Exception:
                    pass
            try:
                pending_summaries = conn.execute(
                    "SELECT COUNT(*) FROM groups WHERE needs_summary=1"
                ).fetchone()[0]
            except Exception:
                pass
    except Exception as e:
        logging.warning("Could not load sync log for %s: %s", db_username, e)

    active_types = {label.split("/")[0] for label in _active_syncs}
    active_phases = {label.split("/")[0]: phase for label, phase in _sync_phase.items()}
    active_progress = {label.split("/")[0]: prog for label, prog in _sync_progress.items()}

    rows = []
    for r in raw_rows:
        stype = r["type"]
        last_ts = r["last"]
        # Mirror the background refresh logic: stable random threshold seeded by last_ts.
        user_threshold = random.Random(int(last_ts)).uniform(MIN_REFRESH_INTERVAL, REFRESH_INTERVAL) if last_ts else REFRESH_INTERVAL
        next_ts = (last_ts + user_threshold) if last_ts else None

        # ETA for the AI group-summary phase: derived from the actual observed
        # pace (elapsed time / groups done so far) rather than the configured
        # throttle alone, so it also reflects real LLM response latency.
        prog = active_progress.get(stype)
        done = total = eta_seconds = None
        if prog and prog.get("total"):
            done, total = prog["done"], prog["total"]
            if done > 0:
                avg = (time.time() - prog["started"]) / done
                eta_seconds = max(0, round(avg * (total - done)))

        rows.append({
            "type": stype,
            "last": last_ts,
            "duration": _fmt_dur(r["duration_seconds"]),
            "next": next_ts,
            "running": stype in active_types,
            "phase": active_phases.get(stype),
            "total": totals.get(stype),
            "pending_summary": pending_summaries if stype == "groups" else None,
            "progress_done": done,
            "progress_total": total,
            "eta_seconds": eta_seconds,
        })
    return rows


def _start_sync(sync_type: str, username: str, user_nsid: str, *, full: bool = False) -> bool:
    """Validate and launch a background sync task.

    Returns True when the sync was started, False when the type is unknown or
    another sync is already running for this user.  Shared by the sync-page
    form route and the JSON API.
    """
    scripts_dir = os.path.dirname(SYNC_SCRIPT)
    user_args   = ["--nsid", user_nsid, "--username", username] if user_nsid else []

    script_map = {
        "photos":   SYNC_SCRIPT,
        "contacts": os.path.join(scripts_dir, "sync_contacts.py"),
        "groups":   os.path.join(scripts_dir, "sync_groups.py"),
        "albums":   os.path.join(scripts_dir, "sync_albums.py"),
    }

    if sync_type not in script_map and sync_type not in ("all", "backfill"):
        return False

    lock = _get_user_lock(username or "_single_user")
    if lock.locked():
        return False

    is_backfill = sync_type == "backfill"
    if is_backfill:
        photo_args = list(user_args) + ["--backfill"]
    else:
        photo_args = list(user_args) + (["--full"] if full else [])

    async def _run():
        async with lock:
            if sync_type == "all":
                for label, path in script_map.items():
                    extra = photo_args if label == "photos" else (user_args or None)
                    await _run_sync_script(path, label, extra_args=extra or None, username=username or None, nsid=user_nsid or None)
            elif is_backfill:
                await _run_sync_script(SYNC_SCRIPT, "photos", extra_args=photo_args or None, username=username or None, nsid=user_nsid or None)
            else:
                extra = photo_args if sync_type == "photos" else (user_args or None)
                await _run_sync_script(script_map[sync_type], sync_type,
                                       extra_args=extra or None, username=username or None, nsid=user_nsid or None)

    asyncio.create_task(_run())
    return True


# --- SSE handler and API key middleware ---

def _bind_user_ctx(scope) -> object | None:
    """Read user_nsid from ASGI scope state set by ApiKeyMiddleware and bind
    _db_current_user for the duration of the request. Returns the ContextVar
    token to pass to _db_current_user.reset(), or None if no user was resolved."""
    state = scope.get("state") or {}
    user_nsid = state.get("user_nsid") if isinstance(state, dict) else getattr(state, "user_nsid", None)
    if not user_nsid:
        return None
    try:
        creds = _load_credentials(nsid=user_nsid)
        return _db_current_user.set({"nsid": user_nsid, "username": creds.get("username", user_nsid)})
    except Exception:
        logging.error("MCP handler: failed to load credentials for %s", user_nsid)
        return None


class _SSEHandler:
    """ASGI handler for the MCP SSE endpoint.

    Sets ``db._current_user`` from the API key resolved by ``ApiKeyMiddleware``
    so that all tool calls within a connection operate on the correct per-user
    database and credentials.
    """

    def __init__(self, sse_transport):
        self._sse = sse_transport

    async def __call__(self, scope, receive, send):
        token = _bind_user_ctx(scope)
        try:
            async with self._sse.connect_sse(scope, receive, send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())
        finally:
            if token is not None:
                _db_current_user.reset(token)


class _StreamableHTTPHandler:
    """ASGI handler for the MCP Streamable HTTP endpoint.

    Creates a fresh stateless transport per request. Sets ``db._current_user``
    from the API key resolved by ``ApiKeyMiddleware`` so all tool calls operate
    on the correct per-user database and credentials.
    """

    async def __call__(self, scope, receive, send):
        from mcp.server.streamable_http import StreamableHTTPServerTransport

        token = _bind_user_ctx(scope)
        try:
            transport = StreamableHTTPServerTransport(mcp_session_id=None)

            async def _run_server(*, task_status=anyio.TASK_STATUS_IGNORED):
                async with transport.connect() as (read_stream, write_stream):
                    task_status.started()
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                        stateless=True,  # safe for concurrent per-request use
                    )

            async with anyio.create_task_group() as tg:
                await tg.start(_run_server)
                try:
                    await transport.handle_request(scope, receive, send)
                finally:
                    # handle_request awaits every send() call before returning,
                    # so the response is fully committed here.  Cancel the server
                    # loop, which is now waiting for a next message that will
                    # never arrive on a stateless connection.
                    tg.cancel_scope.cancel()
        finally:
            if token is not None:
                _db_current_user.reset(token)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Validate MCP API keys and attach the resolved user NSID to request state.

    Every request to ``/sse``, ``/messages``, or ``/mcp`` must carry a valid
    API key via the ``X-API-Key`` header or ``Authorization: Bearer`` header.
    The key is looked up in ``_api_key_registry`` (populated at startup and on
    each login) and the matched NSID is stored in ``request.state.user_nsid``
    for the transport handlers to consume.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/sse") or path.startswith("/messages") or path == "/mcp":
            key = request.headers.get("X-API-Key", "")
            if not key:
                auth = request.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    key = auth[7:]
            if not key or key not in _api_key_registry:
                return Response("Unauthorized", status_code=401)
            request.state.user_nsid = _api_key_registry[key]
        return await call_next(request)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            path = request.url.path
            if path.startswith("/sse") or path.startswith("/messages") or path == "/mcp":
                return await call_next(request)

            # JSON API routes send the token as a header; checking it here
            # (before request.form()) leaves the body untouched for handlers.
            if path.startswith("/api/"):
                token = request.headers.get("X-CSRF-Token", "")
                session_token = request.session.get("csrf_token", "")
                if not session_token or not secrets.compare_digest(token, session_token):
                    logging.warning("CSRF validation failed for path %s", path)
                    return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
                return await call_next(request)

            form_data = await request.form()
            token_in_form = form_data.get("csrf_token")
            token_in_session = request.session.get("csrf_token")

            if not token_in_session or token_in_form != token_in_session:
                logging.warning("CSRF validation failed for path %s", path)
                return Response("CSRF validation failed", status_code=403)

            # Cache parsed form so route handlers can read it after the body stream is consumed.
            request.state.form = form_data

        return await call_next(request)


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Set cache headers for the workbench SPA.

    Vite's build gives JS/CSS content-hashed filenames under /app/assets/,
    so those are safe to cache forever. index.html (and the SPA fallback for
    unmatched /app/* routes) is not hashed, so without an explicit no-cache
    header browsers can serve a stale shell pointing at an old bundle after
    a rebuild until a hard refresh.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/app/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.startswith("/app"):
            response.headers["Cache-Control"] = "no-cache"
        return response


async def main_sse():
    """Start the MCP SSE server with the Starlette web application.

    Loads the per-user API key registry, configures middleware (session,
    CSRF, API key auth), registers all routes, starts the background refresh
    task, and begins serving on ``MCP_PORT``.
    """
    from mcp.server.sse import SseServerTransport
    import uvicorn

    import webapi  # imported here because webapi imports this module
    from agent import routes as agent_routes

    _load_api_key_registry()

    sse = SseServerTransport("/messages/")
    middleware = [
        Middleware(
            SessionMiddleware,
            secret_key=SESSION_SECRET_KEY,
            max_age=30 * 24 * 3600,
            https_only=SESSION_COOKIE_SECURE,
            same_site="lax",
        ),
        Middleware(CSRFMiddleware),
        Middleware(ApiKeyMiddleware),
        Middleware(StaticCacheMiddleware),
    ]

    app = Starlette(
        middleware=middleware,
        routes=[
            Route("/",               endpoint=route_root),
            Route("/login",          endpoint=route_login),
            Route("/login/start",    endpoint=route_login_start),
            Route("/oauth/callback", endpoint=route_oauth_callback),
            Route("/logout",         endpoint=route_logout, methods=["POST"]),
            *webapi.api_routes(),
            *agent_routes.api_routes(),
            Route("/sse",            endpoint=_SSEHandler(sse)),
            Mount("/messages/",      app=sse.handle_post_message),
            Route("/mcp",            endpoint=_StreamableHTTPHandler()),
            # Workbench SPA — only when the frontend has been built
            *([Mount("/app", app=StaticFiles(directory=str(_PROJECT_ROOT / "frontend" / "dist"), html=True), name="app")]
              if os.path.isdir(_PROJECT_ROOT / "frontend" / "dist") else []),
            Mount("/static",         app=StaticFiles(directory=str(_PROJECT_ROOT / "static")), name="static"),
        ],
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=MCP_PORT, log_level="info")
    uv_server = uvicorn.Server(config)

    asyncio.create_task(_background_refresh())
    logging.info("SSE ready on port %d", MCP_PORT)
    await uv_server.serve()
