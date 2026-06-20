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
import requests
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from db import _current_user as _db_current_user, db_file, get_db, SETTINGS_DEFAULTS, get_setting, set_setting
from flickr_api import (
    _CREDS_BASE, CREDENTIALS_FILE,
    credentials_file, _load_credentials, _save_credentials,
    _load_env, _oauth_params, _sign,
)
from mcp_tools import SYNC_SCRIPT, _active_syncs, _background_refresh, _get_user_lock, _run_sync_script, server

import secrets
from starlette.middleware.sessions import SessionMiddleware

MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

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
            logging.warning("Failed to load API key from %s: %s", cpath, e)
    _api_key_registry.clear()
    _api_key_registry.update(new_registry)
    logging.debug("API key registry: %d user(s) loaded", len(_api_key_registry))


def _require_login(request: Request):
    """Return a redirect to ``/login`` if the session has no ``user_nsid``, else ``None``."""
    if not request.session.get("user_nsid"):
        return RedirectResponse("/login", status_code=303)
    return None


_pending_oauth: dict[str, tuple[str, float]] = {}  # token -> (secret, created_at)
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

    msg = request.query_params.get("msg", "")
    user_nsid = request.session.get("user_nsid", "")
    logged_in = bool(user_nsid)

    if not logged_in:
        from starlette.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=302)
    username = request.session.get("fullname") or request.session.get("username") or user_nsid
    db_username = request.session.get("username", "")

    total_photos = 0
    last_sync = None
    db_ok = False
    syncing = False
    if logged_in:
        try:
            from db import get_db_for_user
            with get_db_for_user(db_username) as conn:
                row = conn.execute("SELECT COUNT(*) FROM photos").fetchone()
                total_photos = row[0] if row else 0
                sync_row = conn.execute(
                    "SELECT MAX(synced_at) FROM sync_log WHERE type = 'photos'"
                ).fetchone()
                if sync_row and sync_row[0]:
                    last_sync = sync_row[0]
            db_ok = True
        except Exception as e:
            logging.debug("Could not load home page DB stats: %s", e)
        syncing = _get_user_lock(db_username).locked()

    prompts = [
        {
            "display": "Review my photo at <em>[PHOTO URL]</em> — suggest a better title, description, and tags, then add it to relevant groups.",
            "cmd": "Review my photo at [PHOTO URL] — suggest a better title, description, and tags, then add it to relevant groups.",
        },
        {
            "display": "Fave my photo at <em>[PHOTO URL]</em> and suggest a comment to post on it.",
            "cmd": "Fave my photo at [PHOTO URL] and suggest a comment to post on it.",
        },
        {
            "display": "Check if my photo at <em>[PHOTO URL]</em> qualifies for any threshold groups based on its view and fave counts, and add it.",
            "cmd": "Check if my photo at [PHOTO URL] qualifies for any threshold groups based on its view and fave counts, and add it.",
        },
        {
            "display": "Add my photo at <em>[PHOTO URL]</em> to an appropriate album.",
            "cmd": "Add my photo at [PHOTO URL] to an appropriate album.",
        },
        {
            "display": "Find my weakest photos — low views, zero faves — and help me decide which to make private or improve.",
            "cmd": "Find my weakest photos — low views, zero faves — and help me decide which to make private or improve.",
        },
        {
            "display": "Review my contacts and identify unfollow candidates based on engagement — walk me through them one at a time.",
            "cmd": "Review my contacts and identify unfollow candidates based on engagement — walk me through them one at a time.",
        },
        {
            "display": "Sync my Flickr data — photos, contacts, groups, and albums.",
            "cmd": "Sync my Flickr data — photos, contacts, groups, and albums.",
        },
    ]

    ctx = _base_ctx(request, "Home", logged_in=logged_in)
    ctx.update({
        "msg": msg,
        "username": username,
        "total_photos": f"{total_photos:,}",
        "last_sync": last_sync,
        "db_ok": db_ok,
        "syncing": syncing,
        "prompts": prompts,
    })
    return templates.TemplateResponse(request, "home.html", ctx)


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
        return _login_error(request, f"Failed to get request token: {e}")

    token_data = dict(urllib.parse.parse_qsl(resp.text))
    oauth_token = token_data.get("oauth_token")
    oauth_token_secret = token_data.get("oauth_token_secret")

    if not oauth_token:
        return _login_error(request, f"Flickr returned no token: {resp.text[:200]}")

    cutoff = time.time() - _PENDING_OAUTH_TTL
    stale = [t for t, (_, ts) in _pending_oauth.items() if ts < cutoff]
    for t in stale:
        del _pending_oauth[t]

    if len(_pending_oauth) >= _PENDING_OAUTH_MAX:
        logging.warning("Rejected OAuth start: pending dict at capacity (%d)", _PENDING_OAUTH_MAX)
        return _login_error(request, "Too many login attempts in progress. Try again shortly.", status_code=429)

    _pending_oauth[oauth_token] = (oauth_token_secret, time.time())
    authorize_url = f"{_FLICKR_AUTHORIZE_URL}?oauth_token={oauth_token}&perms=write"
    return RedirectResponse(authorize_url)


