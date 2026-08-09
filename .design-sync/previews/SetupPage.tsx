import { SetupPage } from "flickr-workbench";
import { screen } from "./_fixtures";

// /api/setup — MCP connection details plus the copyable config snippets.
// The fixture's API key is masked, not a real credential.
screen();

export function Default() {
  return <SetupPage />;
}
