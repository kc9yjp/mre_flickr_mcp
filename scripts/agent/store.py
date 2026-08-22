"""Chat conversation persistence.

Kept in ``data/{username}/chat.db`` — a separate file from ``flickr.db`` so
the /reset (delete database) flow preserves chat history.  Messages are
stored in OpenAI wire format so history replays losslessly.
"""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

import db as _db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);
CREATE TABLE IF NOT EXISTS llm_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    message_seq     INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    provider        TEXT,
    model           TEXT,
    request_url     TEXT,
    request_json    TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    finish_reason   TEXT,
    usage_json      TEXT,
    latency_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_conv ON llm_calls(conversation_id, message_seq);
"""

# Schema migrations — each is a (check_sql, alter_sql) pair.  The check
# returns a non-empty Row when the column is missing and the alter adds it.
_MIGRATIONS = [
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='provider'",
        "ALTER TABLE conversations ADD COLUMN provider TEXT DEFAULT ''",
    ),
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='model'",
        "ALTER TABLE conversations ADD COLUMN model TEXT DEFAULT ''",
    ),
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='turns'",
        "ALTER TABLE conversations ADD COLUMN turns INTEGER DEFAULT 0",
    ),
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='prompt_tokens'",
        "ALTER TABLE conversations ADD COLUMN prompt_tokens INTEGER DEFAULT 0",
    ),
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='completion_tokens'",
        "ALTER TABLE conversations ADD COLUMN completion_tokens INTEGER DEFAULT 0",
    ),
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='total_tokens'",
        "ALTER TABLE conversations ADD COLUMN total_tokens INTEGER DEFAULT 0",
    ),
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='total_latency_ms'",
        "ALTER TABLE conversations ADD COLUMN total_latency_ms INTEGER DEFAULT 0",
    ),
    (
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name='last_prompt_tokens'",
        "ALTER TABLE conversations ADD COLUMN last_prompt_tokens INTEGER DEFAULT 0",
    ),
]

_STATS_COLUMNS = (
    "turns", "prompt_tokens", "completion_tokens",
    "total_tokens", "total_latency_ms", "last_prompt_tokens",
)


def _chat_db_path(username: str) -> str:
    return os.path.join(_db._DATA_DIR, username, "chat.db")


@contextmanager
def _chat_db(username: str):
    path = _chat_db_path(username)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    for check_sql, alter_sql in _MIGRATIONS:
        if conn.execute(check_sql).fetchone() is None:
            conn.execute(alter_sql)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_conversation(
    username: str, title: str, provider: str = "", model: str = ""
) -> str:
    conv_id = uuid.uuid4().hex
    now = int(time.time())
    with _chat_db(username) as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at, provider, model) "
            "VALUES (?,?,?,?,?,?)",
            (conv_id, title[:80], now, now, provider, model),
        )
    return conv_id


def conversation_exists(username: str, conversation_id: str) -> bool:
    with _chat_db(username) as conn:
        return conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone() is not None


def list_conversations(username: str) -> list[dict]:
    with _chat_db(username) as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, provider, model "
            "FROM conversations ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation_meta(username: str, conversation_id: str) -> dict | None:
    with _chat_db(username) as conn:
        row = conn.execute(
            "SELECT provider, model FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def get_session_stats(username: str, conversation_id: str) -> dict:
    """Cumulative turn/token/latency stats for a conversation.

    Persisted on the conversation row (rather than kept only in server
    memory) so they survive a restart and are available the moment you
    switch to any past conversation, not just ones that already had a turn
    run against them in the current process.
    """
    with _chat_db(username) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_STATS_COLUMNS)} FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return {col: (row[col] if row else 0) for col in _STATS_COLUMNS}


def record_turn_stats(
    username: str,
    conversation_id: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
) -> None:
    """Fold one completed turn's usage into a conversation's running stats.

    ``last_prompt_tokens`` (the current context size, as opposed to the
    cumulative ``prompt_tokens``) only updates when this turn actually
    reported prompt_tokens — 0 means "no usage in the response", not "empty
    context".
    """
    with _chat_db(username) as conn:
        conn.execute(
            """
            UPDATE conversations SET
                turns = turns + 1,
                prompt_tokens = prompt_tokens + ?,
                completion_tokens = completion_tokens + ?,
                total_tokens = total_tokens + ?,
                total_latency_ms = total_latency_ms + ?,
                last_prompt_tokens = CASE WHEN ? > 0 THEN ? ELSE last_prompt_tokens END
            WHERE id = ?
            """,
            (prompt_tokens, completion_tokens, total_tokens, latency_ms,
             prompt_tokens, prompt_tokens, conversation_id),
        )


def reset_context_stats(username: str, conversation_id: str) -> None:
    """Zero out just the "current context size" stat after a compaction.

    The cumulative turn/token counters are left alone (they're historical
    spend, still accurate) — only ``last_prompt_tokens`` is stale the moment
    the stored history shrinks, since it won't be recomputed until the next
    turn actually calls the LLM.
    """
    with _chat_db(username) as conn:
        conn.execute(
            "UPDATE conversations SET last_prompt_tokens = 0 WHERE id = ?",
            (conversation_id,),
        )


def get_messages(username: str, conversation_id: str) -> list[dict]:
    with _chat_db(username) as conn:
        rows = conn.execute(
            "SELECT content_json FROM messages WHERE conversation_id = ? ORDER BY seq",
            (conversation_id,),
        ).fetchall()
    return [json.loads(r["content_json"]) for r in rows]


def append_message(username: str, conversation_id: str, message: dict) -> int:
    """Append one message and return its ``seq``.

    ``seq`` is a gapless 1..N counter per conversation, shared across every
    role (user/assistant/tool) — messages are only ever added in bulk
    (``replace_messages``) or wiped whole (``delete_conversation``), never
    deleted individually, so a message's position in ``get_messages()``'s
    result is always ``seq - 1``. The returned value lets a caller (see
    ``record_llm_call``) link an LLM call to the exact assistant message it
    produced.
    """
    now = int(time.time())
    with _chat_db(username) as conn:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO messages (conversation_id, seq, role, content_json) VALUES (?,?,?,?)",
            (conversation_id, seq, message.get("role", ""), json.dumps(message)),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
        )
    return seq


def record_llm_call(
    username: str,
    conversation_id: str,
    *,
    message_seq: int,
    provider: str,
    model: str,
    request_url: str,
    request_payload: dict,
    response_content: str,
    response_tool_calls: list,
    finish_reason: str,
    usage: dict,
    latency_ms: int,
) -> None:
    """Log the literal request/response of one LLM call, for the chat
    transparency ("inspect this call") view in the Workbench.

    One row per iteration of ``loop.run_turn``'s tool loop — each iteration
    makes exactly one LLM call and appends exactly one assistant message, so
    ``message_seq`` (that message's ``append_message``-returned seq) links
    this row to the exact bubble it produced. ``request_payload`` is the
    literal wire-format body POSTed to the connection (see llm.py's
    ``on_request`` hook) — not the internal chat-completions-shaped messages
    loop.py builds, so what's shown here is genuinely what left the server.
    """
    now = int(time.time())
    response = {
        "content": response_content,
        "tool_calls": response_tool_calls,
        "finish_reason": finish_reason,
    }
    with _chat_db(username) as conn:
        conn.execute(
            """
            INSERT INTO llm_calls
                (conversation_id, message_seq, created_at, provider, model,
                 request_url, request_json, response_json, finish_reason, usage_json, latency_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                conversation_id, message_seq, now, provider, model,
                request_url, json.dumps(request_payload), json.dumps(response),
                finish_reason, json.dumps(usage or {}), latency_ms,
            ),
        )


