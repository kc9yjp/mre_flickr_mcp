"""Tests for MCP tool handlers — no Flickr API or file-system required."""

import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tests.conftest import FAKE_CREDS, make_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(result) -> str:
    """Extract text from a tool result list."""
    return result[0].text


def _json(result) -> dict | list:
    return json.loads(_text(result))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_globals(tmp_path):
    """Patch all file-system globals before each test so import is clean."""
    import json as _json_mod
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(_json_mod.dumps(FAKE_CREDS))
    env_file = tmp_path / ".env"
    env_file.write_text("FLICKR_API_KEY=k\nFLICKR_API_SECRET=s\n")

    with (
        patch("flickr_api.CREDENTIALS_FILE", str(creds_file)),
        patch("flickr_api.ENV_FILE", str(env_file)),
    ):
        yield


@pytest.fixture()
def db(mem_db):
    """Patch mcp_tools.db() to create fresh connections to the test DB file each call."""
    import sqlite3 as _sqlite3

    def _make_conn():
        c = _sqlite3.connect(mem_db)
        c.row_factory = _sqlite3.Row
        return c

    verify_conn = _make_conn()
    with patch("mcp_tools.db", side_effect=_make_conn):
        yield verify_conn
    verify_conn.close()


@pytest.fixture()
def api_get():
    with patch("mcp_tools._api_get") as m:
        yield m


@pytest.fixture()
def api_post():
    with patch("mcp_tools._api_post") as m:
        yield m


@pytest.fixture()
def creds():
    with patch("mcp_tools._load_credentials", return_value=FAKE_CREDS):
        yield FAKE_CREDS


# ---------------------------------------------------------------------------
# search_photos
# ---------------------------------------------------------------------------

class TestSearchPhotos:
    @pytest.mark.asyncio
    async def test_returns_all_photos_by_default(self, db):
        import mcp_tools
        result = await mcp_tools._search_photos({})
        photos = _json(result)
        assert len(photos) == 1
        assert photos[0]["id"] == "photo1"

    @pytest.mark.asyncio
    async def test_keyword_filter(self, db):
        import mcp_tools
        result = await mcp_tools._search_photos({"query": "Test"})
        assert len(_json(result)) == 1

        result = await mcp_tools._search_photos({"query": "nope"})
        assert len(_json(result)) == 0

    @pytest.mark.asyncio
    async def test_tag_filter(self, db):
        import mcp_tools
        result = await mcp_tools._search_photos({"tags": "sunset"})
        assert len(_json(result)) == 1

        result = await mcp_tools._search_photos({"tags": "portrait"})
        assert len(_json(result)) == 0

    @pytest.mark.asyncio
    async def test_date_range_filter(self, db):
        import mcp_tools
        result = await mcp_tools._search_photos({"date_from": "2024-01-01", "date_to": "2024-12-31"})
        assert len(_json(result)) == 1

        result = await mcp_tools._search_photos({"date_from": "2025-01-01"})
        assert len(_json(result)) == 0

    @pytest.mark.asyncio
    async def test_incomplete_filter(self, db):
        import mcp_tools
        # photo1 has a title, description, and tags — not incomplete
        result = await mcp_tools._search_photos({"incomplete": True})
        assert len(_json(result)) == 0

        # insert a photo with no title
        db.execute(
            "INSERT INTO photos (id, title, tags, views, favorites, comments) VALUES (?,?,?,?,?,?)",
            ("photo2", "", "", 0, 0, 0),
        )
        db.commit()
        result = await mcp_tools._search_photos({"incomplete": True})
        assert len(_json(result)) == 1


# ---------------------------------------------------------------------------
# get_photo
# ---------------------------------------------------------------------------

class TestGetPhoto:
    @pytest.mark.asyncio
    async def test_found(self, db):
        import mcp_tools
        result = await mcp_tools._get_photo({"id": "photo1"})
        photo = _json(result)
        assert photo["id"] == "photo1"
        assert photo["title"] == "Test Photo"

    @pytest.mark.asyncio
    async def test_not_found(self, db):
        import mcp_tools
        result = await mcp_tools._get_photo({"id": "missing"})
        assert "not found" in _text(result)


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------

class TestGetSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self, db):
        import mcp_tools
        result = await mcp_tools._get_summary()
        summary = _json(result)
        assert summary["total_photos"] == 1
        assert summary["public_photos"] == 1
        assert summary["total_views"] == 100
        assert "top_tags" in summary
        assert any(t["tag"] == "sunset" for t in summary["top_tags"])


# ---------------------------------------------------------------------------
# update_photo
# ---------------------------------------------------------------------------

class TestUpdatePhoto:
    @pytest.mark.asyncio
    async def test_update_title_and_tags(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._update_photo({"id": "photo1", "title": "New Title", "tags": "rain"})
        assert "title/description" in _text(result)
        assert "tags" in _text(result)
        row = db.execute("SELECT title, tags FROM photos WHERE id='photo1'").fetchone()
        assert row["title"] == "New Title"
        assert row["tags"] == "rain"

    @pytest.mark.asyncio
    async def test_update_tags_only(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._update_photo({"id": "photo1", "tags": "fog"})
        assert "tags" in _text(result)
        row = db.execute("SELECT tags FROM photos WHERE id='photo1'").fetchone()
        assert row["tags"] == "fog"


# ---------------------------------------------------------------------------
# find_albums / get_album_photos
# ---------------------------------------------------------------------------

class TestAlbums:
    @pytest.mark.asyncio
    async def test_find_albums(self, db):
        import mcp_tools
        result = await mcp_tools._find_albums({"query": "My"})
        albums = _json(result)
        assert len(albums) == 1
        assert albums[0]["id"] == "album1"

    @pytest.mark.asyncio
    async def test_find_albums_no_match(self, db):
        import mcp_tools
        result = await mcp_tools._find_albums({"query": "xyz"})
        assert "No albums" in _text(result)

    @pytest.mark.asyncio
    async def test_get_album_photos(self, db, creds, api_get):
        import mcp_tools
        api_get.return_value = {
            "stat": "ok",
            "photoset": {
                "photo": [{"id": "photo1", "title": "Test Photo"}],
                "total": "1",
                "pages": "1",
            },
        }
        result = await mcp_tools._get_album_photos({"album_id": "album1"})
        data = _json(result)
        assert data["total"] == 1
        assert data["photos"][0]["id"] == "photo1"


# ---------------------------------------------------------------------------
# find_groups / set_group_keywords
# ---------------------------------------------------------------------------

class TestGroups:
    @pytest.mark.asyncio
    async def test_find_groups_by_name(self, db):
        import mcp_tools
        result = await mcp_tools._find_groups({"query": "Landscape"})
        groups = _json(result)
        assert len(groups) == 1
        assert groups[0]["id"] == "group1@N00"

    @pytest.mark.asyncio
    async def test_find_groups_by_keyword(self, db):
        import mcp_tools
        result = await mcp_tools._find_groups({"query": "nature"})
        assert len(_json(result)) == 1

    @pytest.mark.asyncio
    async def test_find_groups_no_match(self, db):
        import mcp_tools
        result = await mcp_tools._find_groups({"query": "zzz"})
        assert "No groups" in _text(result)

    @pytest.mark.asyncio
    async def test_set_group_keywords(self, db):
        import mcp_tools
        result = await mcp_tools._set_group_keywords({"group_id": "group1@N00", "keywords": "mountains snow"})
        assert "updated" in _text(result)
        row = db.execute("SELECT keywords FROM groups WHERE id='group1@N00'").fetchone()
        assert row["keywords"] == "mountains snow"

    @pytest.mark.asyncio
    async def test_set_group_keywords_not_found(self, db):
        import mcp_tools
        result = await mcp_tools._set_group_keywords({"group_id": "bad@N00", "keywords": "x"})
        assert "not found" in _text(result)


# ---------------------------------------------------------------------------
# protect_contact / find_unfollow_candidates
# ---------------------------------------------------------------------------

class TestContacts:
    @pytest.mark.asyncio
    async def test_protect_contact(self, db):
        import mcp_tools
        result = await mcp_tools._protect_contact({"contact_id": "contact1@N00", "reason": "friend"})
        assert "do-not-unfollow" in _text(result)
        row = db.execute("SELECT * FROM do_not_unfollow WHERE contact_id='contact1@N00'").fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_find_unfollow_candidates_excludes_protected(self, db):
        import mcp_tools
        await mcp_tools._protect_contact({"contact_id": "contact1@N00"})
        result = await mcp_tools._find_unfollow_candidates({})
        text = _text(result)
        assert "contact1@N00" not in text

    @pytest.mark.asyncio
    async def test_find_unfollow_candidates_returned(self, db):
        import mcp_tools
        result = await mcp_tools._find_unfollow_candidates({})
        candidates = _json(result)
        assert any(c["contact_id"] == "contact1@N00" for c in candidates)


# ---------------------------------------------------------------------------
# set_visibility
# ---------------------------------------------------------------------------

class TestSetVisibility:
    @pytest.mark.asyncio
    async def test_make_private(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._set_visibility({"id": "photo1", "is_public": False})
        assert "private" in _text(result)
        row = db.execute("SELECT is_public FROM photos WHERE id='photo1'").fetchone()
        assert row["is_public"] == 0

    @pytest.mark.asyncio
    async def test_make_public(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._set_visibility({"id": "photo1", "is_public": True})
        assert "public" in _text(result)
        row = db.execute("SELECT is_public FROM photos WHERE id='photo1'").fetchone()
        assert row["is_public"] == 1


# ---------------------------------------------------------------------------
# set_location / remove_location
# ---------------------------------------------------------------------------

class TestLocation:
    @pytest.mark.asyncio
    async def test_set_location(self, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._set_location({"id": "photo1", "lat": 37.77, "lon": -122.41})
        assert "37.77" in _text(result)
        api_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_location(self, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._remove_location({"id": "photo1"})
        assert "photo1" in _text(result)
        api_post.assert_called_once()


# ---------------------------------------------------------------------------
# comment tools
# ---------------------------------------------------------------------------

class TestComments:
    @pytest.mark.asyncio
    async def test_add_comment(self, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok", "comment": {"id": "cmt1"}}
        result = await mcp_tools._add_comment({"photo_id": "photo1", "comment_text": "Great!"})
        assert "cmt1" in _text(result)

    @pytest.mark.asyncio
    async def test_delete_comment(self, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._delete_comment({"comment_id": "cmt1"})
        assert "deleted" in _text(result)

    @pytest.mark.asyncio
    async def test_get_photo_comments(self, api_get):
        import mcp_tools
        api_get.return_value = {
            "stat": "ok",
            "comments": {"comment": [
                {"authorname": "alice", "datecreate": "1700000000",
                 "_content": "Nice!", "permalink": "https://flickr.com/p/1"},
            ]},
        }
        result = await mcp_tools._get_photo_comments({"photo_id": "photo1"})
        comments = _json(result)
        assert comments[0]["author"] == "alice"
        assert comments[0]["comment"] == "Nice!"


# ---------------------------------------------------------------------------
# find_weak_photos
# ---------------------------------------------------------------------------

class TestFindWeakPhotos:
    @pytest.mark.asyncio
    async def test_returns_photo(self, db):
        import mcp_tools
        result = await mcp_tools._find_weak_photos({"min_age_days": 0})
        photos = _json(result)
        assert any(p["id"] == "photo1" for p in photos)

    @pytest.mark.asyncio
    async def test_require_zero_favorites_excludes_faved(self, db):
        import mcp_tools
        # photo1 has 5 favorites, so it should be excluded
        result = await mcp_tools._find_weak_photos({"min_age_days": 0, "require_zero_favorites": True})
        photos = _json(result)
        assert not any(p["id"] == "photo1" for p in photos)
