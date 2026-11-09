"""API response contract (v2). Every payload carries a run type so demo, backtest and live data are never mixed up.

`mode` says where the data comes from ('real' = stored ESPN data and ledger forecasts, 'demo' = synthetic fixtures).
A prediction's `run_type` says what kind of forecast it is: 'live' (recorded before its cutoff), 'backtest'
(retrospective), or 'demo'.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_serializer

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
    data_snapshot_at: Optional[str] = Field(default=None, description="When the served stores were copied from the live "
                                            "ones (development launcher, scripts/dev.sh); null when serving the stores in place.")


class DataFreshness(BaseModel):
    """How current the served data is, per sport (docs/REFRESH_PLAN.md). The browser polls this cheap read and refetches
    game/forecast views only when `revision` changes. Three separate times: when the source was last fetched successfully,
    when the served copy was published (copies only), and when the latest forecast was generated (NBA/NHL only)."""
    sport: Literal["nba", "nhl", "nfl"]
    as_of: str
    mode: Literal["live", "auto_snapshot", "frozen_snapshot", "demo"] = Field(description="live: the API reads the stores the "
        "jobs write. auto_snapshot: a copy republished periodically. frozen_snapshot: a one-time copy that will not update.")
    revision: str = Field(description="Changes when a stored game, result revision or forecast row changes")
    activity: Optional[Literal["active", "pregame", "idle"]] = None
    interval_seconds: Optional[int] = None
    source_fetched_at: Optional[str] = Field(default=None, description="Last successful schedule/results refresh")
    source_state: Literal["fresh", "stale", "never", "unknown"]
    stale_after_seconds: Optional[int] = None
    last_failed_at: Optional[str] = None
    last_error: Optional[str] = None
    failing: bool = False
    next_check_at: Optional[str] = Field(default=None, description="live: when the next refresh is due by the policy; "
                                         "auto_snapshot: when the next publication is expected; otherwise null")
    published_at: Optional[str] = None
    publish_interval_seconds: Optional[int] = None
    publish_error: Optional[str] = None
    forecasts_supported: bool
    forecast_generated_at: Optional[str] = None
    forecasts_note: Optional[str] = None


class Team(BaseModel):
    abbr: str
    name: str


class SourceStatus(BaseModel):
    name: str
    kind: Literal["schedule", "results", "odds", "forecasts", "injuries", "players", "canonical"]
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


Policy = Literal["pregame", "near_tipoff", "canonical"]   # canonical: game_forecasts (app/coverage.canonical); the others are legacy


class CanonicalRecord(BaseModel):
    """The selected game_forecasts row: the one record every page shows and grades for this game and market."""
    forecast_id: int
    source: Literal["adaptive", "primary_recorded", "primary_direct"] = Field(
        description="adaptive = the intended primary (ensemble); primary_* = the labelled sealed baseline fallback.")
    fallback_rank: int
    fallback_reason: Optional[str] = Field(description="Why each earlier source in the order was not used.")
    model_version: str
    generated_at: datetime
    start_at: datetime = Field(description="The scheduled start the forecast was made for.")
    minutes_before_start: float
    p: list[float] = Field(description="Probabilities of sides a / mid / b (mid: push or regulation draw).")
    home_win_prob: Optional[float] = None
    pick: Optional[Literal["home", "away"]] = Field(default=None, description="Winner market only.")
    side: Literal["a", "mid", "b"] = Field(description="The picked side: the most likely outcome (three-way: of all three, "
                                                       "a draw can be the pick; ties go to a, then mid, then b).")
    pick_label: Optional[str] = Field(default=None, description="The pick in words at the forecast's line, e.g. 'BOS -1.5'.")
    opposite_pick_label: Optional[str] = Field(default=None, description="Other side at the same stored line; no single opposite for three-way markets. Not a recorded recommendation.")
    confidence: float = Field(description="Probability of the pick: two-way markets excluding the push; three-way over all three.")
    line: Optional[float]
    line_source: Literal["bookmaker", "model", "none"]
    market_quote_id: Optional[int] = Field(default=None, description="The bookmaker quote the forecast's source used, if any.")


class CanonicalAttempt(BaseModel):
    forecast_id: int
    generated_at: datetime
    reason: str


class CanonicalGradeRevision(BaseModel):
    """The forecast's grade under one recorded final result (result_revisions); a correction adds an entry."""
    revision: int
    observed_at: datetime = Field(description="The retrieval that delivered this result.")
    change: Literal["first", "decision_known", "correction"] = Field(description="decision_known: same score, the NHL "
                                                                     "regulation decision became known (not a correction).")
    basis: Literal["observed", "backfill"] = Field(description="backfill: the result stored when revisions began; any "
                                                               "earlier versions of it were not recorded.")
    home_score: Optional[int]
    away_score: Optional[int]
    decision: Optional[str]
    grade: Literal["win", "loss", "push", "pending", "void", "unscorable", "unresolved"]


