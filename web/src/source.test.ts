import { describe, expect, it } from "vitest";

import { parseSource } from "./source";

// The page picks its data source once, from the URL:
//   ?mock=<fixture>   fixtures, no network, mock strip. Always wins, even over ?tx= or an empty or
//                     unknown fixture name, so a mistyped mock URL can never turn into a live session.
//   ?tx=<id>          poll the live API
//   neither           empty state

describe("parseSource", () => {
  it.each([
    ["", { kind: "empty" }],
    ["?", { kind: "empty" }],
    ["?tx=", { kind: "empty" }],
    ["?tx=.", { kind: "empty" }],
    ["?tx=..", { kind: "empty" }],
    ["?tx=tx-123", { kind: "live", txId: "tx-123" }],
    ["?tx=a%2Fb%20c", { kind: "live", txId: "a/b c" }],
    ["?mock=awaiting-approval", { kind: "mock", fixture: "awaiting-approval" }],
    ["?mock=awaiting-approval&tx=tx-123", { kind: "mock", fixture: "awaiting-approval" }],
    ["?tx=tx-123&mock=declined", { kind: "mock", fixture: "declined" }],
    ["?mock=", { kind: "mock", fixture: "" }],
    ["?mock", { kind: "mock", fixture: "" }],
    ["?mock=typo&tx=tx-123", { kind: "mock", fixture: "typo" }],
  ])("%j", (search, expected) => {
    expect(parseSource(search)).toEqual(expected);
  });
});
