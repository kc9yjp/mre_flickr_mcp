"""Conversation compaction: replace stored history with an LLM-written summary.

Shared by the manual "Compact now" button (``routes.chat_compact``) and
auto-compact (triggered from ``loop.run_turn`` when the estimated prompt size
crosses the model's context-window threshold) — both need the exact same
transform, just from different call sites.
"""

import logging

from agent import llm, prompts_store, store
from agent.wire import coerce_null_content, wire_messages


async def summarize(username: str, nsid: str, conversation_id: str, cfg: dict) -> str:
    """Ask the LLM to summarize the stored history.

    The instruction comes from the user-editable "compact-conversation"
    prompt (falling back to its shipped default) so the same text a manual
    "Compact now" click uses is also what auto-compact uses.

    Returns "" if there's nothing to summarize or the call fails — callers
    treat an empty summary as "compaction did not happen".
    """
    messages = store.get_messages(username, conversation_id)
    if not messages:
        return ""
    coerce_null_content(messages)
    instruction_prompt = prompts_store.get_prompt_by_code(nsid, "compact-conversation")
    instruction = (
        instruction_prompt["text"] if instruction_prompt
        else prompts_store.COMPACT_PROMPT_DEFAULT
    )
    # Every other call to the model (agent turns) opens with a system
    # message before any history. This one didn't, and when the history
    # being summarized is itself just a prior compaction summary (a lone
    # assistant message, e.g. re-compacting right after a compact with no
    # new turns in between), the payload becomes bare [assistant, user] with
    # no framing at all — a shape unlike anything else this connection ever
    # sends. Some stricter backends (e.g. LM Studio's jinja chat templates)
    # fail to find "a user query" against that shape and error out, so give
    # this call the same system-first shape every other call has.
    prompt_messages = [
        {"role": "system", "content": "You are summarizing a conversation. Read the history below, then follow the instruction in the final message."},
        *wire_messages(messages),
        {"role": "user", "content": instruction},
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


async def compact(username: str, nsid: str, conversation_id: str, cfg: dict) -> str:
    """Summarize the conversation and replace its stored history in place.

    The summary is stored as a single assistant message so it renders like a
    normal chat bubble (visible to the user) and replays naturally as
    context for the next turn. Returns the summary text, or "" if
    compaction didn't happen.
    """
    summary = await summarize(username, nsid, conversation_id, cfg)
    if not summary:
        return ""
    store.replace_messages(username, conversation_id, [
        {"role": "assistant", "content": f"**Conversation compacted.**\n\n{summary}"},
    ])
    return summary
