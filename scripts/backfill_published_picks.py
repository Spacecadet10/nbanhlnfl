"""Reconstruct which forecast each ALREADY SENT Telegram card published, into published_picks (append-only), from the
ledger's card text and the stores' forecast rows (Nov 17 2026).

For every sent card: parse the sport, matchup, start time and each market block (title, pick label, displayed Model %),
find the game, and take, per market, the LATEST forecast generated at or before the card's attempted_at whose displayed
percentage and pick label equal the text. Counted as
  unambiguous  exactly one forecast of that game and market matches the text,
  multiple     several forecasts match (identical % and label); the latest one is used, row basis 'backfill_text_match_multiple',
  unmatched    no forecast matches the text (listed with the reason).
Soccer: 1X2, double chance and draw no bet are rebuilt from soccer_live_forecasts versions; goal/corner blocks are not (listed).

Safety: runs only against the ledger file you name, and refuses the live default ledger unless --allow-live is given (it is
meant for a scratch copy). Reads the stores read-only; the only write is INSERT OR IGNORE into published_picks.

Usage: python scripts/backfill_published_picks.py --data-dir DIR --ledger FILE [--report FILE] [--dry-run]
"""
import argparse
import html
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

TORONTO = ZoneInfo('America/Toronto')
HEAD = re.compile(r'PROP51 PICKS · (\w+)</b>\n<b>(.+?)</b>\nStarts (\w{3} \d{1,2}), (\d{1,2}:\d{2} [AP]M)')
MODEL = re.compile(r'<b>Model ([0-9.]+)%</b>')
POINT_SPORTS = ('nba', 'nhl', 'nfl', 'ncaab')


def parse_card(text: str):
    """{'sport', 'matchup', 'start_local': datetime(naive month/day/time), 'blocks': [(title, label, percentage)]} or None."""
    m = HEAD.search(text)
    if not m or '<b>Market breakdown</b>' not in text:
        return None
    start = datetime.strptime(f'{m.group(3)} {m.group(4)}', '%b %d %I:%M %p')
    body = text.split('<b>Market breakdown</b>', 1)[1]
    lines = [x for x in body.split('\n')]
    blocks = []
    for i, line in enumerate(lines):
        if line.startswith('<b>') and line.endswith('</b>') and 'Model ' not in line and i + 2 < len(lines):
            label_line, third = lines[i + 1], lines[i + 2]
            pm = MODEL.search(third)
            if pm and label_line and not label_line.startswith('<'):
                blocks.append((html.unescape(re.sub(r'</?b>', '', line)), html.unescape(label_line.split(' · ', 1)[0].strip()), pm.group(1)))
    return {'sport': m.group(1).lower(), 'matchup': html.unescape(m.group(2)), 'start_local': start, 'blocks': blocks}


def to_utc(start_local: datetime, attempted_at: str) -> str:
    year = int(attempted_at[:4])
    local = start_local.replace(year=year, tzinfo=TORONTO)
    if (local - datetime.fromisoformat(attempted_at.replace('Z', '+00:00'))).days < -30:   # a December card for a January game
        local = local.replace(year=year + 1)
    return local.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%SZ')


def open_store(data, sport):
    path = (Path(data) / (sport + '.sqlite')).resolve()
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        db.execute('SELECT 1 FROM sqlite_master LIMIT 1')
    except sqlite3.OperationalError:
        db = sqlite3.connect(f'file:{path}?immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    return db


def _titles(sport):
    from app import markets, sports
    return {markets.spec(sport, m).title: m for m in sports.store(sport).markets}


def reconstruct_point(store, sport, card, attempted_at):
    """[(market, status, forecast row or None, detail)] for one NBA/NHL/NFL/NCAAB card."""
    from app import coverage, markets
    from app.threshold_overview import displayed_percentage
    away, _, home = card['matchup'].partition(' @ ')
    tip = to_utc(card['start_local'], attempted_at)
    g = store.execute('SELECT g.game_id, g.tipoff_at FROM games g JOIN teams h ON h.team_id=g.home_team_id JOIN teams a ON '
                      'a.team_id=g.away_team_id WHERE h.abbr=? AND a.abbr=? AND g.tipoff_at=?', (home, away, tip)).fetchone()
    if g is None:
        return None, [(None, 'unmatched', None, f'no game {card["matchup"]} at {tip}')]
    titles, out = _titles(sport), []
    for title, label, pct in card['blocks']:
        market = titles.get(title)
        if market is None:
            out.append((None, 'unmatched', None, f'unknown market title {title!r}'))
            continue
        hits = []
        for r in store.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market=? AND status='forecast' AND generated_at <= ? "
                               "AND generated_at < tipoff_at ORDER BY generated_at, forecast_id", (g['game_id'], market, attempted_at)):
            p = (r['p_a'], r['p_mid'], r['p_b'])
            side = coverage.pick_side(market, p)
            labels = markets.side_labels(market, r['line'], home, away)
            if displayed_percentage(r['confidence']) == pct and labels[('a', 'mid', 'b').index(side)] == label:
                hits.append(r)
        if not hits:
            out.append((market, 'unmatched', None, 'no forecast before the send shows this % and label'))
        else:
            out.append((market, 'unambiguous' if len(hits) == 1 else 'multiple', hits[-1], g['game_id']))
    return g['game_id'], out


