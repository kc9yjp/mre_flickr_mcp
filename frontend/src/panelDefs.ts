// Central registry of dockview panel ids, titles, and default positions,
// shared by App, MobileLayout, and the command palette.

import { DockviewApi } from "dockview";

export const PANEL_SPECS: Record<
  string,
  { title: string; position?: Parameters<DockviewApi["addPanel"]>[0]["position"] }
> = {
  photos:   { title: "Photo Browser" },
  photoViewer: { title: "Photo Viewer", position: { referencePanel: "photos", direction: "within" } },
  summary:  { title: "Summary",  position: { referencePanel: "photos",  direction: "right" } },
  chat:     { title: "Chat" },  // No position constraint so it can be added dynamically
  command:  { title: "Commands", position: { referencePanel: "chat",    direction: "within" } },
  models:   { title: "Models" },  // No position constraint for dynamic opening
  prompts:  { title: "Prompts",  position: { referencePanel: "models",  direction: "within" } },
  sync:     { title: "Sync",     position: { referencePanel: "command", direction: "within" } },
  queue:    { title: "Queue",    position: { referencePanel: "command", direction: "within" } },
  setup:    { title: "Setup",    position: { referencePanel: "command", direction: "within" } },
  settings: { title: "Settings", position: { referencePanel: "command", direction: "within" } },
};

export const PANEL_ORDER = [
  "photos", "photoViewer", "summary", "chat", "command", "models", "prompts", "sync", "queue", "setup", "settings",
];

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
