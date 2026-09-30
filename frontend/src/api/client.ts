// The app's single API client. All screens fetch through here; there is no local fallback data.
import type { GameDetail, GamesResponse, HistoryResponse, Meta, PerformanceResponse } from './types';

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

async function get<T>(path: string, params?: Record<string, string | undefined>, signal?: AbortSignal): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v) query.set(k, v);
  const url = `/api${path}${query.size ? `?${query}` : ''}`;
  let res: Response;
  try {
    res = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError('Cannot reach the API. Is the backend running?');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Non-JSON error body: typically the dev proxy failing to reach the backend.
      if (res.status >= 500) detail = 'API unavailable. Is the backend running?';
    }
    throw new ApiError(`${res.status}: ${detail}`, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  meta: (signal?: AbortSignal) => get<Meta>('/meta', undefined, signal),
  games: (date: string, signal?: AbortSignal) => get<GamesResponse>('/games', { date }, signal),
  game: (id: string, signal?: AbortSignal) => get<GameDetail>(`/games/${encodeURIComponent(id)}`, undefined, signal),
  predictions: (f: { run_type?: 'live' | 'backtest'; date_from?: string; date_to?: string; team?: string },
    signal?: AbortSignal) => get<HistoryResponse>('/predictions', f, signal),
  performance: (runType: 'live' | 'backtest', signal?: AbortSignal) =>
    get<PerformanceResponse>('/performance', { run_type: runType }, signal),
};
