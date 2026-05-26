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

    verify_conn = _sqlite3.connect(mem_db)
    verify_conn.row_factory = _sqlite3.Row
    with patch("db.DB_FILE", mem_db):
        yield verify_conn
    verify_conn.close()


@pytest.fixture()
def api_get():
    with patch("flickr_api._api_get") as m:
        yield m


@pytest.fixture()
def api_post():
    with patch("flickr_api._api_post") as m:
        yield m


@pytest.fixture()
def creds():
    with patch("flickr_api._load_credentials", return_value=FAKE_CREDS):
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
# photo_groups: add/remove, get_photo_contexts, get_group_stats, get_photo_group_count
# ---------------------------------------------------------------------------

class TestPhotoGroups:
    @pytest.mark.asyncio
    async def test_add_to_group_writes_local_db(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        await mcp_tools._add_to_group({"photo_id": "photo1", "group_id": "group1@N00"})
        row = db.execute(
            "SELECT 1 FROM photo_groups WHERE photo_id='photo1' AND group_id='group1@N00'"
        ).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_remove_from_group_deletes_local_db(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        db.execute("INSERT INTO photo_groups VALUES ('photo1', 'group1@N00')")
        db.commit()
        await mcp_tools._remove_from_group({"photo_id": "photo1", "group_id": "group1@N00"})
        row = db.execute(
            "SELECT 1 FROM photo_groups WHERE photo_id='photo1' AND group_id='group1@N00'"
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_get_photo_contexts_local_db_path(self, db, api_get):
        import mcp_tools
        import time as _time
        db.execute("INSERT INTO photo_groups VALUES ('photo1', 'group1@N00')")
        db.execute("INSERT INTO sync_log VALUES (?,?,?,?,?)",
                   (None, int(_time.time()), "full", 1, "groups"))
        db.commit()
        api_get.return_value = {"set": [{"id": "album1", "title": "My Album"}], "pool": []}
        result = await mcp_tools._get_photo_contexts({"photo_id": "photo1"})
        data = _json(result)
        assert data["source"] == "local_db"
        assert any(g["id"] == "group1@N00" for g in data["group_pools"])
        assert any(a["id"] == "album1" for a in data["albums"])

    @pytest.mark.asyncio
    async def test_get_photo_contexts_api_fallback(self, db, api_get):
        import mcp_tools
        api_get.return_value = {
            "pool": [{"id": "group1@N00", "title": "Landscape Lovers"}],
            "set":  [{"id": "album1", "title": "My Album"}],
        }
        result = await mcp_tools._get_photo_contexts({"photo_id": "photo1"})
        data = _json(result)
        assert data["source"] == "flickr_api"
        assert any(g["id"] == "group1@N00" for g in data["group_pools"])
        assert any(a["id"] == "album1" for a in data["albums"])

    @pytest.mark.asyncio
    async def test_get_group_stats_happy_path(self, db):
        import mcp_tools
        db.execute("INSERT INTO photo_groups VALUES ('photo1', 'group1@N00')")
        db.commit()
        result = await mcp_tools._get_group_stats({})
        rows = _json(result)
        assert rows[0]["id"] == "group1@N00"
        assert rows[0]["my_count"] == 1

    @pytest.mark.asyncio
    async def test_get_group_stats_empty(self, db):
        import mcp_tools
        result = await mcp_tools._get_group_stats({})
        # groups exist but none have photos — still returns rows (count 0), not an error
        rows = _json(result)
        assert all(r["my_count"] == 0 for r in rows)

    @pytest.mark.asyncio
    async def test_get_photo_group_count_happy_path(self, db):
        import mcp_tools
        db.execute("INSERT INTO photo_groups VALUES ('photo1', 'group1@N00')")
        db.commit()
        result = await mcp_tools._get_photo_group_count({})
        rows = _json(result)
        assert rows[0]["id"] == "photo1"
        assert rows[0]["group_count"] == 1

    @pytest.mark.asyncio
    async def test_get_photo_group_count_empty(self, db):
        import mcp_tools
        result = await mcp_tools._get_photo_group_count({})
        assert "No photo-group data" in _text(result)

    @pytest.mark.asyncio
    async def test_add_to_group_queues_on_daily_limit(self, db, api_post):
        from flickr_api import FlickrAPIError
        import mcp_tools
        api_post.side_effect = FlickrAPIError(5, "Daily posting limit reached")
        result = await mcp_tools._add_to_group({"photo_id": "photo1", "group_id": "group1@N00"})
        assert "queued" in _text(result).lower()
        row = db.execute(
            "SELECT status FROM pending_group_adds WHERE photo_id='photo1' AND group_id='group1@N00'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "waiting"

    @pytest.mark.asyncio
    async def test_add_to_group_reschedules_if_already_queued(self, db, api_post):
        from flickr_api import FlickrAPIError
        import mcp_tools
        old_retry = int(time.time()) + 1000
        db.execute(
            "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at) "
            "VALUES (?, ?, 'waiting', ?, ?)",
            ("photo1", "group1@N00", old_retry, int(time.time())),
        )
        db.commit()
        api_post.side_effect = FlickrAPIError(5, "Daily posting limit reached")
        result = await mcp_tools._add_to_group(
            {"photo_id": "photo1", "group_id": "group1@N00", "retry_at": "morning"}
        )
        assert "rescheduled" in _text(result).lower()
        count = db.execute(
            "SELECT COUNT(*) FROM pending_group_adds WHERE photo_id='photo1'"
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_add_to_group_reraises_non_limit_errors(self, db, api_post):
        from flickr_api import FlickrAPIError
        import mcp_tools
        api_post.side_effect = FlickrAPIError(2, "Unknown user")
        with pytest.raises(FlickrAPIError):
            await mcp_tools._add_to_group({"photo_id": "photo1", "group_id": "group1@N00"})


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


# ---------------------------------------------------------------------------
# _parse_retry_time
# ---------------------------------------------------------------------------

class TestParseRetryTime:
    def _ct_hour_minute(self, ts: int) -> tuple[int, int]:
        import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.datetime.fromtimestamp(ts, ZoneInfo("America/Chicago"))
        return dt.hour, dt.minute

    def test_none_defaults_to_5pm_ct(self):
        from tools.groups import _parse_retry_time
        h, m = self._ct_hour_minute(_parse_retry_time(None))
        assert h == 17 and m == 0

    def test_named_morning(self):
        from tools.groups import _parse_retry_time
        h, m = self._ct_hour_minute(_parse_retry_time("morning"))
        assert h == 8 and m == 0

    def test_named_lunchtime(self):
        from tools.groups import _parse_retry_time
        h, m = self._ct_hour_minute(_parse_retry_time("lunchtime"))
        assert h == 12 and m == 0

    def test_named_evening(self):
        from tools.groups import _parse_retry_time
        h, m = self._ct_hour_minute(_parse_retry_time("evening"))
        assert h == 18 and m == 0

    def test_hhmm_parsing(self):
        from tools.groups import _parse_retry_time
        h, m = self._ct_hour_minute(_parse_retry_time("09:30"))
        assert h == 9 and m == 30

    def test_result_always_in_future(self):
        from tools.groups import _parse_retry_time
        now = int(time.time())
        for t in [None, "morning", "afternoon", "09:00", "23:59"]:
            assert _parse_retry_time(t) > now

    def test_result_within_25_hours(self):
        from tools.groups import _parse_retry_time
        now = int(time.time())
        limit = now + 25 * 3600
        for t in [None, "morning", "afternoon", "09:00", "23:59", "00:01"]:
            assert _parse_retry_time(t) <= limit

    def test_unrecognised_falls_back_to_midnight_utc(self):
        import datetime
        from tools.groups import _parse_retry_time
        ts = _parse_retry_time("garbage")
        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        assert dt.hour == 0 and dt.minute == 0
        assert ts > int(time.time())

    def test_invalid_hhmm_falls_back_to_midnight_utc(self):
        import datetime
        from tools.groups import _parse_retry_time
        ts = _parse_retry_time("25:00")
        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        assert dt.hour == 0 and dt.minute == 0


# ---------------------------------------------------------------------------
# _flush_group_queue
# ---------------------------------------------------------------------------

class TestFlushGroupQueue:
    def _conn(self):
        from tests.conftest import make_db
        return make_db()

    def test_due_item_succeeds(self):
        from tools.groups import _flush_group_queue
        conn = self._conn()
        now = int(time.time())
        conn.execute(
            "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at) "
            "VALUES (?, ?, 'waiting', ?, ?)",
            ("photo1", "group1@N00", now - 60, now - 3600),
        )
        conn.commit()
        with patch("flickr_api._api_post", return_value={"stat": "ok"}):
            flushed = _flush_group_queue(conn)
        assert len(flushed) == 1
        assert flushed[0]["result"] == "success"
        row = conn.execute("SELECT status FROM pending_group_adds").fetchone()
        assert row["status"] == "success"
        pg = conn.execute(
            "SELECT 1 FROM photo_groups WHERE photo_id='photo1' AND group_id='group1@N00'"
        ).fetchone()
        assert pg is not None

    def test_future_item_is_skipped(self):
        from tools.groups import _flush_group_queue
        conn = self._conn()
        now = int(time.time())
        conn.execute(
            "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at) "
            "VALUES (?, ?, 'waiting', ?, ?)",
            ("photo1", "group1@N00", now + 3600, now - 3600),
        )
        conn.commit()
        with patch("flickr_api._api_post") as mock_post:
            flushed = _flush_group_queue(conn)
        assert flushed == []
        mock_post.assert_not_called()
        row = conn.execute("SELECT status FROM pending_group_adds").fetchone()
        assert row["status"] == "waiting"

    def test_still_limited_reschedules_to_next_midnight(self):
        from flickr_api import FlickrAPIError
        from tools.groups import _flush_group_queue
        conn = self._conn()
        now = int(time.time())
        conn.execute(
            "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at) "
            "VALUES (?, ?, 'waiting', ?, ?)",
            ("photo1", "group1@N00", now - 60, now - 3600),
        )
        conn.commit()
        with patch("flickr_api._api_post", side_effect=FlickrAPIError(5, "Daily limit")):
            flushed = _flush_group_queue(conn)
        assert flushed[0]["result"] == "still_limited"
        row = conn.execute("SELECT status, retry_after FROM pending_group_adds").fetchone()
        assert row["status"] == "waiting"
        assert row["retry_after"] > now

    def test_other_flickr_error_marks_error(self):
        from flickr_api import FlickrAPIError
        from tools.groups import _flush_group_queue
        conn = self._conn()
        now = int(time.time())
        conn.execute(
            "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at) "
            "VALUES (?, ?, 'waiting', ?, ?)",
            ("photo1", "group1@N00", now - 60, now - 3600),
        )
        conn.commit()
        with patch("flickr_api._api_post", side_effect=FlickrAPIError(2, "Unknown user")):
            flushed = _flush_group_queue(conn)
        assert "error" in flushed[0]["result"]
        row = conn.execute("SELECT status FROM pending_group_adds").fetchone()
        assert row["status"] == "error"

    def test_force_processes_future_items(self):
        from tools.groups import _flush_group_queue
        conn = self._conn()
        now = int(time.time())
        conn.execute(
            "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at) "
            "VALUES (?, ?, 'waiting', ?, ?)",
            ("photo1", "group1@N00", now + 3600, now - 3600),
        )
        conn.commit()
        with patch("flickr_api._api_post", return_value={"stat": "ok"}):
            flushed = _flush_group_queue(conn, force=True)
        assert len(flushed) == 1
        assert flushed[0]["result"] == "success"
