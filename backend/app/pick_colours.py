"""GET /api/pick-colours: how the picks that were green, red or neutral BEFORE their start turned out (docs/PICK_COLOURS.md).

Reads app/pick_snapshots.py (the saved colours) beside the forecast of record of every started game and market and the delivery
ledger's confirmed sends, all read-only. Grades are made on read from the saved side and line. It measures the colour rule; it
does not show that green picks are profitable, and no colour here is ever recomputed from today's record."""
import os
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from . import config, coverage, db, pick_snapshots as ps, published_picks as pp, telegram_history as th, threshold_overview as to

SPORTS = ("nba", "nhl", "nfl", "ncaab", "soccer")
RECORDS = ("live", "recovered")
NOTE = ("Each pick's colour is the one saved before its start, from the record its exact percentage had then; later results never change it. "
        "One prediction per game and market: the latest pregame forecast, with that forecast's own first saved colour. A forecast whose "
        "colour was not saved before the start is unknown. Telegram sends: the last confirmed card before the start, graded as that card "
        "published it. Returns use a stored sportsbook price of the same side and line, at most 20 minutes old when saved; everything else "
        "is unpriced. This measures the colours; it does not show that green picks are profitable.")
CACHE_SECONDS = 45
_cache: dict = {}
_turns = {s: threading.Lock() for s in SPORTS}     # one build per sport at a time; a slow sport never holds up another


def markets_of(sport: str) -> tuple:
    if sport == "soccer":
        from . import soccer_markets as sm
        return tuple(dict.fromkeys(("1x2_regulation", *sm.TITLES)))
    return th.markets_of(sport)


def _title(sport: str, market: str) -> str:
    try:
        return th.market_title(sport, market)
    except (KeyError, ValueError):
        return market


def _published(sport: str) -> list:
    p = config.telegram_ledger_path()
    if not p.is_file():
        return []
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'published_picks'").fetchone():
            return []
        return [dict(r) for r in conn.execute("SELECT * FROM published_picks WHERE sport = ? AND basis <> ? ORDER BY published_id",
                                              (sport, pp.UNCONFIRMED_BASIS))]
    finally:
        conn.close()


def starts(conn, sport: str) -> dict:
    sql = "SELECT match_id, kickoff_at FROM soccer_matches" if sport == "soccer" else "SELECT game_id, tipoff_at FROM games"
    return dict(tuple(r) for r in conn.execute(sql))


def _regrade(conn, sport: str, now: str):
    """(saved row, item) -> its grade by the app's own settlement; 'unscorable' when the stored forecast is not the saved pick."""
    if sport == "soccer":
        return lambda row, item: pp.grade(conn, sport, row, None, now)
    games: dict = {}

    def go(row, item):
        gid = row["game_id"]
        if gid not in games:
            g = conn.execute("SELECT * FROM games WHERE game_id = ?", (gid,)).fetchone()
            g = None if g is None else dict(g)
            if g is not None and g["status"] == "final" and (not g["result_available_at"] or g["result_available_at"] > now):
                g.update(status="in_progress", home_score=None, away_score=None)
            games[gid] = g
        f = conn.execute("SELECT line, p_a, p_mid, p_b FROM game_forecasts WHERE forecast_id = ?", (int(row["forecast_id"]),)).fetchone()
        if games[gid] is None or f is None:
            return None
        same_line = row["line"] is None and f["line"] is None or (row["line"] is not None and f["line"] is not None
                                                                  and abs(row["line"] - f["line"]) < 1e-9)
        if not same_line or coverage.pick_side(row["market"], (f["p_a"], f["p_mid"], f["p_b"])) != row["side"]:
            return "unscorable"
        return pp.grade(conn, sport, row, games[gid], now)
    return go


