"""The app's pick against the saved sportsbook quote for the SAME game, market, side and line (never another line).
Display only: nothing here feeds a forecast, a grade or a record."""
from typing import Optional

from . import coverage, odds
from .schemas import SportsbookComparison

NO_QUOTE_SOURCE = ("team_total_home", "team_total_away", "reg3")


def _pct(p: float) -> float:
    return float(f"{p * 100:.1f}")


def _american(p: Optional[int]) -> str:
    return "?" if p is None else f"{p:+d}"


def _line_quote(conn, market: str, game, rec: dict):
    """(quote row, why not): the quote the forecast used when it is acceptable (coverage.forecast_quote), else the latest
    'current' quote at exactly the forecast's line retrieved at or before the forecast and before the start."""
    pa, why = coverage.forecast_quote(conn, market, game, rec)
    if pa is not None:
        return conn.execute("SELECT * FROM market_quotes WHERE quote_id=?", (rec["market_quote_id"],)).fetchone(), None
    q = conn.execute("SELECT * FROM market_quotes WHERE game_id=? AND market=? AND line IS ? AND price_label='current' "
                     "AND retrieved_at <= ? AND retrieved_at < ? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1",
                     (game["game_id"], market, rec["line"], rec["generated_at"], game["tipoff_at"])).fetchone()
    return q, (None if q is not None else "no sportsbook quote saved at this line by the forecast time")


def sportsbook_comparison(conn, sport: str, game, market: str, rec: Optional[dict]) -> Optional[SportsbookComparison]:
    """rec = the canonical record (side, confidence, line, line_source, market_quote_id, generated_at); None without one.
    Quote timing is the forecast's own: the latest quote retrieved at or before generated_at and before the start."""
    if rec is None:
        return None
    app_p, side = float(rec["confidence"]), rec["side"]
    base = {"app_probability": app_p}
    if market in NO_QUOTE_SOURCE:
        return SportsbookComparison(status="unsupported", reason="No sportsbook quotes are collected for this market.", **base)
    if market == "winner":
        q = odds.winner_quote(conn, sport, game["game_id"], rec["generated_at"])
        if q is None or q["retrieved_at"] >= game["tipoff_at"]:
            return SportsbookComparison(status="no_quote", reason="No moneyline saved by the forecast time.", **base)
        home = side == "a"
        book = q["p_home"] if home else 1 - q["p_home"]
        pa, pb = (q["price_home"], q["price_away"]) if home else (q["price_away"], q["price_home"])
        line, fields = None, {"quote_id": q["quote_id"], "provider": q["provider"], "retrieved_at": q["retrieved_at"]}
    else:
        if rec["line_source"] != "bookmaker":
            return SportsbookComparison(status="model_line", reason="The forecast used the model's own line.", **base)
        q, why = _line_quote(conn, market, game, rec)
        if q is None:
            return SportsbookComparison(status="no_quote", reason=why, **base)
        book = q["p_a_devig"] if side == "a" else 1 - q["p_a_devig"]
        pa, pb = (q["price_a"], q["price_b"]) if side == "a" else (q["price_b"], q["price_a"])
        line = q["line"] if market != "spread" or side == "a" else -q["line"]   # the picked team's own signed line
        fields = {"quote_id": q["quote_id"], "provider": q["provider"], "retrieved_at": q["retrieved_at"]}
    # The stored confidence of a two-way line market already excludes the push (coverage._confidence) and the book's two-way
    # de-vig does too: say so when the line can push (integer line), so the card does not show an unexplained gap.
    pushes = market in ("spread", "total", "team_total_home", "team_total_away") and (rec.get("p") or [0, 0, 0])[1] > 1e-9
    return SportsbookComparison(status="exact", book_probability=book, gap_pp=round(_pct(app_p) - _pct(book), 1), line=line,
                                price=f"{_american(pa)}/{_american(pb)}", basis="conditional on no push" if pushes else None,
                                **fields, **base)
