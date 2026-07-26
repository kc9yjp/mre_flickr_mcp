import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API and auth routes to the Python server so the session
// cookie stays same-origin (SameSite=lax). Prod is served by Starlette at /app.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/login": "http://localhost:8000",
      "/oauth": "http://localhost:8000",
      "/static": "http://localhost:8000",
    },
  },
});
