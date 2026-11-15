"""Coverage report over stored data. Reports what exists; never fills gaps. Also the every-game coverage ledger
(game_forecasts): one recorded source or a recorded reason for every scheduled game and market (docs/COVERAGE.md)."""
import sqlite3
from datetime import datetime
from typing import Optional

from .ingest import POSSESSION_LATEST_SQL as POSSESSION_LATEST


def report(conn: sqlite3.Connection) -> dict:
    seasons = [dict(r) for r in conn.execute(f"""
        WITH complete_possession AS (
            SELECT game_id FROM ({POSSESSION_LATEST}) p
            WHERE p.fga IS NOT NULL AND p.fta IS NOT NULL AND p.oreb IS NOT NULL AND p.tov IS NOT NULL
            GROUP BY game_id HAVING COUNT(*) = 2)
        SELECT g.season,
               SUM(g.season_type = 'regular')                        AS regular,
               SUM(g.season_type = 'play_in')                        AS play_in,
               SUM(g.season_type = 'postseason')                     AS postseason,
               SUM(g.season_type = 'preseason')                      AS preseason,
               SUM(g.season_type = 'other')                          AS other,
               SUM(g.status = 'final')                               AS final,
               SUM(g.status = 'scheduled')                           AS scheduled,
               SUM(g.status IN ('postponed', 'canceled'))            AS postponed_or_canceled,
               SUM(g.status = 'final' AND (SELECT COUNT(*) FROM team_game_stats s
                   WHERE s.game_id = g.game_id AND s.fga IS NOT NULL) = 2) AS final_with_box_stats,
               SUM(g.result_availability_basis = 'assumed_tipoff_plus_4h') AS results_assumed_availability,
               SUM(g.result_availability_basis = 'observed_retrieval')     AS results_observed_availability,
               SUM(EXISTS (SELECT 1 FROM odds_quotes o WHERE o.game_id = g.game_id)) AS games_with_odds,
               SUM(g.status = 'final' AND g.season_type IN ('regular', 'play_in', 'postseason')) AS final_competitive,
               SUM(g.status = 'final' AND g.season_type IN ('regular', 'play_in', 'postseason')
                   AND g.game_id IN (SELECT game_id FROM complete_possession))
                                                                     AS final_competitive_with_possession_stats,
               MIN(substr(g.tipoff_at, 1, 10)) AS first_date,
               MAX(substr(g.tipoff_at, 1, 10)) AS last_date
        FROM games g GROUP BY g.season ORDER BY g.season""")]
    totals = {
        "games": conn.execute("SELECT COUNT(*) FROM games").fetchone()[0],
        "teams": conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
        "team_game_stats": conn.execute("SELECT COUNT(*) FROM team_game_stats").fetchone()[0],
        "odds_quotes": conn.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0],
        "team_possession_observations": conn.execute("SELECT COUNT(*) FROM team_possession_observations").fetchone()[0],
        "raw_responses": conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0],
        "raw_fetches": conn.execute("SELECT COUNT(*) FROM raw_fetches").fetchone()[0],
        "predictions": conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
    }
    from . import injuries
    return {"seasons": seasons, "totals": totals, "closing_lines": closing_summary(conn),
            "injury_reports": injuries.summary(conn)}


NEAR_CLOSE_MINUTES = 60

# Per started game and provider: the last pregame 'current' moneyline confirmed (fetched) at or before tipoff.
# Uses raw_fetches, so an unchanged response re-fetched later still counts as a later confirmation of the price.
LAST_PREGAME_SQL = """
    SELECT game_id, tipoff_at, provider, home_american, away_american, home_prob_devig, confirmed_at FROM (
        SELECT g.game_id, g.tipoff_at, q.provider, q.home_american, q.away_american, q.home_prob_devig,
               f.retrieved_at AS confirmed_at,
               ROW_NUMBER() OVER (PARTITION BY g.game_id, q.provider
                                  ORDER BY f.retrieved_at DESC, q.quote_id DESC) AS rn
        FROM games g
        JOIN odds_quotes q ON q.game_id = g.game_id AND q.price_label = 'current'
        JOIN raw_fetches f ON f.raw_id = q.raw_id AND f.retrieved_at <= g.tipoff_at
        WHERE g.status IN ('final', 'in_progress') AND g.season_type IN ('regular', 'play_in', 'postseason'))
    WHERE rn = 1"""


