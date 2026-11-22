"""Which NCAAB games get a Telegram card: owner decision Nov 17, 2026 ("top 4 by gap, let's test it").

Once a day at 11:00 AM Eastern, every tracked NCAAB game tipping off at least two hours later that Eastern day is priced by
the shipped model as of that moment and compared with the latest saved sportsbook quote (no-vig) for the winner, spread and
total. A game's gap is the largest (app % - sportsbook %) of the model's own side in those markets. The four games with the
largest positive gap are saved, with every candidate and its numbers, append-only in the delivery ledger. Only those games
get a card, at the usual two-hour forecast time, from the official forecast.

UNTESTED: no NCAAB evidence shows that a large gap is an edge (DraftKings' 2026 lines beat the model); the saved candidates
let the selected games be compared later with the ones that were not sent. Nothing here changes a forecast, a grade or a record.
"""
import json
import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

RULE = "ncaab-top4-gap-v1"
TOP_N = 4
SELECT_AT = time(11, 0)                 # Eastern
LEAD = timedelta(hours=2)               # a game whose two-hour forecast window is already open cannot be selected
MARKETS = ("winner", "spread", "total")
MAX_QUOTE_AGE = timedelta(hours=6)      # an older saved quote is not a price as of the selection (review finding 2)
EASTERN = ZoneInfo("America/Toronto")

SCHEMA = '''
CREATE TABLE IF NOT EXISTS ncaab_card_selections (
  et_date TEXT PRIMARY KEY,              -- the Eastern day the selection covers
  rule TEXT NOT NULL,
  selected_at TEXT NOT NULL,             -- information time: model and quotes as of this moment, never later
  status TEXT NOT NULL CHECK (status IN ('ok', 'error')), error TEXT,
  model_version TEXT,
  selected TEXT NOT NULL,                -- JSON list of game ids, largest gap first
  candidates TEXT NOT NULL               -- JSON list: every game considered, its per-market numbers, or why it had none
);
CREATE TRIGGER IF NOT EXISTS ncaab_card_selections_no_update BEFORE UPDATE ON ncaab_card_selections
BEGIN SELECT RAISE(ABORT, 'ncaab_card_selections is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ncaab_card_selections_no_delete BEFORE DELETE ON ncaab_card_selections
BEGIN SELECT RAISE(ABORT, 'ncaab_card_selections is append-only'); END;
'''


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _market_entry(conn, dist, game_row, market: str, as_of: str) -> dict:
    from . import market_live, odds
    from .ncaab_app import LineNotModelled
    tip = game_row["tipoff_at"]
    if market == "winner":
        q = odds.winner_quote(conn, "ncaab", game_row["game_id"], as_of)
        if q is None or q["retrieved_at"] >= tip:
            return {"market": market, "reason": "no moneyline saved"}
        if _parse(q["retrieved_at"]) < _parse(as_of) - MAX_QUOTE_AGE:
            return {"market": market, "reason": "latest moneyline older than 6 hours"}
        pa, _, pb = dist.winner()
        book_a, line, qid, provider, at = q["p_home"], None, q["quote_id"], q["provider"], q["retrieved_at"]
    else:
        q = market_live.latest_quote(conn, game_row["game_id"], market, as_of)
        if q is None or q["retrieved_at"] >= tip:
            return {"market": market, "reason": "no quote saved"}
        if _parse(q["retrieved_at"]) < _parse(as_of) - MAX_QUOTE_AGE:
            return {"market": market, "reason": "latest quote older than 6 hours"}
        try:
            pa, _, pb = dist.market(market, q["line"])
        except LineNotModelled:
            return {"market": market, "reason": "the model cannot price the sportsbook's line"}
        book_a, line, qid, provider, at = q["p_a_devig"], q["line"], q["quote_id"], q["provider"], q["retrieved_at"]
    side = "a" if pa >= pb else "b"
    app, book = (pa, book_a) if side == "a" else (pb, 1 - book_a)
    return {"market": market, "side": side, "line": line, "app": round(app, 6), "book": round(book, 6),
            "gap": round((app - book) * 100, 1), "quote_id": qid, "provider": provider, "quote_retrieved_at": at}