class ForecastPercentageRecord(BaseModel):
    """Current observed live record for the exact displayed percentage, excluding the target game."""
    sport: Literal['nba', 'nhl', 'nfl']
    market: MarketId
    market_title: str
    percentage: str
    side: Literal['a', 'mid', 'b']
    direction: str
    source: Literal['adaptive', 'primary_recorded', 'primary_direct']
    model_version: str
    line_source: Literal['bookmaker', 'model', 'none']
    state: Literal['more_right', 'more_wrong', 'neutral', 'no_history']
    wins: int
    losses: int
    settled: int
    win_rate: Optional[float]
    loss_rate: Optional[float]
    ci95: Optional[list[float]]
    small_sample: bool
    excluded: dict[str, int]
    as_of: datetime


class CanonicalForecast(BaseModel):
    """One game's canonical forecast for one market, or why there is none. Graded on read from the stored result."""
    market: MarketId
    state: Literal["forecast", "missing", "upcoming", "unvalidated", "not_recorded", "not_forecastable"]
    reason: Optional[str] = Field(description="Why there is no canonical forecast (every state except 'forecast').")
    record: Optional[CanonicalRecord]
    grade: Optional[Literal["win", "loss", "push", "pending", "void", "unscorable", "unresolved"]]
    result_side: Optional[Literal["a", "mid", "b"]] = Field(default=None, description="The realised side once final (forecast "
                                                                                     "state only).")
    result_label: Optional[str] = None
    grade_history: list[CanonicalGradeRevision] = Field(default=[], description="The grade under each recorded final result, "
                                                        "oldest first; the last one is `grade`.")
    regraded: bool = Field(default=False, description="A result correction (score or decision) changed this forecast's grade.")
    later_failure: Optional[CanonicalAttempt] = Field(description="A later attempt before the start that recorded no "
                                                                  "forecast; the earlier forecast stays the canonical one.")
    attempts: int = Field(description="Rows recorded for this game and market before the start.")
    recording_began_at: Optional[datetime]
    percentage_history: Optional[ForecastPercentageRecord] = None


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
    void: bool = Field(default=False, description="The game's start (two-hour cohort) or Eastern date (archive cohort) "
                                                  "changed after these forecasts: they are never scored and are not current.")
    void_reason: Optional[str] = None


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
    canonical: Optional[CanonicalForecast] = Field(
        default=None, description="The canonical forecast (real mode; null in demo mode): of the winner market, or of the "
                                  "slate's market when the slate is read with the canonical policy.")


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
    below_threshold: Optional[int] = Field(default=None, description="Canonical policy: games with a forecast whose pick is "
                                           "below the confidence level (the only games the filter hides).")
    without_forecast: Optional[dict[str, int]] = Field(default=None, description="Canonical policy: games without a "
                                                       "canonical forecast by state (missing, unvalidated, upcoming, ...); "
                                                       "never hidden by the confidence filter.")

    @model_serializer(mode="wrap")
    def _canonical_counts_only_when_set(self, handler):
        """The legacy policies' selection keeps its earlier shape: the canonical counts appear only when set."""
        out = handler(self)
        for k in ("below_threshold", "without_forecast"):
            if out.get(k) is None:
                out.pop(k, None)
        return out


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
    canonical_markets: list[CanonicalForecast] = Field(
        default=[], description="The canonical forecast of every market of the sport (real mode), winner first.")
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
    returns the counts. `pregame` uses `prediction`; `near_tipoff` uses `near_tipoff`; `canonical` uses the canonical
    record (only in state 'forecast')."""
    for g in games:
        if policy == "canonical":
            source = g.canonical.record if g.canonical is not None and g.canonical.state == "forecast" else None
        else:
            source = g.near_tipoff if policy == "near_tipoff" else g.prediction
        g.slate_pick = None if source is None else SlatePick(
            policy=policy, home_win_prob=source.home_win_prob, pick="home" if source.home_win_prob >= 0.5 else "away",
            confidence=pick_confidence(source.home_win_prob),
            meets_confidence=meets_confidence(source.home_win_prob, min_confidence))
    picks = [g.slate_pick for g in games if g.slate_pick is not None]
    selected = sum(p.meets_confidence for p in picks)
    extra = {}
    if policy == "canonical":
        without: dict[str, int] = {}
        for g in games:
            if g.slate_pick is None and g.canonical is not None:
                without[g.canonical.state] = without.get(g.canonical.state, 0) + 1
        extra = {"below_threshold": len(picks) - selected, "without_forecast": without}
    return SlateSelection(policy=policy, min_confidence=min_confidence, games=len(games), with_forecast=len(picks),
                          selected=selected, **extra)


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
    unmatched: Optional[dict[str, int]] = Field(default=None, description="Canonical non-winner markets: scored forecasts "
                                                "without a valid comparison, by reason.")

    @model_serializer(mode="wrap")
    def _unmatched_only_when_set(self, handler):
        out = handler(self)
        if out.get("unmatched") is None:
            out.pop("unmatched", None)
        return out


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


class CanonicalThreshold(BaseModel):
    """The record of forecasts whose confidence is at least `threshold` (a filter of the record, never of recording)."""
    threshold: float
    forecasts: int
    graded: int = Field(description="Wins + losses, as in the summary (pushes, voids and pending are counted apart).")
    win: int
    loss: int
    push: int
    pending: int
    void: int
    unscorable: int
    unresolved: int
    win_rate: Optional[float] = Field(description="win / (win + loss); null when nothing is graded.")


class CanonicalSummary(BaseModel):
    """Started competitive games (regular season, play-in, playoffs): a game is either forecast or missing (a coverage
    gap, never a loss). Grades and metrics cover settled forecasts only."""
    games: int
    forecast: int
    missing: int
    coverage: Optional[float]
    grades: dict[str, int]
    graded: int = Field(description="Wins + losses.")
    regraded: int = Field(default=0, description="Forecasts whose grade a corrected result changed (each shows its history).")
    accuracy: Optional[float]
    brier_score: Optional[float]
    log_loss: Optional[float]
    by_source: dict[str, int]
    model_versions: list[dict]
    missing_reasons: list[dict]
    thresholds: list[CanonicalThreshold] = Field(default=[], description="The market's reporting grid: 50-90% for two-way "
                                                 "markets, 35-70% for the regulation three-way market.")


class CanonicalRow(BaseModel):
    game_id: str
    game_date: date
    tipoff: datetime
    status: GameStatus
    season_type: SeasonType
    home: Team
    away: Team
    outcome: Optional[Outcome]
    canonical: CanonicalForecast


class CanonicalHistoryResponse(BaseModel):
    mode: Mode
    market: MarketId
    as_of: datetime
    recording_began_at: Optional[datetime]
    total: int
    record: CanonicalSummary = Field(description="Every matching started competitive game, not only the rows shown.")
    preseason_games: int
    rows: list[CanonicalRow]


class CanonicalPreseason(BaseModel):
    games: int
    unvalidated: int
    experimental_rows: int = Field(description="Experimental fallback rows recorded for these games; never canonical, "
                                               "never in the competitive record.")
    note: str


class CanonicalPerformanceResponse(BaseModel):
    mode: Mode
    market: MarketId
    title: str
    note: str
    as_of: datetime
    recording_began_at: Optional[datetime]
    date_from: Optional[date]
    date_to: Optional[date]
    competitive: CanonicalSummary
    calibration: list[CalibrationBin]
    market_comparison: MarketComparison
    preseason: CanonicalPreseason


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


# --- NFL (data-only store; docs/NFL_PLAN.md). Schedule, results and facts about when they were learned. There is no NFL
# forecast, odds, injury or metric field: capabilities say what is not available and why.
class NflTeam(BaseModel):
    team_id: str
    abbr: str
    name: str


class NflCapability(BaseModel):
    id: str
    label: str
    available: bool
    reason: str


class NflGame(BaseModel):
    game_id: str
    season: int = Field(description="ESPN season year: the year the season starts (2024 = 2024-25).")
    season_type: SeasonType
    kickoff: datetime
    status: GameStatus
    home: NflTeam
    away: NflTeam
    home_score: Optional[int]
    away_score: Optional[int]
    winner: Optional[Literal["home", "away", "tie"]] = Field(description="Final games only; a tie is a result.")
    neutral_site: bool
    first_seen_at: datetime
    updated_at: datetime
    result_available_at: Optional[datetime]
    result_availability_basis: Optional[Literal["observed_retrieval", "assumed_tipoff_plus_4h"]]
    forecasts: Optional[list[CanonicalForecast]] = None
    recommended_pick: Optional[str] = None


class NflMeta(BaseModel):
    mode: Literal["real"]
    sport: Literal["nfl"]
    label: str
    available: bool
    default_date: Optional[date]
    game_dates: list[date]
    seasons: list[int]
    games_stored: int
    last_refresh_at: Optional[datetime]
    capabilities: list[NflCapability]


class NflGamesResponse(BaseModel):
    date: date
    games: list[NflGame]
    prev_date: Optional[date]
    next_date: Optional[date]
    last_refresh_at: Optional[datetime]


class NflGameDetail(BaseModel):
    game: NflGame
    head_to_head: list[NflGame]
    home_recent: list[NflGame]
    away_recent: list[NflGame]
    capabilities: list[NflCapability]


# --- European soccer (data only; app/soccer.py). No forecast, odds, injury or metric field exists for soccer. Results keep
# draws, extra time, penalties and two-legged qualification apart; a field the source cannot establish is null.
SoccerCompetitionId = Literal["eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "uefa.europa",
                              # national teams (migration 0039, app/intl_live.py)
                              "uefa.nations", "concacaf.nations.league", "fifa.friendly", "caf.nations_qual", "uefa.euroq",
                              "afc.cupq", "fifa.worldq.uefa", "fifa.worldq.conmebol", "fifa.worldq.concacaf",
                              "fifa.worldq.afc", "fifa.worldq.caf", "fifa.worldq.ofc", "uefa.euro", "conmebol.america",
                              "caf.nations", "afc.asian.cup", "concacaf.gold", "fifa.world"]
SoccerSide = Literal["home", "away", "draw"]


class SoccerCompetition(BaseModel):
    id: SoccerCompetitionId
    label: str
    matches_stored: int


class SoccerMatch(BaseModel):
    match_id: str
    competition_id: SoccerCompetitionId
    competition: str
    event_id: str
    season: int = Field(description="ESPN season year: the year the season starts (2026 = 2026-27).")
    season_type_id: int = Field(description="ESPN's competition-specific numeric season type, as delivered.")
    stage: Optional[str]
    stage_label: Optional[str]
    kickoff: datetime
    status: Literal["scheduled", "in_progress", "final", "postponed", "canceled", "abandoned", "suspended", "other"]
    source_status: str = Field(description="ESPN status name as delivered, e.g. STATUS_FULL_TIME, STATUS_FINAL_PEN.")
    home: NflTeam
    away: NflTeam
    neutral_site: Optional[bool] = Field(description="Null: the source does not say.")
    venue: Optional[str]
    decided_in: Optional[Literal["regulation", "extra_time", "penalties", "unknown"]] = Field(description="'unknown': ESPN's "
        "status, period, clock and goal minutes disagree about how the match ended; never guessed.")
    decision_note: Optional[str] = Field(description="What disagreed, when decided_in is 'unknown'.")
    home_score: Optional[int] = Field(description="Goals when play ended (after extra time if played; never shoot-out kicks).")
    away_score: Optional[int]
    regulation_result: Optional[SoccerSide] = Field(description="90-minute result; only when the match ended in regulation. "
                                                    "Null after extra time: ESPN score fields include extra time; a 90-minute score derived from goal events is not validated yet.")
    end_of_play_result: Optional[SoccerSide] = Field(description="From home_score/away_score (after extra time if played).")
    shootout_winner: Optional[Literal["home", "away"]]
    home_shootout: Optional[int]
    away_shootout: Optional[int]
    leg: Optional[int]
    aggregate_home: Optional[int]
    aggregate_away: Optional[int]
    qualified_team_id: Optional[str] = Field(description="Who went through a decided two-legged tie (a tie fact, not this "
                                             "match's result).")
    note: Optional[str]
    first_seen_at: datetime
    updated_at: datetime
    result_available_at: Optional[datetime]
    result_availability_basis: Optional[Literal["observed_retrieval", "assumed_tipoff_plus_4h"]]
    tie_facts_available_at: Optional[datetime] = Field(description="When the current aggregate / qualifier / shoot-out "
        "became known (own time; never earlier than the retrieval that delivered them).")


class SoccerMeta(BaseModel):
    mode: Literal["real"]
    sport: Literal["soccer"]
    competition_id: SoccerCompetitionId
    competition: str
    competitions: list[SoccerCompetition]
    default_date: Optional[date]
    match_dates: list[date]
    fetched_dates: list[date] = Field(description="Scoreboard days requested for this competition; other days were never asked for.")
    seasons: list[int]
    matches_stored: int
    last_refresh_at: Optional[datetime]
    forecast_state: Literal["recorded", "awaiting", "out_of_scope", "no_live_store"] = Field(
        description="Live forecasts of this competition in this store: recorded; in the sealed scope but none recorded yet; "
                    "outside the sealed scope; or a store without live forecast tables.")
    capabilities: list[NflCapability]


class SoccerMatchesResponse(BaseModel):
    competition_id: SoccerCompetitionId
    date: date
    fetched: bool = Field(description="Whether this day's scoreboard was requested for the competition.")
    matches: list[SoccerMatch]
    prev_date: Optional[date]
    next_date: Optional[date]
    last_refresh_at: Optional[datetime]
    live_forecasts: dict[str, dict] = Field(default_factory=dict, description="match_id -> the latest recorded live 90-minute "
                                            "forecast (as in /api/soccer/live) of the listed matches that have one. Kept "
                                            "beside the matches: a match record itself never carries a forecast.")


class SoccerResultRevision(BaseModel):
    revision: int
    decided_in: Literal["regulation", "extra_time", "penalties", "unknown"]
    home_score: int
    away_score: int
    home_shootout: Optional[int]
    away_shootout: Optional[int]
    aggregate_home: Optional[int]
    aggregate_away: Optional[int]
    qualified_team_id: Optional[str]
    home_team_id: Optional[str] = Field(description="Null for revisions recorded before teams were stored.")
    away_team_id: Optional[str]
    observed_at: datetime
    basis: Literal["observed", "reclassified"] = Field(description="'reclassified': the same cached retrieval re-read by a "
        "newer importer, not a result the source delivered again.")


class SoccerMatchDetail(BaseModel):
    match: SoccerMatch
    head_to_head: list[SoccerMatch]
    home_recent: list[SoccerMatch]
    away_recent: list[SoccerMatch]
    result_revisions: list[SoccerResultRevision]
    live_forecast: Optional[dict] = Field(None, description="This match's latest recorded live 90-minute forecast "
                                                            "(as in /api/soccer/live), or null when none was recorded.")
    forecast_state: Literal["recorded", "awaiting", "out_of_scope", "no_live_store"]
    capabilities: list[NflCapability]


# --- Soccer research (openfootball history + the append-only 1X2 forecast ledger; app/soccer_forecasts.py). Every forecast
# carries its run_type and label: 'backtest' rows are retrospective research forecasts, never Live.
SoccerGrading = Literal["gradable", "not_final", "cancelled", "awarded", "extra_time_noted", "deciding_match_unverified"]


class SoccerClub(BaseModel):
    club_id: str
    name: str
    country: str


class SoccerHistMatch(BaseModel):
    match_id: str
    competition_id: SoccerCompetitionId
    season: int
    round: str
    stage: Literal["league", "group", "league_phase", "knockout"]
    local_date: str = Field(description="Listed local date; the source states no time zone.")
    local_time: Optional[str] = Field(description="Listed local time (zone not stated); null when the source lists none.")
    local_time_listed: Optional[bool] = Field(description="False: the time was carried from the line above in the source.")
    home: SoccerClub
    away: SoccerClub
    status: Literal["final", "scheduled", "result_missing", "cancelled", "awarded"]
    ft_home: Optional[int] = Field(description="90-minute score as the source gives it.")
    ft_away: Optional[int]
    ht_home: Optional[int]
    ht_away: Optional[int]
    aet_home: Optional[int]
    aet_away: Optional[int]
    pen_home: Optional[int]
    pen_away: Optional[int]
    regulation_result: Optional[SoccerSide] = Field(description="Only for matches graded on the 90-minute result.")
    grading: SoccerGrading
    grading_note: Optional[str]
    result_available_at: Optional[datetime]
    result_availability_basis: Optional[Literal["assumed_next_day_0600Z", "observed_retrieval", "observed_correction"]]


class SoccerForecast(BaseModel):
    forecast_id: int
    run_id: str
    run_type: Literal["backtest", "reserved_test", "prospective"]
    period: Literal["development_tune", "development_calibration", "validation", "reserved_test", "prospective"]
    model: Literal["frequency", "elo", "poisson"]
    label: str
    calibrated: bool
    market: Literal["1x2_regulation"]
    version: int
    p_home: float
    p_draw: float
    p_away: float
    cutoff_at: datetime = Field(description="Information cutoff the forecast used.")
    computed_at: datetime = Field(description="When the numbers were computed (a backtest: long after the match).")
    generated_at: datetime
    published_at: Optional[datetime] = Field(description="Null: never published as a live forecast.")
    is_selected_candidate: bool


class SoccerResearchRun(BaseModel):
    run_id: str
    run_type: Literal["backtest", "reserved_test", "prospective"]
    period: str
    model: str
    label: str
    calibrated: bool
    protocol_sha256: str
    created_at: datetime
    is_selected_candidate: bool
    forecasts: int
    first_date: Optional[str]
    last_date: Optional[str]


class SoccerResearchCoverage(BaseModel):
    first_season: Optional[int]
    last_season: Optional[int]
    matches: int


class SoccerResearchMeta(BaseModel):
    competition_id: SoccerCompetitionId
    runs: list[SoccerResearchRun]
    forecast_dates: list[str]
    history: SoccerResearchCoverage
    label: str
    market: Literal["1x2_regulation"]
    notice: str


class SoccerResearchMatch(SoccerHistMatch):
    forecasts: list[SoccerForecast]


class SoccerResearchMatches(BaseModel):
    competition_id: SoccerCompetitionId
    date: str
    matches: list[SoccerResearchMatch]


class SoccerHistRevision(BaseModel):
    revision: int
    local_date: str
    local_time: Optional[str]
    status: str
    ft_home: Optional[int]
    ft_away: Optional[int]
    aet_home: Optional[int]
    aet_away: Optional[int]
    pen_home: Optional[int]
    pen_away: Optional[int]
    deciding: int
    in_file: int
    result_available_at: Optional[str]
    result_availability_basis: Optional[str]
    observed_at: str


class SoccerSourceFile(BaseModel):
    repo: str
    commit_sha: str
    path: str
    retrieved_at: str


class SoccerResearchMatchDetail(BaseModel):
    match: SoccerHistMatch
    forecasts: list[SoccerForecast]
    revisions: list[SoccerHistRevision]
    source: Optional[SoccerSourceFile]


class SoccerReliabilityBin(BaseModel):
    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed: float


class SoccerReliability(BaseModel):
    outcome: Literal["home", "draw", "away"]
    bins: list[SoccerReliabilityBin]


class SoccerPerformanceRow(BaseModel):
    period: str
    model: str
    run_type: str
    label: str
    is_selected_candidate: bool
    calibrated: bool
    n: int
    log_loss: float
    brier: float
    mean_predicted: list[float]
    observed: list[float]
    reliability: list[SoccerReliability]


class SoccerPerformance(BaseModel):
    competition_id: Optional[SoccerCompetitionId]
    label: str
    rows: list[SoccerPerformanceRow]
    notice: str


class SoccerHistoryItem(BaseModel):
    match: SoccerHistMatch
    forecast: SoccerForecast


class SoccerResearchHistory(BaseModel):
    competition_id: SoccerCompetitionId
    season: Optional[int]
    model: Optional[str]
    total: int
    limit: int
    offset: int
    seasons: list[int]
    label: str
    items: list[SoccerHistoryItem]
