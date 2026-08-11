// Prompts panel wrapper — hosts the prompt/category/variable editor.

import { PromptsSection } from "./PromptsSection";

export function Prompts() {
  return (
    <div className="panel">
      <h2>Prompts</h2>
      <PromptsSection />
    </div>
  );
}
