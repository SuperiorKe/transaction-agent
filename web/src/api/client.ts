import type { components } from "./openapi.gen";

export type TransactionView = components["schemas"]["TransactionView"];

// Every HTTP outcome, reduced to what the poll loop needs to decide (eng review D4, D16, D19, D21).
export type FetchResult =
  | { kind: "ok"; view: TransactionView }
  | { kind: "not_found" }
  | { kind: "retryable"; status: number | null; detail: string }
  | { kind: "fatal"; status: number; detail: string }
  | { kind: "aborted" };

/**
 * The only function in web/ that calls fetch (D5). A mock page never constructs the live source that
 * calls it, and src/boundary.test.ts fails if fetch appears anywhere else.
 */
export async function fetchTransaction(id: string, signal: AbortSignal): Promise<FetchResult> {
  let response: Response;
  try {
    response = await fetch(`/transactions/${encodeURIComponent(id)}`, { signal });
  } catch (error) {
    if (isAbort(error, signal)) return { kind: "aborted" };
    return { kind: "retryable", status: null, detail: "Can't reach the API" };
  }

  const body = await readJson(response);
  if (signal.aborted) return { kind: "aborted" };

  if (response.ok) {
    if (body.ok) return { kind: "ok", view: body.value as TransactionView };
    return { kind: "retryable", status: response.status, detail: "The API sent a response that isn't JSON" };
  }
  if (response.status === 404) return { kind: "not_found" };

  const detail = normaliseDetail(body.ok ? body.value : undefined, response.status);
  if (response.status >= 500) return { kind: "retryable", status: response.status, detail };
  return { kind: "fatal", status: response.status, detail };
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

async function readJson(response: Response): Promise<{ ok: true; value: unknown } | { ok: false }> {
  try {
    return { ok: true, value: await response.json() };
  } catch {
    return { ok: false };
  }
}

function isAbort(error: unknown, signal: AbortSignal): boolean {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
