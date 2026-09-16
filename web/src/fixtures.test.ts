import { describe, expect, it } from "vitest";

import { FIXTURES } from "./fixtures";

// Fixtures look exactly like live data, so they must be internally consistent and follow what the
// backend really does: the photography scenario, KES, masked phone numbers.

// Explicit per fixture, not a copy of the terminal-status set: a copy would agree with the builder
// even if both drifted from app/states.py::TERMINAL_STATES.
const EXPECTED_TERMINAL: Record<string, boolean> = {
  created: false,
  negotiating: false,
  "awaiting-approval": false,
  declined: true,
  failed: true,
};
const entries = Object.entries(FIXTURES);

describe("FIXTURES", () => {
  it("covers the step 1 states, including the canonical approval moment", () => {
    expect(Object.keys(FIXTURES).sort()).toEqual(
      ["awaiting-approval", "created", "declined", "failed", "negotiating"].sort(),
    );
    expect(FIXTURES["awaiting-approval"]?.status).toBe("AWAITING_APPROVAL");
  });

  it.each(entries)("%s: history starts at CREATED and ends at the status", (_name, view) => {
    expect(view.status_history[0]).toBe("CREATED");
    expect(view.status_history.at(-1)).toBe(view.status);
  });

  it.each(entries)("%s: terminal is what the backend would say for that status", (name, view) => {
    expect(view.terminal).toBe(EXPECTED_TERMINAL[name]);
  });

  it.each(entries)("%s: audit is newest first", (_name, view) => {
    const times = view.audit.map((event) => Date.parse(event.at));
    expect(times).toEqual([...times].sort((a, b) => b - a));
  });

  it.each(entries)("%s: status.changed events replay the history", (_name, view) => {
    const changes = [...view.audit]
      .reverse()
      .filter((event) => event.type === "status.changed")
      .map((event) => event.payload.to);
    expect(["CREATED", ...changes]).toEqual(view.status_history);
  });

  it.each(entries)("%s: is the photography scenario in KES", (_name, view) => {
    expect(view.service).toBe("photography");
    expect(view.currency).toBe("KES");
  });

  it("never contains an unmasked Kenyan mobile number", () => {
    expect(JSON.stringify(FIXTURES)).not.toMatch(/\+2547\d{8}/);
  });
});
