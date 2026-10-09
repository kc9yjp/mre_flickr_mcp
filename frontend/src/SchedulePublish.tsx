// Scheduled-publish UI shared by the Photo Viewer (schedule this photo) and
// the Queue panel (change a waiting photo's time). Times are picked and shown
// in the browser's own time zone; the server gets an absolute UTC instant, so
// there's no ambiguity about whose "8 AM" it is.

import { useState } from "react";
import { ScheduledPublish, postJSON } from "./api";
import * as bus from "./bus";
import { relativeTime } from "./format";

const whenFormat = new Intl.DateTimeFormat(undefined, {
  weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
});

// "Fri, Oct 10, 8:00 AM · in 20 h"
export function formatWhen(ts: number | null): string {
  if (!ts) return "—";
  return `${whenFormat.format(new Date(ts * 1000))} · ${relativeTime(ts)}`;
}

function toInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function at(daysAhead: number, hour: number): Date {
  const d = new Date();
  d.setDate(d.getDate() + daysAhead);
  d.setHours(hour, 0, 0, 0);
  return d;
}

function quickPicks(): { label: string; date: Date }[] {
  const picks = [];
  if (new Date().getHours() < 17) picks.push({ label: "This evening, 6 PM", date: at(0, 18) });
  picks.push(
    { label: "Tomorrow, 8 AM", date: at(1, 8) },
    { label: "Tomorrow, 6 PM", date: at(1, 18) },
    { label: "In a week, 8 AM", date: at(7, 8) },
  );
  return picks;
}

// Pick a date/time and submit it. Quick picks only fill the field — nothing
// is scheduled until the submit button is pressed.
export function ScheduleForm({ initialTs, submitLabel, busy, onSubmit, onCancel }: {
  initialTs?: number | null;
  submitLabel: string;
  busy: boolean;
  onSubmit: (isoUtc: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(() =>
    toInputValue(initialTs ? new Date(initialTs * 1000) : at(1, 8)),
  );
  const chosen = value ? new Date(value) : null;
  const valid = !!chosen && !Number.isNaN(chosen.getTime()) && chosen.getTime() > Date.now();

  return (
    <div className="schedule-form">
      <div className="schedule-quick">
        {quickPicks().map((p) => (
          <button key={p.label} type="button" className="btn-sm" onClick={() => setValue(toInputValue(p.date))}>
            {p.label}
          </button>
        ))}
      </div>
      <div className="schedule-row">
        <input
          type="datetime-local"
          value={value}
          min={toInputValue(new Date())}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Publish date and time"
        />
        <button
          type="button"
          className="btn-filled"
          disabled={busy || !valid}
          onClick={() => chosen && onSubmit(chosen.toISOString())}
        >
          {submitLabel}
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      <p className="hint schedule-note">
        {valid && chosen
          ? `Goes public ${formatWhen(Math.floor(chosen.getTime() / 1000))}. Its date posted is reset to that time, so it shows up as new.`
          : "Pick a time in the future."}
      </p>
    </div>
  );
}

// The Photo Viewer's "Publishing" block for one of your own photos.
export function PhotoPublishSchedule({ photoId, isPublic, scheduled, onChange }: {
  photoId: string;
  isPublic: boolean;
  scheduled: ScheduledPublish | null;
  onChange: (s: ScheduledPublish | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  if (isPublic && !scheduled) return null;

  const run = async (body: object, done: string, after: (r: { scheduled?: ScheduledPublish | null }) => void) => {
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const r = await postJSON<{ scheduled?: ScheduledPublish | null }>("/api/queue", body);
      after(r);
      bus.emit("publishQueueChanged", undefined);
      setMsg(done);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const schedule = (iso: string) =>
    run({ action: "publish_schedule", photo_id: photoId, publish_at: iso }, "", (r) => onChange(r.scheduled ?? null));
  const cancel = () =>
    run({ action: "publish_cancel_photo", photo_id: photoId }, "Schedule cancelled — the photo stays private.", () => onChange(null));

  return (
    <div className="detail-meta-block publish-schedule">
      <div className="detail-meta-label">Publishing</div>
      <div className="detail-meta-value">
        {scheduled && !editing && (
          <div className="schedule-row">
            <span>Scheduled to go public <strong>{formatWhen(scheduled.when_ts)}</strong></span>
            <button type="button" className="btn-sm" disabled={busy} onClick={() => setEditing(true)}>Change</button>
            <button type="button" className="btn-danger-sm" disabled={busy} onClick={cancel}>Cancel schedule</button>
          </div>
        )}
        {!scheduled && !editing && (
          <div className="schedule-row">
            <span className="hint">Private — not scheduled.</span>
            <button type="button" className="btn-primary btn-sm" onClick={() => setEditing(true)}>
              Schedule publish…
            </button>
          </div>
        )}
        {editing && (
          <ScheduleForm
            initialTs={scheduled?.when_ts}
            submitLabel={scheduled ? "Reschedule" : "Schedule"}
            busy={busy}
            onSubmit={schedule}
            onCancel={() => setEditing(false)}
          />
        )}
        {msg && <p className="hint">{msg}</p>}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
