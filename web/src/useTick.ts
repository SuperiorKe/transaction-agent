import { useEffect, useState } from "react";

/** How often relative times ("last update 3 min ago") re-render without new data. */
export const RELATIVE_TIME_TICK_MS = 15_000;

/**
 * Re-render every `intervalMs` without fetching, so relative times keep advancing after polling
 * stops at a terminal status. Read Date.now() at render time rather than this value, so a fresh poll
 * never shows a time computed from a stale tick.
 */
export function useTick(intervalMs: number): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((tick) => tick + 1), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
}
