"""Scheduled-publish queue: make a private photo public at a future time.

Mirrors the group-add queue (``pending_group_adds`` in tools/groups.py): a
row in ``pending_publishes`` carries a ``publish_after`` timestamp, and
``_flush_publish_queue`` — called from the background loop, from
``get_publish_queue``, and from the Workbench Queue panel — publishes
anything whose time has passed. The Workbench (webapi.api_queue and the
photo detail endpoint) goes through the helpers here too, so the MCP tools
and the UI share one set of rules.
"""

import datetime
import json
import logging
import re
import time

from mcp.types import TextContent, Tool

import flickr_api
from flickr_api import FlickrAPIError
from db import get_db
from tools.groups import _NAMED_TIMES, _RETRY_TZ, _parse_retry_time
from tools.photos import _set_perms

_WHEN_HELP = (
    "A date and time like '2026-10-15 08:00' (or just '2026-10-15' for 5pm that day), "
    "a named time — morning (8am), lunchtime (12pm), afternoon (2pm), evening (6pm), "
    "night (9pm), midnight — or HH:MM (24h). All in Chicago time. Defaults to the next 5pm."
)

TOOLS = [
    Tool(
        name="schedule_publish",
        description=(
            "Schedule one of your private photos to be made public later instead of right away — "
            "e.g. to drip-publish reviewed photos one per day. The server publishes it automatically "
            "at that time; nothing else needs to be running. Just before publishing, the photo's "
            "'date posted' is reset to the publish time, so it shows up as new at the top of your "
            "photostream and in contacts' feeds instead of buried at its original upload date. "
            "Calling this again for a photo that's already waiting changes its time; calling it for "
            "a photo whose earlier publish failed tries again. To publish right now instead, use "
            "set_visibility (which also takes the photo out of this queue). See the queue with "
            "get_publish_queue."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
                "publish_at": {"type": "string", "description": f"When to publish. {_WHEN_HELP}"},
                "days_offset": {
                    "type": "integer",
                    "description": (
                        "Only with a named time or HH:MM: how many days ahead. 0 (default) = the next time "
                        "that clock time comes round (today or tomorrow), 1 = tomorrow, 2 = the day after, "
                        "etc. Handy for spacing photos out: days_offset=1, 2, 3 with publish_at=morning "
                        "publishes one each morning."
                    ),
                },
            },
            "required": ["photo_id"],
        },
    ),
    Tool(
        name="get_publish_queue",
        description=(
            "List the photos scheduled to be made public (soonest first, with when and how long until), "
            "any that failed and why, and the ones published recently. Also publishes anything whose "
            "time has already passed."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="cancel_scheduled_publish",
        description=(
            "Take a photo out of the scheduled-publish queue so it stays private (its date posted is "
            "left untouched). Also clears a failed entry for that photo."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "photo_id": {"type": "string", "description": "Flickr photo ID"},
            },
            "required": ["photo_id"],
        },
    ),
]


# How far before the actual publish the photo's 'date posted' is set, so it
# reads as uploaded just before it went public.
_DATE_POSTED_LEAD_SECONDS = 60

# Clock time used when a caller gives a date with no time (matches the
# _parse_retry_time default).
_DEFAULT_HOUR = 17

