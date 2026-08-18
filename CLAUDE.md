# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Flickr MCP (Model Context Protocol) server. The server always runs in SSE/web mode — it exposes MCP tools to AI clients over SSE, and provides a web dashboard for login, sync, and stats.

**Architecture:**
- `scripts/flickr_mcp.py` — MCP server + web UI (Starlette/uvicorn, SSE transport)
- `scripts/flickr_sync.py` and `scripts/sync_*.py` — sync scripts invoked by the server
- `scripts/flickr_oauth.py` — legacy terminal OAuth flow, superseded by the browser login at `/login` (there is no longer a standalone `flickr.py` CLI)
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

**Interface-design work without a running backend:** see [MOCK_PREVIEW.md](MOCK_PREVIEW.md) — a live Vite dev server (or static build) against fake, in-memory API data instead of the real Python server. VS Code task "Podman: Start Mock UI Preview", or `COMPOSE_PROFILES=dev podman-compose up -d frontend-dev` → `http://localhost:5173/app/`.

## Web UI

Visit `http://localhost:8000` after starting the container:

| Page | Purpose |
|------|---------|
| `/login` | Browser-based Flickr OAuth login (no terminal paste) |
| `/app` | **Mr. E's Photo Workbench** — dockable panels for Photo Browser, Sync (status/trigger, Reset Database), Stats/Summary, Setup (personal MCP config snippet), Chat, Queue, and Settings |

`/` redirects to `/app` for logged-in users, `/login` otherwise. The classic
server-rendered `/stats`, `/sync`, `/setup` pages were removed when the
Workbench SPA replaced them — see [ARCHITECTURE.md](ARCHITECTURE.md#the-workbench).

Logs go to stderr and are visible via `docker compose logs -f flickr-mcp`.

## MCP Server Setup

1. Build and start: `docker compose up -d`
2. Log in at `http://localhost:8000/login` via Flickr OAuth
3. Open `http://localhost:8000/app`, go to the Setup panel, for your personal `.mcp.json` config snippet
4. Add it to your project or global `~/.claude/mcp.json`
5. Restart Claude Code and run `/mcp` to confirm the `flickr` server is connected

## Configuration

- `.env` — Flickr app credentials only: `FLICKR_API_KEY`, `FLICKR_API_SECRET`
- `MCP_API_KEY` env var is **no longer used** — each user gets a personal API key generated on first login
- OAuth access tokens + personal API key: `~/.flickr_mcp/{nsid}/credentials.json` (in the `flickr-creds` Docker volume)
- SQLite database: `data/{username}/flickr.db` (in the `flickr-data` volume)
- `SESSION_COOKIE_SECURE` — set to `true` when running behind something that terminates TLS for the browser (a reverse proxy, or the tailscale sidecar in `docker-compose.yml`), so the session cookie is sent `Secure`. Defaults to `false` to match the documented default of plain `http://localhost:8000`.
- `TS_SERVE_MODE` — controls the tailscale sidecar's exposure: `serve` (default) is tailnet-private; `funnel` also exposes the app to the public internet over the same hostname/TLS cert. `funnel` is required for the Desktop/claude.ai "Connectors" OAuth flow (see `scripts/oauth2.py`) — claude.ai's own backend brokers that handshake and calls your server's `.well-known`/`/oauth/*` endpoints itself, from outside your tailnet, which a tailnet-private `serve` can never satisfy. It exposes the *whole* app, not just the MCP/OAuth endpoints — `tailscale/serve-config.json` proxies `/` wholesale — and separately requires granting this node the `funnel` attribute in your tailnet's admin console (off by default even with the config flag set). See `tailscale/funnel-config.json`.
- `CLOUDFLARE_TUNNEL_TOKEN` — required by the `cloudflared` sidecar (`docker-compose.yml`), an alternative to the tailscale funnel above for public exposure, fronted by Cloudflare's edge (WAF, DDoS protection, traffic analytics). Get a token from the Cloudflare Zero Trust dashboard (Networks → Tunnels → Create a tunnel → Docker). The container needs a token to start — with none set it fails and restart-loops — so start it explicitly (`docker compose up -d cloudflared`) rather than folding it into your regular `up -d`. It always forwards to `http://localhost:8000` (the whole app, sharing `flickr-mcp`'s network namespace); which paths are actually publicly reachable is controlled entirely by the tunnel's Public Hostname rules in the Cloudflare dashboard, not by anything in this repo — scope those to `/mcp`, `/sse`, `/messages` to expose only the MCP endpoints.
- `WORKBENCH_VECTOR_SEARCH_ENABLED` — optional semantic group search, `false` by default. When off, no vector store, no embedding calls, and `chromadb` isn't even imported. See "Group semantic search" below, and `VECTOR_SEARCH.md` for setup plus the companion `WORKBENCH_EMBEDDING_*` / `WORKBENCH_CHROMA_*` variables.

