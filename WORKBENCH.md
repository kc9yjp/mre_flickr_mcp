# Workbench — status and continuation plan

A VS Code-style multi-panel web UI for the Flickr MCP server, replacing the
two-window workflow (Claude Code terminal + a browser tab on flickr.com) with
one app: dockable Photo Browser, Summary, Chat (agent), Commands, Sync, Queue,
Setup, and Settings panels, served from the existing Starlette server at `/app`.

## Status

M1–M5 are complete. The only leftover items are two stretch features from the
original M5 plan (Ctrl/Cmd-K palette, browser extension).

**Done — M1–M4:**
- **M1 — JSON API**: `scripts/webapi.py` — `/api/me`, `/api/photos`,
  `/api/photos/{id}` (+ comments/sizes/exif/stats), `/api/stats`,
  `/api/sync/status`, `/api/sync/{type}`, `/api/queue`, `/api/setup`,
  `/api/reset`, `/api/settings`, `/api/regen-key`. CSRF middleware extended
  with an `X-CSRF-Token` header branch for JSON POSTs.
- **M2 — SPA shell**: `frontend/` (React + TypeScript + Vite + `dockview-react`),
  built to `frontend/dist` and mounted at `/app`. Bookmarklet deep-links
  `#photo={id}` into the app. Docker/Podman multi-stage build handles the
  Node build step automatically.
- **M3+M4 — chat agent**: `scripts/agent/` — `schema.py` (MCP Tool → OpenAI
  function format, `WRITE_TOOLS` gating), `llm.py` (OpenAI-compatible streaming
  client, works with local Ollama by default), `loop.py` (agent loop: dispatches
  through `mcp_tools._HANDLERS`, write tools block on Approve/Deny confirmation
  over SSE), `store.py` (conversations in `data/{username}/chat.db`),
  `settings.py` (per-user LLM config in `~/.flickr_mcp/{nsid}/llm.json`),
  `routes.py` (`/api/chat/stream` SSE, `/api/chat/confirm`, conversation CRUD,
  `/api/llm-settings`, `/api/commands`).
- Workflow buttons (`scripts/agent/commands.py`) adapted from the
  `.claude/commands/flickr-*` skills: photo-scoped (improve metadata, suggest
  groups/albums, threshold groups) and global (reply to comments, review weak
  photos).
- Bug fixes: panels reopen from a View ▾ dropdown; Chat tracks which photo is
  open in the Photo Browser (ephemeral system message, not persisted); confirm
  card shows a photo thumbnail/title preview for any write targeting a photo id.

**Done — M5:**
- Vision guard: `cfg["vision"]` gates image data in tool results; SYSTEM_PROMPT
  has an explicit prohibition; `_result_content()` in `loop.py` replaces image
  data with a disclaimer when vision is off.
- Renamed to **"Mr. E's Photo Workbench"**
- Font size 14px → 16px
- View ▾ dropdown replaces old per-panel reopen buttons
- Auto-approve ⚡ toggle: skips confirm card, immediately approves write tools
- Delete conversation 🗑 button next to conversation dropdown
- Editable base prompt in LLM Settings: persisted to `llm.json`, injected as a
  second system message in each agent turn
- **Mobile layout**: `useIsMobile` hook switches to a two-pane layout at <768px.
  Portrait: chat top or bottom (toggle button, remembers via localStorage).
  Landscape: chat left, content right (CSS orientation query). Content panel has
  a single `<select>` to switch between all pages, defaulting to Stats and
  remembering the last choice.
- **Classic pages ported to React panels**: Sync, Queue, Setup, Settings — each
  backed by a JSON API in `webapi.py`. Setup panel includes "Regenerate API Key"
  button. `/` redirects to `/app` for logged-in users, `/login` otherwise.
- **Old server-rendered pages removed**: `route_stats`, `route_sync_page`,
  `route_sync_status`, `route_sync_trigger`, `route_reset_db`, `route_regen_key`,
  `route_queue`, `route_settings`, `route_setup`, `_require_login` all deleted
  from `web.py`. Templates `home.html`, `stats.html`, `sync.html`, `queue.html`,
  `setup.html`, `settings.html` deleted. Only `login.html` and `base.html` remain.

**Done — M5 remainder:**
- **Ctrl/Cmd-K command palette**: searches panels and global workflow commands;
  keyboard nav (↑↓ Enter Esc); opens panels via dockview on desktop, switches
  the panel selector on mobile via `switchPanel` bus event. Panel definitions
  extracted to `frontend/src/panelDefs.ts` shared between App and palette.
- **Browser extension**: `GET /api/extension` generates a MV3 Chrome/Edge zip
  with the server URL baked in. Popup opens the current Flickr photo page in
  the Workbench with one click. Download button in the Setup panel.
  Install: unzip → Chrome `chrome://extensions` → Developer mode → Load unpacked.
- **`remember` pseudo-tool**: typing "remember X" or "memory: X" in chat calls
  a `remember` tool that appends the guidance to `base_prompt` in `llm.json`,
  taking effect on the next turn. No confirm gate.

**Nothing left to do.** M1–M5 are complete.

## Running it

```bash
podman-compose build
podman rm -f mre_flickr_mcp_flickr-mcp_1
podman-compose up -d
```

Then visit `http://localhost:8000/login`, log in via Flickr OAuth, and open
`http://localhost:8000/app`.

**Frontend dev loop** (iterating on UI without rebuilding the container):

```bash
cd frontend && npm install && npm run dev   # Vite dev server on :5173, proxies /api to :8000
```

**Pre-commit frontend check:**

```bash
cd frontend && npx tsc --noEmit && npm run build
```

## Testing

```bash
pytest
```

128 tests as of the last commit: JSON API, agent loop (schema conversion,
streaming parse, read/write/confirm/deny paths, focused-photo context,
photo preview, vision guard), and the MCP/tool suite.
