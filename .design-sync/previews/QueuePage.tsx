import { QueuePage } from "flickr-workbench";
import { screen } from "./_fixtures";

// /api/queue — the fixture carries all three buckets (waiting, errors with
// real rejection reasons, and completed) so every section of the panel has
// content rather than an empty state.
screen();

export function Default() {
  return <QueuePage />;
}
