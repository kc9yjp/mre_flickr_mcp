"""Photo tool definitions and handlers."""

import base64
import contextvars
import io
import json
import logging
import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from mcp.types import ImageContent, TextContent, Tool
from PIL import Image

import flickr_api
from db import get_db, with_db_retry

TOOLS = [
    Tool(
        name="search_photos",
        description=(
            "Search and filter the photo collection. Supports keyword search on title, "
            "tag filtering, date range, and sorting by date or popularity (views). "
            "Set include_images=true to get each match's thumbnail back in the same call "
            "— use it for metadata sweeps (e.g. incomplete=true) instead of one "
            "fetch_photo_image call per result. "
            "url_original/url_medium are cached from the last sync and can go stale "
            "(403/410) for an external fetcher after a visibility change — call "
            "get_photo for a specific photo to get a freshly-resolved direct URL "
            "before handing it to anything other than a browser."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query":     {"type": "string", "description": "Title keyword"},
                "tags":      {"type": "string", "description": "Tag (partial match)"},
                "date_from": {"type": "string", "description": "Earliest date taken, YYYY-MM-DD"},
                "date_to":   {"type": "string", "description": "Latest date taken, YYYY-MM-DD"},
                "sort_by":   {"type": "string", "enum": ["date_taken", "views", "favorites", "date_uploaded", "random"], "default": "date_taken"},
                "order":     {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "limit":     {"type": "integer", "description": "Max results (default 50, max 200)"},
                "incomplete":   {"type": "boolean", "description": "Only return photos missing a title, description, or tags"},
                "min_comments": {"type": "integer", "description": "Only return photos with at least this many comments"},
                "is_public":    {"type": "boolean", "description": "Filter by visibility: true=public only, false=private only, omit=all"},
                "include_images": {
                    "type": "boolean",
                    "description": (
                        "Also return a thumbnail of each matching photo in this same call, so a "
                        "metadata sweep needs no follow-up fetch_photo_image per photo. "
                        "Images are capped by image_limit."
                    ),
                },
                "image_limit": {
                    "type": "integer",
                    "description": "How many thumbnails to attach when include_images is set (default 10, max 25)",
                },
            },
        },
    ),
    Tool(
        name="get_photo",
        description=(
            "Return full metadata for a single photo by its Flickr ID. Works for the "
            "caller's own photos (from the local synced library) as well as other "
            "users' public photos (e.g. from a group pool or contact) via a live "
            "Flickr API lookup — a \"not found\" result means the photo ID is invalid "
            "or was deleted, not that it's restricted for content reasons. "
            "url_original/url_medium are always freshly resolved from Flickr (not "
            "read from the sync-time cache), so they're safe to hand to an external "
            "service even if a previously-fetched URL for the same photo went stale."
        ),
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
        description=(
            "Update a photo's title, description, and tags on Flickr and in the local "
            "database. Every field is required on every call — id, title, description, "
            "and tags must all be included, even if a field's value is unchanged from "
            "before. This tool fully supports description — never claim it doesn't, and "
            "never omit it. If you proposed a title, description, and tags and the user "
            "approved them, include all three in this same call — do not drop one."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id":          {"type": "string", "description": "Flickr photo ID"},
                "title":       {"type": "string", "description": "New title (required — pass the unchanged value if not updating it)"},
                "description": {"type": "string", "description": "New description (required — pass the unchanged value if not updating it)"},
                "tags":        {"type": "string", "description": "Space-separated tags, replaces existing tags (required — pass the unchanged value if not updating it)"},
            },
            "required": ["id", "title", "description", "tags"],
        },
    ),
    Tool(
        name="batch_update_photos",
        description=(
            "Update the title, description, and tags of many photos in one call — the "
            "batch form of update_photo, for applying a whole reviewed set at once "
            "instead of one call per photo. Pass an array of objects, each with an id "
            "plus whichever of title/description/tags should change; for any photo where "
            "you supply only one of title or description, the other is carried over from "
            "Flickr's current value. Photos are written one at a time and independently: "
            "the result reports per-photo status, and a failure on one photo does not "
            "stop the rest unless stop_on_error is set. Max 50 photos per call."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "photos": {
                    "type": "array",
                    "description": "Photos to update (max 50)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":          {"type": "string", "description": "Flickr photo ID"},
                            "title":       {"type": "string", "description": "New title"},
                            "description": {"type": "string", "description": "New description"},
                            "tags":        {"type": "string", "description": "Space-separated tags, replaces existing tags"},
                        },
                        "required": ["id"],
                    },
                },
                "stop_on_error": {
                    "type": "boolean",
                    "description": "Abort the remaining photos after the first failure (default false)",
                },
            },
            "required": ["photos"],
        },
    ),
    Tool(
        name="replace_photo",
        description=(
            "Replace the image file behind an existing Flickr photo, keeping its ID, "
            "title, description, tags, comments, faves, stats, and group/album memberships. "
            "Use this to push back an edited, upscaled, or otherwise enhanced version of a "
            "photo rather than uploading it as a new one. Supply exactly one of image_url "
            "(an http(s) URL to download, e.g. a render returned by an image service) or "
            "file_path (a file on the server). Note: a replace rotates the photo's URL "
            "secret, so any direct image URL handed out earlier for this photo stops "
            "working; the local cache's URLs are refreshed automatically. Replacing counts "
            "against a free account's monthly upload allowance."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id":  {"type": "string", "description": "Flickr photo ID to replace the image of"},
                "image_url": {"type": "string", "description": "http(s) URL of the replacement image to download and upload"},
                "file_path": {"type": "string", "description": "Path to a replacement image file on the server"},
            },
            "required": ["photo_id"],
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
        name="find_weak_photos",
        description=(
            "Rank photos by a weakness score combining low views-per-day, "
            "zero favorites, and zero comments. By default returns public photos only; "
            "set include_private=true to include private photos as well."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit":                  {"type": "integer", "description": "Max results (default 20, max 100)"},
                "min_age_days":           {"type": "integer", "description": "Min days since upload (default 30)"},
                "require_zero_favorites": {"type": "boolean", "description": "Only include photos with 0 favorites"},
                "review_cooldown_days":   {"type": "integer", "description": "Skip photos reviewed within this many days (default 60)"},
                "include_private":        {"type": "boolean", "description": "Include private photos (default false)"},
                "sort":                   {"type": "string", "enum": ["weakness_score", "random"], "description": "Sort order: weakness_score (default) or random"},
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
        description=(
            "Fetch public profile info for a Flickr user by NSID, username, or a "
            "flickr.com/photos/<x>/ or /people/<x>/ URL. Returns an `is_you` flag "
            "comparing the resolved NSID against the logged-in account. Use this "
            "whenever a username or URL is ambiguous or looks unfamiliar — Flickr "
            "usernames/path aliases can be renamed at any time, so an old link or "
            "profile mention may show a different username than the account uses "
            "today even though the NSID (and the account) hasn't changed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Flickr NSID, username, or profile/photo URL"},
            },
            "required": ["user_id"],
        },
    ),
    Tool(
        name="get_user_photos",
        description=(
            "List a Flickr user's public photostream, fetched live from the API — "
            "works for any user, not just the caller's own library. Most recent "
            "uploads first. Use this to show what someone else has been posting, "
            "e.g. before deciding whether to follow them."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Flickr NSID, username, or profile/photo URL"},
                "limit":   {"type": "integer", "description": "Max results (default 30, max 100)"},
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
        name="get_photo_faves",
        description="List users who have favorited a photo, with a flag indicating whether you follow each one.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "limit":    {"type": "integer", "description": "Max results (default 50)"},
            },
            "required": ["photo_id"],
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
        name="get_photos_with_comments",
        description="Return photos that have at least one comment, sorted by most recently uploaded. Returns only id, title, url_photopage, and comment count — use get_photo_comments to fetch the full thread for each.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 50, max 200)"},
            },
        },
    ),
    Tool(
        name="get_recent_activity",
        description="Show recent comments and faves on the user's photos.",
        inputSchema={
            "type": "object",
            "properties": {
                "timeframe": {"type": "string", "description": "Time window in Flickr format: hours ('4h', '12h') or days ('1d', '7d'). Default: '1d'. Max: '7d'."},
            },
        },
    ),
    Tool(
        name="get_unreplied_comments",
        description=(
            "Check recent activity for comments on the user's photos and return only "
            "those photos that have at least one comment without a reply from the "
            "user yet, along with the unreplied comment(s). Uses "
            "flickr.activity.userPhotos, so it only sees activity within the given "
            "timeframe — it will miss older unreplied comments."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "timeframe": {"type": "string", "description": "Time window in Flickr format: hours ('4h', '12h') or days ('1d', '7d'). Default: '7d'. Max: '7d'."},
            },
        },
    ),
    Tool(
        name="add_to_keeper_list",
        description="Add a photo to the keeper list — a local list of public photos worth preserving even if weak on stats.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "note":     {"type": "string", "description": "Optional reason or note"},
            },
            "required": ["photo_id"],
        },
    ),
    Tool(
        name="get_keeper_list",
        description="List all photos in the keeper list, with title, URL, and note.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="remove_from_keeper_list",
        description="Remove a photo from the keeper list.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
            },
            "required": ["photo_id"],
        },
    ),
]


