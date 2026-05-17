"""Tests for the stdio transport — exercises the full MCP protocol over in-memory streams."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tests.conftest import FAKE_CREDS, FAKE_ENV


@pytest.fixture()
def live_server(mem_db, tmp_path):
    """Patch all external dependencies and return the mcp_tools server object."""
    import json as _json

    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(_json.dumps(FAKE_CREDS))
    env_file = tmp_path / ".env"
    env_file.write_text(f"FLICKR_API_KEY={FAKE_ENV[0]}\nFLICKR_API_SECRET={FAKE_ENV[1]}\n")

    import sqlite3 as _sqlite3

    def _make_conn():
        c = _sqlite3.connect(mem_db)
        c.row_factory = _sqlite3.Row
        return c

    with (
        patch("flickr_api.CREDENTIALS_FILE", str(creds_file)),
        patch("flickr_api.ENV_FILE", str(env_file)),
        patch("mcp_tools.db", side_effect=_make_conn),
        patch("mcp_tools._load_credentials", return_value=FAKE_CREDS),
        patch("mcp_tools._load_env", return_value=FAKE_ENV),
    ):
        import mcp_tools
        yield mcp_tools.server


# ---------------------------------------------------------------------------
# Protocol-level tests
# ---------------------------------------------------------------------------

class TestStdioProtocol:
    @pytest.mark.asyncio
    async def test_initialize_handshake(self, live_server):
        """Server completes the MCP initialize handshake."""
        async with create_connected_server_and_client_session(live_server) as session:
            # If we get here without exception the handshake succeeded
            assert session is not None

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_tools(self, live_server):
        """list_tools returns all registered tool names."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.list_tools()
            names = {t.name for t in result.tools}

        expected = {
            "search_photos", "get_photo", "get_summary", "update_photo",
            "fetch_photo_image", "find_albums", "get_album_photos",
            "add_to_album", "remove_from_album", "create_album", "edit_album",
            "delete_album", "find_groups", "add_to_group", "remove_from_group",
            "set_group_keywords", "find_unfollow_candidates", "protect_contact",
            "unfollow_contact", "get_contacts_summary", "get_photo_comments",
            "add_comment", "delete_comment", "fave_photo", "get_photo_stats",
            "find_weak_photos", "set_visibility", "set_location", "sync",
            "list_recent_syncs", "get_exif", "get_upload_status",
            "get_person_info", "get_photostream_stats", "get_popular_photos",
            "get_gallery_photos", "get_group_photos", "get_faves",
            "get_recent_activity", "remove_fave", "remove_location",
            "join_group", "leave_group", "set_safety_level", "set_content_type",
            "set_dates", "create_gallery", "add_to_gallery", "get_galleries",
            "get_contact_uploads", "search_all_groups",
        }
        assert expected.issubset(names), f"Missing tools: {expected - names}"

    @pytest.mark.asyncio
    async def test_tool_schemas_have_descriptions(self, live_server):
        """Every tool must have a non-empty description."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.list_tools()

        for tool in result.tools:
            assert tool.description, f"Tool '{tool.name}' has no description"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, live_server):
        """Calling a non-existent tool returns an error text result."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.call_tool("no_such_tool", {})

        texts = [c.text for c in result.content if hasattr(c, "text")]
        assert any("Unknown tool" in t for t in texts)


# ---------------------------------------------------------------------------
# End-to-end tool calls over in-memory stdio transport
# ---------------------------------------------------------------------------

class TestStdioToolCalls:
    @pytest.mark.asyncio
    async def test_get_summary_via_stdio(self, live_server):
        """get_summary returns a JSON object with expected keys."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.call_tool("get_summary", {})

        text = result.content[0].text
        data = json.loads(text)
        assert data["total_photos"] == 1
        assert "top_tags" in data

    @pytest.mark.asyncio
    async def test_search_photos_via_stdio(self, live_server):
        """search_photos returns the seeded photo over the wire."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.call_tool("search_photos", {"query": "Test"})

        photos = json.loads(result.content[0].text)
        assert len(photos) == 1
        assert photos[0]["id"] == "photo1"

    @pytest.mark.asyncio
    async def test_get_photo_via_stdio(self, live_server):
        """get_photo returns the correct photo by ID."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.call_tool("get_photo", {"id": "photo1"})

        photo = json.loads(result.content[0].text)
        assert photo["title"] == "Test Photo"

    @pytest.mark.asyncio
    async def test_get_photo_not_found_via_stdio(self, live_server):
        """get_photo for a missing ID returns a not-found message."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.call_tool("get_photo", {"id": "missing"})

        assert "not found" in result.content[0].text

    @pytest.mark.asyncio
    async def test_find_albums_via_stdio(self, live_server):
        """find_albums returns the seeded album."""
        async with create_connected_server_and_client_session(live_server) as session:
            result = await session.call_tool("find_albums", {"query": "My"})

        albums = json.loads(result.content[0].text)
        assert albums[0]["id"] == "album1"

    @pytest.mark.asyncio
    async def test_update_photo_via_stdio(self, live_server):
        """update_photo calls the Flickr API mock and confirms the update."""
        with patch("mcp_tools._api_post", return_value={"stat": "ok"}):
            async with create_connected_server_and_client_session(live_server) as session:
                result = await session.call_tool(
                    "update_photo", {"id": "photo1", "title": "Via Stdio"}
                )

        assert "title/description" in result.content[0].text
