import { useSyncExternalStore } from 'react';

/** Background refetches (useApi) that failed and are being retried, so the freshness bar can say so: a page keeps its
 *  last data on screen during the retries, and this is how that is made visible instead of silent. */
const problems = new Map<symbol, string>();
const listeners = new Set<() => void>();
let snapshot: string[] = [];

export function reportRefreshProblem(id: symbol, message: string | null): void {
  const had = problems.get(id);
  if (message === null) { if (had === undefined) return; problems.delete(id); }
  else { if (had === message) return; problems.set(id, message); }
  snapshot = [...problems.values()];
  listeners.forEach((l) => l());
}

export function useRefreshProblems(): string[] {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => { listeners.delete(l); }; },
    () => snapshot,
  );
}
