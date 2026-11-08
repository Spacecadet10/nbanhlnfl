// The app's single API client. All screens fetch through here; there is no local fallback data.
import type { AutoresearchStatus } from './types';
import type { CandidateGameView } from './types';
import type { CanonicalHistory, CanonicalPerformance, PaperRules, RankedPicks, ThresholdOverview, CoverageReport, DailyEvidence, DailyReport, AdaptiveGame, CombinationAnalysis, RuleLedger, SubsetSearch, ThresholdExplorer, AdaptiveStatus, HistoricalStart,
  JointReport, ThresholdReport, AgreementPerformance, GameDetail, GamesResponse, HistoryResponse, MarketHistoryResponse, MarketId, MarketPerformanceResponse, Meta,
  PerformanceResponse, Policy, ResearchGame, ResearchHistory, ResearchReport, ResearchSlate, Retrospective, RuleReport, CandidateRoster, PoolReport, SelectionStatus, Sport,
  DataFreshness, Health, NflRecord, PolledLeague, SoccerCompetitionId, SoccerMatchDetail, SoccerMatchesResponse, SoccerMeta, SoccerLiveResponse,
  SoccerPerformance, SoccerResearchHistory, SoccerResearchMatchDetail, SoccerResearchMatches, SoccerResearchMeta, NflGameDetail, NflGamesResponse, NflMeta, TrainingRun, TrainingStatus, TrackingCheck, TrackingEvidence, TrackingGame, TrackingKind, TrackingReport,
} from './types';

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

