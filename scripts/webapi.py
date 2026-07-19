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

from db import db_file, get_db_for_user
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


def api_routes() -> list[Route]:
    """Routes to register in the main Starlette app (see ``web.main_sse``)."""
    return [
        Route("/api/me",           endpoint=api_me),
        Route("/api/photos",       endpoint=api_photos),
        Route("/api/photos/{id}",  endpoint=api_photo_detail),
        Route("/api/stats",        endpoint=api_stats),
        Route("/api/sync/status",  endpoint=api_sync_status),
        Route("/api/sync/{type}",  endpoint=api_sync_trigger, methods=["POST"]),
    ]
