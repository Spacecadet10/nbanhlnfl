"""Build <data>/research/telegram_history.json for threshold_overview.telegram_record (owner's option A, Nov 16 2026).

Input: docs/telegram_record.json (scripts/grade_telegram_export.py: Telegram cards transcribed and graded, one card per
game, the last sent before the start). Output rows use the app's keys: sport, market, exact displayed percentage, side
(a/mid/b) and line source. Only games that started before the sport's live ledger began are kept, so nothing the live
record already counts appears twice. Soccer is skipped (the app has no exact-percentage record for it).

Usage: python scripts/build_telegram_history.py --data-dir DIR [--record docs/telegram_record.json] [--dry-run]
Reads the sport stores read-only; writes only the one output file.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

MARKET = {'winner': 'winner', 'spread': 'spread', 'puck line': 'spread', 'total': 'total',
          'home team': 'team_total_home', 'away team': 'team_total_away', 'regulation': 'reg3'}
LINE = {'Bookmaker line at forecast': 'bookmaker', 'Model line': 'model'}


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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', required=True)
    ap.add_argument('--record', default=str(Path(__file__).resolve().parents[1] / 'docs' / 'telegram_record.json'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    data = Path(a.data_dir)
    record = json.loads(Path(a.record).read_text())
    began, teams = {}, {}
    for sport in ('nba', 'nhl', 'nfl'):
        path = (data / (sport + '.sqlite')).resolve()
        try:
            db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
            db.execute('SELECT 1 FROM games LIMIT 1')
        except sqlite3.OperationalError:   # a WAL store with no -shm file cannot open mode=ro; read the file as is
            db = sqlite3.connect(f'file:{path}?immutable=1', uri=True)
        began[sport] = db.execute("SELECT MIN(generated_at) FROM game_forecasts").fetchone()[0]
        for gid, tip, h, w in db.execute("SELECT g.game_id, g.tipoff_at, h.abbr, a.abbr FROM games g JOIN teams h ON "
                                         "h.team_id=g.home_team_id JOIN teams a ON a.team_id=g.away_team_id "
                                         "WHERE g.tipoff_at >= '2026-11-01'"):
            teams[gid] = (tip, h, w)
        db.close()
    rows, skipped = [], {'soccer': 0, 'after_live_began': 0, 'ungraded': 0, 'unmapped': 0}
    for r in record['per_game_rows']:
        sport = r['sport'].lower()
        if sport == 'soccer':
            skipped['soccer'] += 1; continue
        if r['result'] not in ('win', 'loss', 'push'):
            skipped['ungraded'] += 1; continue
        tip, home, away = teams.get(r['game_id'], (None, None, None))
        if tip is None or (began[sport] and tip >= began[sport]):
            skipped['after_live_began'] += 1; continue
        market = MARKET.get(r['market_key'])
        s = side(market, r['selection'], home, away) if market else None
        line_source = 'none' if market in ('winner', 'reg3') else next(
            (v for k, v in LINE.items() if (r['line_source'] or '').startswith(k)), None)
        if market is None or s is None or line_source is None:
            skipped['unmapped'] += 1; continue
        rows.append({'sport': sport, 'market': market, 'percentage': f"{r['percent']:.1f}", 'side': s,
                     'line_source': line_source, 'game_id': r['game_id'], 'grade': r['result'],
                     'pick': r['selection'], 'matchup': r['matchup'], 'final_score': r['final_score'],
                     'message_id': r['message_id']})
    tips = sorted(teams[r['game_id']][0] for r in rows)
    out = {'source': 'telegram_cards', 'built_from': 'docs/telegram_record.json',
           'live_ledger_began': began, 'period': {'from': tips[0][:10], 'to': tips[-1][:10]} if tips else None,
           'rows': rows}
    print(json.dumps({'rows': len(rows), 'skipped': skipped, 'period': out['period'], 'live_ledger_began': began}, indent=1))
    if not a.dry_run:
        path = data / 'research' / 'telegram_history.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(out, indent=1))
        tmp.replace(path)
        print('wrote', path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
