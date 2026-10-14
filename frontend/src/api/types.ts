// Mirrors backend/app/schemas.py (contract v3: sport and market selection). Keep in sync with the API.
// `mode`: where data comes from (real = stored ESPN data + ledger; demo = synthetic fixtures).
// Prediction `run_type`: live (recorded before cutoff), backtest (retrospective) or demo.
export type RunType = 'demo' | 'backtest' | 'live';
export type Mode = 'real' | 'demo';

export interface Team { abbr: string; name: string }

export type Sport = 'nba' | 'nhl';
export type MarketId = 'winner' | 'reg3' | 'spread' | 'total' | 'team_total_home' | 'team_total_away';
export interface SportInfo { id: Sport; label: string; available: boolean; note: string | null }
export interface MarketInfo { id: MarketId; title: string; available: boolean; note: string }

export interface SourceStatus {
  name: string;
  kind: 'schedule' | 'results' | 'odds' | 'forecasts' | 'injuries' | 'players' | 'canonical';
  mode: Mode;
  last_updated: string | null;
  freshness: 'fresh' | 'stale' | 'missing';
  note: string;
}

export interface Meta {
  mode: Mode;
  sport: Sport;
  sports: SportInfo[];
  markets: MarketInfo[];
  score_unit: 'points' | 'goals';
  default_date: string;
  available_dates: string[];
  teams: Team[];
  primary_model: string;
  last_refresh_at: string | null;
}

export interface Prediction {
  model_version: string; run_type: RunType; generated_at: string; cutoff_at: string; home_win_prob: number;
}
export interface Market {
  bookmaker: string; price_label: 'open' | 'current'; quote_time: string | null; retrieved_at: string; home_win_prob: number;
  home_price?: number | null; away_price?: number | null;
}
export interface Outcome { home_score: number; away_score: number; home_won: boolean }

/** canonical = one record per game (game_forecasts); pregame and near_tipoff are the legacy ledgers. */
export type Policy = 'pregame' | 'near_tipoff' | 'canonical';

/** The selected game_forecasts row: the one record every page shows and grades for this game and market. */
export interface CanonicalRecord {
  forecast_id: number; source: 'adaptive' | 'primary_recorded' | 'primary_direct'; fallback_rank: number;
  fallback_reason: string | null; model_version: string; generated_at: string; start_at: string; minutes_before_start: number;
  p: number[]; home_win_prob: number | null; pick: 'home' | 'away' | null; confidence: number; line: number | null;
  line_source: 'bookmaker' | 'model' | 'none';
  /** The picked side (three-way: a draw can be the pick) and the pick in words at the forecast's own line. */
  side: 'a' | 'mid' | 'b'; pick_label: string | null; market_quote_id: number | null;
}
export type CanonicalState = 'forecast' | 'missing' | 'upcoming' | 'unvalidated' | 'not_recorded' | 'not_forecastable';
export type CanonicalGrade = 'win' | 'loss' | 'push' | 'pending' | 'void' | 'unscorable' | 'unresolved';
export interface CanonicalForecast {
  market: MarketId; state: CanonicalState; reason: string | null; record: CanonicalRecord | null; grade: CanonicalGrade | null;
  result_side?: 'a' | 'mid' | 'b' | null; result_label?: string | null;
  /** The grade under each recorded final result, oldest first (a score correction adds an entry); the last is `grade`. */
  grade_history?: { revision: number; observed_at: string; basis: 'observed' | 'backfill'; change: 'first' | 'decision_known' | 'correction'; home_score: number | null;
    away_score: number | null; decision: string | null; grade: CanonicalGrade }[];
  regraded?: boolean;
  later_failure: { forecast_id: number; generated_at: string; reason: string } | null; attempts: number;
  recording_began_at: string | null;
}
export interface CanonicalSummary {
  games: number; forecast: number; missing: number; coverage: number | null; grades: Record<CanonicalGrade, number>; graded: number;
  accuracy: number | null; brier_score: number | null; log_loss: number | null; by_source: Record<string, number>;
  /** Forecasts whose grade a corrected result changed. */
  regraded?: number;
  model_versions: { model_version: string; forecasts: number }[]; missing_reasons: { reason: string; games: number }[];
  /** The market's reporting grid (two-way 50–90%, regulation three-way 35–70%): a filter of the record, never of recording. */
  thresholds: CanonicalThreshold[];
}
export interface CanonicalThreshold {
  threshold: number; forecasts: number; graded: number; win: number; loss: number; push: number; pending: number; void: number;
  unscorable: number; unresolved: number; win_rate: number | null;
}
export interface CanonicalRow {
  game_id: string; game_date: string; tipoff: string; status: GameSummary['status']; season_type: GameSummary['season_type'];
  home: Team; away: Team; outcome: Outcome | null; canonical: CanonicalForecast;
}
export interface CanonicalHistory {
  mode: Mode; market: MarketId; as_of: string; recording_began_at: string | null; total: number; record: CanonicalSummary;
  preseason_games: number; rows: CanonicalRow[];
}
export interface CanonicalPerformance {
  mode: Mode; market: MarketId; title: string; note: string; as_of: string; recording_began_at: string | null;
  date_from: string | null; date_to: string | null; competitive: CanonicalSummary; calibration: CalibrationBin[];
  market_comparison: { matched_games: number; coverage: number; model_brier: number | null; market_brier: number | null;
    /** Non-winner markets: graded forecasts without a valid bookmaker comparison, by reason. */
    unmatched?: Record<string, number> };
  preseason: { games: number; unvalidated: number; experimental_rows: number; note: string };
}

/** A separate forecast made shortly before tipoff from the freshest stored results. Never replaces `prediction`. */
export interface NearTipoffForecast {
  model_version: string; generated_at: string; cutoff_at: string; home_win_prob: number;
  pick: 'home' | 'away'; confidence: number; minutes_before_tipoff: number;
  results_refreshed_at: string | null; latest_result_available_at: string | null; results_used: number | null;
  market_home_prob: number | null; market_retrieved_at: string | null; correct: boolean | null; void?: boolean; limitation: string;
}

export interface SlatePick { policy: Policy; home_win_prob: number; pick: 'home' | 'away'; confidence: number; meets_confidence: boolean }
export interface SlateSelection { market: MarketId; policy: Policy; min_confidence: number | null; games: number; with_forecast: number; selected: number;
  /** Canonical policy only: forecasts below the confidence level (the only games the filter hides). */
  below_threshold?: number | null;
  /** Canonical policy only: games without a canonical forecast, by state; never hidden by the filter. */
  without_forecast?: Partial<Record<CanonicalState, number>> | null }

export interface AgreementSummary {
  policy: Policy;
  status: 'unavailable' | 'partial' | 'complete'; expected: number; available: number;
  agreeing: number; pick: 'home' | 'away' | null; home_votes: number; away_votes: number;
  abstentions: number; missing: string[]; generated_at: string | null;
  models: { name: string; model_version: string; home_win_prob: number; generated_at: string }[];
  void?: boolean; void_reason?: string | null;
}

export interface GameSummary {
  agreement?: AgreementSummary | null;
  game_id: string;
  mode: Mode;
  game_date: string;
  tipoff: string;
  status: 'scheduled' | 'in_progress' | 'final' | 'postponed' | 'canceled' | 'other';
  season_type: 'preseason' | 'regular' | 'play_in' | 'postseason' | 'other';
  home: Team;
  away: Team;
  prediction: Prediction | null;
  forecast_note: string | null;
  market: Market | null;
  outcome: Outcome | null;
  near_tipoff: NearTipoffForecast | null;
  /** The pick under the slate's selected forecast policy; null when the game has no forecast under it. */
  slate_pick: SlatePick | null;
  /** The selected non-winner market's forecast under the selected policy, if any. */
  market_forecast: MarketForecast | null;
  /** The canonical forecast (real mode; null in demo): the winner's, or the slate market's under the canonical policy. */
  canonical?: CanonicalForecast | null;
}

