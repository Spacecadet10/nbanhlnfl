"""Prices at three sportsbooks: only the exact pick on the exact line, never a withdrawn or stale price, and the app's
own stored quote is never replaced."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import rundown_odds as odds, tickets

NOW = datetime(2026, 11, 11, 0, 10, tzinfo=timezone.utc)
START = '2026-11-11T01:00:00Z'


def response(at=NOW, events=True):
    event = dict(event_id='rd-game', sport_id=6, event_date=START, score=dict(event_status='STATUS_SCHEDULED'),
                 teams=[dict(team_id=7, abbreviation='WPG', is_home=True, is_away=False),
                        dict(team_id=8, abbreviation='NJD', is_away=True, is_home=False)])
    event['markets'] = [
        dict(market_id=2, period_id=0, participants=[dict(id=8, name='New Jersey Devils', lines=[
            dict(value='+1.5', prices={'19': dict(price=-125, is_main_line=True, updated_at='2026-11-11T00:05:00Z'),
                                       '23': dict(price=-110, is_main_line=True),
                                       '22': dict(price=155, is_main_line=True, closed_at='2026-11-11T00:01:00Z'),
                                       '3': dict(price=200, is_main_line=True)}),
            dict(value='+2.5', prices={'22': dict(price=-300, is_main_line=True)})]),
            dict(id=7, name='Winnipeg Jets', lines=[dict(value='-1.5', prices={'19': dict(price=105, is_main_line=True)})])]),
        dict(market_id=2, period_id=1, participants=[dict(id=8, name='x', lines=[
            dict(value='+1.5', prices={'22': dict(price=900, is_main_line=True)})])]),
        dict(market_id=1, period_id=0, participants=[
            dict(id=7, name='Winnipeg Jets', lines=[dict(prices={'19': dict(price=-150, is_main_line=True),
                                                                 '22': dict(price=-140, is_main_line=True)})]),
            dict(id=8, name='New Jersey Devils', lines=[dict(prices={'19': dict(price=130, is_main_line=True)})])]),
        dict(market_id=3, period_id=0, participants=[
            dict(id=9, name='Over', lines=[dict(value='6.5', prices={'19': dict(price=-105, is_main_line=True)})]),
            dict(id=10, name='Under', lines=[dict(value='6.5', prices={'19': dict(price=-115, is_main_line=True),
                                                                         '23': dict(price=-112, is_main_line=True)}),
                                             dict(value='6', prices={'22': dict(price=100, is_main_line=True)})])])]
    return dict(_fetched_at=odds.stamp(at), _sport='nhl', _date='2026-11-11', events=[event] if events else [])


def pick(market='spread', side='b', line=-1.5, price=-125, label='NJ +1.5', matchup='NJ @ WPG', game='g1'):
    return dict(sport='nhl', matchup=matchup, market=market, pick_label=label, tipoff=START, game_id=game,
                probability=.60, percentage_history={'side': side},
                quote_check=dict(provider='DraftKings', price_now=price, price_at_forecast=price,
                                 line_at_forecast=line, line_now=line, line_source='bookmaker',
                                 line_moved=False, retrieved_at=odds.stamp(NOW)))


def cache(*responses):
    return {'responses': list(responses or [response()]), 'stale_responses': 0, 'invalid_files': []}


def test_away_spread_exact_line_only_and_stored_quote_untouched():
    p = pick()
    before = copy.deepcopy(p['quote_check'])
    odds.attach('nhl', p, cache(), NOW)
    c = p['book_comparison']
    assert c['status'] == 'available' and c['prices'] == {'DraftKings': -125, 'FanDuel': -110}   # closed, other period, unknown book: out
    assert c['best'] == dict(book='FanDuel', price=-110) and c['checked_at'] == odds.stamp(NOW)
    assert c['other_lines'] == [dict(book='BetMGM', line=-2.5, price=-300)]
    assert p['quote_check'] == before
    a = tickets.leg_assessment('nhl', p)
    assert a['price'] == -125 and a['best_book'] == 'FanDuel' and a['best_book_price'] == -110
    assert abs(a['best_book_gap'] - (.60 - 110 / 210)) < 1e-9 and a['model_gap'] < a['best_book_gap']


def test_winner_total_and_home_sides():
    for p, expected in [(pick('winner', 'a', None, -150, 'WPG win'), {'DraftKings': -150, 'BetMGM': -140}),
                        (pick('winner', 'b', None, 130, 'NJ win'), {'DraftKings': 130}),
                        (pick('total', 'b', 6.5, -115, 'Under 6.5'), {'DraftKings': -115, 'FanDuel': -112}),
                        (pick('total', 'a', 6.5, -105, 'Over 6.5'), {'DraftKings': -105}),
                        (pick('spread', 'a', -1.5, 105, 'WPG -1.5'), {'DraftKings': 105})]:
        assert odds.comparison('nhl', p, cache(), NOW)['prices'] == expected, p['pick_label']
    assert odds.comparison('nhl', pick('total', 'b', 5.5, -115), cache(), NOW)['status'] == 'no_exact_line'


def test_nothing_compared_when_the_game_line_or_time_does_not_match():
    p = pick()
    for attr, value, status in [('line_moved', True, 'forecast_line_changed'), ('line_source', 'model', 'unsupported_market')]:
        q = copy.deepcopy(p)
        q['quote_check'][attr] = value
        odds.attach('nhl', q, cache(), NOW)
        assert q['book_comparison']['status'] == status and not q['book_comparison']['prices']
    assert odds.comparison('nhl', pick(matchup='WPG @ NJ'), cache(), NOW)['status'] == 'no_current_event'   # home and away swapped
    assert odds.comparison('nba', pick(), cache(), NOW)['status'] == 'no_current_event'
    assert odds.comparison('soccer', pick(), cache(), NOW)['status'] == 'unsupported_market'
    assert odds.comparison('nhl', dict(pick(), market='team_total_home'), cache(), NOW)['status'] == 'unsupported_market'
    late = response()
    late['events'][0]['event_date'] = '2026-11-11T02:00:00Z'
    assert odds.comparison('nhl', p, cache(late), NOW)['status'] == 'no_current_event'
    live = response()
    live['events'][0]['score']['event_status'] = 'STATUS_IN_PROGRESS'
    assert odds.comparison('nhl', p, cache(live), NOW)['status'] == 'no_current_event'
    assert odds.comparison('nhl', p, cache(), NOW + timedelta(hours=1))['status'] == 'no_current_event'   # already started
    twice = response()
    twice['events'].append(dict(twice['events'][0], event_id='other'))
    assert odds.comparison('nhl', p, cache(twice), NOW)['status'] == 'ambiguous_event'


def test_conflicting_or_later_dated_prices_are_left_out_and_bad_data_never_raises():
    d = response()
    lines = d['events'][0]['markets'][0]['participants'][0]['lines']
    lines.append(dict(value='+1.5', prices={'23': dict(price=-140, is_main_line=True)}))            # FanDuel twice, two prices
    lines[0]['prices']['19']['updated_at'] = '2026-11-11T00:30:00Z'                                 # dated after the fetch
    assert odds.comparison('nhl', pick(), cache(d), NOW)['status'] == 'no_exact_line'
    broken = response()
    broken['events'][0]['markets'] = [None, dict(market_id=2, period_id=0, participants=[dict(id=8, lines=[dict(value='x', prices=3)])])]
    p = pick()
    odds.attach('nhl', p, cache(broken, dict(_sport='nhl', _fetched_at=odds.stamp(NOW), events=[3])), NOW)
    assert p['book_comparison']['status'] == 'no_exact_line'
    odds.attach('nhl', p, {}, NOW)
    assert p['book_comparison']['status'] == 'error' and p['book_comparison']['prices'] == {}
    assert tickets.leg_assessment('nhl', p)['best_book'] is None


def test_newest_response_replaces_older_and_future_or_old_files_are_not_used(tmp_path):
    for name, data in [('old', response(NOW - timedelta(minutes=10))), ('new', response(events=False)),
                       ('future', response(NOW + timedelta(minutes=1))), ('junk', 'x')]:
        (tmp_path / f'{name}.json').write_text(json.dumps(data))
    c = odds.read_cache(NOW, tmp_path)
    assert sorted(c['invalid_files']) == ['future.json', 'junk.json'] and len(c['responses']) == 1
    p = pick()
    odds.attach('nhl', p, c, NOW)
    assert p['book_comparison']['status'] == 'no_current_event'
    later = odds.read_cache(NOW + timedelta(hours=4), tmp_path)
    assert not later['responses'] and later['stale_responses'] == 1 and later['stale_sports'] == ['nhl']
    assert odds.read_cache(NOW, tmp_path / 'missing') == {'responses': [], 'stale_responses': 0, 'invalid_files': [], 'stale_sports': [], 'stale_days': []}


def test_ticket_names_the_one_sportsbook_that_pays_most_for_the_whole_parlay():
    p = pick()
    q = pick('winner', 'a', None, -150, 'WPG win', game='g2')
    for leg in (p, q):
        odds.attach('nhl', leg, cache(), NOW)
    a = tickets.ticket_assessment([p, q])
    assert sorted(a['book_payouts']) == ['DraftKings']        # only DraftKings prices both legs
    assert abs(a['book_payouts']['DraftKings'] - a['payout']) < 1e-9 and a['best_book'] == 'DraftKings'
    r = pick('total', 'b', 6.5, -115, 'Under 6.5', game='g3')
    odds.attach('nhl', r, cache(), NOW)
    b = tickets.ticket_assessment([p, r])
    assert b['best_book'] == 'FanDuel' and b['book_payouts']['FanDuel'] > b['payout']
    assert tickets.best_book_line(b) == f"Pays most at FanDuel: about ×{b['book_payouts']['FanDuel']:.2f}"
    result = dict(date='2026-11-10', resolved=[dict(label='Example', legs=[p, r], assessment=b)])
    caption = tickets.ticket_captions(result)[0]
    assert 'NJ +1.5 (-125)' in caption and 'Pays most at FanDuel' in caption and 'Other sportsbooks: prices stored' in caption
    none = tickets.ticket_assessment([pick(), pick(game='g4')])
    assert none['best_book'] is None and none['book_payouts'] == {} and tickets.best_book_line(none) is None
    plain = tickets.ticket_captions(dict(date='2026-11-10', resolved=[dict(label='Example', legs=[pick(), pick(game='g4')])]))[0]
    assert 'Pays most' not in plain and 'Other sportsbooks' not in plain


def test_ticket_picture_draws_with_and_without_book_prices():
    from app.telegram_card import render_ticket_card
    p, r = pick(), pick('total', 'b', 6.5, -115, 'Under 6.5', game='g3')
    plain = render_ticket_card('2026-11-10', [dict(label='Example', legs=[copy.deepcopy(p), copy.deepcopy(r)])])
    for leg in (p, r):
        odds.attach('nhl', leg, cache(), NOW)
    priced = render_ticket_card('2026-11-10', [dict(label='Example', legs=[p, r])])
    assert priced[:8] == plain[:8] == b'\x89PNG\r\n\x1a\n' and priced != plain


class Response:
    def __init__(self, headers):
        self.headers = headers
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return b'{"events": []}'


def test_fetch_saves_whole_responses_without_the_key_and_respects_caps(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'test-key-do-not-save')
    calls = []

    def opener(headers):
        def run(request, timeout):
            calls.append(request)
            return Response(headers)
        return run

    result = odds.fetch(NOW, opener=opener({}), pause=lambda _: None)      # provider reports no allowance: caps still hold
    assert result['status'] == 'ok' and len(calls) == 6 and 'hide_closed=true' in calls[0].full_url
    files = list((tmp_path / 'odds-books').glob('*.json'))
    assert len(files) == 6 and all('test-key-do-not-save' not in f.read_text() for f in files)
    assert 'test-key-do-not-save' not in (tmp_path / 'odds-books' / 'requests.jsonl').read_text()
    assert len(odds.read_cache(NOW)['responses']) == 6
    low = odds.fetch(NOW, opener=opener({'X-Datapoints-Remaining': '990'}), pause=lambda _: None)
    assert low['status'] == 'quota_low' and len(calls) == 7
    for _ in range(4):
        odds.fetch(NOW, opener=opener({}), pause=lambda _: None)
    for _ in range(3):
        odds.fetch(NOW, opener=opener({}), pause=lambda _: None)
    assert len(calls) == odds.DAY_REQUESTS and odds.fetch(NOW, opener=opener({}), pause=lambda _: None)['status'] == 'request_cap'

    def fail(request, timeout):
        raise OSError('https://example.invalid/?key=test-key-do-not-save')
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path / 'second'))
    failed = odds.fetch(NOW, opener=fail, pause=lambda _: None)
    assert failed == {'status': 'failed', 'error': 'OSError', 'results': []}


def test_credentials_file_must_be_private(tmp_path, monkeypatch):
    import pytest
    monkeypatch.delenv('THERUNDOWN_API_KEY', raising=False)
    path = tmp_path / '.env.odds'
    path.write_text('THERUNDOWN_API_KEY=abc\n')
    monkeypatch.setenv('NBA_ODDS_CREDENTIALS', str(path))
    path.chmod(0o644)
    with pytest.raises(ValueError):
        odds.credentials()
    path.chmod(0o600)
    assert odds.credentials() == 'abc'


def test_scheduled_job_runs_the_guarded_release_within_the_daily_request_cap():
    import plistlib
    from pathlib import Path
    from app import release
    root = Path(__file__).resolve().parents[2]
    plist = plistlib.loads((root / 'scripts/launchd/com.nba-predictor.rundown-odds.plist').read_bytes())
    live = '/Users/andry/nba-predictor-delivery'
    env = plist['EnvironmentVariables']
    assert env[release.GUARD_ENV] == '1' and env[release.ROOT_ENV] == live and env['NBA_DATA_DIR'].endswith('/data')
    assert plist['ProgramArguments'] == [f'{live}/backend/.venv/bin/python', '-m', 'app.rundown_odds', 'fetch']
    assert plist['WorkingDirectory'] == f'{live}/backend' and plist['RunAtLoad'] is False
    assert len(plist['StartCalendarInterval']) * len(odds.SPORT_IDS) * 2 <= odds.DAY_REQUESTS


def test_a_picture_too_tall_with_the_prices_is_drawn_without_them():
    from app import telegram_card as card

    def tickets_of(words):
        def leg(i):
            p = pick(label=' '.join(['Pelicans'] * words) + f' +11.5 {i}', game=f'g{i}')
            p['book_comparison'] = dict(checked_at=odds.stamp(NOW), prices={'DraftKings': -113, 'FanDuel': -110, 'BetMGM': -115})
            return p
        return [dict(label='Layout check', ticket_id=f'T{k}', legs=[leg(k * 10 + i) for i in range(7)]) for k in range(3)]

    def drawn(tickets, book_lines):
        try:
            return card._ticket_card('2026-11-11', tickets, preview=False, number_from=1, total=None, book_lines=book_lines)
        except ValueError:
            return None
    for words in range(1, 40):      # longer and longer pick names, until the prices no longer fit
        full = tickets_of(words)
        with_prices, without = drawn(full, True), drawn(full, False)
        if with_prices is None and without is not None:
            assert card.render_ticket_card('2026-11-11', full) == without
            return
        assert with_prices is None or card.render_ticket_card('2026-11-11', full) == with_prices
    raise AssertionError('no layout found where only the prices overflow')


def test_therundown_nhl_codes_for_calgary_and_vegas_match_the_app():
    # Review of d608906 (MEDIUM 1): TheRundown sends CAL and LAS; the app stores CGY and VGK. Only for the NHL.
    assert odds.canon('nhl', 'CAL') == 'CGY' and odds.canon('nhl', 'LAS') == 'VGK' and odds.canon('nhl', 'VEG') == 'VGK'
    assert odds.canon('nfl', 'LAS') == 'LAS' and odds.canon('nba', 'CAL') == 'CAL'
    r = response()
    r['events'][0]['teams'][0]['abbreviation'] = 'LAS'
    r['events'][0]['teams'][1]['abbreviation'] = 'CAL'
    p = pick(matchup='CGY @ VGK')
    assert odds.comparison('nhl', p, cache(r), NOW)['prices'] == {'DraftKings': -125, 'FanDuel': -110}


def test_only_old_prices_for_the_sport_are_reported_as_stale_not_as_a_missing_game(tmp_path):
    # Review of d608906 (MEDIUM 2): before the first fetch of the day the status said the game did not exist.
    (tmp_path / 'old.json').write_text(json.dumps(response(NOW - timedelta(hours=4))))
    c = odds.read_cache(NOW, tmp_path)
    assert c['responses'] == [] and c['stale_sports'] == ['nhl']
    out = odds.comparison('nhl', pick(), c, NOW)
    assert out['status'] == 'stale_prices' and out['prices'] == {}
    assert odds.comparison('nhl', pick(), cache(), NOW)['status'] == 'available'


def test_a_morning_price_check_runs_before_reviews_start():
    import plistlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    plist = plistlib.loads((root / 'scripts/launchd/com.nba-predictor.rundown-odds.plist').read_bytes())
    assert min(s['Hour'] for s in plist['StartCalendarInterval']) <= 10


def test_a_low_monthly_allowance_also_stops_the_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'k')
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return Response({'X-Datapoints-Remaining': '19000', 'X-Datapoints-Monthly-Remaining': '500'})
    assert odds.fetch(NOW, opener=opener, pause=lambda _: None)['status'] == 'quota_low' and len(calls) == 1


def test_a_damaged_request_log_line_does_not_stop_price_collection(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'k')
    (tmp_path / 'odds-books').mkdir()
    (tmp_path / 'odds-books' / 'requests.jsonl').write_text('{"day": "2026-11-11"\n')   # cut off mid-write
    result = odds.fetch(NOW, opener=lambda request, timeout: Response({}), pause=lambda _: None)
    assert result['status'] == 'ok' and len(result['results']) == 6


def test_the_picture_and_caption_name_a_better_book_only_when_it_pays_more_than_the_stored_price():
    # Review of d608906 (LOW 1): the picture printed "Pays most at DraftKings" even when that paid no more.
    p, q = pick(), pick('winner', 'a', None, -150, 'WPG win', game='g2')
    for leg in (p, q):
        odds.attach('nhl', leg, cache(), NOW)
    same = tickets.ticket_assessment([p, q])
    assert same['best_book'] == 'DraftKings' and tickets.best_book_line(same) is None


def test_a_game_whose_own_day_has_only_old_prices_is_stale_even_when_the_sport_has_fresh_ones(tmp_path):
    # Delta review LOW 1: a run that stopped after today's file leaves tomorrow's (UTC) evening games with old prices.
    old = response(NOW - timedelta(hours=4))
    fresh_other_day = dict(response(), _date='2026-11-10', events=[])
    for name, data in (('old', old), ('fresh', fresh_other_day)):
        (tmp_path / f'{name}.json').write_text(json.dumps(data))
    c = odds.read_cache(NOW, tmp_path)
    assert c['stale_sports'] == [] and c['stale_days'] == ['nhl 2026-11-11']
    assert odds.comparison('nhl', pick(), c, NOW)['status'] == 'stale_prices'


def reg3_response():
    """Shape of TheRundown's NHL 60-minute 3-way market (563) as saved on Nov 11 (docs/evidence/free-prices-2026-11-10)."""
    r = response()
    r['events'][0]['markets'].append(dict(market_id=563, period_id=0, name='moneyline_3_way_no_push', participants=[
        dict(id=7, name='Winnipeg Jets', type='TYPE_TEAM', lines=[dict(value=None, prices={'23': dict(price=160, is_main_line=True)})]),
        dict(id=8, name='New Jersey Devils', type='TYPE_TEAM', lines=[dict(value=None, prices={'23': dict(price=125, is_main_line=True)})]),
        dict(id=3, name='Draw', type='TYPE_RESULT', lines=[dict(value=None, prices={'23': dict(price=330, is_main_line=True)})])]))
    return r


