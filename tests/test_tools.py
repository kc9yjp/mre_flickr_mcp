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
# batch_update_photos
# ---------------------------------------------------------------------------

class TestBatchUpdatePhotos:
    @pytest.fixture()
    def two_photos(self, db):
        now = int(time.time())
        db.execute(
            "INSERT INTO photos (id, title, description, tags, date_taken, "
            "date_uploaded, synced_at, is_public) VALUES (?,?,?,?,?,?,?,1)",
            ("photo2", "Second", "desc two", "old", "2024-02-01 10:00:00", now, now),
        )
        db.commit()
        return db

    @pytest.mark.asyncio
    async def test_updates_every_photo_in_one_call(self, two_photos, api_post):
        import mcp_tools
        api_post.return_value = {"stat": "ok"}
        result = await mcp_tools._batch_update_photos({"photos": [
            {"id": "photo1", "title": "One", "description": "d1", "tags": "a b"},
            {"id": "photo2", "title": "Two", "description": "d2", "tags": "c d"},
        ]})
        payload = _json(result)
        assert payload["succeeded"] == 2 and payload["failed"] == 0
        assert [r["status"] for r in payload["results"]] == ["ok", "ok"]
        rows = {r["id"]: r for r in two_photos.execute(
            "SELECT id, title, description, tags FROM photos WHERE id IN ('photo1','photo2')"
        ).fetchall()}
        assert rows["photo1"]["title"] == "One" and rows["photo1"]["tags"] == "a b"
        assert rows["photo2"]["description"] == "d2"

    @pytest.mark.asyncio
    async def test_one_failure_does_not_abort_the_batch(self, two_photos, api_post):
        """A rejected photo is reported against that photo only — the rest of
        the batch still gets written, so a partial batch is never silently lost."""
        import mcp_tools
        from flickr_api import FlickrAPIError

        def _post(method, params=None):
            if params and params.get("photo_id") == "photo1":
                raise FlickrAPIError(1, "Photo not found")
            return {"stat": "ok"}
        api_post.side_effect = _post

        result = await mcp_tools._batch_update_photos({"photos": [
            {"id": "photo1", "title": "One", "description": "d1", "tags": "a"},
            {"id": "photo2", "title": "Two", "description": "d2", "tags": "b"},
        ]})
        payload = _json(result)
        assert payload["succeeded"] == 1 and payload["failed"] == 1
        first, second = payload["results"]
        assert first["status"] == "error" and "Photo not found" in first["error"]
        assert second["status"] == "ok"
        row = two_photos.execute("SELECT title FROM photos WHERE id='photo2'").fetchone()
        assert row["title"] == "Two"

    @pytest.mark.asyncio
    async def test_stop_on_error_halts_remaining_photos(self, two_photos, api_post):
        import mcp_tools
        from flickr_api import FlickrAPIError
        api_post.side_effect = FlickrAPIError(1, "Photo not found")

        result = await mcp_tools._batch_update_photos({
            "photos": [
                {"id": "photo1", "title": "One", "description": "d", "tags": "a"},
                {"id": "photo2", "title": "Two", "description": "d", "tags": "b"},
            ],
            "stop_on_error": True,
        })
        payload = _json(result)
        assert payload["stopped_early"] is True
        assert len(payload["results"]) == 1

    @pytest.mark.asyncio
    async def test_rejects_oversized_batch(self, db):
        import mcp_tools
        from tools.photos import MAX_BATCH_UPDATE
        photos = [{"id": str(i), "title": "t"} for i in range(MAX_BATCH_UPDATE + 1)]
        result = await mcp_tools._batch_update_photos({"photos": photos})
        assert "too many photos" in _text(result)

    @pytest.mark.asyncio
    async def test_entry_with_no_fields_is_reported_not_written(self, db, api_post):
        import mcp_tools
        result = await mcp_tools._batch_update_photos({"photos": [{"id": "photo1"}]})
        payload = _json(result)
        assert payload["results"][0]["status"] == "skipped"
        api_post.assert_not_called()


# ---------------------------------------------------------------------------
# replace_photo
# ---------------------------------------------------------------------------

