"""Read-only: would the app's model have had an edge against the OPENING DraftKings line (about a day before the game)?
Model = the stored two-hour forecast's score distribution (params), re-priced at the opening line with app.markets."""
import os, sys, json, numpy as np
import value_audit as va
from app import markets
A=sys.argv[2]; snap=sys.argv[1]; rng=np.random.default_rng(7)
rows=[]
for sp in ('nba','nhl'):
    st=va.Store(sp, f'{snap}/{sp}.sqlite', A); c=st.c
    for g in st.games.values():
        if g['season_type'] not in va.COMPETITIVE or g['status']!='final' or not g['result_available_at'] or g['result_available_at']>A: continue
        gid=g['game_id']; tip=g['tipoff_at']
        mf=c.execute("SELECT * FROM market_forecasts WHERE game_id=? AND policy='near_tipoff' AND run_type='live' AND tipoff_at=? AND generated_at<cutoff_at "
                     "AND model_version NOT LIKE 'research-%' ORDER BY generated_at DESC, forecast_id DESC LIMIT 1",(gid,tip)).fetchone()
        pr=c.execute("SELECT * FROM predictions WHERE game_id=? AND run_type='live' AND cutoff_at=? AND generated_at<cutoff_at AND model_version LIKE '%-policy=near_tipoff' "
                     "AND model_version NOT LIKE 'research-%' ORDER BY generated_at DESC LIMIT 1",(gid,tip)).fetchone()
        if not mf or not pr: continue
        p=json.loads(mf['params'])
        dist=(markets.MarginTotalNormal(p['margin'],p['sd_margin'],p['total'],p['sd_total']) if sp=='nba'
              else markets.poisson_grid(p['lambda_home'],p['lambda_away'],p['ot_home_share'],empty_net_goals='empty_net' in p))
        for m in ('winner','spread','total'):
            o=c.execute("SELECT * FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' AND price_a IS NOT NULL AND price_b IS NOT NULL "
                        "AND retrieved_at<? ORDER BY retrieved_at LIMIT 1",(gid,m,tip)).fetchone()
            cl=c.execute("SELECT * FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' AND price_a IS NOT NULL AND price_b IS NOT NULL "
                         "AND retrieved_at<? ORDER BY retrieved_at DESC LIMIT 1",(gid,m,tip)).fetchone()
            if not o or not cl: continue
            early=(va.ts(tip)-va.ts(o['retrieved_at'])).total_seconds()/3600
            if early<6 or (va.ts(tip)-va.ts(cl['retrieved_at'])).total_seconds()/60>30: continue
            # did either team finish another game between the opening quote and the stored forecast? then the model at open differed
            played=c.execute("SELECT COUNT(*) FROM games WHERE game_id<>? AND status='final' AND tipoff_at>? AND tipoff_at<? AND "
                             "(home_team_id IN (?,?) OR away_team_id IN (?,?))",(gid,o['retrieved_at'],tip,g['home_team_id'],g['away_team_id'],g['home_team_id'],g['away_team_id'])).fetchone()[0]
            if m=='winner': pa,pb=pr['home_win_prob'],1-pr['home_win_prob']
            else:
                a_,mid,b_=dist.market(m,o['line']); pa,pb=a_/(a_+b_),b_/(a_+b_)
            side='a' if pa>=pb else 'b'; conf=max(pa,pb)
            price=o['price_a'] if side=='a' else o['price_b']
            oa,ob,_=va.devig(dict(o)); ca,cb,_=va.devig(dict(cl))
            real=markets.settle(m,o['line'],status='final',home=g['home_score'],away=g['away_score'])
            ret=None if real not in('a','b','mid') else 0.0 if real=='mid' else (va.dec(price)-1 if real==side else -1.0)
            if m=='winner': move=None; dp=(ca if side=='a' else cb)-(oa if side=='a' else ob)
            else:
                d=cl['line']-o['line']; move=(-d if side=='a' else d) if m=='spread' else (d if side=='a' else -d)
                dp=((ca if side=='a' else cb)-(oa if side=='a' else ob)) if abs(d)<1e-9 else None
            rows.append(dict(sp=sp,m=m,g=sp+gid,edge=conf-va.implied(price),ret=ret,move=move,dp=dp,played=played,early=early))
def line(label,rs):
    n=len(rs)
    if n<5: print(f"{label:34s} n={n}"); return
    cl=[x['g'] for x in rs]
    r=va.cboot([x['ret'] for x in rs if x['ret'] is not None],[x['g'] for x in rs if x['ret'] is not None],2000,rng)
    if rs[0]['m']=='winner':
        v=va.cboot([100*x['dp'] for x in rs],cl,2000,rng); mv=f"market prob for pick, open→close {v[0]:+.2f} pts [{v[1]:+.2f},{v[2]:+.2f}]"
    else:
        v=va.cboot([x['move'] for x in rs],cl,2000,rng); tow=sum(x['move']>0 for x in rs)/n; aw=sum(x['move']<0 for x in rs)/n
        mv=f"line toward pick {tow:.0%} / away {aw:.0%}, mean {v[0]:+.2f} pts [{v[1]:+.2f},{v[2]:+.2f}]"
    print(f"{label:34s} n={n:3d} | return at open price {100*r[0]:+.1f}% [{100*r[1]:+.1f},{100*r[2]:+.1f}] | {mv}")
print('median hours before start of opening quote:',round(float(np.median([x['early'] for x in rows])),1),'rows',len(rows))
for m in ('winner','spread','total'):
    for sp in ('nba','nhl','all'):
        rs=[x for x in rows if x['m']==m and (sp=='all' or x['sp']==sp)]
        line(f"{sp.upper()} {m}: every model pick",rs)
        line(f"{sp.upper()} {m}: model beats open price",[x for x in rs if x['edge']>0])
    line(f"ALL {m}: beats price, no game since open",[x for x in rows if x['m']==m and x['edge']>0 and x['played']==0])
allr=[x for x in rows if x['edge']>0]
r=va.cboot([x['ret'] for x in allr if x['ret'] is not None],[x['g'] for x in allr if x['ret'] is not None],2000,rng)
print(f"ALL markets, model beats open price: n={len(allr)} return {100*r[0]:+.1f}% [{100*r[1]:+.1f},{100*r[2]:+.1f}]")