def test_nhl_regulation_result_is_priced_from_the_60_minute_three_way_market_only():
    no_quote = dict(provider=None, price_now=None, price_at_forecast=None, line_at_forecast=None, line_now=None,
                    line_source='none', line_moved=False, retrieved_at=None)
    home, away = pick('reg3', 'a', None, None, 'WPG in regulation'), pick('reg3', 'b', None, None, 'NJ in regulation')
    for p in (home, away):
        p['quote_check'] = dict(no_quote)
    before = copy.deepcopy(home['quote_check'])
    odds.attach('nhl', home, cache(reg3_response()), NOW)
    assert home['book_comparison']['status'] == 'available' and home['book_comparison']['prices'] == {'FanDuel': 160}
    assert home['quote_check'] == before                                   # the app's own (empty) quote is not filled in
    assert odds.comparison('nhl', away, cache(reg3_response()), NOW)['prices'] == {'FanDuel': 125}
    # never the 2-way moneyline (-150/130 in the same response) and nothing when the 3-way market is absent
    assert odds.comparison('nhl', home, cache(), NOW)['status'] == 'no_exact_line'
    # a 60-minute price is never offered for another sport or for the full-game winner
    assert odds.comparison('nba', dict(home, sport='nba'), cache(reg3_response()), NOW)['status'] == 'unsupported_market'
    assert odds.comparison('nhl', pick('winner', 'a', None, -150, 'WPG win'), cache(reg3_response()), NOW)['prices'] == {
        'DraftKings': -150, 'BetMGM': -140}
    clash = reg3_response()                         # the Draw entry is never read as a team, even with a team's id
    clash['events'][0]['markets'][-1]['participants'][2]['id'] = 7
    assert odds.comparison('nhl', home, cache(clash), NOW)['prices'] == {'FanDuel': 160}
    a = tickets.leg_assessment('nhl', home)
    assert a['price'] is None and a['best_book'] == 'FanDuel' and abs(a['best_book_gap'] - (.60 - 1 / 2.6)) < 1e-9


