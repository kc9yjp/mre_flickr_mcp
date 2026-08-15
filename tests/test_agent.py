"""Tests for the chat agent: schema conversion, stream parsing, and the loop."""

import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

USERNAME = "agentuser"
NSID = "12345@N00"
USER = {"nsid": NSID, "username": USERNAME}

CFG = {"base_url": "http://llm.test/v1", "api_key": "", "model": "test-model", "max_tokens": 512}


@pytest.fixture()
def user_db(mem_db, tmp_path):
    """Per-user DB dir holding the seeded flickr.db; chat.db lands beside it."""
    user_dir = tmp_path / "agentdata" / USERNAME
    user_dir.mkdir(parents=True)
    shutil.copy(mem_db, user_dir / "flickr.db")
    with patch("db._DATA_DIR", str(tmp_path / "agentdata")):
        yield str(user_dir)


@pytest.fixture(autouse=True)
def _agent_creds_dir(tmp_path):
    """Keep agent.prompts_store / agent.settings storage inside tmp_path.

    Without this, every test that reaches agent.loop.run_turn (or calls
    prompts_store/commands directly) writes real files under the real
    ~/.flickr_mcp on the machine running the tests.
    """
    creds_dir = str(tmp_path / "flickr_mcp_creds")
    with patch("agent.prompts_store._CREDS_BASE", creds_dir), \
         patch("agent.settings._CREDS_BASE", creds_dir):
        yield


# --- schema ---

def test_write_tools_all_have_handlers():
    import mcp_tools
    from agent import schema

    assert schema.WRITE_TOOLS <= set(mcp_tools._HANDLERS)


def test_read_tools_not_write_gated():
    from agent import schema

    for name in ("search_photos", "get_photo", "get_summary", "fetch_photo_image"):
        assert name not in schema.WRITE_TOOLS


def test_to_openai_tools_shape():
    from agent import schema

    tools = schema.to_openai_tools()
    assert len(tools) == len(schema.all_tools())
    sample = tools[0]
    assert sample["type"] == "function"
    assert set(sample["function"]) == {"name", "description", "parameters"}
    assert isinstance(sample["function"]["parameters"], dict)


# --- llm stream parsing ---

def _sse_body(chunks: list[dict]) -> bytes:
    frames = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode()


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


@pytest.mark.asyncio
async def test_stream_chat_accumulates_content_and_tool_calls():
    from agent import llm

    chunks = [
        _chunk({"content": "Hel"}),
        _chunk({"content": "lo"}),
        _chunk({"tool_calls": [{"index": 0, "id": "call_a",
                                "function": {"name": "get_", "arguments": ""}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"name": "photo", "arguments": "{\"id\":"}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"arguments": " \"42\"}"}}]}, finish="tool_calls"),
    ]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=_sse_body(chunks))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        events = [e async for e in llm.stream_chat(CFG, [], client=client)]

    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Hello"
    final = events[-1]
    assert final["type"] == "message"
    assert final["content"] == "Hello"
    assert final["finish_reason"] == "tool_calls"
    (call,) = final["tool_calls"]
    assert call["function"]["name"] == "get_photo"
    assert json.loads(call["function"]["arguments"]) == {"id": "42"}


@pytest.mark.asyncio
async def test_stream_chat_http_error_raises():
    from agent import llm

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, content=b"boom")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(llm.LLMError, match="500"):
            async for _ in llm.stream_chat(CFG, [], client=client):
                pass


@pytest.mark.asyncio
async def test_stream_chat_owned_client_uses_cfg_timeout():
    """When no client is passed in, stream_chat must build its own
    httpx.AsyncClient using cfg['timeout_seconds'] as the read timeout —
    not the old hardcoded 300s — so a per-connection override actually
    takes effect."""
    from agent import llm

    captured = {}

    class _FakeClient:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        def stream(self, *a, **kw):
            raise RuntimeError("stop before any real network call")

        async def aclose(self):
            pass

    with patch("agent.llm.httpx.AsyncClient", _FakeClient):
        with pytest.raises(RuntimeError):
            async for _ in llm.stream_chat({**CFG, "timeout_seconds": 900}, []):
                pass

    assert captured["timeout"].read == 900
    assert captured["timeout"].connect == 15.0


# --- llm stream parsing (Responses API) ---

def _responses_sse_body(events: list[tuple[str, dict]]) -> bytes:
    frames = [f"event: {etype}\ndata: {json.dumps(payload)}\n\n" for etype, payload in events]
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode()


@pytest.mark.asyncio
async def test_stream_responses_accumulates_content_and_tool_calls():
    from agent import llm

    events = [
        ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "Hel"}),
        ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "lo"}),
        ("response.output_item.added", {"type": "response.output_item.added", "item": {
            "type": "function_call", "id": "item1", "call_id": "call_a", "name": "get_photo",
        }}),
        ("response.function_call_arguments.delta", {
            "type": "response.function_call_arguments.delta", "item_id": "item1", "delta": "{\"id\":",
        }),
        ("response.function_call_arguments.delta", {
            "type": "response.function_call_arguments.delta", "item_id": "item1", "delta": " \"42\"}",
        }),
        ("response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done", "item_id": "item1",
            "arguments": "{\"id\": \"42\"}",
        }),
        ("response.completed", {"type": "response.completed", "response": {
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }}),
    ]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=_responses_sse_body(events))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        results = [e async for e in llm.stream_responses(CFG, [], client=client)]

    deltas = [e["text"] for e in results if e["type"] == "delta"]
    assert "".join(deltas) == "Hello"
    final = results[-1]
    assert final["type"] == "message"
    assert final["content"] == "Hello"
    assert final["finish_reason"] == "tool_calls"
    (call,) = final["tool_calls"]
    assert call["id"] == "call_a"
    assert call["function"]["name"] == "get_photo"
    assert json.loads(call["function"]["arguments"]) == {"id": "42"}
    assert final["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


@pytest.mark.asyncio
async def test_stream_responses_http_error_raises():
    from agent import llm

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, content=b"boom")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(llm.LLMError, match="500"):
            async for _ in llm.stream_responses(CFG, [], client=client):
                pass


# --- llm stream parsing (Messages / Anthropic API) ---

def _messages_sse_body(events: list[tuple[str, dict]]) -> bytes:
    frames = [f"event: {etype}\ndata: {json.dumps(payload)}\n\n" for etype, payload in events]
    return "".join(frames).encode()


