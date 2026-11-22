"""Odds normalization. Converts American prices to implied probabilities and removes the bookmaker margin."""
from typing import Optional


def american_to_implied(price: int) -> float:
    if price == 0 or -100 < price < 100:
        raise ValueError(f"Invalid American odds: {price}")
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def devig_two_way(home_price: int, away_price: int) -> tuple[float, float]:
    """Proportional (multiplicative) de-vig of a two-way market; returns (home, away) summing to 1."""
    h, a = american_to_implied(home_price), american_to_implied(away_price)
    return h / (h + a), a / (h + a)


def parse_american(text: Optional[str]) -> Optional[int]:
    """Parses '-125', '+105', 'EVEN'; returns None for missing/off-board values."""
    if text is None:
        return None
    t = str(text).strip().upper()
    if t in ("EVEN", "EV"):
        return 100
    try:
        value = int(t.replace("+", ""))
    except ValueError:
        return None
    return value if value >= 100 or value <= -100 else None


def provider_key(name: Optional[str]) -> Optional[str]:
    """One key per sportsbook whatever the source's spelling: ESPN sent 'Draft Kings' instead of 'DraftKings' from
    2026-11-16T20:41Z. Only spaces and case are ignored, so two different books never share a key. Saved rows keep the raw
    name; lookups compare keys (SQL: same_provider)."""
    return None if name is None else "".join(name.split()).casefold()


def same_provider(column: str = "provider") -> str:
    """SQL condition: `column` names the same sportsbook as the bound parameter (see provider_key)."""
    return f"lower(replace({column}, ' ', '')) = lower(replace(?, ' ', ''))"


def same_provider_columns(a: str, b: str) -> str:
    """SQL condition: two provider columns name the same sportsbook (see provider_key)."""
    return f"lower(replace({a}, ' ', '')) = lower(replace({b}, ' ', ''))"


def winner_quote(conn, sport: str, game_id: str, at: str, *, earliest: bool = False, provider: Optional[str] = None):
    """The moneyline a winner forecast made at `at` could see: the latest retrieved no later than `at` (NBA: odds_quotes;
    other sports: market_quotes, market 'winner'), as {quote_id, provider, price_label, retrieved_at, p_home, price_home,
    price_away}, or None. `earliest`: the first one retrieved by `at` instead (the opening quote), the book's own 'open'
    price of that retrieval first. `provider`: that sportsbook's quotes only (None: any)."""
    if sport not in ("nba", "nhl", "nfl", "ncaab"):      # NBA: odds_quotes; every other sport: market_quotes (market 'winner')
        raise ValueError(f"No moneyline source is defined for a {sport!r} store")
    order = ("retrieved_at, price_label = 'open' DESC, quote_id" if earliest else
             "retrieved_at DESC, price_label = 'current' DESC, quote_id DESC")
    only = " AND " + same_provider() if provider is not None else ""
    q = conn.execute(
        "SELECT quote_id, provider, price_label, retrieved_at, home_prob_devig AS p_home, home_american AS price_home, "
        f"away_american AS price_away FROM odds_quotes WHERE game_id=? AND retrieved_at <= ?{only} ORDER BY {order} LIMIT 1"
        if sport == "nba" else
        "SELECT quote_id, provider, price_label, retrieved_at, p_a_devig AS p_home, price_a AS price_home, "
        f"price_b AS price_away FROM market_quotes WHERE game_id=? AND market='winner' AND retrieved_at <= ?{only} "
        f"ORDER BY {order} LIMIT 1",
        (game_id, at) + ((provider,) if provider is not None else ())).fetchone()
    return None if q is None else dict(zip(("quote_id", "provider", "price_label", "retrieved_at", "p_home", "price_home",
                                             "price_away"), tuple(q)))


# ---- the ONE sportsbook-quote rule for a forecast (market_quotes: NHL/NFL/NCAAB winner and every non-winner market) ----------
# "What price did the forecast see": the latest 'current' quote of the same game, market and exact line retrieved at or
# before the forecast time and before the start. Ties on the retrieval time break deterministically: the quote recorded
# with the forecast (if it is among them), DraftKings, then the provider key in order, then the newest quote_id. Used by the
# card, the ticket, the scorecard and the sportsbook comparison. Executable prices (price_a/mid/b) and the margin-free
# p_*_devig of the same row stay separate columns; DC/DNB prices are derived elsewhere (bet365_dc, soccer_book), never here.
# Stored rows are only read. Closing-price analysis (scripts/closing_value.py) is a separate, labelled study.
_BOOK_ORDER = ("retrieved_at DESC, quote_id IS ? DESC, lower(replace(provider, ' ', '')) = 'draftkings' DESC, "
               "lower(replace(provider, ' ', '')) ASC, quote_id DESC")


def forecast_quote(conn, game_id: str, market: str, line: Optional[float], generated_at: str, tipoff_at: str, *,
                   recorded_id: Optional[int] = None, need_mid: bool = False):
    """The market_quotes row the forecast made at `generated_at` saw, or None (see the rule above). `line`: exact match
    (None matches only a lineless market). `need_mid`: only a quote with a regulation-draw price."""
    return conn.execute(
        "SELECT * FROM market_quotes WHERE game_id=? AND market=? AND line IS ? AND price_label='current' "
        "AND retrieved_at <= ? AND retrieved_at < ?" + (" AND p_mid_devig IS NOT NULL" if need_mid else "") +
        f" ORDER BY {_BOOK_ORDER} LIMIT 1", (game_id, market, line, generated_at, tipoff_at, recorded_id)).fetchone()


def send_time_quote(conn, game_id: str, market: str, at: str, *, provider: Optional[str] = None):
    """SEND-TIME freshness check, not the forecast's price: the latest 'current' quote of this game and market (any line, so a
    moved line shows) retrieved by `at`, from `provider` when given. Never use it to say what the forecast saw."""
    return conn.execute(
        "SELECT * FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' AND retrieved_at <= ?" +
        (" AND " + same_provider() if provider is not None else "") + f" ORDER BY {_BOOK_ORDER} LIMIT 1",
        (game_id, market, at) + ((provider,) if provider is not None else ()) + (None,)).fetchone()
