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

Writer: `python -m app.scorecard sync` (its own LaunchAgent, every 120 s), a separate process from the forecast jobs, so
a scorecard failure or a slow run can never stop, delay or alter a forecast. Idempotent on forecast_id; never rewrites.
"""
import json
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from . import config, odds

VERSION = "scorecard-v1"
SPORTS = ("nba", "nhl", "nfl", "ncaab", "soccer")
STATES = ("present", "missing", "not_applicable")
GROUPS = ("book", "open", "card", "spread", "rest", "lineup", "books3")
B2B_BELOW_DAYS = 1.5           # the models' back-to-back feature (app/models/logistic.py: rest < 1.5 days)
JOB_LOOKBACK_HOURS = 12        # `sync` without --backfill looks at forecasts made this recently

_STATE_COLS = "".join(f"  {g}_state TEXT NOT NULL CHECK ({g}_state IN ('present','missing','not_applicable')),\n"
                      f"  {g}_note TEXT,\n" for g in GROUPS)
PICK_COLUMNS = f"""  sport TEXT NOT NULL, source_table TEXT NOT NULL, game_id TEXT NOT NULL, game_date TEXT, matchup TEXT,
  season_type TEXT, market TEXT NOT NULL, side TEXT NOT NULL, pick_label TEXT, line REAL, line_source TEXT,
  displayed_pct TEXT NOT NULL, confidence REAL NOT NULL, p_json TEXT NOT NULL, source TEXT, model_version TEXT,
  generated_at TEXT NOT NULL, tipoff_at TEXT NOT NULL,
{_STATE_COLS}  book_provider TEXT, book_retrieved_at TEXT, book_line REAL, book_price INTEGER, book_devig REAL,
  open_retrieved_at TEXT, open_label TEXT, open_line REAL, open_price INTEGER, open_devig REAL,
  move_base_retrieved_at TEXT, move_base_devig REAL, move_pts REAL, move_line REAL,
  card_others TEXT, spread_on_winner INTEGER,
  home_rest_days REAL, away_rest_days REAL, home_b2b INTEGER, away_b2b INTEGER,
  lineup_facts TEXT, lineup_flags TEXT, lineup_flag_count INTEGER,
  books3 TEXT, detail TEXT,
  scorecard_version TEXT NOT NULL, written_by TEXT NOT NULL CHECK (written_by IN ('job','backfill')), written_at TEXT NOT NULL,
  CHECK (book_retrieved_at IS NULL OR book_retrieved_at <= generated_at),
  CHECK (open_retrieved_at IS NULL OR open_retrieved_at <= generated_at),
  CHECK (move_base_retrieved_at IS NULL OR move_base_retrieved_at <= generated_at),
  CHECK (generated_at <= written_at)
