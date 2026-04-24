# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Flickr MCP (Model Context Protocol) server with supporting Python CLI scripts. The project is authored by Eric Wettstein (flickr: ejwettstein / Mr. E Photos).

**Architecture:** Two-tier design
1. **CLI scripts** (`scripts/`) — standalone Python tools for Flickr API interaction and data sync
2. **MCP server** (`scripts/flickr_mcp.py`) — runs inside Docker, exposes tools to AI clients via stdio

## Docker

`Dockerfile` and `docker-compose.yml` are at the repo root. Two services:
- `flickr` — CLI and sync scripts
- `mcp` — MCP server (stdio, used by Claude Code)

Credentials are persisted in a named Docker volume (`flickr-creds` → `/root/.flickr_mcp`).
Photo/contact/group data is stored in `data/flickr.db` (mounted as a volume).

```bash
docker compose build
bin/flickr login      # OAuth flow — requires interactive TTY
bin/flickr status     # Verify session
```

## MCP Server Setup

Run once to configure Claude Code to use the MCP server:
```bash
bin/setup-mcp
docker compose build
```
Then restart Claude Code and run `/mcp` to confirm the `flickr` server is connected.

## Sync Scripts (bin/)

All sync commands run inside the `flickr` Docker service:

```bash
bin/flickr-sync        # Incremental photo sync (--full for full resync)
bin/sync-contacts      # Sync contacts list
bin/sync-groups        # Sync group membership
bin/sync-albums        # Sync album list
bin/sync-engagement    # Sync faves/comments per contact (~20 min, run manually or daily)
```

The MCP server runs a background refresh every 24 hours that runs all syncs including engagement.

## Configuration

- `.env` — API key and secret (`FLICKR_API_KEY`, `FLICKR_API_SECRET`)
- OAuth access tokens: `~/.flickr_mcp/credentials.json` (in the Docker volume, outside the repo)
- SQLite database: `data/flickr.db`

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
| `sync` | Trigger an incremental (or full) photo sync from within MCP |

## Key Implementation Details

- OAuth 1.0a signing is done manually (HMAC-SHA1) via `sign_request()` — no third-party OAuth library
- `_api_get()` / `_api_post()` handle OAuth signing for all Flickr API calls
- `scripts/flickr.py` uses `argparse` subcommands; `load_env()` reads `.env` then falls back to environment variables
- Schema changes must be added to the migrations list in `init_db()` — never use `ALTER TABLE` directly
- `scripts/flickr_oauth.py` and `scripts/flickr_update.py` are earlier standalone scripts (legacy, credentials hardcoded)

## Skills (Claude Code slash commands)

- `/flickr-photo` — process a photo from the current Safari tab: suggest metadata, update, add to groups/albums
- `/flickr-hide` — find weak photos, review visually, make private or update and keep
- `/flickr-sync` — run all sync scripts in order and report results

## Resources

- Flickr API docs: https://www.flickr.com/services/api/
