"""Album tool definitions and handlers."""

import json
import time

from mcp.types import TextContent, Tool

import flickr_api
from db import db

TOOLS = [
    Tool(
        name="find_albums",
        description="Search the user's Flickr albums by keyword from the local database.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search album titles"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
        },
    ),
    Tool(
        name="get_album_photos",
        description="List photos in a Flickr album.",
        inputSchema={
            "type": "object",
            "properties": {
                "album_id": {"type": "string", "description": "Flickr photoset ID"},
                "limit":    {"type": "integer", "description": "Max photos to return per page (default 50)"},
                "page":     {"type": "integer", "description": "Page number (default 1)"},
            },
            "required": ["album_id"],
        },
    ),
    Tool(
        name="add_to_album",
        description="Add a photo to a Flickr album.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "album_id": {"type": "string", "description": "Flickr photoset ID"},
            },
            "required": ["photo_id", "album_id"],
        },
    ),
    Tool(
        name="remove_from_album",
        description="Remove a photo from a Flickr album.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "album_id": {"type": "string", "description": "Flickr photoset ID"},
            },
            "required": ["photo_id", "album_id"],
        },
    ),
    Tool(
        name="create_album",
        description="Create a new Flickr album with an initial primary photo.",
        inputSchema={
            "type": "object",
            "properties": {
                "title":            {"type": "string", "description": "Album title"},
                "primary_photo_id": {"type": "string", "description": "Photo ID for the album cover"},
                "description":      {"type": "string", "description": "Optional album description"},
            },
            "required": ["title", "primary_photo_id"],
        },
    ),
    Tool(
        name="edit_album",
        description="Rename an album or update its description.",
        inputSchema={
            "type": "object",
            "properties": {
                "album_id":    {"type": "string", "description": "Flickr photoset ID"},
                "title":       {"type": "string", "description": "New title"},
                "description": {"type": "string", "description": "New description"},
            },
            "required": ["album_id"],
        },
    ),
    Tool(
        name="delete_album",
        description="Delete a Flickr album (photos are not deleted).",
        inputSchema={
            "type": "object",
            "properties": {
                "album_id": {"type": "string", "description": "Flickr photoset ID"},
            },
            "required": ["album_id"],
        },
    ),
]


async def _find_albums(args):
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    conn = db()
    rows = conn.execute(
        "SELECT id, title, description, count_photos, count_views FROM albums "
        "WHERE title LIKE ? ORDER BY title LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    conn.close()
    if not rows:
        return [TextContent(type="text", text=f"No albums found matching '{query}'. Visit /sync to run an albums sync first.")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _get_album_photos(args):
    album_id = args["album_id"]
    limit = int(args.get("limit", 50))
    page = int(args.get("page", 1))
    creds = flickr_api._load_credentials()
    data = flickr_api._api_get("flickr.photosets.getPhotos", {
        "photoset_id": album_id,
        "user_id": creds["user_nsid"],
        "per_page": str(limit),
        "page": str(page),
        "extras": "title,url_photopage",
    })
    photos = [{"id": p["id"], "title": p.get("title", "")} for p in data["photoset"]["photo"]]
    total = int(data["photoset"]["total"])
    pages = int(data["photoset"]["pages"])
    return [TextContent(type="text", text=json.dumps({"total": total, "pages": pages, "page": page, "returned": len(photos), "photos": photos}, indent=2))]


async def _add_to_album(args):
    flickr_api._api_post("flickr.photosets.addPhoto", {"photoset_id": args["album_id"], "photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to album {args['album_id']}.")]


async def _remove_from_album(args):
    flickr_api._api_post("flickr.photosets.removePhoto", {"photoset_id": args["album_id"], "photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} removed from album {args['album_id']}.")]


async def _create_album(args):
    data = flickr_api._api_post("flickr.photosets.create", {
        "title": args["title"],
        "primary_photo_id": args["primary_photo_id"],
        "description": args.get("description", ""),
    })
    album = data["photoset"]
    conn = db()
    conn.execute("""
        INSERT INTO albums (id, title, description, primary_photo_id, count_photos, count_views, synced_at)
        VALUES (?, ?, ?, ?, 1, 0, ?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, description=excluded.description,
            primary_photo_id=excluded.primary_photo_id, synced_at=excluded.synced_at
    """, (album["id"], args["title"], args.get("description", ""), args["primary_photo_id"], int(time.time())))
    conn.commit()
    conn.close()
    return [TextContent(type="text", text=f"Album created: {args['title']} (ID: {album['id']})\n{album.get('url', '')}")]


async def _edit_album(args):
    album_id = args["album_id"]
    conn = db()
    row = conn.execute("SELECT title, description FROM albums WHERE id = ?", (album_id,)).fetchone()
    title = args.get("title", row["title"] if row else "")
    description = args.get("description", row["description"] if row else "")
    flickr_api._api_post("flickr.photosets.editMeta", {"photoset_id": album_id, "title": title, "description": description})
    conn.execute("UPDATE albums SET title=?, description=? WHERE id=?", (title, description, album_id))
    conn.commit()
    conn.close()
    return [TextContent(type="text", text=f"Album {album_id} updated.")]


async def _delete_album(args):
    album_id = args["album_id"]
    flickr_api._api_post("flickr.photosets.delete", {"photoset_id": album_id})
    conn = db()
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    conn.commit()
    conn.close()
    return [TextContent(type="text", text=f"Album {album_id} deleted.")]


HANDLERS = {
    "find_albums":      _find_albums,
    "get_album_photos": _get_album_photos,
    "add_to_album":     _add_to_album,
    "remove_from_album": _remove_from_album,
    "create_album":     _create_album,
    "edit_album":       _edit_album,
    "delete_album":     _delete_album,
}