def closing_summary(conn: sqlite3.Connection) -> dict:
    """Last pregame snapshot per started game. A snapshot taken long before tipoff is not a closing line, so the
    gap is reported and only snapshots within NEAR_CLOSE_MINUTES count as near-close."""
    started = conn.execute("SELECT COUNT(*) FROM games WHERE status IN ('final', 'in_progress') "
                           "AND season_type IN ('regular', 'play_in', 'postseason')").fetchone()[0]
    gaps = {}
    for r in conn.execute(LAST_PREGAME_SQL):
        mins = (datetime.fromisoformat(r["tipoff_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(r["confirmed_at"].replace("Z", "+00:00"))).total_seconds() / 60
        gaps[r["game_id"]] = min(mins, gaps.get(r["game_id"], mins))
    ordered = sorted(gaps.values())
    return {
        "started_competitive_games": started,
        "games_with_pregame_quote": len(gaps),
        f"games_with_quote_within_{NEAR_CLOSE_MINUTES}_min": sum(g <= NEAR_CLOSE_MINUTES for g in ordered),
        "median_minutes_before_tipoff": round(ordered[len(ordered) // 2], 1) if ordered else None,
        "note": "Last pregame snapshot (confirmed at or before tipoff), not a bookmaker-published closing line.",
    }


# --- Every-game forecasts (game_forecasts, docs/COVERAGE.md) ----------------------------------------------------------
# Every scheduled game of these season types gets a row per market in each two-hour cycle: a probability from the first
# source that can give one (ORDER), or a 'missing' row with the reasons. Exhibitions ('other': All-Star events) are not a
# forecastable game type and are reported as such. Training never reads this ledger; preseason rows are reported apart.
FORECAST_TYPES = ("preseason", "regular", "play_in", "postseason")


def forecast_window(conn, now: str) -> Optional[str]:
    """Why the two-hour cycle works now (jobs._forecast_due): 'upcoming' when a forecastable game starts within
    near_tipoff.WINDOW (refresh, then forecast), 'recent' when a game started within near_tipoff.RECENT_TIPOFF and is not
    final (results refresh only), else None (the cycle makes no request)."""
    from . import near_tipoff
    end = near_tipoff._fmt(near_tipoff._parse(now) + near_tipoff.WINDOW)
    if conn.execute(f"SELECT 1 FROM games WHERE status='scheduled' AND tipoff_at>? AND tipoff_at<=? "
                    f"AND season_type IN ({','.join('?' for _ in FORECAST_TYPES)}) LIMIT 1", (now, end, *FORECAST_TYPES)).fetchone():
        return "upcoming"
    if conn.execute("SELECT 1 FROM games WHERE tipoff_at<=? AND tipoff_at>? AND status IN ('scheduled','in_progress') LIMIT 1",
                    (now, near_tipoff._fmt(near_tipoff._parse(now) - near_tipoff.RECENT_TIPOFF))).fetchone():
        return "recent"
    return None
ORDER_VERSION = "coverage-v2"        # v2 (2026-10-14): an adaptive source needs an approval, and none exists
ORDER = ("adaptive", "primary_recorded", "primary_direct")   # approved adaptive ensemble, then the sealed primary model
LINE_SOURCE = {"market": "bookmaker", "bookmaker": "bookmaker", "model": "model", "none": "none", None: "none"}


def _confidence(market: str, p: tuple) -> float:
    """The probability of the side the forecast favours: three-way markets over all three outcomes; two-way markets
    conditional on no push (the push is never a pick)."""
    if market == "reg3":
        return round(max(p), 9)
    a, b = p[0], p[2]
    return round(max(a, b) / (a + b), 9) if a + b > 0 else 0.5


def _key(*parts) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _adaptive_rejected(row, schedule: Optional[str]) -> Optional[str]:
    """Why this adaptive forecast row may not be a game's canonical forecast (None = approved). Product decision
    (2026-10-14): no adaptive definition is approved for canonical use. A forecast row, a valid fit or a successful retrain
    is not an approval; adaptive rows stay research. A future approval would name the exact definition and operating
    policy (sport, market, season type, training cadence, fallback policy); no approval format exists yet."""
    return f"definition {row['definition']} ({schedule or 'unknown'} schedule) forecast, not approved for canonical use " \
           "(no adaptive definition is approved; kept as research)"


def _adaptive(conn, g, market: str, now: str):
    """The latest approved adaptive row of this game and market made in its two-hour window up to now (any definition: the
    one in force when it was made). (row, None) with a probability, or (None, why not): an unapproved forecast row is
    named with the reason it is not used."""
    if g["season_type"] == "preseason":
        return None, "adaptive: preseason is outside the ensemble's scope (its members train on competitive games only)"
    if not _has_table(conn, "adaptive_forecasts"):
        return None, "adaptive: schema not migrated"
    rows = conn.execute("SELECT * FROM adaptive_forecasts WHERE game_id=? AND market=? AND tipoff_at=? AND generated_at<=? "
                        "AND generated_at<tipoff_at ORDER BY generated_at DESC, forecast_id DESC",
                        (g["game_id"], market, g["tipoff_at"], now)).fetchall()
    if not rows:
        return None, "adaptive: no adaptive forecast recorded for this game and market"
    # Several definitions record in one cycle (weekly, and daily when daily agreement recording is on). The ensemble used is
    # chosen by schedule, never by which row was written last: the newest forecast of a weekly-schedule definition, then of
    # a daily one, then of any other; its definition is in the row's model version.
    rank = {"weekly": 0, "daily": 1}
    schedule = _schedules(conn)
    forecasts = [(rank.get(schedule.get(r["definition"]), 2), i, r) for i, r in enumerate(rows)
                 if r["status"] == "forecast" and _adaptive_rejected(r, schedule.get(r["definition"])) is None]
    if forecasts:
        return min(forecasts, key=lambda x: (x[0], x[1]))[2], None
    latest: dict = {}
    for r in rows:                     # newest first: each definition's latest forecast (unapproved) or reason
        if r["definition"] not in latest:
            latest[r["definition"]] = (f"adaptive: {_adaptive_rejected(r, schedule.get(r['definition']))}" if r["status"] == "forecast"
                                       else f"adaptive ({schedule.get(r['definition'], 'unknown schedule')} {r['definition'][:8]}): {r['reason']}")
    return None, "; ".join(latest.values())


def _schedules(conn) -> dict:
    """definition -> training schedule ('weekly' / 'daily') from its fit records."""
    if not _has_table(conn, "adaptive_fits"):
        return {}
    return {r[0]: r[1] for r in conn.execute("SELECT definition, MIN(schedule) FROM adaptive_fits GROUP BY definition")}


def _primary_recorded(conn, g, market: str, now: str):
    """The sealed primary model's two-hour forecast as recorded by the primary writers (near-tipoff Elo for the winner,
    the sealed market model for the other markets), latest made up to now."""
    if market == "winner":
        r = conn.execute("SELECT * FROM predictions WHERE game_id=? AND run_type='live' AND cutoff_at=? AND generated_at<=? "
                         "AND generated_at<cutoff_at AND model_version LIKE '%-policy=near_tipoff' AND model_version NOT LIKE "
                         "'research-%' ORDER BY generated_at DESC, prediction_id DESC LIMIT 1",
                         (g["game_id"], g["tipoff_at"], now)).fetchone()
        if r is None:
            return None, "primary: no recorded two-hour winner forecast"
        p = r["home_win_prob"]
        return {"table": "predictions", "id": r["prediction_id"], "model_version": r["model_version"], "generated_at": r["generated_at"],
                "p": (p, 0.0, round(1 - p, 9)), "line": None, "line_source": "none", "quote": None}, None
    r = conn.execute("SELECT * FROM market_forecasts WHERE game_id=? AND market=? AND policy='near_tipoff' AND run_type='live' "
                     "AND tipoff_at=? AND generated_at<=? AND generated_at<cutoff_at AND model_version NOT LIKE 'research-%' "
                     "ORDER BY generated_at DESC, forecast_id DESC LIMIT 1", (g["game_id"], market, g["tipoff_at"], now)).fetchone()
    if r is None:
        return None, "primary: no recorded two-hour market forecast"
    return {"table": "market_forecasts", "id": r["forecast_id"], "model_version": r["model_version"], "generated_at": r["generated_at"],
            "p": (r["p_a"], r["p_mid"], r["p_b"]), "line": r["line"], "line_source": LINE_SOURCE[r["line_source"]],
            "quote": r["market_quote_id"]}, None


class _Direct:
    """The sealed primary models computed here, for games the primary writers do not cover (preseason) or have not
    recorded, with the primary writers' guards: no forecast from stale results or with an earlier game of either team
    still unfinished (near_tipoff). Built once per cycle from results available now (training season types only)."""

    def __init__(self, conn, now: str):
        from . import live, market_live, near_tipoff
        self.conn, self.now = conn, now
        self.eng = live.engine(conn)
        rows = live.available_rows(conn, now)
        ordered = sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
        _, self.elo = live.primary_model(conn)
        for r in ordered:
            self.elo.update(r.result)
        self.winner_version = near_tipoff.model_version(self.elo)
        try:
            self.market_model, base = market_live.market_model(conn)
            for r in ordered:
                self.market_model.update(r.result)
            self.market_version, self.market_error = market_live.model_version(base, "near_tipoff"), None
        except Exception as exc:      # MarketModelUnavailable or a broken seal: the line markets cannot use this source
            self.market_model, self.market_version = None, None
            self.market_error = f"{type(exc).__name__}: {exc}"
        refreshed = near_tipoff.results_refreshed_at(conn, now)
        self.stale = (None if refreshed is not None and near_tipoff._parse(now) - near_tipoff._parse(refreshed) <= near_tipoff.MAX_DATA_AGE
                      else f"stale results: no successful schedule and results refresh in the last "
                           f"{int(near_tipoff.MAX_DATA_AGE.total_seconds() // 60)} minutes (last: {refreshed})")
        self._unfinished = near_tipoff._unfinished_earlier_games

    def guard(self, g) -> Optional[str]:
        if self.stale:
            return self.stale
        missing = sorted({gid for team in (g["home_team_id"], g["away_team_id"]) for gid in self._unfinished(self.conn, team, self.now)})
        return f"missing result: earlier games of these teams have no final result yet: {', '.join(missing)}" if missing else None

    def forecast(self, g, market: str):
        import copy
        from . import market_live, markets
        reason = self.guard(g)
        if reason:
            return None, f"primary: {reason}"
        game = self.eng.pregame(g)
        if market == "winner":
            p = round(copy.deepcopy(self.elo).predict(game), 9)
            return {"table": None, "id": None, "model_version": self.winner_version, "generated_at": None,
                    "p": (p, 0.0, round(1 - p, 9)), "line": None, "line_source": "none", "quote": None}, None
        if self.market_model is None:
            return None, f"primary: no sealed market model: {self.market_error}"
        dist = copy.deepcopy(self.market_model).distribution(game)
        line = quote = None
        if market != "reg3":
            quote = market_live.latest_quote(self.conn, g["game_id"], market, self.now)
            line = quote["line"] if quote else markets.model_line(dist, market)
        pa, pm, _ = (round(x, 9) for x in dist.market(market, line))
        return {"table": None, "id": None, "model_version": self.market_version, "generated_at": None,
                "p": (pa, pm, round(1 - pa - pm, 9)), "line": line,
                "line_source": "none" if line is None else ("bookmaker" if quote else "model"),
                "quote": quote["quote_id"] if quote else None}, None


def _content_key(row) -> str:
    """What a cycle decided for a game and market (source, probability, line, reasons), without its time. Works on a new
    row (dict) and on a stored one (sqlite3.Row), so the latest stored decision can be compared with this cycle's."""
    get = row.get if isinstance(row, dict) else (lambda k: row[k])
    p = [round(get(k), 9) for k in ("p_a", "p_mid", "p_b")] if get("status") == "forecast" else None
    return _key(get("order_version"), get("game_id"), get("market"), get("tipoff_at"), get("status"), get("source"),
                get("source_table"), get("source_id"), get("model_version"), get("line"), get("line_source"), p,
                get("fallback_reason"), get("reason"))


def _insert(conn, row: dict) -> tuple[int, bool]:
    """A retry of the latest decision for this game and market returns that row. A different decision is a new row; one
    that returns to an earlier decision (A -> B -> A) gets its own identity (a revision), so the latest row is always the
    latest decision (as market_live does for line changes)."""
    from .db import transaction
    cols = ("game_id", "sport", "season_type", "market", "order_version", "status", "source", "fallback_rank", "fallback_reason",
            "reason", "model_version", "source_table", "source_id", "source_generated_at", "line", "line_source", "market_quote_id",
            "p_a", "p_mid", "p_b", "confidence", "generated_at", "tipoff_at", "idempotency_key")
    content = _content_key(row)
    with transaction(conn):
        prev = conn.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market=? ORDER BY generated_at DESC, forecast_id DESC "
                            "LIMIT 1", (row["game_id"], row["market"])).fetchone()
        if prev is not None and _content_key(prev) == content:
            return prev["forecast_id"], False
        row["idempotency_key"] = content if prev is None else _key(content, row["generated_at"])
        old = conn.execute("SELECT forecast_id FROM game_forecasts WHERE idempotency_key=?", (row["idempotency_key"],)).fetchone()
        if old:
            return old["forecast_id"], False
        cur = conn.execute(f"INSERT INTO game_forecasts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [row.get(c) for c in cols])
        return cur.lastrowid, True


def record(conn: sqlite3.Connection, *, now: Optional[str] = None, game_ids: Optional[list] = None, write: bool = True) -> dict:
    """One game_forecasts row per market for every scheduled game of a forecastable type starting within two hours of
    `now`. Sources in ORDER; each failure is isolated to its game and market and recorded as a 'missing' row with the
    reason (never a substitute probability). A retry with unchanged inputs returns the stored row. Never raises."""
    from datetime import timedelta
    from . import live, sports
    from .db import utc_now
    fixed = now is not None
    now = now or utc_now()
    out = {"status": "success", "order_version": ORDER_VERSION, "as_of": now, "games": 0, "created": 0, "already_recorded": 0,
           "forecast": 0, "missing": 0, "by_source": {}, "errors": {}, "rows": []}
    try:
        if not _has_table(conn, "game_forecasts"):
            return {**out, "status": "skipped", "reason": "store not migrated to 0029 (game_forecasts missing)"}
        sport = live.engine(conn).sport
        end = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        games = [g for g in conn.execute(
            f"SELECT * FROM games WHERE status='scheduled' AND tipoff_at>? AND tipoff_at<=? AND season_type IN "
            f"({','.join('?' * len(FORECAST_TYPES))}) ORDER BY tipoff_at, game_id", (now, end, *FORECAST_TYPES))
            if game_ids is None or g["game_id"] in game_ids]
    except Exception as exc:
        return {**out, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    out["games"] = len(games)
    direct, direct_error = None, None
    for g in games:
        for market in sports.get(sport).markets:
            try:
                reasons, src, rank = [], None, None
                a, why = _adaptive(conn, g, market, now)
                if a is not None:
                    src, rank = {"table": "adaptive_forecasts", "id": a["forecast_id"], "model_version": a["model_version"],
                                 "generated_at": a["generated_at"], "p": (a["p_a"], a["p_mid"], a["p_b"]), "line": a["line"],
                                 "line_source": LINE_SOURCE[a["line_source"]], "quote": a["market_quote_id"]}, 1
                else:
                    reasons.append(why)
                    src, why = _primary_recorded(conn, g, market, now)
                    if src is not None:
                        rank = 2
                    else:
                        reasons.append(why)
                        if direct is None and direct_error is None:
                            try:
                                direct = _Direct(conn, now)
                            except Exception as exc:
                                direct_error = f"primary: sealed model unavailable: {type(exc).__name__}: {exc}"
                        src, why = (None, direct_error) if direct is None else direct.forecast(g, market)
                        if src is not None:
                            rank = 3
                        else:
                            reasons.append(why)
                generated = now if fixed else utc_now()
                if generated >= g["tipoff_at"]:
                    out["errors"][f"{g['game_id']}:{market}"] = "calculation finished at or after the start: nothing recorded"
                    continue
                row = {"game_id": g["game_id"], "sport": sport, "season_type": g["season_type"], "market": market,
                       "order_version": ORDER_VERSION, "generated_at": generated, "tipoff_at": g["tipoff_at"]}
                if src is None:
                    row.update(status="missing", source="none", reason="; ".join(reasons), line_source="none")
                else:
                    p = tuple(float(x) for x in src["p"])
                    row.update(status="forecast", source=ORDER[rank - 1], fallback_rank=rank,
                               fallback_reason="; ".join(reasons) or None, model_version=src["model_version"],
                               source_table=src["table"], source_id=src["id"], source_generated_at=src["generated_at"],
                               line=src["line"], line_source=src["line_source"], market_quote_id=src["quote"],
                               p_a=p[0], p_mid=p[1], p_b=p[2], confidence=_confidence(market, p))
                if write:
                    fid, new = _insert(conn, row)
                    row["forecast_id"] = fid
                    out["created" if new else "already_recorded"] += 1
                out[row["status"]] += 1
                out["by_source"][row["source"]] = out["by_source"].get(row["source"], 0) + 1
                out["rows"].append({k: row.get(k) for k in ("game_id", "market", "status", "source", "fallback_reason", "reason",
                                                            "line", "line_source", "p_a", "p_mid", "p_b", "confidence")})
            except Exception as exc:          # this game and market only; the others are still recorded
                err = f"{type(exc).__name__}: {exc}"
                out["errors"][f"{g['game_id']}:{market}"] = err
                if write:                     # the failure itself is on record as a coverage failure, never a forecast
                    try:
                        generated = now if fixed else utc_now()
                        if generated < g["tipoff_at"]:
                            reason = f"error: {err}"
                            _insert(conn, {"game_id": g["game_id"], "sport": sport, "season_type": g["season_type"],
                                           "market": market, "order_version": ORDER_VERSION, "status": "missing", "source": "none",
                                           "reason": reason, "line_source": "none", "generated_at": generated,
                                           "tipoff_at": g["tipoff_at"]})
                    except Exception as exc2:
                        out["errors"][f"{g['game_id']}:{market}"] += f"; the failure could not be recorded: {type(exc2).__name__}: {exc2}"
    if out["errors"]:
        out["status"] = "partial"
    return out


# --- Grading and the coverage report (computed on read: re-reading never grades twice) ------------------------------
MAX_REPORT_DAYS = 31
UNRESOLVED_AFTER_HOURS = 48


def et_date(ts: str) -> str:
    from zoneinfo import ZoneInfo
    from datetime import timezone
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def et_bounds(date_from: str, date_to: str) -> tuple[str, str]:
    """[00:00 ET of date_from, 00:00 ET of the day after date_to) as UTC times (games belong to their Eastern date)."""
    from datetime import timedelta, timezone
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    a = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=et)
    b = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=et) + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return a.astimezone(timezone.utc).strftime(fmt), b.astimezone(timezone.utc).strftime(fmt)          # a game not final this long after its start (or status 'other') is unresolved
COMPETITIVE = ("regular", "play_in", "postseason")


def _hours_since(start: str, as_of: str) -> float:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(as_of, fmt) - datetime.strptime(start, fmt)).total_seconds() / 3600


def pick_side(market: str, p) -> str:
    """The side a forecast picks: 'a' / 'mid' / 'b'. Three-way markets: the most likely of the three outcomes (a draw can be
    the pick; product decision 2026-10-14), ties to the first in the order a, mid, b. Two-way markets: 'a' when p_a >= p_b
    (the push is never a pick)."""
    if market == "reg3":
        return ("a", "mid", "b")[max(range(3), key=lambda i: p[i])]
    return "a" if p[0] >= p[2] else "b"


def grade(market: str, line, p, game, as_of: str, forecast_tipoff_at: Optional[str] = None, decision: Optional[str] = None) -> str:
    """'win' / 'loss' / 'push' for a graded forecast, else 'pending', 'void' (postponed, canceled, or the start moved after
    the forecast), 'unscorable' (no score, no regulation decision, no line) or 'unresolved' (no final result
    UNRESOLVED_AFTER_HOURS after the start, or a status the source never resolves, e.g. suspended → 'other'). The pick is
    the favoured side; confidence plays no part (thresholds only filter views of these grades)."""
    from . import markets
    state = markets.settle(market, line, status=game["status"], home=game["home_score"], away=game["away_score"],
                           decision=game["decision"] if "decision" in game.keys() else decision,
                           tipoff_at=game["tipoff_at"], forecast_tipoff_at=forecast_tipoff_at)
    if state == "pending":
        return "unresolved" if game["status"] == "other" or _hours_since(game["tipoff_at"], as_of) > UNRESOLVED_AFTER_HOURS else "pending"
    if state in ("void", "unscorable"):
        return state
    if state == "mid" and market != "reg3":
        return "push"
    return "win" if pick_side(market, p) == state else "loss"


# --- The canonical forecast: one record per game and market, read by Today, Game, History and Performance ---------------
CANONICAL_STATES = ("forecast", "missing", "upcoming", "unvalidated", "not_recorded", "not_forecastable")
EXPERIMENTAL_LABEL = "Experimental / unvalidated"     # preseason rows: no model is validated on preseason games
PRESEASON_UNVALIDATED = ("Preseason: no forecast model has been validated on preseason games (the sealed models were "
                         "validated on regular-season and playoff games only), so there is no canonical forecast.")


def recording_began(conn, sport: str) -> Optional[str]:
    if not _has_table(conn, "game_forecasts"):
        return None
    return conn.execute("SELECT MIN(generated_at) FROM game_forecasts WHERE sport = ?", (sport,)).fetchone()[0]


def _minutes(start: str, at: str) -> float:
    return round(_hours_since(at, start) * 60, 1)


def canonical(conn, games, market: str, as_of: str, began: Optional[str] = None) -> dict:
    """game_id -> the canonical forecast of `market` as of `as_of`. Selection: the latest 'forecast' row made before the
    start for the game's current start (else for an earlier start, which grades void); a later 'missing' row never replaces
    it and is reported as `later_failure`. Without a forecast row: the latest 'missing' row's reason, else why nothing was
    recorded. Preseason is never a canonical forecast (no validated model); its experimental rows stay in the ledger.
    Legacy ledgers (pre-game archive, earlier two-hour rows) are never read here. Graded on read by `grade`."""
    from .db import store_sport
    sport = store_sport(conn) or "nba"
    if began is None:
        began = recording_began(conn, sport)
    games = list(games)
    rows = _canonical_rows(conn, [g["game_id"] for g in games], market, as_of)
    decisions = _decisions(conn, [g["game_id"] for g in games]) if market == "reg3" else {}
    revisions = _revisions(conn, [g["game_id"] for g in games])
    return {g["game_id"]: _canonical_one(g, rows.get(g["game_id"], []), market, as_of, began, decisions.get(g["game_id"]),
                                         revisions.get(g["game_id"], [])) for g in games}


def _revisions(conn, ids: list) -> dict:
    """game_id -> its result revisions (result_revisions, migration 0030), oldest first; {} before that migration."""
    out: dict = {}
    if not _has_table(conn, "result_revisions"):
        return out
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"SELECT * FROM result_revisions WHERE game_id IN ({','.join('?' * len(chunk))}) "
                              f"ORDER BY game_id, revision", chunk):
            out.setdefault(r["game_id"], []).append(r)
    return out


def grade_history(market: str, line, p, g, as_of: str, forecast_tipoff_at: str, revisions: list) -> list:
    """The forecast's grade under each final result of its game recorded by `as_of`, oldest first (graded on read, never
    stored), so a regrade is traceable and the earlier grade is never erased. `change`: 'first'; 'decision_known' (same
    score, the NHL regulation decision was unknown before: completed, not corrected); 'correction' (score or decision)."""
    out, prev = [], None
    for r in revisions:
        if r["observed_at"] > as_of:
            break
        change = ("first" if prev is None else
                  "decision_known" if prev["decision"] is None and r["decision"] is not None
                  and (prev["home_score"], prev["away_score"]) == (r["home_score"], r["away_score"]) else "correction")
        prev = r
        result = {"status": "final", "home_score": r["home_score"], "away_score": r["away_score"], "tipoff_at": g["tipoff_at"],
                  "decision": r["decision"]}
        out.append({"revision": r["revision"], "observed_at": r["observed_at"], "basis": r["basis"], "change": change,
                    "home_score": r["home_score"], "away_score": r["away_score"], "decision": r["decision"],
                    "grade": grade(market, line, p, result, as_of, forecast_tipoff_at)})
    return out


def _regraded(history: list) -> bool:
    """A correction changed the grade: a 'correction' entry whose grade differs from the entry before it."""
    return any(h["change"] == "correction" and h["grade"] != before["grade"] for before, h in zip(history, history[1:]))


def _decisions(conn, ids: list) -> dict:
    """game_id -> how the game was decided ('REG' / 'OT' / 'SO'), for games rows read without it (NHL three-way grading)."""
    out: dict = {}
    if not _has_table(conn, "game_decisions"):
        return out
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        out.update(conn.execute(f"SELECT game_id, decision FROM game_decisions WHERE game_id IN ({','.join('?' * len(chunk))})",
                                chunk).fetchall())
    return out


def _canonical_rows(conn, ids: list, market: str, as_of: str) -> dict:
    """game_id -> its game_forecasts rows of `market` made before their start and by `as_of`, oldest first."""
    rows: dict = {}
    if not _has_table(conn, "game_forecasts"):
        return rows
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"SELECT * FROM game_forecasts WHERE market = ? AND generated_at <= ? AND generated_at < tipoff_at "
                              f"AND game_id IN ({','.join('?' * len(chunk))}) ORDER BY generated_at, forecast_id",
                              (market, as_of, *chunk)):
            rows.setdefault(r["game_id"], []).append(r)
    return rows


def _select(g, rows: list):
    """(selected forecast row or None, the latest 'missing' row recorded after it or None): the latest forecast made for
    the game's current start, else for an earlier start; a later missing attempt never replaces it."""
    forecasts = [r for r in rows if r["status"] == "forecast"]
    current = [r for r in forecasts if r["tipoff_at"] == g["tipoff_at"]]
    sel = (current or forecasts)[-1] if forecasts else None
    if sel is None:
        return None, None
    later = [r for r in rows if r["status"] == "missing" and (r["generated_at"], r["forecast_id"]) > (sel["generated_at"], sel["forecast_id"])]
    return sel, (later[-1] if later else None)


def _canonical_one(g, rows: list, market: str, as_of: str, began: Optional[str], decision: Optional[str] = None,
                   revisions: Optional[list] = None) -> dict:
    out = {"market": market, "state": None, "reason": None, "record": None, "grade": None, "result_side": None,
           "grade_history": [], "regraded": False, "later_failure": None, "attempts": len(rows), "recording_began_at": began}
    st = g["season_type"]
    if st not in FORECAST_TYPES:
        return {**out, "state": "not_forecastable", "reason": f"Not a forecastable game type ({st})."}
    if st == "preseason":
        return {**out, "state": "unvalidated", "reason": PRESEASON_UNVALIDATED}
    sel, last_failure = _select(g, rows)
    if sel is not None:
        p = (sel["p_a"], sel["p_mid"], sel["p_b"])
        later = [last_failure] if last_failure is not None else []
        rec = {"forecast_id": sel["forecast_id"], "source": sel["source"], "fallback_rank": sel["fallback_rank"],
               "fallback_reason": sel["fallback_reason"], "model_version": sel["model_version"], "generated_at": sel["generated_at"],
               "start_at": sel["tipoff_at"], "minutes_before_start": _minutes(sel["tipoff_at"], sel["generated_at"]),
               "p": list(p), "side": pick_side(market, p), "confidence": sel["confidence"], "line": sel["line"],
               "line_source": sel["line_source"], "market_quote_id": sel["market_quote_id"]}
        if market == "winner":
            rec.update(home_win_prob=p[0], pick="home" if p[0] >= p[2] else "away")
        from . import markets
        settled = markets.settle(market, sel["line"], status=g["status"], home=g["home_score"], away=g["away_score"],
                                 decision=g["decision"] if "decision" in g.keys() else decision, tipoff_at=g["tipoff_at"],
                                 forecast_tipoff_at=sel["tipoff_at"])
        history = grade_history(market, sel["line"], p, g, as_of, sel["tipoff_at"], revisions or []) if g["status"] == "final" else []
        return {**out, "state": "forecast", "record": rec, "result_side": settled if settled in ("a", "mid", "b") else None,
                "grade_history": history, "regraded": _regraded(history),
                "grade": grade(market, sel["line"], p, g, as_of, sel["tipoff_at"], decision),
                "later_failure": None if not later else {"forecast_id": later[-1]["forecast_id"],
                                                         "generated_at": later[-1]["generated_at"], "reason": later[-1]["reason"]}}
    if rows:                             # attempts were recorded, none could forecast
        return {**out, "state": "missing", "reason": rows[-1]["reason"]}
    if began is None or g["tipoff_at"] <= began:
        return {**out, "state": "not_recorded",
                "reason": "Before canonical recording began" + (f" ({began})" if began else "") +
                          ": any forecast of this game is in the legacy views (Within 2 hours, Pre-game archive)."}
    if g["status"] == "scheduled" and g["tipoff_at"] > as_of:
        return {**out, "state": "upcoming", "reason": "Canonical forecasts are recorded within two hours of the start."}
    if g["status"] in ("postponed", "canceled"):
        return {**out, "state": "missing", "reason": f"not recorded: game {g['status']} before its two-hour window"}
    return {**out, "state": "missing", "reason": "not recorded: no forecast cycle ran within two hours of the start"}


def threshold_grid(market: str) -> tuple:
    """The reporting thresholds of a market (agreement_tracking.REPORT_THRESHOLDS): 50-90% for two-way markets, 35-70% for
    the three-way regulation market. A forecast is in a threshold's row when its confidence is at least the threshold."""
    from .agreement_tracking import REPORT_THRESHOLDS, kind_of
    return REPORT_THRESHOLDS[kind_of(market)]


def scored(market: str, c: dict):
    """(probability vector or side-a probability, realised outcome) for a canonical forecast that is scored: graded
    win/loss (a push, void, pending, unscorable or unresolved forecast is never scored). Winner: P(home), home won.
    Two-way line markets: P(a | no push), side a realised. Three-way: (p_a, p_mid, p_b), index of the realised outcome."""
    if c["state"] != "forecast" or c["grade"] not in ("win", "loss") or c["result_side"] is None:
        return None
    p = c["record"]["p"]
    if market == "reg3":
        return (p[0], p[1], p[2]), ("a", "mid", "b").index(c["result_side"])
    if market == "winner":
        return p[0], c["result_side"] == "a"
    return p[0] / (p[0] + p[2]), c["result_side"] == "a"


def canonical_summary(items: list, market: Optional[str] = None) -> dict:
    """Counts of canonical forecasts of one market for started competitive games. Denominators: `games` = forecast + missing
    (a missing forecast is a coverage gap, never a loss, never dropped); `graded` = wins + losses; accuracy = wins / graded;
    pushes, voids, pending, unscorable and unresolved forecasts are counted apart and are in no rate. Brier score and log
    loss cover the graded forecasts (three-way: the multi-outcome forms). `thresholds`: the same counts for forecasts whose
    confidence is at least each threshold of the market's grid; thresholds never remove a forecast from `games`."""
    from . import metrics
    if market is None:
        market = items[0][1]["market"] if items else "winner"
    states = {k: 0 for k in ("forecast", "missing")}
    grades = {k: 0 for k in ("win", "loss", "push", "pending", "void", "unscorable", "unresolved")}
    by_source: dict = {}
    versions: dict = {}
    reasons: dict = {}
    probs, ys = [], []
    regraded = 0
    grid = threshold_grid(market)
    by_threshold = {t: _blank() for t in grid}
    for g, c in items:
        if c["state"] not in states:
            continue
        states[c["state"]] += 1
        if c["state"] == "missing":
            reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
            continue
        rec = c["record"]
        grades[c["grade"]] += 1
        regraded += bool(c.get("regraded"))
        by_source[rec["source"]] = by_source.get(rec["source"], 0) + 1
        versions[rec["model_version"]] = versions.get(rec["model_version"], 0) + 1
        for t in grid:
            if rec["confidence"] >= t - 1e-12:
                _add(by_threshold[t], c["grade"])
        s = scored(market, c)
        if s is not None:
            probs.append(s[0])
            ys.append(s[1])
    games = states["forecast"] + states["missing"]
    graded = grades["win"] + grades["loss"]
    if market == "reg3":
        brier, log_loss = metrics.brier_multi(probs, ys), metrics.log_loss_multi(probs, ys)
    else:
        brier, log_loss = metrics.brier(probs, ys), metrics.log_loss(probs, ys)
    return {"games": games, "forecast": states["forecast"], "missing": states["missing"],
            "coverage": states["forecast"] / games if games else None, "grades": grades, "graded": graded,
            "accuracy": grades["win"] / graded if graded else None,
            "brier_score": brier, "log_loss": log_loss, "regraded": regraded,
            "by_source": by_source,
            "model_versions": [{"model_version": k, "forecasts": v} for k, v in sorted(versions.items(), key=lambda kv: -kv[1])],
            "missing_reasons": [{"reason": k, "games": v} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])],
            "thresholds": [{"threshold": t, **_finish_record(by_threshold[t]), "graded": by_threshold[t]["win"] + by_threshold[t]["loss"]}
                           for t in grid]}


