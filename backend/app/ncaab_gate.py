"""Which NCAAB picks may be sent to Telegram: policy ncaab-colour-gate-v1 (owner brief Nov 21 2026; docs/preregistration/ncaab-colour-gate-2026-11-22.md).

Replaces the blanket NCAAB-off switch and the top-4-by-gap rule. A pick qualifies only when ALL hold:
  1. its market is one the gate supports (winner, spread, total) and has not failed validation (VALIDATION_BLOCKED);
  2. it is green under the SAME decision the card displays (telegram.card_colour -> card_threshold.decide): never a second
     interpretation here, so a missing, stale or unqualified threshold can never qualify;
  3. the forecast is pregame and on the sportsbook's own line (spread/total), the stored quote at forecast time exists, and the
     newest quote from the SAME provider for the same game, market and side is on the SAME line, retrieved before tip-off and no
     older than MAX_PRICE_AGE;
  4. its expected value at that offered price is positive, with pushes refunded (green is a qualification signal, not a price).
The recommended pick of a card is the qualifying pick with the highest expected value; other markets stay on the card marked
"Context only". Games with no qualifying pick get no card (zero qualifiers = zero cards), except a game that already had a sent
card: it stays, so a lean that stops qualifying becomes the usual single 'Pick withdrawn' update. Every evaluated game's verdicts
are saved append-only in the delivery ledger; forecasts and grades are untouched.
"""
import hashlib
import json
from datetime import datetime, timedelta
from typing import Callable, Optional

