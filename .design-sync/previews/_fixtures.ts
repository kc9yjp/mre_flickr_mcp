// Shared fixture data + fetch stub for the screen previews.
//
// The panels (Chat, PhotoBrowser, SyncPage, …) take NO props — every one of
// them self-fetches from the backend on mount via api.ts's `getJSON`. So the
// only way to preview a screen with real content is to answer its requests:
// `stubApi()` replaces window.fetch with a routing table keyed by path.
// The rendered component is still the real shipped one — this file supplies
// data, never markup.
//
// NOT a component preview: the converter's stale-preview scan only reads
// `.tsx` files in this directory, so a `.ts` helper is invisible to it while
// esbuild still bundles it into each preview (previews compile with
// `bundle: true`).

type Json = unknown;

/** Apply a theme before first paint — see conventions.md. The bare `:root`
 * fallback is Slate's DARK palette, which is near-invisible on the preview
 * card's white background, so every screen preview must call this. */
export function theme(name = "daylight"): void {
  document.documentElement.setAttribute("data-theme", name);
}

// ── inline imagery ────────────────────────────────────────────────────────
// Previews render from file:// with no network, so every remote thumbnail
// would come up broken. These stand in as recognizable photo-shaped art.
const shot = (a: string, b: string) =>
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="180">` +
      `<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0%" stop-color="${a}"/><stop offset="100%" stop-color="${b}"/>` +
      `</linearGradient></defs><rect width="240" height="180" fill="url(#g)"/>` +
      `<circle cx="186" cy="46" r="17" fill="#fff" opacity=".55"/>` +
      `<path d="M0 150 L58 106 L104 142 L156 92 L240 148 L240 180 L0 180 Z" fill="#000" opacity=".22"/>` +
      `</svg>`,
  );

const SUNRISE = shot("#f9c07a", "#c2543b");
const DUSK = shot("#8fb4d9", "#2f4668");
const FOREST = shot("#a9c98a", "#3c5a34");
const NIGHT = shot("#4a5c8a", "#141d33");

// ── photos ────────────────────────────────────────────────────────────────
// Titles are the real ones from this repo's README_*.md notes, so the cards
// read like the owner's actual photostream.
const PHOTO_SEED: [string, string, string, number, number, number, string][] = [
  ["53812004417", "Sunrise, Outer Banks", "First light over the dunes at Coquina Beach.", 4820, 96, 12, SUNRISE],
  ["53811773621", "Lighthouse at Manteo", "The Roanoke Marshes Light on a still morning.", 3140, 71, 8, DUSK],
  ["53810992288", "Main Street Crossing", "Small-town crosswalk, late afternoon light.", 1870, 34, 3, FOREST],
  ["53810554013", "Morning Sun", "Sun breaking through the pines behind the house.", 2260, 48, 5, SUNRISE],
  ["53809877745", "Oak Park Sunset", "Long shadows across the park at golden hour.", 1520, 27, 2, DUSK],
  ["53809112904", "Sky and Cosmos", "Cosmos blooms against an open summer sky.", 980, 19, 1, FOREST],
  ["53808665132", "Sun Through the Leaves", "Backlit maple canopy in early October.", 3390, 64, 7, FOREST],
  ["53808001470", "Winter Morning", "Frost on the field fence just after sunrise.", 2740, 52, 6, NIGHT],
  ["53807445516", "Lady Jane at the Lake", "Portrait by the water, overcast and soft.", 5210, 118, 21, DUSK],
  ["53806998233", "OBX Sunrise 2024", "The whole sky went orange for about four minutes.", 7640, 203, 38, SUNRISE],
  ["53806332107", "Pier Pilings", "Low tide, long exposure, nobody around.", 1180, 22, 0, NIGHT],
  ["53805774890", "Marsh Grass, Evening", "Wind moving through the marsh at dusk.", 1640, 31, 2, FOREST],
];

const photo = (i: number) => {
  const [id, title, description, views, favorites, comments, url_medium] = PHOTO_SEED[i % PHOTO_SEED.length];
  return {
    id,
    title,
    description,
    date_taken: "2024-09-1" + (i % 9) + " 06:4" + (i % 9) + ":00",
    date_uploaded: 1727000000 - i * 86400,
    last_updated: 1727600000 - i * 3600,
    url_photopage: `https://www.flickr.com/photos/photo516/${id}/`,
    url_original: url_medium,
    url_medium,
    tags: "outerbanks sunrise coast northcarolina landscape",
    views,
    favorites,
    comments,
    is_public: 1,
    synced_at: 1727600000,
  };
};

export const PHOTOS = PHOTO_SEED.map((_, i) => photo(i));

// ── route table ───────────────────────────────────────────────────────────

