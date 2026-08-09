import { App } from "flickr-workbench";
import { screen } from "./_fixtures";

// The whole workbench shell: dockview layout, topbar menus, command palette
// and every panel mounted at once. Because App mounts the full panel set,
// each panel's own fetches fire here too — the stub answers all of them, so
// this renders as a populated workbench instead of a grid of error text.
//
// App is `export default`, re-exported onto the bundle global by the
// source-kit fork (a plain `export *` never forwards a default).
screen();

export function Default() {
  return <App />;
}