"""
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS scorecard (
  forecast_id INTEGER PRIMARY KEY,
{PICK_COLUMNS});
-- soccer goals/corners: one row per served market and line of a stored forecast (the side the forecast favours)
CREATE TABLE IF NOT EXISTS scorecard_soccer_markets (
  forecast_id INTEGER NOT NULL, selection_key TEXT NOT NULL,
{PICK_COLUMNS},
  PRIMARY KEY (forecast_id, selection_key));
CREATE TRIGGER IF NOT EXISTS scorecard_soccer_markets_no_update BEFORE UPDATE ON scorecard_soccer_markets
BEGIN SELECT RAISE(ABORT, 'scorecard_soccer_markets is append-only'); END;
CREATE TRIGGER IF NOT EXISTS scorecard_soccer_markets_no_delete BEFORE DELETE ON scorecard_soccer_markets
BEGIN SELECT RAISE(ABORT, 'scorecard_soccer_markets is append-only'); END;
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
    markets = odds.forecast_quote, the ONE forecast-time rule (same game, market and exact line, latest retrieved <= T, before
    the start, deterministic book tie-break) that the card, ticket and sportsbook comparison use too."""
    from . import coverage, odds
    side, market = rec["side"], rec["market"]
    if market == "winner":
        q = odds.winner_quote(conn, sport, g["game_id"], rec["generated_at"])
        if q is None:
            return _state(False, "no moneyline retrieved by the forecast time"), {}
        p = q["p_home"] if side == "a" else 1 - q["p_home"]
        return _state(True), {"book_provider": q["provider"], "book_retrieved_at": q["retrieved_at"], "book_line": None,
                              "book_price": q["price_home"] if side == "a" else q["price_away"], "book_devig": p}
    if market != "reg3" and rec["line_source"] != "bookmaker":
        return _state(False, "model line (no bookmaker line at forecast time)"), {}
    q = odds.forecast_quote(conn, g["game_id"], market, rec["line"], rec["generated_at"], g["tipoff_at"],
                            recorded_id=rec.get("market_quote_id"), need_mid=market == "reg3")
    if q is None:
        return _state(False, "no regulation three-way bookmaker quotes are collected" if market == "reg3"
                      else "no sportsbook quote at the forecast's line by the forecast time"), {}
    pa = (q["p_a_devig"], q["p_mid_devig"], q["p_b_devig"]) if market == "reg3" else q["p_a_devig"]
    price = {"a": q["price_a"], "mid": q["price_mid"], "b": q["price_b"]}[side]
    return _state(True), {"book_provider": q["provider"], "book_retrieved_at": q["retrieved_at"], "book_line": q["line"],
                          "book_price": price, "book_devig": _picked(market, side, pa if market != "reg3" else None,
                                                                     pa if market == "reg3" else None)}


def _first_quote(conn, sport: str, game_id: str, market: str, at: str, *, provider: Optional[str] = None,
                 line: Optional[float] = None) -> Optional[dict]:
    """The first quote of this game and market retrieved by `at` (the book's own 'open' price of that retrieval first), as
    {retrieved_at, label, line, price: {a, mid, b}, devig: (a, mid, b)}; only `provider`'s and only at `line` when given."""
    from . import coverage, odds
    if market == "winner":
        q = odds.winner_quote(conn, sport, game_id, at, earliest=True, provider=provider)
        return None if q is None else {"retrieved_at": q["retrieved_at"], "label": q["price_label"], "line": None,
                                       "provider": q["provider"], "price": {"a": q["price_home"], "b": q["price_away"]},
                                       "devig": (q["p_home"], None, 1 - q["p_home"])}
    if not _has(conn, "market_quotes"):
        return None
    only = (" AND " + odds.same_provider() if provider is not None else "") + \
        (" AND abs(line - ?) < 1e-9" if line is not None else "")
    args = (game_id, market, at) + ((provider,) if provider is not None else ()) + ((line,) if line is not None else ())
    q = conn.execute(f"SELECT * FROM market_quotes WHERE game_id=? AND market=? AND retrieved_at<=?{only} "
                     "ORDER BY retrieved_at, price_label='open' DESC, quote_id LIMIT 1", args).fetchone()
    return None if q is None else {"retrieved_at": q["retrieved_at"], "label": q["price_label"], "line": q["line"],
                                   "provider": q["provider"], "price": {"a": q["price_a"], "mid": q["price_mid"], "b": q["price_b"]},
                                   "devig": (q["p_a_devig"], q["p_mid_devig"], q["p_b_devig"])}


def _side_p(market: str, side: str, devig: tuple) -> Optional[float]:
    return _picked(market, side, devig[0], devig)


