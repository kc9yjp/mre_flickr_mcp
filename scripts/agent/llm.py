"""Streaming clients for OpenAI-compatible connections.

Two wire formats are supported, selected by a connection's ``api_mode``:
Chat Completions (``stream_chat``, ``/chat/completions``) and the newer
Responses API (``stream_responses``, ``/responses``). Both are stateless
here — the full message history is resent every call, no
``previous_response_id`` tracking — so they work uniformly whether the
backend only supports the non-stateful flavor (e.g. Ollama) or the full
stateful one (e.g. LM Studio, real OpenAI).

All wire-format quirks (SSE framing, incremental tool_call fragments) are
isolated here so connection drift is fixed in one place.
"""

import json
import time
from typing import AsyncIterator

import httpx


class LLMError(Exception):
    pass


def _auth_headers(cfg: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    return headers


def stream_for_mode(api_mode: str):
    """Return the streaming client for a resolved ``api_mode``.

    Centralizes the dispatch so loop.py and compact.py don't each grow their
    own if/elif chain as wire formats are added. Unknown modes fall back to
    Chat Completions, the most widely served flavor.
    """
    return {
        "responses": stream_responses,
        "messages": stream_messages,
        "gemini": stream_gemini,
    }.get(api_mode, stream_chat)


def _add_sampling_params(payload: dict, cfg: dict) -> None:
    """Add optional sampling/tool-use params, omitting any left blank."""
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        val = cfg.get(key)
        if val in (None, ""):
            continue
        try:
            payload[key] = float(val)
        except (TypeError, ValueError):
            pass
    seed = cfg.get("seed")
    if seed not in (None, ""):
        try:
            payload["seed"] = int(seed)
        except (TypeError, ValueError):
            pass
    tool_choice = cfg.get("tool_choice")
    if payload.get("tools") and tool_choice and tool_choice != "auto":
        payload["tool_choice"] = tool_choice


class _ToolCallAccumulator:
    """Assemble incremental tool_call deltas (index-keyed argument chunks)."""

    def __init__(self):
        self._calls: dict[int, dict] = {}

    def add(self, deltas: list[dict]) -> None:
        for d in deltas:
            idx = d.get("index", 0)
            call = self._calls.setdefault(
                idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if d.get("id"):
                call["id"] = d["id"]
            fn = d.get("function") or {}
            if fn.get("name"):
                call["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                call["function"]["arguments"] += fn["arguments"]

    def result(self) -> list[dict]:
        calls = [self._calls[i] for i in sorted(self._calls)]
        for n, call in enumerate(calls):
            if not call["id"]:  # some providers omit ids
                call["id"] = f"call_{n}"
        return calls


async def stream_chat(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict]:
    """Stream one assistant turn.

    Yields ``{"type": "delta", "text": str}`` for content tokens, then exactly
    one ``{"type": "message", "content": str, "tool_calls": list, "finish_reason": str, 
    "usage": {...}, "latency_ms": int}``.
    
    Usage contains: prompt_tokens, completion_tokens, total_tokens (if provided by LLM).
    """
    payload: dict = {
        "model": cfg.get("model", ""),
        "messages": messages,
        "stream": True,
    }
    if cfg.get("max_tokens"):
        payload["max_tokens"] = int(cfg["max_tokens"])
    if tools:
        payload["tools"] = tools
    _add_sampling_params(payload, cfg)

    url = cfg.get("base_url", "").rstrip("/") + "/chat/completions"
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(float(cfg.get("timeout_seconds") or 300), connect=15.0))

    content_parts: list[str] = []
    acc = _ToolCallAccumulator()
    finish_reason = ""
    usage: dict = {}
    start_time = time.time()

    try:
        async with client.stream("POST", url, json=payload, headers=_auth_headers(cfg)) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")[:500]
                raise LLMError(f"LLM API returned {response.status_code}: {body}")
            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                # Capture usage from final chunk if present
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                    yield {"type": "delta", "text": delta["content"]}
                if delta.get("tool_calls"):
                    acc.add(delta["tool_calls"])
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
    except httpx.HTTPError as e:
        raise LLMError(f"LLM API request failed: {e}") from e
    finally:
        if owns_client:
            await client.aclose()

    latency_ms = int((time.time() - start_time) * 1000)
    
    yield {
        "type": "message",
        "content": "".join(content_parts),
        "tool_calls": acc.result(),
        "finish_reason": finish_reason,
        "usage": usage,
        "latency_ms": latency_ms,
    }


# ── Responses API (stateless) ────────────────────────────────────────────────


def _content_to_input_parts(content) -> list[dict]:
    """Translate a chat-completions-shaped message ``content`` (a string, or
    a multimodal list of ``{"type":"text"/"image_url", ...}`` parts) into
    Responses API input content parts."""
    if isinstance(content, str) or content is None:
        return [{"type": "input_text", "text": content or ""}]
    parts = []
    for part in content:
        if part.get("type") == "text":
            parts.append({"type": "input_text", "text": part.get("text", "")})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            parts.append({"type": "input_image", "image_url": url})
    return parts


def _messages_to_responses_input(messages: list[dict]) -> list[dict]:
    """Translate chat-completions-shaped messages into Responses API input
    items. Assistant tool calls and tool results become their own flat
    ``function_call``/``function_call_output`` items rather than living
    inside a message, per the Responses API shape."""
    items: list[dict] = []
    idx = 0
    while idx < len(messages):
        m = messages[idx]
        role = m.get("role")
        content = m.get("content")

        if role in ("system", "user"):
            items.append({"role": role, "content": _content_to_input_parts(content)})

        elif role == "assistant":
            tool_calls = m.get("tool_calls") or []
            # Any text the assistant produced comes first — it was generated
            # before the tool calls that follow it in the same turn. Emitting
            # it after their function_call_output items instead (as this used
            # to) makes the replayed conversation read as if the assistant
            # already gave its post-tool-result answer, and a local model can
            # take that as its cue to reply with nothing on the next turn.
            if content:
                items.append({"role": "assistant", "content": [{"type": "output_text", "text": content}]})
            for call in tool_calls:
                items.append({
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": call["function"]["arguments"],
                })
                if idx + 1 < len(messages):
                    next_msg = messages[idx + 1]
                    if (
                        next_msg.get("role") == "tool"
                        and next_msg.get("tool_call_id") == call["id"]
                    ):
                        next_content = next_msg.get("content")
                        if isinstance(next_content, str):
                            output = next_content
                        else:
                            output = next(
                                (p["text"] for p in (next_content or []) if p.get("type") == "text"),
                                "",
                            )
                        items.append({
                            "type": "function_call_output",
                            "call_id": next_msg.get("tool_call_id", ""),
                            "output": output,
                        })
                        idx += 1

        elif role == "tool":
            if isinstance(content, str):
                output = content
            else:
                output = next((p["text"] for p in (content or []) if p.get("type") == "text"), "")
            items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": output,
            })

        idx += 1
    return items


