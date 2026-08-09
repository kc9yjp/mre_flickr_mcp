## Scope: a small UI-chrome slice, not a general component library

This sync ships **7 small, self-contained UI pieces** pulled out of a larger
single-page app (a Flickr workbench) — dropdown menus, an id chip, a
command palette, and two text renderers. It does **not** include the app's
page-level panels (chat, photo browser, settings, etc.) — those fetch from a
live authenticated backend session and have no meaningful standalone render,
so they were deliberately left out. Treat what's here as reusable chrome
(menus, chips, text rendering) to compose into new designs, not as a
component library covering full application screens.

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
