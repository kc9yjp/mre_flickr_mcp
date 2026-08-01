// Chat panel: streams agent turns over SSE (deltas, tool calls/results,
// confirm cards), manages conversations, and emits focusPhoto bus events.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Conversation,
  LLMSettings,
  PromptsData,
  WireMessage,
  compactConversation,
  getJSON,
  listModels,
  postJSON,
  streamChat,
} from "../api";
import * as bus from "../bus";
import { Markdown } from "../markdown";

interface ToolCard {
  id: string;
  name: string;
  arguments: string;
  result?: string;
}

interface PromptOrigin {
  title: string;
  promptId: string;
}

interface ChatMsg {
  role: "user" | "assistant";
  text: string;
  tools: ToolCard[];
  origin?: PromptOrigin;
}

interface PendingConfirm {
  confirm_id: string;
  name: string;
  arguments: string;
  photo: { id: string; title: string; thumb_url: string | null } | null;
  group: { id: string; name: string } | null;
  warning: string | null;
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

const LAST_MODEL_KEY = "chat-last-model-v1";

function loadLastModel(): { connection: string; model: string } | null {
  try {
    const raw = localStorage.getItem(LAST_MODEL_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveLastModel(connection: string, model: string) {
  try {
    localStorage.setItem(LAST_MODEL_KEY, JSON.stringify({ connection, model }));
  } catch {
    // localStorage unavailable (private browsing, quota) — not fatal, just skip persisting
  }
}

// The chat header uses one flat "ConnectionName: model" selector whose
// option values are "connectionId::model" composites.
function parseSelector(value: string): { connectionId: string; model: string } {
  const idx = value.indexOf("::");
  if (idx === -1) return { connectionId: "", model: "" };
  return { connectionId: value.slice(0, idx), model: value.slice(idx + 2) };
}

function makeSelector(connectionId: string, model: string): string {
  return `${connectionId}::${model}`;
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

/** A user message sent from a workflow prompt: collapsed to just its title,
 * openable to see the exact text that was sent and to jump to editing the
 * stored prompt (which only affects future runs, not this one). */
function PromptOriginMsg({ text, origin }: { text: string; origin: PromptOrigin }) {
  return (
    <details className="prompt-origin chat-bubble">
      <summary>▶ {origin.title}</summary>
      <pre>{text}</pre>
      {origin.promptId && (
        <button
          type="button"
          className="edit-prompt-link"
          onClick={() => {
            bus.emit("openPanel", "prompts");
            bus.requestEditPrompt(origin.promptId);
          }}
        >
          Edit prompt →
        </button>
      )}
    </details>
  );
}

export function Chat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
  const [denyReason, setDenyReason] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);

  // Connection / model selector state — one flat "connectionId::model" value.
  const [llmCfg, setLlmCfg] = useState<LLMSettings | null>(null);
  const [modelsByConnection, setModelsByConnection] = useState<Record<string, string[]>>({});
  const [connectionModel, setConnectionModel] = useState("");
  const connectionModelRef = useRef("");
  connectionModelRef.current = connectionModel;

  const scrollRef = useRef<HTMLDivElement>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;
  const focusedPhotoRef = useRef<string | null>(null);
  const autoApproveRef = useRef(false);
  autoApproveRef.current = autoApprove;
  const streamingRef = useRef(false);
  streamingRef.current = streaming;

  const refreshConversations = useCallback(() => {
    getJSON<{ conversations: Conversation[] }>("/api/chat/conversations")
      .then((r) => setConversations(r.conversations))
      .catch(() => {});
  }, []);

  useEffect(refreshConversations, [refreshConversations]);

  // Let the (separately-mounted) Stats panel track which conversation is active.
  useEffect(() => {
    bus.emit("activeConversationChanged", activeId);
  }, [activeId]);

  const fetchModelsForConnection = useCallback((connectionId: string) => {
    listModels(connectionId)
      .then((list) => setModelsByConnection((prev) => ({ ...prev, [connectionId]: list.models })))
      .catch((e) => {
        console.error("Failed to list models for connection:", connectionId, e);
        setModelsByConnection((prev) => ({ ...prev, [connectionId]: [] }));
      });
  }, []);

  // Load LLM settings + eagerly fetch every connection's model list (small
  // expected connection counts). Prefers the last connection/model used in
  // this browser (localStorage) over the saved default in Models & Connections,
  // so switching models in the chat header survives a reload.
  useEffect(() => {
    getJSON<LLMSettings>("/api/llm-settings")
      .then((s) => {
        setLlmCfg(s);
        const last = loadLastModel();
        const lastConnectionValid = !!(last && s.connections?.[last.connection]);
        const cid = (lastConnectionValid ? last!.connection : "")
          || s.active_connection || (s.connections && Object.keys(s.connections)[0]) || "";
        const model = (lastConnectionValid && last!.connection === cid ? last!.model : s.active_model) || "";
        setConnectionModel(cid ? makeSelector(cid, model) : "");

        for (const connectionId of Object.keys(s.connections ?? {})) {
          fetchModelsForConnection(connectionId);
        }
      })
      .catch((e) => {
        console.error("Failed to load LLM settings:", e);
        setError("Failed to load LLM settings: " + (e instanceof Error ? e.message : String(e)));
      });
  }, [fetchModelsForConnection]);

  // The Models panel is a separately-mounted component with its own state —
  // a connection added/edited/deleted there (or its disabled_models toggled)
  // doesn't otherwise reach this panel's llmCfg/modelsByConnection. Re-pull
  // settings and re-fetch every connection's (possibly now-different)
  // filtered model list whenever that happens, rather than only on mount.
  useEffect(() => {
    return bus.on("llmConnectionsChanged", () => {
      getJSON<LLMSettings>("/api/llm-settings")
        .then((s) => {
          setLlmCfg(s);
          for (const connectionId of Object.keys(s.connections ?? {})) {
            fetchModelsForConnection(connectionId);
          }
        })
        .catch((e) => console.error("Failed to refresh LLM settings:", e));
    });
  }, [fetchModelsForConnection]);

  // Persist whatever connection/model is active so a reload restores it.
  useEffect(() => {
    if (!llmCfg || !connectionModel) return;
    const { connectionId, model } = parseSelector(connectionModel);
    if (connectionId) saveLastModel(connectionId, model);
  }, [llmCfg, connectionModel]);

  const refreshConnectionModels = fetchModelsForConnection;

  // Track whichever photo is open in the Photo Browser so free-form messages
  // (no workflow button, no explicit id) can default to it — see loop.py's
  // focused_photo_id handling for why this rides in per-request, not stored.
  useEffect(() => bus.on("photoOpened", (id) => { focusedPhotoRef.current = id; }), []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [msgs, confirm]);

  const send = useCallback(async (message: string, origin?: PromptOrigin) => {
    const text = message.trim();
    if (!text) return;
    setError("");
    setInput("");
    setStreaming(true);
    setMsgs((prev) => [
      ...prev,
      { role: "user", text, tools: [], origin },
      { role: "assistant", text: "", tools: [] },
    ]);

    const patchLast = (fn: (m: ChatMsg) => ChatMsg) =>
      setMsgs((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

    const { connectionId, model } = parseSelector(connectionModelRef.current);
    try {
      await streamChat(
        {
          conversation_id: activeIdRef.current ?? undefined,
          message: text,
          focused_photo_id: focusedPhotoRef.current,
          connection: connectionId || undefined,
          model: model || undefined,
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
              if (autoApproveRef.current && !event.warning) {
                postJSON("/api/chat/confirm", { confirm_id: event.confirm_id, approve: true }).catch(() => {});
              } else {
                setConfirm(event);
              }
              break;
            case "tool_result":
              setConfirm(null);
              setDenyReason(null);
              patchLast((m) => ({
                ...m,
                tools: m.tools.map((t) => (t.id === event.id ? { ...t, result: event.text } : t)),
              }));
              break;
            case "focus":
              bus.emit("focusPhoto", event.photo_id);
              break;
            case "compacted":
              // Auto-compact replaced the whole stored history (down to just
              // a summary message) before this turn even started, so every
              // earlier bubble in this view is now stale — drop them and
              // keep only the user/assistant pair `send()` just appended for
              // this turn (the assistant one still fills in as deltas
              // arrive), with the summary as its own bubble ahead of them.
              setMsgs((prev) => [
                { role: "assistant", text: `**Conversation compacted.**\n\n${event.summary}`, tools: [] },
                ...prev.slice(-2),
              ]);
              break;
            case "photo_list":
              bus.emit("openPanel", "photos");
              bus.emit("showPhotoList", event.photo_ids);
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
      setDenyReason(null);
      refreshConversations();
    }
  }, [refreshConversations]);

  useEffect(
    () => bus.on("runCommand", (cmd) => send(cmd.text, { title: cmd.title, promptId: cmd.promptId })),
    [send],
  );

  // Triggered by the Stats panel's "Compact now" button. Runs in the chat
  // window rather than silently in that panel: shows the compact prompt
  // (from the "compact-conversation" builtin prompt, editable in the Prompts
  // panel via the same "Edit prompt →" link every workflow prompt gets) as a
  // collapsed origin bubble, then the resulting summary — the same shape and
  // the same in-progress (streaming-indicator) display as a normal send().
  // This ephemeral bubble isn't a persisted message of its own — the actual
  // stored history is replaced server-side by compactConversation(), so on
  // success the whole visible transcript collapses to just the summary
  // bubble, and on failure the ephemeral bubble is removed to leave the view
  // exactly as it was.
  const compactNow = useCallback(async () => {
    const conversationId = activeIdRef.current;
    if (!conversationId || streamingRef.current) return;
    setError("");
    let promptText = "Summarize this conversation so it can continue seamlessly.";
    let promptId = "";
    try {
      const data = await getJSON<PromptsData>("/api/prompts");
      const p = data.prompts.find((pr) => pr.code === "compact-conversation");
      if (p) {
        promptText = p.text;
        promptId = p.id;
      }
    } catch {
      // Fall back to the placeholder text above — the instruction actually
      // sent server-side always comes from the compact-conversation prompt
      // (or its built-in default) regardless of whether this lookup succeeds.
    }
    setMsgs((prev) => [
      ...prev,
      { role: "user", text: promptText, tools: [], origin: { title: "Compact conversation", promptId } },
    ]);
    setStreaming(true);
    try {
      const result = await compactConversation(conversationId);
      setMsgs([{ role: "assistant", text: `**Conversation compacted.**\n\n${result.summary}`, tools: [] }]);
      refreshConversations();
    } catch (e) {
      setMsgs((prev) => prev.slice(0, -1));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStreaming(false);
    }
  }, [refreshConversations]);

  useEffect(() => bus.on("compactConversation", compactNow), [compactNow]);

  const answerConfirm = async (approve: boolean, reason?: string) => {
    if (!confirm) return;
    setConfirm(null);
    setDenyReason(null);
    try {
      await postJSON("/api/chat/confirm", { confirm_id: confirm.confirm_id, approve, reason });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const openConversation = async (id: string) => {
    setActiveId(id);
    setError("");
    try {
      const detail = await getJSON<{ provider: string; model: string; messages: WireMessage[] }>(
        `/api/chat/conversations/${id}`,
      );
      setMsgs(wireToRender(detail.messages));
      // Restore per-conversation connection/model into the selector
      if (detail.provider) {
        setConnectionModel(makeSelector(detail.provider, detail.model ?? ""));
        if (!modelsByConnection[detail.provider]) refreshConnectionModels(detail.provider);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const newConversation = () => {
    setActiveId(null);
    setMsgs([]);
    setError("");
  };

  const deleteConversation = async () => {
    if (!activeId) return;
    try {
      await postJSON(`/api/chat/conversations/${activeId}/delete`, {});
      newConversation();
      refreshConversations();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const openModelsPanel = useCallback(() => {
    bus.emit("openPanel", "models");
  }, []);

  const openPromptsPanel = useCallback(() => {
    bus.emit("openPanel", "prompts");
  }, []);

  return (
    <div className="panel chat">
      {error && !llmCfg && (
        <div style={{ padding: 12 }}>
          <p className="error">{error}</p>
          <p className="hint">
            This usually means the backend `/api/llm-settings` endpoint failed.
            Check the browser console (F12) for details.
          </p>
        </div>
      )}
      {!llmCfg && !error && (
        <div style={{ padding: 12 }}>
          <p className="hint">Loading LLM settings…</p>
        </div>
      )}
      {llmCfg && (
        <>
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
        {activeId && (
          <button onClick={deleteConversation} title="Delete conversation" className="icon-btn">
            🗑
          </button>
        )}

        {/* Connection + model selector — one flat "Connection: model" list */}
        <select
          value={connectionModel}
          onChange={(e) => setConnectionModel(e.target.value)}
          title="Connection and model"
          className="chat-connection-model-select"
        >
          {!connectionModel && <option value="">(select connection: model)</option>}
          {llmCfg && Object.entries(llmCfg.connections).map(([cid, conn]) =>
            (modelsByConnection[cid] ?? []).map((m) => (
              <option key={makeSelector(cid, m)} value={makeSelector(cid, m)}>
                {conn.name || cid}: {m}
              </option>
            )),
          )}
        </select>
        {(() => {
          const { connectionId } = parseSelector(connectionModel);
          const cid = connectionId || llmCfg?.active_connection || "";
          return cid ? (
            <button
              onClick={() => refreshConnectionModels(cid)}
              title="Refresh model list"
              className="icon-btn"
            >
              ↻
            </button>
          ) : null;
        })()}

        <button
          onClick={() => setAutoApprove((a) => !a)}
          title={autoApprove ? "Auto-approve ON" : "Auto-approve OFF"}
          className={autoApprove ? "icon-btn active" : "icon-btn"}
        >
          ⚡
        </button>
        <button
          onClick={() => bus.emit("openPanel", "stats")}
          title="Open session stats panel"
          className="icon-btn"
        >
          📊
        </button>
        <button
          onClick={openModelsPanel}
          title="Open models &amp; providers panel"
          className="icon-btn"
        >
          ⚙
        </button>
        <button
          onClick={openPromptsPanel}
          title="Open prompts panel"
          className="icon-btn"
        >
          📝
        </button>
      </div>
      <div className="chat-messages" ref={scrollRef}>
        {error && (
          <p className="error" style={{ marginBottom: 12 }}>{error}</p>
        )}
        {msgs.length === 0 && !error && (
          <p className="hint">
            Ask about your photos, or use a workflow button from the Commands panel or a
            photo's detail view. Configure providers via the ⚙ panel first.
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role}`}>
            {m.tools.map((t) => (
              <ToolCardView key={t.id} card={t} />
            ))}
            {m.text && (
              m.origin
                ? <PromptOriginMsg text={m.text} origin={m.origin} />
                : m.role === "assistant"
                  ? (
                    <div className="chat-bubble markdown-body">
                      <Markdown text={m.text} />
                    </div>
                  )
                  : <div className="chat-bubble">{m.text}</div>
            )}
          </div>
        ))}
        {confirm && (
          <div className="confirm-card">
            <p>
              Run <strong>{confirm.name}</strong>?
            </p>
            {confirm.warning && <p className="error">{confirm.warning}</p>}
            {confirm.photo && (
              <div className="confirm-photo">
                {confirm.photo.thumb_url && <img src={confirm.photo.thumb_url} alt="" />}
                <span>
                  {confirm.photo.title || "(untitled)"} — photo {confirm.photo.id}
                </span>
              </div>
            )}
            {confirm.group && (
              <p className="hint">
                Group: {confirm.group.name || "(unknown)"} — {confirm.group.id}
              </p>
            )}
            <pre>{prettyArgs(confirm.arguments)}</pre>
            {denyReason === null ? (
              <div>
                <button className="approve" onClick={() => answerConfirm(true)}>
                  Approve
                </button>
                <button onClick={() => setDenyReason("")}>Deny</button>
              </div>
            ) : (
              <form
                className="deny-reason"
                onSubmit={(e) => {
                  e.preventDefault();
                  answerConfirm(false, denyReason);
                }}
              >
                <input
                  autoFocus
                  type="text"
                  placeholder="Why? (optional, helps it adjust)"
                  value={denyReason}
                  onChange={(e) => setDenyReason(e.target.value)}
                />
                <button type="submit">Send</button>
                <button type="button" onClick={() => answerConfirm(false)}>
                  Skip
                </button>
              </form>
            )}
          </div>
        )}
        {streaming && !confirm && (
          <div className="streaming-indicator" aria-label="Working" role="status">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </div>
        )}
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
        </>
      )}
    </div>
  );
}
