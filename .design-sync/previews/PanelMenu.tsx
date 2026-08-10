import { useEffect, useRef, useState } from "react";
import { PanelMenu } from "flickr-workbench";

// See Markdown.tsx preview note.
document.documentElement.setAttribute("data-theme", "daylight");

const PANELS = [
  { id: "summary", label: "Stats" },
  { id: "photos", label: "Photos" },
  { id: "sync", label: "Sync" },
  { id: "settings", label: "Settings" },
] as const;

function Demo() {
  const [active, setActive] = useState<string>("summary");
  return <PanelMenu panels={PANELS} active={active} onSelect={setActive} />;
}

export function Default() {
  return <Demo />;
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
      <Demo />
    </div>
  );
}
