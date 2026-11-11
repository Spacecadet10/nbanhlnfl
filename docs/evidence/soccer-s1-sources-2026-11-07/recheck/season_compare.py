"""EPL 2023-24 full season: ESPN (cached CY2023 + recheck CY2024, events dated 2023-07-01..2024-06-30) vs openfootball 2023-24.
Usage: python3 -I season_compare.py <s1_download_dir> <recheck_download_dir>. Parses JSON only."""
import collections, json, os, re, sys
S1, RC = sys.argv[1], sys.argv[2]
load = lambda d, n: json.load(open(os.path.join(d, n), 'rb'))
ev = load(S1, 'espn_eng1_dates_2023_limit1000.json')['events'] + load(RC, 'espn_eng1_dates_2024_limit1000.json')['events']
ids = collections.Counter(e['id'] for e in ev)
print('ESPN events CY2023+CY2024', len(ev), '| duplicate event ids', sum(1 for v in ids.values() if v > 1))
seas = [e for e in ev if '2023-07-01' <= e['date'][:10] <= '2024-06-30']
print('ESPN events dated 2023-07-01..2024-06-30', len(seas), '| status', dict(collections.Counter(e['status']['type']['name'] for e in seas)),
      '| season.year values', dict(collections.Counter(str(e.get('season', {}).get('year')) for e in seas)),
      '| first', min(e['date'] for e in seas), 'last', max(e['date'] for e in seas))
of = load(S1, 'of_fj_2023-24_en.1.json')['matches']
print('openfootball 2023-24 rows', len(of), '| with ft score', sum(1 for m in of if m.get('score', {}).get('ft')),
      '| date range', min(m['date'] for m in of), max(m['date'] for m in of))
norm = lambda n: re.sub(r' FC$', '', n)
idx = collections.defaultdict(list)
for e in seas:
    c = e['competitions'][0]
    h = [t for t in c['competitors'] if t['homeAway'] == 'home'][0]; a = [t for t in c['competitors'] if t['homeAway'] == 'away'][0]
    idx[(h['team']['displayName'], a['team']['displayName'])].append((e['date'][:10], int(h['score']), int(a['score']), e['id'], e['date']))
pairs = sum(len(v) for v in idx.values())
print('ESPN distinct (home, away) pairs', len(idx), '| events', pairs, '| pairs with >1 event', sum(1 for v in idx.values() if len(v) > 1))
m_pair = m_date = same = 0; diffs = []; unmatched = []; date_off = []
used = set()
for m in of:
    k = (norm(m['team1']), norm(m['team2']))
    hits = idx.get(k, [])
    if len(hits) == 1:
        m_pair += 1; used.add(hits[0][3])
        if hits[0][0] == m['date']:
            m_date += 1
        else:
            date_off.append((m['date'], m.get('time'), k, hits[0][4]))
        if [hits[0][1], hits[0][2]] == m['score'].get('ft'):
            same += 1
        else:
            diffs.append((m['date'], k, m['score'].get('ft'), hits[0][1:3]))
    else:
        unmatched.append((m['date'], k, len(hits)))
print('openfootball rows paired to exactly one ESPN event by (home, away) within the season', m_pair,
      '| same calendar date (ESPN UTC date vs openfootball local date)', m_date, '| same full-time score', same)
print('date differences', date_off)
print('score differences', diffs)
print('unmatched openfootball rows', len(unmatched), unmatched[:10], '| ESPN events not paired', len([e for e in seas if e['id'] not in used]))

# kickoff-time offset: openfootball local 'time' minus ESPN UTC time, by month (EPL 2023-24)
from datetime import datetime
off = collections.Counter()
for m in of:
    k = (norm(m['team1']), norm(m['team2']))
    hits = idx.get(k, [])
    if len(hits) == 1 and m.get('time'):
        e = datetime.strptime(hits[0][4], '%Y-%m-%dT%H:%MZ')
        o = datetime.strptime(m['date'] + ' ' + m['time'], '%Y-%m-%d %H:%M')
        off[(m['date'][:7], round((o - e).total_seconds() / 3600, 2))] += 1
print('openfootball local time minus ESPN UTC (hours), by month:', dict(sorted(off.items())))
