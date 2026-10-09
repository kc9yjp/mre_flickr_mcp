"""Scheduled-publish queue: make a private photo public at a future time.

Mirrors the group-add queue (``pending_group_adds`` in tools/groups.py): a
row in ``pending_publishes`` carries a ``publish_after`` timestamp, and
``_flush_publish_queue`` — called from the background loop, from
``get_publish_queue``, and from the Workbench Queue panel — publishes
anything whose time has passed.
"""

import json
import logging
import re
import time

from mcp.types import TextContent, Tool

import flickr_api
from flickr_api import FlickrAPIError
from db import get_db
from tools.groups import _NAMED_TIMES, _fmt_chicago, _parse_retry_time
from tools.photos import _set_perms

TOOLS = [
    Tool(
        name="schedule_publish",
        description=(
            "Schedule a photo to be made public at a future time instead of right away — useful for "
            "drip-publishing photos (e.g. one per day) after reviewing them. The server publishes it "
            "automatically when the time comes. publish_at takes named times (morning, lunchtime, "
            "afternoon, evening, night, midnight) or HH:MM, resolved in Chicago time; combine with "
            "days_offset to pick the day (days_offset=2 with publish_at=morning = 8am two days from now). "
            "If the photo is already scheduled, this reschedules it. When it publishes, the photo's "
            "'date posted' is reset to just before the publish time, so it appears at the top of your "
            "photostream and in contacts' feeds rather than buried at its original upload date."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "publish_at": {
                    "type": "string",
                    "description": (
                        "When to publish. Named times: morning (8am), lunchtime (12pm), afternoon (2pm), "
                        "evening (6pm), night (9pm), midnight. Or HH:MM (24h, Chicago time). Defaults to 5pm CT."
                    ),
                },
                "days_offset": {
                    "type": "integer",
                    "description": (
                        "Days from today (default 0 = the next occurrence of publish_at, today or tomorrow). "
                        "days_offset=1 = tomorrow, 2 = the day after, etc."
                    ),
                },
            },
            "required": ["photo_id"],
        },
    ),
    Tool(
        name="get_publish_queue",
        description=(
            "Show the scheduled-publish queue: photos waiting to be made public and when, plus recent "
            "failures and completions. Also publishes any waiting photos whose time has passed."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="cancel_scheduled_publish",
        description="Remove a photo from the scheduled-publish queue so it stays private.",
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
            },
            "required": ["photo_id"],
        },
    ),
]


_HHMM = re.compile(r"^\d{1,2}:\d{2}$")


def _valid_publish_at(value: str) -> bool:
    """True if *value* is a time _parse_retry_time resolves without falling back.

    _parse_retry_time silently maps unrecognised strings to next midnight UTC,
    which is fine for a rate-limit retry but would publish a photo at a time
    the caller never asked for — so reject those up front instead.
    """
    token = value.lower().strip()
    if token in _NAMED_TIMES:
        return True
    if _HHMM.match(token):
        h, m = (int(x) for x in token.split(":"))
        return 0 <= h <= 23 and 0 <= m <= 59
    return False


# How far before the actual publish the photo's 'date posted' is set, so it
# reads as uploaded just before it went public.
_DATE_POSTED_LEAD_SECONDS = 60


def _flush_publish_queue(conn, force: bool = False) -> list[dict]:
    """Publish waiting items whose publish_after has passed.

    Each photo's date posted is first reset to just before now, then it's
    made public. When *force* is True, all waiting items are published
    regardless of schedule. A Flickr-side rejection of the publish (photo
    deleted, not yours, ...) marks the item 'error'; a transient failure
    (timeout, rate limit, HTTP 5xx) leaves it waiting so the next
    background-loop wake retries it. A Flickr rejection of the date reset
    alone doesn't block the publish — it's recorded as a note.
    """
    now = int(time.time())
    sql = "SELECT id, photo_id FROM pending_publishes WHERE status='waiting'"
    if force:
        rows = conn.execute(sql).fetchall()
    else:
        rows = conn.execute(sql + " AND publish_after <= ?", (now,)).fetchall()

    flushed = []
    for row in rows:
        photo_id = row["photo_id"]
        date_posted = now - _DATE_POSTED_LEAD_SECONDS
        note = None
        try:
            flickr_api._api_post("flickr.photos.setDates", {"photo_id": photo_id, "date_posted": str(date_posted)})
            conn.execute("UPDATE photos SET date_uploaded=? WHERE id=?", (date_posted, photo_id))
        except FlickrAPIError as e:
            logging.warning("publish queue: Flickr rejected resetting date posted for photo %s: %s",
                            photo_id, e.flickr_message)
            note = f"Published, but resetting date posted failed: {e.flickr_message}"
        except RuntimeError as e:
            logging.warning("publish queue: transient failure resetting date posted for photo %s, will retry: %s",
                            photo_id, e)
            flushed.append({"photo_id": photo_id, "result": f"retry_later: {e}"})
            continue

        try:
            _set_perms(photo_id, 1)
        except FlickrAPIError as e:
            logging.warning("publish queue: Flickr rejected publishing photo %s: %s", photo_id, e.flickr_message)
            conn.execute(
                "UPDATE pending_publishes SET status='error', error_msg=?, completed_at=? WHERE id=?",
                (e.flickr_message, now, row["id"]),
            )
            flushed.append({"photo_id": photo_id, "result": f"error: {e.flickr_message}"})
            continue
        except RuntimeError as e:
            logging.warning("publish queue: transient failure publishing photo %s, will retry: %s", photo_id, e)
            flushed.append({"photo_id": photo_id, "result": f"retry_later: {e}"})
            continue

        conn.execute("UPDATE photos SET is_public=1 WHERE id=?", (photo_id,))
        conn.execute(
            "UPDATE pending_publishes SET status='success', error_msg=?, completed_at=? WHERE id=?",
            (note, now, row["id"]),
        )
        flushed.append({"photo_id": photo_id, "result": "success" if not note else f"success ({note})"})
    return flushed