def _open(conn, sport: str, g, rec: dict, book: dict, *, provider: Optional[str] = None) -> tuple:
    """Opening quote and the move to the pick's quote, as of T (review M2, Nov 11):
    - open_*: the first quote of this game and market retrieved by T from the pick's sportsbook (`provider`, else the
      provider of the pick's quote, else any), at whatever line it had; open_line is that line.
    - move_pts: the pick side's de-vigged % at the pick's quote minus the same sportsbook's first quote AT THE PICK'S LINE
      (points; + = toward the pick). Never across lines. 0 is "no move".
    - move_line: how the pick side's own line changed since open_line (spread: the picked team's handicap; totals: the
      total), only when the pick has a sportsbook line."""
    market, side, at = rec["market"], rec["side"], rec["generated_at"]
    prov = provider if provider is not None else book.get("book_provider")
    q = _first_quote(conn, sport, g["game_id"], market, at, provider=prov)
    if q is None:
        return _state(False, "no quote of this market retrieved by the forecast time" if market != "reg3"
                      else "no regulation three-way bookmaker quotes are collected"), {}
    out = {"open_retrieved_at": q["retrieved_at"], "open_label": q["label"], "open_line": q["line"],
           "open_price": q["price"].get(side), "open_devig": _side_p(market, side, q["devig"])}
    if book.get("book_devig") is None:
        return _state(True, "no move: the pick has no sportsbook quote at its own line"), out
    base = _first_quote(conn, sport, g["game_id"], market, at, provider=prov, line=book.get("book_line"))
    if base is not None:
        out.update(move_base_retrieved_at=base["retrieved_at"], move_base_devig=_side_p(market, side, base["devig"]))
        out["move_pts"] = round(100 * (book["book_devig"] - out["move_base_devig"]), 2)
    if q["line"] is not None and book.get("book_line") is not None:
        change = book["book_line"] - q["line"]
        out["move_line"] = -change if (market == "spread" and side == "b") else change
    return _state(True), out


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


def _rest(conn, g, tipoff: str, sport: str = "") -> tuple:
    """Days from each team's previous start to this start as the forecast knew it (its tipoff_at), and back-to-back.
    NFL: no back-to-back (teams never play on consecutive days): b2b stays NULL, rest days are kept."""
    from .providers.real import rest_days
    out = {}
    for s in ("home", "away"):
        days = rest_days(conn, g["season"], g[f"{s}_team_id"], tipoff)
        out[f"{s}_rest_days"] = days
        out[f"{s}_b2b"] = None if days is None or sport == "nfl" else int(days < B2B_BELOW_DAYS)
    gaps = [s for s in ("home", "away") if out[f"{s}_rest_days"] is None]
    return _state(not gaps, ("no earlier game this season: " + ", ".join(gaps)) if gaps else None), out


def _lineup(conn, sport: str, g, rec: dict, matchup: str, cache: dict) -> tuple:
    from . import lineup
    if sport not in ("nba", "nhl"):
        return NA(f"no lineup facts are collected for {sport.upper()}"), {}
    key = (g["game_id"], rec["generated_at"])
    if key not in cache:
        cache[key] = lineup.facts(conn, sport, [g["game_id"]], rec["generated_at"], box_as_of=True).get(g["game_id"])
    fact = cache[key]
    flags = lineup.review_flags(sport, {"market": rec["market"], "matchup": matchup, "pick_label": rec["pick_label"]}, fact)
    out = {"lineup_facts": json.dumps(fact, sort_keys=True), "lineup_flags": json.dumps(flags), "lineup_flag_count": len(flags)}
    if not fact or "error" in fact:
        return _state(False, "lineup facts unavailable" + (f" ({fact['error']})" if fact else "")), out
    if sport == "nba":     # no readable report (or no player minutes) by T: the facts are unknown, never "all clear"
        problems = sorted({p for p in [fact.get("report", {}).get("problem")] +
                           [fact.get(s, {}).get("problem") for s in ("home", "away")] if p})
        if problems:
            return _state(False, "lineup facts unknown at forecast time: " + ", ".join(problems)), out
    return _state(True), out


