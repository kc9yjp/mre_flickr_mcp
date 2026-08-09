// Models panel: a list of named LLM connections (add / update / delete),
// shown as cards. Each card expands to edit its base URL/API key/api
// mode/timeout, refresh its model list, and enable/disable individual
// models via a toggle. Each model also has its own "Details" disclosure —
// vision/max_tokens/context window are edited inline, and the less-common
// sampling knobs (temperature/top-p/penalties/seed/tool choice) sit behind
// an "Advanced settings" disclosure, since a model's capabilities and ideal
// settings vary even within one connection.

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  getJSON,
  postJSON,
  listModels,
  getConnectionPresets,
  createConnection,
  updateConnection,
  deleteConnection,
  updateModelSettings,
  resetModelSettings,
  modelSupportsVision,
  ApiMode,
  Connection,
  ConnectionKind,
  ConnectionPreset,
  DEFAULT_TIMEOUT_SECONDS,
  LLMSettings,
  ModelList,
  ModelSettings,
  MODEL_SETTINGS_DEFAULTS,
} from "../api";
import * as bus from "../bus";

interface NewConnectionDraft {
  name: string;
  kind: ConnectionKind;
  base_url: string;
  api_mode: ApiMode;
  timeout_seconds: number;
}

function draftKey(connectionId: string, model: string): string {
  return `${connectionId}::${model}`;
}

function kindLabel(kind: ConnectionKind): string {
  return kind === "ollama" ? "Ollama" : "OpenAI-compatible";
}

// A Zen connection's wire format is resolved per model on the backend (each
// Zen model is served over exactly one endpoint), so the manual API-mode
// picker is hidden for it — there's nothing to choose.
function isZenConnection(baseUrl: string): boolean {
  return baseUrl.toLowerCase().includes("opencode.ai/zen");
}

function contextLabel(contextWindow: number): string {
  return `${Math.round(contextWindow / 1000)}k ctx`;
}

// Checkbox styled as an on/off track+knob switch, used for "model enabled"
// and "auto-compact". `large` matches the bigger size used for the one
// page-level toggle (auto-compact) vs. the compact per-model ones.
function Switch({
  checked,
  onChange,
  large,
}: {
  checked: boolean;
  onChange: () => void;
  large?: boolean;
}) {
  return (
    <label className={`llm-switch${large ? " llm-switch--lg" : ""}`}>
      <input type="checkbox" checked={checked} onChange={onChange} />
      <span className="llm-switch-track" />
      <span className="llm-switch-knob" />
    </label>
  );
}

