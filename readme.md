# Flickr MCP Server

A Flickr [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that lets AI clients search, update, and manage your Flickr photo library via natural language.

- Search photos, find missing metadata, update titles/descriptions/tags
- Add photos to albums and groups, manage contacts, suggest unfollows
- Fetch stats, set geolocation, post comments, fave photos

> **Experimental** — built with [Claude Code](https://claude.ai/code). Functional, use at your own risk.  
> Source: [github.com/kc9yjp/mre_flickr_mcp](https://github.com/kc9yjp/mre_flickr_mcp) · Author: [Mr. E Photos](https://www.flickr.com/photos/ejwettstein/)

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

## Volumes

| Mount | Purpose |
|-------|---------|
| `flickr-creds:/root/.flickr_mcp` | OAuth credentials (written by web login) |
| `flickr-data:/app/data` | SQLite database of your photo metadata |

---

## Resources

- [Full tool list and local development](https://github.com/kc9yjp/mre_flickr_mcp)
- [Flickr API docs](https://www.flickr.com/services/api/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
