import { PromptsPage } from "flickr-workbench";
import { screen } from "./_fixtures";

// Thin wrapper around PromptsSection — same data, plus the panel heading.
screen();

export function Default() {
  return <PromptsPage />;
}
