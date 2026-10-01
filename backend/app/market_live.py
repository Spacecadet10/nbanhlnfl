"""Live market forecasts (docs/MARKETS.md): regulation 3-way, spread / puck line, totals and team totals.

All markets of a game come from one score distribution of the sport's sealed market model, built from results available
at generation time. Each forecast stores its exact line: the latest bookmaker line retrieved at or before generation
(`market`), or, with no quote, a half-point line next to the model's median (`model`, labelled model-only). A rerun with
unchanged inputs and line returns the stored forecast; a new line or new results make a new forecast, and earlier ones
are kept. Settlement is computed from stored results; nothing here is written after the cutoff.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import backtest, live, markets, metrics, sports
from .db import utc_now
from .ledger import MarketForecast, record_market

POLICIES = ("pregame", "near_tipoff")


class MarketModelUnavailable(RuntimeError):
    pass


def market_model(conn: sqlite3.Connection):
    """(model, base model version) of the sport's sealed market model, or MarketModelUnavailable."""
    eng = live.engine(conn)
    if eng.sport == "nhl":
        from . import nhl_eval
        from .models.hockey import GoalsConfig, GoalsPoisson
        d = nhl_eval.load_decision(conn)
        model = GoalsPoisson(GoalsConfig(**d["goals_config"]), sports.NHL.rating_key)
        return model, backtest.model_version(model)
    from . import nba_markets
    return nba_markets.sealed_model(conn)


def model_version(base: str, policy: str) -> str:
    return base if policy == "pregame" else f"{base}-policy={policy}"


def feature_version(eng, policy: str) -> str:
    base = eng.feature_version
    return base if policy == "pregame" else base.replace("prior-day-et-cutoff", "tipoff-cutoff")


def latest_quote(conn: sqlite3.Connection, game_id: str, market: str, at: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM market_quotes WHERE game_id = ? AND market = ? AND price_label = 'current' "
                        "AND retrieved_at <= ? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1",
                        (game_id, market, at)).fetchone()


