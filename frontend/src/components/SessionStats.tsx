// Small stats readout (turns/tokens/latency) for the active chat conversation.

import { useEffect, useState } from "react";
import { SessionStats, getSessionStats } from "../api";

interface SessionStatsProps {
  conversationId: string | null;
}

export function SessionStatsPanel({ conversationId }: SessionStatsProps) {
  const [stats, setStats] = useState<SessionStats | null>(null);

  useEffect(() => {
    if (!conversationId) {
      setStats(null);
      return;
    }

    getSessionStats(conversationId)
      .then(setStats)
      .catch(() => setStats(null));
  }, [conversationId]);

  if (!stats || stats.turns === 0) {
    return null;
  }

  const avgLatencyMs = Math.round(stats.total_latency_ms / stats.turns);
  const avgTokensPerTurn = Math.round(stats.total_tokens / stats.turns);
  const contextUsedPercent = stats.total_tokens > 0 ? 
    Math.round((stats.total_tokens / (stats.total_tokens + 2000)) * 100) : 
    0;

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
    </div>
  );
}
