// Small dropdown for a photo's Flickr page link — choose between opening it
// in a new tab and copying the URL to the clipboard. Styling mirrors
// ThemeMenu/UserMenu so it reads as the same menu family.

import { useEffect, useRef, useState } from "react";

export function FlickrLinkMenu({ url }: { url: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const copy = () => {
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      setOpen(false);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="view-dropdown flickr-link-menu" ref={ref}>
      <button className="view-dropdown-toggle" onClick={() => setOpen((o) => !o)} title="Flickr link">
        {copied ? "Copied!" : "Flickr ↗ ▾"}
      </button>
      {open && (
        <div className="view-dropdown-menu">
          <a href={url} target="flickr_photo" rel="noreferrer" onClick={() => setOpen(false)}>
            Open on Flickr ↗
          </a>
          <button onClick={copy}>Copy link</button>
        </div>
      )}
    </div>
  );
}
