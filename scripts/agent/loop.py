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

from agent import llm, schema, store, settings as _agent_settings

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
    "id or link in the user's own message always takes priority over it. "
    "That note only gives an id, not details — if the user asks about 'the "
    "current photo' or similar, call get_photo (or another relevant tool) "
    "for that id to get fresh data rather than recalling an earlier photo "
    "from this conversation's history.\n"
    "- You CAN change what the user sees in the Photo Browser panel: calling "
    "any tool with a photo id (get_photo is the cheapest) switches that panel "
    "to show that photo. Whenever the user asks to see, open, switch to, or "
    "pull up a specific photo by id, call get_photo for it — do not claim you "
    "have no way to control the Photo Browser.\n"
    "- When the user says 'remember' or 'memory' followed by guidance, or asks "
    "you to remember a preference or rule for future conversations, call the "
    "`remember` tool with that guidance. Keep each piece of guidance as a "
    "concise, self-contained sentence or rule.\n"
    "- CRITICAL: Never claim to have seen, viewed, or visually described a "
    "photo unless actual image data was provided in the tool result. If a tool "
    "result says vision is disabled, work from title, description, tags, and "
    "EXIF only, and tell the user explicitly that visual inspection is "
    "unavailable. Guessing or fabricating visual details is not allowed.\n"
    "- CRITICAL: Tool schemas are provided to you exactly as they are. Never "
    "claim a tool doesn't support a field, parameter, or capability without "
    "rechecking its schema first — if it's in the schema, it's supported. "
    "Never state you performed an action, or that a specific field was "
    "updated, without checking the actual arguments you sent in that tool "
    "call. If you made a mistake (e.g. left a field out of an update), say so "
    "plainly instead of inventing an explanation for why it couldn't be done."
)

_REMEMBER_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "Save persistent guidance to your base prompt so it applies to all "
            "future conversations. Call this when the user asks you to remember "
            "a preference, rule, or context for future sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "guidance": {
                    "type": "string",
                    "description": "Concise guidance to append to the base prompt.",
                }
            },
            "required": ["guidance"],
        },
    },
}

# One agent turn at a time per user.
_turn_locks: dict[str, asyncio.Lock] = {}

# confirm_id -> Future resolved with True (approve) / False (deny)
_pending_confirms: dict[str, asyncio.Future] = {}

# Session stats per conversation: {conversation_id: {turns: N, tokens: N, latency_ms: N}}
_session_stats: dict[str, dict] = {}


def get_turn_lock(username: str) -> asyncio.Lock:
    return _turn_locks.setdefault(username, asyncio.Lock())


def resolve_confirm(confirm_id: str, approve: bool) -> bool:
    """Resolve a pending write-tool confirmation. Returns False if unknown."""
    future = _pending_confirms.get(confirm_id)
    if future is None or future.done():
        return False
    future.set_result(bool(approve))
    return True


def get_session_stats(conversation_id: str) -> dict:
    """Get accumulated stats for a conversation session."""
    return _session_stats.get(conversation_id, {
        "turns": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "total_latency_ms": 0,
    })


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


_VISION_DISABLED_NOTE = (
    "(image fetched — vision is disabled; work from title/description/tags/EXIF "
    "only. Do not guess or claim to describe the image.)"
)


def _result_content(result: list, vision: bool) -> "str | list":
    """Convert MCP tool result to LLM message content.

    Returns a multimodal list when vision is enabled and images are present;
    otherwise a plain string.
    """
    text_parts = []
    image_parts = []
    for item in result:
        if isinstance(item, TextContent):
            text_parts.append(item.text)
        elif isinstance(item, ImageContent):
            if vision:
                image_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{item.mimeType};base64,{item.data}"},
                })
            else:
                text_parts.append(_VISION_DISABLED_NOTE)
        else:
            text_parts.append(str(item))
    text = "\n".join(text_parts)
    if len(text) > RESULT_CHAR_CAP:
        text = text[:RESULT_CHAR_CAP] + "\n…(truncated)"
    if image_parts:
        content: list = []
        if text:
            content.append({"type": "text", "text": text})
        content.extend(image_parts)
        return content
    return text


_UPDATE_PHOTO_FIELDS = ("title", "description", "tags")


def _proposed_but_omitted_fields(args: dict, messages: list, lookback: int = 8) -> list[str]:
    """Heuristic: flag update_photo fields that were labeled (e.g. "Description:"
    or "**Description Suggestion:**") in a recent assistant message but aren't
    in this call's arguments.

    Best-effort text match, not a guarantee — a false positive just adds a
    warning; a false negative changes nothing. Exists so the model can't
    silently drop a field it just proposed, or tell the user it was updated
    when it never sent it."""
    omitted = [f for f in _UPDATE_PHOTO_FIELDS if f not in args]
    if not omitted:
        return []
    recent_text = "\n".join(
        m["content"] for m in messages[-lookback:]
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    ).lower()
    # Matches a plain "field:" label, or the word inside a **bold** span
    # (covers headers like "**Description Suggestion (Mood/Subject):**").
    return [
        f for f in omitted
        if re.search(rf"\b{f}\b\s*:|\*\*[^*\n]*\b{f}\b[^*\n]*\*\*", recent_text)
    ]


