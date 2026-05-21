"""Gallery tool definitions and handlers."""

import json

from mcp.types import TextContent, Tool

import flickr_api

TOOLS = [
    Tool(
        name="get_galleries",
        description="List galleries created by the authenticated user.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    ),
    Tool(
        name="create_gallery",
        description="Create a new Flickr gallery (curated collection, separate from albums).",
        inputSchema={
            "type": "object",
            "properties": {
                "title":            {"type": "string", "description": "Gallery title"},
                "description":      {"type": "string", "description": "Gallery description"},
                "primary_photo_id": {"type": "string", "description": "Optional cover photo ID"},
            },
            "required": ["title", "description"],
        },
    ),
    Tool(
        name="add_to_gallery",
        description="Add a photo to a Flickr gallery.",
        inputSchema={
            "type": "object",
            "properties": {
                "gallery_id": {"type": "string", "description": "Flickr gallery ID"},
                "photo_id":   {"type": "string", "description": "Flickr photo ID"},
                "comment":    {"type": "string", "description": "Optional comment to add alongside the photo"},
            },
            "required": ["gallery_id", "photo_id"],
        },
    ),
    Tool(
        name="get_gallery_photos",
        description="List photos in a Flickr gallery by gallery ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "gallery_id": {"type": "string", "description": "Flickr gallery ID"},
                "limit":      {"type": "integer", "description": "Max photos (default 50)"},
                "page":       {"type": "integer", "description": "Page number (default 1)"},
            },
            "required": ["gallery_id"],
        },
    ),
]


async def _get_galleries(args):
    creds = flickr_api._load_credentials()
    limit = int(args.get("limit", 20))
    data = flickr_api._api_get("flickr.galleries.getList", {"user_id": creds["user_nsid"], "per_page": str(limit)})
    galleries = data.get("galleries", {}).get("gallery", [])
    return [TextContent(type="text", text=json.dumps([{
        "id":           g.get("id", ""),
        "title":        g.get("title", {}).get("_content", "") if isinstance(g.get("title"), dict) else g.get("title", ""),
        "description":  g.get("description", {}).get("_content", "") if isinstance(g.get("description"), dict) else g.get("description", ""),
        "count_photos": g.get("count_photos", 0),
        "url":          g.get("url", ""),
    } for g in galleries], indent=2))]


async def _create_gallery(args):
    params = {"title": args["title"], "description": args["description"]}
    if "primary_photo_id" in args:
        params["primary_photo_id"] = args["primary_photo_id"]
    data = flickr_api._api_post("flickr.galleries.create", params)
    gallery = data.get("gallery", {})
    return [TextContent(type="text", text=json.dumps({
        "gallery_id": gallery.get("id", ""),
        "url":        gallery.get("url", ""),
        "title":      args["title"],
    }, indent=2))]


async def _add_to_gallery(args):
    params = {"gallery_id": args["gallery_id"], "photo_id": args["photo_id"]}
    if "comment" in args:
        params["comment"] = args["comment"]
    flickr_api._api_post("flickr.galleries.addPhoto", params)
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to gallery {args['gallery_id']}.")]


async def _get_gallery_photos(args):
    gallery_id = args["gallery_id"]
    limit = int(args.get("limit", 50))
    page = int(args.get("page", 1))
    data = flickr_api._api_get("flickr.galleries.getPhotos", {
        "gallery_id": gallery_id,
        "per_page":   str(limit),
        "page":       str(page),
        "extras":     "views,date_taken",
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


HANDLERS = {
    "get_galleries":    _get_galleries,
    "create_gallery":   _create_gallery,
    "add_to_gallery":   _add_to_gallery,
    "get_gallery_photos": _get_gallery_photos,
}
