import { useCallback, useEffect, useRef, useState } from 'react';

import { getCached } from './api';

export interface ApiState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** Set when the data came from the device cache because the server was unreachable. */
  cachedAt: string | null;
  reload: () => void;
}

/** Fetch a GET endpoint; `null` skips the request. Keeps showing old data while reloading. */
export function useApi<T>(path: string | null): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const current = useRef(path);

  useEffect(() => {
    current.current = path;
    if (!path) {
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    getCached<T>(path)
      .then((r) => {
        if (!alive || current.current !== path) return;
        setData(r.data);
        setCachedAt(r.cachedAt);
        setError(null);
      })
      .catch((e: Error) => {
        if (!alive || current.current !== path) return;
        setError(e);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, cachedAt, reload };
}
