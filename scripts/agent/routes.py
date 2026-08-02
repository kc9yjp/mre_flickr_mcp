"""Starlette endpoints for the chat agent: /api/chat/*, /api/llm-settings, /api/llm-models,
/api/llm-connections, /api/llm-connection-presets, /api/commands."""

import asyncio
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from webapi import _session_user, _unauthorized

from agent import commands, compact, llm, loop, prompts_store, settings, store

_PING_INTERVAL = 15  # seconds between SSE keepalive comments


async def _sse_events(inner, username: str) -> "asyncio.AsyncIterator[str]":
    """Wrap an event generator as SSE lines with keepalive pings.

    A producer task feeds a queue so pings flow even while the LLM or a
    pending confirmation keeps the inner generator silent. That task is
    registered under ``username`` for the duration so /api/chat/cancel has
    something to actually cancel — cancelling it unwinds ``inner`` (which
    holds the turn lock) via CancelledError, releasing the lock instead of
    leaving it stuck.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for event in inner:
                await queue.put(event)
        except asyncio.CancelledError:
            await queue.put({"type": "cancelled"})
            raise
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    loop.register_task(username, task)
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
        loop.unregister_task(username, task)


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

    nsid = user["nsid"]
    username = user["username"]
    connection_override = (body.get("connection") or body.get("provider") or "").strip() or None
    model_override = (body.get("model") or "").strip() or None

    cfg = settings.resolve_cfg(nsid, connection_override, model_override)
    if not cfg.get("model") or not cfg.get("base_url"):
        return JSONResponse(
            {"error": "No LLM model configured — open the models panel first."},
            status_code=400,
        )

    lock = loop.get_turn_lock(username)
    if lock.locked():
        return JSONResponse({"error": "A chat turn is already running."}, status_code=409)

    conversation_id = body.get("conversation_id") or ""
    if conversation_id:
        if not store.conversation_exists(username, conversation_id):
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        # Use per-conversation connection/model unless caller explicitly overrides.
        if not connection_override and not model_override:
            meta = store.get_conversation_meta(username, conversation_id)
            if meta:
                cconn = meta.get("provider") or None
                cmodel = meta.get("model") or None
                if cconn or cmodel:
                    cfg = settings.resolve_cfg(nsid, cconn, cmodel)
    else:
        conv_connection = connection_override or settings.load_settings(nsid).get("active_connection", "")
        conv_model = model_override or cfg.get("model", "")
        conversation_id = store.create_conversation(
            username, message, conv_connection, conv_model
        )
        store.prune_conversations(username)

    focused_photo_id = (body.get("focused_photo_id") or "").strip() or None

    async def events():
        async with lock:
            yield {"type": "start", "conversation_id": conversation_id}
            async for event in loop.run_turn(user, conversation_id, message, cfg, focused_photo_id):
                yield event

    return StreamingResponse(
        _sse_events(events(), username),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def chat_cancel(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    cancelled = loop.cancel_turn(user["username"])
    return JSONResponse({"ok": cancelled})


async def chat_inject(request: Request):
    """Fold text into the NEXT LLM call of the user's currently-running turn.

    Requires a turn to actually be in flight — otherwise there's nothing to
    inject into and the caller should just send a normal new message instead.
    """
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    message = (body.get("message") or "").strip()
    conversation_id = (body.get("conversation_id") or "").strip()
    if not message or not conversation_id:
        return JSONResponse({"error": "message and conversation_id are required"}, status_code=400)
    if not store.conversation_exists(user["username"], conversation_id):
        return JSONResponse({"error": "conversation not found"}, status_code=404)
    if not loop.get_turn_lock(user["username"]).locked():
        return JSONResponse({"error": "no turn is currently running"}, status_code=409)
    loop.add_injection(conversation_id, message)
    return JSONResponse({"ok": True})


async def chat_confirm(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    ok = loop.resolve_confirm(
        str(body.get("confirm_id", "")),
        bool(body.get("approve")),
        body.get("reason"),
        username=user["username"],
    )
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
    meta = store.get_conversation_meta(user["username"], conversation_id) or {}
    return JSONResponse({
        "id": conversation_id,
        "provider": meta.get("provider", ""),
        "model": meta.get("model", ""),
        "messages": store.get_messages(user["username"], conversation_id),
    })


async def chat_conversation_delete(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    conversation_id = request.path_params["id"]
    # If this conversation's turn is still in flight, stop it before deleting
    # — otherwise the backend keeps calling the LLM/tools and writing message
    # rows for a conversation that no longer exists.
    loop.cancel_turn(user["username"], conversation_id)
    store.delete_conversation(user["username"], conversation_id)
    return JSONResponse({"ok": True})


async def chat_conversation_compact(request: Request):
    """Manually compact one conversation: summarize its stored history via
    the LLM and replace it in place. Shares the same code path as
    loop.run_turn's auto-compact — see agent/compact.py."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    nsid = user["nsid"]
    username = user["username"]
    conversation_id = request.path_params["id"]
    if not store.conversation_exists(username, conversation_id):
        return JSONResponse({"error": "conversation not found"}, status_code=404)

    lock = loop.get_turn_lock(username)
    if lock.locked():
        return JSONResponse({"error": "A chat turn is already running."}, status_code=409)

    meta = store.get_conversation_meta(username, conversation_id) or {}
    cfg = settings.resolve_cfg(nsid, meta.get("provider") or None, meta.get("model") or None)
    if not cfg.get("model") or not cfg.get("base_url"):
        return JSONResponse(
            {"error": "No LLM model configured — open the models panel first."},
            status_code=400,
        )

    async with lock:
        summary = await compact.compact(username, nsid, conversation_id, cfg)
    if not summary:
        return JSONResponse({"error": "Nothing to compact, or the LLM call failed."}, status_code=400)
    store.reset_context_stats(username, conversation_id)
    return JSONResponse({"ok": True, "summary": summary})


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


