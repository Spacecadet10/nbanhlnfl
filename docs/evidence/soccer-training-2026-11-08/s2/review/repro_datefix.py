import sys, os, json
D='/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/b215902d-5053-4056-b10e-e326a6d4f775/scratchpad/review-s2/repro_store'
os.environ['NBA_DATA_DIR']=D
sys.path.insert(0,'/Users/andry/Desktop/nba-predictor-soccer-train/backend')
from app import db, soccer_history as h
c=db.connect(sport='soccer'); db.migrate(c); db.claim_store(c,'soccer'); c.commit()
def league(ms): return json.dumps({"name":"x","matches":ms}).encode()
def m(r,d,a,b,s): return {"round":r,"date":d,"time":"15:00","team1":a,"team2":b,"score":s}
base=[m("Matchday 1","2023-08-12","Arsenal FC","Burnley FC",{"ft":[2,1]})]
v1=base+[m("Matchday 2","2023-08-19","Burnley FC","Arsenal FC",{"ft":[0,1]})]
# later retrieval corrects the date of the Matchday 2 match to 2023-10-07 (score unchanged)
v2=base+[m("Matchday 2","2023-10-07","Burnley FC","Arsenal FC",{"ft":[0,1]})]
class Hub:
    def __init__(s,sha,body): s.sha,s.body=sha,body
    def __call__(s,url):
        if url.endswith(f"/git/trees/{s.sha}?recursive=1"):
            return json.dumps({"sha":s.sha,"truncated":False,"tree":[{"path":"2023-24/en.1.json","type":"blob"}]}).encode()
        return s.body
for sha,body,at in (("a"*40,league(v1),"2026-11-08T10:00:00Z"),("b"*40,league(v2),"2026-12-01T10:00:00Z")):
    it=iter([at,at]); h.download(c,pins={"football.json":sha},fetch=Hub(sha,body),sleep=0,now=lambda: next(it),only={"football.json"}); print(h.replay(c))
for r in c.execute("select home_name, away_name, local_date, result_available_at, result_availability_basis, grading from soccer_hist_matches"): print(dict(r))
for r in c.execute("select hist_match_id, revision, local_date, result_available_at, result_availability_basis, observed_at from soccer_hist_revisions"): print(dict(r))
