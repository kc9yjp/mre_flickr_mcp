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
    assert s["schema_version"] == 4
    assert set(s["connections"]) == {"ollama"}
    assert s["connections"]["ollama"]["kind"] == "ollama"
    assert s["connections"]["ollama"]["models"] == {}
    assert s["active_connection"] == "ollama"


# --- v1 -> v3 migration ---

def test_v1_flat_file_migrates_to_v3(_creds_dir):
    _write_raw(_creds_dir, NSID, {
        "base_url": "http://custom:11434/v1", "api_key": "abc", "model": "llama3.1",
    })
    from agent import settings

    s = settings.load_settings(NSID)
    assert s["schema_version"] == 4
    assert set(s["connections"]) == {"ollama"}
    conn = s["connections"]["ollama"]
    assert conn["base_url"] == "http://custom:11434/v1"
    assert conn["api_key"] == "abc"
    assert conn["kind"] == "ollama"
    assert conn["disabled_models"] == []
    assert conn["models"] == {}
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
    assert s["schema_version"] == 4
    assert set(s["connections"]) == {"ollama", "zen"}

    # Old provider ids must be reused verbatim as connection ids so any
    # conversations.provider value already stored in chat.db keeps resolving.
    ollama = s["connections"]["ollama"]
    assert ollama["name"] == "Ollama"
    assert ollama["kind"] == "ollama"
    assert ollama["api_mode"] == "chat_completions"
    assert ollama["disabled_models"] == []
    assert ollama["models"] == {}

    zen = s["connections"]["zen"]
    assert zen["name"] == "OpenCode Zen"
    assert zen["kind"] == "openai_compatible"
    assert zen["api_key"] == "secretkey"
    # Zen resolves the wire format per model, so there is no longer a
    # responses-only exclusion to seed — every model stays available.
    assert zen["disabled_models"] == []

    assert s["active_connection"] == "zen"
    assert s["active_model"] == "big-pickle"
    # The old flat max_tokens applied globally at v3 — the v3->v4 migration
    # preserves it by landing it on whatever connection/model was active,
    # rather than silently resetting it, and it's gone from the top level.
    assert zen["models"]["big-pickle"]["max_tokens"] == 2048
    assert "max_tokens" not in s
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


# --- v3 -> v4 migration ---

def test_v3_flat_defaults_migrate_onto_active_connection_and_model(_creds_dir):
    from agent import settings

    _write_raw(_creds_dir, NSID, {
        "schema_version": 3,
        "connections": {
            "ollama": {"name": "Ollama", "kind": "ollama", "api_mode": "chat_completions",
                       "base_url": "http://x/v1", "api_key": "", "disabled_models": []},
        },
        "active_connection": "ollama",
        "active_model": "llama3.2",
        "max_tokens": 2048,
        "vision": True,
        "temperature": "0.7",
        "top_p": "", "frequency_penalty": "", "presence_penalty": "", "seed": "", "tool_choice": "auto",
    })

    s = settings.load_settings(NSID)
    assert s["schema_version"] == 4
    assert "max_tokens" not in s
    assert "vision" not in s
    entry = s["connections"]["ollama"]["models"]["llama3.2"]
    assert entry["max_tokens"] == 2048
    assert entry["vision"] is True
    assert entry["temperature"] == "0.7"


def test_v3_migration_with_no_active_model_just_adds_empty_models_dict(_creds_dir):
    from agent import settings

    _write_raw(_creds_dir, NSID, {
        "schema_version": 3,
        "connections": {
            "ollama": {"name": "Ollama", "kind": "ollama", "api_mode": "chat_completions",
                       "base_url": "http://x/v1", "api_key": "", "disabled_models": []},
        },
        "active_connection": "ollama",
        "active_model": "",
        "max_tokens": 2048,
    })

    s = settings.load_settings(NSID)
    assert s["schema_version"] == 4
    assert s["connections"]["ollama"]["models"] == {}
    assert "max_tokens" not in s


# --- resolve_cfg ---

