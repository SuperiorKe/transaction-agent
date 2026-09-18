import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FIXTURES } from "../fixtures";
import { RecommendationCard } from "./RecommendationCard";

afterEach(cleanup);

describe("RecommendationCard", () => {
  it("joins a recommendation to its offer and exposes the advertised decision actions", () => {
    const approve = vi.fn(async () => undefined);
    const decline = vi.fn(async () => undefined);
    render(<RecommendationCard view={FIXTURES["awaiting-approval"]!} onApprove={approve} onDecline={decline} />);

    expect(screen.getByText("Requires your approval")).toBeTruthy();
    expect(screen.getByText("KES 23,000")).toBeTruthy();
    expect(screen.getByText("8 hours")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(approve).toHaveBeenCalledWith(11);
    expect(screen.getByRole("button", { name: "Decline" })).toBeTruthy();
  });

  it("renders no-price recommendations and only their advertised action", () => {
    render(<RecommendationCard view={FIXTURES["awaiting-no-price"]!} onDecline={async () => undefined} />);

    expect(screen.getByText("No priced offer is available.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByRole("button", { name: "Decline" })).toBeTruthy();
  });

  it("labels a matching, in-cap offer as within the owner's limit", () => {
    render(<RecommendationCard view={FIXTURES["within-limit"]!} />);

    expect(screen.getByText("Within your limit")).toBeTruthy();
    expect(screen.getByText("KES 18,000")).toBeTruthy();
  });

  it.each([
    ["confirmed", "Booking confirmed."],
    ["declined", "You declined this offer."],
    ["failed", "No provider could be booked."],
  ])("reports the outcome and offers no decision on a %s transaction", (fixture, message) => {
    render(<RecommendationCard view={FIXTURES[fixture]!} />);

    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not invent a price when the recommendation points at a missing offer", () => {
    const view = structuredClone(FIXTURES["awaiting-approval"]!);
    view.negotiations[0]!.offers = [];
    render(<RecommendationCard view={view} onApprove={async () => undefined} />);

    expect(screen.getByText(/no longer available/)).toBeTruthy();
    expect(screen.queryByText(/KES 23,000/)).toBeNull();
  });

  it("flags an unavailable provider instead of presenting its price as bookable", () => {
    const view = structuredClone(FIXTURES["awaiting-approval"]!);
    view.negotiations[0]!.offers[0]!.available = false;
    render(<RecommendationCard view={view} />);

    expect(screen.getByText(/provider is unavailable/)).toBeTruthy();
    expect(screen.queryByText("KES 23,000")).toBeNull();
  });

  it("disables both decisions while one is in flight, so a double click can't send two", () => {
    const approve = vi.fn(async () => undefined);
    render(
      <RecommendationCard
        view={FIXTURES["awaiting-approval"]!}
        pending
        onApprove={approve}
        onDecline={async () => undefined}
      />,
    );

    const [first, second] = screen.getAllByRole("button");
    expect(first).toHaveProperty("disabled", true);
    expect(second).toHaveProperty("disabled", true);
    fireEvent.click(first!);
    expect(approve).not.toHaveBeenCalled();
  });

  it("surfaces a failed decision without hiding the buttons", () => {
    render(
      <RecommendationCard
        view={FIXTURES["awaiting-approval"]!}
        error="transaction is CONFIRMING, not awaiting a decision"
        onApprove={async () => undefined}
        onDecline={async () => undefined}
      />,
    );

    expect(screen.getByRole("alert").textContent).toBe(
      "transaction is CONFIRMING, not awaiting a decision",
    );
    expect(screen.getByRole("button", { name: "Approve" })).toHaveProperty("disabled", false);
  });

  it("shows the manual retry only when the API advertises it", () => {
    render(<RecommendationCard view={FIXTURES["confirmation-failed"]!} onRetry={async () => undefined} />);

    expect(screen.getByRole("button", { name: "Retry confirmation" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});
