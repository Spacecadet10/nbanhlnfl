"""Soccer: the app's pick beside the saved sportsbook quote for the SAME match, market, side and line (display only; never a
model input, a ticket price or a stored value). Returns schemas.SportsbookComparison-shaped dicts.

- 1X2: the forecast's picked outcome (most likely of home/draw/away) against DraftKings' three saved 1X2 prices as of the
  forecast's own generated_at (latest row per side retrieved at or before it, before kickoff), margin removed proportionally
  (implied_lines.match_result_probabilities). A side withdrawn or never saved = no_quote.
- Total goals: a selection against the over/under quotes saved at exactly its line, de-vigged two-way (odds.devig_two_way).
  A quote at another line is never used. The app's probability excludes the stake-returned (push) share, as a two-way
  de-vig does: (win + half_win/2) / (win + half_win/2 + loss + half_loss/2).
Handicap quotes are not compared: one stored handicap price alone cannot be matched to a model line.
"""
from typing import Optional

from . import implied_lines
from .odds import devig_two_way, provider_key, same_provider, same_provider_columns

PROVIDER = "DraftKings"


def _gap(app: float, book: float) -> float:
    return round(round(app * 100, 1) - round(book * 100, 1), 1)


def _fmt(american: int) -> str:
    return f"+{american}" if american > 0 else str(american)


def _empty(status: str, app: float, reason: str, **extra) -> dict:
    return {"status": status, "app_probability": app, "book_probability": None, "gap_pp": None, "provider": None, "price": None,
            "line": None, "quote_id": None, "retrieved_at": None, "reason": reason, **extra}


def one_x_two(conn, f: dict) -> dict:
    """Comparison for one live forecast dict (soccer_live.forecast_dict: match_id, kickoff_at, generated_at, most_likely, probabilities)."""
    pick = f["most_likely"]
    app = float(f["probabilities"][pick])
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='soccer_odds_quotes'").fetchone() is None:
        return _empty("no_quote", app, "No sportsbook quotes are stored.")
    at = f["generated_at"]
    q = latest_1x2(conn, f["match_id"], f["generated_at"], f["kickoff_at"])
    if q is None:
        return _empty("no_quote", app, "No complete 1X2 sportsbook quote was saved at or before this forecast.")
    book = q["devig"][pick]
    return {"status": "exact", "app_probability": app, "book_probability": book, "gap_pp": _gap(app, book), "provider": PROVIDER,
            "price": "/".join(_fmt(q["price"][s]) for s in ("home", "draw", "away")), "line": None,
            "quote_id": q["quote_id"], "retrieved_at": q["retrieved_at"], "reason": None}


def latest_1x2(conn, match_id: str, at: str, kickoff_at: str, provider: str = PROVIDER) -> Optional[dict]:
    """The provider's 1X2 price as of `at` (one rule for the card and the scorecard): the latest 'current' row of each side
    retrieved at or before `at` and before kickoff, every spelling of the provider one book (odds.provider_key); the store
    appends a side only when it changes and writes a withdrawal row for a side no longer listed, so a withdrawn or missing
    side means no quote. Returns {provider, retrieved_at (newest side), side_retrieved_at, quote_id (newest), price
    (American per side), devig (proportional, per side)} or None."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='soccer_odds_quotes'").fetchone() is None:
        return None
    rows = conn.execute(
        f"SELECT q.* FROM soccer_odds_quotes q WHERE q.match_id = ? AND q.market = '1x2' AND {same_provider('q.provider')} "
        "AND q.price_kind = 'current' AND q.retrieved_at <= ? AND q.retrieved_at < ? AND q.quote_id = (SELECT MAX(p.quote_id) "
        f"FROM soccer_odds_quotes p WHERE p.match_id = q.match_id AND {same_provider_columns('p.provider', 'q.provider')} "
        "AND p.market = q.market AND p.side = q.side AND p.price_kind = q.price_kind AND p.retrieved_at <= ? "
        "AND p.retrieved_at < ?)",
        (match_id, provider, at, kickoff_at, at, kickoff_at)).fetchall()
    offered = [dict(r, provider=provider) for r in rows if r["offered"]]
    probs = implied_lines.match_result_probabilities(offered, provider)
    if probs is None:
        return None
    by = {q["side"]: q for q in offered}
    return {"provider": provider, "retrieved_at": max(q["retrieved_at"] for q in offered),
            "side_retrieved_at": {s: by[s]["retrieved_at"] for s in ("home", "draw", "away")},
            "quote_id": max(q["quote_id"] for q in offered), "price": {s: by[s]["american"] for s in ("home", "draw", "away")},
            "devig": probs}


def app_share(probs: dict) -> float:
    gain = probs["win"] + probs["half_win"] / 2
    lose = probs["loss"] + probs["half_loss"] / 2
    return gain / (gain + lose) if gain + lose > 0 else 0.0


def total_goals(sel: dict, quotes: list) -> dict:
    """Comparison for one goals_total selection against the match's saved quotes (soccer_markets_live.quotes rows)."""
    app = app_share(sel["probabilities"])
    if sel["source"] != "bookmaker":
        return _empty("model_line", app, "This is the model's own line; no sportsbook line to compare.")
    same = {q["side"]: q for q in quotes if q["market"] == "total_goals" and q["line"] == sel["line"]
            and provider_key(q["provider"]) == provider_key(PROVIDER)}
    if not {"over", "under"} <= set(same):
        return _empty("no_quote", app, f"No complete over/under quote was saved at line {sel['line']}.", line=sel["line"])
    over, under = devig_two_way(same["over"]["american"], same["under"]["american"])
    book = over if sel["side"] == "over" else under
    return {"status": "exact", "app_probability": app, "book_probability": book, "gap_pp": _gap(app, book), "provider": PROVIDER,
            "price": f"{_fmt(same['over']['american'])}/{_fmt(same['under']['american'])}", "line": sel["line"],
            "quote_id": max(q["quote_id"] for q in same.values()), "retrieved_at": max(q["retrieved_at"] for q in same.values()),
            "reason": None}


def annotate_totals(selections: list, quotes: list) -> None:
    """Adds `sportsbook` to each goals_total selection (in place); other markets have no quote source and get nothing."""
    for s in selections:
        if s["market"] == "goals_total":
            s["sportsbook"] = total_goals(s, quotes)


def annotate(conn, forecasts: list) -> None:
    """Adds `sportsbook` (the 1X2 comparison) to each live forecast dict, in place. A failure never hides a forecast."""
    for f in forecasts:
        try:
            f["sportsbook"] = one_x_two(conn, f)
        except Exception:                       # display only
            f["sportsbook"] = None
