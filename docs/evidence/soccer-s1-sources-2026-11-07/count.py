"""Recompute every count in REPORT.md from the saved probe bytes.

Usage: python3 -I count.py <download_dir> [probes.jsonl]
Reads only files in <download_dir> (untrusted data, parsed as JSON/CSV/text, never executed).
"""
import collections
import glob
import hashlib
import html
import json
import os
import re
import sys

D = sys.argv[1]
PROBES = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probes.jsonl')
FINAL = ('STATUS_FULL_TIME', 'STATUS_FINAL_AET', 'STATUS_FINAL_PEN')
MIN_RE = re.compile(r"^(\d+)'(?:\+(\d+)')?$")


def load(name):
    with open(os.path.join(D, name), 'rb') as fh:
        return json.loads(fh.read())


def text(name, enc='utf-8'):
    with open(os.path.join(D, name), encoding=enc, errors='replace') as fh:
        return fh.read()


def strip_html(t):
    t = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', t)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))


def h(title):
    print('\n## ' + title)


# ---------------------------------------------------------------- 0. probe log integrity
h('0. Probe log')
rows = [json.loads(l) for l in open(PROBES)]
bad = 0
for r in rows:
    p = r['saved_path']
    if os.path.exists(p):
        b = open(p, 'rb').read()
        if hashlib.sha256(b).hexdigest() != r['sha256'] or len(b) != r['bytes']:
            bad += 1
    else:
        bad += 1
print('requests', len(rows), 'total_bytes', sum(r['bytes'] for r in rows),
      'status', dict(collections.Counter(r['http_status'] for r in rows)), 'hash_or_size_mismatch', bad)
hosts = collections.Counter(re.sub(r'^https?://([^/]+)/.*$', r'\1', r['url']) for r in rows)
print('requests_per_host', dict(hosts))

# ---------------------------------------------------------------- 1. ESPN scoreboards
h('1. ESPN scoreboard probes (month = dates=YYYYMM unless noted)')
print('file | events | status counts | finals | finals with details | details goal-sum == final score | postponed stubs with score "0" | finals with non-null scoreboard odds | finals with totalShots for both teams | season label')
team_names = collections.defaultdict(set)   # id -> names
name_ids = collections.defaultdict(set)     # name -> ids
id_comp_seasons = collections.defaultdict(set)
et_events = []
for f in sorted(glob.glob(os.path.join(D, 'espn_*_dates_*.json'))):
    b = os.path.basename(f)
    d = load(b)
    ev = d.get('events', [])
    st = collections.Counter(e['status']['type']['name'] for e in ev)
    fin = withdet = match = stubs = with_odds = with_shots = 0
    comp = b.split('_')[1]
    for e in ev:
        c = e['competitions'][0]
        for t in c['competitors']:
            team_names[t['team']['id']].add(t['team']['displayName'])
            name_ids[t['team']['displayName']].add(t['team']['id'])
            id_comp_seasons[t['team']['id']].add((comp.replace('eng1', 'eng.1'), str(e.get('season', {}).get('year'))))
        name = e['status']['type']['name']
        if name == 'STATUS_POSTPONED' and all(t.get('score') == '0' for t in c['competitors']):
            stubs += 1
        if name in FINAL and any(o for o in (c.get('odds') or [])):
            with_odds += 1
        if name in FINAL and all(any(x.get('name') == 'totalShots' for x in (t.get('statistics') or [])) for t in c['competitors']):
            with_shots += 1
        if name not in FINAL:
            continue
        fin += 1
        det = c.get('details') or []
        if det:
            withdet += 1
            cnt = collections.Counter(x['team']['id'] for x in det if x.get('scoringPlay') and not x.get('shootout'))
            if all(cnt.get(t['team']['id'], 0) == int(t['score']) for t in c['competitors']):
                match += 1
        if name in ('STATUS_FINAL_AET', 'STATUS_FINAL_PEN'):
            et_events.append((b, e, c))
    seas = (d.get('leagues') or [{}])[0].get('season', {}).get('displayName')
    print(b, '|', len(ev), '|', dict(st), '|', fin, '|', withdet, '|', match, '|', stubs, '|', with_odds, '|', with_shots, '|', seas)

h('1b. ESPN parameter tests')
for b in ['espn_eng1_range.json']:
    r = [x for x in rows if x['saved_path'].endswith(b)][0]
    print(b, 'http_status', r['http_status'], 'url', r['url'])
