import json, gzip, hashlib, sys
base='/Users/andry/nba-predictor-research-data/soccer-train-20261108/data/raw/'
trees={'football.json':json.load(open('net/tree-fj.json')),'champions-league':json.load(open('net/tree-cl.json'))}
print({k:(v.get('truncated'),len(v['tree'])) for k,v in trees.items()})
blob={k:{e['path']:e['sha'] for e in v['tree'] if e['type']=='blob'} for k,v in trees.items()}
ok=bad=0
for line in open('files.tsv'):
    rp,h,size,repo,path=line.rstrip('\n').split('|')
    b=gzip.open(base+rp).read()
    assert hashlib.sha256(b).hexdigest()==h and len(b)==int(size), path
    g=hashlib.sha1(b'blob %d\0'%len(b)+b).hexdigest()
    if g==blob[repo].get(path): ok+=1
    else: bad+=1; print('MISMATCH',repo,path)
print('ok',ok,'bad',bad)
# list candidate files in trees
import re
for repo,t in blob.items():
    names=sorted(p for p in t if re.match(r'^\d{4}-\d{2}/',p))
    if repo=='champions-league': print(names)
    else:
        print(sorted({p.split('/')[1] for p in names}))
        print(sorted({p.split('/')[0] for p in names if p.split('/')[1] in ('en.1.json','es.1.json','de.1.json','it.1.json','fr.1.json')}))
        for lg in ('en.1.json','es.1.json','de.1.json','it.1.json','fr.1.json'):
            print(lg, sorted(p.split('/')[0] for p in names if p.endswith('/'+lg)))
