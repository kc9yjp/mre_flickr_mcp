# Flickr MCP Server

A Flickr [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that lets AI clients search, update, and manage your Flickr photo library via natural language — designed for ongoing portfolio maintenance.

- Search photos, find missing metadata, update titles/descriptions/tags
- Add photos to albums and groups, track group membership, manage contacts
- Find weak performers, suggest unfollows, fetch stats, set geolocation, post comments

> **Experimental** — built with [Claude Code](https://claude.ai/code). Functional, use at your own risk.  
> Source: [github.com/kc9yjp/mre_flickr_mcp](https://github.com/kc9yjp/mre_flickr_mcp) · Docker: [hub.docker.com/repositories/ejwettstein](https://hub.docker.com/repositories/ejwettstein) · Author: [Mr. E Photos](https://www.flickr.com/photos/ejwettstein/)

---

## How it works

The server always runs in SSE/web mode — one container serves both the MCP endpoint and a web dashboard for login, sync, and stats.

| URL | Purpose |
|-----|---------|
| `http://localhost:8000/` | Home — status overview and navigation |
| `http://localhost:8000/login` | Browser-based Flickr OAuth login |
| `http://localhost:8000/sync` | Sync status and trigger buttons |
| `http://localhost:8000/stats` | Collection statistics |
| `http://localhost:8000/setup` | `.mcp.json` config snippet for your AI client |
| `http://localhost:8000/sse` | MCP SSE endpoint (AI clients connect here) |

---

## Prerequisites

**Flickr API key** — create an app at [flickr.com/services/apps/create](https://www.flickr.com/services/apps/create/) to get your `FLICKR_API_KEY` and `FLICKR_API_SECRET`.

---

## Quick start

**1. Create a `.env` file:**

```bash
FLICKR_API_KEY=your_api_key
FLICKR_API_SECRET=your_api_secret
MCP_API_KEY=your_secret_token   # optional but recommended
```

**2. Start the server:**

```bash
docker run -d \
  --env-file .env \
  -e MCP_PORT=8000 \
  -v flickr-creds:/root/.flickr_mcp \
  -v flickr-data:/app/data \
  -p 8000:8000 \
  ejwettstein/flickr-mcp
```

Or with Docker Compose — save this as `docker-compose.yml`:

```yaml
services:
  flickr-mcp:
    image: ejwettstein/flickr-mcp
    env_file: .env
    environment:
      - MCP_PORT=8000
    volumes:
      - flickr-creds:/root/.flickr_mcp
      - flickr-data:/app/data
    ports:
      - "8000:8000"
    restart: unless-stopped

volumes:
  flickr-creds:
  flickr-data:
```

```bash
docker compose up -d
```

**3. Log in to Flickr:**

Open `http://localhost:8000/login` in your browser and click **Login with Flickr**. This completes OAuth and saves credentials to the `flickr-creds` volume. You only need to do this once.

**4. Run your first sync:**

Visit `http://localhost:8000/sync` and click **Photos** to sync your library to the local database.

**5. Connect your AI client:**

Visit `http://localhost:8000/setup` for a ready-to-paste `.mcp.json` config, or use this template:

```json
{
  "mcpServers": {
    "flickr": {
      "type": "sse",
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Bearer your_secret_token"
      }
    }
  }
}
```

Add this to your project's `.mcp.json` (Claude Code), `~/.cursor/mcp.json` (Cursor), or `~/.codeium/windsurf/mcp_config.json` (Windsurf).

---

## Stdio mode

Stdio transport is available for clients that require it. Set `MCP_TRANSPORT=stdio` and pipe through docker:

```bash
docker run -i --rm \
  --env-file .env \
  -e MCP_TRANSPORT=stdio \
  -v flickr-creds:/root/.flickr_mcp \
  -v flickr-data:/app/data \
  ejwettstein/flickr-mcp
```

`.mcp.json` for stdio:

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "FLICKR_API_KEY=your_api_key",
        "-e", "FLICKR_API_SECRET=your_api_secret",
        "-e", "MCP_TRANSPORT=stdio",
        "-v", "flickr-creds:/root/.flickr_mcp",
        "-v", "flickr-data:/app/data",
        "ejwettstein/flickr-mcp"
      ]
    }
  }
}
```

Note: stdio mode has no web UI — login and sync must be done via the SSE container.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FLICKR_API_KEY` | Yes | Your Flickr API key |
| `FLICKR_API_SECRET` | Yes | Your Flickr API secret |
| `MCP_PORT` | No | Port for the web/SSE server (default: `8000`) |
| `MCP_API_KEY` | No | Bearer token to protect the SSE endpoint |
| `MCP_TRANSPORT` | No | `sse` (default) or `stdio` |
| `WORKBENCH_VECTOR_SEARCH_ENABLED` | No | `false` (default) — see [Group semantic search](#group-semantic-search-optional) |

## Group semantic search (optional)

Off by default. With the flag off, nothing changes: no vector store, no
embedding calls, no extra dependency — group search is the same keyword
search over the local SQLite cache it has always been.

Turned on, group sync gains a final phase that embeds each group's name +
description + AI summary into a [Chroma](https://www.trychroma.com/)
collection, and `find_groups` gains a second retrieval path that embeds the
query and appends nearest-neighbour groups the keyword search missed — so a
photo described as "golden hour brick path" can surface a group called
"Fleeting Light" that shares no literal word with it.

Embeddings come from any OpenAI-compatible `/v1/embeddings` endpoint —
typically the same LM Studio instance already configured for chat and group
summaries, with an embedding model such as `nomic-embed-text` loaded.

**Turning it on:**

```bash
# 1. Build the image with the optional Chroma dependency
docker compose build --build-arg INSTALL_VECTOR_SEARCH=true
#    (or, running outside Docker: pip install -r requirements-vector.txt)

# 2. Set the flags in .env, then restart
WORKBENCH_VECTOR_SEARCH_ENABLED=true
WORKBENCH_EMBEDDING_BASE_URL=http://host.docker.internal:1234/v1
WORKBENCH_EMBEDDING_MODEL=nomic-embed-text

# 3. Backfill vectors for the groups you already have
docker compose exec flickr-mcp \
  python scripts/sync_groups.py --rebuild-vectors --nsid <nsid> --username <username>
```

After the backfill, every later group sync only re-embeds groups whose text
actually changed.

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKBENCH_VECTOR_SEARCH_ENABLED` | `false` | Master switch. Everything below is ignored when off. |
| `WORKBENCH_EMBEDDING_BASE_URL` | your sync LLM connection's base URL | OpenAI-compatible base URL serving `/embeddings` |
| `WORKBENCH_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model id. Changing it re-embeds every group on the next sync. |
| `WORKBENCH_EMBEDDING_API_KEY` | your sync LLM connection's key | Bearer token, if the endpoint needs one |
| `WORKBENCH_EMBEDDING_TIMEOUT` | `120` | Seconds to wait on one embeddings request |
| `WORKBENCH_VECTOR_MAX_DISTANCE` | `1.0` | Cosine-distance ceiling for a semantic match; lower it to cut noise |
| `WORKBENCH_CHROMA_DIR` | `data/{username}/chroma` | Where embedded Chroma persists |
| `WORKBENCH_CHROMA_HOST` | *(unset)* | Set to use a standalone Chroma server instead of embedded mode |
| `WORKBENCH_CHROMA_PORT` | `8000` | Port for the standalone server |

Chroma runs **embedded** (in-process, persisting to a directory) by default —
no extra container or port. If you'd rather run it as its own service, a
`vector-db` service is defined behind the `vector-search` Compose profile:

```bash
docker compose --profile vector-search up -d
# then set WORKBENCH_CHROMA_HOST=vector-db and WORKBENCH_CHROMA_PORT=8000
```

Failures are never fatal: if LM Studio or Chroma is unreachable, the sync
logs a warning and continues, and `find_groups` falls back to keyword-only
results. Deleting `data/{username}/chroma` fully resets the feature.

## Volumes

| Mount | Purpose |
|-------|---------|
| `flickr-creds:/root/.flickr_mcp` | OAuth credentials (written by web login) |
| `flickr-data:/app/data` | SQLite database of your photo metadata |

---

## Playwright tests

A `playwright/` directory contains smoke tests and an interactive OAuth login
helper.  They work against any running instance of the server.

```bash
cd playwright
npm install
npx playwright install --with-deps chromium

# Smoke tests (no login required)
npm test

# Complete OAuth once to save a session, then run authenticated tests
npm run login   # opens a browser — log in on Flickr, script saves session
npm test        # now also runs sync/stats/setup page tests
```

To run the tests inside Docker against the running container:

```bash
docker compose -f docker-compose.yml -f docker-compose.playwright.yml \
  run --rm playwright
```

See [`playwright/README.md`](playwright/README.md) for details.

---

## Resources

- [ARCHITECTURE.md](ARCHITECTURE.md) — technical deep dive: server layout, multi-user model, data layer, sync pipeline, MCP tool dispatch, and the Workbench SPA/chat agent
- [TOOLS.md](TOOLS.md) — full catalog of all MCP tools
- [WORKBENCH.md](WORKBENCH.md) / [MODEL_CONFIG.md](MODEL_CONFIG.md) — Workbench build log and LLM settings reference
- [Docker Hub](https://hub.docker.com/repositories/ejwettstein)
- [Flickr API docs](https://www.flickr.com/services/api/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
