// Topbar dropdown for picking a color theme and text size. Used by both the
// desktop workbench and the mobile layout so the setting is reachable either way.

import { useEffect, useRef, useState } from "react";
import { THEMES, FONT_SIZES, getStoredTheme, getStoredFontSize, applyTheme, applyFontSize } from "./theme";

export function ThemeMenu() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState(getStoredTheme);
  const [fontSize, setFontSize] = useState(getStoredFontSize);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const pickTheme = (id: string) => {
    setTheme(id);
    applyTheme(id);
  };

  const pickFontSize = (id: string) => {
    setFontSize(id);
    applyFontSize(id);
  };

  return (
    <div className="theme-menu" ref={ref}>
      <button
        className="view-dropdown-toggle"
        onClick={() => setOpen((o) => !o)}
        title="Theme and text size"
      >
        Theme ▾
      </button>
      {open && (
        <div className="theme-menu-panel">
          <div className="theme-menu-section-label">Theme</div>
          <div className="theme-swatch-grid">
            {THEMES.map((t) => (
              <button
                key={t.id}
                className={`theme-swatch theme-swatch-${t.id}${t.id === theme ? " active" : ""}`}
                onClick={() => pickTheme(t.id)}
                title={`${t.label} (${t.mode})`}
              >
                <span className="theme-swatch-check">{t.id === theme ? "✓" : ""}</span>
                {t.label}
              </button>
            ))}
          </div>
          <div className="theme-menu-section-label">Text size</div>
          <div className="font-size-row">
            {FONT_SIZES.map((f) => (
              <button
                key={f.id}
                className={`font-size-btn${f.id === fontSize ? " active" : ""}`}
                onClick={() => pickFontSize(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
