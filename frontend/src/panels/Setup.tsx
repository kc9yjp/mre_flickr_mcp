// Setup panel: per-client MCP config snippets, the bookmarklet, browser
// extension download, and API key regeneration.

import { useEffect, useState } from "react";
import { SetupData, getJSON, postJSON } from "../api";

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

export function Setup() {
  const [data, setData] = useState<SetupData | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<string>("claude_code");
  const [regenMsg, setRegenMsg] = useState("");
  const [regenErr, setRegenErr] = useState("");
  const [regenLoading, setRegenLoading] = useState(false);

  const load = () =>
    getJSON<SetupData>("/api/setup")
      .then(setData)
      .catch((e) => setError(e.message));

  useEffect(() => { load(); }, []);

  const regenKey = async () => {
    setRegenMsg("");
    setRegenErr("");
    setRegenLoading(true);
    try {
      await postJSON("/api/regen-key", {});
      setRegenMsg("API key regenerated. Update your MCP config snippets below.");
      load();
    } catch (e) {
      setRegenErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRegenLoading(false);
    }
  };

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

      <h3 style={{ marginTop: 20 }}>Browser Extension</h3>
      <p className="hint">
        Chrome/Edge extension — one-click to open the current Flickr photo in the Workbench.
        Download, unzip, then load as an unpacked extension in your browser's extension settings.
      </p>
      <a className="bookmarklet-link" href="/api/extension" download>
        ⬇ Download Extension
      </a>

      <h3 style={{ marginTop: 20 }}>API Key</h3>
      <p className="hint">Regenerating creates a new key — update your MCP config after.</p>
      {regenMsg && <p className="hint" style={{ color: "var(--accent)" }}>{regenMsg}</p>}
      {regenErr && <p className="error">{regenErr}</p>}
      <button onClick={regenKey} disabled={regenLoading}>
        {regenLoading ? "Regenerating…" : "Regenerate API Key"}
      </button>
    </div>
  );
}
