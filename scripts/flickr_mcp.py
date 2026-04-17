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
    conn = db()
    row = conn.execute(
        "SELECT url_original, url_photopage FROM photos WHERE id = ?", (args["id"],)
    ).fetchone()
    conn.close()
    if not row:
        return [TextContent(type="text", text=f"Photo {args['id']} not found.")]

    url = row["url_original"]
    if not url:
        return [TextContent(type="text", text="No image URL available for this photo.")]

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    data = base64.standard_b64encode(resp.content).decode()
    return [
        TextContent(type="text", text=f"Photo ID: {args['id']}\n{row['url_photopage']}"),
        ImageContent(type="image", data=data, mimeType=mime),
    ]


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


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
