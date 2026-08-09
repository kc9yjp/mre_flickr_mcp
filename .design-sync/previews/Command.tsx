import { Command } from "flickr-workbench";
import { screen } from "./_fixtures";

// Command's sync buttons are static, but its workflow buttons come from the
// prompt library (useWorkflowCommands → /api/prompts), so the stub is what
// makes the lower half of the panel populate.
screen();

export function Default() {
  return <Command />;
}