class TestReplacePhoto:
    @pytest.mark.asyncio
    async def test_replaces_from_file_and_refreshes_cached_urls(self, db, tmp_path):
        import mcp_tools
        image = tmp_path / "enhanced.jpg"
        image.write_bytes(b"\xff\xd8fake-jpeg-bytes")

        with (
            patch("flickr_api._api_replace", return_value="photo1") as replace,
            patch("tools.photos._live_photo_urls",
                  return_value=("https://live.staticflickr.com/x/new_o.jpg",
                                "https://live.staticflickr.com/x/new_z.jpg")),
        ):
            result = await mcp_tools._replace_photo({"photo_id": "photo1", "file_path": str(image)})

        payload = _json(result)
        assert payload["status"] == "replaced"
        assert payload["photo_id"] == "photo1"
        replace.assert_called_once()
        assert replace.call_args.args[1] == b"\xff\xd8fake-jpeg-bytes"
        row = db.execute("SELECT url_original, url_medium FROM photos WHERE id='photo1'").fetchone()
        assert row["url_original"] == "https://live.staticflickr.com/x/new_o.jpg"
        assert row["url_medium"] == "https://live.staticflickr.com/x/new_z.jpg"

    @pytest.mark.asyncio
    async def test_requires_exactly_one_source(self, db):
        import mcp_tools
        neither = await mcp_tools._replace_photo({"photo_id": "photo1"})
        assert "exactly one" in _text(neither)
        both = await mcp_tools._replace_photo(
            {"photo_id": "photo1", "file_path": "/tmp/x.jpg", "image_url": "https://e/x.jpg"}
        )
        assert "exactly one" in _text(both)

    @pytest.mark.asyncio
    async def test_missing_file_is_reported_not_raised(self, db):
        import mcp_tools
        result = await mcp_tools._replace_photo({"photo_id": "photo1", "file_path": "/nope/missing.jpg"})
        assert "File not found" in _text(result)

    @pytest.mark.asyncio
    async def test_flickr_rejection_is_reported_and_urls_untouched(self, db, tmp_path):
        import mcp_tools
        from flickr_api import FlickrAPIError
        image = tmp_path / "x.jpg"
        image.write_bytes(b"bytes")
        before = db.execute("SELECT url_original FROM photos WHERE id='photo1'").fetchone()["url_original"]

        with patch("flickr_api._api_replace", side_effect=FlickrAPIError(3, "Filetype was not recognised")):
            result = await mcp_tools._replace_photo({"photo_id": "photo1", "file_path": str(image)})

        assert "Filetype was not recognised" in _text(result)
        after = db.execute("SELECT url_original FROM photos WHERE id='photo1'").fetchone()["url_original"]
        assert after == before

    @pytest.mark.asyncio
    async def test_rejects_non_http_url(self, db):
        import mcp_tools
        result = await mcp_tools._replace_photo({"photo_id": "photo1", "image_url": "file:///etc/passwd"})
        assert "http(s) URL" in _text(result)


# ---------------------------------------------------------------------------
# search_photos include_images
# ---------------------------------------------------------------------------

class TestSearchWithImages:
    @pytest.mark.asyncio
    async def test_images_are_returned_alongside_metadata(self, db):
        import mcp_tools
        from mcp.types import ImageContent

        fake = ImageContent(type="image", data="Zm9v", mimeType="image/jpeg")
        with patch("tools.photos._photo_thumbnails", return_value={"photo1": fake}) as thumbs:
            result = await mcp_tools._search_photos({"include_images": True})

        assert json.loads(result[0].text)[0]["id"] == "photo1"
        assert any(isinstance(c, ImageContent) for c in result)
        thumbs.assert_called_once_with(["photo1"])

    @pytest.mark.asyncio
    async def test_no_images_unless_requested(self, db):
        import mcp_tools
        with patch("tools.photos._photo_thumbnails") as thumbs:
            result = await mcp_tools._search_photos({})
        thumbs.assert_not_called()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_unfetchable_image_degrades_to_a_text_line(self, db):
        """One photo whose bytes can't be fetched must not fail the search."""
        import mcp_tools
        with patch("tools.photos._photo_thumbnails", return_value={"photo1": None}):
            result = await mcp_tools._search_photos({"include_images": True})
        assert "image unavailable" in result[1].text


# ---------------------------------------------------------------------------
# get_person_info friend/family flags
# ---------------------------------------------------------------------------

