"""Contact tool definitions and handlers."""

import json
import time

from mcp.types import TextContent, Tool

import flickr_api
from db import get_db

TOOLS = [
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
        name="follow_contact",
        description="Follow a Flickr user by NSID. Optionally mark them as a friend and/or family member.",
        inputSchema={
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Flickr NSID of the user to follow"},
                "is_friend":  {"type": "boolean", "description": "Mark as a friend (default false)"},
                "is_family":  {"type": "boolean", "description": "Mark as family (default false)"},
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
]


async def _get_contacts_summary():
    with get_db() as conn:
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
    with get_db() as conn:
        rows = conn.execute(sql, (require_zero, limit)).fetchall()
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
    with get_db() as conn:
        conn.execute(
            "INSERT INTO do_not_unfollow (contact_id, reason, added_at) VALUES (?, ?, ?) "
            "ON CONFLICT(contact_id) DO UPDATE SET reason=excluded.reason",
            (contact_id, reason, int(time.time())),
        )
    return [TextContent(type="text", text=f"Contact {contact_id} added to do-not-unfollow list.")]


async def _follow_contact(args):
    contact_id = flickr_api.resolve_user_id(args["contact_id"])
    is_friend = 1 if args.get("is_friend") else 0
    is_family = 1 if args.get("is_family") else 0
    profile_url = f"https://www.flickr.com/people/{contact_id}/"
    try:
        flickr_api._api_post("flickr.contacts.add", {
            "user_id": contact_id,
            "friend":  str(is_friend),
            "family":  str(is_family),
        })
        # Fetch the user's display name to store locally
        try:
            info = flickr_api._api_get("flickr.people.getInfo", {"user_id": contact_id})
            person = info.get("person", {})
            username = person.get("username", {}).get("_content", "")
            realname = person.get("realname", {}).get("_content", "")
        except Exception:
            username, realname = "", ""
        with get_db() as conn:
            conn.execute(
                "INSERT INTO contacts (id, username, realname, is_friend, is_family, synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  username=excluded.username, realname=excluded.realname, "
                "  is_friend=excluded.is_friend, is_family=excluded.is_family, "
                "  synced_at=excluded.synced_at",
                (contact_id, username, realname, is_friend, is_family, int(time.time())),
            )
        labels = []
        if is_friend:
            labels.append("friend")
        if is_family:
            labels.append("family")
        label_str = f" (marked as {', '.join(labels)})" if labels else ""
        msg = f"Now following {username or contact_id}{label_str}. Profile: {profile_url}"
    except RuntimeError as e:
        msg = f"API follow failed ({e}). Profile: {profile_url}"
    return [TextContent(type="text", text=msg)]


async def _unfollow_contact(args):
    contact_id = args["contact_id"]
    profile_url = f"https://www.flickr.com/people/{contact_id}/"
    try:
        flickr_api._api_post("flickr.contacts.remove", {"user_id": contact_id})
        with get_db() as conn:
            conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        api_result = "Unfollowed via API. "
    except RuntimeError as e:
        api_result = f"API unfollow failed ({e}) — use profile URL to unfollow manually. "
    return [TextContent(type="text", text=f"{api_result}Profile: {profile_url}")]


async def _get_contact_uploads(args):
    limit = int(args.get("limit", 20))
    just_friends = "1" if args.get("just_friends") else "0"
    data = flickr_api._api_get("flickr.photos.getContactsPhotos", {
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


HANDLERS = {
    "get_contacts_summary":     lambda _: _get_contacts_summary(),
    "find_unfollow_candidates": _find_unfollow_candidates,
    "protect_contact":          _protect_contact,
    "follow_contact":           _follow_contact,
    "unfollow_contact":         _unfollow_contact,
    "get_contact_uploads":      _get_contact_uploads,
}
