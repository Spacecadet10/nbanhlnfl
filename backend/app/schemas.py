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


Policy = Literal["pregame", "near_tipoff"]


class NearTipoffForecast(BaseModel):
    """A separate forecast made shortly before tipoff from the freshest stored results. Never replaces `prediction`."""
    model_version: str
    generated_at: datetime
    cutoff_at: datetime = Field(description="The scheduled tipoff when the forecast was made.")
    home_win_prob: float = Field(ge=0, le=1)
    pick: Literal["home", "away"]
    confidence: float = Field(ge=0.5, le=1)
    minutes_before_tipoff: float
    results_refreshed_at: Optional[datetime] = Field(description="Last successful schedule and results refresh used.")
    latest_result_available_at: Optional[datetime] = Field(description="Newest result included in the model state.")
    results_used: Optional[int]
    market_home_prob: Optional[float] = Field(description="De-vigged market probability from the latest quote retrieved "
                                                          "at or before the forecast; null when there was none.")
    market_retrieved_at: Optional[datetime]
    correct: Optional[bool] = Field(description="Whether the pick won; null until the game is final.")
    limitation: str


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
    near_tipoff: Optional[NearTipoffForecast] = Field(
        default=None, description="Near-tipoff forecast, when one was recorded. Separate from `prediction`.")
    slate_pick: Optional["SlatePick"] = Field(
        default=None, description="The pick under the slate's selected forecast policy, with whether it meets the "
                                  "requested confidence. Null when the game has no forecast under that policy.")


class SlatePick(BaseModel):
    """One game's pick under one forecast policy. The slate filter and its counts use this and nothing else."""
    policy: Policy
    home_win_prob: float = Field(ge=0, le=1)
    pick: Literal["home", "away"]
    confidence: float = Field(ge=0.5, le=1)
    meets_confidence: bool


class SlateSelection(BaseModel):
    policy: Policy
    min_confidence: Optional[float]
    games: int
    with_forecast: int = Field(description="Games that have a forecast under this policy.")
    selected: int = Field(description="Games whose pick under this policy meets the confidence level.")


class GamesResponse(BaseModel):
    mode: Mode
    date: date
    selection: SlateSelection
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
    pick: Literal["home", "away"] = Field(description="The predicted winner: home when home_win_prob >= 0.5.")
    confidence: float = Field(ge=0.5, le=1, description="Model probability of the predicted winner: "
                                                        "max(home_win_prob, 1 - home_win_prob).")
    outcome: Optional[Outcome]
    correct: Optional[bool]


class ConfidenceRecord(BaseModel):
    """Observed record of the picks selected by a confidence filter. Unscored games never count toward the win rate."""
    min_confidence: Optional[float] = Field(description="The filter applied; null means all picks.")
    selected: int = Field(description="Picks at or above the threshold, settled or not.")
    settled: int = Field(description="Selected picks whose game has a final result.")
    wins: int
    losses: int
    win_rate: Optional[float] = Field(description="wins / settled; null when nothing is settled.")


class HistoryResponse(BaseModel):
    mode: Mode
    run_type: RunType
    policy: Policy = Field(default="pregame", description="Which live forecast policy the rows belong to.")
    total: int
    record: ConfidenceRecord
    predictions: list[PredictionRecord]


CONFIDENCE_TOLERANCE = 1e-9   # 1 - 0.3 must count as 0.7


def pick_confidence(home_win_prob: float) -> float:
    return max(home_win_prob, 1 - home_win_prob)


def meets_confidence(home_win_prob: float, min_confidence: Optional[float]) -> bool:
    return min_confidence is None or pick_confidence(home_win_prob) >= min_confidence - CONFIDENCE_TOLERANCE


def apply_slate_policy(games: list[GameSummary], policy: str, min_confidence: Optional[float]) -> SlateSelection:
    """Sets each game's `slate_pick` from the selected policy only (never falling back to the other forecast) and
    returns the counts. `pregame` uses `prediction`; `near_tipoff` uses `near_tipoff`."""
    for g in games:
        source = g.near_tipoff if policy == "near_tipoff" else g.prediction
        g.slate_pick = None if source is None else SlatePick(
            policy=policy, home_win_prob=source.home_win_prob, pick="home" if source.home_win_prob >= 0.5 else "away",
            confidence=pick_confidence(source.home_win_prob),
            meets_confidence=meets_confidence(source.home_win_prob, min_confidence))
    picks = [g.slate_pick for g in games if g.slate_pick is not None]
    return SlateSelection(policy=policy, min_confidence=min_confidence, games=len(games), with_forecast=len(picks),
                          selected=sum(p.meets_confidence for p in picks))


def confidence_record(rows: list[PredictionRecord], min_confidence: Optional[float]) -> ConfidenceRecord:
    settled = [r for r in rows if r.correct is not None]
    wins = sum(r.correct for r in settled)
    return ConfidenceRecord(min_confidence=min_confidence, selected=len(rows), settled=len(settled), wins=wins,
                            losses=len(settled) - wins, win_rate=wins / len(settled) if settled else None)


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
    policy: Policy = "pregame"
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


GameSummary.model_rebuild()