export const ME = {
  nsid: "54321000@N00",
  username: "photo516",
  fullname: "Mr. E",
  csrf_token: "preview-csrf-token",
};

export const STATS = {
  total_photos: 4187,
  public_photos: 3902,
  private_photos: 285,
  total_views: 1284630,
  total_groups: 96,
  total_albums: 48,
  total_contacts: 512,
  date_range: { earliest: "2006-04-11", latest: "2024-09-18" },
  last_synced: 1727598000,
  top_tags: [
    { tag: "landscape", count: 812 },
    { tag: "outerbanks", count: 604 },
    { tag: "sunrise", count: 553 },
    { tag: "northcarolina", count: 497 },
    { tag: "coast", count: 431 },
    { tag: "beach", count: 388 },
    { tag: "lighthouse", count: 246 },
    { tag: "clouds", count: 231 },
    { tag: "sunset", count: 209 },
    { tag: "water", count: 194 },
    { tag: "sky", count: 176 },
    { tag: "trees", count: 158 },
    { tag: "morning", count: 141 },
    { tag: "portrait", count: 127 },
    { tag: "winter", count: 118 },
    { tag: "marsh", count: 104 },
    { tag: "pier", count: 96 },
    { tag: "fog", count: 84 },
    { tag: "autumn", count: 77 },
    { tag: "macro", count: 63 },
  ],
};

const syncRow = (type: string, over: Record<string, Json> = {}) => ({
  type,
  last: 1727598000,
  duration: "1m 12s",
  next: 1727641200,
  running: false,
  phase: null,
  total: 4187,
  pending_summary: 0,
  progress_done: null,
  progress_total: null,
  eta_seconds: null,
  ...over,
});

export const SYNC_IDLE = {
  running: false,
  rows: [
    syncRow("photos"),
    syncRow("groups", { total: 96, duration: "44s" }),
    syncRow("albums", { total: 48, duration: "9s" }),
    syncRow("contacts", { total: 512, duration: "27s" }),
  ],
};

export const SYNC_RUNNING = {
  running: true,
  rows: [
    syncRow("photos", { running: true, phase: "flickr", duration: null, next: null }),
    syncRow("groups", {
      running: true,
      phase: "model",
      total: 96,
      pending_summary: 34,
      progress_done: 62,
      progress_total: 96,
      eta_seconds: 2040,
      duration: null,
      next: null,
    }),
    syncRow("albums", { total: 48, duration: "9s" }),
    syncRow("contacts", { total: 512, duration: "27s" }),
  ],
};

export const LLM_SETTINGS = {
  connections: {
    local: {
      name: "Local (Ollama)",
      kind: "ollama",
      api_mode: "chat_completions",
      base_url: "http://localhost:11434/v1",
      api_key: "",
      timeout_seconds: 300,
      disabled_models: [],
      models: {
        "qwen2.5vl:7b": {
          max_tokens: 2048,
          vision: true,
          temperature: "0.6",
          top_p: "",
          frequency_penalty: "",
          presence_penalty: "",
          seed: "",
          tool_choice: "auto",
          context_window: 32768,
        },
      },
      paused: false,
    },
    workstation: {
      name: "Workstation",
      kind: "openai_compatible",
      api_mode: "responses",
      base_url: "http://192.168.1.42:8080/v1",
      api_key: "sk-preview-not-a-real-key",
      timeout_seconds: 600,
      disabled_models: ["tiny-draft-1b"],
      models: {},
      paused: false,
    },
  },
  active_connection: "local",
  active_model: "qwen2.5vl:7b",
  auto_compact: false,
  sync_connection: "workstation",
  sync_model: "llama3.1:70b",
  sync_throttle_seconds: 60,
};

