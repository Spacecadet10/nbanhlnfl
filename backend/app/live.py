"""Live pregame forecasting with the frozen primary model (Elo), recorded append-only in the ledger.

Point-in-time rules match the backtest: a game on Eastern date D has cutoff 00:00 ET on D, and a live forecast must
be generated before that cutoff (the ledger enforces this). The model state uses only results whose
result_available_at <= generation time. Reruns with the same data return the stored forecast (same idempotency key);
new results change the dataset version and create a new forecast version. The latest version before the cutoff is
the one shown and scored.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import backtest
from .db import utc_now
from .ledger import Forecast, record
from .models.base import PregameGame
from .models.elo import Elo

HORIZON_DAYS = 7
ELIGIBLE_TYPES = backtest.SEASON_TYPES  # preseason and exhibitions are not forecast (the model excludes them)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def available_rows(conn: sqlite3.Connection, as_of: str) -> list[backtest.Row]:
    """Every eligible final result available at `as_of` (all seasons; the final test is complete)."""
    rows = backtest.load_rows(conn, "live")
    return [r for r in rows if r.result_available_at <= as_of]


def primary_model(conn: sqlite3.Connection) -> tuple[dict, Elo]:
    decision, cfg = backtest.load_frozen_decision(conn)
    return decision, Elo(cfg)


def predict(conn: sqlite3.Connection, *, now: Optional[str] = None, horizon_days: int = HORIZON_DAYS) -> dict:
    """Records live forecasts for eligible scheduled games whose cutoff is after `now` and within the horizon.
    `now` is for tests; the CLI always uses the real clock."""
    now = now or utc_now()
    horizon = _ts(_parse(now) + timedelta(days=horizon_days))
    _, model = primary_model(conn)
    rows = available_rows(conn, now)
    version = backtest.dataset_version(rows)
    for r in sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id)):
        model.update(r.result)

    upcoming = []
    for g in conn.execute(
            f"SELECT * FROM games WHERE status = 'scheduled' AND season_type IN ({', '.join('?' * len(ELIGIBLE_TYPES))}) "
            "ORDER BY tipoff_at, game_id", ELIGIBLE_TYPES):
        cutoff = backtest.cutoff_for(g["tipoff_at"])
        if now < cutoff <= horizon:
            neutral = bool(g["neutral_site"])
            upcoming.append((cutoff, PregameGame(g["game_id"], g["season"], g["season_type"], g["tipoff_at"],
                                                 g["home_team_id"], g["away_team_id"], neutral)))
    created = existing = 0
    mv = backtest.model_version(model)
    for cutoff, game in sorted(upcoming, key=lambda x: (x[0], x[1].tipoff_at, x[1].game_id)):
        p = round(model.predict(game), 9)
        _, new = record(conn, Forecast(game.game_id, "live", mv, version, backtest.FEATURE_VERSION, cutoff, now, p))
        created += new
        existing += not new
    return {"as_of": now, "horizon_end": horizon, "model_version": mv, "dataset_version": version,
            "results_used": len(rows), "eligible_games": len(upcoming), "created": created, "already_recorded": existing}


# Exactly one row per game: the latest pre-cutoff version. Timestamps have 1 s precision, so ties on generated_at are
# broken by prediction_id (insertion order); the ledger's triggers already reject live rows generated after the cutoff.
LATEST_LIVE_SQL = """
    SELECT p.* FROM predictions p
    WHERE p.run_type = 'live' AND p.model_version = ?
      AND p.prediction_id = (SELECT q.prediction_id FROM predictions q
                             WHERE q.run_type = 'live' AND q.game_id = p.game_id AND q.model_version = p.model_version
                               AND q.generated_at <= q.cutoff_at
                             ORDER BY q.generated_at DESC, q.prediction_id DESC LIMIT 1)
"""


def score(conn: sqlite3.Connection) -> dict:
    """Read-only postgame scoring of the latest live forecast per game against stored final results."""
    from . import metrics
    _, model = primary_model(conn)
    mv = backtest.model_version(model)
    rows = conn.execute(f"SELECT l.game_id, l.home_win_prob, g.status, g.home_score, g.away_score "
                        f"FROM ({LATEST_LIVE_SQL}) l JOIN games g ON g.game_id = l.game_id", (mv,)).fetchall()
    final = [r for r in rows if r["status"] == "final"]
    probs = [r["home_win_prob"] for r in final]
    ys = [r["home_score"] > r["away_score"] for r in final]
    return {"model_version": mv, "live_forecasts": len(rows), "scored": len(final),
            "pending": sum(r["status"] == "scheduled" for r in rows),
            "not_played": sum(r["status"] in ("postponed", "canceled") for r in rows),
            "brier": metrics.brier(probs, ys), "accuracy": metrics.accuracy(probs, ys)}
