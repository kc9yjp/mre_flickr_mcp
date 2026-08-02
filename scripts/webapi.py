"""JSON API for the workbench SPA.

All endpoints live under ``/api`` and use the same browser session cookie as
the server-rendered pages.  POSTs are CSRF-protected by ``CSRFMiddleware`` in
``web.py``, which accepts the session token via the ``X-CSRF-Token`` header
for ``/api/`` paths.

Handlers read the per-user SQLite database directly with ``get_db_for_user``
(same pattern as ``route_stats``) rather than round-tripping through MCP tool
handlers, so responses are structured JSON instead of formatted text.
"""

import json
import logging
import math
import os
import re
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import flickr_api
from db import db_file, get_db_for_user, SETTINGS_DEFAULTS, get_setting, set_setting
from mcp_tools import _get_user_lock, cancel_sync

# Accessed as attributes at call time so tests can reload/patch web freely.
import web as _web

_PHOTO_COLUMNS = (
    "id, title, description, date_taken, date_uploaded, last_updated, "
    "url_photopage, url_original, url_medium, tags, views, favorites, "
    "comments, is_public, synced_at"
)

_SORTABLE = ("date_taken", "views", "favorites", "comments", "date_uploaded")

SYNC_TYPES = ("photos", "contacts", "groups", "albums", "all", "backfill")

# extras requested on every live "list of photos" call below, matched by
# _photo_dict_from_api's field mapping.
_LIVE_PHOTO_EXTRAS = "url_m,url_o,date_taken,views,count_faves,count_comments"

_GROUP_URL_RE = re.compile(r"flickr\.com/groups/([^/\s]+)")


def _photo_dict_from_api(p: dict, owner: str | None = None) -> dict:
    """Map one raw Flickr photo dict (from a photos.search/getPhotos-style
    response fetched with extras=_LIVE_PHOTO_EXTRAS) into the same shape as
    _PHOTO_COLUMNS/the frontend Photo interface, for photos that were never
    synced to the local DB (someone else's photostream, a group pool, search
    results) — mirrors the field set api_photos/api_album_photos return.

    ``owner`` overrides the response's own "owner" field — flickr.photosets.
    getPhotos doesn't embed it (it's implied by the request's user_id), so
    callers that already know the owner nsid should pass it explicitly."""
    owner = owner or p.get("owner", "")
    return {
        "id":            p["id"],
        "title":         p.get("title", ""),
        "description":   "",
        "date_taken":    p.get("datetaken", ""),
        "date_uploaded": 0,
        "last_updated":  0,
        "url_photopage": f"https://www.flickr.com/photos/{owner}/{p['id']}/",
        "url_original":  p.get("url_o"),
        "url_medium":    p.get("url_m"),
        "tags":          "",
        "views":         int(p.get("views", 0) or 0),
        "favorites":     int(p.get("count_faves", 0) or 0),
        "comments":      int(p.get("count_comments", 0) or 0),
        "is_public":     1,
        "synced_at":     0,
    }


def _parse_page_limit(qp, default_limit: int = 60, max_limit: int = 200) -> tuple[int, int]:
    """Shared page/limit parsing for the page-based live-listing routes
    (raises ValueError, matching the pattern each caller already converts
    into a 400 response)."""
    limit = min(int(qp.get("limit", default_limit)), max_limit)
    page = max(int(qp.get("page", 1)), 1)
    return page, limit


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

    if qp.get("ids"):
        ids = [i for i in qp["ids"].split(",") if i]
        rows = []
        if ids:
            with get_db_for_user(user["username"]) as conn:
                rows = conn.execute(
                    f"SELECT {_PHOTO_COLUMNS} FROM photos WHERE id IN "
                    f"({','.join('?' * len(ids))})",
                    ids,
                ).fetchall()
        by_id = {r["id"]: dict(r) for r in rows}
        photos = [by_id[i] for i in ids if i in by_id]
        return JSONResponse({"total": len(photos), "offset": 0, "photos": photos})

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


def _photo_contexts(photo_id: str) -> tuple[list[dict], list[dict]]:
    """Album (photoset) and group (pool) membership for a photo, fetched live
    from the Flickr API via getAllContexts — never read from the local cache.
    Returns ``(albums, groups)``. Caller must have already set the
    ``_current_user`` context."""
    try:
        data = flickr_api._api_get("flickr.photos.getAllContexts", {"photo_id": photo_id})
    except Exception as e:
        logging.warning("failed to fetch contexts for photo %s: %s", photo_id, e)
        return [], []
    albums = [{"id": s["id"], "title": s.get("title", "")} for s in data.get("set", [])]
    groups = [{"id": p["id"], "name": p.get("title", "")} for p in data.get("pool", [])]
    return albums, groups


