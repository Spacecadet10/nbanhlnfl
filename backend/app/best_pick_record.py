"""Paper recorder for the preregistered best-pick rule (Step F; docs/preregistration/best-pick-2026-11-11-v4.md).

For every game card the delivery job actually sends (deliver_card returned 'sent'), one append-only row in the Telegram
ledger's `best_picks` table: what the v4 rule would have named, the card's baseline pick (highest sportsbook %), and the
full picture behind both (every same-game pick of the card's report with its displayed and raw percentages, the quote
ids and American prices behind the sportsbook %, proportional and power de-vig values, each H1-H7 item, the exact live
record with rank and AVOID, the lineup flags, and whether each team played the previous day).

NOTHING here reaches a card: the card's text, PNG and delivery key are built exactly as before. Results are never stored
here; scripts/best_picks.py grades the recorded forecast ids from the sport stores when the evaluation set is complete.
A problem never stops, delays or changes a card: a picture that cannot be built is recorded as an 'error' row (and a
line on stderr), and a row that cannot be written leaves a sent card without a row, which the report counts.

Information used is what the card job sees at send time only: the card's own report (`ranked_picks`, the object the card
is built from), quote rows retrieved at or before each pick's generated_at, `games` rows (schedule), and
`lineup.facts(conn, sport, games, as_of)`. NBA and NHL only are scored; NFL (and soccer, which this job does not send)
are record-only and never name a pick (v4 section 8). The rule itself is not changed here; see the preregistration.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

RECORDER_VERSION = 'best-pick-recorder-v1'
PREREG_PATH = Path(__file__).resolve().parents[2] / 'docs' / 'preregistration' / 'best-pick-2026-11-11-v4.md'
PREREG_SHA256 = 'cc46737d788556b31bd8e062eb423946e988eaaacd756e40a7eb308a826412d6'
T0 = '2026-11-11T22:04:29Z'                 # committer time of ed33e28, the commit that first adds the v4 file
TORONTO = ZoneInfo('America/Toronto')
# The recorder code that was imported (not the file on disk at record time, which a later release could change).
RECORDER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

RULE_MARKETS = {'nba': ('winner', 'spread', 'total'), 'nhl': ('winner', 'spread', 'total', 'reg3')}   # v4 section 2.1
MARKET_ORDER = {'winner': 0, 'spread': 1, 'total': 2, 'reg3': 3}
FLOOR = 2                                   # v4 section 4
H1_HIGH, H1_LOW = Decimal('72.0'), Decimal('58.0')
H2_ABOVE = Decimal('5.6')
H4_MOVE = Decimal('3.0')
H5B_APP = Decimal('67.0')
H7_WINNER_APP = Decimal('60.0')
POWER_LO, POWER_HI, POWER_TOL, POWER_ITER = 0.5, 5.0, 1e-12, 200

SCHEMA = '''
CREATE TABLE IF NOT EXISTS best_picks (
  delivery_key TEXT PRIMARY KEY,
  recorded_at TEXT NOT NULL,             -- send time: the as_of of the report the card was built from
  written_at TEXT NOT NULL,              -- wall clock when the row was written
  sport TEXT NOT NULL, game_id TEXT NOT NULL, matchup TEXT, tipoff_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('ok', 'error')), error TEXT,
  prereg_sha256 TEXT, recorder_version TEXT NOT NULL, recorder_sha256 TEXT, code_commit TEXT,
  rule_applies INTEGER NOT NULL CHECK (rule_applies IN (0, 1)),
  named_forecast_id INTEGER, named_score INTEGER, named_market TEXT, named_pick_label TEXT,
  baseline_forecast_id INTEGER, baseline_book_pct TEXT, baseline_book_raw REAL,
  candidates_n INTEGER, excluded_lineup INTEGER, excluded_avoid INTEGER,
  home_played_prev_day INTEGER, away_played_prev_day INTEGER,
  picture TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS best_picks_no_update BEFORE UPDATE ON best_picks
BEGIN SELECT RAISE(ABORT, 'best_picks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS best_picks_no_delete BEFORE DELETE ON best_picks
BEGIN SELECT RAISE(ABORT, 'best_picks is append-only'); END;
'''
# Review L3: INSERT OR REPLACE would delete a row without firing the delete trigger (sqlite fires it only with
# recursive_triggers on). This module only ever uses INSERT OR IGNORE; nothing else writes best_picks.


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace('Z', '+00:00'))


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def pct(p: Optional[float]) -> Optional[Decimal]:
    """The displayed percentage (threshold_overview.displayed_percentage: one decimal, half-up) as a Decimal."""
    if p is None:
        return None
    from .threshold_overview import displayed_percentage
    return Decimal(displayed_percentage(p))


def power_devig(implied: list) -> Optional[list]:
    """v4 section 6, power de-vig: q_i = r_i^k with sum_j r_j^k = 1, bisection on k in [0.5, 5] until the sum is within
    1e-12 of 1, at most 200 iterations. None when sum r^k - 1 does not change sign on [0.5, 5] or 200 iterations do not
    reach 1e-12."""
    def f(k):
        return sum(r ** k for r in implied) - 1
    lo, hi = POWER_LO, POWER_HI
    f_lo, f_hi = f(lo), f(hi)
    for k, v in ((lo, f_lo), (hi, f_hi)):
        if abs(v) <= POWER_TOL:
            return [r ** k for r in implied]
    if (f_lo > 0) == (f_hi > 0):
        return None
    for _ in range(POWER_ITER):
        mid = (lo + hi) / 2
        v = f(mid)
        if abs(v) <= POWER_TOL:
            return [r ** mid for r in implied]
        if (v > 0) == (f_lo > 0):
            lo, f_lo = mid, v
        else:
            hi = mid
    return None


def played_previous_day(conn, team_id: str, tipoff_at: str) -> bool:
    """v4 section 3: the team has a `games` row (home or away), status not postponed or canceled, whose tipoff_at falls on
    the America/Toronto calendar date one day before this game's America/Toronto date."""
    day = _parse(tipoff_at).astimezone(TORONTO).date() - timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=TORONTO)
    end = start + timedelta(days=1)            # aware + timedelta is wall-clock: the next local midnight, DST included
    return conn.execute(
        "SELECT 1 FROM games WHERE (home_team_id=? OR away_team_id=?) AND status NOT IN ('postponed', 'canceled') "
        "AND tipoff_at >= ? AND tipoff_at < ? LIMIT 1", (team_id, team_id, _stamp(start), _stamp(end))).fetchone() is not None


