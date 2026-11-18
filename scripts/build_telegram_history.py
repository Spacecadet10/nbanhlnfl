"""Build <data>/research/telegram_history.json for threshold_overview.telegram_record (owner's option A, Nov 16 2026;
extended Nov 17: Soccer, regraded rows, exact overlap rule).

Input: docs/telegram_record.json (scripts/grade_telegram_export.py: Telegram cards transcribed and graded, one card per
game, the last sent before the start). Output rows use the app's keys: sport, market, exact displayed percentage (the
card's own text: 57.3 is not 57.4), side and line source.

Rules (declared before this run):
- One row per (sport, game, market); a duplicate in the source is an error, not a silent drop.
- A row is excluded only when the LIVE record already counts that game and market: NBA/NHL/NFL/NCAAB, a canonical pregame
  game_forecasts row (status 'forecast', made before the start); Soccer, a soccer_live_forecasts row for the match (it
  carries the 1X2, double chance and draw-no-bet selections). The old rule (every game after the sport's first forecast) was
  broader than the live record.
- A row the export left ungraded is graded from the store's final score when the store has one; `grade_source` says which.
  A graded row whose store score disagrees is kept as exported and listed under `disagreements`.
- Soccer rows use the app's soccer sides (home/draw/away; home_draw/draw_away/home_away; home/away) and market ids
  (1x2_regulation, double_chance, draw_no_bet). The 90-minute result decides; a draw-no-bet draw is a push.
- Every excluded row is listed with its reason in `excluded`; counts per sport are in `summary`.

Usage: python scripts/build_telegram_history.py --data-dir DIR [--record docs/telegram_record.json] [--dry-run] [--out FILE]
Reads the sport stores read-only; writes only the one output file (default <data>/research/telegram_history.json).
"""
import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

MARKET = {'winner': 'winner', 'spread': 'spread', 'puck line': 'spread', 'total': 'total',
          'home team': 'team_total_home', 'away team': 'team_total_away', 'regulation': 'reg3',
          'match result': '1x2_regulation', 'double chance': 'double_chance', 'draw no bet': 'draw_no_bet'}
LINE = {'Bookmaker line at forecast': 'bookmaker', 'Model line': 'model'}
POINT_SPORTS = ('nba', 'nhl', 'nfl')
LABEL = 'Recovered Telegram cards'


def norm(x):
    return re.sub(r'[^a-z]', '', (x or '').lower())


def side(market, selection, home, away):
    sel = selection.strip()
    low = sel.lower()
    if market in ('total', 'team_total_home', 'team_total_away'):
        m = re.search(r'\b(over|under)\b', low)
        return {'over': 'a', 'under': 'b'}[m.group(1)] if m else None
    if market == 'reg3' and low.startswith('draw'):
        return 'mid'
    team = norm(sel.split()[0])
    return 'a' if team == norm(home) else 'b' if team == norm(away) else None


def selection_line(selection):
    m = re.search(r'([+-]?\d+(?:\.\d+)?)\s*$', selection.strip())
    return float(m.group(1)) if m else None


def soccer_side(market, selection, home, away):
    """The app's soccer side of a card line such as 'Tahiti win (90 min)', 'Tahiti or draw (90 min)',
    'Japan or Brazil (90 min)', 'Tahiti (draw no bet, 90 min)'."""
    body = re.sub(r'\s*\(.*\)\s*$', '', selection.strip())
    h, a = norm(home), norm(away)
    if market == '1x2_regulation':
        if body.lower().startswith('draw'):
            return 'draw'
        name = norm(re.sub(r'\bwin$', '', body.strip(), flags=re.I))
        return 'home' if name == h else 'away' if name == a else None
    if market == 'draw_no_bet':
        name = norm(body)
        return 'home' if name == h else 'away' if name == a else None
    if market == 'double_chance':
        parts = [norm(x) for x in re.split(r'\s+or\s+', body, flags=re.I)]
        if len(parts) != 2:
            return None
        key = {'home': 'home', 'away': 'away', 'draw': 'draw'}
        out = []
        for part in parts:
            out.append('draw' if part == 'draw' else 'home' if part == h else 'away' if part == a else None)
        if None in out or out[0] == out[1]:
            return None
        order = {'home': 0, 'draw': 1, 'away': 2}
        a1, a2 = sorted(out, key=order.get)
        return f'{a1}_{a2}'
    return None


