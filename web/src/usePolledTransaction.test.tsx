import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FetchResult, TransactionView } from "./api/client";
import { usePolledTransaction } from "./usePolledTransaction";

// The poll loop (eng review D3, D4, D16):
//
//   fetch now ──settle──► wait intervalMs ──► fetch ──settle──► ...   one request at a time
//     ok            status "ok", view replaced; stop if view.terminal
//     retryable     keep the last view; "reconnecting", or "server_error" after 3 server
//                   failures in a row (keeps retrying either way)
//     not_found     stop (the page drops ?tx=)
//     fatal         stop and show detail
//     aborted       ignored (unmount or id change)

type Fetcher = (id: string, signal: AbortSignal) => Promise<FetchResult>;

function view(overrides: Partial<TransactionView> = {}): TransactionView {
  return {
    id: "tx-1",
    status: "NEGOTIATING",
    terminal: false,
    status_history: ["CREATED"],
    audit: [],
    ...overrides,
  } as TransactionView;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function scripted(...results: FetchResult[]) {
  const fetcher = vi.fn<Fetcher>();
  for (const result of results) fetcher.mockResolvedValueOnce(result);
  return fetcher;
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const SERVER_ERROR: FetchResult = { kind: "retryable", status: 500, detail: "database is locked" };
const OFFLINE: FetchResult = { kind: "retryable", status: null, detail: "Can't reach the API" };

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("usePolledTransaction", () => {
  it("fetches immediately and exposes the view", async () => {
    const fetcher = scripted({ kind: "ok", view: view() });
    const { result } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    expect(result.current.status).toBe("loading");
    await advance(0);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[0]).toBe("tx-1");
    expect(result.current).toEqual({ status: "ok", view: view(), detail: null });
  });

  it("polls again intervalMs after each response settles", async () => {
    const fetcher = vi.fn<Fetcher>().mockResolvedValue({ kind: "ok", view: view() });
    renderHook(() => usePolledTransaction("tx-1", { fetcher, intervalMs: 1000 }));

    await advance(0);
    await advance(999);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("never overlaps requests when a response is slower than the interval", async () => {
    const slow = deferred<FetchResult>();
    const fetcher = vi
      .fn<Fetcher>()
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValue({ kind: "ok", view: view() });
    renderHook(() => usePolledTransaction("tx-1", { fetcher, intervalMs: 1000 }));

    await advance(3500);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => slow.resolve({ kind: "ok", view: view() }));
    await advance(999);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("stops polling once the view is terminal", async () => {
    const closed = view({ status: "CLOSED", terminal: true });
    const fetcher = vi.fn<Fetcher>().mockResolvedValue({ kind: "ok", view: closed });
    const { result } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    await advance(10_000);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({ status: "ok", view: closed, detail: null });
  });

  it("keeps the last good view while reconnecting, then recovers", async () => {
    const calling = view({ status: "CALLING" });
    const negotiating = view({ status: "NEGOTIATING" });
    const fetcher = scripted(
      { kind: "ok", view: calling },
      OFFLINE,
      { kind: "ok", view: negotiating },
    );
    const { result } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    await advance(0);
    await advance(1000);
    expect(result.current).toEqual({
      status: "reconnecting",
      view: calling,
      detail: "Can't reach the API",
    });

    await advance(1000);
    expect(result.current).toEqual({ status: "ok", view: negotiating, detail: null });
  });

  it("reports a server error after three server failures in a row, and keeps retrying", async () => {
    const good = view();
    const fetcher = scripted(
      { kind: "ok", view: good },
      SERVER_ERROR,
      SERVER_ERROR,
      SERVER_ERROR,
      { kind: "ok", view: good },
    );
    const { result } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    await advance(0);
    await advance(1000);
    await advance(1000);
    expect(result.current.status).toBe("reconnecting");

    await advance(1000);
    expect(result.current).toEqual({ status: "server_error", view: good, detail: "database is locked" });

    await advance(1000);
    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(result.current).toEqual({ status: "ok", view: good, detail: null });
  });

  it("a success resets the server failure count", async () => {
    const good = view();
    const fetcher = scripted(
      { kind: "ok", view: good },
      SERVER_ERROR,
      SERVER_ERROR,
      { kind: "ok", view: good },
      SERVER_ERROR,
    );
    const { result } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    await advance(0);
    for (let i = 0; i < 4; i++) await advance(1000);

    expect(result.current.status).toBe("reconnecting");
  });

  it("network failures alone never escalate to a server error", async () => {
    const fetcher = scripted({ kind: "ok", view: view() }, OFFLINE, OFFLINE, OFFLINE, OFFLINE);
    const { result } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    await advance(0);
    for (let i = 0; i < 4; i++) await advance(1000);

    expect(result.current.status).toBe("reconnecting");
  });

  it("stops on 404 and reports not_found", async () => {
    const fetcher = scripted({ kind: "not_found" });
    const { result } = renderHook(() => usePolledTransaction("gone", { fetcher }));

    await advance(10_000);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({ status: "not_found", view: null, detail: null });
  });

  it("stops on a fatal response and shows its detail", async () => {
    const fetcher = scripted({ kind: "fatal", status: 403, detail: "Owner routes are local only" });
    const { result } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    await advance(10_000);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({
      status: "fatal",
      view: null,
      detail: "Owner routes are local only",
    });
  });

  it("aborts the in-flight request and schedules nothing after unmount", async () => {
    const pending = deferred<FetchResult>();
    const fetcher = vi.fn<Fetcher>().mockReturnValue(pending.promise);
    const { unmount } = renderHook(() => usePolledTransaction("tx-1", { fetcher }));

    await advance(0);
    const signal = fetcher.mock.calls[0]?.[1];
    unmount();

    expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve({ kind: "ok", view: view() }));
    await advance(10_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("aborts and ignores a late response for a previous transaction id", async () => {
    const late = deferred<FetchResult>();
    const fetcher = vi.fn<Fetcher>().mockImplementation((id) =>
      id === "old" ? late.promise : Promise.resolve({ kind: "ok", view: view({ id: "new" }) }),
    );
    const { result, rerender } = renderHook(
      ({ id }) => usePolledTransaction(id, { fetcher }),
      { initialProps: { id: "old" } },
    );

    await advance(0);
    const oldSignal = fetcher.mock.calls[0]?.[1];
    rerender({ id: "new" });
    await advance(0);

    expect(oldSignal?.aborted).toBe(true);
    expect(result.current.view?.id).toBe("new");

    await act(async () => late.resolve({ kind: "ok", view: view({ id: "old" }) }));
    expect(result.current.view?.id).toBe("new");
  });
});
