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
Sport = Literal["nba", "nhl"]
MarketId = Literal["winner", "reg3", "spread", "total", "team_total_home", "team_total_away"]
Settlement = Literal["win", "loss", "push", "void", "pending", "unscorable"]


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


class SportInfo(BaseModel):
    id: Sport
    label: str
    available: bool
    note: Optional[str] = None


class MarketInfo(BaseModel):
    id: MarketId
    title: str
    available: bool = Field(description="Whether forecasts of this market are recorded for this sport.")
    note: str = Field(description="What the market settles on, and its known limitations.")


class Meta(BaseModel):
    mode: Mode
    sport: Sport = "nba"
    sports: list[SportInfo] = []
    markets: list[MarketInfo] = []
    score_unit: Literal["points", "goals"] = "points"
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
    home_price: Optional[float] = Field(None, description="Home moneyline as quoted (American odds).")
    away_price: Optional[float] = Field(None, description="Away moneyline as quoted (American odds).")


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
    void: bool = Field(default=False, description="The start changed after the forecast, or the game was postponed or "
                                                  "cancelled: the forecast is never scored.")
    limitation: str


class Outcome(BaseModel):
    home_score: int
    away_score: int
    home_won: bool


class AgreementModel(BaseModel):
    name: str
    model_version: str
    home_win_prob: float
    generated_at: datetime


class AgreementSummary(BaseModel):
    policy: Policy
    status: Literal["unavailable", "partial", "complete"]
    expected: int
    available: int
    agreeing: int
    pick: Optional[Literal["home", "away"]]
    home_votes: int
    away_votes: int
    abstentions: int
    models: list[AgreementModel]
    missing: list[str]
    generated_at: Optional[datetime]


class GameSummary(BaseModel):
    agreement: Optional[AgreementSummary] = None
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
    market_forecast: Optional["MarketForecastOut"] = Field(
        default=None, description="The selected non-winner market's forecast under the selected policy, if any.")


class SlatePick(BaseModel):
    """One game's pick under one forecast policy. The slate filter and its counts use this and nothing else."""
    policy: Policy
    home_win_prob: float = Field(ge=0, le=1)
    pick: Literal["home", "away"]
    confidence: float = Field(ge=0.5, le=1)
    meets_confidence: bool


class SlateSelection(BaseModel):
    market: MarketId = "winner"
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
    market_forecasts: list["MarketForecastOut"] = Field(
        default=[], description="Latest forecast of every market and policy for this game (non-winner markets).")
    goalies: list["GoalieListingOut"] = Field(default=[], description="NHL only: latest goalie listing per team, if any.")


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
    unscorable: bool = Field(default=False, description="Final but without a usable result; never counted as pending.")
    lead_days: Optional[int] = Field(default=None, description="Live rows: the game's Eastern date minus the Eastern date "
                                                               "the forecast was generated (the registered horizon rule).")
    horizon_hours: Optional[float] = Field(default=None, description="Live rows: scheduled start minus generation, in hours.")
    period: Optional[str] = Field(default=None, description="Backtest rows: 'validation' (seasons used to choose the "
                                                            "model), 'test' (held-out) or another evaluation period.")
    void: bool = Field(default=False, description="The start (two-hour) or game date (pre-game archive) changed after the "
                                                  "forecast, or the game was postponed or cancelled: never scored.")


class ConfidenceRecord(BaseModel):
    """Observed record of the picks selected by a confidence filter. Unscored games never count toward the win rate."""
    min_confidence: Optional[float] = Field(description="The filter applied; null means all picks.")
    selected: int = Field(description="Picks at or above the threshold, settled or not. "
                                      "selected = settled + pending + void + unscorable.")
    settled: int = Field(description="Selected picks whose game has a final result (wins + losses).")
    wins: int
    losses: int
    pending: int = Field(description="Awaiting a final result (scheduled or in progress).")
    void: int = Field(description="Never scored: the game moved after the forecast, or was postponed or cancelled.")
    unscorable: int = Field(description="Final but without a usable result. The store requires scores on a final game, "
                                        "so this is 0 for the winner market; it is reported so the identity is explicit.")
    win_rate: Optional[float] = Field(description="wins / settled; null when nothing is settled.")


class RecordPart(BaseModel):
    """One part of a record that is also reported whole: nothing is dropped, the parts add up to the full record."""
    key: str
    label: str
    record: ConfidenceRecord


class HistoryResponse(BaseModel):
    mode: Mode
    run_type: RunType
    policy: Policy = Field(default="pregame", description="Which live forecast policy the rows belong to.")
    total: int
    record: ConfidenceRecord
    parts: list[RecordPart] = Field(default_factory=list, description="The same record split by lead time (pre-game "
                                    "archive; bands registered in docs/EVALUATION_PLAN.md) or by evaluation period (backtest).")
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
    """Each row is exactly one of settled (scored), void, unscorable (final but unscored) or pending (no final result)."""
    settled = [r for r in rows if r.correct is not None]
    wins = sum(r.correct for r in settled)
    void = sum(r.correct is None and r.void for r in rows)
    unscorable = sum(r.correct is None and not r.void and r.unscorable for r in rows)
    return ConfidenceRecord(min_confidence=min_confidence, selected=len(rows), settled=len(settled), wins=wins,
                            losses=len(settled) - wins, pending=len(rows) - len(settled) - void - unscorable, void=void,
                            unscorable=unscorable, win_rate=wins / len(settled) if settled else None)


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
    accuracy: Optional[float] = Field(description="Picked-winner rate; null for a baseline that never picks a side (always 50%).")
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


