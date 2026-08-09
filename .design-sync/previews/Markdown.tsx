import { Markdown } from "flickr-workbench";

// The app applies its data-theme attribute in main.tsx before first paint
// (via theme.ts's applyTheme, a plain-.ts module the synth-entry sweep
// doesn't reach — see source-kit.mjs override); replicate just the
// attribute set, not the component, as harness setup — otherwise body text
// falls back to :root's dark-theme ink color on this card's white
// background and reads as nearly invisible.
document.documentElement.setAttribute("data-theme", "daylight");

// Real shape of what this renderer exists for — LLM tool-summary replies in
// the Chat panel (see markdown.tsx's own comment): headings, bold/italic,
// lists, code blocks, links.
const RICH_TEXT = `## Sync summary

Synced **482 photos** and refreshed *12 groups*.

- 3 photos need a title
- 1 group hit its posting limit

\`\`\`
GET /api/sync/status → 200
\`\`\`

See the [sync log](https://example.com/log) for details.`;

export function RichText() {
  return <Markdown text={RICH_TEXT} />;
}

const TABLE_TEXT = `| Group | Pool | Your photos |
| --- | --- | --- |
| Chicago Streets | 12,403 | 8 |
| Oak Park Life | 3,102 | 4 |
| Architecture Weekly | 21,980 | 2 |`;

export function Table() {
  return <Markdown text={TABLE_TEXT} />;
}

const QUOTE_TEXT = `> Daily posting limit reached for **Chicago Streets**.

Retrying tomorrow morning.`;

export function Blockquote() {
  return <Markdown text={QUOTE_TEXT} />;
}
