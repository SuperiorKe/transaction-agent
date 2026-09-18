import { describe, expect, it } from "vitest";

// Mock mode must never reach the network, so developing the UI can't dial a real provider through
// Twilio. The guarantee is structural: only src/api/client.ts may call fetch, and mock pages never
// construct the live source that uses it. This is the web/ twin of tests/test_architecture.py.

const sources = import.meta.glob<string>(
  ["./**/*.{ts,tsx}", "!./**/*.test.{ts,tsx}", "!./**/openapi.gen.ts"],
  { query: "?raw", import: "default", eager: true },
);

const CLIENT = "./api/client.ts";
const FETCH_CALL = /(?<![\w$])fetch\s*\(/;

describe("network boundary", () => {
  it("scans the app's source files", () => {
    expect(Object.keys(sources)).toEqual(
      expect.arrayContaining([CLIENT, "./App.tsx", "./usePolledTransaction.ts"]),
    );
  });

  it("finds the one real fetch call, so the scan isn't vacuous", () => {
    expect(FETCH_CALL.test(sources[CLIENT] ?? "")).toBe(true);
  });

  it("calls fetch only from src/api/client.ts", () => {
    const offenders = Object.entries(sources)
      .filter(([path, code]) => path !== CLIENT && FETCH_CALL.test(code))
      .map(([path]) => path);

    expect(offenders).toEqual([]);
  });
});
