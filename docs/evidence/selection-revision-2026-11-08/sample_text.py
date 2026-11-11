"""Render the revised ticket text for the 6:58 PM batch (message 282) from its saved pre-recommendation snapshot and
quotes stored by then. Read-only; nothing is sent. payout_assessment is left as a placeholder: it is the reviewer's job."""
import json, sqlite3
from app import db, tickets as t
L = sqlite3.connect('file:/Users/andry/Desktop/nba-predictor-sprint1/.telegram-delivery.sqlite?mode=ro', uri=True)
conns = {s: db.connect_readonly(s) for s in ('nba', 'nhl', 'nfl')}
payload = L.execute("SELECT b.payload FROM ticket_batches b JOIN deliveries d ON d.delivery_key=b.batch_key WHERE d.message_id=282").fetchone()[0]
pay = json.loads(payload); snap = pay['snapshot']
tickets = []
for tk in pay['resolved']:
    legs = []
    for l in tk['legs']:
        p = dict(next(p for p in snap['sports'][l['sport']]['report']['picks'] if p['forecast_id'] == l['forecast_id']), sport=l['sport'])
        p['quote_check'] = t.quote_check(conns[l['sport']], p, snap['as_of']); p['assessment'] = t.leg_assessment(l['sport'], p)
        legs.append(p)
    tickets.append(dict(tk, legs=legs, assessment=t.ticket_assessment(legs), payout_assessment='(reviewer judgment)'))
print(t.ticket_text(snap['date'], tickets))
