import { useEffect, useState } from "react";
import { WorkflowCommand, getJSON } from "./api";
import * as bus from "./bus";

/** Workflow commands for the given context (or all, if omitted), refetched
 * whenever a prompt is created/edited/deleted/reset in the Prompts panel. */
export function useWorkflowCommands(context?: WorkflowCommand["context"]): WorkflowCommand[] {
  const [commands, setCommands] = useState<WorkflowCommand[]>([]);

  useEffect(() => {
    const load = () =>
      getJSON<{ commands: WorkflowCommand[] }>("/api/commands")
        .then((r) => setCommands(context ? r.commands.filter((c) => c.context === context) : r.commands))
        .catch(() => {});
    load();
    return bus.on("promptsChanged", load);
  }, [context]);

  return commands;
}