def _books3(sport: str, rec: dict, matchup: str, books_dir: Optional[Path], caches: Optional[dict] = None) -> tuple:
    from . import rundown_odds
    if sport not in rundown_odds.KNOWN_SPORT_IDS or rec["market"] not in rundown_odds.SPORT_MARKETS[sport].values():
        return NA("TheRundown does not price this market"), {}
    if rec["line_source"] == "model":
        return _state(False, "model line: no sportsbook line at forecast time"), {}
    at = rundown_odds.moment(rec["generated_at"])
    caches = {} if caches is None else caches                  # one directory read per forecast time per run
    if rec["generated_at"] not in caches:
        caches[rec["generated_at"]] = rundown_odds.read_cache(at, books_dir)   # fetched <= T, at most 3 h old
    cache = caches[rec["generated_at"]]
    pick = {"market": rec["market"], "matchup": matchup, "tipoff": rec["tipoff_at"], "percentage_history": {"side": rec["side"]},
            "quote_check": {"line_source": rec["line_source"], "line_moved": False, "line_at_forecast": rec["line"]}}
    if not any(r["_sport"] == sport for r in cache["responses"]):
        return _state(False, "no three-book prices fetched for this sport in the 3 hours before the forecast"), {}
    c = rundown_odds.comparison(sport, pick, cache, at)
    if c["status"] != "available":
        return _state(False, f"three-book prices: {c['status']}"), {"books3": json.dumps(c, sort_keys=True)}
    return _state(True), {"books3": json.dumps(c, sort_keys=True)}


def game_picture(conn, sport: str, row, *, books_dir: Optional[Path] = None, lineup_cache: Optional[dict] = None,
                 books_cache: Optional[dict] = None) -> dict:
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
    out.update(part("rest", lambda: _rest(conn, g, rec["tipoff_at"], sport)))
    out.update(part("lineup", lambda: _lineup(conn, sport, g, rec, matchup, lineup_cache if lineup_cache is not None else {})))
    out.update(part("books3", lambda: _books3(sport, rec, matchup, books_dir, books_cache)))
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


def _soccer_prices(conn, match_id: str, at: str, market: str, sides: tuple, *, earliest: bool,
                   provider: Optional[str] = None, line: Optional[float] = None, any_line: bool = False) -> Optional[dict]:
    """One retrieval of a book's offered prices for all `sides` of a soccer market by `at` (latest, or the first; the
    book's 'open' price first when earliest), margin removed proportionally. 1X2: no line; total goals: at `line` unless
    `any_line`. Only `provider` when given (DraftKings first otherwise)."""
    if not _has(conn, "soccer_odds_quotes"):
        return None
    order = "ASC" if earliest else "DESC"
    where = "match_id=? AND market=? AND offered=1 AND retrieved_at<=?"
    args = [match_id, market, at]
    if provider is not None:
        where += " AND " + odds.same_provider()
        args.append(provider)
    if line is not None and not any_line:
        where += " AND abs(line - ?) < 1e-9"
        args.append(line)
    for prov, ts, kind, ln in conn.execute(
            f"SELECT provider, retrieved_at, price_kind, line FROM soccer_odds_quotes WHERE {where} "
            f"GROUP BY provider, retrieved_at, price_kind, line HAVING COUNT(DISTINCT side)={len(sides)} "
            f"ORDER BY retrieved_at {order}, price_kind = '{'open' if earliest else 'current'}' DESC, "
            "lower(replace(provider, ' ', ''))='draftkings' DESC",
            args):
        rows = {r["side"]: r for r in conn.execute(
            "SELECT * FROM soccer_odds_quotes WHERE match_id=? AND market=? AND offered=1 AND provider=? AND retrieved_at=? "
            "AND price_kind=? AND line IS ?", (match_id, market, prov, ts, kind, ln))}
        if set(rows) != set(sides):
            continue
        inv = {x: 1 / rows[x]["decimal_odds"] for x in sides}
        total = sum(inv.values())
        return {"provider": prov, "retrieved_at": ts, "label": kind, "line": ln,
                "devig": {x: inv[x] / total for x in sides}, "price": {x: rows[x]["american"] for x in sides}}
    return None


