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
