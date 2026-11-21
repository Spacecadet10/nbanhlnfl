"""Card colouring: per-class daily threshold (owner brief Nov 21 2026). Display only; nothing here changes a forecast."""
import sqlite3
from decimal import Decimal

import pytest

from app import card_threshold as ct

D = Decimal


def pick(i, pct, win, price='1.90', cls='W', sport='nba', settled='2026-11-18T05:00:00Z', push=False):
    return ct.Pick(key=f'{sport}:{i}', sport=sport, cls=cls, pct=D(pct), result='push' if push else 'win' if win else 'loss',
                   implied=D(1) / D(price), settled_at=settled)


# --- Decimal maths -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('p,tenths', [(0.5494, '54.9'), (0.5495, '55.0'), (0.5725, '57.3'), (0.5724, '57.2'), (0.9, '90.0')])
def test_tenths_uses_decimal_of_str_half_up(p, tenths):
    assert ct.tenths(p) == D(tenths)


@pytest.mark.parametrize('p,t,colour', [(0.5494, '55.0', 'red'), (0.5495, '55.0', 'green'),
                                        (0.5725, '57.3', 'green'), (0.5724, '57.3', 'red')])
def test_decimal_boundaries(p, t, colour):
    assert ct.colour_for(ct.tenths(p), D(t)) == colour


def test_float_maths_would_have_got_0_5495_wrong():
    assert round(0.5495 * 100, 1) == 54.9          # why the decision path never multiplies a float by 100
    assert ct.colour_for(ct.tenths(0.5495), D('55.0')) == 'green'


# --- Class routing ---------------------------------------------------------------------------------------------------------
def test_every_rendered_market_maps_to_exactly_one_class_or_none():
    expected = {
        **{(s, 'winner'): 'W' for s in ('nba', 'nhl', 'nfl', 'ncaab')}, ('soccer', '1x2_regulation'): 'W',
        **{(s, m): 'L' for s in ('nba', 'nhl', 'nfl', 'ncaab') for m in ('spread', 'total', 'team_total_home', 'team_total_away')},
        ('soccer', 'draw_no_bet'): 'L', ('soccer', 'goals_total'): 'L', ('soccer', 'goals_home'): 'L', ('soccer', 'goals_away'): 'L',
        ('nhl', 'reg3'): 'R', ('soccer', 'double_chance'): 'D',
        ('soccer', 'btts'): None, ('soccer', 'corners_total'): None, ('soccer', 'corners_home'): None,
        ('soccer', 'corners_away'): None, ('soccer', 'corners_handicap'): None,
    }
    assert ct.CLASS_OF == expected
    for key, cls in expected.items():
        assert ct.class_of(*key) == cls


@pytest.mark.parametrize('sport,market,line_source', [
    ('soccer', 'corners_total', 'model'), ('soccer', 'corners_handicap', 'model'), ('soccer', 'btts', 'model'),
    ('nba', 'team_total_home', 'model'), ('ncaab', 'team_total_away', 'model'),
    ('soccer', 'double_chance', 'none'), ('soccer', 'draw_no_bet', 'none'),          # only a derived no-vig price exists
    ('nba', 'spread', 'model'),
])
def test_unpriced_markets_get_no_colour(sport, market, line_source):
    table = ct.Table({'W': ct.Line('W', None, D('55.0'), 300, 'set', '2026-11-21', '2026-11-21', None),
                      'L': ct.Line('L', None, D('52.0'), 400, 'set', '2026-11-21', '2026-11-21', None),
                      'D': ct.Line('D', None, D('70.0'), 200, 'set', '2026-11-21', '2026-11-21', None)}, day='2026-11-21')
    out = ct.decide(sport, market, 0.80, line_source, table)
    assert out['colour'] is None


