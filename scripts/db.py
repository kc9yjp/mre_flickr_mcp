# TODO: migrate in-code constants to the settings table.  Each entry in
# SETTINGS_DEFAULTS defines the key, human-readable label, description, and
# default value.  Tool code should call get_setting(conn, key) instead of
# hard-coded literals.  The /settings web page already reads and writes these
# rows; the remaining work is wiring the reads into the affected modules:
#   - scripts/tools/groups.py: _RETRY_TZ, default retry time in _parse_retry_time()
#   - scripts/tools/sync.py:   REFRESH_INTERVAL
#   - scripts/flickr_api.py:   HTTP_TIMEOUT, _API_MAX_RETRIES

"""SQLite database access — single-user and multi-user aware.

In multi-user mode, ``_current_user`` (a ContextVar) is set per SSE connection
by the web layer. ``get_db()`` reads it automatically so tool handlers require no
changes.  Sync scripts bypass the ContextVar and use ``get_db_for_user()``
directly, receiving the target username as a CLI argument.
"""

import contextvars
import logging
import os
import pathlib
import re
import sqlite3
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Settings registry
# ---------------------------------------------------------------------------

def _detect_system_tz() -> str:
    """Return the IANA timezone name from the container/host environment.

    Tries in order: TZ env var → /etc/timezone → /etc/localtime symlink → UTC.
    """
    tz = os.environ.get("TZ", "").strip()
    if tz:
        return tz
    try:
        tz = pathlib.Path("/etc/timezone").read_text().strip()
        if tz:
            return tz
    except OSError:
        pass
    try:
        parts = pathlib.Path("/etc/localtime").resolve().parts
        idx = next(i for i, p in enumerate(parts) if p == "zoneinfo")
        tz = "/".join(parts[idx + 1:])
        if tz:
            return tz
    except (OSError, StopIteration):
        pass
    return "UTC"


SETTINGS_DEFAULTS: dict[str, dict] = {
    "group_queue_retry_tz": {
        "label":       "Retry timezone",
        "description": "IANA timezone used when resolving named retry times (e.g. America/Chicago, America/New_York, UTC).",
        "default":     _detect_system_tz(),
    },
    "group_queue_default_retry": {
        "label":       "Default retry time",
        "description": "Time of day to retry queued group adds when no retry_at is specified (HH:MM, 24-hour, in retry timezone).",
        "default":     "17:00",
    },
    "sync_refresh_interval_hours": {
        "label":       "Background sync interval (hours)",
        "description": "How often the background task re-syncs each user's data. Note: this setting is not yet wired in — the background refresh currently uses a fixed 12-hour interval regardless of this value.",
        "default":     "12",
    },
}


def get_setting(conn, key: str) -> str:
    """Return the stored value for *key*, or the registered default."""
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        return row[0]
    return SETTINGS_DEFAULTS.get(key, {}).get("default", "")


def set_setting(conn, key: str, value: str) -> None:
    """Upsert *value* for *key* in the settings table."""
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Legacy single-user constant — kept for test patching and fallback.
DB_FILE = os.path.join(_DATA_DIR, "flickr.db")


_USERNAME_RE = re.compile(r"^[^/\\\x00-\x1f]{1,128}$")


def _validate_username(username: str) -> str:
    """Reject usernames that aren't safe filesystem path segments.

    ``username`` comes straight from Flickr's OAuth response with no format
    guarantee from our side, and is joined directly into a data-directory
    path. Flickr's "username" is a display name (can contain spaces/unicode
    punctuation), not a strict slug, so this only blocks what would actually
    be dangerous as a single path component: path separators, control
    characters (including null bytes), and the special "." / ".." names —
    not an alnum-only allowlist that could reject a legitimate account.
    """
    if not username or username in (".", "..") or not _USERNAME_RE.fullmatch(username):
        raise ValueError(f"Invalid username for database path: {username!r}")
    return username


def _owner_marker_file(username: str) -> str:
    return os.path.join(_DATA_DIR, username, ".owner_nsid")


