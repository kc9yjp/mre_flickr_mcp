// Tiny typed event bus for cross-panel coordination (chat/deep-link → viewer).

interface RunCommandPayload {
  title: string;
  text: string;
  promptId: string;
}

interface Events {
  viewPhoto: string; // open (or focus) the Photo Viewer panel and load this photo id — own or someone else's, the Viewer figures out which
  runCommand: RunCommandPayload; // a chat prompt to send immediately
  editPrompt: string;   // request the Prompts panel open a prompt (by id) for editing
  promptsChanged: void; // a prompt/category/variable was created, edited, deleted, or reset — refetch /api/commands
  llmConnectionsChanged: void; // a connection was added/edited/deleted, or its disabled_models changed — refetch /api/llm-settings + model lists
  photoOpened: string | null; // Photo Viewer's current photo, for chat context
  showPhotoList: string[]; // a tool found these photo ids — show them in the Photo Browser grid
  switchPanel: string;  // mobile: switch to named panel
  openPanel: string;    // request desktop dockview to open/focus a panel by id
}

type Handler<K extends keyof Events> = (payload: Events[K]) => void;

const listeners = new Map<keyof Events, Set<Handler<keyof Events>>>();

export function on<K extends keyof Events>(event: K, handler: Handler<K>): () => void {
  let set = listeners.get(event);
  if (!set) {
    set = new Set();
    listeners.set(event, set);
  }
  set.add(handler as Handler<keyof Events>);
  return () => set.delete(handler as Handler<keyof Events>);
}

export function emit<K extends keyof Events>(event: K, payload: Events[K]): void {
  listeners.get(event)?.forEach((h) => h(payload));
}

// A single "pending edit" slot alongside the editPrompt event: emit() alone
// is fire-and-forget, so a request that arrives before the Prompts panel has
// mounted (and subscribed) would otherwise be silently dropped. The panel
// checks this on mount in addition to subscribing live.
let pendingEditPromptId: string | null = null;

export function requestEditPrompt(promptId: string): void {
  pendingEditPromptId = promptId;
  emit("editPrompt", promptId);
}

export function consumePendingEditPrompt(): string | null {
  const id = pendingEditPromptId;
  pendingEditPromptId = null;
  return id;
}
