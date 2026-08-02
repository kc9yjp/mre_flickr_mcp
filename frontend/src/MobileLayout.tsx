// Two-pane layout for viewports under 768px: a persistent Chat pane plus a
// single-select content pane, replacing the desktop dockview grid.

import { useEffect, useState } from "react";
import { Me } from "./api";
import * as bus from "./bus";
import { CommandPalette } from "./CommandPalette";
import { Chat } from "./panels/Chat";
import { PhotoBrowser } from "./panels/PhotoBrowser";
import { PhotoViewer } from "./panels/PhotoViewer";
import { Summary } from "./panels/Summary";
import { Command } from "./panels/Command";
import { SyncPage } from "./panels/SyncPage";
import { QueuePage } from "./panels/QueuePage";
import { SetupPage } from "./panels/SetupPage";
import { SettingsPage } from "./panels/SettingsPage";
import { ThemeMenu } from "./ThemeMenu";
import { UserMenu } from "./UserMenu";

const PANELS = [
  { id: "summary",  label: "Stats" },
  { id: "photos",   label: "Photos" },
  { id: "photo-viewer", label: "Photo Viewer" },
  { id: "sync",     label: "Sync" },
  { id: "queue",    label: "Queue" },
  { id: "commands", label: "Commands" },
  { id: "setup",    label: "Setup" },
  { id: "settings", label: "Settings" },
] as const;

type PanelId = (typeof PANELS)[number]["id"];

const PANEL_KEY    = "mobile-panel-v1";
const POSITION_KEY = "mobile-chat-position-v1";

const PANEL_COMPONENTS: Record<PanelId, React.FC> = {
  photos: PhotoBrowser,
  "photo-viewer": PhotoViewer,
  summary: Summary,
  commands: Command,
  sync: SyncPage,
  queue: QueuePage,
  setup: SetupPage,
  settings: SettingsPage,
};

export function MobileLayout({ me }: { me: Me | null }) {
  const [panel, setPanel] = useState<PanelId>(() => {
    const saved = localStorage.getItem(PANEL_KEY) as PanelId | null;
    return saved && PANELS.some((p) => p.id === saved) ? saved : "summary";
  });
  // Panels stay mounted once visited so switching tabs (e.g. to Chat to run a
  // workflow, then back) doesn't lose in-progress state like a found-photos grid.
  const [visited, setVisited] = useState<Set<PanelId>>(() => new Set([panel]));

  useEffect(() => {
    setVisited((prev) => (prev.has(panel) ? prev : new Set(prev).add(panel)));
  }, [panel]);

  const [chatBottom, setChatBottom] = useState(() => {
    return localStorage.getItem(POSITION_KEY) !== "top";
  });

  const switchPanel = (id: PanelId) => {
    if (!PANELS.some((p) => p.id === id)) return;
    setPanel(id as PanelId);
    localStorage.setItem(PANEL_KEY, id);
  };

  useEffect(() => bus.on("switchPanel", (id) => switchPanel(id as PanelId)), []);

  const togglePosition = () => {
    setChatBottom((b) => {
      const next = !b;
      localStorage.setItem(POSITION_KEY, next ? "bottom" : "top");
      return next;
    });
  };

  const chatPane = (
    <div className="mobile-chat-pane">
      <Chat />
    </div>
  );

  const contentPane = (
    <div className="mobile-content-pane">
      <div className="mobile-panel-bar">
        <select
          value={panel}
          onChange={(e) => switchPanel(e.target.value as PanelId)}
          className="mobile-panel-select"
        >
          {PANELS.map((p) => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
        <div className="topbar-right">
          <button
            className="icon-btn"
            onClick={togglePosition}
            title={chatBottom ? "Move chat to top" : "Move chat to bottom"}
          >
            {chatBottom ? "⬆" : "⬇"}
          </button>
          <ThemeMenu />
          <UserMenu me={me} className="mobile-user" />
        </div>
      </div>
      <div className="mobile-content-scroll">
        {PANELS.filter((p) => visited.has(p.id)).map((p) => {
          const Component = PANEL_COMPONENTS[p.id];
          return (
            <div key={p.id} style={{ display: panel === p.id ? "block" : "none" }}>
              <Component />
            </div>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className="mobile-layout">
      <CommandPalette />
      {chatBottom ? (
        <>
          {contentPane}
          {chatPane}
        </>
      ) : (
        <>
          {chatPane}
          {contentPane}
        </>
      )}
    </div>
  );
}
