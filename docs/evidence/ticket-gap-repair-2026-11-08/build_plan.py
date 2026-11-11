"""Build a SYNTHETIC test plan from a real snapshot (harness only; not an AI review, never sent to the real chat)."""
import json
import sys
from datetime import datetime

from app import tickets as t

snapshot_path, output, mode = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(snapshot_path, 'rb').read()
snap = json.loads(raw)
now = datetime.fromisoformat(snap['as_of'].replace('Z', '+00:00'))
accounted, markets, winners = {}, {}, []
for sport, section in snap['sports'].items():
    picks = section.get('report', {}).get('picks', [])
    if picks:
        markets[sport] = {p['market']: 'E2E harness note (synthetic, not a review)' for p in picks}
    for p in picks:
        if t.eligible(p, now):
            accounted[f"{sport}:{p['forecast_id']}"] = {'decision': 'candidate', 'reason': 'E2E harness accounting (synthetic)'}
            if p['market'] == 'winner':
                winners.append((sport, p))
review = dict(skill_sha256=snap['skill_sha256'], helper_sha256=snap['helper_sha256'], snapshot_sha256=t.sha(raw),
              notes='E2E isolated harness; synthetic plan; mocked Telegram.', markets_reviewed=markets,
              combination_comparison='E2E harness: synthetic comparison text, not a real review.',
              outcome_summary='E2E harness: no combination selected (synthetic).', forecasts_reviewed=accounted)
tickets = []
if mode == 'ticket':
    games = {}
    for sport, p in winners:
        games.setdefault((sport, p['game_id']), (sport, p))
    legs = list(games.values())[:2]
    tickets = [dict(label='E2E TEST synthetic mixed ticket', legs=[dict(sport=s, forecast_id=p['forecast_id'],
                                                                         rationale='E2E harness leg (synthetic)') for s, p in legs])]
json.dump(dict(snapshot_path=snapshot_path, review=review, tickets=tickets), open(output, 'w'), indent=1)
print(json.dumps({'accounted': len(accounted), 'tickets': [[(l['sport'], l['forecast_id']) for l in x['legs']] for x in tickets]}))