async def route_oauth_callback(request: Request):
    oauth_token    = request.query_params.get("oauth_token", "")
    oauth_verifier = request.query_params.get("oauth_verifier", "")

    if not oauth_token or not oauth_verifier:
        return RedirectResponse("/login?msg=err")

    entry = _pending_oauth.pop(oauth_token, None)
    token_secret = entry[0] if entry is not None else None
    if token_secret is None:
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
        logging.error("No access token in Flickr response: %s", resp.text[:200])
        return RedirectResponse("/login?msg=err")

    # Preserve an existing API key so MCP clients don't break on re-login.
    mcp_api_key = None
    try:
        existing = _load_credentials(nsid=user_nsid)
        mcp_api_key = existing.get("mcp_api_key")
    except Exception as e:
        logging.debug("No existing credentials for %s (first login): %s", user_nsid, e)
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
                                   username=username)
            await _run_sync_script(os.path.join(scripts_dir, "sync_contacts.py"), "contacts",
                                   extra_args=user_args, username=username)
            await _run_sync_script(os.path.join(scripts_dir, "sync_groups.py"),   "groups",
                                   extra_args=user_args, username=username)
            await _run_sync_script(os.path.join(scripts_dir, "sync_albums.py"),   "albums",
                                   extra_args=user_args, username=username)

    asyncio.create_task(_post_login_sync())
    return RedirectResponse("/?msg=ok", status_code=303)


async def route_logout(request: Request):
    """Clear the session cookie.  Credentials and database are preserved."""
    request.session.clear()
    return RedirectResponse("/", status_code=303)


async def route_stats(request: Request):
    """Render collection statistics for the logged-in user."""
    redir = _require_login(request)
    if redir:
        return redir
    from db import get_db_for_user
    db_username = request.session.get("username", "")
    ctx = _base_ctx(request, "Stats")
    try:
        with get_db_for_user(db_username) as conn:
            stats = conn.execute("""
                SELECT COUNT(*) AS total_photos,
                       SUM(CASE WHEN is_public = 1 THEN 1 ELSE 0 END) AS public_photos,
                       SUM(CASE WHEN is_public = 0 THEN 1 ELSE 0 END) AS private_photos,
                       SUM(views) AS total_views,
                       MIN(date_taken) AS earliest,
                       MAX(date_taken) AS latest
                FROM photos
            """).fetchone()
            tag_rows = conn.execute(
                "SELECT tags FROM photos WHERE tags != '' AND tags IS NOT NULL"
            ).fetchall()
            group_count   = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
            album_count   = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
            contact_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    except FileNotFoundError:
        ctx["no_db"] = True
        return templates.TemplateResponse(request, "stats.html", ctx)
    except Exception as e:
        logging.exception("route_stats error")
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "stats.html", ctx)

    counts = {}
    for row in tag_rows:
        for tag in (row[0] or "").split():
            counts[tag] = counts.get(tag, 0) + 1
    top_tags = sorted(counts.items(), key=lambda x: -x[1])[:20]

    total_views = stats["total_views"] or 0
    views_str = f"{total_views / 1_000_000:.2f}M" if total_views >= 1_000_000 else f"{total_views:,}"

    ctx.update({
        "total_photos": f"{stats['total_photos'] or 0:,}",
        "public_photos": f"{stats['public_photos'] or 0:,}",
        "private_photos": f"{stats['private_photos'] or 0:,}",
        "total_views": views_str,
        "album_count": f"{album_count:,}",
        "group_count": f"{group_count:,}",
        "contact_count": f"{contact_count:,}",
        "date_range": f"{stats['earliest'] or '?'} → {stats['latest'] or '?'}",
        "top_tags": top_tags,
    })
    return templates.TemplateResponse(request, "stats.html", ctx)