def _items(conn, sport: str, now: str) -> tuple:
    """(the forecast of record per started game and market, the live items the recovered history is compared with)."""
    if sport == "soccer":
        from . import soccer_live, soccer_record as sr
        if not soccer_live.has_tables(conn):
            return [], []
        names = {r["match_id"]: r for r in conn.execute("SELECT m.match_id, m.home_score, m.away_score, m.status, h.name AS home, a.name AS away "
                                                        "FROM soccer_matches m JOIN teams h ON h.team_id = m.home_team_id "
                                                        "JOIN teams a ON a.team_id = m.away_team_id")}
        out = []
        for p in sr.all_picks(conn, now):
            if p["tipoff"] > now or not p["generated_at"] < p["tipoff"]:
                continue
            m = names.get(p["game_id"])
            final = m is not None and m["status"] == "final" and m["home_score"] is not None and p["grade"] != "pending"
            out.append({"game_id": p["game_id"], "market": p["market"], "percentage": to.displayed_percentage(p["probability"]),
                        "side": sr.pick_side(p), "line": p.get("line"), "grade": p["grade"], "start_at": p["tipoff"],
                        "game_date": coverage.et_date(p["tipoff"]), "forecast_id": str(p["forecast_id"]), "generated_at": p["generated_at"],
                        "model_version": sr.model_version(conn, p), "pick": p["pick_label"], "matchup": p["matchup"],
                        "final_score": f"{m['home']} {m['home_score']} - {m['away_score']} {m['away']}" if final else None,
                        "forecast_key": ps.forecast_key("soccer", p)})
        return out, th.soccer_live_items(conn, now)
    if sport == "nfl":
        from .nfl_live import Views
        raw = th.nfl_live_items(conn, Views(conn, now))
    else:
        from .providers.real import RealProvider
        raw = th.provider_live_items(RealProvider(conn, now))
    keep = ("game_id", "market", "percentage", "side", "grade", "start_at", "game_date", "generated_at", "model_version", "pick", "matchup",
            "final_score")
    return [{**{k: x[k] for k in keep}, "line": x["home_line"], "forecast_id": str(x["forecast_id"]),
             "forecast_key": str(x["forecast_id"])} for x in raw], raw


def build(sport: str, now: str) -> Optional[dict]:
    """Everything the report needs for one sport, or None without a store."""
    if not config.db_path(sport).is_file():
        return None
    conn = db.connect_readonly(sport)
    try:
        conn.execute("BEGIN")
        items, live = _items(conn, sport, now)
        for it in items:
            it.update(sport=sport, market_title=_title(sport, it["market"]))
        snaps = ps.load(sport)
        views = ps.classify(sport, items, snaps, _published(sport), _regrade(conn, sport, now), starts=starts(conn, sport), now=now)
        for r in views["sent"]:
            r.setdefault("market_title", _title(sport, r["market"]))
            r.setdefault("pick", None)
            r.setdefault("matchup", None)
            r.setdefault("final_score", None)
        body, why = th.load()
        rows = [] if body is None else [r for r in body["rows"] if r["sport"] == sport]
        recovered_only = [{**r, "price": None} for r in th.combine(sport, rows, live)["rows"] if r["counted"]] if rows else []
        return {"views": views, "recovered_only": [{"game_id": r["game_id"], "market": r["market"], "grade": r["grade"], "price": None}
                                                   for r in recovered_only],
                "recovered_file": why, "upcoming": len({(s["game_id"], s["market"]) for s in snaps if s["event"] == "seen" and s["start_at"] > now}),
                "tracking_start": ps.tracking_start(snaps),
                "backfilled_from": min((s["observed_at"] for s in snaps if s["basis"] == "card_text"), default=None)}
    finally:
        conn.close()


def _stamp(sport: str) -> tuple:
    out = []
    for p in (config.db_path(sport), ps.path(), config.telegram_ledger_path(), to.telegram_history_path()):
        for f in (str(p), f"{p}-wal"):
            try:
                out.append((f, os.stat(f).st_mtime_ns))
            except OSError:
                out.append((f, None))
    return tuple(out)


def _built(sport: str, now: Optional[str]) -> Optional[dict]:
    """`build`, kept while nothing it reads has changed and at most CACHE_SECONDS (a start can pass without a write)."""
    if now is not None:
        return build(sport, now)
    with _turns[sport]:
        stamp, at = _stamp(sport), time.monotonic()
        hit = _cache.get(sport)
        if hit and hit[0] == stamp and at - hit[1] < CACHE_SECONDS:
            return hit[2]
        body = build(sport, datetime.now(timezone.utc).strftime(ps.FMT))
        _cache[sport] = (stamp, at, body)
        return body


def _groups(rows: list, record: str) -> dict:
    field = "colour" if record == "live" else "recovered_colour"
    return {c: ps.tally([r for r in rows if r[field] == c]) for c in ps.COLOURS if record == "recovered" or c != "no_record"}


