import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClientError } from "./client";

export interface QueryState<T> {
  data: T | null;
  error: ApiClientError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch-on-mount hook. Re-runs when any dep changes.
 * Returns null data while loading; errors surface the API error envelope.
 */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []): QueryState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiClientError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fnRef
      .current()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof ApiClientError ? e : new ApiClientError(0, { code: "network", message: String(e) }));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, ...deps]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

export interface MutationState<A, T> {
  mutate: (args: A) => Promise<T | null>;
  loading: boolean;
  error: ApiClientError | null;
  reset: () => void;
}

/** Mutation hook with loading/error state; returns null and sets error on failure. */
export function useMutation<A, T>(fn: (args: A) => Promise<T>): MutationState<A, T> {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiClientError | null>(null);

  const mutate = useCallback(
    async (args: A): Promise<T | null> => {
      setLoading(true);
      setError(null);
      try {
        return await fn(args);
      } catch (e: unknown) {
        setError(e instanceof ApiClientError ? e : new ApiClientError(0, { code: "network", message: String(e) }));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [fn],
  );

  const reset = useCallback(() => setError(null), []);
  return { mutate, loading, error, reset };
}