def matched_quote(conn, market: str, g, c: dict):
    """(model, market, outcome) for one scored canonical forecast of a non-winner market compared with the bookmaker, or
    (None, why not). The quote is the one the forecast's source used (market_quote_id), accepted only when it is a quote of
    the same game and market at the forecast's exact line, retrieved no later than the forecast and before the start, and
    the forecast's line is the bookmaker's. Two-way: P(a | no push) against the de-vigged P(a); three-way: the
    three-outcome vectors."""
    s = scored(market, c)
    if s is None:
        return None, "not scored"
    q, why = forecast_quote(conn, market, g, c["record"])
    if q is None:
        return None, why
    return (s[0], q, s[1]), None


def forecast_quote(conn, market: str, g, rec: dict):
    """(the bookmaker's de-vigged P(a) — three-way: the vector — from the quote a non-winner forecast used, or None, why
    not), whether or not the game is settled. Same acceptance rules as matched_quote."""
    if market != "reg3" and rec["line_source"] != "bookmaker":
        return None, "model line (no bookmaker line at forecast time)"
    if rec.get("market_quote_id") is None:
        return None, ("no regulation three-way bookmaker quotes are collected" if market == "reg3"
                      else "no bookmaker quote recorded with the forecast")
    q = conn.execute("SELECT * FROM market_quotes WHERE quote_id=?", (rec["market_quote_id"],)).fetchone()
    if q is None or q["game_id"] != g["game_id"] or q["market"] != market:
        return None, "quote is not of this game and market"
    if (q["line"] is None) != (rec["line"] is None) or (q["line"] is not None and abs(q["line"] - rec["line"]) > 1e-9):
        return None, "quote line differs from the forecast line"
    if q["retrieved_at"] > rec["generated_at"] or q["retrieved_at"] >= g["tipoff_at"]:
        return None, "quote retrieved after the forecast or the start"
    if market == "reg3":
        if q["p_mid_devig"] is None:
            return None, "quote has no regulation-draw price"
        return (q["p_a_devig"], q["p_mid_devig"], q["p_b_devig"]), None
    return q["p_a_devig"], None


