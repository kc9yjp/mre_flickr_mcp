// Photo Browser panel: search/browse your own photos and albums, another
// user's photostream/albums, a group's photo pool, or a site-wide Flickr
// search. Clicking a photo opens it in the separate Photo Viewer panel.
// Clearing any non-default mode always returns to My Photos.

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  Album,
  getJSON,
  getGroupInfo,
  getGroupPhotos,
  getUserAlbumPhotos,
  getUserAlbums,
  getUserPhotos,
  GroupInfo,
  GroupSearchResult,
  LivePhotoPage,
  lookupUser,
  Photo,
  PhotoPage,
  searchFlickrPhotos,
  searchGroups,
  UserProfile,
} from "../api";
import * as bus from "../bus";
import { compactNumber } from "../format";
import { PhotoId } from "../PhotoId";
import { SafeHtml } from "../safeHtml";
import { useWorkflowCommands } from "../useWorkflowCommands";

const PAGE_SIZE = 60;

type Mode = "mine" | "albums" | "user" | "group" | "search";

const MODES: { id: Mode; label: string }[] = [
  { id: "mine", label: "My Photos" },
  { id: "albums", label: "My Albums" },
  { id: "user", label: "User" },
  { id: "group", label: "Group" },
  { id: "search", label: "Search Flickr" },
];

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
    // A div (not <button>) because the id-copy control below is a real
    // <button>, and interactive controls can't nest inside <button>.
    <div
      className="thumb"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      title={photo.title}
    >
      {src ? (
        <img src={src} alt={photo.title} loading="lazy" />
      ) : (
        <span className="thumb-placeholder">no image</span>
      )}
      <span className="thumb-caption">
        <span className="thumb-title">{photo.title || "Untitled"}</span>
        <span className="thumb-stats-row">
          <span className="thumb-stats">
            {compactNumber(photo.views)} views · {photo.favorites} ★
            {photo.is_public ? "" : " · private"}
          </span>
          <PhotoId id={photo.id} showId={false} />
        </span>
      </span>
    </div>
  );
}

const GROUP_ID_RE = /^\d+@N\d+$/;

