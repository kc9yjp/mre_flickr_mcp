import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  ApiError,
  commentOnPhoto,
  favePhoto,
  getJSON,
  Photo,
  PhotoDetail,
  PhotoPage,
  WorkflowCommand,
} from "../api";
import * as bus from "../bus";
import { compactNumber } from "../format";

let photoCommands: WorkflowCommand[] = [];
getJSON<{ commands: WorkflowCommand[] }>("/api/commands")
  .then((r) => (photoCommands = r.commands.filter((c) => c.context === "photo")))
  .catch(() => {});

const PAGE_SIZE = 60;

interface Filters {
  query: string;
  tags: string;
  sort: string;
  visibility: string;
}

const DEFAULT_FILTERS: Filters = { query: "", tags: "", sort: "date_taken", visibility: "" };

function filterParams(filters: Filters, offset: number): Record<string, string> {
  const params: Record<string, string> = {
    sort: filters.sort,
    limit: String(PAGE_SIZE),
    offset: String(offset),
  };
  if (filters.query) params.query = filters.query;
  if (filters.tags) params.tags = filters.tags;
  if (filters.visibility) params.is_public = filters.visibility;
  return params;
}

function Thumb({ photo, onClick }: { photo: Photo; onClick: () => void }) {
  const src = photo.url_medium || photo.url_original;
  return (
    <button className="thumb" onClick={onClick} title={photo.title}>
      {src ? (
        <img src={src} alt={photo.title} loading="lazy" />
      ) : (
        <span className="thumb-placeholder">no image</span>
      )}
      <span className="thumb-caption">
        <span className="thumb-title">{photo.title || photo.id}</span>
        <span className="thumb-stats">
          {compactNumber(photo.views)} views · {photo.favorites} ★
          {photo.is_public ? "" : " · private"}
        </span>
      </span>
    </button>
  );
}

function FaveCommentBar({ photoId }: { photoId: string }) {
  const [comment, setComment] = useState("");
  const [faveStatus, setFaveStatus] = useState("");
  const [commentStatus, setCommentStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const doFave = async () => {
    setFaveStatus("");
    setBusy(true);
    try {
      await favePhoto(photoId);
      setFaveStatus("★ Faved");
    } catch (e) {
      setFaveStatus(e instanceof ApiError ? e.message : "Fave failed");
    } finally {
      setBusy(false);
    }
  };

  const submitComment = async (e: FormEvent) => {
    e.preventDefault();
    if (!comment.trim()) return;
    setCommentStatus("");
    setBusy(true);
    try {
      await commentOnPhoto(photoId, comment.trim());
      setComment("");
      setCommentStatus("Comment posted.");
    } catch (e) {
      setCommentStatus(e instanceof ApiError ? e.message : "Comment failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="detail-fave-comment">
      <div className="detail-fave-row">
        <button onClick={doFave} disabled={busy}>★ Fave</button>
        {faveStatus && <span className="hint">{faveStatus}</span>}
      </div>
      <form className="detail-comment-form" onSubmit={submitComment}>
        <textarea
          rows={2}
          placeholder="Write a comment…"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <button type="submit" disabled={busy || !comment.trim()}>
          Comment
        </button>
      </form>
      {commentStatus && <span className="hint">{commentStatus}</span>}
    </div>
  );
}

function DetailView({ detail, onBack }: { detail: PhotoDetail; onBack: () => void }) {
  const src = detail.url_original || detail.url_medium;
  return (
    <div className="photo-detail">
      <div className="detail-toolbar">
        <button onClick={onBack}>← Back to grid</button>
        <a href={detail.url_photopage} target="_blank" rel="noreferrer">
          Open on Flickr ↗
        </a>
      </div>
      {detail.is_own && (
        <div className="detail-workflows">
          {photoCommands.map((c) => (
            <button
              key={c.id}
              title="Runs in the Chat panel"
              onClick={() => bus.emit("runCommand", c.prompt.replaceAll("{photo_id}", detail.id))}
            >
              ▶ {c.label}
            </button>
          ))}
        </div>
      )}
      {src && <img className="detail-image" src={src} alt={detail.title} />}
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
        <span>{detail.is_public ? "public" : "private"}</span>
        {detail.in_keeper_list && <span>keeper</span>}
      </div>
      {detail.is_own && <FaveCommentBar photoId={detail.id} />}
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
          </>
        )}
      </dl>
    </div>
  );
}

export function PhotoBrowser() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [draft, setDraft] = useState<Filters>(DEFAULT_FILTERS);
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<PhotoDetail | null>(null);

  const load = useCallback(async (f: Filters, offset: number) => {
    setLoading(true);
    setError("");
    try {
      const page = await getJSON<PhotoPage>("/api/photos", filterParams(f, offset));
      setTotal(page.total);
      setPhotos((prev) => (offset === 0 ? page.photos : [...prev, ...page.photos]));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const openDetail = useCallback(async (id: string) => {
    setError("");
    try {
      setDetail(await getJSON<PhotoDetail>(`/api/photos/${id}`));
      bus.emit("photoOpened", id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load(DEFAULT_FILTERS, 0);
    const match = window.location.hash.match(/photo=(\d+)/);
    if (match) openDetail(match[1]);
    return bus.on("focusPhoto", openDetail);
  }, [load, openDetail]);

  const closeDetail = useCallback(() => {
    setDetail(null);
    bus.emit("photoOpened", null);
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    closeDetail();
    setFilters(draft);
    load(draft, 0);
  };

  if (detail) {
    return (
      <div className="panel photo-browser">
        <DetailView detail={detail} onBack={closeDetail} />
      </div>
    );
  }

  return (
    <div className="panel photo-browser">
      <form className="toolbar" onSubmit={submit}>
        <input
          placeholder="Search title/description…"
          value={draft.query}
          onChange={(e) => setDraft({ ...draft, query: e.target.value })}
        />
        <input
          placeholder="Tags…"
          value={draft.tags}
          onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
        />
        <select value={draft.sort} onChange={(e) => setDraft({ ...draft, sort: e.target.value })}>
          <option value="date_taken">Newest</option>
          <option value="views">Most viewed</option>
          <option value="favorites">Most faved</option>
          <option value="comments">Most commented</option>
          <option value="random">Random</option>
        </select>
        <select
          value={draft.visibility}
          onChange={(e) => setDraft({ ...draft, visibility: e.target.value })}
        >
          <option value="">All</option>
          <option value="1">Public</option>
          <option value="0">Private</option>
        </select>
        <button type="submit" disabled={loading}>
          Search
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      <p className="result-count">
        {total.toLocaleString("en")} photo{total === 1 ? "" : "s"}
      </p>
      <div className="thumb-grid">
        {photos.map((p) => (
          <Thumb key={p.id} photo={p} onClick={() => openDetail(p.id)} />
        ))}
      </div>
      {photos.length < total && (
        <button className="load-more" disabled={loading} onClick={() => load(filters, photos.length)}>
          {loading ? "Loading…" : `Load more (${photos.length} of ${total.toLocaleString("en")})`}
        </button>
      )}
    </div>
  );
}
