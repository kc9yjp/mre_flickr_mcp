# flickr-mcp

> **⚠️ Experimental project** — built with [Claude Code](https://claude.ai/code) via AI-assisted vibe coding. Functional, but use at your own risk.

A Flickr [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that lets AI clients like Claude Code search, update, and manage your Flickr photo library via natural language.

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

## Prerequisites: First-time authentication

The image uses OAuth to talk to Flickr. You must complete the OAuth flow once before using the image. Clone the source repo and run:

```bash
git clone https://github.com/kc9yjp/mre_flickr_mcp.git
cd mre_flickr_mcp
cp .env.example .env   # add your FLICKR_API_KEY and FLICKR_API_SECRET
docker compose build
bin/flickr login       # opens a browser for OAuth approval
```

This stores credentials in the `flickr-creds` Docker volume. The published image reuses that same volume.

---

## Quick start — stdio (Claude Code / MCP clients)

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "FLICKR_API_KEY=your_api_key",
        "-e", "FLICKR_API_SECRET=your_api_secret",
        "-v", "flickr-creds:/root/.flickr_mcp",
        "-v", "flickr-data:/app/data",
        "ejwettstein/flickr-mcp"
      ]
    }
  }
}
```

---

## Quick start — SSE (HTTP/web clients)

```bash
docker run -d \
  -e FLICKR_API_KEY=your_api_key \
  -e FLICKR_API_SECRET=your_api_secret \
  -e MCP_TRANSPORT=sse \
  -e MCP_PORT=8000 \
  -e MCP_API_KEY=your_secret_key \
  -v flickr-creds:/root/.flickr_mcp \
  -v flickr-data:/app/data \
  -p 8000:8000 \
  ejwettstein/flickr-mcp
```

Connect your client to `http://localhost:8000/sse`.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FLICKR_API_KEY` | Yes | Your Flickr API key |
| `FLICKR_API_SECRET` | Yes | Your Flickr API secret |
| `MCP_TRANSPORT` | No | `stdio` (default) or `sse` |
| `MCP_PORT` | No | Port for SSE mode (default: `8000`) |
| `MCP_API_KEY` | No | API key to protect the SSE endpoint |

Get your API key at [flickr.com/services/apps/create](https://www.flickr.com/services/apps/create/).

---

## Volumes

| Mount | Purpose |
|-------|---------|
| `flickr-creds:/root/.flickr_mcp` | OAuth credentials (created by `bin/flickr login`) |
| `flickr-data:/app/data` | SQLite database of your photo metadata |

---

## Source & tools

Full source, tool list, and local development instructions:
**[github.com/kc9yjp/mre_flickr_mcp](https://github.com/kc9yjp/mre_flickr_mcp)**

The author's Flickr photostream (the real reason this exists):
**[flickr.com/photos/ejwettstein](https://www.flickr.com/photos/ejwettstein/)**
