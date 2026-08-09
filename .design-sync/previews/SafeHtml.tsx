import { SafeHtml } from "flickr-workbench";

// See Markdown.tsx preview note.
document.documentElement.setAttribute("data-theme", "daylight");

// Realistic shape of a Flickr bio/description field — Flickr's own editor
// produces HTML like this (bold/italic/links), which is what SafeHtml exists
// to render safely instead of printing the tags literally.
const BIO_HTML =
  "<p>Chicago-based photographer, mostly shooting <b>street</b> and <i>architecture</i> " +
  'around Oak Park. Say hi on <a href="https://www.flickr.com/people/photo516/">Flickr</a>.</p>';

export function Default() {
  return <SafeHtml html={BIO_HTML} />;
}

const RULES_HTML =
  "<p>Group rules:</p><ul><li>One photo per week</li><li>No watermarks</li><li>Be kind in comments</li></ul>";

export function ListContent() {
  return <SafeHtml html={RULES_HTML} />;
}
