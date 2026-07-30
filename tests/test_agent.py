"""Tests for the chat agent: schema conversion, stream parsing, and the loop."""

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

    async def fake_stream_chat(cfg, messages, tools=None, client=None):
        yield {"type": "message", "content": "Summary of the chat.", "tool_calls": [], "finish_reason": "stop"}

    with patch("agent.compact.llm.stream_chat", fake_stream_chat):
        summary = await compact.compact(USERNAME, NSID, conv, CFG)

    assert summary == "Summary of the chat."
    msgs = store.get_messages(USERNAME, conv)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert "Summary of the chat." in msgs[0]["content"]


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
    from agent import commands

    cmds = commands.commands_for_api("99@N00")
    reply = next(c for c in cmds if c["id"] == "reply-comments")
    assert "99@N00" in reply["prompt"]
    photo_cmds = [c for c in cmds if c["context"] == "photo"]
    assert photo_cmds and all("{photo_id}" in c["prompt"] for c in photo_cmds)


# --- remember tool ---

@pytest.mark.asyncio
async def test_remember_appends_to_user_memory(user_db):
    """'remember' pseudo-tool appends guidance to the user-memory prompt."""
    from agent import loop, prompts_store, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "Always be concise."})]},
        {"content": "Got it."},
    ])

    with patch("agent.loop.llm.stream_chat", scripted):
        events = [e async for e in loop.run_turn(USER, conv, "remember: always be concise", CFG)]

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
        [e async for e in loop.run_turn(USER, conv, "remember second rule", CFG)]

    memory = prompts_store.get_prompt_by_code(NSID, "user-memory")
    assert "First rule." in memory["text"]
    assert "Second rule." in memory["text"]


@pytest.mark.asyncio
async def test_remember_does_not_require_confirm(user_db):
    """'remember' must never emit a confirm_request event."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "No confirms needed."})]},
        {"content": "Done."},
    ])

    with patch("agent.loop.llm.stream_chat", scripted):
        events = [e async for e in loop.run_turn(USER, conv, "remember this", CFG)]

    assert not any(e["type"] == "confirm_request" for e in events)


# --- prompts_store ---

def test_prompts_seed_once_and_are_idempotent():
    from agent import prompts_store

    data = prompts_store.all_data(NSID)
    assert {c["id"] for c in data["categories"]} == {
        "system", "own_photo", "other_photo", "collection",
    }
    assert {p["code"] for p in data["prompts"]} == {
        "system-core", "user-memory", "compact-conversation", "improve-photo",
        "suggest-groups", "suggest-albums", "threshold-groups", "reply-comments",
        "weak-photos", "unearth-private",
    }
    assert {v["code"] for v in data["variables"]} == {"photo_id", "user_nsid"}

    # Calling again must not duplicate rows.
    data2 = prompts_store.all_data(NSID)
    assert len(data2["categories"]) == len(data["categories"])
    assert len(data2["prompts"]) == len(data["prompts"])


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
