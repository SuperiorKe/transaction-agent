import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `vite dev` forwards API calls to the local FastAPI app. The production build is served by FastAPI
// itself from web/dist (index at /, hashed files under /assets), so the default base "/" is right.
const API = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/parse-request": API,
      "/transactions": API,
      "/health": API,
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
