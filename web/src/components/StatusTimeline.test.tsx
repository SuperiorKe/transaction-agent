import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TxStatus } from "../stages";
import { StatusTimeline } from "./StatusTimeline";

// Six chips, marked from the server's status_history (DESIGN.md visual system):
//   reached ✓ · current ● · skipped (no icon) · upcoming; settled stages read ✓ Approved,
//   ✓ Done, Closed or ✕ Failed. Icons are inline SVG (IBM Plex has no ✓ ● ✕ glyphs).
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

function iconOf(element: HTMLElement): string | null {
  return element.querySelector("[data-icon]")?.getAttribute("data-icon") ?? null;
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

    expect(iconOf(chip("Request: reached"))).toBe("check");
    expect(iconOf(chip("Calling: reached"))).toBe("check");
    expect(iconOf(chip("Negotiating: reached"))).toBe("check");
    expect(iconOf(chip("Your decision: current"))).toBe("dot");
    expect(iconOf(chip("Confirming: upcoming"))).toBeNull();
    expect(iconOf(chip("Done: upcoming"))).toBeNull();
  });

  it("draws marks as SVG, never as font glyphs", () => {
    render(<StatusTimeline status="FAILED" statusHistory={["CREATED", "FAILED"]} lastUpdateAt={null} />);

    const text = screen.getByRole("list").textContent ?? "";
    expect(text).not.toMatch(/[✓●✕]/);
    expect(screen.getByRole("list").querySelectorAll("svg[aria-hidden='true']").length).toBeGreaterThan(0);
  });

  it("shows an approval as ✓ Approved under Your decision, not as still pending", () => {
    render(
      <StatusTimeline
        status="APPROVED"
        statusHistory={["CREATED", "PROVIDER_SELECTED", "CALLING", "NEGOTIATING", "AGREED_WITHIN_POLICY", "RESULT_READY", "APPROVED"]}
        lastUpdateAt={null}
      />,
    );

    const decision = chip("Your decision: approved");
    expect(iconOf(decision)).toBe("check");
    expect(decision.textContent).toContain("Approved");
    expect(iconOf(chip("Confirming: upcoming"))).toBeNull();
  });

  it("never ticks Confirming on a declined transaction", () => {
    render(
      <StatusTimeline
        status="CLOSED"
        statusHistory={[...CANONICAL, "DECLINED", "CLOSED"]}
        lastUpdateAt={null}
      />,
    );

    expect(iconOf(chip("Confirming: skipped"))).toBeNull();
    const done = chip("Done: closed");
    expect(iconOf(done)).toBeNull();
    expect(done.textContent).toContain("Closed");
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
    expect(iconOf(done)).toBe("cross");
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
    expect(iconOf(done)).toBe("check");
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

  it("keeps the relative time moving with no new props", async () => {
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
