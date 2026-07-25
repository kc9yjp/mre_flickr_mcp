// Commands panel: sync trigger buttons plus workflow-command shortcuts
// scoped to the currently focused photo (or global commands).

import { useState } from "react";
import { ApiError, postJSON } from "../api";
import * as bus from "../bus";
import { useWorkflowCommands } from "../useWorkflowCommands";

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
  const commands = useWorkflowCommands("global");

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
      <p className="hint">Each button starts the workflow in the Chat panel.</p>
      <div className="command-buttons">
        {commands.map((c) => (
          <button
            key={c.id}
            onClick={() => bus.emit("runCommand", { title: c.label, text: c.prompt, promptId: c.prompt_id })}
          >
            {c.label}
          </button>
        ))}
      </div>
      <p className="hint">
        Photo-specific workflows (metadata, groups, albums, thresholds) are on each
        photo's detail view in the Photo Browser.
      </p>
    </div>
  );
}
