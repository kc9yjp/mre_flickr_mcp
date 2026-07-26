// Detail view for a Flickr photo you don't own, opened via the bookmarklet
// or browser extension; falls back to the Flickr API when the photo isn't
// in the local database.

import { useEffect, useState } from "react";
import { getJSON, PhotoDetail } from "../api";
import * as bus from "../bus";
import { compactNumber } from "../format";
import { FaveAndCommentPrompt } from "./FaveAndCommentPrompt";

function parseHashId(): string | null {
  const m = window.location.hash.match(/other=(\d+)/);
  return m ? m[1] : null;
}

/** Dedicated view for a Flickr photo that isn't yours, opened via the bookmarklet
 * or browser extension. Unlike the Photo Browser, this never shows a grid — the
 * bookmarklet already told us this isn't one of our own photos. */
export function OtherPhotoView() {
  const [photoId, setPhotoId] = useState<string | null>(() => parseHashId());
  const [detail, setDetail] = useState<PhotoDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => bus.on("focusOtherPhoto", setPhotoId), []);

  useEffect(() => {
    if (!photoId) return;
    let cancelled = false;
    setError("");
    setDetail(null);
    getJSON<PhotoDetail>(`/api/photos/${photoId}`)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        bus.emit("photoOpened", d.id);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [photoId]);

  if (!photoId) {
    return (
      <div className="panel other-photo">
        <p className="hint">Open a Flickr photo that isn't yours via the bookmarklet to view it here.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel other-photo">
        <p className="error">{error}</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="panel other-photo">
        <p className="hint">Loading…</p>
      </div>
    );
  }

  const src = detail.url_original || detail.url_medium;

  return (
    <div className="panel other-photo">
      <div className="photo-detail">
        <div className="detail-toolbar">
          <a href={detail.url_photopage} target="_blank" rel="noreferrer">
            Open on Flickr ↗
          </a>
        </div>
        {src && <img className="detail-image" src={src} alt={detail.title} />}
        <h2>{detail.title || detail.id}</h2>
        {detail.owner && (
          <p className="hint other-photo-owner">
            {detail.owner.avatar_url && <img className="owner-avatar" src={detail.owner.avatar_url} alt="" />}
            by{" "}
            <a href={detail.owner.profile_url} target="_blank" rel="noreferrer">
              {detail.owner.realname || detail.owner.username || detail.owner.nsid}
            </a>
          </p>
        )}
        {detail.description && <p className="detail-description">{detail.description}</p>}
        <div className="detail-stats">
          <span>{compactNumber(detail.views)} views</span>
          <span>{compactNumber(detail.favorites)} faves</span>
          <span>{compactNumber(detail.comments)} comments</span>
        </div>
        <FaveAndCommentPrompt photoId={detail.id} />
      </div>
    </div>
  );
}
