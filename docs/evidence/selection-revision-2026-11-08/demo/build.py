"""Write each frozen pregame snapshot (from the sent batch) with quote_check computed at its own as_of (quotes stored by
then), plus plans for: the original sent tickets, my selections and the closest rejected candidates. Read-only on stores."""
import json, sqlite3
from app import db, tickets as t
D = '/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/43a0d8c8-5d19-4e11-a7c0-2df81439b128/scratchpad/demo'
L = sqlite3.connect('file:/Users/andry/Desktop/nba-predictor-sprint1/.telegram-delivery.sqlite?mode=ro', uri=True)
conns = {s: db.connect_readonly(s) for s in ('nba', 'nhl', 'nfl')}
batches = [json.loads(p) for (p,) in L.execute("SELECT b.payload FROM ticket_batches b ORDER BY b.created_at")]
def fid(snap, sport, matchup, market):
    return next(p['forecast_id'] for p in snap['sports'][sport]['report']['picks'] if p['matchup'] == matchup and p['market'] == market)
for n, pay in enumerate(batches, 1):
    snap = pay['snapshot']
    for sport in ('nba', 'nhl', 'nfl'):
        for p in (snap['sports'].get(sport, {}).get('report') or {}).get('picks', []):
            p['quote_check'] = t.quote_check(conns[sport], p, snap['as_of'])
    json.dump(snap, open(f'{D}/snapshot-{n}.json', 'w'))
    tickets = [dict(label='ORIGINAL ' + tk['label'], legs=[dict(sport=l['sport'], forecast_id=l['forecast_id']) for l in tk['legs']]) for tk in pay['resolved']]
    extra = {
        3: [('CANDIDATE (not sent) WPG +1.5 + LAL@DET Over 216.5', [('nhl', 'TBL @ WPG', 'spread'), ('nba', 'LAL @ DET', 'total')])],
        4: [('SELECTED TOR +1.5 + CGY +1.5', [('nhl', 'NJD @ TOR', 'spread'), ('nhl', 'PHI @ CGY', 'spread')]),
            ('CANDIDATE (not sent) TOR win + CGY +1.5', [('nhl', 'NJD @ TOR', 'winner'), ('nhl', 'PHI @ CGY', 'spread')])],
        5: [('SELECTED MIN win + TOR +1.5', [('nba', 'UTAH @ MIN', 'winner'), ('nhl', 'NJD @ TOR', 'spread')]),
            ('CANDIDATE (not sent) MIN win + CGY +1.5', [('nba', 'UTAH @ MIN', 'winner'), ('nhl', 'PHI @ CGY', 'spread')]),
            ('CANDIDATE (not sent) MIN win + PIT +1.5', [('nba', 'UTAH @ MIN', 'winner'), ('nhl', 'PIT @ ANA', 'spread')])],
        7: [('CANDIDATE (not sent) LAC win + LAC-game alternatives n/a; LAC win + DAL win is the original', [])],
    }.get(n, [])
    for label, legs in extra:
        if legs:
            tickets.append(dict(label=label, legs=[dict(sport=s, forecast_id=fid(snap, s, m, k)) for s, m, k in legs]))
    json.dump(dict(snapshot_path=f'{D}/snapshot-{n}.json', tickets=tickets), open(f'{D}/plan-{n}.json', 'w'), indent=1)
print('ok', len(batches))