export interface GamesResponse { mode: Mode; date: string; selection: SlateSelection; games: GameSummary[]; sources: SourceStatus[] }

export interface TeamStats {
  wins: number; losses: number; last10_wins: number; last10_games: number;
  points_for_avg: number | null; points_against_avg: number | null; rest_days: number | null;
}

export interface GameDetail extends GameSummary {
  stats_as_of: string;
  home_stats: TeamStats;
  away_stats: TeamStats;
  projected_score_available: boolean;
  market_forecasts: MarketForecast[];
  /** The canonical forecast of every market of the sport, winner first (real mode). */
  canonical_markets?: CanonicalForecast[];
  /** NHL only: ESPN's latest probable-goalie listing per team, as retrieved; not used by any model. */
  goalies: { side: 'home' | 'away'; player_name: string | null; source_status: string; source_status_label: string | null; retrieved_at: string }[];
}

export type Settlement = 'win' | 'loss' | 'push' | 'void' | 'pending' | 'unscorable';
export interface MarketSide { side: 'a' | 'mid' | 'b'; label: string; probability: number }
export interface MarketQuote { bookmaker: string; retrieved_at: string; line: number | null; price_a: number; price_b: number; prob_a: number; prob_b: number }
/** One forecast at one exact line. line_source 'model' = no bookmaker line existed; the line is model-only. */
export interface MarketForecast {
  market: MarketId; title: string; policy: Policy; model_version: string; current_version?: boolean; generated_at: string; cutoff_at: string;
  line: number | null; line_source: 'market' | 'model' | null; earlier_line_rule?: boolean; sides: MarketSide[]; pick: 'a' | 'b'; pick_label: string;
  confidence: number; meets_confidence: boolean; quote: MarketQuote | null; result: Settlement; limitations: string[];
}
export interface MarketRecordSummary {
  min_confidence: number | null; selected: number; settled: number; wins: number; losses: number; pushes: number;
  voids: number; pending: number; unscorable: number; win_rate: number | null;
  quoted_decided?: number; quoted_wins?: number; mean_implied?: number | null;
}
export interface MarketRecord { game_id: string; game_date: string; tipoff: string; home: Team; away: Team; forecast: MarketForecast; outcome: Outcome | null;
  lead_days?: number | null; horizon_hours?: number | null }
export interface MarketModelVersion { model_version: string; current: boolean; games: number; first_at: string; last_at: string }
export interface MarketHistoryResponse {
  mode: Mode; sport: Sport; market: MarketId; policy: Policy; model_version?: string | null; versions?: MarketModelVersion[]; total: number; record: MarketRecordSummary; predictions: MarketRecord[];
  parts?: { key: string; label: string; record: MarketRecordSummary }[];
}
export interface MarketPerformanceResponse {
  mode: Mode; sport: Sport; market: MarketId; policy: Policy; title: string; model_version: string | null; versions?: MarketModelVersion[]; forecasts: number;
  record: MarketRecordSummary; brier_score: number | null; log_loss: number | null; line_sources: Record<string, number>;
  market_matched: number; model_log_loss_on_matched: number | null; market_log_loss_on_matched: number | null;
  reference: { label: string; tested_model?: string; matches_live_model?: boolean; model_note?: string | null; by_line: Record<string, { n: number; log_loss: number; brier: number; pick_accuracy_excluding_mid: number | null; league_reference_log_loss: number }> } | null;
  limitations: string[]; note: string;
}

export interface PredictionRecord {
  game_id: string; run_type: RunType; game_date: string; tipoff: string; home: Team; away: Team;
  model_version: string; generated_at: string; cutoff_at: string; home_win_prob: number;
  /** The predicted winner and the model's probability for it: max(p_home, 1 - p_home). */
  pick: 'home' | 'away'; confidence: number;
  outcome: Outcome | null; correct: boolean | null; void?: boolean; unscorable?: boolean;
  lead_days?: number | null; horizon_hours?: number | null; period?: string | null;
}

/** Observed record of the picks a confidence filter selects. Unscored games never count toward win_rate. */
/** selected = settled + pending + void + unscorable; settled = wins + losses. */
export interface ConfidenceRecord {
  min_confidence: number | null; selected: number; settled: number; wins: number; losses: number;
  pending: number; void: number; unscorable: number; win_rate: number | null;
}

export interface HistoryResponse {
  mode: Mode; run_type: RunType; policy: Policy; total: number; record: ConfidenceRecord; predictions: PredictionRecord[];
  /** The same record split by lead time (pre-game archive) or evaluation period (backtest); the parts add up to it. */
  parts?: { key: string; label: string; record: ConfidenceRecord }[];
}

export interface CalibrationBin {
  lower: number; upper: number; count: number;
  mean_predicted: number | null; observed_rate: number | null;
}

export interface BaselineScore { name: string; accuracy: number | null; brier_score: number | null; log_loss: number | null }

export interface PerformanceResponse {
  mode: Mode;
  run_type: RunType;
  policy: Policy;
  title: string;
  model_version: string;
  sample_size: number;
  date_from: string | null;
  date_to: string | null;
  accuracy: number | null;
  brier_score: number | null;
  log_loss: number | null;
  calibration: CalibrationBin[];
  baselines: BaselineScore[];
  market: { matched_games: number; coverage: number; model_brier: number | null; market_brier: number | null };
  note: string;
}

export interface AgreementPerformance {
  partial_games: number; note: string;
  groups: { definition: string; games: number; pending: number; excluded: number;
    majority: AgreementRecord; unanimous: AgreementRecord }[];
}
export interface AgreementRecord { picks: number; wins: number; losses: number; accuracy: number | null; coverage: number | null }

export interface ThresholdReport {
  all_stored_rows: { winner_rows: number; market_rows: number }; note: string;
  groups: { market: MarketId; model_version: string; line_source: string;
    thresholds: { threshold: number; forecasts: number; settled: number; win: number; loss: number;
      push: number; void: number; pending: number; unscorable: number; accuracy: number | null;
      coverage: number | null; interval: [number, number] | null }[] }[];
}

