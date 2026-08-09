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

// The height chain matters here. styles.css sizes the shell with
// `html, body, #root { height: 100% }` and `.workbench { height: 100% }`, but
// the preview mounts into the harness's node, not `#root` — so without an
// explicit height the chain breaks, `.workbench` collapses to content height
// and dockview (which measures its container) renders a single squashed panel
// instead of the real multi-panel layout.
export function Default() {
  return (
    <div style={{ height: "100vh", width: "100%" }}>
      <App />
    </div>
  );
}
