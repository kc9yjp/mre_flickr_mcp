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
import { Models } from "./panels/Models";
import { Prompts } from "./panels/Prompts";
import { Sync } from "./panels/Sync";
import { Queue } from "./panels/Queue";
import { Setup } from "./panels/Setup";
import { Settings } from "./panels/Settings";
import { MobileLayout } from "./MobileLayout";
import { useIsMobile } from "./useIsMobile";
import { CommandPalette, paletteShortcut } from "./CommandPalette";
import { UserMenu } from "./UserMenu";
import { PANEL_SPECS, PANEL_ORDER, openOrFocusPanel } from "./panelDefs";

// v2: bumped when the "photo-viewer" panel id was renamed to "photoViewer" so
// any layout saved under the old id (which the components map no longer
// resolves) is discarded in favor of a fresh defaultLayout() instead of
// risking a broken restore.
// v3: bumped when defaultLayout()'s arrangement switched to the chat-centric
// hub (chat right of photoViewer, summary nested within chat) so a layout
// saved under the old arrangement doesn't linger.
const LAYOUT_KEY = "workbench-layout-v3";

const components: Record<string, React.FC<IDockviewPanelProps>> = {
  photos: PhotoBrowser,
  photoViewer: PhotoViewer,
  summary: Summary,
  command: Command,
  chat: Chat,
  models: Models,
  prompts: Prompts,
  sync: Sync,
  queue: Queue,
  setup: Setup,
  settings: Settings,
};

// Both titles and positions come from PANEL_SPECS (the single source of
// truth also used by the View menu, the command palette, and
// openOrFocusPanel) — defaultLayout() only decides which panels start open
// (photos, photoViewer, chat, summary; everything else stays closed until
// opened from the View menu) and adds them in dependency order so each
// referencePanel already exists by the time it's needed.
function defaultLayout(api: DockviewApi) {
  for (const id of ["photos", "photoViewer", "chat", "summary"] as const) {
    const spec = PANEL_SPECS[id];
    api.addPanel({
      id,
      component: id,
      title: spec.title,
      position: spec.position,
      inactive: id === "photoViewer", // keep Photo Browser as the visible tab initially
    });
  }

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
  const [viewMenuOpen, setViewMenuOpen] = useState(false);
  const navRef = useRef<HTMLDivElement>(null);
  const saveTimer = useRef<number | undefined>(undefined);
  const dockApi = useRef<DockviewApi | null>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setViewMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handlePanelClick = useCallback((id: string) => {
    if (dockApi.current) openOrFocusPanel(dockApi.current, id);
    setViewMenuOpen(false);
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
    if (dockApi.current) openOrFocusPanel(dockApi.current, "photoViewer");
    bus.emit("switchPanel", "photoViewer");
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
        <div className="topbar-left">
          <span className="topbar-title">Mr. E's Photo Workbench</span>
          <nav className="topbar-nav" ref={navRef}>
            <div className="view-dropdown">
              <button
                className="view-dropdown-toggle"
                onClick={() => setViewMenuOpen((o) => !o)}
                title={`Show/hide panels (${paletteShortcut})`}
              >
                View ▾
              </button>
              {viewMenuOpen && (
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
          </nav>
        </div>
        <div className="topbar-right">
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
