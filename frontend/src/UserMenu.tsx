// Topbar dropdown showing the signed-in user's name with a logout action.
// Styling mirrors ThemeMenu/the View dropdown so all three read as one menu group.

import { useEffect, useRef, useState } from "react";
import { Me, logout } from "./api";

export function UserMenu({ me, className }: { me: Me | null; className?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div className={`user-menu${className ? ` ${className}` : ""}`} ref={ref}>
      <button className="view-dropdown-toggle" onClick={() => setOpen((o) => !o)} title="Account">
        {me ? me.fullname || me.username : "…"} ▾
      </button>
      {open && (
        <div className="view-dropdown-menu user-menu-panel">
          <button onClick={() => logout()}>Logout</button>
        </div>
      )}
    </div>
  );
}
