// Tiny typed event bus for cross-panel coordination (chat/deep-link → viewer).

interface RunCommandPayload {
  title: string;
  text: string;
  promptId: string;
}

interface Events {
  viewPhoto: string; // open (or focus) the Photo Viewer panel and load this photo id — own or someone else's, the Viewer figures out which
  runCommand: RunCommandPayload; // a chat prompt to send immediately
  insertChatText: string; // append this text to the chat input (not sent) so the user can build a prompt around it
  editPrompt: string;   // request the Prompts panel open a prompt (by id) for editing
  promptsChanged: void; // a prompt/category/variable was created, edited, deleted, or reset — refetch /api/commands
  llmConnectionsChanged: void; // a connection was added/edited/deleted, or its disabled_models changed — refetch /api/llm-settings + model lists
  photoOpened: string | null; // Photo Viewer's current photo, for chat context
  showPhotoList: string[]; // a tool found these photo ids — show them in the Photo Browser grid
  showUserPhotos: string;  // a tool looked up this user's nsid — switch the Photo Browser to the User tab for them
  showGroupPhotos: string; // a group id/URL was identified — switch the Photo Browser to the Group tab for it
  showUserAlbum: { owner: string; albumId: string }; // a user's album URL was identified — switch to User > Albums and load it
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

// Same problem, same fix, for insertChatText: a click that lands before the
// Chat panel remounts (tab was closed, or mobile hasn't visited it yet) would
// otherwise be dropped since the subscription only registers after mount.
let pendingChatText: string | null = null;

export function requestInsertChatText(text: string): void {
  pendingChatText = text;
  emit("insertChatText", text);
}

export function consumePendingChatText(): string | null {
  const text = pendingChatText;
  pendingChatText = null;
  return text;
}

// Same problem, same fix, for viewPhoto: the Photo Viewer panel is created
// on demand (dockview addPanel) by App's own viewPhoto listener the first
// time it's opened, or any time after the user closes that tab. The new
// PhotoViewer's bus.on("viewPhoto", ...) subscription only registers after
// mount — i.e. after the very emit() that triggered its creation has already
// finished dispatching — so that id would otherwise be silently dropped and
// the freshly-mounted panel would fall back to whatever stale #photo=... (if
// any) happens to still be in the URL hash. Track the last-emitted id here so
// a just-mounted panel can pick it up regardless of subscription timing.
let lastViewPhoto: string | null = null;

export function emitViewPhoto(photoId: string): void {
  lastViewPhoto = photoId;
  emit("viewPhoto", photoId);
}

export function getLastViewPhoto(): string | null {
  return lastViewPhoto;
}
