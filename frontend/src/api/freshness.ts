import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { api } from './client';
import type { DataFreshness, League } from './types';

/** How often the visible page asks /api/freshness whether anything changed (docs/REFRESH_PLAN.md). */
export const FRESHNESS_POLL_MS = 30_000;

export interface FreshnessCheck {
  data: DataFreshness | null;
  error: string | null;
  checking: boolean;
  paused: boolean;        // hidden tab or offline: no requests until the page is visible and online again
  /** Number of data revisions seen since this league was selected, minus one: 0 until the data actually changes. Pages pass
   *  it to useApi as `refreshKey`, so the first check never causes a second load of what was just fetched. */
  changes: number;
}

const IDLE: FreshnessCheck = { data: null, error: null, checking: false, paused: false, changes: 0 };

/** One cheap freshness request per selected league every FRESHNESS_POLL_MS while the page is visible and online; paused
 *  otherwise, with an immediate check on return. Requests never overlap; a league switch aborts the previous league's request
 *  so its answer can never be shown for the new one. */
export function useFreshnessPoll(league: League): FreshnessCheck {
  const [check, setCheck] = useState<FreshnessCheck>(IDLE);
  const seen = useRef<{ league: League; revision: string | null; changes: number }>({ league, revision: null, changes: 0 });
  useEffect(() => {
    seen.current = { league, revision: null, changes: 0 };
    setCheck(IDLE);
    if (league === 'soccer') return undefined;   // no soccer refresh job or freshness summary: nothing to poll
    const polled = league;
    let stopped = false;
    let timer: number | undefined;
    let inflight: AbortController | null = null;
    const canRun = () => document.visibilityState === 'visible' && navigator.onLine !== false;
    const schedule = () => {
      window.clearTimeout(timer);
      if (!stopped && canRun()) timer = window.setTimeout(run, FRESHNESS_POLL_MS);
    };
    const run = () => {
      if (stopped || inflight) return;
      if (!canRun()) { setCheck((c) => ({ ...c, paused: true })); return; }
      const ctrl = new AbortController();
      inflight = ctrl;
      setCheck((c) => ({ ...c, checking: true, paused: false }));
      api.freshness(polled, ctrl.signal).then(
        (data) => {
          if (ctrl.signal.aborted) return;
          const s = seen.current;
          if (s.revision !== null && s.revision !== data.revision) s.changes += 1;
          s.revision = data.revision;
          setCheck({ data, error: null, checking: false, paused: false, changes: s.changes });
        },
        (err: Error) => {
          if (ctrl.signal.aborted || err.name === 'AbortError') return;
          setCheck((c) => ({ ...c, error: err.message, checking: false }));
        },
      ).finally(() => {
        if (inflight === ctrl) inflight = null;
        schedule();
      });
    };
    const wake = () => {
      window.clearTimeout(timer);
      if (canRun()) run();
      else setCheck((c) => ({ ...c, paused: true }));
    };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);
    window.addEventListener('offline', wake);
    run();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      inflight?.abort();
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('online', wake);
      window.removeEventListener('offline', wake);
    };
  }, [league]);
  return check;
}

export const FreshnessContext = createContext<FreshnessCheck>(IDLE);

/** The refresh key for useApi on views of games and forecasts: changes only when the stored data changed. */
export function useDataRevision(): number {
  return useContext(FreshnessContext).changes;
}