// --- Research: model agreement, combination and selection (backend app/research_live.py). Research/shadow only. ---
export type Side = 'a' | 'mid' | 'b';
export type Vector = [number, number, number];
export interface ResearchRule { source: string; filter: 'none' | 'majority' | 'unanimous'; threshold: number }
export interface ResearchDecision {
  status: 'active' | 'abstain' | 'insufficient_evidence'; rule: ResearchRule | null; lower_bound: number | null; reason: string;
  selector_version: string; evidence_until: string; effective_from: string; effective_until: string;
}
export interface ResearchSelection {
  status: 'pick' | 'no_pick'; side: Side | null; probability: number | null; threshold: number | null; reason: string;
  recommended: boolean; game_reason: string; generated_at: string; decision: ResearchDecision | null;
}
export interface ResearchAgreement {
  picks: Record<string, Side | null>; votes: Partial<Record<Side, number>>; abstentions: number; expected: number;
  available: number; complete: boolean; side: Side | null; agreeing: number; majority: boolean; unanimous: boolean;
  pairs: { members: [string, string]; agree: boolean }[];
}
export interface ResearchMarket {
  market: MarketId; line: number | null; line_source: 'market' | 'model' | null; line_label: string | null;
  bookmaker: { p: Vector; provider: string; retrieved_at: string; quote_table?: string; quote_id?: number | null } | null;
  sides: Record<Side, string>;
  primary: string; members: { name: string; p: Vector }[]; missing: Record<string, string>;
  average: Vector | null; stack: Vector | null; stack_note: string; agreement: ResearchAgreement; definition: string;
  generated_at: string; information_at: string; tipoff_at: string; minutes_before_start: number; start_changed: boolean;
  selection: ResearchSelection | null;
}
export interface ResearchExpanded {
  market: MarketId; line: number | null; members: { name: string; p: Vector }[]; missing: Record<string, string>;
  average: Vector | null; stack: Vector | null; agreement: ResearchMarket['agreement']; definition: string; generated_at: string;
  minutes_before_start: number;
}
export interface ResearchGame {
  game_id: string; mode: Mode; status: 'unknown_game' | 'no_research_forecast' | 'recorded'; markets: ResearchMarket[];
  expanded?: ResearchExpanded[]; expanded_note?: string;
  recommendation: { market: MarketId; side: Side; label: string; line: number | null; probability: number } | null; note: string;
}
export interface ResearchSlateMarket {
  status: 'pick' | 'no_pick' | 'not_evaluated'; pick: string | null; probability: number | null; threshold: number | null;
  recommended: boolean; reason: string; line: number | null; line_source: 'market' | 'model' | null; line_label: string | null;
  missing: Record<string, string>; generated_at: string; minutes_before_start: number; start_changed: boolean;
}
export type ResearchSlateEntry =
  | { status: 'none'; markets: Partial<Record<MarketId, ResearchSlateMarket>> }
  | { status: 'no_pick'; reasons: Record<string, string>; markets: Partial<Record<MarketId, ResearchSlateMarket>> }
  | { status: 'pick'; market: MarketId; side: Side; label: string; line: number | null; probability: number;
      markets: Partial<Record<MarketId, ResearchSlateMarket>> };
export interface CalibrationBin { n: number; mean_p: number; win_rate: number }
export interface ResearchSlate { mode: Mode; games: Record<string, ResearchSlateEntry> }
export interface ResearchRecord { wins: number; losses: number; pushes: number; decided: number; accuracy: number | null; ci95: [number, number] | null }
export interface ResearchSource extends ResearchRecord {
  cohorts: number; coverage: number | null; log_loss: number | null; brier: number | null; ties: number; calibration: CalibrationBin[];
  thresholds: (ResearchRecord & { threshold: number })[];
}
export interface ResearchGroup {
  definition: string; cohorts: number; settled: number; pending: number; void: number; unscorable: number;
  line_sources: { market: number; model: number }; sources: Record<string, ResearchSource>;
  agreement: Record<'majority' | 'unanimous', ResearchRecord & { cohorts: number; coverage: number | null;
    primary_confidence_same_count: ResearchRecord; average_confidence_same_count: ResearchRecord;
    best_single: string | null; best_single_basis: string; best_single_confidence_same_count: ResearchRecord | null }>;
  periods: { week: string; cohorts: number; primary: ResearchRecord; average: ResearchRecord }[];
  bookmaker: { cohorts: number; primary_log_loss: number | null; bookmaker_log_loss: number | null; note: string };
  selector: { selections: { pick: number; no_pick: number }; picks: ResearchRecord; pending: number; void: number; recommended_for_game: ResearchRecord } | null;
  experiment?: 'core' | 'expanded-v1'; recording_only?: boolean;
}
export interface ResearchReport {
  mode: Mode; sport: Sport; market: MarketId; as_of: string; groups: ResearchGroup[]; selector_version: string; note: string;
  settlement_version: string;
  decisions: { decision_id: number; scheduled_for: string; decided_at: string; effective_until: string; status: ResearchDecision['status'];
    rule: ResearchRule | null; lower_bound: number | null; reason: string; selector_version: string; settlement_version: string | null;
    evidence: { settled_items: number; rules: number; z: number | null } }[];
}
export interface ResearchHistory {
  mode: Mode; games_with_selection_run: number; games_without_pick: number; total: number;
  record: ResearchRecord & { pending: number; void: number; unscorable: number };
  rows: { game_id: string; tipoff_at: string; home: string; away: string; market: MarketId; line: number | null;
    line_source: 'market' | 'model' | null; pick: string; probability: number; threshold: number | null; rule: ResearchRule | null;
    selector_version: string | null; generated_at: string; minutes_before_start: number; result: string }[];
}
export interface Diff { diff: number; ci95: [number, number] }
export interface RetroMarket {
  games: number; seasons: number[]; members: string[]; primary: string; best_single_by_log_loss: string;
  best_single_prior?: Record<string, { model: string | null; games: number; seasons?: number[] }>;
  methods: Record<string, { n: number; accuracy: number | null; log_loss: number; brier: number; calibration?: CalibrationBin[] }>;
  stack: { seasons: number[]; games: number; methods: Record<string, { n: number; accuracy: number | null; log_loss: number }> | null };
  log_loss_diff: Record<string, { mean: number; ci95: [number, number] }>;
  agreement: { rule: string; coverage: number; accuracy: number | null; vs_primary_confidence: Diff | null; vs_average_confidence: Diff | null;
    vs_best_prior_confidence?: Diff | null; best_prior_games?: number | null }[];
  disagreement_vs_primary: Record<string, { disagreements: number; rate: number; primary_right: number; member_right: number; log_loss_correlation: number }>;
  selector_simulation: { weeks: number; active_weeks: number; picks: number; coverage: number; accuracy: number | null; lift: number | null;
    rule_changes: number; primary_confidence_same_count?: { accuracy: number | null; diff: Diff | null };
    best_single_confidence_same_count?: { accuracy: number | null; diff: Diff | null; games: number } };
}
export interface Retrospective {
  mode: Mode; sport: Sport; status?: 'unavailable'; kind: string; generated_at: string; evaluated_seasons: number[]; design: string;
  markets: Partial<Record<MarketId, RetroMarket>>; note: string;
}

// --- Combinations of saved out-of-sample forecasts (backend combination_research.stored). Exploratory research. ---
export interface ComboMetrics { n: number; wins: number; losses: number; pushes: number; no_pick: number; accuracy: number | null;
  accuracy_ci95: [number, number] | null; log_loss: number; brier: number }
export interface ComboPaired { mean: number; ci95: [number, number]; games: number; weeks: number }
export interface ComboPeriod { seasons: number[]; games: number; date_from: string | null; date_to: string | null }
export interface ComboSingle { model: string; rank: number; metrics: ComboMetrics; log_loss_vs_best: ComboPaired | null }
export interface ComboPair { members: [string, string]; metrics: ComboMetrics; strongest_member: string;
  log_loss_vs_strongest: ComboPaired | null; agreement_rate: number;
  agree: { games: number; decided: number; wins: number; accuracy: number | null };
  disagree: { games: number; first_right: number; second_right: number }; shared_mistakes: { games: number; rate: number } }
export interface ComboAddRow { added: string; members: string[]; metrics: ComboMetrics; log_loss_change: ComboPaired;
  accuracy_change: ComboPaired | null; changed_picks: number; corrected: number; introduced: number }
export interface ComboAgreeCount { agreeing: number | string; of: number; games: number; share: number; decided: number; wins: number;
  accuracy: number | null; accuracy_ci95: [number, number] | null; mean_average_confidence: number }
