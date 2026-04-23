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
# OAuth login — opens browser, prompts for verifier code, then runs initial full sync
python scripts/flickr.py login

# Verify session is active
python scripts/flickr.py status

# Delete saved credentials and local database
python scripts/flickr.py logout
```

Credentials are saved to `~/.flickr_mcp/credentials.json` (outside the repo). Logout removes both credentials and `data/flickr.db`.

### Sync

```bash
python scripts/flickr.py sync             # incremental — only photos updated since last sync
python scripts/flickr.py sync --full      # re-fetch all public photos
python scripts/flickr.py sync --create    # create database if missing, then sync
```

`login` runs a full sync automatically, so manual syncs are only needed for subsequent refreshes.

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
bin/flickr login    # OAuth login — opens browser, prompts for verifier, then runs initial sync
bin/flickr status   # Verify session is active
bin/flickr logout   # Delete saved credentials and local database
bin/flickr sync     # Incremental sync
bin/flickr sync --full   # Full re-fetch
```

OAuth credentials are persisted in the `flickr-creds` Docker volume so you only need to log in once.

---

---

## MCP Server (`scripts/flickr_mcp.py`)

Stdio MCP server for use with Claude Code (or any MCP client).

### Claude Code setup

**Using Local Build:**
The project's `.mcp.json` registers the server and `.claude/settings.json` auto-approves it. After `docker compose build`, restart Claude Code from this directory and the `flickr` MCP server will be available.

**Using Docker Hub Image:**
You can use the published Docker Hub image without pulling the repo by adding this to your global Claude Code (or other MCP client) config:

```json
{
  "mcpServers": {
    "flickr": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "FLICKR_API_KEY=your_key",
        "-e",
        "FLICKR_API_SECRET=your_secret",
        "-v",
        "flickr-creds:/root/.flickr_mcp",
        "-v",
        "flickr-data:/app/data",
        "YOUR_DOCKER_ORG/flickr-mcp"
      ]
    }
  }
}
```

### Tools

| Tool | Description |
|---|---|
| `search_photos` | Filter by title keyword, tag, date range; sort by date or views |
| `get_photo` | Full metadata for one photo by ID |
| `get_summary` | Total count, views, date range, top tags |
| `list_recent_syncs` | Sync history |
| `sync` | Trigger an incremental or full sync from Flickr |
| `manage_groups` | Find groups and add photos |
| `manage_contacts` | Find and unfollow candidates, protect contacts |

### Running manually

Since the Docker image's default entrypoint is the MCP server, you can run it directly:

```bash
docker run -i --rm \
  -e FLICKR_API_KEY=... \
  -e FLICKR_API_SECRET=... \
  -v flickr-creds:/root/.flickr_mcp \
  -v ./data:/app/data \
  YOUR_DOCKER_ORG/flickr-mcp
```

Or locally via docker compose:
```bash
docker compose run --rm -i mcp
```