def _tools_to_responses(tools: list[dict] | None) -> list[dict] | None:
    """Flatten chat-completions-shaped tool defs into the Responses API's
    flat function-tool shape (no nested ``"function"`` wrapper)."""
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        })
    return out


class _ResponsesToolCallAccumulator:
    """Assemble function-call items keyed by ``item_id``.

    The Responses API keys function-call argument deltas by ``item_id``,
    not the chat-completions ``index`` field ``_ToolCallAccumulator`` uses,
    so this is a separate, differently-keyed accumulator.
    """

    def __init__(self):
        self._calls: dict[str, dict] = {}
        self._order: list[str] = []

    def _slot(self, item_id: str) -> dict:
        if item_id not in self._calls:
            self._calls[item_id] = {
                "id": item_id, "type": "function",
                "function": {"name": "", "arguments": ""},
            }
            self._order.append(item_id)
        return self._calls[item_id]

    def open(self, item_id: str, call_id: str, name: str) -> None:
        call = self._slot(item_id)
        call["id"] = call_id or item_id
        call["function"]["name"] = name or ""

    def add_delta(self, item_id: str, delta: str) -> None:
        self._slot(item_id)["function"]["arguments"] += delta or ""

    def finalize(self, item_id: str, arguments: str | None) -> None:
        if arguments is not None:
            self._slot(item_id)["function"]["arguments"] = arguments

    def result(self) -> list[dict]:
        return [self._calls[i] for i in self._order]


