import { useEffect, useRef, useState } from "react";
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

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const saveTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    initSession().then(setMe).catch(() => {});
    const onHash = () => emitHashFocus();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const onReady = (event: DockviewReadyEvent) => {
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

    event.api.onDidLayoutChange(() => {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        localStorage.setItem(LAYOUT_KEY, JSON.stringify(event.api.toJSON()));
      }, 500);
    });

    // Deep link (#photo=123) from the bookmarklet: let panels mount first.
    window.setTimeout(emitHashFocus, 100);
  };

  return (
    <div className="workbench">
      <header className="topbar">
        <span className="topbar-title">Flickr Workbench</span>
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
