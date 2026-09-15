import { defineConfig, devices } from "@playwright/test";

// One end-to-end lane for the whole pipe (eng review D11): build web/, serve it from FastAPI, drive
// real status transitions, watch the page. @playwright/test is pinned to 1.62.1 so it uses the
// Chromium build already cached on this machine (D31). No phone calls: see scripts/e2e_server.py.
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
    command: `npm run build && cd .. && uv run python -m scripts.e2e_server --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
  },
});