async def llm_models(request: Request):
    """Fetch available models for a connection's base_url/api_key, filtered
    by that connection's persisted ``disabled_models``."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    connection_id = request.query_params.get("connection") or request.query_params.get("provider") or ""
    if not connection_id:
        return JSONResponse({"error": "connection query param required"}, status_code=400)

    s = settings.load_settings(user["nsid"])
    connections = s.get("connections") or {}
    conn = connections.get(connection_id)
    if not conn:
        return JSONResponse({"error": f"unknown connection: {connection_id}"}, status_code=404)

    base_url = conn.get("base_url", "")
    api_key = conn.get("api_key", "")
    if not base_url:
        return JSONResponse({"error": "connection has no base_url"}, status_code=400)

    try:
        all_models = await llm.list_models(base_url, api_key)
    except llm.LLMError as e:
        logging.warning("llm_models: failed for connection %s (%s): %s", connection_id, user["nsid"], e)
        return JSONResponse({"error": str(e)}, status_code=502)

    disabled = set(conn.get("disabled_models") or [])
    models = [m for m in all_models if m not in disabled]
    return JSONResponse({"models": models, "all_models": all_models})


async def llm_connection_presets(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    return JSONResponse({"presets": settings.CONNECTION_PRESETS})


async def llm_connections_create(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    name = (body.get("name") or "").strip()
    kind = (body.get("kind") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    if not name or not kind or not base_url:
        return JSONResponse({"error": "name, kind, and base_url are required"}, status_code=400)
    try:
        cid, saved = settings.create_connection(
            user["nsid"], name, kind, base_url,
            api_key=body.get("api_key", ""),
            api_mode=body.get("api_mode", "chat_completions"),
            timeout_seconds=body.get("timeout_seconds"),
        )
    except (OSError, ValueError) as e:
        logging.warning("llm_connections_create: failed for %s: %s", user["nsid"], e)
        return JSONResponse({"error": "could not save connection"}, status_code=500)
    return JSONResponse({"id": cid, **saved})


async def llm_connections_update(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    updated = settings.update_connection(user["nsid"], request.path_params["id"], body)
    if updated is None:
        return JSONResponse({"error": "unknown connection"}, status_code=404)
    return JSONResponse(updated)


async def llm_connections_delete(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    deleted = settings.delete_connection(user["nsid"], request.path_params["id"])
    if deleted is None:
        return JSONResponse({"error": "unknown connection"}, status_code=404)
    return JSONResponse(deleted)


async def llm_model_settings_update(request: Request):
    """Patch a model's per-model settings (vision/max_tokens/sampling/tool_choice).

    The model id is carried in the body rather than the URL path — model ids
    such as ``qwen/qwen3.5-9b`` (LM Studio) contain slashes.
    """
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    model = (body.get("model") or "").strip()
    if not model:
        return JSONResponse({"error": "model is required"}, status_code=400)
    updated = settings.update_model_settings(user["nsid"], request.path_params["id"], model, body)
    if updated is None:
        return JSONResponse({"error": "unknown connection"}, status_code=404)
    return JSONResponse(updated)


async def llm_model_settings_reset(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    model = (body.get("model") or "").strip()
    if not model:
        return JSONResponse({"error": "model is required"}, status_code=400)
    updated = settings.reset_model_settings(user["nsid"], request.path_params["id"], model)
    if updated is None:
        return JSONResponse({"error": "unknown connection"}, status_code=404)
    return JSONResponse(updated)


async def api_commands(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    return JSONResponse({"commands": commands.commands_for_api(user["nsid"], user["username"])})


async def prompts_collection(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    if request.method == "GET":
        return JSONResponse(prompts_store.all_data(user["nsid"]))
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    for field in ("code", "name", "category_id", "text"):
        if not (body.get(field) or "").strip():
            return JSONResponse({"error": f"{field} is required"}, status_code=400)
    try:
        prompt = prompts_store.create_prompt(
            user["nsid"],
            code=body["code"].strip(),
            name=body["name"].strip(),
            category_id=body["category_id"],
            text=body["text"],
            description=body.get("description", ""),
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(prompt)


async def prompts_update(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    prompt_id = request.path_params["id"]
    updated = prompts_store.update_prompt(
        user["nsid"],
        prompt_id,
        name=body.get("name"),
        description=body.get("description"),
        category_id=body.get("category_id"),
        text=body.get("text"),
        enabled=body.get("enabled"),
    )
    if updated is None:
        return JSONResponse({"error": "prompt not found"}, status_code=404)
    return JSONResponse(updated)


async def prompts_delete(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    ok, error = prompts_store.delete_prompt(user["nsid"], request.path_params["id"])
    if not ok:
        return JSONResponse({"error": error}, status_code=400)
    return JSONResponse({"ok": True})


async def prompts_reset(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    reset = prompts_store.reset_prompt(user["nsid"], request.path_params["id"])
    if reset is None:
        return JSONResponse({"error": "prompt not found or not built-in"}, status_code=400)
    return JSONResponse(reset)


async def prompt_categories_create(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    category = prompts_store.create_category(user["nsid"], name, body.get("description", ""))
    return JSONResponse(category)


async def prompt_categories_update(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    updated = prompts_store.update_category(
        user["nsid"], request.path_params["id"],
        name=body.get("name"), description=body.get("description"),
    )
    if updated is None:
        return JSONResponse({"error": "category not found"}, status_code=404)
    return JSONResponse(updated)


async def prompt_categories_delete(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    ok, error = prompts_store.delete_category(user["nsid"], request.path_params["id"])
    if not ok:
        return JSONResponse({"error": error}, status_code=400)
    return JSONResponse({"ok": True})


async def prompt_variables_create(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    code = (body.get("code") or "").strip()
    label = (body.get("label") or "").strip()
    if not code or not label:
        return JSONResponse({"error": "code and label are required"}, status_code=400)
    try:
        variable = prompts_store.create_variable(user["nsid"], code, label, body.get("description", ""))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(variable)


async def prompt_variables_delete(request: Request):
    user = _session_user(request)
    if not user:
        return _unauthorized()
    ok, error = prompts_store.delete_variable(user["nsid"], request.path_params["code"])
    if not ok:
        return JSONResponse({"error": error}, status_code=400)
    return JSONResponse({"ok": True})


async def chat_stats(request: Request):
    """Get accumulated stats for a conversation session, plus the active
    model's context_window so the frontend can compute a "context used" %
    from ``last_prompt_tokens`` without a second round trip."""
    user = _session_user(request)
    if not user:
        return _unauthorized()
    conversation_id = request.query_params.get("conversation_id") or ""
    if not conversation_id:
        return JSONResponse({"error": "conversation_id query param required"}, status_code=400)

    stats = store.get_session_stats(user["username"], conversation_id)
    meta = store.get_conversation_meta(user["username"], conversation_id) or {}
    cfg = settings.resolve_cfg(user["nsid"], meta.get("provider") or None, meta.get("model") or None)
    stats["context_window"] = cfg.get("context_window") or loop.DEFAULT_CONTEXT_WINDOW
    return JSONResponse(stats)


def api_routes() -> list[Route]:
    return [
        Route("/api/chat/stream",  endpoint=chat_stream, methods=["POST"]),
        Route("/api/chat/cancel",  endpoint=chat_cancel, methods=["POST"]),
        Route("/api/chat/inject",  endpoint=chat_inject, methods=["POST"]),
        Route("/api/chat/confirm", endpoint=chat_confirm, methods=["POST"]),
        Route("/api/chat/conversations", endpoint=chat_conversations),
        Route("/api/chat/conversations/{id}", endpoint=chat_conversation_detail),
        Route("/api/chat/conversations/{id}/delete", endpoint=chat_conversation_delete, methods=["POST"]),
        Route("/api/chat/conversations/{id}/compact", endpoint=chat_conversation_compact, methods=["POST"]),
        Route("/api/chat/stats",    endpoint=chat_stats),
        Route("/api/llm-settings", endpoint=llm_settings, methods=["GET", "POST"]),
        Route("/api/llm-models",   endpoint=llm_models),
        Route("/api/llm-connection-presets", endpoint=llm_connection_presets),
        Route("/api/llm-connections", endpoint=llm_connections_create, methods=["POST"]),
        Route("/api/llm-connections/{id}/update", endpoint=llm_connections_update, methods=["POST"]),
        Route("/api/llm-connections/{id}/delete", endpoint=llm_connections_delete, methods=["POST"]),
        Route("/api/llm-connections/{id}/model-settings", endpoint=llm_model_settings_update, methods=["POST"]),
        Route("/api/llm-connections/{id}/model-settings/reset", endpoint=llm_model_settings_reset, methods=["POST"]),
        Route("/api/commands",     endpoint=api_commands),
        Route("/api/prompts",            endpoint=prompts_collection, methods=["GET", "POST"]),
        Route("/api/prompts/{id}",       endpoint=prompts_update, methods=["POST"]),
        Route("/api/prompts/{id}/delete", endpoint=prompts_delete, methods=["POST"]),
        Route("/api/prompts/{id}/reset", endpoint=prompts_reset, methods=["POST"]),
        Route("/api/prompt-categories",  endpoint=prompt_categories_create, methods=["POST"]),
        Route("/api/prompt-categories/{id}", endpoint=prompt_categories_update, methods=["POST"]),
        Route("/api/prompt-categories/{id}/delete", endpoint=prompt_categories_delete, methods=["POST"]),
        Route("/api/prompt-variables",   endpoint=prompt_variables_create, methods=["POST"]),
        Route("/api/prompt-variables/{code}/delete", endpoint=prompt_variables_delete, methods=["POST"]),
    ]
