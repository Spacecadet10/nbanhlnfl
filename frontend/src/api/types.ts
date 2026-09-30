// Mirrors backend/app/schemas.py (contract v2). Keep in sync with the API.
// `mode`: where data comes from (real = stored ESPN data + ledger; demo = synthetic fixtures).
// Prediction `run_type`: live (recorded before cutoff), backtest (retrospective) or demo.
export type RunType = 'demo' | 'backtest' | 'live';
export type Mode = 'real' | 'demo';

export interface Team { abbr: string; name: string }

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

export interface GameSummary {
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
}

export interface GamesResponse { mode: Mode; date: string; games: GameSummary[]; sources: SourceStatus[] }

export interface TeamStats {
  wins: number; losses: number; last10_wins: number; last10_games: number;
  points_for_avg: number | null; points_against_avg: number | null; rest_days: number | null;
}

export interface GameDetail extends GameSummary {
  stats_as_of: string;
  home_stats: TeamStats;
  away_stats: TeamStats;
  projected_score_available: boolean;
}

export interface PredictionRecord {
  game_id: string; run_type: RunType; game_date: string; tipoff: string; home: Team; away: Team;
  model_version: string; generated_at: string; cutoff_at: string; home_win_prob: number;
  outcome: Outcome | null; correct: boolean | null;
}

export interface HistoryResponse { mode: Mode; run_type: RunType; total: number; predictions: PredictionRecord[] }

export interface CalibrationBin {
  lower: number; upper: number; count: number;
  mean_predicted: number | null; observed_rate: number | null;
}

export interface BaselineScore { name: string; accuracy: number | null; brier_score: number | null; log_loss: number | null }

export interface PerformanceResponse {
  mode: Mode;
  run_type: RunType;
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
