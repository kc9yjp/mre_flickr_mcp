import { useState } from "react";
import { ApiError, commentOnPhoto, favePhoto } from "../api";

/** Fave + comment on a photo you don't own, via a single button and a native prompt(). */
export function FaveAndCommentPrompt({ photoId }: { photoId: string }) {
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async () => {
    const text = window.prompt("Comment on this photo (leave blank to just fave):", "") ?? "";
    setStatus("");
    setBusy(true);
    try {
      await favePhoto(photoId);
      if (text.trim()) {
        await commentOnPhoto(photoId, text.trim());
        setStatus("★ Faved and commented.");
      } else {
        setStatus("★ Faved.");
      }
    } catch (e) {
      setStatus(e instanceof ApiError ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="detail-fave-comment">
      <div className="detail-fave-row">
        <button onClick={run} disabled={busy}>★ Fave &amp; Comment</button>
        {status && <span className="hint">{status}</span>}
      </div>
    </div>
  );
}