def _blank() -> dict:
    return {"forecasts": 0, "graded": 0, "win": 0, "loss": 0, "push": 0, "pending": 0, "void": 0, "unscorable": 0, "unresolved": 0}


def _add(rec: dict, state: str) -> None:
    rec["forecasts"] += 1
    rec[state] += 1
    if state in ("win", "loss", "push"):
        rec["graded"] += 1


def _finish_record(rec: dict) -> dict:
    decided = rec["win"] + rec["loss"]
    return {**rec, "win_rate": round(rec["win"] / decided, 4) if decided else None}


def _games(conn, start: str, end: str) -> list:
    has_dec = _has_table(conn, "game_decisions")
    dec = "d.decision" if has_dec else "NULL AS decision"
    join = "LEFT JOIN game_decisions d ON d.game_id = g.game_id" if has_dec else ""
    return conn.execute(f"SELECT g.*, {dec} FROM games g {join} WHERE g.tipoff_at >= ? AND g.tipoff_at < ? "
                        "ORDER BY g.tipoff_at, g.game_id", (start, end)).fetchall()


def source_schedule(conn, start: str, end: str) -> dict:
    """The source's own schedule for the window, re-parsed from the cached raw responses (latest retrieval of each
    request wins), compared with the stored games: what the source lists that the store lacks, and differences."""
    import json
    from . import live, raw_cache
    from .sources import espn, nhl_stats
    sport = live.engine(conn).sport
    src: dict = {}
    try:
        if sport == "nhl":
            first, last = int(start[:4]), int(end[:4])
            keys = [f"season={y}{y + 1}" for y in range(first - 1, last + 1)]
            source = nhl_stats.SOURCE
        else:
            from datetime import timedelta
            d0, d1 = datetime.strptime(start[:10], "%Y-%m-%d"), datetime.strptime(end[:10], "%Y-%m-%d")
            days = [(d0 + timedelta(days=i)) for i in range(-1, (d1 - d0).days + 2)]
            keys = sorted({f"dates={d:%Y%m}&limit=1000" for d in days} | {f"dates={d:%Y%m%d}" for d in days})
            source = espn.SOURCE
        fetched = conn.execute(
            f"SELECT r.raw_id, r.request_key, MAX(f.retrieved_at) AS at FROM raw_responses r JOIN raw_fetches f ON f.raw_id = r.raw_id "
            f"WHERE r.source = ? AND r.request_key IN ({','.join('?' * len(keys))}) GROUP BY r.raw_id ORDER BY at, r.raw_id",
            (source, *keys)).fetchall()
        for f in fetched:             # oldest first: a later retrieval overrides what an earlier one said
            body = json.loads(raw_cache.load(conn, f["raw_id"]))
            parsed = ([g for g, _dec, _per in nhl_stats.parse_season(body, {}, f["at"])] if sport == "nhl"
                      else espn.parse_scoreboard(body))
            for g in parsed:
                src[g.game_id] = {"tipoff_at": g.tipoff_at, "season_type": g.season_type, "status": g.status}
    except Exception as exc:
        return {"available": False, "reason": f"source schedule could not be read from the raw cache: {type(exc).__name__}: {exc}"}
    in_window = {k: v for k, v in src.items() if start <= v["tipoff_at"] < end}
    stored = {g["game_id"]: g for g in conn.execute("SELECT game_id, tipoff_at, season_type, status FROM games WHERE tipoff_at >= ? "
                                                    "AND tipoff_at < ?", (start, end))}
    missing = sorted(set(in_window) - set(stored))
    differ = [{"game_id": k, "source": v, "stored": {f: stored[k][f] for f in ("tipoff_at", "season_type", "status")}}
              for k, v in sorted(in_window.items()) if k in stored and any(v[f] != stored[k][f] for f in v)]
    return {"available": True, "requests": len(fetched), "source_games": len(in_window), "stored_games": len(stored),
            "missing_from_store": [{"game_id": k, **in_window[k]} for k in missing],
            "stored_not_in_source": sorted(set(stored) - set(in_window)), "field_differences": differ}


