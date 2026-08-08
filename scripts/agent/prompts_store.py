"""User-editable LLM prompt storage.

Kept in ``~/.flickr_mcp/{nsid}/prompts.db`` — next to ``llm.json`` and the
OAuth credentials, deliberately outside ``flickr.db`` so editing prompts
survives a "Reset Database" (which deletes the whole ``flickr.db`` file).

Three tables: ``prompt_categories`` (some shipped, user can add more),
``prompts`` (named/described templates, each in one category, with an
optional ``{photo_id}``/``{user_nsid}``-style placeholder convention matched
by ``prompt_variables``, a reference catalog of what those placeholders mean
and where they get substituted).

Builtin rows (``builtin=1``) ship with a ``default_text``/definition so they
can be reset; they can't be deleted, only edited or reset.
"""

import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from flickr_api import _CREDS_BASE

from agent import settings as _agent_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_categories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    builtin     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prompts (
    id           TEXT PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    description  TEXT,
    category_id  TEXT NOT NULL REFERENCES prompt_categories(id),
    context      TEXT NOT NULL DEFAULT 'global',
    text         TEXT NOT NULL,
    builtin      INTEGER NOT NULL DEFAULT 0,
    default_text TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS prompt_variables (
    code        TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT,
    resolved_by TEXT NOT NULL DEFAULT 'server',
    builtin     INTEGER NOT NULL DEFAULT 0
);
"""

# Schema migrations — each is a (check_sql, alter_sql) pair, applied on every
# connection open (mirrors agent/store.py; this file is small enough that a
# PRAGMA user_version cursor like flickr_sync.py's would be overkill).
_MIGRATIONS: list[tuple[str, str]] = []

# ---------------------------------------------------------------------------
# Builtin prompt defaults — sourced from default-prompts.md
# ---------------------------------------------------------------------------
#
# ``default-prompts.md`` (repo root, copied into the Docker image alongside
# ``scripts/`` — see Dockerfile) is the single source of truth for every
# builtin prompt's category, name, description, and text. To change a
# default, edit that file — ``_parse_default_prompts_md`` below regenerates
# ``_SEED_CATEGORIES``/``_SEED_PROMPTS`` from it on import, so there's
# nothing to hand-sync here.
#
# ``_sync_builtin_defaults`` (further down) is what actually gets an edited
# default in front of existing users: it diffs the freshly parsed
# ``default_text`` against what's stored, and only overwrites a user's live
# ``text`` if they hadn't already diverged it from the old default.

_DEFAULT_PROMPTS_MD_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "default-prompts.md")
)

# Maps each "## " section heading in default-prompts.md to the stable
# category id stored in the database. Lives here, not in the markdown,
# because it's a code-level identifier rather than user-facing content.
_CATEGORY_IDS = {
    "System": "system",
    "My Photo": "own_photo",
    "Someone Else's Photo": "other_photo",
    "My Collection": "collection",
}

# Same shape as the frontend's own parser (parseExportedMarkdown in
# frontend/src/panels/PromptsSection.tsx) — that panel's "Export as
# Markdown" button produces exactly this layout, and "Import from Markdown"
# reads it back in. Keep the two in sync if the format ever changes.
_PROMPT_META_RE = re.compile(r"^\*Code: `([^`]+)` — Context: (\w+)\*$")


def _parse_default_prompts_md(path: str) -> tuple[list[tuple], list[dict]]:
    """Parse default-prompts.md into (_SEED_CATEGORIES, _SEED_PROMPTS) shape.

    ``user-memory``'s shipped default is always blank, regardless of what
    the fence under "Standing memory" shows — that fence documents what
    accumulated guidance looks like, it isn't meant to ship as everyone's
    starting memory.
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    n = len(lines)
    categories: list[tuple] = []
    prompts: list[dict] = []
    current_category: str | None = None
    current_cat_id: str | None = None
    category_desc_lines: list[str] = []
    cat_sort = 0

    def flush_category():
        nonlocal cat_sort
        if current_category is not None:
            categories.append((current_cat_id, current_category,
                                "\n".join(category_desc_lines).strip(), cat_sort))
            cat_sort += 1

    i = 0
    while i < n:
        line = lines[i]
        if line.startswith("## "):
            flush_category()
            current_category = line[3:].strip()
            current_cat_id = _CATEGORY_IDS.get(current_category)
            if current_cat_id is None:
                raise ValueError(
                    f"default-prompts.md: unknown category heading {current_category!r}"
                )
            category_desc_lines = []
            i += 1
            while i < n and not lines[i].startswith("### ") and not lines[i].startswith("## "):
                category_desc_lines.append(lines[i])
                i += 1
            continue
        if line.startswith("### ") and current_category is not None:
            name = line[4:].strip()
            i += 1
            meta = _PROMPT_META_RE.match(lines[i]) if i < n else None
            code, context = "", "global"
            if meta:
                code, context = meta.group(1), meta.group(2)
                i += 1
            desc_lines: list[str] = []
            while i < n and lines[i].strip() != "```":
                desc_lines.append(lines[i])
                i += 1
            if i < n and lines[i].strip() == "```":
                i += 1
            text_lines: list[str] = []
            while i < n and lines[i].strip() != "```":
                text_lines.append(lines[i])
                i += 1
            if i < n and lines[i].strip() == "```":
                i += 1
            if not code:
                raise ValueError(
                    f"default-prompts.md: prompt {name!r} is missing its "
                    "'*Code: `...` — Context: ...*' line"
                )
            text = "\n".join(text_lines).strip()
            if code == "user-memory":
                text = ""
            prompts.append(dict(code=code, name=name, category_id=current_cat_id,
                                 context=context,
                                 description="\n".join(desc_lines).strip(), text=text))
            continue
        i += 1
    flush_category()
    return categories, prompts


_SEED_CATEGORIES, _SEED_PROMPTS = _parse_default_prompts_md(_DEFAULT_PROMPTS_MD_PATH)

# Kept as module-level constants for the handful of callers that reference a
# single builtin default directly rather than going through _SEED_PROMPTS:
# agent/loop.py (system prompt fallback), agent/compact.py (compaction
# fallback), scripts/flickr_sync.py (group summary fallback).
_prompt_defaults_by_code = {p["code"]: p["text"] for p in _SEED_PROMPTS}
SYSTEM_PROMPT_DEFAULT = _prompt_defaults_by_code["system-core"]
GROUP_SUMMARY_PROMPT_DEFAULT = _prompt_defaults_by_code["group-summary"]
COMPACT_PROMPT_DEFAULT = _prompt_defaults_by_code["compact-conversation"]

# Categories aren't user-editable — a fixed, known set, so the system (not
# the user) can use a prompt's category to decide which page its workflow
# button belongs on. "photo" categories surface in the Photo Viewer panel;
# everything else surfaces in the global Chat/Command Palette.
_CATEGORY_CONTEXT = {
    "system": "global",
    "own_photo": "photo",
    "other_photo": "photo",
    "collection": "global",
}


def _context_for_category(category_id: str) -> str:
    return _CATEGORY_CONTEXT.get(category_id, "global")


_SEED_VARIABLES = [
    ("photo_id", "Photo ID", "The photo in context. Substituted client-side "
     "from the selected photo before the prompt is sent.", "client"),
    ("user_nsid", "Your Flickr NSID", "Your own Flickr user ID — the stable "
     "identifier that never changes even if your username does. Substituted "
     "server-side from your logged-in credentials.", "server"),
    ("username", "Your Flickr username", "Your current Flickr username/URL "
     "path alias. This can change over time (renaming your account), so old "
     "links or messages may reference a previous username for the same "
     "account. Substituted server-side from your logged-in credentials.",
     "server"),
    ("group_name", "Group name", "The joined group's name. Substituted by "
     "the background groups sync before calling the LLM.", "server"),
    ("group_description", "Group description", "The joined group's Flickr "
     "description (rules/restrictions). Substituted by the background "
     "groups sync before calling the LLM.", "server"),
    ("group_user_note", "Your note about the group", "Your own freeform "
     "note about the group (set via the set_group_note tool). Substituted "
     "by the background groups sync before calling the LLM.", "server"),
]


def _prompts_db_path(nsid: str) -> str:
    return os.path.join(_CREDS_BASE, nsid, "prompts.db")


# Per-nsid locks serializing all access to that user's prompts.db within
# this process — see the comment in _prompts_db for why.
_db_locks: dict[str, threading.Lock] = {}
_db_locks_meta_lock = threading.Lock()


def _lock_for(nsid: str) -> threading.Lock:
    with _db_locks_meta_lock:
        lock = _db_locks.get(nsid)
        if lock is None:
            lock = _db_locks[nsid] = threading.Lock()
        return lock


@contextmanager
def _prompts_db(nsid: str):
    # _seed_defaults/_sync_builtin_defaults run on every open (see their own
    # docstrings for why — new builtins must reach existing users on their
    # next load, not just brand-new ones) and are near-instant no-ops in the
    # common case. Right after a default-prompts.md edit changes many
    # builtins' default_text at once though, they briefly become real,
    # repeated write work, and a page load firing several /api/* calls at
    # once for the same user could race for the write lock and hit "database
    # is locked" before either finished. Serializing per nsid — rather than
    # skipping the check once it's been done, which would also mean a
    # not-yet-restarted process could miss a change made *after* its first
    # request — removes the possibility of that race outright: it just
    # means the second request waits a beat instead of erroring.
    with _lock_for(nsid):
        path = _prompts_db_path(nsid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets concurrent readers proceed without waiting on a writer,
        # unlike the default rollback journal — defense in depth alongside
        # the per-nsid lock above (which only covers this process; nothing
        # stops e.g. a second process touching the same file).
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.executescript(_SCHEMA)
        for check_sql, alter_sql in _MIGRATIONS:
            if conn.execute(check_sql).fetchone() is None:
                conn.execute(alter_sql)
        _seed_defaults(conn, nsid)
        _sync_builtin_defaults(conn)
        # Commit the seed/sync writes now rather than leaving them pending
        # until the caller's own work finishes below.
        conn.commit()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _seed_defaults(conn: sqlite3.Connection, nsid: str) -> None:
    """Insert builtin categories/prompts/variables if this is a fresh DB.

    Carries over an existing user's ``base_prompt`` (from llm.json) into the
    new ``user-memory`` row so no one's saved memory is lost in the move.
    """
    if conn.execute("SELECT 1 FROM prompt_categories LIMIT 1").fetchone():
        return
    now = int(time.time())
    conn.executemany(
        "INSERT INTO prompt_categories (id, name, description, sort_order, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_CATEGORIES,
    )
    conn.executemany(
        "INSERT INTO prompt_variables (code, label, description, resolved_by, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_VARIABLES,
    )

    legacy_memory = ""
    try:
        legacy_memory = (_agent_settings.load_settings(nsid).get("base_prompt") or "").strip()
    except Exception:
        pass

    for i, p in enumerate(_SEED_PROMPTS):
        text = p["text"]
        if p["code"] == "user-memory" and legacy_memory:
            text = legacy_memory
        conn.execute(
            "INSERT INTO prompts (id, code, name, description, category_id, context, "
            "text, builtin, default_text, enabled, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?, ?)",
            (uuid.uuid4().hex, p["code"], p["name"], p.get("description", ""),
             p["category_id"], p["context"], text, p["text"], i, now, now),
        )


def _sync_builtin_defaults(conn: sqlite3.Connection) -> None:
    """Keep builtin category/prompt/variable definitions in step with code changes.

    ``_seed_defaults`` only inserts rows once, when a user's DB is brand new —
    it never revisits an existing DB, so a category, prompt, or variable added
    in a later release would otherwise never reach users who logged in before
    that release. Runs on every connection open:
    - Inserts any builtin category in ``_SEED_CATEGORIES`` or variable in
      ``_SEED_VARIABLES`` missing from an existing DB (``INSERT OR IGNORE``,
      so it's a no-op once present) — categories first, since prompts below
      reference them.
    - Inserts any builtin prompt in ``_SEED_PROMPTS`` whose code doesn't
      exist yet for this user (same shape ``_seed_defaults`` would have used
      for a brand-new user), so a new builtin prompt reaches existing users
      too instead of only ever appearing for accounts created after it shipped.
    - Refreshes a builtin prompt's ``default_text`` whenever the shipped
      default in ``_SEED_PROMPTS`` changes, and carries that change into the
      live ``text`` too — but only if the user hasn't diverged their own
      edited ``text`` away from the old default already.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO prompt_categories (id, name, description, sort_order, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_CATEGORIES,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO prompt_variables (code, label, description, resolved_by, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_VARIABLES,
    )
    now = int(time.time())
    for p in _SEED_PROMPTS:
        row = conn.execute(
            "SELECT text, default_text FROM prompts WHERE code = ? AND builtin = 1",
            (p["code"],),
        ).fetchone()
        if row is None:
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM prompts WHERE category_id = ?",
                (p["category_id"],),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO prompts (id, code, name, description, category_id, context, "
                "text, builtin, default_text, enabled, sort_order, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?, ?)",
                (uuid.uuid4().hex, p["code"], p["name"], p.get("description", ""),
                 p["category_id"], p["context"], p["text"], p["text"], sort_order, now, now),
            )
            continue
        if row["default_text"] == p["text"]:
            continue
        new_text = p["text"] if row["text"] == row["default_text"] else row["text"]
        conn.execute(
            "UPDATE prompts SET text = ?, default_text = ?, updated_at = ? WHERE code = ?",
            (new_text, p["text"], now, p["code"]),
        )


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for key in ("builtin", "enabled"):
        if key in d:
            d[key] = bool(d[key])
    return d


# ── Categories ────────────────────────────────────────────────────────────


def list_categories(nsid: str) -> list[dict]:
    with _prompts_db(nsid) as conn:
        rows = conn.execute(
            "SELECT * FROM prompt_categories ORDER BY sort_order, name"
        ).fetchall()
    return [_row(r) for r in rows]


def create_category(nsid: str, name: str, description: str = "") -> dict:
    cat_id = uuid.uuid4().hex
    with _prompts_db(nsid) as conn:
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM prompt_categories"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO prompt_categories (id, name, description, sort_order, builtin) "
            "VALUES (?, ?, ?, ?, 0)",
            (cat_id, name, description, sort_order),
        )
    return {"id": cat_id, "name": name, "description": description,
            "sort_order": sort_order, "builtin": False}


def update_category(nsid: str, category_id: str, name: str | None = None,
                     description: str | None = None) -> dict | None:
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT * FROM prompt_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE prompt_categories SET name = ?, description = ? WHERE id = ?",
            (name if name is not None else row["name"],
             description if description is not None else row["description"],
             category_id),
        )
        updated = conn.execute(
            "SELECT * FROM prompt_categories WHERE id = ?", (category_id,)
        ).fetchone()
    return _row(updated)


def delete_category(nsid: str, category_id: str) -> tuple[bool, str | None]:
    """Returns (ok, error). Refuses builtin categories or ones still in use."""
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT builtin FROM prompt_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            return False, "category not found"
        if row["builtin"]:
            return False, "built-in categories can't be deleted"
        in_use = conn.execute(
            "SELECT 1 FROM prompts WHERE category_id = ? LIMIT 1", (category_id,)
        ).fetchone()
        if in_use:
            return False, "category still has prompts assigned to it"
        conn.execute("DELETE FROM prompt_categories WHERE id = ?", (category_id,))
    return True, None


# ── Prompts ───────────────────────────────────────────────────────────────


def list_prompts(nsid: str, category_id: str | None = None) -> list[dict]:
    with _prompts_db(nsid) as conn:
        if category_id:
            rows = conn.execute(
                "SELECT * FROM prompts WHERE category_id = ? ORDER BY sort_order, name",
                (category_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM prompts ORDER BY category_id, sort_order, name"
            ).fetchall()
    return [_row(r) for r in rows]


def get_prompt(nsid: str, prompt_id: str) -> dict | None:
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
    return _row(row) if row else None


def get_prompt_by_code(nsid: str, code: str) -> dict | None:
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE code = ?", (code,)).fetchone()
    return _row(row) if row else None


def create_prompt(nsid: str, code: str, name: str, category_id: str,
                   text: str, description: str = "") -> dict:
    prompt_id = uuid.uuid4().hex
    now = int(time.time())
    context = _context_for_category(category_id)
    with _prompts_db(nsid) as conn:
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM prompts WHERE category_id = ?",
            (category_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO prompts (id, code, name, description, category_id, context, "
            "text, builtin, default_text, enabled, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, 1, ?, ?, ?)",
            (prompt_id, code, name, description, category_id, context, text,
             sort_order, now, now),
        )
    return get_prompt(nsid, prompt_id)


def update_prompt(nsid: str, prompt_id: str, **fields) -> dict | None:
    """Update any of name/description/category_id/text/enabled.

    ``context`` isn't user-settable — it's derived from ``category_id``
    whenever the category changes, so the system (not the user) decides
    which page a prompt's workflow button belongs on.
    """
    allowed = {"name", "description", "category_id", "text", "enabled"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_prompt(nsid, prompt_id)
    if "category_id" in updates:
        updates["context"] = _context_for_category(updates["category_id"])
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
        if not row:
            return None
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE prompts SET {set_clause}, updated_at = ? WHERE id = ?",
            (*updates.values(), int(time.time()), prompt_id),
        )
    return get_prompt(nsid, prompt_id)


def delete_prompt(nsid: str, prompt_id: str) -> tuple[bool, str | None]:
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT builtin FROM prompts WHERE id = ?", (prompt_id,)
        ).fetchone()
        if not row:
            return False, "prompt not found"
        if row["builtin"]:
            return False, "built-in prompts can't be deleted — use reset instead"
        conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
    return True, None


def reset_prompt(nsid: str, prompt_id: str) -> dict | None:
    """Restore a builtin prompt's text to its shipped default_text."""
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
        if not row or not row["builtin"]:
            return None
        conn.execute(
            "UPDATE prompts SET text = default_text, updated_at = ? WHERE id = ?",
            (int(time.time()), prompt_id),
        )
    return get_prompt(nsid, prompt_id)


def reset_all_prompts(nsid: str, include_user_memory: bool = False) -> list[dict]:
    """Restore every builtin prompt's text to its shipped default_text.

    ``user-memory``'s default_text is always blank, so resetting it wipes
    out the user's accumulated `remember`-tool guidance — it's skipped
    unless *include_user_memory* is explicitly set, which the "reset all"
    UI only does after a separate, explicit confirmation.
    """
    now = int(time.time())
    with _prompts_db(nsid) as conn:
        rows = conn.execute("SELECT id, code FROM prompts WHERE builtin = 1").fetchall()
        for row in rows:
            if row["code"] == "user-memory" and not include_user_memory:
                continue
            conn.execute(
                "UPDATE prompts SET text = default_text, updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
    return list_prompts(nsid)


def append_user_memory(nsid: str, guidance: str) -> str:
    """Append guidance to the user-memory prompt's text. Returns the new text."""
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT id, text FROM prompts WHERE code = 'user-memory'"
        ).fetchone()
        existing = (row["text"] or "").strip() if row else ""
        updated = (existing + "\n" + guidance).strip() if existing else guidance
        conn.execute(
            "UPDATE prompts SET text = ?, updated_at = ? WHERE id = ?",
            (updated, int(time.time()), row["id"]),
        )
    return updated


# ── Variables ─────────────────────────────────────────────────────────────


def resolve_server_variables(text: str, nsid: str, username: str) -> str:
    """Substitute server-resolved placeholders ({user_nsid}, {username}) into *text*.

    Shared by the system prompt (agent/loop.py) and the workflow command list
    (agent/commands.py) so both stay in sync with prompt_variables' contract.
    """
    return text.replace("{user_nsid}", nsid).replace("{username}", username)


def list_variables(nsid: str) -> list[dict]:
    with _prompts_db(nsid) as conn:
        rows = conn.execute("SELECT * FROM prompt_variables ORDER BY code").fetchall()
    return [_row(r) for r in rows]


def create_variable(nsid: str, code: str, label: str, description: str = "") -> dict:
    with _prompts_db(nsid) as conn:
        conn.execute(
            "INSERT INTO prompt_variables (code, label, description, resolved_by, builtin) "
            "VALUES (?, ?, ?, 'documentation only', 0)",
            (code, label, description),
        )
    return {"code": code, "label": label, "description": description,
            "resolved_by": "documentation only", "builtin": False}


def delete_variable(nsid: str, code: str) -> tuple[bool, str | None]:
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT builtin FROM prompt_variables WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return False, "variable not found"
        if row["builtin"]:
            return False, "built-in variables can't be deleted"
        conn.execute("DELETE FROM prompt_variables WHERE code = ?", (code,))
    return True, None


# ── Bulk fetch for the API ───────────────────────────────────────────────


def all_data(nsid: str) -> dict:
    return {
        "categories": list_categories(nsid),
        "prompts": list_prompts(nsid),
        "variables": list_variables(nsid),
    }
