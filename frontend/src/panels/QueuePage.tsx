// Shared page for the Workbench's two deferred-work queues — scheduled
// publishes and group-pool adds. Both come from GET /api/queue/{kind} in the
// same QueueItem shape and take the same per-item actions via POST
// /api/queue (prefixed publish_* / group_*), so one component renders both;
// PublishQueue.tsx and GroupQueue.tsx just supply the wording.

import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { QueueItem, QueueSnapshot, getJSON, postJSON } from "../api";
import * as bus from "../bus";
import { relativeTime } from "../format";
import { ScheduleForm, formatWhen } from "../SchedulePublish";

const REFRESH_MS = 30_000;

export interface QueueConfig {
  kind: "publish" | "groups";
  title: string;
  intro: ReactNode;
  emptyText: string;
  doneLabel: string;        // pill: "12 published" / "12 added"
  waitingVerb: string;      // "Goes public" / "Posts"
  doneVerb: string;         // "Published" / "Added"
  failedHeading: string;
  recentHeading: string;
  nowLabel: string;         // per-item button
  nowAllLabel: string;
  removeLabel: string;
  nowConfirm: (item: QueueItem) => string;
  nowAllConfirm: (n: number) => string;
  removeConfirm: (item: QueueItem) => string;
  actionNames: { now: string; reschedule: string; remove: string; due?: string };
  dueLabel?: string;        // optional "post what's due" header button
  subtitle?: (item: QueueItem) => ReactNode;
  refreshOn?: "publishQueueChanged"; // bus event that should trigger an immediate reload
}

type ActionResult = { ok: boolean; message?: string };