async def stream_responses(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict]:
    """Stream one assistant turn via the Responses API (``/responses``).

    Same yield contract as ``stream_chat``. Stateless: the full translated
    ``input`` is resent every call — no ``previous_response_id`` — so this
    works whether the backend only supports the non-stateful flavor (e.g.
    Ollama v0.13.3+) or the full stateful one (e.g. LM Studio, real OpenAI).
    """
    payload: dict = {
        "model": cfg.get("model", ""),
        "input": _messages_to_responses_input(messages),
        "stream": True,
    }
    if cfg.get("max_tokens"):
        payload["max_output_tokens"] = int(cfg["max_tokens"])
    responses_tools = _tools_to_responses(tools)
    if responses_tools:
        payload["tools"] = responses_tools
    _add_sampling_params(payload, cfg)

    url = cfg.get("base_url", "").rstrip("/") + "/responses"
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(float(cfg.get("timeout_seconds") or 300), connect=15.0))

    content_parts: list[str] = []
    acc = _ResponsesToolCallAccumulator()
    usage: dict = {}
    start_time = time.time()

    try:
        async with client.stream("POST", url, json=payload, headers=_auth_headers(cfg)) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")[:500]
                raise LLMError(f"LLM API returned {response.status_code}: {body}")
            current_event = None
            async for line in response.aiter_lines():
                line = line.rstrip("\n")
                if not line:
                    current_event = None
                    continue
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                etype = event.get("type") or current_event or ""

                if etype == "response.output_text.delta":
                    delta = event.get("delta", "")
                    if delta:
                        content_parts.append(delta)
                        yield {"type": "delta", "text": delta}

                elif etype == "response.output_item.added":
                    item = event.get("item") or {}
                    if item.get("type") == "function_call":
                        acc.open(item.get("id", ""), item.get("call_id", ""), item.get("name", ""))

                elif etype == "response.function_call_arguments.delta":
                    acc.add_delta(event.get("item_id", ""), event.get("delta", ""))

                elif etype == "response.function_call_arguments.done":
                    acc.finalize(event.get("item_id", ""), event.get("arguments"))

                elif etype == "response.completed":
                    raw_usage = (event.get("response") or {}).get("usage") or {}
                    if raw_usage:
                        usage = {
                            "prompt_tokens": raw_usage.get("input_tokens", 0),
                            "completion_tokens": raw_usage.get("output_tokens", 0),
                            "total_tokens": raw_usage.get("total_tokens", 0),
                        }

                elif etype in ("response.failed", "response.error", "error"):
                    resp_obj = event.get("response") or event
                    err = (resp_obj.get("error") or {}).get("message") or "Responses API request failed"
                    raise LLMError(err)
    except httpx.HTTPError as e:
        raise LLMError(f"LLM API request failed: {e}") from e
    finally:
        if owns_client:
            await client.aclose()

    tool_calls = acc.result()
    latency_ms = int((time.time() - start_time) * 1000)

    yield {
        "type": "message",
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "finish_reason": "tool_calls" if tool_calls else "stop",
        "usage": usage,
        "latency_ms": latency_ms,
    }


# ── Messages API (Anthropic, stateless) ──────────────────────────────────────

# Anthropic requires this header on every request. The version is a fixed
# date string; bump it only if Zen starts requiring a newer one.
_ANTHROPIC_VERSION = "2023-06-01"


