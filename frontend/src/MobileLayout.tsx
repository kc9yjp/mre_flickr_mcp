// Mobile Shell v2: a compact topbar (title + panel/theme/user dropdowns,
// mirroring the desktop topbar) above a single-select content pane, with
// Chat pinned to a fixed-height footer — replacing the desktop dockview grid
// for viewports under 768px. Chat no longer repositions between top and
// bottom (that toggle existed in Shell v1); it always docks at the bottom.

import { useEffect, useState } from "react";
import { Me } from "./api";
import * as bus from "./bus";
import { CommandPalette } from "./CommandPalette";
import { PanelMenu } from "./PanelMenu";
import { Chat } from "./panels/Chat";
import { PhotoBrowser } from "./panels/PhotoBrowser";
import { PhotoViewer } from "./panels/PhotoViewer";
import { Summary } from "./panels/Summary";
import { Command } from "./panels/Command";
import { Sync } from "./panels/Sync";
import { Queue } from "./panels/Queue";
import { Setup } from "./panels/Setup";
import { Settings } from "./panels/Settings";
import { ThemeMenu } from "./ThemeMenu";
import { UserMenu } from "./UserMenu";

const PANELS = [
  { id: "summary",  label: "Stats" },
  { id: "photos",   label: "Photos" },
  { id: "photoViewer", label: "Photo Viewer" },
  { id: "sync",     label: "Sync" },
  { id: "queue",    label: "Queue" },
  { id: "commands", label: "Commands" },
  { id: "setup",    label: "Setup" },
  { id: "settings", label: "Settings" },
] as const;

type PanelId = (typeof PANELS)[number]["id"];

const PANEL_KEY = "mobile-panel-v1";

const PANEL_COMPONENTS: Record<PanelId, React.FC> = {
  photos: PhotoBrowser,
  photoViewer: PhotoViewer,
  summary: Summary,
  commands: Command,
  sync: Sync,
  queue: Queue,
  setup: Setup,
  settings: Settings,
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

  const switchPanel = (id: PanelId) => {
    if (!PANELS.some((p) => p.id === id)) return;
    setPanel(id);
    localStorage.setItem(PANEL_KEY, id);
  };

  useEffect(() => bus.on("switchPanel", (id) => switchPanel(id as PanelId)), []);

  return (
    <div className="mobile-layout">
      <CommandPalette />
      <header className="topbar mobile-topbar">
        <span className="topbar-title">Mr. E's Photo Workbench</span>
        <div className="topbar-right">
          <PanelMenu panels={PANELS} active={panel} onSelect={switchPanel} />
          <ThemeMenu />
          <UserMenu me={me} className="mobile-user" />
        </div>
      </header>
      <div className="mobile-content-pane">
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
      <div className="mobile-chat-pane">
        <Chat />
      </div>
    </div>
  );
}
