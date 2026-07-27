// Models panel: manage any number of named LLM connections (base URL/API
// key/api mode), per-connection model enable/disable checkboxes, output/
// sampling settings, and the active connection + model.

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  getJSON,
  postJSON,
  listModels,
  getConnectionPresets,
  createConnection,
  updateConnection,
  deleteConnection,
  modelSupportsVision,
  ApiMode,
  Connection,
  ConnectionKind,
  ConnectionPreset,
  LLMSettings,
  ModelList,
} from "../api";

interface NewConnectionDraft {
  name: string;
  kind: ConnectionKind;
  base_url: string;
  api_mode: ApiMode;
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

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!cfg) return;
    setStatus("");
    setError("");
    try {
      setCfg(await postJSON<LLMSettings>("/api/llm-settings", cfg));
      setStatus("Saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
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

  const setConnection = useCallback(
    (cid: string, patch: Partial<Connection>) => {
      setCfg((c) =>
        c
          ? { ...c, connections: { ...c.connections, [cid]: { ...c.connections[cid], ...patch } } }
          : c,
      );
    },
    [],
  );

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
      setStatus("Model list saved.");
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
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!cfg) return <div className="panel">{error || status || "Loading…"}</div>;

  const connectionIds = Object.keys(cfg.connections);

  return (
    <div className="panel">
      <h2>Models &amp; Connections</h2>
      <form onSubmit={save}>
        <h3>Connections</h3>
        {connectionIds.map((cid) => {
          const conn = cfg.connections[cid];
          const list = modelLists[cid];
          const drafted = disabledDrafts[cid];
          return (
            <div key={cid} className="settings-item">
              <label className="settings-label">
                {conn.name || cid}
                <span className="hint"> — {conn.kind}</span>
              </label>
              <div className="settings-row">
                <input
                  value={conn.name}
                  placeholder="Connection name"
                  onChange={(e) => setConnection(cid, { name: e.target.value })}
                />
                <span className="hint">name</span>
              </div>
              <div className="settings-row">
                <input
                  value={conn.base_url}
                  placeholder="https://…/v1"
                  onChange={(e) => setConnection(cid, { base_url: e.target.value })}
                />
                <span className="hint">base URL</span>
              </div>
              <div className="settings-row">
                <input
                  value={conn.api_key}
                  placeholder={conn.kind === "ollama" ? "(no auth)" : "API key"}
                  onChange={(e) => setConnection(cid, { api_key: e.target.value })}
                />
                <span className="hint">API key</span>
              </div>
              <div className="settings-row">
                <label>
                  API mode
                  <select
                    value={conn.api_mode}
                    onChange={(e) => setConnection(cid, { api_mode: e.target.value as ApiMode })}
                  >
                    <option value="chat_completions">Chat Completions</option>
                    <option value="responses">Responses</option>
                  </select>
                </label>
                <button
                  type="button"
                  disabled={loadingModels === cid}
                  onClick={() => fetchModels(cid, conn.disabled_models)}
                >
                  {loadingModels === cid ? "Fetching…" : "Fetch models"}
                </button>
                <button type="button" className="danger" onClick={() => removeConnection(cid)}>
                  Delete
                </button>
              </div>
              {list && (
                <div className="settings-row" style={{ flexDirection: "column", alignItems: "flex-start" }}>
                  <p className="hint" style={{ marginTop: 4 }}>
                    {list.all_models.length} model{list.all_models.length !== 1 ? "s" : ""} — uncheck to hide from the chat selector
                  </p>
                  {list.all_models.map((m) => (
                    <label key={m} className="chat-settings-checkbox">
                      <input
                        type="checkbox"
                        checked={!(drafted ?? new Set(conn.disabled_models)).has(m)}
                        onChange={() => toggleModel(cid, m)}
                      />
                      {m}
                    </label>
                  ))}
                  <button type="button" onClick={() => saveModels(cid)}>
                    Save models
                  </button>
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
        <div className="settings-item">
          <div className="settings-row">
            <label>
              Connection
              <select
                value={cfg.active_connection}
                onChange={(e) => {
                  setCfg((c) => (c ? { ...c, active_connection: e.target.value, active_model: "" } : c));
                }}
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
          </div>
        </div>

        <h3>Output</h3>
        <div className="settings-item">
          <label>
            Max tokens
            <input
              type="number"
              value={cfg.max_tokens}
              onChange={(e) => setCfg((c) => (c ? { ...c, max_tokens: Number(e.target.value) } : c))}
            />
          </label>
          <label className="chat-settings-checkbox">
            <input
              type="checkbox"
              checked={cfg.vision}
              onChange={(e) => setCfg((c) => (c ? { ...c, vision: e.target.checked } : c))}
            />
            Enable vision (send images to LLM)
            <span className="hint">
              {" "}— only enable if the model supports vision
              {cfg.active_model && !modelSupportsVision(cfg.active_model) && (
                <> (current model does not support vision)</>
              )}
            </span>
          </label>
        </div>

        <h3>Sampling &amp; tool use</h3>
        <p className="hint">Leave blank to use the connection's default.</p>
        <div className="settings-item">
          <label>
            Temperature
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={cfg.temperature}
              onChange={(e) => setCfg((c) => (c ? { ...c, temperature: e.target.value } : c))}
            />
          </label>
          <label>
            Top P
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={cfg.top_p}
              onChange={(e) => setCfg((c) => (c ? { ...c, top_p: e.target.value } : c))}
            />
          </label>
          <label>
            Frequency penalty
            <input
              type="number"
              step="0.1"
              min="-2"
              max="2"
              value={cfg.frequency_penalty}
              onChange={(e) => setCfg((c) => (c ? { ...c, frequency_penalty: e.target.value } : c))}
            />
          </label>
          <label>
            Presence penalty
            <input
              type="number"
              step="0.1"
              min="-2"
              max="2"
              value={cfg.presence_penalty}
              onChange={(e) => setCfg((c) => (c ? { ...c, presence_penalty: e.target.value } : c))}
            />
          </label>
          <label>
            Seed
            <input
              type="number"
              step="1"
              value={cfg.seed}
              onChange={(e) => setCfg((c) => (c ? { ...c, seed: e.target.value } : c))}
            />
          </label>
          <label>
            Tool choice
            <select
              value={cfg.tool_choice}
              onChange={(e) => setCfg((c) => (c ? { ...c, tool_choice: e.target.value } : c))}
            >
              <option value="auto">auto (model decides)</option>
              <option value="required">required (must call a tool)</option>
              <option value="none">none (disable tool calls)</option>
            </select>
          </label>
        </div>

        <p className="hint">
          Standing memory (formerly "base prompt") and all workflow prompts now
          live in the Prompts panel.
        </p>

        <div style={{ marginTop: 12 }}>
          <button type="submit">Save</button>
          {status && <span className="hint" style={{ marginLeft: 8 }}>{status}</span>}
          {error && <span className="error" style={{ marginLeft: 8 }}>{error}</span>}
        </div>
      </form>
    </div>
  );
}
