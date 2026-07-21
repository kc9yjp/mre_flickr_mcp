"""JSON API for the workbench SPA.

All endpoints live under ``/api`` and use the same browser session cookie as
the server-rendered pages.  POSTs are CSRF-protected by ``CSRFMiddleware`` in
``web.py``, which accepts the session token via the ``X-CSRF-Token`` header
for ``/api/`` paths.

Handlers read the per-user SQLite database directly with ``get_db_for_user``
(same pattern as ``route_stats``) rather than round-tripping through MCP tool
handlers, so responses are structured JSON instead of formatted text.
"""

import logging
import os
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from db import db_file, get_db_for_user, SETTINGS_DEFAULTS, get_setting, set_setting
from mcp_tools import _get_user_lock

# Accessed as attributes at call time so tests can reload/patch web freely.
import web as _web

_PHOTO_COLUMNS = (
    "id, title, description, date_taken, date_uploaded, last_updated, "
    "url_photopage, url_original, url_medium, tags, views, favorites, "
    "comments, is_public, synced_at"
)

_SORTABLE = ("date_taken", "views", "favorites", "comments", "date_uploaded")

SYNC_TYPES = ("photos", "contacts", "groups", "albums", "all", "backfill")


def _session_user(request: Request) -> dict | None:
    """Return the logged-in user from the session, or None."""
    nsid = request.session.get("user_nsid")
    if not nsid:
        return None
    return {
        "nsid": nsid,
        "username": request.session.get("username", ""),
        "fullname": request.session.get("fullname", ""),
    }


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthenticated"}, status_code=401)


def _no_db(username: str) -> JSONResponse | None:
    """404 when the user's database doesn't exist yet (never synced)."""
    if not os.path.exists(db_file(username)):
        return JSONResponse(
            {"error": "no database — run a sync first"}, status_code=404
        )
    return None


async def api_me(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)
    return JSONResponse({**user, "csrf_token": request.session["csrf_token"]})


async def api_photos(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if err := _no_db(user["username"]):
        return err

    qp = request.query_params
    conditions, params = [], []
    if qp.get("query"):
        conditions.append("(title LIKE ? OR description LIKE ?)")
        params += [f"%{qp['query']}%"] * 2
    if qp.get("tags"):
        conditions.append("tags LIKE ?")
        params.append(f"%{qp['tags']}%")
    if qp.get("date_from"):
        conditions.append("date_taken >= ?")
        params.append(qp["date_from"])
    if qp.get("date_to"):
        conditions.append("date_taken <= ?")
        params.append(qp["date_to"] + " 23:59:59")
    if qp.get("is_public") in ("0", "1"):
        conditions.append("is_public = ?")
        params.append(int(qp["is_public"]))
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sort_by = qp.get("sort", "date_taken")
    if sort_by == "random":
        order_clause = "RANDOM()"
    else:
        if sort_by not in _SORTABLE:
            sort_by = "date_taken"
        order = "ASC" if qp.get("order") == "asc" else "DESC"
        order_clause = f"{sort_by} {order}"

    try:
        limit = min(int(qp.get("limit", 50)), 200)
        offset = max(int(qp.get("offset", 0)), 0)
    except ValueError:
        return JSONResponse({"error": "limit/offset must be integers"}, status_code=400)

    with get_db_for_user(user["username"]) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM photos {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_PHOTO_COLUMNS} FROM photos {where} "
            f"ORDER BY {order_clause} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return JSONResponse({
        "total": total,
        "offset": offset,
        "photos": [dict(r) for r in rows],
    })


