import { FormEvent, useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, listModels, modelSupportsVision, LLMSettings, ProviderProfile } from "../api";

const PROVIDER_IDS = ["ollama", "zen"];

export function ModelsPage() {
  const [cfg, setCfg] = useState<LLMSettings | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [modelLists, setModelLists] = useState<Record<string, string[]>>({});
  const [loadingModels, setLoadingModels] = useState<string | null>(null);

  useEffect(() => {
    getJSON<LLMSettings>("/api/llm-settings")
      .then((s) => {
        if (!s.providers) {
          setError("Error: providers not in response. Backend may be misconfigured.");
        }
        setCfg(s);
      })
      .catch((e) => setError((e instanceof Error ? e.message : String(e))));
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
    async (providerId: string) => {
      setLoadingModels(providerId);
      setError("");
      try {
        const models = await listModels(providerId);
        setModelLists((prev) => ({ ...prev, [providerId]: models }));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoadingModels(null);
      }
    },
    [],
  );

  const setProvider = useCallback(
    (pid: string, patch: Partial<ProviderProfile>) => {
      setCfg((c) =>
        c
          ? {
              ...c,
              providers: { ...c.providers, [pid]: { ...c.providers[pid], ...patch } },
            }
          : c,
      );
    },
    [],
  );

  if (!cfg) return <div className="panel">{error || status || "Loading…"}</div>;

  return (
    <div className="panel">
      <h2>Models &amp; Providers</h2>
      <form onSubmit={save}>
        <h3>Providers</h3>
        {PROVIDER_IDS.map((pid) => {
          const prof: ProviderProfile = (cfg?.providers?.[pid]) ?? { label: pid, base_url: "", api_key: "" };
          return (
            <div key={pid} className="settings-item">
              <label className="settings-label">
                {prof.label || pid}
              </label>
              <div className="settings-row">
                <input
                  value={prof.base_url}
                  placeholder="https://…/v1"
                  onChange={(e) => setProvider(pid, { base_url: e.target.value })}
                />
                <span className="hint">base URL</span>
              </div>
              <div className="settings-row">
                <input
                  value={prof.api_key}
                  placeholder={pid === "ollama" ? "(no auth)" : "API key"}
                  onChange={(e) => setProvider(pid, { api_key: e.target.value })}
                />
                <span className="hint">API key</span>
                <button
                  type="button"
                  disabled={loadingModels === pid}
                  onClick={() => fetchModels(pid)}
                >
                  {loadingModels === pid ? "Fetching…" : "Fetch models"}
                </button>
              </div>
              {modelLists[pid] && (
                <p className="hint" style={{ marginTop: 4 }}>
                  {modelLists[pid].length} model{modelLists[pid].length !== 1 ? "s" : ""} available
                </p>
              )}
            </div>
          );
        })}

        <h3>Active selection</h3>
        <div className="settings-item">
          <div className="settings-row">
            <label>
              Provider
              <select
                value={cfg.active_provider}
                onChange={(e) => {
                  setCfg((c) => c ? { ...c, active_provider: e.target.value, active_model: "" } : c);
                }}
              >
                {PROVIDER_IDS.map((pid) => (
                  <option key={pid} value={pid}>
                    {cfg.providers[pid]?.label || pid}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Model
              <select
                value={cfg.active_model}
                onChange={(e) => setCfg((c) => c ? { ...c, active_model: e.target.value } : c)}
              >
                <option value="">(fetch models first)</option>
                {(modelLists[cfg.active_provider] ?? []).map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            {!modelLists[cfg.active_provider] && (
              <button
                type="button"
                disabled={loadingModels === cfg.active_provider}
                onClick={() => fetchModels(cfg.active_provider)}
              >
                {loadingModels === cfg.active_provider ? "Fetching…" : "Fetch models"}
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
              onChange={(e) => setCfg((c) => c ? { ...c, max_tokens: Number(e.target.value) } : c)}
            />
          </label>
          <label className="chat-settings-checkbox">
            <input
              type="checkbox"
              checked={cfg.vision}
              onChange={(e) => setCfg((c) => c ? { ...c, vision: e.target.checked } : c)}
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
        <p className="hint">Leave blank to use the provider's default.</p>
        <div className="settings-item">
          <label>
            Temperature
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={cfg.temperature}
              onChange={(e) => setCfg((c) => c ? { ...c, temperature: e.target.value } : c)}
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
              onChange={(e) => setCfg((c) => c ? { ...c, top_p: e.target.value } : c)}
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
              onChange={(e) => setCfg((c) => c ? { ...c, frequency_penalty: e.target.value } : c)}
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
              onChange={(e) => setCfg((c) => c ? { ...c, presence_penalty: e.target.value } : c)}
            />
          </label>
          <label>
            Seed
            <input
              type="number"
              step="1"
              value={cfg.seed}
              onChange={(e) => setCfg((c) => c ? { ...c, seed: e.target.value } : c)}
            />
          </label>
          <label>
            Tool choice
            <select
              value={cfg.tool_choice}
              onChange={(e) => setCfg((c) => c ? { ...c, tool_choice: e.target.value } : c)}
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
