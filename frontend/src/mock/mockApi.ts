// Fetch-intercepting mock API for interface-design work without a running
// Python backend. Only loaded when built/served in "mock" mode (see
// main.tsx and the VITE_MOCK_API check there) — never bundled into a real
// production build.
//
// Patches window.fetch to answer every /api/* route the panels call on
// mount with plausible, mostly-stateful seed data, typed against the real
// interfaces in ../api so this drifts loudly (a type error) rather than
// silently if a response shape changes. Not a general-purpose mock
// framework — just enough surface for "does the interface look/feel right
// with real-ish data in it." Anything not explicitly routed falls back to
// 404 (GET) or {ok:true} (POST) so buttons don't throw, they just no-op
// against data that was never really mocked.
//
// See docker-compose.yml's frontend-dev service (profile "dev") for how to
// run this as a live Vite dev server, or `npm run build:mock` for a static
// build.

import type {
  Album, AlbumPhotoPage, Connection, ConnectionPreset, Conversation, LLMSettings, Me, Photo, PhotoDetail,
  PhotoPage, Prompt, PromptCategory, PromptsData, PromptVariable, QueueData, QueueRow, SessionStats, SetupData,
  Stats, StreamEvent, SyncRow, SyncStatus, WorkflowCommand,
} from "../api";

interface MockSetting {
  key: string;
  label: string;
  description: string;
  default: string;
  value: string;
}

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

const nowSec = () => Math.floor(Date.now() / 1000);
const isoDaysAgo = (d: number) => new Date(Date.now() - d * 86400000).toISOString();

// Deterministic colored-rectangle thumbnail — no network dependency.
function svgThumb(seed: number, label: string): string {
  const hue = (seed * 47) % 360;
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' width='400' height='300'>` +
    `<rect width='100%' height='100%' fill='hsl(${hue},55%,45%)'/>` +
    `<text x='50%' y='50%' font-family='sans-serif' font-size='22' fill='white' ` +
    `text-anchor='middle' dominant-baseline='middle'>${label}</text></svg>`;
  return "data:image/svg+xml," + encodeURIComponent(svg);
}

// ── Seed data ──────────────────────────────────────────────────────────

const TITLES = [
  "Sunset over the bay", "Morning fog on the ridge", "Downtown skyline at dusk", "Old barn, late light",
  "Autumn leaves, close up", "Harbor lights", "Mountain trail switchbacks", "City reflections",
  "Quiet street, early morning", "Golden hour on the dunes", "Rain on the window", "Desert bloom",
  "Coastal cliffs", "Winter branches", "Neon signs, wet pavement", "Garden gate", "River bend",
  "Rooftop view, storm coming", "Foggy pier", "Backyard birds", "Frost on the fence", "Market stalls",
  "Lighthouse at dawn", "Alley cat", "Train platform", "Snow on pines", "Tidepools", "Vineyard rows",
  "Old truck, new paint", "Bridge at night",
];
const TAGS = [
  "landscape", "street", "macro", "blackandwhite", "sunset", "urban", "nature", "travel",
  "architecture", "portrait", "wildlife", "longexposure", "film", "minimal", "texture",
];

function makePhoto(i: number): Photo {
  const id = String(72157700000000 + i);
  const title = TITLES[i % TITLES.length];
  const tags = [TAGS[i % TAGS.length], TAGS[(i * 3 + 1) % TAGS.length]].join(" ");
  const views = Math.floor(20 + Math.pow(i % 12, 2.3) * 7);
  const favorites = Math.floor(views / (8 + (i % 5)));
  const comments = Math.floor(favorites / (2 + (i % 3)));
  const thumb = svgThumb(i, `#${i + 1}`);
  const daysAgo = i * 5 + (i % 7);
  return {
    id, title,
    description: i % 4 === 0 ? "" : `A quiet moment — ${title.toLowerCase()}.`,
    date_taken: isoDaysAgo(daysAgo).slice(0, 19).replace("T", " "),
    date_uploaded: Math.floor(Date.now() / 1000) - daysAgo * 86400,
    last_updated: Math.floor(Date.now() / 1000) - daysAgo * 43200,
    url_photopage: `https://www.flickr.com/photos/demo_user/${id}/`,
    url_original: thumb, url_medium: thumb,
    tags, views, favorites, comments,
    is_public: i % 9 !== 0 ? 1 : 0,
    synced_at: nowSec(),
  };
}
const PHOTOS: Photo[] = Array.from({ length: 42 }, (_, i) => makePhoto(i));

