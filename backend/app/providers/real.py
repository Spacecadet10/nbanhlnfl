"""Real data: games from the SQLite store, forecasts from the append-only ledger, run types kept separate.

Prediction shown for a game: the latest *live* forecast of the primary model generated before its cutoff; otherwise
the primary model's *backtest* prediction (labelled as such); otherwise none, with a `forecast_note` saying why.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import json

from .. import agreement, backtest, config, live, market_live, markets, metrics, near_tipoff, sports
from ..db import utc_now
from ..live import HORIZON_DAYS, LATEST_LIVE_SQL
from ..schemas import (BaselineScore, GameDetail, GamesResponse, GoalieListingOut, GameSummary, HistoryResponse, Market,
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
        if cutoff > self.now:
            horizon = (_parse(self.now) + timedelta(days=HORIZON_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if cutoff > horizon:
                return f"Forecasts are recorded within {HORIZON_DAYS} days of the game by the daily job."
            return "Forecast not recorded yet; the daily job records it before the cutoff."
        return "No pregame forecast was recorded before the cutoff."

    def _market(self, game_id: str, tipoff: str) -> Optional[Market]:
        if self.sport != "nba":   # NHL moneylines are stored with the other line quotes
            row = self.conn.execute(
                "SELECT provider, price_label, quote_time, retrieved_at, p_a_devig AS home_prob_devig FROM market_quotes "
                "WHERE game_id=? AND market='winner' AND retrieved_at <= ? "
                "ORDER BY retrieved_at DESC, price_label = 'current' DESC, quote_id DESC LIMIT 1", (game_id, tipoff)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM odds_quotes WHERE game_id=? AND retrieved_at <= ? "
                "ORDER BY retrieved_at DESC, price_label = 'current' DESC LIMIT 1", (game_id, tipoff)).fetchone()
        if row is None:
            return None
        return Market(bookmaker=row["provider"], price_label=row["price_label"], quote_time=row["quote_time"],
                      retrieved_at=row["retrieved_at"], home_win_prob=row["home_prob_devig"])

    def _summary(self, g: sqlite3.Row, policy: str = "near_tipoff") -> dict:
        pred = self._prediction(g["game_id"])
        outcome = None
        if g["status"] == "final":
            outcome = Outcome(home_score=g["home_score"], away_score=g["away_score"],
                              home_won=g["home_score"] > g["away_score"])
        return dict(game_id=g["game_id"], mode="real", game_date=eastern_date(g["tipoff_at"]), tipoff=g["tipoff_at"],
                    status=g["status"], season_type=g["season_type"], home=self._team(g["home_team_id"]),
                    away=self._team(g["away_team_id"]), prediction=pred,
                    forecast_note=None if pred else self._forecast_note(g),
                    market=self._market(g["game_id"], g["tipoff_at"]), outcome=outcome,
                    near_tipoff=self._near_tipoff(g),
                    agreement=agreement.summary(self.conn, g["game_id"], self.sport, self.now, policy))

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
        last_fetch = self.conn.execute("SELECT MAX(retrieved_at) FROM raw_fetches").fetchone()[0]
        refresh = self._last_refresh() or last_fetch
        odds = self.conn.execute("SELECT MAX(retrieved_at) FROM odds_quotes").fetchone()[0]
        predict = self.conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job='predict' AND status='success'"
                                    ).fetchone()[0]
        lines = self.conn.execute("SELECT MAX(retrieved_at) FROM market_quotes").fetchone()[0]
        if self.sport == "nhl":
            schedule = SourceStatus(name="NHL schedule & results", kind="schedule", mode="real", last_updated=refresh,
                                    freshness=fresh(refresh), note="NHL stats API season game list; refreshed by the daily job.")
            odds, odds_note = lines, "DraftKings lines via ESPN for games in the next 36 h; no quote time published."
        else:
            schedule = SourceStatus(name="ESPN schedule & results", kind="schedule", mode="real", last_updated=refresh,
                                    freshness=fresh(refresh), note="Unofficial ESPN scoreboard API; refreshed by the daily job.")
            odds = max(filter(None, (odds, lines)), default=None)
            odds_note = "Moneylines, spreads and totals for some upcoming games only; no quote time published."
        markets_at = self.conn.execute("SELECT MAX(generated_at) FROM market_forecasts").fetchone()[0]
        return [
            schedule,
            SourceStatus(name="Bookmaker odds (ESPN)", kind="odds", mode="real", last_updated=odds,
                         freshness=fresh(odds), note=odds_note),
            SourceStatus(name="Elo forecasts", kind="forecasts", mode="real", last_updated=predict,
                         freshness=fresh(predict), note="Live forecasts recorded append-only before each game's cutoff."),
            SourceStatus(name="Market forecasts", kind="forecasts", mode="real", last_updated=markets_at,
                         freshness=fresh(markets_at), note="Spread/puck line, totals and team totals from the sealed "
                                                           "score model, each at its exact line."),
        ]

    def games(self, day: date, policy: str = "pregame", min_confidence: Optional[float] = None,
              market: str = "winner") -> GamesResponse:
        start, end = _day_bounds(day)
        rows = self.conn.execute("SELECT * FROM games WHERE tipoff_at >= ? AND tipoff_at < ? ORDER BY tipoff_at, game_id",
                                 (start, end)).fetchall()
        games = [GameSummary(**self._summary(g, policy)) for g in rows]
        if market == "winner":
            selection = apply_slate_policy(games, policy, min_confidence)
        else:   # the selected market and policy only; no fallback to another market or policy
            found = {r["game_id"]: r for r in self._market_latest(policy=policy, market=market,
                                                                 game_ids=[g.game_id for g in games])}
            for g in games:
                r = found.get(g.game_id)
                g.market_forecast = None if r is None else self._market_out(r, g.home, g.away, min_confidence)
            picks = [g.market_forecast for g in games if g.market_forecast is not None]
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
        return MarketForecastOut(
            market=r["market"], title=markets.spec(self.sport, r["market"]).title, policy=r["policy"],
            model_version=r["model_version"], generated_at=r["generated_at"], cutoff_at=r["cutoff_at"], line=r["line"],
            line_source=r["line_source"], sides=sides, pick=side, pick_label=labels[0 if side == "a" else 2],
            confidence=conf, meets_confidence=min_confidence is None or conf >= min_confidence - 1e-9, quote=quote,
            result=result, limitations=markets.limitations(self.sport, r["market"], r["line_source"]))

    def game(self, game_id: str) -> Optional[GameDetail]:
        g = self.conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
        if g is None:
            return None
        cutoff = backtest.cutoff_for(g["tipoff_at"])
        summary = self._summary(g)
        forecasts = [self._market_out(r, summary["home"], summary["away"])
                     for policy in market_live.POLICIES
                     for r in self._market_latest(policy=policy, game_ids=[game_id])]
        goalies = []
        if self.sport == "nhl" and self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='goalie_snapshots'").fetchone():
            from ..ingest import GOALIE_AS_OF_SQL
            goalies = [GoalieListingOut(side=r["side"], player_name=r["player_name"], source_status=r["source_status"],
                                        source_status_label=r["source_status_label"], retrieved_at=r["retrieved_at"])
                       for r in self.conn.execute(f"SELECT * FROM ({GOALIE_AS_OF_SQL}) WHERE game_id = ? ORDER BY side DESC",
                                                  (self.now, game_id))]
        return GameDetail(**summary, stats_as_of=cutoff, projected_score_available=False, goalies=goalies,
                          home_stats=self._stats(g["home_team_id"], g["season"], g["tipoff_at"], cutoff),
                          away_stats=self._stats(g["away_team_id"], g["season"], g["tipoff_at"], cutoff),
                          market_forecasts=forecasts)

    # ---- market history and performance ---------------------------------------------------------------------------
    def _market_latest(self, *, policy: str, **kw) -> list:
        """Latest market forecast per game and market under the CURRENT sealed market model only, as the winner views do
        with theirs: after a re-seal, forecasts of an earlier model version stay stored but are not mixed into (or counted
        twice in) the current record."""
        rows = market_live.latest(self.conn, policy=policy, **kw)
        try:
            current = market_live.model_version(market_live.market_model(self.conn)[1], policy)
        except market_live.MarketModelUnavailable:
            return rows
        return [r for r in rows if r["model_version"] == current]

    def _market_records(self, market: str, policy: str, min_confidence: Optional[float]) -> list[MarketRecord]:
        out = []
        for r in reversed(self._market_latest(policy=policy, market=market)):
            home, away = self._team(r["home_team_id"]), self._team(r["away_team_id"])
            f = self._market_out(r, home, away, min_confidence)
            if not f.meets_confidence:
                continue
            outcome = None
            if r["status"] == "final":
                outcome = Outcome(home_score=r["home_score"], away_score=r["away_score"],
                                  home_won=r["home_score"] > r["away_score"])
            out.append(MarketRecord(game_id=r["game_id"], game_date=eastern_date(r["game_tipoff_at"]),
                                    tipoff=r["game_tipoff_at"], home=home, away=away, forecast=f, outcome=outcome))
        return out

    @staticmethod
    def _record_summary(rows: list[MarketRecord], min_confidence: Optional[float]) -> MarketRecordSummary:
        res = [r.forecast.result for r in rows]
        wins, losses = res.count("win"), res.count("loss")
        return MarketRecordSummary(min_confidence=min_confidence, selected=len(rows),
                                   settled=wins + losses + res.count("push"), wins=wins, losses=losses,
                                   pushes=res.count("push"), voids=res.count("void"),
                                   pending=res.count("pending") + res.count("unscorable"),
                                   win_rate=wins / (wins + losses) if wins + losses else None)

    def market_predictions(self, market: str, policy: str, date_from: Optional[date], date_to: Optional[date],
                           team: Optional[str], limit: int, min_confidence: Optional[float]) -> MarketHistoryResponse:
        rows = [r for r in self._market_records(market, policy, min_confidence)
                if not (date_from and r.game_date < date_from or date_to and r.game_date > date_to)
                and not (team and team.upper() not in (r.home.abbr, r.away.abbr))]
        return MarketHistoryResponse(mode="real", sport=self.sport, market=market, policy=policy, total=len(rows),
                                     record=self._record_summary(rows, min_confidence), predictions=rows[:limit])

    def market_performance(self, market: str, policy: str) -> MarketPerformanceResponse:
        rows = self._market_records(market, policy, None)
        settled_side = {(x["game_id"], x["market"]): market_live.outcome(x)
                        for x in self._market_latest(policy=policy, market=market)}
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
        return MarketPerformanceResponse(
            mode="real", sport=self.sport, market=market, policy=policy,
            title=f"{markets.spec(self.sport, market).title}: live {'near-tipoff' if policy == 'near_tipoff' else 'night-before'} forecasts",
            model_version=rows[0].forecast.model_version if rows else None, forecasts=len(rows),
            record=self._record_summary(rows, None), brier_score=metrics.brier_multi(vecs, real),
            log_loss=metrics.log_loss_multi(vecs, real), line_sources=sources, market_matched=len(matched),
            model_log_loss_on_matched=metrics.log_loss_multi(
                [[m[0][0] / (m[0][0] + m[0][2]), 0.0, m[0][2] / (m[0][0] + m[0][2])] for m in matched], [m[1] for m in matched]),
            market_log_loss_on_matched=metrics.log_loss_multi(
                [[q.prob_a, 0.0, q.prob_b] for _, _, q in matched], [k for _, k, _ in matched]),
            reference=_market_reference(self.sport, market),
            limitations=markets.limitations(self.sport, market, None),
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
        Eastern game date (night-before policy). Backtest rows are computed from final schedules and never void."""
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
                void=moved or r["status"] in ("postponed", "canceled")))
        return HistoryResponse(mode="real", run_type=run_type, policy=policy, total=len(out),
                               record=confidence_record(out, min_confidence), predictions=out[:limit])

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
                baselines.append(BaselineScore(name=name, accuracy=metrics.accuracy(bp, by),
                                               brier_score=metrics.brier(bp, by), log_loss=metrics.log_loss(bp, by)))
        matched = []
        for r in rows:
            # Fair comparison: only a quote the forecast could have seen (retrieved no later than its generation).
            q = self.conn.execute(
                "SELECT home_prob_devig FROM odds_quotes WHERE game_id=? AND retrieved_at <= ? "
                "ORDER BY retrieved_at DESC LIMIT 1" if self.sport == "nba" else     # NHL moneylines: market_quotes
                "SELECT p_a_devig FROM market_quotes WHERE game_id=? AND market='winner' AND retrieved_at <= ? "
                "ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (r["game_id"], r["generated_at"])).fetchone()
            if q:
                matched.append((r["home_win_prob"], q[0], r["home_score"] > r["away_score"]))
        days = [eastern_date(r["tipoff_at"]) for r in rows]
        if run_type == "backtest":
            title = "Final test: retrospective backtest, 2024-25 to 2025-26"
            note = ("Retrospective backtest of the model fixed on validation, evaluated once on held-out seasons. "
                    "Not live forecasts. No historical odds, so no market comparison.")
        elif policy == "near_tipoff":
            title = "Near-tipoff live forecasts"
            note = ("Latest near-tipoff forecast per game, made before tipoff from the freshest stored results. Kept "
                    "separate from the night-before record. Elo does not know about injuries or line-ups. Market "
                    "comparison uses the latest quote retrieved no later than the forecast itself, where available.")
        else:
            title = "Live forecasts"
            note = ("Latest forecast per game recorded before its cutoff. Market comparison uses the latest quote "
                    "retrieved no later than the forecast itself, where available.")
        return PerformanceResponse(
            mode="real", run_type=run_type, policy=policy, title=title, model_version=primary, sample_size=len(rows),
            date_from=min(days, default=None), date_to=max(days, default=None),
            accuracy=metrics.accuracy(probs, ys), brier_score=metrics.brier(probs, ys),
            log_loss=metrics.log_loss(probs, ys), calibration=metrics.calibration(probs, ys), baselines=baselines,
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


def _market_reference(sport: str, market: str) -> Optional[dict]:
    """The one-time historical test of an NHL market (local report), labelled; None when there is none."""
    if sport != "nhl":
        return None
    reports = sorted((config.data_dir() / "reports").glob("nhl-test-*/report.json"))
    if not reports:
        return None
    r = json.loads(reports[-1].read_text())
    keys = {"reg3": ["reg3"], "spread": ["spread -1.5", "spread +1.5"], "total": ["total 5.5", "total 6.5"]}.get(market)
    if not keys:
        return None
    return {"label": "One-time final test (2024-25 and 2025-26), standard lines, no bookmaker comparison",
            "by_line": {k: {x: r["markets"][k]["goals_poisson"][x] for x in ("n", "log_loss", "brier",
                                                                            "pick_accuracy_excluding_mid")}
                        | {"league_reference_log_loss": r["markets"][k]["league_poisson"]["log_loss"]}
                        for k in keys if k in r["markets"]}}


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