def forecast(conn: sqlite3.Connection, *, now: Optional[str] = None, policy: str = "pregame",
             game_ids: Optional[list[str]] = None, horizon_days: int = live.HORIZON_DAYS, write: bool = True) -> dict:
    """Records market forecasts. `pregame`: scheduled eligible games whose night-before cutoff is after `now` and within
    the horizon. `near_tipoff`: exactly `game_ids` (games the near-tipoff winner step accepted), cutoff = tipoff."""
    if policy not in POLICIES:
        raise ValueError(policy)
    now = now or utc_now()
    eng = live.engine(conn)
    model, base = market_model(conn)
    rows = live.available_rows(conn, now)
    version = eng.dataset_version(rows)
    for r in sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id)):
        model.update(r.result)
    mv, fv = model_version(base, policy), feature_version(eng, policy)
    types = eng.eligible_types
    candidates = conn.execute(
        f"SELECT * FROM games WHERE status = 'scheduled' AND tipoff_at > ? AND season_type IN ({', '.join('?' * len(types))}) "
        "ORDER BY tipoff_at, game_id", (now, *types)).fetchall()
    end = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
           + timedelta(days=horizon_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chosen = []
    for g in candidates:
        if policy == "pregame":
            cutoff = backtest.cutoff_for(g["tipoff_at"])
            if now < cutoff <= end:
                chosen.append((g, cutoff))
        elif g["game_id"] in (game_ids or []):
            chosen.append((g, g["tipoff_at"]))
    out = {"sport": eng.sport, "policy": policy, "as_of": now, "model_version": mv, "dataset_version": version,
           "results_used": len(rows), "games": len(chosen), "created": 0, "already_recorded": 0, "forecasts": []}
    markets_for_sport = [m for m in sports.get(eng.sport).markets if m != "winner"]
    for g, cutoff in chosen:
        game = eng.pregame(g)
        dist = model.distribution(game)
        for market in markets_for_sport:
            line = quote = None
            if market != "reg3":
                quote = latest_quote(conn, g["game_id"], market, now)
                line = quote["line"] if quote else markets.model_line(dist, market)
            pa, pmid, pb = (round(x, 9) for x in dist.market(market, line))
            pb = round(1 - pa - pmid, 9)
            # The latest stored forecast for this game, market and policy: if its line differs, this is a line change. When
            # the line returns to one forecast earlier (A -> B -> A), the new forecast gets its own identity, so the
            # current line is shown as the latest instead of the earlier forecast being returned as a retry.
            prev = conn.execute("SELECT line FROM market_forecasts WHERE game_id=? AND market=? AND policy=? AND run_type='live' "
                                "AND model_version=? ORDER BY generated_at DESC, forecast_id DESC LIMIT 1",
                                (g["game_id"], market, policy, mv)).fetchone()
            revision = now if prev is not None and prev["line"] != line else ""
            f = MarketForecast(eng.sport, g["game_id"], market, line,
                               None if line is None else ("market" if quote else "model"),
                               quote["quote_id"] if quote else None, policy, "live", mv, version, fv, cutoff, now,
                               g["tipoff_at"], pa, pmid, pb, json.dumps(dist.params, sort_keys=True), revision)
            entry = {"game_id": g["game_id"], "market": market, "line": line, "line_source": f.line_source,
                     "p": [pa, pmid, pb]}
            if write:
                fid, new = record_market(conn, f)
                out["created"] += new
                out["already_recorded"] += not new
                entry.update(forecast_id=fid, status="created" if new else "already_recorded")
            out["forecasts"].append(entry)
    return out


# --- Reading and settlement --------------------------------------------------------------------------------------
LATEST_SQL = """
    SELECT f.*, g.status, g.tipoff_at AS game_tipoff_at, g.home_score, g.away_score, g.home_team_id, g.away_team_id,
           d.decision,
           q.price_a, q.price_b, q.p_a_devig, q.p_b_devig, q.provider AS quote_provider, q.retrieved_at AS quote_retrieved_at
    FROM market_forecasts f
    JOIN games g ON g.game_id = f.game_id
    LEFT JOIN game_decisions d ON d.game_id = f.game_id
    LEFT JOIN market_quotes q ON q.quote_id = f.market_quote_id
    WHERE f.run_type = 'live' AND f.forecast_id = (
        SELECT x.forecast_id FROM market_forecasts x
        WHERE x.game_id = f.game_id AND x.market = f.market AND x.policy = f.policy AND x.run_type = 'live'
          AND x.model_version = f.model_version AND x.generated_at <= x.cutoff_at
        ORDER BY x.generated_at DESC, x.forecast_id DESC LIMIT 1)
"""


def pick(row) -> str:
    """The side the forecast favours ('a' or 'b'); the middle (push/draw) is never a pick."""
    return "a" if row["p_a"] >= row["p_b"] else "b"


def result_for_pick(market: str, side: str, settled: str) -> str:
    """'win' / 'loss' / 'push' for the picked side. A push is a refunded line; a regulation draw (reg3 'mid') is a real
    outcome the pick did not get, so it is a loss, never a push (docs/MARKETS.md)."""
    if settled == "mid":
        return "loss" if market == "reg3" else "push"
    return "win" if settled == side else "loss"


def outcome(row) -> str:
    return markets.settle(row["market"], row["line"], status=row["status"], home=row["home_score"], away=row["away_score"],
                          decision=row["decision"], tipoff_at=row["game_tipoff_at"], forecast_tipoff_at=row["tipoff_at"])


def latest(conn: sqlite3.Connection, *, policy: str = "pregame", market: Optional[str] = None,
           game_ids: Optional[list[str]] = None) -> list[sqlite3.Row]:
    sql, params = LATEST_SQL + " AND f.policy = ?", [policy]
    if market:
        sql += " AND f.market = ?"
        params.append(market)
    if game_ids is not None:
        if not game_ids:
            return []
        sql += f" AND f.game_id IN ({', '.join('?' * len(game_ids))})"
        params += game_ids
    return conn.execute(sql + " ORDER BY g.tipoff_at, f.game_id, f.market", params).fetchall()


def score(conn: sqlite3.Connection, *, policy: str = "pregame", market: Optional[str] = None,
          min_confidence: Optional[float] = None) -> dict:
    """Win / loss / push / void / pending counts relative to each forecast's pick, plus multi-outcome Brier and log loss
    on settled forecasts, per market. `min_confidence` keeps forecasts whose picked side has at least that probability."""
    by_market: dict[str, dict] = {}
    for r in latest(conn, policy=policy, market=market):
        side = pick(r)
        conf = r["p_a"] if side == "a" else r["p_b"]
        if min_confidence is not None and conf + 1e-9 < min_confidence:
            continue
        m = by_market.setdefault(r["market"], {"forecasts": 0, "win": 0, "loss": 0, "push": 0, "void": 0, "pending": 0,
                                               "unscorable": 0, "vectors": [], "realised": [],
                                               "line_sources": {"market": 0, "model": 0}})
        m["forecasts"] += 1
        if r["line_source"]:
            m["line_sources"][r["line_source"]] += 1
        o = outcome(r)
        if o in ("pending", "void", "unscorable"):
            m[o] += 1
            continue
        m[result_for_pick(r["market"], side, o)] += 1
        m["vectors"].append((r["p_a"], r["p_mid"], r["p_b"]))
        m["realised"].append({"a": 0, "mid": 1, "b": 2}[o])
    for m in by_market.values():
        vecs, real = m.pop("vectors"), m.pop("realised")
        decided = m["win"] + m["loss"]
        m["settled"] = len(real)
        m["pick_accuracy"] = m["win"] / decided if decided else None
        m["brier"] = metrics.brier_multi(vecs, real)
        m["log_loss"] = metrics.log_loss_multi(vecs, real)
    return {"policy": policy, "min_confidence": min_confidence, "markets": by_market,
            "note": "Descriptive. Market-line forecasts are compared with the bookmaker only at the same line; "
                    "model-only lines have no bookmaker comparison."}
