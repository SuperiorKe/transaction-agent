import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { FIXTURES } from "./fixtures";

const MOCK_STRIP = "Mock data — not a live call";

function forbidNetwork() {
  const fetchSpy = vi.fn(() => {
    throw new Error("mock and empty pages must never touch the network");
  });
  vi.stubGlobal("fetch", fetchSpy);
  return fetchSpy;
}

function serve(status: number, body: unknown) {
  const fetchSpy = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve(new Response(JSON.stringify(body), { status })),
  );
  vi.stubGlobal("fetch", fetchSpy);
  return fetchSpy;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App: mock mode", () => {
  it("renders a fixture under the permanent mock strip without touching the network", () => {
    const fetchSpy = forbidNetwork();

    render(<App search="?mock=awaiting-approval" />);

    expect(screen.getByText(MOCK_STRIP)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /dismiss|close/i })).toBeNull();
    expect(screen.getByRole("listitem", { name: "Your decision: current" })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("stays in mock mode when ?tx= is also present", () => {
    const fetchSpy = forbidNetwork();

    render(<App search="?mock=declined&tx=tx-live" />);

    expect(screen.getByText(MOCK_STRIP)).toBeTruthy();
    expect(screen.getByRole("listitem", { name: "Done: closed" })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("an unknown fixture is a mock-mode error, never a live session", () => {
    const fetchSpy = forbidNetwork();

    render(<App search="?mock=typo&tx=tx-live" />);

    expect(screen.getByText(MOCK_STRIP)).toBeTruthy();
    expect(screen.getByText(/Unknown fixture: typo/)).toBeTruthy();
    for (const name of Object.keys(FIXTURES)) {
      expect(screen.getByRole("link", { name })).toBeTruthy();
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("App: empty state", () => {
  it("shows the empty state and makes no request with no parameters", () => {
    const fetchSpy = forbidNetwork();

    render(<App search="" />);

    expect(screen.getByText(/No transaction open/)).toBeTruthy();
    expect(screen.queryByText(MOCK_STRIP)).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("App: live mode", () => {
  it("polls the API for ?tx= and renders the transaction with no mock strip", async () => {
    const fetchSpy = serve(200, FIXTURES["awaiting-approval"]);

    render(<App search="?tx=tx-live" />);

    expect(await screen.findByRole("listitem", { name: "Your decision: current" })).toBeTruthy();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/transactions/tx-live");
    expect(screen.queryByText(MOCK_STRIP)).toBeNull();
  });

  it("a 404 drops ?tx= from the URL and returns to the empty state", async () => {
    window.history.replaceState(null, "", "/?tx=gone&keep=1");
    serve(404, { detail: "transaction not found" });

    render(<App search={window.location.search} />);

    expect(await screen.findByText(/Transaction not found/)).toBeTruthy();
    expect(window.location.search).toBe("?keep=1");
  });

  it("a fatal response stops and shows the server's detail", async () => {
    serve(403, { detail: "Owner routes are local only" });

    render(<App search="?tx=tx-live" />);

    expect(await screen.findByText(/Owner routes are local only/)).toBeTruthy();
  });
});

describe("App: live mode while the API is failing", () => {
  const SERVER_ERROR = { detail: "database is locked" };

  function serveSequence(...replies: Array<[number, unknown] | "offline">) {
    const fetchSpy = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify(SERVER_ERROR), { status: 500 })),
    );
    for (const reply of replies) {
      if (reply === "offline") fetchSpy.mockRejectedValueOnce(new TypeError("Failed to fetch"));
      else fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(reply[1]), { status: reply[0] }));
    }
    vi.stubGlobal("fetch", fetchSpy);
    return fetchSpy;
  }

  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("before any view: loading, then reconnecting, then a server error, never a blank page", async () => {
    serveSequence([500, SERVER_ERROR], [500, SERVER_ERROR], [500, SERVER_ERROR]);

    render(<App search="?tx=tx-live" />);
    expect(screen.getByRole("status").textContent).toBe("Loading transaction…");

    await advance(0);
    expect(screen.getByRole("status").textContent).toBe("Reconnecting… database is locked");

    await advance(1000);
    await advance(1000);
    expect(screen.getByRole("status").textContent).toBe("Server error: database is locked (still retrying)");
  });

  it("with a view: keeps the timeline under a reconnecting banner, then a server error alert", async () => {
    serveSequence(
      [200, FIXTURES["awaiting-approval"]],
      "offline",
      [500, SERVER_ERROR],
      [500, SERVER_ERROR],
      [500, SERVER_ERROR],
    );

    render(<App search="?tx=tx-live" />);
    await advance(0);
    expect(screen.getByRole("listitem", { name: "Your decision: current" })).toBeTruthy();
    expect(screen.queryByText(/Reconnecting/)).toBeNull();

    await advance(1000);
    expect(screen.getByText("Reconnecting… showing the last update")).toBeTruthy();
    expect(screen.getByRole("listitem", { name: "Your decision: current" })).toBeTruthy();

    await advance(3000);
    expect(screen.getByRole("alert").textContent).toBe("Server error: database is locked (still retrying)");
    expect(screen.queryByText(/Reconnecting/)).toBeNull();
    expect(screen.getByRole("listitem", { name: "Your decision: current" })).toBeTruthy();
  });
});
