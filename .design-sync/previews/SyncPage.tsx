import { SyncPage } from "flickr-workbench";
import { screen, SYNC_RUNNING } from "./_fixtures";

// SyncPage self-fetches /api/sync/status (polled) and /api/llm-settings.
screen();

// Idle: every sync type resolved, with its last duration and next run.
export function Idle() {
  return <SyncPage />;
}

// Running: the photos row is mid-fetch and the groups row is in its AI
// summary phase, which is the only place the progress/ETA columns appear.
export function Running() {
  screen({ "/api/sync/status": SYNC_RUNNING });
  return <SyncPage />;
}
