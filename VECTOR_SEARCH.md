# Group Semantic Search

Optional semantic (vector) search over your Flickr groups, so "suggest groups
for this photo" finds thematically relevant groups instead of only literal
keyword matches.

**Off by default.** Everything described here is gated behind a single flag
(`WORKBENCH_VECTOR_SEARCH_ENABLED`). With it off — the default — there is no
vector store, no embedding calls, no extra dependency in the image, and
`find_groups` behaves exactly as it always has.

---

## The problem it solves

`find_groups` searches the local SQLite group cache with SQL `LIKE` over each
group's name, description, AI summary, and keywords. That misses groups whose
text is thematically right but shares no literal word with the query:

| Photo described as | Group | Keyword search | Semantic search |
|---|---|---|---|
| "golden hour brick path" | **Fleeting Light** — *"warm evening light on anything"* | ✗ no shared word | ✓ found |
| "misty pines at dawn" | **Quiet Woodlands** | ✗ | ✓ |
| "sunset" | **Sunset Lovers** | ✓ | ✓ (already found, not duplicated) |

With the feature on, group text is embedded into a vector store during sync;
`find_groups` embeds your query too and appends the nearest neighbours the
keyword search missed, under their own heading:

```markdown
## Sunset Lovers (`12345@N00`)
- Members: 4,200 · Pool: 90,000
...

---

_Semantically similar groups (no keyword match):_

## Fleeting Light (`67890@N00`)
- Members: 900 · Pool: 12,000

Warm evening light on anything.
```

Keyword hits stay first — they're literal and verifiable — with semantic hits
labeled so it's obvious why a group with no shared words is in the list.

---

## What you need

1. **An embedding model** served at an OpenAI-compatible `/v1/embeddings`
   endpoint. This is usually the same LM Studio instance already configured
   for chat and group summaries — load an embedding model such as
   `nomic-embed-text` or `bge-base` alongside your chat model.
2. **The image built with Chroma included.** `chromadb` is deliberately not
   in `requirements.txt`; it's installed only when you ask for it.

Verify the endpoint first — from the host, before touching Docker:

```bash
curl http://localhost:1234/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "nomic-embed-text", "input": "golden hour brick path"}'
```

A JSON response with a `data[0].embedding` array of floats means you're ready.
If that call fails, nothing below will work.

---

## Enabling with Docker Compose

### 1. Add the build arg and environment to `docker-compose.yml`

```yaml
services:
  flickr-mcp:
    build:
      context: .
      args:
        # Installs requirements-vector.txt (chromadb) into the image
        INSTALL_VECTOR_SEARCH: "true"
    env_file: .env
    environment:
      - MCP_PORT=8000
      - WORKBENCH_VECTOR_SEARCH_ENABLED=true
      - WORKBENCH_EMBEDDING_BASE_URL=http://host.docker.internal:1234/v1
      - WORKBENCH_EMBEDDING_MODEL=nomic-embed-text
    volumes:
      - flickr-creds:/home/app/.flickr_mcp
      - flickr-data:/app/data
    ports:
      - "8000:8000"

volumes:
  flickr-creds:
  flickr-data:
```

Two notes on this block:

- **No new volume is needed.** In the default embedded mode the vector store
  lives at `data/{username}/chroma`, inside the `flickr-data` volume you
  already mount, so it's backed up and reset alongside the SQLite database.
- **`host.docker.internal`** is how the container reaches LM Studio running on
  your host. On Linux, add
  `extra_hosts: ["host.docker.internal:host-gateway"]` to the service if it
  isn't already resolving.

The three environment variables can equally live in `.env` (Compose loads it
via `env_file`), which keeps the Compose file generic:

```bash
# .env
WORKBENCH_VECTOR_SEARCH_ENABLED=true
WORKBENCH_EMBEDDING_BASE_URL=http://host.docker.internal:1234/v1
WORKBENCH_EMBEDDING_MODEL=nomic-embed-text
```

### 2. Rebuild and restart

```bash
docker compose build          # picks up INSTALL_VECTOR_SEARCH from the file
docker compose up -d
```

If you'd rather not edit `build.args`, pass it on the command line instead:

```bash
docker compose build --build-arg INSTALL_VECTOR_SEARCH=true
```

Confirm Chroma made it into the image:

```bash
docker compose exec flickr-mcp python -c "import chromadb; print(chromadb.__version__)"
```

### 3. Backfill vectors for the groups you already have

Turning the flag on doesn't retroactively embed anything — the next group sync
would only pick up groups that changed. Backfill once:

```bash
# Find your nsid and username (each is a single directory)
docker compose exec flickr-mcp ls /home/app/.flickr_mcp   # → nsid, e.g. 99999999@N00
docker compose exec flickr-mcp ls /app/data               # → username, e.g. jdoe

docker compose exec flickr-mcp \
  python scripts/sync_groups.py --rebuild-vectors --nsid 99999999@N00 --username jdoe
```

