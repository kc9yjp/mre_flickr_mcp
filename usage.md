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

## MCP Server

Not yet implemented. See `readme.md` for planned features.
