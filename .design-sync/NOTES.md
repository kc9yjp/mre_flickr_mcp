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

## Why 14 components are excluded (`componentSrcMap: null`)

- `App`, `MobileLayout` — app shells that mount the full panel set
  unconditionally; their default render bakes in live-API error text
  ("Not Found", "Could not load stats") from panels that need a real
  authenticated Flickr session.
- `Chat`, `Command`, `ModelsPage`, `PhotoBrowser`, `PhotoViewer`,
  `PromptsPage`, `PromptsSection`, `QueuePage`, `SettingsPage`, `SetupPage`,
  `Summary`, `SyncPage` — page-level panels, same reason: they self-fetch
  from the live backend and have no meaningful standalone render. Not floor
  cards (the mechanical floor-card fallback only triggers on an EMPTY
  render root — these render real, non-empty, but misleading error text) —
  they had to be excluded from the component list entirely.

If any of these ever need syncing, it would require mocking `api.ts`'s
fetch layer per component — out of scope for "compose realistic props",
arguably reimplementation. Left as future work only if requested.

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

- **The `componentSrcMap` exclusion list is manual and content-based**, not
  structural — if a currently-excluded panel (e.g. `Summary`) is ever
  refactored to accept props/mock data instead of self-fetching, nothing
  will automatically re-include it; someone has to notice and edit
  `config.json`.
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
