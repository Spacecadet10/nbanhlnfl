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


def winner_quote(conn, sport: str, game_id: str, at: str, *, earliest: bool = False, provider: Optional[str] = None):
    """The moneyline a winner forecast made at `at` could see: the latest retrieved no later than `at` (NBA: odds_quotes;
    other sports: market_quotes, market 'winner'), as {quote_id, provider, price_label, retrieved_at, p_home, price_home,
    price_away}, or None. `earliest`: the first one retrieved by `at` instead (the opening quote), the book's own 'open'
    price of that retrieval first. `provider`: that sportsbook's quotes only (None: any)."""
    order = ("retrieved_at, price_label = 'open' DESC, quote_id" if earliest else
             "retrieved_at DESC, price_label = 'current' DESC, quote_id DESC")
    only = " AND provider = ?" if provider is not None else ""
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
