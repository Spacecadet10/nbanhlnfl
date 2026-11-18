"""Soccer: live exact-percentage record, sportsbook comparison and record_basis on the card contract (Nov 17 2026).

The same semantics as threshold_overview.percentage_record for the other sports:
- key = (exact displayed %, side, source, model version, line source), one record per market;
- ONE canonical pregame forecast per match and market: the latest forecast version (soccer_live.forecasts / the latest
  soccer_market_forecasts row), earlier versions are kept in the ledger and listed in `record_basis`;
- the match itself is excluded from its own record; a result counts only once available (result_available_at <= as_of);
- 1X2 (home/draw/away), double chance and draw no bet (from the 1X2 forecast), total goals and every other served goal/corner market the
  cards show. Draw no bet: the displayed/stored probability is the chance the leg wins outright (draws are a push, graded
  'push', counted apart); only the SPORTSBOOK comparison is conditional on no draw. Total goals on a whole line likewise: the
  stored probability is unchanged, the comparison is conditional on no push (soccer_book).
Display only: nothing here changes a forecast, a pick, a delivery key or a ticket.
"""
import os
from typing import Optional

from . import soccer_book, soccer_markets as sm, soccer_markets_live as sml, threshold_overview as to

SOURCE = 'soccer_live'
DIRECTION = {'1x2_regulation': {'home': 'home win', 'draw': 'draw', 'away': 'away win'},
             'double_chance': {'home_draw': 'home or draw', 'draw_away': 'draw or away', 'home_away': 'home or away'},
             'draw_no_bet': {'home': 'home (draw no bet)', 'away': 'away (draw no bet)'}}
_cache: dict = {}


def pick_side(pick: dict) -> str:
    return pick['outcome'] if pick['market'] == '1x2_regulation' else pick['side']


def line_source(pick: dict) -> str:
    return 'none' if pick['market'] == '1x2_regulation' else (pick.get('quote_check') or {}).get('line_source', 'model')


def derived_choice(p1: dict) -> dict:
    """The double chance and draw no bet sides a card shows for a 1X2 forecast {home, draw, away} (tickets.soccer_market_picks)."""
    least = min(('home', 'draw', 'away'), key=lambda k: (p1[k], k))
    return {'double_chance': {'home': 'draw_away', 'draw': 'home_away', 'away': 'home_draw'}[least],
            'draw_no_bet': 'home' if p1['home'] >= p1['away'] else 'away'}


def _forecast_row(conn, pick: dict):
    """The stored forecast row behind a pick: (table, row)."""
    if pick['market'] in ('1x2_regulation', *sml.DERIVED):
        rid = pick['forecast_id'] if pick['market'] == '1x2_regulation' else pick['row_id']
        return 'soccer_live_forecasts', conn.execute('SELECT * FROM soccer_live_forecasts WHERE forecast_id=?', (rid,)).fetchone()
    return 'soccer_market_forecasts', conn.execute('SELECT * FROM soccer_market_forecasts WHERE forecast_id=?', (pick['row_id'],)).fetchone()


def model_version(conn, pick: dict) -> str:
    _, row = _forecast_row(conn, pick)
    return f"{row['model']}:{(row['candidate_sha256'] or '')[:12]}"


def rec_of(conn, pick: dict) -> dict:
    """The percentage_record input of a pick, as the other sports' canonical record: confidence, side, source, model version,
    line source (+ title and direction, which the other sports take from markets.py)."""
    side = pick_side(pick)
    direction = DIRECTION.get(pick['market'], {}).get(side) or (side if pick.get('line') is None else f"{side} {pick['line']:g}")
    return {'confidence': pick['probability'], 'side': side, 'source': SOURCE, 'model_version': model_version(conn, pick),
            'line_source': line_source(pick), 'market_title': pick['market_title'], 'direction': direction}


def all_picks(conn, as_of: str) -> list:
    """Every pick the cards show for every recorded match (1X2, double chance, draw no bet, goal and corner markets), graded on
    the results available at `as_of`."""
    from . import tickets
    return tickets.soccer_1x2_picks(conn, as_of) + tickets.soccer_market_picks(conn, as_of, since='')


def build_index(conn, as_of: str, picks: Optional[list] = None) -> dict:
    """{market: {key: {match_id: grade}}}, the canonical pregame cohort. A grade stays 'pending' until the result is available."""
    out: dict = {}
    for pick in (picks if picks is not None else all_picks(conn, as_of)):
        if not pick['generated_at'] < pick['tipoff']:
            continue
        rec = rec_of(conn, pick)
        out.setdefault(pick['market'], {}).setdefault(to._percentage_key(rec), {})[pick['game_id']] = pick['grade']
    return out


def live_since(conn) -> Optional[str]:
    return conn.execute('SELECT MIN(generated_at) FROM soccer_live_forecasts').fetchone()[0]


