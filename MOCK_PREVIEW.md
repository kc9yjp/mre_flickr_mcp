# Mock UI Preview

A live copy of the workbench frontend running against fake, in-memory data
instead of the real Python backend — for interface-design work (layout,
spacing, copy, interaction flow) where you want to see panels full of
realistic content without a Flickr account, OAuth, an LLM connection, or even
a running server.

Everything here is dev-only tooling. It has zero effect on the real app: a
normal `npm run build` / `docker compose build` never includes any of this
code (see [How it works](#how-it-works)).

---

## Quick start

**VS Code:** Command Palette → *Tasks: Run Task* → **"Podman: Start Mock UI
Preview"**. Then open <http://localhost:5173/app/>.

(First run pulls `npm ci` inside the container, so give it a few seconds —
watch progress with the **"Podman: View Mock UI Preview Logs"** task. Stop it
with **"Podman: Stop Mock UI Preview"**.)

**Command line**, same thing:

```bash
COMPOSE_PROFILES=dev podman-compose up -d frontend-dev
```

Node isn't installed on this machine, so that runs a plain `node:22-alpine`
container (not the Dockerfile's build stage) with `frontend/` bind-mounted
for hot reload and `node_modules` cached in a named volume across restarts.
See the `frontend-dev` service in `docker-compose.yml.example` for the exact
command.

If `podman-compose up` doesn't pick up a code change (a known quirk on this
machine), remove the stale container first:

```bash
podman rm -f mre_flickr_mcp_frontend-dev_1
COMPOSE_PROFILES=dev podman-compose up -d frontend-dev
```

(The VS Code start task already does this `rm -f` defensively every time.)

---

## What you get

- **Photo Browser** — 42 generated photos (varied titles/tags/views/favorites,
  colored placeholder thumbnails — no network dependency), search and
  pagination work against the same in-memory pool
- **Photo Viewer** — click any thumbnail for a full detail view with
  groups/albums
- **Summary, Sync, Queue, Settings, Setup** — plausible stats and rows
- **Models / Prompts** — a demo Ollama connection, a couple of prompt
  categories/prompts
- **Chat is actually usable** — type a message and send it; it streams back a
  canned reply over real SSE framing (not a real model, but it exercises the
  whole streaming UI — deltas, the done event, tool-confirm plumbing, etc.)

It's **stateful, not static**: editing Settings, adding/deleting a Prompt,
adding to the Queue, etc. mutate the in-memory data for the rest of that
browser session, so those flows look and behave like the real thing. State
resets on reload — nothing persists anywhere.

## What you don't get

- Real Flickr images/links, real group pools, real contacts
- Real LLM responses (Chat's replies are canned text, not model output)
- `/login` and `/logout` — there's no backend to redirect to, so avoid the
  Logout button (or just reload the page)
- Anything not covered by a route in `mockApi.ts` — unmapped `GET`s 404, and
  unmapped `POST`s return `{ok: true}` without actually doing anything so
  buttons don't visibly error, they just no-op silently

---

## How it works

`frontend/src/mock/mockApi.ts` patches `window.fetch` to intercept every
`/api/*` call (and `/logout`) and answer from an in-memory route table,
instead of letting it hit a nonexistent backend. Real asset/navigation
requests pass through untouched.

It's wired in via `main.tsx`:

```ts
if (import.meta.env.VITE_MOCK_API === "true") {
  import("./mock/mockApi").then(({ installMockApi }) => {
    installMockApi();
    render();
  });
} else {
  render();
}
```

`VITE_MOCK_API` comes from `frontend/.env.mock`, which Vite only loads when
run with `--mode mock` (the `dev:mock` / `build:mock` npm scripts). Because
the flag is a compile-time constant in a normal build, Vite's bundler
dead-code-eliminates the whole `import()` branch — a real `npm run build`
produces exactly one JS file with no trace of the mock, while `build:mock`
splits it into its own chunk. Confirmed by comparing build output: same main
bundle size either way, `mockApi-*.js` only appears in the mock build.

The route table in `mockApi.ts` is typed against the real interfaces in
`api.ts` (`Photo`, `Stats`, `LLMSettings`, …), several with `satisfies` —
so a response shape drifting out of sync with the real API is a type error
at build time, not a silent bug discovered by staring at a blank panel.

## Files involved

| File | Purpose |
|---|---|
| `frontend/src/mock/mockApi.ts` | The route table + seed data + `window.fetch` patch |
| `frontend/src/main.tsx` | Conditionally installs the mock before rendering |
| `frontend/.env.mock` | Sets `VITE_MOCK_API=true` for `--mode mock` |
| `frontend/src/vite-env.d.ts` | Makes `import.meta.env` typecheck |
| `frontend/package.json` | `dev:mock` / `build:mock` scripts |
| `docker-compose.yml.example` | `frontend-dev` service, profile `dev` |
| `.vscode/tasks.json` | Start/stop/logs tasks |

## Extending it

To mock a new endpoint, add a route in `registerRoutes()`:

```ts
on("GET", "/api/something/:id", (match, params, body) => {
  return json({ ... });
});
```

`match` is the regex match array (`match[1]` is the first `:param`),
`params` is the request's `URLSearchParams`, `body` is the parsed JSON POST
body (or `{}` if none/unparseable). Keep new seed data typed against the
matching interface in `api.ts` — that's the whole point of doing this in
TypeScript instead of a raw JS shim.

## Static build alternative

If you'd rather have a shareable static bundle than a live dev server (e.g.
to serve from anywhere without Docker running):

```bash
podman run --rm -v "$PWD/frontend":/build -w /build node:22-alpine \
  sh -c "npm ci && npm run build:mock"
```

Produces `frontend/dist-mock/` (gitignored). Its `index.html` references
assets by absolute path under `/app/` (same `base: "/app/"` as the real
build), so it needs to be served *from* a directory containing an `app/`
folder, not served directly at the static server's root:

```bash
mkdir -p /tmp/mock-preview/app
cp -r frontend/dist-mock/* /tmp/mock-preview/app/
python3 -m http.server --directory /tmp/mock-preview 8765
# → http://localhost:8765/app/
```

Same mock data, same caveats, just no hot reload.
