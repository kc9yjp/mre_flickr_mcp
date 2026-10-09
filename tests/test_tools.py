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
    async def test_not_found(self, db, api_get):
        import mcp_tools
        import flickr_api
        api_get.side_effect = flickr_api.FlickrAPIError(1, "Photo not found")
        result = await mcp_tools._get_photo({"id": "missing"})
        assert "not found" in _text(result)

    @pytest.mark.asyncio
    async def test_falls_back_to_live_api_for_other_users_photo(self, db, api_get):
        """A photo not in the local DB (e.g. someone else's) is fetched live."""
        import mcp_tools

        def _fake_api_get(method, params):
            if method == "flickr.photos.getInfo":
                return {
                    "photo": {
                        "owner": {"nsid": "other@N01", "username": "other", "realname": "Other User"},
                        "title": {"_content": "Someone Else's Photo"},
                        "description": {"_content": ""},
                        "tags": {"tag": []},
                        "views": "42",
                        "comments": {"_content": "3"},
                        "dates": {"taken": "2024-01-01 00:00:00", "posted": "1700000000"},
                        "visibility": {"ispublic": 1},
                    }
                }
            if method == "flickr.photos.getFavorites":
                return {"photo": {"total": "5"}}
            raise AssertionError(f"unexpected method {method}")

        api_get.side_effect = _fake_api_get
        result = await mcp_tools._get_photo({"id": "other_photo"})
        photo = _json(result)
        assert photo["title"] == "Someone Else's Photo"
        assert photo["owner"]["nsid"] == "other@N01"
        assert photo["favorites"] == 5
        assert photo["views"] == 42


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
    async def test_update_title_and_tags(self, db, api_get, api_post):
        import mcp_tools
        # description is omitted, so the handler carries the current value over
        # from the Flickr API (getInfo) rather than the local cache.
        api_get.return_value = {"photo": {
            "title": {"_content": "Old Title"},
            "description": {"_content": "Old description"},
        }}
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._update_photo({"id": "photo1", "title": "New Title", "tags": "rain"})
        assert "title/description" in _text(result)
        assert "tags" in _text(result)
        api_get.assert_called_once_with("flickr.photos.getInfo", {"photo_id": "photo1"})
        # setMeta must preserve the API's current description, not drop it.
        setmeta_call = next(c for c in api_post.call_args_list if c.args[0] == "flickr.photos.setMeta")
        assert setmeta_call.args[1]["description"] == "Old description"
        assert setmeta_call.args[1]["title"] == "New Title"
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
        assert "No albums match 'xyz'." in _text(result)

    @pytest.mark.asyncio
    async def test_find_albums_empty_table(self, db):
        import mcp_tools
        db.execute("DELETE FROM albums")
        db.commit()
        result = await mcp_tools._find_albums({"query": "xyz"})
        assert "Run 'sync albums' first" in _text(result)

    @pytest.mark.asyncio
    async def test_find_albums_like_wildcards_escaped(self, db):
        import mcp_tools
        result = await mcp_tools._find_albums({"query": "%"})
        assert "No albums match" in _text(result)

    @pytest.mark.asyncio
    async def test_get_all_albums(self, db):
        import mcp_tools
        result = await mcp_tools._get_all_albums({})
        albums = _json(result)
        assert len(albums) == 1
        assert albums[0]["id"] == "album1"

    @pytest.mark.asyncio
    async def test_get_all_albums_empty_table(self, db):
        import mcp_tools
        db.execute("DELETE FROM albums")
        db.commit()
        result = await mcp_tools._get_all_albums({})
        assert "Run 'sync albums' first" in _text(result)

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
# find_groups
# ---------------------------------------------------------------------------