for b in ['espn_eng1_dates_2024.json', 'espn_eng1_dates_2023_limit1000.json', 'espn_eng1_20241019_limit.json']:
    ev = load(b)['events']
    print(b, 'events', len(ev), 'first', min(e['date'] for e in ev), 'last', max(e['date'] for e in ev))

# ---------------------------------------------------------------- 2. ET / pens 90-minute derivation
h('2. ESPN AET/PEN events in sampled scoreboards: 90-minute score from scoreboard `details` (minute <= 90, non-shootout)')


def ninety_from_details(c):
    s = collections.Counter()
    unparsed = 0
    for x in c.get('details') or []:
        if not x.get('scoringPlay') or x.get('shootout'):
            continue
        m = MIN_RE.match(x['clock']['displayValue'])
        if not m:
            unparsed += 1
            continue
        if int(m.group(1)) <= 90:
            s[x['team']['id']] += 1
    return s, unparsed


for b, e, c in et_events:
    s, unp = ninety_from_details(c)
    comp = c['competitors']
    print(b, e['id'], e['date'], e['name'], e['status']['type']['name'],
          'final', '-'.join(t['score'] for t in comp),
          'shootout', '-'.join(str(t.get('shootoutScore')) for t in comp),
          '| 90min(details)', '-'.join(str(s.get(t['team']['id'], 0)) for t in comp),
          '| goal minutes', [x['clock']['displayValue'] for x in c.get('details') or [] if x.get('scoringPlay') and not x.get('shootout')],
          '| unparsed', unp)
print('league-file AET/PEN statuses (league matches cannot go to extra time):',
      sum(1 for b, e, c in et_events if not b.startswith('espn_uefa')))

h('3. ESPN summary endpoint')
sb_index = {}
for f in glob.glob(os.path.join(D, 'espn_*_dates_*.json')):
    for e in load(os.path.basename(f)).get('events', []):
        sb_index[e['id']] = e['competitions'][0]
for f in sorted(glob.glob(os.path.join(D, 'espn_summary_*.json'))):
    b = os.path.basename(f)
    s = load(b)
    c = s['header']['competitions'][0]
    comp = c['competitors']
    ls = [[l.get('displayValue') for l in t.get('linescores', [])] for t in comp]
    ls90 = None
    if all(len(x) >= 2 for x in ls):
        ls90 = '-'.join(str(int(x[0]) + int(x[1])) for x in ls)
    lsum_ok = None
    if all(ls):
        nper = 4 if c['status']['type']['name'] == 'STATUS_FINAL_PEN' else len(ls[0])
        lsum_ok = all(sum(int(v) for v in x[:nper]) == int(t['score']) for x, t in zip(ls, comp))
    goals = [k for k in s.get('keyEvents') or [] if k.get('scoringPlay') and not k.get('shootout')]
    per = collections.Counter()
    for k in goals:
        if k['period']['number'] <= 2:
            per[k['team']['id']] += 1
    ke90 = '-'.join(str(per.get(t['team']['id'], 0)) for t in comp)
    det90 = None
    eid = s['header']['id']
    if eid in sb_index:
        sc, _ = ninety_from_details(sb_index[eid])
        det90 = '-'.join(str(sc.get(t['team']['id'], 0)) for t in comp)
    stats = [len(t.get('statistics', [])) for t in s['boxscore'].get('teams', [])]
    odds = s.get('odds') or []
    prov = [o['provider']['name'] for o in odds]
    pre = {}
    tfields = set()
    for o in odds:
        to = o.get('bettingOdds', {}).get('teamOdds', {})
        for k, v in to.items():
            if k.startswith('preMatchFullTimeResult'):
                pre[k.replace('preMatchFullTimeResult', '')] = v['value']
        tfields |= set(re.findall(r'"(\w*(?:[Tt]imestamp|[Uu]pdated|lastModified|asOf)\w*)"', json.dumps(o)))
    print(b, '| status', c['status']['type']['name'], '| final', '-'.join(t['score'] for t in comp),
          '| linescores', ls, '| 90min(linescores 1+2)', ls90, '| linescore sum==final', lsum_ok,
          '| 90min(keyEvents period<=2)', ke90, '| 90min(scoreboard details)', det90,
          '| keyEvent goal (minute,period)', [(k['clock']['displayValue'], k['period']['number']) for k in goals],
          '| boxscore stat counts', stats, '| odds providers', prov, '| Bet365 preMatch 1X2', pre,
          '| quote-time fields', sorted(tfields) or 'none', '| pickcenter', len(s.get('pickcenter') or []))