def _existing(conn, g, market: str):
    """For a game with no every-game row (before that ledger began): the pregame forecast the existing ledgers hold, in the
    same order — the adaptive ensemble, the two-hour primary, then the discontinued night-before primary. Nothing is
    reconstructed; a game none of them covered stays missing."""
    a, _ = _adaptive(conn, g, market, g["tipoff_at"])
    if a is not None:
        return {"source": "adaptive", "model_version": a["model_version"], "p": (a["p_a"], a["p_mid"], a["p_b"]), "line": a["line"],
                "line_source": LINE_SOURCE[a["line_source"]], "tipoff_at": a["tipoff_at"]}
    r, _ = _primary_recorded(conn, g, market, g["tipoff_at"])
    if r is not None:
        return {"source": "primary_recorded", "model_version": r["model_version"], "p": r["p"], "line": r["line"],
                "line_source": r["line_source"], "tipoff_at": g["tipoff_at"]}
    # The night-before primary: its latest row (as its History selects it), void by that History's rule when the game moved
    # since: the winner when the game left the Eastern date it was made for (live.day_changed), the other markets on any
    # start change (markets.settle via market_live.outcome).
    from . import backtest
    cutoff = backtest.cutoff_for(g["tipoff_at"])
    if market == "winner":
        n = conn.execute("SELECT * FROM predictions WHERE game_id=? AND run_type='live' AND generated_at<=cutoff_at "
                         "AND model_version NOT LIKE 'research-%' AND model_version NOT LIKE '%-policy=near_tipoff' "
                         "ORDER BY generated_at DESC, prediction_id DESC LIMIT 1", (g["game_id"],)).fetchone()
        if n is not None:
            p = n["home_win_prob"]
            return {"source": "primary_night_before", "model_version": n["model_version"], "p": (p, 0.0, 1 - p), "line": None,
                    "line_source": "none", "tipoff_at": None, "void": n["cutoff_at"] != cutoff}
        return None
    n = conn.execute("SELECT * FROM market_forecasts WHERE game_id=? AND market=? AND policy='pregame' AND run_type='live' "
                     "AND generated_at<=cutoff_at AND model_version NOT LIKE 'research-%' ORDER BY generated_at DESC, forecast_id DESC "
                     "LIMIT 1", (g["game_id"], market)).fetchone()
    if n is not None:
        return {"source": "primary_night_before", "model_version": n["model_version"], "p": (n["p_a"], n["p_mid"], n["p_b"]),
                "line": n["line"], "line_source": LINE_SOURCE[n["line_source"]], "tipoff_at": n["tipoff_at"]}
    return None