## Multi-User Support

The server supports multiple independent Flickr accounts:

- Each user authenticates via Flickr OAuth at `/login`
- A personal MCP API key is generated automatically on first login and shown in the Workbench's Setup panel (`/app`)
- Each user has an isolated SQLite database (`data/{username}/flickr.db`) and credentials dir (`~/.flickr_mcp/{nsid}/`)
- Sessions last 30 days; logout clears the session but preserves credentials and database
- Users can reset (delete) their own database from the Workbench's Sync panel — a fresh sync recreates it
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
pending_group_adds — queue for rate-limited or scheduled group-pool adds (see ARCHITECTURE.md's "Group-add queue")
settings          — per-user key/value overrides
keeper_list       — photo_id, note (photos flagged as worth keeping despite weak stats)
```

## MCP Tools

69 tools across `scripts/tools/{photos,albums,groups,contacts,galleries,sync}.py`.
See **[TOOLS.md](TOOLS.md)** for the full catalog with parameters — kept
there rather than duplicated here so there's one place to update when tools
change. A few worth knowing up front:

- `get_photo` / `get_photo_contexts` / `get_group_info` — local-DB-first with
  a live API fallback, so they work for photos/groups outside the caller's
  own library (see "Database Schema" below).
- `find_albums` / `find_groups` — keyword search over the local cache,
  favoring recall (see "Key Implementation Details" below); `find_groups`
  also gains an optional semantic path when `WORKBENCH_VECTOR_SEARCH_ENABLED=true`.
- `sync` — trigger an incremental (or full/backfill) sync from within MCP.

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
- `/flickr-album` — suggest and add the current browser-tab photo to matching albums
- `/flickr-comment` — fave the current browser-tab photo and suggest a short comment to post
- `/flickr-award` — post a group's award-comment template to photos as the user reviews them in the browser
- `/flickr-boost` — find photos qualifying for view/fave-count threshold groups and add 1-2 per group per session
- `/flickr-hide` — find weak photos, review visually, make private or update and keep
- `/flickr-unearth` — review private photos oldest-first and decide which to publish
- `/flickr-likes` — review recent faves grouped by owner; mark heavy-fave owners as friends, review light ones for follow
- `/flickr-fans` — review people who fave/comment on your photos but aren't followed; follow, interact, or add to never-follow list
- `/flickr-contacts` — review followed contacts one at a time as unfollow candidates, ranked by lowest engagement
- `/flickr-thanks` — review recent comments on your photos and reply to any that haven't been replied to yet
- `/flickr-sync` — trigger syncs via the Workbench and report results

## Browser Interaction

When interacting with the browser:
- **Always ask the user** before taking browser-based actions
- **Remember user preferences** across sessions
- **Current mechanism:** the `.claude/commands/flickr-*` skills use one of
  two ways to read/drive the browser: `node playwright/scripts/browser-url.js`
  / `browser-open.js <url>` (see `playwright/README.md`), or direct
  AppleScript targeting Safari/Chrome (`osascript -e 'tell application
  "Safari" to ...'`) — check the specific skill file for which it uses
- Use browser context to enhance photo workflows (e.g., detecting current Flickr page, extracting metadata)

## Resources

- Flickr API docs: https://www.flickr.com/services/api/