# ---- the quote behind a pick's sportsbook % ------------------------------------------------------------------------------
def _quote(conn, sport: str, pick: dict, side: str) -> dict:
    """The quote the recorded market_prob came from, its American prices and both de-vig values for the picked side.
    winner: the app's own _winner_quote_prob selection (coverage.winner_quote, latest retrieved <= generated_at);
    other markets: the forecast's market_quote_id (coverage.forecast_quote acceptance, as ranked_picks uses)."""
    from . import coverage, odds
    from .threshold_overview import _picked
    out = {'table': None, 'quote_id': None, 'provider': None, 'retrieved_at': None, 'price_label': None, 'prices': None,
           'picked_index': None, 'proportional_from_prices': None, 'power': None, 'power_note': None,
           'matches_market_prob': None}
    if pick.get('market_prob') is None:
        out['note'] = 'no sportsbook % on the pick'
        return out
    market, gid, at = pick['market'], pick['game_id'], pick['generated_at']
    if market == 'winner':
        q = coverage.winner_quote(conn, sport, gid, at)
        if q is None:
            out['note'] = 'no moneyline retrieved by generated_at'
            return out
        out.update(table='odds_quotes' if sport == 'nba' else 'market_quotes', quote_id=q['quote_id'],
                   provider=q['provider'], retrieved_at=q['retrieved_at'], price_label=q['price_label'],
                   prices=[q['price_home'], q['price_away']], picked_index=0 if side == 'a' else 1)
        p_home = q['p_home']
        book = _picked('winner', p_home, side)
    else:
        f = conn.execute('SELECT market_quote_id FROM game_forecasts WHERE forecast_id=?', (pick['forecast_id'],)).fetchone()
        qid = None if f is None else f[0]
        q = None if qid is None else conn.execute('SELECT * FROM market_quotes WHERE quote_id=?', (qid,)).fetchone()
        if q is None:
            out['note'] = 'no market quote recorded with the forecast'
            return out
        three = q['price_mid'] is not None
        prices = [q['price_a'], q['price_mid'], q['price_b']] if three else [q['price_a'], q['price_b']]
        out.update(table='market_quotes', quote_id=q['quote_id'], provider=q['provider'], retrieved_at=q['retrieved_at'],
                   price_label=q['price_label'], prices=prices,
                   picked_index=(('a', 'mid', 'b') if three else ('a', 'b')).index(side))
        vec = (q['p_a_devig'], q['p_mid_devig'], q['p_b_devig'])
        book = _picked(market, vec if market == 'reg3' else q['p_a_devig'], side)
    out['matches_market_prob'] = book is not None and abs(book - pick['market_prob']) < 1e-9
    implied = [odds.american_to_implied(int(p)) for p in out['prices']]
    out['proportional_from_prices'] = implied[out['picked_index']] / sum(implied)
    power = power_devig(implied)
    if power is None:
        out['power_note'] = 'no power de-vig root on [0.5, 5] within 200 iterations'
    else:
        out['power'] = power[out['picked_index']]
    return out


