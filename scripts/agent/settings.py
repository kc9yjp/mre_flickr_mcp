"""Per-user LLM connection settings, stored next to the OAuth credentials.

``~/.flickr_mcp/{nsid}/llm.json`` — kept out of the resettable data DB and
inside the already-volume-mounted credentials directory.

Storage shape (v4 — named connections, per-model settings)::

    {
      "schema_version": 4,
      "connections": {
        "ollama": {"name": "Ollama", "kind": "ollama", "api_mode": "chat_completions",
                    "base_url": "http://host.docker.internal:11434/v1", "api_key": "",
                    "timeout_seconds": 300, "disabled_models": [],
                    "models": {
                      "llama3.1": {"max_tokens": 1024, "vision": false,
                                    "temperature": "", "top_p": "", "frequency_penalty": "",
                                    "presence_penalty": "", "seed": "", "tool_choice": "auto",
                                    "context_window": 128000}
                    }},
        "zen":    {"name": "OpenCode Zen", "kind": "openai_compatible", "api_mode": "chat_completions",
                    "base_url": "https://opencode.ai/zen/v1", "api_key": "...",
                    "disabled_models": ["gpt-5", "..."], "models": {}, "paused": false,
                    "tool_set": "all"}
      },
      "active_connection": "ollama",
      "active_model": "",
      "auto_compact": false
    }

A connection's ``kind`` (``ollama`` | ``openai_compatible``) only picks a
default base_url/preset at creation time — it has no effect on request
behavior. ``api_mode`` (``chat_completions`` | ``responses``) is what
``llm.py``/``loop.py`` actually branch on.

``timeout_seconds`` is the read timeout for one streamed turn on this
connection — how long to wait for the next chunk of data before giving up.
It lives on the connection (not per-model) because it's a transport
property: local backends (Ollama, LM Studio) doing slow prompt processing on
modest hardware need it much higher than a fast cloud endpoint does. Defaults
to 300s for a freshly created connection. Never applies to the quick
``list_models`` metadata fetch, which uses its own short fixed timeout.

``max_tokens``/``vision``/``temperature``/``top_p``/``frequency_penalty``/
``presence_penalty``/``seed``/``tool_choice``/``context_window`` (the
``DEFAULTS`` keys) live per model, inside
``connections[cid]["models"][model_id]``. A model absent from that dict
simply uses ``DEFAULTS`` — entries are only created when a user edits that
model's settings and saves. ``context_window`` is never sent to the
connection; it only feeds loop.py's auto-compact threshold and the chat
stats "context used" readout.

``tool_set`` (default ``"all"``, the only other value ``"limited"``) picks
which MCP tool schemas are offered to the model on this connection: ``"all"``
sends the full catalog, ``"limited"`` sends only schema.LIMITED_TOOL_NAMES — a
curated subset for small/local models that lose accuracy (wrong tool picked,
a required call skipped) as the tool list grows. See schema.to_openai_tools().

``paused`` (default ``false``) marks a connection as kept-but-unused: it
stays fully configured and editable, but is skipped by the "first connection"
fallback in ``resolve_cfg`` and by the frontend's connection selectors/eager
model-fetch, so a connection that's regularly unreachable (a local backend
that's often off) doesn't get auto-picked or hammered with requests. It has
no effect on an *explicit* connection_id passed to ``resolve_cfg`` — a
conversation already pinned to a paused connection, or a deliberate manual
pick, still resolves normally. Pausing the current ``active_connection`` (or
``sync_connection``) clears that pointer so the fallback takes over, the same
way ``delete_connection`` does.

``auto_compact`` is a top-level, per-user flag (default ``false``): when on,
``loop.run_turn`` compacts a conversation's history into an LLM-written
summary once it's estimated to cross ~80% of the active model's
``context_window``, before sending the next turn.

Older files migrate on load: a flat v1 file (no ``providers``/``connections``
key) becomes a default ``ollama`` connection; a v2 file (``providers`` dict
keyed by a fixed provider id) becomes a v3 ``connections`` dict, reusing each
old provider id verbatim as the new connection id so any conversation rows
that already reference it keep resolving; a v3 file (flat top-level
``DEFAULTS`` keys) becomes v4 by moving those values into the active
connection's active model entry, so upgrading doesn't silently reset a
user's current vision/sampling choice.
"""