@pytest.mark.asyncio
async def test_stream_messages_accumulates_content_and_tool_calls():
    from agent import llm

    events = [
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 12}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "Hel"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "lo"}}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
                                 "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_photo"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta", "partial_json": "{\"id\":"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta", "partial_json": " \"42\"}"}}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 7}}),
    ]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=_messages_sse_body(events))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        results = [e async for e in llm.stream_messages(CFG, [], client=client)]

    deltas = [e["text"] for e in results if e["type"] == "delta"]
    assert "".join(deltas) == "Hello"
    final = results[-1]
    assert final["type"] == "message"
    assert final["content"] == "Hello"
    assert final["finish_reason"] == "tool_calls"
    (call,) = final["tool_calls"]
    assert call["id"] == "toolu_1"
    assert call["function"]["name"] == "get_photo"
    assert json.loads(call["function"]["arguments"]) == {"id": "42"}
    assert final["usage"]["prompt_tokens"] == 12
    assert final["usage"]["completion_tokens"] == 7


@pytest.mark.asyncio
async def test_stream_messages_http_error_raises():
    from agent import llm

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, content=b"boom")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(llm.LLMError, match="500"):
            async for _ in llm.stream_messages(CFG, [], client=client):
                pass


def test_messages_translator_splits_system_and_tool_calls():
    """system content becomes a top-level param; assistant tool calls become
    tool_use blocks; tool results become tool_result blocks in a user turn."""
    from agent import llm

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "let me check", "tool_calls": [
            {"id": "call_a", "type": "function",
             "function": {"name": "get_photo", "arguments": "{\"id\": \"42\"}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_a", "content": "photo data"},
    ]
    system, out = llm._messages_to_anthropic_input(messages)

    assert system == "You are helpful."
    assert out[0] == {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    assistant = out[1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "text", "text": "let me check"}
    assert assistant["content"][1]["type"] == "tool_use"
    assert assistant["content"][1]["input"] == {"id": "42"}
    tool_result = out[2]
    assert tool_result["role"] == "user"
    assert tool_result["content"][0]["type"] == "tool_result"
    assert tool_result["content"][0]["tool_use_id"] == "call_a"


# --- llm stream parsing (Gemini API) ---

@pytest.mark.asyncio
async def test_stream_gemini_accumulates_content_and_tool_calls():
    from agent import llm

    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]},
        {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "get_photo", "args": {"id": "42"}}}]}}]},
        {"usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4, "totalTokenCount": 13}},
    ]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=_sse_body(chunks))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        results = [e async for e in llm.stream_gemini(CFG, [], client=client)]

    deltas = [e["text"] for e in results if e["type"] == "delta"]
    assert "".join(deltas) == "Hello"
    final = results[-1]
    assert final["content"] == "Hello"
    assert final["finish_reason"] == "tool_calls"
    (call,) = final["tool_calls"]
    assert call["function"]["name"] == "get_photo"
    assert json.loads(call["function"]["arguments"]) == {"id": "42"}
    assert final["usage"] == {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}


@pytest.mark.asyncio
async def test_stream_gemini_uses_per_model_url():
    """Gemini is served over /v1/models/{model-id}, not a shared path."""
    from agent import llm

    seen = {}

    def _handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=_sse_body([{"candidates": [{"content": {"parts": [{"text": "x"}]}}]}]))

    transport = httpx.MockTransport(_handler)
    cfg = {**CFG, "base_url": "https://opencode.ai/zen/v1", "model": "gemini-3-flash"}
    async with httpx.AsyncClient(transport=transport) as client:
        async for _ in llm.stream_gemini(cfg, [], client=client):
            pass

    assert seen["url"] == "https://opencode.ai/zen/v1/models/gemini-3-flash"


# --- stream_for_mode dispatch ---

def test_stream_for_mode_maps_each_wire_format():
    from agent import llm

    assert llm.stream_for_mode("responses") is llm.stream_responses
    assert llm.stream_for_mode("messages") is llm.stream_messages
    assert llm.stream_for_mode("gemini") is llm.stream_gemini
    assert llm.stream_for_mode("chat_completions") is llm.stream_chat
    # Unknown modes degrade to chat_completions.
    assert llm.stream_for_mode("something-else") is llm.stream_chat


# --- store ---

def test_store_roundtrip(user_db):
    from agent import store

    conv = store.create_conversation(USERNAME, "hello world")
    assert store.conversation_exists(USERNAME, conv)
    store.append_message(USERNAME, conv, {"role": "user", "content": "hello"})
    store.append_message(USERNAME, conv, {"role": "assistant", "content": "hi"})
    msgs = store.get_messages(USERNAME, conv)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert store.list_conversations(USERNAME)[0]["id"] == conv
    store.delete_conversation(USERNAME, conv)
    assert not store.conversation_exists(USERNAME, conv)


def test_store_replace_messages(user_db):
    from agent import store

    conv = store.create_conversation(USERNAME, "t")
    store.append_message(USERNAME, conv, {"role": "user", "content": "hello"})
    store.append_message(USERNAME, conv, {"role": "assistant", "content": "hi"})

    store.replace_messages(USERNAME, conv, [{"role": "assistant", "content": "summary"}])

    msgs = store.get_messages(USERNAME, conv)
    assert msgs == [{"role": "assistant", "content": "summary"}]


def test_prune_conversations_keeps_all_within_days_even_over_min(user_db):
    """More than keep_min conversations, but all within keep_days, all survive."""
    from agent import store

    conv_ids = [store.create_conversation(USERNAME, f"c{i}") for i in range(15)]

    store.prune_conversations(USERNAME, keep_min=12, keep_days=2)

    remaining = {c["id"] for c in store.list_conversations(USERNAME)}
    assert remaining == set(conv_ids)


def test_prune_conversations_keeps_min_count_when_all_stale(user_db):
    """When everything is past keep_days, keep_min caps it at the most recent dozen."""
    import time

    from agent import store

    conv_ids = [store.create_conversation(USERNAME, f"c{i}") for i in range(15)]

    with store._chat_db(USERNAME) as conn:
        stale_time = int(time.time()) - 3 * 24 * 3600
        conn.execute("UPDATE conversations SET updated_at = ?", (stale_time,))

    store.prune_conversations(USERNAME, keep_min=12, keep_days=2)

    remaining = {c["id"] for c in store.list_conversations(USERNAME)}
    assert remaining == set(conv_ids[-12:])


def test_prune_conversations_keeps_recent_beyond_min(user_db):
    """A conversation newer than keep_days survives even past the keep_min cutoff."""
    import time

    from agent import store

    old_ids = [store.create_conversation(USERNAME, f"old{i}") for i in range(12)]
    recent_id = store.create_conversation(USERNAME, "recent")

    with store._chat_db(USERNAME) as conn:
        stale_time = int(time.time()) - 3 * 24 * 3600
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id != ?",
            (stale_time, recent_id),
        )

    store.prune_conversations(USERNAME, keep_min=12, keep_days=2)

    remaining = {c["id"] for c in store.list_conversations(USERNAME)}
    assert recent_id in remaining
    assert old_ids[0] not in remaining
    assert remaining == set(old_ids[-11:]) | {recent_id}


