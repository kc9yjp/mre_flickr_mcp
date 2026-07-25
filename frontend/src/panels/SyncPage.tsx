// Sync panel: per-type sync status table, trigger buttons, and the
// database reset action.

import { useEffect, useRef, useState } from "react";
import { SyncStatus, getJSON, postJSON } from "../api";
import { relativeTime } from "../format";

const SYNC_TYPES = ["photos", "contacts", "groups", "albums"] as const;

export function SyncPage() {
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const aliveRef = useRef(true);

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
              <th>Last</th>
              <th>Duration</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sync.rows.map((r) => (
              <tr key={r.type}>
                <td>{r.type}</td>
                <td>{relativeTime(r.last)}</td>
                <td>{r.duration ?? "—"}</td>
                <td>
                  {r.running ? (
                    <span className="badge-running">⟳ running</span>
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
