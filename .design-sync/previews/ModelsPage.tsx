import { ModelsPage } from "flickr-workbench";
import { screen } from "./_fixtures";

// /api/llm-settings + /api/llm-models. The fixture has two connections of
// different kinds (a local Ollama and an OpenAI-compatible workstation), one
// with per-model settings and one with a disabled model, so the panel shows
// its real configuration surface rather than an empty first-run state.
screen();

export function Default() {
  return <ModelsPage />;
}
