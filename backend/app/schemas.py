"""API response contract (v2). Every payload carries a run type so demo, backtest and live data are never mixed up.

`mode` says where the data comes from ('real' = stored ESPN data and ledger forecasts, 'demo' = synthetic fixtures).
A prediction's `run_type` says what kind of forecast it is: 'live' (recorded before its cutoff), 'backtest'
(retrospective), or 'demo'.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

RunType = Literal["demo", "backtest", "live"]
Mode = Literal["real", "demo"]
GameStatus = Literal["scheduled", "in_progress", "final", "postponed", "canceled", "other"]
SeasonType = Literal["preseason", "regular", "play_in", "postseason", "other"]
Freshness = Literal["fresh", "stale", "missing"]


class Health(BaseModel):
    status: Literal["ok"]
    mode: Mode
    api_version: str


class Team(BaseModel):
    abbr: str
    name: str


class SourceStatus(BaseModel):
    name: str
    kind: Literal["schedule", "results", "odds", "forecasts"]
    mode: Mode
    last_updated: Optional[datetime]
    freshness: Freshness
    note: str


class Meta(BaseModel):
    mode: Mode
    default_date: date
    available_dates: list[date]
    teams: list[Team]
    primary_model: str
    last_refresh_at: Optional[datetime] = Field(description="Last successful data refresh job (real mode).")


class Prediction(BaseModel):
    model_version: str
    run_type: RunType
    generated_at: datetime = Field(description="When the forecast was produced.")
    cutoff_at: datetime = Field(description="Information cutoff. Live forecasts are generated before it.")
    home_win_prob: float = Field(ge=0, le=1)


class Market(BaseModel):
    bookmaker: str
    price_label: Literal["open", "current"]
    quote_time: Optional[datetime] = Field(description="Publication time, when the source provides one.")
    retrieved_at: datetime
    home_win_prob: float = Field(ge=0, le=1, description="De-vigged implied probability.")


class Outcome(BaseModel):
    home_score: int
    away_score: int
    home_won: bool


class GameSummary(BaseModel):
    game_id: str
    mode: Mode
    game_date: date = Field(description="US Eastern calendar date.")
    tipoff: datetime
    status: GameStatus
    season_type: SeasonType
    home: Team
    away: Team
    prediction: Optional[Prediction]
    forecast_note: Optional[str] = Field(description="Why there is no prediction, when there is none.")
    market: Optional[Market]
    outcome: Optional[Outcome]


class GamesResponse(BaseModel):
    mode: Mode
    date: date
    games: list[GameSummary]
    sources: list[SourceStatus]


class TeamStats(BaseModel):
    """Point-in-time: built only from results available at the game's cutoff."""
    wins: int
    losses: int
    last10_wins: int
    last10_games: int
    points_for_avg: Optional[float]
    points_against_avg: Optional[float]
    rest_days: Optional[float] = Field(description="Days since the team's previous scheduled game this season.")


class GameDetail(GameSummary):
    stats_as_of: datetime
    home_stats: TeamStats
    away_stats: TeamStats
    projected_score_available: bool = Field(
        description="False until a validated score model exists; the UI must not show projected scores."
    )


class PredictionRecord(BaseModel):
    game_id: str
    run_type: RunType
    game_date: date
    tipoff: datetime
    home: Team
    away: Team
    model_version: str
    generated_at: datetime
    cutoff_at: datetime
    home_win_prob: float
    outcome: Optional[Outcome]
    correct: Optional[bool]


class HistoryResponse(BaseModel):
    mode: Mode
    run_type: RunType
    total: int
    predictions: list[PredictionRecord]


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    count: int
    mean_predicted: Optional[float]
    observed_rate: Optional[float]


class MarketComparison(BaseModel):
    matched_games: int
    coverage: float = Field(description="Share of scored games with a market quote.")
    model_brier: Optional[float]
    market_brier: Optional[float]


class BaselineScore(BaseModel):
    name: str
    accuracy: Optional[float]
    brier_score: Optional[float]
    log_loss: Optional[float]


class PerformanceResponse(BaseModel):
    mode: Mode
    run_type: RunType
    title: str
    model_version: str
    sample_size: int
    date_from: Optional[date]
    date_to: Optional[date]
    accuracy: Optional[float]
    brier_score: Optional[float]
    log_loss: Optional[float]
    calibration: list[CalibrationBin]
    baselines: list[BaselineScore]
    market: MarketComparison
    note: str
