"""Read-only: at each sent batch's snapshot, which eligible forecasts were blocked only by the earlier-ticket game lock
(same game already in an earlier sent ticket, different forecast_id), and their app value vs the stored price then."""
import json, sqlite3, collections
from app import db, tickets as t
L = sqlite3.connect('file:/Users/andry/Desktop/nba-predictor-sprint1/.telegram-delivery.sqlite?mode=ro', uri=True)
conns = {s: db.connect_readonly(s) for s in ('nba', 'nhl', 'nfl')}
imp = lambda o: None if o is None else (100 / (o + 100) if o > 0 else -o / (-o + 100))
used, total = {}, collections.Counter()
for msg, payload in L.execute("SELECT d.message_id, b.payload FROM ticket_batches b JOIN deliveries d ON d.delivery_key=b.batch_key ORDER BY b.created_at"):
    pay = json.loads(payload); snap = pay['snapshot']; now = t._parse(snap['as_of'])
    blocked = []
    for sport, sec in snap['sports'].items():
        for p in (sec.get('report') or {}).get('picks', []):
            g = (sport, p['game_id'])
            if t.eligible(p, now) and g in used and p['forecast_id'] not in used[g]['ids']:
                q = t.quote_check(conns[sport], p, snap['as_of']); i = imp(q['price_now'])
                same = p['market'] in used[g]['markets']
                blocked.append((sport, p['matchup'], p['market'], p['pick_label'], p['probability'], q['price_now'], None if i is None else p['probability'] - i, 'newer version' if same else 'other market'))
    total['batches'] += 1; total['blocked'] += len(blocked)
    total['blocked_newer_version'] += sum(b[-1] == 'newer version' for b in blocked)
    total['blocked_positive_value'] += sum(1 for b in blocked if b[6] is not None and b[6] > 0)
    print(f"#{msg} {snap['as_of']}: {len(blocked)} eligible forecasts blocked by the earlier-ticket lock")
    for b in sorted(blocked, key=lambda b: -(b[6] or -9))[:6]:
        print(f"    {b[0].upper()} {b[1]:11} {b[2]:16} {b[3]:18} app {b[4]:.1%} price {b[5]} value {'—' if b[6] is None else f'{b[6]:+.1%}'} ({b[7]})")
    for tk in pay['resolved']:
        for l in tk['legs']:
            e = used.setdefault((l['sport'], l['game_id']), {'ids': set(), 'markets': set()})
            e['ids'].add(l['forecast_id']); e['markets'].add(l['market'])
print(dict(total))
