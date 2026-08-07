// Chat panel: streams agent turns over SSE (deltas, tool calls/results,
// confirm cards), manages conversations, and emits viewPhoto bus events.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  Conversation,
  LLMSettings,
  PromptsData,
  SessionStats,
  WireMessage,
  cancelChat,
  compactConversation,
  contextUsage,
  getJSON,
  getSessionStats,
  injectChat,
  listModels,
  postJSON,
  streamChat,
} from "../api";
import * as bus from "../bus";
import { classifyFlickrUrl, FlickrRoute } from "../flickrUrl";
import { compactNumber, formatLatency } from "../format";
import { Markdown } from "../markdown";
import { useIsMobile } from "../useIsMobile";

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

// Content is usually a plain string, but a stored tool result can be
// multimodal (an image-fetching tool ran with vision on) — a list of
// {type:"text"|"image_url", ...} parts. React can't render that list
// directly (throws "object as child"), so flatten it to display text,
// same idea as loop.py's own vision-disabled fallback note.
function contentToText(content: WireMessage["content"]): string {
  if (content == null) return "";
  if (typeof content === "string") return content;
  return content
    .map((p) => (p.type === "text" ? p.text : "(image)"))
    .join("\n");
}

// Same idea as the bookmarklet/extension (see scripts/webapi.py's api_setup
// and api_extension): a bare Flickr URL jumps straight to the matching
// panel instead of going through the chat loop.
function routeFlickrUrl(route: FlickrRoute) {
  switch (route.kind) {
    case "photo":
      bus.emitViewPhoto(route.id);
      break;
    case "user":
      bus.emit("openPanel", "photos");
      bus.emit("showUserPhotos", route.ref);
      break;
    case "group":
      bus.emit("openPanel", "photos");
      bus.emit("showGroupPhotos", route.url);
      break;
    case "album":
      bus.emit("openPanel", "photos");
      bus.emit("showUserAlbum", { owner: route.owner, albumId: route.albumId });
      break;
  }
}