def _in_keeper_list(username: str, photo_id: str) -> bool:
    """Keeper-list membership is a local-only annotation with no Flickr
    equivalent, so it is read from the per-user database."""
    if not os.path.exists(db_file(username)):
        return False
    with get_db_for_user(username) as conn:
        return conn.execute(
            "SELECT 1 FROM keeper_list WHERE photo_id = ?", (photo_id,)
        ).fetchone() is not None


async def api_photo_detail(request: Request):
    """GET /api/photos/{id} — photo detail fetched live from the Flickr API.

    Core metadata, sizes, favorites, and album/group membership all come
    straight from Flickr so the detail view never shows stale cached values.
    Only the keeper-list flag — a local-only annotation with no Flickr
    equivalent — is read from the per-user database.
    """
    user = _session_user(request)
    if not user:
        return _unauthorized()

    photo_id = request.path_params["id"]

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        payload = _build_photo_detail(photo_id, user)
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    payload["in_keeper_list"] = _in_keeper_list(user["username"], photo_id)
    return JSONResponse(payload)


def _build_photo_detail(photo_id: str, user: dict) -> dict:
    """Assemble a photo's detail payload entirely from the Flickr API.

    Caller must have already set the ``_current_user`` context. Raises
    ``FlickrAPIError`` (or another exception) on API failure so the handler
    can translate it into the right HTTP status."""
    info = flickr_api._api_get("flickr.photos.getInfo", {"photo_id": photo_id})
    sizes_data = flickr_api._api_get("flickr.photos.getSizes", {"photo_id": photo_id})
    try:
        faves_data = flickr_api._api_get(
            "flickr.photos.getFavorites", {"photo_id": photo_id, "per_page": "1"}
        )
        favorites = int(faves_data.get("photo", {}).get("total", 0) or 0)
    except Exception:
        favorites = 0
    albums, groups = _photo_contexts(photo_id)

    photo = info["photo"]
    owner = photo.get("owner", {})
    sizes = sizes_data.get("sizes", {}).get("size", [])

    def _pick(labels):
        return next(
            (s["source"] for label in labels for s in sizes if s["label"] == label),
            None,
        )

    url_original = _pick(("Original", "Large 2048", "Large 1600", "Large"))
    if not url_original and sizes:
        url_original = sizes[-1]["source"]
    url_medium = _pick(("Medium 640", "Medium"))

    tags = " ".join(t["raw"] for t in photo.get("tags", {}).get("tag", []))

    iconserver = str(owner.get("iconserver", "0"))
    iconfarm = owner.get("iconfarm", 0)
    owner_nsid = owner.get("nsid", "")
    if iconserver and iconserver != "0":
        avatar_url = f"https://farm{iconfarm}.staticflickr.com/{iconserver}/buddyicons/{owner_nsid}.jpg"
    else:
        avatar_url = "https://www.flickr.com/images/buddyicon.gif"

    is_own = bool(owner_nsid) and owner_nsid == user.get("nsid")

    return {
        "id":             photo_id,
        "title":          photo.get("title", {}).get("_content", ""),
        "description":    photo.get("description", {}).get("_content", ""),
        "date_taken":     photo.get("dates", {}).get("taken", ""),
        "date_uploaded":  int(photo.get("dates", {}).get("posted", 0) or 0),
        "last_updated":   int(photo.get("dates", {}).get("lastupdate", 0) or 0),
        "url_photopage":  f"https://www.flickr.com/photos/{owner_nsid}/{photo_id}/",
        "url_original":   url_original,
        "url_medium":     url_medium,
        "tags":           tags,
        "views":          int(photo.get("views", 0) or 0),
        "favorites":      favorites,
        "comments":       int(photo.get("comments", {}).get("_content", 0) or 0),
        "is_public":      1 if photo.get("visibility", {}).get("ispublic", 1) else 0,
        "synced_at":      0,
        "groups":         groups,
        "albums":         albums,
        "is_own":         is_own,
        "owner": {
            "nsid":       owner_nsid,
            "username":   owner.get("username", ""),
            "realname":   owner.get("realname", ""),
            "profile_url": f"https://www.flickr.com/people/{owner_nsid}/",
            "avatar_url": avatar_url,
        },
    }