def test_fetch_asks_for_the_three_way_market_on_nhl_requests_only(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'test-key-do-not-save')
    calls = []

    def run(request, timeout):
        calls.append(request.full_url)
        return Response({})
    assert odds.fetch(NOW, opener=run, pause=lambda _: None)['status'] == 'ok' and len(calls) == 6
    nhl = [u for u in calls if '/sports/6/' in u]
    assert len(nhl) == 2 and all('market_ids=1,2,3,563&' in u for u in nhl)
    assert all('market_ids=1,2,3&' in u for u in calls if '/sports/6/' not in u)


@pytest.mark.parametrize('refusal', ['http', 'schema'])
def test_a_refused_three_way_market_still_gets_normal_nhl_and_nfl_prices(tmp_path, monkeypatch, refusal):
    """Review MEDIUM (7f10f71): if TheRundown refuses market 563, the normal NHL prices and the NFL pull must still
    complete. The NHL request is repeated once without 563; that retry counts toward the daily request cap."""
    import io
    import urllib.error
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'test-key-do-not-save')
    calls = []

    class Bad(Response):
        def read(self): return b'{"error": "market not allowed"}'

    def run(request, timeout):
        calls.append(request.full_url)
        if '563' in request.full_url:
            if refusal == 'http':
                raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO(b''))
            return Bad({})
        return Response({})
    result = odds.fetch(NOW, opener=run, pause=lambda _: None)
    if refusal == 'schema':          # a garbled answer is retried each time but never remembered: 8 requests, no note
        assert result['status'] == 'ok' and len(calls) == 8
        assert '"refused"' not in (tmp_path / 'odds-books' / 'requests.jsonl').read_text()
        return
    assert result['status'] == 'ok' and len(calls) == 7                     # refused once, then remembered
    assert [r['sport'] for r in result['results']] == ['nba', 'nba', 'nhl', 'nhl', 'nfl', 'nfl']
    assert sum('market_ids=1,2,3&' in u and '/sports/6/' in u for u in calls) == 2
    assert all(r.get('without') == ['reg3'] for r in result['results'] if r['sport'] == 'nhl')
    log = (tmp_path / 'odds-books' / 'requests.jsonl').read_text().splitlines()
    assert len(log) == 8 and 'test-key-do-not-save' not in ''.join(log)       # 7 requests + 1 refusal note
    assert sum('"refused"' in x for x in log) == 1
    # The refusal is remembered for the rest of the UTC day: later runs ask 6, so 7 runs stay under the daily cap.
    for _ in range(6):
        assert odds.fetch(NOW, opener=run, pause=lambda _: None)['status'] == 'ok'
    assert len(calls) == 7 + 6 * 6 <= odds.DAY_REQUESTS
    assert not any('563' in u for u in calls[7:])