class TestGroups:
    @pytest.mark.asyncio
    async def test_find_groups_by_name(self, db):
        import mcp_tools
        result = await mcp_tools._find_groups({"query": "Landscape"})
        text = _text(result)
        assert "Landscape Lovers" in text
        assert "`group1@N00`" in text

    @pytest.mark.asyncio
    async def test_find_groups_by_keyword(self, db):
        import mcp_tools
        result = await mcp_tools._find_groups({"query": "nature"})
        assert "`group1@N00`" in _text(result)

    @pytest.mark.asyncio
    async def test_find_groups_includes_summary_and_thresholds(self, db):
        import mcp_tools
        db.execute(
            "UPDATE groups SET is_milestone=1, fave_min=50, view_min=500, "
            "open_subject=0, user_note='post at most 1/day' WHERE id='group1@N00'"
        )
        db.commit()
        result = await mcp_tools._find_groups({"query": "Landscape"})
        text = _text(result)
        assert "milestone group" in text
        assert "min faves: 50" in text
        assert "min views: 500" in text
        assert "themed subject" in text
        assert "post at most 1/day" in text
        assert "A group for landscape photography." in text  # summary_md

    @pytest.mark.asyncio
    async def test_find_groups_no_match(self, db):
        import mcp_tools
        result = await mcp_tools._find_groups({"query": "zzz"})
        assert "No groups match 'zzz'." in _text(result)

    @pytest.mark.asyncio
    async def test_find_groups_empty_table(self, db):
        import mcp_tools
        db.execute("DELETE FROM groups")
        db.commit()
        result = await mcp_tools._find_groups({"query": "zzz"})
        assert "Run 'sync groups' first" in _text(result)

    @pytest.mark.asyncio
    async def test_find_groups_underscore_not_wildcard(self, db):
        import mcp_tools
        result = await mcp_tools._find_groups({"query": "Land_cape"})
        assert "No groups match" in _text(result)

    @pytest.mark.asyncio
    async def test_find_groups_converts_html_description_when_no_summary_yet(self, db):
        """A group with no AI summary yet falls back to its raw Flickr
        description, which comes back as HTML — it must be converted to
        plain text with structure preserved, not dumped with markup intact."""
        import mcp_tools
        db.execute(
            "UPDATE groups SET summary_md=NULL, "
            "description='<p>Trees only.</p><ul><li>One post per day</li><li>No watermarks</li></ul>' "
            "WHERE id='group1@N00'"
        )
        db.commit()
        result = await mcp_tools._find_groups({"query": "Landscape"})
        text = _text(result)
        assert "<p>" not in text
        assert "<li>" not in text
        assert "Trees only." in text
        assert "- One post per day" in text
        assert "- No watermarks" in text

    @pytest.mark.asyncio
    async def test_set_group_note(self, db):
        import mcp_tools
        result = await mcp_tools._set_group_note({"group_id": "group1@N00", "note": "max 2 posts/week"})
        assert "Note saved" in _text(result)
        row = db.execute("SELECT user_note, needs_summary FROM groups WHERE id='group1@N00'").fetchone()
        assert row["user_note"] == "max 2 posts/week"
        assert row["needs_summary"] == 1

    @pytest.mark.asyncio
    async def test_set_group_note_not_found(self, db):
        import mcp_tools
        result = await mcp_tools._set_group_note({"group_id": "bad@N00", "note": "x"})
        assert "not found" in _text(result)


# ---------------------------------------------------------------------------
# get_group_info
# ---------------------------------------------------------------------------

class TestGetGroupInfo:
    @pytest.mark.asyncio
    async def test_joined_group(self, db, api_get):
        import mcp_tools
        api_get.return_value = {
            "group": {
                "name": "Landscape Lovers",
                "description": {"_content": "A group for landscape photography."},
                "rules": {"_content": "Be nice."},
                "members": "500",
                "pool_count": "1000",
            }
        }
        result = await mcp_tools._get_group_info({"group_id": "group1@N00"})
        info = _json(result)
        assert info["joined"] is True
        assert info["members"] == 500
        assert info["pool_count"] == 1000
        assert info["name"] == "Landscape Lovers"

    @pytest.mark.asyncio
    async def test_unjoined_group(self, db, api_get):
        import mcp_tools
        api_get.return_value = {
            "group": {
                "name": "Someone Else's Group",
                "description": {"_content": ""},
                "rules": {"_content": ""},
                "members": "42",
                "pool_count": "17",
            }
        }
        result = await mcp_tools._get_group_info({"group_id": "other_group@N00"})
        info = _json(result)
        assert info["joined"] is False
        assert info["your_note"] is None

    @pytest.mark.asyncio
    async def test_not_found(self, db, api_get):
        import mcp_tools
        import flickr_api
        api_get.side_effect = flickr_api.FlickrAPIError(1, "Group not found")
        result = await mcp_tools._get_group_info({"group_id": "bad@N00"})
        assert "not found" in _text(result)


# ---------------------------------------------------------------------------
# photo_groups: add/remove, get_photo_contexts, get_group_stats, get_photo_group_count
# ---------------------------------------------------------------------------