def test_prune_conversations_drops_stale_beyond_min(user_db):
    """Old conversations outside both the keep_min window and keep_days are dropped."""
    import time

    from agent import store

    conv_ids = [store.create_conversation(USERNAME, f"c{i}") for i in range(14)]

    with store._chat_db(USERNAME) as conn:
        stale_time = int(time.time()) - 3 * 24 * 3600
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (stale_time, conv_ids[0]),
        )

    store.prune_conversations(USERNAME, keep_min=12, keep_days=2)

    remaining = {c["id"] for c in store.list_conversations(USERNAME)}
    assert conv_ids[0] not in remaining
    assert not store.conversation_exists(USERNAME, conv_ids[0])


# --- compaction ---

@pytest.mark.asyncio
async def test_compact_replaces_history_with_summary(user_db):
    from agent import compact, store

    conv = store.create_conversation(USERNAME, "t")
    store.append_message(USERNAME, conv, {"role": "user", "content": "hello"})
    store.append_message(USERNAME, conv, {"role": "assistant", "content": "hi"})

    seen_messages = []

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        seen_messages.append(messages)
        yield {"type": "message", "content": "Summary of the chat.", "tool_calls": [], "finish_reason": "stop"}

    with patch("agent.compact.llm.stream_chat", fake_stream_chat):
        summary = await compact.compact(USERNAME, NSID, conv, CFG)

    assert summary == "Summary of the chat."
    msgs = store.get_messages(USERNAME, conv)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert "Summary of the chat." in msgs[0]["content"]
    # Must open with a system message like every other call to the model —
    # re-compacting a conversation whose only history is a prior compaction
    # summary (bare [assistant, user]) broke some backends' chat templates.
    assert seen_messages[0][0]["role"] == "system"


@pytest.mark.asyncio
async def test_compact_empty_conversation_is_a_no_op(user_db):
    from agent import compact, store

    conv = store.create_conversation(USERNAME, "t")
    summary = await compact.compact(USERNAME, NSID, conv, CFG)
    assert summary == ""
    assert store.get_messages(USERNAME, conv) == []


@pytest.mark.asyncio
async def test_compact_llm_error_is_a_no_op(user_db):
    from agent import compact, llm, store

    conv = store.create_conversation(USERNAME, "t")
    store.append_message(USERNAME, conv, {"role": "user", "content": "hello"})

    async def broken(cfg, messages, tools=None, client=None):
        raise llm.LLMError("connection refused")
        yield  # pragma: no cover

    with patch("agent.compact.llm.stream_chat", broken):
        summary = await compact.compact(USERNAME, NSID, conv, CFG)

    assert summary == ""
    # History is left untouched — a failed summarization must not still wipe it.
    assert len(store.get_messages(USERNAME, conv)) == 1


@pytest.mark.asyncio
async def test_run_turn_auto_compacts_when_over_threshold(user_db):
    """auto_compact=True + a history estimated over threshold must compact
    the *prior* history before this turn's own question/answer are appended,
    so the new question survives (it must not get folded into the summary
    and lost — see loop.run_turn's ordering comment).

    ``agent.compact`` and ``agent.loop`` both do ``from agent import llm``,
    i.e. they share the exact same module object — patching
    ``agent.compact.llm.stream_chat`` and ``agent.loop.llm.stream_chat``
    separately would silently clobber each other (last patch wins for both
    call sites), so this dispatches on the request content instead of
    patching two "different" targets that are actually one.
    """
    from agent import loop, prompts_store, store

    conv = store.create_conversation(USERNAME, "t")
    store.append_message(USERNAME, conv, {"role": "user", "content": "x" * 400})
    store.append_message(USERNAME, conv, {"role": "assistant", "content": "y" * 400})

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        if messages and messages[-1].get("content") == prompts_store.COMPACT_PROMPT_DEFAULT:
            yield {"type": "message", "content": "Old chat summarized.", "tool_calls": [], "finish_reason": "stop"}
        else:
            yield {"type": "message", "content": "Answered after compaction.", "tool_calls": [], "finish_reason": "stop"}

    small_cfg = {**CFG, "context_window": 100}

    with patch("agent.settings.load_settings", return_value={"auto_compact": True}), \
         patch("agent.llm.stream_chat", fake_stream_chat):
        events = [e async for e in loop.run_turn(USER, conv, "new question", small_cfg)]

    assert any(
        e["type"] == "compacted" and e["summary"] == "Old chat summarized." for e in events
    )
    stored = store.get_messages(USERNAME, conv)
    assert stored[0]["role"] == "assistant"
    assert "Old chat summarized." in stored[0]["content"]
    assert stored[1] == {"role": "user", "content": "new question"}


@pytest.mark.asyncio
async def test_run_turn_no_auto_compact_when_disabled(user_db):
    """Even a tiny context_window must not trigger compaction while
    auto_compact is off (the default) — see settings._merge_defaults."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    store.append_message(USERNAME, conv, {"role": "user", "content": "x" * 400})
    store.append_message(USERNAME, conv, {"role": "assistant", "content": "y" * 400})

    scripted = _scripted_llm([{"content": "ok"}])
    small_cfg = {**CFG, "context_window": 100}

    with patch("agent.loop.compact.compact") as mock_compact, \
         patch("agent.loop.llm.stream_chat", scripted):
        events = [e async for e in loop.run_turn(USER, conv, "hi", small_cfg)]

    mock_compact.assert_not_called()
    assert not any(e["type"] == "compacted" for e in events)


# --- agent loop ---

def _scripted_llm(turns: list[dict]):
    """Return a stream_chat stand-in yielding one scripted turn per call."""
    calls = iter(turns)

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        turn = next(calls)
        for text in turn.get("deltas", []):
            yield {"type": "delta", "text": text}
        yield {
            "type": "message",
            "content": turn.get("content", ""),
            "tool_calls": turn.get("tool_calls", []),
            "finish_reason": "stop",
        }

    return fake_stream_chat


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.mark.asyncio
async def test_injection_folded_into_next_iteration_is_answered(user_db):
    """An add-info that lands before an iteration's LLM call is folded in and
    answered — yielded as `injected` (not `inject_missed`) and stored."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        # First call asks for a tool; the injection is added *before* the
        # second iteration, so its top-of-loop pop folds it in.
        if not any(m.get("role") == "tool" for m in messages):
            loop.add_injection(conv, "also mention the weather")
            yield {"type": "message", "content": "",
                   "tool_calls": [_tool_call("c1", "get_summary", {})],
                   "finish_reason": "tool_calls"}
        else:
            yield {"type": "message", "content": "done", "tool_calls": [],
                   "finish_reason": "stop"}

    with patch("agent.loop.llm.stream_chat", fake_stream_chat):
        events = [e async for e in loop.run_turn(USER, conv, "hi", CFG)]

    types = [e["type"] for e in events]
    assert "injected" in types
    assert "inject_missed" not in types
    injected = next(e for e in events if e["type"] == "injected")
    assert injected["text"] == "also mention the weather"
    # Folded in => stored as a user message in the history.
    contents = [m.get("content") for m in store.get_messages(USERNAME, conv)]
    assert "also mention the weather" in contents


