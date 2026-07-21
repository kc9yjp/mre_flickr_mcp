import { useState } from "react";
import { Me } from "./api";
import { Chat } from "./panels/Chat";
import { PhotoBrowser } from "./panels/PhotoBrowser";
import { Summary } from "./panels/Summary";
import { Command } from "./panels/Command";
import { SyncPage } from "./panels/SyncPage";
import { QueuePage } from "./panels/QueuePage";
import { SetupPage } from "./panels/SetupPage";

const PANELS = [
  { id: "summary",  label: "Stats" },
  { id: "photos",   label: "Photos" },
  { id: "sync",     label: "Sync" },
  { id: "queue",    label: "Queue" },
  { id: "commands", label: "Commands" },
  { id: "setup",    label: "Setup" },
] as const;

type PanelId = (typeof PANELS)[number]["id"];

const PANEL_KEY    = "mobile-panel-v1";
const POSITION_KEY = "mobile-chat-position-v1";

function ContentPanel({ id }: { id: PanelId }) {
  switch (id) {
    case "photos":   return <PhotoBrowser />;
    case "summary":  return <Summary />;
    case "commands": return <Command />;
    case "sync":     return <SyncPage />;
    case "queue":    return <QueuePage />;
    case "setup":    return <SetupPage />;
  }
}

export function MobileLayout({ me }: { me: Me | null }) {
  const [panel, setPanel] = useState<PanelId>(() => {
    const saved = localStorage.getItem(PANEL_KEY) as PanelId | null;
    return saved && PANELS.some((p) => p.id === saved) ? saved : "summary";
  });

  const [chatBottom, setChatBottom] = useState(() => {
    return localStorage.getItem(POSITION_KEY) !== "top";
  });

  const switchPanel = (id: PanelId) => {
    setPanel(id);
    localStorage.setItem(PANEL_KEY, id);
  };

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
        <button
          className="icon-btn"
          onClick={togglePosition}
          title={chatBottom ? "Move chat to top" : "Move chat to bottom"}
        >
          {chatBottom ? "⬆" : "⬇"}
        </button>
        <span className="topbar-user mobile-user">
          {me ? me.fullname || me.username : ""}
        </span>
      </div>
      <div className="mobile-content-scroll">
        <ContentPanel id={panel} />
      </div>
    </div>
  );

  return (
    <div className="mobile-layout">
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
