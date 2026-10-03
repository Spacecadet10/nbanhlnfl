"""Near-tipoff forecasts: the frozen Elo model run shortly before a game (docs/EVALUATION_PLAN.md, "Near-tipoff
forecast policy").

Kept apart from the night-before forecast: the same Elo configuration, but stored under its own model_version with the
game's tipoff as its cutoff, so the night-before queries, the shadow report and the default app views never read these
rows. A forecast is refused, with the reason, rather than made from stale or incomplete data. The model knows nothing
about injuries or line-ups.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import backtest, live, metrics
from .db import transaction, utc_now
from .ledger import Forecast, record
from .models.elo import Elo

POLICY = "near_tipoff"
FEATURE_VERSION = "pregame-ids-v1+tipoff-cutoff+two-hour-v1"
WINDOW = timedelta(hours=2)            # only games tipping off this soon
MAX_DATA_AGE = timedelta(minutes=60)    # the last successful results refresh must be at most this old
RECENT_TIPOFF = timedelta(hours=6)      # games that started this recently are listed as refused, for visibility
RESULT_LOOKBACK = timedelta(days=7)     # an earlier game without a final result blocks a forecast for this long
FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(value: str) -> datetime:
    return datetime.strptime(value, FMT).replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(FMT)


def model_version(elo: Elo) -> str:
    return f"{backtest.model_version(elo)}-window=2h-policy={POLICY}"


def current_version(conn: sqlite3.Connection) -> str:
    return model_version(live.primary_model(conn)[1])


def results_refreshed_at(conn: sqlite3.Connection, now: str) -> Optional[str]:
    """Finish time of the last successful schedule and results refresh at or before `now`."""
    return conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job = 'refresh' AND status = 'success' "
                        "AND finished_at <= ?", (now,)).fetchone()[0]


def feature_version(eng) -> str:
    return FEATURE_VERSION if eng.sport == "nba" else eng.feature_version.replace("prior-day-et-cutoff", "tipoff-cutoff+two-hour-v1")


def _market(conn: sqlite3.Connection, game_id: str, at: str) -> Optional[dict]:
    """Latest current moneyline retrieved at or before `at`. NBA: odds_quotes (its quote id is stored with the forecast).
    NHL: market_quotes (append-only, so the same snapshot is found again from the forecast's generation time; the
    freshness record keeps no quote id because its column refers to odds_quotes)."""
    if live.engine(conn).sport == "nba":
        row = conn.execute("SELECT quote_id, home_prob_devig, retrieved_at, provider FROM odds_quotes WHERE game_id = ? "
                           "AND price_label = 'current' AND retrieved_at <= ? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1",
                           (game_id, at)).fetchone()
        return dict(row) if row else None
    row = conn.execute("SELECT quote_id, p_a_devig AS home_prob_devig, retrieved_at, provider FROM market_quotes "
                       "WHERE game_id = ? AND market = 'winner' AND price_label = 'current' AND retrieved_at <= ? "
                       "ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (game_id, at)).fetchone()
    return {**dict(row), "quote_id": None, "stored": False} if row else None


def _unfinished_earlier_games(conn: sqlite3.Connection, team_id: str, now: str) -> list[str]:
    """Eligible games of this team that tipped off in the last RESULT_LOOKBACK but are not final in the store."""
    types = live.engine(conn).eligible_types
    return [r[0] for r in conn.execute(
        f"SELECT game_id FROM games WHERE (home_team_id = ? OR away_team_id = ?) AND tipoff_at <= ? AND tipoff_at > ? "
        f"AND status IN ('scheduled', 'in_progress') AND season_type IN ({', '.join('?' * len(types))}) ORDER BY tipoff_at",
        (team_id, team_id, now, _fmt(_parse(now) - RESULT_LOOKBACK), *types))]


def _entry(g: sqlite3.Row, generated_at: str, p: float, market_home_prob: Optional[float],
           market_retrieved_at: Optional[str], status: str) -> dict:
    return {"game_id": g["game_id"], "tipoff_at": g["tipoff_at"], "status": status, "generated_at": generated_at,
            "minutes_before_tipoff": round((_parse(g["tipoff_at"]) - _parse(generated_at)).total_seconds() / 60, 1),
            "home_win_prob": p, "pick": "home" if p >= 0.5 else "away", "confidence": max(p, 1 - p),
            "market_home_prob": market_home_prob, "market_retrieved_at": market_retrieved_at}


def predict_now(conn: sqlite3.Connection, *, now: Optional[str] = None, write: bool = True) -> dict:
    """Forecasts every eligible game tipping off within WINDOW of `now`, or refuses it with a reason. Makes no network
    request: the caller refreshes the data first (jobs.predict_now). `now` is for tests; real runs use the clock."""
    fixed_clock = now is not None
    now = now or utc_now()
    _, model = live.primary_model(conn)
    rows = live.available_rows(conn, now)
    for r in sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id)):
        model.update(r.result)
    eng = live.engine(conn)
    mv, data_version = model_version(model), eng.dataset_version(rows)
    refreshed = results_refreshed_at(conn, now)
    stale = refreshed is None or _parse(now) - _parse(refreshed) > MAX_DATA_AGE
    out = {"as_of": now, "policy": POLICY, "model_version": mv, "dataset_version": data_version,
           "results_refreshed_at": refreshed, "results_used": len(rows),
           "latest_result_available_at": max((r.result_available_at for r in rows), default=None),
           "window_end": _fmt(_parse(now) + WINDOW), "written": write, "created": 0, "already_recorded": 0,
           "forecasts": [], "refused": [],
           "limitation": "Elo uses results only; it does not know about injuries, rest or line-ups."}
    types = eng.eligible_types
    games = conn.execute(
        f"SELECT * FROM games WHERE status IN ('scheduled', 'in_progress') AND tipoff_at > ? AND tipoff_at <= ? "
        f"AND season_type IN ({', '.join('?' * len(types))}) ORDER BY tipoff_at, game_id",
        (_fmt(_parse(now) - RECENT_TIPOFF), out["window_end"], *types)).fetchall()
    for g in games:
        refusal = None
        if g["tipoff_at"] <= now or g["status"] != "scheduled":
            refusal = ("after_tipoff", "the game has already tipped off")
        elif stale:
            refusal = ("stale_results", "no successful schedule and results refresh in the last "
                                        f"{int(MAX_DATA_AGE.total_seconds() // 60)} minutes (last: {refreshed})")
        else:
            missing = sorted({gid for team in (g["home_team_id"], g["away_team_id"])
                              for gid in _unfinished_earlier_games(conn, team, now)})
            if missing:
                refusal = ("missing_result", f"earlier games of these teams have no final result yet: {', '.join(missing)}")
        if refusal:
            out["refused"].append({"game_id": g["game_id"], "tipoff_at": g["tipoff_at"], "reason": refusal[0],
                                   "detail": refusal[1]})
            continue
        p = round(model.predict(eng.pregame(g)), 9)
        generated = now if fixed_clock else utc_now()
        if generated >= g['tipoff_at']:
            out['refused'].append(dict(game_id=g['game_id'], tipoff_at=g['tipoff_at'], reason='after_tipoff',
                                       detail='calculation completed after tipoff'))
            continue
        if not write:   # the current calculation only; nothing is stored and nothing stored is consulted
            quote = _market(conn, g["game_id"], now)
            out["forecasts"].append(_entry(g, generated, p, quote["home_prob_devig"] if quote else None,
                                           quote["retrieved_at"] if quote else None, status="dry_run"))
            continue
        prediction_id, new = record(conn, Forecast(g["game_id"], "live", mv, data_version, feature_version(eng),
                                                   g["tipoff_at"], generated, p))
        out["created"] += new
        out["already_recorded"] += not new
        with transaction(conn):
            if not conn.execute("SELECT 1 FROM near_tipoff_forecasts WHERE prediction_id = ?", (prediction_id,)).fetchone():
                stored_at = conn.execute("SELECT generated_at FROM predictions WHERE prediction_id = ?",
                                         (prediction_id,)).fetchone()[0]
                at_time = _market(conn, g["game_id"], stored_at)
                conn.execute(
                    "INSERT INTO near_tipoff_forecasts (prediction_id, game_id, generated_at, tipoff_at, "
                    "results_refreshed_at, latest_result_available_at, results_used, market_quote_id, market_home_prob, "
                    "market_retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (prediction_id, g["game_id"], stored_at, g["tipoff_at"], refreshed, out["latest_result_available_at"],
                     len(rows), at_time["quote_id"] if at_time and at_time.get("stored", True) else None,
                     at_time["home_prob_devig"] if at_time and at_time.get("stored", True) else None,
                     at_time["retrieved_at"] if at_time and at_time.get("stored", True) else None))
        # Report what is stored, not what was just recomputed: on a retry the retained forecast keeps its own
        # generation time and the market snapshot taken then.
        kept = conn.execute(
            "SELECT p.generated_at, p.home_win_prob, n.market_home_prob, n.market_retrieved_at, n.results_refreshed_at "
            "FROM predictions p JOIN near_tipoff_forecasts n ON n.prediction_id = p.prediction_id "
            "WHERE p.prediction_id = ?", (prediction_id,)).fetchone()
        market_prob, market_at = kept["market_home_prob"], kept["market_retrieved_at"]
        if eng.sport != "nba":   # the NHL snapshot is not stored with the forecast; it is found again from generation time
            snap = _market(conn, g["game_id"], kept["generated_at"])
            market_prob, market_at = (snap["home_prob_devig"], snap["retrieved_at"]) if snap else (None, None)
        out["forecasts"].append({
            **_entry(g, kept["generated_at"], kept["home_win_prob"], market_prob, market_at,
                     status="created" if new else "already_recorded"),
            "prediction_id": prediction_id, "results_refreshed_at": kept["results_refreshed_at"]})
    return out


def market_at(conn: sqlite3.Connection, row) -> tuple[Optional[float], Optional[str]]:
    """(de-vigged home probability, retrieval time) of the moneyline a stored forecast could see. NBA: the snapshot stored
    with the forecast. NHL: the freshness record keeps no quote, so it is the latest current quote retrieved at or before
    the forecast's generation time (quotes are append-only, so this is the snapshot taken then)."""
    if live.engine(conn).sport == "nba":
        return row["market_home_prob"], row["market_retrieved_at"]
    snap = _market(conn, row["game_id"], row["generated_at"])
    return (snap["home_prob_devig"], snap["retrieved_at"]) if snap else (None, None)


