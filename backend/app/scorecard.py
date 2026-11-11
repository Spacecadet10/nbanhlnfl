"""The scorecard: the full picture of every recorded pick, as it was knowable when the pick was made (owner, Nov 11 2026).

One append-only row per recorded forecast (NBA/NHL/NFL: every `game_forecasts` row with status 'forecast'; soccer: every
`soccer_live_forecasts` row), in a separate file per sport, `<data>/scorecard/<sport>.sqlite`, never in the sport store
(the live NBA/NHL stores are at migration 31; a store migration would drag the unreviewed 0032-0040 onto them). Which row
is the canonical pick is decided on read (app/coverage.canonical: the latest forecast before the start; soccer: the
latest version), exactly like every page, so a row that was later superseded stays as it was recorded. Results are never
copied here: they are joined from the store when read (scripts/scorecard_report.py).

Timestamp rule: every fact is read as of the forecast's own `generated_at` (T). Quotes retrieved <= T; the opening quote
is the first one retrieved by T; the other predictions on the same card are the latest forecast of each other market of
the same game with generated_at <= T; lineup facts from listings / injury reports received <= T (app/lineup.py); the
three-book prices from TheRundown responses fetched <= T (app/rundown_odds.read_cache with now=T); rest days from the
team's previous start. Anything not knowable at T is NULL with state 'missing' and a reason, never a later value. A field
the sport does not have (soccer spread, NFL lineup, DRatings outside NBA/NHL winner) is 'not_applicable', kept apart from
'missing'. DRatings numbers are captured by app/dratings_capture a few minutes AFTER the forecast (its page is published
before; the capture time and lag are kept): they sit in their own table and are appended when a capture appears.

Writer: `python -m app.scorecard sync` (its own LaunchAgent, every minute), a separate process from the forecast jobs, so
a scorecard failure or a slow run can never stop, delay or alter a forecast. Idempotent on forecast_id; never rewrites.
"""
import json
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from . import config

VERSION = "scorecard-v1"
SPORTS = ("nba", "nhl", "nfl", "soccer")
STATES = ("present", "missing", "not_applicable")
GROUPS = ("book", "open", "card", "spread", "rest", "lineup", "books3")
B2B_BELOW_DAYS = 1.5           # the models' back-to-back feature (app/models/logistic.py: rest < 1.5 days)
JOB_LOOKBACK_HOURS = 12        # `sync` without --backfill looks at forecasts made this recently