def percentage_history(conn, pick: dict, index: dict, as_of: str, since: Optional[str]) -> dict:
    return to.percentage_record('soccer', pick['market'], rec_of(conn, pick), pick['game_id'], index.get(pick['market'], {}),
                                as_of, live_since=since)


# --- Sportsbook comparison ------------------------------------------------------------------------------------------
def _gap(app: float, book: float) -> float:
    return soccer_book._gap(app, book)


def _unsupported(app: float) -> dict:
    return {'status': 'unsupported', 'app_probability': app, 'book_probability': None, 'gap_pp': None, 'provider': None,
            'price': None, 'line': None, 'quote_id': None, 'retrieved_at': None,
            'reason': 'No sportsbook quotes are collected for this market.', 'basis': None}


def sportsbook(conn, pick: dict) -> dict:
    """The contract's SportsbookComparison dict for a Soccer pick: only quotes retrieved at or before the forecast and before
    kickoff. Draw no bet: BOTH percentages conditional on no draw. Total goals: conditional on no push (soccer_book)."""
    market, side, gen, kick = pick['market'], pick_side(pick), pick['generated_at'], pick['tipoff']
    probs = pick.get('outcome_probabilities') or {}
    if market == '1x2_regulation':
        f = {'match_id': pick['game_id'], 'generated_at': gen, 'kickoff_at': kick, 'most_likely': pick['outcome'],
             'probabilities': pick['probabilities']}
        return {**soccer_book.one_x_two(conn, f), 'basis': None}
    if market in sml.DERIVED:
        q = soccer_book.latest_1x2(conn, pick['game_id'], gen, kick)
        app = float(pick['probability'])
        if q is None:
            return {**soccer_book._empty('no_quote', app, 'No complete 1X2 sportsbook quote was saved at or before this forecast.'),
                    'basis': None}
        d = q['devig']
        common = {'provider': q['provider'], 'line': None, 'quote_id': q['quote_id'], 'retrieved_at': q['retrieved_at'],
                  'price': '/'.join(soccer_book._fmt(q['price'][s]) for s in ('home', 'draw', 'away')), 'reason': None}
        if market == 'double_chance':
            book = sum(d[k] for k in side.split('_'))
            return {'status': 'exact', 'app_probability': app, 'book_probability': book, 'gap_pp': _gap(app, book), **common,
                    'basis': 'derived from 1X2 prices'}
        win, loss = probs.get('win', app), probs.get('loss', 0.0)
        app_c = sm.conditional_on_no_draw({'win': win, 'loss': loss}) if win + loss > 0 else app
        book = d[side] / (d['home'] + d['away'])
        return {'status': 'exact', 'app_probability': app_c, 'book_probability': book, 'gap_pp': _gap(app_c, book), **common,
                'basis': 'conditional on no draw (derived from 1X2 prices)'}
    if market == 'goals_total':
        sel = {'probabilities': probs, 'source': (pick.get('quote_check') or {}).get('line_source'), 'line': pick['line'], 'side': side}
        quotes = sml.quotes(conn, pick['game_id'], min(kick, gen))
        out = soccer_book.total_goals(sel, quotes)
        pushy = bool(probs.get('push') or probs.get('half_win') or probs.get('half_loss'))
        return {**out, 'basis': 'conditional on no push' if pushy else None}
    return _unsupported(float(pick['probability']))


# --- record_basis ---------------------------------------------------------------------------------------------------
def _derived_item(row, market: str) -> dict:
    p1 = {'home': row['p_home'], 'draw': row['p_draw'], 'away': row['p_away']}
    if market == '1x2_regulation':
        side = max(('home', 'draw', 'away'), key=lambda k: (p1[k], -('home', 'draw', 'away').index(k)))
        prob = p1[side]
    else:
        side = derived_choice(p1)[market]
        prob = sm.probabilities_1x2((p1['home'], p1['draw'], p1['away']), market, side)['win']
    return {'forecast_id': row['forecast_id'], 'generated_at': row['generated_at'], 'side': side, 'line': None,
            'probability': prob, 'model_version': f"{row['model']}:{(row['candidate_sha256'] or '')[:12]}"}


def _family_item(row, pick: dict) -> dict:
    import json
    joint = json.loads(row['joint_json'])
    line = pick.get('line')
    best = max(sm.SIDES[pick['market']], key=lambda s: sm.probabilities(joint, pick['market'], s, line)['win'])
    prob = sm.probabilities(joint, pick['market'], best, line)['win']
    return {'forecast_id': row['forecast_id'], 'generated_at': row['generated_at'], 'side': best, 'line': line,
            'probability': prob, 'model_version': f"{row['model']}:{(row['candidate_sha256'] or '')[:12]}"}


