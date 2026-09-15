import { useEffect, useState } from "react";

/**
 * Re-render every `intervalMs` without fetching, so relative times ("last update 3 min ago") keep
 * advancing after polling stops at a terminal status (eng review D23). Read Date.now() at render time
 * rather than this value, so a fresh poll never shows a time computed from a stale tick.
 */
export function useTick(intervalMs: number): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((tick) => tick + 1), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
}