@pytest.mark.asyncio
async def test_injection_arriving_too_late_is_missed_not_stored(user_db):
    """An add-info that lands during the FINAL LLM call (after that
    iteration's pop, with no further iteration) can't be answered: it's
    reported as `inject_missed` so the client re-queues it, and is NOT stored
    as an unanswered user message."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        # No tool calls -> the loop breaks after this one iteration. The
        # injection arrives *during* this final call, so it's only seen by the
        # post-loop "too late" drain.
        loop.add_injection(conv, "one more thing")
        yield {"type": "message", "content": "all done", "tool_calls": [],
               "finish_reason": "stop"}

    with patch("agent.loop.llm.stream_chat", fake_stream_chat):
        events = [e async for e in loop.run_turn(USER, conv, "hi", CFG)]

    types = [e["type"] for e in events]
    assert "inject_missed" in types
    missed = next(e for e in events if e["type"] == "inject_missed")
    assert missed["text"] == "one more thing"
    # Must NOT be stored — the client re-sends it as its own turn, and storing
    # here too would duplicate it.
    contents = [m.get("content") for m in store.get_messages(USERNAME, conv)]
    assert "one more thing" not in contents
    # And it's drained from the pending map so it can't bleed into a later turn.
    assert not loop._injections.get(conv)


@pytest.mark.asyncio
async def test_run_turn_executes_read_tool_without_confirm(user_db):
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "get_summary", {})]},
        {"deltas": ["You have 1 photo."], "content": "You have 1 photo."},
    ])
    with patch("agent.loop.llm.stream_chat", scripted):
        events = [e async for e in loop.run_turn(USER, conv, "how many photos?", CFG)]

    types = [e["type"] for e in events]
    assert "confirm_request" not in types
    assert types.count("tool_call") == 1
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["name"] == "get_summary"
    assert "total_photos" in result["text"]
    assert types[-1] == "done"

    roles = [m["role"] for m in store.get_messages(USERNAME, conv)]
    assert roles == ["user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
async def test_run_turn_search_photos_emits_photo_list(user_db):
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "search_photos", {"query": "Test"})]},
        {"content": "Found it."},
    ])
    with patch("agent.loop.llm.stream_chat", scripted):
        events = [e async for e in loop.run_turn(USER, conv, "find test photos", CFG)]

    photo_list = next(e for e in events if e["type"] == "photo_list")
    assert photo_list["photo_ids"] == ["photo1"]


@pytest.mark.asyncio
async def test_run_turn_search_photos_no_results_emits_no_photo_list(user_db):
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "search_photos", {"query": "nomatch"})]},
        {"content": "Nothing found."},
    ])
    with patch("agent.loop.llm.stream_chat", scripted):
        events = [e async for e in loop.run_turn(USER, conv, "find nomatch", CFG)]

    assert not any(e["type"] == "photo_list" for e in events)


@pytest.mark.asyncio
async def test_run_turn_write_tool_denied(user_db):
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "add_comment",
                                   {"photo_id": "photo1", "text": "nice"})]},
        {"content": "Okay, skipped."},
    ])
    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "comment on it", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], False)

    result = next(e for e in events if e["type"] == "tool_result")
    assert "declined" in result["text"]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_run_turn_write_tool_denied_with_reason(user_db):
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "add_comment",
                                   {"photo_id": "photo1", "text": "nice"})]},
        {"content": "Okay, skipped."},
    ])
    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "comment on it", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], False, "too generic")

    result = next(e for e in events if e["type"] == "tool_result")
    assert "declined" in result["text"]
    assert "too generic" in result["text"]


@pytest.mark.asyncio
async def test_resolve_confirm_rejects_wrong_owner(user_db):
    """A confirm_id belongs to the user whose turn generated it — another
    authenticated user who learned/guessed the id must not be able to
    approve or deny it (confirm_id is an unguessable UUID, but that's not
    the same as an authorization check)."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "add_comment",
                                   {"photo_id": "photo1", "text": "nice"})]},
        {"content": "Okay."},
    ])
    confirm_id = None

    async def _drive():
        nonlocal confirm_id
        async for event in loop.run_turn(USER, conv, "comment on it", CFG):
            if event["type"] == "confirm_request":
                confirm_id = event["confirm_id"]
                # Wrong owner: must be rejected without resolving the future.
                assert loop.resolve_confirm(confirm_id, True, username="someone-else") is False
                # Rightful owner: same confirm_id now succeeds.
                assert loop.resolve_confirm(confirm_id, True, username=USERNAME) is True

    with patch("agent.loop.llm.stream_chat", scripted):
        await _drive()

    assert confirm_id is not None
    # Resolving twice must fail cleanly (future already done / popped).
    assert loop.resolve_confirm(confirm_id, True, username=USERNAME) is False