import copy
import json
import os

from flickr_api import _CREDS_BASE

# ── Connection presets ───────────────────────────────────────────────────────
# Used only for "quick add" defaults (frontend) and to seed suggested
# disabled_models for a freshly created connection of that preset. Presets
# are not persisted as a field on the connection itself.

CONNECTION_PRESETS: dict[str, dict] = {
    "ollama": {
        "label": "Ollama",
        "base_url": "http://host.docker.internal:11434/v1",
        "kind": "ollama",
        "api_mode": "chat_completions",
    },
    "zen": {
        "label": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "kind": "openai_compatible",
        "api_mode": "chat_completions",
    },
    "lmstudio": {
        "label": "LM Studio",
        "base_url": "http://host.docker.internal:1234/v1",
        "kind": "openai_compatible",
        "api_mode": "chat_completions",
    },
    "custom": {
        "label": "Custom",
        "base_url": "",
        "kind": "openai_compatible",
        "api_mode": "chat_completions",
    },
}

# Fresh-install seed. A brand-new user (no llm.json at all) only gets an
# Ollama connection by default — Zen/LM Studio/Custom are quick-add presets
# (CONNECTION_PRESETS above), not auto-created connections.
DEFAULT_CONNECTIONS: dict[str, dict] = {
    "ollama": {
        "name": "Ollama",
        "kind": "ollama",
        "api_mode": "chat_completions",
        "base_url": CONNECTION_PRESETS["ollama"]["base_url"],
        "api_key": "",
        "timeout_seconds": 300,
        "disabled_models": [],
        "models": {},
        "paused": False,
        "tool_set": "all",
    },
}

# Valid values for a connection's "tool_set" field. See the module docstring.
TOOL_SETS: frozenset[str] = frozenset({"all", "limited"})

# Read timeout (seconds) a freshly created connection starts with — long
# enough for slow local prompt processing (see timeout_seconds note above).
DEFAULT_TIMEOUT_SECONDS = 300

DEFAULTS = {
    "max_tokens": 1024,
    "vision": False,
    # Sampling / tool-use controls. Blank ("") means "omit from the request
    # and let the connection use its own default".
    "temperature": "",
    "top_p": "",
    "frequency_penalty": "",
    "presence_penalty": "",
    "seed": "",
    "tool_choice": "auto",  # auto | required | none
    # Total context size this model can hold — used only client-side (by
    # loop.py's auto-compact check and the chat stats "context used" readout),
    # never sent to the connection. Not something we can query from an
    # OpenAI-compatible /v1/models endpoint, so it defaults to a
    # conservative guess the user can override per model.
    "context_window": 128_000,
}

# Default pause between successive group-summary LLM calls within one sync
# run — see "sync_throttle_seconds" in _merge_defaults()/resolve_sync_cfg().
DEFAULT_SYNC_THROTTLE_SECONDS = 60

