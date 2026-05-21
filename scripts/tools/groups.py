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
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to group {args['group_id']}.")]


async def _remove_from_group(args):
    flickr_api._api_post("flickr.groups.pools.remove", {"photo_id": args["photo_id"], "group_id": args["group_id"]})
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


HANDLERS = {
    "find_groups":       _find_groups,
    "set_group_keywords": _set_group_keywords,
    "add_to_group":      _add_to_group,
    "remove_from_group": _remove_from_group,
    "join_group":        _join_group,
    "leave_group":       _leave_group,
    "get_group_photos":  _get_group_photos,
    "search_all_groups": _search_all_groups,
}
