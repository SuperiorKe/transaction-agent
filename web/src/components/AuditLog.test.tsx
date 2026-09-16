import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditEvent } from "./AuditLog";
import { AuditLog } from "./AuditLog";

// Collapsed by default; rows show the raw event type, relative time and a short payload summary.
// No friendly-label map, so a new event type in app/audit.py needs no UI change.

const EVENTS: AuditEvent[] = [
  {
    type: "status.changed",
    payload: { from: "OUTSIDE_AUTHORITY", to: "AWAITING_APPROVAL", reason: "over the cap" },
    at: "2026-09-14T08:59:50Z",
  },
  {
    type: "offer.recorded",
    payload: { amount: 23000, available: true, coverage_hours: 8 },
    at: "2026-09-14T08:59:00Z",
  },
  {
    type: "transaction.created",
    payload: {
      request:
        "Photographer for Monday in Nairobi. Max KES 20,000. Negotiate twice; never agree above KES 20,000 without my approval.",
    },
    at: "2026-09-14T08:50:00Z",
  },
];

function toggle() {
  return screen.getByRole("button", { name: /Audit log/ });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-14T09:00:00Z"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("AuditLog", () => {
  it("starts collapsed and shows how many events there are", () => {
    render(<AuditLog events={EVENTS} />);

    expect(toggle().textContent).toContain("Audit log · 3 events");
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("expands to rows in the order the server sent (newest first)", () => {
    render(<AuditLog events={EVENTS} />);

    fireEvent.click(toggle());

    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows.map((row) => row.querySelector("code")?.textContent)).toEqual([
      "status.changed",
      "offer.recorded",
      "transaction.created",
    ]);
  });

  it("summarises a status change as from → to, with its relative time", () => {
    render(<AuditLog events={EVENTS} />);
    fireEvent.click(toggle());

    const first = within(screen.getByRole("list")).getAllByRole("listitem")[0];
    expect(first?.textContent).toContain("OUTSIDE_AUTHORITY → AWAITING_APPROVAL");
    expect(first?.textContent).toContain("10 s ago");
  });

  it("summarises other payloads as key=value pairs and truncates long values", () => {
    render(<AuditLog events={EVENTS} />);
    fireEvent.click(toggle());

    const [, offer, created] = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(offer?.textContent).toContain("amount=23000 available=true coverage_hours=8");
    expect(created?.textContent).toContain("request=Photographer for Monday in Nairobi.");
    expect(created?.textContent).toContain("…");
    expect(created?.textContent).not.toContain("without my approval");
  });

  it("says 'latest 50' when the list is at the API's cap, since older events were dropped", () => {
    const fifty = Array.from({ length: 50 }, (_, index) => ({
      type: "call.input_received",
      payload: { text: `turn ${index}` },
      at: "2026-09-14T08:59:00Z",
    }));

    render(<AuditLog events={fifty} />);

    expect(toggle().textContent).toContain("Audit log · latest 50 events");
  });

  it("uses the singular for one event and says so when there are none", () => {
    const { rerender } = render(<AuditLog events={EVENTS.slice(0, 1)} />);
    expect(toggle().textContent).toContain("Audit log · 1 event");

    rerender(<AuditLog events={[]} />);
    expect(toggle().textContent).toContain("Audit log · 0 events");
    fireEvent.click(toggle());
    expect(screen.getByText("No events yet")).toBeTruthy();
  });
});
