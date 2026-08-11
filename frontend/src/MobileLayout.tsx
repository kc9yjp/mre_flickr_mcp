// Mobile Shell v2: a compact topbar (title + panel/user dropdowns,
// mirroring the desktop topbar) above a single-select content pane, with
// Chat pinned to a fixed-height footer — replacing the desktop dockview grid
// for viewports under 768px. Chat no longer repositions between top and
// bottom (that toggle existed in Shell v1); it always docks at the bottom.

import { useEffect, useRef, useState } from "react";
import { Me } from "./api";
import * as bus from "./bus";
import { CommandPalette } from "./CommandPalette";
import { PanelMenu } from "./PanelMenu";
import { Chat } from "./panels/Chat";
import { PhotoBrowser } from "./panels/PhotoBrowser";
import { PhotoViewer } from "./panels/PhotoViewer";
import { Summary } from "./panels/Summary";
import { Command } from "./panels/Command";
import { Models } from "./panels/Models";
import { Prompts } from "./panels/Prompts";
import { Sync } from "./panels/Sync";
import { Queue } from "./panels/Queue";
import { Setup } from "./panels/Setup";
import { Settings } from "./panels/Settings";
import { UserMenu } from "./UserMenu";
import { PANEL_SPECS, PANEL_ORDER, PanelId as AllPanelId } from "./panelDefs";

// Ids, titles, and order all come from panelDefs.ts — the same registry the
// desktop View menu and the Ctrl/Cmd-K command palette use — so there's one
// definition of "what panels exist" for the whole app. Chat is the one
// panel excluded here: it's pinned to the fixed footer below and always
// visible, so it has no place in a switchable list.
type PanelId = Exclude<AllPanelId, "chat">;

const PANELS = PANEL_ORDER
  .filter((id): id is PanelId => id !== "chat")
  .map((id) => ({ id, label: PANEL_SPECS[id].title }));

const PANEL_KEY = "mobile-panel-v1";
const CHAT_SIZE_KEY = "mobile-chat-size-v1";
const MIN_CHAT_SIZE = 120; // px — small enough to still show a couple of chat lines
const MAX_CHAT_RATIO = 0.7; // leave at least 30% of the screen for the top panel

function getStoredChatSize(): number | null {
  const n = Number(localStorage.getItem(CHAT_SIZE_KEY));
  return Number.isFinite(n) && n > 0 ? n : null;
}

const PANEL_COMPONENTS: Record<PanelId, React.FC> = {
  photos: PhotoBrowser,
  photoViewer: PhotoViewer,
  summary: Summary,
  command: Command,
  models: Models,
  prompts: Prompts,
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

  // Chat pane size along the layout's main axis (height in portrait, width
  // in landscape) — null means "use the CSS default" until the user drags.
  const [chatSize, setChatSize] = useState<number | null>(getStoredChatSize);
  const layoutRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  useEffect(() => {
    setVisited((prev) => (prev.has(panel) ? prev : new Set(prev).add(panel)));
  }, [panel]);

  // Shared by pointer-drag and keyboard resizing below: clamp to
  // [MIN_CHAT_SIZE, MAX_CHAT_RATIO of the container] and persist.
  const resizeChatTo = (raw: number) => {
    const rect = layoutRef.current?.getBoundingClientRect();
    const landscape = window.matchMedia("(orientation: landscape)").matches;
    const total = rect ? (landscape ? rect.width : rect.height) : raw * 2;
    const next = Math.round(Math.min(Math.max(raw, MIN_CHAT_SIZE), total * MAX_CHAT_RATIO));
    setChatSize(next);
    localStorage.setItem(CHAT_SIZE_KEY, String(next));
  };

  // Pointer capture means move/up keep firing on this element even once the
  // finger/cursor leaves its (thin) bounds, so no document-level listeners
  // are needed.
  const onHandlePointerDown = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    draggingRef.current = true;
  };

  const onHandlePointerMove = (e: React.PointerEvent) => {
    if (!draggingRef.current || !layoutRef.current) return;
    const rect = layoutRef.current.getBoundingClientRect();
    const landscape = window.matchMedia("(orientation: landscape)").matches;
    // The chat pane is the last flex item, so its far edge (bottom in
    // portrait, right in landscape) is the container's own far edge —
    // giving the new size as a simple "far edge minus pointer position".
    resizeChatTo(landscape ? rect.right - e.clientX : rect.bottom - e.clientY);
  };

  const onHandlePointerUp = (e: React.PointerEvent) => {
    draggingRef.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const onHandleKeyDown = (e: React.KeyboardEvent) => {
    const landscape = window.matchMedia("(orientation: landscape)").matches;
    const growKey = landscape ? "ArrowLeft" : "ArrowDown";
    const shrinkKey = landscape ? "ArrowRight" : "ArrowUp";
    if (e.key !== growKey && e.key !== shrinkKey) return;
    e.preventDefault();
    resizeChatTo((chatSize ?? 300) + (e.key === growKey ? 20 : -20));
  };

  const switchPanel = (id: PanelId) => {
    if (!PANELS.some((p) => p.id === id)) return;
    setPanel(id);
    localStorage.setItem(PANEL_KEY, id);
  };

  useEffect(() => bus.on("switchPanel", (id) => switchPanel(id as PanelId)), []);

  return (
    <div className="mobile-layout" ref={layoutRef}>
      <CommandPalette />
      <header className="topbar mobile-topbar">
        <span className="topbar-title">Mr. E's Photo Workbench</span>
        <div className="topbar-right">
          <PanelMenu panels={PANELS} active={panel} onSelect={switchPanel} />
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
      <div
        className="mobile-resize-handle"
        role="separator"
        aria-label="Resize chat panel"
        tabIndex={0}
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={onHandlePointerUp}
        onKeyDown={onHandleKeyDown}
      >
        <div className="mobile-resize-handle-grip" />
      </div>
      <div className="mobile-chat-pane" style={chatSize != null ? { flexBasis: `${chatSize}px` } : undefined}>
        <Chat />
      </div>
    </div>
  );
}
