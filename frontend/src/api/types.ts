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
  kind: 'schedule' | 'results' | 'odds' | 'forecasts';
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
}
export interface Outcome { home_score: number; away_score: number; home_won: boolean }

export type Policy = 'pregame' | 'near_tipoff';

/** A separate forecast made shortly before tipoff from the freshest stored results. Never replaces `prediction`. */
export interface NearTipoffForecast {
  model_version: string; generated_at: string; cutoff_at: string; home_win_prob: number;
  pick: 'home' | 'away'; confidence: number; minutes_before_tipoff: number;
  results_refreshed_at: string | null; latest_result_available_at: string | null; results_used: number | null;
  market_home_prob: number | null; market_retrieved_at: string | null; correct: boolean | null; void?: boolean; limitation: string;
}

export interface SlatePick { policy: Policy; home_win_prob: number; pick: 'home' | 'away'; confidence: number; meets_confidence: boolean }
export interface SlateSelection { market: MarketId; policy: Policy; min_confidence: number | null; games: number; with_forecast: number; selected: number }

export interface AgreementSummary {
  policy: Policy;
  status: 'unavailable' | 'partial' | 'complete'; expected: number; available: number;
  agreeing: number; pick: 'home' | 'away' | null; home_votes: number; away_votes: number;
  abstentions: number; missing: string[]; generated_at: string | null;
  models: { name: string; model_version: string; home_win_prob: number; generated_at: string }[];
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
  /** NHL only: ESPN's latest probable-goalie listing per team, as retrieved; not used by any model. */
  goalies: { side: 'home' | 'away'; player_name: string | null; source_status: string; source_status_label: string | null; retrieved_at: string }[];
}

export type Settlement = 'win' | 'loss' | 'push' | 'void' | 'pending' | 'unscorable';
export interface MarketSide { side: 'a' | 'mid' | 'b'; label: string; probability: number }
export interface MarketQuote { bookmaker: string; retrieved_at: string; line: number | null; price_a: number; price_b: number; prob_a: number; prob_b: number }
/** One forecast at one exact line. line_source 'model' = no bookmaker line existed; the line is model-only. */
export interface MarketForecast {
  market: MarketId; title: string; policy: Policy; model_version: string; current_version?: boolean; generated_at: string; cutoff_at: string;
  line: number | null; line_source: 'market' | 'model' | null; sides: MarketSide[]; pick: 'a' | 'b'; pick_label: string;
  confidence: number; meets_confidence: boolean; quote: MarketQuote | null; result: Settlement; limitations: string[];
}
export interface MarketRecordSummary {
  min_confidence: number | null; selected: number; settled: number; wins: number; losses: number; pushes: number;
  voids: number; pending: number; unscorable: number; win_rate: number | null;
}
export interface MarketRecord { game_id: string; game_date: string; tipoff: string; home: Team; away: Team; forecast: MarketForecast; outcome: Outcome | null }
export interface MarketModelVersion { model_version: string; current: boolean; games: number; first_at: string; last_at: string }
export interface MarketHistoryResponse {
  mode: Mode; sport: Sport; market: MarketId; policy: Policy; model_version?: string | null; versions?: MarketModelVersion[]; total: number; record: MarketRecordSummary; predictions: MarketRecord[];
}
export interface MarketPerformanceResponse {
  mode: Mode; sport: Sport; market: MarketId; policy: Policy; title: string; model_version: string | null; versions?: MarketModelVersion[]; forecasts: number;
  record: MarketRecordSummary; brier_score: number | null; log_loss: number | null; line_sources: Record<string, number>;
  market_matched: number; model_log_loss_on_matched: number | null; market_log_loss_on_matched: number | null;
  reference: { label: string; by_line: Record<string, { n: number; log_loss: number; brier: number; pick_accuracy_excluding_mid: number | null; league_reference_log_loss: number }> } | null;
  limitations: string[]; note: string;
}

export interface PredictionRecord {
  game_id: string; run_type: RunType; game_date: string; tipoff: string; home: Team; away: Team;
  model_version: string; generated_at: string; cutoff_at: string; home_win_prob: number;
  /** The predicted winner and the model's probability for it: max(p_home, 1 - p_home). */
  pick: 'home' | 'away'; confidence: number;
  outcome: Outcome | null; correct: boolean | null; void?: boolean; unscorable?: boolean;
}

/** Observed record of the picks a confidence filter selects. Unscored games never count toward win_rate. */
/** selected = settled + pending + void + unscorable; settled = wins + losses. */
export interface ConfidenceRecord {
  min_confidence: number | null; selected: number; settled: number; wins: number; losses: number;
  pending: number; void: number; unscorable: number; win_rate: number | null;
}

export interface HistoryResponse {
  mode: Mode; run_type: RunType; policy: Policy; total: number; record: ConfidenceRecord; predictions: PredictionRecord[];
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
}
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
}
export interface TrackingCheck {
  mode: Mode; status: string; note?: string;
  folds: { season: number; chosen: string | null; threshold?: number | null; reason?: string;
    earlier?: { decided: number; coverage: number | null; win_rate: number; lift: number; lower_adjusted: number };
    later?: { decided: number; coverage: number | null; win_rate: number | null; ci95: [number, number] | null; matched: TrackingMatched[] } }[];
}
export interface TrackingGameMarket {
  market: MarketId; line: number | null; groups: number; counts: Record<string, number>; recorded_at: string; definition: string;
  picks: { group: string; kind: TrackingKind; side: Side; statistic: number; votes: Record<string, number> | null }[];
  incomplete: { group: string; missing: Record<string, string> }[];
}
export interface TrackingGame {
  mode: Mode; game_id: string; status: string; registry_sha256?: string; markets: TrackingGameMarket[]; note?: string;
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
  online: { member: string; schedule: string; last_update_input: string | null; next_update: string }[];
  updates: { run_id: number; at: string; step: string; member: string | null; outcome: 'failed' | 'skipped'; reason: string }[];
  inputs: Record<string, { last_success: string | null; stale: boolean; stale_after_hours: number }>;
  monitoring: { members: { market: string; member: string; settled: number; recent_log_loss: number; previous_log_loss: number | null;
    missing_rate: number; status?: string; difference?: number }[]; alerts: { member: string; market: string; alert: string }[]; note?: string };
  note: string;
}