export interface ComboConfBand { band: string; games: number; decided: number; wins: number; accuracy: number | null;
  accuracy_ci95: [number, number] | null; unanimous_share: number }
export interface ComboCheck { choice: string; members: string[]; earlier: ComboMetrics; later: ComboMetrics | null;
  later_log_loss_vs_best_single: ComboPaired | null; later_accuracy_vs_best_single: ComboPaired | null }
/** One period's saved games and the compared models' forecast coverage (backend combination_research.coverage). Only `games`
 * and `missing` exist in every API version: older servers sent `coverage` without settled/pending/used counts, and their
 * `missing` denominator differed (all games or settled games), so the UI shows those counts without a denominator. */
export interface PeriodCoverage { games?: number; settled?: number; pending?: number; used?: number; excluded_missing_member?: number;
  missing?: Record<string, number> }
export interface CoverageFields { coverage?: Partial<Record<'earlier' | 'later', PeriodCoverage>>; coverage_definition?: string;
  split?: { earlier_seasons: number[]; later_seasons: number[] } | null }
export interface CombinationAnalysis extends CoverageFields {
  mode: Mode; sport: Sport; market: MarketId; status: 'available' | 'unavailable' | 'no_stored_forecasts' | 'insufficient_evidence';
  reason?: string; kind?: string; note: string; models: string[];
  excluded?: { model: string; reason: string }[]; stored_games?: number; excluded_games?: { no_result: number }; stages?: string[];
  sources?: { stage: string; file: string; sha256: string }[]; later_reserved_test_seasons?: number[];
  games?: number; default_base?: string[]; periods?: { all: ComboPeriod; earlier: ComboPeriod; later: ComboPeriod };
  search?: { singles: number; pairs: number; pairs_with_interval: number; additions_per_base: number; frozen_choices: number; rule: string };
  singles?: ComboSingle[]; pairs?: ComboPair[];
  add_model?: { base: string[]; base_metrics: ComboMetrics; games: number; candidates: number; rows: ComboAddRow[] };
  agreement?: { members: string[]; games: number; by_count: ComboAgreeCount[]; by_confidence: ComboConfBand[]; tie_rule: string; confidence: string };
  holdout?: { status: 'ok' | 'insufficient_evidence'; min_decided: number; chosen_on: string; checks: ComboCheck[] };
}

// --- Thresholds, agreement and exact subsets on saved forecasts (backend combination_thresholds). Exploratory research. ---
export interface ThRecord { eligible: number; picks: number; coverage: number | null; wins: number; losses: number; pushes: number;
  pending: number; win_rate: number | null; win_rate_ci95: [number, number] | null; mean_p: number | null;
  calibration_gap: number | null; first: string | null; last: string | null }
export interface ThRow extends ThRecord { rule: string; lo: number; hi: number | null }
export interface ThLadder { cumulative: ThRow[]; bands: ThRow[] }
export interface ThSource { key: string; kind: 'model' | 'average'; members: string[]; earlier: ThLadder; later: ThLadder }
export interface ThCross extends ThRecord { level: number; agreement_min: number | null; rule: string }
export interface ThCount extends ThRecord { level: number; agreeing: number; of: number; kind: 'unanimous' | 'partial' }
export interface ThAddLevel { level: number; rule: string; base: ThRecord; with: ThRecord; retained: ThRecord; switched_base: ThRecord;
  switched_with: ThRecord; new: ThRecord; dropped: ThRecord }
export interface ThAlone { model: string; threshold: number; own: ThRecord; excluded: ThRecord }
export interface ThMatched extends ThRecord { model: string; picks_matched: number; min_probability: number | null }
export interface ThSameGames extends ThRecord { model: string; member: boolean; same_side: number; abstained: number }
export interface ThRulePeriod { record: ThRecord; member_mean_p: Record<string, number | null>; alone: ThAlone[];
  same_games_best_single: ThSameGames | null; coverage_matched_best_single: ThMatched | null;
  by_season: (ThRecord & { season: number })[]; too_few: boolean }
export interface LedgerRow { date: string; tipoff_at: string; game_id: string; season: number; period: 'earlier' | 'later';
  home: string | null; away: string | null; market: MarketId; line: number | null; shared_pick: string;
  members: { model: string; threshold: number; pick: string; p: number }[]; home_score: number | null; away_score: number | null;
  actual: string | null; result: 'win' | 'loss' | 'push' | 'pending'; best_single: string | null; best_single_pick: string | null;
  best_single_p: number | null; best_single_result: string | null }
/** Pairwise earlier-period pick overlap (backend combination_thresholds.member_overlap): members that agree on almost every
 *  game — one built on the other's inputs, e.g. players_injuries_v1 extends logistic_v2 — are not independent votes. */
export interface MemberOverlap { period: string; note: string;
  pairs: { a: string; b: string; games: number; same_side: number; share: number | null }[] }
export interface RuleLedger { mode: Mode; sport: Sport; market: MarketId; source: string; rule: string; members: string[];
  thresholds: number[]; period: 'earlier' | 'later' | 'all'; best_single: string | null; games: number; exploratory: boolean;
  rows: LedgerRow[]; member_overlap?: MemberOverlap | null }
export interface ThRule { members: string[]; thresholds: number[]; rule: string; excluded_games_missing_member: number;
  earlier: ThRulePeriod; later: ThRulePeriod; earlier_wilson_lower?: number; earlier_lift?: number; later_pick_set?: number }
export interface ThSourceInfo { key: string; label: string; files: { file: string; sha256: string; stage?: string }[];
  in_force?: boolean; exported_at?: string }
export interface ThHead extends CoverageFields {
  mode: Mode; sport: Sport; market: MarketId; source: string; status: 'available' | 'unavailable' | 'no_stored_forecasts' | 'insufficient_evidence';
  reason?: string; note?: string; models: string[]; excluded?: { model: string; reason: string }[]; sources?: ThSourceInfo[];
  not_stored?: { model: string; reason: string }[]; kind?: string; win_rate_denominator?: string; pending_games?: number;
  periods?: { all: ComboPeriod; earlier: ComboPeriod; later: ComboPeriod }; later_reserved_test_seasons?: number[];
  source_detail?: { kind: string; definition?: string; in_force?: boolean; exported_at?: string };
}
export interface ThresholdExplorer extends ThHead {
  group?: string[]; best_single?: string | null;
  thresholds?: { levels: number[]; sources: ThSource[]; probability: string };
  agreement?: { members: string[]; cross: Record<'earlier' | 'later', ThCross[]>; by_count: Record<'earlier' | 'later', ThCount[]>;
    excluded_games_missing_member: number; rule: string } | null;
  add_model?: { base: string[]; rows: ({ added: string; members: string[] } & Record<'earlier' | 'later', ThAddLevel[]>)[] };
  rule?: ThRule;
}
/** Descriptive later-period overlap of one frozen list: rules with the same later games and sides share a set. */
export interface LaterOverlap { rules: number; distinct_later_pick_sets: number; set_of_rule: number[];
  shared: { set: number; rules: number[]; picks: number }[]; beats_matched: { rules: number; distinct_later_pick_sets: number } }
export interface SubsetSearch extends ThHead {
  grid?: number[]; sizes?: number[]; per_size?: Record<string, { subsets: number; rules: number }>; rules_searched?: number;
  rules_with_min_picks?: number; min_decided?: number; frozen?: ThRule[]; frozen_lift?: ThRule[]; frozen_top?: number;
  later_beats_matched?: { reliable: number; lift: number }; duplicates_collapsed?: { reliable: number; lift: number };
  later_overlap?: Record<'reliable' | 'lift', LaterOverlap>; later_overlap_definition?: string;
  ranked_by?: { frozen: string; frozen_lift: string }; best_single?: string | null;
}

