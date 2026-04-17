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
- MCP server with 5 tools: search, get, summary, sync history, trigger sync

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