# ── Zen per-model wire formats ───────────────────────────────────────────────
# OpenCode Zen serves each model over exactly ONE endpoint, and the endpoint's
# wire format varies by model family. Source of truth:
# https://opencode.ai/docs/zen#endpoints (Zen's own /v1/models list exposes no
# api-type field, so this static map is the only way to know which format a
# given model needs). Four formats are in use:
#   responses        -> /v1/responses           (gpt-*, grok-*)
#   messages         -> /v1/messages            (claude-*, qwen*)   [Anthropic]
#   chat_completions -> /v1/chat/completions    (deepseek/minimax/glm/kimi/...)
#   gemini           -> /v1/models/{model-id}   (gemini-*)          [per-model URL]
ZEN_MODEL_API_MODES: dict[str, str] = {
    # Responses API (OpenAI /v1/responses)
    "gpt-5.6-sol": "responses", "gpt-5.6-terra": "responses", "gpt-5.6-luna": "responses",
    "gpt-5.5": "responses", "gpt-5.5-pro": "responses",
    "gpt-5.4": "responses", "gpt-5.4-pro": "responses", "gpt-5.4-mini": "responses",
    "gpt-5.4-nano": "responses",
    "gpt-5.3-codex": "responses", "gpt-5.3-codex-spark": "responses",
    "gpt-5.2": "responses", "gpt-5.2-codex": "responses",
    "gpt-5.1": "responses", "gpt-5.1-codex": "responses", "gpt-5.1-codex-max": "responses",
    "gpt-5.1-codex-mini": "responses",
    "gpt-5": "responses", "gpt-5-codex": "responses", "gpt-5-nano": "responses",
    "grok-4.5": "responses", "grok-build-0.1": "responses",
    # Messages API (Anthropic /v1/messages)
    "claude-fable-5": "messages", "claude-opus-5": "messages", "claude-opus-4-8": "messages",
    "claude-opus-4-7": "messages", "claude-opus-4-6": "messages", "claude-opus-4-5": "messages",
    "claude-sonnet-5": "messages", "claude-sonnet-4-6": "messages", "claude-sonnet-4-5": "messages",
    "claude-sonnet-4": "messages", "claude-haiku-4-5": "messages",
    "qwen3.7-max": "messages", "qwen3.7-plus": "messages",
    "qwen3.6-plus": "messages", "qwen3.5-plus": "messages",
    # Gemini (per-model URL /v1/models/{id})
    "gemini-3.6-flash": "gemini", "gemini-3.5-flash": "gemini",
    "gemini-3.5-flash-lite": "gemini", "gemini-3.1-pro": "gemini", "gemini-3-flash": "gemini",
    # Chat Completions (/v1/chat/completions) — deepseek/minimax/glm/kimi/big-pickle/free
    "deepseek-v4-pro": "chat_completions", "deepseek-v4-flash": "chat_completions",
    "minimax-m3": "chat_completions", "minimax-m2.7": "chat_completions",
    "minimax-m2.5": "chat_completions",
    "glm-5.2": "chat_completions", "glm-5.1": "chat_completions", "glm-5": "chat_completions",
    "kimi-k2.5": "chat_completions", "kimi-k2.6": "chat_completions",
    "kimi-k2.7-code": "chat_completions", "kimi-k3": "chat_completions",
    "big-pickle": "chat_completions",
    "mimo-v2.5-free": "chat_completions", "laguna-s-2.1-free": "chat_completions",
    "ling-3.0-flash-free": "chat_completions", "ling-3.0-tiny-free": "chat_completions",
    "longcat-2.0-free": "chat_completions", "north-mini-code-free": "chat_completions",
    "nemotron-3-ultra-free": "chat_completions", "deepseek-v4-flash-free": "chat_completions",
}

# Fallback wire format for a Zen model not (yet) in ZEN_MODEL_API_MODES — e.g.
# one Zen added after this map was last updated. Chat Completions is the most
# widely served flavor, so an unknown model degrades to it rather than breaking.
ZEN_DEFAULT_API_MODE = "chat_completions"


def is_zen_connection(base_url: str) -> bool:
    """True if a connection's base_url points at OpenCode Zen."""
    return "opencode.ai/zen" in (base_url or "").lower()


def zen_api_mode_for_model(model_id: str) -> str:
    """Resolve the wire format a Zen model is served over.

    Returns the mapped format, or ZEN_DEFAULT_API_MODE for an unmapped model.
    Only meaningful for Zen connections — see is_zen_connection().
    """
    return ZEN_MODEL_API_MODES.get(model_id, ZEN_DEFAULT_API_MODE)


def suggested_disabled_models(kind: str, base_url: str = "") -> set[str]:
    """Default disabled-models seed for a newly created connection.

    Now that every Zen wire format is supported (see ZEN_MODEL_API_MODES), no
    Zen models need disabling on creation — this remains only as a hook for any
    future connection type that genuinely can't serve some of its models.
    """
    return set()


def settings_file(nsid: str) -> str:
    return os.path.join(_CREDS_BASE, nsid, "llm.json")


# ── Low-level load / save ────────────────────────────────────────────────────


