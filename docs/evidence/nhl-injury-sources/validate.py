import json,sys,glob,unicodedata,re,collections
sys.path.insert(0,'.')
from parse_espn import load,parse
def norm(s): return re.sub(r'[^a-z]','',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower())
pairs={'2017-11-15':'20171115105343','2019-01-10':'20190110060711','2022-12-01':'20221201143059','2024-01-15':'20240115083709'}
for day,ts in pairs.items():
    _,rows=parse(load(f'raw/espn_{ts}.html'))
    # latest row per player (old layout lists history; first row per player is newest)
    seen={}
    for r in rows: seen.setdefault((r['team'],norm(r['player'])),r)
    sc=json.load(open(f'raw/pbp/score_{day}.json'))
    playing={}; dressed={}; first=None
    for g in sc['games']:
        if g.get('gameType')!=2: continue
        first=min(first or g['startTimeUTC'],g['startTimeUTC'])
        p=json.load(open(f"raw/pbp/{g['id']}.json"))
        for side in ('homeTeam','awayTeam'):
            t=p[side]; name=(t.get('placeName',{}).get('default','')+' '+t.get('commonName',{}).get('default','')).strip()
            playing[name]=t['id']
        for rs in p.get('rosterSpots',[]):
            dressed.setdefault(rs['teamId'],set()).add(norm(rs['firstName']['default']+rs['lastName']['default']))
    res=collections.defaultdict(lambda:[0,0]); unmatched_team=set()
    for (team,pl),r in seen.items():
        tid=playing.get(team.replace('St. Louis','St. Louis'))
        if tid is None:
            if not any(team.split()[-1]==k.split()[-1] for k in playing): continue
            tid=[v for k,v in playing.items() if k.split()[-1]==team.split()[-1]][0]
        st=r['status'].lower(); st={'ir':'injured reserve'}.get(st,st)
        res[st][0]+=1; res[st][1]+= pl in dressed.get(tid,set())
    print(day,'snapshot',ts,'first puck drop',first,'teams playing',len(playing))
    for st,(n,d) in sorted(res.items()): print(f'   {st:16s} listed {n:3d}  dressed anyway {d:3d}')
