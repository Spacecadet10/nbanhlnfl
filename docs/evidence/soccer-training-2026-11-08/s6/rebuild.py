"""Rebuild the scratch soccer store from the draft store's provenance + raw cache. Usage: rebuild.py <data_dir>"""
import sys, json, os, time
sys.path.insert(0, '.')
D = sys.argv[1]
assert 'nba-predictor-research-data' in D and os.environ['NBA_DATA_DIR'] == D
from app import db, soccer_history as h
p = os.path.join(D, 'soccer.sqlite')
for suf in ('', '-wal', '-shm'):
    if os.path.exists(p + suf): os.remove(p + suf)
c = db.connect(sport='soccer'); db.migrate(c); db.claim_store(c, 'soccer'); c.commit()
print('copied', h.copy_provenance(c, os.path.join(D, 'soccer.draft0036.sqlite')))
t = time.time(); print(json.dumps(h.replay(c)), round(time.time() - t, 1), 's')
print(json.dumps(h.digest(c)))
for r in c.execute('select competition_id, season, locator, reason, detail from soccer_hist_rejections order by 1,2,3'): print(tuple(r))
# verify every cached results file against the blob sha in the cached tree listing of its commit
from app import raw_cache
trees = {}
for r in c.execute("select repo, commit_sha, raw_id from soccer_source_files where kind='tree'"):
    trees[(r[0], r[1])] = {e['path']: e.get('sha') for e in json.loads(raw_cache.load(c, r[2]))['tree']}
ok = bad = 0
for r in c.execute("select repo, commit_sha, path, raw_id from soccer_source_files where kind='results'"):
    if trees[(r[0], r[1])].get(r[2]) == h.git_blob_sha(raw_cache.load(c, r[3])): ok += 1
    else: bad += 1
print('blob check ok', ok, 'bad', bad)
