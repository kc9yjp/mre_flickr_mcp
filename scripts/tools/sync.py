"""Sync tool definition, handler, and background refresh infrastructure.

In multi-user mode, ``_run_sync_script`` forwards ``--nsid`` / ``--username``
to the subprocess so each sync script operates on the correct per-user database
and credentials.  ``_background_refresh`` iterates over all registered users
(via ``flickr_api._all_known_users``) and triggers per-user syncs.

Concurrency model: each user has an independent ``asyncio.Lock`` (see
``_get_user_lock``).  A long-running sync for one user never blocks another
user's sync.  Within a single user, syncs are serialized so the same database
is not written by two concurrent processes.
"""

import asyncio
import logging
import os
import random
import sys
import time

from mcp.types import TextContent, Tool

from db import DB_FILE, get_db, get_db_for_user, db_file
from flickr_api import _all_known_users

SYNC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "flickr_sync.py")
MIN_REFRESH_INTERVAL = 7200   # 2 hours — earliest a user can be re-synced
REFRESH_INTERVAL = 43200      # 12 hours — hard maximum; always refresh by this point
REFRESH_CHECK_INTERVAL = 600  # 10 minutes — how often the loop wakes to check

_user_locks: dict[str, asyncio.Lock] = {}  # username -> per-user sync lock
_active_syncs: dict[str, float] = {}       # label -> start timestamp


def _get_user_lock(username: str) -> asyncio.Lock:
    """Return the per-user sync lock, creating it on first access.

    Using a per-user lock means a long sync for one user (e.g. engagement)
    never blocks another user's sync from running concurrently.
    """
    return _user_locks.setdefault(username, asyncio.Lock())

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
    """MCP tool handler: trigger a sync for the current user."""
    from db import _current_user
    user = _current_user.get()
    if not user:
        return [TextContent(type="text", text="Not authenticated. Connect via MCP with a valid API key.")]
    user_args = ["--nsid", user["nsid"], "--username", user["username"]]
    username = user["username"]

    lock = _get_user_lock(username)
    if lock.locked():
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
    async with lock:
        for label, path in targets:
            extra = list(user_args)
            if label == "photos" and args.get("full"):
                extra.append("--full")
            rc = await _run_sync_script(path, label, extra_args=extra or None, username=username)
            status = "completed" if rc == 0 else "failed"
            results.append(f"{label}: {status}")
    return [TextContent(type="text", text="\n".join(results))]


async def _run_sync_script(
    path: str,
    label: str,
    extra_args: list[str] | None = None,
    username: str | None = None,
) -> int:
    """Spawn a sync script subprocess and log its output.

    After the script exits, updates the ``duration_seconds`` column in
    ``sync_log`` for the matching entry.  Uses ``get_db_for_user(username)``
    when *username* is given, otherwise falls back to the ContextVar-aware
    ``get_db()``.

    Returns the process exit code (0 = success).
    """
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
        # Record duration — use per-user DB if username is known.
        sync_type = label.split("/")[0]  # strip "/username" suffix used in background refresh
        try:
            ctx = get_db_for_user(username) if username else get_db()
            with ctx as conn:
                conn.execute(
                    "UPDATE sync_log SET duration_seconds=? WHERE id=("
                    "SELECT id FROM sync_log WHERE type=? ORDER BY synced_at DESC LIMIT 1)",
                    (duration, sync_type),
                )
        except Exception:
            pass
        return p.returncode
    finally:
        _active_syncs.pop(label, None)


async def _background_refresh():
    """Periodically re-sync all registered users if their data is stale.

    Each user gets a stable random refresh threshold between MIN_REFRESH_INTERVAL
    (2h) and REFRESH_INTERVAL (12h), seeded by their last sync time so the
    threshold doesn't change between loop iterations.  The loop wakes at most
    every REFRESH_CHECK_INTERVAL (10 min) so no user waits more than 10 minutes
    past their due time.  Any user past the hard 12-hour max is always refreshed.
    """
    while True:
        try:
            users = _all_known_users()
            scripts_dir = os.path.dirname(SYNC_SCRIPT)
            sleep_for = REFRESH_INTERVAL

            for user in users:
                nsid = user["nsid"]
                username = user["username"]
                upath = db_file(username)
                if not os.path.exists(upath):
                    last_sync = 0
                else:
                    try:
                        with get_db_for_user(username) as conn:
                            row = conn.execute(
                                "SELECT MAX(synced_at) FROM sync_log WHERE type = 'photos'"
                            ).fetchone()
                        last_sync = row[0] if row and row[0] else 0
                    except Exception:
                        continue

                age = time.time() - last_sync
                # Stable random threshold per sync epoch: between 2h and 12h.
                # Since user_threshold <= REFRESH_INTERVAL, any user with age >= 12h
                # always satisfies age >= user_threshold and is always refreshed.
                user_threshold = random.Random(int(last_sync)).uniform(
                    MIN_REFRESH_INTERVAL, REFRESH_INTERVAL
                )
                if age >= user_threshold:
                    logging.info(
                        "Background refresh for %s (last photos sync %.1fh ago, threshold %.1fh)",
                        username, age / 3600, user_threshold / 3600,
                    )
                    user_args = ["--nsid", nsid, "--username", username]
                    async with _get_user_lock(username):
                        await _run_sync_script(
                            SYNC_SCRIPT,
                            f"photos/{username}",
                            extra_args=user_args,
                            username=username,
                        )
                        await asyncio.gather(
                            _run_sync_script(
                                os.path.join(scripts_dir, "sync_contacts.py"),
                                f"contacts/{username}",
                                extra_args=user_args,
                                username=username,
                            ),
                            _run_sync_script(
                                os.path.join(scripts_dir, "sync_groups.py"),
                                f"groups/{username}",
                                extra_args=user_args,
                                username=username,
                            ),
                            _run_sync_script(
                                os.path.join(scripts_dir, "sync_albums.py"),
                                f"albums/{username}",
                                extra_args=user_args,
                                username=username,
                            ),
                        )
                        await _run_sync_script(
                            os.path.join(scripts_dir, "sync_engagement.py"),
                            f"engagement/{username}",
                            extra_args=user_args,
                            username=username,
                        )
                else:
                    remaining = user_threshold - age
                    if remaining < sleep_for:
                        sleep_for = remaining

            # Wake at most every REFRESH_CHECK_INTERVAL so no user waits long.
            # This also handles the no-users case: sleep_for starts at REFRESH_INTERVAL
            # and is capped here to REFRESH_CHECK_INTERVAL.
            sleep_for = min(sleep_for, REFRESH_CHECK_INTERVAL)

        except Exception:
            logging.exception("Background refresh error")
            sleep_for = REFRESH_CHECK_INTERVAL

        await asyncio.sleep(sleep_for)


HANDLERS = {
    "sync": _sync,
}