// --- Registered combination rules (backend app/rule_tracking.py, docs/MODEL_SELECTION.md §15). Research only. ---
export interface RuleMatched { model: string; games: number; accuracy: number | null }
export interface RuleHistorical { assess_season: number; why: string; picks: number; coverage: number; accuracy: number | null;
  lift: number | null; selection_accuracy: number | null }
export interface RuleProspective {
  eligible_games: number; picks: number; coverage: number | null; win: number; loss: number; push: number; void: number;
  pending: number; unscorable: number; accuracy: number | null; ci95: [number, number] | null;
  strongest_single_matched: RuleMatched | null; constituents_matched: RuleMatched[];
  weeks: { week: string; win: number; loss: number; push: number }[];
  recent: { game_id: string; decision: string; reason?: string; probability?: number; outcome?: string; line?: number | null;
    generated_at: string; minutes_before_start?: number }[];
}
export interface RegisteredRule {
  id: string; kind: string; members: string[]; t: number | null; level: string; selected: string | null; why: string | null;
  prospective_only: boolean; historical: RuleHistorical[]; prospective: RuleProspective;
}
export interface FrontierBand { band: [number, number]; rule: string | null; picks?: number; coverage?: number; accuracy?: number | null;
  comparator?: string; comparator_accuracy?: number | null; per_season?: Record<string, { picks: number; accuracy: number | null }> }
export interface RuleReport {
  mode: Mode; sport: Sport; market: MarketId; status?: string; study?: string; registry_sha256?: string; effective_from?: string;
  cohort_source?: 'core' | 'expanded'; members?: string[]; comparator?: string; cohorts?: number; registered_rules?: number;
  selected_rules?: number; frontier_historical?: FrontierBand[]; rules: RegisteredRule[]; note?: string;
  definition?: string | null; definitions?: { definition: string; games: number; first_at: string; last_at: string }[];
}

// --- Candidate pool roster (backend app/candidates/live.py, docs/CANDIDATE_POOL.md). Research only. ---
export interface CandidateEval { stage: string; seasons: number[] | null; log_loss: number; vs_strongest: Record<string, unknown> | number | null;
  strongest: string | null; shortlisted: boolean;
  assess?: { seasons: number[] | null; log_loss: number; vs_strongest: Record<string, unknown> | number | null; confirmed_gains: unknown[] } }
export interface Candidate {
  name: string; family: string; head: string; features: string; role: string; why: string; markets: string[]; refit: string;
  definition: string; training_status: string; latest_fit: { first_used_at: string; training_games: number; results_available_through: string } | null;
  evaluation: Record<string, CandidateEval>; prospective: { games: number; settled: number }; combination_participation: string[];
  pool?: Record<string, CandidatePoolState>; selection: string;
  coverage?: { fitted: boolean; earlier_fit?: boolean; earlier_forecast_at?: string | null; latest_forecast_at: string | null; forecasting: boolean; in_agreement_tracking: string[]; votes: boolean;
    selectable: { alone: string[]; inside: string[] } };
}
export interface CoreMemberCoverage { name: string; markets: string; votes: boolean; role: string;
  fit_register: { definition: string; first_used_at: string; training_games: number } | null;
  earlier_fit?: { definition: string; first_used_at: string; training_games: number } | null; current_definition?: string | null;
  latest_forecast_at: string | null; forecasting: boolean; earlier_forecast_at?: string | null; selectable: boolean }
// Historical starting policy (backend app/historical_start.py). Research only; separate from the live selector's record.
export interface HistoricalStartRule { source: string; filter: string; threshold: number }
export interface HistoricalStartMarket { status: 'active' | 'abstain' | 'insufficient_evidence'; rule: HistoricalStartRule | null;
  lower?: number | null; lift?: number | null; reason: string; sources: string[]; filters: string[]; excluded: Record<string, string>;
  games: number; seasons?: number[]; from?: string; until?: string; rules_compared?: number; eligible_rules?: number;
  chronological_check?: { weeks: number; active_weeks: number; rule_changes: number; games: number; picks: number; pushes: number;
    coverage: number; wins: number; losses: number; accuracy: number | null; lift: number | null; accuracy_ci95: [number, number] | null;
    mean_probability?: number | null; mean_reference?: number | null; unjudged_picks?: number; judged_decided?: number } }
export interface HistoricalStartLive { cohorts_after_freeze: number; cohorts_before_freeze: number; picks: number; wins: number;
  losses: number; pushes: number; pending: number; void: number; unscorable: number; accuracy: number | null;
  bookmaker: { quoted_decided: number; quoted_wins: number; mean_implied: number | null; note: string };
  recent: { game_id: string; tipoff_at: string; line: number | null; side: string; probability: number; result: string }[] }
export interface HistoricalStart { mode: Mode; sport: Sport; market: string; status: 'frozen' | 'not_built' | 'demo'; label?: string;
  note: string; policy?: string; definition?: string; current_definition?: string; matches_current_definition?: boolean;
  matches_current_selector?: boolean; frozen_at?: string; selector_version?: string; dataset_version?: string;
  evaluated_seasons?: number[]; reference?: string; frozen_policy?: string; matches_current_policy?: boolean; decision?: HistoricalStartMarket | null; live?: HistoricalStartLive | null }
// Candidate-pool selection (backend app/candidates/selection.py, docs/CANDIDATE_POOL.md §7). Research only.
export type PoolRoute = 'standalone' | 'combination';
export interface PoolEvidence { file: string; key: string; comparison: string; seasons: number[] | null; mean: number | null;
  ci95: [number, number] | null; games: number | null; weeks: number | null }
export interface PoolSourceStats { filter: string; threshold: number; picks: number; wins: number; losses: number; pushes: number;
  coverage: number | null; weeks: number; lift: number | null; lower: number | null; eligible: boolean }
export interface PoolDecisionState { status: 'active' | 'abstain' | 'insufficient_evidence';
  rule: { source: string; filter: string; threshold: number } | null; reason: string; evidence_until: string; effective_until: string }
export interface CandidatePoolState {
  sources: { source: string; route: PoolRoute; evidence: PoolEvidence; prospective: PoolSourceStats | null }[];
  decision: PoolDecisionState | null; selected_now: boolean }
export interface CandidateRoster { mode: Mode; sport: Sport; pool?: string; as_of?: string; candidates: Candidate[];
  members?: CoreMemberCoverage[]; coverage_note?: string;
  registry?: { id: string; effective_from: string; routes: Record<string, string>; rule: string } | null;
  registry_error?: string | null; family_audit: unknown[]; note: string }
export interface PoolSourceReport { source: string; kind: 'single' | 'average'; members: string[]; candidates: string[];
  route: PoolRoute; evidence: PoolEvidence; settled_with_vector: number; best_rule: PoolSourceStats | null }
export interface PoolSuspension { sport: Sport; market: MarketId; source: string; reason: string }
export interface PoolReport { mode: Mode; sport: Sport; registry?: string; effective_from?: string; as_of?: string; suspended?: PoolSuspension[];
  routes?: Record<string, string>; rule?: string;
  markets: Record<string, { sources: PoolSourceReport[]; settled_cohorts: number;
    decisions: { scheduled_for: string; decided_at: string; effective_until: string; status: string;
                 rule: { source: string; filter: string; threshold: number } | null; lower_bound: number | null; reason: string }[] }>;
  note: string }

