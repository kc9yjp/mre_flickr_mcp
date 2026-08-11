// Summary panel: collection stat tiles (photos, views, groups, albums,
// contacts) and the top-20 tag list. Sync status/history has its own
// dedicated Sync panel — not duplicated here.

import { useEffect, useRef, useState } from "react";
import { getJSON, Stats, SyncStatus } from "../api";
import { compactNumber } from "../format";

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
    </div>
  );
}

export function Summary() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const wasRunningRef = useRef(false);

  useEffect(() => {
    let alive = true;

    const loadStats = () =>
      getJSON<Stats>("/api/stats")
        .then((s) => alive && setStats(s))
        .catch((e) => alive && setError(e.message));

    // Poll sync status purely to catch a running→idle transition and
    // refresh the stat tiles once a sync (triggered from the Sync panel,
    // Command panel, etc.) finishes elsewhere — no table rendered here,
    // see the Sync panel for that. 5s while a sync runs, 30s when idle.
    const poll = async () => {
      let running = wasRunningRef.current;
      try {
        const status = await getJSON<SyncStatus>("/api/sync/status");
        if (!alive) return;
        if (wasRunningRef.current && !status.running) loadStats(); // sync just finished
        wasRunningRef.current = status.running;
        running = status.running;
      } catch {
        // transient poll failure — keep last known state
      }
      if (alive) timer.current = window.setTimeout(poll, running ? 5000 : 30000);
    };

    loadStats();
    poll();
    return () => {
      alive = false;
      window.clearTimeout(timer.current);
    };
  }, []);

  if (error) return <div className="panel">{`Could not load stats: ${error}`}</div>;
  if (!stats) return <div className="panel">Loading…</div>;

  return (
    <div className="panel summary">
      <div className="tile-grid">
        <Tile label="Photos" value={compactNumber(stats.total_photos)} />
        <Tile label="Views" value={compactNumber(stats.total_views)} />
        <Tile label="Public" value={compactNumber(stats.public_photos)} />
        <Tile label="Private" value={compactNumber(stats.private_photos)} />
        <Tile label="Albums" value={compactNumber(stats.total_albums)} />
        <Tile label="Groups" value={compactNumber(stats.total_groups)} />
        <Tile label="Contacts" value={compactNumber(stats.total_contacts)} />
      </div>
      <p className="date-range">
        {stats.date_range.earliest?.slice(0, 10) ?? "?"} →{" "}
        {stats.date_range.latest?.slice(0, 10) ?? "?"}
      </p>
      <h3>Top tags</h3>
      <div className="chip-row">
        {stats.top_tags.slice(0, 12).map((t) => (
          <span key={t.tag} className="chip">
            {t.tag} <span className="chip-count">{t.count}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
