// Small stats readout (turns/tokens/latency) for the active chat conversation,
// plus a manual "Compact now" control that summarizes the conversation via
// the LLM and replaces its stored history with that summary in place.

import { useEffect, useState } from "react";
import { SessionStats, compactConversation, getSessionStats } from "../api";

interface SessionStatsProps {
  conversationId: string | null;
  /** Called after a successful manual compaction so the caller can refresh
   * the visible message list (the compacted summary replaces it) and this
   * panel's own stats. */
  onCompacted?: () => void;
}

export function SessionStatsPanel({ conversationId, onCompacted }: SessionStatsProps) {
  const [stats, setStats] = useState<SessionStats | null>(null);
  const [compacting, setCompacting] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => {
    if (!conversationId) {
      setStats(null);
      return;
    }
    getSessionStats(conversationId)
      .then(setStats)
      .catch(() => setStats(null));
  };

  useEffect(refresh, [conversationId]);

  const compact = async () => {
    if (!conversationId || compacting) return;
    setCompacting(true);
    setError("");
    try {
      await compactConversation(conversationId);
      refresh();
      onCompacted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCompacting(false);
    }
  };

  if (!stats || stats.turns === 0) {
    return null;
  }

  const avgLatencyMs = Math.round(stats.total_latency_ms / stats.turns);
  const avgTokensPerTurn = Math.round(stats.total_tokens / stats.turns);
  const contextWindow = stats.context_window || 128_000;
  const contextUsedPercent = Math.round((stats.last_prompt_tokens / contextWindow) * 100);

  return (
    <div className="session-stats">
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
        <div className="stat-item">
          <span className="stat-label">Context Used</span>
          <span className="stat-value">{contextUsedPercent}%</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Total Latency</span>
          <span className="stat-value">{(stats.total_latency_ms / 1000).toFixed(1)}s</span>
        </div>
      </div>
      <div className="session-stats-actions">
        <button type="button" className="btn-sm" onClick={compact} disabled={compacting}>
          {compacting ? "Compacting…" : "Compact now"}
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}