POLICY = 'ncaab-colour-gate-v1'
MARKETS = ('winner', 'spread', 'total')
MAX_PRICE_AGE = timedelta(minutes=20)      # the NCAAB odds tick runs every 60 s; an older price fails closed
# Chronological check of the live NCAAB record, Nov 17-21 2026 (HANDOFF, colour release): spread Brier 0.2602 / log loss 0.7154
# and total 0.2595 / 0.7153 on 218 settled games, worse than a coin flip (0.2500 / 0.6931) and than DraftKings' no-vig price.
# Lifting a block is a new policy version with its own evidence, never an edit of this table.
VALIDATION_BLOCKED = {
    'spread': 'NCAAB spread failed validation (Brier 0.2602 vs coin flip 0.2500, Nov 17-21)',
    'total': 'NCAAB total failed validation (Brier 0.2595 vs coin flip 0.2500, Nov 17-21)',
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS ncaab_gate_decisions (
  decision_id INTEGER PRIMARY KEY,
  policy TEXT NOT NULL,
  game_id TEXT NOT NULL,
  decided_at TEXT NOT NULL,              -- the run's information time
  versions TEXT NOT NULL,                -- JSON [forecast_id, ...] the verdicts are about
  verdicts TEXT NOT NULL,                -- JSON list, one per market: colour decision, quote, price, EV, reasons
  chosen TEXT,                           -- forecast_id of the recommended pick, NULL when none qualifies
  digest TEXT NOT NULL UNIQUE            -- a verdict set is saved once; a changed verdict is a new row
);
CREATE TRIGGER IF NOT EXISTS ncaab_gate_decisions_no_update BEFORE UPDATE ON ncaab_gate_decisions
BEGIN SELECT RAISE(ABORT, 'ncaab_gate_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ncaab_gate_decisions_no_delete BEFORE DELETE ON ncaab_gate_decisions
BEGIN SELECT RAISE(ABORT, 'ncaab_gate_decisions is append-only'); END;
'''


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace('Z', '+00:00'))


def decimal_odds(american) -> Optional[float]:
    if american is None or abs(float(american)) < 100:
        return None
    a = float(american)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def expected_value(p_win: float, p_push: float, p_loss: float, american) -> Optional[float]:
    """Per unit staked at the offered American price: win pays (decimal - 1), a push refunds the stake, a loss costs 1."""
    dec = decimal_odds(american)
    return None if dec is None else p_win * (dec - 1) - p_loss + 0 * p_push


def _side(pick) -> Optional[str]:
    h = pick.get('percentage_history')
    h = h.model_dump(mode='json') if hasattr(h, 'model_dump') else h
    return (h or {}).get('side')


def _forecast_time_prob(source, pick):
    """The card's own sportsbook % (same game, market and line, retrieved at or before the forecast and before tip-off)."""
    from .tickets import card_market_prob
    try:
        return card_market_prob(source, pick)
    except Exception:
        return None


def evaluate(source, pick: dict, colour: Optional[dict], now: datetime) -> dict:
    """One pick's verdict. `colour` is the card's own colour decision for this pick (telegram.card_colour)."""
    market, reasons = pick['market'], []
    v = {'forecast_id': pick.get('forecast_id'), 'market': market, 'pick_label': pick.get('pick_label'),
         'probability': pick.get('probability'), 'colour': (colour or {}).get('colour'),
         'green_from': (colour or {}).get('green_from'), 'threshold_state': (colour or {}).get('state'),
         'decide_version': (colour or {}).get('version'), 'provider': None, 'price': None, 'price_retrieved_at': None,
         'line': None, 'ev': None}
    if market not in MARKETS:
        reasons.append('market not supported by the NCAAB gate')
    if market in VALIDATION_BLOCKED:
        reasons.append(VALIDATION_BLOCKED[market])
    if not colour or not colour.get('qualifies'):
        reasons.append('not green under the card threshold' + (f" ({colour['legend']})" if colour and colour.get('legend') else ''))
    tip = pick.get('tipoff')
    if pick.get('status') != 'scheduled' or not tip or _parse(pick['generated_at']) >= _parse(tip):
        reasons.append('no valid pregame forecast')
    q = pick.get('quote_check') or {}
    v.update(provider=q.get('provider'), price=q.get('price_now'), price_retrieved_at=q.get('retrieved_at'),
             line=q.get('line_at_forecast'))
    if q.get('error') or q.get('price_now') is None or not q.get('retrieved_at'):
        reasons.append('no complete sportsbook quote for this side')
    elif _forecast_time_prob(source, pick) is None:
        reasons.append('no stored sportsbook quote at the forecast time')
    else:
        if market != 'winner' and q.get('line_source') != 'bookmaker':
            reasons.append("forecast is not on the sportsbook's line")
        if q.get('line_moved') or (market != 'winner' and q.get('line_now') != q.get('line_at_forecast')):
            reasons.append('sportsbook line differs from the forecast line')
        got = _parse(q['retrieved_at'])
        if now - got > MAX_PRICE_AGE:
            reasons.append(f'newest price older than {int(MAX_PRICE_AGE.total_seconds() // 60)} minutes')
        if tip and got >= _parse(tip):
            reasons.append('price retrieved after tip-off')
    side = _side(pick)
    row = source.execute('SELECT p_a, p_mid, p_b FROM game_forecasts WHERE forecast_id = ?', (pick.get('forecast_id'),)).fetchone() \
        if pick.get('forecast_id') is not None else None
    if row is None or side not in ('a', 'b'):
        reasons.append('forecast probabilities unavailable')
    elif q.get('price_now') is not None:
        win, lose = (row['p_a'], row['p_b']) if side == 'a' else (row['p_b'], row['p_a'])
        ev = expected_value(win, row['p_mid'] or 0.0, lose, q['price_now'])
        v['ev'] = None if ev is None else round(ev, 6)
        if ev is None or ev <= 0:
            reasons.append('expected value not positive')
    v['reasons'], v['qualifies'] = reasons, not reasons
    return v


def keep(picks: list, verdict_of: Callable[[dict], dict], *, sent_games: set) -> list:
    """The picks to card: every pick of a game with a qualifying pick, plus every pick of a game that already had a sent card."""
    good = {p['game_id'] for p in picks if verdict_of(p).get('qualifies')}
    return [p for p in picks if p['game_id'] in good or p['game_id'] in sent_games]


def summary(v: dict) -> str:
    return (f"Qualifies (policy {POLICY}): green from {v['green_from']}% under the card threshold; "
            f"{v['provider']} price {v['price']:+g}, expected value {v['ev'] * 100:+.1f}% per unit at that price "
            '(pushes refunded). Not a guarantee of profit.')


def context_line(v: dict) -> str:
    return 'Context only · not a recommendation: ' + (v['reasons'][0] if v['reasons'] else 'not the recommended pick')


VOLATILE = ('ev', 'price_retrieved_at')


def sent_games(ledger) -> set:
    """NCAAB games with a confirmed sent card (published_picks), not counting uncertain sends resolved from a message-id gap."""
    from .published_picks import UNCONFIRMED_BASIS
    if not ledger.execute("SELECT 1 FROM sqlite_master WHERE name = 'published_picks'").fetchone():
        return set()
    return {r[0] for r in ledger.execute("SELECT DISTINCT game_id FROM published_picks WHERE sport = 'ncaab' "
                                         "AND (basis IS NULL OR basis <> ?)", (UNCONFIRMED_BASIS,))}


def _record(ledger, game_id: str, verdicts: list, now: str) -> None:
    versions = sorted(str(v['forecast_id']) for v in verdicts)
    chosen = max((v for v in verdicts if v['qualifies']), key=lambda v: v['ev'], default=None)
    body = json.dumps(verdicts, sort_keys=True, default=str)
    # A row per changed verdict, not per odds tick: the EV and quote time move every minute (review, Nov 21).
    stable = json.dumps([{k: x for k, x in v.items() if k not in VOLATILE} for v in verdicts], sort_keys=True, default=str)
    digest = hashlib.sha256(json.dumps([POLICY, game_id, versions, stable]).encode()).hexdigest()
    ledger.execute('INSERT OR IGNORE INTO ncaab_gate_decisions (policy, game_id, decided_at, versions, verdicts, chosen, digest) '
                   'VALUES (?, ?, ?, ?, ?, ?, ?)', (POLICY, game_id, now, json.dumps(versions), body,
                                                     None if chosen is None else str(chosen['forecast_id']), digest))


def apply(ledger, source, report: dict, colour_of: Callable[[dict], Optional[dict]], now: datetime) -> dict:
    """Annotate each due NCAAB pick with its verdict (pick['ncaab_gate']), save the verdicts, and narrow report['picks'] to the
    games that may be carded. Returns counts for the run log."""
    ledger.executescript(SCHEMA)
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    def is_due(p):      # the same window message_batches cards (telegram.py): tip-off within 2 h, forecast in that window
        try:
            start, made = _parse(p['tipoff']), _parse(p['generated_at'])
        except Exception:
            return False
        return (p.get('status') == 'scheduled' and p.get('grade') == 'pending' and now < start <= now + timedelta(hours=2)
                and start - timedelta(hours=2) <= made <= now)
    due = [p for p in report['picks'] if is_due(p)]
    by_game: dict = {}
    for p in due:
        try:
            p['ncaab_gate'] = evaluate(source, p, colour_of(p), now)
        except Exception as exc:        # one unreadable pick never fails the sport, and never qualifies
            p['ncaab_gate'] = {'forecast_id': p.get('forecast_id'), 'market': p.get('market'), 'qualifies': False, 'ev': None,
                               'reasons': [f'evaluation error: {type(exc).__name__}']}
        by_game.setdefault(p['game_id'], []).append(p['ncaab_gate'])
    for gid, verdicts in by_game.items():
        _record(ledger, gid, verdicts, stamp)
    report['picks'] = keep(due, lambda p: p['ncaab_gate'], sent_games=sent_games(ledger))
    return {'policy': POLICY, 'evaluated_games': len(by_game),
            'qualifying': sum(1 for vs in by_game.values() for v in vs if v['qualifies'])}
