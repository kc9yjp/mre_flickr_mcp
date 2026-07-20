"""The agent loop: stream LLM turns, dispatch tool calls, gate writes.

``run_turn`` is an async generator yielding stream events consumed by the
/api/chat/stream endpoint:

    {"type": "delta", "text": ...}                       content tokens
    {"type": "tool_call", "id", "name", "arguments"}     model wants a tool
    {"type": "confirm_request", "confirm_id", "name", "arguments"}
    {"type": "tool_result", "id", "name", "text"}
    {"type": "focus", "photo_id"}                        drive the photo viewer
    {"type": "error", "message"}
    {"type": "done"}
"""

import asyncio
import json
import logging
import re
import uuid
from typing import AsyncIterator

from mcp.types import TextContent, ImageContent

import mcp_tools
from db import _current_user, get_db

from agent import llm, schema, store

MAX_ITERATIONS = 15
CONFIRM_TIMEOUT = 300  # seconds to wait for the user's approve/deny
RESULT_CHAR_CAP = 20_000

SYSTEM_PROMPT = (
    "You are the Flickr Workbench assistant. You manage the user's own Flickr "
    "account through the provided tools: their photos, albums, groups, "
    "galleries, and contacts, backed by a local database synced from Flickr.\n"
    "Guidelines:\n"
    "- Read current state before changing anything (e.g. get_photo before "
    "update_photo).\n"
    "- Propose changes and wait for the user's go-ahead; write tools "
    "additionally require an explicit confirmation in the UI, and a declined "
    "confirmation means skip it and move on.\n"
    "- When suggesting groups or albums, use numbered lists so the user can "
    "pick by number.\n"
    "- Suggested tags: lowercase, compound words concatenated (oakpark), "
    "never bare year tags.\n"
    "- Keep responses concise. When you discuss a specific photo, mention its "
    "photo id.\n"
    "- Some turns include a note naming the photo currently open in the "
    "user's Photo Browser panel. Treat that as the default target for "
    "instructions that don't name a different photo — but an explicit photo "
    "id or link in the user's own message always takes priority over it."
)

# One agent turn at a time per user.
_turn_locks: dict[str, asyncio.Lock] = {}

# confirm_id -> Future resolved with True (approve) / False (deny)
_pending_confirms: dict[str, asyncio.Future] = {}


def get_turn_lock(username: str) -> asyncio.Lock:
    return _turn_locks.setdefault(username, asyncio.Lock())


def resolve_confirm(confirm_id: str, approve: bool) -> bool:
    """Resolve a pending write-tool confirmation. Returns False if unknown."""
    future = _pending_confirms.get(confirm_id)
    if future is None or future.done():
        return False
    future.set_result(bool(approve))
    return True


