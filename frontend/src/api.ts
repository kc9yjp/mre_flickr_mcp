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
  albums: { id: string; title: string }[];
  in_keeper_list: boolean;
  is_own: boolean;
  owner?: { nsid: string; username: string; realname: string; profile_url: string; avatar_url: string };
}

export interface PhotoPage {
  total: number;
  offset: number;
  photos: Photo[];
}

export interface Album {
  id: string;
  title: string;
  description: string;
  count_photos: number;
  count_views: number;
  thumb_url: string | null;
}

export interface AlbumPhotoPage {
  total: number;
  page: number;
  pages: number;
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

export type ConnectionKind = "ollama" | "openai_compatible";
export type ApiMode = "chat_completions" | "responses";

// Per-model settings — vision/max_tokens/sampling/tool_choice apply to one
// specific model on one connection, not the whole page. A model with no
// entry in Connection.models simply behaves like ModelSettingsDefaults.
export interface ModelSettings {
  max_tokens: number;
  vision: boolean;
  temperature: string;
  top_p: string;
  frequency_penalty: string;
  presence_penalty: string;
  seed: string;
  tool_choice: string;
  // Total context size this model can hold. Never sent to the connection —
  // only used client/server-side for the auto-compact threshold and the
  // "context used" stat, since it can't be queried from /v1/models.
  context_window: number;
}

export const MODEL_SETTINGS_DEFAULTS: ModelSettings = {
  max_tokens: 1024,
  vision: false,
  temperature: "",
  top_p: "",
  frequency_penalty: "",
  presence_penalty: "",
  seed: "",
  tool_choice: "auto",
  context_window: 128_000,
};

export interface Connection {
  name: string;
  kind: ConnectionKind;
  api_mode: ApiMode;
  base_url: string;
  api_key: string;
  disabled_models: string[];
  models: Record<string, ModelSettings>;
}

export interface ConnectionPreset {
  label: string;
  base_url: string;
  kind: ConnectionKind;
  api_mode: ApiMode;
}

export interface LLMSettings {
  connections: Record<string, Connection>;
  active_connection: string;
  active_model: string;
  // When on, loop.run_turn compacts a conversation's history into an
  // LLM-written summary once it nears the active model's context_window,
  // before sending the next turn. Off by default — compaction is lossy.
  auto_compact: boolean;
  // Connection/model used by background sync jobs (e.g. the AI group
  // summary phase). Empty means "fall back to active_connection/active_model".
  sync_connection: string;
  sync_model: string;
  // Seconds paused between successive group-summary LLM calls in one sync
  // run. Defaults to 60 (one request per minute) — gentle on a local LLM.
  sync_throttle_seconds: number;
}

// ── Prompts ────────────────────────────────────────────────────────────────

export interface PromptCategory {
  id: string;
  name: string;
  description: string;
  sort_order: number;
  builtin: boolean;
}

export interface Prompt {
  id: string;
  code: string;
  name: string;
  description: string;
  category_id: string;
  context: "photo" | "global";
  text: string;
  builtin: boolean;
  default_text: string | null;
  enabled: boolean;
  sort_order: number;
  created_at: number;
  updated_at: number;
}

export interface PromptVariable {
  code: string;
  label: string;
  description: string;
  resolved_by: string;
  builtin: boolean;
}

export interface PromptsData {
  categories: PromptCategory[];
  prompts: Prompt[];
  variables: PromptVariable[];
}

export interface SessionStats {
  turns: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_latency_ms: number;
  // Prompt size of the most recent turn (the actual current context size) —
  // unlike prompt_tokens, which sums across every turn this session.
  last_prompt_tokens: number;
  context_window: number;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  // `provider` holds a connection id (field name kept for chat.db compat).
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
  prompt_id: string;
  label: string;
  context: "photo" | "global";
  category_id: string;
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
  | { type: "photo_list"; photo_ids: string[] }
  | { type: "compacted"; summary: string }
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

export async function logout(): Promise<void> {
  await fetch("/logout", { method: "POST", headers: { "X-CSRF-Token": csrfToken } });
  window.location.href = "/login";
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

export interface ModelList {
  models: string[];
  all_models: string[];
}

export async function listModels(connectionId: string): Promise<ModelList> {
  return getJSON<ModelList>(`/api/llm-models`, { connection: connectionId });
}

export async function getConnectionPresets(): Promise<Record<string, ConnectionPreset>> {
  const r = await getJSON<{ presets: Record<string, ConnectionPreset> }>("/api/llm-connection-presets");
  return r.presets;
}

export async function createConnection(input: {
  name: string;
  kind: ConnectionKind;
  base_url: string;
  api_key?: string;
  api_mode?: ApiMode;
}): Promise<{ id: string } & LLMSettings> {
  return postJSON(`/api/llm-connections`, input);
}

export async function updateConnection(
  connectionId: string,
  patch: Partial<Connection>,
): Promise<LLMSettings> {
  return postJSON(`/api/llm-connections/${connectionId}/update`, patch);
}

export async function deleteConnection(connectionId: string): Promise<LLMSettings> {
  return postJSON(`/api/llm-connections/${connectionId}/delete`, {});
}

export async function updateModelSettings(
  connectionId: string,
  model: string,
  patch: Partial<ModelSettings>,
): Promise<LLMSettings> {
  return postJSON(`/api/llm-connections/${connectionId}/model-settings`, { model, ...patch });
}

export async function resetModelSettings(connectionId: string, model: string): Promise<LLMSettings> {
  return postJSON(`/api/llm-connections/${connectionId}/model-settings/reset`, { model });
}

export async function getSessionStats(conversationId: string): Promise<SessionStats> {
  return getJSON<SessionStats>("/api/chat/stats", { conversation_id: conversationId });
}

export async function compactConversation(conversationId: string): Promise<{ ok: true; summary: string }> {
  return postJSON(`/api/chat/conversations/${conversationId}/compact`, {});
}

// Models known to not support vision
const VISION_UNSUPPORTED = new Set([
  // Ollama models
  "llama2", "llama3", "llama3.1", "llama3.2",
  "mistral", "mixtral",
  "neural-chat", "dolphin-mixtral",
  // Zen models
  "deepseek-v4-pro", "deepseek-v4-flash",
  "minimax-m3", "minimax-m2.7", "minimax-m2.5",
  "glm-5.2", "glm-5.1", "glm-5",
  "kimi-k2.5", "kimi-k2.6", "kimi-k2.7-code",
  "big-pickle",
  "mimo-v2.5-free",
  "laguna-s-2.1-free",
  "ling-3.0-flash-free",
  "north-mini-code-free",
  "nemotron-3-ultra-free",
  "deepseek-v4-flash-free",
]);

export function modelSupportsVision(modelId: string): boolean {
  return !VISION_UNSUPPORTED.has(modelId);
}

/** POST to /api/chat/stream and invoke onEvent for every SSE data event. */
export async function streamChat(
  body: { conversation_id?: string; message: string; focused_photo_id?: string | null; connection?: string; model?: string },
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
