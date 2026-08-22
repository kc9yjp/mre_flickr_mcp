"""Wire-format helpers shared by ``loop.py`` (live turns) and ``compact.py``
(summarization calls) — both need to hand the same stored history to an LLM.
"""


def coerce_null_content(messages: list[dict]) -> None:
    """Normalize a null assistant "content" field to "" in place.

    Null content on a tool-call-only assistant turn is valid OpenAI wire
    format, but some OpenAI-compatible proxies (e.g. OpenCode Zen, when
    translating to Anthropic's Messages API) choke on it and return
    "invalid message content type: <nil>". Conversations stored before this
    was caught may still have a null persisted, so this also heals replayed
    history, not just newly generated turns."""
    for m in messages:
        if m.get("role") == "assistant" and m.get("content") is None:
            m["content"] = ""


def wire_messages(messages: list[dict]) -> list[dict]:
    """Expand tool-result messages that carry an image into wire-safe form.

    OpenAI-compatible Chat Completions (and the Responses API translation in
    llm.py) only allow *text* content in a ``"tool"`` role message — images
    are only valid inside a ``"user"`` message. A vision-enabled tool result
    stores its image_url part directly on the tool message for simplicity,
    but that's spec-invalid on the wire: compliant servers silently drop or
    ignore it, so the model never actually sees the image even with vision
    enabled. Split any such tool message into a text-only tool message
    followed by a synthetic user message carrying the image(s) — this only
    affects what's sent for this call, not what's stored or shown in the
    chat UI.
    """
    out = []
    for m in messages:
        content = m.get("content")
        if m.get("role") == "tool" and isinstance(content, list):
            text = "\n".join(p["text"] for p in content if p.get("type") == "text")
            images = [p for p in content if p.get("type") == "image_url"]
            out.append({**m, "content": text})
            if images:
                out.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "(image from the tool call above, for visual inspection)"},
                        *images,
                    ],
                })
        else:
            out.append(m)

    # A compacted conversation's stored history starts as a single assistant
    # message (the summary bubble, see compact.py) — so every turn after a
    # compaction sends a payload shaped [system…, assistant, user…] with an
    # assistant turn first and no user message ever before it. Some backends'
    # chat templates (e.g. LM Studio's stricter jinja ones) can't find "a
    # user query" in that shape and error out. Insert a placeholder user
    # turn right after any leading system messages so every payload starts
    # its actual dialogue on a user turn, same as a fresh conversation would.
    idx = 0
    while idx < len(out) and out[idx].get("role") == "system":
        idx += 1
    if idx < len(out) and out[idx].get("role") == "assistant":
        out.insert(idx, {"role": "user", "content": "(continuing this conversation)"})

    return out


_REDACT_MAX_LEN = 4_000


def redact_large_strings(obj, max_len: int = _REDACT_MAX_LEN):
    """Deep-copy ``obj``, truncating any string longer than ``max_len``.

    Used for the chat transparency log (agent.loop's llm_calls capture)
    before a request payload is persisted or sent down the SSE stream. A
    vision-enabled call's payload can carry base64 image data inline — the
    exact key differs per provider (``image_url.url``, Anthropic's
    ``source.data``, Gemini's ``inlineData.data``, ...) so rather than
    special-casing each wire shape, any long string anywhere in the
    structure is capped generically. This only affects the *logged copy*;
    the real request already went out over the wire unmodified before this
    is ever called.
    """
    if isinstance(obj, str):
        if len(obj) <= max_len:
            return obj
        return f"{obj[:max_len]}…(truncated, {len(obj)} chars total)"
    if isinstance(obj, dict):
        return {k: redact_large_strings(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_large_strings(v, max_len) for v in obj]
    return obj


def approx_tokens(messages: list[dict]) -> int:
    """Rough token-count estimate (chars / 4) used only to decide whether to
    auto-compact — no tokenizer dependency, so it's a heuristic, not exact.
    """
    total_chars = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total_chars += len(part.get("text", ""))
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            total_chars += len(fn.get("name", "")) + len(fn.get("arguments", ""))
    return total_chars // 4
