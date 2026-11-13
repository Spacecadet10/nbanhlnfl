"""Step F paper recorder (app/best_pick_record.py) and its report (scripts/best_picks.py), against the fixed rule in
docs/preregistration/best-pick-2026-11-11-v4.md. Synthetic fixtures only: no store, ledger or game result is read."""
import copy
import hashlib
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import best_pick_record as bpr, telegram as tg
from test_telegram import Bot, priced_card

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)
AS_OF = '2026-11-20T23:00:00Z'
TIP = '2026-11-21T00:30:00Z'          # Toronto: Nov 20, 19:30
GEN = '2026-11-20T22:45:00Z'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from app import lineup, telegram_card
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)
    monkeypatch.setattr(lineup, 'facts', lambda conn, sport, ids, at: {g: {} for g in ids})
    monkeypatch.setattr(lineup, 'review_flags', lambda sport, pick, fact: [])


def store(tip=TIP, gid='g'):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
      CREATE TABLE games (game_id TEXT, season INT, season_type TEXT, status TEXT, tipoff_at TEXT, home_team_id TEXT,
                          away_team_id TEXT, home_score INT, away_score INT, result_first_seen_at TEXT, result_available_at TEXT);
      CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, status TEXT, line REAL,
        line_source TEXT, market_quote_id INT, p_a REAL, p_mid REAL, p_b REAL, confidence REAL, generated_at TEXT, tipoff_at TEXT);
      CREATE TABLE odds_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, provider TEXT, market TEXT DEFAULT 'moneyline',
        price_label TEXT, home_american INT, away_american INT, home_prob_devig REAL, retrieved_at TEXT);
      CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, provider TEXT, market TEXT, price_label TEXT,
        line REAL, price_a INT, price_mid INT, price_b INT, p_a_devig REAL, p_mid_devig REAL, p_b_devig REAL, retrieved_at TEXT);''')
    c.execute("INSERT INTO games (game_id, season, season_type, status, tipoff_at, home_team_id, away_team_id) "
              "VALUES (?,2027,'regular','scheduled',?,'h','a')", (gid, tip))
    return c


def game(c, gid, tip, home, away, status='final'):
    c.execute("INSERT INTO games (game_id, season, season_type, status, tipoff_at, home_team_id, away_team_id) "
              "VALUES (?,2027,'regular',?,?,?,?)", (gid, status, tip, home, away))


def odds(c, provider, label, p_home, at, prices=(-150, 130)):
    c.execute('INSERT INTO odds_quotes (game_id, provider, price_label, home_american, away_american, home_prob_devig, '
              'retrieved_at) VALUES (?,?,?,?,?,?,?)', ('g', provider, label, prices[0], prices[1], p_home, at))
    return c.execute('SELECT max(quote_id) FROM odds_quotes').fetchone()[0]


def mquote(c, market, prices, devig, line=None, at='2026-11-20T22:00:00Z', provider='DraftKings'):
    a, mid, b = prices
    pa, pm, pb = devig
    c.execute('INSERT INTO market_quotes (game_id, provider, market, price_label, line, price_a, price_mid, price_b, '
              'p_a_devig, p_mid_devig, p_b_devig, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
              ('g', provider, market, 'current', line, a, mid, b, pa, pm, pb, at))
    return c.execute('SELECT max(quote_id) FROM market_quotes').fetchone()[0]


def fc(c, fid, market, quote=None, line=None):
    c.execute("INSERT INTO game_forecasts (forecast_id, game_id, market, status, line, line_source, market_quote_id, p_a, "
              "p_mid, p_b, generated_at, tipoff_at) VALUES (?,?,?,'forecast',?,?,?,.5,0,.5,?,?)",
              (fid, 'g', market, line, 'bookmaker' if quote else 'none', quote, GEN, TIP))


def pick(fid, market, side, app, book, *, rank=None, wins=0, losses=0, label=None):
    return dict(game_id='g', forecast_id=fid, market=market, pick_label=label or f'{market} {side}', matchup='BOS @ TOR',
                tipoff=TIP, generated_at=GEN, status='scheduled', grade='pending', probability=app, market_prob=book,
                percentage_history=dict(side=side, wins=wins, losses=losses, settled=wins + losses, rank=rank,
                                        percentage=None, source='s', model_version='m', line_source='none'))


def report(*picks, sport='nba'):
    return {'sport': sport, 'as_of': AS_OF, 'picks': list(picks)}


def picture(c, rep, sport='nba'):
    return bpr.card_pictures(c, sport, rep, NOW)['g']


def by_id(pic):
    return {p['forecast_id']: p for p in pic['picks']}


# ---- section 6: power de-vig ---------------------------------------------------------------------------------------------
def test_power_devig_solves_the_exponent_and_has_no_root_when_the_sum_never_crosses_one():
    from app import odds
    r = [odds.american_to_implied(-150), odds.american_to_implied(130)]
    q = bpr.power_devig(r)
    assert abs(sum(q) - 1) <= 1e-12 and q[0] > r[0] / sum(r)       # favours the favourite more than proportional
    k = __import__('math').log(q[0]) / __import__('math').log(r[0])
    assert q[1] == pytest.approx(r[1] ** k, abs=1e-12) and 0.5 <= k <= 5
    three = [odds.american_to_implied(p) for p in (150, 230, 180)]
    assert abs(sum(bpr.power_devig(three)) - 1) <= 1e-12
    assert bpr.power_devig([0.99, 0.99]) is None                    # sum r^5 still > 1: no sign change on [0.5, 5]
    assert bpr.power_devig([0.1, 0.1]) is None                      # sum r^0.5 < 1


# ---- section 3: played the previous day (America/Toronto dates) ---------------------------------------------------------
def test_previous_day_is_the_toronto_calendar_date_before_and_ignores_postponed_games():
    c = store()
    game(c, 'p1', '2026-11-20T00:30:00Z', 'h', 'x')                  # Toronto Nov 19 19:30: previous day
    game(c, 'p2', '2026-11-20T04:30:00Z', 'y', 'a')                  # Toronto Nov 19 23:30: previous day (UTC Nov 20)
    game(c, 'p3', '2026-11-20T05:30:00Z', 'z', 'w')                  # Toronto Nov 20 00:30: same day, not previous
    game(c, 'p4', '2026-11-20T00:30:00Z', 'v', 'u', status='postponed')
    game(c, 'p5', '2026-11-19T04:59:00Z', 't', 's')                  # Toronto Nov 18 23:59
    assert bpr.played_previous_day(c, 'h', TIP) and bpr.played_previous_day(c, 'a', TIP)
    assert not any(bpr.played_previous_day(c, t, TIP) for t in ('z', 'v', 't', 'nobody'))


# ---- sections 2-5 on an NBA card -----------------------------------------------------------------------------------------
def nba_card(c, *, open_p=.70, now_p=.74, extra=()):
    odds(c, 'FanDuel', 'current', .55, '2026-11-20T10:00:00Z')      # earliest overall, other provider: never the opening
    odds(c, 'DraftKings', 'current', open_p - .05, '2026-11-20T12:00:00Z')
    odds(c, 'DraftKings', 'open', open_p, '2026-11-20T12:00:00Z')   # same retrieval: 'open' first
    now_id = odds(c, 'DraftKings', 'current', now_p, '2026-11-20T22:30:00Z', prices=(-300, 250))
    odds(c, 'DraftKings', 'current', .90, '2026-11-20T22:50:00Z')   # after generated_at: never used
    q = mquote(c, 'spread', (-110, None, -110), (.5, None, .5), line=-5.5)
    fc(c, 1, 'winner')
    fc(c, 2, 'spread', quote=q, line=-5.5)
    return now_id, report(pick(1, 'winner', 'a', .70, now_p), pick(2, 'spread', 'b', .55, .5), *extra)


def test_nba_winner_scores_h1_to_h4_from_send_time_information_and_is_named():
    c = store()
    now_id, rep = nba_card(c)
    pic = picture(c, rep)
    w = by_id(pic)[1]
    assert w['h'] == {'H1': 1, 'H2': 1, 'H3': 1, 'H4': 1} and w['score'] == 4 and w['candidate']
    assert w['h4']['now_quote_id'] == now_id and w['h4']['provider'] == 'DraftKings'
    assert (w['h4']['open_pct'], w['h4']['now_pct'], w['h4']['movement']) == ('70.0', '74.0', '4.0')
    assert w['quote']['table'] == 'odds_quotes' and w['quote']['quote_id'] == now_id and w['quote']['prices'] == [-300, 250]
    assert w['quote']['matches_market_prob'] and w['quote']['power'] > w['quote']['proportional_from_prices']
    assert (w['app_pct'], w['book_pct'], w['app_raw'], w['book_raw']) == ('70.0', '74.0', .70, .74)
    assert by_id(pic)[2]['h'] == {} and by_id(pic)[2]['score'] == 0          # NBA spread: no hypothesis
    assert pic['named_forecast_id'] == 1 and pic['baseline_forecast_id'] == 1
    assert pic['played_previous_day'] == {'home': False, 'away': False}


@pytest.mark.parametrize('open_p,now_p,item', [(.70, .73, 1), (.73, .70, -1), (.70, .729, 0), (.74, .74, 0)])
def test_h4_movement_boundaries_use_displayed_percentages(open_p, now_p, item):
    c = store()
    _, rep = nba_card(c, open_p=open_p, now_p=now_p)
    assert by_id(picture(c, rep))[1]['h']['H4'] == item


def test_h4_opening_equal_to_now_scores_zero():
    c = store()
    only = odds(c, 'DraftKings', 'current', .74, '2026-11-20T22:30:00Z')
    fc(c, 1, 'winner')
    w = by_id(picture(c, report(pick(1, 'winner', 'a', .70, .74))))[1]
    assert w['h4']['open_quote_id'] == only and w['h4']['note'] == 'opening = now' and w['h']['H4'] == 0


def test_h1_h2_h3_signs_and_the_floor():
    c = store()
    fc(c, 1, 'winner')
    q = mquote(c, 'spread', (-110, None, -110), (.5, None, .5), line=-5.5)
    fc(c, 2, 'spread', quote=q, line=-5.5)
    pic = picture(c, report(pick(1, 'winner', 'a', .64, .58), pick(2, 'spread', 'a', .55, .5)))
    assert by_id(pic)[1]['h'] == {'H1': -1, 'H2': -1, 'H3': -1, 'H4': 0}   # 58.0 <= 58.0; 64.0 - 58.0 >= 5.6; same side
    pic = picture(c, report(pick(1, 'winner', 'a', .656, .60)))
    assert by_id(pic)[1]['h'] == {'H1': 0, 'H2': -1, 'H3': 0, 'H4': 0}    # exactly 5.6 above
    pic = picture(c, report(pick(1, 'winner', 'a', .70, .719)))
    assert by_id(pic)[1]['score'] == 1 and pic['named_forecast_id'] is None and pic['baseline_forecast_id'] == 1


def test_lineup_flag_avoid_and_missing_percentages_exclude_but_rank_none_does_not(monkeypatch):
    from app import lineup
    c = store()
    _, rep = nba_card(c)
    monkeypatch.setattr(lineup, 'review_flags', lambda sport, p, fact: ['TOR top player out'] if p['market'] == 'winner' else [])
    pic = picture(c, rep)
    assert by_id(pic)[1]['excluded'] == ['lineup flag'] and by_id(pic)[1]['score'] == 4
    assert pic['named_forecast_id'] is None and pic['baseline_forecast_id'] == 2 and pic['excluded_lineup'] == 1
    monkeypatch.setattr(lineup, 'review_flags', lambda *a: [])
    rep['picks'][0]['percentage_history'].update(rank=3, wins=2, losses=3)
    pic = picture(c, rep)
    assert by_id(pic)[1]['excluded'] == ['AVOID'] and pic['named_forecast_id'] is None and pic['excluded_avoid'] == 1
    rep['picks'][0]['percentage_history'].update(rank=None)               # not ranked: no AVOID on the card
    assert picture(c, rep)['named_forecast_id'] == 1
    rep['picks'][0]['market_prob'] = None
    assert by_id(picture(c, rep))[1]['excluded'] == ['no app % or no book %']
    tt = pick(9, 'team_total_home', 'a', .9, .9)
    assert by_id(picture(c, report(tt)))[9]['excluded'] == ['market']


def test_missing_or_failing_lineup_facts_are_a_flag(monkeypatch):
    from app import lineup
    c = store()
    _, rep = nba_card(c)
    monkeypatch.setattr(lineup, 'facts', lambda *a: 1 / 0)
    pic = picture(c, rep)
    assert all('lineup flag' in p['excluded'] for p in pic['picks']) and pic['named_forecast_id'] is None
    assert pic['baseline_forecast_id'] is None                                   # no candidate: no baseline
    monkeypatch.setattr(lineup, 'facts', lambda *a: {})
    monkeypatch.setattr(lineup, 'review_flags', lambda sport, p, fact: 1 / 0)
    assert picture(c, rep)['named_forecast_id'] is None


def test_named_and_baseline_tie_breaks():
    def cand(fid, market, score, book, app):
        return dict(forecast_id=fid, market=market, score=score, book_pct=book, app_pct=app)
    a, b = cand(5, 'winner', 2, '75.0', '70.0'), cand(4, 'spread', 2, '75.0', '70.0')
    assert bpr.named_pick([a, b]) is a                                        # market order before forecast id
    assert bpr.named_pick([a, cand(6, 'winner', 3, '60.0', '50.0')])['forecast_id'] == 6
    assert bpr.named_pick([a, cand(3, 'winner', 2, '75.1', '60.0')])['forecast_id'] == 3
    assert bpr.named_pick([a, cand(3, 'winner', 2, '75.0', '70.1')])['forecast_id'] == 3
    assert bpr.named_pick([a, cand(3, 'winner', 2, '75.0', '70.0')])['forecast_id'] == 3
    assert bpr.named_pick([cand(1, 'winner', 1, '99.0', '99.0')]) is None
    low = cand(7, 'total', -1, '80.0', '50.0')
    assert bpr.baseline_pick([a, low]) is low and bpr.baseline_pick([]) is None
    assert bpr.baseline_pick([a, cand(8, 'winner', 0, '75.0', '70.1')])['forecast_id'] == 8


# ---- NHL items -----------------------------------------------------------------------------------------------------------
def test_nhl_puck_line_total_and_reg3_items():
    c = store()
    for fid, m in ((1, 'winner'), (2, 'spread'), (3, 'total'), (4, 'reg3')):
        fc(c, fid, m)
    w = pick(1, 'winner', 'a', .61, .58)
    sp, tot = pick(2, 'spread', 'a', .68, .40), pick(3, 'total', 'b', .55, .52)
    reg = pick(4, 'reg3', 'a', .45, .44)
    pic = picture(c, report(w, sp, tot, reg, sport='nhl'), 'nhl')
    p = by_id(pic)
    assert p[1]['h'] == {} and p[1]['score'] == 0                               # NHL winner: no hypothesis
    assert p[2]['h'] == {'H5a': 1, 'H5b': 1, 'H5c': 0} and pic['named_forecast_id'] == 2
    assert p[3]['h'] == {'H6': 0} and p[4]['h'] == {'H7': 1}
    game(c, 'y', '2026-11-20T00:30:00Z', 'h', 'x')                             # home (the puck-line team) played yesterday
    p = by_id(picture(c, report(w, sp, tot, reg, sport='nhl'), 'nhl'))
    assert p[2]['h']['H5c'] == -1 and p[2]['score'] == 1 and p[3]['h'] == {'H6': -1}
    p = by_id(picture(c, report(dict(w, probability=.5994), dict(sp, probability=.669), reg, sport='nhl'), 'nhl'))
    assert p[4]['h'] == {'H7': -1} and p[2]['h']['H5b'] == 0                    # 60.0 / 67.0 not reached (displayed 59.9 / 66.9)
    away = copy.deepcopy(sp)
    away['percentage_history']['side'] = 'b'
    draw = copy.deepcopy(reg)
    draw['percentage_history']['side'] = 'mid'
    p = by_id(picture(c, report(w, away, draw, sport='nhl'), 'nhl'))
    assert p[2]['h']['H5a'] == -1 and p[4]['h'] == {'H7': -1}
    p = by_id(picture(c, report(sp, reg, sport='nhl'), 'nhl'))
    assert p[2]['h']['H5a'] == 0 and p[4]['h'] == {'H7': -1}                    # no winner pick


def test_three_way_quote_power_and_proportional_for_the_picked_outcome():
    c = store()
    q = mquote(c, 'reg3', (150, 230, 180), (.38, .26, .36))
    fc(c, 4, 'reg3', quote=q)
    p = by_id(picture(c, report(pick(4, 'reg3', 'mid', .3, .26), sport='nhl'), 'nhl'))[4]
    from app import odds
    r = [odds.american_to_implied(x) for x in (150, 230, 180)]
    assert p['quote']['picked_index'] == 1 and p['quote']['proportional_from_prices'] == pytest.approx(r[1] / sum(r))
    assert p['quote']['power'] == pytest.approx(bpr.power_devig(r)[1]) and p['quote']['matches_market_prob']


# ---- NFL record only; the ledger row --------------------------------------------------------------------------------------
def test_nfl_card_is_recorded_with_its_picks_and_names_nothing(tmp_path):
    rep = report(pick(1, 'winner', 'a', .8, .85), sport='nfl')
    pic = bpr.card_pictures(None, 'nfl', rep, NOW)['g']
    assert pic['rule_applies'] is False and pic['named_forecast_id'] is None and pic['baseline_forecast_id'] is None
    assert pic['picks'][0]['book_pct'] == '85.0' and pic['picks'][0]['record']['wins'] == 0
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        assert bpr.record(conn, 'k', 'nfl', 'g', pic, AS_OF)
        row = conn.execute('SELECT * FROM best_picks').fetchone()
    assert row['rule_applies'] == 0 and row['named_forecast_id'] is None and row['status'] == 'ok'


def test_row_is_append_only_carries_the_prereg_hash_and_errors_are_visible(tmp_path, monkeypatch):
    c = store()
    _, rep = nba_card(c)
    pic = picture(c, rep)
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        assert bpr.record(conn, 'k1', 'nba', 'g', pic, AS_OF, '2026-11-20T23:00:05Z')
        assert bpr.record(conn, 'k1', 'nba', 'g', dict(pic, named_forecast_id=None), AS_OF)    # first row stays
        r = conn.execute('SELECT * FROM best_picks').fetchone()
        assert conn.execute('SELECT count(*) FROM best_picks').fetchone()[0] == 1
        assert r['prereg_sha256'] == bpr.PREREG_SHA256 == hashlib.sha256(
            (ROOT / 'docs/preregistration/best-pick-2026-11-11-v4.md').read_bytes()).hexdigest()
        assert r['recorder_sha256'] == hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
        assert (r['status'], r['named_forecast_id'], r['named_score'], r['baseline_forecast_id'], r['baseline_book_pct']) == \
            ('ok', 1, 4, 1, '74.0')
        assert r['recorded_at'] == AS_OF and r['candidates_n'] == 2 and json.loads(r['picture'])['picks'][0]['h']['H4'] == 1
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            conn.execute("UPDATE best_picks SET named_forecast_id=2")
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            conn.execute('DELETE FROM best_picks')
        assert bpr.record(conn, 'k2', 'nba', 'g', {'error': 'ValueError: boom', 'game_id': 'g'}, AS_OF)
        assert bpr.record(conn, 'k3', 'nba', 'g', None, AS_OF)
        monkeypatch.setattr(bpr, 'PREREG_PATH', tmp_path / 'missing.md')
        assert bpr.record(conn, 'k4', 'nba', 'g', pic, AS_OF)
        rows = {x['delivery_key']: x for x in conn.execute('SELECT * FROM best_picks')}
    assert rows['k2']['status'] == 'error' and 'boom' in rows['k2']['error']
    assert rows['k3']['error'] == 'no picture was built for this card'
    assert rows['k4']['status'] == 'error' and rows['k4']['prereg_sha256'] is None
    closed = sqlite3.connect(':memory:')
    closed.close()
    assert bpr.record(closed, 'k', 'nba', 'g', pic, AS_OF) is False                          # never raises


def test_a_picture_problem_is_an_error_entry_not_an_exception():
    c = store()
    rep = report(pick(1, 'winner', None, .7, .74))
    fc(c, 1, 'winner')
    assert 'error' in bpr.card_pictures(c, 'nba', rep, NOW)['g']
    assert bpr.card_pictures(c, 'nba', {'picks': [{}]}, NOW) == {}
    assert bpr.card_pictures(None, 'nba', report(pick(1, 'winner', 'a', .7, .74)), NOW)['g'].keys() >= {'error'}


# ---- the delivery job: card unchanged, one row per sent card ----------------------------------------------------------------
class PhotoBot(Bot):
    def call(self, method, body, **kwargs):
        self.photos = getattr(self, 'photos', []) + [kwargs.get('photo')]
        return super().call(method, body, **kwargs)


def job_card():
    d = priced_card()
    sides = {'winner': 'b', 'spread': 'a', 'total': 'a'}
    for p in d['picks']:
        p['percentage_history'].update(side=sides[p['market']], rank=None)
    return d


def job_store():
    return store(tip='2026-11-02T21:00:00Z', gid='g1')


def run_job(tmp_path, monkeypatch, name, *, broken=False):
    from app import db, freshness
    from app.providers import real
    conn_store = job_store()

    class Source:
        def __init__(self, sport):
            self.sport = sport

        def execute(self, sql, *a):
            return None if sql == 'BEGIN' else conn_store.execute(sql, *a)

        def close(self):
            pass

    class Provider:
        def __init__(self, source, stamp):
            self.sport = source.sport

        def ranked_picks(self, day):
            return job_card() if self.sport == 'nba' else {'sport': self.sport, 'as_of': '2026-11-02T20:00:00Z', 'picks': []}
    monkeypatch.setattr(db, 'connect_readonly', Source)
    monkeypatch.setattr(db, 'store_sport', lambda s: s.sport)
    monkeypatch.setattr(freshness, 'summary', lambda *_: {'source_state': 'fresh', 'failing': False})
    monkeypatch.setattr(real, 'RealProvider', Provider)
    monkeypatch.setattr(tg, 'controls', lambda *a: True)
    monkeypatch.setattr(tg, 'card_lineups', lambda *a: {})
    monkeypatch.setattr(tg, 'card_implied_lines', lambda *a: None)
    if broken:
        monkeypatch.setattr(bpr, 'card_pictures', lambda *a: 1 / 0)
        monkeypatch.setattr(bpr, 'record', lambda *a, **k: 1 / 0)
    bot, now = PhotoBot(), datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    with tg.ledger(tmp_path / name) as conn:
        first = tg.run_once(bot, {'TELEGRAM_CHAT_ID': '123'}, conn, now=now)
        second = tg.run_once(bot, {'TELEGRAM_CHAT_ID': '123'}, conn, now=now)
        sent = [tuple(r) for r in conn.execute('SELECT delivery_key, text, status FROM deliveries')]
        rows = [dict(r) for r in conn.execute('SELECT * FROM best_picks')]
    return first, second, sent, rows, bot


def test_context_card_is_independent_of_frozen_paper_recorder(tmp_path, monkeypatch):
    first, second, sent, rows, bot = run_job(tmp_path, monkeypatch, 'with.sqlite')
    assert first['sports']['nba'] == {'eligible_summaries': 1, 'sent': 1} and first['status'] == 'ok'
    assert 'sent' not in second['sports']['nba'] and len(bot.sends) == 1 and len(rows) == 1
    r = rows[0]
    assert r['delivery_key'] == sent[0][0] and r['status'] == 'ok' and r['recorded_at'] == '2026-11-02T20:00:00Z'
    assert r['game_id'] == 'g1' and r['named_forecast_id'] == 1 and r['named_score'] == 3       # CLE win: H1 H2 H3, no H4 quote
    assert '<b>Recommended pick</b>\n<b>CLE win</b>' in sent[0][1]
    assert 'Context comparison' in sent[0][1] and tg.STRONGEST_PREFIX not in sent[0][1]
    monkeypatch.undo()
    from app import lineup, telegram_card
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)
    monkeypatch.setattr(lineup, 'facts', lambda conn, sport, ids, at: {g: {} for g in ids})
    monkeypatch.setattr(lineup, 'review_flags', lambda sport, pick, fact: [])
    first_b, _, sent_b, rows_b, bot_b = run_job(tmp_path, monkeypatch, 'broken.sqlite', broken=True)
    assert first_b == first and sent_b[0][0] == sent[0][0] and rows_b == []
    assert tg.card_identity(sent_b[0][1]) == tg.card_identity(sent[0][1])
    assert tg.STRONGEST_PREFIX not in sent_b[0][1]
    from io import BytesIO
    from PIL import Image
    assert sent_b[0][1] == sent[0][1]
    assert bot.photos[0] == bot_b.photos[0]


# ---- the report (scripts/best_picks.py) ---------------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location('best_picks_report', ROOT / 'scripts' / 'best_picks.py')


def rep_mod():
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_poisson_binomial_and_permutation_follow_v4():
    m = rep_mod()
    assert m.poisson_binomial_sf(2, [.5, .5]) == pytest.approx(.25) and m.poisson_binomial_sf(0, [.3]) == 1
    assert m.poisson_binomial_sf(1, [.5, .5]) == pytest.approx(.75)
    d, p = m.permutation_p([1, 1, 1], [1, 1], permutations=50)
    assert d == 0 and p == 1                                                     # every reassignment ties: counted
    import numpy as np
    named, base = [1, 1, 0, 1, 1], [0, 1, 0, 0, 1, 0]
    rng, pooled, hits = np.random.default_rng(20260930), np.array(named + base), 0
    for _ in range(2000):
        x = rng.permutation(pooled)
        hits += x[:5].mean() - x[5:].mean() >= 0.8 - 2 / 6 - 1e-12
    assert m.permutation_p(named, base, permutations=2000)[1] == (1 + hits) / 2001


def ledger_with(tmp_path, cards, *, extra_sent=()):
    """cards: [(key, gid, recorded_at, tipoff, named_fid, base_fid, q, qp)]; rows written by the recorder itself."""
    path = tmp_path / 'ledger.sqlite'
    with tg.ledger(path) as conn:
        for key, gid, at, tip, nf, bf, q, qp in cards:
            picks = [dict(forecast_id=f, market='winner', pick_label=f'P{f}', candidate=True, score=2 if f == nf else 0,
                          book_pct='70.0', book_raw=q, app_pct='60.0', quote={'power': qp}) for f in {nf, bf} - {None}]
            pic = dict(sport='nba', game_id=gid, matchup=f'A @ {gid}', tipoff_at=tip, picks=picks, named_forecast_id=nf,
                       baseline_forecast_id=bf, excluded_lineup=0, excluded_avoid=0,
                       played_previous_day={'home': False, 'away': False})
            bpr.record(conn, key, 'nba', gid, pic, at)
        for key, gid, at, tip, *_ in list(cards) + list(extra_sent):
            conn.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,NULL,1,NULL)",
                         (key, '1', f'<b>PROP51 PICKS · NBA</b>\n<b>A @ {gid}</b>\nStarts {tip}\n<b>Recommended pick</b>\nX win',
                          'sent', at))
    return path


def result_store(results):
    """results: {fid: (gid, tipoff, home_won or None for pending, seen_at)}."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE games (game_id TEXT, status TEXT, tipoff_at TEXT, home_score INT, away_score INT,
                         result_first_seen_at TEXT, result_available_at TEXT);
                       CREATE TABLE game_forecasts (forecast_id INTEGER, game_id TEXT, market TEXT, line REAL, p_a REAL,
                         p_mid REAL, p_b REAL, tipoff_at TEXT);''')
    for fid, (gid, tip, won, seen) in results.items():
        if not c.execute('SELECT 1 FROM games WHERE game_id=?', (gid,)).fetchone():
            c.execute('INSERT INTO games VALUES (?,?,?,?,?,?,NULL)', (gid, 'scheduled' if won is None else 'final', tip,
                      None if won is None else (100 if won else 90), None if won is None else 95, seen))
        c.execute("INSERT INTO game_forecasts VALUES (?,?,'winner',NULL,.6,0,.4,?)", (fid, gid, tip))
    return c