export const PROMPTS = {
  categories: [
    { id: "metadata", name: "Metadata", description: "Titles, descriptions, tags", sort_order: 1, builtin: true },
    { id: "engagement", name: "Engagement", description: "Comments, faves, follows", sort_order: 2, builtin: true },
    { id: "curation", name: "Curation", description: "Groups, albums, hiding", sort_order: 3, builtin: true },
    { id: "mine", name: "My prompts", description: "Personal additions", sort_order: 4, builtin: false },
  ],
  prompts: [
    {
      id: "p1",
      code: "suggest_title",
      name: "Suggest a title",
      description: "Propose a short, evocative title from the image.",
      category_id: "metadata",
      context: "photo",
      text: "Look at {{photo}} and suggest three titles. Avoid clichés like 'Golden Hour'.",
      builtin: true,
      default_text: "Look at {{photo}} and suggest three titles.",
      enabled: true,
      sort_order: 1,
      created_at: 1710000000,
      updated_at: 1726000000,
    },
    {
      id: "p2",
      code: "suggest_tags",
      name: "Suggest tags",
      description: "Fill gaps in the tag list without repeating existing tags.",
      category_id: "metadata",
      context: "photo",
      text: "Given {{photo}} and its current tags {{tags}}, suggest up to 8 more.",
      builtin: true,
      default_text: null,
      enabled: true,
      sort_order: 2,
      created_at: 1710000000,
      updated_at: 1724000000,
    },
    {
      id: "p3",
      code: "thank_commenter",
      name: "Thank a commenter",
      description: "Write a warm, specific reply to a comment.",
      category_id: "engagement",
      context: "photo",
      text: "Reply to {{comment}} on {{photo}}. Be specific about what they noticed.",
      builtin: true,
      default_text: null,
      enabled: true,
      sort_order: 1,
      created_at: 1710000000,
      updated_at: 1722000000,
    },
    {
      id: "p4",
      code: "group_pitch",
      name: "Pick groups",
      description: "Match a photo to joined groups worth posting it to.",
      category_id: "curation",
      context: "photo",
      text: "Which of {{groups}} suit {{photo}}? Respect each group's thresholds.",
      builtin: true,
      default_text: null,
      enabled: false,
      sort_order: 1,
      created_at: 1710000000,
      updated_at: 1720000000,
    },
    {
      id: "p5",
      code: "obx_voice",
      name: "OBX caption voice",
      description: "My own house style for coastal shots.",
      category_id: "mine",
      context: "photo",
      text: "Describe {{photo}} the way I would: plain, unhurried, no adjectives stacked up.",
      builtin: false,
      default_text: null,
      enabled: true,
      sort_order: 1,
      created_at: 1719000000,
      updated_at: 1727000000,
    },
  ],
  variables: [
    { code: "photo", label: "Photo", description: "The photo under discussion", resolved_by: "current panel", builtin: true },
    { code: "tags", label: "Tags", description: "That photo's current tags", resolved_by: "local database", builtin: true },
    { code: "groups", label: "Groups", description: "Groups you have joined", resolved_by: "local database", builtin: true },
    { code: "comment", label: "Comment", description: "The comment being replied to", resolved_by: "current panel", builtin: true },
  ],
};

const queueRow = (i: number, over: Record<string, Json> = {}) => {
  const p = PHOTOS[i % PHOTOS.length];
  return {
    id: 900 + i,
    photo_id: p.id,
    photo_title: p.title,
    photo_url: p.url_photopage,
    group_id: "34427469792@N01",
    group_name: ["Coastal North Carolina", "Sunrise & Sunset", "Landscape Lovers", "Nikon Shooters"][i % 4],
    group_url: "https://www.flickr.com/groups/coastalnc/",
    retry_at: null,
    queued_at: "2024-09-18 06:12",
    error_msg: "",
    completed_at: null,
    ...over,
  };
};

export const QUEUE = {
  counts: { waiting: 3, success: 24, error: 2 },
  waiting: [queueRow(0), queueRow(1), queueRow(2)],
  errors: [
    queueRow(3, {
      error_msg: "Photo limit reached for this pool (posted 2 of 2 today)",
      retry_at: "2024-09-19 06:00",
    }),
    queueRow(4, { error_msg: "Group requires 25 faves; photo has 19", retry_at: null }),
  ],
  successes: [
    queueRow(5, { completed_at: "2024-09-18 06:31" }),
    queueRow(6, { completed_at: "2024-09-18 06:30" }),
    queueRow(7, { completed_at: "2024-09-17 18:04" }),
  ],
};

export const SETUP = {
  base_url: "http://localhost:8000",
  mcp_url: "http://localhost:8000/mcp",
  sse_url: "http://localhost:8000/sse",
  has_api_key: true,
  snippets: {
    ".mcp.json":
      '{\n  "mcpServers": {\n    "flickr": {\n      "type": "sse",\n      "url": "http://localhost:8000/sse",\n      "headers": { "X-API-Key": "wb_live_9f3c…" }\n    }\n  }\n}',
    "Claude Code":
      'claude mcp add --transport sse flickr http://localhost:8000/sse \\\n  --header "X-API-Key: wb_live_9f3c…"',
  },
  bookmarklet: "javascript:(function(){window.open('http://localhost:8000/photo?u='+encodeURIComponent(location.href))})()",
};

export const SETTINGS = {
  settings: [
    {
      key: "retry_timezone",
      label: "Retry timezone",
      description: "Timezone used when scheduling group-queue retries.",
      default: "UTC",
      value: "America/New_York",
    },
    {
      key: "queue_retry_time",
      label: "Default retry time",
      description: "Time of day a rejected group post is retried.",
      default: "06:00",
      value: "06:00",
    },
    {
      key: "sync_interval_hours",
      label: "Background sync interval",
      description: "Hours between automatic incremental syncs.",
      default: "12",
      value: "12",
    },
  ],
};

