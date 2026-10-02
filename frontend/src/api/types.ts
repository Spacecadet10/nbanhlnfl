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
  market_home_prob: number | null; market_retrieved_at: string | null; correct: boolean | null; limitation: string;
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
  market: MarketId; title: string; policy: Policy; model_version: string; generated_at: string; cutoff_at: string;
  line: number | null; line_source: 'market' | 'model' | null; sides: MarketSide[]; pick: 'a' | 'b'; pick_label: string;
  confidence: number; meets_confidence: boolean; quote: MarketQuote | null; result: Settlement; limitations: string[];
}
export interface MarketRecordSummary {
  min_confidence: number | null; selected: number; settled: number; wins: number; losses: number; pushes: number;
  voids: number; pending: number; win_rate: number | null;
}
export interface MarketRecord { game_id: string; game_date: string; tipoff: string; home: Team; away: Team; forecast: MarketForecast; outcome: Outcome | null }
export interface MarketHistoryResponse {
  mode: Mode; sport: Sport; market: MarketId; policy: Policy; total: number; record: MarketRecordSummary; predictions: MarketRecord[];
}
export interface MarketPerformanceResponse {
  mode: Mode; sport: Sport; market: MarketId; policy: Policy; title: string; model_version: string | null; forecasts: number;
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
  outcome: Outcome | null; correct: boolean | null;
}

/** Observed record of the picks a confidence filter selects. Unscored games never count toward win_rate. */
export interface ConfidenceRecord {
  min_confidence: number | null; selected: number; settled: number; wins: number; losses: number; win_rate: number | null;
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
