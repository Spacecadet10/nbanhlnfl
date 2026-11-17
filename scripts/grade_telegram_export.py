"""Grade transcribed Prop51 Telegram cards against final results in a read-only store snapshot."""
import json, re, sqlite3, sys, collections
from datetime import datetime, timedelta

SP = sys.argv[1]
SNAP = SP + '/snap'
cards = json.load(open(SP + '/cards.json'))
MON = {m: i + 1 for i, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}


def start_utc(s):  # "Nov 02, 07:00 PM EST"
    m = re.match(r'(\w{3}) (\d+), (\d+):(\d+) (AM|PM) (EST|EDT)', s)
    mon, day, hh, mm, ap, tz = m.groups()
    hh = int(hh) % 12 + (12 if ap == 'PM' else 0)
    local = datetime(2026, MON[mon], int(day), hh, int(mm))
    return local + timedelta(hours=5 if tz == 'EST' else 4)


def load_games(sport):
    try:
        db = sqlite3.connect(f'file:{SNAP}/{sport}.sqlite?mode=ro', uri=True)
        db.execute('SELECT 1 FROM sqlite_master')
    except sqlite3.OperationalError:   # a WAL store with no -shm file cannot open mode=ro; read the file as is
        db = sqlite3.connect(f'file:{SNAP}/{sport}.sqlite?immutable=1', uri=True)
    if sport == 'soccer':
        q = """select m.match_id, m.kickoff_at, h.name, a.name, m.status, m.home_score, m.away_score, m.decided_in
               from soccer_matches m join teams h on h.team_id=m.home_team_id join teams a on a.team_id=m.away_team_id
               where kickoff_at between '2026-11-01' and '2026-11-18'"""
        return [dict(zip(['id', 'tip', 'home', 'away', 'status', 'hs', 'as', 'dec'], r)) for r in db.execute(q)]
    dec = "(select decision from game_decisions d where d.game_id=g.game_id)" if sport == 'nhl' else "null"
    q = f"""select g.game_id, g.tipoff_at, h.abbr, a.abbr, g.status, g.home_score, g.away_score, {dec}
            from games g join teams h on h.team_id=g.home_team_id join teams a on a.team_id=g.away_team_id
            where tipoff_at between '2026-11-01' and '2026-11-18'"""
    return [dict(zip(['id', 'tip', 'home', 'away', 'status', 'hs', 'as', 'dec'], r)) for r in db.execute(q)]


GAMES = {s: load_games(s) for s in ('nba', 'nhl', 'nfl', 'soccer')}


def norm(x):
    return re.sub(r'[^a-z]', '', x.lower())


def soccer_pair(card):
    h = card['home']
    if card['away'].strip():
        return card['away'].strip(), h.strip()
    for sep in (' vs ', ', '):
        if sep in h:
            a, b = h.split(sep, 1); return a.strip(), b.strip()
    return h, ''


def find_game(card):
    sport = card['sport'].lower()
    t = start_utc(card['starts'])
    if sport == 'soccer':
        a, b = soccer_pair(card)
        names = {norm(a), norm(b)}
    else:
        names = {norm(card['home']), norm(card['away'])}
    best = None
    for g in GAMES[sport]:
        if {norm(g['home']), norm(g['away'])} != names:
            continue
        dt = abs((datetime.fromisoformat(g['tip'].rstrip('Z')) - t).total_seconds())
        if dt <= 6 * 3600 and (best is None or dt < best[0]):
            best = (dt, g)
    return best[1] if best else None


