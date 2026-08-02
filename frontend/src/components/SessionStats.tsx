// Session Stats dockview panel: turns/tokens/latency for the active chat
// conversation, polled frequently for a live-ish readout. The "Compact now"
// trigger lives in Chat.tsx's input bar instead (next to its context-used
// indicator), since compaction and its summary show up as part of the chat
// transcript.

import { useEffect, useRef, useState } from "react";
import { SessionStats, contextUsage, getSessionStats } from "../api";
import * as bus from "../bus";

const POLL_MS = 3000;

export function SessionStatsPanel() {
  // Seed from the bus's cached value: dockview panels mount lazily on open,
  // so this panel may mount well after Chat already set the active
  // conversation and fired its one-shot "activeConversationChanged" event.
  const [conversationId, setConversationId] = useState<string | null>(() => bus.getActiveConversationId());
  const [stats, setStats] = useState<SessionStats | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  conversationIdRef.current = conversationId;

  useEffect(() => bus.on("activeConversationChanged", setConversationId), []);

  useEffect(() => {
    const refresh = () => {
      const id = conversationIdRef.current;
      if (!id) {
        setStats(null);
        return;
      }
      getSessionStats(id).then(setStats).catch(() => setStats(null));
    };
    refresh();
    if (!conversationId) return;
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [conversationId]);

  if (!conversationId) {
    return (
      <div className="panel session-stats">
        <p className="hint">No active conversation yet — start chatting to see stats here.</p>
      </div>
    );
  }

  if (!stats || stats.turns === 0) {
    return (
      <div className="panel session-stats">
        <p className="hint">No turns yet in this conversation.</p>
      </div>
    );
  }

  const avgLatencyMs = Math.round(stats.total_latency_ms / stats.turns);
  const avgTokensPerTurn = Math.round(stats.total_tokens / stats.turns);
  // Guaranteed non-null here: we already returned early when turns === 0.
  const context = contextUsage(stats)!;

  return (
    <div className="panel session-stats">
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Turns</span>
          <span className="stat-value">{stats.turns}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Tokens</span>
          <span className="stat-value">{stats.total_tokens}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Prompt</span>
          <span className="stat-value">{stats.prompt_tokens}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Completion</span>
          <span className="stat-value">{stats.completion_tokens}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Avg Latency</span>
          <span className="stat-value">{avgLatencyMs}ms</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Avg Tokens/Turn</span>
          <span className="stat-value">{avgTokensPerTurn}</span>
        </div>
        <div className={`stat-item${context.level !== "ok" ? ` context-${context.level}` : ""}`}>
          <span className="stat-label">
            Context Used{context.level === "critical" ? " · at risk" : ""}
          </span>
          <span className="stat-value">{context.percent}%</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Total Latency</span>
          <span className="stat-value">{(stats.total_latency_ms / 1000).toFixed(1)}s</span>
        </div>
      </div>
    </div>
  );
}
