// Photo Browser panel: search/browse your own photos and albums, another
// user's photostream/albums, a group's photo pool, or a site-wide Flickr
// search. Clicking a photo opens it in the separate Photo Viewer panel.
// Clearing any non-default mode always returns to My Photos.

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Album,
  ApiError,
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
  postJSON,
  searchFlickrPhotos,
  searchGroups,
  SyncStatus,
  UserProfile,
} from "../api";
import * as bus from "../bus";
import { compactNumber } from "../format";
import { PhotoId } from "../PhotoId";
import { SafeHtml } from "../safeHtml";

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

// Recent searches/lists — a small local-storage-backed MRU list so a
// chat-populated photo list or a filled-in search doesn't vanish the moment
// you navigate away or reload the page. Keyed by shape, not identity:
// re-running (or landing back on) the same search moves it to the front
// instead of duplicating it.
const RECENTS_KEY = "flickr-workbench:recent-lists";
const MAX_RECENTS = 8;

type RecentEntry =
  | { type: "photoList"; label: string; ids: string[] }
  | { type: "mine"; label: string; filters: Filters }
  | { type: "user"; label: string; query: string }
  | { type: "group"; label: string; query: string }
  | { type: "search"; label: string; query: string };

const RECENT_KIND_LABELS: Record<RecentEntry["type"], string> = {
  photoList: "List",
  mine: "My Photos",
  user: "User",
  group: "Group",
  search: "Search",
};

const SORT_LABELS: Record<string, string> = {
  date_taken: "Newest",
  views: "Most viewed",
  favorites: "Most faved",
  comments: "Most commented",
  random: "Random",
};

function describeFilters(f: Filters): string {
  const parts: string[] = [];
  if (f.query) parts.push(`"${f.query}"`);
  if (f.tags) parts.push(`tags:${f.tags}`);
  if (f.visibility) parts.push(f.visibility === "1" ? "public" : "private");
  if (f.sort !== DEFAULT_FILTERS.sort) parts.push(SORT_LABELS[f.sort] ?? f.sort);
  return parts.length ? parts.join(" · ") : "All photos";
}