def _messages_to_anthropic_input(messages: list[dict]) -> tuple[str, list[dict]]:
    """Translate chat-completions-shaped messages into Anthropic Messages API
    shape. Returns (system_prompt, messages).

    Differences from the OpenAI shape that matter here:
      * system content is a top-level ``system`` param, not a message — all
        leading/inline system messages are concatenated into it.
      * assistant tool calls become ``tool_use`` content blocks; tool results
        become ``tool_result`` blocks inside a ``user`` message.
      * images ride inside a message's ``content`` blocks as ``image`` parts
        (base64 or url source), not OpenAI's ``image_url`` wrapper.
    """
    system_parts: list[str] = []
    out: list[dict] = []

    def _content_blocks(content) -> list[dict]:
        """Build Anthropic content blocks from a string or OpenAI part list."""
        if isinstance(content, str) or content is None:
            return [{"type": "text", "text": content or ""}]
        blocks = []
        for part in content:
            if part.get("type") == "text":
                blocks.append({"type": "text", "text": part.get("text", "")})
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    # data:<media-type>;base64,<data> -> Anthropic base64 source
                    try:
                        header, b64 = url[5:].split(",", 1)
                        media_type = header.split(";")[0] or "image/jpeg"
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64},
                        })
                    except ValueError:
                        pass
                elif url:
                    blocks.append({"type": "image", "source": {"type": "url", "url": url}})
        return blocks or [{"type": "text", "text": ""}]

    def _tool_text(content) -> str:
        if isinstance(content, str):
            return content
        return next((p["text"] for p in (content or []) if p.get("type") == "text"), "")

    idx = 0
    while idx < len(messages):
        m = messages[idx]
        role = m.get("role")
        content = m.get("content")

        if role == "system":
            text = content if isinstance(content, str) else _tool_text(content)
            if text:
                system_parts.append(text)

        elif role == "user":
            out.append({"role": "user", "content": _content_blocks(content)})

        elif role == "assistant":
            blocks: list[dict] = []
            if content:
                blocks.extend(_content_blocks(content))
            for call in (m.get("tool_calls") or []):
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except (ValueError, KeyError):
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": call.get("id", ""),
                    "name": (call.get("function") or {}).get("name", ""),
                    "input": args,
                })
            if blocks:
                out.append({"role": "assistant", "content": blocks})

        elif role == "tool":
            # Anthropic tool results live in a user message as tool_result
            # blocks. Merge consecutive tool results into one user turn.
            result_block = {
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": _tool_text(content),
            }
            if out and out[-1].get("role") == "user" and all(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in out[-1]["content"]
            ):
                out[-1]["content"].append(result_block)
            else:
                out.append({"role": "user", "content": [result_block]})

        idx += 1

    return "\n\n".join(system_parts), out


def _tools_to_anthropic(tools: list[dict] | None) -> list[dict] | None:
    """Translate chat-completions-shaped tool defs into Anthropic's shape
    (``input_schema`` instead of ``parameters``, no nested ``function``)."""
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


async def stream_messages(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict]:
    """Stream one assistant turn via the Anthropic Messages API (``/messages``).

    Same yield contract as ``stream_chat``. Stateless: the full translated
    message history is resent every call. Used for Zen's claude-*/qwen* models.
    """
    system_prompt, anthropic_messages = _messages_to_anthropic_input(messages)

    payload: dict = {
        "model": cfg.get("model", ""),
        "messages": anthropic_messages,
        # Anthropic requires max_tokens explicitly.
        "max_tokens": int(cfg.get("max_tokens") or 1024),
        "stream": True,
    }
    if system_prompt:
        payload["system"] = system_prompt
    anthropic_tools = _tools_to_anthropic(tools)
    if anthropic_tools:
        payload["tools"] = anthropic_tools
    # Anthropic supports temperature/top_p/seed but not the penalty fields.
    for key in ("temperature", "top_p"):
        val = cfg.get(key)
        if val not in (None, ""):
            try:
                payload[key] = float(val)
            except (TypeError, ValueError):
                pass

    url = cfg.get("base_url", "").rstrip("/") + "/messages"
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": _ANTHROPIC_VERSION,
    }
    if cfg.get("api_key"):
        # Zen accepts the Anthropic-style x-api-key; Bearer works too, but
        # x-api-key is the canonical Anthropic auth header.
        headers["x-api-key"] = cfg["api_key"]

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(float(cfg.get("timeout_seconds") or 300), connect=15.0))

    content_parts: list[str] = []
    # Anthropic keys tool-call argument deltas by content-block index.
    tool_calls: dict[int, dict] = {}
    tool_order: list[int] = []
    usage: dict = {}
    stop_reason = ""
    start_time = time.time()

    try:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")[:500]
                raise LLMError(f"LLM API returned {response.status_code}: {body}")
            current_event = None
            async for line in response.aiter_lines():
                line = line.rstrip("\n")
                if not line:
                    current_event = None
                    continue
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                etype = event.get("type") or current_event or ""

                if etype == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        i = event.get("index", 0)
                        tool_calls[i] = {
                            "id": block.get("id", f"call_{i}"), "type": "function",
                            "function": {"name": block.get("name", ""), "arguments": ""},
                        }
                        tool_order.append(i)

                elif etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        content_parts.append(delta["text"])
                        yield {"type": "delta", "text": delta["text"]}
                    elif delta.get("type") == "input_json_delta":
                        i = event.get("index", 0)
                        if i in tool_calls:
                            tool_calls[i]["function"]["arguments"] += delta.get("partial_json", "")

                elif etype == "message_delta":
                    d = event.get("delta") or {}
                    if d.get("stop_reason"):
                        stop_reason = d["stop_reason"]
                    u = event.get("usage") or {}
                    if u:
                        usage.setdefault("prompt_tokens", u.get("input_tokens", 0))
                        usage["completion_tokens"] = u.get("output_tokens", 0)

                elif etype == "message_start":
                    u = ((event.get("message") or {}).get("usage")) or {}
                    if u:
                        usage["prompt_tokens"] = u.get("input_tokens", 0)

                elif etype == "error":
                    err = (event.get("error") or {}).get("message") or "Messages API request failed"
                    raise LLMError(err)
    except httpx.HTTPError as e:
        raise LLMError(f"LLM API request failed: {e}") from e
    finally:
        if owns_client:
            await client.aclose()

    assembled = [tool_calls[i] for i in tool_order]
    if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
        usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    latency_ms = int((time.time() - start_time) * 1000)

    yield {
        "type": "message",
        "content": "".join(content_parts),
        "tool_calls": assembled,
        "finish_reason": "tool_calls" if (assembled or stop_reason == "tool_use") else "stop",
        "usage": usage,
        "latency_ms": latency_ms,
    }


