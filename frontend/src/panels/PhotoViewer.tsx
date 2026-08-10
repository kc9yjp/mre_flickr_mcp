// Single-photo detail panel — works for the caller's own photos and for
// anyone else's (own vs. other is entirely determined by the /api/photos/{id}
// response's is_own flag). Replaces the old split between PhotoBrowser's
// inline detail view and the standalone OtherPhotoView.

import { useEffect, useState } from "react";
import { getJSON, PhotoDetail } from "../api";
import * as bus from "../bus";
import { compactNumber } from "../format";
import { PhotoId } from "../PhotoId";
import { SafeHtml } from "../safeHtml";
import { useWorkflowCommands } from "../useWorkflowCommands";

function parseHashId(): string | null {
  const m = window.location.hash.match(/(?:photo|other)=(\d+)/);
  return m ? m[1] : null;
}

export function PhotoViewer() {
  // The most recently emitted viewPhoto id (if any) takes priority over the
  // URL hash on mount — see bus.ts's getLastViewPhoto for why: a fresh mount
  // (first open, or reopening a closed tab) must not fall back to a stale
  // deep-link hash when a more recent in-app selection already happened.
  const [photoId, setPhotoId] = useState<string | null>(() => bus.getLastViewPhoto() ?? parseHashId());
  const [detail, setDetail] = useState<PhotoDetail | null>(null);
  const [error, setError] = useState("");
  const [src, setSrc] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);

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

  const copyLink = () => {
    if (!detail) return;
    navigator.clipboard.writeText(detail.url_photopage).then(() => {
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1500);
    });
  };

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
          <button
            type="button"
            className="icon-btn"
            title="Back to Photo Browser"
            onClick={() => bus.emit("openPanel", "photos")}
          >
            ◀
          </button>
          {photoCommands.length > 0 && (
            <div className="detail-workflows">
              {photoCommands.map((c) => (
                <button
                  key={c.id}
                  className="workflow-pill"
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
          <div className="detail-toolbar-right">
            <a
              className="icon-btn"
              href={detail.url_photopage}
              target="flickr_photo"
              rel="noreferrer"
              title="Open on Flickr"
            >
              ↗
            </a>
            <button
              type="button"
              className="icon-btn"
              onClick={copyLink}
              title={linkCopied ? "Copied!" : "Copy link"}
            >
              {linkCopied ? "✓" : "⧉"}
            </button>
          </div>
        </div>
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
        <h2>
          {detail.title || "Untitled"}
          <PhotoId id={detail.id} />
        </h2>
        {!detail.is_own && detail.owner && (
          <p className="hint other-photo-owner">
            {detail.owner.avatar_url && <img className="owner-avatar" src={detail.owner.avatar_url} alt="" />}
            by{" "}
            <a href={detail.owner.profile_url} target="_blank" rel="noreferrer">
              {detail.owner.realname || detail.owner.username || detail.owner.nsid}
            </a>
          </p>
        )}
        {detail.description && (
          <p className="detail-description"><SafeHtml html={detail.description} /></p>
        )}
        <div className="detail-stats">
          <span>{compactNumber(detail.views)} views</span>
          <span>{compactNumber(detail.favorites)} faves</span>
          <span>{compactNumber(detail.comments)} comments</span>
          {detail.is_own && <span>{detail.is_public ? "public" : "private"}</span>}
          {detail.in_keeper_list && <span>keeper</span>}
        </div>

        <div className="detail-meta-list">
          <div className="detail-meta-block">
            <div className="detail-meta-label">Taken</div>
            <div className="detail-meta-value">{detail.date_taken || "unknown"}</div>
          </div>
          <div className="detail-meta-block">
            <div className="detail-meta-label">Tags</div>
            <div className="detail-meta-value">
              {detail.tags
                ? detail.tags.split(" ").map((t) => (
                    <span key={t} className="chip">
                      {t}
                    </span>
                  ))
                : "none"}
            </div>
          </div>
          {detail.is_own && (
            <>
              <div className="detail-meta-block">
                <div className="detail-meta-label">Groups</div>
                <div className="detail-meta-value">
                  {detail.groups.length
                    ? detail.groups.map((g) => (
                        <span key={g.id} className="chip">
                          {g.name}
                        </span>
                      ))
                    : "none"}
                </div>
              </div>
              <div className="detail-meta-block">
                <div className="detail-meta-label">Albums</div>
                <div className="detail-meta-value">
                  {detail.albums.length
                    ? detail.albums.map((a) => (
                        <span key={a.id} className="chip">
                          {a.title}
                        </span>
                      ))
                    : "none"}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