async function get<T>(path: string, params?: Record<string, string | undefined>, signal?: AbortSignal): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v) query.set(k, v);
  const qs = query.toString();      // URLSearchParams.size is missing in older browsers (would drop every parameter)
  const url = `/api${path}${qs ? `?${qs}` : ''}`;
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
      // FastAPI validation errors: a list of {loc, msg}; show which parameter is wrong and why.
      else if (Array.isArray(body.detail)) detail = body.detail
        .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).filter((x) => x !== 'query').join('.') || 'request'}: ${d.msg ?? 'invalid'}`)
        .join('; ') || detail;
    } catch {
      // Non-JSON error body: typically the dev proxy failing to reach the backend.
      if (res.status >= 500) detail = 'API unavailable. Is the backend running?';
    }
    throw new ApiError(`${res.status}: ${detail}`, res.status);
  }
  return res.json() as Promise<T>;
}

// Every call names its sport explicitly; the API never substitutes another sport, market or policy.
const policyParam = (policy?: Policy) => (policy === 'near_tipoff' || policy === 'canonical' ? policy : undefined);
const confParam = (c?: number | null) => (c == null ? undefined : String(c));

export const api = {
  dailyEvidence: (sport: Sport, market: MarketId, rule: string, period: string, offset: number, signal?: AbortSignal) => get<DailyEvidence>('/research/daily-agreement/evidence', { sport, market, rule, period, offset: String(offset) }, signal),
  dailyAgreement: (sport: Sport, signal?: AbortSignal) => get<DailyReport>('/research/daily-agreement', { sport }, signal),
  health: (signal?: AbortSignal) => get<Health>('/health', undefined, signal),
  // Cheap freshness check polled while the page is visible (all three leagues; NFL through its data-only store).
  freshness: (league: PolledLeague, signal?: AbortSignal) => get<DataFreshness>('/freshness', { sport: league }, signal),
  // European soccer: data-only reads (fixtures, results, matchup facts) for one competition at a time; never a `sport`.
  soccerMeta: (competition: SoccerCompetitionId, signal?: AbortSignal) => get<SoccerMeta>('/soccer/meta', { competition }, signal),
  soccerLive: (competition: SoccerCompetitionId, signal?: AbortSignal) => get<SoccerLiveResponse>('/soccer/live', { competition }, signal),
  soccerMatches: (competition: SoccerCompetitionId, date: string, signal?: AbortSignal) =>
    get<SoccerMatchesResponse>('/soccer/matches', { competition, date }, signal),
  soccerMatch: (id: string, signal?: AbortSignal) => get<SoccerMatchDetail>(`/soccer/matches/${encodeURIComponent(id)}`, undefined, signal),
  // Soccer research: stored backtest forecasts of the openfootball history (retrospective, never Live).
  soccerResearchMeta: (competition: SoccerCompetitionId, signal?: AbortSignal) =>
    get<SoccerResearchMeta>('/soccer/research/meta', { competition }, signal),
  soccerResearchMatches: (competition: SoccerCompetitionId, date: string, signal?: AbortSignal) =>
    get<SoccerResearchMatches>('/soccer/research/matches', { competition, date }, signal),
  soccerResearchMatch: (id: string, signal?: AbortSignal) =>
    get<SoccerResearchMatchDetail>(`/soccer/research/matches/${encodeURIComponent(id)}`, undefined, signal),
  soccerResearchPerformance: (competition: SoccerCompetitionId, signal?: AbortSignal) =>
    get<SoccerPerformance>('/soccer/research/performance', { competition }, signal),
  soccerResearchHistory: (competition: SoccerCompetitionId, season: number | null, model: string | null, offset: number,
    signal?: AbortSignal) => get<SoccerResearchHistory>('/soccer/research/history',
    { competition, ...(season != null ? { season: String(season) } : {}), ...(model ? { model } : {}), offset: String(offset), limit: '100' }, signal),
  // NFL: data-only reads (schedule, results, matchup facts). Separate endpoints; NFL is never passed as `sport`.
  nflRecord: (signal?: AbortSignal) => get<NflRecord>('/nfl/record', undefined, signal),
  nflMeta: (signal?: AbortSignal) => get<NflMeta>('/nfl/meta', undefined, signal),
  nflGames: (date: string, signal?: AbortSignal) => get<NflGamesResponse>('/nfl/games', { date }, signal),
  nflGame: (id: string, signal?: AbortSignal) => get<NflGameDetail>(`/nfl/games/${encodeURIComponent(id)}`, undefined, signal),
  trainingRun: (sport: Sport, signal?: AbortSignal) => get<TrainingRun>('/training-run', { sport }, signal),
  recovered: (sport: Sport, signal?: AbortSignal) => get<{ note: string; status: string; groups: ThresholdReport['groups']; adaptive?: { status: string; records: { game_id: string; status: string }[]; unreconstructable: { reason: string; rows: number; games: number }[] }; records: { recovery_id: number; game_id: string; market: MarketId; tipoff_at: string; line: number | null; outcome: string }[] }>('/recovered-forecasts', { sport }, signal),
  jointTracking: (sport: Sport, market: MarketId, params: Record<string, string | undefined>, signal?: AbortSignal) =>
    get<JointReport>('/research/tracking/joint', { sport, market, ...params }, signal),

  // The canonical forecast (one record per game and market).
  canonicalHistory: (sport: Sport, market: MarketId, f: { date_from?: string; date_to?: string; team?: string; limit?: number }, signal?: AbortSignal) =>
    get<CanonicalHistory>('/canonical/forecasts', { sport, market, date_from: f.date_from, date_to: f.date_to, team: f.team,
      limit: f.limit == null ? undefined : String(f.limit) }, signal),
  canonicalPerformance: (sport: Sport, market: MarketId, signal?: AbortSignal) =>
    get<CanonicalPerformance>('/canonical/performance', { sport, market }, signal),
  thresholds: (sport: Sport, signal?: AbortSignal) => get<ThresholdReport>('/thresholds', { sport }, signal),
  canonicalThresholds: (sport: Sport, signal?: AbortSignal) => get<ThresholdOverview>('/canonical/thresholds', { sport }, signal),
  canonicalRanked: (sport: Sport, date: string, signal?: AbortSignal) => get<RankedPicks>('/canonical/ranked', { sport, date }, signal),
  paperRules: (sport: Sport, signal?: AbortSignal) => get<PaperRules>('/paper-rules', { sport }, signal),
  coverage: (sport: Sport, dateFrom: string | undefined, dateTo: string | undefined, signal?: AbortSignal) =>
    get<CoverageReport>('/coverage', { sport, date_from: dateFrom, date_to: dateTo }, signal),
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
    team?: string; min_confidence?: number | null; limit?: number; model_version?: string }, signal?: AbortSignal) =>
    get<MarketHistoryResponse>('/market-predictions', {
      sport, market, policy: policyParam(f.policy), date_from: f.date_from, date_to: f.date_to, team: f.team,
      model_version: f.model_version || undefined,
      min_confidence: confParam(f.min_confidence), limit: f.limit == null ? undefined : String(f.limit),
    }, signal),
  marketPerformance: (sport: Sport, market: MarketId, policy: Policy, signal?: AbortSignal, model_version?: string) =>
    get<MarketPerformanceResponse>('/market-performance', { sport, market, policy: policyParam(policy), model_version: model_version || undefined }, signal),
  // Research / shadow selection: never the primary forecast.
  researchGame: (sport: Sport, id: string, signal?: AbortSignal) =>
    get<ResearchGame>(`/research/game/${encodeURIComponent(id)}`, { sport }, signal),
  researchSlate: (sport: Sport, date: string, signal?: AbortSignal) => get<ResearchSlate>('/research/slate', { sport, date }, signal),
  researchPerformance: (sport: Sport, market: MarketId, signal?: AbortSignal) =>
    get<ResearchReport>('/research/performance', { sport, market }, signal),
  researchRules: (sport: Sport, market: MarketId, show: 'selected' | 'all', signal?: AbortSignal, definition?: string) =>
    get<RuleReport>('/research/rules', { sport, market, show, definition: definition || undefined }, signal),
  researchCandidates: (sport: Sport, signal?: AbortSignal) => get<CandidateRoster>('/research/candidates', { sport }, signal),
  researchHistoricalStart: (sport: Sport, market: MarketId, signal?: AbortSignal) =>
    get<HistoricalStart>('/research/historical-start', { sport, market }, signal),
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
  researchCombinations: (sport: Sport, market: MarketId, base: string[], signal?: AbortSignal) =>
    get<CombinationAnalysis>('/research/combinations', { sport, market, base: base.join(',') || undefined }, signal),
  researchThresholds: (sport: Sport, market: MarketId, f: { source: string; group?: string[]; base?: string[]; rule?: string },
    signal?: AbortSignal) =>
    get<ThresholdExplorer>('/research/thresholds', { sport, market, source: f.source, group: f.group?.join(',') || undefined,
      base: f.base?.join(',') || undefined, rule: f.rule || undefined }, signal),
  researchRuleLedger: (sport: Sport, market: MarketId, source: string, rule: string, period: string, signal?: AbortSignal) =>
    get<RuleLedger>('/research/thresholds/ledger', { sport, market, source, rule, period }, signal),
  /** Same ledger as a CSV download (a plain link: the browser saves the file). */
  researchRuleLedgerCsvUrl: (sport: Sport, market: MarketId, source: string, rule: string, period: string) =>
    `/api/research/thresholds/ledger?${new URLSearchParams({ sport, market, source, rule, period, format: 'csv' })}`,
  researchSubsets: (sport: Sport, market: MarketId, source: string, signal?: AbortSignal) =>
    get<SubsetSearch>('/research/subsets', { sport, market, source }, signal),
  // adaptive-v1: separately versioned adaptive combination (research), never the primary forecast.
  adaptiveStatus: (sport: Sport, signal?: AbortSignal) => get<AdaptiveStatus>('/research/adaptive', { sport }, signal),
  adaptiveGame: (sport: Sport, id: string, signal?: AbortSignal) =>
    get<AdaptiveGame>(`/research/adaptive/game/${encodeURIComponent(id)}`, { sport }, signal),
  // Sports autoresearch: experiment directory status (research only; never a forecast source).
  autoresearch: (signal?: AbortSignal) => get<AutoresearchStatus>('/research/autoresearch', undefined, signal),
  // Every candidate model's recorded pregame forecasts for one game (research only; never canonical, never a vote).
  candidatesGame: (sport: Sport, id: string, signal?: AbortSignal) =>
    get<CandidateGameView>(`/research/candidates/game/${encodeURIComponent(id)}`, { sport }, signal),
};
