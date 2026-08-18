# Usage

## Prerequisites

```bash
cp docker-compose.yml.example docker-compose.yml
echo "FLICKR_API_KEY=your_api_key" >> .env
echo "FLICKR_API_SECRET=your_api_secret" >> .env
docker compose build
docker compose up -d
```

---

## Web dashboard

The server always starts in SSE/web mode. After `docker compose up -d`, log in
once and everything else lives inside **Mr. E's Photo Workbench**, a single
dockable-panel SPA:

| Page | URL | Purpose |
|------|-----|---------|
| Login | `http://localhost:8000/login` | Browser-based Flickr OAuth — click once, done |
| Workbench | `http://localhost:8000/app` | Photo Browser, Sync, Stats, Setup, Chat, Queue, and Settings panels |

`http://localhost:8000/` redirects to `/app` once you're logged in.

---

## First-time setup

1. `docker compose up -d`
2. Open `http://localhost:8000/login` → **Login with Flickr** → complete OAuth in browser
3. Open `http://localhost:8000/app` → **Sync** panel → click **Photos** for initial sync
4. Same app → **Setup** panel → copy the `.mcp.json` snippet into your project

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

`your_mcp_api_key` above is the personal key shown in the Workbench's Setup
panel after you log in — every request to `/sse`/`/mcp` must carry a valid
key or OAuth access token; there is no way to run the endpoint unauthenticated.
`MCP_API_KEY` (the env var) only matters for stdio mode — see below.

### Tools

A representative sample — see **[TOOLS.md](TOOLS.md)** for the full catalog
of all MCP tools.

| Tool | Description |
|------|-------------|
| `search_photos` | Filter by title keyword, tag, date range; sort by date or views |
| `get_photo` | Full metadata for one photo by ID |
| `get_summary` | Total count, views, date range, top tags |
| `list_recent_syncs` | Sync history |
| `sync` | Trigger an incremental or full sync |
| `find_weak_photos` | Photos ranked by weakness (low views, no faves/comments) |
| `find_albums` / `get_all_albums` / `get_album_photos` | Search albums, list all albums, list contents |
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

Or via `docker run`. Stdio mode identifies you by your personal API key, not a
session, so log in via the SSE container first and copy the key from the
Workbench's Setup panel:

```bash
docker run -i --rm \
  --env-file .env \
  -e MCP_TRANSPORT=stdio \
  -e MCP_API_KEY=your_personal_api_key \
  -v flickr-creds:/home/app/.flickr_mcp \
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
| `MCP_API_KEY` | Only for stdio | Personal API key (from the Workbench Setup panel) identifying which user a stdio session is; not used by SSE mode |
| `MCP_TRANSPORT` | No | `sse` (default) or `stdio` |

## Volumes

| Mount | Purpose |
|-------|---------|
| `flickr-creds:/home/app/.flickr_mcp` | OAuth credentials |
| `flickr-data:/app/data` | SQLite photo metadata database |

---

## Scripted / headless login and sync

There is no longer a standalone `flickr.py` CLI. For scripted or headless
use outside the web login flow:

```bash
# Terminal OAuth (out-of-band verifier, no browser redirect) — see the
# module docstring in flickr_oauth.py for the two-step exchange:
python scripts/flickr_oauth.py

# Sync directly, once credentials exist (per-user, needs --nsid/--username
# unless running as the legacy single-user fallback):
python scripts/flickr_sync.py            # incremental
python scripts/flickr_sync.py --full     # full re-fetch
python scripts/flickr_sync.py --backfill # walk full upload history in date windows
```

Credentials are saved to `~/.flickr_mcp/{nsid}/credentials.json`. This path
is for development/debugging; for normal use, log in and sync via the
Workbench (`/login`, then the Sync panel at `/app`).