def test_resolve_cfg_explicit_connection_and_model(_creds_dir):
    from agent import settings

    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")

    cfg = settings.resolve_cfg(NSID, "opencode-zen", "grok-4.5")
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"
    assert cfg["model"] == "grok-4.5"
    # grok-4.5 is served over the Responses API on Zen — the per-model wire
    # format overrides the connection's chat_completions default.
    assert cfg["api_mode"] == "responses"
    # No per-model override saved yet -> falls back to DEFAULTS.
    assert cfg["vision"] is False
    assert cfg["max_tokens"] == settings.DEFAULTS["max_tokens"]


def test_resolve_cfg_zen_per_model_api_modes(_creds_dir):
    """Each Zen model resolves to the wire format its endpoint serves."""
    from agent import settings

    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")

    assert settings.resolve_cfg(NSID, "opencode-zen", "gpt-5.5")["api_mode"] == "responses"
    assert settings.resolve_cfg(NSID, "opencode-zen", "claude-sonnet-5")["api_mode"] == "messages"
    assert settings.resolve_cfg(NSID, "opencode-zen", "qwen3.6-plus")["api_mode"] == "messages"
    assert settings.resolve_cfg(NSID, "opencode-zen", "gemini-3-flash")["api_mode"] == "gemini"
    assert settings.resolve_cfg(NSID, "opencode-zen", "kimi-k3")["api_mode"] == "chat_completions"
    # An unmapped Zen model degrades to chat_completions rather than breaking.
    assert settings.resolve_cfg(NSID, "opencode-zen", "some-brand-new-model")["api_mode"] == "chat_completions"


def test_resolve_cfg_non_zen_connection_keeps_connection_api_mode(_creds_dir):
    """A non-Zen connection ignores the per-model map — its connection-wide
    api_mode applies even to a model id that appears in ZEN_MODEL_API_MODES."""
    from agent import settings

    cid, _ = settings.create_connection(
        NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1"
    )

    cfg = settings.resolve_cfg(NSID, cid, "gpt-5.5")
    assert cfg["api_mode"] == "chat_completions"


