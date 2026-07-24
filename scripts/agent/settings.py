"""Per-user LLM provider settings, stored next to the OAuth credentials.

``~/.flickr_mcp/{nsid}/llm.json`` — kept out of the resettable data DB and
inside the already-volume-mounted credentials directory.

Storage shape (v2 — provider profiles)::

    {
      "providers": {
        "ollama":    {"label": "Ollama",      "base_url": "http://host.docker.internal:11434/v1", "api_key": ""},
        "zen":       {"label": "OpenCode Zen", "base_url": "https://opencode.ai/zen/v1",          "api_key": "..."}
      },
      "active_provider": "ollama",
      "active_model": "",
      "max_tokens": 1024,
      "vision": false,
      "base_prompt": "",
      "temperature": "", "top_p": "", "frequency_penalty": "", "presence_penalty": "",
      "seed": "", "tool_choice": "auto"
    }

A flat v1 file (no ``providers`` key) is auto-migrated on load into a default
``ollama`` profile so existing installs keep working without re-login.
"""

import copy
import json
import os

from flickr_api import _CREDS_BASE

# ── Provider profile defaults ────────────────────────────────────────────────

DEFAULT_PROVIDERS: dict[str, dict] = {
    "ollama": {
        "label": "Ollama",
        "base_url": "http://host.docker.internal:11434/v1",
        "api_key": "",
    },
    "zen": {
        "label": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "api_key": "",
    },
}

DEFAULTS = {
    "max_tokens": 1024,
    "vision": False,
    "base_prompt": "",
    # Sampling / tool-use controls. Blank ("") means "omit from the request
    # and let the provider use its own default".
    "temperature": "",
    "top_p": "",
    "frequency_penalty": "",
    "presence_penalty": "",
    "seed": "",
    "tool_choice": "auto",  # auto | required | none
}


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
    """Return the full settings dict (v2 shape), migrating v1 on the fly."""
    raw = _raw_load(nsid)
    migrated = _maybe_migrate(raw)
    return _merge_defaults(migrated)


def save_settings(nsid: str, data: dict) -> dict:
    """Merge *data* over the stored settings and persist.  Returns the result.

    Per-provider ``api_key`` values equal to their masked placeholder are
    ignored so the UI can round-trip settings without ever seeing the real key.
    """
    current = load_settings(nsid)

    # ── merge global fields ──────────────────────────────────────────────
    for key in DEFAULTS:
        if key in data:
            current[key] = data[key]
    current["max_tokens"] = int(current["max_tokens"] or DEFAULTS["max_tokens"])
    current["vision"] = bool(current.get("vision", False))

    # ── merge active provider / model ────────────────────────────────────
    if "active_provider" in data:
        current["active_provider"] = data["active_provider"]
    if "active_model" in data:
        current["active_model"] = data["active_model"]

    # ── merge provider profiles ──────────────────────────────────────────
    incoming_providers = data.get("providers") or {}
    stored_providers = current.get("providers") or {}
    for pid, profile in incoming_providers.items():
        base = copy.deepcopy(stored_providers.get(pid, {}))
        for field in ("label", "base_url"):
            if field in profile:
                base[field] = profile[field]
        # mask-guard the api_key
        if "api_key" in profile:
            old_key = stored_providers.get(pid, {}).get("api_key", "")
            if profile["api_key"] != _mask_key(old_key):
                base["api_key"] = profile["api_key"]
        stored_providers[pid] = base
    current["providers"] = stored_providers

    path = settings_file(nsid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(current, f, indent=2)
    return current


# ── Resolve a flat cfg dict for llm.py ────────────────────────────────────────


def resolve_cfg(
    nsid: str,
    provider_id: str | None = None,
    model: str | None = None,
) -> dict:
    """Build the flat config dict that ``llm.stream_chat`` expects.

    *provider_id* and *model* override the user's saved active picks so the
    chat page can request a different provider/model for a single turn or
    conversation.
    """
    s = load_settings(nsid)
    providers = s.get("providers") or {}

    pid = provider_id or s.get("active_provider") or ""
    prof = providers.get(pid) or {}
    if not prof and providers:
        # first provider in the dict as fallback
        pid = next(iter(providers))
        prof = providers[pid]

    return {
        **DEFAULTS,
        **{k: s[k] for k in DEFAULTS if k in s},
        "base_url": prof.get("base_url", ""),
        "api_key": prof.get("api_key", ""),
        "model": model or s.get("active_model") or "",
    }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mask_key(key: str) -> str:
    if not key:
        return ""
    return f"…{key[-4:]}" if len(key) > 4 else "…"


def masked(cfg: dict) -> dict:
    """Return a copy with every provider's api_key masked."""
    out = dict(cfg)
    out.pop("providers", None)  # handled below
    providers_raw = cfg.get("providers") or {}
    out["providers"] = {
        pid: {**p, "api_key": _mask_key(p.get("api_key", ""))}
        for pid, p in providers_raw.items()
    }
    return out


# ── Migration helpers ─────────────────────────────────────────────────────────


def _maybe_migrate(raw: dict) -> dict:
    """If the file is v1 (flat, no ``providers`` key), migrate to v2."""
    if "providers" in raw:
        return raw
    # v1 had top-level base_url / api_key / model — migrate into a profile
    profile = {
        "label": "Ollama",
        "base_url": raw.pop("base_url", DEFAULT_PROVIDERS["ollama"]["base_url"]),
        "api_key": raw.pop("api_key", ""),
    }
    raw["providers"] = {"ollama": profile}
    raw["active_provider"] = "ollama"
    if raw.get("model"):
        raw["active_model"] = raw.pop("model")
    return raw


def _merge_defaults(raw: dict) -> dict:
    """Ensure every expected key is present after migration."""
    merged = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in raw:
            merged[k] = raw[k]
    if "providers" not in raw:
        raw["providers"] = copy.deepcopy(DEFAULT_PROVIDERS)
    merged["providers"] = raw["providers"]
    merged.setdefault("active_provider", "")
    merged.setdefault("active_model", "")
    return merged


# Legacy aliases so ``from agent.settings import mask_key`` still works
mask_key = _mask_key
