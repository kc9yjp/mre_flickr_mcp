// Vite entry point: mounts the workbench React app into #root.

import React from "react";
import ReactDOM from "react-dom/client";
import "dockview/dist/styles/dockview.css";
import "./styles.css";
import App from "./App";
import { initThemePreferences } from "./theme";

initThemePreferences();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
