"""Group tool definitions and handlers."""

import json

from mcp.types import TextContent, Tool

import flickr_api
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
        description="Add a photo to a Flickr group pool.",
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
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
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
]


async def _find_groups(args):
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    pat = f"%{query}%"
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, members, pool_count FROM groups "
            "WHERE name LIKE ? OR description LIKE ? OR keywords LIKE ? "
            "ORDER BY members DESC LIMIT ?",
            (pat, pat, pat, limit),
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


async def _add_to_group(args):
    flickr_api._api_post("flickr.groups.pools.add", {"photo_id": args["photo_id"], "group_id": args["group_id"]})
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO photo_groups (photo_id, group_id) VALUES (?, ?)",
            (args["photo_id"], args["group_id"]),
        )
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to group {args['group_id']}.")]


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
    with get_db() as conn:
        synced = conn.execute(
            "SELECT COUNT(*) FROM sync_log WHERE type='groups'"
        ).fetchone()[0] > 0
        if synced:
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
}
