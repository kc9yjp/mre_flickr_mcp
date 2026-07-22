# Workbench LLM Settings

Reference for the ⚙ settings panel on the Chat panel (`frontend/src/panels/Chat.tsx`).
These settings are per-account, stored at `~/.flickr_mcp/{nsid}/llm.json`
(`scripts/agent/settings.py`), and sent as-is to whatever OpenAI-compatible
`/chat/completions` endpoint you point at (`scripts/agent/llm.py`).

Any field left blank is **omitted from the request** — the provider's own
default applies. That's deliberate: forcing a value you haven't tuned can
make things worse, especially on a small local model.

## Connection

| Field | Valid values | Effect |
|---|---|---|
| **API base URL** | A URL ending in `/v1` (e.g. `http://host.docker.internal:11434/v1` for Ollama from Docker, or `https://api.openai.com/v1`) | `/chat/completions` is appended to this to build the request URL. |
| **Model** | Whatever the provider expects (e.g. `gemma3`, `llama3.1`, `gpt-4o`) | Sent as the `model` field. No validation — a typo just gets a 404/model-not-found from the provider. |
| **API key** | Provider key, or blank | Sent as `Authorization: Bearer <key>` if set. Leave blank for Ollama (no auth). |

## Output

| Field | Valid values | Default | Effect |
|---|---|---|---|
| **Max tokens** | Positive integer | `1024` | Caps response length (`max_tokens`). Too low truncates mid-tool-call or mid-answer; raise it if you see cut-off JSON in a tool card. |
| **Enable vision** | on/off | off | Sends photo images to the model as image content blocks. Only turn on if the model actually supports vision — off by default because a non-vision model asked to "look at" an image will confidently describe one anyway (hallucination, not a refusal). |
| **Base prompt** | Free text | empty | Prepended as standing instructions for this account (e.g. location, tone, group preferences). Applies to every conversation. |

## Sampling & tool use

All blank/`auto` unless you set them.

| Field | Valid range | Effect | When to touch it |
|---|---|---|---|
| **Temperature** | `0.0`–`2.0` (provider-dependent; most treat `1.0` as neutral) | Lower = more deterministic/conservative token choices. Higher = more varied, more prone to confident-sounding but wrong statements. | Drop to `0.1`–`0.3` if the model is fabricating explanations, inventing tool capabilities, or otherwise confabulating. Don't expect this alone to fix it — it reduces variance, not the underlying tendency to guess. |
| **Top P** | `0.0`–`1.0` | Nucleus sampling: only sample from the smallest token set whose cumulative probability ≥ this value. Lower = narrower, safer choices. | Usually leave blank. Use *either* temperature *or* top_p, not both — stacking them compounds unpredictably. |
| **Frequency penalty** | `-2.0`–`2.0` | Positive values discourage repeating the same tokens verbatim. | Raise slightly (`0.2`–`0.5`) if the model loops or repeats phrases in long tool-calling sessions. |
| **Presence penalty** | `-2.0`–`2.0` | Positive values discourage returning to topics already covered. | Rarely needed for tool-calling workflows; more relevant to long free-form chat. |
| **Seed** | Any integer | Requests reproducible output for the same input. | Useful for debugging a specific bad response — set a seed, reproduce it, tweak the prompt, compare. Only works if the model/provider actually honors it (not all do). |
| **Tool choice** | `auto` \| `required` \| `none` | `auto`: model decides whether to call a tool. `required`: model must emit a tool call (no plain-text turn). `none`: tool calls disabled for this request even if tools are defined. | Set `required` when the model describes an action in prose instead of actually calling the tool (e.g. "I've updated the description" with no `update_photo` call). Set `none` for a quick sanity-check chat with no side effects possible. |

## Known limitation

**Not every backend honors every field.** These are the standard OpenAI
`/chat/completions` parameters — Ollama's OpenAI-compat layer forwards
`temperature`/`top_p`/`seed` to the model's runtime options, but
`tool_choice: required` support depends on whether that specific model's
chat template implements forced tool calls at all. If setting a field has
no visible effect, check the provider's docs for that model before assuming
it's misconfigured on this end.

## Where this is implemented

- Storage + defaults: `scripts/agent/settings.py` (`DEFAULTS`, `load_settings`, `save_settings`)
- Request construction: `scripts/agent/llm.py` (`stream_chat`, `_add_sampling_params`)
- HTTP API: `scripts/agent/routes.py` (`GET`/`POST /api/llm-settings`)
- UI form: `frontend/src/panels/Chat.tsx` (`SettingsForm`)
- Frontend type: `frontend/src/api.ts` (`LLMSettings`)
