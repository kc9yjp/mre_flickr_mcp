# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Flickr MCP (Model Context Protocol) server with supporting Python CLI scripts. The project is authored by Eric Wettstein (flickr: ejwettstein / Mr. E Photos).

**Architecture:** Two-tier design
1. **CLI scripts** (`scripts/`) — standalone Python tools for Flickr API interaction
2. **MCP server** (planned) — wraps CLI functionality for use by AI clients

## Running the CLI

**Prerequisites:**
```bash
pip install requests
```

**Main CLI (`scripts/flickr.py`):**
```bash
python scripts/flickr.py login    # OAuth flow — opens browser, prompts for verifier
python scripts/flickr.py status   # Verify session via flickr.test.login
python scripts/flickr.py logout   # Delete saved credentials
```

## Docker

`Dockerfile` and `docker-compose.yml` are at the repo root. Credentials are persisted in a named Docker volume (`flickr-creds` → `/root/.flickr_mcp`). The `login` command requires `--rm` and an interactive TTY.

```bash
docker compose build
bin/flickr login
bin/flickr status
```

## Configuration

- `.env` — API key and secret (`FLICKR_API_KEY`, `FLICKR_API_SECRET`)
- OAuth access tokens are saved to `~/.flickr_mcp/credentials.json` after login (outside the repo)
- SQLite database (`.db`) is planned for caching user/photo data but not yet implemented

## Key Implementation Details

- OAuth 1.0a signing is done manually (HMAC-SHA1) via `sign_request()` — no third-party OAuth library
- `scripts/flickr.py` uses `argparse` subcommands; `load_env()` reads `.env` then falls back to environment variables
- `scripts/flickr_oauth.py` and `scripts/flickr_update.py` are earlier standalone scripts (credentials hardcoded)

## Planned Features (not yet implemented)

- Full MCP server layer
- SQLite database mapping Flickr accounts/photos
- Photo search, CRUD for photos/albums, follower management
- Environment-based credential loading from `.env`

## Resources

- Flickr API docs: https://www.flickr.com/services/api/