def _omission_warning(fields: list[str]) -> str:
    joined = ", ".join(fields)
    was_were = "was" if len(fields) == 1 else "were"
    pronoun = "it" if len(fields) == 1 else "they"
    return (
        f"⚠️ This call did not include {joined}, even though {was_were} "
        f"discussed earlier in this conversation. Do not tell the user "
        f"{pronoun} {was_were} changed."
    )


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


def _group_preview_sync(user: dict, group_id: str) -> dict | None:
    """Best-effort group-name lookup for the confirm card. Never raises —
    a missing preview just means the card falls back to showing the raw id."""
    token = _current_user.set(user)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, name FROM groups WHERE id = ?", (group_id,)
            ).fetchone()
        if row:
            return {"id": row["id"], "name": row["name"]}
    except Exception:
        logging.exception("agent: group preview lookup failed for %s", group_id)
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
    vision = bool(cfg.get("vision", False))
    tools = schema.to_openai_tools() + [_REMEMBER_TOOL]

    user_msg = {"role": "user", "content": user_message}
    store.append_message(username, conversation_id, user_msg)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    base_prompt = (cfg.get("base_prompt") or "").strip()
    if base_prompt:
        messages.append({"role": "system", "content": base_prompt})
    messages += store.get_messages(username, conversation_id)
    if focused_photo_id:
        # Appended AFTER the full history (right before the model's turn),
        # not near the top: on a long-running conversation a note buried
        # before dozens of older turns gets lost-in-the-middle and the model
        # falls back to whatever photo it last discussed instead of this one.
        messages.append({
            "role": "system",
            "content": (
                f"The user currently has photo {focused_photo_id} open in "
                "the Photo Browser panel."
            ),
        })

    try:
        for _ in range(MAX_ITERATIONS):
            final = None
            async for event in llm.stream_chat(cfg, messages, tools=tools):
                if event["type"] == "delta":
                    yield event
                else:
                    final = event

            # Track session stats
            if final:
                stats = _session_stats.setdefault(conversation_id, {
                    "turns": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "total_latency_ms": 0,
                })
                stats["turns"] += 1
                if final.get("usage"):
                    usage = final["usage"]
                    stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    stats["completion_tokens"] += usage.get("completion_tokens", 0)
                    stats["total_tokens"] += usage.get("total_tokens", 0)
                stats["total_latency_ms"] += final.get("latency_ms", 0)

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

                if args is not None and name == "remember":
                    guidance = (args.get("guidance") or "").strip()
                    if guidance:
                        nsid = user.get("nsid", "")
                        cur = _agent_settings.load_settings(nsid)
                        existing = (cur.get("base_prompt") or "").strip()
                        updated = (existing + "\n" + guidance).strip() if existing else guidance
                        _agent_settings.save_settings(nsid, {**cur, "base_prompt": updated})
                        text = f"Remembered: {guidance}"
                    else:
                        text = "Nothing to remember (empty guidance)."
                    args = None

                elif args is not None and name not in mcp_tools._HANDLERS:
                    text = f"Unknown tool: {name}"
                    args = None

                omitted_fields: list[str] = []
                if args is not None and name == "update_photo":
                    omitted_fields = _proposed_but_omitted_fields(args, messages)

                if args is not None and name in schema.WRITE_TOOLS:
                    confirm_id = uuid.uuid4().hex
                    future = _register_confirm(confirm_id)
                    target_id = _focus_photo_id(name, args)
                    photo = (
                        await asyncio.to_thread(_photo_preview_sync, user, target_id)
                        if target_id else None
                    )
                    group_id = str(args.get("group_id", ""))
                    group = (
                        await asyncio.to_thread(_group_preview_sync, user, group_id)
                        if group_id else None
                    )
                    yield {
                        "type": "confirm_request",
                        "confirm_id": confirm_id,
                        "name": name,
                        "arguments": raw_args,
                        "photo": photo,
                        "group": group,
                        "warning": _omission_warning(omitted_fields) if omitted_fields else None,
                    }
                    if not await _await_confirmation(confirm_id, future):
                        text = f"User declined: {name} was not executed."
                        args = None

                if args is not None:
                    try:
                        content = _result_content(await _execute_tool(user, name, args), vision)
                        if omitted_fields and isinstance(content, str):
                            content = f"{_omission_warning(omitted_fields)}\n\n{content}"
                    except (FileNotFoundError, RuntimeError) as e:
                        content = str(e)
                    except Exception as e:
                        logging.exception("agent: tool %s failed", name)
                        content = f"Unexpected error: {type(e).__name__}"
                else:
                    content = text

                ui_text = (
                    content if isinstance(content, str)
                    else next((p["text"] for p in content if p.get("type") == "text"), "(image)")
                )
                tool_msg = {"role": "tool", "tool_call_id": call["id"], "content": content}
                store.append_message(username, conversation_id, tool_msg)
                messages.append(tool_msg)
                yield {"type": "tool_result", "id": call["id"], "name": name, "text": ui_text}

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
