// Small "photo id" chip — shows a photo's numeric id with buttons to copy it
// to the clipboard or drop it into the Chat panel's input (for the user to
// build a prompt around). Shared by the Photo Browser thumbnails and the
// Photo Viewer detail header so the id is always visible next to the title,
// not just buried in the URL.

import { MouseEvent, useState } from "react";
import * as bus from "./bus";

export function PhotoId({
  id,
  className,
  showId = true,
}: {
  id: string;
  className?: string;
  /** Render the id as visible text next to the buttons. When false (e.g. in a
   * thumbnail's cramped caption), the id is only surfaced via the button
   * tooltips, so it's still discoverable on hover without taking up space. */
  showId?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const copy = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    navigator.clipboard.writeText(id).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const sendToChat = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    bus.requestInsertChatText(id);
  };

  return (
    <span className={className ? `photo-id ${className}` : "photo-id"}>
      {showId && id}
      <button
        type="button"
        className="icon-btn photo-id-copy"
        onClick={copy}
        title={copied ? "Copied!" : `Copy photo ID ${id}`}
      >
        {copied ? "✓" : "⧉"}
      </button>
      <button
        type="button"
        className="icon-btn photo-id-copy"
        onClick={sendToChat}
        title={`Send photo ID ${id} to chat`}
      >
        ➤
      </button>
    </span>
  );
}
