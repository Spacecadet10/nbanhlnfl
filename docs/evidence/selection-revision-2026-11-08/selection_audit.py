"""Read-only selection audit: every sent leg vs every other market of the same game, using only information available at
that review's snapshot (the snapshot saved in the sent batch + sportsbook quotes stored by snapshot time). No results used."""
import json, sqlite3, collections, re
from app import db, tickets as t, markets as m
L = sqlite3.connect('file:/Users/andry/Desktop/nba-predictor-sprint1/.telegram-delivery.sqlite?mode=ro', uri=True); L.row_factory = sqlite3.Row
conns = {s: db.connect_readonly(s) for s in ('nba', 'nhl', 'nfl')}
implied = lambda o: None if o is None else (100 / (o + 100) if o > 0 else -o / (-o + 100))
def row(sport, p, as_of):
    q = t.quote_check(conns[sport], p, as_of)
    e = p.get('evidence') or {}
    h = p.get('percentage_history') or {}
    imp = implied(q['price_now'])
    return dict(key=f"{sport}:{p['forecast_id']}", market=p['market'], label=p['pick_label'], p=p['probability'], price=q['price_now'],
                implied=imp, value=None if imp is None else p['probability'] - imp,
                band=e.get('win_rate'), n=e.get('settled'), lower=e.get('lower'), src=e.get('source'),
                rec_value=None if imp is None or e.get('win_rate') is None else e['win_rate'] - imp,
                exact=f"{h.get('wins',0)}-{h.get('losses',0)}", rank=p.get('game_rank'), line=q['line_source'],
                note=m.LIMITATIONS.get((sport, p['market'])))
out, summary = [], collections.Counter()
seen = set()
for b in L.execute('SELECT batch_key, created_at, payload FROM ticket_batches ORDER BY created_at'):
    pay = json.loads(b['payload']); snap = pay['snapshot']; as_of = snap['as_of']
    reasons = pay['plan']['review']['forecasts_reviewed']
    msg = L.execute('SELECT message_id FROM deliveries WHERE delivery_key=?', (b['batch_key'],)).fetchone()[0]
    legs = {(l['sport'], l['forecast_id']): l for tk in pay['resolved'] for l in tk['legs']}
    for (sport, fid), leg in legs.items():
        if (sport, fid) in seen:
            continue                      # audit each distinct sent forecast once, at its first recommendation
        seen.add((sport, fid))
        game = [p for p in snap['sports'][sport]['report']['picks'] if p['game_id'] == leg['game_id'] and t.eligible(p, t._parse(as_of))]
        rows = [row(sport, p, as_of) for p in game]
        chosen = next(r for r in rows if r['key'] == f'{sport}:{fid}')
        alts = [r for r in rows if r is not chosen]
        summary['legs'] += 1
        summary['top_probability'] += chosen['p'] == max(r['p'] for r in rows)
        summary['app_rank_1'] += chosen['rank'] == 1
        summary[f"chosen_{sport}_{chosen['market']}"] += 1
        better_value = [a for a in alts if a['value'] is not None and chosen['value'] is not None and a['value'] > chosen['value']]
        dominated = [a for a in better_value if a['rec_value'] is not None and chosen['rec_value'] is not None and a['rec_value'] > chosen['rec_value']]
        summary['alt_better_app_value'] += bool(better_value)
        summary['alt_better_value_and_record'] += bool(dominated)
        summary['chosen_positive_app_value'] += bool(chosen['value'] is not None and chosen['value'] > 0)
        summary['chosen_price_missing'] += chosen['price'] is None
        out.append(dict(msg=msg, as_of=as_of, sport=sport, matchup=leg['matchup'], chosen=chosen, alts=alts,
                        better_value=[a['key'] for a in better_value], dominated=[a['key'] for a in dominated],
                        reasons={a['key']: reasons.get(a['key'], {}).get('reason', '')[:220] for a in alts}))
# lock effect: reasons that cite an earlier ticket / lock, across every sent batch's review
lock = collections.Counter()
for b in L.execute('SELECT payload FROM ticket_batches'):
    for k, v in json.loads(b['payload'])['plan']['review']['forecasts_reviewed'].items():
        if v.get('decision') == 'excluded' and re.search(r'lock|prior ticket|earlier ticket|identical|original sent|already (sent|ticketed|used)', v.get('reason', ''), re.I):
            lock[k.split(':')[0]] += 1
json.dump(dict(summary=summary, lock_exclusions=lock, legs=out), open('/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/43a0d8c8-5d19-4e11-a7c0-2df81439b128/scratchpad/evidence/31-selection-audit.json', 'w'), indent=1, default=str)
f = lambda x, d=1: '—' if x is None else (f'{x:+.{d}%}' if isinstance(x, float) else str(x))
pct = lambda x: '—' if x is None else f'{x:.1%}'
print(dict(summary)); print('lock-cited exclusions', dict(lock))
for o in out:
    c = o['chosen']
    print(f"\n#{o['msg']} {o['as_of'][11:16]}Z {o['sport'].upper()} {o['matchup']}  SENT {c['label']} ({c['market']})")
    for r in [c] + sorted(o['alts'], key=lambda r: -r['p']):
        tag = 'SENT ' if r is c else ('DOM  ' if r['key'] in o['dominated'] else ('VAL  ' if r['key'] in o['better_value'] else '     '))
        print(f"  {tag}{r['market']:16} {r['label']:18} app {pct(r['p'])} price {r['price'] if r['price'] is not None else '—':>5} implied {pct(r['implied'])} "
              f"value {f(r['value'])} | record {pct(r['band'])} n={r['n'] or 0} low {pct(r['lower'])} {r['src'] or ''} rec-value {f(r['rec_value'])} | exact {r['exact']} rank {r['rank'] or '—'} {r['line']}")
