## Scope: an app, in two layers — chrome and whole screens

This is a single-page app (a Flickr workbench), not a general component
library, and it ships in two groups:

- **`general`** — reusable chrome: dropdown menus (`UserMenu`, `ThemeMenu`,
  `FlickrLinkMenu`), an id chip (`PhotoId`), a command palette
  (`CommandPalette`), two text renderers (`Markdown`, `SafeHtml`), and the
  two app shells (`App`, `MobileLayout`).
- **`panels`** — the screens themselves: `Chat`, `PhotoBrowser`,
  `PhotoViewer`, `Summary`, `SyncPage`, `QueuePage`, `SettingsPage`,
  `SetupPage`, `ModelsPage`, `PromptsPage`, `PromptsSection`, `Command`.

Compose chrome freely. Screens are whole panels — place one, don't try to
build one out of parts.

## Setup: set a theme attribute before mounting anything

Every component reads CSS custom properties (`--ink`, `--surface`,
`--border`, `--accent`, …) that only resolve correctly once a theme is
selected. Set it on the document root **before first paint**:

```js
document.documentElement.setAttribute("data-theme", "daylight");
```

Valid values: `slate` (dark, default), `dracula` (dark), `daylight` (light),
`paper` (light), `mist` (light). **Skipping this is not a safe no-op** — the
bare `:root` fallback is Slate's dark palette (`--ink: #eef0f3`, near-white),
so plain text becomes nearly invisible against a light page background.
Pick a light theme (`daylight`/`paper`/`mist`) when composing on a white
canvas, a dark one otherwise.

## Screens take no props — they fetch their own data

Every component in `panels` is declared `export function Chat()` — no props
at all (the sole exception is `MobileLayout`, which takes `me`). They load
themselves from the app's JSON API on mount via `getJSON`. Two consequences:

- **You cannot pass content in.** There is no `items`/`data`/`photos` prop to
  vary. To change what a screen shows, change what the endpoints answer.
- **Unanswered requests render error text, not an empty state.** A screen
  dropped into a page with no backend shows literal `Not Found` / `Could not
  load stats`, which looks like a broken design.

So stub `window.fetch` before mounting a screen:

```tsx
const DATA = {
  "/api/stats": { total_photos: 4187, total_views: 1284630, total_groups: 96,
    total_albums: 48, total_contacts: 512, public_photos: 3902,
    private_photos: 285, date_range: { earliest: "2006-04-11", latest: "2024-09-18" },
    last_synced: Math.floor(Date.now() / 1000) - 7200, top_tags: [{ tag: "landscape", count: 812 }] },
  "/api/sync/status": { running: false, rows: [] },
};
window.fetch = (async (input) => {
  const path = String(typeof input === "string" ? input : (input as Request).url)
    .split("?")[0].replace(/^https?:\/\/[^/]+/, "");
  return new Response(JSON.stringify(DATA[path] ?? { ok: true }),
    { status: 200, headers: { "Content-Type": "application/json" } });
}) as typeof fetch;

<Summary />;
```

Endpoints by screen: `Summary` → `/api/stats` + `/api/sync/status`;
`SyncPage` → those plus `/api/llm-settings`; `PhotoBrowser` → `/api/photos`,
`/api/albums`; `PhotoViewer` → `/api/photos/{id}` (and reads `#photo=<id>`);
`QueuePage` → `/api/queue`; `SettingsPage` → `/api/settings`; `SetupPage` →
`/api/setup` (snippet keys must be `claude_code`, `claude_desktop`, `cursor`,
`windsurf`, `opencode`, `stdio`); `PromptsPage`/`PromptsSection` →
`/api/prompts`; `ModelsPage` → `/api/llm-settings`; `Command` and
`CommandPalette` → `/api/commands`; `Chat` → `/api/chat/conversations`,
`/api/chat/conversations/{id}`, `/api/chat/stats`, `/api/llm-settings`.
`App` and `MobileLayout` mount every panel, so they need all of the above
plus `/api/me`.

**Timestamps must be relative** (`Date.now()`-derived). The panels render
them through `relativeTime()`, so a hard-coded epoch shows up as "680 d ago".

**`App` needs an explicit height.** The stylesheet sizes the shell with
`html, body, #root { height: 100% }`; mounted anywhere else that chain
breaks and dockview collapses to one squashed panel. Wrap it:
`<div style={{ height: "100vh" }}><App /></div>`.

## Styling idiom: hand-authored classes + CSS custom properties, not utilities

This is a plain, app-specific stylesheet — no Tailwind-style utility
vocabulary, no CSS-in-JS. Two things carry the visual language:

- **CSS custom properties** for color: `--ink` / `--ink-secondary` /
  `--ink-muted` (text), `--surface` / `--surface-raised` (backgrounds),
  `--border`, `--accent`, `--good` / `--warn` / `--danger` (status). Reach
  for these (`color: var(--ink-secondary)`), never a hex literal, so new UI
  stays theme-reactive across all 5 themes.
- **Component-scoped class names** matching each source file — e.g.
  `.view-dropdown` / `.view-dropdown-toggle` / `.view-dropdown-menu` (the
  FlickrLinkMenu/UserMenu dropdown family), `.palette*` (CommandPalette),
  `.md-*` (Markdown output). These aren't a shared design-token vocabulary —
  they're this component's own styling, defined once in `styles.css` and not
  meant to be reused loose on other markup.

## Where the truth lives

Read `styles.css` (and what it `@import`s, including `_ds_bundle.css`)
before styling anything — it's the actual compiled stylesheet these
components render against. Each component's `.prompt.md` documents its own
props and usage; its `.d.ts` is the authoritative prop contract.

## Example: a menu-family dropdown

```tsx
document.documentElement.setAttribute("data-theme", "daylight");

<div className="topbar-right" style={{ display: "flex", justifyContent: "flex-end" }}>
  <UserMenu me={{ nsid: "1@N00", username: "photo516", fullname: "Mr. E", csrf_token: "" }} />
  <ThemeMenu />
</div>
```

`UserMenu`/`ThemeMenu`/`FlickrLinkMenu` share one family: a `position:
relative` trigger button (`.view-dropdown-toggle`) whose panel opens
`position: absolute; right: 0` beneath it — compose them right-aligned in a
flex row with room to their left, or the panel clips off the edge of a
narrow container.