def _soccer_book_open(conn, match_id: str, T: str, market: str, sides: tuple, side: str, line: Optional[float],
                      kickoff_at: Optional[str] = None) -> dict:
    """Sportsbook, opening and move columns of one soccer pick (review M2: the move only at the pick's own line, same book).
    1X2 (owner request Nov 17): the price the card shows, soccer_book.latest_1x2 (latest offered row of each side as of T,
    before kickoff); its per-side retrieval times go to `book_side_retrieved_at`. Before this, the scorecard took the last
    retrieval holding all three sides, stale against the card when only some sides had moved."""
    from . import soccer_book
    out = {}
    try:
        if market == "1x2":      # no kickoff time: no quote, never the older all-sides rule (review LOW, Nov 17)
            q = soccer_book.latest_1x2(conn, match_id, T, kickoff_at) if kickoff_at else None
            if q is not None:
                q = dict(q, label="current", line=None)
                out["book_side_retrieved_at"] = q["side_retrieved_at"]
        else:
            q = _soccer_prices(conn, match_id, T, market, sides, earliest=False, line=line)
        o = q and _soccer_prices(conn, match_id, T, market, sides, earliest=True, provider=q["provider"], any_line=True)
        base = q and _soccer_prices(conn, match_id, T, market, sides, earliest=True, provider=q["provider"], line=line)
        err = None
    except Exception as exc:
        q = o = base = None
        err = f"error: {type(exc).__name__}"
    what = "1X2" if market == "1x2" else "goal-line"
    if not q:
        out.update(book_state="missing", book_note=err or f"no {what} prices retrieved by the forecast time",
                   open_state="missing", open_note=err or f"no {what} prices retrieved by the forecast time")
        return out
    out.update(book_state="present", book_note=None, book_provider=q["provider"], book_retrieved_at=q["retrieved_at"],
               book_line=q["line"], book_price=q["price"][side], book_devig=q["devig"][side])
    out.update(open_state="present", open_note=None, open_retrieved_at=o["retrieved_at"], open_label=o["label"],
               open_line=o["line"], open_price=o["price"][side], open_devig=o["devig"][side])
    if base:
        out.update(move_base_retrieved_at=base["retrieved_at"], move_base_devig=base["devig"][side],
                   move_pts=round(100 * (q["devig"][side] - base["devig"][side]), 2))
    if o["line"] is not None and q["line"] is not None:
        out["move_line"] = q["line"] - o["line"]
    return out


def _soccer_match(conn, match_id: str):
    return conn.execute("SELECT m.*, ht.name AS home_name, at.name AS away_name FROM soccer_matches m JOIN teams ht ON "
                        "ht.team_id=m.home_team_id JOIN teams at ON at.team_id=m.away_team_id WHERE m.match_id=?",
                        (match_id,)).fetchone()


def _family_markets(family: str) -> tuple:
    from . import soccer_markets_live as sml
    try:
        served = sml.state(family).get("markets")
    except Exception:
        served = None
    return tuple(served or sml.FAMILY_MARKETS[family])


def _favoured(selections: list) -> list:
    """Per market and line (model fair line, or a book's offered line): the side the forecast favours (higher win share;
    ties: the first side listed)."""
    groups: dict = {}
    for sel in selections:
        line = sel["line"]
        if sel["market"] == "corners_handicap" and sel["side"] == "away" and line is not None:
            line = -line
        key = f"{sel['market']}|{sel['source']}|{sel.get('provider') or ''}|{'' if line is None else line:g}" \
            if line is not None else f"{sel['market']}|{sel['source']}|{sel.get('provider') or ''}|"
        if key not in groups or sel["win_share"] > groups[key]["win_share"]:
            groups[key] = sel
    return list(groups.items())


