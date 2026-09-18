import { useEffect, useRef, useState } from "react";

import type { FetchResult, TransactionView } from "./api/client";
import { OFFLINE, fetchTransaction } from "./api/client";

export type PollStatus = "loading" | "ok" | "reconnecting" | "server_error" | "not_found" | "fatal";

export interface PollState {
  status: PollStatus;
  view: TransactionView | null;
  detail: string | null;
}

export interface PollOptions {
  fetcher?: (id: string, signal: AbortSignal) => Promise<FetchResult>;
  intervalMs?: number;
}

// Three failures in a row that got an HTTP answer (a 5xx, or a 200 that isn't a transaction) are a
// server problem, not a blip. Unreachable results (status null: network, gateway, timeout) neither
// count toward the streak nor reset it; only a successful poll resets it.
const SERVER_FAILURES_BEFORE_ERROR = 3;
const INITIAL: PollState = { status: "loading", view: null, detail: null };

/**
 * Poll GET /transactions/{id} one request at a time: the next request is scheduled `intervalMs`
 * after the previous one settles, so a slow response can never be overtaken by an older one.
 * Polling stops when the view is terminal, on 404, or on a fatal 4xx. An id change or unmount
 * aborts the in-flight request and drops whatever it returns.
 */
export function usePolledTransaction(
  id: string,
  { fetcher = fetchTransaction, intervalMs = 1000 }: PollOptions = {},
): PollState {
  const [state, setState] = useState<PollState>(INITIAL);
  // Callers may pass a fresh options object on every render; only a new id restarts the loop.
  const fetcherRef = useRef(fetcher);
  const intervalRef = useRef(intervalMs);
  useEffect(() => {
    fetcherRef.current = fetcher;
    intervalRef.current = intervalMs;
  });

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let serverFailures = 0;
    setState(INITIAL);

    const poll = async () => {
      let result: FetchResult;
      try {
        result = await fetcherRef.current(id, controller.signal);
      } catch {
        result = OFFLINE;
      }
      if (controller.signal.aborted) return;
      // An abort this loop didn't ask for (the browser, an extension) must not stop polling silently.
      if (result.kind === "aborted") result = OFFLINE;

      switch (result.kind) {
        case "ok": {
          serverFailures = 0;
          setState({ status: "ok", view: result.view, detail: null });
          if (result.view.terminal) return;
          break;
        }
        case "retryable": {
          if (result.status !== null) serverFailures += 1;
          const status = serverFailures >= SERVER_FAILURES_BEFORE_ERROR ? "server_error" : "reconnecting";
          const detail = result.detail;
          setState((previous) => ({ status, view: previous.view, detail }));
          break;
        }
        case "not_found":
          setState({ status: "not_found", view: null, detail: null });
          return;
        case "fatal":
          setState({ status: "fatal", view: null, detail: result.detail });
          return;
      }
      timer = setTimeout(poll, intervalRef.current);
    };

    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id]);

  return state;
}
