import { useEffect, useState } from "react";
import { SetupData, getJSON } from "../api";

const CLIENT_TABS = [
  { id: "claude_code",     label: "Claude Code" },
  { id: "claude_desktop",  label: "Claude Desktop" },
  { id: "cursor",          label: "Cursor" },
  { id: "windsurf",        label: "Windsurf" },
  { id: "opencode",        label: "OpenCode" },
  { id: "stdio",           label: "Stdio" },
] as const;

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button className="copy-btn" onClick={copy}>
      {copied ? "Copied!" : "Copy"}
    </button>
  );
}

export function SetupPage() {
  const [data, setData] = useState<SetupData | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<string>("claude_code");

  useEffect(() => {
    getJSON<SetupData>("/api/setup")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="panel"><p className="error">{error}</p></div>;
  if (!data) return <div className="panel">Loading…</div>;

  const snippet = data.snippets[tab] ?? "";

  return (
    <div className="panel">
      <h2>Setup</h2>

      <p className="hint">
        MCP server at <code>{data.mcp_url}</code>.
        {data.has_api_key && " Snippets include your personal API key — keep them private."}
      </p>

      <div className="setup-tabs">
        {CLIENT_TABS.map((c) => (
          <button
            key={c.id}
            className={tab === c.id ? "setup-tab active" : "setup-tab"}
            onClick={() => setTab(c.id)}
          >
            {c.label}
          </button>
        ))}
      </div>

      <div className="setup-snippet">
        <pre>{snippet}</pre>
        <CopyButton text={snippet} />
      </div>

      {tab === "claude_code" && data.snippets.claude_code_sse && (
        <details className="hint" style={{ marginTop: 8 }}>
          <summary>Legacy SSE snippet (older Claude Code)</summary>
          <div className="setup-snippet" style={{ marginTop: 6 }}>
            <pre>{data.snippets.claude_code_sse}</pre>
            <CopyButton text={data.snippets.claude_code_sse} />
          </div>
        </details>
      )}

      <h3>Bookmarklet</h3>
      <p className="hint">Drag to your bookmarks bar. Opens any Flickr photo page in the Workbench.</p>
      <a className="bookmarklet-link" href={data.bookmarklet}>
        📷 Send to Workbench
      </a>
    </div>
  );
}