# ---------------------------------------------------------------- 4. ESPN team ids
h('4. ESPN team id stability (all sampled scoreboards)')
multi_name = {i: sorted(n) for i, n in team_names.items() if len(n) > 1}
multi_id = {n: sorted(i) for n, i in name_ids.items() if len(i) > 1}
print('distinct team ids', len(team_names), 'ids with >1 displayName', len(multi_name), multi_name)
print('displayNames with >1 id', len(multi_id), multi_id)
for tid in ['360', '364', '86', '83', '160', '110']:
    print('id', tid, sorted(team_names[tid]), 'seen in', len(id_comp_seasons[tid]), 'competition-seasons', sorted(id_comp_seasons[tid]))

# ---------------------------------------------------------------- 5. openfootball
h('5. openfootball')
for r in ['football.json', 'champions-league']:
    m = load('of_repo_%s.json' % r)
    t = load('of_tree_%s.json' % r)
    print(r, 'license', m['license']['spdx_id'], 'pushed_at', m['pushed_at'], 'tree truncated', t['truncated'])
tree = load('of_tree_football.json.json')['tree']
want = {'en.1': 'eng.1', 'es.1': 'esp.1', 'de.1': 'ger.1', 'it.1': 'ita.1', 'fr.1': 'fra.1', 'uefa.cl': 'uefa.champions', 'uefa.el': 'uefa.europa'}
per = collections.defaultdict(list)
for x in tree:
    mm = re.match(r'^(\d{4}-\d{2})/([a-z0-9.]+?)\.json$', x['path'])
    if x['type'] == 'blob' and mm and mm.group(2) in want:
        per[want[mm.group(2)]].append(mm.group(1))
for k in want.values():
    v = sorted(per.get(k, []))
    print('football.json', k, 'season files', len(v), (v[0] + '..' + v[-1]) if v else '-')
tree2 = load('of_tree_champions-league.json')['tree']
per2 = collections.defaultdict(list)
for x in tree2:
    mm = re.match(r'^(\d{4}-\d{2})/(cl|el)\.txt$', x['path'])
    if x['type'] == 'blob' and mm:
        per2[mm.group(2)].append(mm.group(1))
for k in ['cl', 'el']:
    v = sorted(per2[k])
    print('champions-league', k + '.txt', 'season files', len(v), v)

for b in ['of_fj_2024-25_uefa.cl.json', 'of_fj_2014-15_uefa.cl.json', 'of_fj_2024-25_en.1.json', 'of_fj_2023-24_en.1.json', 'of_fj_2022-23_en.1.json']:
    ms = load(b)['matches']
    print(b, 'matches', len(ms), 'score-key patterns', dict(collections.Counter('+'.join(sorted(m.get('score', {}).keys())) or 'none' for m in ms)))
    for m in ms:
        sc = m.get('score', {})
        if 'et' in sc or 'p' in sc:
            print('   ET/P row', m['date'], m['team1'], 'v', m['team2'], sc)
for b in ['of_cl_2024-25_cl.txt', 'of_cl_2024-25_el.txt', 'of_cl_2014-15_cl.txt']:
    t = text(b)
    lines = [l.strip() for l in t.splitlines() if ' v ' in l]
    aet = [l for l in lines if 'a.e.t.' in l]
    print(b, 'match lines', len(lines), 'a.e.t. lines', len(aet))
    for l in aet:
        print('   ', re.sub(r'\s+', ' ', l))
# discrepancy check: Inter v Barcelona 2025-05-06
ms = load('of_fj_2024-25_uefa.cl.json')['matches']
ib = [m for m in ms if m['date'] == '2025-05-06' and 'Internazionale' in m['team1']]
fin = [m for m in ms if 'Final' == m['round'].split(', ')[-1]]
print('openfootball json Inter v Barcelona 2025-05-06:', ib[0]['score'] if ib else None)
print('openfootball json final row:', fin[0]['date'] if fin else None, fin[0]['score'] if fin else None)
c = sb_index.get('733619')
if c:
    sc, _ = ninety_from_details(c)
    print('ESPN 733619 status', load('espn_uefa.champions_dates_202505.json')['events'][0]['status']['type']['name'],
          'final', '-'.join(t['score'] for t in c['competitors']),
          '90min(details)', '-'.join(str(sc.get(t['team']['id'], 0)) for t in c['competitors']))

