import sys, json, sqlite3, os
os.environ['NBA_DATA_DIR'] = '/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/b215902d-5053-4056-b10e-e326a6d4f775/scratchpad/review-s2/none'
sys.path.insert(0, '/Users/andry/Desktop/nba-predictor-soccer-train/backend')
from app import soccer_history as h
base='/Users/andry/nba-predictor-research-data/soccer-train-20261108/'
out={}
for k in ('data','repro'):
    c=sqlite3.connect(f'file:{base}{k}/soccer.sqlite?mode=ro', uri=True); c.row_factory=sqlite3.Row
    out[k]=(h.digest(c), h.coverage(c))
print(out['data'][0]==out['repro'][0])
ev=json.load(open('/Users/andry/Desktop/nba-predictor-soccer-train/docs/evidence/soccer-training-2026-11-08/s2/coverage.json'))
def strip(cv):  # drop retrieval-specific fields
    cv=json.loads(json.dumps(cv))
    for s in cv['seasons']:
        s.pop('latest_file',None)
    return cv
print('evidence==data coverage (excluding latest_file):', strip(ev)==strip(out['data'][1]))
print('data==repro coverage (excl latest_file):', strip(out['repro'][1])==strip(out['data'][1]))
print(ev==out['data'][1], ev==out['repro'][1])
for t,v in out['data'][0].items(): print(t, v[:12], out['repro'][0][t][:12])
