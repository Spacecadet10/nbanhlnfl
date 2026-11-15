import sys, os, json
D='/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/b215902d-5053-4056-b10e-e326a6d4f775/scratchpad/review-s2/repro_store2'
os.environ['NBA_DATA_DIR']=D
sys.path.insert(0,'/Users/andry/Desktop/nba-predictor-soccer-train/backend')
from app import db, soccer_history as h, soccer_clubs
c=db.connect(sport='soccer'); db.migrate(c); db.claim_store(c,'soccer'); c.commit()
def m(r,d,a,b,s): return {"round":r,"date":d,"time":"15:00","team1":a,"team2":b,"score":s}
body=json.dumps({"name":"x","matches":[m("Matchday 1","2023-08-12","Arsenal FC","Burnley",{"ft":[2,1]})]}).encode()
sha="a"*40
def hub(url):
    if url.endswith(f"/git/trees/{sha}?recursive=1"):
        return json.dumps({"sha":sha,"truncated":False,"tree":[{"path":"2023-24/en.1.json","type":"blob"}]}).encode()
    return body
it=iter(["2026-11-08T10:00:00Z"]*2); h.download(c,pins={"football.json":sha},fetch=hub,sleep=0,now=lambda: next(it),only={"football.json"}); print(h.replay(c))
# a new map version aliases "Burnley" -> "Burnley FC"
soccer_clubs.ALIASES[("ENG","Burnley")]=("ENG","Burnley FC"); soccer_clubs.MAP_VERSION="2026-11-09.1"
print(h.replay(c))
print([dict(r) for r in c.execute("select home_club_id, away_club_id from soccer_hist_matches")])
print([dict(r) for r in c.execute("select source_name, club_id, map_version from soccer_club_names where source_name like 'Burnley%'")])
