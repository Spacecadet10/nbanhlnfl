import { Component, type ReactNode } from 'react';
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

/** Keeps one broken page from blanking the whole app: the header and league switch stay usable and the reader gets a way
 * back. Keyed by the caller on the location, so navigating elsewhere clears the error. */
export class PageErrorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.error('Page failed to render', error); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}
