import { useEffect } from "react";
import { CommandPalette } from "flickr-workbench";

// See Markdown.tsx preview note. The palette overlay is self-contained dark
// chrome either way, but this keeps all 7 previews on one consistent theme.
document.documentElement.setAttribute("data-theme", "daylight");

// The palette's open state is internal, toggled only by a real Ctrl/Cmd-K
// keydown (see CommandPalette.tsx) — dispatch the same event a user would
// press, rather than reimplementing the open state. useWorkflowCommands
// swallows fetch failures (.catch(() => {})), so the sandboxed preview shows
// only the static panel list — no live-API error text.
export function Open() {
  useEffect(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
  }, []);
  return <CommandPalette />;
}

// Same open event, then type into the real input to show live filtering.
export function Filtered() {
  useEffect(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    setTimeout(() => {
      const input = document.querySelector<HTMLInputElement>(".palette-input");
      if (!input) return;
      const setValue = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )!.set!;
      setValue.call(input, "photo");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, 50);
  }, []);
  return <CommandPalette />;
}