def record_basis(conn, pick: dict, published: Optional[dict], as_of: str) -> dict:
    from . import published_picks
    market = pick['market']
    table, cur = _forecast_row(conn, pick)
    if table == 'soccer_live_forecasts':
        rows = conn.execute('SELECT * FROM soccer_live_forecasts WHERE match_id=? ORDER BY version', (pick['game_id'],)).fetchall()
        items = [_derived_item(r, market) for r in rows]
        current = _derived_item(cur, market)
    else:
        rows = conn.execute('SELECT * FROM soccer_market_forecasts WHERE match_id=? AND family=? ORDER BY version',
                            (pick['game_id'], cur['family'])).fetchall()
        items = [_family_item(r, pick) for r in rows]
        current = _family_item(cur, pick)
    earlier = [x for x in items if x['forecast_id'] != current['forecast_id']]
    row = (published or {}).get((pick['game_id'], market))
    pub = None
    if row is not None:
        pub = {'forecast_id': published_picks.row_id(row['forecast_id']), 'generated_at': row['generated_at'], 'side': row['side'],
               'line': row['line'], 'probability': row['probability'], 'model_version': row['model_version'],
               'sent_at': row['sent_at'], 'message_id': row['message_id'],
               'grade': published_picks.grade(conn, 'soccer', row, None, as_of)}
    return {'forecast_id': current['forecast_id'], 'generated_at': current['generated_at'], 'model_version': current['model_version'],
            'side': pick_side(pick), 'line': pick.get('line'), 'probability': pick['probability'],
            'pregame_forecasts': len(items), 'earlier': earlier, 'published': pub}


# --- Attach to picks / matches --------------------------------------------------------------------------------------
def _index(conn, as_of: str) -> dict:
    """The record index, cached by store file time and minute (it covers every match, so it is not rebuilt per request)."""
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    stamp = tuple(os.stat(f).st_mtime_ns if os.path.exists(f) else 0 for f in (path, path + '-wal'))
    key = (path, stamp, as_of[:16])
    if _cache.get('key') != key:
        _cache.clear()
        _cache.update(key=key, index=build_index(conn, as_of))
    return _cache['index']


def attach(conn, picks: list, as_of: str, published: Optional[dict] = None, index: Optional[dict] = None) -> None:
    """Adds percentage_history, sportsbook, record_basis, line, side and model_version to Soccer picks (in place). Each field
    is attached independently: a problem with one never hides the pick or the others."""
    from . import published_picks
    index = index if index is not None else _index(conn, as_of)
    since = live_since(conn)
    if published is None:
        published = published_picks.load('soccer')
    for pick in picks:
        pick.setdefault('line', pick.get('line'))
        for name, fn in (('percentage_history', lambda: percentage_history(conn, pick, index, as_of, since)),
                         ('sportsbook', lambda: sportsbook(conn, pick)),
                         ('record_basis', lambda: record_basis(conn, pick, published, as_of)),
                         ('model_version', lambda: model_version(conn, pick))):
            try:
                pick[name] = fn()
            except Exception:       # noqa: BLE001  (display only)
                pick.setdefault(name, None)


def attach_to_live(conn, rows: list, as_of: str) -> None:
    """GET /api/soccer/live: each forecast row (soccer_live.forecast_dict) gets the contract fields of its 1X2 pick."""
    from . import tickets
    picks = {p['forecast_id']: p for p in tickets.soccer_1x2_picks(conn, as_of)}
    attach(conn, [picks[f['forecast_id']] for f in rows if f['forecast_id'] in picks], as_of)
    for f in rows:
        p = picks.get(f['forecast_id'])
        if p is None:
            continue
        for key in ('market', 'market_title', 'pick_label', 'line', 'probability', 'percentage_history', 'record_basis', 'model_version'):
            f[key] = p.get(key)
        if f.get('sportsbook') is None:
            f['sportsbook'] = p.get('sportsbook')
        elif isinstance(f['sportsbook'], dict):
            f['sportsbook'].setdefault('basis', None)


def attach_to_markets(conn, matches: list, as_of: str, since: str) -> None:
    """GET /api/soccer/markets: each match gets `picks`, the double chance / draw no bet / goal / corner picks the cards show,
    with the contract fields (the match's own selections are unchanged)."""
    from . import tickets
    wanted = {m['match_id'] for m in matches}
    picks = [p for p in tickets.soccer_market_picks(conn, as_of, since=since) if p['game_id'] in wanted]
    attach(conn, picks, as_of)
    for m in matches:
        m['picks'] = [p for p in picks if p['game_id'] == m['match_id']]
        # The page renders the match's own selections, so the pick's contract fields go onto the selection it is (same market,
        # side and line); the other sides of a market have no forecast pick of their own and carry none.
        sels = list((m.get('derived') or {}).get('selections') or []) + [s for f in m['families'].values() for s in f['selections']]
        for p in m['picks']:
            for s in sels:
                if (s['market'], s['side'], s['line']) == (p['market'], p['side'], p.get('line')):
                    for name in ('pick_label', 'percentage_history', 'sportsbook', 'record_basis'):
                        if p.get(name) is not None:
                            s[name] = p[name]