def _retrospective(conn, g, market: str) -> bool:
    """Whether a retrospective reconstruction (made after the start; never a pregame forecast) exists for the game."""
    for table, extra in (("recovered_forecasts", ""), ("adaptive_recovered_forecasts", " AND status='reconstructed'")):
        if _has_table(conn, table) and conn.execute(f"SELECT 1 FROM {table} WHERE game_id=? AND market=?{extra} LIMIT 1",
                                                    (g["game_id"], market)).fetchone():
            return True
    return False


def games_report(conn: sqlite3.Connection, *, start: str, end: str, as_of: str, with_source: bool = True,
                 with_ledgers: bool = True) -> dict:
    """Every scheduled game in [start, end) and every market: the canonical every-game forecast (latest made before the
    start) and its grade, or why there is none. Main (adaptive) and fallback coverage, preseason apart, an unfiltered
    record of every graded forecast, and threshold views that only filter it. Read-only."""
    from . import live, sports
    sport = live.engine(conn).sport
    market_list = sports.get(sport).markets
    games = _games(conn, start, end)
    has = _has_table(conn, "game_forecasts")
    began = conn.execute("SELECT MIN(generated_at) FROM game_forecasts WHERE sport = ?", (sport,)).fetchone()[0] if has else None
    latest = {}
    if has:
        for r in conn.execute("SELECT f.* FROM game_forecasts f WHERE f.sport = ? AND f.tipoff_at >= ? AND f.tipoff_at < ? AND "
                              "f.forecast_id = (SELECT x.forecast_id FROM game_forecasts x WHERE x.game_id = f.game_id AND "
                              "x.market = f.market AND x.generated_at < x.tipoff_at ORDER BY x.generated_at DESC, x.forecast_id DESC "
                              "LIMIT 1)", (sport, start, end)):
            latest[(r["game_id"], r["market"])] = r
    # Canonical markets: the same selection as the canonical pages (a later missing attempt never hides an earlier forecast;
    # it is reported apart). Other markets keep the latest row (unchanged so far).
    canon_markets = set(market_list) if has else set()
    canon_rows = {m: _canonical_rows(conn, [g["game_id"] for g in games], m, as_of) for m in canon_markets}
    buckets = {"competitive": {}, "preseason": {}}
    schedule = {"games": len(games), "by_season_type": {}, "by_status": {}, "games_with_any_forecast": 0,
                "games_fully_forecast": 0, "games_unvalidated": 0, "upcoming_games": 0, "not_forecastable_type": 0, "corrected_results": 0}
    missing_reasons: dict = {}
    record = {"all": _blank(), "main": _blank(), "fallback": _blank()}
    record_by_market = {m: {"all": _blank(), "main": _blank(), "fallback": _blank()} for m in market_list}
    by_market = {m: {"cells": 0, "forecast": 0, "main": 0, "fallback": 0, "missing": 0, "upcoming": 0, "unvalidated": 0,
                     **{k: 0 for k in ("graded", "pending", "void", "unscorable", "unresolved")}}
                 for m in market_list}
    thresholds = {m: {t: {"all": _blank(), "main": _blank(), "fallback": _blank()} for t in threshold_grid(m)} for m in market_list}
    games_out = []
    for g in games:
        st = g["season_type"]
        schedule["by_season_type"][st] = schedule["by_season_type"].get(st, 0) + 1
        schedule["by_status"][g["status"]] = schedule["by_status"].get(g["status"], 0) + 1
        if g["status"] == "final" and g["result_first_seen_at"] and g["result_available_at"] and g["result_available_at"] > g["result_first_seen_at"]:
            schedule["corrected_results"] += 1
        if st not in FORECAST_TYPES:
            schedule["not_forecastable_type"] += 1
            continue
        bucket = "preseason" if st == "preseason" else "competitive"
        cells, n_forecast = [], 0
        for market in market_list:
            r = latest.get((g["game_id"], market))
            mk = by_market[market]
            mk["cells"] += 1
            extra, moved = {}, False             # moved: a pre-ledger night-before forecast void by its History's rule
            rows = canon_rows[market].get(g["game_id"], []) if market in canon_markets else None
            if rows is not None and (rows or st == "preseason" or not (began is None or g["tipoff_at"] <= began)):
                c = _canonical_one(g, rows, market, as_of, began)
                sel, _ = _select(g, rows)
                if c["state"] == "unvalidated":            # no canonical forecast; any stored row is experimental only
                    mk["unvalidated"] += 1
                    cell = {"market": market, "status": "unvalidated", "reason": c["reason"]}
                    if sel is not None:
                        buckets[bucket][sel["source"]] = buckets[bucket].get(sel["source"], 0) + 1   # experimental row's source
                        sp = (sel["p_a"], sel["p_mid"], sel["p_b"])
                        cell["experimental"] = {"label": EXPERIMENTAL_LABEL, "forecast_id": sel["forecast_id"], "source": sel["source"],
                                                "model_version": sel["model_version"], "confidence": sel["confidence"], "p": list(sp),
                                                "line": sel["line"], "line_source": sel["line_source"],
                                                "grade": grade(market, sel["line"], sp, g, as_of, sel["tipoff_at"])}
                    cells.append(cell)
                    continue
                r = sel                                    # None when the canonical state is missing or upcoming
                if c["state"] == "forecast":
                    extra = {"later_failure": c["later_failure"]}
                elif c["state"] == "missing":
                    r = {"status": "missing", "reason": c["reason"]}
            before = r is None and (began is None or g["tipoff_at"] <= began)
            if before:              # before the every-game ledger: the existing ledgers' pregame forecast, if any
                e = _existing(conn, g, market)
                if e is not None:
                    r = {"status": "forecast", "source": e["source"], "p_a": e["p"][0], "p_mid": e["p"][1], "p_b": e["p"][2],
                         "line": e["line"], "line_source": e["line_source"], "tipoff_at": e["tipoff_at"],
                         "confidence": _confidence(market, e["p"]), "fallback_reason": None, "model_version": e["model_version"]}
                    moved = e.get("void", False)
            if r is None and g["tipoff_at"] > as_of and g["status"] == "scheduled":   # not started: nothing is missing yet
                mk["upcoming"] += 1
                cells.append({"market": market, "status": "upcoming"})
                continue
            if r is None or r["status"] == "missing":
                if r is not None:
                    why = r["reason"]
                elif before:
                    why = "no pregame forecast in any ledger (before every-game recording began)"
                elif g["status"] in ("postponed", "canceled"):
                    why = f"not recorded: game {g['status']} before its two-hour window"
                else:
                    why = "not recorded: no forecast cycle ran within two hours of the start"
                mk["missing"] += 1
                missing_reasons[why] = missing_reasons.get(why, 0) + 1
                cells.append({"market": market, "status": "missing", "reason": why,
                              "retrospective_available": _retrospective(conn, g, market)})
                continue
            n_forecast += 1
            kind = "main" if r["source"] == "adaptive" else "fallback"
            mk["forecast"] += 1
            mk[kind] += 1
            p = (r["p_a"], r["p_mid"], r["p_b"])
            state = "void" if moved else grade(market, r["line"], p, g, as_of, r["tipoff_at"])
            if state in ("win", "loss", "push"):
                mk["graded"] += 1
            else:
                mk[state] += 1
            src = buckets[bucket].setdefault(r["source"], 0)
            buckets[bucket][r["source"]] = src + 1
            if bucket == "competitive":
                _add(record["all"], state)
                _add(record[kind], state)
                _add(record_by_market[market]["all"], state)
                _add(record_by_market[market][kind], state)
                for t, rec_t in thresholds[market].items():      # the market's own grid; markets are never pooled
                    if r["confidence"] >= t - 1e-12:
                        _add(rec_t["all"], state)
                        _add(rec_t[kind], state)
            cells.append({"market": market, "status": state, "source": r["source"], "fallback_reason": r["fallback_reason"],
                          "line": r["line"], "line_source": r["line_source"], "confidence": r["confidence"],
                          "p": list(p), "model_version": r["model_version"],
                          "forecast_id": None if before else r["forecast_id"],
                          "recorded_in": "existing ledgers" if before else "game_forecasts",
                          **({"label": EXPERIMENTAL_LABEL} if bucket == "preseason" else {}), **extra})
        schedule["upcoming_games"] += all(c["status"] == "upcoming" for c in cells)
        schedule["games_with_any_forecast"] += n_forecast > 0
        schedule["games_fully_forecast"] += n_forecast == len(market_list)
        schedule["games_unvalidated"] += all(c["status"] == "unvalidated" for c in cells)   # preseason: no validated model
        games_out.append({"game_id": g["game_id"], "tipoff_at": g["tipoff_at"], "season_type": st, "status": g["status"],
                          "home_team_id": g["home_team_id"], "away_team_id": g["away_team_id"], "markets": cells})
    preseason = {"all": _blank()}      # experimental rows only (no validated preseason model); never in the record above
    for g in games_out:
        if g["season_type"] == "preseason":
            for c in g["markets"]:
                state = c["experimental"]["grade"] if "experimental" in c else c["status"]
                if state in ("win", "loss", "push", "pending", "void", "unscorable", "unresolved"):
                    _add(preseason["all"], state)
    out = {"sport": sport, "start": start, "end": end, "as_of": as_of, "order_version": ORDER_VERSION, "order": list(ORDER),
           "recording_began_at": began, "schedule": schedule,
           "sources": buckets, "by_market": by_market,
           "missing_reasons": [{"reason": k, "cells": v} for k, v in sorted(missing_reasons.items(), key=lambda kv: -kv[1])],
           "record": {k: _finish_record(v) for k, v in record.items()},        # all markets combined (counts across markets)
           "record_by_market": {m: {k: _finish_record(v) for k, v in r.items()} for m, r in record_by_market.items()},
           "preseason_record": {**_finish_record(preseason["all"]), "label": EXPERIMENTAL_LABEL},
           "thresholds_by_market": {m: [{"threshold": t, **{k: _finish_record(v) for k, v in rows.items()}} for t, rows in ts.items()]
                                    for m, ts in thresholds.items()},
           "games": games_out,
           "notes": ["The record counts every graded every-game forecast (regular season, play-in, playoffs); preseason rows are "
                     "experimental / unvalidated (no model is validated on preseason games), reported separately and never "
                     "mixed in. Thresholds only filter this record, per market on the market's own grid (50-90% for two-way "
                     "markets, 35-70% for the regulation three-way market); markets are never pooled.",
                     "Every market uses the canonical selection (the same record as Today, the game page, History and "
                     "Performance): a later attempt without a forecast never hides an earlier forecast and is shown apart; "
                     "preseason has no canonical forecast.",
                     "Main = an approved adaptive ensemble (none is approved: since coverage-v2 adaptive forecasts stay research and "
                     "are never this record's source; adaptive cells come only from rows recorded earlier); fallback = the sealed "
                     "primary model (recorded by the two-hour primary writers, or computed for this ledger with the same guards). "
                     "Fallback rows never enter ensemble training.",
                     "Grades are computed on read from the stored results, so a corrected result re-grades the forecast once "
                     "(never twice); corrected_results counts games whose final result changed after it was first seen."]}
    if with_source:
        out["source_schedule"] = source_schedule(conn, start, end)
    if with_ledgers:
        out["ledgers"] = ledger_grading(conn, start=start, end=end, as_of=as_of)
    return out