async def _trigger_full_sync(username: str, user_nsid: str, user_args: list[str], scripts_dir: str) -> None:
    """Run a full sync cycle for *username* in the background."""
    lock = _get_user_lock(username)
    if lock.locked():
        return
    async with lock:
        await _run_sync_script(SYNC_SCRIPT, f"photos/{username}", extra_args=user_args, username=username)
        await asyncio.gather(
            _run_sync_script(os.path.join(scripts_dir, "sync_contacts.py"), f"contacts/{username}", extra_args=user_args, username=username),
            _run_sync_script(os.path.join(scripts_dir, "sync_groups.py"),   f"groups/{username}",   extra_args=user_args, username=username),
            _run_sync_script(os.path.join(scripts_dir, "sync_albums.py"),   f"albums/{username}",   extra_args=user_args, username=username),
        )
        await _run_sync_script(os.path.join(scripts_dir, "sync_engagement.py"), f"engagement/{username}", extra_args=user_args, username=username)


def _build_sync_rows(db_username: str) -> list[dict]:
    """Query sync_log and return rows enriched with active-sync status."""
    import random
    from db import get_db_for_user
    from tools.sync import MIN_REFRESH_INTERVAL, REFRESH_INTERVAL

    raw_rows = []
    try:
        with get_db_for_user(db_username) as conn:
            raw_rows = conn.execute(
                "SELECT s.type, s.synced_at AS last, s.duration_seconds"
                " FROM sync_log s"
                " JOIN (SELECT type, MAX(synced_at) AS ts FROM sync_log GROUP BY type) m"
                " ON s.type = m.type AND s.synced_at = m.ts"
            ).fetchall()
    except Exception as e:
        logging.warning("Could not load sync log for %s: %s", db_username, e)

    active_types = {label.split("/")[0] for label in _active_syncs}

    rows = []
    for r in raw_rows:
        stype = r["type"]
        last_ts = r["last"]
        # Mirror the background refresh logic: stable random threshold seeded by last_ts.
        user_threshold = random.Random(int(last_ts)).uniform(MIN_REFRESH_INTERVAL, REFRESH_INTERVAL) if last_ts else REFRESH_INTERVAL
        next_ts = (last_ts + user_threshold) if last_ts else None
        rows.append({
            "type": stype,
            "last": last_ts,
            "duration": _fmt_dur(r["duration_seconds"]),
            "next": next_ts,
            "running": stype in active_types,
        })
    return rows


async def route_sync_page(request: Request):
    """Render the sync status page with trigger buttons and reset option."""
    redir = _require_login(request)
    if redir:
        return redir
    db_username = request.session.get("username", "")
    running = _get_user_lock(db_username).locked()
    sync_rows = _build_sync_rows(db_username)

    ctx = _base_ctx(request, "Sync")
    ctx.update({
        "running": running,
        "sync_rows": sync_rows,
    })
    return templates.TemplateResponse(request, "sync.html", ctx)


async def route_sync_status(request: Request):
    """JSON endpoint polled by the sync page every 30 s."""
    redir = _require_login(request)
    if redir:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    db_username = request.session.get("username", "")
    running = _get_user_lock(db_username).locked()
    rows = _build_sync_rows(db_username)
    return JSONResponse({"running": running, "rows": rows})


async def route_sync_trigger(request: Request):
    """Handle sync trigger button POSTs from the sync page."""
    redir = _require_login(request)
    if redir:
        return redir

    sync_type   = request.path_params["type"]
    scripts_dir = os.path.dirname(SYNC_SCRIPT)
    user_nsid   = request.session.get("user_nsid", "")
    username    = request.session.get("username", "")
    user_args   = ["--nsid", user_nsid, "--username", username] if user_nsid else []

    script_map = {
        "photos":   SYNC_SCRIPT,
        "contacts": os.path.join(scripts_dir, "sync_contacts.py"),
        "groups":   os.path.join(scripts_dir, "sync_groups.py"),
        "albums":   os.path.join(scripts_dir, "sync_albums.py"),
    }

    if sync_type not in script_map and sync_type not in ("all", "backfill"):
        return RedirectResponse("/sync", status_code=303)

    lock = _get_user_lock(username or "_single_user")
    if lock.locked():
        return RedirectResponse("/sync", status_code=303)

    is_full = request.query_params.get("full") == "1"
    is_backfill = sync_type == "backfill"
    if is_backfill:
        photo_args = list(user_args) + ["--backfill"]
    else:
        photo_args = list(user_args) + (["--full"] if is_full else [])

    async def _run():
        async with lock:
            if sync_type == "all":
                for label, path in script_map.items():
                    extra = photo_args if label == "photos" else (user_args or None)
                    await _run_sync_script(path, label, extra_args=extra or None, username=username or None)
            elif is_backfill:
                await _run_sync_script(SYNC_SCRIPT, "photos", extra_args=photo_args or None, username=username or None)
            else:
                extra = photo_args if sync_type == "photos" else (user_args or None)
                await _run_sync_script(script_map[sync_type], sync_type,
                                       extra_args=extra or None, username=username or None)

    asyncio.create_task(_run())
    return RedirectResponse("/sync", status_code=303)


