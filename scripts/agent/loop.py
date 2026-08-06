"""The agent loop: stream LLM turns, dispatch tool calls, gate writes.

``run_turn`` is an async generator yielding stream events consumed by the
/api/chat/stream endpoint:

    {"type": "delta", "text": ...}                       content tokens
    {"type": "tool_call", "id", "name", "arguments"}     model wants a tool
    {"type": "confirm_request", "confirm_id", "name", "arguments"}
    {"type": "tool_result", "id", "name", "text"}
    {"type": "focus", "photo_id"}                        drive the photo viewer
    {"type": "photo_list", "photo_ids"}                  populate the grid
    {"type": "user_photos", "nsid"}                      switch grid to another user's photostream
    {"type": "compacted", "summary"}                     auto-compact ran before this turn
    {"type": "injected", "text"}                         user's add_injection text was folded in
    {"type": "inject_missed", "text"}                    add_injection arrived too late; client re-queues it
    {"type": "error", "message"}
    {"type": "done"}

A turn can also be ended early by cancelling its task (see loop.cancel_turn) —
CancelledError unwinds through the "async with lock" in routes.py, releasing
it, without this generator getting to yield a final event itself.
"""

import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.types import TextContent, ImageContent

import mcp_tools
from db import _current_user, get_db

from agent import compact, llm, prompts_store, schema, settings, store
from agent.wire import approx_tokens
from agent.wire import coerce_null_content as _coerce_null_content
from agent.wire import wire_messages as _wire_messages

MAX_ITERATIONS = 15
CONFIRM_TIMEOUT = 300  # seconds to wait for the user's approve/deny
RESULT_CHAR_CAP = 20_000
DEFAULT_CONTEXT_WINDOW = 128_000
AUTO_COMPACT_THRESHOLD = 0.8  # fraction of context_window that triggers auto-compact

_REMEMBER_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "Save persistent guidance to your standing memory so it applies to "
            "all future conversations. Call this when the user asks you to "
            "remember a preference, rule, or context for future sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "guidance": {
                    "type": "string",
                    "description": "Concise guidance to append to standing memory.",
                }
            },
            "required": ["guidance"],
        },
    },
}

# Two levels of keying, on purpose:
#
#   * The turn LOCK is keyed by (username, conversation_id). Its job is to
#     protect one conversation's stored history from two turns interleaving
#     their message writes, so it must serialize per conversation — including
#     two different browser windows that happen to have the SAME conversation
#     open. It deliberately does NOT include the session id (that would give
#     each window its own lock and reopen the very race).
#
#   * The active TASK, active-conversation map, and cancellation are keyed by
#     (username, session_id) — each browser window/device generates its own
#     session id (the X-Session-Id header, see routes.py) so a window cancels
#     only its OWN in-flight turn, never a sibling window's. A missing/empty
#     session id collapses to (username, "") — the old per-user behavior — so
#     pre-session clients still work.
#
# Conversation *history* stays keyed by username alone (see agent.store):
# every window shares the same past-conversation list.
_SessionKey = tuple[str, str]  # (username, session_id)
_ConvKey = tuple[str, str]     # (username, conversation_id)

# The per-conversation lock, plus a refcount of how many coroutines currently
# hold OR wait on each one. The count lets an idle lock be dropped from the map
# instead of lingering forever — keyed by conversation_id, one entry would
# otherwise accumulate per conversation ever touched and never be reclaimed.
# Only ``conversation_turn_lock`` touches these; peek with ``is_turn_running``.
_turn_locks: dict[_ConvKey, asyncio.Lock] = {}
_turn_lock_refs: dict[_ConvKey, int] = {}

# session key -> the task actually driving that session's in-flight SSE stream,
# so a cancel request has something concrete to call .cancel() on. Cancelling
# this task (rather than e.g. just clearing _turn_locks) is what makes the
# cancellation real: it throws CancelledError at whatever await point the
# turn is suspended on, which unwinds through run_turn's "async with lock"
# and actually releases the lock instead of leaving it stuck.
_active_tasks: dict[_SessionKey, asyncio.Task] = {}

# conversation_id -> queued text the user submitted while a turn was still
# running, to be folded into the next LLM call of THAT SAME turn (as opposed
# to waiting for the turn to finish and starting a new one).
_injections: dict[str, list[str]] = {}

# confirm_id -> (owning username, Future resolved with True (approve) / False (deny))
_pending_confirms: dict[str, tuple[str, asyncio.Future]] = {}

# session key -> conversation_id of that session's currently in-flight turn
# (at most one, per the turn lock). Lets a conversation delete find and cancel
# whichever session is actually running that conversation, instead of blindly
# killing whatever turn a given session happens to be holding.
_active_conversations: dict[_SessionKey, str] = {}