export function PhotoBrowser() {
  const collectionCommands = useWorkflowCommands("global").filter((c) => c.category_id === "collection");

  const [mode, setMode] = useState<Mode>("mine");
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [photoListLabel, setPhotoListLabel] = useState<string | null>(null);

  // My Photos
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [draft, setDraft] = useState<Filters>(DEFAULT_FILTERS);

  // My Albums
  const [albums, setAlbums] = useState<Album[]>([]);
  const [selectedAlbum, setSelectedAlbum] = useState<Album | null>(null);

  // Shared page/pages for every page-based mode (albums/user/group/search) —
  // only one mode is ever active at a time.
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);

  // User
  const [userQuery, setUserQuery] = useState("");
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);
  const [userSubMode, setUserSubMode] = useState<"photos" | "albums">("photos");
  const [userAlbums, setUserAlbums] = useState<Album[]>([]);
  const [selectedUserAlbum, setSelectedUserAlbum] = useState<Album | null>(null);

  // Group
  const [groupQuery, setGroupQuery] = useState("");
  const [groupInfo, setGroupInfo] = useState<GroupInfo | null>(null);
  const [groupResults, setGroupResults] = useState<GroupSearchResult[] | null>(null);

  // Search
  const [searchQuery, setSearchQuery] = useState("");
  const [activeSearchQuery, setActiveSearchQuery] = useState("");

  const load = useCallback(async (f: Filters, offset: number) => {
    setLoading(true);
    setError("");
    try {
      const p = await getJSON<PhotoPage>("/api/photos", filterParams(f, offset));
      setTotal(p.total);
      setPhotos((prev) => (offset === 0 ? p.photos : [...prev, ...p.photos]));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const applyPage = (data: LivePhotoPage, p: number) => {
    setTotal(data.total);
    setPages(data.pages);
    setPhotos((prev) => (p === 1 ? data.photos : [...prev, ...data.photos]));
  };

  const loadAlbumPhotos = useCallback(async (album: Album, p: number) => {
    setLoading(true);
    setError("");
    try {
      applyPage(await getJSON<LivePhotoPage>(`/api/albums/${album.id}/photos`, {
        page: String(p), limit: String(PAGE_SIZE),
      }), p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadUserPhotosPage = useCallback(async (nsid: string, p: number) => {
    setLoading(true);
    setError("");
    try {
      applyPage(await getUserPhotos(nsid, p), p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadUserAlbumPhotosPage = useCallback(async (nsid: string, albumId: string, p: number) => {
    setLoading(true);
    setError("");
    try {
      applyPage(await getUserAlbumPhotos(nsid, albumId, p), p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadGroupPhotosPage = useCallback(async (id: string, p: number) => {
    setLoading(true);
    setError("");
    try {
      applyPage(await getGroupPhotos(id, p), p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSearchPhotosPage = useCallback(async (q: string, p: number) => {
    setLoading(true);
    setError("");
    try {
      applyPage(await searchFlickrPhotos(q, p), p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPhotoList = useCallback(async (ids: string[]) => {
    setLoading(true);
    setError("");
    try {
      const p = await getJSON<PhotoPage>("/api/photos", { ids: ids.join(",") });
      setTotal(p.total);
      setPhotos(p.photos);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // The "✕ Clear" action, and what every mode switch away from a loaded
  // browse eventually funnels back into: always lands on default My Photos.
  const resetToMine = useCallback(() => {
    setError("");
    setPhotoListLabel(null);
    setMode("mine");
    setSelectedAlbum(null);
    setUserQuery("");
    setUserProfile(null);
    setUserSubMode("photos");
    setUserAlbums([]);
    setSelectedUserAlbum(null);
    setGroupQuery("");
    setGroupInfo(null);
    setGroupResults(null);
    setSearchQuery("");
    setActiveSearchQuery("");
    setPage(1);
    setPages(1);
    setFilters(DEFAULT_FILTERS);
    setDraft(DEFAULT_FILTERS);
    load(DEFAULT_FILTERS, 0);
  }, [load]);

  const switchMode = (next: Mode) => {
    if (next === mode) return;
    if (next === "mine") {
      resetToMine();
      return;
    }
    setError("");
    setPhotoListLabel(null);
    setPhotos([]);
    setTotal(0);
    setPage(1);
    setPages(1);
    setMode(next);
    if (next === "albums") setSelectedAlbum(null);
    if (next === "user") {
      setUserQuery("");
      setUserProfile(null);
      setUserSubMode("photos");
      setUserAlbums([]);
      setSelectedUserAlbum(null);
    }
    if (next === "group") {
      setGroupQuery("");
      setGroupInfo(null);
      setGroupResults(null);
    }
    if (next === "search") {
      setSearchQuery("");
      setActiveSearchQuery("");
    }
  };

  const selectAlbum = (album: Album) => {
    setSelectedAlbum(album);
    setPage(1);
    loadAlbumPhotos(album, 1);
  };

  const goUser = async (e: FormEvent) => {
    e.preventDefault();
    const q = userQuery.trim();
    if (!q) return;
    setError("");
    setLoading(true);
    try {
      const profile = await lookupUser(q);
      setUserProfile(profile);
      setUserSubMode("photos");
      setPage(1);
      await loadUserPhotosPage(profile.nsid, 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  };

  const openUserAlbums = async () => {
    if (!userProfile) return;
    setUserSubMode("albums");
    setSelectedUserAlbum(null);
    setPhotos([]);
    setTotal(0);
    setLoading(true);
    setError("");
    try {
      const r = await getUserAlbums(userProfile.nsid);
      setUserAlbums(r.albums);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const backToUserPhotos = () => {
    setUserSubMode("photos");
    setSelectedUserAlbum(null);
    if (userProfile) {
      setPage(1);
      loadUserPhotosPage(userProfile.nsid, 1);
    }
  };

  const selectUserAlbum = (album: Album) => {
    if (!userProfile) return;
    setSelectedUserAlbum(album);
    setPage(1);
    loadUserAlbumPhotosPage(userProfile.nsid, album.id, 1);
  };

  const goGroup = async (e: FormEvent) => {
    e.preventDefault();
    const q = groupQuery.trim();
    if (!q) return;
    setError("");
    setGroupInfo(null);
    setGroupResults(null);
    setLoading(true);
    try {
      if (GROUP_ID_RE.test(q) || q.includes("flickr.com/groups/")) {
        const info = await getGroupInfo(q);
        setGroupInfo(info);
        setPage(1);
        await loadGroupPhotosPage(info.id, 1);
      } else {
        setGroupResults((await searchGroups(q)).groups);
        setLoading(false);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  };

  const pickGroup = async (g: GroupSearchResult) => {
    setError("");
    setLoading(true);
    try {
      const info = await getGroupInfo(g.id);
      setGroupInfo(info);
      setGroupResults(null);
      setPage(1);
      await loadGroupPhotosPage(info.id, 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  };

  const goSearch = async (e: FormEvent) => {
    e.preventDefault();
    const q = searchQuery.trim();
    if (!q) return;
    setActiveSearchQuery(q);
    setPage(1);
    await loadSearchPhotosPage(q, 1);
  };

  useEffect(() => {
    load(DEFAULT_FILTERS, 0);
    getJSON<{ albums: Album[] }>("/api/albums")
      .then((r) => setAlbums(r.albums))
      .catch(() => {});
  }, [load]);

  useEffect(
    () =>
      bus.on("showPhotoList", (ids) => {
        setError("");
        setMode("mine");
        setSelectedAlbum(null);
        setUserProfile(null);
        setGroupInfo(null);
        setGroupResults(null);
        setActiveSearchQuery("");
        setPhotoListLabel(
          ids.length ? `${ids.length.toLocaleString("en")} photo${ids.length === 1 ? "" : "s"} found` : "No photos found",
        );
        loadPhotoList(ids);
      }),
    [loadPhotoList],
  );

  useEffect(
    () =>
      bus.on("showUserPhotos", (nsid) => {
        setError("");
        setMode("user");
        setSelectedAlbum(null);
        setGroupInfo(null);
        setGroupResults(null);
        setActiveSearchQuery("");
        setPhotoListLabel(null);
        setUserQuery(nsid);
        lookupUser(nsid)
          .then((profile) => {
            setUserProfile(profile);
            setUserSubMode("photos");
            setPage(1);
            return loadUserPhotosPage(profile.nsid, 1);
          })
          .catch((e) => setError(e instanceof Error ? e.message : String(e)));
      }),
    [loadUserPhotosPage],
  );

  useEffect(
    () =>
      bus.on("showGroupPhotos", (ref) => {
        setError("");
        setMode("group");
        setSelectedAlbum(null);
        setUserProfile(null);
        setActiveSearchQuery("");
        setPhotoListLabel(null);
        setGroupQuery(ref);
        setGroupInfo(null);
        setGroupResults(null);
        getGroupInfo(ref)
          .then((info) => {
            setGroupInfo(info);
            setPage(1);
            return loadGroupPhotosPage(info.id, 1);
          })
          .catch((e) => setError(e instanceof Error ? e.message : String(e)));
      }),
    [loadGroupPhotosPage],
  );

  useEffect(
    () =>
      bus.on("showUserAlbum", ({ owner, albumId }) => {
        setError("");
        setMode("user");
        setSelectedAlbum(null);
        setGroupInfo(null);
        setGroupResults(null);
        setActiveSearchQuery("");
        setPhotoListLabel(null);
        setUserQuery(owner);
        lookupUser(owner)
          .then(async (profile) => {
            setUserProfile(profile);
            setUserSubMode("albums");
            const { albums } = await getUserAlbums(profile.nsid);
            setUserAlbums(albums);
            const album = albums.find((a) => a.id === albumId) ?? {
              id: albumId, title: "", description: "", count_photos: 0, count_views: 0, thumb_url: null,
            };
            setSelectedUserAlbum(album);
            setPage(1);
            return loadUserAlbumPhotosPage(profile.nsid, albumId, 1);
          })
          .catch((e) => setError(e instanceof Error ? e.message : String(e)));
      }),
    [loadUserAlbumPhotosPage],
  );

  const submitMine = (e: FormEvent) => {
    e.preventDefault();
    setPhotoListLabel(null);
    setFilters(draft);
    load(draft, 0);
  };

  const showResults =
    mode === "mine" ||
    (mode === "albums" && !!selectedAlbum) ||
    (mode === "user" && (userSubMode === "photos" ? !!userProfile : !!selectedUserAlbum)) ||
    (mode === "group" && !!groupInfo) ||
    (mode === "search" && !!activeSearchQuery);

  const canLoadMore =
    mode === "mine" ? !photoListLabel && photos.length < total : showResults && page < pages;

  const loadMore = () => {
    const next = page + 1;
    if (mode === "mine") {
      load(filters, photos.length);
    } else if (mode === "albums" && selectedAlbum) {
      setPage(next);
      loadAlbumPhotos(selectedAlbum, next);
    } else if (mode === "user" && userSubMode === "photos" && userProfile) {
      setPage(next);
      loadUserPhotosPage(userProfile.nsid, next);
    } else if (mode === "user" && userSubMode === "albums" && selectedUserAlbum && userProfile) {
      setPage(next);
      loadUserAlbumPhotosPage(userProfile.nsid, selectedUserAlbum.id, next);
    } else if (mode === "group" && groupInfo) {
      setPage(next);
      loadGroupPhotosPage(groupInfo.id, next);
    } else if (mode === "search" && activeSearchQuery) {
      setPage(next);
      loadSearchPhotosPage(activeSearchQuery, next);
    }
  };

  // Re-fetch whatever's currently on screen from the top, same mode dispatch
  // as loadMore above but reloading page 1 instead of appending. A workflow
  // photo list (photoListLabel) has no query to re-run — its ids aren't kept
  // in state past the initial load — so refresh is a no-op there.
  const refresh = () => {
    if (mode === "mine" && !photoListLabel) {
      load(filters, 0);
    } else if (mode === "albums" && selectedAlbum) {
      setPage(1);
      loadAlbumPhotos(selectedAlbum, 1);
    } else if (mode === "user" && userSubMode === "photos" && userProfile) {
      setPage(1);
      loadUserPhotosPage(userProfile.nsid, 1);
    } else if (mode === "user" && userSubMode === "albums" && selectedUserAlbum && userProfile) {
      setPage(1);
      loadUserAlbumPhotosPage(userProfile.nsid, selectedUserAlbum.id, 1);
    } else if (mode === "group" && groupInfo) {
      setPage(1);
      loadGroupPhotosPage(groupInfo.id, 1);
    } else if (mode === "search" && activeSearchQuery) {
      setPage(1);
      loadSearchPhotosPage(activeSearchQuery, 1);
    }
  };

  return (
    <div className="panel photo-browser">
      <button
        type="button"
        className="icon-btn panel-refresh-btn"
        onClick={refresh}
        disabled={loading}
        title="Refresh"
      >
        ↻
      </button>
      <div className="browse-modes">
        {MODES.map((m) => (
          <button
            key={m.id}
            className={mode === m.id ? "browse-mode active" : "browse-mode"}
            onClick={() => switchMode(m.id)}
          >
            {m.label}
          </button>
        ))}
        {mode !== "mine" && (
          <button className="browse-mode" onClick={resetToMine} title="Back to My Photos">
            ✕ Clear
          </button>
        )}
      </div>

      {mode === "mine" && collectionCommands.length > 0 && (
        <div className="detail-workflows">
          {collectionCommands.map((c) => (
            <button
              key={c.id}
              title="Runs in the Chat panel"
              onClick={() => bus.emit("runCommand", { title: c.label, text: c.prompt, promptId: c.prompt_id })}
            >
              ▶ {c.label}
            </button>
          ))}
        </div>
      )}

      {mode === "mine" &&
        (photoListLabel ? (
          <div className="album-banner">
            <span>
              <strong>{photoListLabel}</strong> — from a workflow, for review
            </span>
          </div>
        ) : (
          <form className="toolbar" onSubmit={submitMine}>
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
        ))}

      {mode === "albums" && (
        <>
          <select
            className="album-select"
            value={selectedAlbum?.id ?? ""}
            onChange={(e) => {
              const album = albums.find((a) => a.id === e.target.value);
              if (album) selectAlbum(album);
            }}
          >
            <option value="" disabled>
              Choose an album…
            </option>
            {albums.map((a) => (
              <option key={a.id} value={a.id}>
                {(a.title || a.id) + ` (${a.count_photos})`}
              </option>
            ))}
          </select>
          {selectedAlbum && (
            <div className="album-banner">
              <span>
                Album: <strong>{selectedAlbum.title || selectedAlbum.id}</strong>
                {selectedAlbum.description ? <> — <SafeHtml html={selectedAlbum.description} /></> : ""}
              </span>
            </div>
          )}
        </>
      )}

      {mode === "user" && (
        <>
          {!userProfile && (
            <form className="toolbar" onSubmit={goUser}>
              <input
                placeholder="Username, NSID, or profile URL…"
                value={userQuery}
                onChange={(e) => setUserQuery(e.target.value)}
              />
              <button type="submit" disabled={loading}>
                Go
              </button>
            </form>
          )}
          {userProfile && (
            <div className="browse-header">
              {userProfile.avatar_url && <img className="owner-avatar" src={userProfile.avatar_url} alt="" />}
              <div>
                <p>
                  <a href={userProfile.profile_url} target="_blank" rel="noreferrer">
                    <strong>{userProfile.realname || userProfile.username || userProfile.nsid}</strong>
                  </a>
                  {userProfile.location ? ` · ${userProfile.location}` : ""}
                  {" · "}
                  {compactNumber(userProfile.photo_count)} photos
                </p>
                {userProfile.description && <p className="hint"><SafeHtml html={userProfile.description} /></p>}
                <p className="chip-row">
                  {userProfile.you_follow && <span className="chip">You follow them</span>}
                  {userProfile.follows_you && <span className="chip">Follows you</span>}
                  {userProfile.is_friend && <span className="chip">Friend</span>}
                  {userProfile.is_family && <span className="chip">Family</span>}
                </p>
                <div className="browse-modes">
                  <button
                    className={userSubMode === "photos" ? "browse-mode active" : "browse-mode"}
                    onClick={backToUserPhotos}
                  >
                    Photos
                  </button>
                  <button
                    className={userSubMode === "albums" ? "browse-mode active" : "browse-mode"}
                    onClick={openUserAlbums}
                  >
                    Albums
                  </button>
                </div>
                {userSubMode === "albums" && (
                  <>
                    <select
                      className="album-select"
                      value={selectedUserAlbum?.id ?? ""}
                      onChange={(e) => {
                        const album = userAlbums.find((a) => a.id === e.target.value);
                        if (album) selectUserAlbum(album);
                      }}
                    >
                      <option value="" disabled>
                        Choose an album…
                      </option>
                      {userAlbums.map((a) => (
                        <option key={a.id} value={a.id}>
                          {(a.title || a.id) + ` (${a.count_photos})`}
                        </option>
                      ))}
                    </select>
                    {selectedUserAlbum?.description && (
                      <p className="hint"><SafeHtml html={selectedUserAlbum.description} /></p>
                    )}
                  </>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {mode === "group" && (
        <>
          {!groupInfo && (
            <form className="toolbar" onSubmit={goGroup}>
              <input
                placeholder="Group name, ID, or URL…"
                value={groupQuery}
                onChange={(e) => setGroupQuery(e.target.value)}
              />
              <button type="submit" disabled={loading}>
                Go
              </button>
            </form>
          )}
          {groupResults && (
            <div className="browse-header">
              <p className="hint">Pick a group:</p>
              {groupResults.map((g) => (
                <button key={g.id} className="browse-mode" onClick={() => pickGroup(g)}>
                  {g.name} ({compactNumber(g.members)} members)
                </button>
              ))}
            </div>
          )}
          {groupInfo && (
            <div className="browse-header">
              <p>
                <a href={groupInfo.url} target="_blank" rel="noreferrer">
                  <strong>{groupInfo.name}</strong>
                </a>
                {" · "}
                {compactNumber(groupInfo.members)} members · {compactNumber(groupInfo.pool_count)} photos
                {" · "}
                <span className="chip">{groupInfo.joined ? "Joined" : "Not joined"}</span>
              </p>
              {groupInfo.description && <p className="hint"><SafeHtml html={groupInfo.description} /></p>}
              {groupInfo.rules && <p className="hint">Rules: <SafeHtml html={groupInfo.rules} /></p>}
            </div>
          )}
        </>
      )}

      {mode === "search" && (
        <form className="toolbar" onSubmit={goSearch}>
          <input
            placeholder="Search all of Flickr…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <button type="submit" disabled={loading}>
            Go
          </button>
        </form>
      )}

      {error && <p className="error">{error}</p>}

      {showResults && (
        <>
          <p className="result-count">
            {total.toLocaleString("en")} photo{total === 1 ? "" : "s"}
          </p>
          <div className="thumb-grid">
            {photos.map((p) => (
              <Thumb key={p.id} photo={p} onClick={() => bus.emitViewPhoto(p.id)} />
            ))}
          </div>
          {canLoadMore && (
            <button className="load-more" disabled={loading} onClick={loadMore}>
              {loading ? "Loading…" : `Load more (${photos.length} of ${total.toLocaleString("en")})`}
            </button>
          )}
        </>
      )}
    </div>
  );
}