export function ModelsPage() {
  const [cfg, setCfg] = useState<LLMSettings | null>(null);
  const [presets, setPresets] = useState<Record<string, ConnectionPreset> | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [modelLists, setModelLists] = useState<Record<string, ModelList>>({});
  const [loadingModels, setLoadingModels] = useState<string | null>(null);
  const [disabledDrafts, setDisabledDrafts] = useState<Record<string, Set<string>>>({});
  const [newConn, setNewConn] = useState<NewConnectionDraft | null>(null);
  const [expandedConnectionId, setExpandedConnectionId] = useState<string | null>(null);
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  const [advancedModel, setAdvancedModel] = useState<string | null>(null);
  const [modelDrafts, setModelDrafts] = useState<Record<string, ModelSettings>>({});

  useEffect(() => {
    getJSON<LLMSettings>("/api/llm-settings")
      .then((s) => {
        if (!s.connections) {
          setError("Error: connections not in response. Backend may be misconfigured.");
        }
        setCfg(s);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    getConnectionPresets()
      .then(setPresets)
      .catch(() => {
        /* quick-add presets are a convenience, not required */
      });
  }, []);

  const flash = (msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(""), 3000);
  };

  const fetchModels = useCallback(
    async (connectionId: string, disabledNow: string[]) => {
      setLoadingModels(connectionId);
      setError("");
      try {
        const list = await listModels(connectionId);
        setModelLists((prev) => ({ ...prev, [connectionId]: list }));
        setDisabledDrafts((prev) => ({ ...prev, [connectionId]: new Set(disabledNow) }));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoadingModels(null);
      }
    },
    [],
  );

  const toggleExpandedConnection = (cid: string, conn: Connection) => {
    if (expandedConnectionId === cid) {
      setExpandedConnectionId(null);
      setExpandedModel(null);
      return;
    }
    setExpandedConnectionId(cid);
    setExpandedModel(null);
    if (!modelLists[cid]) fetchModels(cid, conn.disabled_models);
  };

  const setConnectionField = (cid: string, patch: Partial<Connection>) => {
    setCfg((c) =>
      c
        ? { ...c, connections: { ...c.connections, [cid]: { ...c.connections[cid], ...patch } } }
        : c,
    );
  };

  const togglePaused = async (cid: string) => {
    const conn = cfg?.connections[cid];
    if (!conn) return;
    setError("");
    try {
      const updated = await updateConnection(cid, { paused: !conn.paused });
      setCfg(updated);
      flash(updated.connections[cid]?.paused ? "Connection paused." : "Connection resumed.");
      bus.emit("llmConnectionsChanged", undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const saveConnection = async (cid: string) => {
    const conn = cfg?.connections[cid];
    if (!conn) return;
    setError("");
    try {
      const updated = await updateConnection(cid, {
        name: conn.name,
        base_url: conn.base_url,
        api_key: conn.api_key,
        api_mode: conn.api_mode,
        timeout_seconds: conn.timeout_seconds,
      });
      setCfg(updated);
      flash("Connection saved.");
      bus.emit("llmConnectionsChanged", undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const toggleModel = (connectionId: string, model: string) => {
    setDisabledDrafts((prev) => {
      const next = new Set(prev[connectionId] ?? []);
      if (next.has(model)) next.delete(model);
      else next.add(model);
      return { ...prev, [connectionId]: next };
    });
  };

  const saveModels = async (connectionId: string) => {
    const disabled = Array.from(disabledDrafts[connectionId] ?? []);
    setError("");
    try {
      const updated = await updateConnection(connectionId, { disabled_models: disabled });
      setCfg(updated);
      setModelLists((prev) => {
        const list = prev[connectionId];
        if (!list) return prev;
        const disabledSet = new Set(disabled);
        return { ...prev, [connectionId]: { ...list, models: list.all_models.filter((m) => !disabledSet.has(m)) } };
      });
      flash("Model list saved.");
      bus.emit("llmConnectionsChanged", undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const toggleModelDetails = (cid: string, model: string) => {
    if (expandedModel === model) {
      setExpandedModel(null);
      return;
    }
    setExpandedModel(model);
    setModelDrafts((prev) => {
      const key = draftKey(cid, model);
      if (prev[key]) return prev;
      const existing = cfg?.connections[cid]?.models?.[model];
      return { ...prev, [key]: { ...MODEL_SETTINGS_DEFAULTS, ...existing } };
    });
  };

  const setModelDraft = (cid: string, model: string, patch: Partial<ModelSettings>) => {
    const key = draftKey(cid, model);
    setModelDrafts((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  };

  const saveModelSettings = async (cid: string, model: string) => {
    const draft = modelDrafts[draftKey(cid, model)];
    if (!draft) return;
    setError("");
    try {
      const updated = await updateModelSettings(cid, model, draft);
      setCfg(updated);
      flash("Model settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const resetModel = async (cid: string, model: string) => {
    setError("");
    try {
      const updated = await resetModelSettings(cid, model);
      setCfg(updated);
      setModelDrafts((prev) => ({ ...prev, [draftKey(cid, model)]: { ...MODEL_SETTINGS_DEFAULTS } }));
      flash("Reset to defaults.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const removeConnection = async (connectionId: string) => {
    if (!window.confirm(`Delete connection "${cfg?.connections[connectionId]?.name || connectionId}"?`)) return;
    setError("");
    try {
      setCfg(await deleteConnection(connectionId));
      setModelLists((prev) => {
        const next = { ...prev };
        delete next[connectionId];
        return next;
      });
      if (expandedConnectionId === connectionId) {
        setExpandedConnectionId(null);
        setExpandedModel(null);
      }
      bus.emit("llmConnectionsChanged", undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const addConnection = async () => {
    if (!newConn || !newConn.name.trim() || !newConn.base_url.trim()) return;
    setError("");
    try {
      const result = await createConnection(newConn);
      setCfg(result);
      setNewConn(null);
      bus.emit("llmConnectionsChanged", undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const openCustomConnection = () =>
    setNewConn({ name: "", kind: "openai_compatible", base_url: "", api_mode: "chat_completions", timeout_seconds: DEFAULT_TIMEOUT_SECONDS });

  const saveActiveSelection = async (e: FormEvent) => {
    e.preventDefault();
    if (!cfg) return;
    setError("");
    try {
      setCfg(await postJSON<LLMSettings>("/api/llm-settings", {
        active_connection: cfg.active_connection,
        active_model: cfg.active_model,
        auto_compact: cfg.auto_compact,
      }));
      flash("Saved.");
      bus.emit("llmConnectionsChanged", undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!cfg) return <div className="panel">{error || status || "Loading…"}</div>;

  const connectionIds = Object.keys(cfg.connections);

  return (
    <div className="panel">
      <h2>Models &amp; Connections</h2>
      <p className="hint">
        Manage the LLM connections the Workbench can talk to, which of their models are available, and each
        model's own generation settings.
      </p>
      {status && <p className="hint">{status}</p>}
      {error && <p className="error">{error}</p>}

      <h3>Connections</h3>
      {connectionIds.map((cid) => {
        const conn = cfg.connections[cid];
        const expanded = expandedConnectionId === cid;
        const list = modelLists[cid];
        const drafted = disabledDrafts[cid];
        const enabledCount = list ? list.all_models.length - (drafted ?? new Set(conn.disabled_models)).size : null;

        return (
          <div key={cid} className={`llm-conn-card${conn.paused ? " paused" : ""}`}>
            <div className="llm-conn-header">
              <button
                type="button"
                className="llm-conn-header-main"
                onClick={() => toggleExpandedConnection(cid, conn)}
              >
                <div className="llm-conn-title">
                  <div className="llm-conn-name-row">
                    <span className="llm-conn-name">{conn.name || cid}</span>
                    <span className="llm-kind-badge">{kindLabel(conn.kind)}</span>
                    {conn.paused && <span className="llm-paused-badge">Paused</span>}
                  </div>
                  <div className="llm-conn-url">{conn.base_url}</div>
                </div>
                <div className="llm-conn-meta">
                  {enabledCount !== null ? (
                    <div>
                      {enabledCount} / {list!.all_models.length} models enabled
                    </div>
                  ) : (
                    <div className="hint">models not fetched yet</div>
                  )}
                </div>
                <span className={`llm-chevron${expanded ? " expanded" : ""}`}>›</span>
              </button>
              <label className="llm-pause-toggle" title={conn.paused ? "Paused — click to resume" : "Pause this connection"}>
                <Switch checked={!conn.paused} onChange={() => togglePaused(cid)} />
              </label>
            </div>

            {expanded && (
              <div className="llm-conn-body">
                <div className="llm-field-group">
                  <div className="llm-section-label">Connection details</div>
                  <div className="llm-field-grid">
                    <label className="llm-field">
                      Name
                      <input
                        value={conn.name}
                        placeholder="Connection name"
                        onChange={(e) => setConnectionField(cid, { name: e.target.value })}
                      />
                    </label>
                    <label className="llm-field">
                      Base URL
                      <input
                        value={conn.base_url}
                        placeholder="https://…/v1"
                        onChange={(e) => setConnectionField(cid, { base_url: e.target.value })}
                      />
                    </label>
                    <label className="llm-field">
                      API key
                      <input
                        type="password"
                        value={conn.api_key}
                        placeholder={conn.kind === "ollama" ? "(no auth)" : "API key"}
                        onChange={(e) => setConnectionField(cid, { api_key: e.target.value })}
                      />
                    </label>
                    {isZenConnection(conn.base_url) ? (
                      <p className="hint" style={{ margin: 0, alignSelf: "end" }}>
                        API mode is chosen automatically per model (Responses / Messages / Chat
                        Completions / Gemini) — no manual setting needed.
                      </p>
                    ) : (
                      <label className="llm-field">
                        API mode
                        <select
                          value={conn.api_mode}
                          onChange={(e) => setConnectionField(cid, { api_mode: e.target.value as ApiMode })}
                        >
                          <option value="chat_completions">Chat Completions</option>
                          <option value="responses">Responses</option>
                          <option value="messages">Messages (Anthropic)</option>
                          <option value="gemini">Gemini</option>
                        </select>
                      </label>
                    )}
                    <label className="llm-field">
                      Request timeout (seconds)
                      <input
                        type="number"
                        step="1"
                        min="5"
                        value={conn.timeout_seconds ?? DEFAULT_TIMEOUT_SECONDS}
                        onChange={(e) => setConnectionField(cid, { timeout_seconds: Number(e.target.value) })}
                      />
                    </label>
                  </div>
                  <p className="hint" style={{ marginTop: 4 }}>
                    Timeout is how long to wait for the model to respond before giving up — raise it for a slow
                    local backend.
                  </p>
                  <div className="llm-row-end">
                    <button type="button" className="danger" onClick={() => removeConnection(cid)}>
                      Delete connection
                    </button>
                    <button type="button" className="btn-primary" onClick={() => saveConnection(cid)}>
                      Save connection
                    </button>
                  </div>
                </div>

                <div className="llm-field-group">
                  <div className="llm-models-header">
                    <div className="llm-section-label">Models</div>
                    <span className="hint">Uncheck to hide from selectors</span>
                  </div>
                  <div className="llm-row-end" style={{ justifyContent: "flex-start" }}>
                    <button
                      type="button"
                      className="btn-sm"
                      disabled={loadingModels === cid}
                      onClick={() => fetchModels(cid, conn.disabled_models)}
                    >
                      {loadingModels === cid ? "Refreshing…" : "Refresh models"}
                    </button>
                  </div>

                  {list && (
                    <div className="llm-model-list">
                      {list.all_models.map((m) => {
                        const modelExpanded = expandedModel === m;
                        const draft = modelDrafts[draftKey(cid, m)];
                        const advancedOpen = advancedModel === m;
                        const savedSettings = cfg.connections[cid]?.models?.[m];
                        const ctxWindow = savedSettings?.context_window ?? MODEL_SETTINGS_DEFAULTS.context_window;
                        const enabled = !(drafted ?? new Set(conn.disabled_models)).has(m);

                        return (
                          <div key={m} className="llm-model-card">
                            <div className="llm-model-row">
                              <Switch checked={enabled} onChange={() => toggleModel(cid, m)} />
                              <span className="llm-model-name">{m}</span>
                              <span className="llm-model-ctx">{contextLabel(ctxWindow)}</span>
                              <button type="button" className="btn-sm" onClick={() => toggleModelDetails(cid, m)}>
                                {modelExpanded ? "Hide details" : "Details"}
                              </button>
                            </div>

                            {modelExpanded && draft && (
                              <div className="llm-model-body">
                                <label className="chat-settings-checkbox">
                                  <input
                                    type="checkbox"
                                    checked={draft.vision}
                                    onChange={(e) => setModelDraft(cid, m, { vision: e.target.checked })}
                                  />
                                  Enable vision (send images to this model)
                                  {!modelSupportsVision(m) && (
                                    <span className="hint"> — known not to support vision</span>
                                  )}
                                </label>
                                <div className="llm-field-grid">
                                  <label className="llm-field">
                                    Max output tokens
                                    <input
                                      type="number"
                                      value={draft.max_tokens}
                                      onChange={(e) => setModelDraft(cid, m, { max_tokens: Number(e.target.value) })}
                                    />
                                  </label>
                                  <label className="llm-field">
                                    Context window
                                    <input
                                      type="number"
                                      step="1000"
                                      value={draft.context_window}
                                      onChange={(e) => setModelDraft(cid, m, { context_window: Number(e.target.value) })}
                                    />
                                  </label>
                                </div>
                                <p className="hint">Context window drives auto-compact's threshold for this model.</p>

                                <div>
                                  <button
                                    type="button"
                                    className="llm-advanced-toggle"
                                    onClick={() => setAdvancedModel(advancedOpen ? null : m)}
                                  >
                                    <span className={`llm-chevron-inline${advancedOpen ? " expanded" : ""}`}>›</span>
                                    Advanced settings
                                  </button>

                                  {advancedOpen && (
                                    <div className="llm-advanced-grid">
                                      <label className="llm-field">
                                        Temperature
                                        <input
                                          type="number"
                                          step="0.1"
                                          min="0"
                                          max="2"
                                          value={draft.temperature}
                                          onChange={(e) => setModelDraft(cid, m, { temperature: e.target.value })}
                                        />
                                      </label>
                                      <label className="llm-field">
                                        Top P
                                        <input
                                          type="number"
                                          step="0.05"
                                          min="0"
                                          max="1"
                                          value={draft.top_p}
                                          onChange={(e) => setModelDraft(cid, m, { top_p: e.target.value })}
                                        />
                                      </label>
                                      <label className="llm-field">
                                        Seed
                                        <input
                                          type="number"
                                          step="1"
                                          value={draft.seed}
                                          onChange={(e) => setModelDraft(cid, m, { seed: e.target.value })}
                                        />
                                      </label>
                                      <label className="llm-field">
                                        Frequency penalty
                                        <input
                                          type="number"
                                          step="0.1"
                                          min="-2"
                                          max="2"
                                          value={draft.frequency_penalty}
                                          onChange={(e) => setModelDraft(cid, m, { frequency_penalty: e.target.value })}
                                        />
                                      </label>
                                      <label className="llm-field">
                                        Presence penalty
                                        <input
                                          type="number"
                                          step="0.1"
                                          min="-2"
                                          max="2"
                                          value={draft.presence_penalty}
                                          onChange={(e) => setModelDraft(cid, m, { presence_penalty: e.target.value })}
                                        />
                                      </label>
                                      <label className="llm-field">
                                        Tool choice
                                        <select
                                          value={draft.tool_choice}
                                          onChange={(e) => setModelDraft(cid, m, { tool_choice: e.target.value })}
                                        >
                                          <option value="auto">auto (model decides)</option>
                                          <option value="required">required (must call a tool)</option>
                                          <option value="none">none (disable tool calls)</option>
                                        </select>
                                      </label>
                                    </div>
                                  )}
                                </div>

                                <div className="llm-row-end">
                                  <button type="button" className="btn-sm" onClick={() => resetModel(cid, m)}>
                                    Reset to defaults
                                  </button>
                                  <button type="button" className="btn-sm btn-primary" onClick={() => saveModelSettings(cid, m)}>
                                    Save model settings
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                      <button type="button" onClick={() => saveModels(cid)}>
                        Save enabled/disabled models
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}

      <h3>Add connection</h3>
      <div className="llm-add-row">
        {presets &&
          Object.entries(presets).map(([pid, preset]) => (
            <button
              key={pid}
              type="button"
              className="llm-add-preset-btn"
              onClick={() =>
                setNewConn({
                  name: preset.label,
                  kind: preset.kind,
                  base_url: preset.base_url,
                  api_mode: preset.api_mode,
                  timeout_seconds: DEFAULT_TIMEOUT_SECONDS,
                })
              }
            >
              + {preset.label}
            </button>
          ))}
        <button type="button" className="llm-add-custom-btn" onClick={openCustomConnection}>
          + Custom connection
        </button>
      </div>

      {newConn && (
        <div className="llm-conn-card llm-add-card">
          <div className="llm-field-grid">
            <label className="llm-field">
              Name
              <input
                value={newConn.name}
                placeholder="My connection"
                onChange={(e) => setNewConn({ ...newConn, name: e.target.value })}
              />
            </label>
            <label className="llm-field">
              Base URL
              <input
                value={newConn.base_url}
                placeholder="https://…/v1"
                onChange={(e) => setNewConn({ ...newConn, base_url: e.target.value })}
              />
            </label>
            {!isZenConnection(newConn.base_url) && (
              <label className="llm-field">
                API mode
                <select
                  value={newConn.api_mode}
                  onChange={(e) => setNewConn({ ...newConn, api_mode: e.target.value as ApiMode })}
                >
                  <option value="chat_completions">Chat Completions</option>
                  <option value="responses">Responses</option>
                  <option value="messages">Messages (Anthropic)</option>
                  <option value="gemini">Gemini</option>
                </select>
              </label>
            )}
            <label className="llm-field">
              Request timeout (seconds)
              <input
                type="number"
                step="1"
                min="5"
                value={newConn.timeout_seconds}
                onChange={(e) => setNewConn({ ...newConn, timeout_seconds: Number(e.target.value) })}
              />
            </label>
          </div>
          <div className="llm-row-end">
            <button type="button" onClick={() => setNewConn(null)}>
              Cancel
            </button>
            <button type="button" className="btn-primary" onClick={addConnection}>
              Add connection
            </button>
          </div>
        </div>
      )}

      <h3>Active selection</h3>
      <form onSubmit={saveActiveSelection} className="llm-active-card">
        <p className="hint" style={{ margin: 0 }}>
          The connection/model a brand-new conversation defaults to. Chat's own selector overrides this per
          conversation.
        </p>
        <div className="llm-active-row">
          <label className="llm-field llm-active-field">
            Connection
            <select
              value={cfg.active_connection}
              onChange={(e) => setCfg((c) => (c ? { ...c, active_connection: e.target.value, active_model: "" } : c))}
            >
              {connectionIds.map((cid) => (
                <option key={cid} value={cid}>
                  {cfg.connections[cid]?.name || cid}
                  {cfg.connections[cid]?.paused ? " (paused)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="llm-field llm-active-field">
            Model
            <select
              value={cfg.active_model}
              onChange={(e) => setCfg((c) => (c ? { ...c, active_model: e.target.value } : c))}
            >
              <option value="">(fetch models first)</option>
              {(modelLists[cfg.active_connection]?.models ?? []).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          {!modelLists[cfg.active_connection] && cfg.active_connection && (
            <button
              type="button"
              disabled={loadingModels === cfg.active_connection}
              onClick={() =>
                fetchModels(cfg.active_connection, cfg.connections[cfg.active_connection]?.disabled_models ?? [])
              }
            >
              {loadingModels === cfg.active_connection ? "Fetching…" : "Fetch models"}
            </button>
          )}
        </div>

        <div className="llm-toggle-row">
          <div>
            <div className="llm-toggle-title">Auto-compact conversations</div>
            <p className="hint" style={{ marginTop: 2 }}>
              Summarize history automatically as a conversation nears its model's context window (set per model
              under Details above). Off by default — this is a lossy, irreversible rewrite.
            </p>
          </div>
          <Switch
            large
            checked={cfg.auto_compact}
            onChange={() => setCfg((c) => (c ? { ...c, auto_compact: !c.auto_compact } : c))}
          />
        </div>

        <div className="llm-row-end">
          <button type="submit" className="btn-primary">Save</button>
        </div>
      </form>

      <p className="hint">
        Standing memory (formerly "base prompt") and all workflow prompts now
        live in the Prompts panel.
      </p>
    </div>
  );
}