@pytest.mark.asyncio
async def test_focused_photo_context_is_ephemeral_not_persisted(user_db):
    """The focused-photo note must reach the LLM call but never land in the
    stored conversation — otherwise it goes stale the moment the user looks
    at a different photo and misdirects a later, unrelated turn."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    seen_messages = []

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        seen_messages.append(messages)
        yield {"type": "message", "content": "ok", "tool_calls": [], "finish_reason": "stop"}

    with patch("agent.loop.llm.stream_chat", fake_stream_chat):
        events = [
            e async for e in loop.run_turn(USER, conv, "add red tag", CFG, focused_photo_id="9999999")
        ]

    assert events[-1]["type"] == "done"
    assert any("9999999" in (m.get("content") or "") for m in seen_messages[0])
    # Some OpenAI-compatible backends (e.g. LM Studio's stricter chat
    # templates) require the conversation to end on a "user" turn — the
    # focused-photo note must not be the trailing message.
    assert seen_messages[0][-1]["role"] == "user"

    stored = store.get_messages(USERNAME, conv)
    assert all("9999999" not in json.dumps(m) for m in stored)
    assert [m["role"] for m in stored] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_confirm_request_includes_photo_preview(user_db):
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "update_photo", {"id": "photo1", "tags": "red"})]},
        {"content": "Done."},
    ])
    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "add red tag", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], True)

    confirm = next(e for e in events if e["type"] == "confirm_request")
    # "photo1" fails the numeric-id check (_focus_photo_id), so no preview —
    # this pins the current, deliberately conservative fallback behavior.
    assert confirm["photo"] is None


@pytest.mark.asyncio
async def test_confirm_request_photo_preview_populated_for_numeric_id(user_db):
    from agent import loop, store

    con = sqlite3.connect(Path(user_db) / "flickr.db")
    con.execute(
        "INSERT INTO photos (id, title, description, date_taken, date_uploaded, "
        "last_updated, url_photopage, url_original, tags, views, favorites, "
        "comments, is_public, synced_at, reviewed_at, url_medium) "
        "VALUES ('55405570240','Sunset','','2024-01-15 12:00:00',0,0,'','',"
        "'',0,0,0,1,0,NULL,'https://live.staticflickr.com/x/thumb_z.jpg')"
    )
    con.commit()
    con.close()

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "update_photo", {"id": "55405570240", "tags": "red"})]},
        {"content": "Done."},
    ])
    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "add red tag", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], True)

    confirm = next(e for e in events if e["type"] == "confirm_request")
    assert confirm["photo"] == {
        "id": "55405570240",
        "title": "Sunset",
        "thumb_url": "https://live.staticflickr.com/x/thumb_z.jpg",
    }


@pytest.mark.asyncio
async def test_run_turn_write_tool_approved_executes(user_db, patched_server):
    mcp, api_get, api_post = patched_server
    from agent import loop, store

    api_post.return_value = {"stat": "ok"}
    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "fave_photo", {"photo_id": "photo1"})]},
        {"content": "Faved."},
    ])
    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "fave it", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], True)

    result = next(e for e in events if e["type"] == "tool_result")
    assert "declined" not in result["text"]
    assert api_post.called
    assert any(e["type"] == "focus" and e["photo_id"] for e in events) is False  # photo1 not numeric


@pytest.mark.asyncio
async def test_run_turn_unknown_tool_reports_error(user_db):
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "not_a_tool", {})]},
        {"content": "sorry"},
    ])
    with patch("agent.loop.llm.stream_chat", scripted):
        events = [e async for e in loop.run_turn(USER, conv, "hi", CFG)]

    result = next(e for e in events if e["type"] == "tool_result")
    assert "Unknown tool" in result["text"]


@pytest.mark.asyncio
async def test_run_turn_uses_stream_responses_when_api_mode_responses(user_db):
    """cfg['api_mode'] == 'responses' must route through llm.stream_responses,
    never llm.stream_chat — the one-line branch in loop.run_turn."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    cfg_responses = {**CFG, "api_mode": "responses"}

    async def fake_stream_responses(cfg, messages, tools=None, client=None):
        yield {"type": "message", "content": "hi from responses", "tool_calls": [],
               "finish_reason": "stop", "usage": {}, "latency_ms": 1}

    async def fail_if_called(cfg, messages, tools=None, client=None):
        raise AssertionError("stream_chat should not be called in responses mode")
        yield  # pragma: no cover

    with patch("agent.loop.llm.stream_responses", fake_stream_responses), \
         patch("agent.loop.llm.stream_chat", fail_if_called):
        events = [e async for e in loop.run_turn(USER, conv, "hello", cfg_responses)]

    assert events[-1]["type"] == "done"
    assert any(e.get("type") == "error" for e in events) is False


@pytest.mark.asyncio
async def test_run_turn_llm_error_yields_error_event(user_db):
    from agent import llm, loop, store

    async def broken(cfg, messages, tools=None, client=None):
        raise llm.LLMError("connection refused")
        yield  # pragma: no cover

    conv = store.create_conversation(USERNAME, "t")
    with patch("agent.loop.llm.stream_chat", broken):
        events = [e async for e in loop.run_turn(USER, conv, "hi", CFG)]

    assert any(e["type"] == "error" and "connection refused" in e["message"] for e in events)
    assert events[-1]["type"] == "done"


# --- vision guard ---

def test_result_content_vision_disabled_no_image_url():
    """When vision=False, ImageContent must never produce an image_url part."""
    from mcp.types import ImageContent, TextContent
    from agent.loop import _result_content, _VISION_DISABLED_NOTE

    result = [
        TextContent(type="text", text="here is the image:"),
        ImageContent(type="image", data="abc123", mimeType="image/jpeg"),
    ]
    content = _result_content(result, vision=False)
    assert isinstance(content, str)
    assert "image_url" not in content
    assert _VISION_DISABLED_NOTE in content
    assert "Do not guess" in content


def test_result_content_vision_enabled_includes_image_url():
    """When vision=True, ImageContent should produce an image_url multimodal part."""
    from mcp.types import ImageContent, TextContent
    from agent.loop import _result_content

    result = [
        TextContent(type="text", text="fetched"),
        ImageContent(type="image", data="abc123", mimeType="image/jpeg"),
    ]
    content = _result_content(result, vision=True)
    assert isinstance(content, list)
    types = [p["type"] for p in content]
    assert "image_url" in types
    img = next(p for p in content if p["type"] == "image_url")
    assert img["image_url"]["url"] == "data:image/jpeg;base64,abc123"


@pytest.mark.asyncio
async def test_run_turn_vision_disabled_tool_result_has_disclaimer(user_db):
    """With vision=False in cfg, a fetch_photo_image result must carry the
    explicit disclaimer and no image_url part must reach the stored conversation."""
    from mcp.types import ImageContent
    from agent import loop, store
    from agent.loop import _VISION_DISABLED_NOTE

    cfg_no_vision = {**CFG, "vision": False}
    conv = store.create_conversation(USERNAME, "t")

    fake_image = [ImageContent(type="image", data="IMGDATA", mimeType="image/jpeg")]

    async def fake_execute(user, name, args):
        return fake_image

    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "fetch_photo_image", {"photo_id": "photo1"})]},
        {"content": "I cannot see the image."},
    ])

    with patch("agent.loop.llm.stream_chat", scripted), \
         patch("agent.loop._execute_tool", fake_execute):
        events = [e async for e in loop.run_turn(USER, conv, "show photo", cfg_no_vision)]

    result = next(e for e in events if e["type"] == "tool_result")
    assert _VISION_DISABLED_NOTE in result["text"]
    assert "IMGDATA" not in result["text"]

    stored = store.get_messages(USERNAME, conv)
    stored_json = json.dumps(stored)
    assert "image_url" not in stored_json
    assert "IMGDATA" not in stored_json


# --- pasted-image composer input ---

def test_build_user_content_no_images_returns_plain_string():
    """No attachments — behaves exactly like the pre-image-paste code path."""
    from agent.loop import _build_user_content

    assert _build_user_content("hello", [], vision=True) == "hello"
    assert _build_user_content("hello", [], vision=False) == "hello"


def test_build_user_content_vision_disabled_drops_images_with_note():
    """Same guard as _result_content above, applied to user-pasted images:
    vision=False must never let an image_url part reach the wire."""
    from agent.loop import _build_user_content

    content = _build_user_content("look at this", ["data:image/jpeg;base64,abc123"], vision=False)
    assert isinstance(content, str)
    assert "image_url" not in content
    assert "look at this" in content
    assert "vision is disabled" in content


