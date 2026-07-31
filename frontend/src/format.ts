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
