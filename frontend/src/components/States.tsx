import type { ReactNode } from 'react';
import type { ApiState } from '../api/useApi';

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <div className="state" role="status" aria-live="polite"><span className="spinner" aria-hidden /> {label}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Couldn't load data.</strong>
      <span>{message}</span>
      {onRetry && <button type="button" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state state-empty">{children}</div>;
}

/** Renders loading/error for an ApiState and the children only on success. */
export function AsyncView<T>({ state, retry, children }: { state: ApiState<T>; retry: () => void; children: (data: T) => ReactNode }) {
  if (state.status === 'loading') return <Loading />;
  if (state.status === 'error') return <ErrorState message={state.error} onRetry={retry} />;
  return <>{children(state.data)}</>;
}