async def _search_photos(args):
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
    if args.get("min_comments") is not None:
        conditions.append("comments >= ?")
        params.append(int(args["min_comments"]))
    if "is_public" in args:
        conditions.append("is_public = ?")
        params.append(1 if args["is_public"] else 0)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sort_by = args.get("sort_by", "date_taken")
    if sort_by == "random":
        order_clause = "RANDOM()"
    else:
        if sort_by not in ("date_taken", "views", "favorites", "date_uploaded"):
            sort_by = "date_taken"
        order = "ASC" if args.get("order", "desc") == "asc" else "DESC"
        order_clause = f"{sort_by} {order}"
    limit = min(int(args.get("limit", 50)), 200)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM photos {where} ORDER BY {order_clause} LIMIT ?",
            params + [limit],
        ).fetchall()
    records = [dict(r) for r in rows]
    result = [TextContent(type="text", text=json.dumps(records, indent=2))]
    if not args.get("include_images") or not records:
        return result

    # Metadata sweeps (search incomplete=true, then look at each photo) used to
    # cost one fetch_photo_image call per result. Attaching the thumbnails here
    # collapses that into this one call; the cap keeps the payload bounded.
    image_limit = max(1, min(int(args.get("image_limit", 10)), MAX_SEARCH_IMAGES))
    wanted = records[:image_limit]
    thumbs = _photo_thumbnails([str(r["id"]) for r in wanted])
    for position, record in enumerate(wanted, start=1):
        photo_id = str(record["id"])
        # Numbered so the photo each image belongs to stays unambiguous even
        # for a client that flattens the text blocks away from the images.
        header = (
            f"Image {position} of {len(wanted)} — Photo ID: {photo_id} — "
            f"{record.get('title') or '(untitled)'}"
        )
        image = thumbs.get(photo_id)
        if image is None:
            result.append(TextContent(type="text", text=f"{header}\n(image unavailable)"))
            continue
        result.append(TextContent(type="text", text=header))
        result.append(image)
    if len(records) > image_limit:
        result.append(TextContent(
            type="text",
            text=(f"Showed images for the first {image_limit} of {len(records)} matches. "
                  f"Raise image_limit (max {MAX_SEARCH_IMAGES}) or narrow the search for the rest."),
        ))
    return result


def _live_photo_urls(photo_id: str) -> tuple[str | None, str | None]:
    """Fetch fresh direct-image URLs from Flickr, bypassing the local cache.

    Flickr's photo source URLs embed a secret that can rotate (e.g. on a
    public/private visibility change), so a url_original/url_medium value
    cached at sync time can 403/410 for an external fetcher even though the
    photo still loads fine in a browser. Callers that hand a direct image
    URL to something other than the browser (an external API, a bot) should
    always resolve it live rather than trust the synced DB columns. Mirrors
    the pattern in webapi.py's _build_photo_detail.
    """
    try:
        sizes_data = flickr_api._api_get("flickr.photos.getSizes", {"photo_id": photo_id})
    except Exception as e:
        logging.warning("get_photo: failed to refresh live URLs for %s: %s", photo_id, e)
        return None, None
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
    return url_original, url_medium