def _raw_load(nsid: str) -> dict:
    """Load the raw JSON blob, falling back to empty dict."""
    try:
        with open(settings_file(nsid)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_settings(nsid: str) -> dict:
    """Return the full settings dict (v4 shape), migrating older files on the fly."""
    raw = _raw_load(nsid)
    migrated = _maybe_migrate(raw)
    return _merge_defaults(migrated)


def _write_settings(nsid: str, data: dict) -> None:
    path = settings_file(nsid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=2)


def save_settings(nsid: str, data: dict) -> dict:
    """Merge *data* over the stored settings and persist. Returns the result.

    Per-connection ``api_key`` values equal to their masked placeholder are
    ignored so the UI can round-trip settings without ever seeing the real key.
    A connection key merely absent from ``data["connections"]`` is left
    untouched (never deleted) — deletion only happens via delete_connection().
    """
    current = load_settings(nsid)

    # ── merge active connection / model ──────────────────────────────────
    if "active_connection" in data:
        current["active_connection"] = data["active_connection"]
    if "active_model" in data:
        current["active_model"] = data["active_model"]
    if "auto_compact" in data:
        current["auto_compact"] = bool(data["auto_compact"])
    if "sync_connection" in data:
        current["sync_connection"] = data["sync_connection"]
    if "sync_model" in data:
        current["sync_model"] = data["sync_model"]
    if "sync_throttle_seconds" in data:
        try:
            current["sync_throttle_seconds"] = max(0, int(data["sync_throttle_seconds"]))
        except (TypeError, ValueError):
            pass

    # ── merge connections ────────────────────────────────────────────────
    incoming = data.get("connections") or {}
    stored = current.get("connections") or {}
    for cid, conn in incoming.items():
        base = copy.deepcopy(stored.get(cid, {}))
        for field in ("name", "base_url", "kind", "api_mode"):
            if field in conn:
                base[field] = conn[field]
        if "timeout_seconds" in conn:
            base["timeout_seconds"] = _coerce_timeout(conn["timeout_seconds"])
        if "disabled_models" in conn:
            base["disabled_models"] = list(conn["disabled_models"])
        if "paused" in conn:
            base["paused"] = bool(conn["paused"])
        if "tool_set" in conn:
            base["tool_set"] = _coerce_tool_set(conn["tool_set"])
        # mask-guard the api_key
        if "api_key" in conn:
            old_key = stored.get(cid, {}).get("api_key", "")
            if conn["api_key"] != _mask_key(old_key):
                base["api_key"] = conn["api_key"]
        stored[cid] = base
    current["connections"] = stored
    _clear_dangling_picks_if_paused(current)

    _write_settings(nsid, current)
    return current


def _slugify(name: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in name.strip().lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "connection"


def create_connection(
    nsid: str,
    name: str,
    kind: str,
    base_url: str,
    api_key: str = "",
    api_mode: str = "chat_completions",
    timeout_seconds: int | None = None,
    tool_set: str = "all",
) -> tuple[str, dict]:
    """Create a new named connection with a unique generated id.

    Returns (connection_id, masked full settings dict).
    """
    current = load_settings(nsid)
    connections = current.get("connections") or {}

    base_slug = _slugify(name)
    cid = base_slug
    n = 2
    while cid in connections:
        cid = f"{base_slug}-{n}"
        n += 1

    connections[cid] = {
        "name": name,
        "kind": kind,
        "api_mode": api_mode,
        "base_url": base_url,
        "api_key": api_key,
        "timeout_seconds": _coerce_timeout(timeout_seconds),
        "disabled_models": sorted(suggested_disabled_models(kind, base_url)),
        "models": {},
        "paused": False,
        "tool_set": _coerce_tool_set(tool_set),
    }
    current["connections"] = connections
    _write_settings(nsid, current)
    return cid, masked(current)


def update_connection(nsid: str, connection_id: str, patch: dict) -> dict | None:
    """Patch an existing connection. Returns masked full settings, or None if unknown."""
    current = load_settings(nsid)
    connections = current.get("connections") or {}
    conn = connections.get(connection_id)
    if conn is None:
        return None

    for field in ("name", "base_url", "kind", "api_mode"):
        if field in patch:
            conn[field] = patch[field]
    if "timeout_seconds" in patch:
        conn["timeout_seconds"] = _coerce_timeout(patch["timeout_seconds"])
    if "disabled_models" in patch:
        conn["disabled_models"] = list(patch["disabled_models"])
    if "paused" in patch:
        conn["paused"] = bool(patch["paused"])
    if "tool_set" in patch:
        conn["tool_set"] = _coerce_tool_set(patch["tool_set"])
    if "api_key" in patch:
        if patch["api_key"] != _mask_key(conn.get("api_key", "")):
            conn["api_key"] = patch["api_key"]

    connections[connection_id] = conn
    current["connections"] = connections
    _clear_dangling_picks_if_paused(current)
    _write_settings(nsid, current)
    return masked(current)


def update_model_settings(nsid: str, connection_id: str, model: str, patch: dict) -> dict | None:
    """Patch a model's per-model settings (the ``DEFAULTS`` keys) within a
    connection. Creates the entry (seeded from ``DEFAULTS``) if this is the
    first time this model has been customized. Returns masked full settings,
    or None if the connection is unknown."""
    current = load_settings(nsid)
    connections = current.get("connections") or {}
    conn = connections.get(connection_id)
    if conn is None:
        return None

    models = conn.setdefault("models", {})
    entry = {**DEFAULTS, **models.get(model, {})}
    for key in DEFAULTS:
        if key in patch:
            entry[key] = patch[key]
    entry["max_tokens"] = int(entry["max_tokens"] or DEFAULTS["max_tokens"])
    entry["context_window"] = int(entry.get("context_window") or DEFAULTS["context_window"])
    entry["vision"] = bool(entry.get("vision", False))
    models[model] = entry

    connections[connection_id] = conn
    current["connections"] = connections
    _write_settings(nsid, current)
    return masked(current)


def reset_model_settings(nsid: str, connection_id: str, model: str) -> dict | None:
    """Remove a model's per-model settings override (falls back to
    ``DEFAULTS`` again). Returns masked full settings, or None if the
    connection is unknown."""
    current = load_settings(nsid)
    connections = current.get("connections") or {}
    conn = connections.get(connection_id)
    if conn is None:
        return None

    conn.setdefault("models", {}).pop(model, None)
    connections[connection_id] = conn
    current["connections"] = connections
    _write_settings(nsid, current)
    return masked(current)


def delete_connection(nsid: str, connection_id: str) -> dict | None:
    """Delete a connection. Returns masked full settings, or None if unknown.

    Clears ``active_connection`` if it pointed at the deleted id, rather than
    leaving a dangling reference — resolve_cfg's first-key fallback then
    takes over. Clears ``sync_connection``/``sync_model`` the same way —
    otherwise resolve_sync_cfg would keep resolving a connection id that no
    longer exists (falling back to some other connection while still
    applying the stale sync_model to it).
    """
    current = load_settings(nsid)
    connections = current.get("connections") or {}
    if connection_id not in connections:
        return None
    del connections[connection_id]
    current["connections"] = connections
    if current.get("active_connection") == connection_id:
        current["active_connection"] = ""
    if current.get("sync_connection") == connection_id:
        current["sync_connection"] = ""
        current["sync_model"] = ""
    _write_settings(nsid, current)
    return masked(current)


def _clear_dangling_picks_if_paused(current: dict) -> None:
    """Clear ``active_connection``/``sync_connection`` in place if either now
    points at a paused connection, mirroring what ``delete_connection`` does
    for a removed one — so ``resolve_cfg``'s "first connection" fallback
    picks a live connection instead of silently continuing to target the one
    just paused."""
    connections = current.get("connections") or {}
    active = current.get("active_connection")
    if active and (connections.get(active) or {}).get("paused"):
        current["active_connection"] = ""
    sync_conn = current.get("sync_connection")
    if sync_conn and (connections.get(sync_conn) or {}).get("paused"):
        current["sync_connection"] = ""
        current["sync_model"] = ""


# ── Resolve a flat cfg dict for llm.py ────────────────────────────────────────


def resolve_cfg(
    nsid: str,
    connection_id: str | None = None,
    model: str | None = None,
) -> dict:
    """Build the flat config dict that ``llm.stream_chat``/``stream_responses`` expect.

    *connection_id* and *model* override the user's saved active picks so the
    chat page can request a different connection/model for a single turn or
    conversation.
    """
    s = load_settings(nsid)
    connections = s.get("connections") or {}

    cid = connection_id or s.get("active_connection") or ""
    conn = connections.get(cid) or {}
    if not conn and connections:
        # First non-paused connection in the dict as fallback (a paused one
        # is kept configured but shouldn't be auto-picked); if every
        # connection is paused, fall back to the first one anyway rather
        # than resolving to nothing.
        non_paused = [c for c, v in connections.items() if not v.get("paused")]
        cid = non_paused[0] if non_paused else next(iter(connections))
        conn = connections[cid]

    model_id = model or s.get("active_model") or ""
    model_settings = (conn.get("models") or {}).get(model_id) or {}

    # Resolve the wire format. For a Zen connection the format is a per-model
    # property (Zen serves each model over exactly one endpoint — see
    # ZEN_MODEL_API_MODES), so it overrides whatever the connection carries.
    # For every other connection the connection-wide api_mode applies.
    base_url = conn.get("base_url", "")
    if is_zen_connection(base_url) and model_id:
        api_mode = zen_api_mode_for_model(model_id)
    else:
        api_mode = conn.get("api_mode", "chat_completions")

    return {
        **DEFAULTS,
        **{k: model_settings[k] for k in DEFAULTS if k in model_settings},
        "base_url": base_url,
        "api_key": conn.get("api_key", ""),
        "api_mode": api_mode,
        "timeout_seconds": _coerce_timeout(conn.get("timeout_seconds")),
        "tool_set": _coerce_tool_set(conn.get("tool_set")),
        "model": model_id,
    }


def resolve_sync_cfg(nsid: str) -> dict:
    """Build the flat cfg dict for background sync jobs (e.g. the AI group
    summary phase), using the user's dedicated ``sync_connection``/
    ``sync_model`` pick, or falling back to their active chat connection/
    model if unset.

    Unlike ``resolve_cfg``'s general contract — where an explicit connection
    and model are always supplied together from the same source (the chat
    header selector, or a conversation's stored provider+model pair) —
    ``sync_connection`` and ``sync_model`` can be set independently: a user
    may pick a distinct sync connection while leaving the model on its
    default. If that were passed straight through, ``resolve_cfg`` would
    silently default the model to ``active_model``, which belongs to
    whichever connection is active in *chat* — a different connection that
    may not even serve a model with that id. So when a distinct sync
    connection is chosen with no explicit sync_model, the model is left
    blank here instead (surfacing as "no model configured" rather than
    silently sending the wrong one).

    Also includes ``sync_throttle_seconds`` — not an LLM request param, but
    piggybacked onto this same cfg dict since callers (e.g.
    flickr_sync.sync_group_summaries) need both together.
    """
    s = load_settings(nsid)
    sync_connection = s.get("sync_connection") or ""
    sync_model = s.get("sync_model") or ""
    throttle = s.get("sync_throttle_seconds", DEFAULT_SYNC_THROTTLE_SECONDS)

    if not sync_connection:
        cfg = resolve_cfg(nsid)
    else:
        cfg = resolve_cfg(nsid, sync_connection, sync_model or None)
        if not sync_model and sync_connection != s.get("active_connection"):
            cfg["model"] = ""

    cfg["sync_throttle_seconds"] = throttle
    return cfg


# ── Helpers ──────────────────────────────────────────────────────────────────


def _coerce_tool_set(value) -> str:
    """Fall back to "all" for anything not in TOOL_SETS (missing, unknown,
    or a stale value from a future version)."""
    return value if value in TOOL_SETS else "all"


def _coerce_timeout(value) -> int:
    """Clamp to a sane read-timeout range, falling back to the default for
    anything blank or unparseable."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return min(max(seconds, 5), 3600)


def _mask_key(key: str) -> str:
    if not key:
        return ""
    return f"…{key[-4:]}" if len(key) > 4 else "…"


def masked(cfg: dict) -> dict:
    """Return a copy with every connection's api_key masked."""
    out = dict(cfg)
    connections_raw = cfg.get("connections") or {}
    out["connections"] = {
        cid: {**c, "api_key": _mask_key(c.get("api_key", ""))}
        for cid, c in connections_raw.items()
    }
    return out


# ── Migration helpers ─────────────────────────────────────────────────────────


def _maybe_migrate(raw: dict) -> dict:
    """Migrate an older file shape up to v4 in place."""
    if "providers" not in raw and "connections" not in raw:
        raw = _migrate_v1_to_v2(raw)
    if "connections" not in raw:
        raw = _migrate_v2_to_v3(raw)
    if raw.get("schema_version", 0) < 4:
        raw = _migrate_v3_to_v4(raw)
    raw["schema_version"] = 4
    return raw


def _migrate_v1_to_v2(raw: dict) -> dict:
    """v1 had top-level base_url / api_key / model — migrate into a v2 profile."""
    profile = {
        "label": "Ollama",
        "base_url": raw.pop("base_url", CONNECTION_PRESETS["ollama"]["base_url"]),
        "api_key": raw.pop("api_key", ""),
    }
    raw["providers"] = {"ollama": profile}
    raw["active_provider"] = "ollama"
    if raw.get("model"):
        raw["active_model"] = raw.pop("model")
    return raw


def _migrate_v2_to_v3(raw: dict) -> dict:
    """v2 ``providers`` (fixed ids: ollama/zen) -> v3 ``connections`` (named,
    arbitrary ids). Reuses each old provider id verbatim as the new
    connection id so any ``conversations.provider`` value already stored in
    chat.db keeps resolving without touching store.py at all."""
    connections = {}
    for pid, profile in (raw.get("providers") or {}).items():
        kind = "ollama" if pid == "ollama" else "openai_compatible"
        # No disabled_models seed — every Zen wire format is now supported
        # (ZEN_MODEL_API_MODES), so the old responses-only exclusion is gone.
        connections[pid] = {
            "name": profile.get("label", pid),
            "kind": kind,
            "api_mode": "chat_completions",
            "base_url": profile.get("base_url", ""),
            "api_key": profile.get("api_key", ""),
            "disabled_models": [],
        }
    raw["connections"] = connections
    raw["active_connection"] = raw.pop("active_provider", "")
    raw.pop("providers", None)
    return raw


def _migrate_v3_to_v4(raw: dict) -> dict:
    """v3 flat top-level ``DEFAULTS`` keys (max_tokens/vision/temperature/...)
    -> v4 per-model settings. The old values applied globally regardless of
    connection/model, so they're moved onto whichever connection/model was
    active at upgrade time (if any) — preserving the user's current
    vision/sampling choice instead of silently resetting it. Every
    connection also gets a ``models`` dict (empty if not seeded above)."""
    connections = raw.get("connections") or {}

    old_values = {k: raw.pop(k) for k in DEFAULTS if k in raw}
    active_connection = raw.get("active_connection", "")
    active_model = raw.get("active_model", "")

    for cid, conn in connections.items():
        conn.setdefault("models", {})

    if old_values and active_connection in connections and active_model:
        connections[active_connection]["models"][active_model] = {
            **DEFAULTS, **old_values,
        }

    raw["connections"] = connections
    return raw


def _merge_defaults(raw: dict) -> dict:
    """Ensure every expected key is present after migration.

    Unlike v3, the ``DEFAULTS`` keys are no longer top-level fields — they
    only ever live inside a connection's ``models`` dict, so nothing to
    seed here beyond ``connections``/``active_connection``/``active_model``.
    """
    if "connections" not in raw:
        raw["connections"] = copy.deepcopy(DEFAULT_CONNECTIONS)
    for conn in raw["connections"].values():
        conn.setdefault("models", {})
        conn.setdefault("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        conn.setdefault("paused", False)
        conn["tool_set"] = _coerce_tool_set(conn.get("tool_set"))
    return {
        "connections": raw["connections"],
        "active_connection": raw.get("active_connection", ""),
        "active_model": raw.get("active_model", ""),
        # Off by default — compaction discards raw history irreversibly, and
        # a user should opt into that rather than have it happen silently.
        "auto_compact": bool(raw.get("auto_compact", False)),
        # Connection/model used by background sync jobs (e.g. the AI group
        # summary phase) — deliberately separate from active_connection/
        # active_model so switching chat models doesn't silently change what
        # sync jobs use. Empty means "fall back to the active chat pick" —
        # see resolve_sync_cfg().
        "sync_connection": raw.get("sync_connection", ""),
        "sync_model": raw.get("sync_model", ""),
        # Seconds to wait between successive group-summary LLM calls within a
        # single sync run. Defaults to one request per minute — gentle on a
        # local LLM (the common case for this setting) that would otherwise
        # be hit with one request per flagged group back-to-back.
        "sync_throttle_seconds": int(raw.get("sync_throttle_seconds", DEFAULT_SYNC_THROTTLE_SECONDS)),
        "schema_version": 4,
    }


# Legacy aliases so ``from agent.settings import mask_key`` still works
mask_key = _mask_key
