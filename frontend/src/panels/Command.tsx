import { useState } from "react";
import { ApiError, postJSON } from "../api";

const SYNCS: { type: string; label: string; full?: boolean }[] = [
  { type: "photos", label: "Sync photos" },
  { type: "photos", label: "Sync photos (full)", full: true },
  { type: "backfill", label: "Backfill photos" },
  { type: "contacts", label: "Sync contacts" },
  { type: "groups", label: "Sync groups" },
  { type: "albums", label: "Sync albums" },
  { type: "all", label: "Sync everything" },
];

export function Command() {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const trigger = async (type: string, label: string, full?: boolean) => {
    setBusy(true);
    setMessage("");
    try {
      await postJSON(`/api/sync/${type}${full ? "?full=1" : ""}`);
      setMessage(`${label} started — watch progress in the Summary panel.`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setMessage("A sync is already running — wait for it to finish.");
      } else {
        setMessage(`Failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel command">
      <h3>Sync</h3>
      <div className="command-buttons">
        {SYNCS.map((s) => (
          <button key={s.label} disabled={busy} onClick={() => trigger(s.type, s.label, s.full)}>
            {s.label}
          </button>
        ))}
      </div>
      {message && <p className="command-message">{message}</p>}
      <h3>Workflows</h3>
      <p className="hint">Workflow prompts arrive with the chat agent (milestone 5).</p>
    </div>
  );
}