def check_dir_ownership(username: str, nsid: str | None) -> None:
    """Guard against two different Flickr accounts colliding on the same
    per-user data directory.

    Flickr usernames are mutable (a user can rename) and can be reused
    after being released, unlike the immutable NSID — so keying local
    storage on username alone risks a renamed/reused username silently
    reading or writing a *different* account's cached photos, contacts,
    and comments. The first request for a given username records which
    nsid owns it (see ``seed_dir_ownership``); any later request for the
    same username under a *different* nsid is refused here instead of
    silently served.

    A no-op when *nsid* is unknown (call sites that haven't been wired up
    to pass it yet) or when the directory doesn't exist yet / has no
    marker (nothing to conflict with — ``seed_dir_ownership`` claims it).
    """
    if not nsid:
        return
    try:
        with open(_owner_marker_file(username), "r", encoding="utf-8") as f:
            owner = f.read().strip()
    except FileNotFoundError:
        return
    if owner and owner != nsid:
        logging.error(
            "db: refusing data directory for username %r — owned by nsid %s, "
            "requested by nsid %s (likely a reused/renamed Flickr username)",
            username, owner, nsid,
        )
        raise RuntimeError(
            f"Data directory for username {username!r} belongs to a different "
            "Flickr account; refusing to open it."
        )


def seed_dir_ownership(username: str, nsid: str | None) -> None:
    """Record *nsid* as the owner of *username*'s data directory, if not
    already recorded. Call after the directory is known to exist, before
    or after creating the database file — order doesn't matter since this
    only ever writes the marker once per username."""
    if not nsid:
        return
    marker = _owner_marker_file(username)
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as f:
            f.write(nsid)


def db_file(username: str) -> str:
    """Return the SQLite database path for *username*.

    Creates the per-user subdirectory: ``data/{username}/flickr.db``.
    """
    _validate_username(username)
    return os.path.join(_DATA_DIR, username, "flickr.db")


# ---------------------------------------------------------------------------
# Per-request user context
# ---------------------------------------------------------------------------

_current_user: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "_current_user", default=None
)
"""ContextVar holding the active user dict (``{"nsid": ..., "username": ...}``).

Set by the SSE handler at connection time so that ``get_db()`` and
``flickr_api._load_credentials()`` resolve the correct per-user paths without
any changes to tool handler call sites.
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _connect(path):
    """Shared connection factory for all MCP tool DB access.

    sqlite3.connect's default timeout=5.0 already installs a 5000ms busy
    handler, so no explicit busy_timeout PRAGMA is needed.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def like_pattern(term: str) -> str:
    """Return a %-wrapped LIKE pattern with %, _ and \\ escaped.

    Use together with ``LIKE ? ESCAPE '\\'`` so user input can't act as
    wildcards.
    """
    escaped = term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def table_empty(conn, table: str) -> bool:
    """True if *table* has no rows.  *table* must be a trusted literal."""
    return conn.execute(f"SELECT NOT EXISTS(SELECT 1 FROM {table})").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Context managers
# ---------------------------------------------------------------------------

def db():
    """Return a raw connection to the active database (legacy helper).

    Prefer ``get_db()`` for new code.  This function is kept for backward
    compatibility with code that opens the connection manually.
    """
    user = _current_user.get()
    path = db_file(user["username"]) if user else DB_FILE
    if user:
        check_dir_ownership(user["username"], user.get("nsid"))
    if not os.path.exists(path):
        raise FileNotFoundError("Database not found. Visit http://localhost:8000/sync to run a sync.")
    return _connect(path)


@contextmanager
def get_db():
    """Context manager that opens the database for the current user.

    Resolves the database path from ``_current_user`` if set, otherwise falls
    back to the single-user ``DB_FILE`` constant (useful during tests).
    Commits on clean exit and rolls back on exception.
    """
    user = _current_user.get()
    path = db_file(user["username"]) if user else DB_FILE
    if user:
        check_dir_ownership(user["username"], user.get("nsid"))
    if not os.path.exists(path):
        who = user["username"] if user else "unknown"
        raise FileNotFoundError(
            f"Database not found for {who}. Visit http://localhost:8000/sync to run a sync."
        )
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_db_for_user(username: str, nsid: str | None = None):
    """Context manager that opens the database for an explicit *username*.

    Used by sync scripts and web/API handlers that know the target user
    without relying on the ``_current_user`` ContextVar. Creates the
    per-user data directory if it does not yet exist.

    *nsid*, when given, is checked against (and seeded into) the
    directory's ownership marker — see ``check_dir_ownership`` — so a
    Flickr username that was renamed away and reclaimed by a different
    account can't silently resolve to the previous owner's data. Falls
    back to ``_current_user``'s nsid when not passed explicitly and that
    context happens to be for the same username.

    Commits on clean exit and rolls back on exception.
    """
    if nsid is None:
        ctx_user = _current_user.get()
        if ctx_user and ctx_user.get("username") == username:
            nsid = ctx_user.get("nsid")
    path = db_file(username)
    check_dir_ownership(username, nsid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seed_dir_ownership(username, nsid)
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
