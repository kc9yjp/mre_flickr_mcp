import { SettingsPage } from "flickr-workbench";
import { screen } from "./_fixtures";

// /api/settings — the real per-user settings registry (retry timezone,
// queue retry time, background sync interval), each with its description.
screen();

export function Default() {
  return <SettingsPage />;
}