def test_build_user_content_vision_disabled_image_only_still_has_a_note():
    """No typed text at all — the note must stand on its own, not disappear."""
    from agent.loop import _build_user_content

    content = _build_user_content("", ["data:image/jpeg;base64,abc123"], vision=False)
    assert isinstance(content, str)
    assert "vision is disabled" in content


def test_build_user_content_vision_enabled_includes_image_url():
    from agent.loop import _build_user_content

    content = _build_user_content("look at this", ["data:image/jpeg;base64,aGVsbG8="], vision=True)
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "look at this"}
    img = next(p for p in content if p["type"] == "image_url")
    assert img["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_build_user_content_vision_enabled_image_only_no_text_part():
    from agent.loop import _build_user_content

    content = _build_user_content("", ["data:image/jpeg;base64,aGVsbG8="], vision=True)
    assert isinstance(content, list)
    assert all(p["type"] == "image_url" for p in content)


def test_build_user_content_caps_at_max_user_images():
    from agent.loop import MAX_USER_IMAGES, _build_user_content

    images = [f"data:image/jpeg;base64,img{i}" for i in range(MAX_USER_IMAGES + 3)]
    content = _build_user_content("many", images, vision=True)
    assert sum(1 for p in content if p["type"] == "image_url") == MAX_USER_IMAGES


@pytest.mark.asyncio
async def test_run_turn_pasted_image_reaches_llm_and_storage(user_db):
    """End-to-end: an image pasted into the composer shows up as an image_url
    part on the stored (and outbound) user message when vision is enabled."""
    from agent import loop, store

    cfg_vision = {**CFG, "vision": True}
    conv = store.create_conversation(USERNAME, "t")

    scripted = _scripted_llm([{"content": "Nice photo."}])

    with patch("agent.loop.llm.stream_chat", scripted):
        events = [
            e async for e in loop.run_turn(
                USER, conv, "what is this?", cfg_vision,
                images=["data:image/png;base64,aGVsbG8="],
            )
        ]

    assert events[-1]["type"] == "done"
    stored_user_msg = next(m for m in store.get_messages(USERNAME, conv) if m["role"] == "user")
    assert isinstance(stored_user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in stored_user_msg["content"])


@pytest.mark.asyncio
async def test_run_turn_pasted_image_dropped_when_vision_disabled(user_db):
    from agent import loop, store

    cfg_no_vision = {**CFG, "vision": False}
    conv = store.create_conversation(USERNAME, "t")

    scripted = _scripted_llm([{"content": "I can't see images."}])

    with patch("agent.loop.llm.stream_chat", scripted):
        events = [
            e async for e in loop.run_turn(
                USER, conv, "what is this?", cfg_no_vision,
                images=["data:image/png;base64,aGVsbG8="],
            )
        ]

    assert events[-1]["type"] == "done"
    stored_user_msg = next(m for m in store.get_messages(USERNAME, conv) if m["role"] == "user")
    assert isinstance(stored_user_msg["content"], str)
    assert "vision is disabled" in stored_user_msg["content"]


def test_wire_messages_splits_tool_image_into_following_user_message():
    """A "tool" role message's content is text-only per the Chat Completions
    spec — an image_url part left on it is silently dropped by compliant
    servers. _wire_messages must move it into a synthetic user message
    immediately after, leaving everything else untouched."""
    from agent.loop import _wire_messages

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "show me the photo"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "fetch_photo_image", {"id": "1"})]},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "Photo ID: 1"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,IMGDATA"}},
        ]},
    ]

    wire = _wire_messages(messages)

    assert len(wire) == 5
    assert wire[:3] == messages[:3]
    tool_msg = wire[3]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"] == "Photo ID: 1"
    image_msg = wire[4]
    assert image_msg["role"] == "user"
    assert isinstance(image_msg["content"], list)
    assert image_msg["content"][-1]["type"] == "image_url"
    assert image_msg["content"][-1]["image_url"]["url"] == "data:image/jpeg;base64,IMGDATA"

    # Plain string tool content (vision disabled, or a text-only tool) passes
    # through unchanged — no extra message inserted.
    plain = [{"role": "tool", "tool_call_id": "c1", "content": "just text"}]
    assert _wire_messages(plain) == plain


def test_wire_messages_inserts_user_turn_after_compaction():
    """A compacted conversation's stored history is a single assistant
    message (see compact.compact) — so a payload built from it after
    compaction is [system…, assistant, user] with no user turn before the
    assistant one. Some backends' chat templates (e.g. LM Studio's stricter
    jinja ones) can't find "a user query" in that shape and error out, so
    _wire_messages must insert a placeholder user turn right after any
    leading system messages."""
    from agent.loop import _wire_messages

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "**Conversation compacted.**\n\nSummary text."},
        {"role": "user", "content": "what tags are on this photo?"},
    ]

    wire = _wire_messages(messages)

    assert [m["role"] for m in wire] == ["system", "user", "assistant", "user"]
    assert wire[2]["content"] == "**Conversation compacted.**\n\nSummary text."
    assert wire[3]["content"] == "what tags are on this photo?"


@pytest.mark.asyncio
async def test_run_turn_vision_enabled_image_reaches_llm_as_user_message(user_db):
    """End-to-end: with vision enabled, the image from a tool call must show
    up in a "user" message on the *next* LLM call — not left dangling,
    invisible, inside the "tool" message — while the stored conversation
    keeps the original (untouched) shape."""
    from mcp.types import ImageContent
    from agent import loop, store

    cfg_vision = {**CFG, "vision": True}
    conv = store.create_conversation(USERNAME, "t")

    async def fake_execute(user, name, args):
        return [ImageContent(type="image", data="IMGDATA", mimeType="image/jpeg")]

    captured_messages = []

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        captured_messages.append(messages)
        if len(captured_messages) == 1:
            yield {
                "type": "message", "content": "",
                "tool_calls": [_tool_call("c1", "fetch_photo_image", {"photo_id": "photo1"})],
                "finish_reason": "tool_calls",
            }
        else:
            yield {"type": "message", "content": "I can see the image.", "tool_calls": [], "finish_reason": "stop"}

    with patch("agent.loop.llm.stream_chat", fake_stream_chat), \
         patch("agent.loop._execute_tool", fake_execute):
        events = [e async for e in loop.run_turn(USER, conv, "show photo", cfg_vision)]

    assert events[-1]["type"] == "done"
    assert len(captured_messages) == 2
    second_call = captured_messages[1]
    tool_idx = next(i for i, m in enumerate(second_call) if m.get("role") == "tool")
    assert isinstance(second_call[tool_idx]["content"], str)
    image_msg = second_call[tool_idx + 1]
    assert image_msg["role"] == "user"
    assert any(p.get("type") == "image_url" for p in image_msg["content"])

    # Storage/UI shape is untouched — only the outbound wire payload is split.
    stored_tool_msg = next(m for m in store.get_messages(USERNAME, conv) if m["role"] == "tool")
    assert any(p.get("type") == "image_url" for p in stored_tool_msg["content"])


