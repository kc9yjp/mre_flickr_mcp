"""Conversation compaction: replace stored history with an LLM-written summary.

Shared by the manual "Compact now" button (``routes.chat_compact``) and
auto-compact (triggered from ``loop.run_turn`` when the estimated prompt size
crosses the model's context-window threshold) — both need the exact same
transform, just from different call sites.
"""

import logging

from agent import llm, store
from agent.wire import coerce_null_content, wire_messages

COMPACT_INSTRUCTION = (
    "Summarize this entire conversation so it can continue seamlessly without "
    "the full history above. Capture what the user asked for and why, "
    "decisions made, any photo/album/group ids or titles referenced, and "
    "anything left unresolved. Be concise but keep the specifics the "
    "assistant will need to keep working correctly. Write the summary "
    "itself, not a description of writing one."
)


async def summarize(username: str, conversation_id: str, cfg: dict) -> str:
    """Ask the LLM to summarize the stored history.

    Returns "" if there's nothing to summarize or the call fails — callers
    treat an empty summary as "compaction did not happen".
    """
    messages = store.get_messages(username, conversation_id)
    if not messages:
        return ""
    coerce_null_content(messages)
    prompt_messages = [
        *wire_messages(messages),
        {"role": "user", "content": COMPACT_INSTRUCTION},
    ]
    stream_fn = llm.stream_responses if cfg.get("api_mode") == "responses" else llm.stream_chat
    final = None
    try:
        async for event in stream_fn(cfg, prompt_messages):
            if event["type"] != "delta":
                final = event
    except llm.LLMError:
        logging.exception("agent: compaction summarize call failed")
        return ""
    return ((final or {}).get("content") or "").strip()


async def compact(username: str, conversation_id: str, cfg: dict) -> str:
    """Summarize the conversation and replace its stored history in place.

    The summary is stored as a single assistant message so it renders like a
    normal chat bubble (visible to the user) and replays naturally as
    context for the next turn. Returns the summary text, or "" if
    compaction didn't happen.
    """
    summary = await summarize(username, conversation_id, cfg)
    if not summary:
        return ""
    store.replace_messages(username, conversation_id, [
        {"role": "assistant", "content": f"**Conversation compacted.**\n\n{summary}"},
    ])
    return summary
