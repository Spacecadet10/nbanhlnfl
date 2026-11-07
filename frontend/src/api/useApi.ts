import { useCallback, useEffect, useRef, useState } from 'react';
import { reportRefreshProblem } from './refreshProblems';

export type ApiState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: string }
  | { status: 'success'; data: T };

/** First wait before a failed background refetch is retried; doubles per failure up to REFRESH_RETRY_MAX_MS. */
export const REFRESH_RETRY_MS = 30_000;
export const REFRESH_RETRY_MAX_MS = 120_000;

/** Runs an API call whenever deps change; exposes a retry for error states. A change of `refreshKey` alone (the data
 *  revision from the freshness check) refetches quietly: the current data stays on screen and `refreshing` is true
 *  meanwhile. A failed background refetch also keeps the data, but is never silent: `refreshError` is set, the problem is
 *  reported to the freshness bar (refreshProblems), and the same request is retried with backoff until it succeeds — so a
 *  transient failure cannot leave old data on screen until some later revision happens to arrive. Each request is aborted
 *  when a newer one starts, and an aborted response never replaces newer state. */
export function useApi<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[], refreshKey?: unknown) {
  const [state, setState] = useState<ApiState<T>>({ status: 'loading' });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [quietRetry, setQuietRetry] = useState(0);
  const current = useRef(state);
  current.current = state;
  const lastDeps = useRef<unknown[] | null>(null);
  const lastAttempt = useRef(attempt);
  const failures = useRef(0);
  const id = useRef<symbol>(Symbol('useApi'));
  useEffect(() => () => reportRefreshProblem(id.current, null), []);
  useEffect(() => {
    const ctrl = new AbortController();
    let retryTimer: number | undefined;
    const prev = lastDeps.current;
    const sameDeps = prev !== null && prev.length === deps.length && prev.every((v, i) => Object.is(v, deps[i]));
    const quiet = sameDeps && lastAttempt.current === attempt && current.current.status === 'success';
    lastDeps.current = deps;
    lastAttempt.current = attempt;
    if (quiet) setRefreshing(true);
    else {
      setRefreshing(false); setState({ status: 'loading' });
      setRefreshError(null); failures.current = 0; reportRefreshProblem(id.current, null);
    }
    fetcher(ctrl.signal).then(
      (data) => {
        if (ctrl.signal.aborted) return;
        setState({ status: 'success', data }); setRefreshing(false);
        setRefreshError(null); failures.current = 0; reportRefreshProblem(id.current, null);
      },
      (err: Error) => {
        if (ctrl.signal.aborted || err.name === 'AbortError') return;
        setRefreshing(false);
        if (!quiet) { setState({ status: 'error', error: err.message }); return; }
        failures.current += 1;
        const wait = Math.min(REFRESH_RETRY_MS * 2 ** (failures.current - 1), REFRESH_RETRY_MAX_MS);
        setRefreshError(err.message);
        reportRefreshProblem(id.current, err.message);
        retryTimer = window.setTimeout(() => setQuietRetry((n) => n + 1), wait);
      },
    );
    return () => { ctrl.abort(); window.clearTimeout(retryTimer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt, refreshKey, quietRetry]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { state, retry, refreshing, refreshError };
}
