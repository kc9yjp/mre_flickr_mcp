# design-sync notes — flickr-workbench

## Repo shape (read before re-running)

`frontend/` is an **application** (a Vite SPA, `flickr-workbench`), not a
design-system component library — no Storybook, no `.d.ts` build output, no
`main`/`module`/`exports` in `package.json`. This sync uses the package
shape's "synthesize an entry from `src/`" last-resort path, not a real
`dist/` build. That's a structural mismatch this skill isn't built for; the
user explicitly chose to proceed anyway. Scope was narrowed hard from "every
exported component" (21) down to 7 genuinely reusable, self-contained UI
pieces — see `componentSrcMap` nulls in `config.json` for the excluded 14.

**Environment**: this machine has no local Node.js at all (see repo
CLAUDE.md). The entire sync ran inside a Docker container
(`node:22-bookworm` — NOT `node:22-alpine`, Playwright's bundled Chromium
doesn't run on musl) with `/repo` bind-mounted to the repo root, started via:

```
docker run -d --name design-sync-node -v "<repo>:/repo" -w /repo node:22-bookworm sleep infinity
```

then `docker exec -w /repo design-sync-node <cmd>` for every build/validate/
capture/upload step (MSYS_NO_PATHCONV=1 needed for every docker command with
container-side path arguments — Git Bash mangles them otherwise). `npm ci`
was run fresh inside the container for both `frontend/` and `.ds-sync/`
(host `frontend/node_modules` has Windows-native binaries, e.g. esbuild,
that don't run in Linux). Re-syncing needs the same container setup —
recreate it if `design-sync-node` isn't already running.

**On a Linux box with native Node the container is unnecessary** — a
claude.ai/code session runs the whole sync directly (Node 22 at
`/opt/node22`). The Docker recipe above is a *Windows-host* workaround, not
a requirement of this sync. Two environment facts that matter there:
Chromium is pre-cached at `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`
(build `chromium-1194`), which pins **`playwright@1.56.0`** — install that
exact version into `.ds-sync/`, since a mismatched release fails with
`browserType.launch: Executable doesn't exist`. And a fresh clone needs the
fork symlink recreated (`ln -sfn ../.ds-sync/node_modules
.design-sync/node_modules`) because `overrides/source-kit.mjs` imports
`ts-morph` by bare name.

## Converter invocation (read before re-running — the build hard-fails without this)

`frontend/` IS the DS package (`flickr-workbench`), so the converter's
default `PKG_DIR = <node-modules>/<pkg>` points at
`frontend/node_modules/flickr-workbench`, which npm never creates (a package
doesn't self-install). Without it the build dies immediately:

```
Error: ENOENT: ... open '.../frontend/node_modules/flickr-workbench/package.json'
    at projectFor (lib/dts.mjs) ← exportedNames ← package-build.mjs
```

Fix — create the self-link after every `npm ci` (it lives inside gitignored
`node_modules`, so it does NOT survive a fresh install or clone):

```sh
ln -sfn .. frontend/node_modules/flickr-workbench
```

It does double duty: it's also how esbuild resolves the previews' own
`import { … } from "flickr-workbench"`.

**Do NOT "fix" this by passing `--entry`.** `--entry` looks like the
documented answer for a package that isn't in `node_modules` (skill §7), and
it does resolve `PKG_DIR` — but in the package shape `resolveDistEntry()`
returns the override *as the bundle entry*, which silently bypasses the
synth-from-`src/` path this repo depends on and bundles one file instead of
sweeping all 21. There is no dist here; the entry must stay synthesized.

The full re-sync command, from the repo root (no `--entry`):

```sh
node .ds-sync/resync.mjs --config .design-sync/config.json \
  --node-modules frontend/node_modules --out ./ds-bundle \
  --remote .design-sync/.cache/remote-sync.json
```

A healthy run prints `[NO_DIST] no built entry — synthesizing from 21 src
files` — that line is expected here, not a failure to chase.

## The 14 screens are now IN (was: excluded) — how they work

A previous sync excluded all 14 page-level components via
`componentSrcMap: null`, on the grounds that they self-fetch and render
error text. That exclusion has been removed; all 21 components now sync.
Two facts made it straightforward, both worth knowing before touching this:

- **They were always in the bundle.** The exclusion only removed them from
  the *component list* (no card, no `.d.ts`, no `.prompt.md`). The synth
  entry sweeps all 21 `src` `.tsx` files regardless, so
  `window.FlickrWorkbench` has exposed `Chat`, `PhotoBrowser`, `App` &c. the
  whole time — the design agent simply had no contract or card for them.
- **The network is a single choke point.** Every panel's data goes through
  `getJSON` in `api.ts` (5 `fetch(` call sites total). One `window.fetch`
  stub feeds any screen, so the previews supply *data* and the real shipped
  component still renders — no reimplementation.

`.design-sync/previews/_fixtures.ts` is that stub plus the fixture set. It is
deliberately **`.ts`, not `.tsx`**: the converter's stale-preview scan only
reads `.tsx` in `previews/`, so a helper module is invisible to it while
esbuild still bundles it (previews compile with `bundle: true`).

Every panel takes **zero props** (`export function Chat()`); only
`MobileLayout` takes one (`me`). Their `.d.ts` contracts are therefore empty
by nature — that is accurate, not a extraction failure. It also means the
design agent can place and restyle a screen but cannot parameterize its
content; making that possible would require refactoring the panels to accept
data instead of self-fetching, which is app work, not sync work.

## Fixture gotchas (each one cost a debugging cycle)

- **Timestamps must be relative to render time.** The panels render epochs
  through `format.ts`'s `relativeTime()`, so a hard-coded 2024 epoch renders
  as "680 d ago". `_fixtures.ts` derives everything from `Date.now()`.
- **`package-capture.mjs` pins the browser clock** to `2024-05-15T12:00:00Z`
  (`page.clock.setFixedTime`, line ~102) for deterministic screenshots, while
  `package-validate.mjs` uses the real clock. Relative fixtures are correct
  under both; this is also why absolute dates in captures read as 2024 and
  why render hashes stay stable despite `Date.now()` in the fixtures. Not a
  bug — do not "fix" it.
- **`SetupPage` looks up snippets by `CLIENT_TABS` id**, not display label —
  `claude_code`, `claude_desktop`, `cursor`, `windsurf`, `opencode`, `stdio`
  (plus optional `claude_code_sse` for the legacy disclosure). A mismatched
  key renders an empty snippet box, silently.
- **`Command` and `CommandPalette` read `/api/commands`**, which is separate
  from `/api/prompts`. `Command` filters to `context: "global"`, a photo's
  detail view takes `"photo"` — the fixture needs both or a section is empty.
- **`Chat` needs its conversation selected.** It loads messages only on
  selection, and the list arrives asynchronously, so setting the `<select>`
  on mount is a silent no-op (no matching `<option>` yet). The preview polls
  via `whenReady()` and then drives the real selector with a native-setter
  `change` event, since React tracks its own value.
- **`PhotoViewer`** takes its photo from the `#photo=<id>` deep link (the
  bookmarklet's own path), which is simpler than poking the bus.

## dockview CSS must be routed in explicitly

`main.tsx` is the only file that does `import "dockview/dist/styles/dockview.css"`,
and the source-kit fork excludes `main.tsx` — so dockview's stylesheet never
reached the bundle. The shell rendered unstyled: tabs as bare text, and the
layout collapsed to one squashed panel. Fixed with

```json
"tokensPkg": "dockview",
"tokensGlob": "dist/styles/dockview.css"
```

which copies it to `tokens/` and `@import`s it from `styles.css` ahead of the
app's own CSS. **This is not preview-only cosmetics** — designs receive just
the `styles.css` import closure, so without it every design built with the
shell renders unstyled. Same failure family as the `cssEntry` note above:
anything `main.tsx` alone imported is invisible to the build.

`App` additionally needs an explicit height in a preview: `styles.css` sizes
the shell with `html, body, #root { height: 100% }`, and the preview mounts
outside `#root`, so the chain breaks and dockview measures a zero-height
container. Its preview wraps it in `<div style={{ height: "100vh" }}>`.

## Known fixes baked into the fork/config (don't rediscover these)

- **`.design-sync/overrides/source-kit.mjs`** (declared in
  `cfg.libOverrides`): the synth-entry sweep (`export * from <every src
  file>`) originally included `frontend/src/main.tsx`, which has top-level
  DOM-mounting side effects (`ReactDOM.createRoot(...).render(<App/>)`).
  That throws when the bundle IIFE evaluates outside a real `#root`
  element, which took down **every** export in the bundle, not just App's
  (`[BUNDLE_EXPORT]` flagged all 21 as missing). Fixed by excluding
  `main.tsx` from the sweep. The same fork also adds `export { default as
  Name }` lines for files with a recoverable default export — plain
  `export *` never re-exports a default (ES module semantics), so `App`
  (a `export default function App()`) silently vanished from
  `window.FlickrWorkbench` until this was added.
- **`cfg.cssEntry: "src/styles.css"`**: needed explicitly because excluding
  `main.tsx` (which is the only file that does `import "./styles.css"`)
  also removed the only route by which the bundler's entry-graph scan would
  have found the stylesheet ([CSS_RUNTIME] fired without this).
- **Theme bootstrap in every preview**: the real app applies
  `data-theme="slate"` via `theme.ts`'s `applyTheme()`, called from
  `main.tsx` before first paint. `theme.ts` is a plain `.ts` file (not
  `.tsx`), so it's outside the synth sweep too (`SRC_IMPL_RX` only matches
  `.tsx`/`.jsx`) — `import { applyTheme } from "flickr-workbench"` in a
  preview throws `is not a function`. Every preview instead sets the
  attribute directly: `document.documentElement.setAttribute("data-theme",
  "daylight")`. Without it, `--ink` falls back to `:root`'s bare (Slate/dark)
  value — near-white text on the preview card's forced-white background,
  reading as nearly invisible. `daylight` was picked (not `slate`) precisely
  because the card background is always white.
- **`FlickrLinkMenu`/`ThemeMenu`/`UserMenu` "Open" stories**: these
  components' dropdown panels are `position: absolute; right: 0` relative to
  their own `position: relative` wrapper. In the real app that wrapper sits
  inside `.topbar-right` (`display: flex`, right-aligned near the edge of a
  wide topbar), giving the panel room to open leftward. A bare preview
  wrapper puts the button at the left of an unconstrained-width card, so the
  panel either escapes off the right edge (no width constraint) or clips off
  the left edge (button flush left, panel has no room to its left). Fixed
  by wrapping in `<div className="topbar-right" style={{display:"flex",
  justifyContent:"flex-end", width:340}}>`. These three also need
  `cardMode: "column"` (`[GRID_OVERFLOW]`) since the fixed 340px width
  exceeds a normal grid cell.
- **`CommandPalette`**: `cardMode: "single", viewport: "560x460"` — it's a
  `position: fixed; inset: 0` overlay, needs a bounded viewport rather than
  escaping the card. Its "Open"/"Filtered" stories dispatch a real
  `Ctrl+K` `KeyboardEvent` (its open state is internal, no prop) rather than
  reimplementing the state; `useWorkflowCommands` swallows fetch failures
  (`.catch(() => {})`), so the sandboxed preview shows only the static
  panel list, no live-API error text — safe to preview as-is.

## Known render warns (already triaged — not new if seen again)

None currently — final validate run was fully clean (0 bad, 0 thin, 0
variantsIdentical) after the fixes above.

- `[RENDER_SKIPPED] render check did not run` — **expected on a no-change
  re-sync**, not a regression. The driver scopes the render check by what
  ships: nothing to upload → skipped entirely (the `[SYNC_STALE]` and
  file-shape checks still run, and validate still exits 0). Force the full
  visual pass with `--render-sample 0` if you actually want the screenshots.

## Re-sync risks

- **`componentSrcMap` is now empty** — every PascalCase `.tsx` export is a
  component. A new file under `src/` or `src/panels/` is synced
  automatically, with no card review, and a new *panel* will ship a
  misleading error-text card unless someone also writes its fixture preview.
  Watch the `components: N` count in the build log for unexplained growth.
- **The fixtures mirror `api.ts` by hand.** `_fixtures.ts` is shaped from
  `Stats`, `SyncStatus`, `QueueData`, `SetupData`, `PromptsData`,
  `LLMSettings`, `WireMessage` &c. Nothing checks it against those types —
  it is plain data, and the previews compile with esbuild (no typecheck). If
  a backend response shape changes, the affected card quietly degrades to a
  partial or empty render rather than failing the build. Re-read the changed
  interface and update the fixture.
- **Screen cards can pass the mechanical gate while being wrong.** The render
  check only fails an EMPTY root, and these panels render *non-empty* error
  text ("Not Found", "Could not load stats"). That is exactly how they slipped
  past before. Grep the rendered `texts` in `.render-check.json` for that
  wording after any fixture change — a green render check is not sufficient
  evidence for this repo.
- **The `main.tsx` exclusion and default-export re-export fix are file-path
  specific** (`(^|\/)main\.tsx$`) — if the app ever gains a second
  side-effecting entry file (e.g. a service worker registration script
  under `src/`), it would need the same treatment.
- **Synth-entry mode has no `.d.ts` to verify against** — the "authoritative
  component list" here is a heuristic PascalCase-export scan of `.tsx`/
  `.jsx` files, not a real published API surface. A real library build
  (`main`/`module`/`exports` in `package.json`) would be materially more
  reliable if this app ever grows a proper component-library layer.
- **Theme choice (`daylight`) is a fixed, arbitrary pick** among 5 supported
  themes, chosen only because the preview card background is hardcoded
  white. If the product's card background ever becomes theme-aware, this
  should be revisited.
- **Docker container `design-sync-node` is not persistent infrastructure**
  — it's a manually-started container for this sync session. A future
  re-sync on the Windows host needs to recreate it (see "Environment"
  above) unless it's still running; on a native-Node Linux host, skip it.
- **The `flickr-workbench` self-link is machine state, not repo state** —
  it lives in gitignored `node_modules` and is destroyed by every `npm ci`
  and every fresh clone, while the failure it prevents looks like a
  converter bug rather than a missing symlink. See "Converter invocation".
