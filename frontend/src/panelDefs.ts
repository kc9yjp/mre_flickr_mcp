// Central registry of dockview panel ids, titles, and default positions,
// shared by App, MobileLayout, and the command palette.

import { DockviewApi } from "dockview";

export const PANEL_SPECS: Record<
  string,
  { title: string; position?: Parameters<DockviewApi["addPanel"]>[0]["position"] }
> = {
  photos:   { title: "Photo Browser" },
  photoViewer: { title: "Photo Viewer", position: { referencePanel: "photos", direction: "within" } },
  chat:     { title: "Chat" , position: { referencePanel: "photoViewer", direction: "right" } },  
  summary:  { title: "Flickr Summary",  position: { referencePanel: "chat",  direction: "within" } },
  command:  { title: "Commands", position: { referencePanel: "chat",    direction: "within" } },
  models:   { title: "Models", position: { referencePanel: "chat",    direction: "within" } },
  prompts:  { title: "Prompts",  position: { referencePanel: "chat",  direction: "within" } },
  sync:     { title: "Sync",     position: { referencePanel: "chat", direction: "within" } },
  queue:    { title: "Queue",    position: { referencePanel: "chat", direction: "within" } },
  setup:    { title: "Setup",    position: { referencePanel: "chat", direction: "within" } },
  settings: { title: "Settings", position: { referencePanel: "chat", direction: "within" } },
};

export const PANEL_ORDER = [
  "photos", "photoViewer", "chat", "summary", "command", "models", "prompts", "sync", "queue", "setup", "settings",
] as const;

export type PanelId = (typeof PANEL_ORDER)[number];

export function openOrFocusPanel(api: DockviewApi, id: string) {
  const existing = api.getPanel(id);
  if (existing) {
    existing.api.setActive();
    return;
  }
  const spec = PANEL_SPECS[id];
  const position = spec.position;
  const refId = position && "referencePanel" in position ? (position.referencePanel as string) : undefined;
  // The reference panel may not exist yet (e.g. opening Prompts before Models
  // has ever been opened) — open it first so dockview has something to anchor to.
  if (refId && !api.getPanel(refId)) {
    openOrFocusPanel(api, refId);
  }
  api.addPanel({ id, component: id, title: spec.title, position });
}