# ── Gemini (per-model URL, stateless) ────────────────────────────────────────


def _messages_to_gemini_input(messages: list[dict]) -> tuple[str, list[dict]]:
    """Translate chat-completions-shaped messages into Gemini's ``contents``
    shape. Returns (system_instruction, contents).

    Gemini uses ``user``/``model`` roles (not ``assistant``), inline ``parts``
    rather than a flat content string, and function calls/results as
    ``functionCall``/``functionResponse`` parts. System content becomes a
    top-level ``system_instruction``.
    """
    system_parts: list[str] = []
    contents: list[dict] = []

    def _parts(content) -> list[dict]:
        if isinstance(content, str) or content is None:
            return [{"text": content or ""}]
        parts = []
        for part in content:
            if part.get("type") == "text":
                parts.append({"text": part.get("text", "")})
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    try:
                        header, b64 = url[5:].split(",", 1)
                        media_type = header.split(";")[0] or "image/jpeg"
                        parts.append({"inlineData": {"mimeType": media_type, "data": b64}})
                    except ValueError:
                        pass
        return parts or [{"text": ""}]

    def _tool_text(content) -> str:
        if isinstance(content, str):
            return content
        return next((p["text"] for p in (content or []) if p.get("type") == "text"), "")

    for m in messages:
        role = m.get("role")
        content = m.get("content")

        if role == "system":
            text = content if isinstance(content, str) else _tool_text(content)
            if text:
                system_parts.append(text)

        elif role == "user":
            contents.append({"role": "user", "parts": _parts(content)})

        elif role == "assistant":
            parts: list[dict] = []
            if content:
                parts.extend(_parts(content))
            for call in (m.get("tool_calls") or []):
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except (ValueError, KeyError):
                    args = {}
                parts.append({
                    "functionCall": {
                        "name": (call.get("function") or {}).get("name", ""),
                        "args": args,
                    }
                })
            if parts:
                contents.append({"role": "model", "parts": parts})

        elif role == "tool":
            # Gemini function responses are user-role parts keyed by the
            # function name (there's no tool_call_id; use the name).
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": m.get("name", ""),
                        "response": {"result": _tool_text(content)},
                    }
                }],
            })

    return "\n\n".join(system_parts), contents


def _tools_to_gemini(tools: list[dict] | None) -> list[dict] | None:
    """Translate chat-completions-shaped tool defs into Gemini's
    ``functionDeclarations`` shape."""
    if not tools:
        return None
    decls = []
    for t in tools:
        fn = t.get("function", t)
        decls.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return [{"functionDeclarations": decls}]


