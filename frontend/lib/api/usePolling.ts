"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "./client";

interface PollingState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
}

/**
 * Опрос ресурса с ограниченной частотой. Останавливается, когда `isDone(data)` истинно
 * или после ошибки 404 (ресурс не существует). Интервал растёт с 1,5 до 5 секунд.
 */
export function usePolling<T>(
  fetcher: (() => Promise<T>) | null,
  isDone: (data: T) => boolean,
  deps: unknown[] = [],
): PollingState<T> & { refresh: () => void } {
  const [state, setState] = useState<PollingState<T>>({ data: null, error: null, loading: Boolean(fetcher) });
  const [tick, setTick] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!fetcher) return;
    let cancelled = false;
    let attempt = 0;

    const run = async () => {
      try {
        const data = await fetcher();
        if (cancelled) return;
        setState({ data, error: null, loading: false });
        if (isDone(data)) return;
      } catch (e) {
        if (cancelled) return;
        const error = e instanceof Error ? e : new Error(String(e));
        setState((s) => ({ ...s, error, loading: false }));
        if (e instanceof ApiError && (e.status === 404 || e.status === 410)) return;
      }
      attempt += 1;
      const delay = Math.min(1500 + attempt * 500, 5000);
      timer.current = setTimeout(run, delay);
    };

    void run();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  if (!fetcher) return { data: null, error: null, loading: false, refresh: () => setTick((t) => t + 1) };
  return { ...state, refresh: () => setTick((t) => t + 1) };
}
