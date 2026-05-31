"""Photo tool definitions and handlers."""

import base64
import json
import logging
import time
from datetime import datetime

import requests
from mcp.types import ImageContent, TextContent, Tool

import flickr_api
from db import get_db

TOOLS = [
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
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sort_by = args.get("sort_by", "date_taken")
    if sort_by not in ("date_taken", "views", "favorites", "date_uploaded"):
        sort_by = "date_taken"
    order = "ASC" if args.get("order", "desc") == "asc" else "DESC"
    limit = min(int(args.get("limit", 50)), 200)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM photos {where} ORDER BY {sort_by} {order} LIMIT ?",
            params + [limit],
        ).fetchall()
    return [TextContent(type="text", text=json.dumps([dict(r) for r in rows], indent=2))]


async def _get_photo(args):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (args["id"],)).fetchone()
    if not row:
        return [TextContent(type="text", text=f"Photo {args['id']} not found.")]
    return [TextContent(type="text", text=json.dumps(dict(row), indent=2))]


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


async def _update_photo(args):
    photo_id = args["id"]
    updated = []
    with get_db() as conn:
        if "title" in args or "description" in args:
            row = conn.execute("SELECT title, description FROM photos WHERE id = ?", (photo_id,)).fetchone()
            title = args.get("title", row["title"] if row else "")
            description = args.get("description", row["description"] if row else "")
            flickr_api._api_post("flickr.photos.setMeta", {"photo_id": photo_id, "title": title, "description": description})
            updated.append("title/description")
        if "tags" in args:
            flickr_api._api_post("flickr.photos.setTags", {"photo_id": photo_id, "tags": args["tags"]})
            updated.append("tags")
        if "title" in args:
            conn.execute("UPDATE photos SET title=? WHERE id=?", (args["title"], photo_id))
        if "description" in args:
            conn.execute("UPDATE photos SET description=? WHERE id=?", (args["description"], photo_id))
        if "tags" in args:
            conn.execute("UPDATE photos SET tags=? WHERE id=?", (args["tags"], photo_id))
    return [TextContent(type="text", text=f"Updated {', '.join(updated)} for photo {photo_id}.")]


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
    data = base64.standard_b64encode(resp.content).decode()
    return [
        TextContent(type="text", text=f"Photo ID: {photo_id}\n{photopage}"),
        ImageContent(type="image", data=data, mimeType=mime),
    ]


async def _get_photo_comments(args):
    data = flickr_api._api_get("flickr.photos.comments.getList", {"photo_id": args["photo_id"]})
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
    with get_db() as conn:
        rows = conn.execute(sql, (min_age_days, review_cooldown_days, require_zero_faves, limit)).fetchall()
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

    return [TextContent(type="text", text=json.dumps({
        "nsid":           nsid,
        "username":       p.get("username", {}).get("_content", ""),
        "realname":       p.get("realname", {}).get("_content", ""),
        "location":       p.get("location", {}).get("_content", ""),
        "description":    p.get("description", {}).get("_content", ""),
        "photos":         p.get("photos", {}).get("count", {}).get("_content", 0),
        "profile_url":    f"https://www.flickr.com/people/{nsid}/",
        "ispro":          p.get("ispro", 0),
        "you_follow":     bool(p.get("contact")),
        "follows_you":    bool(p.get("revcontact")),
        "is_friend":      bool(p.get("friend")),
        "is_family":      bool(p.get("family")),
        "last_upload":    last_upload,
    }, indent=2))]


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


async def _get_photo_faves(args):
    photo_id = args["photo_id"]
    limit = int(args.get("limit", 50))
    data = flickr_api._api_get("flickr.photos.getFavorites", {
        "photo_id": photo_id,
        "per_page": str(limit),
        "page":     "1",
    })
    persons = data.get("photo", {}).get("person", [])
    nsids = {p["nsid"] for p in persons}
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id FROM contacts WHERE id IN ({','.join('?' * len(nsids))})",
            list(nsids),
        ).fetchall() if nsids else []
    following = {r[0] for r in rows}
    return [TextContent(type="text", text=json.dumps([{
        "nsid":         p["nsid"],
        "username":     p.get("username", ""),
        "realname":     p.get("realname", ""),
        "profile_url":  f"https://www.flickr.com/people/{p['nsid']}/",
        "you_follow":   p["nsid"] in following,
    } for p in persons], indent=2))]


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


async def _get_recent_activity(args):
    timeframe = args.get("timeframe", "day")
    if timeframe not in ("day", "week"):
        timeframe = "day"
    limit = int(args.get("limit", 20))
    data = flickr_api._api_get("flickr.activity.userPhotos", {"timeframe": timeframe, "per_page": str(limit)})
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


HANDLERS = {
    "search_photos":        _search_photos,
    "get_photo":            _get_photo,
    "get_summary":          lambda _: _get_summary(),
    "list_recent_syncs":    _list_recent_syncs,
    "update_photo":         _update_photo,
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
    "get_photostream_stats": _get_photostream_stats,
    "get_popular_photos":   _get_popular_photos,
    "get_photo_faves":      _get_photo_faves,
    "get_faves":            _get_faves,
    "get_recent_activity":  _get_recent_activity,
}