async def _get_photo(args):
    photo_id = args["id"]
    with get_db() as conn:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row:
        result = dict(row)
        url_original, url_medium = _live_photo_urls(photo_id)
        if url_original:
            result["url_original"] = url_original
        if url_medium:
            result["url_medium"] = url_medium
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Not one of the caller's own synced photos (e.g. someone else's photo in
    # a group pool or from a contact) — the local DB only ever holds the
    # caller's own library, so fall back to a live lookup instead of
    # reporting "not found" for every photo that isn't theirs.
    try:
        info = flickr_api._api_get("flickr.photos.getInfo", {"photo_id": photo_id})
    except flickr_api.FlickrAPIError as e:
        return [TextContent(type="text", text=f"Photo {photo_id} not found ({e.flickr_message}).")]
    photo = info["photo"]
    owner = photo.get("owner", {})
    owner_nsid = owner.get("nsid", "")
    try:
        faves_data = flickr_api._api_get(
            "flickr.photos.getFavorites", {"photo_id": photo_id, "per_page": "1"}
        )
        favorites = int(faves_data.get("photo", {}).get("total", 0) or 0)
    except Exception:
        favorites = None
    result = {
        "id":            photo_id,
        "title":         photo.get("title", {}).get("_content", ""),
        "description":   photo.get("description", {}).get("_content", ""),
        "tags":          " ".join(t["raw"] for t in photo.get("tags", {}).get("tag", [])),
        "views":         int(photo.get("views", 0) or 0),
        "favorites":     favorites,
        "comments":      int(photo.get("comments", {}).get("_content", 0) or 0),
        "date_taken":    photo.get("dates", {}).get("taken", ""),
        "date_uploaded": int(photo.get("dates", {}).get("posted", 0) or 0),
        "url_photopage": f"https://www.flickr.com/photos/{owner_nsid}/{photo_id}/",
        "is_public":     1 if photo.get("visibility", {}).get("ispublic", 1) else 0,
        "owner": {
            "nsid":     owner_nsid,
            "username": owner.get("username", ""),
            "realname": owner.get("realname", ""),
        },
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _get_summary():
    with get_db() as conn:
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
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_log ORDER BY synced_at DESC LIMIT ?",
            (int(args.get("limit", 5)),),
        ).fetchall()
    syncs = [{
        "id": r["id"],
        "synced_at": datetime.fromtimestamp(r["synced_at"]).isoformat() if r["synced_at"] else None,
        "mode": r["mode"],
        "photos_fetched": r["photos_fetched"],
    } for r in rows]
    return [TextContent(type="text", text=json.dumps(syncs, indent=2))]


# Cap on one batch_update_photos call. Each entry is 1-2 Flickr writes, so a
# larger batch mostly means a longer window in which a partial failure is
# harder to reason about.
MAX_BATCH_UPDATE = 50


def _apply_photo_update(args: dict) -> list[str]:
    """Push one photo's title/description/tags to Flickr and the local cache.

    Returns the list of field groups actually written ("title/description",
    "tags").  Raises whatever the Flickr call raises -- callers decide whether
    one failure aborts a batch.
    """
    photo_id = args["id"]
    updated = []

    if "title" in args or "description" in args:
        # setMeta replaces BOTH title and description, so any field not supplied
        # must be carried over from the photo's current values. Read those live
        # from the Flickr API — never from the local cache, which may be stale.
        if "title" not in args or "description" not in args:
            info = flickr_api._api_get("flickr.photos.getInfo", {"photo_id": photo_id})
            photo = info.get("photo", {})
            current_title = photo.get("title", {}).get("_content", "")
            current_description = photo.get("description", {}).get("_content", "")
        else:
            current_title = current_description = ""
        title = args.get("title", current_title)
        description = args.get("description", current_description)
        flickr_api._api_post("flickr.photos.setMeta", {"photo_id": photo_id, "title": title, "description": description})
        updated.append("title/description")
    if "tags" in args:
        flickr_api._api_post("flickr.photos.setTags", {"photo_id": photo_id, "tags": args["tags"]})
        updated.append("tags")

    # Keep the local cache in step with what was just written to Flickr. The
    # Flickr writes above already succeeded, so a lock held by a concurrent
    # sync must not fail the whole call — retry the cache write instead of
    # surfacing an OperationalError the caller can only answer by retrying
    # the API writes too. Idempotent: re-running it writes the same values.
    def _write_cache(conn):
        if "title" in args:
            conn.execute("UPDATE photos SET title=? WHERE id=?", (args["title"], photo_id))
        if "description" in args:
            conn.execute("UPDATE photos SET description=? WHERE id=?", (args["description"], photo_id))
        if "tags" in args:
            conn.execute("UPDATE photos SET tags=? WHERE id=?", (args["tags"], photo_id))

    try:
        with_db_retry(_write_cache, label=f"update_photo cache write (photo {photo_id})")
    except Exception as e:
        # Flickr is the source of truth and it's already updated; a stale local
        # row is a cache miss the next sync fixes, not a failed update.
        logging.error(
            "update_photo: Flickr updated but local cache write failed for photo %s: %s",
            photo_id, e,
        )
        updated.append("local cache NOT updated (next sync will fix)")
    return updated


async def _update_photo(args):
    photo_id = args["id"]
    updated = _apply_photo_update(args)
    return [TextContent(type="text", text=f"Updated {', '.join(updated)} for photo {photo_id}.")]


