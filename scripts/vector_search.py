"""Optional semantic (vector) search over Flickr group text.

**Off by default.** Every entry point here first checks :func:`enabled`
(``WORKBENCH_VECTOR_SEARCH_ENABLED``, default ``false``); when the flag is
off nothing in this module runs, nothing is imported from Chroma, and no
embedding request is ever made — group sync and ``find_groups`` behave
exactly as they did before this module existed.

When the flag is on:

* ``sync_groups.py`` gains a final phase (:func:`sync_group_vectors`) that
  embeds each new/changed group's name + description + AI summary and
  upserts the vector into Chroma.
* ``find_groups`` gains a second retrieval path (:func:`search_group_ids`)
  that embeds the query and asks Chroma for nearest neighbours, so a photo
  described as "golden hour brick path" can surface a group called
  "Fleeting Light" that shares no literal keyword.

Design notes:

* **Chroma runs embedded** by default (``chromadb.PersistentClient``) —
  a directory under ``data/{username}/chroma``, no extra container or port.
  Setting ``WORKBENCH_CHROMA_HOST`` switches to a standalone Chroma server
  (``chromadb.HttpClient``) instead; see the ``vector-search`` Compose
  profile in ``docker-compose.yml.example``.
* **Embeddings come from an OpenAI-compatible ``/v1/embeddings``
  endpoint** — typically the same LM Studio instance already configured for
  chat/summaries. ``WORKBENCH_EMBEDDING_BASE_URL`` overrides it; when unset
  the user's configured sync connection (``resolve_sync_cfg``) is reused.
* **Change detection lives in the vector store, not SQLite.** Each vector
  carries a ``fingerprint`` metadata field (hash of the embedded text +
  model id), so a sync only re-embeds groups whose text actually changed
  and no schema migration is needed to hold vector state. Deleting
  ``data/{username}/chroma`` fully resets the feature.
* **Nothing here is allowed to fail a sync.** An unreachable LM Studio or a
  missing ``chromadb`` package logs a warning and leaves the affected group
  SQL-searchable but not vector-searchable until the next successful run.
* **The store is re-opened per operation**, under :data:`_STORE_LOCK` — see
  ``get_collection``. Vectors are written by the ``sync_groups.py``
  subprocess but read by the long-lived server process, and embedded Chroma
  caches its HNSW index in-process, so anything else would leave newly
  embedded groups invisible to search until a restart.
"""

import contextlib
import hashlib
import logging
import os
import re
import threading

import httpx

# ── Config ───────────────────────────────────────────────────────────────────

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Embedding model used when WORKBENCH_EMBEDDING_MODEL is unset. Any model
# served at the connection's /v1/embeddings works — this is just the id LM
# Studio / Ollama use for the common small embedding model.
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_EMBEDDING_TIMEOUT = 120

# Cosine-distance ceiling for a neighbour to count as a match. Chroma always
# returns the n nearest vectors, however far away they are, so without a
# ceiling every search would append its full quota of "matches" no matter how
# unrelated. 1.0 is the principled default: it drops anything with no positive
# correlation to the query at all. Tighten it (e.g. 0.6) via
# WORKBENCH_VECTOR_MAX_DISTANCE if a particular embedding model still surfaces
# noise.
DEFAULT_MAX_DISTANCE = 1.0

# Texts per /v1/embeddings request. Kept small because the usual backend is a
# local model on modest hardware, and because one failed batch only costs
# that batch (the rest of the sync continues).
EMBED_BATCH_SIZE = 16

_TRUTHY = ("1", "true", "yes", "on")


class VectorSearchError(RuntimeError):
    """Vector store or embedding backend could not be used."""


def _env(name: str, default: str = "") -> str:
    """Read *name* from the environment, falling back to the ``.env`` file.

    Docker Compose loads ``.env`` into the container environment already;
    the file fallback is for running ``python scripts/...`` directly during
    local development, where only ``flickr_api._load_env`` would otherwise
    see it.
    """
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == name:
                    return val.strip()
    except OSError:
        pass
    return default


def enabled() -> bool:
    """True when ``WORKBENCH_VECTOR_SEARCH_ENABLED`` is set to a truthy value."""
    return _env("WORKBENCH_VECTOR_SEARCH_ENABLED", "false").lower() in _TRUTHY


