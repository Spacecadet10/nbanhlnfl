import sys, sqlite3
from pathlib import Path
import app
assert app.__file__.startswith('/Users/andry/Desktop/nba-predictor-batch1/backend/app/'), app.__file__
from app import db
T='2026-11-01T00:00:00Z'
def build(path, sport, wal=False):
    c = db.connect(Path(path)); db.migrate(c)
    c.execute("PRAGMA foreign_keys=ON")
    if wal: c.execute("PRAGMA journal_mode=WAL")
    c.execute("INSERT INTO store_identity(sport,claimed_at) VALUES (?,?)",(sport,T))
    p = {'nba':'espn:','nhl':'nhl:','nfl':'nfl:','soccer':'soccer:'}[sport]
    for i in (1,2): c.execute("INSERT INTO teams VALUES (?,?,?,?)",(f"{p}t{i}",f"T{i}",f"Team {i}",T))
    c.execute("INSERT INTO raw_responses VALUES (1,'src','ep','k','h',10,'p','v1',?)",(T,))
    c.execute("INSERT INTO raw_fetches VALUES (1,1,?)",(T,))
    if sport=='soccer':
        c.execute("""INSERT INTO soccer_matches(match_id,competition_id,event_id,season,season_type_id,kickoff_at,home_team_id,away_team_id,status,source_status,decided_in,home_score,away_score,first_seen_at,result_available_at,last_raw_id,updated_at)
          VALUES ('soccer:eng.1:123','eng.1','123',2026,1,?,?,?,'final','FT','regulation',2,1,?,?,1,?)""",(T,p+'t1',p+'t2',T,T,T))
    else:
        c.execute("""INSERT INTO games(game_id,season,season_type,tipoff_at,home_team_id,away_team_id,neutral_site,status,home_score,away_score,first_seen_at,result_available_at,last_raw_id,updated_at)
          VALUES (?,2027,'regular',?,?,?,0,'final',100,90,?,?,1,?)""",(p+'g1',T,p+'t1',p+'t2',T,T,T))
        c.execute("INSERT INTO predictions(idempotency_key,game_id,run_type,model_version,dataset_version,feature_version,cutoff_at,generated_at,home_win_prob) VALUES ('k1',?,'live','m','d','f',?,?,0.6)",(p+'g1',T,T))
        c.execute("INSERT INTO result_revisions(game_id,revision,home_score,away_score,observed_at,raw_id,basis) VALUES (?,1,100,90,?,1,'observed')",(p+'g1',T))
        if sport in ('nfl','nhl','nba'):
            try: c.execute("INSERT INTO market_quotes(sport,game_id,provider,market,price_label,line,price_a,price_b,p_a_devig,p_b_devig,retrieved_at,fetch_id,raw_id) VALUES (?,?,'pv','spread','open',-3.5,-110,-110,0.5,0.5,?,1,1)",(sport,p+'g1',T))
            except Exception as e: print('market_quotes insert',sport,e)
    c.execute("INSERT INTO job_runs(job,started_at,status) VALUES ('refresh',?,'success')",(T,))
    c.commit()
    if not wal: c.execute("PRAGMA journal_mode=DELETE")
    print(sport,'fk_check',c.execute("PRAGMA foreign_key_check").fetchall(),'ver',c.execute("select max(version) from schema_migrations").fetchone()[0])
    c.close()
if __name__=='__main__':
    d=Path(sys.argv[1]); d.mkdir(parents=True,exist_ok=True)
    for s in sys.argv[2:]:
        build(d/f"{s.split(':')[0]}.sqlite", s.split(':')[0], wal=s.endswith(':wal'))