def _ledger_rows(conn, start: str, end: str):
    """(ledger, model, retrospective, game_id, market, line, p, forecast tipoff or None, night-before cutoff or None) for
    every stored row with a probability for games in the window, made before the start (retrospective ledgers: their
    simulated cutoff before the start)."""
    import json
    w = "g.tipoff_at >= ? AND g.tipoff_at < ?"
    if _has_table(conn, "predictions"):
        for r in conn.execute(f"SELECT p.*, g.tipoff_at AS gt FROM predictions p JOIN games g ON g.game_id = p.game_id WHERE {w} "
                              "AND p.run_type = 'live' AND p.generated_at < g.tipoff_at", (start, end)):
            p = r["home_win_prob"]
            yield "predictions", r["model_version"], False, r["game_id"], "winner", None, (p, 0.0, 1 - p), r["cutoff_at"], r["cutoff_at"]
    simple = [("market_forecasts", "model_version", "AND x.run_type = 'live'"), ("candidate_forecasts", "candidate", ""),
              ("adaptive_forecasts", "model_version", "AND x.status = 'forecast'"), ("game_forecasts", "source", "AND x.status = 'forecast'")]
    for table, model, extra in simple:
        if _has_table(conn, table):
            for r in conn.execute(f"SELECT x.* FROM {table} x JOIN games g ON g.game_id = x.game_id WHERE {w} {extra} "
                                  "AND x.generated_at < g.tipoff_at", (start, end)):
                yield table, r[model], False, r["game_id"], r["market"], r["line"], (r["p_a"], r["p_mid"], r["p_b"]), r["tipoff_at"], None
    if _has_table(conn, "research_cohorts"):
        for r in conn.execute(f"SELECT x.* FROM research_cohorts x JOIN games g ON g.game_id = x.game_id WHERE {w} "
                              "AND x.generated_at < g.tipoff_at", (start, end)):
            for part in ("average", "stack"):
                v = json.loads(r[part]) if r[part] else None
                v = v.get("p") if isinstance(v, dict) else v
                if v:
                    yield ("research_cohorts", f"{part} ({r['definition'][:12]})", False, r["game_id"], r["market"], r["line"],
                           tuple(v), r["tipoff_at"], None)
    for table, extra in (("recovered_forecasts", ""), ("adaptive_recovered_forecasts", "AND x.status = 'reconstructed'")):
        if _has_table(conn, table):
            for r in conn.execute(f"SELECT x.* FROM {table} x JOIN games g ON g.game_id = x.game_id WHERE {w} {extra}", (start, end)):
                yield table, r["model_version"], True, r["game_id"], r["market"], r["line"], (r["p_a"], r["p_mid"], r["p_b"]), r["tipoff_at"], None


