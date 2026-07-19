// Fetch wrapper for the /api JSON endpoints. Auth rides on the session
// cookie; POSTs carry the CSRF token (bootstrapped from /api/me) as a header.

export interface Me {
  nsid: string;
  username: string;
  fullname: string;
  csrf_token: string;
}

export interface Photo {
  id: string;
  title: string;
  description: string;
  date_taken: string;
  date_uploaded: number;
  last_updated: number;
  url_photopage: string;
  url_original: string;
  url_medium: string | null;
  tags: string;
  views: number;
  favorites: number;
  comments: number;
  is_public: number;
  synced_at: number;
}

export interface PhotoDetail extends Photo {
  groups: { id: string; name: string }[];
  in_keeper_list: boolean;
}

export interface PhotoPage {
  total: number;
  offset: number;
  photos: Photo[];
}

export interface Stats {
  total_photos: number;
  public_photos: number;
  private_photos: number;
  total_views: number;
  total_groups: number;
  total_albums: number;
  total_contacts: number;
  date_range: { earliest: string | null; latest: string | null };
  last_synced: number | null;
  top_tags: { tag: string; count: number }[];
}

export interface SyncRow {
  type: string;
  last: number | null;
  duration: string | null;
  next: number | null;
  running: boolean;
}

export interface SyncStatus {
  running: boolean;
  rows: SyncRow[];
}

let csrfToken = "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    window.location.href = "/login";
    throw new ApiError(401, "unauthenticated");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, body.error ?? body.reason ?? response.statusText);
  }
  return body as T;
}

export async function initSession(): Promise<Me> {
  const me = await handle<Me>(await fetch("/api/me"));
  csrfToken = me.csrf_token;
  return me;
}

export async function getJSON<T>(url: string, params?: Record<string, string>): Promise<T> {
  const qs = params ? "?" + new URLSearchParams(params).toString() : "";
  return handle<T>(await fetch(url + qs));
}

export async function postJSON<T>(url: string, body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}