Expected output for ~600 groups (a few minutes on a local embedding model):

```
Rebuilding group vectors...
  Vectors 16/612
  Vectors 32/612
  ...
  612 group vectors written (0 unchanged, 0 removed, 0 failed).
Done.
```

`--rebuild-vectors` makes no Flickr API calls — it only reads the local group
cache and writes embeddings — and exits non-zero if any group failed, so it's
safe to run from a script.

Multi-user installs: run it once per user, with that user's nsid/username
pair. Each user has an independent store.

### 4. Verify

Ask `find_groups` something with no keyword overlap and look for the
"Semantically similar groups" heading. Or watch the server log during the next
group sync:

```bash
docker compose logs -f flickr-mcp | grep -i vector
```

From here on, every group sync re-embeds only the groups whose text actually
changed.

---

## Standalone Chroma (optional)

Embedded mode is the default and is sufficient for this data size (~600
groups, a few MB). Run Chroma as its own service only if you want the vector
store queryable from outside the server process.

A `vector-db` service is defined behind a Compose profile, so it never starts
unless you ask for it:

```yaml
  vector-db:
    image: chromadb/chroma:latest
    profiles: [vector-search]
    ports:
      - "8100:8000"
    volumes:
      - chroma-data:/chroma/chroma
```

```bash
docker compose --profile vector-search up -d
```

Then point the server at it (Compose service name, container-internal port):

```yaml
    environment:
      - WORKBENCH_CHROMA_HOST=vector-db
      - WORKBENCH_CHROMA_PORT=8000
```

`WORKBENCH_CHROMA_DIR` is ignored in this mode. Collections are named per user
(`groups_{username}_{hash}`), so one server can share a standalone Chroma
across accounts. Switching between embedded and standalone does not migrate
existing vectors — run `--rebuild-vectors` after the switch.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `WORKBENCH_VECTOR_SEARCH_ENABLED` | `false` | Master switch. Everything below is ignored when off. |
| `WORKBENCH_EMBEDDING_BASE_URL` | your sync LLM connection's base URL | OpenAI-compatible base URL serving `/embeddings` (must end in `/v1`) |
| `WORKBENCH_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model id. Changing it re-embeds every group on the next sync. |
| `WORKBENCH_EMBEDDING_API_KEY` | your sync LLM connection's key | Bearer token, if the endpoint requires one |
| `WORKBENCH_EMBEDDING_TIMEOUT` | `120` | Seconds to wait on one embeddings request |
| `WORKBENCH_VECTOR_MAX_DISTANCE` | `1.0` | Cosine-distance ceiling for a semantic match. Lower it (e.g. `0.6`) to cut noise. |
| `WORKBENCH_CHROMA_DIR` | `data/{username}/chroma` | Where embedded Chroma persists |
| `WORKBENCH_CHROMA_HOST` | *(unset)* | Set to use a standalone Chroma server instead of embedded mode |
| `WORKBENCH_CHROMA_PORT` | `8000` | Port for the standalone server |

Truthy values for the flag: `true`, `1`, `yes`, `on` (case-insensitive).

When `WORKBENCH_EMBEDDING_BASE_URL` / `_API_KEY` are unset, the user's
configured **sync connection** is reused — the same one the AI group summaries
use, chosen on the Sync page (see
[MODEL_CONFIG.md](MODEL_CONFIG.md#sync-jobs-model-selection)). You still have
to name an embedding model, since it's rarely the same model as the chat one.

---

## How it works

### Sync (write path)

`sync_groups.py` gains a third phase, after the existing Flickr sync and the
AI summary generation:

```
1. Sync group list + descriptions from Flickr → SQLite      (unchanged)
2. Generate AI summaries for flagged groups                 (unchanged)
3. [if enabled] For each new/changed group:
     - Build embedding text: name + description + AI summary + keywords + note
     - POST it to /v1/embeddings (batches of 16)
     - Upsert vector + metadata into Chroma
```

It runs *after* the summaries so the embedded text includes each group's
freshly generated summary and keywords.

**Change detection** uses a fingerprint — a hash of the embedded text plus the
model id — stored as metadata on each vector. On every sync, stored
fingerprints are compared against freshly computed ones, and only groups that
differ are re-embedded. Consequences:

- A normal sync with nothing changed makes **zero** embedding calls.
- Editing a group note (`set_group_note`) or a changed Flickr description
  re-embeds just that group.
- Changing `WORKBENCH_EMBEDDING_MODEL` re-embeds **everything** automatically
  — vectors from different models aren't comparable.
- Groups you've left are dropped from the store on the next sync.

Vector state lives entirely in Chroma — there is deliberately no SQLite column
and no schema migration for it, so deleting the chroma directory is a complete
reset.

### Search (read path)

`find_groups` runs both retrieval paths and merges:

```
1. SQL LIKE search over name/description/summary/keywords   (unchanged)
2. [if enabled]
     - Embed the query
     - Budget: semantic hits get whatever is left of `limit` after the
       keyword rows, so the response never exceeds the requested size
     - Ask Chroma for (budget + keyword hits) neighbours, since the keyword
       matches are usually also the nearest vectors and get filtered below
     - Drop anything the keyword search already returned
     - Drop anything beyond WORKBENCH_VECTOR_MAX_DISTANCE, and anything the
       store returned without a distance to check against it
     - Look the survivors up in SQLite and append them, nearest first
