import { Summary } from "flickr-workbench";
import { screen, SYNC_RUNNING } from "./_fixtures";

// Summary takes no props — it fetches /api/stats and polls /api/sync/status
// on mount. _fixtures answers both; the component itself is untouched.
screen();

export function Default() {
  return <Summary />;
}

// While a sync is running the panel swaps its "last synced" line for live
// sync state, so it's worth showing as its own state.
export function DuringSync() {
  screen({ "/api/sync/status": SYNC_RUNNING });
  return <Summary />;
}
