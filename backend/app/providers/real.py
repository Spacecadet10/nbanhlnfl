"""Real data: games from the SQLite store, forecasts from the append-only ledger, run types kept separate.

Prediction shown for a game: the latest *live* forecast of the primary model generated before its cutoff; otherwise
the primary model's *backtest* prediction (labelled as such); otherwise none, with a `forecast_note` saying why.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import json

from .. import agreement, backtest, config, coverage, live, market_live, markets, metrics, near_tipoff, sports
from .. import db
from ..db import utc_now
from ..live import LATEST_LIVE_SQL
from ..schemas import (BaselineScore, CanonicalForecast, CanonicalHistoryResponse, CanonicalPerformanceResponse,
                       CanonicalPreseason, CanonicalRow, CanonicalSummary, MarketModelVersion, GameDetail, GamesResponse, GoalieListingOut, GameSummary, HistoryResponse, Market,
                       MarketComparison, MarketForecastOut, MarketHistoryResponse, MarketInfo, MarketPerformanceResponse,
                       MarketQuoteOut, MarketRecord, MarketRecordSummary, MarketSide, Meta, NearTipoffForecast, Outcome,
                       PerformanceResponse, Prediction, PredictionRecord, SlateSelection, SourceStatus, SportInfo, Team,
                       TeamStats, apply_slate_policy, confidence_record, meets_confidence, pick_confidence)

EASTERN = ZoneInfo("America/New_York")
FRANCHISE_IDS = tuple(f"espn:{i}" for i in range(1, 31))
FRESH_FOR = timedelta(hours=24)
BASELINE_NAMES = {"home_rate-smoothing=laplace": "Home-win rate", "constant-p=0.5": "Always 50%"}
UNSCORED_TYPES = ("preseason", "other")
WARMUP_SEASONS = backtest.PERIODS["warmup"]
# Model-only lines made before this moment (commit 2df4f75) used the earlier rule: median rounded down + 0.5, which put low
# team totals where 'under' was near 70%. Those rows keep their stored line and score; they are labelled, never changed.
MODEL_LINE_RULE_SINCE = "2026-10-02T03:18:53Z"
EARLIER_LINE_RULE_NOTE = ("Earlier line rule: this model-only line was set by the rule used before 2026-10-02 (median rounded "
                          "down + 0.5), which could place the line far from an even split, so the pick partly reflects "
                          "where the line was placed.")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def eastern_date(ts: str) -> date:
    return _parse(ts).astimezone(EASTERN).date()


# Lead-time bands of the pre-game archive, registered in docs/EVALUATION_PLAN.md before the multi-day rows settled: the
# game's Eastern date minus the forecast's Eastern generation date, in whole days. Nothing is dropped; the bands add up.
HORIZON_BANDS = (("1_day", "Made the day before (or the same day)", lambda d: d <= 1),
                 ("2_days", "Made 2 days before", lambda d: d == 2),
                 ("3_7_days", "Made 3–7 days before", lambda d: d >= 3))
PERIOD_LABELS = {"validation": "Seasons used to choose the model (validation)", "test": "Held-out test seasons",
                 "development": "Development seasons", "warmup": "Warm-up seasons"}


def lead(tipoff_at: str, generated_at: str) -> tuple[int, float]:
    """(whole Eastern days between generation and the game's date, hours between generation and the scheduled start)."""
    return ((eastern_date(tipoff_at) - eastern_date(generated_at)).days,
            round((_parse(tipoff_at) - _parse(generated_at)).total_seconds() / 3600, 1))


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
        self.eng = live.engine(conn)
        self.sport = self.eng.sport
        _, model = self.eng.primary(conn)
        self.model_version = backtest.model_version(model)
        self.near_version = near_tipoff.model_version(model)   # stored apart; default views never read it
        if self.sport == "nhl":
            from .. import nhl_eval
            self.periods = nhl_eval.PERIODS
        else:
            self.periods = backtest.PERIODS

    # ---- helpers -------------------------------------------------------------------------------------------------
    def _team(self, team_id: str) -> Team:
        if not hasattr(self, "_team_map"):
            self._team_map = _teams(self.conn)  # one cache lookup per request, not per row
        return self._team_map[team_id]

    def _last_refresh(self) -> Optional[str]:
        return near_tipoff.results_refreshed_at(self.conn, self.now)

    def _prediction(self, game_id: str) -> Optional[Prediction]:
        live = self.conn.execute(LATEST_LIVE_SQL + " AND p.game_id = ?", (self.model_version, game_id)).fetchone()
        row = live or self.conn.execute(
            "SELECT * FROM predictions WHERE run_type='backtest' AND model_version=? AND game_id=? "
            "ORDER BY generated_at DESC, prediction_id DESC LIMIT 1", (self.model_version, game_id)).fetchone()
        if row is None:
            return None
        return Prediction(model_version=row["model_version"], run_type=row["run_type"], generated_at=row["generated_at"],
                          cutoff_at=row["cutoff_at"], home_win_prob=row["home_win_prob"])

    def _near_tipoff(self, g: sqlite3.Row) -> Optional[NearTipoffForecast]:
        row = self.conn.execute(
            f"SELECT l.*, n.results_refreshed_at, n.latest_result_available_at, n.results_used, n.market_home_prob, "
            f"n.market_retrieved_at FROM ({LATEST_LIVE_SQL}) l LEFT JOIN near_tipoff_forecasts n "
            f"ON n.prediction_id = l.prediction_id WHERE l.game_id = ?", (self.near_version, g["game_id"])).fetchone()
        if row is None:
            return None
        p = row["home_win_prob"]
        void = row["cutoff_at"] != g["tipoff_at"]           # start changed after the forecast: void, never scored
        correct = None if g["status"] != "final" or void else (p >= 0.5) == (g["home_score"] > g["away_score"])
        market_prob, market_at = near_tipoff.market_at(self.conn, row)
        return NearTipoffForecast(
            model_version=row["model_version"], generated_at=row["generated_at"], cutoff_at=row["cutoff_at"],
            home_win_prob=p, pick="home" if p >= 0.5 else "away", confidence=pick_confidence(p),
            minutes_before_tipoff=round((_parse(row["cutoff_at"]) - _parse(row["generated_at"])).total_seconds() / 60, 1),
            results_refreshed_at=row["results_refreshed_at"], latest_result_available_at=row["latest_result_available_at"],
            results_used=row["results_used"], market_home_prob=market_prob,
            market_retrieved_at=market_at, correct=correct, void=void or g["status"] in ("postponed", "canceled"),
            limitation="Elo uses results only; it does not know about injuries, rest or line-ups.")

    def _forecast_note(self, g: sqlite3.Row) -> str:
        cutoff = backtest.cutoff_for(g["tipoff_at"])
        if g["season_type"] not in self.eng.eligible_types:
            return "No forecast: preseason and exhibition games are outside the model's scope."
        if g["season"] in self.periods["warmup"]:
            seasons = " and ".join(f"{y - 1}-{str(y)[2:]}" for y in self.periods["warmup"])
            return f"No forecast: {seasons} were model warm-up seasons."
        if g["status"] in ("postponed", "canceled"):
            return f"No forecast: game {g['status']}."
        if cutoff > self.now:   # the daily job that filled the pre-game archive is discontinued: nothing more will be recorded
            return ("No pre-game archive forecast: the daily job that recorded them is discontinued. "
                    "New forecasts are recorded within two hours before the start.")
        return "No pre-game archive forecast was recorded before the cutoff."

    def _market(self, game_id: str, tipoff: str) -> Optional[Market]:
        if self.sport != "nba":   # NHL moneylines are stored with the other line quotes
            row = self.conn.execute(
                "SELECT provider, price_label, quote_time, retrieved_at, p_a_devig AS home_prob_devig, price_a AS home_american, "
                "price_b AS away_american FROM market_quotes "
                "WHERE game_id=? AND market='winner' AND retrieved_at <= ? "
                "ORDER BY retrieved_at DESC, price_label = 'current' DESC, quote_id DESC LIMIT 1", (game_id, tipoff)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM odds_quotes WHERE game_id=? AND retrieved_at <= ? "
                "ORDER BY retrieved_at DESC, price_label = 'current' DESC LIMIT 1", (game_id, tipoff)).fetchone()
        if row is None:
            return None
        return Market(bookmaker=row["provider"], price_label=row["price_label"], quote_time=row["quote_time"],
                      retrieved_at=row["retrieved_at"], home_win_prob=row["home_prob_devig"],
                      home_price=row["home_american"], away_price=row["away_american"])

    def _summary(self, g: sqlite3.Row, policy: str = "near_tipoff", canon: Optional[dict] = None) -> dict:
        pred = self._prediction(g["game_id"])
        if canon is None:
            canon = coverage.canonical(self.conn, [g], "winner", self.now)[g["game_id"]]
        outcome = None
        if g["status"] == "final":
            outcome = Outcome(home_score=g["home_score"], away_score=g["away_score"],
                              home_won=g["home_score"] > g["away_score"])
        return dict(game_id=g["game_id"], mode="real", game_date=eastern_date(g["tipoff_at"]), tipoff=g["tipoff_at"],
                    status=g["status"], season_type=g["season_type"], home=self._team(g["home_team_id"]),
                    away=self._team(g["away_team_id"]), prediction=pred,
                    forecast_note=None if pred else self._forecast_note(g),
                    market=self._market(g["game_id"], g["tipoff_at"]), outcome=outcome,
                    near_tipoff=self._near_tipoff(g), canonical=self._canon_out(canon, g),
                    # research agreement has two-hour and archive cohorts only; the canonical view shows the two-hour one
                    agreement=self._agreement(g["game_id"], "pregame" if policy == "pregame" else "near_tipoff", g["tipoff_at"]))

    def _canon_out(self, c: dict, g) -> CanonicalForecast:
        """A canonical forecast with its pick and realised side in words, at the forecast's own line."""
        if c["state"] != "forecast":
            return CanonicalForecast(**c)
        rec = c["record"]
        from .. import threshold_overview as to
        if not hasattr(self, '_percentage_indexes'):
            self._percentage_indexes = {}
        if c['market'] not in self._percentage_indexes:
            _, cohort, _ = self._canonical_cohort(c['market'])
            self._percentage_indexes[c['market']] = to.percentage_index(cohort, self.now)
        percentage_history = to.percentage_record(self.sport, c['market'], rec, g['game_id'],
                                                 self._percentage_indexes[c['market']], self.now)
        labels = markets.side_labels(c["market"], rec["line"], self._team(g["home_team_id"]).abbr,
                                     self._team(g["away_team_id"]).abbr)
        idx = {"a": 0, "mid": 1, "b": 2}
        opposite = None if c['market'] == 'reg3' else labels[2 if rec['side'] == 'a' else 0]
        return CanonicalForecast(**{**c, "record": {**rec, "pick_label": labels[idx[rec["side"]]], "opposite_pick_label": opposite},
                                    "percentage_history": percentage_history,
                                    "result_label": None if c["result_side"] is None else labels[idx[c["result_side"]]]})

    def _agreement(self, game_id: str, policy: str, tipoff_at: Optional[str] = None) -> dict:
        """Winner agreement for a game card, from ONE research definition: if the latest cohort's members come from more
        than one definition (a code change on unchanged data), only the newest definition's members are shown. Void when
        the game moved after the cohort, by the policy's own rule (two-hour: the exact start; archive: the Eastern date)."""
        out = agreement.summary(self.conn, game_id, self.sport, self.now, policy)
        definitions = {m["model_version"].rsplit(":", 1)[-1] for m in out["models"]}
        if len(definitions) > 1:
            newest = max(out["models"], key=lambda m: m["generated_at"])["model_version"].rsplit(":", 1)[-1]
            out = agreement.summary(self.conn, game_id, self.sport, self.now, policy, definition=newest)
        if out["cutoff_at"] is not None:
            g = self.conn.execute("SELECT tipoff_at, status FROM games WHERE game_id=?", (game_id,)).fetchone()
            row = {"cutoff_at": out["cutoff_at"], "tipoff_at": tipoff_at or g["tipoff_at"]}
            moved = near_tipoff.start_changed(row) if policy == "near_tipoff" else live.day_changed(row)
            reason = (f"Game {g['status']}: void, never scored." if g["status"] in ("postponed", "canceled") else
                      "The game's start changed after these forecasts were made (they were for "
                      f"{out['cutoff_at'] if policy == 'near_tipoff' else 'another Eastern date'}): void, never scored."
                      if moved else None)
            out = {**out, "void": reason is not None, "void_reason": reason}
        return out

    def _stats(self, team_id: str, season: int, tipoff: str, cutoff: str) -> TeamStats:
        rows = self.conn.execute(
            f"SELECT * FROM games WHERE season=? AND status='final' AND result_available_at <= ? "
            f"AND season_type IN ({', '.join('?' * len(self.eng.eligible_types))}) "
            "AND (home_team_id=? OR away_team_id=?) ORDER BY tipoff_at",
            (season, cutoff, *self.eng.eligible_types, team_id, team_id)).fetchall()
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
        if self.sport == "nba":
            ids = FRANCHISE_IDS
        else:   # teams in eligible games of the latest season
            types = self.eng.eligible_types
            ids = [r[0] for r in self.conn.execute(
                f"SELECT home_team_id FROM games WHERE season = (SELECT MAX(season) FROM games) "
                f"AND season_type IN ({', '.join('?' * len(types))}) GROUP BY home_team_id", types)]
        teams = sorted((team_map[t] for t in ids if t in team_map), key=lambda t: t.abbr)
        return Meta(mode="real", sport=self.sport, sports=available_sports(), markets=self.market_infos(),
                    score_unit="goals" if self.sport == "nhl" else "points", default_date=default,
                    available_dates=dates, teams=teams, primary_model=self.model_version,
                    last_refresh_at=self._last_refresh())

    def market_infos(self) -> list[MarketInfo]:
        out = []
        try:
            market_live.market_model(self.conn)
            model_ok = True
        except Exception:   # no usable sealed market model: the markets are listed as not available
            model_ok = False
        for m in sports.get(self.sport).markets:
            spec = markets.spec(self.sport, m)
            if m == "winner":
                available, note = True, f"Settles on the final result ({spec.overtime})."
            else:
                available = model_ok
                note = f"Settles on the final score ({spec.overtime}). " + " ".join(markets.limitations(self.sport, m, None))
            out.append(MarketInfo(id=m, title=spec.title, available=available, note=note.strip()))
        return out

    def sources(self) -> list[SourceStatus]:
        def fresh(ts: Optional[str]) -> str:
            if ts is None:
                return "missing"
            return "fresh" if _parse(self.now) - _parse(ts) <= FRESH_FOR else "stale"
        # Results: the last successful refresh up to now (a raw fetch of a failed or partial refresh never counts; none is
        # 'missing'). While the two-hour cycle works (coverage.forecast_window) it is judged by the forecast guard
        # (near_tipoff.MAX_DATA_AGE); otherwise by the daily job's limit (training_monitor.STALE_JOB_HOURS), as System status.
        from .. import training_monitor
        refresh = near_tipoff.results_refreshed_at(self.conn, self.now)
        guard = int(near_tipoff.MAX_DATA_AGE.total_seconds() // 60)
        daily = training_monitor.STALE_JOB_HOURS["refresh"]
        window = coverage.forecast_window(self.conn, self.now)
        limit = near_tipoff.MAX_DATA_AGE if window else timedelta(hours=daily)
        results_state = "missing" if refresh is None else "fresh" if _parse(self.now) - _parse(refresh) <= limit else "stale"
        results_note = (f" A game starts within two hours, so the forecast rule applies: no new forecast is made unless the "
                        f"last successful results refresh is at most {guard} minutes old (each cycle refreshes first)."
                        if window == "upcoming" else
                        f" A game is under way: each cycle refreshes the results, so the last successful refresh should be "
                        f"at most {guard} minutes old (no forecast is due)." if window == "recent" else
                        f" No game is within two hours of its start, so the daily job's limit applies ({daily} h); each "
                        f"forecast cycle refreshes the results again before it forecasts.")
        odds = self.conn.execute("SELECT MAX(retrieved_at) FROM odds_quotes").fetchone()[0]
        # Winner forecasts are made only within two hours of a start, so "stale" is never the age of a job run: it means an
        # eligible game started in the last 24 h without a two-hour forecast. No eligible game is a healthy state.
        predict = self.conn.execute("SELECT MAX(generated_at) FROM predictions WHERE run_type='live' "
                                    "AND model_version IN (?, ?)", (self.model_version, self.near_version)).fetchone()[0]
        types = self.eng.eligible_types
        since = (_parse(self.now) - FRESH_FOR).strftime("%Y-%m-%dT%H:%M:%SZ")
        started, covered = self.conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(EXISTS(SELECT 1 FROM predictions p WHERE p.game_id=g.game_id AND p.run_type='live' "
            f"AND p.model_version=?)), 0) FROM games g WHERE g.tipoff_at > ? AND g.tipoff_at <= ? "
            f"AND g.status NOT IN ('postponed', 'canceled') AND g.season_type IN ({', '.join('?' * len(types))})",
            (self.near_version, since, self.now, *types)).fetchone()
        if started:
            forecast_note = f"{covered} of {started} eligible games that started in the last 24 h had a two-hour forecast."
        else:
            forecast_note = "No eligible game started in the last 24 h; forecasts are made within two hours of a start."
        forecast_state = "stale" if started > covered else "fresh" if predict or not started else "missing"
        lines = self.conn.execute("SELECT MAX(retrieved_at) FROM market_quotes").fetchone()[0]
        if self.sport == "nhl":
            schedule = SourceStatus(name="NHL schedule & results", kind="schedule", mode="real", last_updated=refresh,
                                    freshness=results_state, note="NHL stats API season game list; refreshed by the daily job and each forecast cycle." + results_note)
            odds, odds_note = lines, "DraftKings lines via ESPN for games in the next 36 h; no quote time published."
        else:
            schedule = SourceStatus(name="ESPN schedule & results", kind="schedule", mode="real", last_updated=refresh,
                                    freshness=results_state, note="Unofficial ESPN scoreboard API; refreshed by the daily job and each forecast cycle." + results_note)
            odds = max(filter(None, (odds, lines)), default=None)
            odds_note = "Moneylines, spreads and totals for some upcoming games only; no quote time published."
        markets_at = self.conn.execute("SELECT MAX(generated_at) FROM market_forecasts").fetchone()[0]
        return [
            schedule, *self._input_sources(),
            SourceStatus(name="Bookmaker odds (ESPN)", kind="odds", mode="real", last_updated=odds,
                         freshness=fresh(odds), note=odds_note),
            SourceStatus(name="Elo forecasts", kind="forecasts", mode="real", last_updated=predict,
                         freshness=forecast_state, note="Live forecasts recorded append-only. " + forecast_note),
            SourceStatus(name="Market forecasts", kind="forecasts", mode="real", last_updated=markets_at,
                         freshness=fresh(markets_at), note="Spread/puck line, totals and team totals from the sealed "
                                                           "score model, each at its exact line."),
        ]

    def _input_sources(self) -> list[SourceStatus]:
        """Injury, player and canonical-ledger inputs: an input without data is 'missing' or 'stale', never left out. Each
        entry is isolated: one that cannot be read is listed as missing with the error (the slate still loads)."""
        builders = ([self._nba_injury_source, self._player_box_source] if self.sport == "nba" else [self._nhl_injury_source])
        out = []
        for build in [*builders, self._canonical_source]:
            try:
                out.append(build())
            except Exception as exc:          # never fail the slate because one status cannot be read
                label, kind = {self._nba_injury_source: ("NBA injury reports (official)", "injuries"),
                               self._player_box_source: ("NBA player box file (current season)", "players"),
                               self._nhl_injury_source: ("NHL injuries (ESPN page)", "injuries"),
                               self._canonical_source: ("Canonical forecasts (every game)", "canonical")}[build]
                out.append(SourceStatus(name=label, kind=kind,
                                        mode="real", last_updated=None, freshness="missing",
                                        note=f"Status unavailable: {type(exc).__name__}: {exc}"))
        return out

    def _nba_injury_source(self) -> SourceStatus:
        """Fresh when a report for a slot of the last 60 minutes was stored by now (the injury-aware forecast needs one
        published within 60 minutes). Stored is not the same as usable: the forecast also reconciles the report."""
        from .. import availability, injuries
        plan = injuries.plan(self.conn, _parse(self.now))
        since = (_parse(self.now) - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        stored = self.conn.execute("SELECT MAX(completed_at) FROM injury_report_attempts WHERE outcome='stored' "
                                   "AND completed_at <= ?", (self.now,)).fetchone()[0]
        recent = self.conn.execute("SELECT 1 FROM injury_report_attempts WHERE outcome='stored' AND completed_at <= ? AND "
                                   "slot_at >= ? LIMIT 1", (self.now, since)).fetchone() is not None
        collecting = ("collected by the injuries job (NBA_INJURY_COLLECTION=1)" if config.injury_collection_enabled() else
                      "collected by the two-hour cycle (the sealed availability facts exist)"
                      if (availability.research_dir() / "forward-facts.json").exists() else
                      "not collected: NBA_INJURY_COLLECTION is not 1 and the two-hour cycle has no availability facts")
        if not plan["due"]:
            return SourceStatus(name="NBA injury reports (official)", kind="injuries", mode="real", last_updated=stored,
                                freshness="missing", note=f"No official injury report is due: reports are collected around "
                                f"regular-season and playoff games (none are published for preseason); {collecting}. "
                                "Injury-aware forecasts have no report now.")
        waiting = len(plan["pending"]) + len(plan["given_up"])
        note = (("A report for the last 60 minutes is stored (stored, not yet reconciled by the forecast). " if recent else
                 "No report for the last 60 minutes is stored. ")
                + f"{plan['stored']} of {plan['due']} due reports stored in the last 24 h"
                + (f"; {waiting} not stored ({len(plan['given_up'])} given up)" if waiting else "") + f"; {collecting}.")
        return SourceStatus(name="NBA injury reports (official)", kind="injuries", mode="real", last_updated=stored,
                            freshness="fresh" if recent else "stale", note=note)

    def _player_box_source(self) -> SourceStatus:
        """File age of the current season's player box. The player-rating members check rows themselves (the box rows of
        each team's recent finals, and that consumed rows did not change) and abstain otherwise."""
        from .. import availability, jobs
        name = "NBA player box file (current season)"
        row = self.conn.execute("SELECT MAX(season) FROM games WHERE tipoff_at <= ?", (self.now,)).fetchone()
        path = availability.player_box_path(row[0]) if row and row[0] else None
        switch = ("refreshed nightly (NBA_PLAYER_BOX_REFRESH=1)" if config.player_box_refresh_enabled() else
                  "not refreshed: the refresh job is off (NBA_PLAYER_BOX_REFRESH is not 1)")
        rows_note = "file age only: the player-rating members also require the recent finals' box rows and abstain without them"
        if path is None or not path.exists():
            return SourceStatus(name=name, kind="players", mode="real", last_updated=None, freshness="missing",
                                note=f"No player box file for the current season; {switch}.")
        at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if at > _parse(self.now):          # a replay or fixed clock: the file did not exist yet at `now`
            return SourceStatus(name=name, kind="players", mode="real", last_updated=None, freshness="missing",
                                note=f"{path.name} was written after this view's time ({self.now}); {switch}.")
        age = (_parse(self.now) - at).total_seconds() / 3600
        return SourceStatus(name=name, kind="players", mode="real", last_updated=at,
                            freshness="fresh" if age <= jobs.PLAYER_BOX_MAX_AGE_HOURS else "stale",
                            note=f"{path.name}; stale after {jobs.PLAYER_BOX_MAX_AGE_HOURS} h; {switch}; {rows_note}.")

    def _nhl_injury_source(self) -> SourceStatus:
        """The newest ESPN NHL injuries snapshot (live capture or archive) captured by now: fresh while the NHL injury member
        would use it (nhl_player_ratings.MAX_AGE_HOURS), else stale; none → missing (the member abstains)."""
        from .. import nhl_player_ratings
        from ..sources import nhl_injuries
        name = "NHL injuries (ESPN page)"
        switch = ("captured by the two-hour cycle (NHL_INJURY_CAPTURE=1)" if config.nhl_injury_capture_enabled() else
                  "not captured live: NHL_INJURY_CAPTURE is not 1")
        folder = nhl_injuries.root() / "injuries"
        times = []                  # capture times from the file-name stamps (archive and live both name the capture UTC)
        for path in [*folder.glob("[0-9]*.json"), *folder.glob("live-[0-9]*.json")]:
            stamp = path.stem.removeprefix("live-")
            if len(stamp) == 14 and stamp.isdigit():
                at = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}Z"
                if at <= self.now:
                    times.append(at)
        if not times:
            return SourceStatus(name=name, kind="injuries", mode="real", last_updated=None, freshness="missing",
                                note=f"No NHL injury snapshot captured by now; {switch}. NHL injury inputs are 'report "
                                     "missing' and the injury member abstains.")
        last = max(times)
        age = (_parse(self.now) - _parse(last)).total_seconds() / 3600
        limit = nhl_player_ratings.MAX_AGE_HOURS
        return SourceStatus(name=name, kind="injuries", mode="real", last_updated=last,
                            freshness="fresh" if age <= limit else "stale",
                            note=f"Newest snapshot of the ESPN NHL injuries page; the injury member uses one at most {limit:g} h "
                                 f"old (otherwise it abstains); {switch}.")

    def _canonical_source(self) -> SourceStatus:
        """Fresh only when every forecastable game that started in the last 24 h (after recording began) has a canonical
        forecast in every market; a recorded reason for no forecast is a gap, never health."""
        name = "Canonical forecasts (every game)"
        if not db.has_table(self.conn, "game_forecasts"):
            return SourceStatus(name=name, kind="canonical", mode="real", last_updated=None, freshness="missing",
                                note="This store has no every-game ledger (migration 0029 not applied): no canonical "
                                     "forecasts are recorded.")
        last = self.conn.execute("SELECT MAX(generated_at) FROM game_forecasts WHERE generated_at <= ?", (self.now,)).fetchone()[0]
        began = coverage.recording_began(self.conn, self.sport)
        if last is None or began is None or began > self.now:
            return SourceStatus(name=name, kind="canonical", mode="real", last_updated=None, freshness="missing",
                                note="No canonical forecast or reason has been recorded yet.")
        since = max((_parse(self.now) - FRESH_FOR).strftime("%Y-%m-%dT%H:%M:%SZ"), began)
        types = coverage.COMPETITIVE          # preseason has no canonical forecast by rule ('unvalidated'), never a gap
        games = [r[0] for r in self.conn.execute(
            f"SELECT game_id FROM games WHERE tipoff_at > ? AND tipoff_at <= ? AND status NOT IN ('postponed', 'canceled') "
            f"AND season_type IN ({', '.join('?' * len(types))})", (since, self.now, *types))]
        market_list = sports.get(self.sport).markets
        unrecorded_pre = self.conn.execute(          # preseason has rows by rule ('unvalidated'); none at all = no cycle ran
            "SELECT COUNT(*) FROM games g WHERE g.tipoff_at > ? AND g.tipoff_at <= ? AND g.status NOT IN ('postponed', "
            "'canceled') AND g.season_type = 'preseason' AND NOT EXISTS (SELECT 1 FROM game_forecasts f WHERE f.game_id = "
            "g.game_id AND f.generated_at <= ? AND f.generated_at < g.tipoff_at)", (since, self.now, self.now)).fetchone()[0]
        full, gaps, reasons = 0, 0, {}
        for gid in games:
            states = {}
            for r in self.conn.execute("SELECT market, status, reason FROM game_forecasts WHERE game_id=? AND generated_at <= ? "
                                       "AND generated_at < tipoff_at ORDER BY generated_at, forecast_id", (gid, self.now)):
                if r["status"] == "forecast" or r["market"] not in states:
                    states[r["market"]] = (r["status"], r["reason"])
                elif states[r["market"]][0] != "forecast":
                    states[r["market"]] = (r["status"], r["reason"])
            if all(states.get(m, ("none",))[0] == "forecast" for m in market_list):
                full += 1
            for m in market_list:
                st = states.get(m)
                if st is None or st[0] != "forecast":
                    gaps += 1
                    why = "not recorded" if st is None else (st[1] or "missing")
                    reasons[why] = reasons.get(why, 0) + 1
        if not games:
            note = ("No regular-season, play-in or playoff game started in the last 24 h (since recording began); records are "
                    "made within two hours of each start. Preseason games have no canonical forecast.")
        else:
            top = "; ".join(f"{n}× {why[:90]}" for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:2])
            note = (f"{full} of {len(games)} regular-season, play-in or playoff games that started in the last 24 h have a "
                    f"canonical forecast in every market" + (f"; {gaps} market cells missing ({top})." if gaps else ".")
                    + " Preseason games have no canonical forecast.")
        recorded_pre = self.conn.execute(
            "SELECT COUNT(*) FROM games g WHERE g.tipoff_at > ? AND g.tipoff_at <= ? AND g.status NOT IN ('postponed', "
            "'canceled') AND g.season_type = 'preseason' AND EXISTS (SELECT 1 FROM game_forecasts f WHERE f.game_id = "
            "g.game_id AND f.generated_at <= ? AND f.generated_at < g.tipoff_at)", (since, self.now, self.now)).fetchone()[0]
        if recorded_pre:
            note += (f" {recorded_pre} preseason game{'s' if recorded_pre > 1 else ''} recorded without a canonical forecast "
                     "(none by rule).")
        if unrecorded_pre:
            note += f" {unrecorded_pre} preseason game{'s' if unrecorded_pre > 1 else ''} not recorded (no cycle ran for it)."
        return SourceStatus(name=name, kind="canonical", mode="real", last_updated=last,
                            freshness="fresh" if full == len(games) and not unrecorded_pre else "stale", note=note)

    def games(self, day: date, policy: str = "pregame", min_confidence: Optional[float] = None,
              market: str = "winner") -> GamesResponse:
        start, end = _day_bounds(day)
        rows = self.conn.execute("SELECT * FROM games WHERE tipoff_at >= ? AND tipoff_at < ? ORDER BY tipoff_at, game_id",
                                 (start, end)).fetchall()
        canon = coverage.canonical(self.conn, rows, "winner", self.now)    # one canonical read for the whole slate
        games = [GameSummary(**self._summary(g, policy, canon[g["game_id"]])) for g in rows]
        if market == "winner":
            selection = apply_slate_policy(games, policy, min_confidence)
        elif policy == "canonical":   # the market's canonical record only; never a legacy ledger's forecast
            canon = coverage.canonical(self.conn, rows, market, self.now)
            for g, row in zip(games, rows):
                g.canonical = self._canon_out(canon[g.game_id], row)
            picks = [g.canonical.record for g in games if g.canonical.state == "forecast"]
            selected = sum(min_confidence is None or r.confidence >= min_confidence - 1e-9 for r in picks)
            without: dict[str, int] = {}
            for g in games:
                if g.canonical.state != "forecast":
                    without[g.canonical.state] = without.get(g.canonical.state, 0) + 1
            selection = SlateSelection(market=market, policy=policy, min_confidence=min_confidence, games=len(games),
                                       with_forecast=len(picks), selected=selected, below_threshold=len(picks) - selected,
                                       without_forecast=without)
        else:   # the selected market and policy only; no fallback to another market or policy
            found = {}     # the current model's forecast; else the latest of an earlier version (labelled, never both)
            for r in self._market_all_versions(policy, market=market, game_ids=[g.game_id for g in games]):
                found.setdefault(r["game_id"], r)
            for g in games:
                r = found.get(g.game_id)
                g.market_forecast = None if r is None else self._market_out(r, g.home, g.away, min_confidence)
                if g.market_forecast is not None and not g.market_forecast.current_version:
                    g.market_forecast.meets_confidence = False     # display only: never a pick of the current model
            # Counts and the confidence filter cover the current model only; an earlier version's forecast is shown, labelled.
            picks = [g.market_forecast for g in games if g.market_forecast is not None and g.market_forecast.current_version]
            selection = SlateSelection(market=market, policy=policy, min_confidence=min_confidence, games=len(games),
                                       with_forecast=len(picks), selected=sum(p.meets_confidence for p in picks))
        return GamesResponse(mode="real", date=day, sources=self.sources(), games=games, selection=selection)

    def _market_out(self, r, home: Team, away: Team, min_confidence: Optional[float] = None) -> MarketForecastOut:
        labels = markets.side_labels(r["market"], r["line"], home.abbr, away.abbr)
        side = market_live.pick(r)
        conf = r["p_a"] if side == "a" else r["p_b"]
        quote = None
        if r["market_quote_id"] is not None:
            quote = MarketQuoteOut(bookmaker=r["quote_provider"], retrieved_at=r["quote_retrieved_at"], line=r["line"],
                                   price_a=r["price_a"], price_b=r["price_b"], prob_a=r["p_a_devig"], prob_b=r["p_b_devig"])
        outcome = market_live.outcome(r)
        result = market_live.result_for_pick(r["market"], side, outcome) if outcome in ("a", "b", "mid") else outcome
        sides = [MarketSide(side="a", label=labels[0], probability=r["p_a"])]
        if r["market"] == "reg3" or r["p_mid"] > 0:
            sides.append(MarketSide(side="mid", label=labels[1], probability=r["p_mid"]))
        sides.append(MarketSide(side="b", label=labels[2], probability=r["p_b"]))
        earlier_rule = r["line_source"] == "model" and r["generated_at"] < MODEL_LINE_RULE_SINCE
        return MarketForecastOut(
            market=r["market"], title=markets.spec(self.sport, r["market"]).title, policy=r["policy"],
            model_version=r["model_version"],
            current_version=r["model_version"] == (self._current_market_version(r["policy"]) or r["model_version"]),
            generated_at=r["generated_at"], cutoff_at=r["cutoff_at"], line=r["line"],
            line_source=r["line_source"], earlier_line_rule=earlier_rule,
            sides=sides, pick=side, pick_label=labels[0 if side == "a" else 2],
            confidence=conf, meets_confidence=min_confidence is None or conf >= min_confidence - 1e-9, quote=quote,
            result=result, limitations=markets.limitations(self.sport, r["market"], r["line_source"])
            + ([EARLIER_LINE_RULE_NOTE] if earlier_rule else []))

    def game(self, game_id: str) -> Optional[GameDetail]:
        g = self.conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
        if g is None:
            return None
        cutoff = backtest.cutoff_for(g["tipoff_at"])
        summary = self._summary(g)
        forecasts = [self._market_out(r, summary["home"], summary["away"])      # every version, labelled; current first
                     for policy in market_live.POLICIES
                     for r in self._market_all_versions(policy, game_ids=[game_id])]
        goalies = []
        if self.sport == "nhl" and self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='goalie_snapshots'").fetchone():
            from ..ingest import GOALIE_AS_OF_SQL
            goalies = [GoalieListingOut(side=r["side"], player_name=r["player_name"], source_status=r["source_status"],
                                        source_status_label=r["source_status_label"], retrieved_at=r["retrieved_at"])
                       for r in self.conn.execute(f"SELECT * FROM ({GOALIE_AS_OF_SQL}) WHERE game_id = ? ORDER BY side DESC",
                                                  (self.now, game_id))]
        canonical_markets = [self._canon_out(coverage.canonical(self.conn, [g], m, self.now)[game_id], g)
                             for m in sports.get(self.sport).markets]
        return GameDetail(**summary, stats_as_of=cutoff, projected_score_available=False, goalies=goalies,
                          home_stats=self._stats(g["home_team_id"], g["season"], g["tipoff_at"], cutoff),
                          away_stats=self._stats(g["away_team_id"], g["season"], g["tipoff_at"], cutoff),
                          market_forecasts=forecasts, canonical_markets=canonical_markets)

    # ---- market history and performance ---------------------------------------------------------------------------
    def _current_market_version(self, policy: str) -> Optional[str]:
        cache = self.__dict__.setdefault("_market_versions", {})          # once per request and policy
        if policy not in cache:
            cache[policy] = market_live.current_version(self.conn, policy)
        return cache[policy]

    def _market_all_versions(self, policy: str, **kw) -> list:
        """Latest forecast per game, market and model version: the current version's rows first, then earlier versions
        (newest first). For display of single games only; records always use one version (`latest_current`)."""
        current = self._current_market_version(policy)
        rows = market_live.latest(self.conn, policy=policy, **kw)
        return sorted(rows, key=lambda r: (r["game_id"], r["market"], r["model_version"] != current,
                                           tuple(-ord(c) for c in r["generated_at"])))

    def _versions(self, market: str, policy: str) -> list[MarketModelVersion]:
        return [MarketModelVersion(**v) for v in market_live.versions(self.conn, policy=policy, market=market)]

    def _market_records(self, market: str, policy: str, min_confidence: Optional[float],
                        model_version: Optional[str] = None) -> list[MarketRecord]:
        out = []
        for r in reversed(market_live.latest_current(self.conn, policy=policy, market=market, version=model_version)):
            home, away = self._team(r["home_team_id"]), self._team(r["away_team_id"])
            f = self._market_out(r, home, away, min_confidence)
            if not f.meets_confidence:
                continue
            outcome = None
            if r["status"] == "final":
                outcome = Outcome(home_score=r["home_score"], away_score=r["away_score"],
                                  home_won=r["home_score"] > r["away_score"])
            days, hours = lead(r["game_tipoff_at"], r["generated_at"])
            out.append(MarketRecord(game_id=r["game_id"], game_date=eastern_date(r["game_tipoff_at"]),
                                    tipoff=r["game_tipoff_at"], home=home, away=away, forecast=f, outcome=outcome,
                                    lead_days=days, horizon_hours=hours))
        return out

    @staticmethod
    def _record_summary(rows: list[MarketRecord], min_confidence: Optional[float]) -> MarketRecordSummary:
        res = [r.forecast.result for r in rows]
        wins, losses = res.count("win"), res.count("loss")
        # What the bookmaker expected of these picks: its de-vigged probability of the picked side, on decided picks that
        # were made at a quoted line. A high win rate on heavily favoured sides is not skill by itself.
        quoted = [(r.forecast.quote.prob_a if r.forecast.pick == "a" else r.forecast.quote.prob_b, r.forecast.result == "win")
                  for r in rows if r.forecast.quote is not None and r.forecast.result in ("win", "loss")]
        implied = dict(quoted_decided=len(quoted), quoted_wins=sum(w for _, w in quoted),
                       mean_implied=sum(p for p, _ in quoted) / len(quoted) if quoted else None)
        return MarketRecordSummary(**implied, min_confidence=min_confidence, selected=len(rows),
                                   settled=wins + losses + res.count("push"), wins=wins, losses=losses,
                                   pushes=res.count("push"), voids=res.count("void"),
                                   pending=res.count("pending"), unscorable=res.count("unscorable"),
                                   win_rate=wins / (wins + losses) if wins + losses else None)

    def market_predictions(self, market: str, policy: str, date_from: Optional[date], date_to: Optional[date],
                           team: Optional[str], limit: int, min_confidence: Optional[float],
                           model_version: Optional[str] = None) -> MarketHistoryResponse:
        rows = [r for r in self._market_records(market, policy, min_confidence, model_version)
                if not (date_from and r.game_date < date_from or date_to and r.game_date > date_to)
                and not (team and team.upper() not in (r.home.abbr, r.away.abbr))]
        return MarketHistoryResponse(mode="real", sport=self.sport, market=market, policy=policy, total=len(rows),
                                     model_version=model_version or market_live.current_version(self.conn, policy),
                                     versions=self._versions(market, policy),
                                     record=self._record_summary(rows, min_confidence),
                                     parts=[] if policy != "pregame" else [
                                         {"key": k, "label": label, "record": self._record_summary(
                                             [r for r in rows if keep(r.lead_days)], min_confidence)}
                                         for k, label, keep in HORIZON_BANDS],
                                     predictions=rows[:limit])

    def market_performance(self, market: str, policy: str, model_version: Optional[str] = None) -> MarketPerformanceResponse:
        rows = self._market_records(market, policy, None, model_version)
        settled_side = {(x["game_id"], x["market"]): market_live.outcome(x)
                        for x in market_live.latest_current(self.conn, policy=policy, market=market, version=model_version)}
        settled = [r for r in rows if settled_side[(r.game_id, r.forecast.market)] in ("a", "mid", "b")]

        def realised(r: MarketRecord) -> int:
            return {"a": 0, "mid": 1, "b": 2}[settled_side[(r.game_id, r.forecast.market)]]
        vecs = [[next((s.probability for s in r.forecast.sides if s.side == k), 0.0) for k in ("a", "mid", "b")]
                for r in settled]
        real = [realised(r) for r in settled]
        # Same games for both: decided ones only. A push refunds both sides, and the two-way bookmaker price has no push
        # probability, so including pushes would score the bookmaker unfairly.
        matched = [(v, k, r.forecast.quote) for v, k, r in zip(vecs, real, settled) if r.forecast.quote is not None and k != 1]
        sources: dict[str, int] = {}
        for r in rows:
            if r.forecast.line_source:
                sources[r.forecast.line_source] = sources.get(r.forecast.line_source, 0) + 1
        earlier = sum(r.forecast.earlier_line_rule for r in rows)
        limits = markets.limitations(self.sport, market, None)
        if earlier:
            limits = limits + [f"{earlier} of these {len(rows)} forecasts used model-only lines set by the earlier line rule "
                               "(before 2026-10-02), which could place the line far from an even split; their win rate "
                               "partly reflects where the line was placed."]
        return MarketPerformanceResponse(
            mode="real", sport=self.sport, market=market, policy=policy,
            title=f"{markets.spec(self.sport, market).title}: live {'near-tipoff forecasts' if policy == 'near_tipoff' else 'pre-game archive forecasts'}",
            model_version=model_version or market_live.current_version(self.conn, policy),
            versions=self._versions(market, policy), forecasts=len(rows),
            record=self._record_summary(rows, None), brier_score=metrics.brier_multi(vecs, real),
            log_loss=metrics.log_loss_multi(vecs, real), line_sources=sources, market_matched=len(matched),
            model_log_loss_on_matched=metrics.log_loss_multi(
                [[m[0][0] / (m[0][0] + m[0][2]), 0.0, m[0][2] / (m[0][0] + m[0][2])] for m in matched], [m[1] for m in matched]),
            market_log_loss_on_matched=metrics.log_loss_multi(
                [[q.prob_a, 0.0, q.prob_b] for _, _, q in matched], [k for _, k, _ in matched]),
            reference=_market_reference(self.sport, market, market_live.current_version(self.conn, policy)),
            limitations=limits,
            note="Latest forecast per game recorded before its cutoff. The win rate excludes pushes and voids; a regulation "
                 "draw counts as a loss for a 3-way pick. The bookmaker comparison uses only decided games forecast at the "
                 "quoted line, with the quote retrieved no later than the forecast, and both sides' probabilities "
                 "renormalised without the push. Nothing here is a claim of betting value.")

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

    @staticmethod
    def _moved(r, run_type: str, policy: str) -> bool:
        """A live forecast is void once the game leaves what it was made for: the exact start (two-hour policy) or the
        Eastern game date (pre-game archive policy). Backtest rows are computed from final schedules and never void."""
        if run_type != "live":
            return False
        return near_tipoff.start_changed(r) if policy == "near_tipoff" else live.day_changed(r)

    def predictions(self, run_type: str, date_from: Optional[date], date_to: Optional[date], team: Optional[str],
                    limit: int, min_confidence: Optional[float] = None, policy: str = "pregame") -> HistoryResponse:
        out = []
        for r in self._latest_rows(run_type, self.near_version if policy == "near_tipoff" else self.model_version):
            if not meets_confidence(r["home_win_prob"], min_confidence):
                continue
            day = eastern_date(r["tipoff_at"])
            if date_from and day < date_from or date_to and day > date_to:
                continue
            home, away = self._team(r["home_team_id"]), self._team(r["away_team_id"])
            if team and team.upper() not in (home.abbr, away.abbr):
                continue
            outcome = None
            moved = self._moved(r, run_type, policy)                # void: made for a different start or game date
            if r["status"] == "final" and not moved:
                outcome = Outcome(home_score=r["home_score"], away_score=r["away_score"],
                                  home_won=r["home_score"] > r["away_score"])
            out.append(PredictionRecord(
                game_id=r["game_id"], run_type=r["run_type"], game_date=day, tipoff=r["tipoff_at"], home=home,
                away=away, model_version=r["model_version"], generated_at=r["generated_at"], cutoff_at=r["cutoff_at"],
                home_win_prob=r["home_win_prob"], pick="home" if r["home_win_prob"] >= 0.5 else "away",
                confidence=pick_confidence(r["home_win_prob"]), outcome=outcome,
                correct=None if outcome is None else (r["home_win_prob"] >= 0.5) == outcome.home_won,
                unscorable=r["status"] == "final" and not moved and outcome is None,
                lead_days=lead(r["tipoff_at"], r["generated_at"])[0] if run_type == "live" else None,
                horizon_hours=lead(r["tipoff_at"], r["generated_at"])[1] if run_type == "live" else None,
                period=self._period(r["season"]) if run_type == "backtest" else None,
                void=moved or r["status"] in ("postponed", "canceled")))
        if run_type == "backtest":      # every row is kept; the record is also shown per evaluation period
            order = [p for p in ("validation", "test", "development", "warmup") if any(x.period == p for x in out)]
            parts = [{"key": p, "label": PERIOD_LABELS[p], "record": confidence_record([x for x in out if x.period == p], min_confidence)}
                     for p in order]
            parts = parts if len(parts) > 1 or (parts and parts[0]["key"] != "test") else []
        elif policy == "pregame":       # the registered lead-time bands of the pre-game archive
            parts = [{"key": k, "label": label, "record": confidence_record([x for x in out if keep(x.lead_days)], min_confidence)}
                     for k, label, keep in HORIZON_BANDS]
        else:
            parts = []
        return HistoryResponse(mode="real", run_type=run_type, policy=policy, total=len(out),
                               record=confidence_record(out, min_confidence), parts=parts, predictions=out[:limit])

    def _period(self, season: int) -> Optional[str]:
        return next((name for name, seasons in self.periods.items() if season in seasons), None)

    def _winner_quote_prob(self, game_id: str, at: str) -> Optional[float]:
        """De-vigged home probability of the latest moneyline retrieved no later than `at` (a quote the forecast could
        have seen), or None."""
        q = self.conn.execute(
            "SELECT home_prob_devig FROM odds_quotes WHERE game_id=? AND retrieved_at <= ? "
            "ORDER BY retrieved_at DESC, price_label = 'current' DESC, quote_id DESC LIMIT 1"
            if self.sport == "nba" else     # NHL moneylines: market_quotes
            "SELECT p_a_devig FROM market_quotes WHERE game_id=? AND market='winner' AND retrieved_at <= ? "
            "ORDER BY retrieved_at DESC, price_label = 'current' DESC, quote_id DESC LIMIT 1",
            (game_id, at)).fetchone()
        return None if q is None else q[0]

    # ---- canonical forecast (game_forecasts; one record per game and market) -------------------------------------------
    def _canonical_games(self, start: Optional[str] = None, end: Optional[str] = None,
                         market: str = "winner") -> tuple[Optional[str], list, dict]:
        """Games since canonical recording began (none before it), in [start, end), with their canonical forecast of `market`."""
        began = coverage.recording_began(self.conn, self.sport)
        if began is None:
            return None, [], {}
        lo = max(began, start) if start else began
        rows = self.conn.execute("SELECT * FROM games WHERE tipoff_at >= ? AND tipoff_at < ? ORDER BY tipoff_at DESC, game_id",
                                 (lo, end or "9999")).fetchall()
        return began, rows, coverage.canonical(self.conn, rows, market, self.now, began)

    def canonical_history(self, date_from: Optional[date], date_to: Optional[date], team: Optional[str],
                          limit: int, market: str = "winner") -> CanonicalHistoryResponse:
        start = _day_bounds(date_from)[0] if date_from else None
        end = _day_bounds(date_to)[1] if date_to else None
        began, rows, canon = self._canonical_games(start, end, market)
        shown, cohort, preseason = [], [], 0
        for g in rows:
            c = canon[g["game_id"]]
            if c["state"] in ("upcoming", "not_recorded", "not_forecastable"):
                continue
            home, away = self._team(g["home_team_id"]), self._team(g["away_team_id"])
            if team and team.upper() not in (home.abbr, away.abbr):
                continue
            preseason += c["state"] == "unvalidated"
            if g["season_type"] in coverage.COMPETITIVE and g["tipoff_at"] <= self.now:
                cohort.append((g, c))
            outcome = (Outcome(home_score=g["home_score"], away_score=g["away_score"], home_won=g["home_score"] > g["away_score"])
                       if g["status"] == "final" else None)
            shown.append(CanonicalRow(game_id=g["game_id"], game_date=eastern_date(g["tipoff_at"]), tipoff=g["tipoff_at"],
                                      status=g["status"], season_type=g["season_type"], home=home, away=away,
                                      outcome=outcome, canonical=self._canon_out(c, g)))
        return CanonicalHistoryResponse(mode="real", market=market, as_of=self.now, recording_began_at=began,
                                        total=len(shown), record=CanonicalSummary(**coverage.canonical_summary(cohort, market)),
                                        preseason_games=preseason, rows=shown[:limit])

    def _canonical_cohort(self, market: str) -> tuple[Optional[str], list, list]:
        """(recording start, started competitive games [(game, canonical)], started preseason games) of one market."""
        began, rows, canon = self._canonical_games(market=market)
        cohort, pre = [], []
        for g in rows:
            if g["tipoff_at"] > self.now or (began and g["tipoff_at"] <= began):
                continue                     # not started, or before canonical recording began (legacy views)
            if g["season_type"] in coverage.COMPETITIVE:
                cohort.append((g, canon[g["game_id"]]))
            elif g["season_type"] == "preseason":
                pre.append(g)
        return began, cohort, pre

    def threshold_overview(self) -> dict:
        """Every market of the sport: live canonical record per confidence threshold (with the bookmaker's probability for
        the same picks), the primary's history where it is comparable, and the declared best-threshold verdict
        (app/threshold_overview.py)."""
        from .. import threshold_overview as to

        def quote_for(market):
            def q(g, c):
                if market == "winner":
                    return self._winner_quote_prob(g["game_id"], c["record"]["generated_at"])
                m, _ = coverage.matched_quote(self.conn, market, g, c)
                return None if m is None else m[1]
            return q

        blocks = []
        began = None
        for market in sports.get(self.sport).markets:
            began, cohort, _ = self._canonical_cohort(market)
            blocks.append(to.market_block(self.sport, market, markets.spec(self.sport, market).title, cohort,
                                          coverage.threshold_grid(market), quote_for(market)))
        return {"mode": "real", "sport": self.sport, "as_of": self.now, "recording_began_at": began, "rule": to.RULE,
                "break_even_standard": to.BREAK_EVEN_STANDARD, "markets": blocks}

    def _pick_market_prob(self, market: str, g, c: dict) -> Optional[float]:
        """The bookmaker's de-vigged probability for the canonical forecast's picked side, from a quote the forecast could
        see (winner: latest moneyline at or before the forecast; other markets: the quote the forecast used)."""
        from .. import threshold_overview as to
        rec = c["record"]
        if market == "winner":
            q = self._winner_quote_prob(g["game_id"], rec["generated_at"])
        else:
            q, _ = coverage.forecast_quote(self.conn, market, g, rec)
        return to._picked(market, q, rec["side"])

    def ranked_picks(self, day: date) -> dict:
        """Every canonical pick of the date's competitive games in every market, with the threshold it reaches, the past
        evidence for picks like it, the market's probability for it, and its rank for the day and within its game
        (app/threshold_overview.py RANK_RULE). Evidence never includes the date's own games."""
        from .. import threshold_overview as to
        start, end = _day_bounds(day)
        picks, without = [], {}
        for market in sports.get(self.sport).markets:
            grid = coverage.threshold_grid(market)
            title = markets.spec(self.sport, market).title
            _, cohort, _ = self._canonical_cohort(market)
            ladder = to.evidence_ladder(self.sport, market, grid, [(g, c) for g, c in cohort if g["tipoff_at"] < start])
            _, rows, canon = self._canonical_games(start, end, market)
            for g in rows:
                if g["season_type"] not in coverage.COMPETITIVE:
                    continue
                c = canon[g["game_id"]]
                if c["state"] != "forecast":
                    without[c["state"]] = without.get(c["state"], 0) + 1
                    continue
                out = self._canon_out(c, g)
                rec = c["record"]
                home, away = self._team(g["home_team_id"]).abbr, self._team(g["away_team_id"]).abbr
                picks.append({
                    "game_id": g["game_id"], "matchup": f"{away} @ {home}", "tipoff": g["tipoff_at"], "status": g["status"],
                    "market": market, "market_title": title, "pick_label": out.record.pick_label,
                    "forecast_id": rec['forecast_id'],
                    "opposite_pick_label": out.record.opposite_pick_label,
                    "probability": rec["confidence"], "threshold": to.threshold_reached(rec["confidence"], grid),
                    "evidence": to.pick_evidence(rec["confidence"], ladder, grid),
                    "percentage_history": out.percentage_history,
                    "market_prob": self._pick_market_prob(market, g, c), "grade": c["grade"],
                    "generated_at": rec["generated_at"]})
        return {"mode": "real", "sport": self.sport, "date": day.isoformat(), "as_of": self.now, "rule": to.RANK_RULE,
                "min_settled": to.MIN_LIVE_GRADED, "picks": to.rank(picks), "without_forecast": without}

    def canonical_performance(self, market: str = "winner") -> CanonicalPerformanceResponse:
        began, cohort, pre = self._canonical_cohort(market)
        summary = coverage.canonical_summary(cohort, market)
        scored = [(g, c, coverage.scored(market, c)) for g, c in cohort]
        scored = [(g, c, s) for g, c, s in scored if s is not None]
        unmatched = None
        if market == "winner":   # P(home) against the latest moneyline retrieved at or before the forecast
            calibration = metrics.calibration([s[0] for _, _, s in scored], [s[1] for _, _, s in scored],
                                              metrics.API_CALIBRATION_BINS)
            matched = []
            for g, c, (p, y) in scored:
                q = self._winner_quote_prob(g["game_id"], c["record"]["generated_at"])
                if q is not None:
                    matched.append((p, q, y))
            model_brier = metrics.brier([m[0] for m in matched], [m[2] for m in matched])
            market_brier = metrics.brier([m[1] for m in matched], [m[2] for m in matched])
        else:
            if market == "reg3":   # the picked outcome's probability against whether it happened
                calibration = metrics.calibration([c["record"]["confidence"] for _, c, _ in scored],
                                                  [c["grade"] == "win" for _, c, _ in scored], metrics.API_CALIBRATION_BINS)
            else:                  # P(a | no push) against side a
                calibration = metrics.calibration([s[0] for _, _, s in scored], [s[1] for _, _, s in scored],
                                                  metrics.API_CALIBRATION_BINS)
            matched, unmatched = [], {}
            for g, c, _ in scored:
                m, why = coverage.matched_quote(self.conn, market, g, c)
                if m is None:
                    unmatched[why] = unmatched.get(why, 0) + 1
                else:
                    matched.append(m)
            if market == "reg3":
                ys = [m[2] for m in matched]
                model_brier = metrics.brier_multi([m[0] for m in matched], ys)
                market_brier = metrics.brier_multi([m[1] for m in matched], ys)
            else:
                model_brier = metrics.brier([m[0] for m in matched], [m[2] for m in matched])
                market_brier = metrics.brier([m[1] for m in matched], [m[2] for m in matched])
        experimental = 0
        if pre:
            ids = [g["game_id"] for g in pre]
            experimental = self.conn.execute(
                f"SELECT COUNT(*) FROM game_forecasts WHERE market=? AND status='forecast' "
                f"AND game_id IN ({','.join('?' * len(ids))})", (market, *ids)).fetchone()[0]
        days = [eastern_date(g["tipoff_at"]) for g, _ in cohort]
        title = markets.spec(self.sport, market).title
        return CanonicalPerformanceResponse(
            mode="real", market=market, title=f"Canonical forecasts: {title}",
            note=("One canonical forecast per game and market: the sealed baseline model (no adaptive ensemble is approved "
                  "for this record; adaptive forecasts stay research), recorded within two hours of the start. Every started "
                  "regular-season, play-in and playoff game since canonical recording began is counted: a game without a "
                  "forecast is a coverage gap, never a loss. Accuracy is wins / (wins + losses); pushes, voids and "
                  "unscorable forecasts are counted apart. Metrics cover graded forecasts only"
                  + ("" if market == "winner" else
                     "; no regulation three-way bookmaker quotes are collected, so there is no bookmaker comparison"
                     if market == "reg3" else
                     "; the bookmaker comparison uses only the quote the forecast used, at its exact line, retrieved before "
                     "the forecast") + ". Preseason has no validated model and is reported apart. Forecasts made before "
                  "canonical recording began are in the legacy views."),
            as_of=self.now, recording_began_at=began, date_from=min(days, default=None), date_to=max(days, default=None),
            competitive=CanonicalSummary(**summary),
            calibration=calibration,
            market_comparison=MarketComparison(
                matched_games=len(matched), coverage=len(matched) / len(scored) if scored else 0.0,
                model_brier=model_brier, market_brier=market_brier, unmatched=unmatched),
            preseason=CanonicalPreseason(games=len(pre), unvalidated=len(pre), experimental_rows=experimental,
                                         note=coverage.PRESEASON_UNVALIDATED))

    def performance(self, run_type: str, policy: str = "pregame") -> PerformanceResponse:
        primary = self.near_version if policy == "near_tipoff" else self.model_version
        def scored(model_version: str) -> list[sqlite3.Row]:
            rows = [r for r in self._latest_rows(run_type, model_version)
                    if r["status"] == "final" and not self._moved(r, run_type, policy)]
            if run_type == "backtest":
                rows = [r for r in rows if r["season"] in self.periods["test"]]
            return rows

        def score(rows):
            probs = [r["home_win_prob"] for r in rows]
            ys = [r["home_score"] > r["away_score"] for r in rows]
            return probs, ys

        rows = scored(primary)
        probs, ys = score(rows)
        baselines = []
        if run_type == "backtest":
            for mv, name in BASELINE_NAMES.items():
                bp, by = score(scored(mv))
                no_pick = bool(bp) and all(p == 0.5 for p in bp)     # exactly 50% everywhere: no side is ever picked
                baselines.append(BaselineScore(name=name, accuracy=None if no_pick else metrics.accuracy(bp, by),
                                               brier_score=metrics.brier(bp, by), log_loss=metrics.log_loss(bp, by)))
        matched = []
        for r in rows:
            q = self._winner_quote_prob(r["game_id"], r["generated_at"])
            if q is not None:
                matched.append((r["home_win_prob"], q, r["home_score"] > r["away_score"]))
        days = [eastern_date(r["tipoff_at"]) for r in rows]
        if run_type == "backtest":
            title = "Final test: retrospective backtest, 2024-25 to 2025-26"
            note = ("Retrospective backtest of the model fixed on validation, evaluated once on held-out seasons. "
                    "Not live forecasts. No historical odds, so no market comparison.")
        elif policy == "near_tipoff":
            title = "Near-tipoff live forecasts"
            note = ("Latest near-tipoff forecast per game, made before tipoff from the freshest stored results. Kept "
                    "separate from the pre-game archive. Elo does not know about injuries or line-ups. Market "
                    "comparison uses the latest quote retrieved no later than the forecast itself, where available.")
        else:
            title = "Pre-game archive: live forecasts"
            note = ("Archive of the discontinued daily forecast job: the latest forecast per game recorded before its "
                    "cutoff (midnight Eastern on the game date). Some were generated several days before the game. "
                    "Market comparison uses the latest quote retrieved no later than the forecast itself, where available.")
        return PerformanceResponse(
            mode="real", run_type=run_type, policy=policy, title=title, model_version=primary, sample_size=len(rows),
            date_from=min(days, default=None), date_to=max(days, default=None),
            accuracy=metrics.accuracy(probs, ys), brier_score=metrics.brier(probs, ys),
            log_loss=metrics.log_loss(probs, ys), calibration=metrics.calibration(probs, ys, metrics.API_CALIBRATION_BINS), baselines=baselines,
            market=MarketComparison(
                matched_games=len(matched), coverage=len(matched) / len(rows) if rows else 0.0,
                model_brier=metrics.brier([m[0] for m in matched], [m[2] for m in matched]),
                market_brier=metrics.brier([m[1] for m in matched], [m[2] for m in matched])),
            note=note)


def available_sports() -> list[SportInfo]:
    out = []
    for s in sports.SPORTS.values():
        exists = config.db_path(s.id).is_file()
        out.append(SportInfo(id=s.id, label=s.label, available=exists,
                             note=None if exists else f"No {s.label} data store on this machine."))
    return out


def _market_reference(sport: str, market: str, live_version: Optional[str] = None) -> Optional[dict]:
    """The one-time historical test of an NHL market (local report), labelled; None when there is none. That test scored
    the plain Poisson score grid (nhl_eval.market_scores); a live model with the empty-net adjustment is a different
    model, so the reference says so instead of passing as the live model's backtest."""
    if sport != "nhl":
        return None
    reports = sorted((config.data_dir() / "reports").glob("nhl-test-*/report.json"))
    if not reports:
        return None
    r = json.loads(reports[-1].read_text())
    keys = {"reg3": ["reg3"], "spread": ["spread -1.5", "spread +1.5"], "total": ["total 5.5", "total 6.5"]}.get(market)
    if not keys:
        return None
    same = live_version is not None and "empty_net" not in live_version
    return {"label": "One-time final test (2024-25 and 2025-26), standard lines, no bookmaker comparison",
            "tested_model": nhl_eval_grid(),
            "matches_live_model": same,
            "model_note": None if same else (
                "Earlier model version: this test scored the score grid without the empty-net adjustment. The live model "
                "now adds it, so these figures describe the earlier version, not the current forecasts."),
            "by_line": {k: {x: r["markets"][k]["goals_poisson"][x] for x in ("n", "log_loss", "brier",
                                                                            "pick_accuracy_excluding_mid")}
                        | {"league_reference_log_loss": r["markets"][k]["league_poisson"]["log_loss"]}
                        for k in keys if k in r["markets"]}}


def nhl_eval_grid() -> str:
    from .. import nhl_eval
    return nhl_eval.MARKET_GRID


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
