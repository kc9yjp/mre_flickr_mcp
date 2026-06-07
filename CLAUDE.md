# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Flickr MCP (Model Context Protocol) server. The server always runs in SSE/web mode — it exposes MCP tools to AI clients over SSE, and provides a web dashboard for login, sync, and stats.

**Architecture:**
- `scripts/flickr_mcp.py` — MCP server + web UI (Starlette/uvicorn, SSE transport)
- `scripts/flickr_sync.py` and `scripts/sync_*.py` — sync scripts invoked by the server
- `scripts/flickr.py` — standalone CLI (legacy, rarely used directly)

## Local Development

```bash
# Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Run server locally (needs .env)
python scripts/flickr_mcp.py

# Run tests
pytest
```

## Docker

```bash
docker compose build
docker compose up -d
```

The single `flickr-mcp` service starts in SSE/web mode on port 8000. No separate services needed.

## Web UI

Visit `http://localhost:8000` after starting the container:

| Page | Purpose |
|------|---------|
| `/login` | Browser-based Flickr OAuth login (no terminal paste) |
| `/stats` | Collection statistics from local SQLite |
| `/sync` | Sync status and trigger buttons; includes Reset Database button |
| `/setup` | Personal MCP connection config snippet (shows your API key) |

Logs go to stderr and are visible via `docker compose logs -f flickr-mcp`.

## MCP Server Setup

1. Build and start: `docker compose up -d`
2. Log in at `http://localhost:8000/login` via Flickr OAuth
3. Visit `http://localhost:8000/setup` for your personal `.mcp.json` config snippet
4. Add it to your project or global `~/.claude/mcp.json`
5. Restart Claude Code and run `/mcp` to confirm the `flickr` server is connected

## Configuration

- `.env` — Flickr app credentials only: `FLICKR_API_KEY`, `FLICKR_API_SECRET`
- `MCP_API_KEY` env var is **no longer used** — each user gets a personal API key generated on first login
- OAuth access tokens + personal API key: `~/.flickr_mcp/{nsid}/credentials.json` (in the `flickr-creds` Docker volume)
- SQLite database: `data/{username}/flickr.db` (in the `flickr-data` volume)

## Multi-User Support

The server supports multiple independent Flickr accounts:

- Each user authenticates via Flickr OAuth at `/login`
- A personal MCP API key is generated automatically on first login and shown at `/setup`
- Each user has an isolated SQLite database (`data/{username}/flickr.db`) and credentials dir (`~/.flickr_mcp/{nsid}/`)
- Sessions last 30 days; logout clears the session but preserves credentials and database
- Users can reset (delete) their own database from the `/sync` page — a fresh sync recreates it
- The background refresh task syncs each registered user independently every 12 hours

### Migration from single-user installs

Existing deployments with credentials at `~/.flickr_mcp/credentials.json` (flat path) must re-login via `/login` after upgrading. The old flat file is not migrated automatically.

## Database Schema

```
photos            — id, title, description, tags, views, favorites, comments,
                    date_taken, date_uploaded, url_photopage, url_original,
                    is_public, reviewed_at, synced_at
contacts          — id, username, realname, is_friend, is_family, synced_at
contact_engagement — contact_id, faves, comments, last_updated
do_not_unfollow   — contact_id, reason, added_at
groups            — id, name, members, pool_count, synced_at
albums            — id, title, description, primary_photo_id, count_photos, count_views, synced_at
photo_groups      — photo_id, group_id (which of your photos are in each group)
sync_log          — type, mode, photos_fetched, synced_at
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `get_summary` | Total photos, views, top tags, date range |
| `list_recent_syncs` | Recent sync log entries |
| `search_photos` | Search local DB by keyword, incomplete metadata, sort by views/date |
| `get_photo` | Fetch single photo details |
| `get_photo_stats` | Views, favorites, comments for a photo |
| `get_photo_comments` | Fetch comments on a photo |
| `fetch_photo_image` | Download photo and return as image for visual inspection |
| `update_photo` | Update title, description, tags (Flickr + local DB) |
| `set_visibility` | Make photo public or private |
| `find_weak_photos` | Rank photos by weakness score (low views, zero faves/comments) |
| `add_comment` | Post a comment on a photo |
| `fave_photo` | Add a photo to the user's favorites |
| `get_photo_faves` | List users who faved a photo, with `you_follow` flag cross-referenced from local contacts DB |
| `find_albums` | Search albums by keyword |
| `get_album_photos` | List photos in an album |
| `add_to_album` | Add photo to an album |
| `remove_from_album` | Remove photo from an album |
| `create_album` | Create a new album |
| `edit_album` | Update album title/description |
| `delete_album` | Delete an album |
| `find_groups` | Search joined groups by keyword |
| `get_group_stats` | Groups ranked by how many of your photos are in each |
| `get_photo_group_count` | Photos ranked by how many groups they belong to |
| `add_to_group` | Add photo to a group pool |
| `remove_from_group` | Remove photo from a group pool |
| `get_photo_contexts` | Return group pools and albums a photo belongs to (local DB after sync, API fallback) |
| `get_contacts_summary` | Total contacts, friends/family count, engagement stats, top engagers |
| `find_unfollow_candidates` | Contacts ranked by lowest engagement (faves + comments) |
| `protect_contact` | Add contact to do-not-unfollow whitelist |
| `unfollow_contact` | Unfollow a contact via API |
| `set_location` | Set photo geolocation (lat/lon) on Flickr |
| `sync` | Trigger an incremental (or full) photo sync from within MCP |

## Key Implementation Details

- OAuth 1.0a signing is done manually (HMAC-SHA1) via `_sign()` — no third-party OAuth library
- `_api_get()` / `_api_post()` handle OAuth signing for all Flickr API calls
- Web UI routes live inside `main_sse()` alongside the MCP SSE endpoint
- OAuth login uses a full browser redirect flow: `/login/start` → Flickr → `/oauth/callback`
- Schema changes must be added to the migrations list in `init_db()` — never use `ALTER TABLE` directly

## Skills (Claude Code slash commands)

- `/flickr-photo` — process a photo from the current browser tab: suggest metadata, update, add to groups/albums
- `/flickr-fave` — fave the current browser photo, then suggest a comment with any input given, wait for confirm
- `/flickr-hide` — find weak photos, review visually, make private or update and keep
- `/flickr-sync` — trigger syncs via the web UI and report results

## Browser Interaction

When interacting with the browser:
- **Always ask the user** before taking browser-based actions
- **Remember user preferences** across sessions
- **Preferred setup:** macOS Safari withy cli, Chrome DevTools, or something else (suggest)
- Use browser context to enhance photo workflows (e.g., detecting current Flickr page, extracting metadata)

## Resources

- Flickr API docs: https://www.flickr.com/services/api/
