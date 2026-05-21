"""SQLite database access — single-user and multi-user aware.

In multi-user mode, ``_current_user`` (a ContextVar) is set per SSE connection
by the web layer. ``get_db()`` reads it automatically so tool handlers require no
changes.  Sync scripts bypass the ContextVar and use ``get_db_for_user()``
directly, receiving the target username as a CLI argument.
"""

import contextvars
import os
import sqlite3
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Legacy single-user constant — kept for test patching and fallback.
DB_FILE = os.path.join(_DATA_DIR, "flickr.db")


def db_file(username: str) -> str:
    """Return the SQLite database path for *username*.

    Creates the per-user subdirectory: ``data/{username}/flickr.db``.
    """
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
# Context managers
# ---------------------------------------------------------------------------

def db():
    """Return a raw connection to the active database (legacy helper).

    Prefer ``get_db()`` for new code.  This function is kept for backward
    compatibility with code that opens the connection manually.
    """
    user = _current_user.get()
    path = db_file(user["username"]) if user else DB_FILE
    if not os.path.exists(path):
        raise FileNotFoundError("Database not found. Visit http://localhost:8000/sync to run a sync.")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager that opens the database for the current user.

    Resolves the database path from ``_current_user`` if set, otherwise falls
    back to the single-user ``DB_FILE`` constant (useful during tests).
    Commits on clean exit and rolls back on exception.
    """
    user = _current_user.get()
    path = db_file(user["username"]) if user else DB_FILE
    if not os.path.exists(path):
        who = user["username"] if user else "unknown"
        raise FileNotFoundError(
            f"Database not found for {who}. Visit http://localhost:8000/sync to run a sync."
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_db_for_user(username: str):
    """Context manager that opens the database for an explicit *username*.

    Used by sync scripts that receive ``--username`` as a CLI argument and
    therefore know the target user without relying on the ContextVar.
    Creates the per-user data directory if it does not yet exist.
    Commits on clean exit and rolls back on exception.
    """
    path = db_file(username)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
