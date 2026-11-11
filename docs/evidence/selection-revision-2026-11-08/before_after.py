"""Before/after examples for the selection revision, using ONLY information available at each original recommendation:
the snapshot saved with each sent batch and sportsbook quotes stored by that snapshot's as_of. No results are read.
BEFORE = the estimate line the delivered text showed. AFTER = what the revised helper shows the reviewer and the user
(ticket_assessment) for the same ticket, plus two MECHANICAL illustrations (not recommendations and not the new process,
which stays reviewer judgment): (a) swap the leg priced furthest above the model for the same game's priced market with
the largest model-minus-break-even gap; (b) drop that leg when the ticket has three or more legs."""
import json, sqlite3, re
from app import db, tickets as t
L = sqlite3.connect('file:/Users/andry/Desktop/nba-predictor-sprint1/.telegram-delivery.sqlite?mode=ro', uri=True)
conns = {s: db.connect_readonly(s) for s in ('nba', 'nhl', 'nfl')}
def enrich(sport, pick, as_of):
    p = dict(pick, sport=sport)
    p['quote_check'] = t.quote_check(conns[sport], pick, as_of)
    p['assessment'] = t.leg_assessment(sport, p)
    return p
def show(name, legs):
    a = t.ticket_assessment(legs)
    pay = f"pays ×{a['payout']:.2f}, break-even {a['break_even']:.1%}" if a['payout'] else f"payout unknown ({', '.join(a['unpriced'])})"
    rng = '; '.join(f"{x['label']} " + (f"{x['record']['win_rate']:.0%} of {x['record']['settled']}" if x['record'] else 'none') for x in a['leg_records'])
    legtxt = ' + '.join(f"{l['pick_label']} {l['probability']:.0%}@{l['assessment']['price'] if l['assessment']['price'] is not None else '—'}" for l in legs)
    print(f"    {name:9} {legtxt}\n              model {a['model_estimate']:.1%} | {pay} | leg records: {rng}" + (f" | priced above model: {', '.join(a['priced_above_model'])}" if a['priced_above_model'] else ''))
    return a
for msg, payload, text in L.execute("SELECT d.message_id, b.payload, d.text FROM ticket_batches b JOIN deliveries d ON d.delivery_key=b.batch_key ORDER BY b.created_at"):
    pay = json.loads(payload); snap = pay['snapshot']; as_of = snap['as_of']; now = t._parse(as_of)
    shown = re.findall(r'Model estimate [0-9.]+%', text)
    print(f"\n#{msg} snapshot {as_of}  BEFORE (delivered text): {'; '.join(shown)}")
    for tk in pay['resolved']:
        legs = [enrich(l['sport'], next(p for p in snap['sports'][l['sport']]['report']['picks'] if p['forecast_id'] == l['forecast_id']), as_of) for l in tk['legs']]
        print(f"  {tk['ticket_id']} {tk['label']}")
        show('AFTER', legs)
        worst = min((l for l in legs if l['assessment']['model_gap'] is not None), key=lambda l: l['assessment']['model_gap'], default=None)
        if worst is None or worst['assessment']['model_gap'] >= 0:
            continue
        alts = [enrich(worst['sport'], p, as_of) for p in snap['sports'][worst['sport']]['report']['picks']
                if p['game_id'] == worst['game_id'] and p['forecast_id'] != worst['forecast_id'] and t.eligible(p, now)]
        alts = [a for a in alts if a['assessment']['model_gap'] is not None]
        if alts:
            best = max(alts, key=lambda a: a['assessment']['model_gap'])
            show('(a) swap', [best if l is worst else l for l in legs])
            r = best['assessment']['record']
            print(f"              swapped in {best['pick_label']}: record {'none' if not r else f'{r[chr(119)+chr(105)+chr(110)+chr(95)+chr(114)+chr(97)+chr(116)+chr(101)]:.0%} of {r[chr(115)+chr(101)+chr(116)+chr(116)+chr(108)+chr(101)+chr(100)]} ({r[chr(115)+chr(111)+chr(117)+chr(114)+chr(99)+chr(101)]})'}; app note: {best['assessment']['app_note'] or 'none'}")
        if len(legs) >= 3:
            show('(b) drop', [l for l in legs if l is not worst])
