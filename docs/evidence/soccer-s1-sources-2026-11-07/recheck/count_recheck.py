"""Recompute recheck counts. Usage: python3 -I count_recheck.py <s1_download_dir> <recheck_download_dir>
Parses saved bytes as JSON/text only (untrusted data, never executed)."""
import collections, glob, hashlib, json, os, re, sys
S1, RC = sys.argv[1], sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))
print('## 0. recheck probe log')
rows = [json.loads(l) for l in open(os.path.join(HERE, 'probes.jsonl'))]
bad = sum(1 for r in rows if not os.path.exists(r['saved_path']) or hashlib.sha256(open(r['saved_path'], 'rb').read()).hexdigest() != r['sha256'])
print('requests', len(rows), 'bytes', sum(r['bytes'] for r in rows), 'status', dict(collections.Counter(r['http_status'] for r in rows)), 'hash_mismatch', bad)
print('per host', dict(collections.Counter(re.sub(r'^https?://([^/]+)/.*$', r'\1', r['url']) for r in rows)))
print('first', min(r['retrieved_at'] for r in rows), 'last', max(r['retrieved_at'] for r in rows))

print('\n## 1. repo licences (GitHub API)')
for f in sorted(glob.glob(os.path.join(RC, 'gh_repo_*.json'))):
    d = json.load(open(f))
    print(d['full_name'], '| spdx', (d.get('license') or {}).get('spdx_id'), '| pushed_at', d['pushed_at'])

print('\n## 2. openfootball league .txt season files (repo trees)')
spec = {'england': r'(?:^|/)(\d{4}-\d{2})/1-premierleague\.txt$', 'espana': r'(?:^|/)(\d{4}-\d{2})/1-liga\.txt$',
        'deutschland': r'(?:^|/)(\d{4}-\d{2})/1-bundesliga\.txt$', 'italy': r'(?:^|/)(\d{4}-\d{2})/1-seriea\.txt$',
        'europe': r'^france/(\d{4}-\d{2})_fr1\.txt$'}
for r, pat in spec.items():
    t = json.load(open(os.path.join(RC, 'gh_tree_openfootball_%s.json' % r)))
    s = sorted({re.search(pat, x['path']).group(1) for x in t['tree'] if x['type'] == 'blob' and re.search(pat, x['path'])})
    print(r, 'truncated', t['truncated'], '| season files', len(s), s[0], '..', s[-1])

print('\n## 3. openfootball champions-league cl.txt / el.txt: header "# Matches", score lines, a.e.t., pen.')
SC = re.compile(r'\s(\d+)-(\d+)(?:\s|$)')
for comp in ['cl', 'el']:
    for f in sorted(glob.glob(os.path.join(RC, 'of_cl_*_%s.txt' % comp)) + glob.glob(os.path.join(S1, 'of_cl_*_%s.txt' % comp))):
        t = open(f, encoding='utf-8', errors='replace').read()
        hdr = re.search(r'#\s*Matches\s+(\d+)', t)
        lines = [l for l in t.splitlines() if ' v ' in l]
        scored = [l for l in lines if SC.search(l.split(' v ', 1)[1])]
        print(os.path.basename(f), '| header matches', hdr.group(1) if hdr else None, '| " v " lines', len(lines), '| with score', len(scored),
              '| a.e.t.', sum('a.e.t.' in l for l in lines), '| pen.', sum('pen.' in l for l in lines), '| from', 'S1' if f.startswith(S1) else 'recheck')

print('\n## 4. engsoccerdata champs.csv')
import csv
r = list(csv.DictReader(open(os.path.join(RC, 'esd_champs.csv'), encoding='utf-8', errors='replace')))
c = collections.Counter(x['Season'] for x in r)
print('rows', len(r), '| seasons', min(c, key=int), '..', max(c, key=int), '| rows 2010..2017', {s: c[s] for s in sorted(c, key=int) if int(s) >= 2010})
print('rows with aet', sum(1 for x in r if x['aet'] not in ('', 'NA')), '| with pens', sum(1 for x in r if x['pens'] not in ('', 'NA')))
print('DESCRIPTION License line:', [l for l in open(os.path.join(RC, 'esd_DESCRIPTION')) if l.startswith('License')])

print('\n## 5. FiveThirtyEight spi_matches.csv response')
b = open(os.path.join(RC, 'fte_spi_matches.csv'), 'rb').read()
print('bytes', len(b), '| starts with html', b.lstrip()[:15].lower().startswith(b'<!doctype html'), '| title', re.findall(rb'<title>([^<]*)', b)[:1])

print('\n## 6. Wikidata')
print(json.load(open(os.path.join(RC, 'wikidata_epl_match_items.json')))['results']['bindings'])