def reconstruct_soccer(store, card, attempted_at):
    from app import soccer_markets as sm, soccer_record
    from app.threshold_overview import displayed_percentage
    home, _, away = card['matchup'].partition(' vs ')
    tip = to_utc(card['start_local'], attempted_at)
    m = store.execute('SELECT m.match_id FROM soccer_matches m JOIN teams h ON h.team_id=m.home_team_id JOIN teams a ON '
                      'a.team_id=m.away_team_id WHERE h.name=? AND a.name=? AND m.kickoff_at=?', (home, away, tip)).fetchone()
    if m is None:
        return None, [(None, 'unmatched', None, f'no match {card["matchup"]} at {tip}')]
    titles = {'Match result (90 min)': '1x2_regulation', 'Double chance': 'double_chance', 'Draw no bet': 'draw_no_bet'}
    out = []
    rows = store.execute('SELECT * FROM soccer_live_forecasts WHERE match_id=? AND generated_at <= ? ORDER BY version',
                         (m['match_id'], attempted_at)).fetchall()
    for title, label, pct in card['blocks']:
        market = titles.get(title)
        if market is None:
            out.append((None, 'unmatched', None, f'{title}: soccer goal/corner blocks are not reconstructed'))
            continue
        hits = []
        for r in rows:
            p1 = {'home': r['p_home'], 'draw': r['p_draw'], 'away': r['p_away']}
            if market == '1x2_regulation':
                side = max(('home', 'draw', 'away'), key=lambda k: (p1[k], -('home', 'draw', 'away').index(k)))
                prob = p1[side]
            else:
                side = soccer_record.derived_choice(p1)[market]
                prob = sm.probabilities_1x2((p1['home'], p1['draw'], p1['away']), market, side)['win']
            if displayed_percentage(prob) == pct:
                hits.append((r, side, prob))
        if not hits:
            out.append((market, 'unmatched', None, 'no forecast before the send shows this %'))
        else:
            out.append((market, 'unambiguous' if len(hits) == 1 else 'multiple', hits[-1], m['match_id']))
    return m['match_id'], out


def run(data_dir, ledger_path, dry_run=False):
    ledger = sqlite3.connect(ledger_path, isolation_level=None)
    ledger.row_factory = sqlite3.Row
    from app import published_picks
    published_picks.ensure(ledger)
    stores = {s: open_store(data_dir, s) for s in (*POINT_SPORTS, 'soccer')}
    counts, unmatched, written = {}, [], 0
    for d in ledger.execute("SELECT * FROM deliveries WHERE status='sent' ORDER BY attempted_at").fetchall():
        card = parse_card(d['text'])
        if card is None or card['sport'] not in stores:
            unmatched.append({'delivery_key': d['delivery_key'], 'reason': 'not a game card'})
            continue
        sport = card['sport']
        try:
            gid, results = (reconstruct_soccer(stores['soccer'], card, d['attempted_at']) if sport == 'soccer'
                            else reconstruct_point(stores[sport], sport, card, d['attempted_at']))
        except Exception as exc:
            unmatched.append({'delivery_key': d['delivery_key'], 'reason': f'{type(exc).__name__}: {exc}'})
            continue
        c = counts.setdefault(sport, {'unambiguous': 0, 'multiple': 0, 'unmatched': 0})
        for market, status, hit, detail in results:
            c[status] += 1
            if status == 'unmatched':
                unmatched.append({'delivery_key': d['delivery_key'], 'sport': sport, 'game_id': gid, 'market': market, 'reason': detail})
                continue
            if sport == 'soccer':
                r, side, prob = hit
                row = (d['delivery_key'], sport, gid, market, str(r['forecast_id']), side, None, prob,
                       f"{r['model']}:{(r['candidate_sha256'] or '')[:12]}", r['generated_at'])
            else:
                from app import coverage
                row = (d['delivery_key'], sport, gid, market, str(hit['forecast_id']), coverage.pick_side(market, (hit['p_a'], hit['p_mid'], hit['p_b'])),
                       hit['line'], hit['confidence'], hit['model_version'], hit['generated_at'])
            if not dry_run:
                cur = ledger.execute('INSERT OR IGNORE INTO published_picks (delivery_key, sport, game_id, market, forecast_id, side, line, '
                                     'probability, model_version, generated_at, sent_at, message_id, basis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                     (*row, d['attempted_at'], d['message_id'],
                                      'backfill_text_match' if status == 'unambiguous' else 'backfill_text_match_multiple'))
                written += cur.rowcount
    return {'counts': counts, 'written': written, 'unmatched': unmatched, 'dry_run': dry_run}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', required=True)
    ap.add_argument('--ledger', required=True)
    ap.add_argument('--report', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--allow-live', action='store_true', help='permit the live default ledger (owner approval only)')
    a = ap.parse_args(argv)
    from app import config
    if Path(a.ledger).resolve() == Path(config.telegram_ledger_path()).resolve() and not a.allow_live and Path(a.ledger).name == '.telegram-delivery.sqlite':
        print('refusing the live ledger without --allow-live', file=sys.stderr)
        return 2
    report = run(a.data_dir, a.ledger, a.dry_run)
    print(json.dumps({k: v for k, v in report.items() if k != 'unmatched'}, indent=1), f"unmatched rows: {len(report['unmatched'])}")
    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