def _soccer_card(conn, match_id: str, T: str, *, own: tuple) -> list:
    """Every soccer prediction on this match as shown at T (the latest 1X2 forecast and its double chance / draw no bet,
    and the latest goals/corners forecast of each family, all generated <= T, with the book lines retrieved <= T), except
    the pick itself (`own` = (forecast table, forecast_id, market, side, line))."""
    from . import soccer_markets_live as sml
    from .threshold_overview import displayed_percentage
    out = []
    if _has(conn, "soccer_live_forecasts"):
        f = conn.execute("SELECT * FROM soccer_live_forecasts WHERE match_id=? AND generated_at<=? ORDER BY version DESC "
                         "LIMIT 1", (match_id, T)).fetchone()
        if f is not None:
            p = (f["p_home"], f["p_draw"], f["p_away"])
            for side, x in zip(SOCCER_OUTCOMES, p):
                out.append({"table": "soccer_live_forecasts", "forecast_id": f["forecast_id"], "market": "1x2_regulation",
                            "side": side, "line": None, "displayed_pct": displayed_percentage(x), "source": "model"})
            for sel in sml.derived_selections(p):
                out.append({"table": "soccer_live_forecasts", "forecast_id": f["forecast_id"], "market": sel["market"],
                            "side": sel["side"], "line": None, "displayed_pct": displayed_percentage(sel["win_share"]),
                            "source": "model"})
    if _has(conn, "soccer_market_forecasts"):
        quotes = sml.quotes(conn, match_id, T)
        for r in conn.execute("SELECT * FROM soccer_market_forecasts f WHERE match_id=? AND generated_at<=? AND "
                              "version=(SELECT MAX(version) FROM soccer_market_forecasts h WHERE h.match_id=f.match_id "
                              "AND h.family=f.family AND h.generated_at<=?) ORDER BY family", (match_id, T, T)):
            for sel in sml.market_selections(json.loads(r["joint_json"]), r["family"], _family_markets(r["family"]), quotes):
                out.append({"table": "soccer_market_forecasts", "forecast_id": r["forecast_id"], "market": sel["market"],
                            "side": sel["side"], "line": sel["line"], "displayed_pct": displayed_percentage(sel["win_share"]),
                            "source": sel["source"], "provider": sel.get("provider")})
    return [o for o in out if (o["table"], o["forecast_id"], o["market"], o["side"], o["line"]) != own]


_SOCCER_FIXED = dict(spread_state="not_applicable", spread_note="soccer has no spread pick",
                     rest_state="missing", rest_note="not collected for soccer (earlier matches in other competitions are not stored)",
                     lineup_state="missing", lineup_note="not collected for soccer (no lineup source)",
                     books3_state="missing", books3_note="not collected for soccer (TheRundown prices: NBA/NHL/NFL only)")


def soccer_picture(conn, row) -> dict:
    """The 90-minute 1X2 pick (soccer_live_forecasts): the most likely outcome."""
    from .threshold_overview import displayed_percentage
    m = _soccer_match(conn, row["match_id"])
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
    out.update(_soccer_book_open(conn, row["match_id"], T, "1x2", SOCCER_OUTCOMES, side, None, m["kickoff_at"]))
    sides_at = out.pop("book_side_retrieved_at", None)
    try:
        others = _soccer_card(conn, row["match_id"], T, own=("soccer_live_forecasts", row["forecast_id"], row["market"], side, None))
        out.update(card_state="present", card_note=None, card_others=json.dumps(others))
    except Exception as exc:
        out.update(card_state="missing", card_note=f"error: {type(exc).__name__}: {exc}"[:300], card_others="[]")
    out.update(_SOCCER_FIXED, detail=json.dumps({"cutoff_at": row["cutoff_at"], "inputs_through": row["inputs_through"],
                                                 "source_retrieved_at": row["source_retrieved_at"],
                                                 **({"book_side_retrieved_at": sides_at} if sides_at else {})}))
    return out