async def route_reset_db(request: Request):
    """Delete the current user's local database.

    Credentials and the MCP API key are preserved.  The user is redirected to
    the sync page where they can trigger a fresh sync.
    """
    redir = _require_login(request)
    if redir:
        return redir
    username = request.session.get("username", "")
    user_nsid = request.session.get("user_nsid", "")
    if username:
        path = db_file(username)
        if os.path.exists(path):
            os.remove(path)
            logging.info("Database reset by user %s", username)
        if user_nsid:
            user_args = ["--nsid", user_nsid, "--username", username]
            asyncio.create_task(_trigger_full_sync(username, user_nsid, user_args, os.path.dirname(SYNC_SCRIPT)))
    return RedirectResponse("/sync", status_code=303)


async def route_regen_key(request: Request):
    """Regenerate the user's MCP API key and update the in-memory registry.

    Generates a new UUID4, removes the old key from ``_api_key_registry``,
    persists the updated credentials, and redirects to /setup.
    Requires login; CSRF is enforced by CSRFMiddleware before this handler runs.
    """
    redir = _require_login(request)
    if redir:
        return redir

    user_nsid = request.session.get("user_nsid", "")
    creds = _load_credentials(nsid=user_nsid)
    old_key = creds.get("mcp_api_key", "")
    new_key = str(uuid.uuid4())

    creds["mcp_api_key"] = new_key
    _save_credentials(creds, user_nsid)

    if old_key in _api_key_registry:
        del _api_key_registry[old_key]
    _api_key_registry[new_key] = user_nsid

    return RedirectResponse("/setup", status_code=303)


