// Sync panel: per-type sync status table, trigger buttons, and the
// database reset action.

import { useEffect, useRef, useState } from "react";
import { LLMSettings, ModelList, SyncStatus, getJSON, listModels, postJSON } from "../api";
import { relativeTime, syncStatusLabel } from "../format";

const SYNC_TYPES = ["photos", "contacts", "groups", "albums"] as const;

export function SyncPage() {
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const aliveRef = useRef(true);

  // Model used for background sync jobs that call an LLM (currently the AI
  // group-summary phase; more sync jobs may use this same setting later).
  // This is a global preference, not a per-run picker.
  const [llmSettings, setLlmSettings] = useState<LLMSettings | null>(null);
  const [syncModels, setSyncModels] = useState<ModelList | null>(null);
  const [modelMsg, setModelMsg] = useState("");

  useEffect(() => {
    getJSON<LLMSettings>("/api/llm-settings")
      .then((s) => {
        setLlmSettings(s);
        if (s.sync_connection && s.connections[s.sync_connection]) {
          listModels(s.sync_connection).then(setSyncModels).catch(() => setSyncModels(null));
        }
      })
      .catch(() => {});
  }, []);

  const saveSyncModel = async (connection: string, model: string) => {
    setModelMsg("");
    try {
      const saved = await postJSON<LLMSettings>("/api/llm-settings", {
        sync_connection: connection,
        sync_model: model,
      });
      setLlmSettings(saved);
      setModelMsg("Saved.");
    } catch (e) {
      setModelMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const onSyncConnectionChange = async (connection: string) => {
    setSyncModels(null);
    // Auto-pick the connection's first available model rather than leaving
    // it blank: an explicit connection with no model would otherwise resolve
    // to nothing usable (the backend won't borrow the chat model from a
    // different connection — see resolve_sync_cfg).
    let firstModel = "";
    if (connection && llmSettings?.connections[connection]) {
      try {
        const models = await listModels(connection);
        setSyncModels(models);
        firstModel = models.models[0] ?? "";
      } catch {
        setSyncModels(null);
      }
    }
    await saveSyncModel(connection, firstModel);
  };

  const saveThrottle = async (seconds: number) => {
    setModelMsg("");
    try {
      const saved = await postJSON<LLMSettings>("/api/llm-settings", {
        sync_throttle_seconds: seconds,
      });
      setLlmSettings(saved);
      setModelMsg("Saved.");
    } catch (e) {
      setModelMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const load = async () => {
    try {
      const s = await getJSON<SyncStatus>("/api/sync/status");
      if (aliveRef.current) setSync(s);
      return s.running;
    } catch {
      return false;
    }
  };

  useEffect(() => {
    aliveRef.current = true;
    const poll = async () => {
      const running = await load();
      if (aliveRef.current) {
        timer.current = window.setTimeout(poll, running ? 5000 : 30000);
      }
    };
    poll();
    return () => {
      aliveRef.current = false;
      window.clearTimeout(timer.current);
    };
  }, []);

  const trigger = async (type: string, full = false) => {
    setBusy(true);
    setMsg("");
    try {
      const url = `/api/sync/${type}${full ? "?full=1" : ""}`;
      await postJSON(url);
      setMsg(`${type} sync started.`);
      window.setTimeout(load, 1000);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const resetDb = async () => {
    setBusy(true);
    setMsg("");
    try {
      await postJSON("/api/reset");
      setMsg("Database reset and full sync started.");
      setConfirmReset(false);
      window.setTimeout(load, 1000);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <h2>Sync</h2>

      {sync && (
        <table className="sync-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Total</th>
              <th>Pending summaries</th>
              <th>Last</th>
              <th>Duration</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sync.rows.map((r) => (
              <tr key={r.type}>
                <td>{r.type}</td>
                <td>{r.total != null ? r.total.toLocaleString("en") : "—"}</td>
                <td>{r.pending_summary != null ? r.pending_summary.toLocaleString("en") : "—"}</td>
                <td>{relativeTime(r.last)}</td>
                <td>{r.duration ?? "—"}</td>
                <td>
                  {r.running ? (
                    <span className="badge-running">{syncStatusLabel(r.running, r.phase)}</span>
                  ) : (
                    "idle"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Trigger</h3>
      <div className="sync-btn-row">
        {SYNC_TYPES.map((t) => (
          <button key={t} disabled={busy || sync?.running} onClick={() => trigger(t)}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
        <button disabled={busy || sync?.running} onClick={() => trigger("all")} className="btn-primary">
          Sync All
        </button>
        <button disabled={busy || sync?.running} onClick={() => trigger("photos", true)}>
          Full Photo Sync
        </button>
        <button disabled={busy || sync?.running} onClick={() => trigger("backfill")}>
          Backfill All
        </button>
      </div>

      {msg && <p className={msg.includes("Error") || msg.includes("error") ? "error" : "hint"}>{msg}</p>}

      <h3>Sync Model</h3>
      <p className="hint">
        Model used by background sync jobs that call an LLM (currently the AI group-summary
        phase). This is a global preference shared by every sync job, separate from your chat
        model. Leave on "Use chat model" to always follow whatever model is active in Chat.
        The throttle paces successive requests within one sync run — defaults to 60s (one
        per minute), gentle on a local LLM.
      </p>
      {llmSettings && (
        <div className="sync-model-row">
          <select
            value={llmSettings.sync_connection}
            onChange={(e) => onSyncConnectionChange(e.target.value)}
          >
            <option value="">Use chat model ({llmSettings.active_connection || "none set"})</option>
            {Object.entries(llmSettings.connections).map(([id, conn]) => (
              <option key={id} value={id}>{conn.name}</option>
            ))}
          </select>
          {llmSettings.sync_connection && (
            <select
              value={llmSettings.sync_model}
              onChange={(e) => saveSyncModel(llmSettings.sync_connection, e.target.value)}
            >
              <option value="">Default model</option>
              {(syncModels?.models ?? []).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          )}
          <label className="sync-throttle-label">
            Throttle
            <input
              type="number"
              min={0}
              step={1}
              className="sync-throttle-input"
              value={llmSettings.sync_throttle_seconds}
              onChange={(e) => saveThrottle(Math.max(0, Number(e.target.value) || 0))}
            />
            sec between requests
          </label>
          {modelMsg && <span className="hint">{modelMsg}</span>}
        </div>
      )}

      <h3>Reset Database</h3>
      <p className="hint">
        Deletes your local database and re-syncs from scratch. Credentials and API key are not affected.
      </p>
      {!confirmReset ? (
        <button onClick={() => setConfirmReset(true)}>Reset DB…</button>
      ) : (
        <div className="confirm-inline">
          <span className="error">Delete your local database? This cannot be undone.</span>
          <button onClick={resetDb} disabled={busy} className="btn-danger">Yes, reset</button>
          <button onClick={() => setConfirmReset(false)}>Cancel</button>
        </div>
      )}
    </div>
  );
}