def is_turn_running(username: str, conversation_id: str = "") -> bool:
    """Whether a turn currently holds this conversation's lock.

    A read-only peek, used by the 409 pre-checks and by inject. Unlike
    acquiring the lock it never creates an entry, so idle probes don't
    accumulate locks in ``_turn_locks`` that are never actually used.
    """
    lock = _turn_locks.get((username, conversation_id))
    return lock is not None and lock.locked()


@asynccontextmanager
async def conversation_turn_lock(
    username: str, conversation_id: str = ""
) -> "AsyncIterator[None]":
    """Hold the per-conversation turn lock for the ``with`` block.

    Keyed by conversation, NOT by browser session — this is what keeps two
    windows with the same conversation open from running concurrent turns and
    corrupting its history. Windows on *different* conversations get different
    locks and run in parallel; cancellation is tracked separately, per session
    (see ``_active_tasks``), so a window can still cancel its own turn.

    A refcount of current holders+waiters rides alongside each lock; when it
    falls back to zero the lock is dropped from the map, so ``_turn_locks``
    doesn't grow one entry per conversation forever. Removing an idle lock is
    safe: every coroutine currently using it has already bumped the count and
    holds its own reference to the object, so a zero count proves nobody else
    is about to touch this exact lock — a later caller just makes a fresh one.
    The refcount bookkeeping runs with no ``await`` between check and pop, so
    it stays atomic against other coroutines on the single event loop.
    """
    key = (username, conversation_id)
    lock = _turn_locks.setdefault(key, asyncio.Lock())
    _turn_lock_refs[key] = _turn_lock_refs.get(key, 0) + 1
    try:
        async with lock:
            yield
    finally:
        _turn_lock_refs[key] -= 1
        if _turn_lock_refs[key] <= 0:
            _turn_lock_refs.pop(key, None)
            _turn_locks.pop(key, None)


def register_task(username: str, session_id: str, task: asyncio.Task) -> None:
    _active_tasks[(username, session_id)] = task


def unregister_task(username: str, session_id: str, task: asyncio.Task) -> None:
    if _active_tasks.get((username, session_id)) is task:
        del _active_tasks[(username, session_id)]


@asynccontextmanager
async def tracked(username: str, session_id: str, coro) -> "AsyncIterator[asyncio.Task]":
    """Run ``coro`` as this session's cancellable active task for the ``with`` block.

    Anything that holds the turn lock — a streamed turn or a single plain
    await like a manual compact — needs to register itself this way so a
    later /api/chat/cancel has a real task to cancel. Without it, a client
    that vanishes mid-request with no clean disconnect (a network change,
    not a closed tab) leaves the lock held with nothing able to release it.
    """
    task = asyncio.create_task(coro)
    register_task(username, session_id, task)
    try:
        yield task
    finally:
        unregister_task(username, session_id, task)


def cancel_turn(
    username: str,
    session_id: str | None = None,
    conversation_id: str | None = None,
) -> bool:
    """Cancel an in-flight turn for this user. Returns False if nothing matched.

    Pass ``session_id`` to cancel that one browser session's own turn — the
    generic cancel button and the orphaned-lock cleanup on a dead stream, so a
    window only ever tears down its *own* turn, never a sibling window's.

    Pass ``conversation_id`` (conversation delete) to cancel whichever session
    is currently running that conversation, regardless of which window started
    it. With neither, cancels every in-flight turn the user has.
    """
    if session_id is not None:
        keys: list[_SessionKey] = [(username, session_id)]
    elif conversation_id is not None:
        keys = [
            key for key, cid in _active_conversations.items()
            if key[0] == username and cid == conversation_id
        ]
    else:
        keys = [key for key in _active_tasks if key[0] == username]

    cancelled = False
    for key in keys:
        task = _active_tasks.get(key)
        if task is not None and not task.done():
            task.cancel()
            cancelled = True
    return cancelled


def add_injection(conversation_id: str, text: str) -> None:
    _injections.setdefault(conversation_id, []).append(text)


def _pop_injections(conversation_id: str) -> list[str]:
    return _injections.pop(conversation_id, [])


def resolve_confirm(
    confirm_id: str, approve: bool, reason: str | None = None, username: str | None = None,
) -> bool:
    """Resolve a pending write-tool confirmation. Returns False if unknown.

    ``reason`` is the user's explanation for a denial (ignored on approval) so
    the model can adjust its next proposal instead of just seeing "declined".

    ``username``, when given, must match the confirmation's owner (the user
    whose turn generated it) — confirm_id is an unguessable UUID, but without
    this check any authenticated user who learned/leaked another user's
    confirm_id could approve or deny that user's pending write-tool
    confirmation. Omit it only for trusted internal callers (e.g. tests
    driving the loop directly) that already know they own the confirmation.
    """
    entry = _pending_confirms.get(confirm_id)
    if entry is None:
        return False
    owner, future = entry
    if future.done():
        return False
    if username is not None and username != owner:
        logging.warning(
            "chat_confirm: refusing confirm_id %s — owned by %s, requested by %s",
            confirm_id, owner, username,
        )
        return False
    future.set_result({"approve": bool(approve), "reason": (reason or "").strip() or None})
    return True