def soccer_market_pictures(conn, row) -> list:
    """One row per served market and line of a goals/corners forecast (soccer_market_forecasts): the favoured side, its
    win share as displayed, the book's line retrieved <= T where one exists, and the other predictions on the match."""
    from . import soccer_markets_live as sml
    from .threshold_overview import displayed_percentage
    m = _soccer_match(conn, row["match_id"])
    T = row["generated_at"]
    sels = sml.market_selections(json.loads(row["joint_json"]), row["family"], _family_markets(row["family"]),
                                 sml.quotes(conn, row["match_id"], T))
    out = []
    for key, sel in _favoured(sels):
        r = {"forecast_id": row["forecast_id"], "selection_key": key, "sport": "soccer", "source_table": "soccer_market_forecasts",
             "game_id": row["match_id"], "game_date": m["kickoff_at"][:10], "matchup": f"{m['home_name']} v {m['away_name']}",
             "season_type": row["competition_id"], "market": sel["market"], "side": sel["side"],
             "pick_label": f"{sel['market']} {sel['side']}" + ("" if sel["line"] is None else f" {sel['line']:g}"),
             "line": sel["line"], "line_source": "bookmaker" if sel["source"] == "bookmaker" else "model",
             "displayed_pct": displayed_percentage(sel["win_share"]), "confidence": sel["win_share"],
             "p_json": json.dumps(sel["probabilities"]), "source": row["family"], "model_version": row["model"],
             "generated_at": T, "tipoff_at": m["kickoff_at"]}
        if sel["source"] == "bookmaker" and sel["market"] == "goals_total":
            r.update(_soccer_book_open(conn, row["match_id"], T, "total_goals", ("over", "under"), sel["side"], sel["line"]))
        else:
            why = "model line: no bookmaker price for this line" if sel["market"] in ("goals_total",) else \
                "no bookmaker prices for this market in the free source"
            r.update(book_state="missing", book_note=why, open_state="missing", open_note=why)
        try:
            others = _soccer_card(conn, row["match_id"], T,
                                  own=("soccer_market_forecasts", row["forecast_id"], sel["market"], sel["side"], sel["line"]))
            r.update(card_state="present", card_note=None, card_others=json.dumps(others))
        except Exception as exc:
            r.update(card_state="missing", card_note=f"error: {type(exc).__name__}: {exc}"[:300], card_others="[]")
        r.update(_SOCCER_FIXED, detail=json.dumps({"cutoff_at": row["cutoff_at"], "inputs_through": row["inputs_through"],
                                                   "source_retrieved_at": row["source_retrieved_at"], "selection": {
                                                       k: sel.get(k) for k in ("provider", "american", "retrieved_at",
                                                                               "market_probability", "fair_decimal")}}))
        out.append(r)
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
                        (lambda c, r, cache: game_picture(c, sport, r, books_dir=books_dir, lineup_cache=cache,
                                                         books_cache=cache.setdefault("_books3", {}))))
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
    if sport == "soccer" and picture is None and _has(conn, "soccer_market_forecasts"):
        out["market_rows"] = 0
        have = {r[0] for r in card.execute("SELECT DISTINCT forecast_id FROM scorecard_soccer_markets")}
        sql = "SELECT * FROM soccer_market_forecasts" + ("" if since is None else " WHERE generated_at >= ?")
        for r in conn.execute(sql + " ORDER BY generated_at, forecast_id", () if since is None else (since,)):
            if r["forecast_id"] in have:
                continue
            try:
                rows = soccer_market_pictures(conn, r)
                with card:
                    for row in rows:
                        row.update(scorecard_version=VERSION, written_by="backfill" if backfill else "job", written_at=now)
                        cols = list(row)
                        card.execute(f"INSERT INTO scorecard_soccer_markets ({','.join(cols)}) VALUES "
                                     f"({','.join('?' * len(cols))})", [row[c] for c in cols])
                out["market_rows"] += len(rows)
            except Exception as exc:
                out["errors"][f"market:{r['forecast_id']}"] = f"{type(exc).__name__}: {exc}"[:300]
    if out["errors"]:
        out["status"] = "partial"
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse
    from .db import utc_now
    ap = argparse.ArgumentParser(description="Append the full picture of every recorded pick to the scorecard (no forecast "
                                             "changes; the sport stores are only read).")
    ap.add_argument("cmd", choices=["sync"])
    ap.add_argument("--sport", choices=SPORTS, action="append", help="default: every sport")
    ap.add_argument("--backfill", action="store_true", help="every past forecast, labelled written_by='backfill'")
    ap.add_argument("--explicit-release", action="store_true",
                    help="required to write next to protected live stores (the scheduled job, a release decision)")
    a = ap.parse_args(argv)
    from . import release
    if config.protected_data_dir(config.data_dir()):
        # Only scorecard files are written, never a store: the checkout checks apply (as app.dratings_capture and
        # app.rundown_odds), the store-schema check does not.
        if not release.enabled() or release._checkout(release.code_root().resolve())[1]:
            raise SystemExit("release guard: the scorecard writer requires the clean release checkout")
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
