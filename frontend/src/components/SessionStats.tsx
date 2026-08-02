// Session Stats dockview panel: turns/tokens/latency for the active chat
// conversation, polled frequently for a live-ish readout. The "Compact now"
// trigger lives in Chat.tsx's input bar instead (next to its context-used
// indicator), since compaction and its summary show up as part of the chat
// transcript.

import { useEffect, useRef, useState } from "react";
import { SessionStats, getSessionStats } from "../api";
import * as bus from "../bus";

const POLL_MS = 3000;

// Local connections can be slow (self-hosted models), so latencies commonly
// run into the tens of seconds or minutes — a raw millisecond count is hard
// to read at a glance, so scale to whichever unit keeps the number small.
function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

function formatCount(n: number): string {
  return n.toLocaleString();
}

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
  const contextWindow = stats.context_window || 128_000;
  const contextUsedPercent = Math.round((stats.last_prompt_tokens / contextWindow) * 100);

  return (
    <div className="panel session-stats">
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Turns</span>
          <span className="stat-value">{formatCount(stats.turns)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Tokens</span>
          <span className="stat-value">{formatCount(stats.total_tokens)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Prompt</span>
          <span className="stat-value">{formatCount(stats.prompt_tokens)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Completion</span>
          <span className="stat-value">{formatCount(stats.completion_tokens)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Avg Latency</span>
          <span className="stat-value">{formatDuration(avgLatencyMs)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Avg Tokens/Turn</span>
          <span className="stat-value">{formatCount(avgTokensPerTurn)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Context Used</span>
          <span className="stat-value">{contextUsedPercent}%</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Total Latency</span>
          <span className="stat-value">{formatDuration(stats.total_latency_ms)}</span>
        </div>
      </div>
    </div>
  );
}