def embedding_cfg(nsid: str | None = None) -> dict:
    """Resolve the ``/v1/embeddings`` endpoint config.

    ``WORKBENCH_EMBEDDING_BASE_URL`` / ``WORKBENCH_EMBEDDING_API_KEY`` win
    when set; otherwise the user's configured sync connection is reused, so
    a working LM Studio setup needs no extra configuration beyond naming an
    embedding model.
    """
    base_url = _env("WORKBENCH_EMBEDDING_BASE_URL")
    api_key = _env("WORKBENCH_EMBEDDING_API_KEY")

    if not base_url and nsid:
        try:
            from agent.settings import resolve_sync_cfg
            cfg = resolve_sync_cfg(nsid)
            base_url = cfg.get("base_url", "")
            api_key = api_key or cfg.get("api_key", "")
        except Exception as e:
            logging.warning("vector_search: could not resolve sync connection for %s: %s", nsid, e)

    try:
        timeout = int(_env("WORKBENCH_EMBEDDING_TIMEOUT", str(DEFAULT_EMBEDDING_TIMEOUT)))
    except ValueError:
        timeout = DEFAULT_EMBEDDING_TIMEOUT

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": _env("WORKBENCH_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        "timeout": timeout,
    }


# ── Embeddings ───────────────────────────────────────────────────────────────


def embed_texts(texts: list[str], cfg: dict) -> list[list[float]]:
    """Embed *texts* via ``POST {base_url}/embeddings``.

    Blocking (httpx sync client) — async callers should hand it to
    ``asyncio.to_thread``. Raises :class:`VectorSearchError` on any
    transport, status, or shape problem so callers have a single exception
    type to log and skip on.
    """
    if not texts:
        return []
    if not cfg.get("base_url") or not cfg.get("model"):
        raise VectorSearchError(
            "no embedding endpoint configured (set WORKBENCH_EMBEDDING_BASE_URL "
            "and WORKBENCH_EMBEDDING_MODEL, or configure a sync connection)"
        )

    url = cfg["base_url"].rstrip("/") + "/embeddings"
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    try:
        with httpx.Client(timeout=httpx.Timeout(cfg.get("timeout", DEFAULT_EMBEDDING_TIMEOUT), connect=15.0)) as client:
            resp = client.post(url, json={"model": cfg["model"], "input": texts}, headers=headers)
    except httpx.HTTPError as e:
        raise VectorSearchError(f"embeddings endpoint {url} unreachable: {e}") from e

    if resp.status_code != 200:
        raise VectorSearchError(f"embeddings endpoint {url} returned {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json().get("data") or []
        # OpenAI's contract allows out-of-order entries; sort defensively so a
        # batch can never be silently mis-paired with the wrong group.
        vectors = [item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0))]
    except (ValueError, KeyError, TypeError) as e:
        raise VectorSearchError(f"unexpected embeddings response from {url}: {e}") from e

    if len(vectors) != len(texts):
        raise VectorSearchError(
            f"embeddings endpoint {url} returned {len(vectors)} vectors for {len(texts)} inputs"
        )
    return vectors


def group_embedding_text(row) -> str:
    """Build the text embedded for one group row (any mapping of column name
    to value — ``sqlite3.Row`` or a plain dict).

    Combines everything that describes what the group is *about*: its name,
    Flickr description, AI summary/keywords, and the user's own note. HTML
    in the Flickr description is flattened first so tags don't pollute the
    embedding.
    """
    from text_utils import html_to_text

    def _field(key: str) -> str:
        try:
            value = row[key]
        except (KeyError, IndexError):
            return ""
        return (value or "").strip() if isinstance(value, str) else ""

    parts = [
        _field("name"),
        html_to_text(_field("description"), limit=3000),
        _field("summary_md"),
        _field("ai_keywords"),
        _field("user_note"),
    ]
    return "\n\n".join(p for p in parts if p)


def embedding_fingerprint(text: str, model: str) -> str:
    """Stable hash of the embedded text *and* the model that produced it.

    Stored as vector metadata and compared on the next sync, so only groups
    whose text actually changed are re-embedded — and switching embedding
    models re-embeds everything (vectors from different models aren't
    comparable).
    """
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


# ── Chroma store ─────────────────────────────────────────────────────────────


def collection_name(username: str | None) -> str:
    """Per-user Chroma collection name.

    Embedded mode already isolates users by directory, but a standalone
    Chroma server shares one namespace across users, so the name carries a
    hash of the username. Chroma requires 3–63 chars of ``[a-zA-Z0-9._-]``
    starting and ending alphanumeric.
    """
    if not username:
        return "flickr_groups"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", username).strip("_").lower()[:32]
    digest = hashlib.sha256(username.encode()).hexdigest()[:8]
    return f"groups_{slug}_{digest}" if slug else f"groups_{digest}"


def chroma_dir(username: str | None) -> str:
    """Persistence directory for embedded mode."""
    override = _env("WORKBENCH_CHROMA_DIR")
    if override:
        return override
    return os.path.join(_DATA_DIR, username, "chroma") if username else os.path.join(_DATA_DIR, "chroma")


