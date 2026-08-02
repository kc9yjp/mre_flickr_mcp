"""Group tool definitions and handlers."""

import datetime
import json
import logging
import time

from mcp.types import TextContent, Tool

import flickr_api
from flickr_api import FlickrAPIError
from db import get_db, like_pattern, table_empty
from text_utils import html_to_text

TOOLS = [
    Tool(
        name="find_groups",
        description="Search the user's Flickr groups by keyword from the local database. Searches group name, description, and AI-generated summary/keywords (see 'sync' with type='groups').",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword(s) to search group names, descriptions, and AI-generated keywords. Comma-separate multiple unrelated keywords to OR them together (e.g. 'wildlife, sunset, macro')."},
                "limit": {"type": "integer", "description": "Max results (default 25)"},
            },
        },
    ),
    Tool(
        name="set_group_note",
        description=(
            "Set a personal note about a group (e.g. posting limits you've noticed, or a reminder). "
            "Incorporated into the group's AI-generated summary the next time groups are synced."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Flickr group NSID"},
                "note": {"type": "string", "description": "Freeform note text"},
            },
            "required": ["group_id", "note"],
        },
    ),
    Tool(
        name="add_to_group",
        description=(
            "Add a photo to a Flickr group pool. "
            "If the daily posting limit is hit, the add is queued for automatic retry. "
            "Use retry_at to control when the retry fires: named times (morning, lunchtime, "
            "afternoon, evening, night, midnight) or HH:MM are resolved in Chicago time. "
            "If the photo/group pair is already waiting in the queue, retry_at updates its schedule. "
            "Set queue=true to skip the immediate Flickr call and schedule the add for a future time — "
            "useful for drip-posting one photo per day. Combine with days_offset to spread adds across days "
            "(e.g. days_offset=2 with retry_at=morning schedules for morning two days from now)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "group_id": {"type": "string", "description": "Flickr group NSID"},
                "retry_at": {
                    "type": "string",
                    "description": (
                        "When to retry if the daily limit is hit, or when to fire if queue=true. "
                        "Named times: morning (8am), lunchtime (12pm), afternoon (2pm), evening (6pm), "
                        "night (9pm), midnight. Or HH:MM (24h, Chicago time). Defaults to 5pm CT."
                    ),
                },
                "queue": {
                    "type": "boolean",
                    "description": (
                        "If true, skip the immediate Flickr API call and schedule the add for a future time. "
                        "Use with retry_at and days_offset for drip-posting across multiple days."
                    ),
                },
                "days_offset": {
                    "type": "integer",
                    "description": (
                        "Number of days from today to schedule the add (default 0 = today/tomorrow as needed). "
                        "Use with queue=true to spread adds: days_offset=1 = tomorrow, days_offset=2 = day after, etc."
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
        name="get_group_info",
        description=(
            "Fetch live info for any Flickr group by ID — name, description, rules, "
            "member count, and pool (photo) count — whether or not you've joined it. "
            "Also reports whether it's one of your joined groups. Use this for groups "
            "encountered outside your own joined-groups list, e.g. a group a photo you "
            "don't own belongs to."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Flickr group NSID"},
            },
            "required": ["group_id"],
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
    Tool(
        name="remove_from_queue",
        description="Remove a waiting item from the group-add queue by photo ID and group ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "group_id": {"type": "string", "description": "Flickr group NSID"},
            },
            "required": ["photo_id", "group_id"],
        },
    ),
]


async def _find_groups(args):
    query = args.get("query", "")
    limit = int(args.get("limit", 25))
    import re as _re

    # Comma-separated terms are OR'd together so multiple unrelated keywords
    # can be searched in one call (e.g. "wildlife, sunset, macro").
    terms = [t.strip() for t in query.split(",") if t.strip()]
    if not terms:
        terms = [query]

    columns = ("name", "description", "ai_keywords", "summary_md")
    clauses = []
    params = []
    for term in terms:
        # Normalize: replace hyphens/underscores with spaces, strip non-alphanumeric.
        normalized = _re.sub(r"[-_]", " ", term)
        normalized = _re.sub(r"[^\w\s]", "", normalized).strip()
        patterns = {like_pattern(term)}
        if normalized:
            patterns.add(like_pattern(normalized))
        for pat in patterns:
            for col in columns:
                clauses.append(f"{col} LIKE ? ESCAPE '\\'")
                params.append(pat)

    where_sql = " OR ".join(clauses)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, name, members, pool_count, description, summary_md, "
            f"is_milestone, fave_min, view_min, open_subject, user_note FROM groups "
            f"WHERE {where_sql} "
            "ORDER BY members DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        if not rows:
            if table_empty(conn, "groups"):
                return [TextContent(type="text", text="No groups found. Run 'sync groups' first via the web UI or the sync tool.")]
            return [TextContent(type="text", text=f"No groups match '{query}'.")]
    return [TextContent(type="text", text=_format_groups_markdown(rows))]


def _format_groups_markdown(rows) -> str:
    """Render group rows as a markdown listing, one section per group,
    each headed by the group id so it can be passed straight to
    add_to_group/remove_from_group without a further lookup."""
    sections = []
    for r in rows:
        lines = [f"## {r['name']} (`{r['id']}`)"]
        lines.append(f"- Members: {r['members']} · Pool: {r['pool_count']}")

        flags = []
        if r["is_milestone"]:
            flags.append("milestone group")
        if r["fave_min"] is not None:
            flags.append(f"min faves: {r['fave_min']}")
        if r["view_min"] is not None:
            flags.append(f"min views: {r['view_min']}")
        if r["open_subject"] is not None:
            flags.append("open subject" if r["open_subject"] else "themed subject")
        if flags:
            lines.append(f"- {' · '.join(flags)}")

        if r["user_note"]:
            lines.append(f"- Your note: {r['user_note']}")

        if r["summary_md"]:
            lines.append("")
            lines.append(r["summary_md"])
        elif r["description"]:
            # No AI summary yet (e.g. group just synced) — fall back to the
            # raw Flickr description, which comes back as HTML.
            lines.append("")
            lines.append(html_to_text(r["description"]))

        sections.append("\n".join(lines))
    return "\n\n".join(sections)


async def _set_group_note(args):
    group_id = args["group_id"]
    note = args["note"]
    with get_db() as conn:
        updated = conn.execute(
            "UPDATE groups SET user_note=?, needs_summary=1 WHERE id=?",
            (note, group_id),
        ).rowcount
    if not updated:
        return [TextContent(type="text", text=f"Group {group_id} not found in local database.")]
    return [TextContent(type="text", text=(
        f"Note saved for group {group_id}. It will be incorporated into the group's AI summary "
        "the next time groups are synced."
    ))]


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


def _parse_retry_time(retry_at: str | None, days_offset: int = 0) -> int:
    """Convert a named time or HH:MM string to a UTC Unix timestamp.

    Times are resolved in Chicago time (_RETRY_TZ).  If *days_offset* is 0 and
    the target time has already passed today, the next day's instance is used.
    If *days_offset* > 0, the result is always that many days from today at the
    given time (regardless of whether it has passed today).  Defaults to 5pm
    Chicago time when *retry_at* is None; falls back to next midnight UTC for
    unrecognised strings.
    """
    if retry_at is None:
        # TODO: read default from DB settings key "group_queue_default_retry" (see db.SETTINGS_DEFAULTS)
        return _parse_retry_time("17:00", days_offset)

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
    if days_offset > 0:
        candidate += datetime.timedelta(days=days_offset)
    elif candidate <= now_local:
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
    queue_immediately = bool(args.get("queue", False))
    days_offset = int(args.get("days_offset", 0))

    with get_db() as conn:
        _flush_group_queue(conn)

        if queue_immediately:
            retry_after = _parse_retry_time(retry_at_str, days_offset)
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
            return [TextContent(type="text", text=f"Photo {photo_id} {action} for group {group_id} — scheduled for {eta}.")]

        try:
            flickr_api._api_post("flickr.groups.pools.add", {"photo_id": photo_id, "group_id": group_id})
            conn.execute(
                "INSERT OR IGNORE INTO photo_groups (photo_id, group_id) VALUES (?, ?)",
                (photo_id, group_id),
            )
            return [TextContent(type="text", text=f"Photo {photo_id} added to group {group_id}.")]
        except FlickrAPIError as e:
            if e.code == 5:
                retry_after = _parse_retry_time(retry_at_str, days_offset)
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


async def _get_group_info(args):
    group_id = args["group_id"]
    try:
        data = flickr_api._api_get("flickr.groups.getInfo", {"group_id": group_id})
    except FlickrAPIError as e:
        return [TextContent(type="text", text=f"Group {group_id} not found ({e.flickr_message}).")]
    group = data.get("group", {})
    with get_db() as conn:
        joined = conn.execute(
            "SELECT user_note FROM groups WHERE id = ?", (group_id,)
        ).fetchone()
    return [TextContent(type="text", text=json.dumps({
        "id":          group_id,
        "name":        group.get("name", ""),
        "description": (group.get("description") or {}).get("_content", ""),
        "rules":       (group.get("rules") or {}).get("_content", ""),
        "members":     int(group.get("members", 0) or 0),
        "pool_count":  int(group.get("pool_count", 0) or 0),
        "url":         f"https://www.flickr.com/groups/{group_id}/",
        "joined":      joined is not None,
        "your_note":   joined["user_note"] if joined else None,
    }, indent=2))]


async def _get_photo_contexts(args):
    photo_id = args["photo_id"]
    force_api = args.get("force_api", False)
    with get_db() as conn:
        # photo_groups only ever maps the caller's OWN photo ids to group ids
        # (see the schema comment on that table), so the local fast path must
        # never be trusted for a photo that isn't in the caller's own library
        # — otherwise every group pool/contact photo silently comes back with
        # an empty group_pools list instead of falling back to the live API.
        is_own = conn.execute("SELECT 1 FROM photos WHERE id = ?", (photo_id,)).fetchone() is not None
        synced = conn.execute(
            "SELECT COUNT(*) FROM sync_log WHERE type='groups'"
        ).fetchone()[0] > 0
        if is_own and synced and not force_api:
            rows = conn.execute(
                "SELECT g.id, g.name FROM photo_groups pg "
                "JOIN groups g ON pg.group_id = g.id WHERE pg.photo_id = ?",
                (photo_id,),
            ).fetchall()
            # photo-album membership isn't tracked locally yet — fetch from API
            albums_error = None
            try:
                api_data = flickr_api._api_get("flickr.photos.getAllContexts", {"photo_id": photo_id})
                sets = [{"id": s["id"], "title": s.get("title", "")} for s in api_data.get("set", [])]
            except RuntimeError as e:
                logging.warning("get_photo_contexts: failed to fetch album contexts for photo %s: %s", photo_id, e)
                sets = []
                albums_error = f"Album lookup failed ({e}); 'albums' does NOT mean the photo is in no albums."
            payload = {
                "photo_id":    photo_id,
                "source":      "local_db",
                "group_pools": [{"id": r["id"], "title": r["name"]} for r in rows],
                "albums":      sets,
            }
            if albums_error:
                payload["albums_error"] = albums_error
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]
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
            if table_empty(conn, "photo_groups"):
                return [TextContent(type="text", text="No photo-group data found. Run 'sync groups' first.")]
            return [TextContent(type="text", text="No synced photos with group memberships found (run a photo sync if photos are missing locally).")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _remove_from_queue(args):
    photo_id = args["photo_id"]
    group_id = args["group_id"]
    with get_db() as conn:
        deleted = conn.execute(
            "DELETE FROM pending_group_adds WHERE photo_id=? AND group_id=? AND status='waiting'",
            (photo_id, group_id),
        ).rowcount
    if deleted:
        return [TextContent(type="text", text=f"Removed photo {photo_id} / group {group_id} from queue.")]
    return [TextContent(type="text", text=f"No queue entry found for photo {photo_id} / group {group_id}.")]


HANDLERS = {
    "find_groups":       _find_groups,
    "set_group_note":    _set_group_note,
    "add_to_group":      _add_to_group,
    "remove_from_group": _remove_from_group,
    "join_group":        _join_group,
    "leave_group":       _leave_group,
    "get_group_photos":  _get_group_photos,
    "search_all_groups":    _search_all_groups,
    "get_group_info":       _get_group_info,
    "get_photo_contexts":   _get_photo_contexts,
    "get_group_stats":      _get_group_stats,
    "get_photo_group_count": _get_photo_group_count,
    "get_group_queue":      _get_group_queue,
    "remove_from_queue":    _remove_from_queue,
}