class GoalieListingOut(BaseModel):
    """ESPN's probable-starting-goalie listing as last retrieved before now (NHL). Not used by any model."""
    side: Literal["home", "away"]
    player_name: Optional[str]
    source_status: str = Field(description="As published: 'expected', 'confirmed', ...; 'none_listed' when no goalie was listed.")
    source_status_label: Optional[str]
    retrieved_at: datetime = Field(description="When we retrieved this listing (the source publishes no announcement time).")


class MarketSide(BaseModel):
    side: Literal["a", "mid", "b"]
    label: str
    probability: float = Field(ge=0, le=1)


class MarketQuoteOut(BaseModel):
    """The bookmaker quote at the forecast's exact line, retrieved no later than the forecast."""
    bookmaker: str
    retrieved_at: datetime
    line: Optional[float]
    price_a: int
    price_b: int
    prob_a: float = Field(description="De-vigged (proportional, two-way) probability of side a.")
    prob_b: float


class MarketForecastOut(BaseModel):
    market: MarketId
    title: str
    policy: Policy
    model_version: str
    current_version: bool = Field(default=True, description="False: made by an earlier market model version (archive).")
    generated_at: datetime
    cutoff_at: datetime
    line: Optional[float] = Field(description="Exact line of this forecast: home handicap (spread) or threshold.")
    line_source: Optional[Literal["market", "model"]] = Field(
        description="'market' = the bookmaker's line at generation; 'model' = no line was quoted, model-only line.")
    earlier_line_rule: bool = Field(default=False, description="A model-only line set by the rule used before 2026-10-02 "
                                                                "(could sit far from an even split). Label only.")
    sides: list[MarketSide]
    pick: Literal["a", "b"]
    pick_label: str
    confidence: float = Field(ge=0, le=1, description="Model probability of the picked side (a push is never picked).")
    meets_confidence: bool = True
    quote: Optional[MarketQuoteOut]
    result: Settlement
    limitations: list[str]


class MarketRecordSummary(BaseModel):
    min_confidence: Optional[float]
    selected: int
    settled: int = Field(description="Win, loss or push.")
    wins: int
    losses: int
    pushes: int
    voids: int
    pending: int = Field(description="Awaiting a final result.")
    unscorable: int = Field(description="Final but cannot be settled (e.g. no regulation decision for the 3-way market). "
                                        "selected = wins + losses + pushes + voids + pending + unscorable.")
    quoted_decided: int = Field(default=0, description="Decided picks (win or loss) that had a bookmaker quote at their line.")
    quoted_wins: int = Field(default=0, description="Wins among those quoted, decided picks.")
    mean_implied: Optional[float] = Field(default=None, description="Mean de-vigged bookmaker probability of the picked side "
                                          "on those picks: what the bookmaker expected the win rate to be.")
    win_rate: Optional[float] = Field(description="wins / (wins + losses); pushes and voids excluded.")


class MarketRecord(BaseModel):
    game_id: str
    game_date: date
    tipoff: datetime
    home: Team
    away: Team
    forecast: MarketForecastOut
    outcome: Optional[Outcome]
    lead_days: Optional[int] = Field(default=None, description="Game's Eastern date minus the forecast's Eastern generation date.")
    horizon_hours: Optional[float] = None


class MarketModelVersion(BaseModel):
    model_version: str
    current: bool = Field(description="The sport's sealed market model now; records default to it.")
    games: int
    first_at: datetime
    last_at: datetime


class MarketHistoryResponse(BaseModel):
    mode: Mode
    sport: Sport
    market: MarketId
    policy: Policy
    model_version: Optional[str] = Field(default=None, description="The one model version these rows and the record cover.")
    versions: list[MarketModelVersion] = Field(default_factory=list, description="Every stored version (archive views).")
    total: int
    record: MarketRecordSummary
    parts: list["MarketRecordPart"] = Field(default_factory=list, description="The record split by lead time (pre-game archive).")
    predictions: list[MarketRecord]


class MarketRecordPart(BaseModel):
    key: str
    label: str
    record: MarketRecordSummary


MarketHistoryResponse.model_rebuild()


class MarketPerformanceResponse(BaseModel):
    mode: Mode
    sport: Sport
    market: MarketId
    policy: Policy
    title: str
    model_version: Optional[str]
    versions: list[MarketModelVersion] = Field(default_factory=list)
    forecasts: int
    record: MarketRecordSummary
    brier_score: Optional[float] = Field(description="Multi-outcome Brier (sides a / push-draw / b) on settled forecasts.")
    log_loss: Optional[float]
    line_sources: dict[str, int]
    market_matched: int = Field(description="Settled forecasts made at a quoted bookmaker line.")
    model_log_loss_on_matched: Optional[float]
    market_log_loss_on_matched: Optional[float]
    reference: Optional[dict] = Field(description="One-time historical test of this market, when one exists (labelled).")
    limitations: list[str]
    note: str


GameSummary.model_rebuild()
GameDetail.model_rebuild()