def compute(source: sqlite3.Connection, now: datetime) -> dict:
    """The selection for the Eastern day of `now`, made with information available at `now` only (results through the model's
    own cutoff, quotes retrieved at or before `now`). Pure: writes nothing."""
    from . import live
    as_of = _ts(now)
    day = now.astimezone(EASTERN).date()
    eng = live.engine(source)
    if eng.sport != "ncaab":
        raise ValueError("The NCAAB card selection needs an NCAAB store")
    _, model = eng.primary(source)
    model.as_of = as_of                 # the model reads nothing newer than the selection time
    types = eng.eligible_types
    rows = source.execute(
        f"SELECT * FROM games WHERE status = 'scheduled' AND tipoff_at >= ? AND season_type IN ({', '.join('?' * len(types))}) "
        "ORDER BY tipoff_at, game_id", (_ts(now + LEAD), *types)).fetchall()
    candidates = []
    for g in rows:
        if _parse(g["tipoff_at"]).astimezone(EASTERN).date() != day:
            continue
        entry = {"game_id": g["game_id"], "tipoff_at": g["tipoff_at"]}
        try:
            dist = model.distribution(eng.pregame(g))
            entry["markets"] = [_market_entry(source, dist, g, m, as_of) for m in MARKETS]
        except Exception as exc:        # one game the model cannot price is reported, never a reason to card every game
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            entry["markets"] = []
        priced = [m for m in entry["markets"] if m.get("gap") is not None]
        best = max(priced, key=lambda m: (m["gap"], -MARKETS.index(m["market"])), default=None)
        entry["gap"] = best["gap"] if best else None
        entry["best_market"] = best["market"] if best else None
        candidates.append(entry)
    ranked = sorted((c for c in candidates if c["gap"] is not None and c["gap"] > 0),
                    key=lambda c: (-c["gap"], c["tipoff_at"], c["game_id"]))
    return {"et_date": day.isoformat(), "rule": RULE, "selected_at": as_of, "status": "ok", "error": None,
            "model_version": getattr(model, "version", None), "selected": [c["game_id"] for c in ranked[:TOP_N]],
            "candidates": candidates}


def _row(conn, day: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM ncaab_card_selections WHERE et_date = ?", (day,)).fetchone()
    if r is None:
        return None
    out = dict(r)
    out["selected"], out["candidates"] = json.loads(out["selected"]), json.loads(out["candidates"])
    return out


def ensure(ledger: sqlite3.Connection, source: sqlite3.Connection, now: datetime) -> Optional[dict]:
    """Today's saved selection; made and saved once, at the first call at or after 11:00 AM Eastern. None before then (no
    NCAAB card goes out). A failed selection is saved as an error with no games: that day gets no NCAAB cards, never all of them."""
    ledger.executescript(SCHEMA)
    local = now.astimezone(EASTERN)
    day = local.date().isoformat()
    saved = _row(ledger, day)
    if saved is not None or local.time() < SELECT_AT:
        return saved
    try:
        sel = compute(source, now)
    except Exception as exc:
        sel = {"et_date": day, "rule": RULE, "selected_at": _ts(now), "status": "error",
               "error": f"{type(exc).__name__}: {exc}"[:300], "model_version": None, "selected": [], "candidates": []}
    # INSERT OR IGNORE only (an OR REPLACE would delete a row without firing the delete trigger)
    ledger.execute("INSERT OR IGNORE INTO ncaab_card_selections (et_date, rule, selected_at, status, error, model_version, "
                   "selected, candidates) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (sel["et_date"], sel["rule"], sel["selected_at"], sel["status"], sel["error"], sel["model_version"],
                    json.dumps(sel["selected"]), json.dumps(sel["candidates"], sort_keys=True)))
    return _row(ledger, day)
