import { useCallback, useEffect, useState } from 'react';

export type ApiState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: string }
  | { status: 'success'; data: T };

/** Runs an API call whenever deps change; exposes a retry for error states. */
export function useApi<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<ApiState<T>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: 'loading' });
    fetcher(ctrl.signal).then(
      (data) => setState({ status: 'success', data }),
      (err: Error) => { if (err.name !== 'AbortError') setState({ status: 'error', error: err.message }); },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { state, retry };
}
