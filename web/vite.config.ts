import react from "@vitejs/plugin-react";
import type { ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";

// `vite dev` forwards API calls to the local FastAPI app. The production build is served by FastAPI
// itself from web/dist (index at /, hashed files under /assets), so the default base "/" is right.
const API = "http://localhost:8000";

// Vite types its proxy through bundled http-proxy declarations that need @types/node to resolve.
// web/ deliberately has none (tsconfig "types": ["vite/client"]), so browser code under src/ can't
// reach for process or Buffer. Rather than pull Node's globals in for every file, the two members
// this handler touches are narrowed here.
type ProxyErrorEmitter = {
  on(event: "error", handler: (error: Error, request: unknown, response: unknown) => void): void;
};

type WritableResponse = {
  headersSent: boolean;
  writeHead(status: number, headers: Record<string, string>): void;
  end(body: string): void;
};

// A ws proxy error hands back a raw socket, which can't answer with a status line.
function isWritableResponse(response: unknown): response is WritableResponse {
  return typeof response === "object" && response !== null && "writeHead" in response;
}

// While uvicorn is down or restarting (`--reload` restarts it on every .py save), answer 502 so the
// client reports "Can't reach the API" rather than a server error (src/api/client.ts).
const toApi: ProxyOptions = {
  target: API,
  configure: (proxy) => {
    (proxy as unknown as ProxyErrorEmitter).on("error", (_error, _request, response) => {
      if (isWritableResponse(response) && !response.headersSent) {
        response.writeHead(502, { "Content-Type": "text/plain" });
        response.end("API unreachable");
      }
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/parse-request": toApi,
      "/transactions": toApi,
      "/health": toApi,
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