_STATE_COLS = "".join(f"  {g}_state TEXT NOT NULL CHECK ({g}_state IN ('present','missing','not_applicable')),\n"
                      f"  {g}_note TEXT,\n" for g in GROUPS)
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS scorecard (
  forecast_id INTEGER PRIMARY KEY,
  sport TEXT NOT NULL, source_table TEXT NOT NULL, game_id TEXT NOT NULL, game_date TEXT, matchup TEXT,
  season_type TEXT, market TEXT NOT NULL, side TEXT NOT NULL, pick_label TEXT, line REAL, line_source TEXT,
  displayed_pct TEXT NOT NULL, confidence REAL NOT NULL, p_json TEXT NOT NULL, source TEXT, model_version TEXT,
  generated_at TEXT NOT NULL, tipoff_at TEXT NOT NULL,
{_STATE_COLS}  book_provider TEXT, book_retrieved_at TEXT, book_line REAL, book_price INTEGER, book_devig REAL,
  open_retrieved_at TEXT, open_label TEXT, open_line REAL, open_price INTEGER, open_devig REAL, move_pts REAL, move_line REAL,
  card_others TEXT, spread_on_winner INTEGER,
  home_rest_days REAL, away_rest_days REAL, home_b2b INTEGER, away_b2b INTEGER,
  lineup_facts TEXT, lineup_flags TEXT, lineup_flag_count INTEGER,
  books3 TEXT, detail TEXT,
  scorecard_version TEXT NOT NULL, written_by TEXT NOT NULL CHECK (written_by IN ('job','backfill')), written_at TEXT NOT NULL,
  CHECK (book_retrieved_at IS NULL OR book_retrieved_at <= generated_at),
  CHECK (open_retrieved_at IS NULL OR open_retrieved_at <= generated_at),
  CHECK (generated_at <= written_at)
);
CREATE TABLE IF NOT EXISTS scorecard_dratings (
  forecast_id INTEGER PRIMARY KEY REFERENCES scorecard(forecast_id),
  sport TEXT NOT NULL, captured_at TEXT NOT NULL, lag_seconds INTEGER, matched INTEGER NOT NULL,
  dr_p_home REAL, dr_p_away REAL, dr_proj_home REAL, dr_proj_away REAL, page TEXT, page_sha256 TEXT, written_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS scorecard_no_update BEFORE UPDATE ON scorecard
BEGIN SELECT RAISE(ABORT, 'scorecard is append-only'); END;
CREATE TRIGGER IF NOT EXISTS scorecard_no_delete BEFORE DELETE ON scorecard
BEGIN SELECT RAISE(ABORT, 'scorecard is append-only'); END;
CREATE TRIGGER IF NOT EXISTS scorecard_dratings_no_update BEFORE UPDATE ON scorecard_dratings
BEGIN SELECT RAISE(ABORT, 'scorecard_dratings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS scorecard_dratings_no_delete BEFORE DELETE ON scorecard_dratings
BEGIN SELECT RAISE(ABORT, 'scorecard_dratings is append-only'); END;
"""


def path(sport: str, data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or config.data_dir()) / "scorecard" / f"{sport}.sqlite"


def connect(p: Path, *, create: bool = False) -> Optional[sqlite3.Connection]:
    """The scorecard file (schema ensured when `create`); None when it does not exist and `create` is False."""
    if not create and not Path(p).is_file():
        return None
    if create:
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p)
        conn.executescript(SCHEMA)
    else:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _state(present: bool, note: Optional[str] = None) -> tuple:
    return ("present", note) if present else ("missing", note)


NA = lambda why: ("not_applicable", why)                                     # noqa: E731


def _picked(market: str, side: str, p_a: Optional[float], vec: Optional[tuple] = None) -> Optional[float]:
    from .threshold_overview import _picked as picked
    return picked(market, vec if market == "reg3" else p_a, side)


# ---- game sports (NBA, NHL, NFL): game_forecasts ------------------------------------------------------------------------
def _abbr(conn, team_id: str) -> str:
    r = conn.execute("SELECT abbr FROM teams WHERE team_id=?", (team_id,)).fetchone()
    return r[0] if r else team_id


def _side_of(market: str, row) -> str:
    from .coverage import pick_side
    return pick_side(market, (row["p_a"], row["p_mid"], row["p_b"]))


def _book(conn, sport: str, g, rec: dict) -> tuple:
    """((state, note), quote fields) of the quote the pick could see: winner = latest moneyline retrieved <= T; other
    markets = the quote the forecast used (coverage.forecast_quote: same game, market and line, retrieved <= T)."""
    from . import coverage
    side, market = rec["side"], rec["market"]
    if market == "winner":
        q = coverage.winner_quote(conn, sport, g["game_id"], rec["generated_at"])
        if q is None:
            return _state(False, "no moneyline retrieved by the forecast time"), {}
        p = q["p_home"] if side == "a" else 1 - q["p_home"]
        return _state(True), {"book_provider": q["provider"], "book_retrieved_at": q["retrieved_at"], "book_line": None,
                              "book_price": q["price_home"] if side == "a" else q["price_away"], "book_devig": p}
    pa, why = coverage.forecast_quote(conn, market, g, rec)
    if pa is None:
        return _state(False, why), {}
    q = conn.execute("SELECT * FROM market_quotes WHERE quote_id=?", (rec["market_quote_id"],)).fetchone()
    price = {"a": q["price_a"], "mid": q["price_mid"], "b": q["price_b"]}[side]
    return _state(True), {"book_provider": q["provider"], "book_retrieved_at": q["retrieved_at"], "book_line": q["line"],
                          "book_price": price, "book_devig": _picked(market, side, pa if market != "reg3" else None,
                                                                     pa if market == "reg3" else None)}


def _open(conn, sport: str, g, rec: dict, book: dict) -> tuple:
    """The opening quote: the first quote of this game and market retrieved by T (the book's own 'open' price of that
    retrieval first), its pick-side de-vigged % and the move to the pick's quote (points; + = toward the pick)."""
    from . import coverage
    market, side, at = rec["market"], rec["side"], rec["generated_at"]
    if market == "winner":
        q = coverage.winner_quote(conn, sport, g["game_id"], at, earliest=True)
        if q is None:
            return _state(False, "no moneyline retrieved by the forecast time"), {}
        out = {"open_retrieved_at": q["retrieved_at"], "open_label": q["price_label"], "open_line": None,
               "open_price": q["price_home"] if side == "a" else q["price_away"],
               "open_devig": q["p_home"] if side == "a" else 1 - q["p_home"]}
    else:
        if not _has(conn, "market_quotes"):
            return _state(False, "no market quotes table"), {}
        q = conn.execute("SELECT * FROM market_quotes WHERE game_id=? AND market=? AND retrieved_at<=? "
                         "ORDER BY retrieved_at, price_label='open' DESC, quote_id LIMIT 1",
                         (g["game_id"], market, at)).fetchone()
        if q is None:
            return _state(False, "no quote of this market retrieved by the forecast time"
                          if market != "reg3" else "no regulation three-way bookmaker quotes are collected"), {}
        vec = (q["p_a_devig"], q["p_mid_devig"], q["p_b_devig"])
        out = {"open_retrieved_at": q["retrieved_at"], "open_label": q["price_label"], "open_line": q["line"],
               "open_price": {"a": q["price_a"], "mid": q["price_mid"], "b": q["price_b"]}[side],
               "open_devig": _picked(market, side, q["p_a_devig"], vec)}
    if book.get("book_devig") is not None and out["open_devig"] is not None:
        out["move_pts"] = round(100 * (book["book_devig"] - out["open_devig"]), 2)
        if out["open_line"] is not None and book.get("book_line") is not None:
            out["move_line"] = book["book_line"] - out["open_line"]
        note = None
    else:
        note = "no move: the pick has no sportsbook quote at its own line"
    return _state(True, note), out


def _card(conn, g, rec: dict, home: str, away: str) -> tuple:
    """The other markets' latest forecasts of the same game recorded at or before T (the card as it stood at T)."""
    from . import markets
    from .threshold_overview import displayed_percentage
    from .coverage import _confidence
    others = []
    for r in conn.execute("SELECT * FROM game_forecasts f WHERE game_id=? AND market<>? AND status='forecast' "
                          "AND generated_at<=? AND forecast_id = (SELECT forecast_id FROM game_forecasts h WHERE "
                          "h.game_id=f.game_id AND h.market=f.market AND h.status='forecast' AND h.generated_at<=? "
                          "ORDER BY h.generated_at DESC, h.forecast_id DESC LIMIT 1) ORDER BY market",
                          (g["game_id"], rec["market"], rec["generated_at"], rec["generated_at"])):
        side = _side_of(r["market"], r)
        labels = markets.side_labels(r["market"], r["line"], home, away)
        conf = _confidence(r["market"], (r["p_a"], r["p_mid"], r["p_b"]))
        others.append({"market": r["market"], "forecast_id": r["forecast_id"], "side": side,
                       "pick_label": labels[("a", "mid", "b").index(side)], "displayed_pct": displayed_percentage(conf),
                       "line": r["line"], "line_source": r["line_source"], "generated_at": r["generated_at"]})
    return _state(True, None if others else "no other forecast of this game recorded by the forecast time"), others


def _spread_on_winner(rec: dict, others: list) -> tuple:
    """Is the spread pick on the team picked to win (both as known at T)? Side 'a' is the home team in both markets."""
    by = {o["market"]: o["side"] for o in others}
    by[rec["market"]] = rec["side"]
    if "winner" not in by or "spread" not in by:
        return _state(False, "no " + " and no ".join(m for m in ("winner", "spread") if m not in by) +
                      " forecast on the card by the forecast time"), None
    return _state(True), int(by["winner"] == by["spread"])


def _rest(conn, g, tipoff: str) -> tuple:
    """Days from each team's previous start to this start as the forecast knew it (its tipoff_at), and back-to-back."""
    from .providers.real import rest_days
    out = {}
    for s in ("home", "away"):
        days = rest_days(conn, g["season"], g[f"{s}_team_id"], tipoff)
        out[f"{s}_rest_days"] = days
        out[f"{s}_b2b"] = None if days is None else int(days < B2B_BELOW_DAYS)
    gaps = [s for s in ("home", "away") if out[f"{s}_rest_days"] is None]
    return _state(not gaps, ("no earlier game this season: " + ", ".join(gaps)) if gaps else None), out


def _lineup(conn, sport: str, g, rec: dict, matchup: str, cache: dict) -> tuple:
    from . import lineup
    if sport not in ("nba", "nhl"):
        return NA(f"no lineup facts are collected for {sport.upper()}"), {}
    key = (g["game_id"], rec["generated_at"])
    if key not in cache:
        cache[key] = lineup.facts(conn, sport, [g["game_id"]], rec["generated_at"]).get(g["game_id"])
    fact = cache[key]
    flags = lineup.review_flags(sport, {"market": rec["market"], "matchup": matchup, "pick_label": rec["pick_label"]}, fact)
    out = {"lineup_facts": json.dumps(fact, sort_keys=True), "lineup_flags": json.dumps(flags), "lineup_flag_count": len(flags)}
    if not fact or "error" in fact:
        return _state(False, "lineup facts unavailable" + (f" ({fact['error']})" if fact else "")), out
    if sport == "nba":     # no readable report (or no player minutes) by T: the facts are unknown, never "all clear"
        problems = sorted({p for p in [fact.get("report", {}).get("problem")] +
                           [fact.get(s, {}).get("problem") for s in ("home", "away")] if p})
        if problems:
            return _state(False, "injury report facts unknown at forecast time: " + ", ".join(problems)), out
    return _state(True), out


def _books3(sport: str, rec: dict, matchup: str, books_dir: Optional[Path]) -> tuple:
    from . import rundown_odds
    if sport not in rundown_odds.SPORT_IDS or rec["market"] not in rundown_odds.SPORT_MARKETS[sport].values():
        return NA("TheRundown does not price this market"), {}
    if rec["line_source"] == "model":
        return _state(False, "model line: no sportsbook line at forecast time"), {}
    at = rundown_odds.moment(rec["generated_at"])
    cache = rundown_odds.read_cache(at, books_dir)            # only responses fetched at or before T, at most 3 h old
    pick = {"market": rec["market"], "matchup": matchup, "tipoff": rec["tipoff_at"], "percentage_history": {"side": rec["side"]},
            "quote_check": {"line_source": rec["line_source"], "line_moved": False, "line_at_forecast": rec["line"]}}
    if not any(r["_sport"] == sport for r in cache["responses"]):
        return _state(False, "no three-book prices fetched for this sport in the 3 hours before the forecast"), {}
    c = rundown_odds.comparison(sport, pick, cache, at)
    if c["status"] != "available":
        return _state(False, f"three-book prices: {c['status']}"), {"books3": json.dumps(c, sort_keys=True)}
    return _state(True), {"books3": json.dumps(c, sort_keys=True)}


def game_picture(conn, sport: str, row, *, books_dir: Optional[Path] = None, lineup_cache: Optional[dict] = None) -> dict:
    """The scorecard columns of one game_forecasts row (status 'forecast'), every fact as of its generated_at."""
    from . import markets
    from .threshold_overview import displayed_percentage
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (row["game_id"],)).fetchone()
    home, away = _abbr(conn, g["home_team_id"]), _abbr(conn, g["away_team_id"])
    matchup = f"{away} @ {home}"
    side = _side_of(row["market"], row)
    rec = {k: row[k] for k in ("forecast_id", "market", "line", "line_source", "market_quote_id", "generated_at", "tipoff_at")}
    rec.update(side=side, pick_label=markets.side_labels(row["market"], row["line"], home, away)[("a", "mid", "b").index(side)])
    out = {"forecast_id": row["forecast_id"], "sport": sport, "source_table": "game_forecasts", "game_id": row["game_id"],
           "game_date": _eastern(g["tipoff_at"]), "matchup": matchup, "season_type": row["season_type"], "market": row["market"],
           "side": side, "pick_label": rec["pick_label"], "line": row["line"], "line_source": row["line_source"],
           "displayed_pct": displayed_percentage(row["confidence"]), "confidence": row["confidence"],
           "p_json": json.dumps([row["p_a"], row["p_mid"], row["p_b"]]), "source": row["source"],
           "model_version": row["model_version"], "generated_at": row["generated_at"], "tipoff_at": row["tipoff_at"]}
    detail = {}

    def part(group: str, fn: Callable):
        try:
            (state, note), fields = fn()
        except Exception as exc:                  # one fact failing never drops the row; it is said, never filled in
            (state, note), fields = ("missing", f"error: {type(exc).__name__}: {exc}"[:300]), {}
        out[f"{group}_state"], out[f"{group}_note"] = state, note
        return fields

    book = part("book", lambda: _book(conn, sport, g, rec))
    out.update(book)
    out.update(part("open", lambda: _open(conn, sport, g, rec, book)))
    others = part("card", lambda: (lambda st, o: (st, {"others": o}))(*_card(conn, g, rec, home, away))).get("others", [])
    out["card_others"] = json.dumps(others)
    out.update(part("spread", lambda: (lambda st, v: (st, {"spread_on_winner": v}))(*_spread_on_winner(rec, others))))
    out.update(part("rest", lambda: _rest(conn, g, rec["tipoff_at"])))
    out.update(part("lineup", lambda: _lineup(conn, sport, g, rec, matchup, lineup_cache if lineup_cache is not None else {})))
    out.update(part("books3", lambda: _books3(sport, rec, matchup, books_dir)))
    if row["market"] in ("team_total_home", "team_total_away"):
        try:
            from .implied_lines import implied_team_total
            detail["implied_team_total"] = implied_team_total(conn, row["game_id"], row["market"], row["generated_at"], sport)
        except Exception as exc:
            detail["implied_team_total_error"] = type(exc).__name__
    out["detail"] = json.dumps(detail, sort_keys=True)
    return out


def _eastern(ts: str) -> Optional[str]:
    from .providers.real import eastern_date
    return eastern_date(ts).isoformat() if ts else None


# ---- soccer: soccer_live_forecasts (90-minute 1X2) ------------------------------------------------------------------------
SOCCER_OUTCOMES = ("home", "draw", "away")


def _soccer_1x2(conn, match_id: str, at: str, *, earliest: bool) -> Optional[dict]:
    """One retrieval of a book's three offered 1X2 prices by `at` (latest, or the first), margin removed proportionally."""
    if not _has(conn, "soccer_odds_quotes"):
        return None
    order = "ASC" if earliest else "DESC"
    for prov, ts, kind in conn.execute(
            "SELECT provider, retrieved_at, price_kind FROM soccer_odds_quotes WHERE match_id=? AND market='1x2' AND offered=1 "
            f"AND retrieved_at<=? GROUP BY provider, retrieved_at, price_kind HAVING COUNT(DISTINCT side)=3 "
            f"ORDER BY retrieved_at {order}, price_kind = '{'open' if earliest else 'current'}' DESC, provider='DraftKings' DESC",
            (match_id, at)):
        rows = {r["side"]: r for r in conn.execute("SELECT * FROM soccer_odds_quotes WHERE match_id=? AND market='1x2' AND "
                                                    "offered=1 AND provider=? AND retrieved_at=? AND price_kind=?",
                                                    (match_id, prov, ts, kind))}
        inv = {s: 1 / rows[s]["decimal_odds"] for s in SOCCER_OUTCOMES}
        total = sum(inv.values())
        return {"provider": prov, "retrieved_at": ts, "label": kind, "devig": {s: inv[s] / total for s in SOCCER_OUTCOMES},
                "price": {s: rows[s]["american"] for s in SOCCER_OUTCOMES}}
    return None


def soccer_picture(conn, row) -> dict:
    from .threshold_overview import displayed_percentage
    m = conn.execute("SELECT m.*, ht.name AS home_name, at.name AS away_name FROM soccer_matches m JOIN teams ht ON "
                     "ht.team_id=m.home_team_id JOIN teams at ON at.team_id=m.away_team_id WHERE m.match_id=?",
                     (row["match_id"],)).fetchone()
    p = (row["p_home"], row["p_draw"], row["p_away"])
    i = max(range(3), key=lambda k: p[k])
    side = SOCCER_OUTCOMES[i]
    label = {"home": f"{m['home_name']} win", "draw": "Draw", "away": f"{m['away_name']} win"}[side]
    T = row["generated_at"]
    out = {"forecast_id": row["forecast_id"], "sport": "soccer", "source_table": "soccer_live_forecasts",
           "game_id": row["match_id"], "game_date": m["kickoff_at"][:10], "matchup": f"{m['home_name']} v {m['away_name']}",
           "season_type": row["competition_id"], "market": row["market"], "side": side, "pick_label": label, "line": None,
           "line_source": "none", "displayed_pct": displayed_percentage(p[i]), "confidence": p[i], "p_json": json.dumps(p),
           "source": "soccer_live", "model_version": row["model"], "generated_at": T, "tipoff_at": m["kickoff_at"]}
    try:
        q = _soccer_1x2(conn, row["match_id"], T, earliest=False)
        o = _soccer_1x2(conn, row["match_id"], T, earliest=True)
    except Exception as exc:
        q = o = None
        err = f"error: {type(exc).__name__}"
    else:
        err = None
    if q:
        out.update(book_state="present", book_note=None, book_provider=q["provider"], book_retrieved_at=q["retrieved_at"],
                   book_price=q["price"][side], book_devig=q["devig"][side])
    else:
        out.update(book_state="missing", book_note=err or "no 1X2 prices retrieved by the forecast time")
    if o:
        out.update(open_state="present", open_note=None, open_retrieved_at=o["retrieved_at"], open_label=o["label"],
                   open_price=o["price"][side], open_devig=o["devig"][side],
                   move_pts=round(100 * (q["devig"][side] - o["devig"][side]), 2) if q else None)
    else:
        out.update(open_state="missing", open_note=err or "no 1X2 prices retrieved by the forecast time")
    others = []
    if _has(conn, "soccer_market_forecasts"):
        others = [{"family": r["family"], "forecast_id": r["forecast_id"], "mean_home": r["mean_home"], "mean_away": r["mean_away"],
                   "generated_at": r["generated_at"]}
                  for r in conn.execute("SELECT * FROM soccer_market_forecasts f WHERE match_id=? AND generated_at<=? AND "
                                        "version=(SELECT MAX(version) FROM soccer_market_forecasts h WHERE h.match_id=f.match_id "
                                        "AND h.family=f.family AND h.generated_at<=?)", (row["match_id"], T, T))]
    out.update(card_state="present", card_note=None if others else "no goals/corners forecast of this match by the forecast time",
               card_others=json.dumps(others),
               spread_state="not_applicable", spread_note="soccer has no spread pick",
               rest_state="missing", rest_note="not collected for soccer (earlier matches in other competitions are not stored)",
               lineup_state="not_applicable", lineup_note="no lineup facts are collected for soccer",
               books3_state="not_applicable", books3_note="TheRundown prices are collected for NBA/NHL/NFL only",
               detail=json.dumps({"cutoff_at": row["cutoff_at"], "inputs_through": row["inputs_through"],
                                  "source_retrieved_at": row["source_retrieved_at"]}))
    return out


# ---- writer -------------------------------------------------------------------------------------------------------------
def _pending(conn, card, sport: str, since: Optional[str]) -> list:
    have = {r[0] for r in card.execute("SELECT forecast_id FROM scorecard")}
    if sport == "soccer":
        if not _has(conn, "soccer_live_forecasts"):
            return []
        sql, args = "SELECT * FROM soccer_live_forecasts", []
    else:
        if not _has(conn, "game_forecasts"):
            return []
        sql, args = "SELECT * FROM game_forecasts WHERE status='forecast'", []
    if since:
        sql += (" WHERE" if sport == "soccer" else " AND") + " generated_at >= ?"
        args.append(since)
    return [r for r in conn.execute(sql + " ORDER BY generated_at, forecast_id", args) if r["forecast_id"] not in have]


def _dratings(card, sport: str, captures: Optional[Path], now: str) -> int:
    """Append each DRatings capture of a scorecard row once (NBA/NHL winner forecasts only)."""
    if sport not in ("nba", "nhl") or captures is None or not Path(captures).is_file():
        return 0
    have = {r[0] for r in card.execute("SELECT forecast_id FROM scorecard_dratings")}
    rows = {r[0] for r in card.execute("SELECT forecast_id FROM scorecard WHERE market='winner'")}
    n = 0
    for line in Path(captures).read_text().splitlines():
        try:
            c = json.loads(line)
            fid = int(c["forecast_id"])
        except (ValueError, KeyError, TypeError):
            continue
        if c.get("sport") != sport or fid not in rows or fid in have:
            continue
        card.execute("INSERT INTO scorecard_dratings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fid, sport, c["captured_at"], c.get("lag_seconds"), int(bool(c.get("matched"))), c.get("dr_p_home"),
                      c.get("dr_p_away"), c.get("dr_proj_home"), c.get("dr_proj_away"), c.get("page"), c.get("page_sha256"), now))
        have.add(fid)
        n += 1
    return n


