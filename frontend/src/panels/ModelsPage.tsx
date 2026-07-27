// Models panel: a list of named LLM connections (add / update / delete).
// "Update" expands a connection inline to edit its base URL/API key/api
// mode, refresh its model list, and enable/disable individual models. Each
// model also has a "Details" panel of its own — vision/max_tokens/sampling/
// tool_choice all apply to one specific model, not the whole connection or
// the whole page (a model's capabilities and ideal settings vary even
// within one connection).

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
}

function draftKey(connectionId: string, model: string): string {
  return `${connectionId}::${model}`;
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

  const saveActiveSelection = async (e: FormEvent) => {
    e.preventDefault();
    if (!cfg) return;
    setError("");
    try {
      setCfg(await postJSON<LLMSettings>("/api/llm-settings", {
        active_connection: cfg.active_connection,
        active_model: cfg.active_model,
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
      {status && <p className="hint">{status}</p>}
      {error && <p className="error">{error}</p>}

      <h3>Connections</h3>
      {connectionIds.map((cid) => {
        const conn = cfg.connections[cid];
        const expanded = expandedConnectionId === cid;
        const list = modelLists[cid];
        const drafted = disabledDrafts[cid];
        return (
          <div key={cid} className="settings-item">
            <div className="settings-row">
              <strong>{conn.name || cid}</strong>
              <span className="hint">— {conn.kind}, {conn.base_url}</span>
              <button type="button" className="btn-sm" onClick={() => toggleExpandedConnection(cid, conn)}>
                {expanded ? "Close" : "Update"}
              </button>
              <button type="button" className="danger" onClick={() => removeConnection(cid)}>
                Delete
              </button>
            </div>

            {expanded && (
              <div style={{ marginTop: 8 }}>
                <div className="settings-row">
                  <input
                    value={conn.name}
                    placeholder="Connection name"
                    onChange={(e) => setConnectionField(cid, { name: e.target.value })}
                  />
                  <span className="hint">name</span>
                </div>
                <div className="settings-row">
                  <input
                    value={conn.base_url}
                    placeholder="https://…/v1"
                    onChange={(e) => setConnectionField(cid, { base_url: e.target.value })}
                  />
                  <span className="hint">base URL</span>
                </div>
                <div className="settings-row">
                  <input
                    value={conn.api_key}
                    placeholder={conn.kind === "ollama" ? "(no auth)" : "API key"}
                    onChange={(e) => setConnectionField(cid, { api_key: e.target.value })}
                  />
                  <span className="hint">API key</span>
                </div>
                <div className="settings-row">
                  <label>
                    API mode
                    <select
                      value={conn.api_mode}
                      onChange={(e) => setConnectionField(cid, { api_mode: e.target.value as ApiMode })}
                    >
                      <option value="chat_completions">Chat Completions</option>
                      <option value="responses">Responses</option>
                    </select>
                  </label>
                  <button type="button" className="btn-sm" onClick={() => saveConnection(cid)}>
                    Save connection
                  </button>
                </div>

                <div className="settings-row">
                  <button
                    type="button"
                    disabled={loadingModels === cid}
                    onClick={() => fetchModels(cid, conn.disabled_models)}
                  >
                    {loadingModels === cid ? "Refreshing…" : "Refresh models"}
                  </button>
                </div>

                {list && (
                  <div className="settings-row" style={{ flexDirection: "column", alignItems: "flex-start" }}>
                    <p className="hint" style={{ marginTop: 4 }}>
                      {list.all_models.length} model{list.all_models.length !== 1 ? "s" : ""} — uncheck to hide from
                      the chat selector; use Details to set that model's own vision/output/sampling settings.
                    </p>
                    {list.all_models.map((m) => {
                      const modelExpanded = expandedModel === m;
                      const draft = modelDrafts[draftKey(cid, m)];
                      return (
                        <div key={m} style={{ width: "100%" }}>
                          <div className="settings-row">
                            <label className="chat-settings-checkbox">
                              <input
                                type="checkbox"
                                checked={!(drafted ?? new Set(conn.disabled_models)).has(m)}
                                onChange={() => toggleModel(cid, m)}
                              />
                              {m}
                            </label>
                            <button type="button" className="btn-sm" onClick={() => toggleModelDetails(cid, m)}>
                              {modelExpanded ? "Hide details" : "Details"}
                            </button>
                          </div>

                          {modelExpanded && draft && (
                            <div className="settings-item" style={{ marginLeft: 16 }}>
                              <label className="chat-settings-checkbox">
                                <input
                                  type="checkbox"
                                  checked={draft.vision}
                                  onChange={(e) => setModelDraft(cid, m, { vision: e.target.checked })}
                                />
                                Enable vision (send images to LLM)
                                <span className="hint">
                                  {" "}— only enable if this model supports vision
                                  {!modelSupportsVision(m) && <> (known not to support vision)</>}
                                </span>
                              </label>
                              <label>
                                Max tokens
                                <input
                                  type="number"
                                  value={draft.max_tokens}
                                  onChange={(e) => setModelDraft(cid, m, { max_tokens: Number(e.target.value) })}
                                />
                              </label>
                              <label>
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
                              <label>
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
                              <label>
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
                              <label>
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
                              <label>
                                Seed
                                <input
                                  type="number"
                                  step="1"
                                  value={draft.seed}
                                  onChange={(e) => setModelDraft(cid, m, { seed: e.target.value })}
                                />
                              </label>
                              <label>
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
                              <div className="settings-row">
                                <button type="button" className="btn-sm" onClick={() => saveModelSettings(cid, m)}>
                                  Save
                                </button>
                                <button type="button" className="btn-sm" onClick={() => resetModel(cid, m)}>
                                  Reset to defaults
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
            )}
          </div>
        );
      })}

      <h3>Add connection</h3>
      <div className="settings-item">
        {presets && (
          <div className="settings-row">
            {Object.entries(presets).map(([pid, preset]) => (
              <button
                key={pid}
                type="button"
                onClick={() =>
                  setNewConn({ name: preset.label, kind: preset.kind, base_url: preset.base_url, api_mode: preset.api_mode })
                }
              >
                Quick add: {preset.label}
              </button>
            ))}
          </div>
        )}
        {newConn && (
          <>
            <div className="settings-row">
              <input
                value={newConn.name}
                placeholder="Connection name"
                onChange={(e) => setNewConn({ ...newConn, name: e.target.value })}
              />
              <span className="hint">name</span>
            </div>
            <div className="settings-row">
              <input
                value={newConn.base_url}
                placeholder="https://…/v1"
                onChange={(e) => setNewConn({ ...newConn, base_url: e.target.value })}
              />
              <span className="hint">base URL</span>
            </div>
            <div className="settings-row">
              <label>
                API mode
                <select
                  value={newConn.api_mode}
                  onChange={(e) => setNewConn({ ...newConn, api_mode: e.target.value as ApiMode })}
                >
                  <option value="chat_completions">Chat Completions</option>
                  <option value="responses">Responses</option>
                </select>
              </label>
              <button type="button" onClick={addConnection}>
                Add connection
              </button>
              <button type="button" onClick={() => setNewConn(null)}>
                Cancel
              </button>
            </div>
          </>
        )}
      </div>

      <h3>Active selection</h3>
      <p className="hint">
        The connection/model a brand-new conversation defaults to. The Chat panel's own selector overrides this per
        conversation.
      </p>
      <form onSubmit={saveActiveSelection} className="settings-item">
        <div className="settings-row">
          <label>
            Connection
            <select
              value={cfg.active_connection}
              onChange={(e) => setCfg((c) => (c ? { ...c, active_connection: e.target.value, active_model: "" } : c))}
            >
              {connectionIds.map((cid) => (
                <option key={cid} value={cid}>
                  {cfg.connections[cid]?.name || cid}
                </option>
              ))}
            </select>
          </label>
          <label>
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
          <button type="submit">Save</button>
        </div>
      </form>

      <p className="hint">
        Standing memory (formerly "base prompt") and all workflow prompts now
        live in the Prompts panel.
      </p>
    </div>
  );
}
