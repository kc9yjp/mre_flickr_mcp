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

## CLI Features (implemented)

- OAuth 1.0a login / status / logout
- Manual HMAC-SHA1 request signing — no third-party OAuth library

## Planned Features

- Full MCP server layer
- SQLite database mapping Flickr accounts and photos
- Photo search and CRUD (title, description, tags)
- Album and follower management
- Paginated photo listing with rich metadata (EXIF, geo, stats, etc.)

---

## Configuration

| File | Purpose |
|---|---|
| `.env` | `FLICKR_API_KEY` and `FLICKR_API_SECRET` |
| `~/.flickr_mcp/credentials.json` | OAuth access tokens (outside repo, auto-created on login) |

---

## Resources

- [Flickr API docs](https://www.flickr.com/services/api/)
