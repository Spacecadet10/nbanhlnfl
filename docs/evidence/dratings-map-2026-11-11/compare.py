"""DRatings vs the app, settled NBA/NHL games in the 14 days before 2026-11-11T05:00Z. Read-only (immutable) on the stores.
Match = DRatings start time == store start + fixed offset (NBA 10 min, NHL 7 min; checked per game) + home and away team (DRatings full name -> store name via normalisation and a short alias
list). App = canonical game_forecasts row (latest generated before the start) for winner and team totals. DraftKings =
stored market_quotes (latest current before the start). DRatings = completed-list win % (as shown after the game) and the
detail page's projected points (page 'Updated' time recorded)."""
import json, math, os, re, html, sqlite3, sys, statistics as st, unicodedata
from datetime import datetime, timedelta, timezone
HERE = os.path.dirname(os.path.abspath(__file__)); P = f'{HERE}/pages'; DATA = sys.argv[1]
sys.path.insert(0, HERE); import parse
NOW = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc); SINCE = NOW - timedelta(days=14)
OFFSET = {'nba': timedelta(minutes=10), 'nhl': timedelta(minutes=7)}
ALIAS = {'los angeles clippers': 'la clippers', 'utah hockey club': 'utah mammoth', 'montreal canadiens': 'montreal canadiens'}
def norm(n):
    n = unicodedata.normalize('NFKD', n).encode('ascii', 'ignore').decode().lower().replace('.', '').strip()
    return ALIAS.get(n, n)
def dr_rows(sport, files):
    out = []
    for f in files:
        t = open(f'{P}/{f}').read()
        for tb in re.findall(r'<table.*?</table>', t, re.S):
            if 'Final' not in tb[:4000]: continue
            for tr in re.findall(r'<tr.*?</tr>', re.sub(r'<thead.*?</thead>', '', tb, flags=re.S), re.S):
                dt = re.findall(r'datetime="([^"]+)"', tr); link = re.findall(r'href="(/predictor/[^"]+/([0-9a-f-]{36}))"', tr)
                teams = [html.unescape(x).strip() for x in re.findall(r'href="/teams/[^"]+">([^<]+)</a>', tr)]
                c = parse.cells(tr)
                if not dt or len(teams) != 2 or len(c) < 6: continue
                pct = [float(x) / 100 for x in re.findall(r'([\d.]+)%', c[2])]
                fin = [int(x) for x in re.findall(r'\d+', c[5])][:2]
                out.append(dict(start=dt[0], away=teams[0], home=teams[1], p_away=pct[0], p_home=pct[1], final_away=fin[0], final_home=fin[1], uuid=link[0][1] if link else None))
    return {(r['start'], norm(r['away']), norm(r['home'])): r for r in out}
def detail(sport, uuid):
    f = f'{P}/detail-{sport}/{uuid}.html'
    if not uuid or not os.path.exists(f): return None
    t = open(f).read(); x = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'(?s)<(script|style)[^>]*>.*?</\1>', ' ', t))))
    m = re.search(r'([\d.]+) Projected (?:Points|Goals) ([\d.]+)', x)
    upd = re.findall(r'datetime="([^"]+)"', t)
    return dict(proj_away=float(m.group(1)), proj_home=float(m.group(2)), updated=upd[0] if upd else None) if m else None
