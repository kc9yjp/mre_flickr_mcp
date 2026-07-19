// Tiny typed event bus for cross-panel coordination (chat/deep-link → viewer).

interface Events {
  focusPhoto: string;
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
