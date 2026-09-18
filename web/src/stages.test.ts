import { describe, expect, it } from "vitest";

import spec from "../openapi.json";
import type { TxStatus } from "./stages";
import { STAGES, STAGE_OF, reachedStages } from "./stages";

// The timeline collapses 18 statuses into 6 stages (DESIGN.md decision 2). A stage is ticked only
// if the transaction was really in it, according to the server's uncapped status_history; it is
// never inferred from position. Marks per stage:
//   reached   the transaction passed through this stage
//   current   the stage of `status`, with an outcome when settled (approved / confirmed / closed / failed)
//   skipped   before the current stage but never visited, e.g. Confirming on a decline
//   future    after the current stage, including stages visited before moving back a stage

function marks(status: TxStatus, history: TxStatus[]) {
  return Object.fromEntries(
    reachedStages(status, history).map((s) => [s.stage, s.outcome ? `${s.mark}:${s.outcome}` : s.mark]),
  );
}

describe("STAGE_OF", () => {
  it("maps every status the API publishes", () => {
    const published: string[] = spec.components.schemas.TxStatus.enum;
    expect(Object.keys(STAGE_OF).sort()).toEqual([...published].sort());
  });

  it("moves APPROVED to Confirming as confirmation starts immediately", () => {
    expect(STAGE_OF.APPROVED).toBe("confirming");
  });

  it("orders the six stages as the chips appear", () => {
    expect(STAGES.map((s) => s.label)).toEqual([
      "Request",
      "Calling",
      "Negotiating",
      "Your decision",
      "Confirming",
      "Done",
    ]);
  });
});

describe("reachedStages", () => {
  it.each<[string, TxStatus, TxStatus[], Record<string, string>]>([
    [
      "a new transaction",
      "CREATED",
      ["CREATED"],
      {
        request: "current",
        calling: "future",
        negotiating: "future",
        decision: "future",
        confirming: "future",
        done: "future",
      },
    ],
    [
      "mid-call",
      "NEGOTIATING",
      ["CREATED", "PROVIDER_SELECTED", "CALLING", "NEGOTIATING"],
      {
        request: "reached",
        calling: "reached",
        negotiating: "current",
        decision: "future",
        confirming: "future",
        done: "future",
      },
    ],
    [
      "the canonical approval moment (over the cap)",
      "AWAITING_APPROVAL",
      ["CREATED", "PROVIDER_SELECTED", "CALLING", "NEGOTIATING", "OUTSIDE_AUTHORITY", "AWAITING_APPROVAL"],
      {
        request: "reached",
        calling: "reached",
        negotiating: "reached",
        decision: "current",
        confirming: "future",
        done: "future",
      },
    ],
    [
      "approved: confirmation is beginning",
      "APPROVED",
      ["CREATED", "PROVIDER_SELECTED", "CALLING", "NEGOTIATING", "AGREED_WITHIN_POLICY", "RESULT_READY", "APPROVED"],
      {
        request: "reached",
        calling: "reached",
        negotiating: "reached",
        decision: "reached",
        confirming: "current:approved",
        done: "future",
      },
    ],
    [
      "declined: Confirming was never reached",
      "CLOSED",
      ["CREATED", "PROVIDER_SELECTED", "CALLING", "NEGOTIATING", "OUTSIDE_AUTHORITY", "AWAITING_APPROVAL", "DECLINED", "CLOSED"],
      {
        request: "reached",
        calling: "reached",
        negotiating: "reached",
        decision: "reached",
        confirming: "skipped",
        done: "current:closed",
      },
    ],
    [
      "failed after nobody answered",
      "FAILED",
      ["CREATED", "PROVIDER_SELECTED", "CALLING", "UNAVAILABLE", "FAILED"],
      {
        request: "reached",
        calling: "reached",
        negotiating: "skipped",
        decision: "skipped",
        confirming: "skipped",
        done: "current:failed",
      },
    ],
    [
      "failed straight from Request (no active providers)",
      "FAILED",
      ["CREATED", "FAILED"],
      {
        request: "reached",
        calling: "skipped",
        negotiating: "skipped",
        decision: "skipped",
        confirming: "skipped",
        done: "current:failed",
      },
    ],
    [
      "confirmed end to end",
      "CONFIRMED",
      ["CREATED", "PROVIDER_SELECTED", "CALLING", "NEGOTIATING", "AGREED_WITHIN_POLICY", "RESULT_READY", "APPROVED", "CONFIRMING", "CONFIRMED"],
      {
        request: "reached",
        calling: "reached",
        negotiating: "reached",
        decision: "reached",
        confirming: "reached",
        done: "current:confirmed",
      },
    ],
    [
      "terms changed on the confirmation call: the highlight moves back",
      "AGREED_WITHIN_POLICY",
      ["CREATED", "PROVIDER_SELECTED", "CALLING", "NEGOTIATING", "AGREED_WITHIN_POLICY", "RESULT_READY", "APPROVED", "CONFIRMING", "AGREED_WITHIN_POLICY"],
      {
        request: "reached",
        calling: "reached",
        negotiating: "current",
        decision: "future",
        confirming: "future",
        done: "future",
      },
    ],
  ])("%s", (_name, status, history, expected) => {
    expect(marks(status, history)).toEqual(expected);
  });

  it("treats an empty history as the current status alone", () => {
    expect(marks("CALLING", [])).toEqual(marks("CALLING", ["CALLING"]));
  });
});
