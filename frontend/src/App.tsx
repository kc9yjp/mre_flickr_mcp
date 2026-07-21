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
import { Summary } from "./panels/Summary";
import { Command } from "./panels/Command";
import { Chat } from "./panels/Chat";
import { MobileLayout } from "./MobileLayout";
import { useIsMobile } from "./useIsMobile";

const LAYOUT_KEY = "workbench-layout-v1";

const components: Record<string, React.FC<IDockviewPanelProps>> = {
  photos: PhotoBrowser,
  summary: Summary,
  command: Command,
  chat: Chat,
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

function emitHashFocus() {
  const match = window.location.hash.match(/photo=(\d+)/);
  if (match) bus.emit("focusPhoto", match[1]);
}

// Where to (re-)add each panel when it isn't already open. Mirrors defaultLayout.
const PANEL_SPECS: Record<string, { title: string; position?: Parameters<DockviewApi["addPanel"]>[0]["position"] }> = {
  photos:  { title: "Photo Browser" },
  summary: { title: "Summary", position: { referencePanel: "photos", direction: "right" } },
  chat:    { title: "Chat", position: { referencePanel: "summary", direction: "below" } },
  command: { title: "Commands", position: { referencePanel: "chat", direction: "within" } },
};
const PANEL_ORDER = ["photos", "summary", "chat", "command"];

function openOrFocusPanel(api: DockviewApi, id: string) {
  const existing = api.getPanel(id);
  if (existing) {
    existing.api.setActive();
    return;
  }
  const spec = PANEL_SPECS[id];
  api.addPanel({ id, component: id, title: spec.title, position: spec.position });
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

  useEffect(() => {
    initSession().then(setMe).catch(() => {});
    const onHash = () => emitHashFocus();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

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
        <div className="view-dropdown" ref={viewRef}>
          <button
            className="view-dropdown-toggle"
            onClick={() => setViewOpen((o) => !o)}
            title="Show/hide panels"
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
        <span className="topbar-user">
          {me ? me.fullname || me.username : "…"}
          <a href="/">classic UI</a>
        </span>
      </header>
      <div className="dock-container">
        <DockviewReact components={components} onReady={onReady} theme={themeDark} />
      </div>
    </div>
  );
}