class TestPersonInfoContactFlags:
    UNRELIABLE_PERSON = {
        "person": {
            "nsid": "contact1@N00",
            "username": {"_content": "jsmith"},
            # people.getInfo claims both flags for essentially every contact.
            "friend": 1, "family": 1, "contact": 1,
        }
    }

    @pytest.mark.asyncio
    async def test_flags_come_from_synced_contacts_not_people_getinfo(self, db, creds):
        import mcp_tools
        db.execute("INSERT INTO sync_log (synced_at, mode, photos_fetched, type) "
                   "VALUES (?, 'full', 1, 'contacts')", (int(time.time()),))
        db.execute("UPDATE contacts SET is_friend=1, is_family=0 WHERE id='contact1@N00'")
        db.commit()

        with (
            patch("flickr_api.resolve_user_id", return_value="contact1@N00"),
            patch("flickr_api._api_get", return_value=self.UNRELIABLE_PERSON),
        ):
            result = await mcp_tools._get_person_info({"user_id": "contact1@N00"})
        info = _json(result)
        assert info["is_friend"] is True
        assert info["is_family"] is False
        assert "local contacts cache" in info["flags_source"]

    @pytest.mark.asyncio
    async def test_non_contact_after_sync_is_neither_friend_nor_family(self, db, creds):
        import mcp_tools
        db.execute("INSERT INTO sync_log (synced_at, mode, photos_fetched, type) "
                   "VALUES (?, 'full', 1, 'contacts')", (int(time.time()),))
        db.commit()
        person = {"person": dict(self.UNRELIABLE_PERSON["person"], nsid="stranger@N00")}

        with (
            patch("flickr_api.resolve_user_id", return_value="stranger@N00"),
            patch("flickr_api._api_get", return_value=person),
        ):
            result = await mcp_tools._get_person_info({"user_id": "stranger@N00"})
        info = _json(result)
        assert info["is_friend"] is False and info["is_family"] is False
        assert "not a contact" in info["flags_source"]

    @pytest.mark.asyncio
    async def test_without_a_contacts_sync_the_api_values_are_labelled_unreliable(self, db, creds):
        """No contacts sync and no cached row: there's nothing to correct the
        API's flags with, so they're passed through but marked unreliable
        rather than presented as fact."""
        import mcp_tools
        person = {"person": dict(self.UNRELIABLE_PERSON["person"], nsid="stranger@N00")}
        with (
            patch("flickr_api.resolve_user_id", return_value="stranger@N00"),
            patch("flickr_api._api_get", return_value=person),
        ):
            result = await mcp_tools._get_person_info({"user_id": "stranger@N00"})
        info = _json(result)
        assert "UNRELIABLE" in info["flags_source"]
        # The unreliable source still reports what Flickr said, just labelled.
        assert info["is_friend"] is True and info["is_family"] is True


# ---------------------------------------------------------------------------
# add_to_group skip_existing
# ---------------------------------------------------------------------------

class TestAddToGroupSkipExisting:
    CONTEXTS = {"pool": [{"id": "group1@N00", "title": "Landscape Lovers"}]}

    @pytest.mark.asyncio
    async def test_skips_groups_the_photo_is_already_in(self, db, api_post):
        import mcp_tools
        with patch("flickr_api._api_get", return_value=self.CONTEXTS):
            result = await mcp_tools._add_to_group(
                {"photo_id": "photo1", "group_ids": ["group1@N00", "group2@N00"]}
            )
        text = _text(result)
        assert "already in group group1@N00" in text
        assert "added to group group2@N00" in text
        posted = [c.args[1]["group_id"] for c in api_post.call_args_list]
        assert posted == ["group2@N00"]
        # The skipped group is still recorded locally — the photo really is in it.
        assert db.execute(
            "SELECT COUNT(*) FROM photo_groups WHERE photo_id='photo1'"
        ).fetchone()[0] == 2

    @pytest.mark.asyncio
    async def test_skip_existing_false_attempts_every_add(self, db, api_post):
        import mcp_tools
        with patch("flickr_api._api_get", return_value=self.CONTEXTS) as api_get:
            await mcp_tools._add_to_group(
                {"photo_id": "photo1", "group_ids": ["group1@N00"], "skip_existing": False}
            )
        api_get.assert_not_called()
        assert api_post.call_count == 1

    @pytest.mark.asyncio
    async def test_failed_context_lookup_still_attempts_the_add(self, db, api_post):
        """A context lookup that errors must never silently drop a requested add."""
        import mcp_tools
        with patch("flickr_api._api_get", side_effect=RuntimeError("Flickr API timed out")):
            result = await mcp_tools._add_to_group({"photo_id": "photo1", "group_ids": ["group1@N00"]})
        assert "added to group group1@N00" in _text(result)
        assert api_post.call_count == 1