async def stream_gemini(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict]:
    """Stream one assistant turn via Zen's Gemini endpoint.

    Zen serves Gemini over a per-model URL (``/v1/models/{model-id}``) rather
    than a shared ``/chat/completions`` path — see
    https://opencode.ai/docs/zen#endpoints. Same yield contract as
    ``stream_chat``. Stateless: full translated ``contents`` resent each call.
    """
    system_instruction, contents = _messages_to_gemini_input(messages)

    payload: dict = {"contents": contents}
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    gemini_tools = _tools_to_gemini(tools)
    if gemini_tools:
        payload["tools"] = gemini_tools
    generation_config: dict = {}
    if cfg.get("max_tokens"):
        generation_config["maxOutputTokens"] = int(cfg["max_tokens"])
    for key in ("temperature", "top_p"):
        val = cfg.get(key)
        if val not in (None, ""):
            try:
                generation_config[{"top_p": "topP"}.get(key, key)] = float(val)
            except (TypeError, ValueError):
                pass
    if generation_config:
        payload["generationConfig"] = generation_config

    # Per-model URL: base_url already ends in /v1, so append /models/{id}.
    model_id = cfg.get("model", "")
    url = cfg.get("base_url", "").rstrip("/") + f"/models/{model_id}"

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(float(cfg.get("timeout_seconds") or 300), connect=15.0))

    content_parts: list[str] = []
    tool_calls: list[dict] = []
    usage: dict = {}
    start_time = time.time()

    try:
        async with client.stream("POST", url, json=payload, headers=_auth_headers(cfg)) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")[:500]
                raise LLMError(f"LLM API returned {response.status_code}: {body}")
            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usageMetadata"):
                    um = chunk["usageMetadata"]
                    usage = {
                        "prompt_tokens": um.get("promptTokenCount", 0),
                        "completion_tokens": um.get("candidatesTokenCount", 0),
                        "total_tokens": um.get("totalTokenCount", 0),
                    }
                for cand in (chunk.get("candidates") or []):
                    for part in ((cand.get("content") or {}).get("parts") or []):
                        if part.get("text"):
                            content_parts.append(part["text"])
                            yield {"type": "delta", "text": part["text"]}
                        elif part.get("functionCall"):
                            fc = part["functionCall"]
                            tool_calls.append({
                                "id": f"call_{len(tool_calls)}",
                                "type": "function",
                                "function": {
                                    "name": fc.get("name", ""),
                                    "arguments": json.dumps(fc.get("args") or {}),
                                },
                            })
    except httpx.HTTPError as e:
        raise LLMError(f"LLM API request failed: {e}") from e
    finally:
        if owns_client:
            await client.aclose()

    latency_ms = int((time.time() - start_time) * 1000)

    yield {
        "type": "message",
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "finish_reason": "tool_calls" if tool_calls else "stop",
        "usage": usage,
        "latency_ms": latency_ms,
    }


# ── Model filtering ──────────────────────────────────────────────────────────

# Models known to NOT support vision
_VISION_UNSUPPORTED_MODELS = {
    # Most open-source models don't support vision
    "llama2", "llama3", "llama3.1", "llama3.2",
    "mistral", "mixtral",
    "neural-chat", "dolphin-mixtral",
    # Zen models without vision support
    "deepseek-v4-pro", "deepseek-v4-flash",
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "glm-5.2", "glm-5.1", "glm-5",
    "kimi-k2.5", "kimi-k2.6", "kimi-k2.7-code",
    "big-pickle",
    "mimo-v2.5-free",
    "laguna-s-2.1-free",
    "ling-3.0-flash-free",
    "north-mini-code-free",
    "nemotron-3-ultra-free",
    "deepseek-v4-flash-free",
    # Add others as needed
}


def model_supports_vision(model_id: str) -> bool:
    """Check if a model supports vision/image input."""
    return model_id not in _VISION_UNSUPPORTED_MODELS


# ── Model listing ─────────────────────────────────────────────────────────────


async def list_models(
    base_url: str,
    api_key: str = "",
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Fetch available model ids from ``GET {base_url}/models``.

    Works for any connection exposing the OpenAI-compatible /models endpoint
    (Ollama, OpenCode Zen, LM Studio, ...). Returns the full unfiltered,
    sorted list — per-connection ``disabled_models`` filtering happens in
    routes.py, not here.
    """
    url = base_url.rstrip("/") + "/models"
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            body = resp.text[:500]
            raise LLMError(f"Model list returned {resp.status_code}: {body}")
        data = resp.json()
        models = data.get("data") or data.get("models") or []
        ids = []
        for m in models:
            mid = m.get("id") if isinstance(m, dict) else m
            if isinstance(mid, str) and mid.strip():
                ids.append(mid.strip())
        return sorted(ids)
    except httpx.HTTPError as e:
        raise LLMError(f"Failed to reach model list endpoint: {e}") from e
    finally:
        if owns_client:
            await client.aclose()
