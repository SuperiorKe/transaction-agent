import { defineConfig, devices } from "@playwright/test";

// One end-to-end lane for the whole pipe: build web/, serve it from FastAPI, drive real status
// transitions, watch the page. @playwright/test is pinned to 1.62.1 so it uses the Chromium build
// already cached on this machine. No phone calls: see scripts/e2e_server.py.
//
// The lane builds into web/dist-e2e, never web/dist: Vite empties its output folder first, so
// building into dist would blank a demo tab served by a uvicorn that's already running.
const PORT = 8123;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-projector",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } },
    },
  ],
  webServer: {
    // `-m` from the repo root, so `import app` resolves (running the file by path would not).
    command:
      "npm run gen:api && npx tsc --noEmit && npx vite build --outDir dist-e2e --emptyOutDir" +
      ` && cd .. && uv run python -m scripts.e2e_server --port ${PORT} --web-dist web/dist-e2e`,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
  },
});
