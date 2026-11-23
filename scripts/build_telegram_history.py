"""Build <data>/research/telegram_history.json for threshold_overview.telegram_record (owner's option A, Nov 16 2026;
extended Nov 17: Soccer, regraded rows, exact overlap rule).

Input: docs/telegram_record.json (scripts/grade_telegram_export.py: Telegram cards transcribed and graded, one card per
game, the last sent before the start). Output rows use the app's keys: sport, market, exact displayed percentage (the
card's own text: 57.3 is not 57.4), side and line source.

Rules (declared before this run):
- One row per (sport, game, market); a duplicate in the source is an error, not a silent drop.
- OVERLAP RULE (Nov 22, owner brief): on the cards the recovered and live records are shown separately and never summed (Performance
  and Prediction History total them with each game and market counted once, app/telegram_history.py), so a game both sources hold is counted ONCE IN EACH displayed record. Such a row is no longer dropped; it is kept with
  `also_in_live: true` (NBA/NHL/NFL/NCAAB: a canonical pregame game_forecasts row, status 'forecast' made before the start;
  Soccer: a soccer_live_forecasts row for the match) and counted in `summary[sport].overlap_with_live`. Before Nov 22 these rows
  were excluded as "live_record_counts_it" (216 of 1189), which left the recovered record without the games Nov 15-16.
- UNSETTLED (Nov 22, owner brief): a row without an exported result and without a store final stays in, grade 'unsettled';
  it is never a loss and never dropped (before, it was excluded as no_final_result_in_store).
- A card sent at or after the start is not a prediction: it is listed in `not_predictions` (sent_after_start), never counted.
- Each row keeps what the card said: pick, line (`line` as printed, `home_line` in the app's home convention), the printed line
  source and sportsbook text, recommended or not, send time, message id, and the game's earlier cards (`earlier_versions`, with
  whether each differed). The export holds no prices: `price` is always None (unpriced).
- `reconciliation[sport]` accounts for every source card row (unaccounted must be 0).
- Rows are excluded only when they cannot be imported: game_not_in_store, unmapped (market/side/line source),
  send_time_missing, or sent_after_start. Each is listed with its reason; `by_sport_date` reports source rows, imported, excluded by reason,
  unmatched and superseded earlier cards (all_card_rows cards that are not the one card per game) by sport and local start date.
- Dates are Toronto local start dates taken from the imported rows (period and `dates`), never assumed.
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
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

MARKET = {'winner': 'winner', 'spread': 'spread', 'puck line': 'spread', 'total': 'total',
          'home team': 'team_total_home', 'away team': 'team_total_away', 'regulation': 'reg3',
          'match result': '1x2_regulation', 'double chance': 'double_chance', 'draw no bet': 'draw_no_bet'}
LINE = {'Bookmaker line at forecast': 'bookmaker', 'Model line': 'model'}
POINT_SPORTS = ('nba', 'nhl', 'nfl')
LABEL = 'Recovered Telegram cards'
TORONTO = ZoneInfo('America/Toronto')
OVERLAP_RULE = ('On the cards, recovered and live records are shown apart and never summed (Performance and Prediction History count '
                'each game and market once): a game both sources hold is counted once in the recovered '
                'record and once in the live record; the recovered rows keep also_in_live=true and are reported, not dropped.')


def sent_date_local(r):
    return r['sent_at_local'][:10]


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


def sent_at_utc(r):
    """The card's send time (the export's local Toronto time) as an aware datetime, or None."""
    at = r.get('sent_at_local')
    return datetime.fromisoformat(at).replace(tzinfo=TORONTO) if at else None


def start_utc(tip):
    return datetime.fromisoformat(tip.replace('Z', '+00:00'))


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
                         'percent': r['percent'], 'message_id': r['message_id'], 'sent_date': sent_date_local(r), 'reason': reason, **({'detail': detail} if detail else {})})

    start_at = lambda gid: (matches[gid]['kickoff_at'] if gid in matches else games[gid]['tipoff_at'] if gid in games else None)  # noqa: E731
    late = lambda r: (s0 := start_at(r['game_id'])) is not None and (t := sent_at_utc(r)) is not None and t >= start_utc(s0)    # noqa: E731
    final_cards = {(r['sport'].lower(), r['game_id'], r['market_key']): r for r in record['per_game_rows']}
    earlier, not_predictions, recon = {}, [], {}
    for r in record.get('all_card_rows') or record['per_game_rows']:
        sport = r['sport'].lower()
        key = (sport, r['game_id'], r['market_key'])
        c = recon.setdefault(sport, {'source_rows': 0, 'one_per_game': 0, 'included': 0, 'excluded': 0, 'unmatched': 0,
                                     'earlier_versions': 0, 'sent_after_start': 0, 'unaccounted': 0, 'unsettled': 0})
        c['source_rows'] += 1
        last = final_cards.get(key)
        if last is not None and last['message_id'] == r['message_id']:
            c['one_per_game'] += 1
        elif late(r):
            c['sent_after_start'] += 1
            not_predictions.append({'sport': sport, 'game_id': r['game_id'], 'market_key': r['market_key'], 'matchup': r['matchup'],
                                    'pick': r['selection'], 'percentage': f"{r['percent']:.1f}", 'message_id': r['message_id'],
                                    'sent_at_local': r['sent_at_local'], 'start_at': start_at(r['game_id']), 'reason': 'sent_after_start'})
        elif last is not None:
            c['earlier_versions'] += 1
            earlier.setdefault(key, []).append(r)
        else:
            c['unaccounted'] += 1
            not_predictions.append({'sport': sport, 'game_id': r['game_id'], 'market_key': r['market_key'], 'matchup': r['matchup'],
                                    'pick': r['selection'], 'percentage': f"{r['percent']:.1f}", 'message_id': r['message_id'],
                                    'sent_at_local': r.get('sent_at_local'), 'start_at': start_at(r['game_id']), 'reason': 'no_card_of_record'})

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
            also_live = r['game_id'] in soccer_live
            store_grade = grade_soccer_row(market, s, m)
            tip, line_source, g_line = m['kickoff_at'], ('none' if market == '1x2_regulation' else 'model'), None
            line = None
            store_score = (f"{m['away']} {m['away_score']} - {m['home_score']} {m['home']}"
                           if m['status'] == 'final' and m['home_score'] is not None else None)
        else:
            g = games.get(r['game_id'])
            if g is None or g['sport'] != sport:
                skip(r, sport, 'game_not_in_store'); continue
            s = side(market, r['selection'], g['home'], g['away']) if market else None
            line_source = 'none' if market in ('winner', 'reg3') else next(
                (v for k, v in LINE.items() if (r['line_source'] or '').startswith(k)), None)
            if market is None or s is None or line_source is None:
                skip(r, sport, 'unmapped'); continue
            also_live = (sport, r['game_id'], market) in canon
            line = g_line = None if market in ('winner', 'reg3') else selection_line(r['selection'])
            if market == 'spread' and g_line is not None and s == 'b':
                g_line = -g_line      # the card shows the picked team's line; the app stores the home team's
            store_grade = grade_point_row(market, s, g_line, g, g.get('decision'))
            tip = g['tipoff_at']
            decided = f" ({g['decision']})" if g.get('decision') else ''
            store_score = (f"{g['away']} {g['away_score']} - {g['home_score']} {g['home']}{decided}"
                           if g['status'] == 'final' and g['home_score'] is not None else None)
        if sent_at_utc(r) is None:
            skip(r, sport, 'send_time_missing'); continue
        if late(r):
            skip(r, sport, 'sent_after_start'); continue
        exported = r['result'] if r['result'] in ('win', 'loss', 'push') else None
        if exported:
            grade, source = exported, 'telegram_export'
            if store_grade is not None and store_grade != exported:
                disagreements.append({'sport': sport, 'game_id': r['game_id'], 'market': market, 'exported': exported, 'store': store_grade})
        elif store_grade:
            grade, source = store_grade, 'store_final'
        else:
            grade, source = 'unsettled', None
        rows.append({'sport': sport, 'market': market, 'percentage': f"{r['percent']:.1f}", 'side': s, 'line_source': line_source,
                     'game_id': r['game_id'], 'grade': grade, 'grade_source': source, 'also_in_live': also_live, 'pick': r['selection'],
                     'line': line, 'home_line': g_line, 'recommended': bool(r.get('recommended')), 'card_line_text': r['line_source'] or None,
                     'price': None, 'earlier_versions': [
                         {'message_id': e['message_id'], 'sent_at_local': e['sent_at_local'], 'pick': e['selection'],
                          'percentage': f"{e['percent']:.1f}", 'card_line_text': e['line_source'] or None,
                          'differs': (e['selection'], e['percent']) != (r['selection'], r['percent']),
                          'note_differs': (e['line_source'] or None) != (r['line_source'] or None)}
                         for e in sorted(earlier.get(ident, []), key=lambda e: e['sent_at_local'])],
                     'matchup': r['matchup'], 'final_score': r['final_score'] or store_score, 'message_id': r['message_id'], 'start_at': tip,
                     'sent_at_local': r.get('sent_at_local')})
    keys = [(x['sport'], x['game_id'], x['market']) for x in rows]
    assert len(keys) == len(set(keys)), 'duplicate (sport, game, market) rows'
    local = lambda x: datetime.fromisoformat(x['start_at'].replace('Z', '+00:00')).astimezone(TORONTO).date().isoformat()
    for x in rows:
        x['start_date_local'] = local(x)
    for sport, c in recon.items():
        c['included'] = sum(1 for x in rows if x['sport'] == sport)
        c['excluded'] = sum(1 for x in excluded if x['sport'] == sport)
        c['unmatched'] = sum(1 for x in excluded if x['sport'] == sport and x['reason'] in ('game_not_in_store', 'unmapped'))
        c['unsettled'] = sum(1 for x in rows if x['sport'] == sport and x['grade'] == 'unsettled')
    sent_ids = {(r['sport'].lower(), r['game_id'], r['market_key'], r['message_id']) for r in record['per_game_rows']}
    superseded = Counter()
    for r in record.get('all_card_rows', []):
        if (r['sport'].lower(), r['game_id'], r['market_key'], r['message_id']) not in sent_ids:
            superseded[(r['sport'].lower(), r['game_id'], sent_date_local(r))] += 1
    by_day: dict = {}     # key: the local START date of an imported row, else the SENT date of one that could not be imported
    def cell(sport, day):
        return by_day.setdefault(sport, {}).setdefault(day, {'date': day, 'source_rows': 0, 'imported': 0, 'overlap_with_live': 0,
                                                              'excluded': 0, 'excluded_by_reason': Counter(), 'unmatched': 0,
                                                              'superseded_earlier_cards': 0})
    start_of = {(x['sport'], x['game_id'], x['market']): x['start_date_local'] for x in rows}
    for r in record['per_game_rows']:
        sport = r['sport'].lower()
        key = (sport, r['game_id'], MARKET.get(r['market_key']))
        c = cell(sport, start_of.get(key) or sent_date_local(r))
        c['source_rows'] += 1
    for x in rows:
        c = cell(x['sport'], x['start_date_local'])
        c['imported'] += 1
        c['overlap_with_live'] += int(x['also_in_live'])
    for e in excluded:
        c = cell(e['sport'], e['sent_date'])      # an excluded row has no start date: its sent date
        c['excluded'] += 1
        c['excluded_by_reason'][e['reason']] += 1
        c['unmatched'] += int(e['reason'] in ('game_not_in_store', 'unmapped'))
    game_day = {(x['sport'], x['game_id']): x['start_date_local'] for x in rows}
    for (sport, gid, sent), n in superseded.items():
        cell(sport, game_day.get((sport, gid), sent))['superseded_earlier_cards'] += n
    by_sport_date = {sp: [dict(v, excluded_by_reason=dict(v['excluded_by_reason'])) for _, v in sorted(d.items())]
                     for sp, d in by_day.items()}
    summary = {}
    for sport in ('nba', 'nhl', 'nfl', 'soccer'):
        inc = [x for x in rows if x['sport'] == sport]
        exc = [x for x in excluded if x['sport'] == sport]
        dates = sorted({x['start_date_local'] for x in inc})
        summary[sport] = {'included': len(inc), 'excluded': len(exc), 'excluded_by_reason': dict(Counter(x['reason'] for x in exc)),
                          'overlap_with_live': sum(1 for x in inc if x['also_in_live']),
                          'unsettled': sum(1 for x in inc if x['grade'] == 'unsettled'),
                          'grade_source': dict(Counter(x['grade_source'] for x in inc if x['grade_source'])), 'live_ledger_began': began[sport],
                          'dates': dates, 'period': {'from': dates[0], 'to': dates[-1]} if dates else None}
    dates = sorted({x['start_date_local'] for x in rows})
    return {'source': 'telegram_cards', 'label': LABEL, 'built_from': 'docs/telegram_record.json', 'model_version_known': False,
            'reconciliation': recon, 'not_predictions': not_predictions, 'overlap_rule': OVERLAP_RULE, 'live_ledger_began': began,
            'period': {'from': dates[0], 'to': dates[-1]} if dates else None,
            'summary': summary, 'by_sport_date': by_sport_date, 'rows': rows, 'excluded': excluded, 'disagreements': disagreements}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', required=True)
    ap.add_argument('--record', default=str(Path(__file__).resolve().parents[1] / 'docs' / 'telegram_record.json'))
    ap.add_argument('--out', default=None, help='output file (default <data-dir>/research/telegram_history.json)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    out = build(json.loads(Path(a.record).read_text()), a.data_dir)
    print(json.dumps({'rows': len(out['rows']), 'excluded': len(out['excluded']), 'period': out['period'],
                      'disagreements': len(out['disagreements']), 'reconciliation': out['reconciliation'], 'summary': out['summary']}, indent=1))
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