function loadRecents(): RecentEntry[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function sameRecent(a: RecentEntry, b: RecentEntry): boolean {
  if (a.type !== b.type) return false;
  if (a.type === "photoList" && b.type === "photoList") return a.ids.join(",") === b.ids.join(",");
  if (a.type === "mine" && b.type === "mine") return JSON.stringify(a.filters) === JSON.stringify(b.filters);
  if ((a.type === "user" || a.type === "group" || a.type === "search") && b.type === a.type) {
    return (b as { query: string }).query === (a as { query: string }).query;
  }
  return false;
}

// Writes through to localStorage and returns the updated list, so callers
// can put it straight into state without a second read.
function saveRecent(entry: RecentEntry): RecentEntry[] {
  const next = [entry, ...loadRecents().filter((r) => !sameRecent(r, entry))].slice(0, MAX_RECENTS);
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {
    // Storage full/unavailable (private browsing, quota) — recents are a
    // convenience, so just skip persisting rather than erroring the search.
  }
  return next;
}

// Small dropdown of recent searches/lists, styled to match FlickrLinkMenu /
// UserMenu (.view-dropdown family) — click the toggle, pick an entry, it
// closes on selection or on an outside click.
function RecentListsMenu({ recents, onPick }: { recents: RecentEntry[]; onPick: (entry: RecentEntry) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div className="view-dropdown recent-lists-menu" ref={ref}>
      <button type="button" className="browse-mode" onClick={() => setOpen((o) => !o)} title="Recent searches and lists">
        Recent ▾
      </button>
      {open && (
        <div className="view-dropdown-menu">
          {recents.length === 0 ? (
            <p className="hint recent-lists-empty">No recent searches yet.</p>
          ) : (
            recents.map((r, i) => (
              <button
                key={i}
                onClick={() => {
                  setOpen(false);
                  onPick(r);
                }}
              >
                <span className="recent-list-kind">{RECENT_KIND_LABELS[r.type]}</span>
                <span className="recent-list-label">{r.label}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
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
  const [mode, setMode] = useState<Mode>("mine");
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [photoListLabel, setPhotoListLabel] = useState<string | null>(null);

  // My Photos' refresh checks Flickr for new photos (a background sync) before
  // reloading the local list, rather than just re-querying the same cached
  // rows — see refresh() below. syncingPhotos drives the button's disabled
  // state while that sync + poll loop is in flight.
  const [syncingPhotos, setSyncingPhotos] = useState(false);
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

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

  // Recent searches/lists dropdown (My Photos searches, User/Group/Search
  // lookups, and chat/workflow photo lists)
  const [recents, setRecents] = useState<RecentEntry[]>(() => loadRecents());
  const remember = useCallback((entry: RecentEntry) => setRecents(saveRecent(entry)), []);

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

  // Re-load whatever a recents-menu entry points at. Mirrors the reset
  // blocks in switchMode/the bus effects below, plus each mode's own
  // page-1 loader — restoring is just "navigate there again."
  const restoreRecent = useCallback(
    (entry: RecentEntry) => {
      setError("");
      switch (entry.type) {
        case "photoList":
          setMode("mine");
          setSelectedAlbum(null);
          setUserProfile(null);
          setGroupInfo(null);
          setGroupResults(null);
          setActiveSearchQuery("");
          setPhotoListLabel(entry.label);
          loadPhotoList(entry.ids);
          break;
        case "mine":
          setMode("mine");
          setSelectedAlbum(null);
          setUserProfile(null);
          setGroupInfo(null);
          setGroupResults(null);
          setActiveSearchQuery("");
          setPhotoListLabel(null);
          setFilters(entry.filters);
          setDraft(entry.filters);
          load(entry.filters, 0);
          break;
        case "user":
          setMode("user");
          setSelectedAlbum(null);
          setGroupInfo(null);
          setGroupResults(null);
          setActiveSearchQuery("");
          setPhotoListLabel(null);
          setUserQuery(entry.query);
          setLoading(true);
          lookupUser(entry.query)
            .then((profile) => {
              setUserProfile(profile);
              setUserSubMode("photos");
              setPage(1);
              return loadUserPhotosPage(profile.nsid, 1);
            })
            .catch((e) => {
              setError(e instanceof Error ? e.message : String(e));
              setLoading(false);
            });
          break;
        case "group":
          setMode("group");
          setSelectedAlbum(null);
          setUserProfile(null);
          setActiveSearchQuery("");
          setPhotoListLabel(null);
          setGroupQuery(entry.query);
          setGroupInfo(null);
          setGroupResults(null);
          setLoading(true);
          getGroupInfo(entry.query)
            .then((info) => {
              setGroupInfo(info);
              setPage(1);
              return loadGroupPhotosPage(info.id, 1);
            })
            .catch((e) => {
              setError(e instanceof Error ? e.message : String(e));
              setLoading(false);
            });
          break;
        case "search":
          setMode("search");
          setSelectedAlbum(null);
          setUserProfile(null);
          setGroupInfo(null);
          setGroupResults(null);
          setPhotoListLabel(null);
          setSearchQuery(entry.query);
          setActiveSearchQuery(entry.query);
          setPage(1);
          loadSearchPhotosPage(entry.query, 1);
          break;
      }
    },
    [loadPhotoList, load, loadUserPhotosPage, loadGroupPhotosPage, loadSearchPhotosPage],
  );

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
      remember({ type: "user", label: profile.realname || profile.username || profile.nsid, query: profile.nsid });
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
        remember({ type: "group", label: info.name, query: info.id });
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
      remember({ type: "group", label: info.name, query: info.id });
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
    remember({ type: "search", label: q, query: q });
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
        const label = ids.length
          ? `${ids.length.toLocaleString("en")} photo${ids.length === 1 ? "" : "s"} found`
          : "No photos found";
        setPhotoListLabel(label);
        if (ids.length) remember({ type: "photoList", label, ids });
        loadPhotoList(ids);
      }),
    [loadPhotoList, remember],
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
            remember({ type: "user", label: profile.realname || profile.username || profile.nsid, query: profile.nsid });
            setUserSubMode("photos");
            setPage(1);
            return loadUserPhotosPage(profile.nsid, 1);
          })
          .catch((e) => setError(e instanceof Error ? e.message : String(e)));
      }),
    [loadUserPhotosPage, remember],
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
            remember({ type: "group", label: info.name, query: info.id });
            setPage(1);
            return loadGroupPhotosPage(info.id, 1);
          })
          .catch((e) => setError(e instanceof Error ? e.message : String(e)));
      }),
    [loadGroupPhotosPage, remember],
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
    if (draft.query || draft.tags || draft.visibility || draft.sort !== DEFAULT_FILTERS.sort) {
      remember({ type: "mine", label: describeFilters(draft), filters: draft });
    }
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

  // Poll /api/sync/status until the photos sync finishes, then reload the
  // local list — the sync writes into the same DB `load` reads from, so new
  // photos only show up once the background job actually lands, not right
  // after it's triggered.
  const pollPhotoSync = useCallback(() => {
    const check = async () => {
      let running = false;
      try {
        running = (await getJSON<SyncStatus>("/api/sync/status")).running;
      } catch {
        // Treat a failed status poll as "done" rather than spinning forever.
      }
      if (!aliveRef.current) return;
      if (running) {
        window.setTimeout(check, 2000);
        return;
      }
      setSyncingPhotos(false);
      load(filters, 0);
    };
    check();
  }, [filters, load]);

  // My Photos' refresh checks Flickr for new photos first (an incremental
  // sync) rather than just re-querying the same cached rows the panel
  // already has. A 409 means a sync — from here, the Sync panel, or the
  // background refresh task — is already running; just ride that one out
  // instead of erroring.
  const refreshMinePhotos = async () => {
    setSyncingPhotos(true);
    setError("");
    try {
      await postJSON("/api/sync/photos");
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) {
        setSyncingPhotos(false);
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    pollPhotoSync();
  };

  // Re-fetch whatever's currently on screen from the top, same mode dispatch
  // as loadMore above but reloading page 1 instead of appending. A workflow
  // photo list (photoListLabel) has no query to re-run — its ids aren't kept
  // in state past the initial load — so refresh is a no-op there.
  const refresh = () => {
    if (mode === "mine" && !photoListLabel) {
      refreshMinePhotos();
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
        className={syncingPhotos ? "icon-btn panel-refresh-btn active" : "icon-btn panel-refresh-btn"}
        onClick={refresh}
        disabled={loading || syncingPhotos}
        title={
          mode === "mine" && !photoListLabel
            ? syncingPhotos
              ? "Checking Flickr for new photos…"
              : "Check Flickr for new photos"
            : "Refresh"
        }
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
        {(mode !== "mine" || photoListLabel) && (
          <button className="browse-mode" onClick={resetToMine} title="Back to My Photos">
            ✕ Clear
          </button>
        )}
        <RecentListsMenu recents={recents} onPick={restoreRecent} />
      </div>

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