def settle(sport, market, sel, g):
    """Return 'win'/'loss'/'push'/None (cannot settle)."""
    if g['status'] != 'final' or g['hs'] is None:
        return None
    hs, as_ = g['hs'], g['as']
    m = market.lower()
    s = sel.strip()
    if sport == 'soccer':
        if g['dec'] not in ('regulation', None, ''):
            return None  # score may include extra time
        home, away = [norm(x) for x in (g['home'], g['away'])]
        draw = hs == as_
        winner = None if draw else (home if hs > as_ else away)
        team = norm(re.sub(r'\(.*\)|win|or draw', '', s))
        if 'double chance' in m:
            parts = [norm(x) for x in re.sub(r'\(.*\)', '', s).split(' or ')]
            if len(parts) != 2 or any(x not in ('draw', home, away) for x in parts):
                return None
            actual = 'draw' if draw else winner
            return 'win' if actual in parts else 'loss'
        if 'draw no bet' in m:
            return 'push' if draw else ('win' if winner == team else 'loss')
        if 'match result' in m:
            if norm(s).startswith('draw'):
                return 'win' if draw else 'loss'
            return 'win' if winner == team else 'loss'
        return None
    home, away = norm(g['home']), norm(g['away'])
    pts = {home: hs, away: as_}
    tot = hs + as_
    mo = re.match(r'(over|under)\s+([0-9.]+)$', s, re.I)
    if mo and ('total' in m):
        line = float(mo.group(2)); side = mo.group(1).lower()
        if tot == line: return 'push'
        return 'win' if (tot > line) == (side == 'over') else 'loss'
    mo = re.match(r'(\S+)\s+(over|under)\s+([0-9.]+)$', s, re.I)
    if mo and ('team' in m):
        team = norm(mo.group(1)); line = float(mo.group(3)); side = mo.group(2).lower()
        if team not in pts: return None
        v = pts[team]
        if v == line: return 'push'
        return 'win' if (v > line) == (side == 'over') else 'loss'
    mo = re.match(r'(\S+)\s+([+-][0-9.]+)$', s)
    if mo and ('spread' in m or 'puck' in m):
        team = norm(mo.group(1)); line = float(mo.group(2))
        if team not in pts: return None
        other = away if team == home else home
        margin = pts[team] + line - pts[other]
        if margin == 0: return 'push'
        return 'win' if margin > 0 else 'loss'
    mo = re.match(r'(\S+)\s+win$', s)
    if mo and 'winner' in m:
        team = norm(mo.group(1))
        if team not in pts or hs == as_: return None
        return 'win' if pts[team] == max(hs, as_) else 'loss'
    if 'regulation' in m:
        if g['dec'] is None: return None
        reg_draw = g['dec'] in ('OT', 'SO')
        if norm(s).startswith('draw') or 'tie' in s.lower():
            return 'win' if reg_draw else 'loss'
        mo = re.match(r'(\S+)\s+in regulation', s)
        if not mo: return None
        team = norm(mo.group(1))
        if team not in pts: return None
        return 'win' if (not reg_draw and pts[team] == max(hs, as_)) else 'loss'
    return None


def mkey(market):
    m = market.lower()
    for k in ('regulation', 'puck line', 'double chance', 'draw no bet', 'match result', 'home team', 'away team',
              'total', 'spread', 'winner'):
        if k in m:
            return k
    return m


rows_out, unmatched = [], []
for c in cards:
    g = find_game(c)
    if not g:
        unmatched.append({'message_id': c['message_id'], 'sport': c['sport'], 'teams': f"{c['away']} @ {c['home']}", 'starts': c['starts']})
    rec = c['recommended_pick'].strip()
    for r in c['rows']:
        res = settle(c['sport'].lower(), r['market'], r['selection'], g) if g else None
        rows_out.append({
            'message_id': c['message_id'], 'sent_at_local': c['sent_at'], 'sport': c['sport'], 'game_id': g['id'] if g else None,
            'matchup': f"{c['away']} @ {c['home']}" if c['sport'] != 'SOCCER' else ' vs '.join(soccer_pair(c)), 'starts': c['starts'],
            'final_score': (f"{g['away']} {g['as']} - {g['hs']} {g['home']}" + (f" ({g['dec']})" if g['dec'] and c['sport'] == 'NHL' else '')) if g and g['status'] == 'final' else None,
            'market': r['market'], 'market_key': mkey(r['market']), 'selection': r['selection'], 'percent': r['percent'],
            'line_source': r['line_note'], 'recommended': r['selection'].strip() == rec, 'result': res,
        })

# Latest card per game sent before start (what was live at tip-off); earlier cards kept in all_rows.
latest = {}
for c in cards:
    g = find_game(c)
    if not g: continue
    sent = datetime.fromisoformat(c['sent_at']) + timedelta(hours=5)  # export times are Toronto local (EST)
    if sent >= datetime.fromisoformat(g['tip'].rstrip('Z')):   # strictly before the start
        continue
    k = (c['sport'], g['id'])
    if k not in latest or c['sent_at'] > latest[k]['sent_at']:
        latest[k] = c
final_ids = {c['message_id'] for c in latest.values()}
per_game = [r for r in rows_out if r['message_id'] in final_ids]


