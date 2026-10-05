import { useCallback, useEffect, useRef, useState } from "react";

/** Poll a loader on an interval; `interval` may depend on the last result. */
export function usePoll<T>(
  load: () => Promise<T>,
  interval: number | ((data: T | undefined) => number),
  deps: unknown[] = [],
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error | null>(null);
  const latest = useRef<T>();
  const refresh = useCallback(async () => {
    try {
      const value = await load();
      latest.current = value;
      setData(value);
      setError(null);
    } catch (caught) {
      setError(caught as Error);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      await refresh();
      if (cancelled) return;
      const wait = typeof interval === "function" ? interval(latest.current) : interval;
      timer = window.setTimeout(tick, wait);
    };
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  return { data, error, refresh };
}