async def api_photo_detail(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if err := _no_db(user["username"]):
        return err

    photo_id = request.path_params["id"]
    with get_db_for_user(user["username"]) as conn:
        row = conn.execute(
            f"SELECT {_PHOTO_COLUMNS} FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"error": "photo not found"}, status_code=404)
        groups = conn.execute(
            "SELECT g.id, g.name FROM photo_groups pg "
            "JOIN groups g ON pg.group_id = g.id WHERE pg.photo_id = ? "
            "ORDER BY g.name",
            (photo_id,),
        ).fetchall()
        keeper = conn.execute(
            "SELECT 1 FROM keeper_list WHERE photo_id = ?", (photo_id,)
        ).fetchone()

    return JSONResponse({
        **dict(row),
        "groups": [dict(g) for g in groups],
        "in_keeper_list": bool(keeper),
    })


async def api_stats(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if err := _no_db(user["username"]):
        return err

    with get_db_for_user(user["username"]) as conn:
        stats = conn.execute("""
            SELECT COUNT(*) AS total_photos,
                   SUM(CASE WHEN is_public = 1 THEN 1 ELSE 0 END) AS public_photos,
                   SUM(CASE WHEN is_public = 0 THEN 1 ELSE 0 END) AS private_photos,
                   SUM(views) AS total_views,
                   MIN(date_taken) AS earliest,
                   MAX(date_taken) AS latest,
                   MAX(synced_at) AS last_synced
            FROM photos
        """).fetchone()
        tag_rows = conn.execute(
            "SELECT tags FROM photos WHERE tags != '' AND tags IS NOT NULL"
        ).fetchall()
        group_count   = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        album_count   = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        contact_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    counts: dict[str, int] = {}
    for row in tag_rows:
        for tag in (row[0] or "").split():
            counts[tag] = counts.get(tag, 0) + 1
    top_tags = [
        {"tag": t, "count": c}
        for t, c in sorted(counts.items(), key=lambda x: -x[1])[:20]
    ]

    return JSONResponse({
        "total_photos":   stats["total_photos"] or 0,
        "public_photos":  stats["public_photos"] or 0,
        "private_photos": stats["private_photos"] or 0,
        "total_views":    stats["total_views"] or 0,
        "total_groups":   group_count,
        "total_albums":   album_count,
        "total_contacts": contact_count,
        "date_range":     {"earliest": stats["earliest"], "latest": stats["latest"]},
        "last_synced":    stats["last_synced"],
        "top_tags":       top_tags,
    })


async def api_sync_status(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    username = user["username"]
    return JSONResponse({
        "running": _get_user_lock(username).locked(),
        "rows": _web._build_sync_rows(username),
    })


async def api_sync_trigger(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()

    sync_type = request.path_params["type"]
    if sync_type not in SYNC_TYPES:
        return JSONResponse({"error": f"unknown sync type: {sync_type}"}, status_code=400)

    username = user["username"]
    if _get_user_lock(username or "_single_user").locked():
        return JSONResponse({"started": False, "reason": "sync already running"}, status_code=409)

    full = request.query_params.get("full") == "1"
    started = _web._start_sync(sync_type, username, user["nsid"], full=full)
    if not started:
        logging.warning("api_sync_trigger: sync %s did not start for %s", sync_type, username)
        return JSONResponse({"started": False, "reason": "could not start"}, status_code=409)
    return JSONResponse({"started": True, "type": sync_type, "full": full})


async def api_queue(request: Request):
    """GET /api/queue — queue data; POST /api/queue — retry/delete actions."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if err := _no_db(user["username"]):
        return err

    from db import _current_user as _ctx
    from tools.groups import _flush_group_queue, _fmt_chicago

    username = user["username"]

    if request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        action = body.get("action", "")
        token = _ctx.set(user)
        try:
            with get_db_for_user(username) as conn:
                if action == "delete_item":
                    item_id = body.get("item_id")
                    if not isinstance(item_id, int):
                        return JSONResponse({"error": "item_id required"}, status_code=400)
                    deleted = conn.execute(
                        "DELETE FROM pending_group_adds WHERE id=? AND status='waiting'",
                        (item_id,),
                    ).rowcount
                    return JSONResponse({"ok": True, "deleted": deleted})
                elif action in ("retry_ready", "retry_all"):
                    flushed = _flush_group_queue(conn, force=(action == "retry_all"))
                    ok  = sum(1 for r in flushed if r["result"] == "success")
                    lim = sum(1 for r in flushed if r["result"] == "still_limited")
                    err = sum(1 for r in flushed if r["result"].startswith("error"))
                    return JSONResponse({"ok": True, "added": ok, "limited": lim, "errors": err})
                else:
                    return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)
        except Exception as e:
            logging.exception("api_queue action error")
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            _ctx.reset(token)

    def _row(r):
        return {
            "id":           r["id"],
            "photo_id":     r["photo_id"],
            "photo_title":  r["photo_title"] or r["photo_id"],
            "photo_url":    r["photo_url"] or f"https://www.flickr.com/photo.gne?id={r['photo_id']}",
            "group_id":     r["group_id"],
            "group_name":   r["group_name"] or r["group_id"],
            "group_url":    f"https://www.flickr.com/groups/{r['group_id']}/pool/",
            "retry_at":     _fmt_chicago(r["retry_after"]) if r["retry_after"] else None,
            "queued_at":    _fmt_chicago(r["queued_at"]),
            "error_msg":    r["error_msg"] or "",
            "completed_at": _fmt_chicago(r["completed_at"]) if r["completed_at"] else None,
        }

    try:
        with get_db_for_user(username) as conn:
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
                "       NULL AS retry_after, "
                "       p.title AS photo_title, p.url_photopage AS photo_url, g.name AS group_name "
                "FROM pending_group_adds pga "
                "LEFT JOIN photos p ON pga.photo_id = p.id "
                "LEFT JOIN groups g ON pga.group_id = g.id "
                "WHERE pga.status='error' ORDER BY pga.queued_at DESC LIMIT 30"
            ).fetchall()]

            success_rows = [_row(r) for r in conn.execute(
                "SELECT pga.id, pga.photo_id, pga.group_id, pga.queued_at, pga.completed_at, "
                "       NULL AS error_msg, NULL AS retry_after, "
                "       p.title AS photo_title, p.url_photopage AS photo_url, g.name AS group_name "
                "FROM pending_group_adds pga "
                "LEFT JOIN photos p ON pga.photo_id = p.id "
                "LEFT JOIN groups g ON pga.group_id = g.id "
                "WHERE pga.status='success' ORDER BY pga.completed_at DESC LIMIT 20"
            ).fetchall()]

    except Exception as e:
        logging.exception("api_queue load error")
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({
        "counts": counts,
        "waiting": waiting_rows,
        "errors": error_rows,
        "successes": success_rows,
    })


async def api_setup(request: Request):
    """GET /api/setup — MCP client config snippets and bookmarklet."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    import json as _json
    from web import _load_credentials, _load_env

    base = str(request.base_url).rstrip("/")
    sse_url = f"{base}/sse"
    mcp_url = f"{base}/mcp"

    mcp_api_key = ""
    try:
        mcp_api_key = _load_credentials(nsid=user["nsid"]).get("mcp_api_key", "")
    except Exception:
        pass

    headers = {"Authorization": f"Bearer {mcp_api_key}"} if mcp_api_key else {}

    claude_code_cfg = {"mcpServers": {"flickr": {"type": "http", "url": mcp_url}}}
    if headers:
        claude_code_cfg["mcpServers"]["flickr"]["headers"] = headers
    claude_code_sse_cfg = {"mcpServers": {"flickr": {"type": "sse", "url": sse_url}}}
    if headers:
        claude_code_sse_cfg["mcpServers"]["flickr"]["headers"] = headers
    cursor_cfg = {"mcpServers": {"flickr": {"url": mcp_url}}}
    if headers:
        cursor_cfg["mcpServers"]["flickr"]["headers"] = headers
    opencode_cfg = {"mcp": {"flickr": {"type": "remote", "url": mcp_url}}}
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
    stdio_args += ["-v", "flickr-creds:/home/app/.flickr_mcp", "-v", "flickr-data:/app/data", "ejwettstein/flickr-mcp"]
    stdio_cfg = {"mcpServers": {"flickr": {"command": "docker", "args": stdio_args}}}

    bookmarklet = (
        "javascript:(function(){"
        "var m=location.href.match(/flickr\\.com\\/photos\\/[^\\/]+\\/(\\d+)/);"
        f"if(m){{window.open('{base}/app/#photo='+m[1]);}}"
        "else{alert('Not a Flickr photo page');}"
        "})();"
    )

    return JSONResponse({
        "base_url":    base,
        "mcp_url":     mcp_url,
        "sse_url":     sse_url,
        "has_api_key": bool(mcp_api_key),
        "snippets": {
            "claude_code":     _json.dumps(claude_code_cfg, indent=2),
            "claude_code_sse": _json.dumps(claude_code_sse_cfg, indent=2),
            "claude_desktop":  _json.dumps(claude_code_cfg, indent=2),
            "cursor":          _json.dumps(cursor_cfg, indent=2),
            "windsurf":        _json.dumps(cursor_cfg, indent=2),
            "opencode":        _json.dumps(opencode_cfg, indent=2),
            "stdio":           _json.dumps(stdio_cfg, indent=2),
        },
        "bookmarklet": bookmarklet,
    })


async def api_reset(request: Request):
    """POST /api/reset — delete the user's local database and trigger a fresh sync."""
    import asyncio
    from mcp_tools import SYNC_SCRIPT
    user = _session_user(request)
    if not user:
        return _unauthorized()
    username = user["username"]
    path = db_file(username)
    if os.path.exists(path):
        os.remove(path)
        logging.info("Database reset via API by user %s", username)
    asyncio.create_task(_web._trigger_full_sync(
        username, user["nsid"],
        ["--nsid", user["nsid"], "--username", username],
        os.path.dirname(SYNC_SCRIPT),
    ))
    return JSONResponse({"ok": True})


async def api_regen_key(request: Request):
    """POST /api/regen-key — regenerate the user's MCP API key."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    from web import _load_credentials, _save_credentials, _api_key_registry
    nsid = user["nsid"]
    creds = _load_credentials(nsid=nsid)
    old_key = creds.get("mcp_api_key", "")
    new_key = str(__import__("uuid").uuid4())
    if old_key in _api_key_registry:
        del _api_key_registry[old_key]
    creds["mcp_api_key"] = new_key
    _save_credentials(creds, nsid)
    _api_key_registry[new_key] = nsid
    return JSONResponse({"ok": True})


async def api_settings(request: Request):
    """GET /api/settings — list settings with values; POST — save updates."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    username = user["username"]

    if request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        errors = []
        updates: dict[str, str] = {}

        tz_val = (body.get("group_queue_retry_tz") or "").strip()
        if tz_val:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz_val)
                updates["group_queue_retry_tz"] = tz_val
            except Exception:
                errors.append(f"Invalid timezone: {tz_val!r}. Use an IANA name like America/Chicago.")

        retry_val = (body.get("group_queue_default_retry") or "").strip()
        if retry_val:
            parts = retry_val.split(":")
            if len(parts) == 2 and all(p.isdigit() for p in parts) and 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59:
                updates["group_queue_default_retry"] = retry_val
            else:
                errors.append(f"Invalid time: {retry_val!r}. Use HH:MM in 24-hour format.")

        interval_val = (body.get("sync_refresh_interval_hours") or "").strip()
        if interval_val:
            try:
                h = int(interval_val)
                if h < 1 or h > 168:
                    raise ValueError
                updates["sync_refresh_interval_hours"] = str(h)
            except ValueError:
                errors.append("Sync interval must be 1–168 hours.")

        if errors:
            return JSONResponse({"error": " ".join(errors)}, status_code=400)

        if err := _no_db(username):
            return err
        try:
            with get_db_for_user(username) as conn:
                for key, value in updates.items():
                    set_setting(conn, key, value)
        except Exception as e:
            logging.exception("api_settings POST error")
            return JSONResponse({"error": str(e)}, status_code=500)

    try:
        with get_db_for_user(username) as conn:
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
    except Exception:
        settings = [
            {"key": key, "label": meta["label"], "description": meta["description"],
             "default": meta["default"], "value": meta["default"]}
            for key, meta in SETTINGS_DEFAULTS.items()
        ]

    return JSONResponse({"settings": settings})


def api_routes() -> list[Route]:
    """Routes to register in the main Starlette app (see ``web.main_sse``)."""
    return [
        Route("/api/me",           endpoint=api_me),
        Route("/api/photos",       endpoint=api_photos),
        Route("/api/photos/{id}",  endpoint=api_photo_detail),
        Route("/api/stats",        endpoint=api_stats),
        Route("/api/sync/status",  endpoint=api_sync_status),
        Route("/api/sync/{type}",  endpoint=api_sync_trigger, methods=["POST"]),
        Route("/api/queue",        endpoint=api_queue, methods=["GET", "POST"]),
        Route("/api/setup",        endpoint=api_setup),
        Route("/api/reset",        endpoint=api_reset, methods=["POST"]),
        Route("/api/settings",     endpoint=api_settings, methods=["GET", "POST"]),
        Route("/api/regen-key",    endpoint=api_regen_key, methods=["POST"]),
    ]
