# Workbench — status and continuation plan

A VS Code-style multi-panel web UI for this Flickr MCP server, replacing the
two-window workflow (Claude Code terminal + a browser tab on flickr.com) with
one app: a dockable Photo Browser, Summary, Chat (agent), and Command panel,
served from the existing Starlette server at `/app`.

This file is the handoff doc for picking this up on a different machine —
read this first, then dig into the code.

## Status

Milestones M1–M4 are done, plus part of M5. Reference plan (architecture,
milestone breakdown, risks) originally lived at
`~/.claude/plans/my-2-window-solution-greedy-spark.md` on the machine this was
built on — that path is outside the repo and won't follow you to another
computer, so the summary below is now the source of truth going forward.

**Done:**
- **M1 — JSON API**: `scripts/webapi.py` (`/api/me`, `/api/photos`,
  `/api/photos/{id}(+comments|sizes|exif|stats)`, `/api/stats`,
  `/api/sync/status`, `/api/sync/{type}`, `/api/queue`), `url_medium` column
  added to `photos` via the migrations list in `flickr_sync.py` for grid
  thumbnails, CSRF middleware extended with an `X-CSRF-Token` header branch
  for JSON POSTs (`scripts/web.py`).
- **M2 — SPA shell**: `frontend/` (React + TypeScript + Vite + `dockview-react`),
  built to `frontend/dist` and mounted at `/app` in `main_sse()`. Bookmarklet
  ("Send to Workbench") on `/setup` deep-links `#photo={id}` into the app.
  Docker multi-stage build (`Dockerfile`) adds a `node:22-alpine` stage to
  build the frontend before the Python stage copies in `frontend/dist`.
- **M3+M4 — chat agent, shipped together**: `scripts/agent/` — `schema.py`
  (mcp Tool → OpenAI function format, `WRITE_TOOLS` gating 31 of the 64
  tools), `llm.py` (OpenAI-compatible streaming client over httpx, works
  against local Ollama by default), `loop.py` (the agent loop: dispatches
  through `mcp_tools._HANDLERS` exactly like the MCP path does, write tools
  block on an Approve/Deny confirmation sent over the SSE stream), `store.py`
  (conversations in `data/{username}/chat.db`, separate from `flickr.db` so
  `/reset` doesn't wipe chat history), `settings.py` (per-user LLM config in
  `~/.flickr_mcp/{nsid}/llm.json`), `routes.py` (`/api/chat/stream` SSE,
  `/api/chat/confirm`, conversation CRUD, `/api/llm-settings`, `/api/commands`).
  Workflow buttons (`scripts/agent/commands.py`) adapted from the
  `.claude/commands/flickr-*` skills — photo-scoped (improve metadata,
  suggest groups, suggest albums, threshold groups) and global (reply to
  comments, review weak photos).
- **Bug fixes from live testing this session** (see git log on this branch
  for the exact diffs):
  - **Closed panels had no way back.** Dockview panel close was permanent —
    removed from the layout and persisted to `localStorage` with no menu to
    reopen it. Fixed in `frontend/src/App.tsx`: a small view-menu in the
    topbar (Photos / Summary / Chat / Commands) that reopens a closed panel
    or focuses it if already open.
  - **Chat had no idea which photo you were looking at.** Free-form chat
    messages (as opposed to workflow buttons, which fill in `{photo_id}`
    explicitly) had no link to whatever photo was open in the Photo Browser
    — the model would silently fall back to whatever photo id was last
    mentioned in that conversation's stored history, which could easily be a
    different photo than the one currently on screen. Fixed with a bus event
    (`frontend/src/bus.ts`: `photoOpened`) that the Photo Browser emits and
    the Chat panel tracks, sent to the backend as `focused_photo_id` on every
    `/api/chat/stream` call. `scripts/agent/loop.py`'s `run_turn` injects it
    as a **system message scoped to that single LLM call only — never
    persisted to the stored conversation.** That's deliberate: baking
    "photo X is focused" permanently into history would go stale the moment
    you looked at something else and could misdirect a much later turn. An
    explicit photo id/link in your own message still overrides it.
  - **Confirm card showed raw JSON only.** No way to catch a wrong-photo
    write before approving it. `loop.py` now resolves a thumbnail/title
    preview (`_photo_preview_sync`) for any write whose arguments name a
    photo id, included in the `confirm_request` SSE event and rendered in
    `frontend/src/panels/Chat.tsx`'s confirm card.
  - Regression tests for both: `tests/test_agent.py::test_focused_photo_context_is_ephemeral_not_persisted`,
    `::test_confirm_request_photo_preview_populated_for_numeric_id`,
    `::test_confirm_request_includes_photo_preview`.

