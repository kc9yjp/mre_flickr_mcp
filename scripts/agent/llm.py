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
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))

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
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "user"):
            items.append({"role": role, "content": _content_to_input_parts(content)})
        elif role == "assistant":
            for call in m.get("tool_calls") or []:
                items.append({
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": call["function"]["arguments"],
                })
            if content:
                items.append({"role": "assistant", "content": [{"type": "output_text", "text": content}]})
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
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))

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
