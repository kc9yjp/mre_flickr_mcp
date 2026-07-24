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
  is_own: boolean;
  owner?: { nsid: string; username: string; realname: string; profile_url: string; avatar_url: string };
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

export interface QueueRow {
  id: number;
  photo_id: string;
  photo_title: string;
  photo_url: string;
  group_id: string;
  group_name: string;
  group_url: string;
  retry_at: string | null;
  queued_at: string;
  error_msg: string;
  completed_at: string | null;
}

export interface QueueData {
  counts: { waiting: number; success: number; error: number };
  waiting: QueueRow[];
  errors: QueueRow[];
  successes: QueueRow[];
}

export interface SetupData {
  base_url: string;
  mcp_url: string;
  sse_url: string;
  has_api_key: boolean;
  snippets: Record<string, string>;
  bookmarklet: string;
}

// ── LLM / model config ────────────────────────────────────────────────────

export interface ProviderProfile {
  label: string;
  base_url: string;
  api_key: string;
}

export interface LLMSettings {
  providers: Record<string, ProviderProfile>;
  active_provider: string;
  active_model: string;
  max_tokens: number;
  vision: boolean;
  base_prompt: string;
  temperature: string;
  top_p: string;
  frequency_penalty: string;
  presence_penalty: string;
  seed: string;
  tool_choice: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  provider: string;
  model: string;
}

export interface WireMessage {
  role: "user" | "assistant" | "tool" | "system";
  content: string | null;
  tool_calls?: { id: string; function: { name: string; arguments: string } }[];
  tool_call_id?: string;
}

export interface WorkflowCommand {
  id: string;
  label: string;
  context: "photo" | "global";
  prompt: string;
}

export type StreamEvent =
  | { type: "start"; conversation_id: string }
  | { type: "delta"; text: string }
  | { type: "tool_call"; id: string; name: string; arguments: string }
  | {
      type: "confirm_request";
      confirm_id: string;
      name: string;
      arguments: string;
      photo: { id: string; title: string; thumb_url: string | null } | null;
      group: { id: string; name: string } | null;
      warning: string | null;
    }
  | { type: "tool_result"; id: string; name: string; text: string }
  | { type: "focus"; photo_id: string }
  | { type: "error"; message: string }
  | { type: "done" };

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

export async function favePhoto(id: string): Promise<{ ok: true }> {
  return postJSON(`/api/photos/${id}/fave`, {});
}

export async function commentOnPhoto(id: string, commentText: string): Promise<{ ok: true; comment_id: string }> {
  return postJSON(`/api/photos/${id}/comment`, { comment_text: commentText });
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

export async function listModels(provider: string): Promise<string[]> {
  const r = await getJSON<{ models: string[] }>(`/api/llm-models`, { provider });
  return r.models;
}

/** POST to /api/chat/stream and invoke onEvent for every SSE data event. */
export async function streamChat(
  body: { conversation_id?: string; message: string; focused_photo_id?: string | null; provider?: string; model?: string },
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const err = await response.json().catch(() => ({}));
    throw new ApiError(response.status, err.error ?? response.statusText);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      for (const line of frame.split("\n")) {
        if (line.startsWith("data:")) {
          try {
            onEvent(JSON.parse(line.slice(5).trim()) as StreamEvent);
          } catch {
            // ignore malformed frame
          }
        }
      }
    }
  }
}