export interface SelectionRuleView { selector: 'core' | 'pool'; status: string; label: string; decision_id?: number;
  selector_version?: string; definition?: string; registry?: string | null; selected_at?: string; evidence_until?: string;
  next_review?: string; reason?: string; lower_bound?: number | null;
  rule: { source: string; filter: string; threshold: number; members: string[]; agreement: string } | null;
  evidence?: { settled_cohorts: number | null; rules_compared: number | null; eligible_rules: number | null; min_picks: number | null;
    min_coverage: number | null; min_weeks: number | null;
    record: { picks: number; wins: number; losses: number; pushes: number; coverage: number; weeks: number; accuracy: number;
              lift: number; lower: number } | null };
  eligible_sources?: { source: string; route: string; members: string[] }[]
}
export interface SelectionStatusMarket { market: MarketId; status: string; core: SelectionRuleView; pool: SelectionRuleView | null;
  research_sources: string[]; recording: { latest_cohort_at: string | null; members?: unknown; missing: Record<string, string> }
}
export interface SelectionStatus { mode: Mode; sport: Sport; as_of?: string; definition?: string; selector_version?: string;
  pool_registry?: string | null; pool_registry_error?: string | null; markets: SelectionStatusMarket[]; note: string
}

// --- Persistent agreement tracking tracking-v2 (docs/MODEL_SELECTION.md §18). Research only. ---------------------------
export type TrackingKind = 'single' | 'unanimous' | 'avg' | 'majority' | 'stack' | 'stack_pool';
export type TrackingEvidence = 'prospective' | 'historical';
export interface TrackingMatched { model: string; games: number; win_rate: number | null }
export interface TrackingCell {
  selected: number; settled: number; pending: number; wins: number; losses: number; pushes: number; voids: number;
  unscorable: number; coverage: number | null; decided: number; win_rate: number | null; ci95: [number, number] | null;
  mean_confidence: number | null; brier: number | null; log_loss: number | null; evidence: 'sufficient' | 'insufficient evidence';
  threshold?: number | null; matched?: TrackingMatched[]; band?: [number, number]; label?: string; sealed?: boolean;
}
export interface TrackingGroup {
  id: string; kind: TrackingKind; members: string[]; cohort: 'core' | 'expanded'; priority: string; eligible: number;
  complete: number; status_counts: Record<string, number>; missing_reasons: Record<string, number>;
  all_picks: TrackingCell; thresholds: TrackingCell[]; bands: TrackingCell[];
  probabilistic: { games: number; brier: number | null; log_loss: number | null } | null; versions: Record<string, number>;
}
export interface TrackingLeader {
  id: string; kind: TrackingKind; members: string[]; threshold: number | null; decided: number; coverage: number | null;
  win_rate: number; ci95: [number, number] | null; anchor: string; anchor_win_rate: number; lift: number; lower_adjusted: number;
}
export interface TrackingReport {
  mode: Mode; sport: Sport; market: MarketId; evidence: TrackingEvidence; status: string; study?: string;
  registry_sha256?: string; effective_from?: string; anchor?: string; thresholds?: number[]; sealed_thresholds?: number[]; grid?: string;
  bands?: { band: [number, number]; label: string }[]; registered_groups?: number; configurations_searched?: number;
  skipped_allocation?: number; games?: number; lines?: number[]; kinds?: Record<string, string>; min_evidence?: number;
  groups: TrackingGroup[]; leaderboard: TrackingLeader[]; note: string; leaderboard_note?: string;
  recommendation?: SelectionStatusMarket | null;
  exact_rules?: ExactRulesReport;
  /** Prospective evidence only - the research definitions with observations, newest first; the record covers only `definition_applied`. */
  definitions?: { definition: string; games: number; last_cohort_at: string }[]; definition_applied?: string | null;
  signature_applied?: string | null;
}
/** exact-rules-v1: a registered subset with one threshold per member, tracked prospectively only (CLI-only registration).
 * The statistic is the minimum member confidence for the agreed side: descriptive, never an ensemble probability. */
export interface ExactRule extends Omit<TrackingGroup, 'kind'> {
  kind: 'exact'; member_thresholds: number[]; effective_from: string; registered_at: string; registered_version: number;
  rule_sha256: string; first_observed: string | null; last_observed: string | null;
  provenance?: { source: string; note: string | null; members: Record<string, string>;
    explorer: { file: string; sha256: string; list: string; rank: number; source?: string; later_pick_set?: number | null;
      earlier: Record<string, unknown>; later: Record<string, unknown> } | null } | null;
}
export interface ExactRulesReport {
  /** `failed` = the registry file cannot be loaded (rules unknown); `schema_required` = rules exist but this store lacks
   * migration 0028, so nothing is recorded or reported. Neither means "no rules registered" or "waiting for games". */
  study: string; status: 'recording' | 'no_observations_yet' | 'no_registered_rules' | 'not_applicable' | 'demo' | 'failed' | 'schema_required' | string;
  games: number; rules: ExactRule[]; note: string; registration?: string; registry_version?: number;
  registry_sha256?: string | null; statistic?: string; kinds?: Record<string, string>;
  error?: string; reason?: string; registered_rules?: string[]; versions_note?: string;
}
export interface TrackingCheck {
  mode: Mode; status: string; note?: string;
  folds: { season: number; chosen: string | null; threshold?: number | null; reason?: string; reserved_test_season?: boolean;
    earlier?: { decided: number; coverage: number | null; win_rate: number; lift: number; lower_adjusted: number };
    later?: { decided: number; coverage: number | null; win_rate: number | null; ci95: [number, number] | null; matched: TrackingMatched[] } }[];
}
export interface TrackingGameMarket {
  market: MarketId; line: number | null; groups: number; counts: Record<string, number>; recorded_at: string | null; definition: string | null;
  /** 'recorded' = the report's canonical cohort, 'void' = the game moved or was not played, 'not_recorded' = none for that cohort. */
  state?: 'recorded' | 'void' | 'not_recorded'; state_reason?: string | null; definition_note?: string | null;
  picks: { group: string; kind: TrackingKind; side: Side; statistic: number; votes: Record<string, number> | null }[];
  incomplete: { group: string; missing: Record<string, string> }[];
}
export interface TrackingGame {
  mode: Mode; game_id: string; status: string; registry_sha256?: string; markets: TrackingGameMarket[]; note?: string;
  /** Set when the exact-rule registry file could not be loaded; the sealed groups above are still reported. */
  exact_rules_error?: string | null;
}

// --- Training and update status (training_monitor). Never refits anything. ---------------------------------------------
export interface TrainingFit {
  member: string; schedule: string; schedule_note: string | null; fit_kind: string; trained_season: number | null;
  training_games: number; results_available_through: string; params_digest: string; fit_in_force_since: string;
  new_settled_results_since_fit: number; action: string; next_update: { when: string | null; rule: string };
  versions: { definition: string | null; libraries: string | null; params: string; calibration: string };
}
export interface TrainingStatus {
  mode: Mode; as_of?: string; fits: TrainingFit[];
  online: { member: string; schedule: string; last_update_input: string | null; latest_available_result: string | null; consumption_verified: boolean; verified_at: string | null; results_used: number | null; next_update: string }[];
  updates: { run_id: number; at: string; step: string; member: string | null; outcome: 'failed' | 'skipped'; reason: string }[];
  inputs: Record<string, { last_success: string | null; stale: boolean; stale_after_hours: number }>;
  monitoring: { members: { market: string; member: string; settled: number; recent_log_loss: number; previous_log_loss: number | null;
    missing_rate: number; status?: string; difference?: number }[]; alerts: { member: string; market: string; alert: string }[]; note?: string };
  note: string;
}