**Not done — pick up here:**
1. **Vision guard is still a stub, not a real fix.** `loop.py`'s
   `_result_text()` unconditionally replaces any `ImageContent` result with
   the literal string `"(image fetched — vision support not enabled yet)"` —
   there is no `vision` field in `settings.py` at all, and no branching logic.
   Meanwhile `scripts/agent/commands.py`'s photo workflow prompts
   (`improve-photo`, `suggest-groups`, `suggest-albums`) explicitly instruct
   the model to describe "mood and subject" after calling
   `fetch_photo_image` — so those workflows currently **confabulate a photo
   description every time**, which is exactly the failure mode that
   triggered this investigation (see conversation history / commit log for
   the reproduction). Needed:
   - Add `vision: bool` (default `false`) to `settings.py` DEFAULTS, exposed
     in the LLM settings UI with a warning about hallucination risk.
   - In `loop.py`, only attach real image data (as an `image_url` message
     part) when `cfg["vision"]` is true; otherwise keep today's placeholder
     text but make it explicit that the model must not guess — e.g. "no
     visual content was provided; work from title/description/tags/EXIF
     only, or tell the user vision is unavailable."
   - Add a standing rule to `SYSTEM_PROMPT` forbidding the model from
     claiming to have seen an image it wasn't given.
   - Regression test: assert no `image_url` part is ever emitted when
     `vision` is false, and that the tool-result text carries the explicit
     disclaimer.
2. **M5 remainder**: Ctrl/Cmd-K command palette, an "approve all writes this
   conversation" toggle, conversation-management UI beyond the basic
   dropdown switcher, browser-extension upgrade of the bookmarklet.
3. Nothing yet exercises the confirm-card photo preview or focused-photo
   context against a *real* LLM end-to-end (only against the scripted fake in
   tests, and manually against local Ollama) — worth a manual pass after any
   further agent-loop changes.

## Running it

```bash
docker compose build
docker compose up -d
```

then visit `http://localhost:8000/login`, log in via Flickr OAuth, and open
`http://localhost:8000/app`.

**Gotcha hit this session**: if you `cd` into a worktree checkout of this
branch and run plain `docker compose build`/`up`, Compose derives the project
name from the *directory* name (e.g. `workbench-m1-api`), which is a
**different Compose project** from whatever's already running under the
repo's usual project name (`mre_flickr_mcp`) — you'll get a second, separate
container instead of replacing the one you meant to update, and it may fail
to bind port 8000 if the original is still up. Pin it explicitly:

```bash
docker compose -p mre_flickr_mcp build flickr-mcp
docker compose -p mre_flickr_mcp up -d flickr-mcp
```

Frontend has its own dev loop if you're iterating on it directly:

```bash
cd frontend && npm install && npm run dev   # vite dev server on :5173, proxies /api to :8000
```

For a production check before committing frontend changes:

```bash
cd frontend && npx tsc --noEmit && npm run build
```

## Testing

```bash
pytest
```

125 tests as of this commit, covering the JSON API, the agent loop (schema
conversion, streaming parse, read/write/confirm/deny paths, the
focused-photo and photo-preview behavior above), and the existing MCP/tool
suite unchanged from `main`.