def test_resolve_cfg_uses_per_model_settings(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")
    settings.update_model_settings(NSID, cid, "qwen/qwen3.5-9b", {"vision": True, "max_tokens": 2048})

    cfg = settings.resolve_cfg(NSID, cid, "qwen/qwen3.5-9b")
    assert cfg["vision"] is True
    assert cfg["max_tokens"] == 2048

    # A different, never-customized model on the same connection still gets
    # plain DEFAULTS — settings are per-model, not per-connection.
    other_cfg = settings.resolve_cfg(NSID, cid, "other-model")
    assert other_cfg["vision"] is False


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


# --- resolve_sync_cfg ---

def test_resolve_sync_cfg_defaults_to_active_chat_pick(_creds_dir):
    from agent import settings

    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["active_connection"] = "opencode-zen"
    s["active_model"] = "grok-4.5"
    settings.save_settings(NSID, s)

    cfg = settings.resolve_sync_cfg(NSID)
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"
    assert cfg["model"] == "grok-4.5"


def test_resolve_sync_cfg_same_connection_as_active_uses_active_model(_creds_dir):
    """sync_connection explicitly set to the same connection as active_connection:
    active_model IS valid for it, so the fallback is safe."""
    from agent import settings

    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["active_connection"] = "opencode-zen"
    s["active_model"] = "grok-4.5"
    s["sync_connection"] = "opencode-zen"
    s["sync_model"] = ""
    settings.save_settings(NSID, s)

    cfg = settings.resolve_sync_cfg(NSID)
    assert cfg["model"] == "grok-4.5"


def test_resolve_sync_cfg_distinct_connection_without_model_does_not_borrow_active_model(_creds_dir):
    """Regression: picking a distinct sync connection with no explicit sync_model
    must never fall back to active_model, which belongs to a different connection."""
    from agent import settings

    settings.create_connection(NSID, "Ollama chat", "ollama", "http://host.docker.internal:11434/v1")
    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["active_connection"] = "ollama-chat"
    s["active_model"] = "llama3.1"  # a model id that only exists on the Ollama connection
    s["sync_connection"] = "opencode-zen"
    s["sync_model"] = ""
    settings.save_settings(NSID, s)

    cfg = settings.resolve_sync_cfg(NSID)
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"
    assert cfg["model"] == ""  # never "llama3.1" — that belongs to Ollama, not Zen


def test_resolve_sync_cfg_distinct_connection_with_explicit_model(_creds_dir):
    from agent import settings

    settings.create_connection(NSID, "Ollama chat", "ollama", "http://host.docker.internal:11434/v1")
    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["active_connection"] = "ollama-chat"
    s["active_model"] = "llama3.1"
    s["sync_connection"] = "opencode-zen"
    s["sync_model"] = "grok-4.5"
    settings.save_settings(NSID, s)

    cfg = settings.resolve_sync_cfg(NSID)
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"
    assert cfg["model"] == "grok-4.5"


def test_resolve_sync_cfg_default_throttle_is_one_per_minute(_creds_dir):
    from agent import settings

    cfg = settings.resolve_sync_cfg(NSID)
    assert cfg["sync_throttle_seconds"] == 60
    assert settings.DEFAULT_SYNC_THROTTLE_SECONDS == 60


def test_sync_throttle_seconds_persists(_creds_dir):
    from agent import settings

    saved = settings.save_settings(NSID, {"sync_throttle_seconds": 5})
    assert saved["sync_throttle_seconds"] == 5
    assert settings.load_settings(NSID)["sync_throttle_seconds"] == 5
    assert settings.resolve_sync_cfg(NSID)["sync_throttle_seconds"] == 5


def test_sync_throttle_seconds_rejects_negative_and_invalid(_creds_dir):
    from agent import settings

    settings.save_settings(NSID, {"sync_throttle_seconds": -5})
    assert settings.load_settings(NSID)["sync_throttle_seconds"] == 0

    # Non-numeric value is ignored rather than raising or corrupting storage.
    before = settings.load_settings(NSID)["sync_throttle_seconds"]
    settings.save_settings(NSID, {"sync_throttle_seconds": "not-a-number"})
    assert settings.load_settings(NSID)["sync_throttle_seconds"] == before


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


def test_delete_connection_clears_dangling_sync_connection(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["sync_connection"] = cid
    s["sync_model"] = "grok-4.5"
    settings.save_settings(NSID, s)

    deleted = settings.delete_connection(NSID, cid)
    assert deleted["sync_connection"] == ""
    assert deleted["sync_model"] == ""


def test_create_connection_slug_collision_gets_suffix(_creds_dir):
    from agent import settings

    id1, _ = settings.create_connection(NSID, "Custom", "openai_compatible", "http://a/v1")
    id2, _ = settings.create_connection(NSID, "Custom", "openai_compatible", "http://b/v1")
    assert id1 == "custom"
    assert id2 == "custom-2"


def test_create_connection_seeds_zen_responses_only_models(_creds_dir):
    from agent import settings

    cid, out = settings.create_connection(NSID, "My Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    # Per-model api_mode resolution replaced the old responses-only seeding.
    assert out["connections"][cid]["disabled_models"] == []


# --- timeout_seconds ---

def test_create_connection_defaults_timeout(_creds_dir):
    from agent import settings

    cid, out = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")
    assert out["connections"][cid]["timeout_seconds"] == settings.DEFAULT_TIMEOUT_SECONDS


def test_create_connection_accepts_custom_timeout(_creds_dir):
    from agent import settings

    cid, out = settings.create_connection(
        NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1",
        timeout_seconds=900,
    )
    assert out["connections"][cid]["timeout_seconds"] == 900


def test_update_connection_timeout_is_clamped(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")

    updated = settings.update_connection(NSID, cid, {"timeout_seconds": 1})
    assert updated["connections"][cid]["timeout_seconds"] == 5  # floor

    updated = settings.update_connection(NSID, cid, {"timeout_seconds": 999999})
    assert updated["connections"][cid]["timeout_seconds"] == 3600  # ceiling

    # Non-numeric value falls back to the default rather than raising.
    updated = settings.update_connection(NSID, cid, {"timeout_seconds": "not-a-number"})
    assert updated["connections"][cid]["timeout_seconds"] == settings.DEFAULT_TIMEOUT_SECONDS


def test_resolve_cfg_includes_timeout_seconds(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(
        NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1",
        timeout_seconds=900,
    )
    cfg = settings.resolve_cfg(NSID, cid, "qwen/qwen3.5-9b")
    assert cfg["timeout_seconds"] == 900


def test_pre_existing_connection_without_timeout_field_gets_default(_creds_dir):
    """Older llm.json files predate timeout_seconds — load_settings must
    backfill it rather than leave it missing."""
    from agent import settings

    settings.create_connection(NSID, "Ollama chat", "ollama", "http://host.docker.internal:11434/v1")
    raw = settings._raw_load(NSID)
    cid = next(iter(raw["connections"]))
    del raw["connections"][cid]["timeout_seconds"]
    settings._write_settings(NSID, raw)

    loaded = settings.load_settings(NSID)
    assert loaded["connections"][cid]["timeout_seconds"] == settings.DEFAULT_TIMEOUT_SECONDS


# --- per-model settings CRUD ---

def test_update_model_settings_creates_and_patches_entry(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")

    out = settings.update_model_settings(NSID, cid, "qwen/qwen3.5-9b", {"vision": True, "max_tokens": 2048})
    entry = out["connections"][cid]["models"]["qwen/qwen3.5-9b"]
    assert entry["vision"] is True
    assert entry["max_tokens"] == 2048
    # Untouched DEFAULTS keys are still present.
    assert entry["tool_choice"] == "auto"

    # A second patch only touches the given keys, keeping the rest.
    out2 = settings.update_model_settings(NSID, cid, "qwen/qwen3.5-9b", {"temperature": "0.5"})
    entry2 = out2["connections"][cid]["models"]["qwen/qwen3.5-9b"]
    assert entry2["vision"] is True
    assert entry2["temperature"] == "0.5"

    assert settings.update_model_settings(NSID, "no-such-id", "m", {"vision": True}) is None


def test_reset_model_settings_removes_override(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")
    settings.update_model_settings(NSID, cid, "qwen/qwen3.5-9b", {"vision": True})

    out = settings.reset_model_settings(NSID, cid, "qwen/qwen3.5-9b")
    assert "qwen/qwen3.5-9b" not in out["connections"][cid]["models"]

    # Resolving now falls back to plain DEFAULTS.
    cfg = settings.resolve_cfg(NSID, cid, "qwen/qwen3.5-9b")
    assert cfg["vision"] is False

    # Resetting a model with no override, or an unknown connection, is safe.
    settings.reset_model_settings(NSID, cid, "never-customized")
    assert settings.reset_model_settings(NSID, "no-such-id", "m") is None


# --- paused connections ---

def test_new_connection_defaults_to_not_paused(_creds_dir):
    from agent import settings

    cid, out = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")
    assert out["connections"][cid]["paused"] is False


def test_pre_existing_connection_without_paused_field_gets_default(_creds_dir):
    """Older llm.json files predate `paused` — load_settings must backfill
    it (as False) rather than leave it missing."""
    from agent import settings

    settings.create_connection(NSID, "Ollama chat", "ollama", "http://host.docker.internal:11434/v1")
    raw = settings._raw_load(NSID)
    cid = next(iter(raw["connections"]))
    del raw["connections"][cid]["paused"]
    settings._write_settings(NSID, raw)

    loaded = settings.load_settings(NSID)
    assert loaded["connections"][cid]["paused"] is False


def test_update_connection_toggles_paused(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "LM Studio", "openai_compatible", "http://host.docker.internal:1234/v1")

    updated = settings.update_connection(NSID, cid, {"paused": True})
    assert updated["connections"][cid]["paused"] is True

    updated = settings.update_connection(NSID, cid, {"paused": False})
    assert updated["connections"][cid]["paused"] is False


def test_pausing_active_connection_clears_active_connection(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["active_connection"] = cid
    settings.save_settings(NSID, s)

    updated = settings.update_connection(NSID, cid, {"paused": True})
    assert updated["active_connection"] == ""
    # The connection itself is kept, just paused — not deleted.
    assert cid in updated["connections"]
    assert updated["connections"][cid]["paused"] is True


def test_pausing_sync_connection_clears_sync_pick(_creds_dir):
    from agent import settings

    cid, _ = settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["sync_connection"] = cid
    s["sync_model"] = "grok-4.5"
    settings.save_settings(NSID, s)

    updated = settings.update_connection(NSID, cid, {"paused": True})
    assert updated["sync_connection"] == ""
    assert updated["sync_model"] == ""


def test_pausing_via_save_settings_also_clears_active_connection(_creds_dir):
    """The bulk /api/llm-settings save path (save_settings) must clear a
    now-dangling active_connection the same way update_connection does."""
    from agent import settings

    cid, _ = settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    s = settings.load_settings(NSID)
    s["active_connection"] = cid
    settings.save_settings(NSID, s)

    s2 = settings.load_settings(NSID)
    saved = settings.save_settings(NSID, {
        "connections": {cid: {"name": "OpenCode Zen", "base_url": s2["connections"][cid]["base_url"], "paused": True}},
    })
    assert saved["active_connection"] == ""
    assert saved["connections"][cid]["paused"] is True


def test_resolve_cfg_fallback_skips_paused_connections(_creds_dir):
    from agent import settings

    # A fresh install already seeds a default "ollama" connection — pause it
    # too, so Zen is the only non-paused one and the fallback is unambiguous.
    settings.load_settings(NSID)
    settings.update_connection(NSID, "ollama", {"paused": True})
    settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")

    # No active_connection set -> fallback must pick the non-paused one.
    cfg = settings.resolve_cfg(NSID)
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"

    # An unknown explicit id falls back the same way.
    cfg2 = settings.resolve_cfg(NSID, "does-not-exist")
    assert cfg2["base_url"] == "https://opencode.ai/zen/v1"


def test_resolve_cfg_fallback_uses_paused_connection_if_all_paused(_creds_dir):
    """Every connection paused is a degenerate case — still resolve to
    something rather than an empty cfg."""
    from agent import settings

    settings.load_settings(NSID)
    settings.update_connection(NSID, "ollama", {"paused": True})
    cid, _ = settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    settings.update_connection(NSID, cid, {"paused": True})

    cfg = settings.resolve_cfg(NSID, "does-not-exist")
    assert cfg["base_url"] in {
        "http://host.docker.internal:11434/v1",
        "https://opencode.ai/zen/v1",
    }


def test_resolve_cfg_explicit_id_ignores_paused(_creds_dir):
    """A conversation already pinned to a specific (now-paused) connection
    must still resolve to it — pausing only affects the fallback pick."""
    from agent import settings

    cid, _ = settings.create_connection(NSID, "OpenCode Zen", "openai_compatible", "https://opencode.ai/zen/v1")
    settings.update_connection(NSID, cid, {"paused": True})

    cfg = settings.resolve_cfg(NSID, cid, "grok-4.5")
    assert cfg["base_url"] == "https://opencode.ai/zen/v1"
    assert cfg["model"] == "grok-4.5"


# --- masked() ---

def test_masked_strips_all_connection_api_keys(_creds_dir):
    from agent import settings

    settings.create_connection(NSID, "A", "ollama", "http://a/v1", api_key="topsecret1")
    s = settings.load_settings(NSID)
    m = settings.masked(s)
    for conn in m["connections"].values():
        assert "topsecret1" not in conn["api_key"]
