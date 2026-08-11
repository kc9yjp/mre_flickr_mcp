// Theme and text-size picker content. Rendered as a sub-item inside
// UserMenu's dropdown (both desktop and mobile) — it owns no toggle/open
// state or outside-click handling of its own; the parent menu is
// responsible for showing/hiding it.

import { useState } from "react";
import { THEMES, FONT_SIZES, getStoredTheme, getStoredFontSize, applyTheme, applyFontSize } from "./theme";

export function ThemeMenu() {
  const [theme, setTheme] = useState(getStoredTheme);
  const [fontSize, setFontSize] = useState(getStoredFontSize);

  const pickTheme = (id: string) => {
    setTheme(id);
    applyTheme(id);
  };

  const pickFontSize = (id: string) => {
    setFontSize(id);
    applyFontSize(id);
  };

  return (
    <div className="theme-picker">
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
  );
}
