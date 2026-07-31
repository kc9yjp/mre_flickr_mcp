"""Tests for flickr_sync groups sync — no real Flickr API or LLM required."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import flickr_sync

FAKE_CREDS = {"user_nsid": "99999999@N00"}


def _groups_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE groups (
            id TEXT PRIMARY KEY, name TEXT, members INTEGER, pool_count INTEGER,
            synced_at INTEGER, description TEXT, user_note TEXT,
            needs_summary INTEGER DEFAULT 1, summary_md TEXT,
            is_milestone INTEGER DEFAULT 0, fave_min INTEGER, view_min INTEGER,
            open_subject INTEGER, ai_keywords TEXT, summary_generated_at INTEGER
        )
    """)
    conn.execute("CREATE TABLE photo_groups (photo_id TEXT, group_id TEXT, PRIMARY KEY (photo_id, group_id))")
    return conn


def _insert_group(conn, gid, name, needs_summary=0, description="", user_note=""):
    conn.execute(
        "INSERT INTO groups (id, name, members, pool_count, synced_at, description, user_note, needs_summary) "
        "VALUES (?, ?, 10, 5, 0, ?, ?, ?)",
        (gid, name, description, user_note, needs_summary),
    )
    conn.commit()


def _group_row(conn, gid):
    return conn.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()


