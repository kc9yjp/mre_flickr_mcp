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
]


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


def get_messages(username: str, conversation_id: str) -> list[dict]:
    with _chat_db(username) as conn:
        rows = conn.execute(
            "SELECT content_json FROM messages WHERE conversation_id = ? ORDER BY seq",
            (conversation_id,),
        ).fetchall()
    return [json.loads(r["content_json"]) for r in rows]


def append_message(username: str, conversation_id: str, message: dict) -> None:
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


def replace_messages(username: str, conversation_id: str, messages: list[dict]) -> None:
    """Discard a conversation's stored messages and replace them wholesale.

    Used by compaction: the full history is deleted and replaced with just
    the summary message(s), same conversation id and title.
    """
    now = int(time.time())
    with _chat_db(username) as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
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
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