# ---------------------------------------------------------------- 6. ESPN vs openfootball EPL calendar 2023 + names
h('6. ESPN vs openfootball: EPL calendar year 2023 and team names')


def norm(n):
    return re.sub(r' FC$', '', n)


of23 = []
for b in ['of_fj_2022-23_en.1.json', 'of_fj_2023-24_en.1.json']:
    for m in load(b)['matches']:
        if m['date'].startswith('2023'):
            of23.append(m)
esp = load('espn_eng1_dates_2023_limit1000.json')['events']
esp_names = {t['team']['displayName'] for e in esp for t in e['competitions'][0]['competitors']}
of_names = {m[k] for m in of23 for k in ('team1', 'team2')}
print('ESPN calendar-2023 EPL events', len(esp), '| openfootball rows dated 2023', len(of23))
print('distinct names ESPN', len(esp_names), 'openfootball', len(of_names),
      '| exact matches', len(esp_names & of_names), '| matches after stripping trailing " FC"', len(esp_names & {norm(n) for n in of_names}))
resid = sorted({norm(n) for n in of_names} - esp_names)
print('residual openfootball names needing a manual map', resid, '| ESPN names unmatched', sorted(esp_names - {norm(n) for n in of_names}))
MANUAL = {}  # filled only if residuals exist; kept explicit (no fuzzy matching)
e_idx = collections.defaultdict(list)
for e in esp:
    c = e['competitions'][0]
    hm = [t for t in c['competitors'] if t['homeAway'] == 'home'][0]
    aw = [t for t in c['competitors'] if t['homeAway'] == 'away'][0]
    e_idx[(hm['team']['displayName'], aw['team']['displayName'])].append((e['date'][:10], int(hm['score']), int(aw['score']), e['id']))
matched = score_eq = 0
unmatched, score_diff = [], []
for m in of23:
    k = (MANUAL.get(norm(m['team1']), norm(m['team1'])), MANUAL.get(norm(m['team2']), norm(m['team2'])))
    hit = [v for v in e_idx.get(k, []) if v[0] == m['date']]  # ESPN UTC date == openfootball local date
    if len(hit) == 1:
        matched += 1
        if [hit[0][1], hit[0][2]] == m['score'].get('ft'):
            score_eq += 1
        else:
            score_diff.append((m['date'], k, m['score'].get('ft'), hit[0]))
    else:
        unmatched.append((m['date'], m['team1'], m['team2'], len(hit)))
print('openfootball 2023 rows matched to exactly one ESPN event by (home, away, date)', matched, '| same full-time score', score_eq,
      '| score differences', score_diff, '| unmatched', len(unmatched), unmatched[:10])

of25 = {m[k] for m in load('of_fj_2024-25_en.1.json')['matches'] for k in ('team1', 'team2')}
e25 = {t['team']['displayName'] for b in ['espn_eng1_dates_202410.json', 'espn_eng1_dates_202412.json'] for e in load(b)['events'] for t in e['competitions'][0]['competitors']}
print('EPL 2024-25 names: ESPN', len(e25), 'openfootball', len(of25), 'exact', len(e25 & of25), 'after " FC" strip', len(e25 & {norm(n) for n in of25}))
ucl_of = {m[k] for m in load('of_fj_2024-25_uefa.cl.json')['matches'] for k in ('team1', 'team2')}
ucl_es = {t['team']['displayName'] for b in ['espn_uefa.champions_dates_202503.json', 'espn_uefa.champions_dates_202505.json'] for e in load(b)['events'] for t in e['competitions'][0]['competitors']}
ucl_norm = {re.sub(r' \([A-Z]{3}\)$', '', n) for n in ucl_of}
ucl_norm2 = {norm(n) for n in ucl_norm}
print('UCL 2024-25: ESPN names in Mar+May 2025 sample', len(ucl_es), '| found in openfootball after stripping " (CCC)" and " FC"', len(ucl_es & ucl_norm2), '| not found', sorted(ucl_es - ucl_norm2))