const ALBUMS: Album[] = [
  { id: "1001", title: "Best of 2025", description: "A running favorites album.", count_photos: 18, count_views: 4213, thumb_url: svgThumb(1, "Album") },
  { id: "1002", title: "Street", description: "Candid street shots.", count_photos: 27, count_views: 1890, thumb_url: svgThumb(2, "Album") },
  { id: "1003", title: "Travel — Pacific Northwest", description: "", count_photos: 41, count_views: 9021, thumb_url: svgThumb(3, "Album") },
];

const STATS: Stats = {
  total_photos: 1842, public_photos: 1690, private_photos: 152, total_views: 284310,
  total_groups: 34, total_albums: 12, total_contacts: 96,
  date_range: { earliest: "2011-03-02", latest: new Date().toISOString().slice(0, 10) },
  last_synced: nowSec() - 3600,
  top_tags: [
    { tag: "landscape", count: 412 }, { tag: "street", count: 301 }, { tag: "macro", count: 198 },
    { tag: "blackandwhite", count: 176 }, { tag: "travel", count: 154 },
  ],
};

function syncRow(type: string, total: number, pendingSummary?: number): SyncRow {
  return {
    type, last: nowSec() - 3600, duration: "42s", next: nowSec() + 3600 * 8,
    running: false, phase: null, total, pending_summary: pendingSummary ?? null,
    progress_done: null, progress_total: null, eta_seconds: null,
  };
}
const SYNC_STATUS: SyncStatus = {
  running: false,
  rows: [syncRow("photos", 1842), syncRow("contacts", 96), syncRow("groups", 34, 3), syncRow("albums", 12)],
};

function queueRow(id: number, status: "waiting" | "error" | "success"): QueueRow {
  const photo = PHOTOS[id % PHOTOS.length];
  const groupNames = ["Street Photographers", "Golden Hour", "Film Only", "Mono Monday"];
  return {
    id, photo_id: photo.id, photo_title: photo.title, photo_url: photo.url_photopage,
    group_id: `700${id % 4}`, group_name: groupNames[id % 4], group_url: `https://www.flickr.com/groups/700${id % 4}/`,
    retry_at: status === "waiting" ? isoDaysAgo(-1) : null,
    queued_at: isoDaysAgo(1),
    error_msg: status === "error" ? "Pool full — try again later." : "",
    completed_at: status === "success" ? isoDaysAgo(2) : null,
  };
}
const QUEUE = {
  waiting: [queueRow(1, "waiting"), queueRow(2, "waiting")],
  errors: [queueRow(3, "error")],
  successes: [queueRow(4, "success"), queueRow(5, "success"), queueRow(6, "success")],
};
const queueData = (): QueueData => ({
  counts: { waiting: QUEUE.waiting.length, success: QUEUE.successes.length, error: QUEUE.errors.length },
  ...QUEUE,
});

const SETTINGS: MockSetting[] = [
  { key: "retry_timezone", label: "Retry timezone", description: "Timezone used to schedule queued group-pool retries.", default: "UTC", value: "America/Los_Angeles" },
  { key: "queue_retry_time", label: "Default retry time", description: "Time of day queued retries fire, in the timezone above.", default: "09:00", value: "09:00" },
  { key: "sync_interval_hours", label: "Background sync interval (hours)", description: "How often the background task re-syncs each registered user.", default: "12", value: "12" },
];