export interface JointReport {
  status: string; note: string; options: string[]; lines: number[]; definitions: string[]; games: number;
  definition_applied?: string | null;
  by_definition: { definition: string; games: number; status_counts: Record<string, number>; cell: TrackingCell }[];
  recent: { game_id: string; tipoff_at: string; line: number | null; state: string; status: string;
    side: number | null; agreement: number | null; statistic: number | null; votes: Record<string, number>; missing: Record<string, string>; matches_filter: boolean }[];
  walk?: { configurations: number; note: string; folds: { season: string; reason?: string; reserved_test_season?: boolean;
    chosen: { agreement_min: number; agreement_max: number | null; confidence_min: number; confidence_max: number | null; comparator: string } | null;
    later?: TrackingCell; matched?: { model: string; games: number; win_rate: number | null } }[] } | null;
}

// adaptive-v1 (docs/ADAPTIVE_SYSTEM.md): separately versioned adaptive combination. Research only; never the primary forecast.
export interface AdaptiveMemberRow { name: string; p: Vector; weight: number | null; fit_id: number | null; role: string }
export interface AdaptiveSelection { status: 'pick' | 'no_pick'; side: Side | null; probability: number | null; reason: string; label?: string | null }
export interface AdaptiveMarket {
  market: MarketId; line: number | null; line_source: string; line_label: string | null;
  sides: { a: string; mid: string | null; b: string }; status: 'forecast' | 'unavailable'; reason: string | null;
  p: Vector | null; members: AdaptiveMemberRow[]; missing: Record<string, string>; contributing: string[];
  weights: { members: string[]; weights: number[]; bias: number[]; l2: number | null } | null;
  bookmaker: { p: Vector; provider: string; retrieved_at: string } | null; odds_status: string; edge: number[] | null;
  selection: AdaptiveSelection; training_cutoff: string | null; weights_updated_at: string | null; combiner_fit_id: number | null;
  definition: string; model_version: string; generated_at: string; information_at: string; tipoff_at: string;
  minutes_before_start: number; start_changed: boolean;
}
export interface AdaptiveGame { mode: Mode; game_id: string; version: string; markets: AdaptiveMarket[]; status: string; note?: string }
export interface AdaptiveSummary { n: number; accuracy: number | null; accuracy_ci95: [number, number] | null; log_loss: number | null; brier: number | null;
  calibration: { n: number; mean_p: number; win_rate: number }[] }
export interface AdaptiveReplayMarket {
  primary: string; core_members: string[]; adaptive_members: string[]; evaluated_games: number; scored_games: number; pushes: number;
  coverage: Record<string, number | null>; sources: Record<string, AdaptiveSummary | null>;
  paired: Record<string, { mean: number; ci95: [number, number]; per_season: Record<string, number | null>; metric: string; games: number }>;
  single_members_log_loss: Record<string, number | null>; adaptive_fit_count: number; adaptive_weights_last: Record<string, number> | null;
  adaptive_excluded_last: Record<string, string> | null; adaptive_l2_last: number | null;
}
export interface AdaptiveReplay { version: string; sport: string; started_at: string; evaluated_seasons: number[]; loaded_seasons: number[];
  members: string[]; excluded: Record<string, string>; markets: Record<string, AdaptiveReplayMarket>; seconds: number }
export interface AdaptiveStatus {
  mode: Mode; version: string; sport: string; as_of: string; definition: string | null; definition_error: string | null;
  schedule: Record<string, string>; enabled: boolean; due: { due: boolean; reason: string; period?: string };
  combiner: { fit_id: number; period: string; status: 'success' | 'partial'; partial_reason: string | null; market_fit_ids: Record<string, number>;
    training_cutoff: string | null; weights_updated_at: string; artifact_sha256: string | null;
    markets: Record<string, { members: string[] | null; excluded: Record<string, string> | null; weights: Record<string, number> | null; l2: number | null;
      l2_validation: Record<string, number | null> | null; training: { games: number; prospective: number; walk_forward: number; seasons: number[]; results_through: string } | null; reason: string | null }>;
    selection: Record<string, { status: string; rule: { source: string; filter: string; threshold: number } | null; reason: string; settled_items: number; judged_items: number }>;
    excluded: Record<string, string>; members_available: string[] } | null;
  last_failure: { at: string; period: string; error: string | null } | null;
  members: { name: string; fit_in_force: { fit_id: number; period: string; training_cutoff: string | null; training_games: number | null; since: string; detail: Record<string, unknown> } | null;
    last_failure: { at: string; error: string | null } | null }[];
  attempts: { fit_id: number; kind: string; name: string; period: string; status: string; training_cutoff: string | null; training_games: number | null; started_at: string; finished_at: string; detail: Record<string, unknown> }[];
  prospective: { markets: Record<string, { settled: number; log_loss: number | null; brier: number | null; record: ResearchRecord;
    primary: { name: string; games: number; adaptive_log_loss: number | null; primary_log_loss: number | null };
    bookmaker: { games: number; coverage: number | null; adaptive_log_loss: number | null; bookmaker_log_loss: number | null } }>; pending: number; note?: string };
  replay: AdaptiveReplay | null; note: string;
}

export interface Health { status: 'ok'; mode: Mode; api_version: string; data_snapshot_at?: string | null }

/** One adaptive training run, identified by its combination record (backend training_monitor._run_row). */
export interface TrainingRunRecord {
  definition: string; period: string; status: 'success' | 'partial' | 'failed' | 'skipped'; started_at: string; finished_at: string;
  training_cutoff: string | null; members_succeeded: number; members_failed: number; members_skipped: number; error: string | null;
}
export interface ArtifactCount { members_written: number; combiner_written: boolean; last_written_at: string | null }
/** /api/training-run: evidence only (lock holder, artifacts written, ledger rows); never a percentage or time left. */
export interface TrainingRun {
  mode: Mode; sport?: Sport; note: string; as_of?: string;
  running?: boolean | null; running_note?: string;
  current_run?: { started_at: string | null; last_progress_at: string | null; processes: { pid: number; command: string | null }[];
    by_definition: Record<string, ArtifactCount> } | null;
  last_run?: TrainingRunRecord | null; last_success?: TrainingRunRecord | null;
  last_failure?: { definition: string; kind: string; name: string; at: string; error: string | null } | null;
  code_definition?: string | null; expected_members?: number | null;
  code_definition_published?: { members_succeeded: number; members_failed: number; combiner: { status: string; finished_at: string } | null } | null;
  code_definition_on_disk?: ArtifactCount | null;
  not_serving?: { definition: string; state: string; members_written: number; at: string | null }[];
  serving?: { adaptive: { definition: string; last_forecast_at: string; forecasts: number; unavailable?: number;
      definitions?: { definition: string; forecasts: number; unavailable: number; last_forecast_at: string | null; last_row_at: string | null }[] } | null;
    primary: { model_version: string; last_forecast_at: string } | null };
  live_data_dir?: string; serving_data_dir?: string;
}

