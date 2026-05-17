# Usage

## Prerequisites

```bash
cp .env.example .env   # add your FLICKR_API_KEY and FLICKR_API_SECRET
docker compose build
docker compose up -d
```

---

## Web dashboard

The server always starts in SSE/web mode. Visit these pages after `docker compose up -d`:

| Page | URL | Purpose |
|------|-----|---------|
| Login | `http://localhost:8000/login` | Browser-based Flickr OAuth — click once, done |
| Sync | `http://localhost:8000/sync` | Trigger and monitor syncs |
| Stats | `http://localhost:8000/stats` | Collection stats from local DB |
| Setup | `http://localhost:8000/setup` | `.mcp.json` config snippet for your AI client |

---

## First-time setup

1. `docker compose up -d`
2. Open `http://localhost:8000/login` → **Login with Flickr** → complete OAuth in browser
3. Open `http://localhost:8000/sync` → click **Photos** for initial sync
4. Open `http://localhost:8000/setup` → copy the `.mcp.json` snippet into your project

---

## MCP Server

The SSE endpoint is `http://localhost:8000/sse`. Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "flickr": {
      "type": "sse",
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Bearer your_mcp_api_key"
      }
    }
  }
}
```

`MCP_API_KEY` is optional — if not set, no auth is required. Set it in `.env` to restrict access.

### Tools

| Tool | Description |
|------|-------------|
| `search_photos` | Filter by title keyword, tag, date range; sort by date or views |
| `get_photo` | Full metadata for one photo by ID |
| `get_summary` | Total count, views, date range, top tags |
| `list_recent_syncs` | Sync history |
| `sync` | Trigger an incremental or full sync |
| `find_weak_photos` | Photos ranked by weakness (low views, no faves/comments) |
| `find_albums` / `get_album_photos` | Search albums, list contents |
| `add_to_album` / `remove_from_album` | Manage album membership |
| `find_groups` / `add_to_group` | Search groups, submit photos |
| `get_contacts_summary` / `find_unfollow_candidates` | Engagement stats |
| `set_visibility` / `set_location` | Bulk edits |
| `get_exif` / `get_photo_stats` | Photo metadata and analytics |
| `fave_photo` / `add_comment` | Social actions |

---

## Stdio mode

For clients that require stdio transport, override with `MCP_TRANSPORT=stdio`:

```bash
docker compose --profile stdio up flickr-mcp-stdio
```

Or via `docker run`:

```bash
docker run -i --rm \
  --env-file .env \
  -e MCP_TRANSPORT=stdio \
  -v flickr-creds:/root/.flickr_mcp \
  -v flickr-data:/app/data \
  ejwettstein/flickr-mcp
```

Note: stdio mode has no web UI. Manage login and sync via the SSE container first.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FLICKR_API_KEY` | Yes | Your Flickr API key |
| `FLICKR_API_SECRET` | Yes | Your Flickr API secret |
| `MCP_PORT` | No | Port for the web/SSE server (default: `8000`) |
| `MCP_API_KEY` | No | Bearer token to protect the SSE endpoint |
| `MCP_TRANSPORT` | No | `sse` (default) or `stdio` |

## Volumes

| Mount | Purpose |
|-------|---------|
| `flickr-creds:/root/.flickr_mcp` | OAuth credentials |
| `flickr-data:/app/data` | SQLite photo metadata database |

---

## CLI (`scripts/flickr.py`)

The CLI is still available for direct use outside Docker:

```bash
pip install requests
python scripts/flickr.py login    # OAuth login (terminal-based, opens browser)
python scripts/flickr.py status   # Verify session
python scripts/flickr.py sync     # Incremental sync
python scripts/flickr.py sync --full   # Full re-fetch
```

Credentials are saved to `~/.flickr_mcp/credentials.json`. The CLI is useful for development and debugging; for production use the web dashboard.
