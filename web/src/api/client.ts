import type { components } from "./openapi.gen";

export type TransactionView = components["schemas"]["TransactionView"];

// Every HTTP outcome, reduced to what the poll loop needs to decide.
export type FetchResult =
  | { kind: "ok"; view: TransactionView }
  | { kind: "not_found" }
  | { kind: "retryable"; status: number | null; detail: string }
  | { kind: "fatal"; status: number; detail: string }
  | { kind: "aborted" };

/** The API couldn't be reached. `status: null` never counts toward the poll loop's server-error streak. */
export const OFFLINE: FetchResult = { kind: "retryable", status: null, detail: "Can't reach the API" };

// Without a limit, one GET that never answers (a half-open connection after the laptop sleeps, or
// SQLite held by a live call's webhook) freezes the single-flight poll loop with no banner at all.
export const REQUEST_TIMEOUT_MS = 5000;
const TIMED_OUT: FetchResult = {
  kind: "retryable",
  status: null,
  detail: `The API didn't answer within ${REQUEST_TIMEOUT_MS / 1000} s`,
};

// The Vite dev proxy (web/vite.config.ts) and gateways answer 502/503/504 when the API behind
// them is down or restarting. That is "unreachable", not a server bug.
const GATEWAY_STATUSES = new Set([502, 503, 504]);

/**
 * The only function in web/ that calls fetch. A mock page never constructs the live source that
 * calls it, and src/boundary.test.ts fails if fetch appears anywhere else.
 */
export async function fetchTransaction(id: string, signal: AbortSignal): Promise<FetchResult> {
  const request = new AbortController();
  const abortRequest = () => request.abort();
  signal.addEventListener("abort", abortRequest);
  if (signal.aborted) request.abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    request.abort();
  }, REQUEST_TIMEOUT_MS);

  try {
    let response: Response;
    try {
      response = await fetch(`/transactions/${encodeURIComponent(id)}`, { signal: request.signal });
    } catch {
      if (signal.aborted) return { kind: "aborted" };
      return timedOut ? TIMED_OUT : OFFLINE;
    }

    const body = await readJson(response);
    if (signal.aborted) return { kind: "aborted" };
    if (timedOut) return TIMED_OUT;

    if (response.ok) {
      if (body.ok && looksLikeView(body.value)) return { kind: "ok", view: body.value };
      return {
        kind: "retryable",
        status: response.status,
        detail: "The API sent a response that isn't a transaction",
      };
    }
    if (response.status === 404) return { kind: "not_found" };
    if (GATEWAY_STATUSES.has(response.status)) return OFFLINE;

    const detail = normaliseDetail(body.ok ? body.value : undefined, response.status);
    if (response.status >= 500) return { kind: "retryable", status: response.status, detail };
    return { kind: "fatal", status: response.status, detail };
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abortRequest);
  }
}

/**
 * FastAPI's `detail` is a string for HTTPException but a list of `{loc, msg}` objects for request
 * validation (422). Rendering the list directly would crash React, so always hand back a string.
 */
export function normaliseDetail(body: unknown, status: number): string {
  const detail = isRecord(body) ? body.detail : undefined;
  if (typeof detail === "string" && detail.length > 0) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const messages = detail.map((item) =>
      isRecord(item) && typeof item.msg === "string" ? item.msg : null,
    );
    if (messages.every((message): message is string => message !== null)) {
      return messages.join("; ");
    }
  }
  return `Request failed (${status})`;
}

// Enough to know the page won't crash on it: an older backend without `terminal`, or HTML from a
// misrouted URL, is treated as a failed poll rather than rendered.
function looksLikeView(value: unknown): value is TransactionView {
  return (
    isRecord(value) &&
    typeof value.status === "string" &&
    typeof value.terminal === "boolean" &&
    Array.isArray(value.status_history) &&
    Array.isArray(value.audit)
  );
}

async function readJson(response: Response): Promise<{ ok: true; value: unknown } | { ok: false }> {
  try {
    return { ok: true, value: await response.json() };
  } catch {
    return { ok: false };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