def ledger_grading(conn, *, start: str, end: str, as_of: str) -> list:
    """Every stored forecast row with a probability, in every ledger, graded (all rows, not only the latest per game; no
    threshold). Retrospective ledgers are flagged and never mixed with pregame ones."""
    from . import backtest
    games = {g["game_id"]: g for g in _games(conn, start, end)}
    groups: dict = {}
    for ledger, model, retro, gid, market, line, p, ftip, cutoff in _ledger_rows(conn, start, end):
        g = games.get(gid)
        if g is None or p is None or p[0] is None:
            continue
        if cutoff is not None and ledger == "predictions":
            night = cutoff == backtest.cutoff_for(cutoff) and cutoff != g["tipoff_at"]
            if night:      # night-before rows are void only when the game moved to another Eastern date (live.day_changed)
                ftip = None if backtest.cutoff_for(g["tipoff_at"]) == cutoff else "void"
        state = "void" if ftip == "void" else grade(market, line, p, g, as_of, ftip)
        key = (ledger, model, market)
        rec = groups.setdefault(key, {"ledger": ledger, "model": model, "market": market, "retrospective": retro,
                                      "games": set(), **_blank()})
        rec["games"].add(gid)
        _add(rec, state)
    out = []
    for rec in groups.values():
        rec["games"] = len(rec["games"])
        out.append(_finish_record(rec))
    return sorted(out, key=lambda r: (r["retrospective"], r["ledger"], r["model"], r["market"]))
