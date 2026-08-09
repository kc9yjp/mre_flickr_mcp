import { MobileLayout } from "flickr-workbench";
import { screen, ME } from "./_fixtures";

// The phone-width shell: a tab strip instead of dockview, one panel at a
// time. The only screen that takes a prop (`me`), because the desktop shell
// resolves the session itself and hands it down.
screen();

export function Default() {
  return <MobileLayout me={ME} />;
}