async def _schedule_publish(args):
    photo_id = args["photo_id"]
    publish_at = args.get("publish_at")
    if publish_at is not None and not _valid_publish_at(publish_at):
        return [TextContent(type="text", text=(
            f"Error: unrecognised publish_at {publish_at!r}. Use a named time "
            f"({', '.join(_NAMED_TIMES)}) or HH:MM (24h, Chicago time)."
        ))]
    try:
        days_offset = int(args.get("days_offset", 0) or 0)
    except (TypeError, ValueError):
        return [TextContent(type="text", text=f"Error: days_offset must be an integer, got {args.get('days_offset')!r}.")]
    if days_offset < 0:
        return [TextContent(type="text", text="Error: days_offset can't be negative.")]
    publish_after = _parse_retry_time(publish_at, days_offset)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM pending_publishes WHERE photo_id=? AND status='waiting'", (photo_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE pending_publishes SET publish_after=? WHERE id=?",
                (publish_after, existing["id"]),
            )
            action = "rescheduled"
        else:
            conn.execute(
                "INSERT INTO pending_publishes (photo_id, status, publish_after, queued_at) "
                "VALUES (?, 'waiting', ?, ?)",
                (photo_id, publish_after, int(time.time())),
            )
            action = "scheduled"
        photo = conn.execute("SELECT is_public FROM photos WHERE id=?", (photo_id,)).fetchone()

    text = (
        f"Photo {photo_id} {action} to be made public at {_fmt_chicago(publish_after)} "
        "(its date posted will be reset to just before then)."
    )
    if photo is not None and photo["is_public"]:
        text += " Note: the local database already lists this photo as public."
    return [TextContent(type="text", text=text)]


async def _get_publish_queue(args):
    with get_db() as conn:
        flushed = _flush_publish_queue(conn)
        waiting = conn.execute(
            "SELECT pp.photo_id, p.title, pp.publish_after "
            "FROM pending_publishes pp LEFT JOIN photos p ON pp.photo_id = p.id "
            "WHERE pp.status='waiting' ORDER BY pp.publish_after ASC",
        ).fetchall()
        errors = conn.execute(
            "SELECT pp.photo_id, p.title, pp.error_msg "
            "FROM pending_publishes pp LEFT JOIN photos p ON pp.photo_id = p.id "
            "WHERE pp.status='error' ORDER BY pp.completed_at DESC LIMIT 20",
        ).fetchall()
        recent = conn.execute(
            "SELECT pp.photo_id, p.title, pp.completed_at, pp.error_msg "
            "FROM pending_publishes pp LEFT JOIN photos p ON pp.photo_id = p.id "
            "WHERE pp.status='success' ORDER BY pp.completed_at DESC LIMIT 10",
        ).fetchall()
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM pending_publishes GROUP BY status"
        ).fetchall()

    summary = {"waiting": 0, "success": 0, "error": 0}
    summary.update({r["status"]: r["n"] for r in counts})
    result = {
        "summary": summary,
        "waiting": [{
            "photo_id": r["photo_id"], "photo_title": r["title"],
            "publish_at": _fmt_chicago(r["publish_after"]),
        } for r in waiting],
        "errors": [{"photo_id": r["photo_id"], "photo_title": r["title"], "error": r["error_msg"]} for r in errors],
        "recently_published": [{
            "photo_id": r["photo_id"], "photo_title": r["title"],
            "published_at": _fmt_chicago(r["completed_at"]),
            **({"note": r["error_msg"]} if r["error_msg"] else {}),
        } for r in recent],
    }
    if flushed:
        result["flushed_this_call"] = flushed
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _cancel_scheduled_publish(args):
    photo_id = args["photo_id"]
    with get_db() as conn:
        deleted = conn.execute(
            "DELETE FROM pending_publishes WHERE photo_id=? AND status='waiting'", (photo_id,),
        ).rowcount
    if deleted:
        return [TextContent(type="text", text=f"Cancelled scheduled publish for photo {photo_id}; it stays as-is.")]
    return [TextContent(type="text", text=f"No scheduled publish found for photo {photo_id}.")]


HANDLERS = {
    "schedule_publish":         _schedule_publish,
    "get_publish_queue":        _get_publish_queue,
    "cancel_scheduled_publish": _cancel_scheduled_publish,
}