def _register_confirm(confirm_id: str, username: str) -> asyncio.Future:
    """Create the pending future BEFORE the confirm_request event is emitted,
    so an immediate /api/chat/confirm can never race the generator."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_confirms[confirm_id] = (username, future)
    return future


async def _await_confirmation(confirm_id: str, future: asyncio.Future) -> dict:
    try:
        return await asyncio.wait_for(future, timeout=CONFIRM_TIMEOUT)
    except asyncio.TimeoutError:
        return {"approve": False, "reason": None}
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


# Tools whose results are a JSON array of photo objects (each with an "id")
# rather than a single photo or a scalar — their results should populate the
# Photo Browser grid in one shot instead of just being narrated in chat.
_LIST_TOOLS = {"find_weak_photos", "search_photos"}

# get_user_photos results are someone else's photostream, fetched live — they
# were never synced to the local DB, so they can't go through _LIST_TOOLS
# (the Photo Browser grid's ids= path only queries local photos). Instead,
# point the UI at the Photo Browser's own live "User" tab fetch, keyed by
# nsid pulled from the tool's own output rather than re-resolving user_id
# (which could be a username/URL) and spending a second API call on it.
_USER_PHOTOS_URL_RE = re.compile(r"flickr\.com/photos/([^/\s]+)/")


def _user_photos_nsid(name: str, text: str) -> str | None:
    if name != "get_user_photos":
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    m = _USER_PHOTOS_URL_RE.search(parsed[0].get("url", ""))
    return m.group(1) if m else None


def _list_photo_ids(name: str, text: str) -> list[str] | None:
    """Best-effort extraction of photo ids from a list-tool's JSON result.

    Returns None (never an empty list) when the result isn't a non-empty
    photo array, so callers can skip emitting a photo_list event entirely."""
    if name not in _LIST_TOOLS:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    ids = [str(item["id"]) for item in parsed if isinstance(item, dict) and "id" in item]
    return ids or None


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
    session_id: str = "",
) -> AsyncIterator[dict]:
    """Run one user turn: LLM ↔ tools until the model stops calling tools.

    Caller must hold the user's turn lock and have verified cfg has a model.

    ``focused_photo_id`` — whichever photo is currently open in the caller's
    Photo Viewer panel, if any. It is injected as a system note for THIS
    turn's LLM call only and is never persisted to the stored conversation:
    the note would otherwise go stale the moment the user looks at a
    different photo, silently misdirecting later turns that replay history.
    """
    username = user["username"]
    nsid = user["nsid"]
    vision = bool(cfg.get("vision", False))
    tools = schema.to_openai_tools() + [_REMEMBER_TOOL]
    session_key = (username, session_id)
    _active_conversations[session_key] = conversation_id

    # Auto-compact runs on the *prior* history, before this turn's message is
    # appended — compacting after would fold the user's brand-new question
    # into the summary and leave nothing for the model to actually respond to.
    if settings.load_settings(nsid).get("auto_compact"):
        prior_messages = store.get_messages(username, conversation_id)
        context_window = cfg.get("context_window") or DEFAULT_CONTEXT_WINDOW
        if approx_tokens(prior_messages) >= context_window * AUTO_COMPACT_THRESHOLD:
            summary = await compact.compact(username, nsid, conversation_id, cfg)
            if summary:
                store.reset_context_stats(username, conversation_id)
                yield {"type": "compacted", "summary": summary}

    user_msg = {"role": "user", "content": user_message}
    store.append_message(username, conversation_id, user_msg)
    system_prompt = prompts_store.get_prompt_by_code(nsid, "system-core")
    system_text = system_prompt["text"] if system_prompt else prompts_store.SYSTEM_PROMPT_DEFAULT
    messages = [{
        "role": "system",
        "content": prompts_store.resolve_server_variables(system_text, nsid, username),
    }]
    user_memory = prompts_store.get_prompt_by_code(nsid, "user-memory")
    memory_text = ((user_memory["text"] if user_memory else "") or "").strip()
    if memory_text:
        messages.append({"role": "system", "content": memory_text})
    messages += store.get_messages(username, conversation_id)
    _coerce_null_content(messages)
    if focused_photo_id:
        # Inserted just BEFORE the final message (right before the model's
        # turn), not near the top: on a long-running conversation a note
        # buried before dozens of older turns gets lost-in-the-middle and the
        # model falls back to whatever photo it last discussed instead of
        # this one. It can't go strictly last, though — some OpenAI-compatible
        # backends (e.g. LM Studio's stricter chat templates) require the
        # conversation to end on a "user" turn and error ("No user query
        # found in messages") if the trailing message is a system note.
        messages.insert(len(messages) - 1, {
            "role": "system",
            "content": (
                f"The user currently has photo {focused_photo_id} open in "
                "the Photo Viewer panel."
            ),
        })

    stream_fn = llm.stream_responses if cfg.get("api_mode") == "responses" else llm.stream_chat

    try:
        for _ in range(MAX_ITERATIONS):
            for extra in _pop_injections(conversation_id):
                extra_msg = {"role": "user", "content": extra}
                store.append_message(username, conversation_id, extra_msg)
                messages.append(extra_msg)
                yield {"type": "injected", "text": extra}

            final = None
            async for event in stream_fn(cfg, _wire_messages(messages), tools=tools):
                if event["type"] == "delta":
                    yield event
                else:
                    final = event

            # Track session stats
            if final:
                usage = final.get("usage") or {}
                store.record_turn_stats(
                    username,
                    conversation_id,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    latency_ms=final.get("latency_ms", 0),
                )

            assistant_msg: dict = {"role": "assistant", "content": final["content"]}
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
                    if not guidance:
                        text = "Nothing to remember (empty guidance)."
                    else:
                        # Persisted guidance is re-injected into every future
                        # turn's system prompt (see get_prompt_by_code("user-memory")
                        # above), so it's gated behind the same confirm_request/
                        # approve flow as any other write tool — otherwise text
                        # smuggled in via an untrusted photo/comment/group
                        # description the model reads could get the model to
                        # "remember" instructions that steer all later sessions
                        # without the user ever seeing them.
                        confirm_id = uuid.uuid4().hex
                        future = _register_confirm(confirm_id, username)
                        yield {
                            "type": "confirm_request",
                            "confirm_id": confirm_id,
                            "name": name,
                            "arguments": raw_args,
                            "photo": None,
                            "group": None,
                            "warning": None,
                        }
                        decision = await _await_confirmation(confirm_id, future)
                        if decision["approve"]:
                            prompts_store.append_user_memory(nsid, guidance)
                            text = f"Remembered: {guidance}"
                        else:
                            text = f"User declined: {name} was not executed."
                            if decision["reason"]:
                                text += f" Reason: {decision['reason']}"
                    args = None

                elif args is not None and name not in mcp_tools._HANDLERS:
                    text = f"Unknown tool: {name}"
                    args = None

                omitted_fields: list[str] = []
                if args is not None and name == "update_photo":
                    omitted_fields = _proposed_but_omitted_fields(args, messages)

                if args is not None and name in schema.WRITE_TOOLS:
                    confirm_id = uuid.uuid4().hex
                    future = _register_confirm(confirm_id, username)
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
                    decision = await _await_confirmation(confirm_id, future)
                    if not decision["approve"]:
                        text = f"User declined: {name} was not executed."
                        if decision["reason"]:
                            text += f" Reason: {decision['reason']}"
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

                if args is not None and (photo_ids := _list_photo_ids(name, ui_text)):
                    yield {"type": "photo_list", "photo_ids": photo_ids}
                elif args is not None and (nsid := _user_photos_nsid(name, ui_text)):
                    yield {"type": "user_photos", "nsid": nsid}
                elif args is not None and (photo_id := _focus_photo_id(name, args)):
                    yield {"type": "focus", "photo_id": photo_id}
        else:
            yield {
                "type": "error",
                "message": f"Stopped after {MAX_ITERATIONS} tool iterations.",
            }

        # Anything submitted too late to be folded into the loop above — it
        # arrived during the final LLM call, after that iteration already
        # popped its injections, so this turn can't answer it. Don't store it
        # as an unanswered user message (that's the "missed add-info" bug:
        # text sitting in the history with no reply). Instead tell the client
        # so it re-queues the text as its own follow-up turn — "send it last".
        # Not stored here; the re-send stores it, so storing now would dup it.
        for extra in _pop_injections(conversation_id):
            yield {"type": "inject_missed", "text": extra}
    except llm.LLMError as e:
        logging.warning("agent: LLM call failed for %s conversation %s: %s", username, conversation_id, e)
        yield {"type": "error", "message": str(e)}
    except Exception as e:
        logging.exception("agent: turn failed")
        yield {"type": "error", "message": f"Agent error: {type(e).__name__}: {e}"}
    finally:
        # Guarantees cleanup even on cancellation (CancelledError skips the
        # `except Exception` above but not this) so a stray injection can
        # never bleed into an unrelated later turn on this conversation.
        _injections.pop(conversation_id, None)
        if _active_conversations.get(session_key) == conversation_id:
            del _active_conversations[session_key]

    yield {"type": "done"}