async def route_queue(request: Request):
    """GET: show pending group-add queue. POST: flush ready or force-retry all."""
    redir = _require_login(request)
    if redir:
        return redir

    from db import get_db_for_user, _current_user as _ctx
    from tools.groups import _flush_group_queue, _fmt_chicago

    db_username = request.session.get("username", "")
    user_nsid   = request.session.get("user_nsid", "")
    ctx = _base_ctx(request, "Group Queue")
    alert_ok = alert_err = None
    flushed = []

    if request.method == "POST":
        form = getattr(request.state, "form", None) or await request.form()
        action = form.get("action", "retry_ready")
        token = _ctx.set({"nsid": user_nsid, "username": db_username})
        try:
            with get_db_for_user(db_username) as conn:
                if action == "delete_item":
                    try:
                        item_id = int(form.get("item_id", ""))
                    except (ValueError, TypeError):
                        alert_err = "Invalid item ID."
                        item_id = None
                    if item_id is not None:
                        deleted = conn.execute(
                            "DELETE FROM pending_group_adds WHERE id=? AND status='waiting'",
                            (item_id,),
                        ).rowcount
                        alert_ok = "Item removed." if deleted else "Item not found."
                else:
                    force = (action == "retry_all")
                    flushed = _flush_group_queue(conn, force=force)
                    if flushed:
                        ok  = sum(1 for r in flushed if r["result"] == "success")
                        lim = sum(1 for r in flushed if r["result"] == "still_limited")
                        err = sum(1 for r in flushed if r["result"].startswith("error"))
                        parts = []
                        if ok:  parts.append(f"{ok} added")
                        if lim: parts.append(f"{lim} still limited")
                        if err: parts.append(f"{err} errored")
                        alert_ok = "Retry complete: " + ", ".join(parts) + "." if parts else "Nothing to retry."
        except Exception as e:
            logging.exception("route_queue action error")
            alert_err = f"Action failed: {e}"
        finally:
            _ctx.reset(token)

    def _row(r):
        return {
            "id":          r["id"],
            "photo_id":    r["photo_id"],
            "photo_title": r["photo_title"] or r["photo_id"],
            "photo_url":   r["photo_url"] or f"https://www.flickr.com/photo.gne?id={r['photo_id']}",
            "group_id":    r["group_id"],
            "group_name":  r["group_name"] or r["group_id"],
            "group_url":   f"https://www.flickr.com/groups/{r['group_id']}/pool/",
            "retry_at":    _fmt_chicago(r["retry_after"]) if r["retry_after"] else "—",
            "queued_at":   _fmt_chicago(r["queued_at"]),
            "error_msg":   r["error_msg"] or "",
            "completed_at": _fmt_chicago(r["completed_at"]) if r["completed_at"] else "—",
        }

    try:
        with get_db_for_user(db_username) as conn:
            counts = {row["status"]: row["n"] for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM pending_group_adds GROUP BY status"
            ).fetchall()}
            counts.setdefault("waiting", 0)
            counts.setdefault("success", 0)
            counts.setdefault("error", 0)

            waiting_rows = [_row(r) for r in conn.execute(
                "SELECT pga.id, pga.photo_id, pga.group_id, pga.retry_after, pga.queued_at, "
                "       NULL AS error_msg, NULL AS completed_at, "
                "       p.title AS photo_title, p.url_photopage AS photo_url, g.name AS group_name "
                "FROM pending_group_adds pga "
                "LEFT JOIN photos p ON pga.photo_id = p.id "
                "LEFT JOIN groups g ON pga.group_id = g.id "
                "WHERE pga.status='waiting' ORDER BY pga.retry_after ASC"
            ).fetchall()]

            error_rows = [_row(r) for r in conn.execute(
                "SELECT pga.id, pga.photo_id, pga.group_id, pga.queued_at, pga.completed_at, pga.error_msg, "
                "       p.title AS photo_title, p.url_photopage AS photo_url, g.name AS group_name "
                "FROM pending_group_adds pga "
                "LEFT JOIN photos p ON pga.photo_id = p.id "
                "LEFT JOIN groups g ON pga.group_id = g.id "
                "WHERE pga.status='error' ORDER BY pga.queued_at DESC LIMIT 30"
            ).fetchall()]

            success_rows = [_row(r) for r in conn.execute(
                "SELECT pga.id, pga.photo_id, pga.group_id, pga.queued_at, pga.completed_at, "
                "       NULL AS error_msg, "
                "       p.title AS photo_title, p.url_photopage AS photo_url, g.name AS group_name "
                "FROM pending_group_adds pga "
                "LEFT JOIN photos p ON pga.photo_id = p.id "
                "LEFT JOIN groups g ON pga.group_id = g.id "
                "WHERE pga.status='success' ORDER BY pga.completed_at DESC LIMIT 20"
            ).fetchall()]

    except FileNotFoundError:
        ctx["no_db"] = True
        return templates.TemplateResponse(request, "queue.html", ctx)
    except Exception as e:
        logging.exception("route_queue load error")
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "queue.html", ctx)

    ctx.update({
        "counts":       counts,
        "waiting_rows": waiting_rows,
        "error_rows":   error_rows,
        "success_rows": success_rows,
        "alert_ok":     alert_ok,
        "alert_err":    alert_err,
    })
    return templates.TemplateResponse(request, "queue.html", ctx)


