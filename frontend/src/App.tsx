// Root workbench shell: dockview panel layout (with localStorage persistence),
// the View menu, bookmarklet hash deep-linking, and the mobile layout switch.

import { useEffect, useRef, useState, useCallback } from "react";
import {
  DockviewReact,
  DockviewReadyEvent,
  DockviewApi,
  IDockviewPanelProps,
  themeDark,
} from "dockview";
import { initSession, Me } from "./api";
import * as bus from "./bus";
import { PhotoBrowser } from "./panels/PhotoBrowser";
import { PhotoViewer } from "./panels/PhotoViewer";
import { Summary } from "./panels/Summary";
import { Command } from "./panels/Command";
import { Chat } from "./panels/Chat";
import { ModelsPage } from "./panels/ModelsPage";
import { PromptsPage } from "./panels/PromptsPage";
import { SyncPage } from "./panels/SyncPage";
import { QueuePage } from "./panels/QueuePage";
import { SetupPage } from "./panels/SetupPage";
import { SettingsPage } from "./panels/SettingsPage";
import { MobileLayout } from "./MobileLayout";
import { useIsMobile } from "./useIsMobile";
import { CommandPalette, paletteShortcut } from "./CommandPalette";
import { ThemeMenu } from "./ThemeMenu";
import { UserMenu } from "./UserMenu";
import { PANEL_SPECS, PANEL_ORDER, openOrFocusPanel } from "./panelDefs";

const LAYOUT_KEY = "workbench-layout-v1";

const components: Record<string, React.FC<IDockviewPanelProps>> = {
  photos: PhotoBrowser,
  "photo-viewer": PhotoViewer,
  summary: Summary,
  command: Command,
  chat: Chat,
  models: ModelsPage,
  prompts: PromptsPage,
  sync: SyncPage,
  queue: QueuePage,
  setup: SetupPage,
  settings: SettingsPage,
};

function defaultLayout(api: DockviewApi) {
  api.addPanel({ id: "photos", component: "photos", title: "Photo Browser" });
  api.addPanel({
    id: "summary",
    component: "summary",
    title: "Summary",
    position: { referencePanel: "photos", direction: "right" },
  });
  api.addPanel({
    id: "chat",
    component: "chat",
    title: "Chat",
    position: { referencePanel: "summary", direction: "below" },
  });
  api.addPanel({
    id: "command",
    component: "command",
    title: "Commands",
    position: { referencePanel: "chat", direction: "within" },
  });
  api.getPanel("chat")?.api.setActive();
}

// #photo=<id> (own) and #other=<id> (bookmarklet/extension, someone else's)
// both just mean "view this photo" — the Viewer determines is_own itself.
function parseHash(): string | null {
  const m = window.location.hash.match(/(?:photo|other)=(\d+)/);
  return m ? m[1] : null;
}

export default function App() {
  const isMobile = useIsMobile();
  const [me, setMe] = useState<Me | null>(null);
  const [openPanels, setOpenPanels] = useState<Set<string>>(new Set(PANEL_ORDER));
  const [viewOpen, setViewOpen] = useState(false);
  const viewRef = useRef<HTMLDivElement>(null);
  const saveTimer = useRef<number | undefined>(undefined);
  const dockApi = useRef<DockviewApi | null>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (viewRef.current && !viewRef.current.contains(e.target as Node)) {
        setViewOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handlePanelClick = useCallback((id: string) => {
    if (dockApi.current) openOrFocusPanel(dockApi.current, id);
    setViewOpen(false);
  }, []);

  const emitHashFocus = useCallback(() => {
    const id = parseHash();
    if (id) bus.emitViewPhoto(id);
  }, []);

  useEffect(() => {
    initSession().then(setMe).catch(() => {});
    emitHashFocus();
    const onHash = () => emitHashFocus();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [emitHashFocus]);

  // Allow any panel to request opening/focusing another panel via bus
  useEffect(() => bus.on("openPanel", (id) => {
    if (dockApi.current) openOrFocusPanel(dockApi.current, id);
  }), []);

  // Prompt buttons fire runCommand to send a chat message; bring Chat to the
  // foreground so the user sees the response instead of a background tab.
  useEffect(() => bus.on("runCommand", () => {
    if (dockApi.current) openOrFocusPanel(dockApi.current, "chat");
  }), []);

  // Same for insertChatText (e.g. "send photo id to chat") — bring Chat
  // forward so the user can see the id land in the input and keep typing.
  useEffect(() => bus.on("insertChatText", () => {
    if (dockApi.current) openOrFocusPanel(dockApi.current, "chat");
  }), []);

  // The single place that opens/focuses the Photo Viewer panel — reacts to
  // both hash deep-links (emitHashFocus) and in-app grid clicks, on desktop
  // (dockApi) and mobile (switchPanel) alike.
  useEffect(() => bus.on("viewPhoto", () => {
    if (dockApi.current) openOrFocusPanel(dockApi.current, "photo-viewer");
    bus.emit("switchPanel", "photo-viewer");
  }), []);

  const onReady = (event: DockviewReadyEvent) => {
    dockApi.current = event.api;
    const saved = localStorage.getItem(LAYOUT_KEY);
    let restored = false;
    if (saved) {
      try {
        event.api.fromJSON(JSON.parse(saved));
        restored = true;
      } catch {
        localStorage.removeItem(LAYOUT_KEY);
      }
    }
    if (!restored) defaultLayout(event.api);
    setOpenPanels(new Set(event.api.panels.map((p) => p.id)));

    event.api.onDidLayoutChange(() => {
      setOpenPanels(new Set(event.api.panels.map((p) => p.id)));
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        localStorage.setItem(LAYOUT_KEY, JSON.stringify(event.api.toJSON()));
      }, 500);
    });

    // Deep link (#photo=123) from the bookmarklet: let panels mount first.
    window.setTimeout(emitHashFocus, 100);
  };

  if (isMobile) return <MobileLayout me={me} />;

  return (
    <div className="workbench">
      <header className="topbar">
        <span className="topbar-title">Mr. E's Photo Workbench</span>
        <div className="topbar-right">
          <div className="view-dropdown" ref={viewRef}>
            <button
              className="view-dropdown-toggle"
              onClick={() => setViewOpen((o) => !o)}
              title={`Show/hide panels (${paletteShortcut})`}
            >
              View ▾
            </button>
            {viewOpen && (
              <div className="view-dropdown-menu">
                {PANEL_ORDER.map((id) => (
                  <button key={id} onClick={() => handlePanelClick(id)}>
                    <span className="view-check">{openPanels.has(id) ? "✓" : " "}</span>
                    {PANEL_SPECS[id].title}
                  </button>
                ))}
              </div>
            )}
          </div>
          <ThemeMenu />
          <UserMenu me={me} />
        </div>
      </header>
      <div className="dock-container">
        <DockviewReact components={components} onReady={onReady} theme={themeDark} />
      </div>
      <CommandPalette dockApiRef={dockApi} />
    </div>
  );
}