def test_a_network_failure_still_stops_the_run_without_a_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'test-key-do-not-save')
    calls = []

    def run(request, timeout):
        calls.append(request.full_url)
        if '/sports/6/' in request.full_url:
            raise OSError('down')
        return Response({})
    assert odds.fetch(NOW, opener=run, pause=lambda _: None)['status'] == 'failed' and len(calls) == 3


@pytest.mark.parametrize('code', [429, 500, 503])
def test_a_rate_limit_or_server_error_is_not_a_refusal_of_the_market(tmp_path, monkeypatch, code):
    import io
    import urllib.error
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'test-key-do-not-save')
    calls = []

    def run(request, timeout):
        calls.append(request.full_url)
        if '/sports/6/' in request.full_url:
            raise urllib.error.HTTPError(request.full_url, code, 'x', {}, io.BytesIO(b''))
        return Response({})
    assert odds.fetch(NOW, opener=run, pause=lambda _: None)['status'] == 'failed' and len(calls) == 3


def test_a_damaged_refusal_note_never_stops_price_collection(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('THERUNDOWN_API_KEY', 'test-key-do-not-save')
    (tmp_path / 'odds-books').mkdir()
    (tmp_path / 'odds-books' / 'requests.jsonl').write_text(json.dumps({'day': NOW.date().isoformat(), 'refused': 5}) + '\n')
    calls = []
    assert odds.fetch(NOW, opener=lambda r, timeout: calls.append(r) or Response({}), pause=lambda _: None)['status'] == 'ok'
    assert len(calls) == 6