const SETUP: SetupData = {
  base_url: window.location.origin,
  mcp_url: window.location.origin + "/mcp",
  sse_url: window.location.origin + "/sse",
  has_api_key: true,
  snippets: {
    "Claude Code": `{\n  "mcpServers": {\n    "flickr": {\n      "url": "${window.location.origin}/sse"\n    }\n  }\n}`,
  },
  bookmarklet: "javascript:void(0)",
};

const DEMO_CONNECTION: Connection = {
  name: "Ollama (local)", kind: "ollama", api_mode: "chat_completions",
  base_url: "http://localhost:11434", api_key: "", timeout_seconds: 300,
  disabled_models: [], models: {}, paused: false, tool_set: "all",
};
const LLM: LLMSettings = {
  connections: { "ollama-local": DEMO_CONNECTION },
  active_connection: "ollama-local", active_model: "llama3.1",
  auto_compact: false, sync_connection: "", sync_model: "", sync_throttle_seconds: 60,
};

const CATEGORIES: PromptCategory[] = [
  { id: "own_photo", name: "My Photos", description: "Shown on your own photos in the viewer.", sort_order: 1, builtin: true },
  { id: "other_photo", name: "Other Photos", description: "Shown when viewing someone else's photo.", sort_order: 2, builtin: true },
  // category_id "collection" (context "global") — matches the real app's
  // default-prompts.md; these show up as global commands on the Commands
  // panel and command palette, not tied to any one photo.
  { id: "collection", name: "My Collection", description: "Runs against your whole library, from the Commands panel.", sort_order: 3, builtin: true },
];
const VARIABLES: PromptVariable[] = [
  { code: "photo_id", label: "Photo ID", description: "The currently focused photo's id.", resolved_by: "focused photo", builtin: true },
  { code: "user_nsid", label: "User NSID", description: "Your Flickr user id.", resolved_by: "session", builtin: true },
];
function makePrompt(
  sortOrder: number, id: string, code: string, name: string,
  category_id: string, context: "photo" | "global", text: string,
): Prompt {
  return {
    id, code, name, description: name, category_id, context, text,
    builtin: true, default_text: text, enabled: true, sort_order: sortOrder,
    created_at: nowSec() - 999999, updated_at: nowSec() - 999999,
  };
}
const PROMPTS: Prompt[] = [
  makePrompt(1, "p1", "suggest_metadata", "Suggest metadata", "own_photo", "photo", "Suggest a title, description, and tags for photo {photo_id}."),
  makePrompt(2, "p2", "find_groups", "Find matching groups", "own_photo", "photo", "Find groups this photo {photo_id} would fit well in."),
  makePrompt(3, "p3", "compliment", "Suggest a comment", "other_photo", "photo", "Write a genuine, specific comment for photo {photo_id}."),
  makePrompt(4, "p4", "reply_to_comments", "Reply to comments", "collection", "global", "Find recent comments on my photos I haven't replied to yet and draft replies."),
  makePrompt(5, "p5", "review_weak_photos", "Review weak photos", "collection", "global", "Find weak photos (low views, no favorites or comments) and suggest improving or hiding them."),
  makePrompt(6, "p6", "unearth_private", "Unearth private photos", "collection", "global", "Review private photos from oldest to newest and decide which ones to publish."),
];
const promptsData = (): PromptsData => ({ categories: CATEGORIES, prompts: PROMPTS, variables: VARIABLES });
const COMMANDS: WorkflowCommand[] = PROMPTS.map((p) => ({
  id: `wf-${p.id}`, prompt_id: p.id, label: p.name, context: p.context, category_id: p.category_id, prompt: p.text,
}));

const CONVERSATIONS: Conversation[] = [
  { id: "c1", title: "Weekly review", created_at: nowSec() - 86400 * 2, updated_at: nowSec() - 3600, provider: "ollama-local", model: "llama3.1" },
  { id: "c2", title: "Group suggestions for harbor shot", created_at: nowSec() - 86400 * 6, updated_at: nowSec() - 86400 * 5, provider: "ollama-local", model: "llama3.1" },
];