```

Chroma always returns the *n* nearest vectors however far away they are, which
is why the distance ceiling exists: without it every search would append its
full quota of "matches" regardless of relevance. The `1.0` default (cosine
distance) drops anything with no positive correlation to the query at all;
tighten it if your embedding model still surfaces noise.

Because the budget is what's left of `limit`, a query whose keyword matches
already fill `limit` returns no semantic section at all — the caller got the
number of groups it asked for, all of them literal matches. Raise `limit` to
leave room for semantic hits; that trade-off is stated in the `find_groups`
tool description so the calling model can act on it.

### Failure behaviour

No failure here is allowed to escape:

| Failure | What happens |
|---|---|
| LM Studio unreachable during sync | Logged, that batch counted as failed, sync continues and succeeds; groups stay keyword-searchable and are retried next sync |
| LM Studio unreachable during search | Logged, `find_groups` returns keyword results only |
| `chromadb` not installed | Logged as "chromadb is not installed", both paths skip |
| Chroma store unreadable | Logged, sync skips the vector phase; search returns keyword results only |
| Group left / stale vector | Vector dropped on next sync; a stale id that reaches search is ignored (not in the SQL cache) |

The one exception is `--rebuild-vectors`, which exits non-zero on failure —
it's an explicit one-shot command, not part of a scheduled sync.

---

## Operations

**Re-embed everything** (after changing embedding model, switching between
embedded/standalone, or restoring a partial run):

```bash
docker compose exec flickr-mcp \
  python scripts/sync_groups.py --rebuild-vectors --nsid <nsid> --username <username>
```

**Turn the feature off:** set `WORKBENCH_VECTOR_SEARCH_ENABLED=false` (or drop
the variable) and restart. Nothing else needs undoing — the stored vectors are
simply never read. The image can keep Chroma installed; it just won't be
imported.

**Reset the store completely:**

```bash
docker compose exec flickr-mcp rm -rf /app/data/<username>/chroma
```

The next sync (or a `--rebuild-vectors` run) recreates it.

**Disk footprint:** trivial. ~600 groups × a few KB per vector ≈ 2–10 MB.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Log: `chromadb is not installed` | Image built without the build arg | Rebuild with `INSTALL_VECTOR_SEARCH=true` |
| Log: `no embedding endpoint configured` | `WORKBENCH_EMBEDDING_BASE_URL` unset and no sync connection configured | Set the variable, or pick a sync connection on the Sync page |
| Log: `embeddings endpoint ... unreachable` | Wrong host/port, or LM Studio not running | Check the `curl` from [What you need](#what-you-need); on Linux add `extra_hosts: ["host.docker.internal:host-gateway"]` |
| Log: `embeddings endpoint ... returned 404` | Model id not loaded in LM Studio | Load the embedding model; match `WORKBENCH_EMBEDDING_MODEL` to its id exactly |
| Sync says `0 group vectors written (612 unchanged...)` | Nothing changed — normal | Use `--rebuild-vectors` to force |
| No "Semantically similar" section ever appears | Feature off, store empty, or everything filtered by the distance ceiling | Check the flag reached the container (`docker compose exec flickr-mcp env \| grep WORKBENCH`), run the backfill, try raising `WORKBENCH_VECTOR_MAX_DISTANCE` |
| Newly synced groups don't turn up in search | — | Shouldn't happen: the store is re-opened per query specifically to avoid this (see below). If it does, check the sync log for embedding failures. |

---

## Implementation notes

All the code lives in `scripts/vector_search.py`, plus two call sites
(`sync_groups.py` for the write path, `tools/groups.py::_find_groups` for the
read path). `chromadb` is imported lazily inside `get_collection()`, so with
the feature off the module never touches it.

One non-obvious detail: **the store is re-opened on every operation**, under a
process-wide lock. Vectors are written by the `sync_groups.py` *subprocess*
but read by the long-lived server process, and embedded Chroma caches its HNSW
index in-process — so a server that opened the store once would keep querying
the index as it looked at startup, and groups embedded by a later sync would
stay invisible until a restart. Re-opening per operation (which drops Chroma's
system cache) is what keeps a running server current.

See [ARCHITECTURE.md](ARCHITECTURE.md#sync-pipeline) for how this fits the
wider sync pipeline, and `tests/test_vector_search.py` for the test suite
covering both flag states.