async def _batch_update_photos(args):
    """Apply title/description/tags updates to many photos in one tool call.

    Each photo is written independently: one failure is reported against that
    photo and the rest of the batch still runs (unless stop_on_error is set),
    so a partly-applied batch is always legible from the result.
    """
    photos = args.get("photos") or []
    if not isinstance(photos, list) or not photos:
        return [TextContent(type="text", text="batch_update_photos: 'photos' must be a non-empty array.")]
    if len(photos) > MAX_BATCH_UPDATE:
        return [TextContent(
            type="text",
            text=f"batch_update_photos: too many photos ({len(photos)}); max {MAX_BATCH_UPDATE} per call.",
        )]
    stop_on_error = bool(args.get("stop_on_error", False))

    results, succeeded, failed = [], 0, 0
    for entry in photos:
        if not isinstance(entry, dict) or not entry.get("id"):
            results.append({"id": None, "status": "error", "error": "entry is missing 'id'"})
            failed += 1
            if stop_on_error:
                break
            continue
        photo_id = str(entry["id"])
        fields = {k: v for k, v in entry.items() if k in ("id", "title", "description", "tags")}
        fields["id"] = photo_id
        if len(fields) == 1:
            results.append({"id": photo_id, "status": "skipped",
                            "error": "no title, description, or tags supplied"})
            failed += 1
            if stop_on_error:
                break
            continue
        try:
            updated = _apply_photo_update(fields)
            results.append({"id": photo_id, "status": "ok", "updated": updated})
            succeeded += 1
        except Exception as e:
            message = getattr(e, "flickr_message", None) or str(e)
            logging.warning("batch_update_photos: photo %s failed: %s", photo_id, e)
            results.append({"id": photo_id, "status": "error", "error": message})
            failed += 1
            if stop_on_error:
                break

    payload = {
        "requested": len(photos),
        "succeeded": succeeded,
        "failed":    failed,
        "stopped_early": stop_on_error and failed > 0 and len(results) < len(photos),
        "results":   results,
    }
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


# Claude's vision pipeline downscales anything larger than this anyway (long edge,
# per Anthropic's vision guidance) -- resizing client-side avoids shipping multi-MB
# originals for no visual benefit.
MAX_IMAGE_DIMENSION = 1568


def _downsample_image(content: bytes, mime: str, max_dim: int = MAX_IMAGE_DIMENSION) -> tuple[bytes, str]:
    """Resize image bytes to fit within *max_dim*, re-encoding as JPEG.

    Returns the (possibly unchanged) bytes and mime type. Falls back to the
    original bytes/mime on any decode error rather than failing the tool call.
    """
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception as e:
        logging.warning("fetch_photo_image: failed to decode image for downsampling (%s): %s", mime, e)
        return content, mime
    if max(img.size) <= max_dim:
        return content, mime
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


