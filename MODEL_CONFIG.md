# Workbench LLM Settings

## Architecture

Each user's LLM config is stored at `~/.flickr_mcp/{nsid}/llm.json`
(`scripts/agent/settings.py`).

Settings have two layers:
1. **Provider profiles** — named connections (e.g. `ollama`, `zen`), each with
   its own `base_url` and `api_key`.
2. **Global output/sampling params** — shared across all providers (`max_tokens`,
   `vision`, `temperature`, `tool_choice`, etc.).

The currently selected provider and model are saved per-conversation in
`chat.db` (`store.py`), so returning to an older conversation automatically
restores its model. The **Models panel** (⚙) in the chat header sets the
provider/model for new conversations.

Any field left blank is **omitted from the request** — the provider's own
default applies.

## Provider profiles

Pre-configured providers:

| ID | Label | Default base URL |
|----|-------|-----------------|
| `ollama` | Ollama | `http://host.docker.internal:11434/v1` |
| `zen` | OpenCode Zen | `https://opencode.ai/zen/v1` |

Each profile has:
- **Base URL** — API endpoint (must end in `/v1`)
- **API key** — Bearer token (blank for Ollama, required for Zen)

### Adding a custom provider

Add a new entry to the `providers` dict in `llm.json`, or use the Models
panel UI to add one — though currently the UI only shows `ollama` and `zen`.
To add a third provider, edit the file directly or add it to
`DEFAULT_PROVIDERS` in `settings.py`.

## Model listing

The Models panel and chat header fetch available models via:
```
GET /api/llm-models?provider=<id>
```
This calls the provider's `GET /v1/models` endpoint (OpenAI-compatible,
supported by both Ollama and Zen).

### Zen model caveat

OpenCode Zen's GPT-5.x models use the `/v1/responses` endpoint, which the
current chat-completions client (`llm.py`) does **not** support. Only models
using `/v1/chat/completions` will work. Models like `gpt-5`, `gpt-5.5`, etc.
will return errors if selected. Use `big-pickle`, `grok-4.5`, or other
chat-completions models.

## Per-conversation model selection

Each conversation in `chat.db` stores the `provider` and `model` that were
active when the first message was sent. Opening that conversation later
restores its provider/model into the chat header selector. Sending a message
in an existing conversation uses that conversation's model unless the user
explicitly changes it in the selector.

## Connection fields

| Field | Valid values | Effect |
|---|---|---|
| **Model** | Whatever the provider expects (e.g. `big-pickle`, `grok-4.5`, `llama3.1`) | Sent as the `model` field. No validation — a typo just gets a 404/model-not-found from the provider. |
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
| **Top P** | `0.0`–`1.0` | Nucleus sampling: only sample from the smallest token set whose cumulative probability ≥ this value. Lower = narrower, safer choices. | Usually leave blank. Use *either* temperature *or* top_p, not both — stacking them compounds unpredictability. |
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

- Storage + defaults + provider profiles: `scripts/agent/settings.py` (`DEFAULT_PROVIDERS`, `DEFAULTS`, `load_settings`, `save_settings`, `resolve_cfg`)
- Model listing: `scripts/agent/llm.py` (`list_models`)
- Per-conversation model: `scripts/agent/store.py` (`conversations.provider`, `conversations.model` columns)
- HTTP API: `scripts/agent/routes.py` (`GET/POST /api/llm-settings`, `GET /api/llm-models`)
- Provider + model selector in chat header: `frontend/src/panels/Chat.tsx`
- Provider profile editor: `frontend/src/panels/ModelsPage.tsx` (registered as dockview panel `models`)
- Frontend types: `frontend/src/api.ts` (`LLMSettings`, `ProviderProfile`, `listModels`)
