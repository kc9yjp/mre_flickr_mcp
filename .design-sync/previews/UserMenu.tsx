import { useEffect, useRef } from "react";
import { UserMenu } from "flickr-workbench";

// See Markdown.tsx preview note.
document.documentElement.setAttribute("data-theme", "daylight");

const ME = { nsid: "12345678@N00", username: "photo516", fullname: "Mr. E", csrf_token: "" };

export function Default() {
  return <UserMenu me={ME} />;
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
      <UserMenu me={ME} />
    </div>
  );
}
