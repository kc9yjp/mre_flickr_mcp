# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Flickr MCP (Model Context Protocol) server. The server always runs in SSE/web mode — it exposes MCP tools to AI clients over SSE, and provides a web dashboard for login, sync, and stats.

**Architecture:**
- `scripts/flickr_mcp.py` — MCP server + web UI (Starlette/uvicorn, SSE transport)
- `scripts/flickr_sync.py` and `scripts/sync_*.py` — sync scripts invoked by the server
- `scripts/flickr.py` — standalone CLI (legacy, rarely used directly)
- `scripts/vector_search.py` — optional semantic group search (Chroma + `/v1/embeddings`), off by default
- `frontend/` — the workbench's React/TypeScript UI (Chat, Photo Browser, Session Stats, etc.), built to `frontend/dist` and served by `flickr_mcp.py`

## Local Development

```bash
# Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Optional: group semantic search (WORKBENCH_VECTOR_SEARCH_ENABLED=true)
pip install -r requirements-vector.txt

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

## Frontend (`frontend/`)

**Node.js is not installed on this machine/dev environment.** There is no local `node`, `npm`, or `npx` — do not try to run them directly. The frontend is only ever built inside Docker, via the `frontend` stage of the multi-stage `Dockerfile` (`node:22-alpine`, `npm ci && npm run build` → `tsc -b && vite build`).

To typecheck/build the frontend after editing `frontend/src/**` (without doing a full image build), build just that stage:

```bash
docker build --target frontend -t flickr-frontend-check .
docker image rm flickr-frontend-check   # cleanup, it's just a verification build
```

A full `docker compose build` also rebuilds it (as the first stage) and copies `frontend/dist` into the final image — see `Dockerfile`.

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
- `SESSION_COOKIE_SECURE` — set to `true` when running behind something that terminates TLS for the browser (a reverse proxy, or the tailscale sidecar in `docker-compose.yml`), so the session cookie is sent `Secure`. Defaults to `false` to match the documented default of plain `http://localhost:8000`.
- `WORKBENCH_VECTOR_SEARCH_ENABLED` — optional semantic group search, `false` by default. When off, no vector store, no embedding calls, and `chromadb` isn't even imported. See "Group semantic search" below, and `VECTOR_SEARCH.md` for setup plus the companion `WORKBENCH_EMBEDDING_*` / `WORKBENCH_CHROMA_*` variables.

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

The local SQLite caches (`photos`, `groups`, `albums`, `contacts`, and their
join tables) exist for fast, enriched retrieval — search, filtering, ranking,
and listing across the caller's own library — not for looking up one
specific item. They only ever hold the authenticated user's own data, so a
lookup by ID for a photo/group/album that isn't theirs (e.g. another
member's photo in a group pool) will never be in the cache. Tools that fetch
a single known item by ID should call the Flickr API directly (or fall back
to it when the cache misses) rather than treating a cache miss as
"not found" — see `_get_photo` in `scripts/tools/photos.py` for the pattern.

```
photos            — id, title, description, tags, views, favorites, comments,
                    date_taken, date_uploaded, url_photopage, url_original,
                    is_public, reviewed_at, synced_at
contacts          — id, username, realname, is_friend, is_family, synced_at
contact_engagement — contact_id, faves, comments, last_updated
do_not_unfollow   — contact_id, reason, added_at
never_follow      — contact_id, reason, added_at (excluded from follow suggestions)
groups            — id, name, members, pool_count, synced_at, description,
                    user_note, needs_summary, summary_md, is_milestone,
                    fave_min, view_min, open_subject, ai_keywords,
                    summary_generated_at
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
| `get_photo` | Fetch single photo details — local DB for own photos, live API fallback for other users' |
| `get_photo_stats` | Views, favorites, comments for a photo |
| `get_photo_comments` | Fetch comments on a photo |
| `get_unreplied_comments` | Scan recent activity (`flickr.activity.userPhotos`) and return photos with comments the user hasn't replied to yet |
| `fetch_photo_image` | Download photo and return as image for visual inspection |
| `update_photo` | Update title, description, tags (Flickr + local DB) |
| `set_visibility` | Make photo public or private |
| `find_weak_photos` | Rank photos by weakness score (low views, zero faves/comments) |
| `add_comment` | Post a comment on a photo |
| `fave_photo` | Add a photo to the user's favorites |
| `get_photo_faves` | List users who faved a photo, with `you_follow` flag cross-referenced from local contacts DB |
| `find_albums` | Search albums by keyword — matches any individual word against title or description |
| `get_all_albums` | List all albums, optionally sorted by title/photo count/views |
| `get_album_photos` | List photos in an album |
| `add_to_album` | Add photo to an album |
| `remove_from_album` | Remove photo from an album |
| `create_album` | Create a new album |
| `edit_album` | Update album title/description |
| `delete_album` | Delete an album |
| `find_groups` | Search joined groups by keyword; returns a markdown listing (one section per group, headed by its id) with the AI summary, milestone thresholds, and your note. With `WORKBENCH_VECTOR_SEARCH_ENABLED=true`, leftover result slots are filled with semantically similar groups under their own heading (`limit` caps both paths combined) |
| `set_group_note` | Set a personal note about a group, incorporated into its AI summary on the next sync |
| `get_group_stats` | Groups ranked by how many of your photos are in each |
| `get_threshold_groups` | Joined groups with a fave or view count minimum to post, sorted by that threshold |
| `get_photo_group_count` | Photos ranked by how many groups they belong to |
| `add_to_group` | Add photo to a group pool |
| `remove_from_group` | Remove photo from a group pool |
| `get_photo_contexts` | Return group pools and albums a photo belongs to (local DB after sync, API fallback — only for the caller's own photos) |
| `get_group_info` | Live lookup for any group by ID — name, description, rules, member/pool counts, and whether you've joined it |
| `get_contacts_summary` | Total contacts, friends/family count, engagement stats, top engagers |
| `find_unfollow_candidates` | Contacts ranked by lowest engagement (faves + comments) |
| `protect_contact` | Add contact to do-not-unfollow whitelist |
| `unfollow_contact` | Unfollow a contact via API |
| `find_follow_candidates` | People who faved/commented on your photos that you don't follow yet, ranked by engagement |
| `add_to_never_follow` | Permanently exclude a contact from follow suggestions |
| `set_location` | Set photo geolocation (lat/lon) on Flickr |
| `sync` | Trigger an incremental (or full) photo sync from within MCP |

## Key Implementation Details

- **Log liberally to the server log.** Any handler that catches an exception or hits an error path (failed API call, bad connection, save failure, etc.) should `logging.warning`/`logging.error` it with enough context (user nsid, connection/resource id, the exception) to debug from `docker compose logs -f flickr-mcp` alone — don't let errors surface only in a JSONResponse to the client. Follow the existing style, e.g. `logging.warning("llm_models: failed for connection %s (%s): %s", connection_id, user["nsid"], e)` in `routes.py`.
- OAuth 1.0a signing is done manually (HMAC-SHA1) via `_sign()` — no third-party OAuth library
- `_api_get()` / `_api_post()` handle OAuth signing for all Flickr API calls
- Web UI routes live inside `main_sse()` alongside the MCP SSE endpoint
- OAuth login uses a full browser redirect flow: `/login/start` → Flickr → `/oauth/callback`
- Schema changes must be added to the migrations list in `init_db()` — never use `ALTER TABLE` directly
- Groups sync sets `groups.needs_summary=1` whenever a group is new, renamed, has a changed description, or gets a `set_group_note` edit — the next `sync --type=groups` regenerates its AI summary (see ARCHITECTURE.md's "Group AI summary")
- **Group semantic search is strictly opt-in.** Everything in `scripts/vector_search.py` is gated behind `vector_search.enabled()` (`WORKBENCH_VECTOR_SEARCH_ENABLED`, default `false`), `chromadb` is imported lazily inside `get_collection()` so it stays an optional dependency (`requirements-vector.txt`), and no failure there may propagate: an unreachable embedding endpoint or vector store is logged and skipped, leaving groups keyword-searchable as before. Vector state (including the change-detection fingerprint) lives entirely in Chroma, not in `flickr.db` — there is deliberately no migration for it. Backfill after first enabling with `python scripts/sync_groups.py --rebuild-vectors`.
- **Search tools should favor recall over precision.** Split the query into individual words/terms and OR them across all relevant text columns (e.g. title and description) rather than requiring the whole phrase to match one column — a missed match is worse than a few extra results the caller can filter mentally. See `_find_albums` in `scripts/tools/albums.py` and `_find_groups` in `scripts/tools/groups.py` for the pattern.

## Skills (Claude Code slash commands)

- `/flickr-photo` — process a photo from the current browser tab: suggest metadata, update, add to groups/albums
- `/flickr-fave` — suggest a comment with any input given, wait for confirm comment and suggest fave
- `/flickr-hide` — find weak photos, review visually, make private or update and keep
- `/flickr-likes` — review recent faves grouped by owner; mark heavy-fave owners as friends, review light ones for follow
- `/flickr-fans` — review people who fave/comment on your photos but aren't followed; follow, interact, or add to never-follow list
- `/flickr-sync` — trigger syncs via the web UI and report results

## Browser Interaction

When interacting with the browser:
- **Always ask the user** before taking browser-based actions
- **Remember user preferences** across sessions
- **Preferred setup:** macOS Safari withy cli, Chrome DevTools, or something else (suggest)
- Use browser context to enhance photo workflows (e.g., detecting current Flickr page, extracting metadata)

## Resources

- Flickr API docs: https://www.flickr.com/services/api/
