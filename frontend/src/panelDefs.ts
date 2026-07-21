import { DockviewApi } from "dockview";

export const PANEL_SPECS: Record<
  string,
  { title: string; position?: Parameters<DockviewApi["addPanel"]>[0]["position"] }
> = {
  photos:   { title: "Photo Browser" },
  summary:  { title: "Summary",  position: { referencePanel: "photos",  direction: "right" } },
  chat:     { title: "Chat",     position: { referencePanel: "summary", direction: "below" } },
  command:  { title: "Commands", position: { referencePanel: "chat",    direction: "within" } },
  sync:     { title: "Sync",     position: { referencePanel: "command", direction: "within" } },
  queue:    { title: "Queue",    position: { referencePanel: "command", direction: "within" } },
  setup:    { title: "Setup",    position: { referencePanel: "command", direction: "within" } },
  settings: { title: "Settings", position: { referencePanel: "command", direction: "within" } },
};

export const PANEL_ORDER = [
  "photos", "summary", "chat", "command", "sync", "queue", "setup", "settings",
];

export function openOrFocusPanel(api: DockviewApi, id: string) {
  const existing = api.getPanel(id);
  if (existing) {
    existing.api.setActive();
    return;
  }
  const spec = PANEL_SPECS[id];
  api.addPanel({ id, component: id, title: spec.title, position: spec.position });
}