_STORE_LOCK = threading.Lock()
"""Serializes vector-store access within one process.

Every open re-reads the store from disk (see ``get_collection``), so only one
thread may hold a collection at a time — the MCP server can otherwise run
several ``find_groups`` calls concurrently.
"""


@contextlib.contextmanager
def open_collection(username: str | None):
    """Context manager yielding the collection with :data:`_STORE_LOCK` held."""
    with _STORE_LOCK:
        yield get_collection(username)


def get_collection(username: str | None):
    """Return the Chroma collection for *username*, creating it if needed.

    Imports ``chromadb`` lazily so the package is only required when the
    feature is actually turned on (see ``requirements-vector.txt``).
    Raises :class:`VectorSearchError` if the package is missing or the store
    can't be opened.

    Prefer :func:`open_collection`, which also takes the store lock.
    """
    try:
        import chromadb
    except ImportError as e:
        raise VectorSearchError(
            "chromadb is not installed — `pip install -r requirements-vector.txt` "
            "(or build the image with --build-arg INSTALL_VECTOR_SEARCH=true)"
        ) from e

    host = _env("WORKBENCH_CHROMA_HOST")
    try:
        if host:
            port = int(_env("WORKBENCH_CHROMA_PORT", "8000"))
            client = chromadb.HttpClient(host=host, port=port)
        else:
            path = chroma_dir(username)
            os.makedirs(path, exist_ok=True)
            # Embedded Chroma caches its system — including the in-memory HNSW
            # index — per path for the life of the process. Vectors are written
            # by the sync_groups.py *subprocess*, so without dropping that cache
            # the long-lived server process would keep querying the index as it
            # looked when it first opened, and newly embedded groups would stay
            # invisible to find_groups until a restart. Clearing only unbinds
            # the cache (it doesn't stop live systems), and _STORE_LOCK keeps
            # concurrent opens from stacking.
            from chromadb.api.shared_system_client import SharedSystemClient
            SharedSystemClient.clear_system_cache()
            client = chromadb.PersistentClient(path=path)
        # embedding_function=None: vectors are always supplied explicitly by
        # embed_texts(), so Chroma must never fall back to downloading and
        # running its own default embedding model.
        return client.get_or_create_collection(
            name=collection_name(username),
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
    except VectorSearchError:
        raise
    except Exception as e:
        raise VectorSearchError(f"could not open Chroma collection: {e}") from e


def stored_fingerprints(collection) -> dict[str, str]:
    """Map group id -> stored fingerprint for everything in the collection."""
    result = collection.get(include=["metadatas"]) or {}
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    out = {}
    for i, gid in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else None
        out[gid] = (meta or {}).get("fingerprint", "")
    return out


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ── Sync phase ───────────────────────────────────────────────────────────────


def sync_group_vectors(conn, nsid: str | None = None, username: str | None = None,
                       rebuild: bool = False) -> dict:
    """Embed new/changed groups into the vector store.

    Runs as the last phase of ``sync_groups.py`` and is a no-op unless the
    feature flag is on. Only groups whose embedding text changed since the
    last run are re-embedded (compared by fingerprint); groups no longer in
    the SQL cache — i.e. groups the user has left — are dropped from the
    store. Pass ``rebuild=True`` to re-embed every group regardless, which
    is what ``sync_groups.py --rebuild-vectors`` does when backfilling after
    first enabling the feature.

    Never raises: any failure is logged and reported in the returned stats
    dict, leaving the affected groups SQL-searchable as before.

    ``stats["skipped"]`` is the one non-numeric key: empty when the phase ran,
    otherwise the reason it couldn't. Callers need it to tell "ran, nothing to
    do" (all counters zero because every group was already current) apart from
    "never ran" (all counters zero because there was no endpoint, no chromadb,
    or no reachable store) — the two are otherwise indistinguishable.
    """
    stats = {"total": 0, "embedded": 0, "unchanged": 0, "deleted": 0, "failed": 0, "skipped": ""}
    if not enabled():
        stats["skipped"] = "vector search disabled"
        return stats

    cfg = embedding_cfg(nsid)
    if not cfg["base_url"] or not cfg["model"]:
        logging.warning("vector_search: no embedding endpoint configured for %s; skipping group vectors", nsid)
        print("  Skipping group vectors: no embedding endpoint configured "
              "(set WORKBENCH_EMBEDDING_BASE_URL / WORKBENCH_EMBEDDING_MODEL).")
        stats["skipped"] = "no embedding endpoint configured"
        return stats

    try:
        with open_collection(username) as collection:
            return _write_group_vectors(conn, collection, cfg, stats, rebuild, username or nsid)
    except VectorSearchError as e:
        logging.warning("vector_search: vector store unavailable for %s: %s", username or nsid, e)
        print(f"  Skipping group vectors: {e}")
        stats["skipped"] = str(e)
        return stats


def _write_group_vectors(conn, collection, cfg: dict, stats: dict, rebuild: bool, label: str) -> dict:
    """Diff the groups table against the vector store and embed what changed.

    Called by :func:`sync_group_vectors` with the store lock held; *label*
    identifies the user in log lines.
    """
    # Read as dicts rather than relying on the caller's row factory — the sync
    # scripts open plain tuple-returning connections.
    cur = conn.execute("SELECT id, name, description, summary_md, ai_keywords, user_note FROM groups")
    columns = [c[0] for c in cur.description]
    rows = [dict(zip(columns, values)) for values in cur.fetchall()]
    stats["total"] = len(rows)

    try:
        existing = stored_fingerprints(collection)
    except Exception as e:
        logging.warning("vector_search: could not read existing fingerprints for %s: %s", label, e)
        print(f"  Skipping group vectors: could not read vector store ({e}).")
        stats["skipped"] = f"could not read vector store ({e})"
        return stats

    live_ids = set()
    pending = []
    for row in rows:
        live_ids.add(row["id"])
        text = group_embedding_text(row)
        if not text:
            continue
        fingerprint = embedding_fingerprint(text, cfg["model"])
        if not rebuild and existing.get(row["id"]) == fingerprint:
            stats["unchanged"] += 1
            continue
        pending.append({
            "id": row["id"],
            "name": row["name"] or "",
            "text": text,
            "fingerprint": fingerprint,
        })

    stale = [gid for gid in existing if gid not in live_ids]
    if stale:
        try:
            collection.delete(ids=stale)
            stats["deleted"] = len(stale)
        except Exception as e:
            logging.warning("vector_search: could not delete %d stale vectors for %s: %s",
                            len(stale), label, e)

    for batch in _batched(pending, EMBED_BATCH_SIZE):
        try:
            vectors = embed_texts([item["text"] for item in batch], cfg)
            collection.upsert(
                ids=[item["id"] for item in batch],
                embeddings=vectors,
                documents=[item["text"] for item in batch],
                metadatas=[{"name": item["name"], "fingerprint": item["fingerprint"]} for item in batch],
            )
        except Exception as e:
            stats["failed"] += len(batch)
            logging.warning("vector_search: embedding failed for %d group(s) (%s): %s",
                            len(batch), label, e)
            print(f"  Warning: embedding failed for {len(batch)} group(s): {e}")
            continue
        stats["embedded"] += len(batch)
        print(f"  Vectors {stats['embedded']}/{len(pending)}", flush=True)

    print(f"  {stats['embedded']} group vectors written "
          f"({stats['unchanged']} unchanged, {stats['deleted']} removed, {stats['failed']} failed).")
    return stats


# ── Query path ───────────────────────────────────────────────────────────────


def search_group_ids(query: str, limit: int = 25, nsid: str | None = None,
                     username: str | None = None) -> list[tuple[str, float]]:
    """Return ``(group_id, distance)`` nearest neighbours for *query*,
    nearest first, excluding anything beyond ``WORKBENCH_VECTOR_MAX_DISTANCE``.

    Blocking — call from a thread when on the event loop. Returns an empty
    list when the feature is off or the query is empty; raises
    :class:`VectorSearchError` when it's on but the store or embedding
    backend can't be reached, so the caller can log the real reason.
    """
    if not enabled() or not query.strip():
        return []

    cfg = embedding_cfg(nsid)
    # Embed before taking the store lock — the HTTP round trip is the slow
    # part and doesn't touch Chroma.
    vector = embed_texts([query], cfg)[0]
    with open_collection(username) as collection:
        try:
            # include=["distances"] is explicit rather than relying on the
            # default, since the distance is what the relevance ceiling below
            # is applied to — a result set without it is unusable, not a
            # result set to accept unfiltered.
            result = collection.query(
                query_embeddings=[vector],
                n_results=max(1, limit),
                include=["distances"],
            )
        except Exception as e:
            raise VectorSearchError(f"vector query failed: {e}") from e

    try:
        max_distance = float(_env("WORKBENCH_VECTOR_MAX_DISTANCE", str(DEFAULT_MAX_DISTANCE)))
    except ValueError:
        max_distance = DEFAULT_MAX_DISTANCE

    ids = (result.get("ids") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    hits = []
    for i, gid in enumerate(ids):
        if i >= len(distances):
            # No distance means the ceiling can't be applied to this id.
            # Dropping it fails closed; substituting a default would let an
            # arbitrarily distant group through labelled "semantically
            # similar", which is exactly what the ceiling exists to prevent.
            logging.warning("vector_search: dropping group %s from results — no distance returned", gid)
            continue
        if distances[i] < max_distance:
            hits.append((gid, distances[i]))
    return hits