export function QueuePage({ config }: { config: QueueConfig }) {
  const [data, setData] = useState<QueueSnapshot | null>(null);
  const [loadError, setLoadError] = useState("");
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadedAt, setLoadedAt] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [, setTick] = useState(0);
  const busyRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getJSON<QueueSnapshot>(`/api/queue/${config.kind}`));
      setLoadError("");
      setLoadedAt(Date.now() / 1000);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [config.kind]);

  // Initial load, then every REFRESH_MS while the tab is visible and no action
  // is in flight; also reload as soon as the tab becomes visible again.
  useEffect(() => {
    load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !busyRef.current) load();
    }, REFRESH_MS);
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  useEffect(() => (config.refreshOn ? bus.on(config.refreshOn, () => { load(); }) : undefined), [config.refreshOn, load]);

  // Keep "updated … ago" and the per-item relative times current between loads.
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 15_000);
    return () => window.clearInterval(t);
  }, []);

  const action = async (body: object): Promise<boolean> => {
    setBusy(true);
    busyRef.current = true;
    setMsg(null);
    try {
      const r = await postJSON<ActionResult>("/api/queue", body);
      setMsg({ text: r.message ?? "Done.", error: false });
      await load();
      return true;
    } catch (e) {
      setMsg({ text: e instanceof Error ? e.message : String(e), error: true });
      return false;
    } finally {
      setBusy(false);
      busyRef.current = false;
    }
  };

  const { actionNames: names } = config;
  const confirmThen = (text: string, body: object) => { if (window.confirm(text)) action(body); };
  const reschedule = async (item: QueueItem, iso: string) => {
    if (await action({ action: names.reschedule, item_id: item.id, publish_at: iso })) setEditing(null);
  };

  const header = (
    <div className="queue-page-header">
      <h2>{config.title}</h2>
      <div className="queue-refresh">
        <span className="queue-time" title={`Refreshes automatically every ${REFRESH_MS / 1000}s`}>
          {loading ? "Refreshing…" : loadedAt ? `Updated ${relativeTime(loadedAt)}` : ""}
        </span>
        <button className="btn-sm" disabled={loading} onClick={() => load()} title="Refresh now">↻ Refresh</button>
      </div>
    </div>
  );

  if (!data) {
    return (
      <div className="panel queue-panel">
        {header}
        {loadError ? <p className="error">{loadError}</p> : <p className="hint">Loading…</p>}
      </div>
    );
  }

  const { counts, waiting, errors, successes } = data;

  return (
    <div className="panel queue-panel">
      {header}
      <p className="hint">{config.intro}</p>
      {loadError && <p className="error">Couldn't refresh: {loadError}</p>}
      {msg && <p className={msg.error ? "queue-msg error" : "queue-msg"}>{msg.text}</p>}

      <div className="queue-section-header">
        <div className="queue-stats">
          <span className="queue-stat-pill waiting">{counts.waiting} waiting</span>
          <span className="queue-stat-pill success">{counts.success} {config.doneLabel}</span>
          {counts.error > 0 && <span className="queue-stat-pill errored">{counts.error} failed</span>}
        </div>
        {waiting.length > 0 && (
          <div className="queue-actions">
            {config.dueLabel && names.due && (
              <button className="btn-sm" disabled={busy} onClick={() => action({ action: names.due })}
                title="Only the items whose scheduled time has already passed">{config.dueLabel}</button>
            )}
            {waiting.length > 1 && (
              <button className="btn-sm" disabled={busy}
                onClick={() => confirmThen(config.nowAllConfirm(waiting.length), { action: names.now })}>
                {config.nowAllLabel}
              </button>
            )}
          </div>
        )}
      </div>

      {waiting.length === 0 ? (
        <p className="hint">{config.emptyText}</p>
      ) : (
        <ul className="pub-list">
          {waiting.map((item) => (
            <ItemCard key={item.id} item={item} config={config}>
              <div className="pub-when">{config.waitingVerb} {formatWhen(item.when_ts)}</div>
              {editing === item.id ? (
                <ScheduleForm initialTs={item.when_ts} submitLabel="Save" busy={busy}
                  onSubmit={(iso) => reschedule(item, iso)} onCancel={() => setEditing(null)} />
              ) : (
                <div className="pub-actions">
                  <button className="btn-sm" disabled={busy} onClick={() => setEditing(item.id)}>Change time</button>
                  <button className="btn-sm" disabled={busy}
                    onClick={() => confirmThen(config.nowConfirm(item), { action: names.now, item_id: item.id })}>
                    {config.nowLabel}
                  </button>
                  <button className="btn-danger-sm" disabled={busy}
                    onClick={() => confirmThen(config.removeConfirm(item), { action: names.remove, item_id: item.id })}>
                    {config.removeLabel}
                  </button>
                </div>
              )}
            </ItemCard>
          ))}
        </ul>
      )}

      {errors.length > 0 && (
        <>
          <h3>{config.failedHeading}</h3>
          <ul className="pub-list">
            {errors.map((item) => (
              <ItemCard key={item.id} item={item} config={config}>
                <div className="error">Flickr said: {item.message || "unknown error"}</div>
                <div className="pub-when">Tried {formatWhen(item.when_ts)}</div>
                {editing === item.id ? (
                  <ScheduleForm submitLabel="Schedule again" busy={busy}
                    onSubmit={(iso) => reschedule(item, iso)} onCancel={() => setEditing(null)} />
                ) : (
                  <div className="pub-actions">
                    <button className="btn-sm" disabled={busy}
                      onClick={() => action({ action: names.now, item_id: item.id })}>Try again now</button>
                    <button className="btn-sm" disabled={busy} onClick={() => setEditing(item.id)}>Schedule again</button>
                    <button className="btn-danger-sm" disabled={busy}
                      onClick={() => action({ action: names.remove, item_id: item.id })}>Dismiss</button>
                  </div>
                )}
              </ItemCard>
            ))}
          </ul>
        </>
      )}

      {successes.length > 0 && (
        <details className="queue-history">
          <summary>{config.recentHeading} ({successes.length})</summary>
          <ul className="pub-list pub-list-compact">
            {successes.map((item) => (
              <ItemCard key={item.id} item={item} config={config}>
                <div className="pub-when">{config.doneVerb} {formatWhen(item.when_ts)}</div>
                {item.message && <div className="hint">{item.message}</div>}
              </ItemCard>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ItemCard({ item, config, children }: { item: QueueItem; config: QueueConfig; children: ReactNode }) {
  return (
    <li className="pub-item">
      <button type="button" className="pub-thumb-btn" title="Open in Photo Viewer"
        onClick={() => bus.emitViewPhoto(item.photo_id)}>
        {item.thumb_url
          ? <img className="pub-thumb" src={item.thumb_url} alt="" loading="lazy" />
          : <div className="pub-thumb pub-thumb-empty" />}
      </button>
      <div className="pub-body">
        <button type="button" className="link-btn pub-title" title="Open in Photo Viewer"
          onClick={() => bus.emitViewPhoto(item.photo_id)}>
          {item.photo_title}
        </button>
        {config.subtitle?.(item)}
        {children}
      </div>
    </li>
  );
}