# --- commands ---

def test_commands_resolve_user_placeholder():
    """{user_nsid}/{username} are resolved server-side for any command that
    references them, and {photo_id} is left alone for the client to fill in
    on photo-context commands. Uses a custom prompt rather than a specific
    builtin's wording, since builtin prompt text is sourced from
    default-prompts.md and can change independently of this test."""
    from agent import commands, prompts_store

    collection = next(c["id"] for c in prompts_store.all_data("99@N00")["categories"]
                       if c["id"] == "collection")
    prompts_store.create_prompt(
        "99@N00", code="placeholder-check", name="Placeholder check",
        category_id=collection, text="nsid={user_nsid} username={username}",
    )

    cmds = commands.commands_for_api("99@N00", "someuser")
    custom = next(c for c in cmds if c["id"] == "placeholder-check")
    assert "99@N00" in custom["prompt"]
    assert "someuser" in custom["prompt"]
    photo_cmds = [c for c in cmds if c["context"] == "photo"]
    assert photo_cmds and all("{photo_id}" in c["prompt"] for c in photo_cmds)


# --- remember tool ---

@pytest.mark.asyncio
async def test_remember_appends_to_user_memory_when_approved(user_db):
    """'remember' appends guidance to the user-memory prompt once approved."""
    from agent import loop, prompts_store, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "Always be concise."})]},
        {"content": "Got it."},
    ])

    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "remember: always be concise", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], True)

    memory = prompts_store.get_prompt_by_code(NSID, "user-memory")
    assert "Always be concise." in memory["text"]

    result = next(e for e in events if e["type"] == "tool_result")
    assert "Remembered" in result["text"]
    assert "Always be concise." in result["text"]


@pytest.mark.asyncio
async def test_remember_appends_to_existing_user_memory(user_db):
    """'remember' appends new guidance after existing user-memory content."""
    from agent import loop, prompts_store, store

    prompts_store.append_user_memory(NSID, "First rule.")

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "Second rule."})]},
        {"content": "Done."},
    ])

    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "remember second rule", CFG):
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], True)

    memory = prompts_store.get_prompt_by_code(NSID, "user-memory")
    assert "First rule." in memory["text"]
    assert "Second rule." in memory["text"]


@pytest.mark.asyncio
async def test_remember_requires_confirm(user_db):
    """'remember' must be gated behind a confirm_request like other write tools —
    otherwise guidance smuggled in via an untrusted photo/comment/group
    description the model reads could silently steer all future sessions."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "Confirm gated."})]},
        {"content": "Done."},
    ])

    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "remember this", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], True)

    assert any(e["type"] == "confirm_request" and e["name"] == "remember" for e in events)


@pytest.mark.asyncio
async def test_remember_denied_does_not_persist(user_db):
    """Denying the remember confirmation must not write to user memory."""
    from agent import loop, prompts_store, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "Should not stick."})]},
        {"content": "Okay, skipped."},
    ])

    events = []
    with patch("agent.loop.llm.stream_chat", scripted):
        async for event in loop.run_turn(USER, conv, "remember this", CFG):
            events.append(event)
            if event["type"] == "confirm_request":
                assert loop.resolve_confirm(event["confirm_id"], False, "not now")

    memory = prompts_store.get_prompt_by_code(NSID, "user-memory")
    assert "Should not stick." not in (memory["text"] if memory else "")

    result = next(e for e in events if e["type"] == "tool_result")
    assert "declined" in result["text"]
    assert "not now" in result["text"]


# --- prompts_store ---

def test_prompts_seed_once_and_are_idempotent():
    from agent import prompts_store

    data = prompts_store.all_data(NSID)
    assert {c["id"] for c in data["categories"]} == {
        "system", "own_photo", "other_photo", "collection",
    }
    assert {p["code"] for p in data["prompts"]} == {
        "system-core", "user-memory", "compact-conversation", "group-summary",
        "improve-photo", "suggest-groups", "suggest-albums", "threshold-groups",
        "Review photo", "suggest-comment-fave", "other-photo-owner", "other-photo-groups",
        "reply-comments", "weak-photos", "unearth-private",
    }
    assert {v["code"] for v in data["variables"]} == {
        "photo_id", "user_nsid", "username", "group_name", "group_description", "group_user_note",
    }

    # Calling again must not duplicate rows.
    data2 = prompts_store.all_data(NSID)
    assert len(data2["categories"]) == len(data["categories"])
    assert len(data2["prompts"]) == len(data["prompts"])


def test_prompts_backfill_new_builtins_for_existing_db():
    """A category/prompt added to the code after a user's DB was already
    seeded must still reach them on the next load — not just brand-new
    accounts (regression test for the other_photo prompts never appearing
    for existing users)."""
    from agent import prompts_store

    trimmed_categories = [c for c in prompts_store._SEED_CATEGORIES if c[0] != "other_photo"]
    trimmed_prompts = [p for p in prompts_store._SEED_PROMPTS if p["category_id"] != "other_photo"]

    with patch.object(prompts_store, "_SEED_CATEGORIES", trimmed_categories), \
         patch.object(prompts_store, "_SEED_PROMPTS", trimmed_prompts):
        data = prompts_store.all_data(NSID)
        assert "other_photo" not in {c["id"] for c in data["categories"]}
        assert not any(p["category_id"] == "other_photo" for p in data["prompts"])

    # Real _SEED_CATEGORIES/_SEED_PROMPTS restored — the next load should
    # backfill the category and its prompts into this already-seeded DB.
    data = prompts_store.all_data(NSID)
    assert "other_photo" in {c["id"] for c in data["categories"]}
    backfilled = {p["code"] for p in data["prompts"] if p["category_id"] == "other_photo"}
    assert backfilled == {"suggest-comment-fave", "other-photo-owner", "other-photo-groups"}


@pytest.mark.asyncio
async def test_run_turn_uses_system_core_and_user_memory(user_db):
    """run_turn's first two messages are the DB-backed system-core and
    user-memory prompts (when memory is non-empty)."""
    from agent import loop, prompts_store, store

    prompts_store.update_prompt(NSID, prompts_store.get_prompt_by_code(NSID, "system-core")["id"],
                                 text="CUSTOM SYSTEM PROMPT")
    prompts_store.append_user_memory(NSID, "Remembered rule.")

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([{"content": "hi"}])

    captured = {}

    async def capturing(cfg, messages, tools=None):
        captured["messages"] = messages
        async for event in scripted(cfg, messages, tools=tools):
            yield event

    with patch("agent.loop.llm.stream_chat", capturing):
        [e async for e in loop.run_turn(USER, conv, "hello", CFG)]

    assert captured["messages"][0] == {"role": "system", "content": "CUSTOM SYSTEM PROMPT"}
    assert captured["messages"][1] == {"role": "system", "content": "Remembered rule."}


def test_builtin_prompt_cannot_be_deleted_but_can_be_reset():
    from agent import prompts_store

    system_prompt = prompts_store.get_prompt_by_code(NSID, "system-core")
    ok, error = prompts_store.delete_prompt(NSID, system_prompt["id"])
    assert not ok and "built-in" in error

    prompts_store.update_prompt(NSID, system_prompt["id"], text="edited")
    assert prompts_store.get_prompt(NSID, system_prompt["id"])["text"] == "edited"

    reset = prompts_store.reset_prompt(NSID, system_prompt["id"])
    assert reset["text"] == system_prompt["text"]


def test_reset_all_prompts_skips_user_memory_unless_asked():
    """reset_all_prompts restores every edited builtin's text, but leaves
    user-memory alone unless include_user_memory=True — resetting it means
    discarding the user's accumulated `remember`-tool guidance, which the UI
    only does after a separate, explicit confirmation."""
    from agent import prompts_store

    system_prompt = prompts_store.get_prompt_by_code(NSID, "system-core")
    prompts_store.update_prompt(NSID, system_prompt["id"], text="edited system prompt")
    prompts_store.append_user_memory(NSID, "Remembered rule.")

    prompts_store.reset_all_prompts(NSID, include_user_memory=False)
    assert prompts_store.get_prompt_by_code(NSID, "system-core")["text"] == system_prompt["text"]
    assert prompts_store.get_prompt_by_code(NSID, "user-memory")["text"] == "Remembered rule."

    prompts_store.update_prompt(NSID, system_prompt["id"], text="edited again")
    prompts_store.reset_all_prompts(NSID, include_user_memory=True)
    assert prompts_store.get_prompt_by_code(NSID, "system-core")["text"] == system_prompt["text"]
    assert prompts_store.get_prompt_by_code(NSID, "user-memory")["text"] == ""


# --- per-browser-window session isolation of turn state ---

@pytest.mark.asyncio
async def test_conversation_turn_lock_serializes_and_cleans_up():
    """The turn lock is keyed by conversation, not by browser session: holding
    it marks that conversation (and only that one) as running, and once
    released the map entry is dropped so _turn_locks doesn't grow forever."""
    from agent import loop

    assert loop.is_turn_running(USERNAME, "conv-x") is False
    async with loop.conversation_turn_lock(USERNAME, "conv-x"):
        assert loop.is_turn_running(USERNAME, "conv-x") is True
        # A different conversation is independent (own lock, runs in parallel).
        assert loop.is_turn_running(USERNAME, "conv-y") is False
    # Released and reclaimed — no lingering entry.
    assert loop.is_turn_running(USERNAME, "conv-x") is False
    assert (USERNAME, "conv-x") not in loop._turn_locks
    assert (USERNAME, "conv-x") not in loop._turn_lock_refs


