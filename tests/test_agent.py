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
async def test_remember_appends_to_base_prompt(user_db, tmp_path):
    """'remember' pseudo-tool appends guidance to base_prompt in llm.json."""
    from agent import loop, store, settings as _settings

    settings_dir = tmp_path / "creds" / NSID
    settings_dir.mkdir(parents=True)

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "Always be concise."})]},
        {"content": "Got it."},
    ])

    with patch("agent.loop.llm.stream_chat", scripted), \
         patch("agent.loop._agent_settings.load_settings", return_value={
             "base_url": "", "api_key": "", "model": "", "max_tokens": 512,
             "vision": False, "base_prompt": "",
         }) as mock_load, \
         patch("agent.loop._agent_settings.save_settings") as mock_save:
        events = [e async for e in loop.run_turn(USER, conv, "remember: always be concise", CFG)]

    mock_save.assert_called_once()
    saved_cfg = mock_save.call_args[0][1]
    assert "Always be concise." in saved_cfg["base_prompt"]

    result = next(e for e in events if e["type"] == "tool_result")
    assert "Remembered" in result["text"]
    assert "Always be concise." in result["text"]


@pytest.mark.asyncio
async def test_remember_appends_to_existing_base_prompt(user_db):
    """'remember' appends new guidance after existing base_prompt content."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "Second rule."})]},
        {"content": "Done."},
    ])

    existing_cfg = {
        "base_url": "", "api_key": "", "model": "", "max_tokens": 512,
        "vision": False, "base_prompt": "First rule.",
    }
    with patch("agent.loop.llm.stream_chat", scripted), \
         patch("agent.loop._agent_settings.load_settings", return_value=existing_cfg), \
         patch("agent.loop._agent_settings.save_settings") as mock_save:
        [e async for e in loop.run_turn(USER, conv, "remember second rule", CFG)]

    saved_cfg = mock_save.call_args[0][1]
    assert "First rule." in saved_cfg["base_prompt"]
    assert "Second rule." in saved_cfg["base_prompt"]


@pytest.mark.asyncio
async def test_remember_does_not_require_confirm(user_db):
    """'remember' must never emit a confirm_request event."""
    from agent import loop, store

    conv = store.create_conversation(USERNAME, "t")
    scripted = _scripted_llm([
        {"tool_calls": [_tool_call("c1", "remember", {"guidance": "No confirms needed."})]},
        {"content": "Done."},
    ])

    with patch("agent.loop.llm.stream_chat", scripted), \
         patch("agent.loop._agent_settings.load_settings", return_value={
             "base_url": "", "api_key": "", "model": "", "max_tokens": 512,
             "vision": False, "base_prompt": "",
         }), \
         patch("agent.loop._agent_settings.save_settings"):
        events = [e async for e in loop.run_turn(USER, conv, "remember this", CFG)]

    assert not any(e["type"] == "confirm_request" for e in events)
