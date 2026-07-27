"""Tests for agent.settings: schema v3 shape, migrations, and CRUD helpers."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

NSID = "12345@N00"


@pytest.fixture(autouse=True)
def _creds_dir(tmp_path):
    creds_dir = str(tmp_path / "flickr_mcp_creds")
    with patch("agent.settings._CREDS_BASE", creds_dir):
        yield creds_dir


def _write_raw(creds_dir, nsid, data):
    from agent import settings

    path = Path(settings.settings_file(nsid))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# --- fresh install ---

def test_fresh_install_seeds_default_connections():
    """A brand-new user with no llm.json gets only an Ollama connection —
    Zen/LM Studio/Custom are quick-add presets, not auto-created."""
    from agent import settings

    s = settings.load_settings(NSID)
    assert s["schema_version"] == 3
    assert set(s["connections"]) == {"ollama"}
    assert s["connections"]["ollama"]["kind"] == "ollama"
    assert s["active_connection"] == "ollama"


# --- v1 -> v3 migration ---

def test_v1_flat_file_migrates_to_v3(_creds_dir):
    _write_raw(_creds_dir, NSID, {
        "base_url": "http://custom:11434/v1", "api_key": "abc", "model": "llama3.1",
    })
    from agent import settings

    s = settings.load_settings(NSID)
    assert s["schema_version"] == 3
    assert set(s["connections"]) == {"ollama"}
    conn = s["connections"]["ollama"]
    assert conn["base_url"] == "http://custom:11434/v1"
    assert conn["api_key"] == "abc"
    assert conn["kind"] == "ollama"
    assert conn["disabled_models"] == []
    assert s["active_connection"] == "ollama"
    assert s["active_model"] == "llama3.1"


# --- v2 -> v3 migration ---

def test_v2_providers_migrate_to_v3_connections_preserving_ids(_creds_dir):
    from agent import settings

    _write_raw(_creds_dir, NSID, {
        "providers": {
            "ollama": {"label": "Ollama", "base_url": "http://host.docker.internal:11434/v1", "api_key": ""},
            "zen": {"label": "OpenCode Zen", "base_url": "https://opencode.ai/zen/v1", "api_key": "secretkey"},
        },
        "active_provider": "zen",
        "active_model": "big-pickle",
        "max_tokens": 2048,
    })

    s = settings.load_settings(NSID)
    assert s["schema_version"] == 3
    assert set(s["connections"]) == {"ollama", "zen"}

    # Old provider ids must be reused verbatim as connection ids so any
    # conversations.provider value already stored in chat.db keeps resolving.
    ollama = s["connections"]["ollama"]
    assert ollama["name"] == "Ollama"
    assert ollama["kind"] == "ollama"
    assert ollama["api_mode"] == "chat_completions"
    assert ollama["disabled_models"] == []

    zen = s["connections"]["zen"]
    assert zen["name"] == "OpenCode Zen"
    assert zen["kind"] == "openai_compatible"
    assert zen["api_key"] == "secretkey"
    # Migrated Zen connection must see the exact same model list as before
    # the upgrade: the old hardcoded exclusion becomes an explicit seed.
    assert zen["disabled_models"] == sorted(settings._ZEN_RESPONSES_ONLY_MODELS)

    assert s["active_connection"] == "zen"
    assert s["active_model"] == "big-pickle"
    assert s["max_tokens"] == 2048
    assert "providers" not in s
    assert "active_provider" not in s


def test_v2_migration_is_idempotent_on_reload(_creds_dir):
    from agent import settings

    _write_raw(_creds_dir, NSID, {
        "providers": {"ollama": {"label": "Ollama", "base_url": "http://x/v1", "api_key": ""}},
        "active_provider": "ollama",
    })
    first = settings.load_settings(NSID)
    second = settings.load_settings(NSID)
    assert first == second


# --- resolve_cfg ---

def test_resolve_cfg_explicit_connection_and_model(_creds_dir):
    from agent import settings

    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")

    cfg = settings.resolve_cfg(NSID, "opencode-zen", "grok-4.5")
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"
    assert cfg["model"] == "grok-4.5"
    assert cfg["api_mode"] == "chat_completions"


def test_resolve_cfg_falls_back_to_active_then_first_key(_creds_dir):
    from agent import settings

    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["active_connection"] = "opencode-zen"
    settings.save_settings(NSID, s)

    cfg = settings.resolve_cfg(NSID)
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"

    # Unknown explicit id with connections present -> falls back to first key.
    cfg2 = settings.resolve_cfg(NSID, "does-not-exist")
    assert cfg2["base_url"] in {
        s["connections"]["ollama"]["base_url"],
        s["connections"]["opencode-zen"]["base_url"],
    }


# --- mask-guard round trip ---

def test_save_settings_api_key_mask_guard_round_trip(_creds_dir):
    from agent import settings

    _, out = settings.create_connection(
        NSID, "My Zen", "openai_compatible", "https://opencode.ai/zen/v1", api_key="realsecret",
    )
    cid = next(k for k, v in out["connections"].items() if v["name"] == "My Zen")
    masked_key = out["connections"][cid]["api_key"]
    assert masked_key == "…cret"

    # Round-tripping the masked placeholder back through save_settings must
    # NOT overwrite the real stored key.
    settings.save_settings(NSID, {
        "connections": {cid: {"name": "My Zen", "base_url": "https://opencode.ai/zen/v1", "api_key": masked_key}},
    })
    raw = settings._raw_load(NSID)
    assert raw["connections"][cid]["api_key"] == "realsecret"


# --- connection CRUD ---

def test_create_update_delete_connection(_creds_dir):
    from agent import settings

    cid, out = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")
    assert cid == "lm-studio"
    assert out["connections"]["lm-studio"]["base_url"] == "http://host.docker.internal:1234/v1"

    updated = settings.update_connection(NSID, cid, {"name": "LM Studio (renamed)", "disabled_models": ["foo"]})
    assert updated["connections"][cid]["name"] == "LM Studio (renamed)"
    assert updated["connections"][cid]["disabled_models"] == ["foo"]

    assert settings.update_connection(NSID, "no-such-id", {"name": "x"}) is None

    s = settings.load_settings(NSID)
    s["active_connection"] = cid
    settings.save_settings(NSID, s)

    deleted = settings.delete_connection(NSID, cid)
    assert cid not in deleted["connections"]
    assert deleted["active_connection"] == ""

    assert settings.delete_connection(NSID, "no-such-id") is None


def test_create_connection_slug_collision_gets_suffix(_creds_dir):
    from agent import settings

    id1, _ = settings.create_connection(NSID, "Custom", "openai_compatible", "http://a/v1")
    id2, _ = settings.create_connection(NSID, "Custom", "openai_compatible", "http://b/v1")
    assert id1 == "custom"
    assert id2 == "custom-2"


def test_create_connection_seeds_zen_responses_only_models(_creds_dir):
    from agent import settings

    cid, out = settings.create_connection(NSID, "My Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    assert out["connections"][cid]["disabled_models"] == sorted(settings._ZEN_RESPONSES_ONLY_MODELS)


# --- masked() ---

def test_masked_strips_all_connection_api_keys(_creds_dir):
    from agent import settings

    settings.create_connection(NSID, "A", "ollama", "http://a/v1", api_key="topsecret1")
    s = settings.load_settings(NSID)
    m = settings.masked(s)
    for conn in m["connections"].values():
        assert "topsecret1" not in conn["api_key"]
