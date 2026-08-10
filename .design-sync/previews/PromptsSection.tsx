import { PromptsSection } from "flickr-workbench";
import { screen } from "./_fixtures";

// /api/prompts + /api/prompt-variables. The fixture covers all four
// categories, a mix of builtin and user-authored prompts, one disabled
// prompt, and the {{variable}} vocabulary the editor resolves against.
screen();

export function Default() {
  return <PromptsSection />;
}
