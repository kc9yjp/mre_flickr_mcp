import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Conversation,
  LLMSettings,
  WireMessage,
  getJSON,
  postJSON,
  streamChat,
} from "../api";
import * as bus from "../bus";

interface ToolCard {
  id: string;
  name: string;
  arguments: string;
  result?: string;
}

interface ChatMsg {
  role: "user" | "assistant";
  text: string;
  tools: ToolCard[];
}

interface PendingConfirm {
  confirm_id: string;
  name: string;
  arguments: string;
  photo: { id: string; title: string; thumb_url: string | null } | null;
}

function wireToRender(messages: WireMessage[]): ChatMsg[] {
  const out: ChatMsg[] = [];
  const cardsById = new Map<string, ToolCard>();
  for (const m of messages) {
    if (m.role === "user") {
      out.push({ role: "user", text: m.content ?? "", tools: [] });
    } else if (m.role === "assistant") {
      const tools = (m.tool_calls ?? []).map((c) => {
        const card: ToolCard = { id: c.id, name: c.function.name, arguments: c.function.arguments };
        cardsById.set(c.id, card);
        return card;
      });
      out.push({ role: "assistant", text: m.content ?? "", tools });
    } else if (m.role === "tool" && m.tool_call_id) {
      const card = cardsById.get(m.tool_call_id);
      if (card) card.result = m.content ?? "";
    }
  }
  return out;
}