async def api_photo_fave(request: Request):
    """POST /api/photos/{id}/fave — add any Flickr photo to the user's favorites."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        flickr_api._api_post("flickr.favorites.add", {"photo_id": request.path_params["id"]})
    except flickr_api.FlickrAPIError as e:
        return JSONResponse({"error": e.flickr_message}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)
    return JSONResponse({"ok": True})


async def api_photo_comment(request: Request):
    """POST /api/photos/{id}/comment — post a comment on any Flickr photo."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    comment_text = (body.get("comment_text") or "").strip()
    if not comment_text:
        return JSONResponse({"error": "comment_text is required"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_post("flickr.photos.comments.addComment", {
            "photo_id":     request.path_params["id"],
            "comment_text": comment_text,
        })
    except flickr_api.FlickrAPIError as e:
        return JSONResponse({"error": e.flickr_message}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)
    return JSONResponse({"ok": True, "comment_id": data.get("comment", {}).get("id", "")})


async def api_albums(request: Request):
    """GET /api/albums — the user's albums, with a cover thumbnail if synced."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if err := _no_db(user["username"]):
        return err

    with get_db_for_user(user["username"]) as conn:
        rows = conn.execute(
            "SELECT a.id, a.title, a.description, a.count_photos, a.count_views, "
            "       p.url_medium, p.url_original "
            "FROM albums a LEFT JOIN photos p ON p.id = a.primary_photo_id "
            "ORDER BY a.title COLLATE NOCASE"
        ).fetchall()

    return JSONResponse({
        "albums": [
            {
                "id":           r["id"],
                "title":        r["title"],
                "description":  r["description"],
                "count_photos": r["count_photos"],
                "count_views":  r["count_views"],
                "thumb_url":    r["url_medium"] or r["url_original"],
            }
            for r in rows
        ],
    })


async def api_album_photos(request: Request):
    """GET /api/albums/{id}/photos — photos in an album, joined against the
    local DB for thumbnails/stats. Order follows the album's Flickr order."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if err := _no_db(user["username"]):
        return err

    album_id = request.path_params["id"]
    qp = request.query_params
    try:
        limit = min(int(qp.get("limit", 60)), 200)
        page = max(int(qp.get("page", 1)), 1)
    except ValueError:
        return JSONResponse({"error": "limit/page must be integers"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        creds = flickr_api._load_credentials()
        data = flickr_api._api_get("flickr.photosets.getPhotos", {
            "photoset_id": album_id,
            "user_id": creds["user_nsid"],
            "per_page": str(limit),
            "page": str(page),
        })
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    photoset = data.get("photoset", {})
    ids = [p["id"] for p in photoset.get("photo", [])]
    total = int(photoset.get("total", 0) or 0)
    pages = int(photoset.get("pages", 0) or 0)

    photos_by_id: dict = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        with get_db_for_user(user["username"]) as conn:
            rows = conn.execute(
                f"SELECT {_PHOTO_COLUMNS} FROM photos WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        photos_by_id = {r["id"]: dict(r) for r in rows}

    return JSONResponse({
        "total": total,
        "page": page,
        "pages": pages,
        "photos": [photos_by_id[i] for i in ids if i in photos_by_id],
    })


async def api_user_lookup(request: Request):
    """GET /api/users/lookup?q=<username|nsid|profile-or-photo-URL> —
    resolve any Flickr user reference to a full profile, for the Photo
    Browser's "User" browse mode."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "q is required"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        nsid = flickr_api.resolve_user_id(q)
        data = flickr_api._api_get("flickr.people.getInfo", {"user_id": nsid})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    p = data.get("person", {})
    resolved_nsid = p.get("nsid", nsid)
    iconserver = str(p.get("iconserver", "0"))
    iconfarm = p.get("iconfarm", 0)
    if iconserver and iconserver != "0":
        avatar_url = f"https://farm{iconfarm}.staticflickr.com/{iconserver}/buddyicons/{resolved_nsid}.jpg"
    else:
        avatar_url = "https://www.flickr.com/images/buddyicon.gif"

    return JSONResponse({
        "nsid":        resolved_nsid,
        "username":    p.get("username", {}).get("_content", ""),
        "realname":    p.get("realname", {}).get("_content", ""),
        "location":    p.get("location", {}).get("_content", ""),
        "description": p.get("description", {}).get("_content", ""),
        "photo_count": int(p.get("photos", {}).get("count", {}).get("_content", 0) or 0),
        "profile_url": f"https://www.flickr.com/people/{resolved_nsid}/",
        "avatar_url":  avatar_url,
        "is_own":      resolved_nsid == user["nsid"],
        "you_follow":  bool(p.get("contact")),
        "follows_you": bool(p.get("revcontact")),
        "is_friend":   bool(p.get("friend")),
        "is_family":   bool(p.get("family")),
    })


async def api_user_photos(request: Request):
    """GET /api/users/{nsid}/photos?page=&limit= — any user's public
    photostream, fetched live (never cached locally)."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    nsid = request.path_params["nsid"]
    try:
        page, limit = _parse_page_limit(request.query_params)
    except ValueError:
        return JSONResponse({"error": "page/limit must be integers"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_get("flickr.people.getPhotos", {
            "user_id": nsid, "per_page": str(limit), "page": str(page),
            "extras": _LIVE_PHOTO_EXTRAS,
        })
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    container = data.get("photos", {})
    photos = [_photo_dict_from_api(p, owner=nsid) for p in container.get("photo", [])]
    return JSONResponse({
        "total": int(container.get("total", 0) or 0),
        "page":  page,
        "pages": int(container.get("pages", 0) or 0),
        "photos": photos,
    })


async def api_user_albums(request: Request):
    """GET /api/users/{nsid}/albums — any user's public albums, fetched live."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    nsid = request.path_params["nsid"]

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_get("flickr.photosets.getList", {
            "user_id": nsid, "primary_photo_extras": "url_q",
        })
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    albums = [
        {
            "id":           s["id"],
            "title":        s.get("title", {}).get("_content", ""),
            "description":  s.get("description", {}).get("_content", ""),
            "count_photos": int(s.get("photos", 0) or 0) + int(s.get("videos", 0) or 0),
            "count_views":  0,  # not exposed by flickr.photosets.getList
            "thumb_url":    s.get("url_q"),
        }
        for s in data.get("photosets", {}).get("photoset", [])
    ]
    return JSONResponse({"albums": albums})


async def api_user_album_photos(request: Request):
    """GET /api/users/{nsid}/albums/{album_id}/photos?page=&limit= — any
    user's album photos, fetched live. Never joined against the local DB
    (unlike api_album_photos for your own albums) since another user's
    photos are never synced there."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    nsid = request.path_params["nsid"]
    album_id = request.path_params["album_id"]
    try:
        page, limit = _parse_page_limit(request.query_params)
    except ValueError:
        return JSONResponse({"error": "page/limit must be integers"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_get("flickr.photosets.getPhotos", {
            "photoset_id": album_id, "user_id": nsid,
            "per_page": str(limit), "page": str(page),
            "extras": _LIVE_PHOTO_EXTRAS,
        })
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    photoset = data.get("photoset", {})
    photos = [_photo_dict_from_api(p, owner=nsid) for p in photoset.get("photo", [])]
    return JSONResponse({
        "total": int(photoset.get("total", 0) or 0),
        "page":  page,
        "pages": int(photoset.get("pages", 0) or 0),
        "photos": photos,
    })


def _resolve_group_id(raw: str) -> str | None:
    """Extract a group NSID from a raw group-id or flickr.com/groups/<x>/
    URL. Returns None if the result isn't NSID-shaped (e.g. a path alias, or
    plain keywords) — flickr.groups.getInfo requires the real NSID and
    there's no lookup-by-name API, so callers should fall back to group
    search in that case."""
    m = _GROUP_URL_RE.search(raw)
    candidate = m.group(1) if m else raw
    return candidate if flickr_api._NSID_RE.match(candidate) else None


async def api_group_info(request: Request):
    """GET /api/groups/{id} — any group's info (name/description/rules/
    popularity), plus whether the caller has joined it, for the Photo
    Browser's "Group" browse mode header."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    raw_id = request.path_params["id"]
    group_id = _resolve_group_id(raw_id)
    if not group_id:
        return JSONResponse(
            {"error": "Not a group ID — try Search Flickr Groups instead."},
            status_code=400,
        )

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_get("flickr.groups.getInfo", {"group_id": group_id})
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    group = data.get("group", {})
    with get_db_for_user(user["username"]) as conn:
        joined = conn.execute(
            "SELECT user_note FROM groups WHERE id = ?", (group_id,)
        ).fetchone()

    return JSONResponse({
        "id":          group_id,
        "name":        group.get("name", ""),
        "description": (group.get("description") or {}).get("_content", ""),
        "rules":       (group.get("rules") or {}).get("_content", ""),
        "members":     int(group.get("members", 0) or 0),
        "pool_count":  int(group.get("pool_count", 0) or 0),
        "url":         f"https://www.flickr.com/groups/{group_id}/",
        "joined":      joined is not None,
        "your_note":   joined["user_note"] if joined else None,
    })


async def api_group_search(request: Request):
    """GET /api/groups/search?q= — keyword search across all Flickr groups,
    for picking a group by name in the Photo Browser's "Group" mode."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "q is required"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_get("flickr.groups.search", {"text": q, "per_page": "20"})
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    groups = [
        {
            "id":         g.get("nsid", ""),
            "name":       g.get("name", ""),
            "members":    int(g.get("members", 0) or 0),
            "pool_count": int(g.get("pool_count", 0) or 0),
            "url":        f"https://www.flickr.com/groups/{g.get('nsid', '')}/",
        }
        for g in data.get("groups", {}).get("group", [])
    ]
    return JSONResponse({"groups": groups})


async def api_group_photos(request: Request):
    """GET /api/groups/{id}/photos?page=&limit= — a group pool's photos,
    fetched live."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    group_id = request.path_params["id"]
    try:
        page, limit = _parse_page_limit(request.query_params)
    except ValueError:
        return JSONResponse({"error": "page/limit must be integers"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_get("flickr.groups.pools.getPhotos", {
            "group_id": group_id, "per_page": str(limit), "page": str(page),
            "extras": _LIVE_PHOTO_EXTRAS,
        })
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    container = data.get("photos", {})
    total = int(container.get("total", 0) or 0)
    pages = container.get("pages")
    pages = int(pages) if pages else math.ceil(total / limit) if total else 0
    photos = [_photo_dict_from_api(p) for p in container.get("photo", [])]
    return JSONResponse({"total": total, "page": page, "pages": pages, "photos": photos})


async def api_search_photos(request: Request):
    """GET /api/search/photos?q=&page=&limit= — site-wide Flickr keyword
    search, for the Photo Browser's "Search" browse mode."""
    user = _session_user(request)
    if not user:
        return _unauthorized()

    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "q is required"}, status_code=400)
    try:
        page, limit = _parse_page_limit(request.query_params)
    except ValueError:
        return JSONResponse({"error": "page/limit must be integers"}, status_code=400)

    from db import _current_user as _ctx

    token = _ctx.set(user)
    try:
        data = flickr_api._api_get("flickr.photos.search", {
            "text": q, "per_page": str(limit), "page": str(page),
            "extras": _LIVE_PHOTO_EXTRAS,
        })
    except flickr_api.FlickrAPIError as e:
        status = 404 if e.code == 1 else 502
        return JSONResponse({"error": e.flickr_message}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        _ctx.reset(token)

    container = data.get("photos", {})
    photos = [_photo_dict_from_api(p) for p in container.get("photo", [])]
    return JSONResponse({
        "total": int(container.get("total", 0) or 0),
        "page":  page,
        "pages": int(container.get("pages", 0) or 0),
        "photos": photos,
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


async def api_sync_cancel(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()

    sync_type = request.path_params["type"]
    if sync_type not in SYNC_TYPES:
        return JSONResponse({"error": f"unknown sync type: {sync_type}"}, status_code=400)

    cancelled = cancel_sync(sync_type, user["username"])
    if not cancelled:
        return JSONResponse({"cancelled": False, "reason": "not running"}, status_code=409)
    return JSONResponse({"cancelled": True})


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

    you_username = _json.dumps(user["username"] or "")
    you_nsid = _json.dumps(user["nsid"] or "")
    bookmarklet = (
        "javascript:(function(){"
        f"var YOU_USER={you_username},YOU_NSID={you_nsid};"
        "var m=location.href.match(/flickr\\.com\\/photos\\/([^\\/]+)\\/(\\d+)/);"
        "if(!m){alert('Not a Flickr photo page');return;}"
        "var owner=decodeURIComponent(m[1]),id=m[2];"
        "var mine=(owner===YOU_USER)||(owner===YOU_NSID);"
        f"window.open('{base}/app/#'+(mine?'photo=':'other=')+id);"
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


async def api_extension(request: Request):
    """GET /api/extension — download a browser extension zip with the server URL baked in."""
    import io, zipfile, textwrap
    user = _session_user(request)
    if not user:
        return _unauthorized()

    base = str(request.base_url).rstrip("/")

    manifest = json.dumps({
        "manifest_version": 3,
        "name": "Photo Workbench",
        "version": "1.0.0",
        "description": "Open the current Flickr photo in Mr. E's Photo Workbench",
        "action": {"default_popup": "popup.html"},
        "permissions": ["tabs"],
    }, indent=2)

    popup_html = textwrap.dedent("""\
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            body { width: 220px; padding: 14px; font-family: sans-serif; font-size: 14px; }
            button { width: 100%; padding: 10px; font-size: 14px; cursor: pointer; border-radius: 6px; border: 1px solid #888; background: #222; color: #eee; }
            button:hover:not(:disabled) { border-color: #5aa2e8; }
            button:disabled { opacity: 0.45; cursor: default; }
            p { color: #888; font-size: 12px; margin: 8px 0 0; }
          </style>
        </head>
        <body>
          <button id="btn">Open in Workbench</button>
          <p id="msg"></p>
          <script src="popup.js"></script>
        </body>
        </html>
    """)

    popup_js = textwrap.dedent(f"""\
        const BASE = {json.dumps(base)};
        const YOU_USER = {json.dumps(user["username"] or "")};
        const YOU_NSID = {json.dumps(user["nsid"] or "")};
        chrome.tabs.query({{active: true, currentWindow: true}}, function([tab]) {{
          const m = tab && tab.url && tab.url.match(/flickr\\.com\\/photos\\/([^/]+)\\/(\\d+)/);
          const btn = document.getElementById('btn');
          const msg = document.getElementById('msg');
          if (m) {{
            const owner = decodeURIComponent(m[1]);
            const id = m[2];
            const mine = (owner === YOU_USER) || (owner === YOU_NSID);
            btn.onclick = function() {{
              chrome.tabs.create({{url: BASE + '/app/#' + (mine ? 'photo=' : 'other=') + id}});
              window.close();
            }};
            msg.textContent = (mine ? 'Your photo ' : 'Photo ') + id;
          }} else {{
            btn.disabled = true;
            msg.textContent = 'Not a Flickr photo page';
          }}
        }});
    """)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("popup.html", popup_html)
        zf.writestr("popup.js", popup_js)
    buf.seek(0)

    from starlette.responses import Response as _Response
    return _Response(
        buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="photo-workbench-extension.zip"'},
    )


def api_routes() -> list[Route]:
    """Routes to register in the main Starlette app (see ``web.main_sse``)."""
    return [
        Route("/api/me",           endpoint=api_me),
        Route("/api/photos",       endpoint=api_photos),
        Route("/api/photos/{id}",  endpoint=api_photo_detail),
        Route("/api/photos/{id}/fave",    endpoint=api_photo_fave, methods=["POST"]),
        Route("/api/photos/{id}/comment", endpoint=api_photo_comment, methods=["POST"]),
        Route("/api/albums",       endpoint=api_albums),
        Route("/api/albums/{id}/photos", endpoint=api_album_photos),
        Route("/api/users/lookup",       endpoint=api_user_lookup),
        Route("/api/users/{nsid}/photos", endpoint=api_user_photos),
        Route("/api/users/{nsid}/albums", endpoint=api_user_albums),
        Route("/api/users/{nsid}/albums/{album_id}/photos", endpoint=api_user_album_photos),
        # /groups/search must precede /groups/{id} — otherwise {id} would
        # greedily match the literal "search" segment first.
        Route("/api/groups/search",      endpoint=api_group_search),
        Route("/api/groups/{id}",        endpoint=api_group_info),
        Route("/api/groups/{id}/photos", endpoint=api_group_photos),
        Route("/api/search/photos", endpoint=api_search_photos),
        Route("/api/stats",        endpoint=api_stats),
        Route("/api/sync/status",  endpoint=api_sync_status),
        Route("/api/sync/{type}/cancel", endpoint=api_sync_cancel, methods=["POST"]),
        Route("/api/sync/{type}",  endpoint=api_sync_trigger, methods=["POST"]),
        Route("/api/queue",        endpoint=api_queue, methods=["GET", "POST"]),
        Route("/api/setup",        endpoint=api_setup),
        Route("/api/reset",        endpoint=api_reset, methods=["POST"]),
        Route("/api/settings",     endpoint=api_settings, methods=["GET", "POST"]),
        Route("/api/regen-key",    endpoint=api_regen_key, methods=["POST"]),
        Route("/api/extension",    endpoint=api_extension),
    ]
