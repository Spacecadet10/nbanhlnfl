"""Lines and fair prices worked out from stored sportsbook prices (owner request, Nov 11 2026). Display only: never a price
anyone can bet at, never a model input, never a ticket price.

- Implied team totals, labelled "implied from spread and total": from DraftKings' spread and total stored from the SAME
  response (one fetch), the latest retrieved at or before the moment asked. home = total/2 - home spread/2, away =
  total/2 + home spread/2 (the stored spread line is the home handicap). NHL: the puck line is always +/-1.5, so the NHL
  number is rough (docs/evidence/free-price-sources-2026-11-10 section 5).
- Soccer double chance and draw no bet, labelled "derived from match result prices": DraftKings' three 1X2 prices of one
  retrieval with the margin removed proportionally, turned into fair decimal prices.
"""
from typing import Optional

IMPLIED_LABEL = 'implied from spread and total'
DERIVED_LABEL = 'derived from match result prices'
NOT_A_PRICE = 'Not a price you can bet at.'


def implied_team_total(conn, game_id: str, market: str, at: str, sport: str = '') -> Optional[dict]:
    """{'line', 'retrieved_at', 'label', 'note'} for team_total_home / team_total_away, or None."""
    if market not in ('team_total_home', 'team_total_away'):
        return None
    row = conn.execute(
        "SELECT s.line AS spread, t.line AS total, s.retrieved_at FROM market_quotes s JOIN market_quotes t "
        "ON t.fetch_id = s.fetch_id AND t.game_id = s.game_id AND t.provider = s.provider AND t.price_label = s.price_label "
        "WHERE s.game_id = ? AND s.provider = 'DraftKings' AND s.price_label = 'current' AND s.market = 'spread' "
        "AND t.market = 'total' AND s.retrieved_at <= ? AND s.line IS NOT NULL AND t.line IS NOT NULL "
        "ORDER BY s.retrieved_at DESC, s.fetch_id DESC LIMIT 1", (game_id, at)).fetchone()
    if row is None:
        return None
    spread, total = float(row['spread']), float(row['total'])
    line = total / 2 - spread / 2 if market == 'team_total_home' else total / 2 + spread / 2
    note = NOT_A_PRICE + (' Rough for hockey: the puck line is always 1.5.' if sport == 'nhl' else '')
    return {'line': round(line, 2), 'retrieved_at': row['retrieved_at'], 'label': IMPLIED_LABEL, 'note': note}


def fair_decimal(p: float) -> Optional[float]:
    return round(1 / p, 3) if p > 0 else None


def derived_dc_dnb(quotes: list[dict], provider: str = 'DraftKings') -> Optional[dict]:
    """Fair double chance / draw no bet from one retrieval of the provider's three 1X2 prices, or None."""
    sides = {q['side']: q for q in quotes if q.get('market') == '1x2' and q.get('provider') == provider}
    if set(sides) != {'home', 'draw', 'away'} or len({q['retrieved_at'] for q in sides.values()}) != 1:
        return None
    inv = {k: 1 / float(q['decimal_odds']) for k, q in sides.items()}
    s = sum(inv.values())
    p = {k: v / s for k, v in inv.items()}
    return {'label': DERIVED_LABEL, 'note': NOT_A_PRICE + ' Fair prices with the sportsbook margin removed.',
            'provider': provider, 'retrieved_at': next(iter(sides.values()))['retrieved_at'],
            'double_chance': {'home_draw': fair_decimal(p['home'] + p['draw']),
                              'draw_away': fair_decimal(p['draw'] + p['away']),
                              'home_away': fair_decimal(p['home'] + p['away'])},
            'draw_no_bet': {'home': round((p['home'] + p['away']) / p['home'], 3),
                            'away': round((p['home'] + p['away']) / p['away'], 3)}}
