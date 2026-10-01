"""Real data: games from the SQLite store, forecasts from the append-only ledger, run types kept separate.

Prediction shown for a game: the latest *live* forecast of the primary model generated before its cutoff; otherwise
the primary model's *backtest* prediction (labelled as such); otherwise none, with a `forecast_note` saying why.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .. import backtest, metrics
from ..db import utc_now
from ..live import HORIZON_DAYS, LATEST_LIVE_SQL
from ..schemas import (BaselineScore, GameDetail, GamesResponse, GameSummary, HistoryResponse, Market,
                       MarketComparison, Meta, Outcome, PerformanceResponse, Prediction, PredictionRecord,
                       SourceStatus, Team, TeamStats, confidence_record, meets_confidence, pick_confidence)

EASTERN = ZoneInfo("America/New_York")
FRANCHISE_IDS = tuple(f"espn:{i}" for i in range(1, 31))
FRESH_FOR = timedelta(hours=24)
BASELINE_NAMES = {"home_rate-smoothing=laplace": "Home-win rate", "constant-p=0.5": "Always 50%"}
UNSCORED_TYPES = ("preseason", "other")
WARMUP_SEASONS = backtest.PERIODS["warmup"]


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def eastern_date(ts: str) -> date:
    return _parse(ts).astimezone(EASTERN).date()


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=EASTERN)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (start.astimezone(timezone.utc).strftime(fmt),
            (start + timedelta(days=1)).astimezone(timezone.utc).strftime(fmt))


class RealProvider:
    mode = "real"

    def __init__(self, conn: sqlite3.Connection, now: Optional[str] = None):
        self.conn = conn
        self.now = now or utc_now()
        decision, cfg = backtest.load_frozen_decision(conn)
        from ..models.elo import Elo
        self.model_version = backtest.model_version(Elo(cfg))

    # ---- helpers -------------------------------------------------------------------------------------------------
    def _team(self, team_id: str) -> Team:
        if not hasattr(self, "_team_map"):
            self._team_map = _teams(self.conn)  # one cache lookup per request, not per row
        return self._team_map[team_id]

    def _last_refresh(self) -> Optional[str]:
        return self.conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job='refresh' AND status='success'"
                                 ).fetchone()[0]

    def _prediction(self, game_id: str) -> Optional[Prediction]:
        live = self.conn.execute(LATEST_LIVE_SQL + " AND p.game_id = ?", (self.model_version, game_id)).fetchone()
        row = live or self.conn.execute(
            "SELECT * FROM predictions WHERE run_type='backtest' AND model_version=? AND game_id=? "
            "ORDER BY generated_at DESC, prediction_id DESC LIMIT 1", (self.model_version, game_id)).fetchone()
        if row is None:
            return None
        return Prediction(model_version=row["model_version"], run_type=row["run_type"], generated_at=row["generated_at"],
                          cutoff_at=row["cutoff_at"], home_win_prob=row["home_win_prob"])

    def _forecast_note(self, g: sqlite3.Row) -> str:
        cutoff = backtest.cutoff_for(g["tipoff_at"])
        if g["season_type"] in UNSCORED_TYPES:
            return "No forecast: preseason and exhibition games are outside the model's scope."
        if g["season"] in WARMUP_SEASONS:
            return "No forecast: 2019-20 and 2020-21 were model warm-up seasons."
        if g["status"] in ("postponed", "canceled"):
            return f"No forecast: game {g['status']}."
        if cutoff > self.now:
            horizon = (_parse(self.now) + timedelta(days=HORIZON_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if cutoff > horizon:
                return f"Forecasts are recorded within {HORIZON_DAYS} days of the game by the daily job."
            return "Forecast not recorded yet; the daily job records it before the cutoff."
        return "No pregame forecast was recorded before the cutoff."

    def _market(self, game_id: str, tipoff: str) -> Optional[Market]:
        row = self.conn.execute(
            "SELECT * FROM odds_quotes WHERE game_id=? AND retrieved_at <= ? "
            "ORDER BY retrieved_at DESC, price_label = 'current' DESC LIMIT 1", (game_id, tipoff)).fetchone()
        if row is None:
            return None
        return Market(bookmaker=row["provider"], price_label=row["price_label"], quote_time=row["quote_time"],
                      retrieved_at=row["retrieved_at"], home_win_prob=row["home_prob_devig"])

    def _summary(self, g: sqlite3.Row) -> dict:
        pred = self._prediction(g["game_id"])
        outcome = None
        if g["status"] == "final":
            outcome = Outcome(home_score=g["home_score"], away_score=g["away_score"],
                              home_won=g["home_score"] > g["away_score"])
        return dict(game_id=g["game_id"], mode="real", game_date=eastern_date(g["tipoff_at"]), tipoff=g["tipoff_at"],
                    status=g["status"], season_type=g["season_type"], home=self._team(g["home_team_id"]),
                    away=self._team(g["away_team_id"]), prediction=pred,
                    forecast_note=None if pred else self._forecast_note(g),
                    market=self._market(g["game_id"], g["tipoff_at"]), outcome=outcome)

    def _stats(self, team_id: str, season: int, tipoff: str, cutoff: str) -> TeamStats:
        rows = self.conn.execute(
            f"SELECT * FROM games WHERE season=? AND status='final' AND result_available_at <= ? "
            f"AND season_type IN ({', '.join('?' * len(backtest.SEASON_TYPES))}) "
            "AND (home_team_id=? OR away_team_id=?) ORDER BY tipoff_at",
            (season, cutoff, *backtest.SEASON_TYPES, team_id, team_id)).fetchall()
        results = []
        for r in rows:
            home = r["home_team_id"] == team_id
            pf, pa = (r["home_score"], r["away_score"]) if home else (r["away_score"], r["home_score"])
            results.append((r["season_type"], pf, pa))
        regular = [x for x in results if x[0] == "regular"]
        last10 = results[-10:]
        prev = self.conn.execute(
            "SELECT MAX(tipoff_at) FROM games WHERE season=? AND tipoff_at < ? AND status NOT IN ('postponed', 'canceled') "
            "AND season_type <> 'other' AND (home_team_id=? OR away_team_id=?)", (season, tipoff, team_id, team_id)
        ).fetchone()[0]
        return TeamStats(
            wins=sum(pf > pa for _, pf, pa in regular), losses=sum(pf < pa for _, pf, pa in regular),
            last10_wins=sum(pf > pa for _, pf, pa in last10), last10_games=len(last10),
            points_for_avg=round(sum(pf for _, pf, _ in results) / len(results), 1) if results else None,
            points_against_avg=round(sum(pa for _, _, pa in results) / len(results), 1) if results else None,
            rest_days=round((_parse(tipoff) - _parse(prev)).total_seconds() / 86400, 1) if prev else None)

    # ---- endpoints -----------------------------------------------------------------------------------------------
    def meta(self) -> Meta:
        dates = _dates(self.conn)
        today = _parse(self.now).astimezone(EASTERN).date()
        upcoming = [d for d in dates if d >= today]
        default = upcoming[0] if upcoming else (dates[-1] if dates else today)
        team_map = _teams(self.conn)
        teams = sorted((team_map[t] for t in FRANCHISE_IDS if t in team_map), key=lambda t: t.abbr)
        return Meta(mode="real", default_date=default, available_dates=dates, teams=teams,
                    primary_model=self.model_version, last_refresh_at=self._last_refresh())

    def sources(self) -> list[SourceStatus]:
        def fresh(ts: Optional[str]) -> str:
            if ts is None:
                return "missing"
            return "fresh" if _parse(self.now) - _parse(ts) <= FRESH_FOR else "stale"
        last_fetch = self.conn.execute("SELECT MAX(retrieved_at) FROM raw_fetches").fetchone()[0]
        refresh = self._last_refresh() or last_fetch
        odds = self.conn.execute("SELECT MAX(retrieved_at) FROM odds_quotes").fetchone()[0]
        predict = self.conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job='predict' AND status='success'"
                                    ).fetchone()[0]
        return [
            SourceStatus(name="ESPN schedule & results", kind="schedule", mode="real", last_updated=refresh,
                         freshness=fresh(refresh), note="Unofficial ESPN scoreboard API; refreshed by the daily job."),
            SourceStatus(name="Bookmaker odds (ESPN)", kind="odds", mode="real", last_updated=odds,
                         freshness=fresh(odds), note="Moneylines for some upcoming games only; no quote time published."),
            SourceStatus(name="Elo forecasts", kind="forecasts", mode="real", last_updated=predict,
                         freshness=fresh(predict), note="Live forecasts recorded append-only before each game's cutoff."),
        ]

    def games(self, day: date) -> GamesResponse:
        start, end = _day_bounds(day)
        rows = self.conn.execute("SELECT * FROM games WHERE tipoff_at >= ? AND tipoff_at < ? ORDER BY tipoff_at, game_id",
                                 (start, end)).fetchall()
        return GamesResponse(mode="real", date=day, sources=self.sources(),
                             games=[GameSummary(**self._summary(g)) for g in rows])

    def game(self, game_id: str) -> Optional[GameDetail]:
        g = self.conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
        if g is None:
            return None
        cutoff = backtest.cutoff_for(g["tipoff_at"])
        return GameDetail(**self._summary(g), stats_as_of=cutoff, projected_score_available=False,
                          home_stats=self._stats(g["home_team_id"], g["season"], g["tipoff_at"], cutoff),
                          away_stats=self._stats(g["away_team_id"], g["season"], g["tipoff_at"], cutoff))

    def _latest_rows(self, run_type: str, model_version: str) -> list[sqlite3.Row]:
        if run_type == "live":
            sql = LATEST_LIVE_SQL
        else:
            sql = ("SELECT p.* FROM predictions p WHERE p.run_type='backtest' AND p.model_version=? AND p.prediction_id = "
                   "(SELECT q.prediction_id FROM predictions q WHERE q.run_type='backtest' AND q.game_id=p.game_id "
                   " AND q.model_version=p.model_version ORDER BY q.generated_at DESC, q.prediction_id DESC LIMIT 1)")
        return self.conn.execute(
            f"SELECT l.*, g.tipoff_at, g.status, g.home_score, g.away_score, g.home_team_id, g.away_team_id, g.season "
            f"FROM ({sql}) l JOIN games g ON g.game_id = l.game_id ORDER BY g.tipoff_at DESC, l.game_id",
            (model_version,)).fetchall()

    def predictions(self, run_type: str, date_from: Optional[date], date_to: Optional[date], team: Optional[str],
                    limit: int, min_confidence: Optional[float] = None) -> HistoryResponse:
        out = []
        for r in self._latest_rows(run_type, self.model_version):
            if not meets_confidence(r["home_win_prob"], min_confidence):
                continue
            day = eastern_date(r["tipoff_at"])
            if date_from and day < date_from or date_to and day > date_to:
                continue
            home, away = self._team(r["home_team_id"]), self._team(r["away_team_id"])
            if team and team.upper() not in (home.abbr, away.abbr):
                continue
            outcome = None
            if r["status"] == "final":
                outcome = Outcome(home_score=r["home_score"], away_score=r["away_score"],
                                  home_won=r["home_score"] > r["away_score"])
            out.append(PredictionRecord(
                game_id=r["game_id"], run_type=r["run_type"], game_date=day, tipoff=r["tipoff_at"], home=home,
                away=away, model_version=r["model_version"], generated_at=r["generated_at"], cutoff_at=r["cutoff_at"],
                home_win_prob=r["home_win_prob"], pick="home" if r["home_win_prob"] >= 0.5 else "away",
                confidence=pick_confidence(r["home_win_prob"]), outcome=outcome,
                correct=None if outcome is None else (r["home_win_prob"] >= 0.5) == outcome.home_won))
        return HistoryResponse(mode="real", run_type=run_type, total=len(out),
                               record=confidence_record(out, min_confidence), predictions=out[:limit])

    def performance(self, run_type: str) -> PerformanceResponse:
        def scored(model_version: str) -> list[sqlite3.Row]:
            rows = [r for r in self._latest_rows(run_type, model_version) if r["status"] == "final"]
            if run_type == "backtest":
                rows = [r for r in rows if r["season"] in backtest.PERIODS["test"]]
            return rows

        def score(rows):
            probs = [r["home_win_prob"] for r in rows]
            ys = [r["home_score"] > r["away_score"] for r in rows]
            return probs, ys

        rows = scored(self.model_version)
        probs, ys = score(rows)
        baselines = []
        if run_type == "backtest":
            for mv, name in BASELINE_NAMES.items():
                bp, by = score(scored(mv))
                baselines.append(BaselineScore(name=name, accuracy=metrics.accuracy(bp, by),
                                               brier_score=metrics.brier(bp, by), log_loss=metrics.log_loss(bp, by)))
        matched = []
        for r in rows:
            # Fair comparison: only a quote the forecast could have seen (retrieved no later than its generation).
            q = self.conn.execute("SELECT home_prob_devig FROM odds_quotes WHERE game_id=? AND retrieved_at <= ? "
                                  "ORDER BY retrieved_at DESC LIMIT 1", (r["game_id"], r["generated_at"])).fetchone()
            if q:
                matched.append((r["home_win_prob"], q[0], r["home_score"] > r["away_score"]))
        days = [eastern_date(r["tipoff_at"]) for r in rows]
        if run_type == "backtest":
            title = "Final test: retrospective backtest, 2024-25 to 2025-26"
            note = ("Retrospective backtest of the model fixed on validation, evaluated once on held-out seasons. "
                    "Not live forecasts. No historical odds, so no market comparison.")
        else:
            title = "Live forecasts"
            note = ("Latest forecast per game recorded before its cutoff. Market comparison uses the latest quote "
                    "retrieved no later than the forecast itself, where available.")
        return PerformanceResponse(
            mode="real", run_type=run_type, title=title, model_version=self.model_version, sample_size=len(rows),
            date_from=min(days, default=None), date_to=max(days, default=None),
            accuracy=metrics.accuracy(probs, ys), brier_score=metrics.brier(probs, ys),
            log_loss=metrics.log_loss(probs, ys), calibration=metrics.calibration(probs, ys), baselines=baselines,
            market=MarketComparison(
                matched_games=len(matched), coverage=len(matched) / len(rows) if rows else 0.0,
                model_brier=metrics.brier([m[0] for m in matched], [m[2] for m in matched]),
                market_brier=metrics.brier([m[1] for m in matched], [m[2] for m in matched])),
            note=note)


def _cache_key(conn: sqlite3.Connection) -> tuple:
    """Database file + game count + latest game update: changes whenever ingestion adds or updates games or teams."""
    db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
    return (db_file, *conn.execute("SELECT COUNT(*), MAX(updated_at) FROM games").fetchone())


_TEAM_CACHE: dict = {}
_DATE_CACHE: dict = {}


def _teams(conn: sqlite3.Connection) -> dict[str, Team]:
    key = _cache_key(conn)
    if key not in _TEAM_CACHE:
        _TEAM_CACHE.clear()
        _TEAM_CACHE[key] = {r["team_id"]: Team(abbr=r["abbr"], name=r["name"])
                            for r in conn.execute("SELECT team_id, abbr, name FROM teams")}
    return _TEAM_CACHE[key]


def _dates(conn: sqlite3.Connection) -> list[date]:
    key = _cache_key(conn)
    if key not in _DATE_CACHE:
        _DATE_CACHE.clear()
        _DATE_CACHE[key] = sorted({eastern_date(r[0]) for r in conn.execute(
            "SELECT tipoff_at FROM games WHERE status NOT IN ('postponed', 'canceled')")})
    return _DATE_CACHE[key]
