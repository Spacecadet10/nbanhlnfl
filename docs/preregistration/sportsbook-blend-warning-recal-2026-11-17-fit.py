"""Fit p' = sigmoid(a + b*logit(p)) on recovered WINNER rows, tipoff < 2026-11-15, per sport. Frozen output."""
import sqlite3,math,sys,json
S=sys.argv[1]
def lg(p):p=min(max(p,1e-6),1-1e-6);return math.log(p/(1-p))
out={}
for sp in ['nba','nhl']:
  db=sqlite3.connect(f'file:{S}/snap/{sp}.sqlite?immutable=1',uri=True)
  rows=db.execute("""select r.game_id,r.p_a,g.home_score,g.away_score from recovered_forecasts r join games g using(game_id)
     where r.market='winner' and r.tipoff_at<'2026-11-15' and g.home_score is not null and g.away_score is not null
       and r.latest_result_at<=r.cutoff_at""").fetchall()
  # one row per game (latest cutoff not available here; recovered is one per game) 
  seen={};[seen.setdefault(g,(p,h,a)) for g,p,h,a in rows]
  X=[(lg(p),1 if h>a else 0) for p,h,a in seen.values() if h!=a]
  a,b=0.0,1.0
  for _ in range(100):  # Newton
    ga=gb=haa=hab=hbb=0
    for x,y in X:
      m=1/(1+math.exp(-(a+b*x)));w=m*(1-m);ga+=y-m;gb+=(y-m)*x;haa+=w;hab+=w*x;hbb+=w*x*x
    det=haa*hbb-hab*hab;da=(hbb*ga-hab*gb)/det;db_=(haa*gb-hab*ga)/det;a+=da;b+=db_
    if abs(da)+abs(db_)<1e-10:break
  out[sp]=dict(a=round(a,4),b=round(b,4),games=len(X))
print(json.dumps(out))