def _h4(conn, pick: dict, side: str) -> dict:
    """H4 (NBA winner): movement of the displayed picked-side % from the earliest same-provider odds_quotes row retrieved
    <= generated_at ("opening": ties price_label 'open' first, then lowest quote_id) to the app's "now" quote."""
    from . import coverage
    from .threshold_overview import _picked
    out = {'item': 0, 'now_quote_id': None, 'open_quote_id': None, 'provider': None, 'now_pct': None, 'open_pct': None,
           'movement': None, 'note': None}
    now = coverage.winner_quote(conn, 'nba', pick['game_id'], pick['generated_at'])
    if now is None:
        out['note'] = 'no now quote'
        return out
    cols = {r[1] for r in conn.execute('PRAGMA table_info(odds_quotes)')}
    market_filter = " AND market='moneyline'" if 'market' in cols else ''
    o = conn.execute('SELECT quote_id, home_prob_devig FROM odds_quotes WHERE game_id=? AND provider=? AND retrieved_at<=?'
                     + market_filter + " ORDER BY retrieved_at, price_label='open' DESC, quote_id LIMIT 1",
                     (pick['game_id'], now['provider'], pick['generated_at'])).fetchone()
    out.update(now_quote_id=now['quote_id'], provider=now['provider'], now_pct=str(pct(_picked('winner', now['p_home'], side))))
    if o is None:
        out['note'] = 'no same-provider opening quote'
        return out
    out.update(open_quote_id=o[0], open_pct=str(pct(_picked('winner', o[1], side))))
    if o[0] == now['quote_id']:
        out['note'] = 'opening = now'
        return out
    move = Decimal(out['now_pct']) - Decimal(out['open_pct'])
    out['movement'] = str(move)
    out['item'] = 1 if move >= H4_MOVE else -1 if move <= -H4_MOVE else 0
    return out


# ---- the picture of one card --------------------------------------------------------------------------------------------
def _avoid(ph: dict) -> bool:
    """v4 section 2.4: the card's own AVOID on the live exact-percentage record (history never counts)."""
    return ph.get('rank') is not None and (ph.get('losses') or 0) > (ph.get('wins') or 0)


def _order(c: dict) -> tuple:
    return (MARKET_ORDER[c['market']], c['forecast_id'])


def named_pick(candidates: list) -> Optional[dict]:
    """v4 section 4: the candidate with score >= 2, by higher score, higher book %, higher app %, market order, lower
    forecast_id; None ("none stands out") below the floor."""
    able = [c for c in candidates if c['score'] >= FLOOR]
    return min(able, key=lambda c: (-c['score'], -Decimal(c['book_pct']), -Decimal(c['app_pct']), *_order(c))) if able else None


def baseline_pick(candidates: list) -> Optional[dict]:
    """v4 section 5: the candidate with the highest book %; ties higher app %, market order, lower forecast_id."""
    return min(candidates, key=lambda c: (-Decimal(c['book_pct']), -Decimal(c['app_pct']), *_order(c))) if candidates else None


