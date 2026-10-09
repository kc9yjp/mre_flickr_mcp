// Queue panel: waiting/errored/completed group-add queue items, with retry
// and delete actions, plus the scheduled-publish queue (schedule_publish).

import { useEffect, useState } from "react";
import { PublishQueueData, QueueData, getJSON, postJSON } from "../api";

export function Queue() {
  const [data, setData] = useState<QueueData | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    getJSON<QueueData>("/api/queue")
      .then(setData)
      .catch((e) => setError(e.message));

  useEffect(() => { load(); }, []);

  const action = async (body: object) => {
    setBusy(true);
    setMsg("");
    try {
      const r = await postJSON<{ ok: boolean; added?: number; deleted?: number; published?: number; errors?: number }>(
        "/api/queue", body,
      );
      if ("deleted" in r) setMsg(r.deleted ? "Item removed." : "Item not found.");
      else if ("published" in r) setMsg(`Done: ${r.published ?? 0} published${r.errors ? `, ${r.errors} failed` : ""}.`);
      else setMsg(`Done: ${r.added ?? 0} added.`);
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <div className="panel"><p className="error">{error}</p></div>;
  if (!data) return <div className="panel">Loading…</div>;

  const { counts, waiting, errors, successes } = data;

  return (
    <div className="panel">
      <h2>Group Queue</h2>

      <div className="queue-stats">
        <span className="queue-stat-pill waiting">{counts.waiting} waiting</span>
        <span className="queue-stat-pill success">{counts.success} succeeded</span>
        <span className="queue-stat-pill errored">{counts.error} failed</span>
      </div>

      {msg && <p className="hint">{msg}</p>}

      <div className="queue-section-header">
        <h3>Waiting</h3>
        {waiting.length > 0 && (
          <div className="queue-actions">
            <button disabled={busy} onClick={() => action({ action: "retry_ready" })}>Retry ready</button>
            <button disabled={busy} onClick={() => action({ action: "retry_all" })}>Retry all now</button>
          </div>
        )}
      </div>

      {waiting.length === 0 ? (
        <p className="hint">Nothing waiting.</p>
      ) : (
        <table className="sync-table queue-table">
          <thead>
            <tr><th>Photo</th><th>Group</th><th>Scheduled</th><th></th></tr>
          </thead>
          <tbody>
            {waiting.map((r) => (
              <tr key={r.id}>
                <td><a href={r.photo_url} target="_blank" rel="noreferrer">{r.photo_title}</a></td>
                <td><a href={r.group_url} target="_blank" rel="noreferrer">{r.group_name}</a></td>
                <td className="queue-time">{r.retry_at ?? "—"}</td>
                <td>
                  <button
                    className="btn-danger-sm"
                    disabled={busy}
                    onClick={() => action({ action: "delete_item", item_id: r.id })}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {errors.length > 0 && (
        <>
          <h3>Errors</h3>
          <table className="sync-table queue-table">
            <thead><tr><th>Photo</th><th>Group</th><th>Error</th><th>Queued</th></tr></thead>
            <tbody>
              {errors.map((r) => (
                <tr key={r.id}>
                  <td><a href={r.photo_url} target="_blank" rel="noreferrer">{r.photo_title}</a></td>
                  <td><a href={r.group_url} target="_blank" rel="noreferrer">{r.group_name}</a></td>
                  <td className="error">{r.error_msg}</td>
                  <td className="queue-time">{r.queued_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {successes.length > 0 && (
        <>
          <h3>Recent successes</h3>
          <table className="sync-table queue-table">
            <thead><tr><th>Photo</th><th>Group</th><th>Completed</th></tr></thead>
            <tbody>
              {successes.map((r) => (
                <tr key={r.id}>
                  <td><a href={r.photo_url} target="_blank" rel="noreferrer">{r.photo_title}</a></td>
                  <td><a href={r.group_url} target="_blank" rel="noreferrer">{r.group_name}</a></td>
                  <td className="queue-time">{r.completed_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data.publishes && <PublishSection pub={data.publishes} busy={busy} action={action} />}
    </div>
  );
}

function PublishSection({ pub, busy, action }: {
  pub: PublishQueueData;
  busy: boolean;
  action: (body: object) => void;
}) {
  const { counts, waiting, errors, successes } = pub;
  return (
    <>
      <h2>Scheduled Publishes</h2>

      <div className="queue-stats">
        <span className="queue-stat-pill waiting">{counts.waiting} waiting</span>
        <span className="queue-stat-pill success">{counts.success} published</span>
        <span className="queue-stat-pill errored">{counts.error} failed</span>
      </div>

      <div className="queue-section-header">
        <h3>Waiting</h3>
        {waiting.length > 0 && (
          <div className="queue-actions">
            <button disabled={busy} onClick={() => action({ action: "publish_now" })}>Publish all now</button>
          </div>
        )}
      </div>

      {waiting.length === 0 ? (
        <p className="hint">Nothing scheduled.</p>
      ) : (
        <table className="sync-table queue-table">
          <thead>
            <tr><th>Photo</th><th>Publishes</th><th></th></tr>
          </thead>
          <tbody>
            {waiting.map((r) => (
              <tr key={r.id}>
                <td><a href={r.photo_url} target="_blank" rel="noreferrer">{r.photo_title}</a></td>
                <td className="queue-time">{r.publish_at}</td>
                <td>
                  <button
                    className="btn-danger-sm"
                    disabled={busy}
                    onClick={() => action({ action: "delete_publish", item_id: r.id })}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {errors.length > 0 && (
        <>
          <h3>Errors</h3>
          <table className="sync-table queue-table">
            <thead><tr><th>Photo</th><th>Error</th><th>Failed</th></tr></thead>
            <tbody>
              {errors.map((r) => (
                <tr key={r.id}>
                  <td><a href={r.photo_url} target="_blank" rel="noreferrer">{r.photo_title}</a></td>
                  <td className="error">{r.error_msg}</td>
                  <td className="queue-time">{r.completed_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {successes.length > 0 && (
        <>
          <h3>Recently published</h3>
          <table className="sync-table queue-table">
            <thead><tr><th>Photo</th><th>Published</th></tr></thead>
            <tbody>
              {successes.map((r) => (
                <tr key={r.id}>
                  <td>
                    <a href={r.photo_url} target="_blank" rel="noreferrer">{r.photo_title}</a>
                    {r.error_msg && <div className="hint">{r.error_msg}</div>}
                  </td>
                  <td className="queue-time">{r.completed_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
