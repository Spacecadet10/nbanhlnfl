"""Prospective NFL adapter. Shared ledger/views; frozen NFL learners, never NBA/NHL dispatch.

An activation artifact binds the reviewed seal, completed one-shot test and serving definition. All
inputs stop at T-120; actual generation times and append-only canonical versions are retained.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, coverage, db, markets, nfl, nfl_eval, sports, threshold_overview as to
from .models.base import PregameGame
from .models.points import PointsConfig

MARKETS = sports.NFL.markets


def definition():
    paths = ('nfl_live.py', 'nfl_eval.py', 'coverage.py', 'markets.py', 'models/base.py', 'models/elo.py', 'models/points.py')
    return hashlib.sha256(b''.join((Path(__file__).parent / p).read_bytes() for p in paths)).hexdigest()[:16]


def activation_path():
    return config.data_dir() / 'research/nfl_live/activation.json'


def activation():
    try:
        a = json.loads(activation_path().read_text())
        s, r = a['sealed'], a['test_report']
        if (not nfl_eval._valid_seal(s) or r['choice_sha256'] != s['sha256']
                or r['winner']['frozen_threshold']['threshold'] != s['line_markets']['markets']['winner']['best_threshold']
                or a['definition'] != definition() or not a.get('activated_at')):
            return None
        return a
    except (OSError, ValueError, KeyError, TypeError):
        return None


def activate(conn, sealed, report, access_log):
    nfl._require_nfl(conn)
    nfl_eval._check_frozen(conn, sealed)  # open data only; never repeats the held-out test
    entries = [json.loads(x) for x in access_log.read_text().splitlines() if x.strip()]
    if (report.get('choice_sha256') != sealed['sha256']
            or report['winner']['frozen_threshold']['threshold'] != sealed['line_markets']['markets']['winner']['best_threshold']
            or not any(
            x.get('choice_sha256') == sealed['sha256'] and x.get('purpose') == nfl_eval._RUN_DONE for x in entries)):
        raise ValueError('NFL activation requires the completed, matching one-shot test.')
    old = activation()
    if old:
        return old
    a = {'activated_at': db.utc_now(), 'definition': definition(), 'sealed': sealed, 'test_report': report}
    path = activation_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(a, indent=1))
    temp.replace(path)
    return a


def _game(g):
    return PregameGame(g['game_id'], g['season'], g['season_type'], g['tipoff_at'],
                       g['home_team_id'], g['away_team_id'], bool(g['neutral_site']))


def record(conn, *, now=None):
    nfl._require_nfl(conn)
    a = activation()
    if not a:
        return {'status': 'inactive', 'reason': 'NFL live activation is missing or its definition changed.'}
    now = now or db.utc_now()
    if now < a['activated_at']:
        raise ValueError('NFL forecasts cannot predate activation.')
    from . import freshness
    health = freshness.summary(conn, 'nfl', datetime.fromisoformat(now.replace('Z', '+00:00')))
    if health['source_state'] != 'fresh' or health['failing']:
        return {'status': 'blocked', 'reason': 'NFL scoreboard is not fresh.'}
    games = conn.execute("SELECT * FROM games WHERE status='scheduled' AND season_type IN ('regular','postseason') "
                         "AND tipoff_at>? ORDER BY tipoff_at, game_id", (now,)).fetchall()
    due = [g for g in games if nfl_eval.cutoff_for(g['tipoff_at']) <= now and conn.execute(
        "SELECT COUNT(DISTINCT market) FROM game_forecasts WHERE game_id=? AND tipoff_at=? AND status='forecast' AND order_version=?",
        (g['game_id'], g['tipoff_at'], 'nfl-live-v1')).fetchone()[0] < len(MARKETS)]
    if not due:
        return {'status': 'not_due', 'recorded': 0}
    s = a['sealed']
    winner = nfl_eval._winner_model(s)
    cfg = s['line_markets']['score_model']
    points = nfl_eval.NflPoints(PointsConfig(eta=cfg['eta'], carry=cfg['carry']))
    # Forward-state initialization after the single completed test: access is logged, never rescored/reselected.
    rows = nfl_eval.load_rows(conn, through=max(g['season'] for g in due), sealed=s,
                             access_log=nfl_eval.access_log_path(), purpose='prospective NFL state initialization')
    rows = sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
    i, added = 0, 0
    for g in due:
        cutoff = nfl_eval.cutoff_for(g['tipoff_at'])
        # Refuse a missing recent result rather than silently forecasting from an incomplete scoreboard.
        missing = conn.execute("SELECT 1 FROM games WHERE season_type IN ('regular','postseason') "
                               "AND julianday(tipoff_at)<julianday(?, '-6 hours') AND julianday(tipoff_at)>julianday(?, '-7 days') "
                               "AND status IN ('scheduled','in_progress','other') LIMIT 1", (cutoff, cutoff)).fetchone()
        if missing:
            continue
        while i < len(rows) and rows[i].result_available_at <= cutoff:
            winner.update(rows[i].result)
            points.update(rows[i].result)
            i += 1
        pg = _game(g)
        p = winner.predict(pg)
        if s.get('calibration', {}).get('accepted'):
            p = nfl_eval.apply_frozen([{'p_home': p}], s['calibration']['final'])[0]['p_home']
        dist = points.distribution(pg)
        for market in MARKETS:
            q = conn.execute("SELECT * FROM market_quotes WHERE sport='nfl' AND game_id=? AND market=? "
                             "AND retrieved_at<=? AND julianday(retrieved_at)>=julianday(?, '-2 hours') ORDER BY retrieved_at DESC,quote_id DESC LIMIT 1",
                             (g['game_id'], market, cutoff, cutoff)).fetchone()
            line = None if market == 'winner' else q['line'] if q else markets.model_line(dist, market)
            probs = (p, 0., 1-p) if market == 'winner' else dist.market(market, line)
            row = dict(game_id=g['game_id'], sport='nfl', season_type=g['season_type'], market=market,
                       order_version='nfl-live-v1', status='forecast', source='primary_direct', fallback_rank=3,
                       fallback_reason=None, reason=None, model_version=f"nfl-{'elo' if market == 'winner' else 'points'}:{s['sha256'][:12]}:{a['definition']}",
                       source_table=None, source_id=None, source_generated_at=now,
                       line=line, line_source='none' if market == 'winner' else 'bookmaker' if q else 'model',
                       market_quote_id=q['quote_id'] if q else None, p_a=probs[0], p_mid=probs[1], p_b=probs[2],
                       confidence=coverage._confidence(market, probs), generated_at=now, tipoff_at=g['tipoff_at'])
            _, inserted = coverage._insert(conn, row)
            added += inserted
    return {'status': 'ok', 'recorded': added, 'games_due': len(due), 'as_of': now}


class Views:
    """One read snapshot and one canonical cohort per market, used by every NFL screen and Telegram."""
    def __init__(self, conn, now=None):
        nfl._require_nfl(conn)
        self.conn, self.now = conn, now or db.utc_now()
        self.active = activation()
        self.games = [dict(g) for g in conn.execute("SELECT * FROM games WHERE season_type IN ('regular','postseason') ORDER BY tipoff_at,game_id")]
        for g in self.games:
            if g['status'] == 'final' and (not g['result_available_at'] or g['result_available_at'] > self.now):
                g.update(status='in_progress', home_score=None, away_score=None)
        self.cohorts, self.indexes = {}, {}

    def cohort(self, market):
        if market not in self.cohorts:
            cs = coverage.canonical(self.conn, self.games, market, self.now,
                                    began=self.active['activated_at'] if self.active else None)
            self.cohorts[market] = [(g, cs[g['game_id']]) for g in self.games]
            self.indexes[market] = to.percentage_index(self.cohorts[market], self.now)
        return self.cohorts[market]

    def forecasts(self, game_id):
        out = []
        for m in MARKETS:
            cohort = self.cohort(m)
            pair = next(((g, c) for g, c in cohort if g['game_id'] == game_id), None)
            if not pair:
                continue
            g, c = pair
            c = {**c}
            if c['record']:
                rec = c['record']
                teams = self.conn.execute("SELECT team_id,abbr FROM teams WHERE team_id IN (?,?)",
                                          (g['home_team_id'], g['away_team_id'])).fetchall()
                abbr = {t['team_id']: t['abbr'] for t in teams}
                labels = markets.side_labels(m, rec['line'], abbr[g['home_team_id']], abbr[g['away_team_id']])
                c['record'] = {**rec, 'pick_label': labels[0 if rec['side'] == 'a' else 2],
                               'opposite_pick_label': labels[2 if rec['side'] == 'a' else 0]}
                c['percentage_history'] = to.percentage_record('nfl', m, rec, game_id, self.indexes[m], self.now)
                c['result_label'] = labels[('a', 'mid', 'b').index(c['result_side'])] if c['result_side'] else None
            out.append(c)
        return out

    def recommended(self, forecasts):
        w = next((c for c in forecasts if c['market'] == 'winner' and c['record']), None)
        if not self.active or not w:
            return None
        tested = self.active['test_report']['winner']
        return w['record']['pick_label'] if (tested['confirmed'] and tested['frozen_threshold']['holds'] and
                   w['record']['confidence'] >= tested['frozen_threshold']['threshold']) else None

    def attach(self, game):
        if self.active:
            game['forecasts'] = self.forecasts(game['game_id'])
            game['recommended_pick'] = self.recommended(game['forecasts'])
        return game

    def report(self):
        began = self.active['activated_at'] if self.active else coverage.recording_began(self.conn, 'nfl')
        blocks = []
        for m in MARKETS:
            cohort = [(g,c) for g,c in self.cohort(m) if began and g['tipoff_at'] >= began and g['tipoff_at'] <= self.now]
            rows = []
            for g,c in cohort:
                if c['record']:
                    record = next(x for x in self.forecasts(g['game_id']) if x['market'] == m)
                    rows.append({'game_id': g['game_id'], 'tipoff': g['tipoff_at'], 'forecast': record})
            blocks.append({'market': m, 'title': markets.spec('nfl',m).title,
                           'summary': coverage.canonical_summary(cohort, m), 'rows': rows})
        return {'sport': 'nfl', 'as_of': self.now, 'active': bool(self.active), 'recording_began_at': began,
                'markets': blocks, 'historical_test': self.active['test_report']['winner']['frozen_threshold'] if self.active else None}

    def ranked_picks(self, day):
        lo, hi = nfl._day_bounds(day)
        slate = {"games": [nfl._game(g) for g in self.conn.execute(f"{nfl.GAME_SQL} WHERE g.tipoff_at>=? AND g.tipoff_at<?", (lo, hi))]}
        picks = []
        for g in slate['games']:
            forecasts = self.forecasts(g['game_id'])
            recommended = self.recommended(forecasts)
            for c in forecasts:
                if not c['record']:
                    continue
                r = c['record']
                strong = c['market'] == 'winner' and recommended is not None
                picks.append(dict(game_id=g['game_id'], matchup=f"{g['away']['abbr']} @ {g['home']['abbr']}",
                    tipoff=g['kickoff'], status=g['status'], market=c['market'], market_title=markets.spec('nfl',c['market']).title,
                    forecast_id=r['forecast_id'], pick_label=r['pick_label'], opposite_pick_label=r['opposite_pick_label'],
                    probability=r['confidence'], percentage_history=c['percentage_history'], generated_at=r['generated_at'],
                    grade=c['grade'], strong=strong, skill_proven=c['market']=='winner', game_rank=1 if strong else None,
                    day_rank=None, game_ranked=int(recommended is not None), evidence=None, threshold=.75 if strong else None,market_prob=None))
        return {'mode':'real','sport':'nfl','date':str(day),'as_of':self.now,'picks':picks,
                'rule':'Frozen NFL winner threshold; line markets informational.','min_settled':200,'without_forecast':{}}