def _register_confirm(confirm_id: str) -> asyncio.Future:
    """Create the pending future BEFORE the confirm_request event is emitted,
    so an immediate /api/chat/confirm can never race the generator."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_confirms[confirm_id] = future
    return future


async def _await_confirmation(confirm_id: str, future: asyncio.Future) -> bool:
    try:
        return await asyncio.wait_for(future, timeout=CONFIRM_TIMEOUT)
    except asyncio.TimeoutError:
        return False
    finally:
        _pending_confirms.pop(confirm_id, None)


async def _execute_tool(user: dict, name: str, args: dict) -> list:
    """Dispatch one tool call with the user's context bound.

    Mirrors mcp_tools.call_tool: sync tools stay on the main loop; everything
    else runs in a worker thread (contextvars carry across to_thread).
    """
    handler = mcp_tools._HANDLERS[name]
    token = _current_user.set(user)
    try:
        if name in mcp_tools._MAIN_LOOP_HANDLERS:
            return await handler(args)
        return await asyncio.to_thread(lambda: asyncio.run(handler(args)))
    finally:
        _current_user.reset(token)


def _result_text(result: list) -> str:
    parts = []
    for item in result:
        if isinstance(item, TextContent):
            parts.append(item.text)
        elif isinstance(item, ImageContent):
            parts.append("(image fetched — vision support not enabled yet)")
        else:
            parts.append(str(item))
    text = "\n".join(parts)
    if len(text) > RESULT_CHAR_CAP:
        text = text[:RESULT_CHAR_CAP] + "\n…(truncated)"
    return text


def _focus_photo_id(name: str, args: dict) -> str | None:
    if name in ("sync",):
        return None
    for key in ("photo_id", "id"):
        value = str(args.get(key, ""))
        if re.fullmatch(r"\d{6,}", value):
            return value
    return None


def _photo_preview_sync(user: dict, photo_id: str) -> dict | None:
    """Best-effort thumbnail/title lookup for the confirm card. Never raises —
    a missing preview just means the card falls back to showing the raw id."""
    token = _current_user.set(user)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, title, url_medium FROM photos WHERE id = ?", (photo_id,)
            ).fetchone()
        if row:
            return {"id": row["id"], "title": row["title"], "thumb_url": row["url_medium"]}
    except Exception:
        logging.exception("agent: photo preview lookup failed for %s", photo_id)
    finally:
        _current_user.reset(token)
    return None


async def run_turn(
    user: dict,
    conversation_id: str,
    user_message: str,
    cfg: dict,
    focused_photo_id: str | None = None,
) -> AsyncIterator[dict]:
    """Run one user turn: LLM ↔ tools until the model stops calling tools.

    Caller must hold the user's turn lock and have verified cfg has a model.

    ``focused_photo_id`` — whichever photo is currently open in the caller's
    Photo Browser panel, if any. It is injected as a system note for THIS
    turn's LLM call only and is never persisted to the stored conversation:
    the note would otherwise go stale the moment the user looks at a
    different photo, silently misdirecting later turns that replay history.
    """
    username = user["username"]
    tools = schema.to_openai_tools()

    user_msg = {"role": "user", "content": user_message}
    store.append_message(username, conversation_id, user_msg)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if focused_photo_id:
        messages.append({
            "role": "system",
            "content": (
                f"The user currently has photo {focused_photo_id} open in "
                "the Photo Browser panel."
            ),
        })
    messages += store.get_messages(username, conversation_id)

    try:
        for _ in range(MAX_ITERATIONS):
            final = None
            async for event in llm.stream_chat(cfg, messages, tools=tools):
                if event["type"] == "delta":
                    yield event
                else:
                    final = event

            assistant_msg: dict = {"role": "assistant", "content": final["content"] or None}
            if final["tool_calls"]:
                assistant_msg["tool_calls"] = final["tool_calls"]
            store.append_message(username, conversation_id, assistant_msg)
            messages.append(assistant_msg)

            if not final["tool_calls"]:
                break

            for call in final["tool_calls"]:
                name = call["function"]["name"]
                raw_args = call["function"]["arguments"] or "{}"
                yield {"type": "tool_call", "id": call["id"], "name": name, "arguments": raw_args}

                try:
                    args = json.loads(raw_args)
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                except ValueError as e:
                    text = f"Invalid tool arguments: {e}"
                    args = None

                if args is not None and name not in mcp_tools._HANDLERS:
                    text = f"Unknown tool: {name}"
                    args = None

                if args is not None and name in schema.WRITE_TOOLS:
                    confirm_id = uuid.uuid4().hex
                    future = _register_confirm(confirm_id)
                    target_id = _focus_photo_id(name, args)
                    photo = (
                        await asyncio.to_thread(_photo_preview_sync, user, target_id)
                        if target_id else None
                    )
                    yield {
                        "type": "confirm_request",
                        "confirm_id": confirm_id,
                        "name": name,
                        "arguments": raw_args,
                        "photo": photo,
                    }
                    if not await _await_confirmation(confirm_id, future):
                        text = f"User declined: {name} was not executed."
                        args = None

                if args is not None:
                    try:
                        text = _result_text(await _execute_tool(user, name, args))
                    except (FileNotFoundError, RuntimeError) as e:
                        text = str(e)
                    except Exception as e:
                        logging.exception("agent: tool %s failed", name)
                        text = f"Unexpected error: {type(e).__name__}"

                tool_msg = {"role": "tool", "tool_call_id": call["id"], "content": text}
                store.append_message(username, conversation_id, tool_msg)
                messages.append(tool_msg)
                yield {"type": "tool_result", "id": call["id"], "name": name, "text": text}

                if args is not None and (photo_id := _focus_photo_id(name, args)):
                    yield {"type": "focus", "photo_id": photo_id}
        else:
            yield {
                "type": "error",
                "message": f"Stopped after {MAX_ITERATIONS} tool iterations.",
            }
    except llm.LLMError as e:
        yield {"type": "error", "message": str(e)}
    except Exception as e:
        logging.exception("agent: turn failed")
        yield {"type": "error", "message": f"Agent error: {type(e).__name__}: {e}"}

    yield {"type": "done"}