async def _fetch_photo_image(args):
    photo_id = args["id"]
    with get_db() as conn:
        row = conn.execute("SELECT url_original, url_photopage FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row:
        photopage = row["url_photopage"]
    else:
        info = flickr_api._api_get("flickr.photos.getInfo", {"photo_id": photo_id})
        photo = info["photo"]
        owner = photo["owner"]["nsid"]
        photopage = f"https://www.flickr.com/photos/{owner}/{photo_id}/"
    sizes_data = flickr_api._api_get("flickr.photos.getSizes", {"photo_id": photo_id})
    sizes = sizes_data["sizes"]["size"]
    preferred = ("Large 2048", "Large 1600", "Large")
    url = next(
        (s["source"] for label in preferred for s in sizes if s["label"] == label),
        sizes[-1]["source"],
    )
    if not url:
        return [TextContent(type="text", text="No image URL available for this photo.")]
    resp = requests.get(url, timeout=flickr_api.HTTP_TIMEOUT)
    resp.raise_for_status()
    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    content, mime = _downsample_image(resp.content, mime)
    data = base64.standard_b64encode(content).decode()
    return [
        TextContent(type="text", text=f"Photo ID: {photo_id}\n{photopage}"),
        ImageContent(type="image", data=data, mimeType=mime),
    ]


# Thumbnails bundled into a search result are for triage ("does this photo
# still need a title?"), not close inspection -- a smaller long edge keeps a
# 10-photo sweep to a sane payload. Callers wanting detail use fetch_photo_image.
THUMBNAIL_DIMENSION = 640

# Ceiling on how many thumbnails one search_photos call will attach.
MAX_SEARCH_IMAGES = 25

# Thumbnail fetches are two network round-trips each (getSizes + download),
# so they run in a small pool rather than serially.
_THUMBNAIL_WORKERS = 4

# Ceiling on a replacement image fetched from a URL, so a bad link can't pull
# an unbounded download into memory.
MAX_REPLACE_BYTES = 64 * 1024 * 1024


def _photo_thumbnail(photo_id: str, max_dim: int = THUMBNAIL_DIMENSION) -> ImageContent | None:
    """Return *photo_id*'s image as a downscaled ImageContent, or None on failure.

    Never raises: a photo whose sizes or bytes can't be fetched is reported by
    the caller as a text line rather than failing the whole batch.
    """
    try:
        sizes_data = flickr_api._api_get("flickr.photos.getSizes", {"photo_id": photo_id})
        sizes = sizes_data.get("sizes", {}).get("size", [])
        if not sizes:
            logging.warning("thumbnail: no sizes returned for photo %s", photo_id)
            return None
        preferred = ("Medium 800", "Medium 640", "Medium", "Small 320", "Large")
        url = next(
            (s["source"] for label in preferred for s in sizes if s["label"] == label),
            sizes[-1]["source"],
        )
        resp = requests.get(url, timeout=flickr_api.HTTP_TIMEOUT)
        resp.raise_for_status()
        mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        content, mime = _downsample_image(resp.content, mime, max_dim=max_dim)
        return ImageContent(
            type="image",
            data=base64.standard_b64encode(content).decode(),
            mimeType=mime,
        )
    except Exception as e:
        logging.warning("thumbnail: failed to fetch image for photo %s: %s", photo_id, e)
        return None


def _photo_thumbnails(photo_ids: list[str], max_dim: int = THUMBNAIL_DIMENSION) -> dict[str, ImageContent | None]:
    """Fetch thumbnails for several photos concurrently, keyed by photo id.

    Each worker runs under a copy of the calling context so ``_current_user``
    (and therefore per-user credentials and DB path) still resolves inside the
    thread pool.
    """
    if not photo_ids:
        return {}
    results: dict[str, ImageContent | None] = {}
    workers = min(len(photo_ids), _THUMBNAIL_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for pid in photo_ids:
            ctx = contextvars.copy_context()
            futures[pool.submit(ctx.run, _photo_thumbnail, pid, max_dim)] = pid
        for future in futures:
            pid = futures[future]
            try:
                results[pid] = future.result()
            except Exception as e:
                logging.warning("thumbnail: worker failed for photo %s: %s", pid, e)
                results[pid] = None
    return results



def _load_replacement_bytes(args: dict) -> tuple[bytes, str]:
    """Resolve replace_photo's image source to (bytes, filename).

    Accepts an ``image_url`` (the usual case — an enhanced/upscaled render
    handed back by another service) or a local ``file_path``.  Raises
    ``ValueError`` with a caller-facing message on bad input.
    """
    image_url = (args.get("image_url") or "").strip()
    file_path = (args.get("file_path") or "").strip()
    if bool(image_url) == bool(file_path):
        raise ValueError("Supply exactly one of image_url or file_path.")

    if file_path:
        if not os.path.isfile(file_path):
            raise ValueError(f"File not found: {file_path}")
        size = os.path.getsize(file_path)
        if size > MAX_REPLACE_BYTES:
            raise ValueError(f"File is {size} bytes; the limit is {MAX_REPLACE_BYTES}.")
        with open(file_path, "rb") as f:
            return f.read(), os.path.basename(file_path)

    if not image_url.lower().startswith(("http://", "https://")):
        raise ValueError("image_url must be an http(s) URL.")
    with requests.get(image_url, timeout=max(flickr_api.HTTP_TIMEOUT, 120), stream=True) as resp:
        resp.raise_for_status()
        declared = int(resp.headers.get("content-length") or 0)
        if declared > MAX_REPLACE_BYTES:
            raise ValueError(f"Image at image_url is {declared} bytes; the limit is {MAX_REPLACE_BYTES}.")
        # Streamed and counted rather than resp.content, so a server that lies
        # about (or omits) content-length still can't pull an unbounded body in.
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            total += len(chunk)
            if total > MAX_REPLACE_BYTES:
                raise ValueError(f"Image at image_url exceeds the {MAX_REPLACE_BYTES}-byte limit.")
            chunks.append(chunk)
    filename = os.path.basename(urllib.parse.urlparse(image_url).path) or "photo.jpg"
    return b"".join(chunks), filename


async def _replace_photo(args):
    photo_id = str(args["photo_id"])
    try:
        content, filename = _load_replacement_bytes(args)
    except ValueError as e:
        return [TextContent(type="text", text=f"replace_photo: {e}")]
    except requests.exceptions.RequestException as e:
        logging.warning("replace_photo: failed to fetch replacement image for %s: %s", photo_id, e)
        return [TextContent(type="text", text=f"replace_photo: could not download image_url ({e}).")]

    try:
        returned_id = flickr_api._api_replace(photo_id, content, filename)
    except flickr_api.FlickrAPIError as e:
        logging.warning("replace_photo: Flickr rejected replace for photo %s: %s", photo_id, e.flickr_message)
        return [TextContent(type="text", text=f"replace_photo: Flickr rejected the replace — {e.flickr_message}")]
    except RuntimeError as e:
        logging.error("replace_photo: replace failed for photo %s: %s", photo_id, e)
        return [TextContent(type="text", text=f"replace_photo: {e}")]

    # A replace rotates the URL secret, so the synced url_original/url_medium
    # now 404 for anything fetching them directly. Refresh both from Flickr;
    # the cache write is retried because a concurrent sync can hold the lock.
    url_original, url_medium = _live_photo_urls(photo_id)
    refreshed = False
    if url_original or url_medium:
        def _write_urls(conn):
            if url_original:
                conn.execute("UPDATE photos SET url_original=? WHERE id=?", (url_original, photo_id))
            if url_medium:
                conn.execute("UPDATE photos SET url_medium=? WHERE id=?", (url_medium, photo_id))
        try:
            with_db_retry(_write_urls, label=f"replace_photo url refresh (photo {photo_id})")
            refreshed = True
        except Exception as e:
            logging.error("replace_photo: could not refresh cached URLs for photo %s: %s", photo_id, e)

    return [TextContent(type="text", text=json.dumps({
        "photo_id":     returned_id,
        "status":       "replaced",
        "bytes_sent":   len(content),
        "source":       args.get("image_url") or args.get("file_path"),
        "url_original": url_original,
        "urls_refreshed_locally": refreshed,
        "note": (
            "Title, description, tags, comments, faves, and group/album "
            "memberships are unchanged — only the image was replaced. Any "
            "previously handed-out direct image URL for this photo is now stale."
        ),
    }, indent=2))]


async def _get_photo_comments(args):
    data = flickr_api._api_get("flickr.photos.comments.getList", {"photo_id": args["photo_id"]})
    comments = [{
        "author":      c["authorname"],
        "author_nsid": c["author"],
        "author_url":  f"https://www.flickr.com/photos/{c['author']}/",
        "date":        datetime.fromtimestamp(int(c["datecreate"])).strftime("%Y-%m-%d"),
        "comment":     c["_content"],
        "permalink":   c["permalink"],
    } for c in data.get("comments", {}).get("comment", [])]
    if not comments:
        return [TextContent(type="text", text="No comments found.")]
    return [TextContent(type="text", text=json.dumps(comments, indent=2))]


async def _add_comment(args):
    data = flickr_api._api_post("flickr.photos.comments.addComment", {
        "photo_id":     args["photo_id"],
        "comment_text": args["comment_text"],
    })
    comment_id = data.get("comment", {}).get("id", "unknown")
    return [TextContent(type="text", text=f"Comment posted (id: {comment_id}).")]


async def _delete_comment(args):
    flickr_api._api_post("flickr.photos.comments.deleteComment", {"comment_id": args["comment_id"]})
    return [TextContent(type="text", text=f"Comment {args['comment_id']} deleted.")]


async def _fave_photo(args):
    flickr_api._api_post("flickr.favorites.add", {"photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} added to favorites.")]


async def _remove_fave(args):
    flickr_api._api_post("flickr.favorites.remove", {"photo_id": args["photo_id"]})
    return [TextContent(type="text", text=f"Photo {args['photo_id']} removed from favorites.")]


async def _get_photo_stats(args):
    from datetime import date as date_type
    photo_id = args["photo_id"]
    query_date = args.get("date", date_type.today().isoformat())
    data = flickr_api._api_get("flickr.stats.getPhotoStats", {"photo_id": photo_id, "date": query_date})
    stats = data.get("stats", {})
    return [TextContent(type="text", text=json.dumps({
        "date":      query_date,
        "views":     stats.get("views", 0),
        "favorites": stats.get("favorites", 0),
        "comments":  stats.get("comments", 0),
    }, indent=2))]


async def _find_weak_photos(args):
    limit = min(int(args.get("limit", 20)), 100)
    min_age_days = int(args.get("min_age_days", 30))
    require_zero_faves = 1 if args.get("require_zero_favorites") else 0
    review_cooldown_days = int(args.get("review_cooldown_days", 60))
    include_private = 1 if args.get("include_private") else 0
    order_clause = "RANDOM()" if args.get("sort") == "random" else "weakness_score DESC"
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
              AND (reviewed_at IS NULL OR reviewed_at < (strftime('%s','now') - ? * 86400))
              AND (? OR is_public = 1)
              AND (? = 0 OR favorites = 0)
        )
        SELECT * FROM scored ORDER BY {order_clause} LIMIT ?
    """
    with get_db() as conn:
        rows = conn.execute(sql, (min_age_days, review_cooldown_days, include_private, require_zero_faves, limit)).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute(
                f"UPDATE photos SET reviewed_at = strftime('%s','now') WHERE id IN ({','.join('?'*len(ids))})",
                ids,
            )
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
    flickr_api._api_post("flickr.photos.setPerms", {
        "photo_id":     photo_id,
        "is_public":    str(is_public),
        "is_friend":    str(is_friend),
        "is_family":    str(is_family),
        "perm_comment": "3" if is_public else "0",
        "perm_addmeta": "2" if is_public else "0",
    })
    with get_db() as conn:
        conn.execute("UPDATE photos SET is_public = ? WHERE id = ?", (is_public, photo_id))
    visibility = "public" if is_public else "private"
    return [TextContent(type="text", text=f"Photo {photo_id} is now {visibility} on Flickr.")]


async def _set_location(args):
    photo_id = args["id"]
    flickr_api._api_post("flickr.photos.geo.setLocation", {
        "photo_id": photo_id,
        "lat":      str(args["lat"]),
        "lon":      str(args["lon"]),
        "accuracy": str(args.get("accuracy", 16)),
    })
    return [TextContent(type="text", text=f"Photo {photo_id} location set to ({args['lat']}, {args['lon']}).")]


async def _remove_location(args):
    flickr_api._api_post("flickr.photos.geo.removeLocation", {"photo_id": args["id"]})
    return [TextContent(type="text", text=f"Location removed from photo {args['id']}.")]


async def _set_safety_level(args):
    level_map = {"safe": "1", "moderate": "2", "restricted": "3"}
    level = level_map.get(args["safety_level"], "1")
    flickr_api._api_post("flickr.photos.setSafetyLevel", {"photo_id": args["id"], "safety_level": level})
    return [TextContent(type="text", text=f"Photo {args['id']} safety level set to {args['safety_level']}.")]


async def _set_content_type(args):
    type_map = {"photo": "1", "screenshot": "2", "other": "3"}
    ct = type_map.get(args["content_type"], "1")
    flickr_api._api_post("flickr.photos.setContentType", {"photo_id": args["id"], "content_type": ct})
    return [TextContent(type="text", text=f"Photo {args['id']} content type set to {args['content_type']}.")]


async def _set_dates(args):
    granularity_map = {"exact": "0", "month": "4", "year": "6"}
    granularity = granularity_map.get(args.get("granularity", "exact"), "0")
    flickr_api._api_post("flickr.photos.setDates", {
        "photo_id":               args["id"],
        "date_taken":             args["date_taken"],
        "date_taken_granularity": granularity,
    })
    with get_db() as conn:
        conn.execute("UPDATE photos SET date_taken=? WHERE id=?", (args["date_taken"], args["id"]))
    return [TextContent(type="text", text=f"Date taken for photo {args['id']} set to {args['date_taken']}.")]


async def _get_exif(args):
    data = flickr_api._api_get("flickr.photos.getExif", {"photo_id": args["photo_id"]})
    exif = data.get("photo", {}).get("exif", [])
    result = [{
        "tag":   e["tag"],
        "label": e.get("label", e["tag"]),
        "value": e.get("clean", {}).get("_content", e.get("raw", {}).get("_content", "")),
    } for e in exif]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _get_upload_status():
    data = flickr_api._api_get("flickr.people.getUploadStatus")
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
    import datetime
    user_id = flickr_api.resolve_user_id(args["user_id"])
    data = flickr_api._api_get("flickr.people.getInfo", {"user_id": user_id})
    p = data.get("person", {})
    nsid = p.get("nsid", "")

    last_upload = None
    try:
        recent = flickr_api._api_get("flickr.people.getPhotos", {
            "user_id": nsid, "per_page": "1", "extras": "date_upload",
        })
        photos = recent.get("photos", {}).get("photo", [])
        if photos:
            ts = int(photos[0].get("dateupload", 0))
            last_upload = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else None
    except Exception:
        pass

    try:
        own_nsid = flickr_api._load_credentials().get("user_nsid", "")
    except RuntimeError:
        own_nsid = ""

    # flickr.people.getInfo's friend/family attributes are not trustworthy —
    # it reports both as 1 for essentially every contact, which made contact
    # cleanup impossible to drive from this tool. flickr.contacts.getList
    # (what sync_contacts.py stores in the contacts table) returns the real
    # per-contact flags, so prefer the synced values and only fall back to
    # the API's when contacts have never been synced — saying so in the
    # payload either way.
    is_friend, is_family, flags_source = None, None, ""
    try:
        with get_db() as conn:
            contacts_synced = conn.execute(
                "SELECT COUNT(*) FROM sync_log WHERE type='contacts'"
            ).fetchone()[0] > 0
            row = conn.execute(
                "SELECT is_friend, is_family FROM contacts WHERE id = ?", (nsid,)
            ).fetchone()
        if row is not None:
            is_friend, is_family = bool(row["is_friend"]), bool(row["is_family"])
            flags_source = "local contacts cache (flickr.contacts.getList)"
        elif contacts_synced:
            # Synced, and they aren't in it — you don't follow them, so
            # neither flag can be set.
            is_friend = is_family = False
            flags_source = "local contacts cache (not a contact)"
    except Exception as e:
        logging.warning("get_person_info: contacts lookup failed for %s: %s", nsid, e)
    if flags_source == "":
        is_friend, is_family = bool(p.get("friend")), bool(p.get("family"))
        flags_source = (
            "flickr.people.getInfo — UNRELIABLE (this endpoint reports friend "
            "and family as true for nearly every contact); run a contacts sync "
            "for accurate flags"
        )

    # Flickr's API never exposes a user's follower count (for anyone,
    # including yourself) — only how many people *they* follow, and only
    # when their contact list is public.
    following_count = None
    try:
        public_contacts = flickr_api._api_get("flickr.contacts.getPublicList", {
            "user_id": nsid, "per_page": "1",
        })
        following_count = int(public_contacts.get("contacts", {}).get("total", 0))
    except Exception:
        pass

    return [TextContent(type="text", text=json.dumps({
        "nsid":             nsid,
        "username":         p.get("username", {}).get("_content", ""),
        "path_alias":       p.get("path_alias", ""),
        "realname":         p.get("realname", {}).get("_content", ""),
        "location":         p.get("location", {}).get("_content", ""),
        "description":      p.get("description", {}).get("_content", ""),
        "photos":           p.get("photos", {}).get("count", {}).get("_content", 0),
        "profile_url":      f"https://www.flickr.com/people/{nsid}/",
        "ispro":            p.get("ispro", 0),
        "you_follow":       bool(p.get("contact")),
        "follows_you":      bool(p.get("revcontact")),
        "is_friend":        is_friend,
        "is_family":        is_family,
        "flags_source":     flags_source,
        "following_count":  following_count,
        "followers_count":  "unavailable — Flickr's API does not expose follower counts for any account",
        "last_upload":      last_upload,
        # NSID is the only stable identifier — usernames/path aliases (what
        # shows up in flickr.com URLs) can be renamed at any time, so an old
        # link may show a username that no longer matches this same account.
        "is_you":           bool(own_nsid) and nsid == own_nsid,
    }, indent=2))]


async def _get_user_photos(args):
    user_id = flickr_api.resolve_user_id(args["user_id"])
    limit = max(1, min(100, int(args.get("limit", 30))))
    data = flickr_api._api_get("flickr.people.getPhotos", {
        "user_id":  user_id,
        "per_page": str(limit),
        "extras":   "views,date_taken",
    })
    photos = data.get("photos", {}).get("photo", [])
    return [TextContent(type="text", text=json.dumps([{
        "id":        p["id"],
        "title":     p.get("title", ""),
        "date_taken": p.get("datetaken", ""),
        "views":     int(p.get("views", 0) or 0),
        "url":       f"https://www.flickr.com/photos/{user_id}/{p['id']}/",
    } for p in photos], indent=2))]


async def _get_photostream_stats(args):
    from datetime import date as date_type, timedelta
    query_date = args.get("date", (date_type.today() - timedelta(days=1)).isoformat())
    data = flickr_api._api_get("flickr.stats.getTotalViews", {"date": query_date})
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
    creds = flickr_api._load_credentials()
    sort = args.get("sort", "favorites")
    if sort not in ("favorites", "comments", "views"):
        sort = "favorites"
    limit = int(args.get("limit", 20))
    data = flickr_api._api_get("flickr.photos.getPopular", {
        "user_id":  creds["user_nsid"],
        # getPopular's sort token for favorites is "faves"; unknown tokens
        # silently fall back to the default "interesting" sort
        "sort":     "faves" if sort == "favorites" else sort,
        "per_page": str(limit),
        "extras":   "views,date_taken",
    })
    photos = data.get("photos", {}).get("photo", [])
    ids = [p["id"] for p in photos]
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, favorites, comments FROM photos WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall() if ids else []
    counts = {r["id"]: r for r in rows}
    return [TextContent(type="text", text=json.dumps([{
        "id":        p["id"],
        "title":     p.get("title", ""),
        "views":     p.get("views", 0),
        "favorites": counts[p["id"]]["favorites"] if p["id"] in counts else None,
        "comments":  counts[p["id"]]["comments"] if p["id"] in counts else None,
        "url":       f"https://www.flickr.com/photos/{p.get('owner', '')}/{p['id']}/",
    } for p in photos], indent=2))]


async def _get_photo_faves(args):
    photo_id = args["photo_id"]
    limit = max(1, min(500, int(args.get("limit", 50))))
    data = flickr_api._api_get("flickr.photos.getFavorites", {
        "photo_id": photo_id,
        "per_page": str(limit),
        "page":     "1",
    })
    photo_data = data.get("photo", {})
    persons = photo_data.get("person", [])
    total = int(photo_data.get("total", len(persons)))
    nsids = {p["nsid"] for p in persons}
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id FROM contacts WHERE id IN ({','.join('?' * len(nsids))})",
            list(nsids),
        ).fetchall() if nsids else []
    following = {r[0] for r in rows}
    result = {
        "total": total,
        "showing": len(persons),
        "faves": [{
            "nsid":        p["nsid"],
            "username":    p.get("username", ""),
            "realname":    p.get("realname", ""),
            "profile_url": f"https://www.flickr.com/people/{p.get('path_alias') or p['nsid']}/",
            "you_follow":  p["nsid"] in following,
        } for p in persons],
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _get_faves(args):
    creds = flickr_api._load_credentials()
    limit = int(args.get("limit", 20))
    page = int(args.get("page", 1))
    data = flickr_api._api_get("flickr.favorites.getList", {
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


async def _get_photos_with_comments(args):
    limit = min(int(args.get("limit", 50)), 200)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, url_photopage, comments FROM photos WHERE comments > 0 ORDER BY date_uploaded DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _get_recent_activity(args):
    timeframe = args.get("timeframe", "1d")
    import re as _re
    if not _re.match(r"^\d+[dh]$", timeframe):
        timeframe = "1d"
    data = flickr_api._api_get("flickr.activity.userPhotos", {"timeframe": timeframe, "per_page": "50"})
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
    return [TextContent(type="text", text=json.dumps(results, indent=2))]


async def _get_unreplied_comments(args):
    timeframe = args.get("timeframe", "7d")
    import re as _re
    if not _re.match(r"^\d+[dh]$", timeframe):
        timeframe = "7d"
    data = flickr_api._api_get("flickr.activity.userPhotos", {"timeframe": timeframe, "per_page": "50"})
    items = data.get("items", {}).get("item", [])
    own_nsid = flickr_api._load_credentials().get("user_nsid", "")

    # Only photos with a comment-type event are worth a full comment-thread
    # fetch — faves/other event types can't be "unreplied".
    commented_photo_ids = []
    for item in items:
        activity = item.get("activity", {}).get("event", [])
        if isinstance(activity, dict):
            activity = [activity]
        if any(e.get("type") == "comment" for e in activity):
            pid = item.get("id", "")
            if pid and pid not in commented_photo_ids:
                commented_photo_ids.append(pid)

    results = []
    for photo_id in commented_photo_ids:
        try:
            comments_data = flickr_api._api_get("flickr.photos.comments.getList", {"photo_id": photo_id})
        except flickr_api.FlickrAPIError as e:
            logging.warning("get_unreplied_comments: failed to fetch comments for photo %s: %s", photo_id, e)
            continue
        comments = comments_data.get("comments", {}).get("comment", [])
        if isinstance(comments, dict):
            comments = [comments]
        if not comments:
            continue

        # Replies posted via add_comment always start with "[<author_url>]" to
        # notify the commenter (see .claude/commands/flickr-thanks.md) — so a
        # commenter has actually been replied to only if one of the owner's
        # own comments mentions their nsid, not merely if the owner posted
        # *something* afterward (that reply could be addressed to someone
        # else entirely).
        addressed_nsids = set()
        for c in comments:
            if c.get("author") == own_nsid:
                addressed_nsids |= set(_re.findall(
                    r"flickr\.com/(?:people|photos)/([^/\s\]]+)/?", c.get("_content", "")
                ))

        unreplied = [{
            "author":      c["authorname"],
            "author_nsid": c["author"],
            "author_url":  f"https://www.flickr.com/photos/{c['author']}/",
            "date":        datetime.fromtimestamp(int(c["datecreate"])).strftime("%Y-%m-%d"),
            "comment":     c["_content"],
            "permalink":   c["permalink"],
        } for c in comments
          if c.get("author") != own_nsid
          and c.get("author") not in addressed_nsids]

        if unreplied:
            with get_db() as conn:
                row = conn.execute("SELECT title, url_photopage FROM photos WHERE id = ?", (photo_id,)).fetchone()
            results.append({
                "photo_id":           photo_id,
                "title":              row["title"] if row else "",
                "url_photopage":      row["url_photopage"] if row else f"https://www.flickr.com/photos/{own_nsid}/{photo_id}/",
                "unreplied_comments": unreplied,
            })
    return [TextContent(type="text", text=json.dumps(results, indent=2))]


async def _add_to_keeper_list(args):
    photo_id = args.get("photo_id", "").strip()
    note = args.get("note", "").strip()
    if not photo_id:
        return [TextContent(type="text", text="photo_id is required.")]
    import time as _time
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO keeper_list (photo_id, note, added_at) VALUES (?, ?, ?)",
            (photo_id, note or None, int(_time.time())),
        )
    return [TextContent(type="text", text=f"Photo {photo_id} added to keeper list.")]


async def _get_keeper_list(args):
    own_nsid = flickr_api._load_credentials().get("user_nsid", "")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT k.photo_id, p.title, p.url_photopage, k.note, k.added_at
               FROM keeper_list k
               LEFT JOIN photos p ON p.id = k.photo_id
               ORDER BY k.added_at DESC"""
        ).fetchall()
    result = [
        {
            "photo_id":    r[0],
            "title":       r[1] or "",
            "url":         r[2] or f"https://www.flickr.com/photos/{own_nsid}/{r[0]}/",
            "note":        r[3] or "",
            "added_at":    r[4],
        }
        for r in rows
    ]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _remove_from_keeper_list(args):
    photo_id = args.get("photo_id", "").strip()
    if not photo_id:
        return [TextContent(type="text", text="photo_id is required.")]
    with get_db() as conn:
        conn.execute("DELETE FROM keeper_list WHERE photo_id = ?", (photo_id,))
    return [TextContent(type="text", text=f"Photo {photo_id} removed from keeper list.")]