// ── SSE (for /api/chat/stream) ────────────────────────────────────────

function sseResponse(events: StreamEvent[], delayMs = 140): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const stream = new ReadableStream({
    pull(controller) {
      if (i >= events.length) {
        controller.close();
        return;
      }
      const chunk = "data: " + JSON.stringify(events[i]) + "\n\n";
      i++;
      return new Promise<void>((resolve) => {
        setTimeout(() => {
          controller.enqueue(encoder.encode(chunk));
          resolve();
        }, delayMs);
      });
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

// ── Router ─────────────────────────────────────────────────────────────

type Handler = (
  match: RegExpExecArray,
  params: URLSearchParams,
  body: Record<string, any>,
) => Response | Promise<Response>;

interface Route {
  method: string;
  re: RegExp;
  fn: Handler;
}

const routes: Route[] = [];
function on(method: string, pattern: string, fn: Handler) {
  const re = new RegExp("^" + pattern.replace(/:[^/]+/g, "([^/]+)") + "$");
  routes.push({ method, re, fn });
}

function registerRoutes() {
  on("GET", "/api/me", () => json({ nsid: "12345678@N00", username: "demo_user", fullname: "Dana Demo", csrf_token: "mock-csrf-token" } satisfies Me));

  on("GET", "/api/photos", (_m, p) => {
    const offset = parseInt(p.get("offset") || "0", 10);
    const limit = parseInt(p.get("limit") || "60", 10);
    let pool = PHOTOS;
    if (p.get("ids")) {
      const ids = p.get("ids")!.split(",");
      pool = PHOTOS.filter((x) => ids.includes(x.id));
    } else if (p.get("query")) {
      const q = p.get("query")!.toLowerCase();
      pool = PHOTOS.filter((x) => x.title.toLowerCase().includes(q) || x.tags.includes(q));
    }
    return json({ total: pool.length, offset, photos: pool.slice(offset, offset + limit) } satisfies PhotoPage);
  });

  on("GET", "/api/photos/:id", (m) => {
    const p = PHOTOS.find((x) => x.id === m[1]);
    if (!p) return json({ error: "not found" }, 404);
    return json({
      ...p,
      groups: [{ id: "70012345", name: "Street Photographers" }],
      albums: [{ id: ALBUMS[0].id, title: ALBUMS[0].title }],
      in_keeper_list: false,
      is_own: true,
    } satisfies PhotoDetail);
  });

  on("GET", "/api/albums", () => json({ albums: ALBUMS }));
  on("GET", "/api/albums/:id/photos", () =>
    json({ total: 20, page: 1, pages: 1, photos: PHOTOS.slice(0, 20) } satisfies AlbumPhotoPage));

  on("GET", "/api/stats", () => json(STATS));
  on("GET", "/api/sync/status", () => json(SYNC_STATUS));
  on("POST", "/api/sync/:type", () => json({ ok: true, started: true }));
  on("POST", "/api/sync/:type/cancel", () => json({ cancelled: false, reason: "nothing running (mock)" }));
  on("POST", "/api/reset", () => json({ ok: true }));

  on("GET", "/api/queue", () => json(queueData()));
  on("POST", "/api/queue", (_m, _p, body) => {
    if (body && body.delete_id != null) {
      for (const key of ["waiting", "errors", "successes"] as const) {
        QUEUE[key] = QUEUE[key].filter((r) => r.id !== body.delete_id);
      }
      return json({ ok: true, deleted: 1 });
    }
    const allIds = [...QUEUE.waiting, ...QUEUE.errors, ...QUEUE.successes].map((r) => r.id);
    const id = Math.max(0, ...allIds) + 1;
    QUEUE.waiting.push(queueRow(id, "waiting"));
    return json({ ok: true, added: 1 });
  });

  on("GET", "/api/settings", () => json({ settings: SETTINGS }));
  on("POST", "/api/settings", (_m, _p, body) => {
    for (const s of SETTINGS) if (body && body[s.key] !== undefined) s.value = body[s.key];
    return json({ ok: true });
  });

  on("GET", "/api/setup", () => json(SETUP));
  on("POST", "/api/regen-key", () => json({ ok: true, api_key: "demo-" + Math.random().toString(36).slice(2) }));

  on("GET", "/api/llm-settings", () => json(LLM));
  on("POST", "/api/llm-settings", (_m, _p, body) => {
    Object.assign(LLM, body);
    return json(LLM);
  });
  on("GET", "/api/llm-models", () => json({ models: ["llama3.1", "llama3.2", "mistral"], all_models: ["llama3.1", "llama3.2", "mistral", "llava"] }));
  on("GET", "/api/llm-connection-presets", () => json({
    presets: {
      ollama: { label: "Ollama (local)", base_url: "http://localhost:11434", kind: "ollama", api_mode: "chat_completions" },
      openai_compatible: { label: "OpenAI-compatible", base_url: "https://api.openai.com/v1", kind: "openai_compatible", api_mode: "chat_completions" },
    } satisfies Record<string, ConnectionPreset>,
  }));
  on("POST", "/api/llm-connections", (_m, _p, body) => {
    const id = String(body.name || "connection").toLowerCase().replace(/\s+/g, "-");
    LLM.connections[id] = {
      name: body.name, kind: body.kind, api_mode: body.api_mode || "chat_completions", base_url: body.base_url,
      api_key: body.api_key || "", timeout_seconds: body.timeout_seconds || 300, disabled_models: [], models: {}, paused: false,
      tool_set: body.tool_set || "all",
    };
    return json({ id, ...LLM });
  });
  on("POST", "/api/llm-connections/:id/update", (m, _p, body) => {
    if (LLM.connections[m[1]]) Object.assign(LLM.connections[m[1]], body);
    return json(LLM);
  });
  on("POST", "/api/llm-connections/:id/delete", (m) => {
    delete LLM.connections[m[1]];
    return json(LLM);
  });
  on("POST", "/api/llm-connections/:id/model-settings", () => json(LLM));
  on("POST", "/api/llm-connections/:id/model-settings/reset", () => json(LLM));

  on("GET", "/api/chat/conversations", () => json({ conversations: CONVERSATIONS }));
  on("GET", "/api/chat/conversations/:id", () => json({
    provider: "ollama-local", model: "llama3.1",
    messages: [
      { role: "user", content: "What are my weakest photos from last month?" },
      { role: "assistant", content: "Mocked reply — this is canned demo data, not a live model." },
    ],
  }));
  on("POST", "/api/chat/conversations/:id/delete", (m) => {
    const i = CONVERSATIONS.findIndex((c) => c.id === m[1]);
    if (i >= 0) CONVERSATIONS.splice(i, 1);
    return json({ ok: true });
  });
  on("GET", "/api/chat/stats", () => json({
    turns: 6, prompt_tokens: 5230, completion_tokens: 1890, total_tokens: 7120,
    total_latency_ms: 15400, last_prompt_tokens: 1290, context_window: 128000,
  } satisfies SessionStats));
  on("POST", "/api/chat/confirm", () => json({ ok: true }));
  on("POST", "/api/chat/answer", () => json({ ok: true }));
  on("POST", "/api/chat/cancel", () => json({ ok: false }));
  on("POST", "/api/chat/inject", () => json({ ok: true }));
  on("POST", "/api/chat/conversations/:id/compact", () => json({ ok: true, summary: "(mock) conversation compacted." }));

  on("POST", "/api/chat/stream", (_m, _p, body) => {
    const convId = body.conversation_id || "mock-" + Math.random().toString(36).slice(2, 8);
    const said = body.message ? ` You said: "${body.message}"` : "";
    const reply = `This is a mocked reply from the static preview — there's no real model behind it.${said}`;
    const words = reply.split(" ");
    const events: StreamEvent[] = [{ type: "start", conversation_id: convId }];
    let chunk = "";
    words.forEach((w, idx) => {
      chunk += (chunk ? " " : "") + w;
      if (chunk.length > 18 || idx === words.length - 1) {
        events.push({ type: "delta", text: chunk + (idx === words.length - 1 ? "" : " ") });
        chunk = "";
      }
    });
    events.push({ type: "done" });
    return sseResponse(events);
  });

  on("GET", "/api/prompts", () => json(promptsData()));
  on("POST", "/api/prompts", (_m, _p, body) => {
    const id = "p" + (PROMPTS.length + 1);
    PROMPTS.push(makePrompt(PROMPTS.length + 1, id, body.code, body.name, body.category_id, body.context, body.text));
    return json({ ok: true });
  });
  on("POST", "/api/prompts/:id", (m, _p, body) => {
    const pr = PROMPTS.find((x) => x.id === m[1]);
    if (pr) Object.assign(pr, body, { updated_at: nowSec() });
    return json({ ok: true });
  });
  on("POST", "/api/prompts/:id/reset", (m) => {
    const pr = PROMPTS.find((x) => x.id === m[1]);
    if (pr && pr.default_text != null) pr.text = pr.default_text;
    return json({ ok: true });
  });
  on("POST", "/api/prompts/:id/delete", (m) => {
    const i = PROMPTS.findIndex((x) => x.id === m[1]);
    if (i >= 0) PROMPTS.splice(i, 1);
    return json({ ok: true });
  });
  on("POST", "/api/prompts/reset-all", () => json({ ok: true }));
  on("POST", "/api/prompt-variables", (_m, _p, body) => {
    VARIABLES.push({ code: body.code, label: body.label, description: body.description, resolved_by: "user-defined", builtin: false });
    return json({ ok: true });
  });
  on("POST", "/api/prompt-variables/:code/delete", (m) => {
    const i = VARIABLES.findIndex((v) => v.code === m[1]);
    if (i >= 0) VARIABLES.splice(i, 1);
    return json({ ok: true });
  });

  on("GET", "/api/commands", () => json({ commands: COMMANDS }));

  on("POST", "/logout", () => json({ ok: true }));
}

// ── fetch patch ────────────────────────────────────────────────────────

/** Patch window.fetch so every /api/* (and /logout) call is answered locally
 * instead of hitting a real backend. Idempotent — safe to call more than once. */
export function installMockApi() {
  if ((window.fetch as any).__isMockApi) return;
  registerRoutes();

  const realFetch = window.fetch.bind(window);
  const mockFetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method || "GET";
    let u: URL;
    try {
      u = new URL(url, window.location.origin);
    } catch {
      return realFetch(input, init);
    }
    if (!u.pathname.startsWith("/api/") && u.pathname !== "/logout") return realFetch(input, init);

    for (const r of routes) {
      if (r.method !== method) continue;
      const m = r.re.exec(u.pathname);
      if (!m) continue;
      let body: Record<string, any> = {};
      if (init?.body) {
        try {
          body = JSON.parse(init.body as string);
        } catch {
          // non-JSON body (shouldn't happen — postJSON always sends JSON)
        }
      }
      const res = r.fn(m, u.searchParams, body);
      return Promise.resolve(res).then((response) => {
        // Streaming responses (chat/stream) pace themselves via their own
        // ReadableStream; this artificial delay only applies to plain JSON
        // responses, to feel a bit more like a real network round-trip.
        if (response.headers.get("Content-Type") === "text/event-stream") return response;
        return new Promise<Response>((resolve) => setTimeout(() => resolve(response), 120));
      });
    }

    return Promise.resolve(
      method === "GET" ? json({ error: `mock: no handler for ${method} ${u.pathname}` }, 404) : json({ ok: true }),
    );
  };
  (mockFetch as any).__isMockApi = true;
  window.fetch = mockFetch;

  // eslint-disable-next-line no-console
  console.info(
    "%c[mock-api] /api/* is intercepted with fake data — this build has no real backend behind it.",
    "color: #888",
  );
}
