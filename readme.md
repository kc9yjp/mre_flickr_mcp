# Flickr MCP Server

A Flickr MCP (Model Context Protocol) server with supporting Python CLI scripts.  
Author: Eric Wettstein — [Mr. E Photos](https://www.flickr.com/photos/ejwettstein/) (`ejwettstein` on Flickr)

---

## Architecture

Two-tier design:

1. **CLI scripts** (`scripts/`) — standalone Python tools for Flickr API interaction
2. **MCP server** (planned) — wraps CLI functionality for use by AI clients

---

## Quick Start

### Run MCP Server via Docker

The pre-built Docker container defaults to the MCP server. This is the easiest way to connect to an MCP client like Claude Code:

```bash
# Add to your .mcp.json or equivalent client config
docker run -i --rm \
  -e FLICKR_API_KEY="your_key" \
  -e FLICKR_API_SECRET="your_secret" \
  -v flickr-creds:/root/.flickr_mcp \
  -v ./data:/app/data \
  YOUR_DOCKER_ORG/flickr-mcp
```

### Local Development

For authentication and syncing metadata, or developing locally:

```bash
pip install requests
# add FLICKR_API_KEY and FLICKR_API_SECRET to .env
python scripts/flickr.py login
```

Or with Docker:

```bash
docker compose build
bin/flickr login
```

See [usage.md](usage.md) for full instructions.

---

## Features

**Implemented**
- OAuth 1.0a login / status / logout (`bin/flickr`)
- Public photo metadata sync to SQLite (`bin/flickr-sync`)
- MCP server with 14 tools: search, get, sync, manage groups, find unfollow candidates, etc.

**Planned**
- Photo CRUD (title, description, tags)
- Album and follower management
- EXIF, geo, stats metadata

---

## Configuration

| File | Purpose |
|---|---|
| `.env` | `FLICKR_API_KEY` and `FLICKR_API_SECRET` |
| `~/.flickr_mcp/credentials.json` | OAuth access tokens (outside repo, auto-created on login) |

---

## Resources

- [Flickr API docs](https://www.flickr.com/services/api/)