HANDLERS = {
    "search_photos":        _search_photos,
    "get_photo":            _get_photo,
    "get_summary":          lambda _: _get_summary(),
    "list_recent_syncs":    _list_recent_syncs,
    "update_photo":         _update_photo,
    "batch_update_photos":  _batch_update_photos,
    "replace_photo":        _replace_photo,
    "fetch_photo_image":    _fetch_photo_image,
    "get_photo_comments":   _get_photo_comments,
    "add_comment":          _add_comment,
    "delete_comment":       _delete_comment,
    "fave_photo":           _fave_photo,
    "remove_fave":          _remove_fave,
    "get_photo_stats":      _get_photo_stats,
    "find_weak_photos":     _find_weak_photos,
    "set_visibility":       _set_visibility,
    "set_location":         _set_location,
    "remove_location":      _remove_location,
    "set_safety_level":     _set_safety_level,
    "set_content_type":     _set_content_type,
    "set_dates":            _set_dates,
    "get_exif":             _get_exif,
    "get_upload_status":    lambda _: _get_upload_status(),
    "get_person_info":      _get_person_info,
    "get_user_photos":      _get_user_photos,
    "get_photostream_stats": _get_photostream_stats,
    "get_popular_photos":   _get_popular_photos,
    "get_photo_faves":      _get_photo_faves,
    "get_faves":            _get_faves,
    "get_photos_with_comments": _get_photos_with_comments,
    "get_recent_activity":  _get_recent_activity,
    "get_unreplied_comments": _get_unreplied_comments,
    "add_to_keeper_list":   _add_to_keeper_list,
    "get_keeper_list":      _get_keeper_list,
    "remove_from_keeper_list": _remove_from_keeper_list,
}
