# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Flickr MCP (Model Context Protocol) server. The server always runs in SSE/web mode — it exposes MCP tools to AI clients over SSE, and provides a web dashboard for login, sync, and stats.

**Architecture:**
- `scripts/flickr_mcp.py` — MCP server + web UI (Starlette/uvicorn, SSE transport)
- `scripts/flickr_sync.py` and `scripts/sync_*.py` — sync scripts invoked by the server
- `scripts/flickr.py` — standalone CLI (legacy, rarely used directly)

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
| `/sync` | Sync status and trigger buttons |
| `/setup` | MCP connection config snippet for Claude Code |

## MCP Server Setup

1. Build and start: `docker compose up -d`
2. Visit `http://localhost:8000/setup` for the `.mcp.json` config snippet
3. Add it to your project or global `~/.claude/mcp.json`
4. Restart Claude Code and run `/mcp` to confirm the `flickr` server is connected

## Configuration

- `.env` — API key and secret (`FLICKR_API_KEY`, `FLICKR_API_SECRET`, optionally `MCP_API_KEY`)
- OAuth access tokens: persisted in the `flickr-creds` Docker volume (`/root/.flickr_mcp/credentials.json`)
- SQLite database: `data/flickr.db` (in the `flickr-data` volume)

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
| `find_albums` | Search albums by keyword |
| `get_album_photos` | List photos in an album |
| `add_to_album` | Add photo to an album |
| `remove_from_album` | Remove photo from an album |
| `create_album` | Create a new album |
| `edit_album` | Update album title/description |
| `delete_album` | Delete an album |
| `find_groups` | Search joined groups by keyword |
| `add_to_group` | Add photo to a group pool |
| `remove_from_group` | Remove photo from a group pool |
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

- `/flickr-photo` — process a photo from the current Safari tab: suggest metadata, update, add to groups/albums
- `/flickr-fave` — fave the current Safari photo immediately, then suggest a comment
- `/flickr-hide` — find weak photos, review visually, make private or update and keep
- `/flickr-sync` — trigger syncs via the web UI and report results

## Resources

- Flickr API docs: https://www.flickr.com/services/api/