_HHMM = re.compile(r"^\d{1,2}:\d{2}$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PublishTimeError(ValueError):
    """A publish time the caller gave that we refuse to guess at."""


def _resolve_publish_time(publish_at: str | None, days_offset: int = 0) -> int:
    """Turn a caller's publish_at/days_offset into a Unix timestamp.

    Accepts everything add_to_group's retry_at does (named times, HH:MM, plus
    days_offset), and also an absolute date or date-time — ISO strings with an
    explicit offset (what the Workbench sends) are honoured as-is, naive ones
    are read as Chicago time. Unlike _parse_retry_time, which silently maps
    junk to next midnight UTC (fine for a rate-limit retry), anything
    unrecognised or in the past raises PublishTimeError: publishing a photo
    at a time nobody asked for isn't recoverable.
    """
    if days_offset < 0:
        raise PublishTimeError("days_offset can't be negative.")
    if publish_at is None or not str(publish_at).strip():
        return _parse_retry_time(None, days_offset)

    token = str(publish_at).strip()
    lowered = token.lower()
    if lowered in _NAMED_TIMES:
        return _parse_retry_time(lowered, days_offset)
    if _HHMM.match(lowered):
        h, m = (int(x) for x in lowered.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise PublishTimeError(f"{token!r} isn't a valid time of day.")
        return _parse_retry_time(lowered, days_offset)

    from zoneinfo import ZoneInfo
    try:
        if _DATE_ONLY.match(token):
            dt = datetime.datetime.fromisoformat(token).replace(hour=_DEFAULT_HOUR)
        else:
            dt = datetime.datetime.fromisoformat(token.replace(" ", "T", 1))
    except ValueError:
        raise PublishTimeError(f"Couldn't understand {token!r} as a time. {_WHEN_HELP}") from None
    if days_offset:
        raise PublishTimeError("days_offset only applies to a named time or HH:MM, not a full date.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(_RETRY_TZ))
    ts = int(dt.timestamp())
    if ts <= time.time():
        raise PublishTimeError(f"{_fmt_when(ts)} is in the past.")
    return ts


def _fmt_when(ts: int) -> str:
    """'Fri Oct 10, 8:00 AM CT' — readable, unambiguous, no leading zeros."""
    from zoneinfo import ZoneInfo
    dt = datetime.datetime.fromtimestamp(ts, ZoneInfo(_RETRY_TZ))
    hour = dt.strftime("%I").lstrip("0")
    return f"{dt.strftime('%a %b')} {dt.day}, {hour}:{dt.strftime('%M %p')} CT"


def _fmt_relative(ts: int, now: float | None = None) -> str:
    """'in 3 hours' / '2 days ago' — coarse, for at-a-glance reading."""
    delta = ts - (time.time() if now is None else now)
    future = delta >= 0
    secs = abs(delta)
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size:
            n = int(round(secs / size))
            label = f"{n} {unit}{'' if n == 1 else 's'}"
            break
    else:
        return "now"
    return f"in {label}" if future else f"{label} ago"


def _queue_publish(conn, photo_id: str, publish_after: int) -> str:
    """Insert or reschedule *photo_id*. Returns 'scheduled' or 'rescheduled'.

    A failed earlier attempt for the same photo is superseded by this one, so
    it's dropped rather than lingering in the errors list.
    """
    conn.execute("DELETE FROM pending_publishes WHERE photo_id=? AND status='error'", (photo_id,))
    existing = conn.execute(
        "SELECT id FROM pending_publishes WHERE photo_id=? AND status='waiting'", (photo_id,),
    ).fetchone()
    if existing:
        conn.execute("UPDATE pending_publishes SET publish_after=? WHERE id=?", (publish_after, existing["id"]))
        return "rescheduled"
    conn.execute(
        "INSERT INTO pending_publishes (photo_id, status, publish_after, queued_at) VALUES (?, 'waiting', ?, ?)",
        (photo_id, publish_after, int(time.time())),
    )
    return "scheduled"


def _flush_publish_queue(conn, force: bool = False, item_id: int | None = None) -> list[dict]:
    """Publish waiting items whose publish_after has passed.

    Each photo's date posted is first reset to just before now, then it's
    made public. *force* publishes waiting items regardless of schedule;
    *item_id* limits the run to that one row (implies force). A Flickr-side
    rejection of the publish (photo deleted, not yours, ...) marks the item
    'error'; a transient failure (timeout, rate limit, HTTP 5xx) leaves it
    waiting so the next background-loop wake retries it. A Flickr rejection
    of the date reset alone doesn't block the publish — it's recorded as a
    note.
    """
    now = int(time.time())
    sql = "SELECT id, photo_id FROM pending_publishes WHERE status='waiting'"
    if item_id is not None:
        rows = conn.execute(sql + " AND id=?", (item_id,)).fetchall()
    elif force:
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


def _queue_snapshot(conn) -> dict:
    """Counts plus waiting/error/recent-success rows, shared by the MCP tool and the Workbench."""
    counts = {"waiting": 0, "success": 0, "error": 0}
    counts.update({r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM pending_publishes GROUP BY status"
    ).fetchall()})

    def rows(where_order: str) -> list[dict]:
        out = []
        for r in conn.execute(
            "SELECT pp.*, p.title AS photo_title, p.url_photopage AS photo_url, p.url_medium AS thumb_url "
            "FROM pending_publishes pp LEFT JOIN photos p ON pp.photo_id = p.id " + where_order
        ).fetchall():
            ts = r["completed_at"] if r["status"] != "waiting" else r["publish_after"]
            out.append({
                "id":          r["id"],
                "photo_id":    r["photo_id"],
                "photo_title": r["photo_title"] or "Untitled",
                "photo_url":   r["photo_url"] or f"https://www.flickr.com/photo.gne?id={r['photo_id']}",
                "thumb_url":   r["thumb_url"],
                "when_ts":     ts,
                "when":        _fmt_when(ts) if ts else None,
                "when_relative": _fmt_relative(ts) if ts else None,
                "message":     r["error_msg"] or "",
            })
        return out

    return {
        "counts":    counts,
        "waiting":   rows("WHERE pp.status='waiting' ORDER BY pp.publish_after ASC"),
        "errors":    rows("WHERE pp.status='error' ORDER BY pp.completed_at DESC LIMIT 30"),
        "successes": rows("WHERE pp.status='success' ORDER BY pp.completed_at DESC LIMIT 20"),
    }


def _scheduled_for_photo(conn, photo_id: str) -> dict | None:
    """The waiting queue entry for *photo_id*, for the photo detail view."""
    r = conn.execute(
        "SELECT id, publish_after FROM pending_publishes WHERE photo_id=? AND status='waiting'", (photo_id,),
    ).fetchone()
    if not r:
        return None
    return {
        "id": r["id"],
        "when_ts": r["publish_after"],
        "when": _fmt_when(r["publish_after"]),
        "when_relative": _fmt_relative(r["publish_after"]),
    }


def _summarize_flush(flushed: list[dict]) -> str:
    ok = sum(1 for r in flushed if r["result"].startswith("success"))
    err = sum(1 for r in flushed if r["result"].startswith("error"))
    later = sum(1 for r in flushed if r["result"].startswith("retry_later"))
    if not flushed:
        return "Nothing to publish."
    parts = [f"{ok} published"]
    if err:
        parts.append(f"{err} failed")
    if later:
        parts.append(f"{later} couldn't reach Flickr and will retry automatically")
    return ", ".join(parts) + "."


def _web_action(conn, body: dict) -> tuple[dict, int]:
    """Handle a Workbench publish-queue action. Returns (json_body, http_status)."""
    action = body.get("action", "")
    item_id = body.get("item_id")

    def need_item():
        if not isinstance(item_id, int):
            return {"error": "item_id required"}, 400
        if not conn.execute("SELECT 1 FROM pending_publishes WHERE id=?", (item_id,)).fetchone():
            return {"error": "That item is no longer in the queue."}, 404
        return None

    if action == "publish_schedule":
        photo_id = str(body.get("photo_id") or "").strip()
        if not photo_id:
            return {"error": "photo_id required"}, 400
        try:
            ts = _resolve_publish_time(body.get("publish_at"))
        except PublishTimeError as e:
            return {"error": str(e)}, 400
        verb = _queue_publish(conn, photo_id, ts)
        return {"ok": True, "message": f"{verb.capitalize()} for {_fmt_when(ts)} ({_fmt_relative(ts)}).",
                "scheduled": _scheduled_for_photo(conn, photo_id)}, 200

    if action == "publish_cancel_photo":
        photo_id = str(body.get("photo_id") or "").strip()
        n = conn.execute(
            "DELETE FROM pending_publishes WHERE photo_id=? AND status IN ('waiting','error')", (photo_id,),
        ).rowcount
        return {"ok": True, "message": "Schedule cancelled." if n else "It wasn't scheduled."}, 200

    if action == "publish_reschedule":
        if err := need_item():
            return err
        try:
            ts = _resolve_publish_time(body.get("publish_at"))
        except PublishTimeError as e:
            return {"error": str(e)}, 400
        conn.execute(
            "UPDATE pending_publishes SET status='waiting', publish_after=?, error_msg=NULL, completed_at=NULL "
            "WHERE id=?", (ts, item_id),
        )
        return {"ok": True, "message": f"Rescheduled for {_fmt_when(ts)} ({_fmt_relative(ts)})."}, 200

    if action == "publish_remove":
        if err := need_item():
            return err
        conn.execute("DELETE FROM pending_publishes WHERE id=? AND status IN ('waiting','error')", (item_id,))
        return {"ok": True, "message": "Removed from the queue."}, 200

    if action == "publish_now":
        # One item (waiting, or a failed one being retried) or, without item_id, everything waiting.
        if item_id is not None:
            if err := need_item():
                return err
            conn.execute(
                "UPDATE pending_publishes SET status='waiting', error_msg=NULL, completed_at=NULL "
                "WHERE id=? AND status='error'", (item_id,),
            )
            flushed = _flush_publish_queue(conn, item_id=item_id)
        else:
            flushed = _flush_publish_queue(conn, force=True)
        return {"ok": True, "message": _summarize_flush(flushed)}, 200

    return {"error": f"unknown action: {action}"}, 400


async def _schedule_publish(args):
    photo_id = args["photo_id"]
    try:
        days_offset = int(args.get("days_offset", 0) or 0)
    except (TypeError, ValueError):
        return [TextContent(type="text", text=f"Error: days_offset must be a whole number, got {args.get('days_offset')!r}.")]
    try:
        publish_after = _resolve_publish_time(args.get("publish_at"), days_offset)
    except PublishTimeError as e:
        return [TextContent(type="text", text=f"Error: {e}")]

    with get_db() as conn:
        action = _queue_publish(conn, photo_id, publish_after)
        photo = conn.execute("SELECT is_public FROM photos WHERE id=?", (photo_id,)).fetchone()

    text = (
        f"Photo {photo_id} {action} to go public {_fmt_when(publish_after)} ({_fmt_relative(publish_after)}). "
        "Its date posted will be reset to just before then."
    )
    if photo is not None and photo["is_public"]:
        text += " Note: this photo already looks public in the local database."
    return [TextContent(type="text", text=text)]


async def _get_publish_queue(args):
    with get_db() as conn:
        flushed = _flush_publish_queue(conn)
        snap = _queue_snapshot(conn)

    def slim(r, *keys):
        return {k: r[k] for k in ("photo_id", "photo_title", *keys) if r.get(k) not in (None, "")}

    result = {
        "counts": snap["counts"],
        "waiting": [slim(r, "when", "when_relative") for r in snap["waiting"]],
        "failed": [slim(r, "message", "when") for r in snap["errors"]],
        "recently_published": [slim(r, "when", "message") for r in snap["successes"][:10]],
    }
    if flushed:
        result["just_published"] = _summarize_flush(flushed)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _cancel_scheduled_publish(args):
    photo_id = args["photo_id"]
    with get_db() as conn:
        deleted = conn.execute(
            "DELETE FROM pending_publishes WHERE photo_id=? AND status IN ('waiting','error')", (photo_id,),
        ).rowcount
    if deleted:
        return [TextContent(type="text", text=f"Photo {photo_id} is no longer scheduled to publish; it stays as-is.")]
    return [TextContent(type="text", text=f"Photo {photo_id} wasn't scheduled to publish.")]


HANDLERS = {
    "schedule_publish":         _schedule_publish,
    "get_publish_queue":        _get_publish_queue,
    "cancel_scheduled_publish": _cancel_scheduled_publish,
}
