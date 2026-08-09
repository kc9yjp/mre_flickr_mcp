import { Chat } from "flickr-workbench";
import { screen } from "./_fixtures";

// Chat pulls its conversation list, the LLM connection settings and the
// prompt library on mount. The streaming endpoint (/api/chat/stream) is only
// touched when you actually send a turn, so a static preview never reaches it.
screen();

export function Default() {
  return <Chat />;
}
