import { useEffect } from "react";
import { Chat } from "flickr-workbench";
import { screen, rememberModel, selectOption, whenReady, CONVERSATIONS } from "./_fixtures";

// Chat pulls its conversation list, LLM connection settings and prompt library
// on mount. The streaming endpoint (/api/chat/stream) is only touched when you
// actually send a turn, so a static preview never reaches it.
screen();
rememberModel();

// Chat starts on "＋ New conversation" and only loads a thread when one is
// picked — there's no prop and no restore-on-mount, so the preview picks one
// through the real selector. An empty chat window is a truthful render but a
// useless card: it shows none of the message, tool-call or table styling a
// designer needs to see.
export function Default() {
  const id = CONVERSATIONS.conversations[0].id;
  useEffect(() => {
    // The conversation list is fetched, so its <option>s do not exist on the
    // first tick — wait for the one we want before selecting it.
    whenReady(() => {
      const sel = Array.from(document.querySelectorAll("select")).find((s) =>
        /New conversation/.test(s.options[0]?.text ?? ""),
      );
      if (!sel?.querySelector(`option[value="${id}"]`)) return false;
      selectOption(sel, id);
      return true;
    });
  }, [id]);
  return <Chat />;
}
