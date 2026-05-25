# Playwright Examples

Playwright tests and scripts for the Flickr MCP server's web dashboard.

Covers:
- Smoke tests (no login required) — verify the server is up and public pages load
- Interactive OAuth login — drives the browser through Flickr's auth flow and saves a session
- Sync trigger tests — click the sync buttons and confirm they fire

---

## Browser control scripts

Two utility scripts let Claude Code skills read from and navigate your browser
on any platform — replacing the macOS-only `osascript`/Safari approach.

They connect to a running Chrome, Chromium, or Edge instance via the
[Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)
on `localhost:9222`.

### Start your browser with remote debugging

```bash
# macOS
open -a "Google Chrome" --args --remote-debugging-port=9222

# Linux
google-chrome --remote-debugging-port=9222
# or: chromium --remote-debugging-port=9222

# Windows
start chrome --remote-debugging-port=9222
```

You only need to do this once per browser session.  The port persists until you
quit Chrome.

### Get the current tab's URL

```bash
node playwright/scripts/browser-url.js
# → https://www.flickr.com/photos/ejwettstein/54321/
```

Prefers the first open Flickr tab; falls back to the last open tab.

### Navigate a tab to a URL

```bash
node playwright/scripts/browser-open.js https://www.flickr.com/photos/ejwettstein/54321/
```

Reuses the existing Flickr tab (or last tab) and brings it to the front.

### Used by Claude Code skills

The `/flickr-photo`, `/flickr-album`, `/flickr-comment`, `/flickr-hide`, and
`/flickr-contacts` skills call these scripts instead of `osascript`, so they
work on Linux and Windows as well as macOS.

---

## Prerequisites

- **Node.js 20+** for local runs
- **Docker** for containerised runs
- The Flickr MCP server running (see the main README)

---

## Quick start (local)

```bash
cd playwright

# Install dependencies and Chromium
npm install
npx playwright install --with-deps chromium

# 1. Run smoke tests — no login needed
npm test

# 2. Complete OAuth login (opens a browser window; you log in manually on Flickr)
npm run login

# 3. Run sync and stats tests using the saved session
npm test
```

After `npm run login` succeeds, a session file is written to
`playwright/.auth/session.json`.  Subsequent `npm test` runs use it
automatically.

---

## Running in Docker

A `docker-compose.playwright.yml` override adds a headless `playwright` service
that runs against the `flickr-mcp` container and waits for it to be healthy:

```bash
# Start the MCP server first (if not already running)
docker compose up -d

# Run Playwright smoke tests against the running server
docker compose -f docker-compose.yml -f docker-compose.playwright.yml \
  run --rm playwright
```

The service mounts the `playwright/` directory, so test results and any saved
session files are written back to your local working tree.

> **Note:** The interactive OAuth login test (`login.spec.js`) requires a visible
> browser and cannot run headless inside Docker. Complete the login locally
> (`npm run login`) and commit the session file, or mount it into the container.

---

## Test files

| File | Auth required | Description |
|------|:---:|---------|
| `tests/smoke.spec.js` | No | Server health and unauthenticated page checks |
| `tests/login.spec.js` | No | Interactive OAuth flow — run once with `npm run login` |
| `tests/sync.spec.js` | Yes | Navigate to `/sync`, trigger a sync, verify it starts |

`login.spec.js` is excluded from the default `npm test` run and must be
invoked explicitly via `npm run login`.

---

## Authentication

The login test saves browser cookies to `playwright/.auth/session.json` after a
successful Flickr OAuth.  Tests in `sync.spec.js` load this file via Playwright's
`storageState` option so they run as an authenticated user.

`playwright/.auth/session.json` is excluded from git — it contains your
session cookie.  Re-run `npm run login` if the session expires (sessions last
30 days on the server).

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BASE_URL` | `http://localhost:8000` | Base URL of the MCP server |

Set `BASE_URL=http://flickr-mcp:8000` when running inside Docker Compose (the
`docker-compose.playwright.yml` override sets this automatically).