def _score(sport: str, c: dict, same: dict, b2b: dict) -> dict:
    """The H items of one pick (v4 section 3). `same`: {market: pick} of the same game; b2b: {'home': bool, 'away': bool}."""
    m, side, app, book = c['market'], c['side'], Decimal(c['app_pct']), Decimal(c['book_pct'])
    h = {}

    def side_of(p):
        return (p.get('percentage_history') or {}).get('side')
    if sport == 'nba' and m == 'winner':
        h['H1'] = 1 if book >= H1_HIGH else -1 if book <= H1_LOW else 0
        h['H2'] = 1 if app < book else -1 if app - book >= H2_ABOVE else 0
        sp = same.get('spread')
        h['H3'] = 0 if sp is None else 1 if side_of(sp) != side else -1
    elif sport == 'nhl' and m == 'spread':
        w = same.get('winner')
        h['H5a'] = 0 if w is None else 1 if side_of(w) == side else -1
        h['H5b'] = 1 if app >= H5B_APP else 0
        h['H5c'] = -1 if b2b['home' if side == 'a' else 'away'] else 0
    elif m == 'total':
        h['H6'] = -1 if b2b['home'] or b2b['away'] else 0
    elif sport == 'nhl' and m == 'reg3':
        w = same.get('winner')
        h['H7'] = 1 if (w is not None and pct(w['probability']) >= H7_WINNER_APP and side_of(w) == side) else -1
    return h


def card_picture(conn, sport: str, report: dict, game_id: str, facts) -> dict:
    """The full picture of one card's game at send time. Raises on a problem (the caller records it as an error)."""
    picks = [p for p in report['picks'] if p.get('game_id') == game_id]
    if not picks:
        raise ValueError('no pick of this game in the report')
    rule = sport in RULE_MARKETS
    g = conn.execute('SELECT * FROM games WHERE game_id=?', (game_id,)).fetchone() if rule else None
    if rule and g is None:
        raise ValueError('game not in the store')
    b2b = None
    if rule:
        b2b = {'home': played_previous_day(conn, g['home_team_id'], g['tipoff_at']),
               'away': played_previous_day(conn, g['away_team_id'], g['tipoff_at'])}
    same = {}
    for p in picks:
        same.setdefault(p.get('market'), p)
    rows = []
    for p in picks:
        ph = p.get('percentage_history') or {}
        c = {'forecast_id': p.get('forecast_id'), 'market': p.get('market'), 'pick_label': p.get('pick_label'),
             'side': ph.get('side'), 'generated_at': p.get('generated_at'),
             'app_raw': p.get('probability'), 'app_pct': None if p.get('probability') is None else str(pct(p['probability'])),
             'book_raw': p.get('market_prob'),
             'book_pct': None if p.get('market_prob') is None else str(pct(p['market_prob'])),
             'record': {k: ph.get(k) for k in ('percentage', 'side', 'wins', 'losses', 'settled', 'rank', 'ranked', 'state',
                                               'source', 'model_version', 'line_source')},
             'avoid': _avoid(ph), 'excluded': [], 'candidate': False, 'h': {}, 'score': None}
        rows.append(c)
        if not rule:
            continue
        if c['market'] not in RULE_MARKETS[sport]:
            c['excluded'].append('market')
            continue
        if c['app_raw'] is None or c['book_raw'] is None:
            c['excluded'].append('no app % or no book %')
            continue
        if c['side'] not in ('a', 'mid', 'b') or c['forecast_id'] is None or not c['generated_at']:
            raise ValueError(f"pick {c['forecast_id']} has no side, forecast id or generated_at")
        if isinstance(facts, Exception):
            flags = [f'lineup facts unavailable ({type(facts).__name__})']
        else:
            try:
                from . import lineup
                flags = list(lineup.review_flags(sport, p, (facts or {}).get(game_id)))
            except Exception as exc:
                flags = [f'lineup review failed ({type(exc).__name__})']
        c['lineup_flags'] = flags
        c['quote'] = _quote(conn, sport, p, c['side'])
        h = _score(sport, c, same, b2b)
        if sport == 'nba' and c['market'] == 'winner':
            c['h4'] = _h4(conn, p, c['side'])
            h['H4'] = c['h4']['item']
        c['h'], c['score'] = h, sum(h.values())
        if flags:
            c['excluded'].append('lineup flag')
        if c['avoid']:
            c['excluded'].append('AVOID')
        c['candidate'] = not c['excluded']
    cands = [c for c in rows if c['candidate']]
    named = named_pick(cands) if rule else None
    base = baseline_pick(cands) if rule else None
    fact = None if facts is None or isinstance(facts, Exception) else (facts or {}).get(game_id)
    return {'sport': sport, 'game_id': game_id, 'matchup': picks[0].get('matchup'), 'tipoff_at': picks[0].get('tipoff'),
            'as_of': report.get('as_of'), 'rule_applies': rule, 'floor': FLOOR, 'played_previous_day': b2b,
            'lineup_facts': fact if not isinstance(facts, Exception) else {'error': type(facts).__name__},
            'picks': rows, 'named_forecast_id': named and named['forecast_id'],
            'baseline_forecast_id': base and base['forecast_id'],
            'excluded_lineup': sum('lineup flag' in c['excluded'] for c in rows),
            'excluded_avoid': sum('AVOID' in c['excluded'] for c in rows)}