def open_store(data, sport):
    path = (Path(data) / (sport + '.sqlite')).resolve()
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        db.execute('SELECT 1 FROM sqlite_master LIMIT 1')
    except sqlite3.OperationalError:   # a WAL store with no -shm file cannot open mode=ro; read the file as is
        db = sqlite3.connect(f'file:{path}?immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    return db


def grade_point_row(market, s, line, game, decision):
    """win/loss/push from the store's final score, or None when the store has no usable final."""
    from app import markets
    if game['status'] != 'final' or game['home_score'] is None:
        return None
    realised = markets.settle(market, line, status='final', home=game['home_score'], away=game['away_score'], decision=decision)
    if realised in ('pending', 'void', 'unscorable'):
        return None
    if realised == 'mid' and market != 'reg3':
        return 'push'
    return 'win' if realised == s else 'loss'


def grade_soccer_row(market, s, match):
    from app import soccer_markets as sm
    if match['status'] != 'final' or match['decided_in'] != 'regulation' or match['home_score'] is None:
        return None
    h, a = match['home_score'], match['away_score']
    if market == '1x2_regulation':
        out = 'home' if h > a else 'draw' if h == a else 'away'
        return 'win' if out == s else 'loss'
    return sm.grade_1x2_market((h, a), market, s)


def build(record, data_dir):
    data = Path(data_dir)
    stores, games, canon = {}, {}, {}
    for sport in POINT_SPORTS:
        db = open_store(data, sport)
        stores[sport] = db
        for g in db.execute("SELECT g.game_id, g.tipoff_at, g.status, g.home_score, g.away_score, h.abbr AS home, a.abbr AS away "
                            "FROM games g JOIN teams h ON h.team_id=g.home_team_id JOIN teams a ON a.team_id=g.away_team_id "
                            "WHERE g.tipoff_at >= '2026-11-01'"):
            games[g['game_id']] = dict(g, sport=sport)
        try:
            dec = dict(db.execute('SELECT game_id, decision FROM game_decisions').fetchall())
        except sqlite3.OperationalError:
            dec = {}
        for gid in games:
            if games[gid]['sport'] == sport:
                games[gid]['decision'] = dec.get(gid)
        for r in db.execute("SELECT game_id, market, MIN(generated_at) AS first, COUNT(*) AS n FROM game_forecasts "
                            "WHERE status='forecast' AND generated_at < tipoff_at GROUP BY game_id, market"):
            canon[(sport, r['game_id'], r['market'])] = r['n']
    soccer = open_store(data, 'soccer')
    matches = {m['match_id']: dict(m) for m in soccer.execute(
        "SELECT m.match_id, m.kickoff_at, m.status, m.decided_in, m.home_score, m.away_score, ht.name AS home, at.name AS away "
        "FROM soccer_matches m JOIN teams ht ON ht.team_id=m.home_team_id JOIN teams at ON at.team_id=m.away_team_id")}
    soccer_live = {r[0] for r in soccer.execute('SELECT DISTINCT match_id FROM soccer_live_forecasts')}
    began = {s: stores[s].execute('SELECT MIN(generated_at) FROM game_forecasts').fetchone()[0] for s in POINT_SPORTS}
    began['soccer'] = soccer.execute('SELECT MIN(generated_at) FROM soccer_live_forecasts').fetchone()[0]

    rows, excluded, disagreements, seen = [], [], [], set()

    def skip(r, sport, reason, detail=None):
        excluded.append({'sport': sport, 'game_id': r['game_id'], 'market_key': r['market_key'], 'selection': r['selection'],
                         'percent': r['percent'], 'message_id': r['message_id'], 'reason': reason, **({'detail': detail} if detail else {})})

    for r in record['per_game_rows']:
        sport = r['sport'].lower()
        ident = (sport, r['game_id'], r['market_key'])
        assert ident not in seen, f'duplicate source row {ident}'
        seen.add(ident)
        market = MARKET.get(r['market_key'])
        if sport == 'soccer':
            m = matches.get(r['game_id'])
            if m is None:
                skip(r, sport, 'game_not_in_store'); continue
            s = soccer_side(market, r['selection'], m['home'], m['away']) if market else None
            if s is None:
                skip(r, sport, 'unmapped'); continue
            if r['game_id'] in soccer_live:
                skip(r, sport, 'live_record_counts_it'); continue
            store_grade = grade_soccer_row(market, s, m)
            tip, line_source, g_line = m['kickoff_at'], ('none' if market == '1x2_regulation' else 'model'), None
        else:
            g = games.get(r['game_id'])
            if g is None or g['sport'] != sport:
                skip(r, sport, 'game_not_in_store'); continue
            s = side(market, r['selection'], g['home'], g['away']) if market else None
            line_source = 'none' if market in ('winner', 'reg3') else next(
                (v for k, v in LINE.items() if (r['line_source'] or '').startswith(k)), None)
            if market is None or s is None or line_source is None:
                skip(r, sport, 'unmapped'); continue
            if (sport, r['game_id'], market) in canon:
                skip(r, sport, 'live_record_counts_it'); continue
            g_line = None if market in ('winner', 'reg3') else selection_line(r['selection'])
            if market == 'spread' and g_line is not None and s == 'b':
                g_line = -g_line      # the card shows the picked team's line; the app stores the home team's
            store_grade = grade_point_row(market, s, g_line, g, g.get('decision'))
            tip = g['tipoff_at']
        exported = r['result'] if r['result'] in ('win', 'loss', 'push') else None
        if exported:
            grade, source = exported, 'telegram_export'
            if store_grade is not None and store_grade != exported:
                disagreements.append({'sport': sport, 'game_id': r['game_id'], 'market': market, 'exported': exported, 'store': store_grade})
        elif store_grade:
            grade, source = store_grade, 'store_final'
        else:
            skip(r, sport, 'no_final_result_in_store'); continue
        rows.append({'sport': sport, 'market': market, 'percentage': f"{r['percent']:.1f}", 'side': s, 'line_source': line_source,
                     'game_id': r['game_id'], 'grade': grade, 'grade_source': source, 'pick': r['selection'],
                     'matchup': r['matchup'], 'final_score': r['final_score'], 'message_id': r['message_id'], 'start_at': tip,
                     'sent_at_local': r.get('sent_at_local')})
    keys = [(x['sport'], x['game_id'], x['market']) for x in rows]
    assert len(keys) == len(set(keys)), 'duplicate (sport, game, market) rows'
    summary = {}
    for sport in ('nba', 'nhl', 'nfl', 'soccer'):
        inc = [x for x in rows if x['sport'] == sport]
        exc = [x for x in excluded if x['sport'] == sport]
        summary[sport] = {'included': len(inc), 'excluded': len(exc), 'excluded_by_reason': dict(Counter(x['reason'] for x in exc)),
                          'grade_source': dict(Counter(x['grade_source'] for x in inc)), 'live_ledger_began': began[sport],
                          'period': ({'from': min(x['start_at'] for x in inc)[:10], 'to': max(x['start_at'] for x in inc)[:10]}
                                     if inc else None)}
    tips = sorted(x['start_at'] for x in rows)
    return {'source': 'telegram_cards', 'label': LABEL, 'built_from': 'docs/telegram_record.json', 'model_version_known': False,
            'live_ledger_began': began, 'period': {'from': tips[0][:10], 'to': tips[-1][:10]} if tips else None,
            'summary': summary, 'rows': rows, 'excluded': excluded, 'disagreements': disagreements}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', required=True)
    ap.add_argument('--record', default=str(Path(__file__).resolve().parents[1] / 'docs' / 'telegram_record.json'))
    ap.add_argument('--out', default=None, help='output file (default <data-dir>/research/telegram_history.json)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    out = build(json.loads(Path(a.record).read_text()), a.data_dir)
    print(json.dumps({'rows': len(out['rows']), 'excluded': len(out['excluded']), 'period': out['period'],
                      'disagreements': len(out['disagreements']), 'summary': out['summary']}, indent=1))
    if not a.dry_run:
        path = Path(a.out) if a.out else Path(a.data_dir) / 'research' / 'telegram_history.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(out, indent=1))
        tmp.replace(path)
        print('wrote', path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
