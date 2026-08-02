// Single-photo detail panel — works for the caller's own photos and for
// anyone else's (own vs. other is entirely determined by the /api/photos/{id}
// response's is_own flag). Replaces the old split between PhotoBrowser's
// inline detail view and the standalone OtherPhotoView.

import { useEffect, useState } from "react";
import { getJSON, PhotoDetail } from "../api";
import * as bus from "../bus";
import { compactNumber } from "../format";
import { useWorkflowCommands } from "../useWorkflowCommands";
import { FaveAndCommentPrompt } from "./FaveAndCommentPrompt";

function parseHashId(): string | null {
  const m = window.location.hash.match(/(?:photo|other)=(\d+)/);
  return m ? m[1] : null;
}

export function PhotoViewer() {
  const [photoId, setPhotoId] = useState<string | null>(() => parseHashId());
  const [detail, setDetail] = useState<PhotoDetail | null>(null);
  const [error, setError] = useState("");
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => bus.on("viewPhoto", setPhotoId), []);

  useEffect(() => {
    if (!photoId) return;
    let cancelled = false;
    setError("");
    setDetail(null);
    getJSON<PhotoDetail>(`/api/photos/${photoId}`)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setSrc(d.url_original || d.url_medium);
        bus.emit("photoOpened", d.id);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [photoId]);

  // "photo" context is shared by the own_photo and other_photo categories —
  // pick the right set once we know whose photo this is.
  const photoCommands = useWorkflowCommands("photo").filter(
    (c) => c.category_id === (detail?.is_own ? "own_photo" : "other_photo"),
  );

  if (!photoId) {
    return (
      <div className="panel photo-viewer">
        <p className="hint">Open a photo from the Photo Browser (or a bookmarklet/extension link) to view it here.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel photo-viewer">
        <p className="error">{error}</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="panel photo-viewer">
        <p className="hint">Loading…</p>
      </div>
    );
  }

  return (
    <div className="panel photo-viewer">
      <div className="photo-detail">
        <div className="detail-toolbar">
          <button onClick={() => bus.emit("openPanel", "photos")}>◀ Photo Browser</button>
          <a href={detail.url_photopage} target="flickr_photo" rel="noreferrer">
            Open on Flickr ↗
          </a>
        </div>
        {photoCommands.length > 0 && (
          <div className="detail-workflows">
            {photoCommands.map((c) => (
              <button
                key={c.id}
                title="Runs in the Chat panel"
                onClick={() => bus.emit("runCommand", {
                  title: c.label,
                  text: c.prompt.replaceAll("{photo_id}", detail.id),
                  promptId: c.prompt_id,
                })}
              >
                ▶ {c.label}
              </button>
            ))}
          </div>
        )}
        {src && (
          <img
            className="detail-image"
            src={src}
            alt={detail.title}
            onError={() => {
              if (src === detail.url_original && detail.url_medium && detail.url_medium !== src) {
                setSrc(detail.url_medium);
              }
            }}
          />
        )}
        <h2>{detail.title || detail.id}</h2>
        {!detail.is_own && detail.owner && (
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
          {detail.is_own && <span>{detail.is_public ? "public" : "private"}</span>}
          {detail.in_keeper_list && <span>keeper</span>}
        </div>

        {!detail.is_own && <FaveAndCommentPrompt photoId={detail.id} />}

        <dl className="detail-meta">
          <dt>Taken</dt>
          <dd>{detail.date_taken || "unknown"}</dd>
          <dt>Tags</dt>
          <dd>
            {detail.tags
              ? detail.tags.split(" ").map((t) => (
                  <span key={t} className="chip">
                    {t}
                  </span>
                ))
              : "none"}
          </dd>
          {detail.is_own && (
            <>
              <dt>Groups</dt>
              <dd>
                {detail.groups.length
                  ? detail.groups.map((g) => (
                      <span key={g.id} className="chip">
                        {g.name}
                      </span>
                    ))
                  : "none"}
              </dd>
              <dt>Albums</dt>
              <dd>
                {detail.albums.length
                  ? detail.albums.map((a) => (
                      <span key={a.id} className="chip">
                        {a.title}
                      </span>
                    ))
                  : "none"}
              </dd>
            </>
          )}
        </dl>
      </div>
    </div>
  );
}
