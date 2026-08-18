# flickr-mcp

> **⚠️ Experimental project** — built with [Claude Code](https://claude.ai/code) via AI-assisted vibe coding. Functional, but use at your own risk.

A Flickr [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that lets AI clients like Claude Code search, update, and manage your Flickr photo library via natural language — designed for ongoing portfolio maintenance.

Built by [Mr. E Photos](https://www.flickr.com/photos/ejwettstein/) — source on [GitHub](https://github.com/kc9yjp/mre_flickr_mcp).

---

## What it does

Connect this server to an MCP client and you can ask it to:

- Search your photo library, find photos missing metadata
- Update titles, descriptions, and tags
- Add photos to albums and groups
- Find weak performers (low views, zero faves) and make them private
- Manage contacts, check engagement stats, suggest unfollows
- Fetch EXIF data, set geolocation, view stats by date
- Post comments and fave photos
- And more — see the [full tool list on GitHub](https://github.com/kc9yjp/mre_flickr_mcp)

---

## How it works

One container serves both the MCP SSE endpoint and **Mr. E's Photo Workbench**,
a browser-based dashboard:

| URL | Purpose |
|-----|---------|
| `http://localhost:8000/login` | Browser-based Flickr OAuth login |
| `http://localhost:8000/app` | The Workbench — Photo Browser, Sync, Stats, Setup, Chat, and more |
| `http://localhost:8000/sse` | MCP endpoint (AI clients connect here) |

---

## Quick start

**1. Create a `.env` file:**

```bash
FLICKR_API_KEY=your_api_key
FLICKR_API_SECRET=your_api_secret
```

Get your API key at [flickr.com/services/apps/create](https://www.flickr.com/services/apps/create/).

**2. Start with Docker Compose:**

```yaml
services:
  flickr-mcp:
    image: ejwettstein/flickr-mcp
    env_file: .env
    environment:
      - MCP_PORT=8000
    volumes:
      - flickr-creds:/home/app/.flickr_mcp
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

**3. Log in:** Open `http://localhost:8000/login` and click **Login with Flickr**. Credentials are saved to the `flickr-creds` volume — you only need to do this once.

**4. Sync:** Open `http://localhost:8000/app`, go to the **Sync** panel, and click **Photos** to populate the local database.

**5. Connect your client:** Open the **Setup** panel in the Workbench for a config snippet with your personal API key already filled in, or add this to your `.mcp.json`:

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

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FLICKR_API_KEY` | Yes | Your Flickr API key |
| `FLICKR_API_SECRET` | Yes | Your Flickr API secret |
| `MCP_PORT` | No | Port (default: `8000`) |
| `MCP_API_KEY` | Only for stdio | Personal API key (from the Workbench Setup panel) identifying which user a stdio session is; not used by SSE mode |
| `MCP_TRANSPORT` | No | `sse` (default) or `stdio` |

## Volumes

| Mount | Purpose |
|-------|---------|
| `flickr-creds:/home/app/.flickr_mcp` | OAuth credentials |
| `flickr-data:/app/data` | SQLite photo metadata database |

---

## Source & tools

Full source, tool list, and local development instructions:
**[github.com/kc9yjp/mre_flickr_mcp](https://github.com/kc9yjp/mre_flickr_mcp)**

The author's Flickr photostream (the real reason this exists):
**[flickr.com/photos/ejwettstein](https://www.flickr.com/photos/ejwettstein/)**
