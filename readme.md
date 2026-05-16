# Flickr MCP Server

A Flickr [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that lets AI clients search, update, and manage your Flickr photo library via natural language.

- Search photos, find missing metadata, update titles/descriptions/tags
- Add photos to albums and groups, manage contacts, suggest unfollows
- Fetch stats, set geolocation, post comments, fave photos

> **Experimental** — built with [Claude Code](https://claude.ai/code). Functional, use at your own risk.  
> Source: [github.com/kc9yjp/mre_flickr_mcp](https://github.com/kc9yjp/mre_flickr_mcp) · Author: [Mr. E Photos](https://www.flickr.com/photos/ejwettstein/)

---

## Prerequisites

**1. Flickr API key**

Create an app at [flickr.com/services/apps/create](https://www.flickr.com/services/apps/create/) to get your `FLICKR_API_KEY` and `FLICKR_API_SECRET`.

**2. One-time OAuth login**

The server authenticates to Flickr via OAuth. Run this once to authorize and store credentials in the `flickr-creds` Docker volume:

```bash
docker run -it --rm \
  -e FLICKR_API_KEY=your_api_key \
  -e FLICKR_API_SECRET=your_api_secret \
  -v flickr-creds:/root/.flickr_mcp \
  -v flickr-data:/app/data \
  --entrypoint python \
  ejwettstein/flickr-mcp \
  scripts/flickr.py login
```

This opens a browser for OAuth approval and saves credentials to the `flickr-creds` volume. You only need to do this once; both stdio and SSE modes share the same volume.

---

## 1. Stdio Mode

Stdio is the default transport — the MCP client launches the container directly and communicates over stdin/stdout. Best for Claude Code, Cursor, Windsurf, and most desktop MCP clients.

### Docker run

```bash
docker run -i --rm \
  -e FLICKR_API_KEY=your_api_key \
  -e FLICKR_API_SECRET=your_api_secret \
  -v flickr-creds:/root/.flickr_mcp \
  -v flickr-data:/app/data \
  ejwettstein/flickr-mcp
```

### Docker Compose starter

Save this as `docker-compose.yml` (no repo clone needed):

```yaml
services:
  mcp:
    image: ejwettstein/flickr-mcp
    env_file: .env
    volumes:
      - flickr-creds:/root/.flickr_mcp
      - flickr-data:/app/data
    stdin_open: true

volumes:
  flickr-creds:
  flickr-data:
```

Create a `.env` file alongside it:

```bash
FLICKR_API_KEY=your_api_key
FLICKR_API_SECRET=your_api_secret
```

Run the server:

```bash
docker compose run --rm -i mcp
```

### Client configuration

**Claude Code** — add to `.mcp.json` in your project root or `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
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

Or if using Docker Compose, point to your compose directory:

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": ["compose", "run", "--rm", "-i", "mcp"],
      "cwd": "/path/to/your/compose/directory"
    }
  }
}
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
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

**Cursor** — add to `.cursor/mcp.json` in your project or `~/.cursor/mcp.json` globally:

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
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

**Windsurf** — add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
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

## 2. Streaming HTTP Mode (SSE)

SSE mode runs the server as a persistent HTTP service. Use this for web-based clients, remote access over a network, or when you want a single server instance shared across multiple clients.

### Docker run

```bash
docker run -d \
  -e FLICKR_API_KEY=your_api_key \
  -e FLICKR_API_SECRET=your_api_secret \
  -e MCP_TRANSPORT=sse \
  -e MCP_PORT=8000 \
  -e MCP_API_KEY=your_secret_token \
  -v flickr-creds:/root/.flickr_mcp \
  -v flickr-data:/app/data \
  -p 8000:8000 \
  ejwettstein/flickr-mcp
```

The server listens at `http://localhost:8000/sse`.

`MCP_API_KEY` is optional but recommended — clients must pass it as `Authorization: Bearer your_secret_token`.

### Docker Compose starter

Save this as `docker-compose.yml` (no repo clone needed):

```yaml
services:
  mcp-web:
    image: ejwettstein/flickr-mcp
    env_file: .env
    environment:
      - MCP_TRANSPORT=sse
      - MCP_PORT=8000
    volumes:
      - flickr-creds:/root/.flickr_mcp
      - flickr-data:/app/data
    ports:
      - "8000:8000"

volumes:
  flickr-creds:
  flickr-data:
```

Create a `.env` file alongside it:

```bash
FLICKR_API_KEY=your_api_key
FLICKR_API_SECRET=your_api_secret
MCP_API_KEY=your_secret_token
```

Start the server:

```bash
docker compose up -d mcp-web
```

### Client configuration

**Claude Code** — add to `.mcp.json` or `~/.claude/claude_desktop_config.json`:

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

**Cursor** — add to `.cursor/mcp.json` or `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "flickr": {
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Bearer your_secret_token"
      }
    }
  }
}
```

**Windsurf** — add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "flickr": {
      "serverUrl": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Bearer your_secret_token"
      }
    }
  }
}
```

**Remote access** — replace `localhost` with your server's IP or hostname. If exposing publicly, use a reverse proxy (nginx, Caddy) with TLS.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `FLICKR_API_KEY` | Yes | Your Flickr API key |
| `FLICKR_API_SECRET` | Yes | Your Flickr API secret |
| `MCP_TRANSPORT` | No | `stdio` (default) or `sse` |
| `MCP_PORT` | No | Port for SSE mode (default: `8000`) |
| `MCP_API_KEY` | No | Bearer token to protect the SSE endpoint |

## Volumes

| Mount | Purpose |
|---|---|
| `flickr-creds:/root/.flickr_mcp` | OAuth credentials (written by the login step) |
| `flickr-data:/app/data` | SQLite database of your photo metadata |

---

## Resources

- [Full tool list and local development](https://github.com/kc9yjp/mre_flickr_mcp)
- [Flickr API docs](https://www.flickr.com/services/api/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
