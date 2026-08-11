// Vite entry point: mounts the workbench React app into #root.

import React from "react";
import ReactDOM from "react-dom/client";
import "dockview/dist/styles/dockview.css";
import "./styles.css";
import App from "./App";
import { initThemePreferences } from "./theme";

initThemePreferences();

function render() {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

// Mock mode (npm run dev:mock / build:mock, or docker-compose's frontend-dev
// service) — dynamic import keeps mockApi.ts entirely out of a real
// production bundle, since VITE_MOCK_API is statically known at build time
// and Vite dead-code-eliminates the branch when it's unset.
if (import.meta.env.VITE_MOCK_API === "true") {
  import("./mock/mockApi").then(({ installMockApi }) => {
    installMockApi();
    render();
  });
} else {
  render();
}
