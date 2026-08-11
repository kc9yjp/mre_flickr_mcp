// Topbar dropdown showing the signed-in user's name, with the Theme picker
// nested as an expandable sub-item and a Logout action below it.
// Styling mirrors the View dropdown so all three read as one menu group.

import { useEffect, useRef, useState } from "react";
import { Me, logout } from "./api";
import { ThemeMenu } from "./ThemeMenu";

// First letter of the first two words of the display name (falls back to
// the first two characters of a single-word name/username) — no avatar
// image anywhere in this app, so initials are the only real data to show.
function initials(me: Me): string {
  const src = (me.fullname || me.username || "").trim();
  if (!src) return "?";
  const parts = src.split(/\s+/).filter(Boolean);
  return (parts.length >= 2 ? parts[0][0] + parts[1][0] : src.slice(0, 2)).toUpperCase();
}

export function UserMenu({ me, className }: { me: Me | null; className?: string }) {
  const [open, setOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Collapse the Theme sub-item whenever the menu itself closes (whichever
  // way that happens — toggle click or outside click) so it doesn't reopen
  // pre-expanded next time.
  useEffect(() => {
    if (!open) setThemeOpen(false);
  }, [open]);

  const label = me ? me.fullname || me.username : "…";

  return (
    <div className={`user-menu${className ? ` ${className}` : ""}`} ref={ref}>
      <button
        className="view-dropdown-toggle user-menu-toggle"
        onClick={() => setOpen((o) => !o)}
        title="Account"
      >
        {me && <span className="user-avatar">{initials(me)}</span>}
        <span className="user-menu-label">{label} ▾</span>
      </button>
      {open && (
        <div className="view-dropdown-menu user-menu-panel">
          {me && <div className="user-menu-header">{label}</div>}
          <button
            className="user-menu-submenu-toggle"
            onClick={() => setThemeOpen((o) => !o)}
          >
            <span>Theme</span>
            <span>{themeOpen ? "▾" : "▸"}</span>
          </button>
          {themeOpen && <ThemeMenu />}
          <button onClick={() => logout()}>Logout</button>
        </div>
      )}
    </div>
  );
}
