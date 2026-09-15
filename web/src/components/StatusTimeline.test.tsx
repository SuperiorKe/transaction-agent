import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TxStatus } from "../stages";
import { StatusTimeline } from "./StatusTimeline";

// Six chips, marked from the server's status_history (DESIGN.md visual system, eng review D27):
//   reached ✓ · current ● · skipped (no mark) · upcoming; Done shows ✓ Done / Closed / ✕ Failed.
// One full-width status line underneath: raw status · "last update N ago", kept true by a 15 s tick.

const CANONICAL: TxStatus[] = [
  "CREATED",
  "PROVIDER_SELECTED",
  "CALLING",
  "NEGOTIATING",
  "OUTSIDE_AUTHORITY",
  "AWAITING_APPROVAL",
];

function chip(name: string) {
  return screen.getByRole("listitem", { name });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-14T09:00:00Z"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("StatusTimeline", () => {
  it("marks the canonical approval moment", () => {
    render(
      <StatusTimeline
        status="AWAITING_APPROVAL"
        statusHistory={CANONICAL}
        lastUpdateAt="2026-09-14T08:59:55Z"
      />,
    );

    expect(chip("Request: reached").textContent).toContain("✓");
    expect(chip("Calling: reached").textContent).toContain("✓");
    expect(chip("Negotiating: reached").textContent).toContain("✓");
    expect(chip("Your decision: current").textContent).toContain("●");
    expect(chip("Confirming: upcoming").textContent).not.toMatch(/[✓●✕]/);
    expect(chip("Done: upcoming").textContent).not.toMatch(/[✓●✕]/);
  });

  it("never ticks Confirming on a declined transaction", () => {
    render(
      <StatusTimeline
        status="CLOSED"
        statusHistory={[...CANONICAL, "DECLINED", "CLOSED"]}
        lastUpdateAt={null}
      />,
    );

    const confirming = chip("Confirming: skipped");
    expect(confirming.textContent).not.toMatch(/[✓●✕]/);
    expect(chip("Done: closed").textContent).toContain("Closed");
  });

  it("shows a failed transaction as ✕ Failed", () => {
    render(
      <StatusTimeline
        status="FAILED"
        statusHistory={["CREATED", "PROVIDER_SELECTED", "CALLING", "UNAVAILABLE", "FAILED"]}
        lastUpdateAt={null}
      />,
    );

    expect(chip("Negotiating: skipped")).toBeTruthy();
    const done = chip("Done: failed");
    expect(done.textContent).toContain("✕");
    expect(done.textContent).toContain("Failed");
  });

  it("shows a confirmed transaction as ✓ Done", () => {
    render(
      <StatusTimeline
        status="CONFIRMED"
        statusHistory={[
          "CREATED",
          "PROVIDER_SELECTED",
          "CALLING",
          "NEGOTIATING",
          "AGREED_WITHIN_POLICY",
          "RESULT_READY",
          "APPROVED",
          "CONFIRMING",
          "CONFIRMED",
        ]}
        lastUpdateAt={null}
      />,
    );

    const done = chip("Done: confirmed");
    expect(done.textContent).toContain("✓");
    expect(done.textContent).toContain("Done");
  });

  it("prints the raw status and the last update under the chips", () => {
    render(
      <StatusTimeline
        status="AWAITING_APPROVAL"
        statusHistory={CANONICAL}
        lastUpdateAt="2026-09-14T08:59:55Z"
      />,
    );

    expect(screen.getByText("AWAITING_APPROVAL")).toBeTruthy();
    expect(screen.getByText("last update 5 s ago")).toBeTruthy();
  });

  it("keeps the relative time moving with no new props (eng review D23)", async () => {
    render(
      <StatusTimeline
        status="AWAITING_APPROVAL"
        statusHistory={CANONICAL}
        lastUpdateAt="2026-09-14T08:59:55Z"
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(screen.getByText("last update 20 s ago")).toBeTruthy();
  });

  it("omits the last update when there are no audit events yet", () => {
    render(<StatusTimeline status="CREATED" statusHistory={["CREATED"]} lastUpdateAt={null} />);

    expect(screen.queryByText(/last update/)).toBeNull();
  });
});