export const CONVERSATIONS = {
  conversations: [
    { id: "c1", title: "Tag gaps in the 2024 OBX set", created_at: 1727500000, updated_at: 1727598400, provider: "local", model: "qwen2.5vl:7b" },
    { id: "c2", title: "Which groups for Lighthouse at Manteo?", created_at: 1727400000, updated_at: 1727411000, provider: "local", model: "qwen2.5vl:7b" },
    { id: "c3", title: "Replies for last week's comments", created_at: 1727200000, updated_at: 1727260000, provider: "workstation", model: "llama3.1:70b" },
  ],
};

export const ALBUMS = {
  albums: [
    { id: "72177720319", title: "Outer Banks 2024", description: "A week on Hatteras and Roanoke.", count_photos: 84, count_views: 12400, thumb_url: SUNRISE },
    { id: "72177720320", title: "Lighthouses", description: "Every one I can get to in a day.", count_photos: 37, count_views: 8210, thumb_url: DUSK },
    { id: "72177720321", title: "Around Town", description: "Small-town street work.", count_photos: 122, count_views: 6480, thumb_url: FOREST },
    { id: "72177720322", title: "Night Sky", description: "Long exposures, mostly failures.", count_photos: 29, count_views: 4130, thumb_url: NIGHT },
  ],
};

export const PHOTO_PAGE = { total: 4187, offset: 0, photos: PHOTOS };
export const LIVE_PAGE = { total: 84, page: 1, pages: 7, photos: PHOTOS };

export const PHOTO_DETAIL = {
  ...PHOTOS[9],
  groups: [
    { id: "g1", name: "Coastal North Carolina" },
    { id: "g2", name: "Sunrise & Sunset" },
    { id: "g3", name: "Landscape Lovers" },
  ],
  albums: [
    { id: "72177720319", title: "Outer Banks 2024" },
    { id: "72177720320", title: "Lighthouses" },
  ],
  in_keeper_list: true,
  is_own: true,
};

const DEFAULT_ROUTES: Record<string, Json> = {
  "/api/me": ME,
  "/api/stats": STATS,
  "/api/sync/status": SYNC_IDLE,
  "/api/llm-settings": LLM_SETTINGS,
  "/api/prompts": PROMPTS,
  "/api/prompt-variables": { variables: PROMPTS.variables },
  "/api/queue": QUEUE,
  "/api/setup": SETUP,
  "/api/settings": SETTINGS,
  "/api/photos": PHOTO_PAGE,
  "/api/albums": ALBUMS,
  "/api/chat/conversations": CONVERSATIONS,
  "/api/extension": { installed: false, version: null },
};

// Pattern routes for the `/api/thing/{id}` shapes, tried after exact matches.
const PATTERN_ROUTES: [RegExp, Json][] = [
  [/^\/api\/photos\/[^/]+$/, PHOTO_DETAIL],
  [/^\/api\/albums\/[^/]+\/photos$/, LIVE_PAGE],
  [/^\/api\/chat\/conversations\/[^/]+$/, { messages: [], stats: null }],
  [/^\/api\/users\/[^/]+\/photos$/, LIVE_PAGE],
  [/^\/api\/users\/[^/]+\/albums$/, ALBUMS],
];

/**
 * Replace window.fetch with a routing table so a self-fetching panel renders
 * with real content. Call it in the preview's module scope (or at the top of
 * a cell function, which still runs before the child's effects fire).
 *
 * `extra` merges over the defaults — that's how a cell shows a different
 * state, e.g. stubApi({ "/api/sync/status": SYNC_RUNNING }).
 */
export function stubApi(extra: Record<string, Json> = {}): void {
  const table = { ...DEFAULT_ROUTES, ...extra };
  window.fetch = (async (input: RequestInfo | URL) => {
    const raw =
      typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
    const path = raw.split("?")[0].replace(/^https?:\/\/[^/]+/, "");
    let body: Json = Object.prototype.hasOwnProperty.call(table, path) ? table[path] : undefined;
    if (body === undefined) {
      for (const [rx, value] of PATTERN_ROUTES) {
        if (rx.test(path)) {
          body = value;
          break;
        }
      }
    }
    // Unmapped endpoints (POST actions, anything added later) answer with a
    // bland OK rather than an error, so a screen never renders failure text.
    return new Response(JSON.stringify(body ?? { ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

/** theme() + stubApi() in one call — what a screen preview almost always wants. */
export function screen(extra: Record<string, Json> = {}): void {
  theme();
  stubApi(extra);
}