function wireToRender(messages: WireMessage[]): ChatMsg[] {
  const out: ChatMsg[] = [];
  const cardsById = new Map<string, ToolCard>();
  for (const m of messages) {
    if (m.role === "user") {
      out.push({ role: "user", text: contentToText(m.content), tools: [] });
    } else if (m.role === "assistant") {
      const tools = (m.tool_calls ?? []).map((c) => {
        const card: ToolCard = { id: c.id, name: c.function.name, arguments: c.function.arguments };
        cardsById.set(c.id, card);
        return card;
      });
      out.push({ role: "assistant", text: contentToText(m.content), tools });
    } else if (m.role === "tool" && m.tool_call_id) {
      const card = cardsById.get(m.tool_call_id);
      if (card) card.result = contentToText(m.content);
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

// The 409 the server sends when a previous turn's lock is still held (e.g.
// the client dropped a connection without a clean close — see send()'s catch
// below) is the one error case with a concrete recovery action: cancel the
// stuck turn so the lock frees up and the next send isn't blocked too.
function isTurnLockedError(message: string): boolean {
  return /turn is already running/i.test(message);
}

function ErrorBanner({ message, onCancelTurn }: { message: string; onCancelTurn: () => void }) {
  return (
    <p className="error error-banner">
      <span>{message}</span>
      {isTurnLockedError(message) && (
        <button type="button" className="btn-sm cancel-turn-btn" onClick={onCancelTurn}>
          Cancel stuck turn
        </button>
      )}
    </p>
  );
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

// Runs of consecutive same-name tool calls (the "get_person_info ×15" case
// an agent produces when it loops a lookup over a list) collapse into one
// group card with a count badge, rather than one <details> per call — still
// individually expandable inside the group.
interface ToolGroup {
  name: string;
  cards: ToolCard[];
}

function groupToolCards(tools: ToolCard[]): ToolGroup[] {
  const groups: ToolGroup[] = [];
  for (const t of tools) {
    const last = groups[groups.length - 1];
    if (last && last.name === t.name) last.cards.push(t);
    else groups.push({ name: t.name, cards: [t] });
  }
  return groups;
}

function ToolCardGroupView({ group }: { group: ToolGroup }) {
  if (group.cards.length === 1) return <ToolCardView card={group.cards[0]} />;
  const running = group.cards.some((c) => c.result === undefined);
  return (
    <details className="tool-card tool-card-group">
      <summary>
        <span>
          🔧 {group.name} {running && <em>running…</em>}
        </span>
        <span className="tool-card-count">×{group.cards.length}</span>
      </summary>
      {group.cards.map((c) => (
        <ToolCardView key={c.id} card={c} />
      ))}
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
  // Messages typed while a turn is streaming, waiting to be sent as a fresh
  // turn once the current one finishes (see the effect below).
  const [queued, setQueued] = useState<string[]>([]);

  // Connection / model selector state — one flat "connectionId::model" value.
  const [llmCfg, setLlmCfg] = useState<LLMSettings | null>(null);
  const [modelsByConnection, setModelsByConnection] = useState<Record<string, string[]>>({});
  const [connectionModel, setConnectionModel] = useState("");
  const connectionModelRef = useRef("");
  connectionModelRef.current = connectionModel;

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Kept in sync with activeId, but ONLY via setActive below — never derived
  // from the activeId state at render time. State updates are batched and
  // only land on the next render, which is too late for send()'s stale()
  // check: right after a brand-new conversation's "start" event calls
  // setActive, the very next SSE event in that same synchronous stream needs
  // this ref to already reflect the new id, not lag a render behind.
  const activeIdRef = useRef<string | null>(null);
  const setActive = useCallback((id: string | null) => {
    activeIdRef.current = id;
    setActiveId(id);
  }, []);
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

  // Polled session stats, both for the context-used% shown next to the
  // Compact button and for the stats strip at the bottom. The strip is
  // collapsible only on mobile, where screen space is scarce; it's always
  // open otherwise.
  const isMobile = useIsMobile();
  const [stats, setStats] = useState<SessionStats | null>(null);
  // Defaults closed on mobile (crowds the screen; toggle it back on with the
  // ⋯ button) — irrelevant to desktop, where statsVisible ignores this flag.
  const [statsOpen, setStatsOpen] = useState(false);
  const statsVisible = statsOpen || !isMobile;
  useEffect(() => {
    const refresh = () => {
      const id = activeIdRef.current;
      if (!id) {
        setStats(null);
        return;
      }
      // A single transient poll failure (flaky connection, brief server
      // hiccup) must not blank out stats we already know are correct — that
      // flashes the Compact button and stats grid away mid-conversation. It
      // self-heals on the next successful poll, so just leave the last
      // known-good value in place and try again in 3s.
      getSessionStats(id).then(setStats).catch(() => {});
    };
    refresh();
    if (!activeId) return;
    const timer = window.setInterval(refresh, 3000);
    return () => window.clearInterval(timer);
  }, [activeId]);
  // Percent + severity of context-window usage, driving both the escalating
  // Compact button and the at-risk banner above the input bar.
  const contextUse = contextUsage(stats);
  const avgLatencyMs = stats && stats.turns > 0 ? Math.round(stats.total_latency_ms / stats.turns) : 0;
  const avgTokensPerTurn = stats && stats.turns > 0 ? Math.round(stats.total_tokens / stats.turns) : 0;

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

    const route = classifyFlickrUrl(text);
    if (route) {
      setInput("");
      routeFlickrUrl(route);
      return;
    }

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

    // This send() call is "for" whichever conversation was active when it
    // started (a brand-new one gets its id from the "start" event). If the
    // user switches to a different conversation — or back to "new" — before
    // this stream ends, later events must stop touching msgs/confirm: the
    // backend still finishes and persists the turn, but a stale event
    // handler silently patching whatever conversation happens to be on
    // screen right now is exactly what caused an old turn's compacted-
    // conversation summary to bleed into a totally unrelated new chat.
    let forConversationId = activeIdRef.current;
    const stale = () => activeIdRef.current !== forConversationId;

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
              // A brand-new conversation only gets its real id here — if the
              // user is still looking at "new conversation" (activeId still
              // null), adopt it as the still-current view.
              if (activeIdRef.current === forConversationId && forConversationId === null) {
                setActive(event.conversation_id);
              }
              forConversationId = event.conversation_id;
              break;
            case "delta":
              if (stale()) break;
              patchLast((m) => ({ ...m, text: m.text + event.text }));
              break;
            case "tool_call":
              if (stale()) break;
              patchLast((m) => ({
                ...m,
                tools: [...m.tools, { id: event.id, name: event.name, arguments: event.arguments }],
              }));
              break;
            case "confirm_request":
              // Always actionable even if stale — a confirmation left
              // unanswered just times out server-side and stalls that
              // (invisible) turn, so still surface it regardless of which
              // conversation is on screen.
              if (autoApproveRef.current && !event.warning) {
                postJSON("/api/chat/confirm", { confirm_id: event.confirm_id, approve: true }).catch(() => {});
              } else {
                setConfirm(event);
              }
              break;
            case "tool_result":
              setConfirm(null);
              setDenyReason(null);
              if (stale()) break;
              patchLast((m) => ({
                ...m,
                tools: m.tools.map((t) => (t.id === event.id ? { ...t, result: event.text } : t)),
              }));
              break;
            case "focus":
              if (stale()) break;
              bus.emitViewPhoto(event.photo_id);
              break;
            case "compacted":
              if (stale()) break;
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
            case "injected":
              if (stale()) break;
              // A live "add info" note was folded into this same turn —
              // append it as its own user bubble, then a fresh assistant
              // bubble for whatever the model says next (mirrors the
              // user/assistant pair pushed when this send() call started).
              setMsgs((prev) => [
                ...prev,
                { role: "user", text: event.text, tools: [] },
                { role: "assistant", text: "", tools: [] },
              ]);
              break;
            case "inject_missed":
              // The "add info" text was accepted while the turn was running
              // but landed too late to be folded into any LLM call (it arrived
              // during the final response), so this turn never answered it.
              // Re-queue it — the queued-message effect sends it as its own
              // follow-up turn once this one finishes ("send it last"). The
              // queued send targets whatever conversation is active when it
              // fires, so only re-queue while still viewing this one; if the
              // user has since switched away (stale), skip it rather than
              // misroute their text into a different conversation. No bubbles
              // here — send() adds the user/assistant pair on re-send.
              if (stale()) break;
              setQueued((prev) => [...prev, event.text]);
              break;
            case "photo_list":
              if (stale()) break;
              bus.emit("openPanel", "photos");
              bus.emit("showPhotoList", event.photo_ids);
              break;
            case "user_photos":
              if (stale()) break;
              bus.emit("openPanel", "photos");
              bus.emit("showUserPhotos", event.nsid);
              break;
            case "cancelled":
              if (stale()) break;
              patchLast((m) => ({ ...m, text: m.text + (m.text ? "\n\n" : "") + "*(cancelled)*" }));
              break;
            case "error":
              if (stale()) break;
              setError(event.message);
              break;
            case "done":
              break;
          }
        },
      );
    } catch (e) {
      if (!stale()) setError(e instanceof Error ? e.message : String(e));
      // The stream died on the client (network change, tab lost connectivity,
      // 409 because a previous attempt died this same way, etc.) without a
      // clean close, so the server has no signal that its side is orphaned —
      // it keeps holding this user's turn lock, sometimes for a long time,
      // since it's just waiting on a dead half-open socket rather than
      // seeing an error. Proactively cancel so that stuck turn (if any) gets
      // torn down and the lock is freed for the next send. No-op if nothing
      // was actually running server-side.
      cancelChat().catch(() => {});
    } finally {
      setStreaming(false);
      if (!stale()) {
        setConfirm(null);
        setDenyReason(null);
      }
      refreshConversations();
    }
  }, [refreshConversations, setActive]);

  // Auto-send the next queued message once the current turn finishes.
  useEffect(() => {
    if (streaming || queued.length === 0) return;
    const [next, ...rest] = queued;
    setQueued(rest);
    send(next);
  }, [streaming, queued, send]);

  const cancelTurn = useCallback(async () => {
    try {
      const res = await cancelChat();
      // Clear the error on success so the banner (and this button) disappear
      // as visible confirmation the stuck turn is gone; ok:false just means
      // nothing was running server-side any more (e.g. it finished or was
      // already cancelled), which is worth saying rather than leaving the
      // original "already running" text sitting there looking unresolved.
      setError(res.ok ? "" : "No turn was running server-side — try sending again.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // While a turn is running, Enter/Send means "add this to the turn that's
  // already in flight" rather than starting a new one.
  const addInfo = useCallback(async () => {
    const text = input.trim();
    const conversationId = activeIdRef.current;
    if (!text) return;
    setInput("");
    if (!conversationId || !streamingRef.current) {
      send(text);
      return;
    }
    try {
      await injectChat(conversationId, text);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // The turn finished in the race between typing and hitting send —
        // just send it as a normal new message instead of losing it.
        send(text);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
  }, [input, send]);

  const queueNext = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    setQueued((prev) => [...prev, text]);
  }, [input]);

  const removeQueued = useCallback((index: number) => {
    setQueued((prev) => prev.filter((_, i) => i !== index));
  }, []);

  useEffect(
    () => bus.on("runCommand", (cmd) => send(cmd.text, { title: cmd.title, promptId: cmd.promptId })),
    [send],
  );

  // A photo id (or other snippet) sent from elsewhere in the workbench —
  // append to the input rather than sending, so the user can type the rest
  // of the prompt around it. The bus event alone can arrive before this
  // panel has mounted/subscribed (closed tab), so also check the sticky
  // slot once on mount — same pattern as editPrompt above.
  const insertChatText = useCallback((text: string) => {
    setInput((prev) => (prev ? `${prev} ${text}` : text));
    inputRef.current?.focus();
  }, []);
  useEffect(() => {
    const sticky = bus.consumePendingChatText();
    if (sticky) insertChatText(sticky);
    return bus.on("insertChatText", insertChatText);
  }, [insertChatText]);

  // Triggered by the "Compact" button in the input bar below. Shows the
  // compact prompt (from the "compact-conversation" builtin prompt, editable
  // in the Prompts panel via the same "Edit prompt →" link every workflow
  // prompt gets) as a collapsed origin bubble, then the resulting summary —
  // the same shape and the same in-progress (streaming-indicator) display as
  // a normal send(). This ephemeral bubble isn't a persisted message of its
  // own — the actual stored history is replaced server-side by
  // compactConversation(), so on success the whole visible transcript
  // collapses to just the summary bubble, and on failure the ephemeral
  // bubble is removed to leave the view exactly as it was.
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
      // Same orphaned-lock risk as send()'s catch — see the comment there.
      cancelChat().catch(() => {});
    } finally {
      setStreaming(false);
    }
  }, [refreshConversations]);

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
    setActive(id);
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
    setActive(null);
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
        <button onClick={newConversation} title="New conversation" className="icon-btn">
          ＋
        </button>

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
        {activeId && (
          <button onClick={deleteConversation} title="Delete conversation" className="icon-btn">
            🗑
          </button>
        )}
      </div>
      <div className="chat-messages" ref={scrollRef}>
        {error && (
          <div style={{ marginBottom: 12 }}>
            <ErrorBanner message={error} onCancelTurn={cancelTurn} />
          </div>
        )}
        {msgs.length === 0 && !error && (
          <p className="hint">
            Ask about your photos, or use a workflow button from the Commands panel or a
            photo's detail view. Configure providers via the ⚙ panel first.
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role}`}>
            {groupToolCards(m.tools).map((g) => (
              <ToolCardGroupView key={g.cards[0].id} group={g} />
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
          <div className="streaming-row">
            <div className="streaming-indicator" aria-label="Working" role="status">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
            <button
              type="button"
              className="icon-btn cancel-turn-btn"
              onClick={cancelTurn}
              title="Cancel this turn"
              aria-label="Cancel this turn"
            >
              ⏹
            </button>
          </div>
        )}
        {error && <ErrorBanner message={error} onCancelTurn={cancelTurn} />}
      </div>
      {queued.length > 0 && (
        <div className="queued-messages">
          {queued.map((q, i) => (
            <div key={i} className="queued-item">
              <span className="hint">Up next:</span> {q}
              <button type="button" className="icon-btn" onClick={() => removeQueued(i)} title="Remove">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      {contextUse && contextUse.level !== "ok" && (
        <div className={`context-warning context-${contextUse.level}`} role="alert">
          <span className="context-warning-text">
            {contextUse.level === "critical" ? "⚠️ " : ""}
            Context {contextUse.percent}% full
            {contextUse.level === "critical"
              ? " — this conversation is at risk of hitting its limit. Compact it to keep going."
              : " — getting long. Consider compacting soon."}
          </span>
          <button
            type="button"
            className="btn-sm context-warning-btn"
            onClick={compactNow}
            disabled={streaming || !activeId}
            title="Summarize this conversation and replace its history"
          >
            Compact now
          </button>
        </div>
      )}
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          if (streaming) addInfo();
          else send(input);
        }}
      >
        <textarea
          ref={inputRef}
          value={input}
          rows={2}
          placeholder={streaming ? "Add info to the running turn…" : "Message the workbench agent…"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (streaming) addInfo();
              else send(input);
            }
          }}
        />
        {contextUse && (
          <button
            type="button"
            className={`btn-sm compact-btn context-${contextUse.level}`}
            onClick={compactNow}
            disabled={streaming || !activeId}
            title="Summarize this conversation and replace its history"
          >
            Compact <span className="context-used">({contextUse.percent}%)</span>
          </button>
        )}
        {streaming ? (
          <>
            <button type="submit" disabled={!input.trim()} title="Fold this into the running turn">
              Add info
            </button>
            <button type="button" disabled={!input.trim()} onClick={queueNext} title="Send after this turn finishes">
              Queue
            </button>
          </>
        ) : (
          <button type="submit" disabled={!input.trim()}>
            Send
          </button>
        )}
       </form>
        <div className="chat-stats-bar">
          {isMobile && (
            <button
              type="button"
              className="icon-btn"
              onClick={() => setStatsOpen((o) => !o)}
              title={statsOpen ? "Hide session stats" : "Show session stats"}
              aria-expanded={statsOpen}
            >
              ⋯
            </button>
          )}
          {statsVisible && (
            stats && stats.turns > 0 ? (
              <div className="stats-grid">
                <div className="stat-item">
                  <span className="stat-label">Turns</span>
                  <span className="stat-value">{compactNumber(stats.turns)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Tokens</span>
                  <span className="stat-value">{compactNumber(stats.total_tokens)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Prompt</span>
                  <span className="stat-value">{compactNumber(stats.prompt_tokens)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Completion</span>
                  <span className="stat-value">{compactNumber(stats.completion_tokens)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Avg Latency</span>
                  <span className="stat-value">{formatLatency(avgLatencyMs)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Avg Tokens/Turn</span>
                  <span className="stat-value">{compactNumber(avgTokensPerTurn)}</span>
                </div>
                <div className={`stat-item${contextUse && contextUse.level !== "ok" ? ` context-${contextUse.level}` : ""}`}>
                  <span className="stat-label">
                    Context Used{contextUse?.level === "critical" ? " · at risk" : ""}
                  </span>
                  <span className="stat-value">{contextUse?.percent ?? 0}%</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Total Latency</span>
                  <span className="stat-value">{formatLatency(stats.total_latency_ms)}</span>
                </div>
              </div>
            ) : (
              <span className="hint">No turns yet in this conversation.</span>
            )
          )}
        </div>
        </>
      )}
    </div>
  );
}
