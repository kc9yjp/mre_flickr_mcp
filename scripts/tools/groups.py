"""Group tool definitions and handlers."""

import datetime
import json
import logging
import time

from mcp.types import TextContent, Tool

import flickr_api
from flickr_api import FlickrAPIError
from db import get_db

TOOLS = [
    Tool(
        name="find_groups",
        description="Search the user's Flickr groups by keyword from the local database. Searches group name, description, and keywords.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search group names, descriptions, and keywords"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
        },
    ),
    Tool(
        name="set_group_keywords",
        description="Set custom search keywords/synonyms for a group to improve future findability.",
        inputSchema={
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Flickr group NSID"},
                "keywords": {"type": "string", "description": "Space or comma-separated keywords/synonyms"},
            },
            "required": ["group_id", "keywords"],
        },
    ),
    Tool(
        name="add_to_group",
        description=(
            "Add a photo to a Flickr group pool. "
            "If the daily posting limit is hit, the add is queued for automatic retry. "
            "Use retry_at to control when the retry fires: named times (morning, lunchtime, "
            "afternoon, evening, night, midnight) or HH:MM are resolved in Chicago time. "
            "If the photo/group pair is already waiting in the queue, retry_at updates its schedule."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "group_id": {"type": "string", "description": "Flickr group NSID"},
                "retry_at": {
                    "type": "string",
                    "description": (
                        "When to retry if the daily limit is hit. Named times: morning (8am), "
                        "lunchtime (12pm), afternoon (2pm), evening (6pm), night (9pm), midnight. "
                        "Or HH:MM (24h, Chicago time). Defaults to 5pm CT."
                    ),
                },
            },
            "required": ["photo_id", "group_id"],
        },
    ),
    Tool(
        name="remove_from_group",
        description="Remove a photo from a Flickr group pool.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "group_id": {"type": "string", "description": "Flickr group NSID"},
            },
            "required": ["photo_id", "group_id"],
        },
    ),
    Tool(
        name="join_group",
        description="Join a public Flickr group.",
        inputSchema={
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Flickr group NSID"},
            },
            "required": ["group_id"],
        },
    ),
    Tool(
        name="leave_group",
        description="Leave a Flickr group you have joined.",
        inputSchema={
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Flickr group NSID"},
            },
            "required": ["group_id"],
        },
    ),
    Tool(
        name="get_group_photos",
        description="List photos in a Flickr group pool.",
        inputSchema={
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Flickr group NSID"},
                "limit":    {"type": "integer", "description": "Max photos (default 50)"},
                "page":     {"type": "integer", "description": "Page number (default 1)"},
            },
            "required": ["group_id"],
        },
    ),
    Tool(
        name="search_all_groups",
        description="Search all Flickr groups (not just ones you've joined) by keyword.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_photo_contexts",
        description="Return all group pools and albums a photo currently belongs to. Use this before add_to_group to skip groups the photo is already in.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id":  {"type": "string",  "description": "Flickr photo ID"},
                "force_api": {"type": "boolean", "description": "Skip local DB and fetch live from Flickr API (default false)"},
            },
            "required": ["photo_id"],
        },
    ),
    Tool(
        name="get_group_stats",
        description="Show how many of your photos are in each group you've joined, ranked by photo count. Requires groups sync to have run.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max groups to return (default 20)"},
            },
        },
    ),
    Tool(
        name="get_photo_group_count",
        description="List your photos ranked by how many groups they belong to. Useful for finding well-distributed or under-distributed photos. Requires groups sync to have run.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max photos to return (default 20)"},
            },
        },
    ),
    Tool(
        name="get_group_queue",
        description=(
            "Show status of the pending group-add queue. "
            "Returns counts for waiting, success, and error states, plus details of waiting and errored items. "
            "Also flushes any waiting items whose retry window has passed."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


async def _find_groups(args):
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    # Normalize query: replace hyphens/underscores with spaces, strip non-alphanumeric
    import re as _re
    normalized = _re.sub(r"[-_]", " ", query)
    normalized = _re.sub(r"[^\w\s]", "", normalized).strip()
    pat = f"%{query}%"
    npat = f"%{normalized}%"
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, members, pool_count FROM groups "
            "WHERE name LIKE ? OR name LIKE ? "
            "   OR description LIKE ? OR description LIKE ? "
            "   OR keywords LIKE ? OR keywords LIKE ? "
            "   OR auto_keywords LIKE ? OR auto_keywords LIKE ? "
            "ORDER BY members DESC LIMIT ?",
            (pat, npat, pat, npat, pat, npat, pat, npat, limit),
        ).fetchall()
    if not rows:
        return [TextContent(type="text", text=f"No groups found matching '{query}'. Run sync to populate groups.")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _set_group_keywords(args):
    group_id = args["group_id"]
    keywords = args["keywords"]
    with get_db() as conn:
        updated = conn.execute("UPDATE groups SET keywords=? WHERE id=?", (keywords, group_id)).rowcount
    if not updated:
        return [TextContent(type="text", text=f"Group {group_id} not found in local database.")]
    return [TextContent(type="text", text=f"Keywords updated for group {group_id}.")]


# TODO: read _RETRY_TZ from DB settings key "group_queue_retry_tz" (see db.SETTINGS_DEFAULTS)
_RETRY_TZ = "America/Chicago"

_NAMED_TIMES: dict[str, tuple[int, int]] = {
    "midnight":  (0,  0),
    "morning":   (8,  0),
    "lunchtime": (12, 0),
    "lunch":     (12, 0),
    "afternoon": (14, 0),
    "evening":   (18, 0),
    "night":     (21, 0),
}


def _next_midnight_utc() -> int:
    """Unix timestamp for the start of tomorrow UTC."""
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = (now + datetime.timedelta(days=1)).date()
    return int(datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                                 tzinfo=datetime.timezone.utc).timestamp())


def _parse_retry_time(retry_at: str | None) -> int:
    """Convert a named time or HH:MM string to a UTC Unix timestamp (next occurrence).

    Times are resolved in Chicago time (_RETRY_TZ).  If the target time has
    already passed today, the next day's instance is used.  Defaults to 5pm
    Chicago time when *retry_at* is None; falls back to next midnight UTC for
    unrecognised strings.
    """
    if retry_at is None:
        # TODO: read default from DB settings key "group_queue_default_retry" (see db.SETTINGS_DEFAULTS)
        return _parse_retry_time("17:00")

    from zoneinfo import ZoneInfo
    tz = ZoneInfo(_RETRY_TZ)
    now_local = datetime.datetime.now(tz)
    token = retry_at.lower().strip()

    hour, minute = _NAMED_TIMES.get(token, (None, None))

    if hour is None and ":" in token:
        try:
            h, m = token.split(":", 1)
            hour, minute = int(h), int(m)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return _next_midnight_utc()
        except ValueError:
            return _next_midnight_utc()

    if hour is None:
        return _next_midnight_utc()

    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += datetime.timedelta(days=1)
    return int(candidate.timestamp())


def _fmt_chicago(ts: int) -> str:
    """Format a Unix timestamp as a human-readable Chicago local time."""
    from zoneinfo import ZoneInfo
    dt = datetime.datetime.fromtimestamp(ts, ZoneInfo(_RETRY_TZ))
    return dt.strftime("%Y-%m-%d %I:%M %p CT")


def _flush_group_queue(conn, force: bool = False) -> list[dict]:
    """Process waiting queue items whose retry_after has passed.

    When *force* is True, all waiting items are retried regardless of schedule.
    Returns a list of result dicts.
    """
    now = int(time.time())
    if force:
        rows = conn.execute(
            "SELECT id, photo_id, group_id FROM pending_group_adds WHERE status='waiting'",
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, photo_id, group_id FROM pending_group_adds WHERE status='waiting' AND retry_after <= ?",
            (now,),
        ).fetchall()
    flushed = []
    for row in rows:
        try:
            flickr_api._api_post("flickr.groups.pools.add",
                                 {"photo_id": row["photo_id"], "group_id": row["group_id"]})
            conn.execute(
                "UPDATE pending_group_adds SET status='success', completed_at=? WHERE id=?",
                (now, row["id"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO photo_groups (photo_id, group_id) VALUES (?, ?)",
                (row["photo_id"], row["group_id"]),
            )
            flushed.append({"photo_id": row["photo_id"], "group_id": row["group_id"], "result": "success"})
        except FlickrAPIError as e:
            if e.code == 5:
                conn.execute(
                    "UPDATE pending_group_adds SET retry_after=? WHERE id=?",
                    (_next_midnight_utc(), row["id"]),
                )
                flushed.append({"photo_id": row["photo_id"], "group_id": row["group_id"], "result": "still_limited"})
            else:
                conn.execute(
                    "UPDATE pending_group_adds SET status='error', error_msg=?, completed_at=? WHERE id=?",
                    (e.flickr_message, now, row["id"]),
                )
                flushed.append({"photo_id": row["photo_id"], "group_id": row["group_id"],
                                "result": f"error: {e.flickr_message}"})
        except RuntimeError as e:
            logging.exception("Unexpected error flushing queue item photo=%s group=%s", row["photo_id"], row["group_id"])
            conn.execute(
                "UPDATE pending_group_adds SET status='error', error_msg=?, completed_at=? WHERE id=?",
                (str(e), now, row["id"]),
            )
            flushed.append({"photo_id": row["photo_id"], "group_id": row["group_id"], "result": f"error: {e}"})
    return flushed


async def _add_to_group(args):
    photo_id = args["photo_id"]
    group_id = args["group_id"]
    retry_at_str = args.get("retry_at")
    with get_db() as conn:
        _flush_group_queue(conn)
        try:
            flickr_api._api_post("flickr.groups.pools.add", {"photo_id": photo_id, "group_id": group_id})
            conn.execute(
                "INSERT OR IGNORE INTO photo_groups (photo_id, group_id) VALUES (?, ?)",
                (photo_id, group_id),
            )
            return [TextContent(type="text", text=f"Photo {photo_id} added to group {group_id}.")]
        except FlickrAPIError as e:
            if e.code == 5:
                retry_after = _parse_retry_time(retry_at_str)
                existing = conn.execute(
                    "SELECT id FROM pending_group_adds WHERE photo_id=? AND group_id=? AND status='waiting'",
                    (photo_id, group_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE pending_group_adds SET retry_after=? WHERE id=?",
                        (retry_after, existing["id"]),
                    )
                    action = "rescheduled"
                else:
                    conn.execute(
                        "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at) "
                        "VALUES (?, ?, 'waiting', ?, ?)",
                        (photo_id, group_id, retry_after, int(time.time())),
                    )
                    action = "queued"
                eta = _fmt_chicago(retry_after)
                return [TextContent(type="text", text=(
                    f"Daily posting limit reached for group {group_id}. "
                    f"{action.capitalize()} for retry at {eta}."
                ))]
            raise


async def _remove_from_group(args):
    flickr_api._api_post("flickr.groups.pools.remove", {"photo_id": args["photo_id"], "group_id": args["group_id"]})
    with get_db() as conn:
        conn.execute(
            "DELETE FROM photo_groups WHERE photo_id=? AND group_id=?",
            (args["photo_id"], args["group_id"]),
        )
    return [TextContent(type="text", text=f"Photo {args['photo_id']} removed from group {args['group_id']}.")]


async def _join_group(args):
    flickr_api._api_post("flickr.groups.join", {"group_id": args["group_id"]})
    return [TextContent(type="text", text=f"Joined group {args['group_id']}.")]


async def _leave_group(args):
    flickr_api._api_post("flickr.groups.leave", {"group_id": args["group_id"]})
    return [TextContent(type="text", text=f"Left group {args['group_id']}.")]


async def _get_group_photos(args):
    group_id = args["group_id"]
    limit = int(args.get("limit", 50))
    page = int(args.get("page", 1))
    data = flickr_api._api_get("flickr.groups.pools.getPhotos", {
        "group_id": group_id,
        "per_page": str(limit),
        "page":     str(page),
        "extras":   "views,date_taken",
    })
    container = data.get("photos", {})
    photos = container.get("photo", [])
    return [TextContent(type="text", text=json.dumps({
        "total": container.get("total", 0),
        "page":  page,
        "photos": [{"id": p["id"], "title": p.get("title", ""), "owner": p.get("owner", ""),
                    "url": f"https://www.flickr.com/photos/{p.get('owner', '')}/{p['id']}/"}
                   for p in photos],
    }, indent=2))]


async def _search_all_groups(args):
    query = args["query"]
    limit = int(args.get("limit", 20))
    data = flickr_api._api_get("flickr.groups.search", {"text": query, "per_page": str(limit)})
    groups = data.get("groups", {}).get("group", [])
    return [TextContent(type="text", text=json.dumps([{
        "nsid":       g.get("nsid", ""),
        "name":       g.get("name", ""),
        "members":    g.get("members", 0),
        "pool_count": g.get("pool_count", 0),
        "url":        f"https://www.flickr.com/groups/{g.get('nsid', '')}/",
    } for g in groups], indent=2))]


async def _get_photo_contexts(args):
    photo_id = args["photo_id"]
    force_api = args.get("force_api", False)
    with get_db() as conn:
        synced = conn.execute(
            "SELECT COUNT(*) FROM sync_log WHERE type='groups'"
        ).fetchone()[0] > 0
        if synced and not force_api:
            rows = conn.execute(
                "SELECT g.id, g.name FROM photo_groups pg "
                "JOIN groups g ON pg.group_id = g.id WHERE pg.photo_id = ?",
                (photo_id,),
            ).fetchall()
            # photo-album membership isn't tracked locally yet — fetch from API
            try:
                api_data = flickr_api._api_get("flickr.photos.getAllContexts", {"photo_id": photo_id})
                sets = [{"id": s["id"], "title": s.get("title", "")} for s in api_data.get("set", [])]
            except RuntimeError:
                sets = []
            return [TextContent(type="text", text=json.dumps({
                "photo_id":    photo_id,
                "source":      "local_db",
                "group_pools": [{"id": r["id"], "title": r["name"]} for r in rows],
                "albums":      sets,
            }, indent=2))]
    # No local data yet — fall back to API for everything
    data = flickr_api._api_get("flickr.photos.getAllContexts", {"photo_id": photo_id})
    pools = [{"id": p["id"], "title": p.get("title", "")} for p in data.get("pool", [])]
    sets  = [{"id": s["id"], "title": s.get("title", "")} for s in data.get("set",  [])]
    return [TextContent(type="text", text=json.dumps({
        "photo_id":    photo_id,
        "source":      "flickr_api",
        "group_pools": pools,
        "albums":      sets,
        "note":        "Run 'sync groups' to enable faster local group lookups",
    }, indent=2))]


async def _get_group_stats(args):
    limit = int(args.get("limit", 20))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT g.name, g.id, g.pool_count, g.members, COUNT(pg.photo_id) AS my_count "
            "FROM groups g LEFT JOIN photo_groups pg ON g.id = pg.group_id "
            "GROUP BY g.id ORDER BY my_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return [TextContent(type="text", text="No group data found. Run 'sync groups' first.")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _get_group_queue(args):
    with get_db() as conn:
        flushed = _flush_group_queue(conn)

        waiting_rows = conn.execute(
            "SELECT pga.photo_id, pga.group_id, g.name AS group_name, p.title AS photo_title, pga.retry_after "
            "FROM pending_group_adds pga "
            "LEFT JOIN groups g ON pga.group_id = g.id "
            "LEFT JOIN photos p ON pga.photo_id = p.id "
            "WHERE pga.status='waiting' ORDER BY pga.retry_after ASC",
        ).fetchall()

        error_rows = conn.execute(
            "SELECT pga.photo_id, pga.group_id, g.name AS group_name, p.title AS photo_title, "
            "pga.error_msg, pga.queued_at "
            "FROM pending_group_adds pga "
            "LEFT JOIN groups g ON pga.group_id = g.id "
            "LEFT JOIN photos p ON pga.photo_id = p.id "
            "WHERE pga.status='error' ORDER BY pga.queued_at DESC LIMIT 20",
        ).fetchall()

        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM pending_group_adds GROUP BY status"
        ).fetchall()

    summary = {row["status"]: row["n"] for row in counts}
    summary.setdefault("waiting", 0)
    summary.setdefault("success", 0)
    summary.setdefault("error", 0)

    def fmt_waiting(row):
        eta = _fmt_chicago(row["retry_after"]) if row["retry_after"] else "anytime"
        return {
            "photo_id": row["photo_id"],
            "photo_title": row["photo_title"],
            "group_id": row["group_id"],
            "group_name": row["group_name"],
            "retry_after": eta,
        }

    def fmt_error(row):
        return {
            "photo_id": row["photo_id"],
            "photo_title": row["photo_title"],
            "group_id": row["group_id"],
            "group_name": row["group_name"],
            "error": row["error_msg"],
        }

    result = {
        "summary": summary,
        "waiting": [fmt_waiting(r) for r in waiting_rows],
        "errors":  [fmt_error(r) for r in error_rows],
    }
    if flushed:
        result["flushed_this_call"] = flushed

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _get_photo_group_count(args):
    limit = int(args.get("limit", 20))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT p.title, p.id, p.views, p.favorites, COUNT(pg.group_id) AS group_count "
            "FROM photos p JOIN photo_groups pg ON p.id = pg.photo_id "
            "GROUP BY p.id ORDER BY group_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return [TextContent(type="text", text="No photo-group data found. Run 'sync groups' first.")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


HANDLERS = {
    "find_groups":       _find_groups,
    "set_group_keywords": _set_group_keywords,
    "add_to_group":      _add_to_group,
    "remove_from_group": _remove_from_group,
    "join_group":        _join_group,
    "leave_group":       _leave_group,
    "get_group_photos":  _get_group_photos,
    "search_all_groups":    _search_all_groups,
    "get_photo_contexts":   _get_photo_contexts,
    "get_group_stats":      _get_group_stats,
    "get_photo_group_count": _get_photo_group_count,
    "get_group_queue":      _get_group_queue,
}
