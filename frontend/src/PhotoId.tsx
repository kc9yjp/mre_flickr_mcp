// Small "photo id + copy" chip — shows a photo's numeric id with a button
// that copies it to the clipboard. Shared by the Photo Browser thumbnails
// and the Photo Viewer detail header so the id is always visible next to
// the title, not just buried in the URL.

import { MouseEvent, useState } from "react";

export function PhotoId({ id, className }: { id: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  const copy = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    navigator.clipboard.writeText(id).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <span className={className ? `photo-id ${className}` : "photo-id"}>
      {id}
      <button
        type="button"
        className="icon-btn photo-id-copy"
        onClick={copy}
        title={copied ? "Copied!" : "Copy photo ID"}
      >
        {copied ? "✓" : "⧉"}
      </button>
    </span>
  );
}
