import { PhotoId } from "flickr-workbench";

// See Markdown.tsx preview note.
document.documentElement.setAttribute("data-theme", "daylight");

const SAMPLE_ID = "53987654321";

export function Default() {
  return <PhotoId id={SAMPLE_ID} />;
}

// Used in cramped captions (e.g. Photo Browser thumbnails) — id is still
// reachable via the button tooltips, just not shown as text.
export function IconOnly() {
  return <PhotoId id={SAMPLE_ID} showId={false} />;
}