export interface DailyDecision {
  status: string; reason: string; cutoff: string; rule: { members: string[]; thresholds: number[]; rule: string } | null;
  rules: ThRule[]; best_single?: string; member_overlap?: MemberOverlap | null;
  search: { sizes: number[]; models: string[]; thresholds: number[]; budget_per_size: number; evaluated: number;
    evaluated_by_size: Record<string, number>; method: string; note: string } | null;
}
export interface DailyReport {
  mode: Mode; sport: Sport; definition: string; as_of: string; recording_enabled: boolean; schedule: string; note: string;
  error: string | null; fit: { fit_id: number; finished_at: string; training_cutoff: string; status: string } | null;
  attempts: { fit_id: number; name: string; status: string; finished_at: string; detail: string }[];
  markets: { market: MarketId; reason: string | null; history_games: number; source_counts: Record<string, number>; fit_id: number | null; levels: number[];
    models: { name: string; supported: boolean; reason: string | null; fit_id: number | null; historical_games: number }[];
    thresholds: { levels: number[]; sources: ThSource[] } | null; decision: DailyDecision | null; incumbent_error: string | null;
    prospective: { eligible: number; picks: number; wins: number; losses: number; pushes: number; pending: number; void: number; unscorable: number; no_pick: number; win_rate: number | null; win_rate_ci95: [number, number] | null };
    ledger: { game_id: string; tipoff_at: string; generated_at: string; line: number | null; fit_id: number | null;
      outcome: string; selection: { status?: string; reason?: string; rule?: { rule: string } } }[];
  }[];
}
export interface DailyEvidence {
  definition: string; fit_id: number; period: string; rule: string; evaluation: ThRule; ladder: ThLadder;
  games: { game_id: string; tipoff_at: string; line: number | null; source: string; side: number; result: string; probabilities: Record<string, number> }[];
  total: number; offset: number; limit: number; note: string;
}

/** Every-game coverage (GET /api/coverage; docs/COVERAGE.md). Grades are computed from stored results on read. */
export type CoverageState = 'win' | 'loss' | 'push' | 'pending' | 'void' | 'unscorable' | 'unresolved' | 'missing' | 'upcoming' | 'unvalidated';
export interface CoverageRecord {
  forecasts: number; graded: number; win: number; loss: number; push: number; pending: number; void: number;
  unscorable: number; unresolved: number; win_rate: number | null;
}
export interface CoverageCell {
  market: MarketId; status: CoverageState; reason?: string; source?: string; fallback_reason?: string | null;
  line?: number | null; line_source?: string; confidence?: number; model_version?: string; recorded_in?: string;
  retrospective_available?: boolean; forecast_id?: number | null;
  /** Preseason rows: 'Experimental / unvalidated' (no model is validated on preseason games). */
  label?: string;
  later_failure?: { forecast_id: number; generated_at: string; reason: string } | null;
  /** NBA winner preseason: the stored row, shown only as experimental (never the canonical forecast). */
  experimental?: { label: string; forecast_id: number; source: string; model_version: string | null; confidence: number;
    p: number[]; line: number | null; line_source: string; grade: CoverageState };
}
export interface CoverageReport {
  status: 'available' | 'demo'; note?: string; sport?: Sport; date_from?: string; date_to?: string; as_of?: string;
  order_version?: string; order?: string[]; recording_began_at?: string | null;
  schedule?: { games: number; by_season_type: Record<string, number>; by_status: Record<string, number>;
    games_with_any_forecast: number; games_fully_forecast: number; games_unvalidated?: number; upcoming_games: number; not_forecastable_type: number; corrected_results: number };
  sources?: { competitive: Record<string, number>; preseason: Record<string, number> };
  by_market?: Record<string, { cells: number; forecast: number; main: number; fallback: number; missing: number; upcoming: number; unvalidated?: number; graded: number;
    pending: number; void: number; unscorable: number; unresolved: number }>;
  missing_reasons?: { reason: string; cells: number }[];
  /** All markets combined (counts only); the card shows `record_by_market`. */
  record?: { all: CoverageRecord; main: CoverageRecord; fallback: CoverageRecord };
  record_by_market?: Record<string, { all: CoverageRecord; main: CoverageRecord; fallback: CoverageRecord }>;
  preseason_record?: CoverageRecord & { label?: string };
  /** Each market on its own grid (two-way 50–90%, regulation three-way 35–70%); markets are never pooled. */
  thresholds_by_market?: Record<string, { threshold: number; all: CoverageRecord; main: CoverageRecord; fallback: CoverageRecord }[]>;
  games?: { game_id: string; tipoff_at: string; season_type: string; status: string; home_team_id: string; away_team_id: string;
    markets: CoverageCell[] }[];
  source_schedule?: { available: boolean; reason?: string; requests?: number; source_games?: number; stored_games?: number;
    missing_from_store?: { game_id: string; tipoff_at: string; season_type: string; status: string }[];
    stored_not_in_source?: string[]; field_differences?: unknown[] };
  ledgers?: (CoverageRecord & { ledger: string; model: string; market: MarketId; retrospective: boolean; games: number })[];
  notes?: string[];
}

/** /api/research/autoresearch: the sports autoresearch experiment directory (research only; never a forecast source). */
export interface AutoresearchTrial {
  trial_id: string; commit: string; status: string; cached_from?: string | null; description?: string | null;
  reason?: string | null; finished_at?: string | null; agent_run?: string | null;
  resources?: { wall_s?: number; max_rss_gb?: number; timed_out?: boolean } | null;
  markets: Record<string, { decision: string; incumbent?: string; forecast?: number; cohort?: number; delta?: number | null; ci95?: number[] | null }>;
}
export interface AutoresearchSport {
  protocol_id: string; registered_at?: string; scored_seasons?: number[]; holdout_seasons?: number[];
  cohort?: Record<string, number>; dataset_version?: string;
  bar: Record<string, { member: string; log_loss?: number | null }>;
  incumbents: Record<string, { source: string; label: string; log_loss?: number | null; delta_vs_previous?: number | null }>;
  trials: number; by_status: Record<string, number>; failures: number; running: string[]; unreadable_lines: number;
  recent: AutoresearchTrial[]; holdout: { event: string; at?: string }[];
}
export interface AutoresearchRun {
  run_id: string; branch?: string; budget?: { max_trials?: number; max_minutes?: number; max_usd?: number }; sports?: string[];
  agent?: string; model?: string | null; started_at?: string; finished_at?: string; end?: string; stopped?: string;
  outcome?: string; trials?: number; cost_usd?: number | null; reason?: string; report?: string;
  corrections?: { at?: string; reason?: string }[];
}
export interface AutoresearchStatus {
  mode: 'real' | 'demo'; configured: boolean; note: string; reason?: string; experiment_dir?: string; stopped?: string | null;
  active_run?: { run_id: string; branch?: string; started_at?: string; deadline?: number; max_trials?: number; driver_alive: boolean } | null;
  agent_runs?: AutoresearchRun[];
  last_daily?: { started_at?: string; finished_at?: string; outcome?: string; stopped?: string; refused?: Record<string, string> } | null;
  sports?: Record<string, AutoresearchSport>;
}
/** /api/research/candidates/game/{id}: each candidate model version's latest pregame forecast (research only, never canonical). */
export interface CandidateGameForecast {
  candidate: string; family: string | null; neural: boolean; market: MarketId; model_version: string;
  line: number | null; line_source: string; line_ref: string | null; sides: { label: string; p: number }[];
  fit_id: number | null; artifact_sha256: string; training_cutoff: string; dataset_version: string; generated_at: string;
  start_at_forecast: string; start_moved: boolean; inputs: Record<string, unknown>; research: true; canonical: false;
}
export interface CandidateGameView {
  mode: 'real' | 'demo'; game_id: string; sport?: Sport; start?: string; status?: string; as_of?: string; note: string;
  forecasts: CandidateGameForecast[]; without_forecast: { candidate: string; family: string; neural: boolean; reason: string }[];
}
