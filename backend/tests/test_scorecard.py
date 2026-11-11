"""The scorecard (app/scorecard.py, owner request Nov 11): the full picture of every recorded pick as of its forecast time,
append-only, idempotent, never later information, and a writer that can never break a forecast job."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from app import scorecard as sc

T = '2026-11-11T23:00:00Z'
TIP = '2026-11-12T00:30:00Z'
ROOT = Path(__file__).resolve().parents[2]


def store(sport='nba'):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
      CREATE TABLE store_identity (sport TEXT);
      CREATE TABLE teams (team_id TEXT, abbr TEXT, name TEXT);
      CREATE TABLE games (game_id TEXT, season INT, season_type TEXT, status TEXT, tipoff_at TEXT, home_team_id TEXT,
                          away_team_id TEXT, home_score INT, away_score INT, result_available_at TEXT);
      CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, game_id TEXT, sport TEXT, season_type TEXT, market TEXT,
        status TEXT, source TEXT, model_version TEXT, line REAL, line_source TEXT, market_quote_id INT, p_a REAL, p_mid REAL,
        p_b REAL, confidence REAL, generated_at TEXT, tipoff_at TEXT);
      CREATE TABLE odds_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, provider TEXT, price_label TEXT,
        home_american INT, away_american INT, home_prob_devig REAL, retrieved_at TEXT);
      CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, provider TEXT, market TEXT, price_label TEXT,
        line REAL, price_a INT, price_mid INT, price_b INT, p_a_devig REAL, p_mid_devig REAL, p_b_devig REAL, retrieved_at TEXT,
        fetch_id INT);''')
    c.execute('INSERT INTO store_identity VALUES (?)', (sport,))
    c.executemany('INSERT INTO teams VALUES (?,?,?)', [('h', 'TOR', 'Toronto'), ('a', 'BOS', 'Boston')])
    c.execute("INSERT INTO games VALUES ('g',2027,'regular','scheduled',?,'h','a',NULL,NULL,NULL)", (TIP,))
    c.execute("INSERT INTO games VALUES ('prev',2027,'regular','final','2026-11-11T00:30:00Z','h','x',1,0,NULL)")  # TOR yesterday
    c.execute("INSERT INTO games VALUES ('prev2',2027,'regular','final','2026-11-09T00:30:00Z','a','y',1,0,NULL)")
    return c


def forecast(c, fid, market, p_a, at=T, line=None, line_source='none', quote=None, p_mid=0.0):
    p_b = round(1 - p_a - p_mid, 9)
    conf = max(p_a, p_b) / (p_a + p_b) if market != 'reg3' else max(p_a, p_mid, p_b)
    c.execute('INSERT INTO game_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (fid, 'g', 'nba', 'regular', market, 'forecast', 'primary_direct', 'v1', line, line_source, quote, p_a, p_mid,
               p_b, conf, at, TIP))


@pytest.fixture(autouse=True)
def _no_lineup(monkeypatch):
    from app import lineup
    monkeypatch.setattr(lineup, 'facts', lambda conn, sport, ids, at, box_as_of=False: {i: {'error': 'not in this test'} for i in ids}
                        if sport in ('nba', 'nhl') else {})


def card():
    return sc.connect(Path(':memory:'), create=True)


def test_a_quote_retrieved_after_the_forecast_never_enters_the_row():
    c = store()
    forecast(c, 1, 'winner', 0.60)
    c.executemany('INSERT INTO odds_quotes (game_id, provider, price_label, home_american, away_american, home_prob_devig, '
                  'retrieved_at) VALUES (?,?,?,?,?,?,?)',
                  [('g', 'DraftKings', 'open', -120, 100, 0.53, '2026-11-11T10:00:00Z'),
                   ('g', 'DraftKings', 'current', -120, 100, 0.53, '2026-11-11T10:00:00Z'),
                   ('g', 'DraftKings', 'current', -150, 130, 0.58, '2026-11-11T22:30:00Z'),
                   ('g', 'DraftKings', 'current', -300, 250, 0.74, '2026-11-11T23:00:01Z'),    # one second after T
                   ('g', 'DraftKings', 'open', -400, 300, 0.79, '2026-11-11T23:30:00Z')])
    row = sc.game_picture(c, 'nba', c.execute('SELECT * FROM game_forecasts').fetchone())
    assert row['book_state'] == 'present' and row['book_price'] == -150 and row['book_devig'] == pytest.approx(0.58)
    assert row['book_retrieved_at'] == '2026-11-11T22:30:00Z'
    assert row['open_retrieved_at'] == '2026-11-11T10:00:00Z' and row['open_label'] == 'open' and row['open_price'] == -120
    assert row['move_pts'] == pytest.approx(5.0)                       # 53% -> 58% toward the pick
    assert row['displayed_pct'] == '60.0' and row['side'] == 'a' and row['pick_label'] == 'TOR win'


def test_no_quote_by_the_forecast_time_is_missing_never_a_later_one():
    c = store()
    forecast(c, 1, 'winner', 0.40)
    c.execute("INSERT INTO odds_quotes (game_id, provider, price_label, home_american, away_american, home_prob_devig, "
              "retrieved_at) VALUES ('g','DraftKings','current',-150,130,0.58,'2026-11-11T23:10:00Z')")
    row = sc.game_picture(c, 'nba', c.execute('SELECT * FROM game_forecasts').fetchone())
    assert row['book_state'] == 'missing' and row.get('book_price') is None and row.get('book_devig') is None
    assert row['open_state'] == 'missing' and row.get('move_pts') is None
    assert 'forecast time' in row['book_note']


def test_line_market_move_is_measured_only_at_the_picks_own_line_and_book():
    c = store('nhl')
    c.executemany('INSERT INTO market_quotes (game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, '
                  'p_b_devig, retrieved_at, fetch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  [('g', 'FanDuel', 'total', 'current', 6.0, -110, -110, 0.60, 0.40, '2026-11-11T08:00:00Z', 0),   # other book
                   ('g', 'DraftKings', 'total', 'open', 6.5, -110, -110, 0.5, 0.5, '2026-11-11T09:00:00Z', 1),
                   ('g', 'DraftKings', 'total', 'current', 6.0, -110, -110, 0.50, 0.50, '2026-11-11T10:00:00Z', 2),
                   ('g', 'DraftKings', 'total', 'current', 6.0, -120, 100, 0.53, 0.47, '2026-11-11T22:00:00Z', 3),
                   ('g', 'DraftKings', 'total', 'current', 5.5, -140, 120, 0.57, 0.43, '2026-11-11T23:20:00Z', 4)])
    forecast(c, 1, 'total', 0.40, line=6.0, line_source='bookmaker', quote=4, p_mid=0.05)
    row = sc.game_picture(c, 'nhl', c.execute('SELECT * FROM game_forecasts').fetchone())
    assert row['side'] == 'b' and row['pick_label'] == 'Under 6'
    assert row['book_price'] == 100 and row['book_devig'] == pytest.approx(0.47) and row['book_line'] == 6.0
    assert row['open_line'] == 6.5 and row['open_retrieved_at'] == '2026-11-11T09:00:00Z'      # the book's first quote
    assert row['move_base_retrieved_at'] == '2026-11-11T10:00:00Z'                            # first quote at 6.0, same book
    assert row['move_pts'] == pytest.approx(-3.0)                                             # 50% -> 47%, never 6.5 vs 6.0
    assert row['move_line'] == pytest.approx(-0.5)


def test_spread_line_move_is_oriented_to_the_pick_side():
    c = store()
    c.executemany('INSERT INTO market_quotes (game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, '
                  'p_b_devig, retrieved_at, fetch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  [('g', 'DraftKings', 'spread', 'open', -3.5, -110, -110, 0.5, 0.5, '2026-11-11T09:00:00Z', 1),
                   ('g', 'DraftKings', 'spread', 'current', -4.5, -110, -110, 0.5, 0.5, '2026-11-11T22:00:00Z', 2)])
    forecast(c, 1, 'spread', 0.40, line=-4.5, line_source='bookmaker', quote=2)        # BOS +4.5 (was +3.5)
    row = sc.game_picture(c, 'nba', c.execute('SELECT * FROM game_forecasts').fetchone())
    assert row['pick_label'] == 'BOS +4.5' and row['move_line'] == pytest.approx(1.0) and row['move_pts'] == 0


def test_card_holds_only_predictions_recorded_by_the_forecast_time_and_spread_side():
    c = store()
    forecast(c, 1, 'winner', 0.62)                                                  # TOR to win
    forecast(c, 2, 'spread', 0.45, line=-4.5, line_source='model')                  # BOS +4.5 (other team), same time
    forecast(c, 3, 'spread', 0.55, at='2026-11-11T23:05:00Z', line=-3.5, line_source='model')   # later: TOR -3.5
    forecast(c, 4, 'total', 0.70, at='2026-11-11T22:00:00Z', line=221.5, line_source='model')
    rows = {r['forecast_id']: r for r in c.execute('SELECT * FROM game_forecasts')}
    w = sc.game_picture(c, 'nba', rows[1])
    others = json.loads(w['card_others'])
    assert sorted(o['forecast_id'] for o in others) == [2, 4]                        # never the 23:05 spread
    assert {o['market']: o['displayed_pct'] for o in others} == {'spread': '55.0', 'total': '70.0'}
    assert w['spread_on_winner'] == 0 and w['spread_state'] == 'present'
    later = sc.game_picture(c, 'nba', rows[3])
    assert later['spread_on_winner'] == 1                                            # TOR -3.5 with TOR to win
    early = sc.game_picture(c, 'nba', rows[4])
    assert early['card_others'] == '[]' and early['spread_state'] == 'missing'       # nothing else known at 22:00


def test_rest_days_and_back_to_back():
    c = store()
    forecast(c, 1, 'winner', 0.6)
    row = sc.game_picture(c, 'nba', c.execute('SELECT * FROM game_forecasts').fetchone())
    assert row['home_rest_days'] == 1.0 and row['home_b2b'] == 1
    assert row['away_rest_days'] == 3.0 and row['away_b2b'] == 0 and row['rest_state'] == 'present'


def test_three_book_prices_fetched_after_the_forecast_are_excluded(tmp_path):
    c = store()
    c.execute("INSERT INTO odds_quotes (game_id, provider, price_label, home_american, away_american, home_prob_devig, "
              "retrieved_at) VALUES ('g','DraftKings','current',-150,130,0.58,'2026-11-11T22:00:00Z')")
    forecast(c, 1, 'winner', 0.6)

    def response(at, price):
        ev = {'event_id': 'e1', 'event_date': TIP, 'score': {'event_status': 'STATUS_SCHEDULED'},
              'teams': [{'team_id': 1, 'abbreviation': 'TOR', 'is_home': True}, {'team_id': 2, 'abbreviation': 'BOS', 'is_away': True}],
              'markets': [{'market_id': 1, 'period_id': 0, 'participants': [
                  {'id': 1, 'name': 'Toronto', 'lines': [{'prices': {'19': {'price': price, 'is_main_line': True}}}]}]}]}
        return {'_fetched_at': at, '_sport': 'nba', '_date': '2026-11-12', 'events': [ev]}
    (tmp_path / 'a.json').write_text(json.dumps(response('2026-11-11T22:30:00Z', -140)))
    (tmp_path / 'b.json').write_text(json.dumps(response('2026-11-11T23:30:00Z', -300)))   # after T
    row = sc.game_picture(c, 'nba', c.execute('SELECT * FROM game_forecasts').fetchone(), books_dir=tmp_path)
    got = json.loads(row['books3'])
    assert row['books3_state'] == 'present' and got['prices'] == {'DraftKings': -140}
    assert got['checked_at'] == '2026-11-11T22:30:00Z'


def test_nba_lineup_without_a_report_by_the_forecast_time_is_missing(monkeypatch):
    from app import lineup
    monkeypatch.setattr(lineup, 'facts', lambda conn, sport, ids, at, box_as_of=False: {i: {'report': {'problem': 'no_report'},
        'home': {'flagged': None, 'problem': 'no_report'}, 'away': {'flagged': None, 'problem': 'no_report'}} for i in ids})
    c = store()
    forecast(c, 1, 'winner', 0.6)
    row = sc.game_picture(c, 'nba', c.execute('SELECT * FROM game_forecasts').fetchone())
    assert row['lineup_state'] == 'missing' and 'no_report' in row['lineup_note']
    assert json.loads(row['lineup_flags']) == ['TOR no injury report received']


def test_not_applicable_is_kept_apart_from_missing():
    c = store('nfl')
    forecast(c, 1, 'team_total_home', 0.55, line=24.5, line_source='model')
    row = sc.game_picture(c, 'nfl', c.execute('SELECT * FROM game_forecasts').fetchone())
    assert row['lineup_state'] == 'not_applicable'
    assert row['books3_state'] == 'not_applicable'                                  # team totals are not priced there
    assert row['book_state'] == 'missing'                                           # a model line: no sportsbook line
    nba = store('nba')
    forecast(nba, 1, 'winner', 0.6)
    assert sc.game_picture(nba, 'nba', nba.execute('SELECT * FROM game_forecasts').fetchone())['lineup_state'] == 'missing'


def test_append_only_and_idempotent_rerun():
    c, k = store(), card()
    forecast(c, 1, 'winner', 0.6)
    forecast(c, 2, 'spread', 0.55, line=-3.5, line_source='model')
    first = sc.sync(c, k, 'nba', now='2026-11-11T23:01:00Z')
    assert first['written'] == 2 and first['status'] == 'ok'
    before = [tuple(r) for r in k.execute('SELECT * FROM scorecard ORDER BY forecast_id')]
    again = sc.sync(c, k, 'nba', now='2026-11-11T23:02:00Z')
    assert again['written'] == 0 and [tuple(r) for r in k.execute('SELECT * FROM scorecard ORDER BY forecast_id')] == before
    with pytest.raises(sqlite3.DatabaseError, match='append-only'):
        k.execute("UPDATE scorecard SET displayed_pct='99.9'")
    with pytest.raises(sqlite3.DatabaseError, match='append-only'):
        k.execute('DELETE FROM scorecard')
    with pytest.raises(sqlite3.IntegrityError):                                     # one row per forecast id, ever
        k.execute('INSERT INTO scorecard SELECT * FROM scorecard WHERE forecast_id=1')
    assert {r[0] for r in k.execute('SELECT written_by FROM scorecard')} == {'job'}


def test_the_database_refuses_a_quote_later_than_the_forecast():
    c, k = store(), card()
    forecast(c, 1, 'winner', 0.6)
    bad = sc.game_picture(c, 'nba', c.execute('SELECT * FROM game_forecasts').fetchone())
    bad.update(book_retrieved_at='2026-11-11T23:00:01Z', scorecard_version='x', written_by='job', written_at=T)
    with pytest.raises(sqlite3.IntegrityError):
        k.execute(f"INSERT INTO scorecard ({','.join(bad)}) VALUES ({','.join('?' * len(bad))})", list(bad.values()))


def test_a_failing_row_or_fact_never_raises_and_never_blocks_the_others(monkeypatch):
    c, k = store(), card()
    forecast(c, 1, 'winner', 0.6)
    forecast(c, 2, 'total', 0.6, line=220.5, line_source='model')

    def picture(conn, r, cache):
        if r['forecast_id'] == 1:
            raise RuntimeError('boom')
        return sc.game_picture(conn, 'nba', r)
    out = sc.sync(c, k, 'nba', now='2026-11-11T23:01:00Z', picture=picture)
    assert out['status'] == 'partial' and out['written'] == 1 and '1' in out['errors']
    assert sc.sync(c, k, 'nba', now='2026-11-11T23:02:00Z')['written'] == 1           # retried on the next run
    monkeypatch.setattr(sc, '_rest', lambda *a: 1 / 0)                                # one fact failing: said, not filled
    forecast(c, 3, 'spread', 0.6, line=-2.5, line_source='model')
    sc.sync(c, k, 'nba', now='2026-11-11T23:03:00Z')
    r = k.execute('SELECT * FROM scorecard WHERE forecast_id=3').fetchone()
    assert r['rest_state'] == 'missing' and r['rest_note'].startswith('error: ZeroDivisionError') and r['home_rest_days'] is None
    broken = sqlite3.connect(':memory:')
    assert sc.sync(broken, k, 'nba', now=T)['status'] in ('ok', 'failed')               # no table: never raises


def test_writer_refuses_the_live_folder_without_the_release_flag_and_never_writes_the_store(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, 'protected_path', lambda p: True)
    assert sc.main(['sync', '--sport', 'nba']) == 1
    assert not (tmp_path / 'scorecard').exists()


def test_dratings_capture_appended_once(tmp_path):
    c, k = store(), card()
    forecast(c, 1, 'winner', 0.6)
    cap = tmp_path / 'captures.jsonl'
    cap.write_text(json.dumps({'sport': 'nba', 'forecast_id': 1, 'captured_at': '2026-11-11T23:01:30Z', 'lag_seconds': 90,
                               'matched': True, 'dr_p_home': 0.58, 'dr_p_away': 0.42}) + '\n' +
                   json.dumps({'sport': 'nhl', 'forecast_id': 1, 'captured_at': 'x', 'matched': True}) + '\n')
    assert sc.sync(c, k, 'nba', now='2026-11-11T23:02:00Z', captures=cap)['dratings'] == 1
    assert sc.sync(c, k, 'nba', now='2026-11-11T23:03:00Z', captures=cap)['dratings'] == 0
    assert tuple(k.execute('SELECT dr_p_home, lag_seconds FROM scorecard_dratings').fetchone()) == (0.58, 90)


def test_soccer_row_uses_prices_retrieved_by_the_forecast_time():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE teams (team_id TEXT, name TEXT);
      CREATE TABLE soccer_matches (match_id TEXT, kickoff_at TEXT, home_team_id TEXT, away_team_id TEXT);
      CREATE TABLE soccer_live_forecasts (forecast_id INTEGER PRIMARY KEY, match_id TEXT, competition_id TEXT, market TEXT,
        version INT, model TEXT, p_home REAL, p_draw REAL, p_away REAL, cutoff_at TEXT, generated_at TEXT, inputs_through TEXT,
        source_retrieved_at TEXT);
      CREATE TABLE soccer_odds_quotes (quote_id INTEGER PRIMARY KEY, match_id TEXT, provider TEXT, market TEXT, side TEXT,
        price_kind TEXT, offered INT, line REAL, american INT, decimal_odds REAL, retrieved_at TEXT);
      INSERT INTO teams VALUES ('h','Albania'),('a','Finland');
      INSERT INTO soccer_matches VALUES ('m','2026-11-12T19:45:00Z','h','a');''')
    c.execute("INSERT INTO soccer_live_forecasts VALUES (1,'m','uefa.nations','1x2_regulation',1,'intl-elo',0.5,0.3,0.2,"
              "'2026-11-12T06:00:00Z',?,NULL,?)", (T, T))
    for at, odds in (('2026-11-11T20:00:00Z', (2.0, 3.2, 4.0)), ('2026-11-11T23:30:00Z', (1.5, 4.0, 6.0))):
        for side, d in zip(('home', 'draw', 'away'), odds):
            c.execute("INSERT INTO soccer_odds_quotes (match_id, provider, market, side, price_kind, offered, american, "
                      "decimal_odds, retrieved_at) VALUES ('m','DraftKings','1x2',?,'current',1,?,?,?)", (side, int(100 * (d - 1)), d, at))
    k = card()
    assert sc.sync(c, k, 'soccer', now='2026-11-11T23:40:00Z')['written'] == 1
    r = k.execute('SELECT * FROM scorecard').fetchone()
    inv = (1 / 2.0) / (1 / 2.0 + 1 / 3.2 + 1 / 4.0)
    assert r['side'] == 'home' and r['displayed_pct'] == '50.0' and r['book_devig'] == pytest.approx(inv)
    assert r['book_retrieved_at'] == '2026-11-11T20:00:00Z'
    assert r['spread_state'] == 'not_applicable' and r['lineup_state'] == 'missing' and r['rest_state'] == 'missing'
    others = {(o['market'], o['side']): o['displayed_pct'] for o in json.loads(r['card_others'])}
    assert others[('1x2_regulation', 'draw')] == '30.0' and ('1x2_regulation', 'home') not in others
    assert others[('double_chance', 'home_draw')] == '80.0' and ('draw_no_bet', 'home') in others


# ---- the read-only report ----------------------------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location('scorecard_report', ROOT / 'scripts' / 'scorecard_report.py')


def report_module():
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_report_lists_each_exact_percentage_that_both_won_and_lost_side_by_side():
    rep = report_module()
    rows = [dict(sport='nba', market='winner', displayed_pct='57.3', grade=g, pick_label=l, book_devig=b, forecast_id=i,
                 game_date='2026-11-0%d' % i, matchup='A @ B', move_pts=None, spread_on_winner=s, home_b2b=0, away_b2b=0,
                 lineup_flag_count=0, card_others='[]', book_state='present', open_state='missing', side='a',
                 home_rest_days=2.0, away_rest_days=2.0)
            for i, (g, l, b, s) in enumerate([('win', 'B win', 0.62, 0), ('loss', 'C win', 0.54, 1), ('win', 'D win', 0.70, 0)], 1)]
    rows.append(dict(rows[0], displayed_pct='57.4', grade='win', forecast_id=9))     # 57.4 only won: not listed
    text = rep.render(rows)
    assert '57.3%' in text and '57.4%' not in text.split('Per-factor')[0]
    assert 'won 2, lost 1' in text
    assert 'too few to tell' in text                                                    # n on every line, never a claim
    assert 'n=' in text


def test_soccer_goals_forecast_gets_its_own_rows_and_appears_on_the_1x2_card(monkeypatch):
    import math
    from app import soccer_markets_live as sml
    monkeypatch.setattr(sml, 'state', lambda family: {'markets': ['goals_total']})
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE teams (team_id TEXT, name TEXT);
      CREATE TABLE soccer_matches (match_id TEXT, kickoff_at TEXT, home_team_id TEXT, away_team_id TEXT);
      CREATE TABLE soccer_live_forecasts (forecast_id INTEGER PRIMARY KEY, match_id TEXT, competition_id TEXT, market TEXT,
        version INT, model TEXT, p_home REAL, p_draw REAL, p_away REAL, cutoff_at TEXT, generated_at TEXT, inputs_through TEXT,
        source_retrieved_at TEXT);
      CREATE TABLE soccer_market_forecasts (forecast_id INTEGER PRIMARY KEY, match_id TEXT, competition_id TEXT, family TEXT,
        version INT, model TEXT, mean_home REAL, mean_away REAL, joint_json TEXT, cutoff_at TEXT, generated_at TEXT,
        inputs_through TEXT, source_retrieved_at TEXT);
      CREATE TABLE soccer_odds_quotes (quote_id INTEGER PRIMARY KEY, match_id TEXT, provider TEXT, market TEXT, side TEXT,
        price_kind TEXT, offered INT, line REAL, american INT, decimal_odds REAL, retrieved_at TEXT);
      INSERT INTO teams VALUES ('h','Arsenal'),('a','Fulham');
      INSERT INTO soccer_matches VALUES ('m','2026-11-12T19:45:00Z','h','a');''')
    c.execute("INSERT INTO soccer_live_forecasts VALUES (1,'m','eng.1','1x2_regulation',1,'clubs',0.6,0.25,0.15,"
              "'2026-11-12T06:00:00Z',?,NULL,?)", (T, T))
    pois = lambda lam, k: math.exp(-lam) * lam ** k / math.factorial(k)                       # noqa: E731
    joint = [[pois(1.8, h) * pois(0.9, a) for a in range(8)] for h in range(8)]
    c.execute("INSERT INTO soccer_market_forecasts VALUES (1,'m','eng.1','goals',1,'goals-model',1.8,0.9,?,"
              "'2026-11-12T06:00:00Z',?,NULL,?)", (json.dumps(joint), T, T))
    for side, am, d in (('over', -120, 1.83), ('under', 100, 2.0)):
        c.execute("INSERT INTO soccer_odds_quotes (match_id, provider, market, side, price_kind, offered, line, american, "
                  "decimal_odds, retrieved_at) VALUES ('m','DraftKings','total_goals',?,'current',1,2.5,?,?,'2026-11-11T20:00:00Z')",
                  (side, am, d))
    k = card()
    out = sc.sync(c, k, 'soccer', now='2026-11-11T23:40:00Z')
    assert out['written'] == 1 and out['market_rows'] == 2                                  # fair line + book line 2.5
    rows = {r['selection_key']: r for r in k.execute('SELECT * FROM scorecard_soccer_markets')}
    book = next(r for key, r in rows.items() if '|bookmaker|' in key)
    assert book['line'] == 2.5 and book['book_state'] == 'present' and book['book_retrieved_at'] == '2026-11-11T20:00:00Z'
    one = k.execute('SELECT card_others FROM scorecard').fetchone()[0]
    assert any(o['market'] == 'goals_total' and o['line'] == 2.5 for o in json.loads(one))
    assert sc.sync(c, k, 'soccer', now='2026-11-11T23:41:00Z')['market_rows'] == 0          # idempotent


def test_scheduled_writer_runs_only_from_the_clean_release_checkout(monkeypatch, tmp_path):
    import plistlib
    from app import config, release
    env = plistlib.loads((ROOT / 'scripts/launchd/com.nba-predictor.scorecard.plist').read_bytes())['EnvironmentVariables']
    assert env['NBA_RELEASE_GUARD'] == '1' and env['NBA_RELEASE_ROOT'] == '/Users/andry/nba-predictor-delivery'
    monkeypatch.setattr(config, 'protected_data_dir', lambda p: True)
    monkeypatch.setattr(config, 'protected_path', lambda p: True)
    monkeypatch.setattr(release, 'enabled', lambda: False)
    with pytest.raises(SystemExit, match='release guard'):
        sc.main(['sync', '--explicit-release'])


def test_box_file_written_after_the_forecast_makes_top_players_unknown_but_keeps_the_report(monkeypatch):
    import test_lineup as tl
    from app import lineup
    c = tl.nba_store(monkeypatch, [tl.entry('Barnes', 'Scottie', 'Out')])
    monkeypatch.setattr(lineup, '_box_written_after', lambda season, at: True)
    games = list(c.execute("SELECT * FROM games WHERE game_id='g'"))     # facts() is stubbed in this file: call the builder
    f = lineup.nba_facts(c, games, tl.AT, box_as_of=True)['g']
    assert f['home']['problem'] == 'player_file_after_forecast' and f['home']['top_players'] is None
    assert f['home']['report_status'] == 'submitted' and f['home']['listed'] == [{'player': 'Scottie Barnes', 'status': 'Out'}]
    assert 'TOR top players unknown (player file updated after forecast time)' in \
        lineup.review_flags('nba', {'market': 'winner', 'matchup': 'BOS @ TOR', 'pick_label': 'TOR win'}, f)
    assert lineup.nba_facts(c, games, tl.AT)['g']['home']['flagged']                     # the card's default is unchanged


def test_report_move_zero_is_no_move_lineup_kinds_and_missing_rows():
    rep = report_module()
    base = dict(sport='nba', market='winner', displayed_pct='60.0', pick_label='X win', book_devig=None, confidence=0.6,
                game_date='2026-11-01', matchup='A @ B', spread_on_winner=None, home_b2b=None, away_b2b=None,
                card_others='[]', book_state='present', open_state='present', side='a', lineup_state='present')
    rows = [dict(base, forecast_id=1, grade='win', move_pts=0.0, lineup_flags='["B top player X Out"]', lineup_flag_count=1),
            dict(base, forecast_id=2, grade='loss', move_pts=2.0, lineup_flags='["B goalie not confirmed (expected)"]',
                 lineup_flag_count=1)]
    text = rep.render(rows, without_row={('nba', 'winner'): 3})
    assert 'no move 1W-0L' in text and 'toward pick 0W-1L' in text
    assert 'injury/backup 1W-0L' in text and 'unconfirmed/unknown 0W-1L' in text
    assert 'canonical picks without a scorecard row: nba winner 3' in text