async def route_settings(request: Request):
    """GET: render settings form. POST: validate and save changed values."""
    redir = _require_login(request)
    if redir:
        return redir

    from db import get_db_for_user
    db_username = request.session.get("username", "")
    ctx = _base_ctx(request, "Settings")
    alert_ok = alert_err = None

    if request.method == "POST":
        form = getattr(request.state, "form", None) or await request.form()
        errors = []
        updates = {}

        tz_val = (form.get("group_queue_retry_tz") or "").strip()
        if tz_val:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz_val)
                updates["group_queue_retry_tz"] = tz_val
            except Exception:
                errors.append(f"Invalid timezone: {tz_val!r}. Use an IANA name like America/Chicago.")

        retry_val = (form.get("group_queue_default_retry") or "").strip()
        if retry_val:
            parts = retry_val.split(":")
            if len(parts) == 2 and all(p.isdigit() for p in parts) and 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59:
                updates["group_queue_default_retry"] = retry_val
            else:
                errors.append(f"Invalid time: {retry_val!r}. Use HH:MM in 24-hour format.")

        interval_val = (form.get("sync_refresh_interval_hours") or "").strip()
        if interval_val:
            try:
                h = int(interval_val)
                if h < 1 or h > 168:
                    raise ValueError
                updates["sync_refresh_interval_hours"] = str(h)
            except ValueError:
                errors.append("Sync interval must be a whole number of hours between 1 and 168.")

        if errors:
            alert_err = " ".join(errors)
        else:
            try:
                with get_db_for_user(db_username) as conn:
                    for key, value in updates.items():
                        set_setting(conn, key, value)
                alert_ok = "Settings saved."
            except Exception as e:
                logging.exception("route_settings POST error")
                alert_err = f"Save failed: {e}"

    try:
        with get_db_for_user(db_username) as conn:
            settings = [
                {
                    "key":         key,
                    "label":       meta["label"],
                    "description": meta["description"],
                    "default":     meta["default"],
                    "value":       get_setting(conn, key),
                }
                for key, meta in SETTINGS_DEFAULTS.items()
            ]
    except Exception as e:
        logging.warning("route_settings: could not load settings: %s", e)
        settings = [
            {"key": key, "label": meta["label"], "description": meta["description"],
             "default": meta["default"], "value": meta["default"]}
            for key, meta in SETTINGS_DEFAULTS.items()
        ]

    ctx.update({"settings": settings, "alert_ok": alert_ok, "alert_err": alert_err})
    return templates.TemplateResponse(request, "settings.html", ctx)


async def route_setup(request: Request):
    """Render the MCP client setup page with the user's personal API key."""
    redir = _require_login(request)
    if redir:
        return redir

    base = str(request.base_url).rstrip("/")
    sse_url = f"{base}/sse"

    user_nsid   = request.session.get("user_nsid", "")
    mcp_api_key = ""
    if user_nsid:
        try:
            mcp_api_key = _load_credentials(nsid=user_nsid).get("mcp_api_key", "")
        except Exception as e:
            logging.warning("Could not load credentials for setup page (%s): %s", user_nsid, e)

    headers = {"Authorization": f"Bearer {mcp_api_key}"} if mcp_api_key else {}

    claude_code_cfg = {"mcpServers": {"flickr": {"type": "sse", "url": sse_url}}}
    if headers:
        claude_code_cfg["mcpServers"]["flickr"]["headers"] = headers

    cursor_cfg = {"mcpServers": {"flickr": {"url": sse_url}}}
    if headers:
        cursor_cfg["mcpServers"]["flickr"]["headers"] = headers

    opencode_cfg = {"mcp": {"flickr": {"type": "remote", "url": sse_url}}}
    if headers:
        opencode_cfg["mcp"]["flickr"]["headers"] = headers

    try:
        flickr_api_key, flickr_api_secret = _load_env()
    except Exception:
        flickr_api_key, flickr_api_secret = "", ""

    stdio_args = [
        "run", "-i", "--rm",
        "-e", f"FLICKR_API_KEY={flickr_api_key}",
        "-e", f"FLICKR_API_SECRET={flickr_api_secret}",
        "-e", "MCP_TRANSPORT=stdio",
    ]
    if mcp_api_key:
        stdio_args += ["-e", f"MCP_API_KEY={mcp_api_key}"]
    stdio_args += [
        "-v", "flickr-creds:/home/app/.flickr_mcp",
        "-v", "flickr-data:/app/data",
        "ejwettstein/flickr-mcp",
    ]
    stdio_cfg = {"mcpServers": {"flickr": {"command": "docker", "args": stdio_args}}}

    exec_key_part = f" MCP_API_KEY={mcp_api_key}" if mcp_api_key else " MCP_API_KEY=<your-api-key>"
    stdio_exec_cmd = (
        "docker compose exec -T flickr-mcp \\\n"
        f"  env MCP_TRANSPORT=stdio{exec_key_part} \\\n"
        "  python scripts/flickr_mcp.py"
    )

    snippets = {
        "claude_code":    json.dumps(claude_code_cfg, indent=2),
        "claude_desktop": json.dumps(claude_code_cfg, indent=2),
        "cursor":         json.dumps(cursor_cfg, indent=2),
        "windsurf":       json.dumps(cursor_cfg, indent=2),
        "opencode":       json.dumps(opencode_cfg, indent=2),
        "stdio":          json.dumps(stdio_cfg, indent=2),
        "stdio_exec":     stdio_exec_cmd,
    }

    ctx = _base_ctx(request, "Setup")
    ctx.update({
        "sse_url":   sse_url,
        "base_url":  base,
        "mcp_api_key": mcp_api_key,
        "snippets":  snippets,
    })
    return templates.TemplateResponse(request, "setup.html", ctx)