REVIEWED = '2026-11-15T00:00:00Z'


def cards_for(n, *, start=datetime(2026, 11, 16, tzinfo=timezone.utc), shared_every=3):
    """n cards, each naming forecast 2i and with baseline 2i+1 (or the named pick itself every `shared_every` cards)."""
    out, res = [], {}
    for i in range(n):
        at = start + timedelta(hours=3 * i)
        tip = at + timedelta(hours=1)
        s, t = at.strftime('%Y-%m-%dT%H:%M:%SZ'), tip.strftime('%Y-%m-%dT%H:%M:%SZ')
        nf = 2 * i
        bf = nf if i % shared_every == 0 else nf + 1
        out.append((f'k{i}', f'g{i}', s, t, nf, bf, .6, .62))
        seen = (tip + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
        res[nf] = (f'g{i}', t, i % 4 != 0, seen)      # named forecasts pick home (p_a .6): home wins 3 of 4
        if bf != nf:
            res[bf] = (f'g{i}', t, i % 4 != 0, seen)
    return out, res


def test_report_shows_counts_only_until_100_named_picks_are_decided(tmp_path, capsys):
    m = rep_mod()
    cards, res = cards_for(99)
    path = ledger_with(tmp_path, cards)
    sha = hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
    with sqlite3.connect(path) as led:
        led.row_factory = sqlite3.Row
        info = m.counted_cards(led, 'nba', REVIEWED, {sha})
    m.poisson_binomial_sf = m.permutation_p = m.wilson = lambda *a, **k: pytest.fail('no test before completion')
    out = m.evaluate(result_store(res), info['cards'], datetime(2026, 12, 31, tzinfo=timezone.utc))
    assert out['state'] == 'counting' and out['named'] == 99 and out['named_decided'] == 99
    assert set(out) == {'cards_counted', 'named', 'named_decided', 'baseline_decided', 'excluded_lineup', 'excluded_avoid', 'state'}


def test_report_runs_tests_a_and_b_once_the_set_is_complete(tmp_path):
    m = rep_mod()
    cards, res = cards_for(103)
    path = ledger_with(tmp_path, cards)
    sha = hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
    with sqlite3.connect(path) as led:
        led.row_factory = sqlite3.Row
        info = m.counted_cards(led, 'nba', REVIEWED, {sha})
    out = m.evaluate(result_store(res), info['cards'], datetime(2026, 12, 31, tzinfo=timezone.utc))
    assert out['n'] == 100 and out['W'] == 75 and out['cards_through_cut'] == 100
    assert out['test_a']['p_proportional'] == pytest.approx(m.poisson_binomial_sf(75, [.6] * 100))
    assert out['test_a']['p_power'] == pytest.approx(m.poisson_binomial_sf(75, [.62] * 100))
    assert out['test_b']['shared'] == 34 and out['test_b']['named_only'] == 66 and out['test_b']['baseline_only'] == 66
    assert out['test_b']['D'] == 0 and out['test_b']['p'] > .05 and not out['test_b']['pass']   # same games, same results
    assert out['full_hit_rate']['named_at_least_baseline'] and out['state'] == 'DROP'
    assert out['excess_reported_only']['proportional']['named'] == pytest.approx(.75 - .6)


def test_report_counts_only_the_earliest_reviewed_v4_card_of_each_game(tmp_path):
    m = rep_mod()
    cards, _ = cards_for(6)
    cards[1] = ('k1', 'g1', '2026-11-14T00:00:00Z', '2026-11-14T01:00:00Z', 2, 3, .6, .6)  # before the review
    cards[2] = ('k2', 'g2', '2026-11-11T21:00:00Z', '2026-11-11T23:00:00Z', 4, 5, .6, .6)  # before T0
    later = ('k0b', 'g0', '2026-11-16T00:30:00Z', cards[0][3], 0, 0, .6, .6)                   # second card of game g0
    path = ledger_with(tmp_path, cards + [later],
                       extra_sent=[('k9', 'g9', '2026-11-17T00:00:00Z', '2026-11-17T01:00:00Z')])   # sent, no row
    sha = hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
    with sqlite3.connect(path) as led:
        led.row_factory = sqlite3.Row
        led.execute("INSERT INTO deliveries VALUES ('k3x','1',?, 'sent', '2026-11-16T08:00:00Z', NULL, 1, NULL)",
                    (f'<b>PROP51 PICKS · NBA</b>\n<b>A @ g3</b>\nStarts {cards[3][3]}\n<b>Recommended pick</b>\nX win',))
        info = m.counted_cards(led, 'nba', REVIEWED, {sha})
        assert info['sent_without_row'] == 2 and info['sent_without_row_after_review'] == 2
        assert [c['game_id'] for c in info['cards']] == ['g0', 'g4', 'g5']
        assert info['cards'][0]['delivery_key'] == 'k0'
        assert info['excluded'] == {'recorded before the independent review': 1, 'earliest card has no row': 2,
                                    'earliest card sent before T0': 1}
        assert info['rows_without_sent_card'] == 0 and info['sent'] == 8          # since T0: k0 k0b k1 k3 k3x k4 k5 k9
        assert m.counted_cards(led, 'nba', None, {sha})['cards'] == []
        assert m.counted_cards(led, 'nba', REVIEWED, {'other'})['cards'] == []


def test_grading_excludes_a_result_first_seen_more_than_7_days_after_the_start_and_pushes():
    m = rep_mod()
    c = result_store({1: ('g', '2026-11-16T00:00:00Z', True, '2026-11-23T01:00:00Z'),
                      2: ('h', '2026-11-16T00:00:00Z', True, '2026-11-16T03:00:00Z'),
                      3: ('p', '2026-11-16T00:00:00Z', None, None)})
    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    assert m.grade(c, 1, now)[0] == 'past_limit' and m.grade(c, 2, now)[0] == 'win'
    assert m.grade(c, 3, now)[0] == 'past_limit' and m.grade(c, 3, datetime(2026, 11, 20, tzinfo=timezone.utc))[0] == 'pending'
    c.execute("INSERT INTO games VALUES ('s','final','2026-11-16T00:00:00Z',100,95,'2026-11-16T03:00:00Z',NULL)")
    c.execute("INSERT INTO game_forecasts VALUES (4,'s','spread',-5,.6,0,.4,'2026-11-16T00:00:00Z')")
    assert m.grade(c, 4, now)[0] == 'push'


def test_a_pending_earlier_named_pick_holds_the_evaluation(tmp_path):
    m = rep_mod()
    cards, res = cards_for(101)
    res[0] = ('g0', res[0][1], None, None)                                       # first named pick not final yet
    path = ledger_with(tmp_path, cards)
    sha = hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
    with sqlite3.connect(path) as led:
        led.row_factory = sqlite3.Row
        info = m.counted_cards(led, 'nba', REVIEWED, {sha})
    store_ = result_store(res)
    assert m.evaluate(store_, info['cards'], datetime(2026, 11, 20, tzinfo=timezone.utc))['state'] == 'counting'
    assert m.evaluate(store_, info['cards'], datetime(2027, 7, 1, tzinfo=timezone.utc))['state'] != 'counting'


def test_a_game_whose_earliest_card_was_sent_before_t0_does_not_count(tmp_path):
    m = rep_mod()
    cards, _ = cards_for(2)
    pre = ('kpre', 'g0', '2026-11-11T21:30:00Z', cards[0][3])                     # same game, first card before T0, no row
    path = ledger_with(tmp_path, cards, extra_sent=[pre])
    sha = hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
    with sqlite3.connect(path) as led:
        led.row_factory = sqlite3.Row
        info = m.counted_cards(led, 'nba', REVIEWED, {sha})
    assert [c['game_id'] for c in info['cards']] == ['g1'] and info['excluded'] == {'earliest card sent before T0': 1}
    assert info['sent'] == 2 and info['sent_without_row'] == 0                    # since-T0 counts are unchanged


def test_baseline_picks_are_graded_as_of_the_completion_time(tmp_path):
    m = rep_mod()
    cards, res = cards_for(103)
    gid, tip, won, _ = res[197]                                                    # card 98's own baseline pick (not shared)
    res[197] = (gid + 'x', tip, won, '2026-11-29T12:00:00Z')                       # result first seen after the 100th named
    path = ledger_with(tmp_path, cards)
    sha = hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
    with sqlite3.connect(path) as led:
        led.row_factory = sqlite3.Row
        info = m.counted_cards(led, 'nba', REVIEWED, {sha})
    out = m.evaluate(result_store(res), info['cards'], datetime(2026, 12, 31, tzinfo=timezone.utc))
    assert out['n'] == 100 and out['baseline_states'].get('pending_at_completion') == 1
    assert out['test_b']['baseline_only'] == 65 and out['full_hit_rate']['baseline_n'] == 99


def test_the_guarded_telegram_command_records_which_commit_ran(monkeypatch):
    import os
    from app import cli, release
    seen = {}
    monkeypatch.delenv('NBA_RELEASE_COMMIT', raising=False)
    monkeypatch.setattr(release, 'enabled', lambda: True)
    monkeypatch.setattr(release, 'delivery_check', lambda: {'commit': 'abc1234def', 'problems': []})
    monkeypatch.setattr(tg, 'command', lambda args: seen.setdefault('commit', os.environ.get('NBA_RELEASE_COMMIT')) and 0)
    assert cli.main(['telegram', 'run', '--once']) == 0 and seen['commit'] == 'abc1234def'


def test_recorder_identity_is_the_code_that_was_imported(tmp_path, monkeypatch):
    assert bpr.RECORDER_SHA256 == hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()
    monkeypatch.setattr(bpr, 'RECORDER_SHA256', 'imported-code')                 # the file changing on disk later is not it
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        bpr.record(conn, 'k', 'nfl', 'g', bpr.card_pictures(None, 'nfl', report(pick(1, 'winner', 'a', .8, .85), sport='nfl'), NOW)['g'], AS_OF)
        assert conn.execute('SELECT recorder_sha256 FROM best_picks').fetchone()[0] == 'imported-code'


def real_record(sport, market, side, wins=0, losses=0, rank=None):
    """The object the real provider puts on a pick (schemas.ForecastPercentageRecord), not a dict (live bug Nov 12)."""
    from app.schemas import ForecastPercentageRecord
    return ForecastPercentageRecord(sport=sport, market=market, market_title=market, percentage='70.0', side=side,
                                    direction='x', source='primary_direct', model_version='m', line_source='none',
                                    state='no_history', wins=wins, losses=losses, settled=wins + losses, win_rate=None,
                                    loss_rate=None, ci95=None, small_sample=True, excluded={}, as_of=AS_OF, rank=rank)


def test_the_real_providers_record_objects_are_read_like_dicts(tmp_path):
    c = store()
    _, rep = nba_card(c)
    for p in rep['picks']:
        ph = p['percentage_history']
        p['percentage_history'] = real_record('nba', p['market'], ph['side'])
    pic = picture(c, rep)
    assert 'error' not in pic and pic['named_forecast_id'] == 1 and by_id(pic)[1]['h'] == {'H1': 1, 'H2': 1, 'H3': 1, 'H4': 1}
    assert by_id(pic)[1]['record']['side'] == 'a'
    rep['picks'][0]['percentage_history'] = real_record('nba', 'winner', 'a', wins=2, losses=3, rank=4)
    assert by_id(picture(c, rep))[1]['excluded'] == ['AVOID']
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        assert bpr.record(conn, 'k', 'nba', 'g', picture(c, rep), AS_OF)
        assert conn.execute('SELECT status FROM best_picks').fetchone()[0] == 'ok'
    nhl = report(pick(1, 'winner', 'a', .61, .58), pick(2, 'spread', 'a', .68, .40), sport='nhl')
    for p in nhl['picks']:
        p['percentage_history'] = real_record('nhl', p['market'], p['percentage_history']['side'])
    assert by_id(picture(c, nhl, 'nhl'))[2]['h']['H5a'] == 1
