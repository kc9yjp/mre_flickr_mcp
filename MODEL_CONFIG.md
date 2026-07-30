# Workbench LLM Settings

## Architecture

Each user's LLM config is stored at `~/.flickr_mcp/{nsid}/llm.json`
(`scripts/agent/settings.py`).

Settings have two layers:
1. **Connections** — any number of user-named connections (e.g. "Ollama",
   "OpenCode Zen", "My LM Studio"), each with its own `base_url`, `api_key`,
   `api_mode`, and `disabled_models` list.
2. **Global output/sampling params** — shared across all connections
   (`max_tokens`, `vision`, `temperature`, `tool_choice`, etc.).

The currently selected connection and model are saved per-conversation in
`chat.db` (`store.py`), so returning to an older conversation automatically
restores its model. The **Models panel** (⚙) in the chat header sets the
connection/model for new conversations.

Any field left blank is **omitted from the request** — the connection's own
default applies.

## Connections

A connection has:
- **Name** — free text, shown in the chat header selector (`"Name: model"`).
- **Kind** — `ollama` or `openai_compatible`. Cosmetic only: it just picks a
  default base URL when quick-adding a connection. It has no effect on
  request behavior.
- **API mode** — `chat_completions` or `responses` (see below). This is what
  actually controls the wire format used.
- **Base URL** — API endpoint (must end in `/v1`).
- **API key** — Bearer token (blank for Ollama, required for most others).
- **Disabled models** — a per-connection list of model ids to hide from the
  chat selector, editable via checkboxes in the Models panel after fetching
  the connection's model list.

Zen, LM Studio, and a blank Custom entry are **quick-add presets**, not
distinct connection types — clicking one just prefills name/base URL/kind/
api_mode for a new `openai_compatible` connection:

| Preset | Default base URL |
|--------|-------------------|
| Ollama | `http://host.docker.internal:11434/v1` |
| OpenCode Zen | `https://opencode.ai/zen/v1` |
| LM Studio | `http://host.docker.internal:1234/v1` |
| Custom | (blank) |

A brand-new install only gets a default **Ollama** connection; the others
are added on demand from the Models panel's "Quick add" buttons (fetched
from `GET /api/llm-connection-presets`).

### Adding/editing/removing connections

- **Add**: Models panel → "Add connection" → pick a quick-add preset (or
  fill in the fields manually) → "Add connection". Calls
  `POST /api/llm-connections`.
- **Edit** (name/base_url/api_key/api_mode): edit inline in the connection's
  card, then hit the panel's main **Save** button (whole-object
  `POST /api/llm-settings`, same as other settings).
- **Disable specific models**: "Fetch models" on a connection, uncheck any
  models to hide, then that connection's own "Save models" button
  (`POST /api/llm-connections/{id}/update`) — a separate, immediate save so
  toggling checkboxes doesn't get bundled with unrelated pending edits.
- **Delete**: "Delete" button on the connection card
  (`POST /api/llm-connections/{id}/delete`). If it was the active
  connection, `active_connection` is cleared rather than left dangling.

## Model listing

The Models panel and chat header fetch available models via:
```
GET /api/llm-models?connection=<id>
```
This calls the connection's `GET /v1/models` endpoint (OpenAI-compatible,
supported by Ollama, Zen, and LM Studio alike) and returns two lists:
`models` (filtered — excludes anything in that connection's
`disabled_models`) and `all_models` (unfiltered, so the Models panel can
still render a checkbox for an already-disabled model).

## Chat Completions vs. Responses

Two wire formats are supported, selected per-connection by **API mode**:

- **`chat_completions`** (`/v1/chat/completions`) — the default, and the only
  mode most local backends need. Widely supported.
- **`responses`** (`/v1/responses`) — OpenAI's newer API. Required for some
  models (e.g. OpenCode Zen's GPT-5.x line, which only serves them over
  `/v1/responses`). Support varies by backend:
  - **Ollama** (v0.13.3+) supports only the *non-stateful* flavor — no
    `previous_response_id`.
  - **LM Studio** supports the full *stateful* flavor.
  - This app always talks to `/v1/responses` **statelessly** — it resends
    the full translated conversation on every turn, the same way
    `chat_completions` mode already works here, and never sends
    `previous_response_id`. This works against both backend flavors, but
    means we don't get the payload-size/latency/reasoning-continuity
    benefits a stateful integration would provide. That's a deliberate,
    documented scope cut — see `scripts/agent/llm.py`'s `stream_responses`
    docstring — not an oversight.

If a model only works over `/v1/responses`, set that connection's API mode
to `responses`; if you're not sure, leave it on `chat_completions` and only
switch if a model returns errors.

## Per-conversation model selection

Each conversation in `chat.db` stores the `provider` (a connection id — the
column name predates the v3 "connections" rename and was kept to avoid a
schema migration) and `model` that were active when the first message was
sent. Opening that conversation later restores its connection/model into the
chat header selector. Sending a message in an existing conversation uses
that conversation's connection/model unless the user explicitly changes it
in the selector.

## Sync jobs model selection

Background sync jobs that call an LLM (currently the AI group-summary phase
of `sync --type=groups`) use a separate `sync_connection`/`sync_model` pick,
configurable from a dropdown on the **Sync** page — deliberately independent
of `active_connection`/`active_model` so switching your chat model doesn't
silently change what sync jobs use. Leaving it on "Use chat model" (the
default — both fields empty) falls back to the active chat connection/model
at call time. This is a single global preference shared by every such sync
job, not a per-run picker. See `resolve_sync_cfg()` in `scripts/agent/settings.py`.

