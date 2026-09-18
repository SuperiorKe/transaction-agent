import { afterEach, describe, expect, it, vi } from "vitest";

import {
  REQUEST_TIMEOUT_MS,
  approveTransaction,
  createTransaction,
  declineTransaction,
  fetchTransaction,
  normaliseDetail,
  parseRequest,
  retryConfirmation,
  startTransaction,
} from "./client";
import type { TransactionView } from "./client";

// client.ts is the ONLY module allowed to call fetch. It turns every HTTP outcome into one of five
// kinds the poll loop understands:
//   ok          200 with something shaped like a TransactionView
//   not_found   404, so the page drops ?tx= and shows the empty state
//   retryable   5xx or a bad 200 (status set); network, 502/503/504 or timeout (status null)
//   fatal       any other 4xx (403 through the tunnel included): stop and show `detail`
//   aborted     the caller cancelled; not an error

const VIEW = {
  id: "tx-1",
  status: "CREATED",
  terminal: false,
  status_history: ["CREATED"],
  audit: [],
} as unknown as TransactionView;

function respond(status: number, body: unknown, { json = true } = {}) {
  const text = json ? JSON.stringify(body) : String(body);
  return vi.fn().mockResolvedValue(new Response(text, { status }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchTransaction", () => {
  it("returns the parsed view on 200", async () => {
    vi.stubGlobal("fetch", respond(200, VIEW));

    const result = await fetchTransaction("tx-1", new AbortController().signal);

    expect(result).toEqual({ kind: "ok", view: VIEW });
  });

  it("requests the URL-encoded id with a signal the caller's abort reaches", async () => {
    const fetchMock = respond(200, VIEW);
    vi.stubGlobal("fetch", fetchMock);
    const caller = new AbortController();

    await fetchTransaction("a/b c", caller.signal);

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/transactions/a%2Fb%20c");
    const passed = (fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.signal;
    expect(passed?.aborted).toBe(false);
  });

  it("treats a 200 that isn't shaped like a transaction as a failed poll, not a view", async () => {
    vi.stubGlobal("fetch", respond(200, { id: "tx-1", status: "CREATED" }));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "retryable",
      status: 200,
      detail: "The API sent a response that isn't a transaction",
    });
  });

  it.each([502, 503, 504])("maps gateway status %i to unreachable, not a server error", async (status) => {
    vi.stubGlobal("fetch", respond(status, "API unreachable", { json: false }));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "retryable",
      status: null,
      detail: "Can't reach the API",
    });
  });

  it("gives up after REQUEST_TIMEOUT_MS and reports the API as unreachable", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (_input: RequestInfo | URL, init?: RequestInit) =>
            new Promise<Response>((_resolve, reject) => {
              init?.signal?.addEventListener("abort", () =>
                reject(new DOMException("The operation was aborted.", "AbortError")),
              );
            }),
        ),
      );

      const pending = fetchTransaction("tx-1", new AbortController().signal);
      await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);

      expect(await pending).toEqual({
        kind: "retryable",
        status: null,
        detail: "The API didn't answer within 5 s",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("maps 404 to not_found", async () => {
    vi.stubGlobal("fetch", respond(404, { detail: "transaction not found" }));

    expect(await fetchTransaction("gone", new AbortController().signal)).toEqual({
      kind: "not_found",
    });
  });

  it.each([500, 501])("maps %i to retryable with the server's detail", async (status) => {
    vi.stubGlobal("fetch", respond(status, { detail: "database is locked" }));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "retryable",
      status,
      detail: "database is locked",
    });
  });

  it("maps a 5xx whose body isn't JSON (uvicorn's plain-text 500) to retryable with a fallback detail", async () => {
    vi.stubGlobal("fetch", respond(500, "Internal Server Error", { json: false }));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "retryable",
      status: 500,
      detail: "Request failed (500)",
    });
  });

  it("returns aborted when the caller cancels while the body is being read", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const response = new Response(JSON.stringify(VIEW), { status: 200 });
        controller.abort();
        return response;
      }),
    );

    expect(await fetchTransaction("tx-1", controller.signal)).toEqual({ kind: "aborted" });
  });

  it("maps a network failure to retryable with no status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "retryable",
      status: null,
      detail: "Can't reach the API",
    });
  });

  it("maps a 200 whose body isn't JSON to retryable", async () => {
    vi.stubGlobal("fetch", respond(200, "<html>proxy error</html>", { json: false }));

    const result = await fetchTransaction("tx-1", new AbortController().signal);

    expect(result).toMatchObject({ kind: "retryable", status: 200 });
  });

  it("maps 403 to fatal with the middleware's message", async () => {
    vi.stubGlobal("fetch", respond(403, { detail: "Owner routes are local only" }));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "fatal",
      status: 403,
      detail: "Owner routes are local only",
    });
  });

  it("maps a 422 validation list to fatal with the messages joined", async () => {
    const detail = [
      { loc: ["body", "max_budget"], msg: "Input should be greater than or equal to 1000" },
      { loc: ["body", "service"], msg: "Input should be 'photography'" },
    ];
    vi.stubGlobal("fetch", respond(422, { detail }));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "fatal",
      status: 422,
      detail:
        "Input should be greater than or equal to 1000; Input should be 'photography'",
    });
  });

  it("returns aborted when the caller cancels", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("The operation was aborted.", "AbortError")),
    );
    controller.abort();

    expect(await fetchTransaction("tx-1", controller.signal)).toEqual({ kind: "aborted" });
  });
});

