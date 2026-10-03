// The app's single API client. All screens fetch through here; there is no local fallback data.
import type {
  ThresholdReport, AgreementPerformance, GameDetail, GamesResponse, HistoryResponse, MarketHistoryResponse, MarketId, MarketPerformanceResponse, Meta,
  PerformanceResponse, Policy, ResearchGame, ResearchHistory, ResearchReport, ResearchSlate, Retrospective, RuleReport, CandidateRoster, PoolReport, SelectionStatus, Sport,
  TrainingStatus, TrackingCheck, TrackingEvidence, TrackingGame, TrackingKind, TrackingReport,
} from './types';

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

// Every call names its sport explicitly; the API never substitutes another sport, market or policy.
const policyParam = (policy?: Policy) => (policy === 'near_tipoff' ? 'near_tipoff' : undefined);
const confParam = (c?: number | null) => (c == null ? undefined : String(c));

export const api = {
  thresholds: (sport: Sport, signal?: AbortSignal) => get<ThresholdReport>('/thresholds', { sport }, signal),
  agreementPerformance: (sport: Sport, signal?: AbortSignal) => get<AgreementPerformance>('/agreement-performance', { sport }, signal),
  meta: (sport: Sport, signal?: AbortSignal) => get<Meta>('/meta', { sport }, signal),
  games: (sport: Sport, date: string, policy: Policy, minConfidence: number | null, market: MarketId, signal?: AbortSignal) =>
    get<GamesResponse>('/games', {
      sport, date, policy: policyParam(policy), min_confidence: confParam(minConfidence),
      market: market === 'winner' ? undefined : market,
    }, signal),
  game: (sport: Sport, id: string, signal?: AbortSignal) =>
    get<GameDetail>(`/games/${encodeURIComponent(id)}`, { sport }, signal),
  predictions: (sport: Sport, f: { run_type?: 'live' | 'backtest'; date_from?: string; date_to?: string; team?: string;
    min_confidence?: number | null; limit?: number; policy?: Policy }, signal?: AbortSignal) =>
    get<HistoryResponse>('/predictions', {
      sport, run_type: f.run_type, date_from: f.date_from, date_to: f.date_to, team: f.team,
      policy: policyParam(f.policy), min_confidence: confParam(f.min_confidence),
      limit: f.limit == null ? undefined : String(f.limit),
    }, signal),
  performance: (sport: Sport, runType: 'live' | 'backtest', policy: Policy, signal?: AbortSignal) =>
    get<PerformanceResponse>('/performance', {
      sport, run_type: runType, policy: runType === 'live' ? policyParam(policy) : undefined,
    }, signal),
  marketPredictions: (sport: Sport, market: MarketId, f: { policy?: Policy; date_from?: string; date_to?: string;
    team?: string; min_confidence?: number | null; limit?: number }, signal?: AbortSignal) =>
    get<MarketHistoryResponse>('/market-predictions', {
      sport, market, policy: policyParam(f.policy), date_from: f.date_from, date_to: f.date_to, team: f.team,
      min_confidence: confParam(f.min_confidence), limit: f.limit == null ? undefined : String(f.limit),
    }, signal),
  marketPerformance: (sport: Sport, market: MarketId, policy: Policy, signal?: AbortSignal) =>
    get<MarketPerformanceResponse>('/market-performance', { sport, market, policy: policyParam(policy) }, signal),
  // Research / shadow selection: never the primary forecast.
  researchGame: (sport: Sport, id: string, signal?: AbortSignal) =>
    get<ResearchGame>(`/research/game/${encodeURIComponent(id)}`, { sport }, signal),
  researchSlate: (sport: Sport, date: string, signal?: AbortSignal) => get<ResearchSlate>('/research/slate', { sport, date }, signal),
  researchPerformance: (sport: Sport, market: MarketId, signal?: AbortSignal) =>
    get<ResearchReport>('/research/performance', { sport, market }, signal),
  researchRules: (sport: Sport, market: MarketId, show: 'selected' | 'all', signal?: AbortSignal) =>
    get<RuleReport>('/research/rules', { sport, market, show }, signal),
  researchCandidates: (sport: Sport, signal?: AbortSignal) => get<CandidateRoster>('/research/candidates', { sport }, signal),
  researchPool: (sport: Sport, signal?: AbortSignal) => get<PoolReport>('/research/pool', { sport }, signal),
  researchTracking: (sport: Sport, market: MarketId, f: { evidence: TrackingEvidence; kind?: TrackingKind | ''; member?: string;
    line?: string; date_from?: string; date_to?: string; version?: string }, signal?: AbortSignal) =>
    get<TrackingReport>('/research/tracking', { sport, market, evidence: f.evidence, kind: f.kind || undefined, member: f.member,
      line: f.line, date_from: f.date_from, date_to: f.date_to, version: f.version }, signal),
  researchTrackingCheck: (sport: Sport, market: MarketId, signal?: AbortSignal) =>
    get<TrackingCheck>('/research/tracking/check', { sport, market }, signal),
  researchTrackingGame: (sport: Sport, id: string, signal?: AbortSignal) =>
    get<TrackingGame>(`/research/tracking/game/${encodeURIComponent(id)}`, { sport }, signal),
  researchTraining: (sport: Sport, signal?: AbortSignal) => get<TrainingStatus>('/research/training', { sport }, signal),
  researchStatus: (sport: Sport, signal?: AbortSignal) => get<SelectionStatus>('/research/status', { sport }, signal),
  researchHistory: (sport: Sport, signal?: AbortSignal) => get<ResearchHistory>('/research/history', { sport }, signal),
  researchRetrospective: (sport: Sport, signal?: AbortSignal) => get<Retrospective>('/research/retrospective', { sport }, signal),
};
