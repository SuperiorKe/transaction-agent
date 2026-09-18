import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FIXTURES } from "../fixtures";
import { CallPanel } from "./CallPanel";

afterEach(cleanup);

describe("CallPanel", () => {
  it("shows the latest call with its masked number, duration, and recorded-offer highlight", () => {
    const { container } = render(<CallPanel view={FIXTURES["awaiting-approval"]!} />);

    expect(screen.getByText("+254 1•• ••• 678")).toBeTruthy();
    expect(container.textContent).not.toContain("+254100000678");
    expect(screen.getByText("1:42")).toBeTruthy();
    expect(container.querySelector("mark")?.textContent).toBe("23,000");
  });

  it("says so when no call has started, rather than rendering an empty panel", () => {
    render(<CallPanel view={FIXTURES["created"]!} />);

    expect(screen.getByText("No provider call has started yet.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Call transcript" })).toBeNull();
  });

  it("shows a call with no answer as in progress with an empty transcript", () => {
    render(<CallPanel view={FIXTURES["failed"]!} />);

    // duration_seconds is null until the provider hangs up.
    expect(screen.getByText("In progress")).toBeTruthy();
    expect(screen.getByText("The call has no transcript yet.")).toBeTruthy();
  });

  it("labels a confirmation call as a confirmation, not a negotiation", () => {
    render(<CallPanel view={FIXTURES["confirmation-in-progress"]!} />);

    expect(screen.getByText("Confirmation")).toBeTruthy();
    expect(screen.queryByText("Negotiation")).toBeNull();
  });

  it("highlights only recorded offers, never a budget the agent merely said aloud", () => {
    const { container } = render(<CallPanel view={FIXTURES["awaiting-approval"]!} />);

    const marked = [...container.querySelectorAll("mark")].map((node) => node.textContent);
    // The agent's counteroffer of 20,000 was never a provider quote, so it stays unmarked.
    expect(marked.every((text) => text?.includes("23,000"))).toBe(true);
    expect(marked.some((text) => text?.includes("20,000"))).toBe(false);
  });

  it("renders transcript text as text instead of HTML", () => {
    const view = structuredClone(FIXTURES["awaiting-approval"]!);
    view.negotiations[0]!.transcript[0]!.text = "<img src=x onerror=alert(1)> KES 23,000";
    const { container } = render(<CallPanel view={view} />);

    expect(screen.getByText(/<img src=x/)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });
});
