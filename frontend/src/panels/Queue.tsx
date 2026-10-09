// Queue panel: the two kinds of deferred work the server does on its own —
// scheduled publishes (schedule_publish / Photo Viewer) and group-pool adds
// waiting on a group's daily limit or a chosen time (add_to_group).

import { useEffect, useState } from "react";
import { PublishQueueData, PublishRow, QueueData, getJSON, postJSON } from "../api";
import * as bus from "../bus";
import { ScheduleForm, formatWhen } from "../SchedulePublish";

type ActionResult = { ok: boolean; message?: string; added?: number; deleted?: number };

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
  useEffect(() => bus.on("publishQueueChanged", () => { load(); }), []);

  // Returns true on success so callers (e.g. an inline reschedule form) can close.
  const action = async (body: object): Promise<boolean> => {
    setBusy(true);
    setMsg("");
    try {
      const r = await postJSON<ActionResult>("/api/queue", body);
      if (r.message) setMsg(r.message);
      else if ("deleted" in r) setMsg(r.deleted ? "Item removed." : "Item not found.");
      else setMsg(`Done: ${r.added ?? 0} added.`);
      await load();
      return true;
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (error) return <div className="panel"><p className="error">{error}</p></div>;
  if (!data) return <div className="panel">Loading…</div>;

  return (
    <div className="panel queue-panel">
      {msg && <p className="queue-msg">{msg}</p>}
      {data.publishes && <PublishSection pub={data.publishes} busy={busy} action={action} />}
      <GroupSection data={data} busy={busy} action={action} />
    </div>
  );
}

function Thumb({ row }: { row: PublishRow }) {
  return row.thumb_url ? (
    <img className="pub-thumb" src={row.thumb_url} alt="" loading="lazy" />
  ) : (
    <div className="pub-thumb pub-thumb-empty" />
  );
}

function PhotoTitle({ row }: { row: PublishRow }) {
  return (
    <button type="button" className="link-btn pub-title" title="Open in Photo Viewer"
      onClick={() => bus.emitViewPhoto(row.photo_id)}>
      {row.photo_title}
    </button>
  );
}

function PublishSection({ pub, busy, action }: {
  pub: PublishQueueData;
  busy: boolean;
  action: (body: object) => Promise<boolean>;
}) {
  const { counts, waiting, errors, successes } = pub;
  const [editing, setEditing] = useState<number | null>(null);

  const publishNow = (row: PublishRow) => {
    if (window.confirm(`Make "${row.photo_title}" public now? Its date posted will be reset to now.`)) {
      action({ action: "publish_now", item_id: row.id });
    }
  };
  const publishAll = () => {
    if (window.confirm(`Make all ${waiting.length} waiting photos public now? Their dates posted will be reset to now.`)) {
      action({ action: "publish_now" });
    }
  };
  const remove = (row: PublishRow, verb: string) => {
    if (window.confirm(`${verb} "${row.photo_title}"? It stays private.`)) {
      action({ action: "publish_remove", item_id: row.id });
    }
  };
  const reschedule = async (row: PublishRow, iso: string) => {
    if (await action({ action: "publish_reschedule", item_id: row.id, publish_at: iso })) setEditing(null);
  };

  return (
    <section className="queue-section">
      <div className="queue-section-header">
        <h2>Scheduled publishes</h2>
        {waiting.length > 1 && (
          <div className="queue-actions">
            <button className="btn-sm" disabled={busy} onClick={publishAll}>Publish all now</button>
          </div>
        )}
      </div>
      <p className="hint">
        Private photos waiting to go public. At the scheduled time the server resets each photo's
        date posted to that moment and makes it public, so it shows up as new in your photostream
        and your contacts' feeds. Schedule a photo from its <em>Publishing</em> section in the Photo
        Viewer, or ask in Chat.
      </p>

      <div className="queue-stats">
        <span className="queue-stat-pill waiting">{counts.waiting} waiting</span>
        <span className="queue-stat-pill success">{counts.success} published</span>
        {counts.error > 0 && <span className="queue-stat-pill errored">{counts.error} failed</span>}
      </div>

      {waiting.length === 0 ? (
        <p className="hint">Nothing scheduled.</p>
      ) : (
        <ul className="pub-list">
          {waiting.map((r) => (
            <li key={r.id} className="pub-item">
              <Thumb row={r} />
              <div className="pub-body">
                <PhotoTitle row={r} />
                <div className="pub-when">Goes public {formatWhen(r.when_ts)}</div>
                {editing === r.id ? (
                  <ScheduleForm initialTs={r.when_ts} submitLabel="Save" busy={busy}
                    onSubmit={(iso) => reschedule(r, iso)} onCancel={() => setEditing(null)} />
                ) : (
                  <div className="pub-actions">
                    <button className="btn-sm" disabled={busy} onClick={() => setEditing(r.id)}>Change time</button>
                    <button className="btn-sm" disabled={busy} onClick={() => publishNow(r)}>Publish now</button>
                    <button className="btn-danger-sm" disabled={busy} onClick={() => remove(r, "Unschedule")}>Unschedule</button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {errors.length > 0 && (
        <>
          <h3>Couldn't publish</h3>
          <ul className="pub-list">
            {errors.map((r) => (
              <li key={r.id} className="pub-item">
                <Thumb row={r} />
                <div className="pub-body">
                  <PhotoTitle row={r} />
                  <div className="error">Flickr said: {r.message}</div>
                  <div className="pub-when">Tried {formatWhen(r.when_ts)}</div>
                  {editing === r.id ? (
                    <ScheduleForm submitLabel="Schedule again" busy={busy}
                      onSubmit={(iso) => reschedule(r, iso)} onCancel={() => setEditing(null)} />
                  ) : (
                    <div className="pub-actions">
                      <button className="btn-sm" disabled={busy} onClick={() => publishNow(r)}>Try again now</button>
                      <button className="btn-sm" disabled={busy} onClick={() => setEditing(r.id)}>Schedule again</button>
                      <button className="btn-danger-sm" disabled={busy} onClick={() => remove(r, "Dismiss")}>Dismiss</button>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {successes.length > 0 && (
        <details className="queue-history">
          <summary>Recently published ({successes.length})</summary>
          <ul className="pub-list pub-list-compact">
            {successes.map((r) => (
              <li key={r.id} className="pub-item">
                <Thumb row={r} />
                <div className="pub-body">
                  <PhotoTitle row={r} />
                  <div className="pub-when">Published {formatWhen(r.when_ts)}</div>
                  {r.message && <div className="hint">{r.message}</div>}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function GroupSection({ data, busy, action }: {
  data: QueueData;
  busy: boolean;
  action: (body: object) => Promise<boolean>;
}) {
  const { counts, waiting, errors, successes } = data;
  return (
    <section className="queue-section">
      <div className="queue-section-header">
        <h2>Group adds</h2>
        {waiting.length > 0 && (
          <div className="queue-actions">
            <button className="btn-sm" disabled={busy} onClick={() => action({ action: "retry_ready" })}
              title="Post the ones whose scheduled time has passed">Post due now</button>
            <button className="btn-sm" disabled={busy} onClick={() => action({ action: "retry_all" })}
              title="Post every waiting item, ignoring schedules">Post all now</button>
          </div>
        )}
      </div>
      <p className="hint">
        Photos waiting to be added to a group pool — either because the group's daily posting
        limit was reached (retried automatically) or because they were scheduled for later.
      </p>

      <div className="queue-stats">
        <span className="queue-stat-pill waiting">{counts.waiting} waiting</span>
        <span className="queue-stat-pill success">{counts.success} added</span>
        {counts.error > 0 && <span className="queue-stat-pill errored">{counts.error} failed</span>}
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
          <h3>Couldn't add</h3>
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
        <details className="queue-history">
          <summary>Recently added ({successes.length})</summary>
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
        </details>
      )}
    </section>
  );
}
