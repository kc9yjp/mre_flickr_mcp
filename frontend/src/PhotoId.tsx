// Small "photo id" chip — shows a photo's numeric id with buttons to copy it
// to the clipboard or drop it into the Chat panel's input (for the user to
// build a prompt around). Shared by the Photo Browser thumbnails and the
// Photo Viewer detail header so the id is always visible next to the title,
// not just buried in the URL.

import { MouseEvent, useState } from "react";
import * as bus from "./bus";

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

  const sendToChat = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    bus.requestInsertChatText(id);
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
      <button
        type="button"
        className="icon-btn photo-id-copy"
        onClick={sendToChat}
        title="Send photo ID to chat"
      >
        💬
      </button>
    </span>
  );
}