function prettyArgs(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function ToolCardView({ card }: { card: ToolCard }) {
  return (
    <details className="tool-card">
      <summary>
        🔧 {card.name} {card.result === undefined && <em>running…</em>}
      </summary>
      <pre>{prettyArgs(card.arguments)}</pre>
      {card.result !== undefined && <pre className="tool-result">{card.result}</pre>}
    </details>
  );
}

function SettingsForm({ onClose }: { onClose: () => void }) {
  const [cfg, setCfg] = useState<LLMSettings | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    getJSON<LLMSettings>("/api/llm-settings").then(setCfg).catch((e) => setStatus(e.message));
  }, []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!cfg) return;
    try {
      setCfg(await postJSON<LLMSettings>("/api/llm-settings", cfg));
      setStatus("Saved.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  if (!cfg) return <div className="chat-settings">{status || "Loading…"}</div>;
  return (
    <form className="chat-settings" onSubmit={save}>
      <label>
        API base URL
        <input
          value={cfg.base_url}
          onChange={(e) => setCfg({ ...cfg, base_url: e.target.value })}
          placeholder="http://host.docker.internal:11434/v1"
        />
      </label>
      <label>
        Model
        <input
          value={cfg.model}
          onChange={(e) => setCfg({ ...cfg, model: e.target.value })}
          placeholder="e.g. qwen3, llama3.1, gpt-4o"
        />
      </label>
      <label>
        API key (blank for Ollama)
        <input
          value={cfg.api_key}
          onChange={(e) => setCfg({ ...cfg, api_key: e.target.value })}
        />
      </label>
      <label>
        Max tokens
        <input
          type="number"
          value={cfg.max_tokens}
          onChange={(e) => setCfg({ ...cfg, max_tokens: Number(e.target.value) })}
        />
      </label>
      <div className="chat-settings-actions">
        <button type="submit">Save</button>
        <button type="button" onClick={onClose}>
          Close
        </button>
        {status && <span className="hint">{status}</span>}
      </div>
    </form>
  );
}

export function Chat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
  const [error, setError] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;
  const focusedPhotoRef = useRef<string | null>(null);

  const refreshConversations = useCallback(() => {
    getJSON<{ conversations: Conversation[] }>("/api/chat/conversations")
      .then((r) => setConversations(r.conversations))
      .catch(() => {});
  }, []);

  useEffect(refreshConversations, [refreshConversations]);

  // Track whichever photo is open in the Photo Browser so free-form messages
  // (no workflow button, no explicit id) can default to it — see loop.py's
  // focused_photo_id handling for why this rides in per-request, not stored.
  useEffect(() => bus.on("photoOpened", (id) => { focusedPhotoRef.current = id; }), []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [msgs, confirm]);

  const send = useCallback(async (message: string) => {
    const text = message.trim();
    if (!text) return;
    setError("");
    setInput("");
    setStreaming(true);
    setMsgs((prev) => [
      ...prev,
      { role: "user", text, tools: [] },
      { role: "assistant", text: "", tools: [] },
    ]);

    const patchLast = (fn: (m: ChatMsg) => ChatMsg) =>
      setMsgs((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

    try {
      await streamChat(
        {
          conversation_id: activeIdRef.current ?? undefined,
          message: text,
          focused_photo_id: focusedPhotoRef.current,
        },
        (event) => {
          switch (event.type) {
            case "start":
              setActiveId(event.conversation_id);
              break;
            case "delta":
              patchLast((m) => ({ ...m, text: m.text + event.text }));
              break;
            case "tool_call":
              patchLast((m) => ({
                ...m,
                tools: [...m.tools, { id: event.id, name: event.name, arguments: event.arguments }],
              }));
              break;
            case "confirm_request":
              setConfirm(event);
              break;
            case "tool_result":
              setConfirm(null);
              patchLast((m) => ({
                ...m,
                tools: m.tools.map((t) => (t.id === event.id ? { ...t, result: event.text } : t)),
              }));
              break;
            case "focus":
              bus.emit("focusPhoto", event.photo_id);
              break;
            case "error":
              setError(event.message);
              break;
            case "done":
              break;
          }
        },
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStreaming(false);
      setConfirm(null);
      refreshConversations();
    }
  }, [refreshConversations]);

  useEffect(() => bus.on("runCommand", send), [send]);

  const answerConfirm = async (approve: boolean) => {
    if (!confirm) return;
    setConfirm(null);
    try {
      await postJSON("/api/chat/confirm", { confirm_id: confirm.confirm_id, approve });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const openConversation = async (id: string) => {
    setActiveId(id);
    setError("");
    try {
      const detail = await getJSON<{ messages: WireMessage[] }>(`/api/chat/conversations/${id}`);
      setMsgs(wireToRender(detail.messages));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const newConversation = () => {
    setActiveId(null);
    setMsgs([]);
    setError("");
  };

  return (
    <div className="panel chat">
      <div className="chat-header">
        <select
          value={activeId ?? ""}
          onChange={(e) => (e.target.value ? openConversation(e.target.value) : newConversation())}
        >
          <option value="">＋ New conversation</option>
          {conversations.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button onClick={() => setShowSettings((s) => !s)} title="LLM settings">
          ⚙
        </button>
      </div>
      {showSettings && <SettingsForm onClose={() => setShowSettings(false)} />}
      <div className="chat-messages" ref={scrollRef}>
        {msgs.length === 0 && !showSettings && (
          <p className="hint">
            Ask about your photos, or use a workflow button from the Commands panel or a
            photo's detail view. Configure your LLM endpoint via ⚙ first.
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role}`}>
            {m.tools.map((t) => (
              <ToolCardView key={t.id} card={t} />
            ))}
            {m.text && <div className="chat-bubble">{m.text}</div>}
          </div>
        ))}
        {confirm && (
          <div className="confirm-card">
            <p>
              Run <strong>{confirm.name}</strong>?
            </p>
            {confirm.photo && (
              <div className="confirm-photo">
                {confirm.photo.thumb_url && <img src={confirm.photo.thumb_url} alt="" />}
                <span>
                  {confirm.photo.title || "(untitled)"} — photo {confirm.photo.id}
                </span>
              </div>
            )}
            <pre>{prettyArgs(confirm.arguments)}</pre>
            <div>
              <button className="approve" onClick={() => answerConfirm(true)}>
                Approve
              </button>
              <button onClick={() => answerConfirm(false)}>Deny</button>
            </div>
          </div>
        )}
        {streaming && !confirm && <p className="hint">…</p>}
        {error && <p className="error">{error}</p>}
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          if (!streaming) send(input);
        }}
      >
        <textarea
          value={input}
          rows={2}
          placeholder="Message the workbench agent…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!streaming) send(input);
            }
          }}
        />
        <button type="submit" disabled={streaming || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
