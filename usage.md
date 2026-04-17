# Usage

## Prerequisites

### Without Docker
```bash
pip install requests
cp .env.example .env   # add your FLICKR_API_KEY and FLICKR_API_SECRET
```

### With Docker
```bash
docker compose build
```

---

## CLI (`scripts/flickr.py`)

### Authentication

```bash
# OAuth login — opens browser, prompts for verifier code
python scripts/flickr.py login

# Verify session is active
python scripts/flickr.py status

# Delete saved credentials
python scripts/flickr.py logout
```

Credentials are saved to `~/.flickr_mcp/credentials.json` (outside the repo).

### Configuration

| Variable | Description |
|---|---|
| `FLICKR_API_KEY` | App key from flickr.com/services/apps |
| `FLICKR_API_SECRET` | App secret from flickr.com/services/apps |

Set these in `.env` or as environment variables.

---

## Docker

A wrapper script at `bin/flickr` handles the Docker boilerplate. From the repo root:

```bash
bin/flickr login    # OAuth login — opens browser, prompts for verifier
bin/flickr status   # Verify session is active
bin/flickr logout   # Delete saved credentials
```

OAuth credentials are persisted in the `flickr-creds` Docker volume so you only need to log in once.

---

## Photo Sync (`scripts/flickr_sync.py`)

Fetches public photo metadata into a local SQLite database (`flickr.db`).

```bash
bin/flickr-sync --create          # first run — creates data/flickr.db
bin/flickr-sync                   # incremental — only photos updated since last sync
bin/flickr-sync --full            # fetch all public photos
bin/flickr-sync --full --create   # full sync, creating db if needed
```

Or without Docker:
```bash
python scripts/flickr_sync.py --create
python scripts/flickr_sync.py
python scripts/flickr_sync.py --full
```

The first run always does a full sync regardless of the flag. Subsequent runs default to incremental.

### Database schema

| Table | Purpose |
|---|---|
| `photos` | One row per photo: title, description, dates, tags, views, URLs |
| `sync_log` | History of each sync run (timestamp, mode, count) |

---

## MCP Server (`scripts/flickr_mcp.py`)

Stdio MCP server for use with Claude Code (or any MCP client).

### Claude Code setup

The project's `.mcp.json` registers the server and `.claude/settings.json` auto-approves it. After `docker compose build`, restart Claude Code from this directory and the `flickr` MCP server will be available.

### Tools

| Tool | Description |
|---|---|
| `search_photos` | Filter by title keyword, tag, date range; sort by date or views |
| `get_photo` | Full metadata for one photo by ID |
| `get_summary` | Total count, views, date range, top tags |
| `list_recent_syncs` | Sync history |
| `sync` | Trigger an incremental or full sync from Flickr |

### Running manually

```bash
docker compose run --rm -i mcp
```
