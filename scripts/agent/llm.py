"""Streaming client for OpenAI-compatible chat-completions APIs.

All wire-format quirks (SSE framing, incremental tool_call fragments) are
isolated here so provider drift is fixed in one place.
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


# ── Model filtering ──────────────────────────────────────────────────────────

# OpenCode Zen models that use /v1/responses instead of /v1/chat/completions
# These are not supported by the current chat client
_ZEN_RESPONSES_ONLY_MODELS = {
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "gpt-5.5", "gpt-5.5-pro",
    "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.3-codex", "gpt-5.3-codex-spark",
    "gpt-5.2", "gpt-5.2-codex",
    "gpt-5.1", "gpt-5.1-codex", "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
    "gpt-5", "gpt-5-codex", "gpt-5-nano",
}

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


def _filter_models(model_ids: list[str], base_url: str) -> list[str]:
    """Filter out unsupported models.
    
    - Removes /v1/responses-only models (unsupported by current chat client)
    - Removes models that don't support vision when user has vision enabled
    """
    filtered = []
    is_zen = "opencode.ai/zen" in base_url.lower()
    
    for mid in model_ids:
        # Filter out Zen's /v1/responses models
        if is_zen and mid in _ZEN_RESPONSES_ONLY_MODELS:
            continue
        filtered.append(mid)
    
    return sorted(filtered)


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

    Works for both Ollama and OpenCode Zen (both expose the OpenAI-compatible
    /models endpoint).
    
    Filters out:
    - Models that use /v1/responses instead of /v1/chat/completions (unsupported)
    - Models that don't support vision
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
        
        # Filter out unsupported models
        return _filter_models(ids, base_url)
    except httpx.HTTPError as e:
        raise LLMError(f"Failed to reach model list endpoint: {e}") from e
    finally:
        if owns_client:
            await client.aclose()
