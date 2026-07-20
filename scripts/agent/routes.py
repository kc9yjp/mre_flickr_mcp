"""Starlette endpoints for the chat agent: /api/chat/*, /api/llm-settings, /api/commands."""

import asyncio
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from webapi import _session_user, _unauthorized

from agent import commands, loop, settings, store

_PING_INTERVAL = 15  # seconds between SSE keepalive comments


async def _sse_events(inner) -> "asyncio.AsyncIterator[str]":
    """Wrap an event generator as SSE lines with keepalive pings.

    A producer task feeds a queue so pings flow even while the LLM or a
    pending confirmation keeps the inner generator silent.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for event in inner:
                await queue.put(event)
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_PING_INTERVAL)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        task.cancel()


async def chat_stream(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    cfg = settings.load_settings(user["nsid"])
    if not cfg.get("model"):
        return JSONResponse(
            {"error": "No LLM model configured — open the chat settings first."},
            status_code=400,
        )

    username = user["username"]
    lock = loop.get_turn_lock(username)
    if lock.locked():
        return JSONResponse({"error": "A chat turn is already running."}, status_code=409)

    conversation_id = body.get("conversation_id") or ""
    if conversation_id:
        if not store.conversation_exists(username, conversation_id):
            return JSONResponse({"error": "conversation not found"}, status_code=404)
    else:
        conversation_id = store.create_conversation(username, message)

    focused_photo_id = (body.get("focused_photo_id") or "").strip() or None

    async def events():
        async with lock:
            yield {"type": "start", "conversation_id": conversation_id}
            async for event in loop.run_turn(user, conversation_id, message, cfg, focused_photo_id):
                yield event

    return StreamingResponse(
        _sse_events(events()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def chat_confirm(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    ok = loop.resolve_confirm(str(body.get("confirm_id", "")), bool(body.get("approve")))
    if not ok:
        return JSONResponse({"error": "unknown or expired confirmation"}, status_code=404)
    return JSONResponse({"ok": True})


async def chat_conversations(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    return JSONResponse({"conversations": store.list_conversations(user["username"])})


async def chat_conversation_detail(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    conversation_id = request.path_params["id"]
    if not store.conversation_exists(user["username"], conversation_id):
        return JSONResponse({"error": "conversation not found"}, status_code=404)
    return JSONResponse({
        "id": conversation_id,
        "messages": store.get_messages(user["username"], conversation_id),
    })


async def chat_conversation_delete(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    store.delete_conversation(user["username"], request.path_params["id"])
    return JSONResponse({"ok": True})


async def llm_settings(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        try:
            saved = settings.save_settings(user["nsid"], body)
        except (OSError, ValueError) as e:
            logging.warning("llm_settings: save failed for %s: %s", user["nsid"], e)
            return JSONResponse({"error": "could not save settings"}, status_code=500)
        return JSONResponse(settings.masked(saved))
    return JSONResponse(settings.masked(settings.load_settings(user["nsid"])))


async def api_commands(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    return JSONResponse({"commands": commands.commands_for_api(user["nsid"])})


def api_routes() -> list[Route]:
    return [
        Route("/api/chat/stream",  endpoint=chat_stream, methods=["POST"]),
        Route("/api/chat/confirm", endpoint=chat_confirm, methods=["POST"]),
        Route("/api/chat/conversations", endpoint=chat_conversations),
        Route("/api/chat/conversations/{id}", endpoint=chat_conversation_detail),
        Route("/api/chat/conversations/{id}/delete", endpoint=chat_conversation_delete, methods=["POST"]),
        Route("/api/llm-settings", endpoint=llm_settings, methods=["GET", "POST"]),
        Route("/api/commands",     endpoint=api_commands),
    ]