res = {'window': [SINCE.isoformat(), NOW.isoformat()]}; allrows = []
for sport, slug in (('nba', 'nba-basketball'), ('nhl', 'nhl-hockey')):
    dr = dr_rows(sport, [f'{sport}-predictions.html'] + [f'{slug}-completed-{n}.html' for n in range(2, 8)])
    c = sqlite3.connect(f'file:{DATA}/{sport}.sqlite?immutable=1', uri=True); c.row_factory = sqlite3.Row
    games = c.execute("""SELECT g.game_id, g.tipoff_at, g.home_score, g.away_score, h.name hn, a.name an, h.abbr ha, a.abbr aa FROM games g
        JOIN teams h ON h.team_id=g.home_team_id JOIN teams a ON a.team_id=g.away_team_id
        WHERE g.status='final' AND g.season_type IN ('regular','play_in','postseason') AND g.tipoff_at>=? AND g.tipoff_at<?""",
                      (SINCE.strftime('%Y-%m-%dT%H:%M:%SZ'), NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))).fetchall()
    def fc(gid, market, tip):
        return c.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market=? AND status='forecast' AND generated_at<? ORDER BY generated_at DESC, forecast_id DESC LIMIT 1", (gid, market, tip)).fetchone()
    def quote(gid, market, tip):
        return c.execute("SELECT * FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' AND provider IN ('DraftKings','Draft Kings') AND retrieved_at<? ORDER BY retrieved_at DESC LIMIT 1", (gid, market, tip)).fetchone()
    def pair(gid, tip):
        return c.execute("""SELECT s.line sl, t.line tl, s.retrieved_at FROM market_quotes s JOIN market_quotes t ON t.game_id=s.game_id AND t.provider=s.provider
            AND t.fetch_id=s.fetch_id AND t.price_label=s.price_label WHERE s.game_id=? AND s.market='spread' AND t.market='total' AND s.price_label='current'
            AND s.provider IN ('DraftKings','Draft Kings') AND s.retrieved_at<? ORDER BY s.retrieved_at DESC LIMIT 1""", (gid, tip)).fetchone()
    rows, unmatched, used = [], [], set()
    for g in games:
        # DRatings lists the actual tip/puck-drop: the store's scheduled start + a fixed offset (NBA 10 min, NHL 7 min).
        k = ((datetime.fromisoformat(g['tipoff_at'].replace('Z', '+00:00')) + OFFSET[sport]).strftime('%Y-%m-%dT%H:%M:%SZ'), norm(g['an']), norm(g['hn']))
        d = dr.get(k)
        if not d:
            near = [kk for kk in dr if norm(g['an']) == kk[1] and norm(g['hn']) == kk[2]]
            unmatched.append(dict(game_id=g['game_id'], start=g['tipoff_at'], matchup=f"{g['aa']} @ {g['ha']}",
                                  reason=('same teams at a different start time: ' + ','.join(x[0] for x in near)) if near else
                                         'no DRatings row with this start time and these team names in the 7 completed pages'))
            continue
        used.add(k)
        w = fc(g['game_id'], 'winner', g['tipoff_at']); q = quote(g['game_id'], 'winner', g['tipoff_at']); pr = pair(g['game_id'], g['tipoff_at'])
        th, ta = fc(g['game_id'], 'team_total_home', g['tipoff_at']), fc(g['game_id'], 'team_total_away', g['tipoff_at'])
        det = detail(sport, d['uuid'])
        home_won = g['home_score'] > g['away_score']
        if d['final_home'] != g['home_score'] or d['final_away'] != g['away_score']:
            unmatched.append(dict(game_id=g['game_id'], start=g['tipoff_at'], matchup=f"{g['aa']} @ {g['ha']}", reason=f"final score differs: DRatings {d['final_away']}-{d['final_home']} vs store {g['away_score']}-{g['home_score']} (kept, flagged)"))
        r = dict(sport=sport, game_id=g['game_id'], start=g['tipoff_at'], matchup=f"{g['aa']} @ {g['ha']}", home_won=home_won,
                 final=f"{g['away_score']}-{g['home_score']}",
                 app_p_home=w['p_a'] if w else None, app_generated_at=w['generated_at'] if w else None,
                 dr_p_home=d['p_home'], dk_home_price=q['price_a'] if q else None, dk_away_price=q['price_b'] if q else None,
                 dk_p_home_devig=q['p_a_devig'] if q else None, dk_quote_at=q['retrieved_at'] if q else None,
                 dr_proj_home=det and det['proj_home'], dr_proj_away=det and det['proj_away'], dr_updated=det and det['updated'],
                 implied_home=(pr['tl'] / 2 - pr['sl'] / 2) if pr else None, implied_away=(pr['tl'] / 2 + pr['sl'] / 2) if pr else None,
                 app_tt_home=th['line'] if th else None, app_tt_away=ta['line'] if ta else None,
                 actual_home=g['home_score'], actual_away=g['away_score'])
        rows.append(r)
    allrows += rows
    def wl(sub, key):
        s = [r for r in sub if r[key] is not None and r[key] != .5]
        w = sum((r[key] > .5) == r['home_won'] for r in s); return dict(n=len(s), wins=w, losses=len(s) - w, rate=round(w / len(s), 3) if s else None)
    both = [r for r in rows if r['app_p_home'] is not None]
    agree = [r for r in both if (r['app_p_home'] > .5) == (r['dr_p_home'] > .5)]
    dis = [r for r in both if (r['app_p_home'] > .5) != (r['dr_p_home'] > .5)]
    def brier(sub, key): s = [r for r in sub if r[key] is not None]; return round(st.mean((r[key] - r['home_won']) ** 2 for r in s), 4) if s else None
    def ll(sub, key): s = [r for r in sub if r[key] is not None]; return round(st.mean(-math.log(min(max(r[key] if r['home_won'] else 1 - r[key], 1e-6), 1)) for r in s), 4) if s else None
    tt = [(r, side) for r in rows for side in ('home', 'away') if r[f'dr_proj_{side}'] is not None]
    def mae(pairs, a, b): s = [(x, side) for x, side in pairs if x[f'{a}_{side}'] is not None and x[f'{b}_{side}'] is not None]; return (round(st.mean(abs(x[f'{a}_{side}'] - x[f'{b}_{side}']) for x, side in s), 2), len(s)) if s else (None, 0)
    res[sport] = dict(store_settled_games=len(games), matched=len(rows), unmatched=[u for u in unmatched], dratings_rows_in_pages=len(dr),
        app_vs_dratings=dict(both=len(both), agree=len(agree), disagree=len(dis),
            app_when_agree=wl(agree, 'app_p_home'), app_when_disagree=wl(dis, 'app_p_home'), dratings_when_disagree=wl(dis, 'dr_p_home')),
        pick_records=dict(app=wl(both, 'app_p_home'), dratings=wl(both, 'dr_p_home'), draftkings_devig=wl([r for r in both if r['dk_p_home_devig'] is not None], 'dk_p_home_devig')),
        brier_same_games=dict(app=brier(both, 'app_p_home'), dratings=brier(both, 'dr_p_home'), draftkings=brier([r for r in both if r['dk_p_home_devig'] is not None], 'dk_p_home_devig'), n=len([r for r in both if r['dk_p_home_devig'] is not None])),
        logloss_same_games=dict(app=ll(both, 'app_p_home'), dratings=ll(both, 'dr_p_home')),
        team_scores=dict(dratings_proj_vs_implied_mae=mae(tt, 'dr_proj', 'implied'), dratings_proj_vs_app_tt_mae=mae(tt, 'dr_proj', 'app_tt'),
                         actual_mae=dict(dratings_proj=mae(tt, 'dr_proj', 'actual'), implied=mae(tt, 'implied', 'actual'), app_tt_line=mae(tt, 'app_tt', 'actual')),
                         detail_pages_parsed=len({r['game_id'] for r, s in tt})))
json.dump(res, open(f'{HERE}/comparison-summary.json', 'w'), indent=1)
with open(f'{HERE}/comparison-rows.jsonl', 'w') as f:
    for r in allrows: f.write(json.dumps(r) + '\n')
print(json.dumps({s: {k: v for k, v in res[s].items() if k != 'unmatched'} | {'unmatched_n': len(res[s]['unmatched'])} for s in ('nba', 'nhl')}, indent=1))