def test_scale_sanity_each_class_uses_its_own_threshold():
    table = ct.Table({'W': ct.Line('W', None, D('52.0'), 300, 'set', '2026-11-21', '2026-11-21', None),
                      'R': ct.Line('R', None, D('60.0'), 300, 'set', '2026-11-21', '2026-11-21', None),
                      'D': ct.Line('D', None, D('80.0'), 300, 'set', '2026-11-21', '2026-11-21', None)}, day='2026-11-21')
    assert ct.decide('nhl', 'reg3', 0.53, 'none', table)['colour'] == 'red'          # 53.0 vs R 60.0, not W 52.0
    assert ct.decide('nhl', 'reg3', 0.53, 'none', table)['class'] == 'R'
    # D is unpriced (no stored price), so it is never coloured, and never against W
    assert ct.decide('soccer', 'double_chance', 0.70, 'none', table)['colour'] is None
    assert ct.decide('nba', 'winner', 0.53, 'none', table)['colour'] == 'green'


@pytest.mark.parametrize('cls,sport,market,ls', [('W', 'nba', 'winner', 'none'), ('L', 'nhl', 'total', 'bookmaker'),
                                                 ('R', 'nhl', 'reg3', 'none')])
@pytest.mark.parametrize('t', ['55.0', '61.3'])
def test_boundary_per_class(cls, sport, market, ls, t):
    T = D(t)
    table = ct.Table({cls: ct.Line(cls, None, T, 300, 'set', '2026-11-21', '2026-11-21', None)}, day='2026-11-21')
    below, at, above = (float((T + d) / 100) for d in (D('-0.1'), D('0'), D('0.1')))
    assert ct.decide(sport, market, below, ls, table)['colour'] == 'red'
    assert ct.decide(sport, market, at, ls, table)['colour'] == 'green'
    assert ct.decide(sport, market, above, ls, table)['colour'] == 'green'


def test_cell_override_applies_only_to_its_sport():
    table = ct.Table({'L': ct.Line('L', None, D('55.0'), 400, 'set', '2026-11-21', '2026-11-21', None),
                      'ncaab:L': ct.Line('L', 'ncaab', D('60.0'), 250, 'set', '2026-11-21', '2026-11-21', None)},
                     day='2026-11-21')
    assert ct.decide('ncaab', 'spread', 0.58, 'bookmaker', table)['colour'] == 'red'
    assert ct.decide('nba', 'spread', 0.58, 'bookmaker', table)['colour'] == 'green'


# --- The rule -------------------------------------------------------------------------------------------------------------
def winners_above(t='55.0', n_low=100, n_high=120):
    """Below t: 30% wins at 1.90 (lose to the price); at/above t: 70% wins at 1.90 (beat it)."""
    out = [pick(i, '51.0', i % 10 < 3) for i in range(n_low)]
    out += [pick(1000 + i, t, i % 10 < 7) for i in range(n_high)]
    return out


def test_fit_picks_lowest_candidate_that_beats_the_price():
    r = ct.fit(winners_above('55.0'), prev=None, min_n=150, clamp=False)
    # every candidate from 51.1 to 55.0 holds exactly the 120 strong picks -> the lowest, 51.1, wins
    assert r.threshold == D('51.1')
    assert r.state == 'set' and r.n == 220


def test_fit_skips_candidates_with_fewer_than_100_picks():
    picks = [pick(i, '60.0', i % 10 < 6) for i in range(99)] + [pick(500 + i, '51.0', False) for i in range(60)]
    r = ct.fit(picks, prev=None, min_n=150, clamp=False)
    assert r.threshold is None and r.state == 'not established'


def test_pushes_change_nothing():
    base = winners_above('55.0')
    with_push = base + [pick(9000 + i, '56.0', False, push=True) for i in range(30)]
    a, b = ct.fit(base, prev=None, min_n=150, clamp=False), ct.fit(with_push, prev=None, min_n=150, clamp=False)
    assert (a.threshold, a.n, a.inputs_hash) == (b.threshold, b.n, b.inputs_hash)