@pytest.mark.asyncio
async def test_conversation_turn_lock_kept_while_a_waiter_is_pending():
    """A second caller on the same conversation waits on the same lock, and
    the lock is only reclaimed once BOTH holders+waiters have left — so the
    refcount cleanup can never yank a lock out from under a pending waiter."""
    from agent import loop

    key = (USERNAME, "conv-z")
    started = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with loop.conversation_turn_lock(USERNAME, "conv-z"):
            started.set()
            await release.wait()

    async def waiter():
        async with loop.conversation_turn_lock(USERNAME, "conv-z"):
            pass

    h = asyncio.ensure_future(holder())
    await started.wait()
    w = asyncio.ensure_future(waiter())
    await asyncio.sleep(0)  # let the waiter enter the CM and block on acquire
    # One holds, one waits -> refcount 2, lock retained.
    assert loop._turn_lock_refs.get(key) == 2
    assert loop.is_turn_running(USERNAME, "conv-z") is True

    release.set()
    await asyncio.gather(h, w)
    # Both gone -> reclaimed.
    assert key not in loop._turn_locks
    assert key not in loop._turn_lock_refs


@pytest.mark.asyncio
async def test_cancel_turn_by_session_only_cancels_that_session():
    """A cancel scoped to one window's session cancels only that window's
    in-flight turn, leaving a sibling window's turn untouched."""
    from agent import loop

    async def _never():
        await asyncio.Event().wait()

    task_a = asyncio.ensure_future(_never())
    task_b = asyncio.ensure_future(_never())
    loop.register_task(USERNAME, "sess-a", task_a)
    loop.register_task(USERNAME, "sess-b", task_b)
    try:
        assert loop.cancel_turn(USERNAME, session_id="sess-a") is True
        await asyncio.sleep(0)
        assert task_a.cancelled()
        assert not task_b.done()
        # Nothing running under an unknown session -> False, no side effects.
        assert loop.cancel_turn(USERNAME, session_id="sess-unknown") is False
        assert not task_b.done()
    finally:
        task_b.cancel()
        loop.unregister_task(USERNAME, "sess-a", task_a)
        loop.unregister_task(USERNAME, "sess-b", task_b)


@pytest.mark.asyncio
async def test_cancel_turn_by_conversation_targets_running_session():
    """Deleting a conversation cancels whichever window's session is actually
    running it, regardless of which window that is — and leaves a session
    running a different conversation alone."""
    from agent import loop

    async def _never():
        await asyncio.Event().wait()

    running = asyncio.ensure_future(_never())
    other = asyncio.ensure_future(_never())
    loop.register_task(USERNAME, "sess-a", running)
    loop.register_task(USERNAME, "sess-b", other)
    loop._active_conversations[(USERNAME, "sess-a")] = "conv-X"
    loop._active_conversations[(USERNAME, "sess-b")] = "conv-Y"
    try:
        assert loop.cancel_turn(USERNAME, conversation_id="conv-X") is True
        await asyncio.sleep(0)
        assert running.cancelled()
        assert not other.done()
        # A conversation nobody is running -> nothing to cancel.
        assert loop.cancel_turn(USERNAME, conversation_id="conv-none") is False
    finally:
        other.cancel()
        loop._active_conversations.pop((USERNAME, "sess-a"), None)
        loop._active_conversations.pop((USERNAME, "sess-b"), None)
        loop.unregister_task(USERNAME, "sess-a", running)
        loop.unregister_task(USERNAME, "sess-b", other)


def test_category_in_use_cannot_be_deleted():
    from agent import prompts_store

    ok, error = prompts_store.delete_category(NSID, "own_photo")
    assert not ok and "built-in" in error

    in_use = prompts_store.create_category(NSID, "In-use category")
    prompts_store.create_prompt(NSID, code="custom-1", name="Custom", category_id=in_use["id"], text="x")
    ok, error = prompts_store.delete_category(NSID, in_use["id"])
    assert not ok and "prompts assigned" in error

    empty = prompts_store.create_category(NSID, "Empty category")
    ok, error = prompts_store.delete_category(NSID, empty["id"])
    assert ok and error is None