def get_llm_calls(username: str, conversation_id: str) -> list[dict]:
    """All logged LLM calls for a conversation, ordered as they happened.

    Each entry carries ``message_seq`` so the frontend can attach it to the
    matching assistant message (position ``message_seq - 1`` in
    ``get_messages()``'s result — see ``append_message``'s docstring).
    """
    with _chat_db(username) as conn:
        rows = conn.execute(
            "SELECT message_seq, created_at, provider, model, request_url, request_json, "
            "response_json, finish_reason, usage_json, latency_ms FROM llm_calls "
            "WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["request"] = json.loads(d.pop("request_json"))
        d["response"] = json.loads(d.pop("response_json"))
        d["usage"] = json.loads(d.pop("usage_json") or "{}")
        out.append(d)
    return out


def replace_messages(username: str, conversation_id: str, messages: list[dict]) -> None:
    """Discard a conversation's stored messages and replace them wholesale.

    Used by compaction: the full history is deleted and replaced with just
    the summary message(s), same conversation id and title.

    Also clears this conversation's logged ``llm_calls`` — ``seq`` numbering
    restarts from 1 for the replacement messages, so any call log left
    behind would point at the wrong (new) message by coincidence of number
    rather than actually describing it.
    """
    now = int(time.time())
    with _chat_db(username) as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM llm_calls WHERE conversation_id = ?", (conversation_id,))
        for seq, message in enumerate(messages, start=1):
            conn.execute(
                "INSERT INTO messages (conversation_id, seq, role, content_json) VALUES (?,?,?,?)",
                (conversation_id, seq, message.get("role", ""), json.dumps(message)),
            )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
        )


def delete_conversation(username: str, conversation_id: str) -> None:
    with _chat_db(username) as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM llm_calls WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def prune_conversations(username: str, keep_min: int = 12, keep_days: int = 2) -> None:
    """Drop old conversations, keeping whichever is more: the ``keep_min``
    most recently updated, or everything touched in the last ``keep_days``.
    """
    cutoff = int(time.time()) - keep_days * 24 * 3600
    with _chat_db(username) as conn:
        rows = conn.execute(
            "SELECT id, updated_at FROM conversations ORDER BY updated_at DESC, rowid DESC"
        ).fetchall()
        keep_ids = {r["id"] for r in rows[:keep_min]}
        keep_ids |= {r["id"] for r in rows if r["updated_at"] >= cutoff}
        stale_ids = [r["id"] for r in rows if r["id"] not in keep_ids]
        if not stale_ids:
            return
        placeholders = ",".join("?" * len(stale_ids))
        conn.execute(
            f"DELETE FROM messages WHERE conversation_id IN ({placeholders})", stale_ids
        )
        conn.execute(
            f"DELETE FROM llm_calls WHERE conversation_id IN ({placeholders})", stale_ids
        )
        conn.execute(
            f"DELETE FROM conversations WHERE id IN ({placeholders})", stale_ids
        )
