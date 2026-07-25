import { useCallback, useEffect, useRef, useState } from "react";
import { WorkflowCommand, getJSON } from "./api";
import * as bus from "./bus";
import { DockviewApi } from "dockview";
import { PANEL_SPECS, PANEL_ORDER, openOrFocusPanel } from "./panelDefs";

interface PaletteItem {
  id: string;
  label: string;
  kind: "panel" | "workflow";
  panelId?: string;
  prompt?: string;
  promptId?: string;
}

interface Props {
  dockApiRef?: React.RefObject<DockviewApi | null>;
}

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

function buildPanelItems(): PaletteItem[] {
  return PANEL_ORDER.map((id) => ({
    id: `panel:${id}`,
    label: PANEL_SPECS[id].title,
    kind: "panel",
    panelId: id,
  }));
}

export function CommandPalette({ dockApiRef }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [commands, setCommands] = useState<WorkflowCommand[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getJSON<{ commands: WorkflowCommand[] }>("/api/commands")
      .then((r) => setCommands(r.commands.filter((c) => c.context === "global")))
      .catch(() => {});
  }, []);

  const allItems: PaletteItem[] = [
    ...buildPanelItems(),
    ...commands.map((c) => ({
      id: `cmd:${c.id}`,
      label: c.label,
      kind: "workflow" as const,
      prompt: c.prompt,
      promptId: c.prompt_id,
    })),
  ];

  const filtered = query.trim()
    ? allItems.filter((item) =>
        item.label.toLowerCase().includes(query.toLowerCase())
      )
    : allItems;

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setSelected(0);
  }, []);

  const run = useCallback(
    (item: PaletteItem) => {
      close();
      if (item.kind === "panel") {
        const api = dockApiRef?.current;
        if (api) {
          openOrFocusPanel(api, item.panelId!);
        } else {
          bus.emit("switchPanel", item.panelId!);
        }
      } else if (item.prompt) {
        bus.emit("runCommand", { title: item.label, text: item.prompt, promptId: item.promptId ?? "" });
      }
    },
    [close, dockApiRef]
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setOpen((o) => {
          if (!o) setSelected(0);
          return !o;
        });
      } else if (e.key === "Escape" && open) {
        close();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter" && filtered[selected]) {
      run(filtered[selected]);
    }
  };

  if (!open) return null;

  return (
    <div className="palette-overlay" onMouseDown={close}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Search panels and commands…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
          }}
          onKeyDown={onKeyDown}
        />
        <div className="palette-list">
          {filtered.length === 0 && (
            <div className="palette-empty">No results for "{query}"</div>
          )}
          {filtered.map((item, i) => (
            <div
              key={item.id}
              className={`palette-item${i === selected ? " active" : ""}`}
              onMouseDown={() => run(item)}
              onMouseEnter={() => setSelected(i)}
            >
              <span className="palette-item-label">{item.label}</span>
              <span className="palette-item-kind">
                {item.kind === "panel" ? "Panel" : "Workflow"}
              </span>
            </div>
          ))}
        </div>
        <div className="palette-footer">
          <span>↑↓ navigate</span>
          <span>↵ open</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}

export const paletteShortcut = isMac ? "⌘K" : "Ctrl+K";
