import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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

describe("App: request composer", () => {
  it("shows the request composer and makes no request until the owner reads a request", () => {
    const fetchSpy = forbidNetwork();

    render(<App search="" />);

    expect(screen.getByRole("heading", { name: "New request" })).toBeTruthy();
    expect(screen.queryByText(MOCK_STRIP)).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

// The composer is the one flow that spans parse -> review -> create -> start, and the only place
// the owner can put a wrong number in front of a real phone call, so its guards are pinned here.
describe("App: composer parse -> review -> create -> start", () => {
  function nairobiToday(): string {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "Africa/Nairobi",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
  }

  function daysFromToday(days: number): string {
    const date = new Date(`${nairobiToday()}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
  }

  const SOON = daysFromToday(10);
  const PARSED = {
    service: "photography",
    service_date: SOON,
    date_text: "next Monday",
    location: "Nairobi",
    max_budget: 20000,
    max_attempts: 2,
    missing: [],
  };

  /** Dispatches by URL, longest path first so /transactions never swallows /transactions/x/start. */
  function route(handlers: Record<string, () => Response>) {
    const paths = Object.keys(handlers).sort((a, b) => b.length - a.length);
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      const match = paths.find((path) => url === path || url.startsWith(`${path}/`));
      if (!match) throw new Error(`unexpected request: ${url}`);
      return Promise.resolve(handlers[match]!());
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status });

  function readRequest(text = "Photographer next Monday in Nairobi, max 20k") {
    fireEvent.change(screen.getByLabelText("Client request"), { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: "Read request" }));
  }

  it("parses the request, then creates and immediately starts the transaction", async () => {
    const created = { ...FIXTURES["created"]!, id: "tx-new" };
    const fetchMock = route({
      "/parse-request": () => json(200, PARSED),
      "/transactions": () => json(201, created),
      "/transactions/tx-new/start": () => json(202, created),
      "/transactions/tx-new": () => json(200, created),
    });

    render(<App search="" />);
    readRequest();

    expect(await screen.findByRole("heading", { name: "Constraints" })).toBeTruthy();
    expect(screen.getByDisplayValue("Nairobi")).toBeTruthy();
    expect(screen.getByDisplayValue("20000")).toBeTruthy();
    expect(screen.getByText(/Client said/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Start call" }));

    // The created transaction takes over the page and the URL, and starts without a second click.
    expect(await screen.findByRole("listitem", { name: /Request/ })).toBeTruthy();
    expect(window.location.search).toBe("?tx=tx-new");
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toBe("/parse-request");
    expect(urls[1]).toBe("/transactions");
    expect(urls).toContain("/transactions/tx-new/start");
    const createBody = JSON.parse((fetchMock.mock.calls[1]?.[1] as RequestInit).body as string);
    expect(createBody).toMatchObject({ service: "photography", location: "Nairobi", max_budget: 20000 });
  });

  it("surfaces a parse failure and offers no constraints to submit", async () => {
    route({ "/parse-request": () => json(503, { detail: "the model is unavailable" }) });

    render(<App search="" />);
    readRequest();

    expect(await screen.findByText("the model is unavailable")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Constraints" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start call" })).toBeNull();
  });

  it("refuses to start an unsupported service", async () => {
    route({ "/parse-request": () => json(200, { ...PARSED, service: "unsupported" }) });

    render(<App search="" />);
    readRequest("Plumber for Tuesday");

    expect(await screen.findByText(/Only photography is supported/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start call" })).toHaveProperty("disabled", true);
  });

  it.each([
    ["a missing location", { location: null }],
    ["a missing budget", { max_budget: null }],
    ["a budget under the floor", { max_budget: 500 }],
    ["a date in the past", { service_date: daysFromToday(-1) }],
    ["a date beyond the 90-day window", { service_date: daysFromToday(120) }],
  ])("keeps the start button disabled for %s", async (_label, override) => {
    route({ "/parse-request": () => json(200, { ...PARSED, ...override }) });

    render(<App search="" />);
    readRequest();

    await screen.findByRole("heading", { name: "Constraints" });
    expect(screen.getByRole("button", { name: "Start call" })).toHaveProperty("disabled", true);
  });

  it("lets the owner correct a constraint the parser got wrong before dialing", async () => {
    route({ "/parse-request": () => json(200, { ...PARSED, max_budget: null }) });

    render(<App search="" />);
    readRequest();

    await screen.findByRole("heading", { name: "Constraints" });
    const start = screen.getByRole("button", { name: "Start call" });
    expect(start).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByLabelText(/Your cap/), { target: { value: "18000" } });

    expect(start).toHaveProperty("disabled", false);
  });

  it("shows a create failure and leaves the owner able to try again", async () => {
    const fetchMock = route({
      "/parse-request": () => json(200, PARSED),
      "/transactions": () => json(429, { detail: "daily call limit reached" }),
    });

    render(<App search="" />);
    readRequest();
    await screen.findByRole("heading", { name: "Constraints" });
    fireEvent.click(screen.getByRole("button", { name: "Start call" }));

    expect(await screen.findByText("daily call limit reached")).toBeTruthy();
    expect(window.location.search).toBe("");
    expect(screen.getByRole("button", { name: "Start call" })).toHaveProperty("disabled", false);
    expect(fetchMock.mock.calls.filter((call) => String(call[0]) === "/transactions")).toHaveLength(1);
  });

  it("a double-clicked start call creates exactly one transaction", async () => {
    const created = { ...FIXTURES["created"]!, id: "tx-new" };
    const fetchMock = route({
      "/parse-request": () => json(200, PARSED),
      "/transactions": () => json(201, created),
      "/transactions/tx-new/start": () => json(202, created),
      "/transactions/tx-new": () => json(200, created),
    });

    render(<App search="" />);
    readRequest();
    await screen.findByRole("heading", { name: "Constraints" });
    const start = screen.getByRole("button", { name: "Start call" });
    fireEvent.click(start);
    fireEvent.click(start);

    expect(await screen.findByRole("listitem", { name: /Request/ })).toBeTruthy();
    expect(fetchMock.mock.calls.filter((call) => String(call[0]) === "/transactions")).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter((call) => String(call[0]) === "/transactions/tx-new/start"),
    ).toHaveLength(1);
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

describe("App: live mode decisions", () => {
  function serveWithOverride(path: string, status: number, body: unknown, view = FIXTURES["awaiting-approval"]) {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(path)) return Promise.resolve(new Response(JSON.stringify(body), { status }));
      return Promise.resolve(new Response(JSON.stringify(view), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("approves the offer the recommendation actually names", async () => {
    const fetchMock = serveWithOverride("/approve", 202, FIXTURES["awaiting-approval"]);

    render(<App search="?tx=tx-live" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));

    const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/approve"));
    expect(String(call?.[0])).toBe("/transactions/tx-live/approve");
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({ offer_id: 11 });
  });

  it("shows a rejected decision instead of pretending it succeeded", async () => {
    serveWithOverride("/approve", 429, { detail: "daily call limit reached" });

    render(<App search="?tx=tx-live" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByText("daily call limit reached")).toBeTruthy();
    // The poll, not the failed response, stays the source of truth for the status.
    expect(screen.getByRole("listitem", { name: "Your decision: current" })).toBeTruthy();
  });

  it("posts a manual confirmation retry from a failed confirmation", async () => {
    const fetchMock = serveWithOverride(
      "/retry-confirmation",
      202,
      FIXTURES["confirmation-failed"],
      FIXTURES["confirmation-failed"],
    );

    render(<App search="?tx=tx-live" />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry confirmation" }));

    const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/retry-confirmation"));
    expect(String(call?.[0])).toBe("/transactions/tx-live/retry-confirmation");
    expect(call?.[1]).toMatchObject({ method: "POST" });
  });

  it("declines through the decline route", async () => {
    const fetchMock = serveWithOverride("/decline", 200, FIXTURES["declined"]);

    render(<App search="?tx=tx-live" />);
    fireEvent.click(await screen.findByRole("button", { name: "Decline" }));

    const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/decline"));
    expect(String(call?.[0])).toBe("/transactions/tx-live/decline");
    expect(call?.[1]).toMatchObject({ method: "POST" });
  });

  it("starts the call through the start route", async () => {
    const fetchMock = serveWithOverride("/start", 202, FIXTURES["created"], FIXTURES["created"]);

    render(<App search="?tx=tx-live" />);
    fireEvent.click(await screen.findByRole("button", { name: "Start call" }));

    const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/start"));
    expect(String(call?.[0])).toBe("/transactions/tx-live/start");
    expect(call?.[1]).toMatchObject({ method: "POST" });
  });

  it("surfaces a rejected start instead of silently doing nothing", async () => {
    serveWithOverride("/start", 429, { detail: "daily call limit reached" }, FIXTURES["created"]);

    render(<App search="?tx=tx-live" />);
    fireEvent.click(await screen.findByRole("button", { name: "Start call" }));

    expect(await screen.findByText("daily call limit reached")).toBeTruthy();
    // The poll, not the failed response, stays the source of truth for the status.
    expect(screen.getByRole("listitem", { name: "Request: current" })).toBeTruthy();
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
