import { useEffect, useRef } from "react";
import { FlickrLinkMenu } from "flickr-workbench";

// See Markdown.tsx preview note.
document.documentElement.setAttribute("data-theme", "daylight");

const SAMPLE_URL = "https://www.flickr.com/photos/photo516/53987654321/";

export function Default() {
  return <FlickrLinkMenu url={SAMPLE_URL} />;
}

// The dropdown's open state is internal (no prop for it) — click the real
// toggle button after mount so the composed menu itself renders the open
// state, rather than reimplementing it. Wrapped in the real "topbar-right"
// flex container (its actual composition context in App/MobileLayout),
// right-aligned in a fixed-width box the way the real topbar (a wide flex
// row ending in topbar-right) places it near the right edge — the menu's
// `right: 0` needs that room to its left, or it clips off the card edge.
export function Open() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>(".view-dropdown-toggle")?.click();
  }, []);
  return (
    <div className="topbar-right" ref={ref} style={{ display: "flex", justifyContent: "flex-end", width: 340 }}>
      <FlickrLinkMenu url={SAMPLE_URL} />
    </div>
  );
}