describe("normaliseDetail", () => {
  it("passes a string detail through", () => {
    expect(normaliseDetail({ detail: "service_date must be within the next 90 days" }, 422)).toBe(
      "service_date must be within the next 90 days",
    );
  });

  it("falls back to the status for anything it can't read", () => {
    expect(normaliseDetail("not json", 400)).toBe("Request failed (400)");
    expect(normaliseDetail({ detail: [{ nope: true }] }, 422)).toBe("Request failed (422)");
    expect(normaliseDetail(null, 500)).toBe("Request failed (500)");
  });

  it("falls back to the status for an empty string or an empty validation list", () => {
    expect(normaliseDetail({ detail: "" }, 400)).toBe("Request failed (400)");
    expect(normaliseDetail({ detail: [] }, 422)).toBe("Request failed (422)");
  });
});

describe("owner mutations", () => {
  const PARSED = {
    service: "photography",
    service_date: "2026-09-20",
    date_text: "Saturday",
    location: "Nairobi",
    max_budget: 20000,
    max_attempts: 2,
    missing: [],
  };

  it("posts the original request to the parser and returns the parsed constraints", async () => {
    const fetchMock = respond(200, PARSED);
    vi.stubGlobal("fetch", fetchMock);

    expect(await parseRequest("Photographer in Nairobi")).toEqual({ ok: true, value: PARSED });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/parse-request");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
    expect(JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string)).toEqual({
      text: "Photographer in Nairobi",
    });
  });

  it("posts transaction fields and URL-encodes an id when starting", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(VIEW), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(VIEW), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    await createTransaction({
      request: "Photographer in Nairobi",
      service: "photography",
      service_date: "2026-09-20",
      location: "Nairobi",
      max_budget: 20000,
      max_attempts: 2,
    });
    await startTransaction("a/b c");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/transactions");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/transactions/a%2Fb%20c/start");
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
  });

  it("posts approval, decline, and manual confirmation retry through encoded URLs", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(VIEW), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(VIEW), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(VIEW), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    await approveTransaction("a/b c", 42);
    await declineTransaction("a/b c");
    await retryConfirmation("a/b c");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/transactions/a%2Fb%20c/approve");
    expect(JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string)).toEqual({ offer_id: 42 });
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/transactions/a%2Fb%20c/decline");
    expect(fetchMock.mock.calls[2]?.[0]).toBe("/transactions/a%2Fb%20c/retry-confirmation");
  });

  it("normalizes validation and non-JSON mutation failures for display", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ detail: [{ msg: "Field required" }] }), { status: 422 }),
        )
        .mockResolvedValueOnce(new Response("Bad gateway", { status: 502 })),
    );

    expect(await parseRequest("x")).toEqual({ ok: false, detail: "Field required" });
    expect(await startTransaction("tx-1")).toEqual({ ok: false, detail: "Request failed (502)" });
  });
});