def test_trailing_window_is_400_latest_settled():
    old = [pick(i, '51.0', True, settled='2026-11-16T05:00:00Z') for i in range(300)]
    new = winners_above('55.0', n_low=200, n_high=200)
    r = ct.fit(old + new, prev=None, min_n=150, clamp=False)
    assert r.n == 400 and r.window == 400


def test_no_lookahead_pick_settled_after_cutoff_is_ignored():
    picks = winners_above('55.0')
    late = [pick(7000 + i, '51.0', True, settled='2026-11-21T06:00:00Z') for i in range(200)]
    cutoff = '2026-11-21T05:00:00Z'
    assert ct.settled_before(picks + late, cutoff) == ct.settled_before(picks, cutoff)
    a = ct.fit(ct.settled_before(picks + late, cutoff), prev=None, min_n=150, clamp=False)
    b = ct.fit(picks, prev=None, min_n=150, clamp=False)
    assert (a.threshold, a.inputs_hash) == (b.threshold, b.inputs_hash)


def test_capped_three_point_jump_moves_one_point():
    r = ct.fit(winners_above('55.0'), prev=D('54.1'), min_n=150, clamp=True)
    assert r.requested == D('51.1') and r.threshold == D('53.1') and r.state == 'capped'


def test_under_150_keeps_yesterday_as_held():
    r = ct.fit(winners_above('55.0', n_low=20, n_high=100), prev=D('56.0'), min_n=150, clamp=True)
    assert r.threshold == D('56.0') and r.state == 'held' and r.n == 120


def test_no_qualifying_candidate_keeps_yesterday():
    picks = [pick(i, '60.0', i % 2 == 0) for i in range(200)]       # 50% at 1.90: never beats the price
    r = ct.fit(picks, prev=D('58.0'), min_n=150, clamp=True)
    assert r.threshold == D('58.0') and r.state == 'held' and r.reason == 'no candidate beat the price'


def test_rails_50_to_90():
    r = ct.fit(winners_above('55.0'), prev=D('50.3'), min_n=150, clamp=True)
    assert D('50.0') <= r.threshold <= D('90.0')


# --- Storage and states ---------------------------------------------------------------------------------------------------
def store(tmp_path):
    conn = sqlite3.connect(tmp_path / 'ct.sqlite')
    conn.row_factory = sqlite3.Row
    ct.migrate(conn)
    return conn


def test_seed_then_daily_run_and_states(tmp_path):
    conn = store(tmp_path)
    picks = winners_above('55.0')
    ct.seed(conn, '2026-11-20', {'W': picks}, generated_at='2026-11-20T15:00:00Z')
    t = ct.current(conn, '2026-11-20')
    assert t.lines['W'].state == 'seed' and t.lines['W'].threshold == D('51.1')
    assert t.lines['L'].state == 'not established'
    assert ct.legend(t.lines['W'], '2026-11-20').startswith('Green = 51.1% and above · seeded from')
    assert ct.legend(t.lines['L'], '2026-11-20') == 'No threshold for this market yet'
    # next day, no job ran: not refreshed, yesterday's line
    t2 = ct.current(conn, '2026-11-21')
    assert t2.lines['W'].state == 'not refreshed' and t2.lines['W'].threshold == D('51.1')
    assert ct.legend(t2.lines['W'], '2026-11-21') == "Threshold not refreshed today · using yesterday's line (set Nov 20)"


def test_failed_job_is_not_refreshed(tmp_path):
    conn = store(tmp_path)
    ct.seed(conn, '2026-11-20', {'W': winners_above('55.0')}, generated_at='2026-11-20T15:00:00Z')

    def boom():
        raise RuntimeError('store unavailable')
    with pytest.raises(RuntimeError):
        ct.run(conn, '2026-11-21', boom, generated_at='2026-11-21T15:00:00Z')
    assert ct.current(conn, '2026-11-21').lines['W'].state == 'not refreshed'


