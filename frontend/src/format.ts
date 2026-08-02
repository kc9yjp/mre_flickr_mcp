// Shared number/time formatting helpers (compact counts, relative timestamps)
// used across panel displays.

const compact = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export function compactNumber(n: number): string {
  return n < 10_000 ? n.toLocaleString("en") : compact.format(n);
}

export function syncStatusLabel(running: boolean, phase: "flickr" | "model" | null): string {
  if (!running) return "idle";
  return phase === "model" ? "⟳ generating summaries" : "⟳ retrieving from Flickr";
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "";
  if (seconds < 60) return "<1 min";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m} min`;
}

// Distinct from formatDuration() above: latencies range from tens of
// milliseconds to several minutes on slow local connections, so this needs
// finer-grained scaling (ms/s/m) than that one's coarse "<1 min"/"Xh Ym".
export function formatLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

export function relativeTime(unixSeconds: number | null): string {
  if (!unixSeconds) return "never";
  const delta = Date.now() / 1000 - unixSeconds;
  if (delta < 0) {
    const ahead = -delta;
    if (ahead < 3600) return `in ${Math.round(ahead / 60)} min`;
    if (ahead < 86400) return `in ${Math.round(ahead / 3600)} h`;
    return `in ${Math.round(ahead / 86400)} d`;
  }
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`;
  return `${Math.round(delta / 86400)} d ago`;
}
