import { describe, expect, it } from "vitest";

import { formatAgo } from "./time";

const NOW = Date.parse("2026-09-14T09:00:00Z");

describe("formatAgo", () => {
  it.each([
    ["2026-09-14T08:59:55Z", "5 s ago"],
    ["2026-09-14T08:59:01Z", "59 s ago"],
    ["2026-09-14T08:59:00Z", "1 min ago"],
    ["2026-09-14T08:15:00Z", "45 min ago"],
    ["2026-09-14T06:00:00Z", "3 h ago"],
    ["2026-09-14T09:00:00Z", "just now"],
    ["2026-09-14T09:00:03Z", "just now"], // server clock slightly ahead of the browser
    ["2026-09-14T11:00:00.000+03:00", "1 h ago"], // offset-aware ISO: 11:00+03:00 is 08:00Z
  ])("%s is %s", (at, expected) => {
    expect(formatAgo(at, NOW)).toBe(expected);
  });

  it("returns an empty string for a timestamp it can't parse", () => {
    expect(formatAgo("not a time", NOW)).toBe("");
  });
});