class TestPhotoGroups:
    @pytest.mark.asyncio
    async def test_add_to_group_writes_local_db(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        await mcp_tools._add_to_group({"photo_id": "photo1", "group_ids": ["group1@N00"]})
        row = db.execute(
            "SELECT 1 FROM photo_groups WHERE photo_id='photo1' AND group_id='group1@N00'"
        ).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_add_to_group_multiple_groups(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._add_to_group(
            {"photo_id": "photo1", "group_ids": ["group1@N00", "group2@N00"]}
        )
        text = _text(result)
        assert "group1@N00" in text and "group2@N00" in text
        rows = db.execute(
            "SELECT group_id FROM photo_groups WHERE photo_id='photo1' ORDER BY group_id"
        ).fetchall()
        assert [r["group_id"] for r in rows] == ["group1@N00", "group2@N00"]

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
        db.execute("INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?,?,?,?)",
                   (int(_time.time()), "full", 1, "groups"))
        db.commit()
        api_get.return_value = {"set": [{"id": "album1", "title": "My Album"}], "pool": []}
        result = await mcp_tools._get_photo_contexts({"photo_id": "photo1"})
        data = _json(result)
        assert data["source"] == "local_db"
        assert any(g["id"] == "group1@N00" for g in data["group_pools"])
        assert any(a["id"] == "album1" for a in data["albums"])

    @pytest.mark.asyncio
    async def test_get_photo_contexts_ignores_local_db_for_other_users_photo(self, db, api_get):
        """A photo that isn't in the caller's own library must always go via the
        API, even once groups have been synced — the photo_groups table only
        ever maps the caller's OWN photo ids, so trusting it for someone else's
        photo id would silently report zero group memberships."""
        import mcp_tools
        import time as _time
        db.execute("INSERT INTO sync_log (synced_at, mode, photos_fetched, type) VALUES (?,?,?,?)",
                   (int(_time.time()), "full", 1, "groups"))
        db.commit()
        api_get.return_value = {
            "pool": [{"id": "group1@N00", "title": "Landscape Lovers"}],
            "set":  [],
        }
        result = await mcp_tools._get_photo_contexts({"photo_id": "not_my_photo"})
        data = _json(result)
        assert data["source"] == "flickr_api"
        assert any(g["id"] == "group1@N00" for g in data["group_pools"])

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
        result = await mcp_tools._add_to_group({"photo_id": "photo1", "group_ids": ["group1@N00"]})
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
            {"photo_id": "photo1", "group_ids": ["group1@N00"], "retry_at": "morning"}
        )
        assert "rescheduled" in _text(result).lower()
        count = db.execute(
            "SELECT COUNT(*) FROM pending_group_adds WHERE photo_id='photo1'"
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_add_to_group_reports_non_limit_errors_without_raising(self, db, api_post):
        from flickr_api import FlickrAPIError
        import mcp_tools
        api_post.side_effect = FlickrAPIError(2, "Unknown user")
        result = await mcp_tools._add_to_group({"photo_id": "photo1", "group_ids": ["group1@N00"]})
        text = _text(result)
        assert "NOT added" in text
        assert "Unknown user" in text

    @pytest.mark.asyncio
    async def test_add_to_group_continues_after_one_group_fails(self, db, api_post):
        from flickr_api import FlickrAPIError
        import mcp_tools
        api_post.side_effect = [
            FlickrAPIError(2, "Unknown user"),
            {"stat": "ok"},
        ]
        result = await mcp_tools._add_to_group(
            {"photo_id": "photo1", "group_ids": ["bad_group@N00", "group2@N00"]}
        )
        text = _text(result)
        assert "NOT added" in text and "bad_group@N00" in text
        assert "added to group group2@N00" in text
        row = db.execute(
            "SELECT 1 FROM photo_groups WHERE photo_id='photo1' AND group_id='group2@N00'"
        ).fetchone()
        assert row is not None


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

    @pytest.mark.asyncio
    async def test_add_to_never_follow(self, db):
        import mcp_tools
        result = await mcp_tools._add_to_never_follow({"contact_id": "contact2@N00", "reason": "no thanks"})
        assert "never-follow" in _text(result)
        row = db.execute("SELECT * FROM never_follow WHERE contact_id='contact2@N00'").fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_find_follow_candidates_excludes_existing_contacts(self, db):
        import mcp_tools
        db.execute(
            "INSERT INTO contact_engagement (contact_id, faves, comments, last_updated) VALUES (?, ?, ?, ?)",
            ("contact1@N00", 5, 2, int(time.time())),
        )
        db.commit()
        result = await mcp_tools._find_follow_candidates({})
        text = _text(result)
        assert "contact1@N00" not in text

    @pytest.mark.asyncio
    async def test_find_follow_candidates_excludes_never_follow(self, db):
        import mcp_tools
        db.execute(
            "INSERT INTO contact_engagement (contact_id, faves, comments, last_updated) VALUES (?, ?, ?, ?)",
            ("contact2@N00", 5, 2, int(time.time())),
        )
        db.commit()
        await mcp_tools._add_to_never_follow({"contact_id": "contact2@N00"})
        result = await mcp_tools._find_follow_candidates({})
        text = _text(result)
        assert "contact2@N00" not in text

    @pytest.mark.asyncio
    async def test_find_follow_candidates_returned(self, db):
        import mcp_tools
        db.execute(
            "INSERT INTO contact_engagement (contact_id, faves, comments, last_updated) VALUES (?, ?, ?, ?)",
            ("contact3@N00", 5, 2, int(time.time())),
        )
        db.commit()
        result = await mcp_tools._find_follow_candidates({})
        candidates = _json(result)
        assert any(c["contact_id"] == "contact3@N00" for c in candidates)


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
# set_dates
# ---------------------------------------------------------------------------

class TestSetDates:
    @pytest.mark.asyncio
    async def test_date_taken_only_leaves_posted_alone(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        before = db.execute("SELECT date_uploaded FROM photos WHERE id='photo1'").fetchone()["date_uploaded"]
        await mcp_tools._set_dates({"id": "photo1", "date_taken": "2020-01-02 03:04:05"})
        params = api_post.call_args[0][1]
        assert params["date_taken"] == "2020-01-02 03:04:05"
        assert params["date_taken_granularity"] == "0"
        assert "date_posted" not in params
        row = db.execute("SELECT date_taken, date_uploaded FROM photos WHERE id='photo1'").fetchone()
        assert row["date_taken"] == "2020-01-02 03:04:05"
        assert row["date_uploaded"] == before

    @pytest.mark.asyncio
    async def test_date_posted_only_leaves_taken_alone(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        before = db.execute("SELECT date_taken FROM photos WHERE id='photo1'").fetchone()["date_taken"]
        result = await mcp_tools._set_dates({"id": "photo1", "date_posted": 1600000000})
        params = api_post.call_args[0][1]
        assert params == {"photo_id": "photo1", "date_posted": "1600000000"}
        row = db.execute("SELECT date_taken, date_uploaded FROM photos WHERE id='photo1'").fetchone()
        assert row["date_uploaded"] == 1600000000
        assert row["date_taken"] == before
        assert "2020-09-13" in _text(result)

    @pytest.mark.asyncio
    async def test_both_dates(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        await mcp_tools._set_dates({"id": "photo1", "date_taken": "2020-01-02", "granularity": "month",
                                    "date_posted": "1600000000"})
        params = api_post.call_args[0][1]
        assert params["date_taken_granularity"] == "4"
        assert params["date_posted"] == "1600000000"

    @pytest.mark.asyncio
    async def test_requires_a_date(self, db, api_post):
        import mcp_tools
        result = await mcp_tools._set_dates({"id": "photo1"})
        assert "Error" in _text(result)
        api_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_non_timestamp(self, db, api_post):
        import mcp_tools
        result = await mcp_tools._set_dates({"id": "photo1", "date_posted": "yesterday"})
        assert "Error" in _text(result)
        api_post.assert_not_called()


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
                {"authorname": "alice", "author": "11111111@N00",
                 "datecreate": "1700000000",
                 "_content": "Nice!", "permalink": "https://flickr.com/p/1"},
            ]},
        }
        result = await mcp_tools._get_photo_comments({"photo_id": "photo1"})
        comments = _json(result)
        assert comments[0]["author"] == "alice"
        assert comments[0]["author_nsid"] == "11111111@N00"
        assert comments[0]["author_url"] == "https://www.flickr.com/photos/11111111@N00/"
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


# ---------------------------------------------------------------------------
# _background_refresh threshold
# ---------------------------------------------------------------------------

class TestBackgroundRefreshThreshold:
    def test_threshold_within_bounds(self):
        import random
        from tools.sync import MIN_REFRESH_INTERVAL, REFRESH_INTERVAL
        for last_sync in [0, 1000, 1700000000, 1748000000]:
            threshold = random.Random(int(last_sync)).uniform(MIN_REFRESH_INTERVAL, REFRESH_INTERVAL)
            assert MIN_REFRESH_INTERVAL <= threshold <= REFRESH_INTERVAL

    def test_threshold_stable_for_same_seed(self):
        import random
        from tools.sync import MIN_REFRESH_INTERVAL, REFRESH_INTERVAL
        last_sync = 1748000000
        t1 = random.Random(int(last_sync)).uniform(MIN_REFRESH_INTERVAL, REFRESH_INTERVAL)
        t2 = random.Random(int(last_sync)).uniform(MIN_REFRESH_INTERVAL, REFRESH_INTERVAL)
        assert t1 == t2

    def test_threshold_varies_across_seeds(self):
        import random
        from tools.sync import MIN_REFRESH_INTERVAL, REFRESH_INTERVAL
        thresholds = {
            random.Random(int(last_sync)).uniform(MIN_REFRESH_INTERVAL, REFRESH_INTERVAL)
            for last_sync in range(1748000000, 1748000020)
        }
        assert len(thresholds) > 1


# ---------------------------------------------------------------------------
# get_keeper_list
# ---------------------------------------------------------------------------

class TestKeeperList:
    @pytest.mark.asyncio
    async def test_fallback_url_uses_current_session_nsid_not_hardcoded_username(self, db, creds):
        """A keeper_list entry whose photo isn't in the local photos table
        (LEFT JOIN miss -> no url_photopage) must build its fallback URL from
        the CURRENT session's own nsid, not a hardcoded operator username —
        this server is multi-tenant, so every other user's fallback link
        would otherwise point at the operator's own Flickr account."""
        import mcp_tools

        db.execute(
            "INSERT INTO keeper_list (photo_id, note, added_at) VALUES (?, ?, ?)",
            ("unknownphoto", "note", int(time.time())),
        )
        db.commit()

        result = await mcp_tools._HANDLERS["get_keeper_list"]({})
        items = _json(result)
        entry = next(i for i in items if i["photo_id"] == "unknownphoto")
        assert entry["url"] == f"https://www.flickr.com/photos/{FAKE_CREDS['user_nsid']}/unknownphoto/"
        assert "ejwettstein" not in entry["url"]


# ---------------------------------------------------------------------------
# Scheduled-publish queue
# ---------------------------------------------------------------------------

class TestSchedulePublish:
    @pytest.mark.asyncio
    async def test_schedules_without_calling_flickr(self, db, api_post):
        import mcp_tools
        result = await mcp_tools._schedule_publish({"photo_id": "photo1", "publish_at": "morning", "days_offset": 1})
        assert "scheduled" in _text(result)
        api_post.assert_not_called()
        row = db.execute("SELECT status, publish_after FROM pending_publishes WHERE photo_id='photo1'").fetchone()
        assert row["status"] == "waiting"
        assert row["publish_after"] > time.time()

    @pytest.mark.asyncio
    async def test_reschedules_existing(self, db, api_post):
        import mcp_tools
        await mcp_tools._schedule_publish({"photo_id": "photo1", "publish_at": "morning"})
        result = await mcp_tools._schedule_publish({"photo_id": "photo1", "publish_at": "evening", "days_offset": 3})
        assert "rescheduled" in _text(result)
        count = db.execute("SELECT COUNT(*) FROM pending_publishes WHERE photo_id='photo1'").fetchone()[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_rejects_unrecognised_time(self, db, api_post):
        import mcp_tools
        result = await mcp_tools._schedule_publish({"photo_id": "photo1", "publish_at": "next tuesday"})
        assert "Error" in _text(result)
        assert db.execute("SELECT COUNT(*) FROM pending_publishes").fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_cancel(self, db, api_post):
        import mcp_tools
        await mcp_tools._schedule_publish({"photo_id": "photo1"})
        result = await mcp_tools._cancel_scheduled_publish({"photo_id": "photo1"})
        assert "no longer scheduled" in _text(result)
        assert db.execute("SELECT COUNT(*) FROM pending_publishes").fetchone()[0] == 0


class TestFlushPublishQueue:
    def _conn(self, publish_after_delta: int):
        conn = make_db()
        now = int(time.time())
        conn.execute(
            "INSERT INTO photos (id, title, is_public, date_uploaded) VALUES ('photo1', 'T', 0, 1000)"
        )
        conn.execute(
            "INSERT INTO pending_publishes (photo_id, status, publish_after, queued_at) VALUES (?, 'waiting', ?, ?)",
            ("photo1", now + publish_after_delta, now - 3600),
        )
        conn.commit()
        return conn

    def test_due_item_resets_date_then_publishes(self):
        from tools.publish_queue import _flush_publish_queue, _DATE_POSTED_LEAD_SECONDS
        conn = self._conn(-60)
        with patch("flickr_api._api_post", return_value={"stat": "ok"}) as mock_post:
            before = int(time.time())
            flushed = _flush_publish_queue(conn)
        assert flushed == [{"photo_id": "photo1", "result": "success"}]
        methods = [c.args[0] for c in mock_post.call_args_list]
        assert methods == ["flickr.photos.setDates", "flickr.photos.setPerms"]
        posted = int(mock_post.call_args_list[0].args[1]["date_posted"])
        assert before - _DATE_POSTED_LEAD_SECONDS - 1 <= posted <= int(time.time()) - _DATE_POSTED_LEAD_SECONDS
        assert mock_post.call_args_list[1].args[1]["is_public"] == "1"
        photo = conn.execute("SELECT is_public, date_uploaded FROM photos").fetchone()
        assert photo["is_public"] == 1
        assert photo["date_uploaded"] == posted
        assert conn.execute("SELECT status FROM pending_publishes").fetchone()["status"] == "success"

    def test_future_item_is_skipped(self):
        from tools.publish_queue import _flush_publish_queue
        conn = self._conn(3600)
        with patch("flickr_api._api_post") as mock_post:
            assert _flush_publish_queue(conn) == []
        mock_post.assert_not_called()

    def test_force_publishes_future_items(self):
        from tools.publish_queue import _flush_publish_queue
        conn = self._conn(3600)
        with patch("flickr_api._api_post", return_value={"stat": "ok"}):
            flushed = _flush_publish_queue(conn, force=True)
        assert flushed[0]["result"] == "success"

    def test_flickr_rejection_of_publish_marks_error(self):
        from flickr_api import FlickrAPIError
        from tools.publish_queue import _flush_publish_queue
        conn = self._conn(-60)
        with patch("flickr_api._api_post", side_effect=[{"stat": "ok"}, FlickrAPIError(1, "Photo not found")]):
            flushed = _flush_publish_queue(conn)
        assert "error" in flushed[0]["result"]
        row = conn.execute("SELECT status, error_msg FROM pending_publishes").fetchone()
        assert row["status"] == "error" and row["error_msg"] == "Photo not found"

    def test_transient_failure_stays_waiting(self):
        from tools.publish_queue import _flush_publish_queue
        conn = self._conn(-60)
        with patch("flickr_api._api_post", side_effect=RuntimeError("Flickr API timed out")):
            flushed = _flush_publish_queue(conn)
        assert flushed[0]["result"].startswith("retry_later")
        assert conn.execute("SELECT status FROM pending_publishes").fetchone()["status"] == "waiting"
        assert conn.execute("SELECT is_public FROM photos").fetchone()["is_public"] == 0

    def test_date_reset_rejection_still_publishes_with_note(self):
        from flickr_api import FlickrAPIError
        from tools.publish_queue import _flush_publish_queue
        conn = self._conn(-60)
        with patch("flickr_api._api_post", side_effect=[FlickrAPIError(3, "Invalid date"), {"stat": "ok"}]):
            flushed = _flush_publish_queue(conn)
        assert flushed[0]["result"].startswith("success")
        row = conn.execute("SELECT status, error_msg FROM pending_publishes").fetchone()
        assert row["status"] == "success" and "Invalid date" in row["error_msg"]
        assert conn.execute("SELECT is_public FROM photos").fetchone()["is_public"] == 1


class TestPublishTimes:
    def test_absolute_date_time_is_chicago(self):
        import datetime
        from zoneinfo import ZoneInfo
        from tools.publish_queue import _resolve_publish_time
        year = datetime.date.today().year + 1
        ts = _resolve_publish_time(f"{year}-03-15 08:30")
        dt = datetime.datetime.fromtimestamp(ts, ZoneInfo("America/Chicago"))
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (year, 3, 15, 8, 30)

    def test_date_only_defaults_to_5pm(self):
        import datetime
        from zoneinfo import ZoneInfo
        from tools.publish_queue import _resolve_publish_time
        year = datetime.date.today().year + 1
        dt = datetime.datetime.fromtimestamp(_resolve_publish_time(f"{year}-03-15"), ZoneInfo("America/Chicago"))
        assert dt.hour == 17

    def test_iso_with_offset_is_honoured(self):
        import datetime
        from tools.publish_queue import _resolve_publish_time
        year = datetime.date.today().year + 1
        ts = _resolve_publish_time(f"{year}-03-15T13:00:00Z")
        assert ts == int(datetime.datetime(year, 3, 15, 13, tzinfo=datetime.timezone.utc).timestamp())

    def test_past_is_rejected(self):
        import pytest as _pytest
        from tools.publish_queue import PublishTimeError, _resolve_publish_time
        with _pytest.raises(PublishTimeError, match="past"):
            _resolve_publish_time("2020-01-01 08:00")

    def test_days_offset_with_full_date_is_rejected(self):
        import datetime
        import pytest as _pytest
        from tools.publish_queue import PublishTimeError, _resolve_publish_time
        with _pytest.raises(PublishTimeError):
            _resolve_publish_time(f"{datetime.date.today().year + 1}-03-15", days_offset=2)

    def test_fmt_relative(self):
        from tools.publish_queue import _fmt_relative
        assert _fmt_relative(1000 + 2 * 86400, now=1000) == "in 2 days"
        assert _fmt_relative(1000 - 3600, now=1000) == "1 hour ago"


class TestPublishQueueManagement:
    @pytest.mark.asyncio
    async def test_set_visibility_public_cancels_schedule(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        await mcp_tools._schedule_publish({"photo_id": "photo1", "publish_at": "morning"})
        result = await mcp_tools._set_visibility({"id": "photo1", "is_public": True})
        assert "cancelled" in _text(result)
        assert db.execute("SELECT COUNT(*) FROM pending_publishes").fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_set_visibility_private_keeps_schedule(self, db, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        await mcp_tools._schedule_publish({"photo_id": "photo1", "publish_at": "morning"})
        await mcp_tools._set_visibility({"id": "photo1", "is_public": False})
        assert db.execute("SELECT COUNT(*) FROM pending_publishes WHERE status='waiting'").fetchone()[0] == 1

    @pytest.mark.asyncio
    async def test_rescheduling_a_failed_photo_replaces_the_error(self, db, api_post):
        import mcp_tools
        now = int(time.time())
        db.execute(
            "INSERT INTO pending_publishes (photo_id, status, publish_after, queued_at, completed_at, error_msg) "
            "VALUES ('photo1', 'error', ?, ?, ?, 'boom')", (now - 60, now - 3600, now - 60),
        )
        db.commit()
        result = await mcp_tools._schedule_publish({"photo_id": "photo1", "publish_at": "evening"})
        assert "scheduled" in _text(result)
        statuses = [r[0] for r in db.execute("SELECT status FROM pending_publishes").fetchall()]
        assert statuses == ["waiting"]

    def _conn_with(self, status="waiting", delta=3600):
        conn = make_db()
        now = int(time.time())
        conn.execute("INSERT INTO photos (id, title, is_public) VALUES ('photo1', 'T', 0)")
        conn.execute(
            "INSERT INTO pending_publishes (photo_id, status, publish_after, queued_at, error_msg) "
            "VALUES ('photo1', ?, ?, ?, ?)", (status, now + delta, now, "boom" if status == "error" else None),
        )
        conn.commit()
        return conn

    def test_web_reschedule(self):
        import datetime
        from tools.publish_queue import _web_action
        conn = self._conn_with()
        target = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
        body, status = _web_action(conn, {"action": "publish_reschedule", "item_id": 1,
                                          "publish_at": target.isoformat()})
        assert status == 200, body
        row = conn.execute("SELECT publish_after FROM pending_publishes").fetchone()
        assert abs(row["publish_after"] - target.timestamp()) < 2

    def test_web_reschedule_rejects_past(self):
        from tools.publish_queue import _web_action
        conn = self._conn_with()
        body, status = _web_action(conn, {"action": "publish_reschedule", "item_id": 1,
                                          "publish_at": "2020-01-01T00:00:00Z"})
        assert status == 400 and "past" in body["error"]

    def test_web_publish_one_now_ignores_schedule(self):
        from tools.publish_queue import _web_action
        conn = self._conn_with(delta=86400)
        with patch("flickr_api._api_post", return_value={"stat": "ok"}):
            body, status = _web_action(conn, {"action": "publish_now", "item_id": 1})
        assert status == 200 and "1 published" in body["message"]
        assert conn.execute("SELECT status FROM pending_publishes").fetchone()["status"] == "success"

    def test_web_retry_failed_item(self):
        from tools.publish_queue import _web_action
        conn = self._conn_with(status="error", delta=-60)
        with patch("flickr_api._api_post", return_value={"stat": "ok"}):
            body, status = _web_action(conn, {"action": "publish_now", "item_id": 1})
        assert status == 200
        assert conn.execute("SELECT status FROM pending_publishes").fetchone()["status"] == "success"

    def test_web_remove_and_missing_item(self):
        from tools.publish_queue import _web_action
        conn = self._conn_with()
        assert _web_action(conn, {"action": "publish_remove", "item_id": 1})[1] == 200
        assert _web_action(conn, {"action": "publish_remove", "item_id": 1})[1] == 404

    def test_web_schedule_returns_entry_for_viewer(self):
        import datetime
        from tools.publish_queue import _web_action
        conn = make_db()
        target = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)).isoformat()
        body, status = _web_action(conn, {"action": "publish_schedule", "photo_id": "p9", "publish_at": target})
        assert status == 200
        assert body["scheduled"]["id"] == 1


class TestGroupQueueWeb:
    def _conn(self, status="waiting", delta=3600):
        conn = make_db()
        now = int(time.time())
        conn.execute("INSERT INTO photos (id, title, url_medium) VALUES ('photo1', 'T', 'https://x/photo1_z.jpg')")
        conn.execute("INSERT INTO groups (id, name) VALUES ('group1@N00', 'Landscape Lovers')")
        conn.execute(
            "INSERT INTO pending_group_adds (photo_id, group_id, status, retry_after, queued_at, error_msg, completed_at) "
            "VALUES ('photo1', 'group1@N00', ?, ?, ?, ?, ?)",
            (status, now + delta, now - 60, "boom" if status == "error" else None, now if status == "error" else None),
        )
        conn.commit()
        return conn

    def test_snapshot_has_thumbnail_group_and_time(self):
        from tools.queue_web import group_snapshot
        snap = group_snapshot(self._conn())
        item = snap["waiting"][0]
        assert snap["counts"]["waiting"] == 1
        assert item["thumb_url"] == "https://x/photo1_z.jpg"
        assert item["group_name"] == "Landscape Lovers"
        assert item["when_ts"] > time.time()

    def test_post_one_now_ignores_schedule(self):
        from tools.queue_web import web_action
        conn = self._conn(delta=86400)
        with patch("flickr_api._api_post", return_value={"stat": "ok"}):
            body, status = web_action(conn, {"action": "group_post_now", "item_id": 1})
        assert status == 200 and "1 added" in body["message"]
        assert conn.execute("SELECT status FROM pending_group_adds").fetchone()["status"] == "success"

    def test_retry_failed_item(self):
        from tools.queue_web import web_action
        conn = self._conn(status="error")
        with patch("flickr_api._api_post", return_value={"stat": "ok"}):
            body, status = web_action(conn, {"action": "group_post_now", "item_id": 1})
        assert status == 200
        assert conn.execute("SELECT status FROM pending_group_adds").fetchone()["status"] == "success"

    def test_post_due_skips_future(self):
        from tools.queue_web import web_action
        conn = self._conn(delta=3600)
        with patch("flickr_api._api_post") as mock_post:
            body, status = web_action(conn, {"action": "group_post_due"})
        mock_post.assert_not_called()
        assert body["message"] == "Nothing was due."

    def test_reschedule_failed_item_requeues_it(self):
        import datetime
        from tools.queue_web import web_action
        conn = self._conn(status="error")
        target = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        body, status = web_action(conn, {"action": "group_reschedule", "item_id": 1, "publish_at": target.isoformat()})
        assert status == 200
        row = conn.execute("SELECT status, retry_after, error_msg FROM pending_group_adds").fetchone()
        assert row["status"] == "waiting" and row["error_msg"] is None
        assert abs(row["retry_after"] - target.timestamp()) < 2

    def test_remove_and_unknown_action(self):
        from tools.queue_web import web_action
        conn = self._conn()
        assert web_action(conn, {"action": "group_remove", "item_id": 1})[1] == 200
        assert conn.execute("SELECT COUNT(*) FROM pending_group_adds").fetchone()[0] == 0
        assert web_action(conn, {"action": "group_explode"})[1] == 400
        assert web_action(conn, {"action": "nonsense"})[1] == 400