def summarize(rows):
    out = {}
    W = sum(r['result'] == 'win' for r in rows); L = sum(r['result'] == 'loss' for r in rows); P = sum(r['result'] == 'push' for r in rows)
    pend = sum(r['result'] is None for r in rows)
    decided = [r for r in rows if r['result'] in ('win', 'loss')]
    exp = sum(r['percent'] for r in decided) / 100
    brier = sum((r['percent'] / 100 - (r['result'] == 'win')) ** 2 for r in decided) / len(decided) if decided else None
    out.update(W=W, L=L, P=P, ungraded=pend, hit_rate=round(W / (W + L), 4) if W + L else None,
               expected_wins_from_percentages=round(exp, 1), brier=round(brier, 4) if brier is not None else None)
    return out


report = {'recommended_picks': {}, 'all_markets': {}}
for sport in ('NBA', 'NHL', 'NFL', 'SOCCER', 'ALL'):
    rs = [r for r in per_game if sport == 'ALL' or r['sport'] == sport]
    report['recommended_picks'][sport] = summarize([r for r in rs if r['recommended']])
    by = collections.defaultdict(list)
    for r in rs: by[r['market_key']].append(r)
    report['all_markets'][sport] = {k: summarize(v) for k, v in sorted(by.items())}


def direction(r, g):
    """Side of the pick, as the app keys its exact-percentage record: home/away/over/under/draw (soccer: combined)."""
    sel = re.sub(r'\(.*\)', '', r['selection']).strip()
    low = sel.lower()
    if re.match(r'^(over|under)\b', low):
        return low.split()[0]
    if g is None:
        return None
    home, away = norm(g['home']), norm(g['away'])
    found = []
    for part in re.split(r' or ', sel):
        t = norm(re.sub(r'\b(win|in regulation|draw no bet|over|under)\b.*$', '', part.strip(), flags=re.I) or part)
        found.append('draw' if t == 'draw' else 'home' if t == home else 'away' if t == away else '?')
    side = '+'.join(sorted(found))
    m = re.search(r'\b(over|under)\b', low)
    return side + (' ' + m.group(1) if m and 'team' in r['market_key'] else '')


game_by_id = {g['id']: g for s in GAMES.values() for g in s}
by_key = collections.defaultdict(list)
for r in per_game:
    if r['result'] not in ('win', 'loss', 'push'):
        continue
    k = (r['sport'], r['market_key'], direction(r, game_by_id.get(r['game_id'])), r['percent'])
    by_key[k].append(r)
report['exact_percentages'] = [
    {'sport': k[0], 'market': k[1], 'direction': k[2], 'percent': k[3], 'W': sum(x['result'] == 'win' for x in v),
     'L': sum(x['result'] == 'loss' for x in v), 'P': sum(x['result'] == 'push' for x in v),
     'picks': [f"{x['selection']} ({x['matchup']}, {x['final_score']}): {x['result']}" for x in v]}
    for k, v in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]), kv[0][3]))]

out = {
    'source': '~/Downloads/ChatExport_2026-11-16/result.json (Telegram export), 341 live game cards Nov 2-16 2026, transcribed from card images',
    'graded_against': 'read-only snapshot of the app stores taken 2026-11-17T00:41Z',
    'method': 'One card per game: the last card sent before start. Every market row settled from the final score; '
              'NHL winner includes OT/SO, regulation uses the OT/SO decision; soccer is 90-minute result. '
              'brier = mean (stated% - outcome)^2 on decided rows. Pushes excluded from hit rate.',
    'cards_total': len(cards), 'games_graded_one_card_each': len(latest), 'unmatched_cards': unmatched,
    # summary: exact percentages only, keyed like the app (sport, market, direction, exact %); no ranges
    'summary': report, 'per_game_rows': per_game, 'all_card_rows': rows_out,
}
json.dump(out, open(sys.argv[2], 'w'), indent=1)
print(json.dumps({k: out[k] for k in ('cards_total', 'games_graded_one_card_each')}, indent=1))
print('unmatched', len(unmatched), unmatched[:8])
print(json.dumps(report['recommended_picks'], indent=1))
for s in ('NBA', 'NHL', 'NFL', 'SOCCER'):
    print(s, {k: (v['W'], v['L'], v['P'], v['ungraded'], v['hit_rate'], v['expected_wins_from_percentages'], v['brier']) for k, v in report['all_markets'][s].items()})