def test_run_writes_once_and_never_backfills(tmp_path):
    conn = store(tmp_path)
    ct.seed(conn, '2026-11-20', {'W': winners_above('55.0')}, generated_at='2026-11-20T15:00:00Z')
    ct.run(conn, '2026-11-21', lambda: {'W': winners_above('55.0')}, generated_at='2026-11-21T15:00:00Z')
    first = ct.current(conn, '2026-11-21').lines['W']
    ct.run(conn, '2026-11-21', lambda: {'W': winners_above('60.0')}, generated_at='2026-11-21T16:00:00Z')
    assert ct.current(conn, '2026-11-21').lines['W'] == first              # frozen: the day keeps its first T
    with pytest.raises(ValueError):
        ct.run(conn, '2026-11-19', lambda: {'W': winners_above('55.0')}, generated_at='2026-11-21T16:00:00Z')
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE card_thresholds SET threshold = '60.0'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute('DELETE FROM card_thresholds')


def test_unseeded_class_is_not_established_and_never_coloured(tmp_path):
    conn = store(tmp_path)
    ct.seed(conn, '2026-11-20', {'W': winners_above('55.0'), 'D': [pick(i, '80.0', True, cls='D') for i in range(87)]},
            generated_at='2026-11-20T15:00:00Z')
    t = ct.current(conn, '2026-11-20')
    assert t.lines['D'].state == 'not established' and t.lines['D'].n == 87
    assert ct.decide('nhl', 'reg3', 0.70, 'none', t)['colour'] is None
    assert ct.decide('nhl', 'reg3', 0.70, 'none', t)['legend'] is None          # no card line for an unseeded class
    assert ct.legend(t.lines['R'], '2026-11-20') == 'No threshold for this market yet'   # the API / CLI still say why


def test_legends_for_every_state():
    day = '2026-11-21'
    L = lambda state, carried=None, n=202: ct.Line('W', None, D('55.0'), n, state, '2026-11-21' if state != 'not refreshed' else '2026-11-20', '2026-11-21', carried)
    assert ct.legend(L('set'), day) == 'Green = 55.0% and above · this class has beaten the price from here (n=202 priced, set Nov 21)'
    assert ct.legend(ct.Line('W', None, D('55.0'), 202, 'seed', '2026-11-21', '2026-11-02', None, seed_to='2026-11-21'), day) == \
        'Green = 55.0% and above · seeded from Nov 2-21 (n=202 priced)'
    assert ct.legend(L('held', '2026-11-20', 41), day) == \
        'Green = 55.0% and above · line carried from Nov 20, not re-fitted today (n=41 priced)'
    assert ct.legend(L('capped', '2026-11-20'), day) == \
        'Green = 55.0% and above · line capped at +1.0 pt today, carried from Nov 20 (n=202 priced)'
    assert ct.legend(L('not refreshed'), day) == "Threshold not refreshed today · using yesterday's line (set Nov 20)"
    assert ct.legend(ct.Line('W', None, None, 0, 'not established', day, day, None), day) == 'No threshold for this market yet'
    # only set and seed claim the class has beaten the price
    for state in ('held', 'capped', 'not refreshed'):
        assert 'beaten' not in ct.legend(L(state, '2026-11-20'), day)


def test_api_flag_off_and_on(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('NBA_CARD_COLOURS', raising=False)
    client = TestClient(app)
    assert client.get('/api/card-thresholds').json() == {'enabled': False}
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    body = client.get('/api/card-thresholds').json()
    assert body['enabled'] is True and body['routes']['soccer']['double_chance'] is None
    assert {s['scope']: s['state'] for s in body['scopes']} == {c: 'not established' for c in ('D', 'L', 'R', 'W')}
    assert not (tmp_path / 'card_thresholds.sqlite').exists()          # the API never creates or writes the store