def report(sport: str = "all", view: str = "all", record: str = "live", market: Optional[str] = None, percentage: Optional[str] = None,
           date_from: Optional[str] = None, date_to: Optional[str] = None, colour: Optional[str] = None, limit: int = 100, offset: int = 0,
           now: Optional[str] = None) -> dict:
    names = SPORTS if sport == "all" else (sport,)
    states, everything, recovered_only, excluded, sports_out = {}, {v: [] for v in ps.VIEWS}, [], Counter(), []
    for s in names:
        try:
            b = _built(s, now)
            status = "ok" if b is not None else "no_store"
        except Exception as exc:          # one sport's problem is named and never hides the others or reads as zero
            b, status = None, f"unavailable: {type(exc).__name__}"
        states[s] = b
        sports_out.append({"sport": s, "status": status, "tracking_start": b and b["tracking_start"], "backfilled_from": b and b["backfilled_from"],
                           "upcoming": b["upcoming"] if b else 0, "markets": [{"market": m, "title": _title(s, m)} for m in markets_of(s)]})
        if b is None:
            continue
        for v in ps.VIEWS:
            everything[v] += b["views"][v]
        recovered_only += b["recovered_only"]
        excluded.update(b["views"]["excluded"])

    def keep(r, pct=True):
        return ((market is None or r["market"] == market) and (not pct or percentage is None or r["percentage"] == percentage)
                and (date_from is None or r["game_date"] >= date_from) and (date_to is None or r["game_date"] <= date_to))

    scope = [r for r in everything[view] if keep(r, False)]
    rows = [r for r in scope if keep(r)]
    field = "colour" if record == "live" else "recovered_colour"
    listed = sorted((r for r in rows if colour is None or r[field] == colour),
                    key=lambda r: (r["start_at"], r["game_id"], r["market"]), reverse=True)
    by_market = []
    for key in sorted({(r["sport"], r["market"]) for r in rows}, key=lambda k: (SPORTS.index(k[0]), k[1])):
        mine = [r for r in rows if (r["sport"], r["market"]) == key]
        by_market.append({"sport": key[0], "market": key[1], "title": _title(*key), "all": ps.tally(mine), "groups": _groups(mine, record)})
    by_pct = []
    for p in sorted({r["percentage"] for r in scope}, key=float, reverse=True):
        mine = [r for r in scope if r["percentage"] == p]
        by_pct.append({"percentage": p, "all": ps.tally(mine), "groups": _groups(mine, record)})
    reasons = Counter(r["unknown_reason"] for r in everything["all"] if r["colour"] == "unknown")
    whole = ps.summarise(everything["all"])
    return {
        "sport": sport, "view": view, "record": record, "as_of": now or datetime.now(timezone.utc).strftime(ps.FMT), "note": NOTE,
        "colour_rule": to.COLOUR_RULE, "legend": to.COLOUR_LEGEND, "rule_live_since": ps.RULE_LIVE_SINCE,
        "tracking_start": min((x["tracking_start"] for x in sports_out if x["tracking_start"]), default=None),
        "backfilled_from": min((x["backfilled_from"] for x in sports_out if x["backfilled_from"]), default=None),
        "sports": sports_out, "filters": {"market": market, "percentage": percentage, "date_from": date_from, "date_to": date_to, "colour": colour},
        "summary": ps.summarise(rows), "by_market": by_market, "by_percentage": by_pct,
        "views": {v: ps.tally([r for r in everything[v] if keep(r)]) for v in ps.VIEWS},
        "rows": [{k: v for k, v in r.items() if k != "forecast_key"} for r in listed[offset:offset + limit]], "rows_total": len(listed),
        "reconciliation": {
            "live_forecasts": whole["total"], "known_colour": sum(whole["live"][c]["predictions"] for c in ("green", "red", "neutral")),
            "unknown_colour": whole["live"]["unknown"]["predictions"], "unknown_reasons": dict(reasons),
            "observed_live": sum(1 for r in everything["all"] if r["support"] and r["support"]["basis"] == "live"),
            "observed_card_text": sum(1 for r in everything["all"] if r["support"] and r["support"]["basis"] == "card_text"),
            "recovered_only": ps.tally(recovered_only), "combined_predictions": len(everything["all"]) + len(recovered_only),
            "recovered_note": "Recovered Nov 2-16 cards without a live forecast: colour unknown under this rule, listed in History, counted once.",
            "sent": len(everything["sent"]), "sent_excluded": dict(excluded),
            "upcoming_saved": sum(x["upcoming"] for x in sports_out), "adds_up": whole["adds_up"]}}
