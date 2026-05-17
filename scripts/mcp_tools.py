"""MCP server instance, tool definitions, and tool handlers."""

import asyncio
import base64
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime

import requests
from mcp.server import Server
from mcp.types import ImageContent, TextContent, Tool

from db import DB_FILE, db
from flickr_api import (
    HTTP_TIMEOUT,
    _api_get,
    _api_post,
    _load_credentials,
)

server = Server("flickr")
_sync_lock = asyncio.Lock()

SYNC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flickr_sync.py")
REFRESH_INTERVAL = 86400  # 24 hours


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
            name="get_photo_comments",
            description="Fetch all comments on a Flickr photo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "photo_id": {"type": "string", "description": "Flickr photo ID"},
                },
                "required": ["photo_id"],
            },
        ),
        Tool(
            name="add_comment",
            description="Post a comment on a Flickr photo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "photo_id":      {"type": "string", "description": "Flickr photo ID"},
                    "comment_text":  {"type": "string", "description": "Text of the comment to post"},
                },
                "required": ["photo_id", "comment_text"],
            },
        ),
        Tool(
            name="delete_comment",
            description="Delete a comment posted on a Flickr photo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "comment_id": {"type": "string", "description": "Flickr comment ID"},
                },
                "required": ["comment_id"],
            },
        ),
        Tool(
            name="fave_photo",
            description="Add a photo to the user's Flickr favorites.",
            inputSchema={
                "type": "object",
                "properties": {
                    "photo_id": {"type": "string", "description": "Flickr photo ID"},
                },
                "required": ["photo_id"],
            },
        ),
        Tool(
            name="get_photo_stats",
            description="Get view/favorite/comment stats for a photo on a specific date (defaults to today).",
            inputSchema={
                "type": "object",
                "properties": {
                    "photo_id": {"type": "string", "description": "Flickr photo ID"},
                    "date":     {"type": "string", "description": "Date to query, YYYY-MM-DD (default: today)"},
                },
                "required": ["photo_id"],
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
            name="get_contacts_summary",
            description="Return an overview of followed contacts: total count, friend/family breakdown, engagement stats, and top engagers.",
            inputSchema={"type": "object", "properties": {}},
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
                "Returns their profile URL regardless."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string", "description": "Flickr NSID of the contact"},
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
                    "review_cooldown_days":   {"type": "integer", "description": "Skip photos reviewed within this many days (default 60)"},
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
            name="set_location",
            description=(
                "Set the geolocation of a photo on Flickr. "
                "Accepts latitude and longitude (decimal degrees). "
                "accuracy is optional (1–16, default 16 = street level)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id":       {"type": "string",  "description": "Flickr photo ID"},
                    "lat":      {"type": "number",  "description": "Latitude (decimal degrees)"},
                    "lon":      {"type": "number",  "description": "Longitude (decimal degrees)"},
                    "accuracy": {"type": "integer", "description": "Location accuracy 1–16 (default 16 = street)"},
                },
                "required": ["id", "lat", "lon"],
            },
        ),
        Tool(
            name="sync",
            description=(
                "Sync Flickr data into the local database. "
                "type controls what to sync: 'photos' (default), 'groups', 'contacts', 'albums', or 'all'. "
                "Pass full=true to re-fetch all photos instead of just updates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "What to sync: photos, groups, contacts, albums, or all (default: photos)"},
                    "full": {"type": "boolean", "description": "Re-fetch all photos instead of just updates (photos sync only)"},
                },
            },
        ),
        Tool(
            name="get_exif",
            description="Fetch EXIF data for a photo (camera, lens, exposure settings, etc.).",
            inputSchema={
                "type": "object",
                "properties": {
                    "photo_id": {"type": "string", "description": "Flickr photo ID"},
                },
                "required": ["photo_id"],
            },
        ),
        Tool(
            name="get_upload_status",
            description="Get the user's upload bandwidth and storage status for the current month.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_person_info",
            description="Fetch public profile info for a Flickr user by NSID or username.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Flickr NSID or username"},
                },
                "required": ["user_id"],
            },
        ),
        Tool(
            name="get_photostream_stats",
            description="Get total view counts across all photos, sets, and galleries for a given date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date to query, YYYY-MM-DD (default: yesterday)"},
                },
            },
        ),
        Tool(
            name="get_popular_photos",
            description="List the user's most popular photos sorted by favorites, comments, or views.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sort":  {"type": "string", "enum": ["favorites", "comments", "views"], "description": "Sort order (default: favorites)"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
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
            name="get_faves",
            description="List photos the authenticated user has favorited.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                    "page":  {"type": "integer", "description": "Page number (default 1)"},
                },
            },
        ),
        Tool(
            name="get_recent_activity",
            description="Show recent comments and faves on the user's photos.",
            inputSchema={
                "type": "object",
                "properties": {
                    "timeframe": {"type": "string", "description": "Time window: 'day' or 'week' (default: day)"},
                    "limit":     {"type": "integer", "description": "Max items (default 20)"},
                },
            },
        ),
        Tool(
            name="remove_fave",
            description="Remove a photo from the user's Flickr favorites.",
            inputSchema={
                "type": "object",
                "properties": {
                    "photo_id": {"type": "string", "description": "Flickr photo ID"},
                },
                "required": ["photo_id"],
            },
        ),
        Tool(
            name="remove_location",
            description="Remove the geolocation from a photo on Flickr.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Flickr photo ID"},
                },
                "required": ["id"],
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
            name="set_safety_level",
            description="Set the safety level of a photo: safe, moderate, or restricted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id":           {"type": "string", "description": "Flickr photo ID"},
                    "safety_level": {"type": "string", "enum": ["safe", "moderate", "restricted"], "description": "Safety level"},
                },
                "required": ["id", "safety_level"],
            },
        ),
        Tool(
            name="set_content_type",
            description="Set the content type of a photo: photo, screenshot, or other.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id":           {"type": "string", "description": "Flickr photo ID"},
                    "content_type": {"type": "string", "enum": ["photo", "screenshot", "other"], "description": "Content type"},
                },
                "required": ["id", "content_type"],
            },
        ),
        Tool(
            name="set_dates",
            description="Set the date taken for a photo (corrects wrong timestamps from camera clock errors).",
            inputSchema={
                "type": "object",
                "properties": {
                    "id":          {"type": "string", "description": "Flickr photo ID"},
                    "date_taken":  {"type": "string", "description": "Date taken, YYYY-MM-DD HH:MM:SS"},
                    "granularity": {"type": "string", "enum": ["exact", "month", "year"], "description": "Precision of date_taken (default: exact)"},
                },
                "required": ["id", "date_taken"],
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
            name="get_contact_uploads",
            description="Show recent photo uploads from people you follow.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit":        {"type": "integer", "description": "Max photos (default 20)"},
                    "just_friends": {"type": "boolean", "description": "Only show uploads from people marked as friends"},
                },
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


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        match name:
            case "search_photos":         return await _search_photos(arguments)
            case "get_photo":             return await _get_photo(arguments)
            case "get_summary":           return await _get_summary()
            case "list_recent_syncs":     return await _list_recent_syncs(arguments)
            case "update_photo":          return await _update_photo(arguments)
            case "fetch_photo_image":     return await _fetch_photo_image(arguments)
            case "get_contacts_summary":      return await _get_contacts_summary()
            case "find_unfollow_candidates":  return await _find_unfollow_candidates(arguments)
            case "protect_contact":       return await _protect_contact(arguments)
            case "unfollow_contact":      return await _unfollow_contact(arguments)
            case "get_photo_comments":    return await _get_photo_comments(arguments)
            case "add_comment":           return await _add_comment(arguments)
            case "delete_comment":        return await _delete_comment(arguments)
            case "fave_photo":            return await _fave_photo(arguments)
            case "get_photo_stats":       return await _get_photo_stats(arguments)
            case "find_albums":           return await _find_albums(arguments)
            case "get_album_photos":      return await _get_album_photos(arguments)
            case "add_to_album":          return await _add_to_album(arguments)
            case "remove_from_album":     return await _remove_from_album(arguments)
            case "create_album":          return await _create_album(arguments)
            case "edit_album":            return await _edit_album(arguments)
            case "delete_album":          return await _delete_album(arguments)
            case "remove_from_group":     return await _remove_from_group(arguments)
            case "find_groups":           return await _find_groups(arguments)
            case "set_group_keywords":    return await _set_group_keywords(arguments)
            case "add_to_group":          return await _add_to_group(arguments)
            case "find_weak_photos":      return await _find_weak_photos(arguments)
            case "set_visibility":        return await _set_visibility(arguments)
            case "set_location":          return await _set_location(arguments)
            case "sync":                  return await _sync(arguments)
            case "get_exif":              return await _get_exif(arguments)
            case "get_upload_status":     return await _get_upload_status()
            case "get_person_info":       return await _get_person_info(arguments)
            case "get_photostream_stats": return await _get_photostream_stats(arguments)
            case "get_popular_photos":    return await _get_popular_photos(arguments)
            case "get_gallery_photos":    return await _get_gallery_photos(arguments)
            case "get_group_photos":      return await _get_group_photos(arguments)
            case "get_faves":             return await _get_faves(arguments)
            case "get_recent_activity":   return await _get_recent_activity(arguments)
            case "remove_fave":           return await _remove_fave(arguments)
            case "remove_location":       return await _remove_location(arguments)
            case "join_group":            return await _join_group(arguments)
            case "leave_group":           return await _leave_group(arguments)
            case "set_safety_level":      return await _set_safety_level(arguments)
            case "set_content_type":      return await _set_content_type(arguments)
            case "set_dates":             return await _set_dates(arguments)
            case "create_gallery":        return await _create_gallery(arguments)
            case "add_to_gallery":        return await _add_to_gallery(arguments)
            case "get_galleries":         return await _get_galleries(arguments)
            case "get_contact_uploads":   return await _get_contact_uploads(arguments)
            case "search_all_groups":     return await _search_all_groups(arguments)
            case _: return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except (FileNotFoundError, RuntimeError) as e:
        return [TextContent(type="text", text=str(e))]
    except Exception as e:
        logging.exception("Unexpected error in tool %s", name)
        return [TextContent(type="text", text=f"Unexpected error: {type(e).__name__}")]


# --- Tool handlers ---

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
        params.append(args["date_to"] + " 23:59:59")
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
               SUM(CASE WHEN is_public = 1 THEN 1 ELSE 0 END) AS public_photos,
               SUM(CASE WHEN is_public = 0 THEN 1 ELSE 0 END) AS private_photos,
               SUM(views) AS total_views,
               MIN(date_taken) AS earliest,
               MAX(date_taken) AS latest,
               MAX(synced_at) AS last_synced
        FROM photos
    """).fetchone()
    tag_rows = conn.execute("SELECT tags FROM photos WHERE tags != '' AND tags IS NOT NULL").fetchall()
    group_count   = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
    album_count   = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    contact_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    conn.close()
    counts = {}
    for row in tag_rows:
        for tag in row[0].split():
            counts[tag] = counts.get(tag, 0) + 1
    top_tags = [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda x: -x[1])[:20]]
    result = {
        "total_photos":   stats["total_photos"],
        "public_photos":  stats["public_photos"],
        "private_photos": stats["private_photos"],
        "total_views":    stats["total_views"],
        "total_groups":   group_count,
        "total_albums":   album_count,
        "total_contacts": contact_count,
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
        _api_post("flickr.photos.setMeta", {"photo_id": photo_id, "title": title, "description": description})
        updated.append("title/description")
    if "tags" in args:
        _api_post("flickr.photos.setTags", {"photo_id": photo_id, "tags": args["tags"]})
        updated.append("tags")
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
    row = conn.execute("SELECT url_original, url_photopage FROM photos WHERE id = ?", (photo_id,)).fetchone()
    conn.close()
    if row:
        photopage = row["url_photopage"]
    else:
        info = _api_get("flickr.photos.getInfo", {"photo_id": photo_id})
        photo = info["photo"]
        owner = photo["owner"]["nsid"]
        photopage = f"https://www.flickr.com/photos/{owner}/{photo_id}/"
    sizes_data = _api_get("flickr.photos.getSizes", {"photo_id": photo_id})
    sizes = sizes_data["sizes"]["size"]
    preferred = ("Large 2048", "Large 1600", "Large")
    url = next(
        (s["source"] for label in preferred for s in sizes if s["label"] == label),
        sizes[-1]["source"],
    )
    if not url:
        return [TextContent(type="text", text="No image URL available for this photo.")]
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    data = base64.standard_b64encode(resp.content).decode()
    return [
        TextContent(type="text", text=f"Photo ID: {photo_id}\n{photopage}"),
        ImageContent(type="image", data=data, mimeType=mime),
    ]


async def _get_contacts_summary():
    conn = db()
    total       = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    friends     = conn.execute("SELECT COUNT(*) FROM contacts WHERE is_friend = 1").fetchone()[0]
    family      = conn.execute("SELECT COUNT(*) FROM contacts WHERE is_family = 1").fetchone()[0]
    protected   = conn.execute("SELECT COUNT(*) FROM do_not_unfollow").fetchone()[0]
    eng_total   = conn.execute("SELECT COUNT(*) FROM contact_engagement").fetchone()[0]
    eng_nonzero = conn.execute("SELECT COUNT(*) FROM contact_engagement WHERE faves > 0 OR comments > 0").fetchone()[0]
    top_rows    = conn.execute("""
        SELECT c.username, c.realname, e.faves, e.comments, e.faves + e.comments AS total
        FROM contact_engagement e
        JOIN contacts c ON c.id = e.contact_id
        WHERE e.faves > 0 OR e.comments > 0
        ORDER BY total DESC LIMIT 10
    """).fetchall()
    conn.close()
    summary = {
        "total_following": total,
        "friends": friends,
        "family": family,
        "protected_from_unfollow": protected,
        "engagement_data": {
            "contacts_with_records": eng_total,
            "contacts_with_any_engagement": eng_nonzero,
            "note": "Visit /sync to run engagement sync (~20 min)." if eng_total == 0 else None,
        },
        "top_engagers": [
            {"username": r["username"], "realname": r["realname"],
             "faves": r["faves"], "comments": r["comments"]}
            for r in top_rows
        ],
    }
    return [TextContent(type="text", text=json.dumps(summary, indent=2))]


async def _find_unfollow_candidates(args):
    limit = int(args.get("limit", 20))
    require_zero = 1 if args.get("require_zero_engagement") else 0
    sql = """
        SELECT c.id, c.username, c.realname,
               COALESCE(e.faves, 0)    AS faves,
               COALESCE(e.comments, 0) AS comments,
               COALESCE(e.faves, 0) + COALESCE(e.comments, 0) AS total_engagement
        FROM contacts c
        LEFT JOIN contact_engagement e ON e.contact_id = c.id
        WHERE c.id NOT IN (SELECT contact_id FROM do_not_unfollow)
          AND (? = 0 OR COALESCE(e.faves, 0) + COALESCE(e.comments, 0) = 0)
        ORDER BY total_engagement ASC, c.username ASC LIMIT ?
    """
    conn = db()
    rows = conn.execute(sql, (require_zero, limit)).fetchall()
    conn.close()
    if not rows:
        return [TextContent(type="text", text="No contacts found. Visit /sync to run a contacts sync first.")]
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
    profile_url = f"https://www.flickr.com/people/{contact_id}/"
    try:
        _api_post("flickr.contacts.remove", {"user_nsid": contact_id})
        conn = db()
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()
        conn.close()
        api_result = "Unfollowed via API. "
    except RuntimeError as e:
        api_result = f"API unfollow failed ({e}) — use profile URL to unfollow manually. "
    return [TextContent(type="text", text=f"{api_result}Profile: {profile_url}")]


async def _get_photo_comments(args):
    data = _api_get("flickr.photos.comments.getList", {"photo_id": args["photo_id"]})
    comments = [{
        "author":    c["authorname"],
        "date":      datetime.fromtimestamp(int(c["datecreate"])).strftime("%Y-%m-%d"),
        "comment":   c["_content"],
        "permalink": c["permalink"],
    } for c in data.get("comments", {}).get("comment", [])]
    if not comments:
        return [TextContent(type="text", text="No comments found.")]
    return [TextContent(type="text", text=json.dumps(comments, indent=2))]


async def _add_comment(args):
    data = _api_post("flickr.photos.comments.addComment", {
        "photo_id":     args["photo_id"],
        "comment_text": args["comment_text"],
    })
    comment_id = data.get("comment", {}).get("id", "unknown")
    return [TextContent(type="text", text=f"Comment posted (id: {comment_id}).")]


async def _delete_comment(args):
    _api_post("flickr.photos.comments.deleteComment", {"comment_id": args["comment_id"]})
    return [TextContent(type="text", text=f"Comment {args['comment_id']} deleted.")]


async def _fave_photo(args):
    _api_post("flickr.favorites.add", {"photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to favorites.")]


async def _get_photo_stats(args):
    from datetime import date as date_type
    photo_id = args["photo_id"]
    query_date = args.get("date", date_type.today().isoformat())
    data = _api_get("flickr.stats.getPhotoStats", {"photo_id": photo_id, "date": query_date})
    stats = data.get("stats", {})
    return [TextContent(type="text", text=json.dumps({
        "date":      query_date,
        "views":     stats.get("views", 0),
        "favorites": stats.get("favorites", 0),
        "comments":  stats.get("comments", 0),
    }, indent=2))]


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
    creds = _load_credentials()
    data = _api_get("flickr.photosets.getPhotos", {
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
    pat = f"%{query}%"
    rows = conn.execute(
        "SELECT id, name, members, pool_count FROM groups "
        "WHERE name LIKE ? OR description LIKE ? OR keywords LIKE ? "
        "ORDER BY members DESC LIMIT ?",
        (pat, pat, pat, limit),
    ).fetchall()
    conn.close()
    if not rows:
        return [TextContent(type="text", text=f"No groups found matching '{query}'. Run sync to populate groups.")]
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _set_group_keywords(args):
    group_id = args["group_id"]
    keywords = args["keywords"]
    conn = db()
    updated = conn.execute("UPDATE groups SET keywords=? WHERE id=?", (keywords, group_id)).rowcount
    conn.commit()
    conn.close()
    if not updated:
        return [TextContent(type="text", text=f"Group {group_id} not found in local database.")]
    return [TextContent(type="text", text=f"Keywords updated for group {group_id}.")]


async def _add_to_group(args):
    _api_post("flickr.groups.pools.add", {"photo_id": args["photo_id"], "group_id": args["group_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to group {args['group_id']}.")]


async def _find_weak_photos(args):
    limit = min(int(args.get("limit", 20)), 100)
    min_age_days = int(args.get("min_age_days", 30))
    require_zero_faves = 1 if args.get("require_zero_favorites") else 0
    review_cooldown_days = int(args.get("review_cooldown_days", 60))
    sql = """
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
              AND (reviewed_at IS NULL OR reviewed_at < (strftime('%s','now') - ? * 86400))
              AND (is_public IS NULL OR is_public != 0)
              AND (? = 0 OR favorites = 0)
        )
        SELECT * FROM scored ORDER BY weakness_score DESC LIMIT ?
    """
    conn = db()
    rows = conn.execute(sql, (min_age_days, review_cooldown_days, require_zero_faves, limit)).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        conn.execute(
            f"UPDATE photos SET reviewed_at = strftime('%s','now') WHERE id IN ({','.join('?'*len(ids))})",
            ids,
        )
        conn.commit()
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
        "photo_id":     photo_id,
        "is_public":    str(is_public),
        "is_friend":    str(is_friend),
        "is_family":    str(is_family),
        "perm_comment": "3" if is_public else "0",
        "perm_addmeta": "2" if is_public else "0",
    })
    conn = db()
    conn.execute("UPDATE photos SET is_public = ? WHERE id = ?", (is_public, photo_id))
    conn.commit()
    conn.close()
    visibility = "public" if is_public else "private"
    return [TextContent(type="text", text=f"Photo {photo_id} is now {visibility} on Flickr.")]


async def _set_location(args):
    photo_id = args["id"]
    _api_post("flickr.photos.geo.setLocation", {
        "photo_id": photo_id,
        "lat":      str(args["lat"]),
        "lon":      str(args["lon"]),
        "accuracy": str(args.get("accuracy", 16)),
    })
    return [TextContent(type="text", text=f"Photo {photo_id} location set to ({args['lat']}, {args['lon']}).")]


async def _sync(args):
    if _sync_lock.locked():
        return [TextContent(type="text", text="Sync already in progress.")]
    scripts_dir = os.path.dirname(SYNC_SCRIPT)
    sync_type = args.get("type", "photos")
    script_map = {
        "photos":   SYNC_SCRIPT,
        "groups":   os.path.join(scripts_dir, "sync_groups.py"),
        "contacts": os.path.join(scripts_dir, "sync_contacts.py"),
        "albums":   os.path.join(scripts_dir, "sync_albums.py"),
    }
    if sync_type == "all":
        targets = list(script_map.items())
    elif sync_type in script_map:
        targets = [(sync_type, script_map[sync_type])]
    else:
        return [TextContent(type="text", text=f"Unknown sync type '{sync_type}'. Use: photos, groups, contacts, albums, all.")]
    results = []
    async with _sync_lock:
        for label, path in targets:
            cmd = [sys.executable, path]
            if label == "photos" and args.get("full"):
                cmd.append("--full")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            status = "completed" if proc.returncode == 0 else "failed"
            results.append(f"{label}: {status}\n{stdout.decode().strip()}")
    return [TextContent(type="text", text="\n\n".join(results))]


async def _get_exif(args):
    data = _api_get("flickr.photos.getExif", {"photo_id": args["photo_id"]})
    exif = data.get("photo", {}).get("exif", [])
    result = [{
        "tag":   e["tag"],
        "label": e.get("label", e["tag"]),
        "value": e.get("clean", {}).get("_content", e.get("raw", {}).get("_content", "")),
    } for e in exif]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _get_upload_status():
    data = _api_get("flickr.people.getUploadStatus")
    user = data.get("user", {})
    return [TextContent(type="text", text=json.dumps({
        "username":  user.get("username", {}).get("_content", ""),
        "bandwidth": user.get("bandwidth", {}),
        "filesize":  user.get("filesize", {}),
        "sets":      user.get("sets", {}),
        "videos":    user.get("videos", {}),
        "pro":       user.get("ispro", 0),
    }, indent=2))]


async def _get_person_info(args):
    data = _api_get("flickr.people.getInfo", {"user_id": args["user_id"]})
    p = data.get("person", {})
    return [TextContent(type="text", text=json.dumps({
        "nsid":        p.get("nsid", ""),
        "username":    p.get("username", {}).get("_content", ""),
        "realname":    p.get("realname", {}).get("_content", ""),
        "location":    p.get("location", {}).get("_content", ""),
        "description": p.get("description", {}).get("_content", ""),
        "photos":      p.get("photos", {}).get("count", {}).get("_content", 0),
        "profile_url": f"https://www.flickr.com/people/{p.get('nsid', '')}/",
        "ispro":       p.get("ispro", 0),
    }, indent=2))]


async def _get_photostream_stats(args):
    from datetime import date as date_type, timedelta
    query_date = args.get("date", (date_type.today() - timedelta(days=1)).isoformat())
    data = _api_get("flickr.stats.getTotalViews", {"date": query_date})
    stats = data.get("stats", {})
    return [TextContent(type="text", text=json.dumps({
        "date":        query_date,
        "total":       stats.get("total", {}).get("views", 0),
        "photos":      stats.get("photos", {}).get("views", 0),
        "photostream": stats.get("photostream", {}).get("views", 0),
        "sets":        stats.get("sets", {}).get("views", 0),
        "collections": stats.get("collections", {}).get("views", 0),
        "galleries":   stats.get("galleries", {}).get("views", 0),
    }, indent=2))]


async def _get_popular_photos(args):
    creds = _load_credentials()
    sort = args.get("sort", "favorites")
    if sort not in ("favorites", "comments", "views"):
        sort = "favorites"
    limit = int(args.get("limit", 20))
    data = _api_get("flickr.photos.getPopular", {
        "user_id":  creds["user_nsid"],
        "sort":     sort,
        "per_page": str(limit),
        "extras":   "views,date_taken",
    })
    photos = data.get("photos", {}).get("photo", [])
    return [TextContent(type="text", text=json.dumps([{
        "id":    p["id"],
        "title": p.get("title", ""),
        "views": p.get("views", 0),
        "url":   f"https://www.flickr.com/photos/{p.get('owner', '')}/{p['id']}/",
    } for p in photos], indent=2))]


async def _get_gallery_photos(args):
    gallery_id = args["gallery_id"]
    limit = int(args.get("limit", 50))
    page = int(args.get("page", 1))
    data = _api_get("flickr.galleries.getPhotos", {
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


async def _get_group_photos(args):
    group_id = args["group_id"]
    limit = int(args.get("limit", 50))
    page = int(args.get("page", 1))
    data = _api_get("flickr.groups.pools.getPhotos", {
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


async def _get_faves(args):
    creds = _load_credentials()
    limit = int(args.get("limit", 20))
    page = int(args.get("page", 1))
    data = _api_get("flickr.favorites.getList", {
        "user_id":  creds["user_nsid"],
        "per_page": str(limit),
        "page":     str(page),
        "extras":   "date_faved,date_taken",
    })
    container = data.get("photos", {})
    photos = container.get("photo", [])
    return [TextContent(type="text", text=json.dumps({
        "total": container.get("total", 0),
        "page":  page,
        "photos": [{"id": p["id"], "title": p.get("title", ""), "owner": p.get("owner", ""),
                    "date_faved": p.get("date_faved", ""),
                    "url": f"https://www.flickr.com/photos/{p.get('owner', '')}/{p['id']}/"}
                   for p in photos],
    }, indent=2))]


async def _get_recent_activity(args):
    timeframe = args.get("timeframe", "day")
    if timeframe not in ("day", "week"):
        timeframe = "day"
    limit = int(args.get("limit", 20))
    data = _api_get("flickr.activity.userPhotos", {"timeframe": timeframe, "per_page": str(limit)})
    items = data.get("items", {}).get("item", [])
    results = []
    for item in items:
        activity = item.get("activity", {}).get("event", [])
        if isinstance(activity, dict):
            activity = [activity]
        title = item.get("title", {})
        title_text = title.get("_content", "") if isinstance(title, dict) else title
        for event in activity:
            ts = event.get("dateadded", 0)
            results.append({
                "photo_id":    item.get("id", ""),
                "photo_title": title_text,
                "type":        event.get("type", ""),
                "username":    event.get("username", ""),
                "date":        datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M") if ts else "",
                "value":       event.get("_content", ""),
            })
    return [TextContent(type="text", text=json.dumps(results[:limit], indent=2))]


async def _remove_fave(args):
    _api_post("flickr.favorites.remove", {"photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} removed from favorites.")]


async def _remove_location(args):
    _api_post("flickr.photos.geo.removeLocation", {"photo_id": args["id"]})
    return [TextContent(type="text", text=f"Location removed from photo {args['id']}.")]


async def _join_group(args):
    _api_post("flickr.groups.join", {"group_id": args["group_id"]})
    return [TextContent(type="text", text=f"Joined group {args['group_id']}.")]


async def _leave_group(args):
    _api_post("flickr.groups.leave", {"group_id": args["group_id"]})
    return [TextContent(type="text", text=f"Left group {args['group_id']}.")]


async def _set_safety_level(args):
    level_map = {"safe": "1", "moderate": "2", "restricted": "3"}
    level = level_map.get(args["safety_level"], "1")
    _api_post("flickr.photos.setSafetyLevel", {"photo_id": args["id"], "safety_level": level})
    return [TextContent(type="text", text=f"Photo {args['id']} safety level set to {args['safety_level']}.")]


async def _set_content_type(args):
    type_map = {"photo": "1", "screenshot": "2", "other": "3"}
    ct = type_map.get(args["content_type"], "1")
    _api_post("flickr.photos.setContentType", {"photo_id": args["id"], "content_type": ct})
    return [TextContent(type="text", text=f"Photo {args['id']} content type set to {args['content_type']}.")]


async def _set_dates(args):
    granularity_map = {"exact": "0", "month": "4", "year": "6"}
    granularity = granularity_map.get(args.get("granularity", "exact"), "0")
    _api_post("flickr.photos.setDates", {
        "photo_id":               args["id"],
        "date_taken":             args["date_taken"],
        "date_taken_granularity": granularity,
    })
    conn = db()
    conn.execute("UPDATE photos SET date_taken=? WHERE id=?", (args["date_taken"], args["id"]))
    conn.commit()
    conn.close()
    return [TextContent(type="text", text=f"Date taken for photo {args['id']} set to {args['date_taken']}.")]


async def _create_gallery(args):
    params = {"title": args["title"], "description": args["description"]}
    if "primary_photo_id" in args:
        params["primary_photo_id"] = args["primary_photo_id"]
    data = _api_post("flickr.galleries.create", params)
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
    _api_post("flickr.galleries.addPhoto", params)
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to gallery {args['gallery_id']}.")]


async def _get_galleries(args):
    creds = _load_credentials()
    limit = int(args.get("limit", 20))
    data = _api_get("flickr.galleries.getList", {"user_id": creds["user_nsid"], "per_page": str(limit)})
    galleries = data.get("galleries", {}).get("gallery", [])
    return [TextContent(type="text", text=json.dumps([{
        "id":           g.get("id", ""),
        "title":        g.get("title", {}).get("_content", "") if isinstance(g.get("title"), dict) else g.get("title", ""),
        "description":  g.get("description", {}).get("_content", "") if isinstance(g.get("description"), dict) else g.get("description", ""),
        "count_photos": g.get("count_photos", 0),
        "url":          g.get("url", ""),
    } for g in galleries], indent=2))]


async def _get_contact_uploads(args):
    limit = int(args.get("limit", 20))
    just_friends = "1" if args.get("just_friends") else "0"
    data = _api_get("flickr.photos.getContactsPhotos", {
        "count": str(limit), "just_friends": just_friends, "extras": "date_upload,owner_name",
    })
    photos = data.get("photos", {}).get("photo", [])
    return [TextContent(type="text", text=json.dumps([{
        "id":          p["id"],
        "title":       p.get("title", ""),
        "owner":       p.get("owner", ""),
        "owner_name":  p.get("ownername", ""),
        "date_upload": p.get("dateupload", ""),
        "url":         f"https://www.flickr.com/photos/{p.get('owner', '')}/{p['id']}/",
    } for p in photos], indent=2))]


async def _search_all_groups(args):
    query = args["query"]
    limit = int(args.get("limit", 20))
    data = _api_get("flickr.groups.search", {"text": query, "per_page": str(limit)})
    groups = data.get("groups", {}).get("group", [])
    return [TextContent(type="text", text=json.dumps([{
        "nsid":       g.get("nsid", ""),
        "name":       g.get("name", ""),
        "members":    g.get("members", 0),
        "pool_count": g.get("pool_count", 0),
        "url":        f"https://www.flickr.com/groups/{g.get('nsid', '')}/",
    } for g in groups], indent=2))]


# --- Sync helpers ---

async def _run_sync_script(path: str, label: str, extra_args: list[str] | None = None) -> int:
    logging.info("Sync starting: %s", label)
    p = await asyncio.create_subprocess_exec(
        sys.executable, path, *(extra_args or []),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await p.communicate()
    for line in stdout.decode().splitlines():
        if line.strip():
            logging.info("[%s] %s", label, line)
    if p.returncode != 0:
        logging.error("Sync failed: %s (exit %s)", label, p.returncode)
    else:
        logging.info("Sync completed: %s", label)
    return p.returncode


async def _background_refresh():
    """Check daily whether photo/contact/group data needs refreshing and sync if so."""
    while True:
        try:
            if os.path.exists(DB_FILE):
                conn = sqlite3.connect(DB_FILE)
                row = conn.execute("SELECT MAX(synced_at) FROM sync_log WHERE type = 'photos'").fetchone()
                conn.close()
                last_sync = row[0] if row and row[0] else 0
                age = time.time() - last_sync
                if age >= REFRESH_INTERVAL:
                    logging.info("Background refresh triggered (last photos sync %.1fh ago)", age / 3600)
                    async with _sync_lock:
                        await _run_sync_script(SYNC_SCRIPT, "photos")
                        scripts_dir = os.path.dirname(SYNC_SCRIPT)
                        await asyncio.gather(
                            _run_sync_script(os.path.join(scripts_dir, "sync_contacts.py"), "contacts"),
                            _run_sync_script(os.path.join(scripts_dir, "sync_groups.py"),   "groups"),
                        )
                        await _run_sync_script(os.path.join(scripts_dir, "sync_engagement.py"), "engagement")
                    sleep_for = REFRESH_INTERVAL
                else:
                    sleep_for = REFRESH_INTERVAL - age
            else:
                sleep_for = REFRESH_INTERVAL
        except Exception:
            logging.exception("Background refresh error")
            sleep_for = REFRESH_INTERVAL
        await asyncio.sleep(sleep_for)
