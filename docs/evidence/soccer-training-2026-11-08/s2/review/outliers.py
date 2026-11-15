import sqlite3, statistics, datetime as dt
c=sqlite3.connect('file:/Users/andry/nba-predictor-research-data/soccer-train-20261108/data/soccer.sqlite?mode=ro',uri=True)
rows=c.execute("select competition_id,season,round,local_date,home_name,away_name,status from soccer_hist_matches where stage='league'").fetchall()
from collections import defaultdict
g=defaultdict(list)
for r in rows: g[(r[0],r[1],r[2])].append(r)
out=[]
for k,v in g.items():
    ds=sorted(dt.date.fromisoformat(r[3]) for r in v)
    med=ds[len(ds)//2]
    for r in v:
        d=dt.date.fromisoformat(r[3])
        if abs((d-med).days)>45: out.append((k,r[3],str(med),r[4],r[5],r[6]))
for o in sorted(out): print(o)
print(len(out))
# rounds: each matchday has 10/9 matches?
bad=[(k,len(v)) for k,v in g.items() if len(v) not in (9,10)]
print('round sizes off:',sorted(bad)[:40], len(bad))
