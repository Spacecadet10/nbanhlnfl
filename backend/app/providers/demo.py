"""Synthetic demo data (fixtures). Every payload says mode='demo' and run_type='demo'."""
from datetime import date
from typing import Optional

from .. import fixtures, metrics
from ..schemas import (GameDetail, GamesResponse, HistoryResponse, MarketComparison, Meta, PerformanceResponse,
                       PredictionRecord, SourceStatus, confidence_record, meets_confidence, pick_confidence)

SUMMARY_EXCLUDE = {"home_stats", "away_stats", "projected_score_available", "stats_as_of"}


class DemoProvider:
    mode = "demo"

    def meta(self) -> Meta:
        return Meta(mode="demo", default_date=fixtures.DEFAULT_DATE, available_dates=fixtures.demo_dates(),
                    teams=sorted(fixtures.TEAMS.values(), key=lambda t: t.abbr), primary_model=fixtures.MODEL_VERSION,
                    last_refresh_at=None)

    def sources(self) -> list[SourceStatus]:
        return fixtures.SOURCES

    def games(self, day: date) -> GamesResponse:
        return GamesResponse(mode="demo", date=day, sources=fixtures.SOURCES,
                             games=[g.model_dump(exclude=SUMMARY_EXCLUDE) for g in fixtures.games() if g.game_date == day])

    def game(self, game_id: str) -> Optional[GameDetail]:
        return next((g for g in fixtures.games() if g.game_id == game_id), None)

    def predictions(self, run_type: str, date_from: Optional[date], date_to: Optional[date], team: Optional[str],
                    limit: int, min_confidence: Optional[float] = None) -> HistoryResponse:
        rows = []
        for g in fixtures.games():
            if not meets_confidence(g.prediction.home_win_prob, min_confidence):
                continue
            if date_from and g.game_date < date_from or date_to and g.game_date > date_to:
                continue
            if team and team.upper() not in (g.home.abbr, g.away.abbr):
                continue
            p = g.prediction
            rows.append(PredictionRecord(
                game_id=g.game_id, run_type="demo", game_date=g.game_date, tipoff=g.tipoff, home=g.home, away=g.away,
                model_version=p.model_version, generated_at=p.generated_at, cutoff_at=p.cutoff_at,
                home_win_prob=p.home_win_prob, pick="home" if p.home_win_prob >= 0.5 else "away",
                confidence=pick_confidence(p.home_win_prob), outcome=g.outcome,
                correct=None if g.outcome is None else (p.home_win_prob >= 0.5) == g.outcome.home_won))
        rows.sort(key=lambda r: r.tipoff, reverse=True)
        return HistoryResponse(mode="demo", run_type="demo", total=len(rows),
                               record=confidence_record(rows, min_confidence), predictions=rows[:limit])

    def performance(self, run_type: str) -> PerformanceResponse:
        scored = [g for g in fixtures.games() if g.outcome is not None]
        probs = [g.prediction.home_win_prob for g in scored]
        ys = [g.outcome.home_won for g in scored]
        matched = [g for g in scored if g.market is not None]
        return PerformanceResponse(
            mode="demo", run_type="demo", title="Demo fixtures", model_version=fixtures.MODEL_VERSION,
            sample_size=len(scored), date_from=min((g.game_date for g in scored), default=None),
            date_to=max((g.game_date for g in scored), default=None),
            accuracy=metrics.accuracy(probs, ys), brier_score=metrics.brier(probs, ys),
            log_loss=metrics.log_loss(probs, ys), calibration=metrics.calibration(probs, ys), baselines=[],
            market=MarketComparison(
                matched_games=len(matched), coverage=len(matched) / len(scored) if scored else 0.0,
                model_brier=metrics.brier([g.prediction.home_win_prob for g in matched],
                                          [g.outcome.home_won for g in matched]),
                market_brier=metrics.brier([g.market.home_win_prob for g in matched],
                                           [g.outcome.home_won for g in matched])),
            note="Demo metrics computed from synthetic fixtures with simulated outcomes. "
                 "They say nothing about real prediction accuracy.")