def sync(conn, card, sport: str, *, now: str, backfill: bool = False, books_dir: Optional[Path] = None,
         captures: Optional[Path] = None, picture: Optional[Callable] = None) -> dict:
    """Append a row for every recorded forecast not yet on the scorecard (job mode: made in the last JOB_LOOKBACK_HOURS),
    then any new DRatings captures. Never raises; a row that cannot be built is reported and tried again next run."""
    from datetime import datetime, timedelta
    out = {"sport": sport, "status": "ok", "written": 0, "dratings": 0, "errors": {}}
    try:
        since = None if backfill else (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") -
                                       timedelta(hours=JOB_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        todo = _pending(conn, card, sport, since)
    except Exception as exc:
        return {**out, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    build = picture or (soccer_picture if sport == "soccer" else
                        (lambda c, r, cache: game_picture(c, sport, r, books_dir=books_dir, lineup_cache=cache)))
    cache: dict = {}
    for r in todo:
        try:
            row = build(conn, r) if (picture is None and sport == "soccer") else build(conn, r, cache)
            row.update(scorecard_version=VERSION, written_by="backfill" if backfill else "job", written_at=now)
            cols = list(row)
            with card:
                card.execute(f"INSERT INTO scorecard ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [row[c] for c in cols])
            out["written"] += 1
        except Exception as exc:
            out["errors"][str(r["forecast_id"])] = f"{type(exc).__name__}: {exc}"[:300]
    try:
        with card:
            out["dratings"] = _dratings(card, sport, captures, now)
    except Exception as exc:
        out["errors"]["dratings"] = f"{type(exc).__name__}: {exc}"[:300]
    if out["errors"]:
        out["status"] = "partial"
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse
    from .db import utc_now
    ap = argparse.ArgumentParser(description="Append the full picture of every recorded pick to the scorecard (no forecast "
                                             "changes; the sport stores are only read).")
    ap.add_argument("cmd", choices=["sync"])
    ap.add_argument("--sport", choices=SPORTS, action="append", help="default: all four")
    ap.add_argument("--backfill", action="store_true", help="every past forecast, labelled written_by='backfill'")
    ap.add_argument("--explicit-release", action="store_true",
                    help="required to write next to protected live stores (the scheduled job, a release decision)")
    a = ap.parse_args(argv)
    now, results = utc_now(), []
    for sport in a.sport or SPORTS:
        store = config.db_path(sport)
        target = path(sport)
        if config.protected_path(target) and not a.explicit_release:
            results.append({"sport": sport, "status": "refused",
                            "error": f"{target} is in a protected live data folder; refusing without --explicit-release"})
            continue
        if not store.is_file():
            results.append({"sport": sport, "status": "skipped", "reason": "no store"})
            continue
        try:
            conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)      # the store is only read
            conn.row_factory = sqlite3.Row
            card = connect(target, create=True)
            results.append(sync(conn, card, sport, now=now, backfill=a.backfill, books_dir=config.data_dir() / "odds-books",
                                captures=config.data_dir() / "dratings" / "captures.jsonl"))
            card.close()
            conn.close()
        except Exception as exc:
            results.append({"sport": sport, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"as_of": now, "results": results}, indent=1))
    return 0 if all(r["status"] in ("ok", "skipped") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