def start_changed(row) -> bool:
    """A near-tipoff forecast is made for the start known when it was generated (its cutoff). If the game's start has
    changed since, the forecast is void: it is never scored, as research cohorts (research_live.state)."""
    return row["cutoff_at"] != row["tipoff_at"]


def latest_rows(conn: sqlite3.Connection, version: Optional[str] = None) -> list[sqlite3.Row]:
    """Latest near-tipoff forecast per game with its freshness record and the game's result fields."""
    return conn.execute(
        f"SELECT l.*, n.results_refreshed_at, n.latest_result_available_at, n.results_used, n.market_home_prob, "
        f"n.market_retrieved_at, g.status, g.tipoff_at, g.home_score, g.away_score, g.season, g.season_type, "
        f"g.home_team_id, g.away_team_id FROM ({live.LATEST_LIVE_SQL}) l JOIN games g ON g.game_id = l.game_id "
        f"LEFT JOIN near_tipoff_forecasts n ON n.prediction_id = l.prediction_id ORDER BY g.tipoff_at DESC, l.game_id",
        (version or current_version(conn),)).fetchall()


def score(conn: sqlite3.Connection) -> dict:
    """Read-only scoring of the latest near-tipoff forecast per game, separate from the night-before record. Where
    the same game also has a night-before Elo forecast or a market quote from generation time, they are scored on
    those same games."""
    _, model = live.primary_model(conn)
    rows = latest_rows(conn, model_version(model))
    final = [r for r in rows if r["status"] == "final" and not start_changed(r)]
    ys = {r["game_id"]: r["home_score"] > r["away_score"] for r in final}

    def summary(pairs: list[tuple[float, bool]]) -> dict:
        probs, outcomes = [p for p, _ in pairs], [y for _, y in pairs]
        wins = sum((p >= 0.5) == y for p, y in pairs)
        return {"games": len(pairs), "wins": wins, "losses": len(pairs) - wins, "accuracy": metrics.accuracy(probs, outcomes),
                "brier": metrics.brier(probs, outcomes), "log_loss": metrics.log_loss(probs, outcomes)}
    night = {r["game_id"]: r["home_win_prob"] for r in conn.execute(live.LATEST_LIVE_SQL, (backtest.model_version(model),))}
    both = [r for r in final if r["game_id"] in night]
    quoted = {r["game_id"]: market_at(conn, r)[0] for r in final}
    priced = [r for r in final if quoted[r["game_id"]] is not None]
    return {
        "policy": POLICY, "model_version": model_version(model), "forecasts": len(rows),
        "pending": sum(r["status"] in ("scheduled", "in_progress") and not start_changed(r) for r in rows),
        "not_played": sum(r["status"] in ("postponed", "canceled") for r in rows),
        "start_changed": sum(r["status"] not in ("postponed", "canceled") and start_changed(r) for r in rows),
        "scored": summary([(r["home_win_prob"], ys[r["game_id"]]) for r in final]),
        "same_games_as_night_before": {
            "near_tipoff": summary([(r["home_win_prob"], ys[r["game_id"]]) for r in both]),
            "night_before": summary([(night[r["game_id"]], ys[r["game_id"]]) for r in both])},
        "same_games_as_market": {
            "coverage": len(priced) / len(final) if final else 0.0,
            "near_tipoff": summary([(r["home_win_prob"], ys[r["game_id"]]) for r in priced]),
            "market": summary([(quoted[r["game_id"]], ys[r["game_id"]]) for r in priced])},
        "note": "Descriptive only. A near-tipoff forecast has more information than a night-before one; no decision follows.",
    }
