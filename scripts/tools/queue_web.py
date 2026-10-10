"""Workbench (web UI) side of the two deferred-work queues.

webapi.py's queue endpoints go through here: the scheduled-publish queue's
helpers live in tools/publish_queue.py, and this module adds the matching
snapshot/actions for the group-add queue (``pending_group_adds``, flushed by
tools/groups.py) so both Workbench pages can offer the same cards and
buttons. Kept out of groups.py so it can reuse publish_queue's time parsing
without an import cycle (publish_queue already imports from groups).
"""

from tools.groups import _flush_group_queue
from tools.publish_queue import (
    PublishTimeError, _fmt_relative, _fmt_when, _queue_snapshot as publish_snapshot,
    _resolve_publish_time, _web_action as _publish_action,
)

__all__ = ["group_snapshot", "publish_snapshot", "web_action"]


def group_snapshot(conn) -> dict:
    """Counts plus waiting/error/recent-success group adds, shaped like publish_snapshot rows."""
    counts = {"waiting": 0, "success": 0, "error": 0}
    counts.update({r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM pending_group_adds GROUP BY status"
    ).fetchall()})

    def rows(where_order: str) -> list[dict]:
        out = []
        for r in conn.execute(
            "SELECT pga.*, p.title AS photo_title, p.url_photopage AS photo_url, p.url_medium AS thumb_url, "
            "       g.name AS group_name "
            "FROM pending_group_adds pga "
            "LEFT JOIN photos p ON pga.photo_id = p.id "
            "LEFT JOIN groups g ON pga.group_id = g.id " + where_order
        ).fetchall():
            ts = r["retry_after"] if r["status"] == "waiting" else (r["completed_at"] or r["queued_at"])
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
                "group_id":    r["group_id"],
                "group_name":  r["group_name"] or r["group_id"],
                "group_url":   f"https://www.flickr.com/groups/{r['group_id']}/pool/",
            })
        return out

    return {
        "counts":    counts,
        "waiting":   rows("WHERE pga.status='waiting' ORDER BY pga.retry_after ASC"),
        "errors":    rows("WHERE pga.status='error' ORDER BY COALESCE(pga.completed_at, pga.queued_at) DESC LIMIT 30"),
        "successes": rows("WHERE pga.status='success' ORDER BY pga.completed_at DESC LIMIT 20"),
    }


def _summarize_group_flush(flushed: list[dict]) -> str:
    if not flushed:
        return "Nothing was due."
    ok = sum(1 for r in flushed if r["result"] == "success")
    pending = sum(1 for r in flushed if r["result"] == "pending_approval")
    already = sum(1 for r in flushed if r["result"] == "already_in_pool")
    limited = sum(1 for r in flushed if r["result"] == "still_limited")
    err = sum(1 for r in flushed if r["result"].startswith("error"))
    parts = [f"{ok} added"]
    if pending:
        parts.append(f"{pending} submitted and awaiting the group admins' approval")
    if already:
        parts.append(f"{already} already in the pool")
    if limited:
        parts.append(f"{limited} still at the group's daily limit (will retry after midnight UTC)")
    if err:
        parts.append(f"{err} failed")
    return ", ".join(parts) + "."


def _group_action(conn, body: dict) -> tuple[dict, int]:
    action = body.get("action", "")
    item_id = body.get("item_id")

    def need_item():
        if not isinstance(item_id, int):
            return {"error": "item_id required"}, 400
        if not conn.execute("SELECT 1 FROM pending_group_adds WHERE id=?", (item_id,)).fetchone():
            return {"error": "That item is no longer in the queue."}, 404
        return None

    if action == "group_reschedule":
        if err := need_item():
            return err
        try:
            ts = _resolve_publish_time(body.get("publish_at"))
        except PublishTimeError as e:
            return {"error": str(e)}, 400
        conn.execute(
            "UPDATE pending_group_adds SET status='waiting', retry_after=?, error_msg=NULL, completed_at=NULL "
            "WHERE id=?", (ts, item_id),
        )
        return {"ok": True, "message": f"Rescheduled for {_fmt_when(ts)} ({_fmt_relative(ts)})."}, 200

    if action == "group_remove":
        if err := need_item():
            return err
        conn.execute("DELETE FROM pending_group_adds WHERE id=? AND status IN ('waiting','error')", (item_id,))
        return {"ok": True, "message": "Removed from the queue."}, 200

    if action == "group_post_now":
        # One item (waiting, or a failed one being retried) or, without item_id, everything waiting.
        if item_id is not None:
            if err := need_item():
                return err
            conn.execute(
                "UPDATE pending_group_adds SET status='waiting', error_msg=NULL, completed_at=NULL "
                "WHERE id=? AND status='error'", (item_id,),
            )
            flushed = _flush_group_queue(conn, item_id=item_id)
        else:
            flushed = _flush_group_queue(conn, force=True)
        return {"ok": True, "message": _summarize_group_flush(flushed)}, 200

    if action == "group_post_due":
        return {"ok": True, "message": _summarize_group_flush(_flush_group_queue(conn))}, 200

    return {"error": f"unknown action: {action}"}, 400


def web_action(conn, body: dict) -> tuple[dict, int]:
    """Dispatch a Workbench queue action (publish_* or group_*). Returns (json_body, http_status)."""
    action = str(body.get("action", ""))
    if action.startswith("publish_"):
        return _publish_action(conn, body)
    if action.startswith("group_"):
        return _group_action(conn, body)
    return {"error": f"unknown action: {action}"}, 400
