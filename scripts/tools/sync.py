"""Sync tool definition, handler, and background refresh infrastructure."""

import asyncio
import logging
import os
import sys
import time

from mcp.types import TextContent, Tool

from db import DB_FILE, get_db

SYNC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "flickr_sync.py")
REFRESH_INTERVAL = 43200  # 12 hours

_sync_lock = asyncio.Lock()
_active_syncs: dict[str, float] = {}  # label -> start timestamp

TOOLS = [
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
]


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


async def _run_sync_script(path: str, label: str, extra_args: list[str] | None = None) -> int:
    started = time.time()
    _active_syncs[label] = started
    logging.info("Sync starting: %s", label)
    try:
        p = await asyncio.create_subprocess_exec(
            sys.executable, path, *(extra_args or []),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await p.communicate()
        for line in stdout.decode().splitlines():
            if line.strip():
                logging.info("[%s] %s", label, line)
        duration = int(time.time() - started)
        if p.returncode != 0:
            logging.error("Sync failed: %s (exit %s, %ds)", label, p.returncode, duration)
        else:
            logging.info("Sync completed: %s (%ds)", label, duration)
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE sync_log SET duration_seconds=? WHERE id=("
                    "SELECT id FROM sync_log WHERE type=? ORDER BY synced_at DESC LIMIT 1)",
                    (duration, label),
                )
        except Exception:
            pass
        return p.returncode
    finally:
        _active_syncs.pop(label, None)


async def _background_refresh():
    """Check daily whether photo/contact/group data needs refreshing and sync if so."""
    while True:
        try:
            if os.path.exists(DB_FILE):
                with get_db() as conn:
                    row = conn.execute("SELECT MAX(synced_at) FROM sync_log WHERE type = 'photos'").fetchone()
                last_sync = row[0] if row and row[0] else 0
                age = time.time() - last_sync
                if age >= REFRESH_INTERVAL:
                    logging.info("Background refresh triggered (last photos sync %.1fh ago)", age / 3600)
                    scripts_dir = os.path.dirname(SYNC_SCRIPT)
                    async with _sync_lock:
                        await _run_sync_script(SYNC_SCRIPT, "photos")
                        await asyncio.gather(
                            _run_sync_script(os.path.join(scripts_dir, "sync_contacts.py"), "contacts"),
                            _run_sync_script(os.path.join(scripts_dir, "sync_groups.py"),   "groups"),
                            _run_sync_script(os.path.join(scripts_dir, "sync_albums.py"),   "albums"),
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


HANDLERS = {
    "sync": _sync,
}