# --- SSE handler and API key middleware ---

class _SSEHandler:
    """ASGI handler for the MCP SSE endpoint.

    Sets ``db._current_user`` from the API key resolved by ``ApiKeyMiddleware``
    so that all tool calls within a connection operate on the correct per-user
    database and credentials.
    """

    def __init__(self, sse_transport):
        self._sse = sse_transport

    async def __call__(self, scope, receive, send):
        state = scope.get("state") or {}
        if isinstance(state, dict):
            user_nsid = state.get("user_nsid")
        else:
            user_nsid = getattr(state, "user_nsid", None)
        token = None
        if user_nsid:
            try:
                creds = _load_credentials(nsid=user_nsid)
                user_ctx = {
                    "nsid":     user_nsid,
                    "username": creds.get("username", user_nsid),
                }
                token = _db_current_user.set(user_ctx)
            except Exception as e:
                logging.error("SSE handler: failed to load credentials for %s: %s", user_nsid, e)
        try:
            async with self._sse.connect_sse(scope, receive, send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())
        finally:
            if token is not None:
                _db_current_user.reset(token)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Validate MCP API keys and attach the resolved user NSID to request state.

    Every request to ``/sse`` or ``/messages`` must carry a valid API key via
    the ``X-API-Key`` header or ``Authorization: Bearer`` header.  The key is
    looked up in ``_api_key_registry`` (populated at startup and on each login)
    and the matched NSID is stored in ``request.state.user_nsid`` for the SSE
    handler to consume.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/sse") or path.startswith("/messages"):
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
            if path.startswith("/sse") or path.startswith("/messages"):
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


async def main_sse():
    """Start the MCP SSE server with the Starlette web application.

    Loads the per-user API key registry, configures middleware (session,
    CSRF, API key auth), registers all routes, starts the background refresh
    task, and begins serving on ``MCP_PORT``.
    """
    from mcp.server.sse import SseServerTransport
    import uvicorn

    _load_api_key_registry()

    sse = SseServerTransport("/messages/")
    middleware = [
        Middleware(
            SessionMiddleware,
            secret_key=SESSION_SECRET_KEY,
            max_age=30 * 24 * 3600,
            https_only=False,
            same_site="lax",
        ),
        Middleware(CSRFMiddleware),
        Middleware(ApiKeyMiddleware),
    ]

    app = Starlette(
        middleware=middleware,
        routes=[
            Route("/",               endpoint=route_root),
            Route("/login",          endpoint=route_login),
            Route("/login/start",    endpoint=route_login_start),
            Route("/oauth/callback", endpoint=route_oauth_callback),
            Route("/logout",         endpoint=route_logout, methods=["POST"]),
            Route("/stats",          endpoint=route_stats),
            Route("/sync",           endpoint=route_sync_page),
            # status.json MUST precede the {type} catch-all — Starlette matches top-to-bottom
            Route("/sync/status.json", endpoint=route_sync_status),
            Route("/sync/{type}",    endpoint=route_sync_trigger, methods=["POST"]),
            Route("/reset",          endpoint=route_reset_db, methods=["POST"]),
            Route("/regen-key",      endpoint=route_regen_key, methods=["POST"]),
            Route("/queue",          endpoint=route_queue, methods=["GET", "POST"]),
            Route("/settings",       endpoint=route_settings, methods=["GET", "POST"]),
            Route("/setup",          endpoint=route_setup),
            Route("/sse",            endpoint=_SSEHandler(sse)),
            Mount("/messages/",      app=sse.handle_post_message),
            Mount("/static",         app=StaticFiles(directory=str(_PROJECT_ROOT / "static")), name="static"),
        ],
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=MCP_PORT, log_level="info")
    uv_server = uvicorn.Server(config)

    asyncio.create_task(_background_refresh())
    logging.info("SSE ready on port %d", MCP_PORT)
    await uv_server.serve()
