import { useEffect, useRef } from "react";
import { ThemeMenu } from "flickr-workbench";

// See Markdown.tsx preview note.
document.documentElement.setAttribute("data-theme", "daylight");

export function Default() {
  return <ThemeMenu />;
}

// The panel's open state is internal (no prop) — click the real toggle
// button after mount. Wrapped in the real "topbar-right" flex container,
// right-aligned in a fixed-width box (see FlickrLinkMenu.tsx preview note)
// so the panel has room to open leftward instead of clipping off the card.
export function Open() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>(".view-dropdown-toggle")?.click();
  }, []);
  return (
    <div className="topbar-right" ref={ref} style={{ display: "flex", justifyContent: "flex-end", width: 340 }}>
      <ThemeMenu />
    </div>
  );
}
