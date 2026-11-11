"""Facts per frozen pregame snapshot (the snapshot saved with each sent batch), as the revised helper presents them:
every eligible forecast whose source was usable then, minus games locked by tickets sent BEFORE that snapshot (current
identical-forecast rule), with quotes stored by that snapshot's as_of. No results are read."""
import json, sqlite3, sys
from app import db, tickets as t
L = sqlite3.connect('file:/Users/andry/Desktop/nba-predictor-sprint1/.telegram-delivery.sqlite?mode=ro', uri=True)
conns = {s: db.connect_readonly(s) for s in ('nba', 'nhl', 'nfl')}
pct = lambda x: '—' if x is None else f'{x:.0%}'
used = {}
for n, (msg, payload) in enumerate(L.execute("SELECT d.message_id, b.payload FROM ticket_batches b JOIN deliveries d ON d.delivery_key=b.batch_key ORDER BY b.created_at"), 1):
    pay = json.loads(payload); snap = pay['snapshot']; as_of = snap['as_of']; now = t._parse(as_of)
    print(f"\n=== Snapshot {n} ({as_of}, before message {msg}) ===")
    for sport, sec in snap['sports'].items():
        if sec.get('error'):
            print(f"  {sport.upper()}: unavailable ({sec['error']})"); continue
        picks = [p for p in sec['report']['picks'] if t.eligible(p, now)]
        if not picks: continue
        if not t.source_usable(sec):
            print(f"  {sport.upper()}: source {sec['freshness']['source_state']} -> {len(picks)} forecasts blocked"); continue
        games = {}
        for p in picks: games.setdefault(p['game_id'], []).append(p)
        for g, ps in games.items():
            lock = used.get((sport, g))
            print(f"  {sport.upper()} {ps[0]['matchup']} ({ps[0]['tipoff'][11:16]}Z)" + (f"  LOCKED to {lock}" if lock else ''))
            for p in sorted(ps, key=lambda p: -p['probability']):
                if lock and p['forecast_id'] not in lock: continue
                q = t.quote_check(conns[sport], p, as_of); p = dict(p, quote_check=q); a = t.leg_assessment(sport, p)
                r = a['record']
                rec = f"rec {r['win_rate']:.0%} of {r['settled']} [{r['interval'][0]:.0%}-{r['interval'][1]:.0%}] {r['source']}" if r else 'no record'
                note = (a['app_note'] or '')[:38]
                print(f"    {p['market']:15} {p['pick_label']:17} p {a['probability']:.1%}  {('DK %+d' % a['price']) if a['price'] is not None else 'no price':9} "
                      f"BE {pct(a['break_even']):4} gap {('%+.1f' % (100*a['model_gap'])) if a['model_gap'] is not None else '—':5} | {rec:34} | exact {a['exact']:15} rank {a['app_rank'] or '—'} {a['line_source']}{' moved' if q.get('line_moved') else ''} {note}")
    for tk in pay['resolved']:
        for l in tk['legs']: used.setdefault((l['sport'], l['game_id']), set()).add(l['forecast_id'])
