#!/usr/bin/env python3
"""Flickr MCP server — stdio interface."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent, TextContent, Tool

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "flickr.db")
SYNC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flickr_sync.py")
CREDENTIALS_FILE = os.path.expanduser("~/.flickr_mcp/credentials.json")
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
API_URL = "https://api.flickr.com/services/rest/"


# --- Flickr auth (mirrors flickr.py) ---

def _load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    api_key = env.get("FLICKR_API_KEY") or os.environ.get("FLICKR_API_KEY")
    api_secret = env.get("FLICKR_API_SECRET") or os.environ.get("FLICKR_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("FLICKR_API_KEY and FLICKR_API_SECRET must be set in .env")
    return api_key, api_secret


def _load_credentials():
    if not os.path.exists(CREDENTIALS_FILE):
        raise RuntimeError("Not logged in. Run: bin/flickr login")
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def _sign(method, url, params, api_secret, token_secret=""):
    sorted_params = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    base = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    sig = hmac.new(key.encode(), base.encode(), hashlib.sha1)
    return base64.b64encode(sig.digest()).decode()


def _oauth_params(api_key, extra=None):
    p = {
        "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
        "oauth_timestamp": str(int(time.time())),
        "oauth_consumer_key": api_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }
    if extra:
        p.update(extra)
    return p


def _api_get(method, extra=None):
    api_key, api_secret = _load_env()
    creds = _load_credentials()
    params = _oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": method,
        "format": "json",
        "nojsoncallback": "1",
    })
    if extra:
        params.update(extra)
    params["oauth_signature"] = _sign("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
    resp = requests.get(API_URL, params=params)
    data = resp.json()
    if data.get("stat") != "ok":
        raise RuntimeError(f"Flickr API error: {data.get('message', 'unknown')}")
    return data


def _api_post(method, extra=None):
    api_key, api_secret = _load_env()
    creds = _load_credentials()
    params = _oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": method,
        "format": "json",
        "nojsoncallback": "1",
    })
    if extra:
        params.update(extra)
    params["oauth_signature"] = _sign("POST", API_URL, params, api_secret, creds["oauth_token_secret"])
    resp = requests.post(API_URL, data=params)
    data = resp.json()
    if data.get("stat") != "ok":
        raise RuntimeError(f"Flickr API error: {data.get('message', 'unknown')}")
    return data

server = Server("flickr")


def db():
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(f"Database not found. Run: bin/flickr-sync --create")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="search_photos",
            description=(
                "Search and filter the photo collection. Supports keyword search on title, "
                "tag filtering, date range, and sorting by date or popularity (views)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query":     {"type": "string", "description": "Title keyword"},
                    "tags":      {"type": "string", "description": "Tag (partial match)"},
                    "date_from": {"type": "string", "description": "Earliest date taken, YYYY-MM-DD"},
                    "date_to":   {"type": "string", "description": "Latest date taken, YYYY-MM-DD"},
                    "sort_by":   {"type": "string", "enum": ["date_taken", "views", "favorites", "date_uploaded"], "default": "date_taken"},
                    "order":     {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                    "limit":     {"type": "integer", "description": "Max results (default 50, max 200)"},
                    "incomplete": {"type": "boolean", "description": "Only return photos missing a title, description, or tags"},
                },
            },
        ),
        Tool(
            name="get_photo",
            description="Return full metadata for a single photo by its Flickr ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Flickr photo ID"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="get_summary",
            description=(
                "Return a summary of the entire photo collection: total count, total views, "
                "date range, last sync time, and top 20 tags by frequency."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_recent_syncs",
            description="Show sync history — when photo data was last fetched from Flickr.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of records (default 5)"},
                },
            },
        ),
        Tool(
            name="update_photo",
            description="Update a photo's title, description, and/or tags on Flickr and in the local database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id":          {"type": "string", "description": "Flickr photo ID"},
                    "title":       {"type": "string", "description": "New title"},
                    "description": {"type": "string", "description": "New description"},
                    "tags":        {"type": "string", "description": "Space-separated tags (replaces existing tags)"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="fetch_photo_image",
            description="Download a photo by ID and return it as an image for visual inspection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Flickr photo ID"},
                },
                "required": ["id"],
            },
        ),
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
                    "limit":    {"type": "integer", "description": "Max photos to return (default 50)"},
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
            name="find_groups",
            description="Search the user's Flickr groups by keyword from the local database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword to search group names"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
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
            name="find_unfollow_candidates",
            description=(
                "List contacts you follow ranked by lowest engagement (faves + comments on your photos). "
                "Excludes contacts on the do-not-unfollow list."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit":                  {"type": "integer", "description": "Max results (default 20)"},
                    "require_zero_engagement": {"type": "boolean", "description": "Only include contacts with zero engagement"},
                },
            },
        ),
        Tool(
            name="protect_contact",
            description="Add a contact to the do-not-unfollow whitelist so they never appear as a candidate.",
            inputSchema={
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string", "description": "Flickr NSID of the contact"},
                    "reason":     {"type": "string", "description": "Optional reason for protecting"},
                },
                "required": ["contact_id"],
            },
        ),
        Tool(
            name="unfollow_contact",
            description=(
                "Attempt to unfollow a contact via the Flickr API. "
                "Returns their profile URL regardless; optionally opens it in Safari."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contact_id":   {"type": "string",  "description": "Flickr NSID of the contact"},
                    "open_browser": {"type": "boolean", "description": "Open their profile in Safari (default false)"},
                },
                "required": ["contact_id"],
            },
        ),
        Tool(
            name="find_weak_photos",
            description=(
                "Rank public photos by a weakness score combining low views-per-day, "
                "zero favorites, and zero comments. Use to find candidates for making private."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit":                  {"type": "integer", "description": "Max results (default 20, max 100)"},
                    "min_age_days":           {"type": "integer", "description": "Min days since upload (default 30)"},
                    "require_zero_favorites": {"type": "boolean", "description": "Only include photos with 0 favorites"},
                },
            },
        ),
        Tool(
            name="set_visibility",
            description="Set a photo's visibility on Flickr — pass is_public=false to make it private.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id":        {"type": "string",  "description": "Flickr photo ID"},
                    "is_public": {"type": "boolean", "description": "False = private"},
                    "is_friend": {"type": "boolean", "description": "Visible to friends (default false)"},
                    "is_family": {"type": "boolean", "description": "Visible to family (default false)"},
                },
                "required": ["id", "is_public"],
            },
        ),
        Tool(
            name="sync",
            description=(
                "Fetch updated photo metadata from Flickr into the local database. "
                "Incremental by default; pass full=true to re-fetch everything."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "full": {"type": "boolean", "description": "Re-fetch all photos instead of just updates"},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        match name:
            case "search_photos":    return await _search_photos(arguments)
            case "get_photo":        return await _get_photo(arguments)
            case "get_summary":      return await _get_summary()
            case "list_recent_syncs": return await _list_recent_syncs(arguments)
            case "update_photo":      return await _update_photo(arguments)
            case "fetch_photo_image": return await _fetch_photo_image(arguments)
            case "find_unfollow_candidates": return await _find_unfollow_candidates(arguments)
            case "protect_contact":   return await _protect_contact(arguments)
            case "unfollow_contact":  return await _unfollow_contact(arguments)
            case "find_albums":       return await _find_albums(arguments)
            case "get_album_photos":  return await _get_album_photos(arguments)
            case "add_to_album":      return await _add_to_album(arguments)
            case "remove_from_album": return await _remove_from_album(arguments)
            case "create_album":      return await _create_album(arguments)
            case "edit_album":        return await _edit_album(arguments)
            case "delete_album":      return await _delete_album(arguments)
            case "remove_from_group": return await _remove_from_group(arguments)
            case "find_groups":       return await _find_groups(arguments)
            case "add_to_group":      return await _add_to_group(arguments)
            case "find_weak_photos":  return await _find_weak_photos(arguments)
            case "set_visibility":    return await _set_visibility(arguments)
            case "sync":             return await _sync(arguments)
            case _: return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except FileNotFoundError as e:
        return [TextContent(type="text", text=str(e))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def _search_photos(args):
    conn = db()
    conditions, params = [], []

    if args.get("query"):
        conditions.append("title LIKE ?")
        params.append(f"%{args['query']}%")
    if args.get("tags"):
        conditions.append("tags LIKE ?")
        params.append(f"%{args['tags']}%")
    if args.get("date_from"):
        conditions.append("date_taken >= ?")
        params.append(args["date_from"])
    if args.get("date_to"):
        conditions.append("date_taken <= ?")
        params.append(args["date_to"] + " 99:99:99")
    if args.get("incomplete"):
        conditions.append("""(
            (title IS NULL OR title = '' OR title = id)
            OR (tags IS NULL OR tags = '')
            OR (description IS NULL OR description = '')
        )""")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sort_by = args.get("sort_by", "date_taken")
    if sort_by not in ("date_taken", "views", "favorites", "date_uploaded"):
        sort_by = "date_taken"
    order = "ASC" if args.get("order", "desc") == "asc" else "DESC"
    limit = min(int(args.get("limit", 50)), 200)

    rows = conn.execute(
        f"SELECT * FROM photos {where} ORDER BY {sort_by} {order} LIMIT ?",
        params + [limit],
    ).fetchall()
    conn.close()
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _get_photo(args):
    conn = db()
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (args["id"],)).fetchone()
    conn.close()
    if not row:
        return [TextContent(type="text", text=f"Photo {args['id']} not found.")]
    return [TextContent(type="text", text=json.dumps(dict(row), indent=2))]


async def _get_summary():
    conn = db()
    stats = conn.execute("""
        SELECT COUNT(*) AS total_photos,
               SUM(views) AS total_views,
               MIN(date_taken) AS earliest,
               MAX(date_taken) AS latest,
               MAX(synced_at) AS last_synced
        FROM photos
    """).fetchone()

    tag_rows = conn.execute(
        "SELECT tags FROM photos WHERE tags != '' AND tags IS NOT NULL"
    ).fetchall()
    conn.close()

    counts = {}
    for row in tag_rows:
        for tag in row[0].split():
            counts[tag] = counts.get(tag, 0) + 1
    top_tags = [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda x: -x[1])[:20]]

    result = {
        "total_photos": stats["total_photos"],
        "total_views":  stats["total_views"],
        "date_range":   {"earliest": stats["earliest"], "latest": stats["latest"]},
        "last_synced":  datetime.fromtimestamp(stats["last_synced"]).isoformat() if stats["last_synced"] else None,
        "top_tags":     top_tags,
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _list_recent_syncs(args):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM sync_log ORDER BY synced_at DESC LIMIT ?",
        (int(args.get("limit", 5)),),
    ).fetchall()
    conn.close()
    syncs = [{
        "id": r["id"],
        "synced_at": datetime.fromtimestamp(r["synced_at"]).isoformat() if r["synced_at"] else None,
        "mode": r["mode"],
        "photos_fetched": r["photos_fetched"],
    } for r in rows]
    return [TextContent(type="text", text=json.dumps(syncs, indent=2))]


async def _update_photo(args):
    photo_id = args["id"]
    updated = []

    if "title" in args or "description" in args:
        conn = db()
        row = conn.execute("SELECT title, description FROM photos WHERE id = ?", (photo_id,)).fetchone()
        conn.close()
        title = args.get("title", row["title"] if row else "")
        description = args.get("description", row["description"] if row else "")
        _api_post("flickr.photos.setMeta", {
            "photo_id": photo_id,
            "title": title,
            "description": description,
        })
        updated.append("title/description")

    if "tags" in args:
        _api_post("flickr.photos.setTags", {
            "photo_id": photo_id,
            "tags": args["tags"],
        })
        updated.append("tags")

    # Update local db
    conn = db()
    if "title" in args:
        conn.execute("UPDATE photos SET title=? WHERE id=?", (args["title"], photo_id))
    if "description" in args:
        conn.execute("UPDATE photos SET description=? WHERE id=?", (args["description"], photo_id))
    if "tags" in args:
        conn.execute("UPDATE photos SET tags=? WHERE id=?", (args["tags"], photo_id))
    conn.commit()
    conn.close()

    return [TextContent(type="text", text=f"Updated {', '.join(updated)} for photo {photo_id}.")]


async def _fetch_photo_image(args):
    photo_id = args["id"]
    conn = db()
    row = conn.execute(
        "SELECT url_original, url_photopage FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()
    conn.close()

    if row:
        photopage = row["url_photopage"]
    else:
        info = _api_get("flickr.photos.getInfo", {"photo_id": photo_id})
        photo = info["photo"]
        owner = photo["owner"]["nsid"]
        photopage = f"https://www.flickr.com/photos/{owner}/{photo_id}/"

    # always fetch the live URL so edits are reflected
    sizes_data = _api_get("flickr.photos.getSizes", {"photo_id": photo_id})
    sizes = sizes_data["sizes"]["size"]
    preferred = ("Original", "Large 2048", "Large 1600", "Large")
    url = next(
        (s["source"] for label in preferred for s in sizes if s["label"] == label),
        sizes[-1]["source"],
    )

    if not url:
        return [TextContent(type="text", text="No image URL available for this photo.")]

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    data = base64.standard_b64encode(resp.content).decode()
    return [
        TextContent(type="text", text=f"Photo ID: {photo_id}\n{photopage}"),
        ImageContent(type="image", data=data, mimeType=mime),
    ]


async def _find_unfollow_candidates(args):
    limit = int(args.get("limit", 20))
    require_zero = args.get("require_zero_engagement", False)
    extra_where = "AND COALESCE(e.faves, 0) + COALESCE(e.comments, 0) = 0" if require_zero else ""

    sql = f"""
        SELECT c.id, c.username, c.realname,
               COALESCE(e.faves, 0)    AS faves,
               COALESCE(e.comments, 0) AS comments,
               COALESCE(e.faves, 0) + COALESCE(e.comments, 0) AS total_engagement
        FROM contacts c
        LEFT JOIN contact_engagement e ON e.contact_id = c.id
        WHERE c.id NOT IN (SELECT contact_id FROM do_not_unfollow)
        {extra_where}
        ORDER BY total_engagement ASC, c.username ASC
        LIMIT ?
    """
    conn = db()
    rows = conn.execute(sql, (limit,)).fetchall()
    conn.close()

    if not rows:
        return [TextContent(type="text", text="No contacts found. Run bin/sync-contacts first.")]

    results = [{
        "contact_id":       r["id"],
        "username":         r["username"],
        "realname":         r["realname"],
        "faves":            r["faves"],
        "comments":         r["comments"],
        "total_engagement": r["total_engagement"],
        "url_profile":      f"https://www.flickr.com/people/{r['id']}/",
    } for r in rows]
    return [TextContent(type="text", text=json.dumps(results, indent=2))]


async def _protect_contact(args):
    contact_id = args["contact_id"]
    reason = args.get("reason", "")
    conn = db()
    conn.execute(
        "INSERT INTO do_not_unfollow (contact_id, reason, added_at) VALUES (?, ?, ?) "
        "ON CONFLICT(contact_id) DO UPDATE SET reason=excluded.reason",
        (contact_id, reason, int(time.time())),
    )
    conn.commit()
    conn.close()
    return [TextContent(type="text", text=f"Contact {contact_id} added to do-not-unfollow list.")]


async def _unfollow_contact(args):
    contact_id = args["contact_id"]
    open_browser = args.get("open_browser", False)
    profile_url = f"https://www.flickr.com/people/{contact_id}/"
    api_result = ""

    try:
        _api_post("flickr.contacts.remove", {"user_nsid": contact_id})
        conn = db()
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()
        conn.close()
        api_result = "Unfollowed via API. "
    except RuntimeError as e:
        api_result = f"API unfollow failed ({e}) — use profile URL to unfollow manually. "

    if open_browser:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e",
            f'tell application "Safari" to set URL of current tab of front window to "{profile_url}"',
        )
        await proc.communicate()

    return [TextContent(type="text", text=f"{api_result}Profile: {profile_url}")]


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
        return [TextContent(type="text", text=f"No albums found matching '{query}'. Run bin/sync-albums first.")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _get_album_photos(args):
    album_id = args["album_id"]
    limit = int(args.get("limit", 50))
    creds = _load_credentials()
    data = _api_get("flickr.photosets.getPhotos", {
        "photoset_id": album_id,
        "user_id": creds["user_nsid"],
        "per_page": str(limit),
        "page": "1",
        "extras": "title,url_photopage",
    })
    photos = [{"id": p["id"], "title": p.get("title", "")} for p in data["photoset"]["photo"]]
    total = int(data["photoset"]["total"])
    return [TextContent(type="text", text=json.dumps({"total": total, "returned": len(photos), "photos": photos}, indent=2))]


async def _add_to_album(args):
    _api_post("flickr.photosets.addPhoto", {"photoset_id": args["album_id"], "photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to album {args['album_id']}.")]


async def _remove_from_album(args):
    _api_post("flickr.photosets.removePhoto", {"photoset_id": args["album_id"], "photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} removed from album {args['album_id']}.")]


async def _create_album(args):
    data = _api_post("flickr.photosets.create", {
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
    _api_post("flickr.photosets.editMeta", {"photoset_id": album_id, "title": title, "description": description})
    conn.execute("UPDATE albums SET title=?, description=? WHERE id=?", (title, description, album_id))
    conn.commit()
    conn.close()
    return [TextContent(type="text", text=f"Album {album_id} updated.")]


async def _delete_album(args):
    album_id = args["album_id"]
    _api_post("flickr.photosets.delete", {"photoset_id": album_id})
    conn = db()
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    conn.commit()
    conn.close()
    return [TextContent(type="text", text=f"Album {album_id} deleted.")]


async def _remove_from_group(args):
    _api_post("flickr.groups.pools.remove", {"photo_id": args["photo_id"], "group_id": args["group_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} removed from group {args['group_id']}.")]


async def _find_groups(args):
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    conn = db()
    rows = conn.execute(
        "SELECT id, name, members, pool_count FROM groups WHERE name LIKE ? ORDER BY members DESC LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    conn.close()
    if not rows:
        return [TextContent(type="text", text=f"No groups found matching '{query}'. Run sync to populate groups.")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _add_to_group(args):
    _api_post("flickr.groups.pools.add", {
        "photo_id": args["photo_id"],
        "group_id": args["group_id"],
    })
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to group {args['group_id']}.")]


async def _find_weak_photos(args):
    limit = min(int(args.get("limit", 20)), 100)
    min_age_days = int(args.get("min_age_days", 30))
    extra_where = "AND favorites = 0" if args.get("require_zero_favorites") else ""

    sql = f"""
        WITH scored AS (
            SELECT id, title, tags, date_taken, date_uploaded, views, favorites, comments,
                   url_photopage,
                   CAST((strftime('%s','now') - date_uploaded) AS REAL) / 86400.0 AS days_since_upload,
                   CAST(views AS REAL) / MAX(
                       CAST((strftime('%s','now') - date_uploaded) AS REAL) / 86400.0, 1.0
                   ) AS views_per_day,
                   (1.0 / (
                       CAST(views AS REAL) / MAX(
                           CAST((strftime('%s','now') - date_uploaded) AS REAL) / 86400.0, 1.0
                       ) + 0.1
                   ))
                   + CASE WHEN favorites = 0 THEN 2.0 ELSE 0.0 END
                   + CASE WHEN comments = 0 THEN 1.0 ELSE 0.0 END
                   AS weakness_score
            FROM photos
            WHERE date_uploaded IS NOT NULL
              AND date_uploaded < (strftime('%s','now') - ? * 86400)
              {extra_where}
        )
        SELECT * FROM scored ORDER BY weakness_score DESC LIMIT ?
    """

    conn = db()
    rows = conn.execute(sql, (min_age_days, limit)).fetchall()
    conn.close()

    results = [{
        "id":               r["id"],
        "title":            r["title"],
        "tags":             r["tags"],
        "date_taken":       r["date_taken"],
        "days_since_upload": round(r["days_since_upload"], 1),
        "views":            r["views"],
        "favorites":        r["favorites"],
        "comments":         r["comments"],
        "views_per_day":    round(r["views_per_day"], 4),
        "weakness_score":   round(r["weakness_score"], 2),
        "url_photopage":    r["url_photopage"],
    } for r in rows]

    return [TextContent(type="text", text=json.dumps(results, indent=2))]


async def _set_visibility(args):
    photo_id = args["id"]
    is_public = 1 if args["is_public"] else 0
    is_friend = 1 if args.get("is_friend", False) else 0
    is_family = 1 if args.get("is_family", False) else 0

    _api_post("flickr.photos.setPerms", {
        "photo_id":      photo_id,
        "is_public":     str(is_public),
        "is_friend":     str(is_friend),
        "is_family":     str(is_family),
        "perm_comment":  "3" if is_public else "0",
        "perm_addmeta":  "2" if is_public else "0",
    })

    visibility = "public" if is_public else "private"
    return [TextContent(type="text", text=f"Photo {photo_id} is now {visibility} on Flickr.")]


async def _sync(args):
    cmd = [sys.executable, SYNC_SCRIPT]
    if args.get("full"):
        cmd.append("--full")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    status = "completed" if proc.returncode == 0 else "failed"
    return [TextContent(type="text", text=f"Sync {status}:\n{stdout.decode()}")]


REFRESH_INTERVAL = 86400  # 24 hours


async def _background_refresh():
    """Check daily whether photo/contact/group data needs refreshing and sync if so."""
    while True:
        try:
            if os.path.exists(DB_FILE):
                conn = sqlite3.connect(DB_FILE)
                row = conn.execute("SELECT MAX(synced_at) FROM sync_log").fetchone()
                conn.close()
                last_sync = row[0] if row and row[0] else 0
                age = time.time() - last_sync

                if age >= REFRESH_INTERVAL:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, SYNC_SCRIPT,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.communicate()

                    # also refresh contacts and groups
                    for script in ("sync_contacts.py", "sync_groups.py"):
                        path = os.path.join(os.path.dirname(SYNC_SCRIPT), script)
                        if os.path.exists(path):
                            p = await asyncio.create_subprocess_exec(
                                sys.executable, path,
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            await p.communicate()

                    sleep_for = REFRESH_INTERVAL
                else:
                    sleep_for = REFRESH_INTERVAL - age
            else:
                sleep_for = REFRESH_INTERVAL
        except Exception:
            sleep_for = REFRESH_INTERVAL

        await asyncio.sleep(sleep_for)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        asyncio.create_task(_background_refresh())
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
