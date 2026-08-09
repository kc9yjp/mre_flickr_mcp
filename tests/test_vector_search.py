"""Tests for the optional group vector-search feature.

Two halves: the feature *off* (the default — must behave exactly as before,
with no Chroma or embedding backend involved at all) and the feature *on*
(sync embeds changed groups, search merges semantic hits into find_groups).
No real Chroma, LM Studio, or Flickr API is used anywhere here.
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import vector_search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(result) -> str:
    return result[0].text


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Keep every test independent of the developer's real .env / environment."""
    monkeypatch.setattr(vector_search, "ENV_FILE", str(tmp_path / "nonexistent.env"))
    monkeypatch.setattr(vector_search, "_DATA_DIR", str(tmp_path / "data"))
    for name in ("WORKBENCH_VECTOR_SEARCH_ENABLED", "WORKBENCH_EMBEDDING_BASE_URL",
                 "WORKBENCH_EMBEDDING_MODEL", "WORKBENCH_EMBEDDING_API_KEY",
                 "WORKBENCH_CHROMA_DIR", "WORKBENCH_CHROMA_HOST", "WORKBENCH_CHROMA_PORT",
                 "WORKBENCH_VECTOR_MAX_DISTANCE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def enabled_env(monkeypatch):
    monkeypatch.setenv("WORKBENCH_VECTOR_SEARCH_ENABLED", "true")
    monkeypatch.setenv("WORKBENCH_EMBEDDING_BASE_URL", "http://lmstudio.test/v1")
    monkeypatch.setenv("WORKBENCH_EMBEDDING_MODEL", "test-embed")


class FakeCollection:
    """Minimal stand-in for a Chroma collection."""

    def __init__(self):
        self.records: dict[str, dict] = {}
        self.query_calls: list[dict] = []
        self.next_query_ids: list[str] = []
        self.next_query_distances: list[float] | None = None

    def get(self, include=None):
        ids = list(self.records)
        return {"ids": ids, "metadatas": [self.records[i]["metadata"] for i in ids]}

    def upsert(self, ids, embeddings, documents, metadatas):
        for i, gid in enumerate(ids):
            self.records[gid] = {
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            }

    def delete(self, ids):
        for gid in ids:
            self.records.pop(gid, None)

    def query(self, query_embeddings, n_results):
        self.query_calls.append({"embeddings": query_embeddings, "n_results": n_results})
        ids = self.next_query_ids[:n_results]
        if self.next_query_distances is not None:
            distances = self.next_query_distances[:n_results]
        else:
            distances = [0.1 * (i + 1) for i in range(len(ids))]
        return {"ids": [ids], "distances": [distances]}


def _groups_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE groups (
            id TEXT PRIMARY KEY, name TEXT, members INTEGER, pool_count INTEGER,
            synced_at INTEGER, description TEXT, user_note TEXT,
            needs_summary INTEGER DEFAULT 0, summary_md TEXT,
            is_milestone INTEGER DEFAULT 0, fave_min INTEGER, view_min INTEGER,
            open_subject INTEGER, ai_keywords TEXT, summary_generated_at INTEGER
        )
    """)
    return conn


def _insert_group(conn, gid, name, description="", summary_md="", ai_keywords="", user_note=""):
    conn.execute(
        "INSERT INTO groups (id, name, members, pool_count, synced_at, description, "
        "user_note, summary_md, ai_keywords) VALUES (?, ?, 10, 5, 0, ?, ?, ?, ?)",
        (gid, name, description, user_note, summary_md, ai_keywords),
    )
    conn.commit()


def _fake_embed(texts, cfg):
    """Deterministic stand-in for the embeddings endpoint."""
    return [[float(len(t)), 1.0, 0.0] for t in texts]


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFlag:
    def test_off_by_default(self):
        assert vector_search.enabled() is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("WORKBENCH_VECTOR_SEARCH_ENABLED", value)
        assert vector_search.enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", ""])
    def test_falsy_values_leave_it_off(self, monkeypatch, value):
        monkeypatch.setenv("WORKBENCH_VECTOR_SEARCH_ENABLED", value)
        assert vector_search.enabled() is False

    def test_env_file_fallback(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nWORKBENCH_VECTOR_SEARCH_ENABLED=true\n")
        monkeypatch.setattr(vector_search, "ENV_FILE", str(env_file))
        assert vector_search.enabled() is True


# ---------------------------------------------------------------------------
# Embedding text / fingerprints
# ---------------------------------------------------------------------------

class TestEmbeddingText:
    def test_combines_all_group_text_and_flattens_html(self):
        row = {
            "id": "1@N", "name": "Fleeting Light",
            "description": "<p>Golden hour &amp; dusk.</p>",
            "summary_md": "Warm evening light.",
            "ai_keywords": "sunset dusk",
            "user_note": "one a day",
        }
        text = vector_search.group_embedding_text(row)
        assert "Fleeting Light" in text
        assert "Golden hour & dusk." in text
        assert "<p>" not in text
        assert "Warm evening light." in text
        assert "sunset dusk" in text
        assert "one a day" in text

    def test_missing_and_null_fields_are_skipped(self):
        row = {"id": "1@N", "name": "Just A Name", "description": None}
        assert vector_search.group_embedding_text(row) == "Just A Name"

    def test_fingerprint_changes_with_text_and_model(self):
        a = vector_search.embedding_fingerprint("hello", "model-a")
        assert a == vector_search.embedding_fingerprint("hello", "model-a")
        assert a != vector_search.embedding_fingerprint("hello!", "model-a")
        assert a != vector_search.embedding_fingerprint("hello", "model-b")


class TestCollectionNaming:
    def test_per_user_names_are_distinct_and_chroma_legal(self):
        import re
        a = vector_search.collection_name("jdoe")
        b = vector_search.collection_name("jsmith")
        assert a != b
        for name in (a, b, vector_search.collection_name(None)):
            assert 3 <= len(name) <= 63
            assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]", name)

    def test_awkward_username_is_sanitized(self):
        name = vector_search.collection_name("Jane Doe / 42!")
        assert " " not in name and "/" not in name


# ---------------------------------------------------------------------------
# embed_texts
# ---------------------------------------------------------------------------

class TestEmbedTexts:
    def _client(self, response):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = response
        return client

    def _response(self, status=200, payload=None, text=""):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload or {}
        resp.text = text
        return resp

    def test_posts_to_embeddings_endpoint(self, monkeypatch):
        resp = self._response(payload={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})
        client = self._client(resp)
        monkeypatch.setattr(vector_search.httpx, "Client", lambda **kw: client)
        cfg = {"base_url": "http://lmstudio.test/v1/", "model": "m", "api_key": "k"}

        assert vector_search.embed_texts(["hi"], cfg) == [[1.0, 2.0]]
        url, = client.post.call_args[0]
        assert url == "http://lmstudio.test/v1/embeddings"
        assert client.post.call_args.kwargs["json"] == {"model": "m", "input": ["hi"]}
        assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer k"

    def test_out_of_order_results_are_realigned(self, monkeypatch):
        resp = self._response(payload={"data": [
            {"index": 1, "embedding": [2.0]},
            {"index": 0, "embedding": [1.0]},
        ]})
        monkeypatch.setattr(vector_search.httpx, "Client", lambda **kw: self._client(resp))
        cfg = {"base_url": "http://x/v1", "model": "m"}
        assert vector_search.embed_texts(["a", "b"], cfg) == [[1.0], [2.0]]

    def test_error_status_raises(self, monkeypatch):
        resp = self._response(status=404, text="no such model")
        monkeypatch.setattr(vector_search.httpx, "Client", lambda **kw: self._client(resp))
        with pytest.raises(vector_search.VectorSearchError, match="404"):
            vector_search.embed_texts(["a"], {"base_url": "http://x/v1", "model": "m"})

    def test_count_mismatch_raises(self, monkeypatch):
        resp = self._response(payload={"data": [{"index": 0, "embedding": [1.0]}]})
        monkeypatch.setattr(vector_search.httpx, "Client", lambda **kw: self._client(resp))
        with pytest.raises(vector_search.VectorSearchError):
            vector_search.embed_texts(["a", "b"], {"base_url": "http://x/v1", "model": "m"})

    def test_unconfigured_endpoint_raises(self):
        with pytest.raises(vector_search.VectorSearchError, match="no embedding endpoint"):
            vector_search.embed_texts(["a"], {"base_url": "", "model": ""})

    def test_empty_input_short_circuits(self):
        assert vector_search.embed_texts([], {"base_url": "", "model": ""}) == []


# ---------------------------------------------------------------------------
# Sync phase
# ---------------------------------------------------------------------------

class TestSyncGroupVectors:
    def test_no_op_when_disabled(self, monkeypatch):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics")
        get_collection = MagicMock()
        monkeypatch.setattr(vector_search, "get_collection", get_collection)

        stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        assert stats == {"total": 0, "embedded": 0, "unchanged": 0, "deleted": 0, "failed": 0}
        get_collection.assert_not_called()

    def test_embeds_all_groups_on_first_run(self, monkeypatch, enabled_env):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics", description="Trees")
        _insert_group(conn, "2@N", "Fleeting Light", description="Golden hour")
        collection = FakeCollection()
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)
        monkeypatch.setattr(vector_search, "embed_texts", _fake_embed)

        stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        assert stats["embedded"] == 2
        assert stats["unchanged"] == 0
        assert set(collection.records) == {"1@N", "2@N"}
        assert collection.records["2@N"]["metadata"]["name"] == "Fleeting Light"

    def test_second_run_skips_unchanged_groups(self, monkeypatch, enabled_env):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics", description="Trees")
        _insert_group(conn, "2@N", "Fleeting Light", description="Golden hour")
        collection = FakeCollection()
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)
        monkeypatch.setattr(vector_search, "embed_texts", _fake_embed)
        vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        conn.execute("UPDATE groups SET description='Golden hour and dusk' WHERE id='2@N'")
        conn.commit()
        embedded_texts = []

        def _tracking_embed(texts, cfg):
            embedded_texts.extend(texts)
            return _fake_embed(texts, cfg)

        monkeypatch.setattr(vector_search, "embed_texts", _tracking_embed)
        stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        assert stats == {"total": 2, "embedded": 1, "unchanged": 1, "deleted": 0, "failed": 0}
        assert len(embedded_texts) == 1
        assert "Golden hour and dusk" in embedded_texts[0]

    def test_rebuild_re_embeds_everything(self, monkeypatch, enabled_env):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics", description="Trees")
        collection = FakeCollection()
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)
        monkeypatch.setattr(vector_search, "embed_texts", _fake_embed)
        vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe", rebuild=True)

        assert stats["embedded"] == 1
        assert stats["unchanged"] == 0

    def test_left_groups_are_dropped_from_the_store(self, monkeypatch, enabled_env):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics")
        _insert_group(conn, "2@N", "Fleeting Light")
        collection = FakeCollection()
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)
        monkeypatch.setattr(vector_search, "embed_texts", _fake_embed)
        vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        conn.execute("DELETE FROM groups WHERE id='2@N'")
        conn.commit()
        stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        assert stats["deleted"] == 1
        assert set(collection.records) == {"1@N"}

    def test_embedding_failure_is_survivable(self, monkeypatch, enabled_env):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics")
        collection = FakeCollection()
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)

        def _boom(texts, cfg):
            raise vector_search.VectorSearchError("LM Studio unreachable")

        monkeypatch.setattr(vector_search, "embed_texts", _boom)
        stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        assert stats["failed"] == 1
        assert stats["embedded"] == 0
        assert collection.records == {}

    def test_unavailable_vector_store_is_survivable(self, monkeypatch, enabled_env):
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics")

        def _boom(username):
            raise vector_search.VectorSearchError("chromadb is not installed")

        monkeypatch.setattr(vector_search, "get_collection", _boom)
        stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        assert stats["embedded"] == 0
        assert stats["failed"] == 0

    def test_skips_when_no_embedding_endpoint_configured(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_VECTOR_SEARCH_ENABLED", "true")
        conn = _groups_db()
        _insert_group(conn, "1@N", "Tree Pics")
        get_collection = MagicMock()
        monkeypatch.setattr(vector_search, "get_collection", get_collection)
        # No WORKBENCH_EMBEDDING_BASE_URL, and resolve_sync_cfg has nothing either.
        with patch("agent.settings.resolve_sync_cfg", return_value={"base_url": "", "api_key": ""}):
            stats = vector_search.sync_group_vectors(conn, "user@N00", "jdoe")

        assert stats["embedded"] == 0
        get_collection.assert_not_called()

    def test_falls_back_to_the_users_sync_connection(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_VECTOR_SEARCH_ENABLED", "true")
        monkeypatch.setenv("WORKBENCH_EMBEDDING_MODEL", "test-embed")
        with patch("agent.settings.resolve_sync_cfg",
                   return_value={"base_url": "http://host.docker.internal:1234/v1", "api_key": "sk"}):
            cfg = vector_search.embedding_cfg("user@N00")
        assert cfg["base_url"] == "http://host.docker.internal:1234/v1"
        assert cfg["api_key"] == "sk"
        assert cfg["model"] == "test-embed"


class TestStoreLock:
    def test_open_collection_holds_the_lock_and_releases_it(self, monkeypatch):
        collection = FakeCollection()
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)

        with vector_search.open_collection("jdoe") as opened:
            assert opened is collection
            assert vector_search._STORE_LOCK.locked()
        assert not vector_search._STORE_LOCK.locked()

    def test_lock_is_released_when_opening_fails(self, monkeypatch):
        def _boom(username):
            raise vector_search.VectorSearchError("nope")

        monkeypatch.setattr(vector_search, "get_collection", _boom)
        with pytest.raises(vector_search.VectorSearchError):
            with vector_search.open_collection("jdoe"):
                pass
        assert not vector_search._STORE_LOCK.locked()


# ---------------------------------------------------------------------------
# search_group_ids
# ---------------------------------------------------------------------------

class TestSearchGroupIds:
    def test_returns_nothing_when_disabled(self, monkeypatch):
        get_collection = MagicMock()
        monkeypatch.setattr(vector_search, "get_collection", get_collection)
        assert vector_search.search_group_ids("golden hour", 5) == []
        get_collection.assert_not_called()

    def test_returns_nearest_ids_with_distances(self, monkeypatch, enabled_env):
        collection = FakeCollection()
        collection.next_query_ids = ["2@N", "1@N"]
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)
        monkeypatch.setattr(vector_search, "embed_texts", _fake_embed)

        hits = vector_search.search_group_ids("golden hour brick path", 5, "user@N00", "jdoe")

        assert [gid for gid, _ in hits] == ["2@N", "1@N"]
        assert hits[0][1] < hits[1][1]
        assert collection.query_calls[0]["n_results"] == 5

    def test_uncorrelated_neighbours_are_dropped(self, monkeypatch, enabled_env):
        collection = FakeCollection()
        collection.next_query_ids = ["1@N", "2@N"]
        collection.next_query_distances = [0.4, 1.0]  # 1.0 = no correlation at all
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)
        monkeypatch.setattr(vector_search, "embed_texts", _fake_embed)

        hits = vector_search.search_group_ids("golden hour", 5, "user@N00", "jdoe")

        assert [gid for gid, _ in hits] == ["1@N"]

    def test_max_distance_is_configurable(self, monkeypatch, enabled_env):
        monkeypatch.setenv("WORKBENCH_VECTOR_MAX_DISTANCE", "0.3")
        collection = FakeCollection()
        collection.next_query_ids = ["1@N", "2@N"]
        collection.next_query_distances = [0.2, 0.5]
        monkeypatch.setattr(vector_search, "get_collection", lambda username: collection)
        monkeypatch.setattr(vector_search, "embed_texts", _fake_embed)

        hits = vector_search.search_group_ids("golden hour", 5, "user@N00", "jdoe")

        assert [gid for gid, _ in hits] == ["1@N"]

    def test_blank_query_short_circuits(self, monkeypatch, enabled_env):
        get_collection = MagicMock()
        monkeypatch.setattr(vector_search, "get_collection", get_collection)
        assert vector_search.search_group_ids("   ", 5) == []
        get_collection.assert_not_called()


# ---------------------------------------------------------------------------
# find_groups integration
# ---------------------------------------------------------------------------

@pytest.fixture()
def groups_db_file(mem_db):
    """conftest's populated DB, plus a second group with no keyword overlap."""
    conn = sqlite3.connect(mem_db)
    conn.execute(
        "INSERT INTO groups (id, name, members, pool_count, synced_at, description, "
        "user_note, needs_summary, summary_md) VALUES "
        "('group2@N00', 'Fleeting Light', 200, 300, 0, 'Golden hour and dusk', '', 0, 'Warm evening light.')"
    )
    conn.commit()
    conn.close()
    with patch("db.DB_FILE", mem_db):
        yield mem_db


class TestFindGroupsIntegration:
    @pytest.mark.asyncio
    async def test_disabled_leaves_find_groups_untouched(self, groups_db_file, monkeypatch):
        import mcp_tools
        search = MagicMock()
        monkeypatch.setattr(vector_search, "search_group_ids", search)

        text = _text(await mcp_tools._find_groups({"query": "Landscape"}))

        assert "Landscape Lovers" in text
        assert "Fleeting Light" not in text
        assert "Semantically similar" not in text
        search.assert_not_called()

    @pytest.mark.asyncio
    async def test_semantic_matches_are_appended(self, groups_db_file, monkeypatch, enabled_env):
        import mcp_tools
        monkeypatch.setattr(vector_search, "search_group_ids",
                            lambda q, limit, nsid, username: [("group2@N00", 0.2)])

        text = _text(await mcp_tools._find_groups({"query": "Landscape"}))

        assert "Landscape Lovers" in text
        assert "Semantically similar" in text
        assert "Fleeting Light" in text
        assert "`group2@N00`" in text
        # Keyword hit stays first.
        assert text.index("Landscape Lovers") < text.index("Fleeting Light")

    @pytest.mark.asyncio
    async def test_group_already_matched_by_keyword_is_not_repeated(self, groups_db_file, monkeypatch, enabled_env):
        import mcp_tools
        monkeypatch.setattr(vector_search, "search_group_ids",
                            lambda q, limit, nsid, username: [("group1@N00", 0.1)])

        text = _text(await mcp_tools._find_groups({"query": "Landscape"}))

        assert text.count("`group1@N00`") == 1
        assert "Semantically similar" not in text

    @pytest.mark.asyncio
    async def test_semantic_only_result_still_returned(self, groups_db_file, monkeypatch, enabled_env):
        import mcp_tools
        monkeypatch.setattr(vector_search, "search_group_ids",
                            lambda q, limit, nsid, username: [("group2@N00", 0.2)])

        text = _text(await mcp_tools._find_groups({"query": "zzz"}))

        assert "Fleeting Light" in text
        assert "No groups match" not in text

    @pytest.mark.asyncio
    async def test_vector_backend_failure_keeps_keyword_results(self, groups_db_file, monkeypatch, enabled_env):
        import mcp_tools

        def _boom(query, limit, nsid, username):
            raise vector_search.VectorSearchError("chroma is down")

        monkeypatch.setattr(vector_search, "search_group_ids", _boom)

        text = _text(await mcp_tools._find_groups({"query": "Landscape"}))

        assert "Landscape Lovers" in text
        assert "Semantically similar" not in text

    @pytest.mark.asyncio
    async def test_stale_vector_id_not_in_local_cache_is_ignored(self, groups_db_file, monkeypatch, enabled_env):
        import mcp_tools
        monkeypatch.setattr(vector_search, "search_group_ids",
                            lambda q, limit, nsid, username: [("gone@N00", 0.2)])

        text = _text(await mcp_tools._find_groups({"query": "Landscape"}))

        assert "Landscape Lovers" in text
        assert "Semantically similar" not in text