class TestSyncGroups:
    def test_new_group_inserted_needs_summary(self):
        conn = _groups_db()
        fetched = {"groups": {"group": [{"nsid": "1@N", "name": "Tree Pics", "members": "10", "pool_count": "5"}], "pages": 1}}
        with (
            patch("flickr_sync.flickr_api._load_credentials", return_value=FAKE_CREDS),
            patch("flickr_sync.api_get", return_value=fetched),
        ):
            total = flickr_sync.sync_groups(conn)
        assert total == 1
        row = _group_row(conn, "1@N")
        assert row["name"] == "Tree Pics"
        assert row["needs_summary"] == 1

    def test_renamed_group_flags_needs_summary(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Old Name", needs_summary=0)
        fetched = {"groups": {"group": [{"nsid": "1@N", "name": "New Name", "members": "10", "pool_count": "5"}], "pages": 1}}
        with (
            patch("flickr_sync.flickr_api._load_credentials", return_value=FAKE_CREDS),
            patch("flickr_sync.api_get", return_value=fetched),
        ):
            flickr_sync.sync_groups(conn)
        row = _group_row(conn, "1@N")
        assert row["name"] == "New Name"
        assert row["needs_summary"] == 1

    def test_unchanged_group_keeps_needs_summary_false(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Same Name", needs_summary=0)
        fetched = {"groups": {"group": [{"nsid": "1@N", "name": "Same Name", "members": "10", "pool_count": "5"}], "pages": 1}}
        with (
            patch("flickr_sync.flickr_api._load_credentials", return_value=FAKE_CREDS),
            patch("flickr_sync.api_get", return_value=fetched),
        ):
            flickr_sync.sync_groups(conn)
        row = _group_row(conn, "1@N")
        assert row["needs_summary"] == 0

    def test_left_group_deleted_with_memberships(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Still Joined")
        _insert_group(conn, "2@N", "Left This One")
        conn.execute("INSERT INTO photo_groups VALUES ('photoA', '2@N')")
        conn.commit()
        fetched = {"groups": {"group": [{"nsid": "1@N", "name": "Still Joined", "members": "10", "pool_count": "5"}], "pages": 1}}
        with (
            patch("flickr_sync.flickr_api._load_credentials", return_value=FAKE_CREDS),
            patch("flickr_sync.api_get", return_value=fetched),
        ):
            flickr_sync.sync_groups(conn)
        assert _group_row(conn, "2@N") is None
        assert conn.execute("SELECT 1 FROM photo_groups WHERE group_id='2@N'").fetchone() is None
        assert _group_row(conn, "1@N") is not None


class TestSyncGroupDescriptions:
    def test_changed_description_flags_needs_summary(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics", needs_summary=0, description="old text")
        info = {"group": {"description": {"_content": "new text"}}}
        with patch("flickr_sync.flickr_api._api_get", return_value=info):
            changed = flickr_sync.sync_group_descriptions(conn)
        assert changed == 1
        row = _group_row(conn, "1@N")
        assert row["description"] == "new text"
        assert row["needs_summary"] == 1

    def test_unchanged_description_leaves_needs_summary(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics", needs_summary=0, description="same text")
        info = {"group": {"description": {"_content": "same text"}}}
        with patch("flickr_sync.flickr_api._api_get", return_value=info):
            changed = flickr_sync.sync_group_descriptions(conn)
        assert changed == 0
        assert _group_row(conn, "1@N")["needs_summary"] == 0

    def test_api_error_skips_group(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics", needs_summary=0, description="text")
        with patch("flickr_sync.flickr_api._api_get", side_effect=RuntimeError("boom")):
            changed = flickr_sync.sync_group_descriptions(conn)
        assert changed == 0


class TestParseGroupSummaryJson:
    def test_plain_json(self):
        result = flickr_sync._parse_group_summary_json('{"summary": "x"}')
        assert result["summary"] == "x"

    def test_strips_code_fence(self):
        text = "```json\n{\"summary\": \"x\"}\n```"
        result = flickr_sync._parse_group_summary_json(text)
        assert result["summary"] == "x"


class TestBuildGroupSummaryPrompt:
    def test_uses_default_template_and_substitutes_fields(self):
        from agent import prompts_store
        with patch.object(prompts_store, "get_prompt_by_code", return_value=None):
            prompt = flickr_sync._build_group_summary_prompt(
                "user@N00", "Tree Pics", "Trees only, please.", "post max 1/day"
            )
        assert "Tree Pics" in prompt
        assert "Trees only, please." in prompt
        assert "post max 1/day" in prompt

    def test_uses_custom_prompt_text_when_edited(self):
        from agent import prompts_store
        custom = {"text": "CUSTOM {group_name} / {group_description} / {group_user_note}"}
        with patch.object(prompts_store, "get_prompt_by_code", return_value=custom):
            prompt = flickr_sync._build_group_summary_prompt("user@N00", "Tree Pics", "desc", "note")
        assert prompt == "CUSTOM Tree Pics / desc / note"

    def test_curly_braces_in_description_do_not_break_substitution(self):
        from agent import prompts_store
        with patch.object(prompts_store, "get_prompt_by_code", return_value=None):
            prompt = flickr_sync._build_group_summary_prompt(
                "user@N00", "Tree Pics", "Rules: {no watermarks}", ""
            )
        assert "{no watermarks}" in prompt

    def test_html_description_is_converted_to_plain_text(self):
        """Flickr group descriptions come back as HTML — the prompt sent to
        the LLM must not contain raw markup, and block structure (paragraphs,
        list items) should survive as line breaks rather than collapsing."""
        from agent import prompts_store
        html_description = "<p>Trees only.</p><ul><li>Max 1/day</li><li>No watermarks</li></ul>"
        with patch.object(prompts_store, "get_prompt_by_code", return_value=None):
            prompt = flickr_sync._build_group_summary_prompt(
                "user@N00", "Tree Pics", html_description, ""
            )
        assert "<p>" not in prompt
        assert "<li>" not in prompt
        assert "Trees only." in prompt
        assert "- Max 1/day" in prompt
        assert "- No watermarks" in prompt


class TestSyncGroupSummaries:
    def _fake_cfg(self):
        return {"base_url": "http://fake/v1", "model": "test-model", "api_mode": "chat_completions"}

    async def _fake_stream_chat(self, cfg, messages, client=None):
        yield {"type": "message", "content": json.dumps({
            "summary": "A milestone group for well-viewed photos.",
            "is_milestone": True,
            "fave_min": 50,
            "view_min": 500,
            "open_subject": False,
            "keywords": ["milestone", "views"],
        })}

    def test_generates_summary_for_flagged_group(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "500 Views Club", needs_summary=1,
                       description="Post once you hit 500 views.", user_note="be patient")
        from agent import llm, settings, prompts_store
        with (
            patch.object(settings, "resolve_sync_cfg", return_value=self._fake_cfg()),
            patch.object(llm, "stream_chat", self._fake_stream_chat),
            patch.object(prompts_store, "get_prompt_by_code", return_value=None),
        ):
            updated = flickr_sync.sync_group_summaries(conn, "user@N00")
        assert updated == 1
        row = _group_row(conn, "1@N")
        assert row["needs_summary"] == 0
        assert row["is_milestone"] == 1
        assert row["fave_min"] == 50
        assert row["view_min"] == 500
        assert row["open_subject"] == 0
        assert "milestone" in row["ai_keywords"]
        assert row["summary_generated_at"]

    def test_throttles_between_successive_groups(self):
        """Regression: pacing must apply between groups (not before the first,
        not after the last) and use the configured throttle value."""
        conn = _groups_db()
        _insert_group(conn, "1@N", "Group One", needs_summary=1)
        _insert_group(conn, "2@N", "Group Two", needs_summary=1)
        from agent import llm, settings, prompts_store

        cfg = {**self._fake_cfg(), "sync_throttle_seconds": 5}
        with (
            patch.object(settings, "resolve_sync_cfg", return_value=cfg),
            patch.object(llm, "stream_chat", self._fake_stream_chat),
            patch.object(prompts_store, "get_prompt_by_code", return_value=None),
            patch("flickr_sync.asyncio.sleep") as mock_sleep,
        ):
            updated = flickr_sync.sync_group_summaries(conn, "user@N00")
        assert updated == 2
        mock_sleep.assert_called_once_with(5)

    def test_no_throttle_sleep_for_single_group(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Only Group", needs_summary=1)
        from agent import llm, settings, prompts_store

        cfg = {**self._fake_cfg(), "sync_throttle_seconds": 60}
        with (
            patch.object(settings, "resolve_sync_cfg", return_value=cfg),
            patch.object(llm, "stream_chat", self._fake_stream_chat),
            patch.object(prompts_store, "get_prompt_by_code", return_value=None),
            patch("flickr_sync.asyncio.sleep") as mock_sleep,
        ):
            flickr_sync.sync_group_summaries(conn, "user@N00")
        mock_sleep.assert_not_called()

    def test_zero_throttle_disables_pacing(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Group One", needs_summary=1)
        _insert_group(conn, "2@N", "Group Two", needs_summary=1)
        from agent import llm, settings, prompts_store

        cfg = {**self._fake_cfg(), "sync_throttle_seconds": 0}
        with (
            patch.object(settings, "resolve_sync_cfg", return_value=cfg),
            patch.object(llm, "stream_chat", self._fake_stream_chat),
            patch.object(prompts_store, "get_prompt_by_code", return_value=None),
            patch("flickr_sync.asyncio.sleep") as mock_sleep,
        ):
            flickr_sync.sync_group_summaries(conn, "user@N00")
        mock_sleep.assert_not_called()

    def test_no_groups_need_summary(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Fine As Is", needs_summary=0)
        updated = flickr_sync.sync_group_summaries(conn, "user@N00")
        assert updated == 0

    def test_skips_when_no_llm_connection_configured(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Needs Summary", needs_summary=1)
        from agent import settings
        with patch.object(settings, "resolve_sync_cfg", return_value={"base_url": "", "model": ""}):
            updated = flickr_sync.sync_group_summaries(conn, "user@N00")
        assert updated == 0
        assert _group_row(conn, "1@N")["needs_summary"] == 1

    async def _fake_stream_chat_bad_json(self, cfg, messages, client=None):
        yield {"type": "message", "content": "not json"}

    def test_malformed_llm_response_leaves_group_flagged(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Needs Summary", needs_summary=1)
        from agent import llm, settings, prompts_store
        with (
            patch.object(settings, "resolve_sync_cfg", return_value=self._fake_cfg()),
            patch.object(llm, "stream_chat", self._fake_stream_chat_bad_json),
            patch.object(prompts_store, "get_prompt_by_code", return_value=None),
        ):
            updated = flickr_sync.sync_group_summaries(conn, "user@N00")
        assert updated == 0
        assert _group_row(conn, "1@N")["needs_summary"] == 1

    def test_user_note_reaches_the_llm_prompt(self):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Needs Summary", needs_summary=1,
                       description="desc", user_note="only post at night")
        from agent import llm, settings, prompts_store
        captured = {}

        async def _capturing_stream_chat(cfg, messages, client=None):
            captured["messages"] = messages
            yield {"type": "message", "content": json.dumps({"summary": "x", "keywords": []})}

        with (
            patch.object(settings, "resolve_sync_cfg", return_value=self._fake_cfg()),
            patch.object(llm, "stream_chat", _capturing_stream_chat),
            patch.object(prompts_store, "get_prompt_by_code", return_value=None),
        ):
            flickr_sync.sync_group_summaries(conn, "user@N00")
        assert "only post at night" in captured["messages"][0]["content"]
