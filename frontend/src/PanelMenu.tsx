// Topbar dropdown for switching the mobile shell's single active content
// panel — a single-select sibling of the desktop View dropdown's multi-select
// checkboxes (see App.tsx), styled identically via the same view-dropdown
// classes so all three mobile header dropdowns (this, Theme, User) read as
// one menu group. Introduced with Mobile Shell v2 to replace the native
// <select> panel switcher.

import { useEffect, useRef, useState } from "react";

export function PanelMenu<T extends string>({
  panels,
  active,
  onSelect,
}: {
  panels: readonly { id: T; label: string }[];
  active: T;
  onSelect: (id: T) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const activeLabel = panels.find((p) => p.id === active)?.label ?? "";

  return (
    <div className="panel-menu view-dropdown" ref={ref}>
      <button className="view-dropdown-toggle" onClick={() => setOpen((o) => !o)} title="Switch panel">
        {activeLabel} ▾
      </button>
      {open && (
        <div className="view-dropdown-menu">
          {panels.map((p) => (
            <button
              key={p.id}
              onClick={() => {
                onSelect(p.id);
                setOpen(false);
              }}
            >
              <span className="view-check">{p.id === active ? "✓" : " "}</span>
              {p.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
