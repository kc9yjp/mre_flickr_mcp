"""Per-user LLM provider settings, stored next to the OAuth credentials.

``~/.flickr_mcp/{nsid}/llm.json`` — kept out of the resettable data DB and
inside the already-volume-mounted credentials directory.
"""

import json
import os

from flickr_api import _CREDS_BASE

DEFAULTS = {
    "base_url": "http://host.docker.internal:11434/v1",  # local Ollama from Docker
    "api_key": "",
    "model": "",
    "max_tokens": 1024,
    "vision": False,
    "base_prompt": "",
    # Sampling / tool-use controls. Blank ("") means "omit from the request
    # and let the provider use its own default" — same convention as
    # max_tokens used to have before it got a hard default.
    "temperature": "",
    "top_p": "",
    "frequency_penalty": "",
    "presence_penalty": "",
    "seed": "",
    "tool_choice": "auto",  # auto | required | none
}


def settings_file(nsid: str) -> str:
    return os.path.join(_CREDS_BASE, nsid, "llm.json")


def load_settings(nsid: str) -> dict:
    merged = dict(DEFAULTS)
    try:
        with open(settings_file(nsid)) as f:
            merged.update(json.load(f))
    except (OSError, ValueError):
        pass
    return merged


def save_settings(nsid: str, data: dict) -> dict:
    """Merge *data* over the stored settings and persist. Returns the result.

    An ``api_key`` equal to the masked placeholder is ignored so the UI can
    round-trip settings without ever seeing the real key.
    """
    current = load_settings(nsid)
    for key in DEFAULTS:
        if key not in data:
            continue
        if key == "api_key" and data[key] == mask_key(current["api_key"]):
            continue
        current[key] = data[key]
    current["max_tokens"] = int(current["max_tokens"] or DEFAULTS["max_tokens"])
    current["vision"] = bool(current.get("vision", False))

    path = settings_file(nsid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(current, f, indent=2)
    return current


def mask_key(key: str) -> str:
    if not key:
        return ""
    return f"…{key[-4:]}" if len(key) > 4 else "…"


def masked(cfg: dict) -> dict:
    return {**cfg, "api_key": mask_key(cfg.get("api_key", ""))}
