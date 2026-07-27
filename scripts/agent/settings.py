"""Per-user LLM connection settings, stored next to the OAuth credentials.

``~/.flickr_mcp/{nsid}/llm.json`` — kept out of the resettable data DB and
inside the already-volume-mounted credentials directory.

Storage shape (v3 — named connections)::

    {
      "schema_version": 3,
      "connections": {
        "ollama": {"name": "Ollama", "kind": "ollama", "api_mode": "chat_completions",
                    "base_url": "http://host.docker.internal:11434/v1", "api_key": "",
                    "disabled_models": []},
        "zen":    {"name": "OpenCode Zen", "kind": "openai_compatible", "api_mode": "chat_completions",
                    "base_url": "https://opencode.ai/zen/v1", "api_key": "...",
                    "disabled_models": ["gpt-5", "..."]}
      },
      "active_connection": "ollama",
      "active_model": "",
      "max_tokens": 1024,
      "vision": false,
      "temperature": "", "top_p": "", "frequency_penalty": "", "presence_penalty": "",
      "seed": "", "tool_choice": "auto"
    }

A connection's ``kind`` (``ollama`` | ``openai_compatible``) only picks a
default base_url/preset at creation time — it has no effect on request
behavior. ``api_mode`` (``chat_completions`` | ``responses``) is what
``llm.py``/``loop.py`` actually branch on.

Older files migrate on load: a flat v1 file (no ``providers``/``connections``
key) becomes a default ``ollama`` connection; a v2 file (``providers`` dict
keyed by a fixed provider id) becomes a v3 ``connections`` dict, reusing each
old provider id verbatim as the new connection id so any conversation rows
that already reference it keep resolving.
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
        "disabled_models": [],
    },
}

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
}

# Models known to require the (unsupported-by-default) Responses API instead
# of Chat Completions. Only used to seed a new Zen-preset connection's
# disabled_models so it behaves the same as the pre-v3 hardcoded exclusion —
# users can re-enable any of these once they flip that connection's
# api_mode to "responses".
_ZEN_RESPONSES_ONLY_MODELS = {
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "gpt-5.5", "gpt-5.5-pro",
    "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.3-codex", "gpt-5.3-codex-spark",
    "gpt-5.2", "gpt-5.2-codex",
    "gpt-5.1", "gpt-5.1-codex", "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
    "gpt-5", "gpt-5-codex", "gpt-5-nano",
}


def suggested_disabled_models(kind: str, base_url: str = "") -> set[str]:
    """Default disabled-models seed for a newly created connection.

    Best-effort: only Zen's known /v1/responses-only models are suggested,
    since that's the one case we know breaks chat_completions today.
    """
    if kind == "openai_compatible" and "opencode.ai/zen" in base_url.lower():
        return set(_ZEN_RESPONSES_ONLY_MODELS)
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
    """Return the full settings dict (v3 shape), migrating older files on the fly."""
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

    # ── merge global fields ──────────────────────────────────────────────
    for key in DEFAULTS:
        if key in data:
            current[key] = data[key]
    current["max_tokens"] = int(current["max_tokens"] or DEFAULTS["max_tokens"])
    current["vision"] = bool(current.get("vision", False))

    # ── merge active connection / model ──────────────────────────────────
    if "active_connection" in data:
        current["active_connection"] = data["active_connection"]
    if "active_model" in data:
        current["active_model"] = data["active_model"]

    # ── merge connections ────────────────────────────────────────────────
    incoming = data.get("connections") or {}
    stored = current.get("connections") or {}
    for cid, conn in incoming.items():
        base = copy.deepcopy(stored.get(cid, {}))
        for field in ("name", "base_url", "kind", "api_mode"):
            if field in conn:
                base[field] = conn[field]
        if "disabled_models" in conn:
            base["disabled_models"] = list(conn["disabled_models"])
        # mask-guard the api_key
        if "api_key" in conn:
            old_key = stored.get(cid, {}).get("api_key", "")
            if conn["api_key"] != _mask_key(old_key):
                base["api_key"] = conn["api_key"]
        stored[cid] = base
    current["connections"] = stored

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
        "disabled_models": sorted(suggested_disabled_models(kind, base_url)),
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
    if "disabled_models" in patch:
        conn["disabled_models"] = list(patch["disabled_models"])
    if "api_key" in patch:
        if patch["api_key"] != _mask_key(conn.get("api_key", "")):
            conn["api_key"] = patch["api_key"]

    connections[connection_id] = conn
    current["connections"] = connections
    _write_settings(nsid, current)
    return masked(current)


def delete_connection(nsid: str, connection_id: str) -> dict | None:
    """Delete a connection. Returns masked full settings, or None if unknown.

    Clears ``active_connection`` if it pointed at the deleted id, rather than
    leaving a dangling reference — resolve_cfg's first-key fallback then
    takes over.
    """
    current = load_settings(nsid)
    connections = current.get("connections") or {}
    if connection_id not in connections:
        return None
    del connections[connection_id]
    current["connections"] = connections
    if current.get("active_connection") == connection_id:
        current["active_connection"] = ""
    _write_settings(nsid, current)
    return masked(current)


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
        # first connection in the dict as fallback
        cid = next(iter(connections))
        conn = connections[cid]

    return {
        **DEFAULTS,
        **{k: s[k] for k in DEFAULTS if k in s},
        "base_url": conn.get("base_url", ""),
        "api_key": conn.get("api_key", ""),
        "api_mode": conn.get("api_mode", "chat_completions"),
        "model": model or s.get("active_model") or "",
    }


# ── Helpers ──────────────────────────────────────────────────────────────────


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
    """Migrate an older file shape up to v3 in place."""
    if "providers" not in raw and "connections" not in raw:
        raw = _migrate_v1_to_v2(raw)
    if "connections" not in raw:
        raw = _migrate_v2_to_v3(raw)
    raw["schema_version"] = 3
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
        disabled = sorted(_ZEN_RESPONSES_ONLY_MODELS) if pid == "zen" else []
        connections[pid] = {
            "name": profile.get("label", pid),
            "kind": kind,
            "api_mode": "chat_completions",
            "base_url": profile.get("base_url", ""),
            "api_key": profile.get("api_key", ""),
            "disabled_models": disabled,
        }
    raw["connections"] = connections
    raw["active_connection"] = raw.pop("active_provider", "")
    raw.pop("providers", None)
    return raw


def _merge_defaults(raw: dict) -> dict:
    """Ensure every expected key is present after migration."""
    merged = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in raw:
            merged[k] = raw[k]
    if "connections" not in raw:
        raw["connections"] = copy.deepcopy(DEFAULT_CONNECTIONS)
    merged["connections"] = raw["connections"]
    merged["active_connection"] = raw.get("active_connection", "")
    merged["active_model"] = raw.get("active_model", "")
    merged["schema_version"] = 3
    return merged


# Legacy aliases so ``from agent.settings import mask_key`` still works
mask_key = _mask_key