def card_pictures(conn, sport: str, report: dict, now: datetime) -> dict:
    """{game_id: picture, or {'error': ...}} for every game whose card may go out now (start within two hours). Never
    raises."""
    try:
        soon = sorted({p['game_id'] for p in report['picks']
                       if now < _parse(p['tipoff']) <= now + timedelta(hours=2)})
    except Exception as exc:
        _log(f'cannot list the games of the report: {type(exc).__name__}')
        return {}
    facts = None
    if sport in RULE_MARKETS and soon:
        try:
            from . import lineup
            facts = lineup.facts(conn, sport, soon, report['as_of'])
        except Exception as exc:
            facts = exc
    out = {}
    for gid in soon:
        try:
            out[gid] = card_picture(conn, sport, report, gid, facts)
        except Exception as exc:
            out[gid] = {'error': f'{type(exc).__name__}: {exc}'[:300], 'sport': sport, 'game_id': gid}
    return out


def _log(message: str) -> None:
    print(f'best-pick recorder (card unaffected): {message}', file=sys.stderr, flush=True)


def record(conn, key: str, sport: str, game_id: Optional[str], picture: Optional[dict], recorded_at: str,
           written_at: Optional[str] = None) -> bool:
    """One append-only row for a sent card (INSERT OR IGNORE on the delivery key: the first row stays). Never raises."""
    try:
        conn.executescript(SCHEMA)
        prereg = _sha256(PREREG_PATH)
        error = None
        if picture is None:
            error = 'no picture was built for this card'
        elif 'error' in picture and 'picks' not in picture:
            error = picture['error']
        if prereg != PREREG_SHA256 and error is None:
            error = 'preregistration file missing or changed'
        pic = picture if picture is not None and 'picks' in picture else None
        named = baseline = None
        if pic:
            by = {c['forecast_id']: c for c in pic['picks']}
            named, baseline = by.get(pic['named_forecast_id']), by.get(pic['baseline_forecast_id'])
        b2b = (pic or {}).get('played_previous_day') or {}
        conn.execute('INSERT OR IGNORE INTO best_picks VALUES (' + ','.join('?' * 27) + ')', (
            key, recorded_at, written_at or _stamp(datetime.now(timezone.utc)), sport,
            game_id or (picture or {}).get('game_id') or '', (pic or {}).get('matchup'), (pic or {}).get('tipoff_at'),
            'error' if error else 'ok', error, prereg, RECORDER_VERSION, RECORDER_SHA256,
            os.environ.get('NBA_RELEASE_COMMIT'), int(sport in RULE_MARKETS),
            named and named['forecast_id'], named and named['score'], named and named['market'], named and named['pick_label'],
            baseline and baseline['forecast_id'], baseline and baseline['book_pct'], baseline and baseline['book_raw'],
            None if pic is None else sum(c['candidate'] for c in pic['picks']),
            None if pic is None else pic['excluded_lineup'], None if pic is None else pic['excluded_avoid'],
            None if 'home' not in b2b else int(b2b['home']), None if 'away' not in b2b else int(b2b['away']),
            json.dumps(picture if picture is not None else {}, sort_keys=True, default=str)))
        if error:
            _log(f'{sport} {game_id}: recorded as error: {error}')
        return True
    except Exception as exc:
        _log(f'{sport} {game_id}: row not written: {type(exc).__name__}')
        return False
