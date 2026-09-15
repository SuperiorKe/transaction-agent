import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchTransaction, normaliseDetail } from "./client";
import type { TransactionView } from "./client";

// client.ts is the ONLY module allowed to call fetch (eng review D5). It turns every HTTP outcome
// into one of five kinds the poll loop understands (D4, D16, D19, D21):
//   ok          200 with a parsed TransactionView
//   not_found   404, so the page drops ?tx= and shows the empty state
//   retryable   network failure, 5xx, or a 200 whose body isn't JSON: keep polling
//   fatal       any other 4xx (403 through the tunnel included): stop and show `detail`
//   aborted     the caller cancelled; not an error

const VIEW = { id: "tx-1", status: "CREATED" } as unknown as TransactionView;

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

  it("requests the URL-encoded id and passes the abort signal through", async () => {
    const fetchMock = respond(200, VIEW);
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;

    await fetchTransaction("a/b c", signal);

    expect(fetchMock).toHaveBeenCalledWith("/transactions/a%2Fb%20c", { signal });
  });

  it("maps 404 to not_found", async () => {
    vi.stubGlobal("fetch", respond(404, { detail: "transaction not found" }));

    expect(await fetchTransaction("gone", new AbortController().signal)).toEqual({
      kind: "not_found",
    });
  });

  it.each([500, 502, 503])("maps %i to retryable with the server's detail", async (status) => {
    vi.stubGlobal("fetch", respond(status, { detail: "database is locked" }));

    expect(await fetchTransaction("tx-1", new AbortController().signal)).toEqual({
      kind: "retryable",
      status,
      detail: "database is locked",
    });
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
});