# ---------------------------------------------------------------- 7. football-data.co.uk (index pages only)
h('7. football-data.co.uk (index/notes pages only; no CSV downloaded)')
for p, div in [('englandm', 'E0'), ('spainm', 'SP1'), ('germanym', 'D1'), ('italym', 'I1'), ('francem', 'F1')]:
    t = text('fd_%s.php' % p, 'latin-1')
    s = sorted(set(re.findall(r'mmz4281/(\d{4})/%s\.csv' % div, t)), key=lambda x: (int(x[:2]) < 90, x))
    print(p, div, 'season CSV links', len(s), 'first', s[0], 'last', s[-1])
dt = strip_html(text('fd_data.php', 'latin-1'))
for kw in ['Champions League', 'Europa League', 'UEFA']:
    print('data.php mentions of', repr(kw), len(re.findall(kw, dt)))
for kw in ['private individuals only', 'collected after market opening', 'closing odds', "Pinnacle's public API", 'Friday afternoons']:
    print('data.php phrase present', repr(kw), kw in dt)
nt = text('fd_notes.txt', 'latin-1')
for kw in ['Time = Time of match kick off', 'These are for pre-closing odds', 'B365CH', 'collected Friday afternoons', 'time zone', 'UK time', 'GMT']:
    print('notes.txt phrase present', repr(kw), kw in nt)
rb = text('fd_robots.txt', 'latin-1')
print('robots.txt user-agents disallowed "/":', re.findall(r'User-agent: (\S+)\s+Disallow: /\s', rb))

# ---------------------------------------------------------------- 8. others
h('8. StatsBomb open data / ClubElo / docs')
sb = load('sb_competitions.json')
names7 = {'Premier League': 'eng.1', 'La Liga': 'esp.1', '1. Bundesliga': 'ger.1', 'Serie A': 'ita.1', 'Ligue 1': 'fra.1', 'Champions League': 'uefa.champions', 'UEFA Europa League': 'uefa.europa'}
cs = collections.defaultdict(list)
for x in sb:
    if x['competition_name'] in names7 and x['competition_gender'] == 'male' and x['country_name'] in ('England', 'Spain', 'Germany', 'Italy', 'France', 'Europe'):
        cs[names7[x['competition_name']]].append(x['season_name'])
for k in names7.values():
    print('StatsBomb', k, len(cs[k]), sorted(cs[k]))
print('StatsBomb repo license', load('sb_repo.json')['license']['spdx_id'])
for r in rows:
    if 'clubelo' in r['url']:
        print('ClubElo', r['url'], r['http_status'], r['bytes'], open(r['saved_path'], 'rb').read()[:60])
for r in rows:
    if r['http_status'] not in (200,):
        print('non-200', r['http_status'], r['url'])

# ---------------------------------------------------------------- 9. promoted teams / cross-league links (ESPN ids)
h('9. ESPN ids: 2024-25 EPL teams seen in earlier sampled eng.1 seasons; UEFA appearances')
e25ids = {t['team']['id']: t['team']['displayName'] for b in ['espn_eng1_dates_202410.json', 'espn_eng1_dates_202412.json'] for e in load(b)['events'] for t in e['competitions'][0]['competitors']}
for tid, nm in sorted(e25ids.items(), key=lambda x: x[1]):
    eng = sorted(s for c, s in id_comp_seasons[tid] if c == 'eng.1' and s < '2024')
    uefa = sorted(c + ':' + s for c, s in id_comp_seasons[tid] if c.startswith('uefa'))
    print(nm, tid, '| earlier sampled eng.1 seasons', eng, '| sampled UEFA', uefa)
tot_fin = 0
for f in glob.glob(os.path.join(D, 'espn_*_dates_*.json')):
    tot_fin += sum(1 for e in load(os.path.basename(f)).get('events', []) if e['status']['type']['name'] in FINAL)
print('total final events across sampled ESPN scoreboards', tot_fin)
ko = ['espn_uefa.champions_dates_200503.json', 'espn_uefa.champions_dates_200505.json', 'espn_uefa.champions_dates_201503.json',
      'espn_uefa.champions_dates_202503.json', 'espn_uefa.champions_dates_202505.json', 'espn_uefa.europa_dates_201503.json', 'espn_uefa.europa_dates_202504.json']
n = et = 0
for b in ko:
    ev = load(b)['events']
    n += len(ev)
    et += sum(1 for e in ev if e['status']['type']['name'] in ('STATUS_FINAL_AET', 'STATUS_FINAL_PEN'))
print('knockout-month samples: events', n, 'AET/PEN', et)