## Connection fields

| Field | Valid values | Effect |
|---|---|---|
| **Model** | Whatever the connection exposes (e.g. `big-pickle`, `grok-4.5`, `llama3.1`) | Sent as the `model` field. No validation — a typo just gets a 404/model-not-found from the backend. |
| **API key** | Backend key, or blank | Sent as `Authorization: Bearer <key>` if set. Leave blank for Ollama (no auth). |

## Output

| Field | Valid values | Default | Effect |
|---|---|---|---|
| **Max tokens** | Positive integer | `1024` | Caps response length (`max_tokens` for chat_completions, `max_output_tokens` for responses). Too low truncates mid-tool-call or mid-answer; raise it if you see cut-off JSON in a tool card. |
| **Enable vision** | on/off | off | Sends photo images to the model as image content blocks. Only turn on if the model actually supports vision — off by default because a non-vision model asked to "look at" an image will confidently describe one anyway (hallucination, not a refusal). |
| **Base prompt** | Free text | empty | Prepended as standing instructions for this account (e.g. location, tone, group preferences). Applies to every conversation. |

## Sampling & tool use

All blank/`auto` unless you set them. These are **global**, not
per-connection — shared across whichever connection/model you pick.

| Field | Valid range | Effect | When to touch it |
|---|---|---|---|
| **Temperature** | `0.0`–`2.0` (backend-dependent; most treat `1.0` as neutral) | Lower = more deterministic/conservative token choices. Higher = more varied, more prone to confident-sounding but wrong statements. | Drop to `0.1`–`0.3` if the model is fabricating explanations, inventing tool capabilities, or otherwise confabulating. Don't expect this alone to fix it — it reduces variance, not the underlying tendency to guess. |
| **Top P** | `0.0`–`1.0` | Nucleus sampling: only sample from the smallest token set whose cumulative probability ≥ this value. Lower = narrower, safer choices. | Usually leave blank. Use *either* temperature *or* top_p, not both — stacking them compounds unpredictability. |
| **Frequency penalty** | `-2.0`–`2.0` | Positive values discourage repeating the same tokens verbatim. | Raise slightly (`0.2`–`0.5`) if the model loops or repeats phrases in long tool-calling sessions. |
| **Presence penalty** | `-2.0`–`2.0` | Positive values discourage returning to topics already covered. | Rarely needed for tool-calling workflows; more relevant to long free-form chat. |
| **Seed** | Any integer | Requests reproducible output for the same input. | Useful for debugging a specific bad response — set a seed, reproduce it, tweak the prompt, compare. Only works if the model/backend actually honors it (not all do). |
| **Tool choice** | `auto` \| `required` \| `none` | `auto`: model decides whether to call a tool. `required`: model must emit a tool call (no plain-text turn). `none`: tool calls disabled for this request even if tools are defined. | Set `required` when the model describes an action in prose instead of actually calling the tool (e.g. "I've updated the description" with no `update_photo` call). Set `none` for a quick sanity-check chat with no side effects possible. |

## Known limitation

**Not every backend honors every field.** These are the standard OpenAI
sampling parameters — Ollama's OpenAI-compat layer forwards
`temperature`/`top_p`/`seed` to the model's runtime options, but
`tool_choice: required` support depends on whether that specific model's
chat template implements forced tool calls at all. If setting a field has
no visible effect, check the backend's docs for that model before assuming
it's misconfigured on this end.

## Migration from pre-v3 installs

Older `llm.json` files (a flat single-provider v1 file, or a v2 file with a
fixed `providers: {ollama, zen}` dict) are migrated automatically on first
load — no action needed. The old provider ids (`ollama`, `zen`) are reused
verbatim as the new connection ids, so any existing conversations' stored
`provider`/`model` values keep resolving correctly. A migrated Zen
connection is seeded with the same `disabled_models` the old hardcoded
`/v1/responses`-only exclusion used, so its visible model list doesn't
change on upgrade.

## Where this is implemented

- Storage + defaults + connection profiles: `scripts/agent/settings.py` (`CONNECTION_PRESETS`, `DEFAULT_CONNECTIONS`, `DEFAULTS`, `load_settings`, `save_settings`, `resolve_cfg`, `resolve_sync_cfg`, `create_connection`, `update_connection`, `delete_connection`)
- Chat Completions + Responses clients: `scripts/agent/llm.py` (`stream_chat`, `stream_responses`, `list_models`)
- Per-conversation model: `scripts/agent/store.py` (`conversations.provider`, `conversations.model` columns)
- HTTP API: `scripts/agent/routes.py` (`GET/POST /api/llm-settings`, `GET /api/llm-models`, `GET /api/llm-connection-presets`, `POST /api/llm-connections`, `POST /api/llm-connections/{id}/update`, `POST /api/llm-connections/{id}/delete`)
- Connection + model selector in chat header: `frontend/src/panels/Chat.tsx`
- Connection editor: `frontend/src/panels/ModelsPage.tsx` (registered as dockview panel `models`)
- Sync jobs' connection + model selector: `frontend/src/panels/SyncPage.tsx`, saved via the same `POST /api/llm-settings`
- Frontend types: `frontend/src/api.ts` (`LLMSettings`, `Connection`, `ConnectionPreset`, `listModels`, `createConnection`, `updateConnection`, `deleteConnection`)
