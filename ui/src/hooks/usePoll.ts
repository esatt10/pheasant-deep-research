import { useCallback, useEffect, useRef, useState } from "react";
import { onAuthChanged } from "../api";

/**
 * Poll a loader on an interval; `interval` may depend on the last result.
 *
 * `loading` is true until the first answer (or error) arrives - what a page
 * shows a skeleton for. Later polls do not flip it back: a list that blanks
 * every four seconds to say it is refreshing is worse than one that is a
 * second stale. A new access key re-polls at once.
 */
export function usePoll<T>(
  load: () => Promise<T>,
  interval: number | ((data: T | undefined) => number),
  deps: unknown[] = [],
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const latest = useRef<T>();
  const refresh = useCallback(async () => {
    try {
      const value = await load();
      latest.current = value;
      setData(value);
      setError(null);
    } catch (caught) {
      setError(caught as Error);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => onAuthChanged(() => void refresh()), [refresh]);

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

  return { data, error, loading, refresh };
}
