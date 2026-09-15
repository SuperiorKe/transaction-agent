export type Source =
  | { kind: "mock"; fixture: string }
  | { kind: "live"; txId: string }
  | { kind: "empty" };

/**
 * Decide the page's data source once, from the URL (eng review D5, D24). `mock` wins whenever it
 * is present, even empty, unknown, or next to `tx`: a mistyped mock URL must never fall through to
 * a live session that can reach real phones.
 */
export function parseSource(search: string): Source {
  const params = new URLSearchParams(search);
  if (params.has("mock")) return { kind: "mock", fixture: params.get("mock") ?? "" };
  const txId = params.get("tx");
  if (txId) return { kind: "live", txId };
  return { kind: "empty" };
}
