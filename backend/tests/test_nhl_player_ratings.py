import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from app import adaptive, backtest, nhl_player_ratings as pr
from app.models.base import PregameGame
from app.models.hockey import HockeyEloConfig
from app.nhl_eval import HockeyRow
from app.sources import nhl_injuries as src


def page(status='Out'):
    teams = [{'displayName': team, 'items': [{'athlete': {'id': pid, 'name': name},
              'statusDesc': status, 'description': 'Jan 1: update', 'date': '2024-02-01'}]}
             for team, pid, name in [('Boston Bruins', '101', 'Pat One'), ('Toronto Maple Leafs', '102', 'Pat Two')]]
    return ("<script>window['__espnfitt__']=" + json.dumps({'page': {'content': {'injuries': teams}}}) + ';</script>').encode()


def response(stamp='20240101120000', body=None):
    request = httpx.Request('GET', f'https://web.archive.org/web/{stamp}id_/{src.PAGE}')
    return httpx.Response(200, content=body or page(), request=request)


def test_capture_redirect_hash_idempotency_and_cutoff(tmp_path):
    stamp = '20240101120000'
    a = src.save_capture(response(), stamp, tmp_path / 'injuries')
    assert a['captured_at'] == '2024-01-01T12:00:00Z'
    assert a['rows'][0]['estimated_return'] == '2024-02-01'
    src.save_capture(response(), stamp, tmp_path / 'injuries')
    assert len(list((tmp_path / 'injuries').glob('*.json'))) == 1
    with pytest.raises(ValueError, match='requested raw capture'):
        src.save_capture(response('20240102120000'), stamp, tmp_path / 'injuries')
    inputs = pr.Inputs(tmp_path)
    assert inputs.snapshot('2024-01-01T11:59:59Z')[0] is None
    assert inputs.snapshot('2024-01-01T12:00:00Z')[0]['captured_at'] == a['captured_at']
    assert inputs.snapshot('2024-01-02T12:00:01Z')[0] is None
    (tmp_path / 'injuries' / a['raw_file']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash mismatch'): pr.Inputs(tmp_path)


def test_parser_rejects_empty_and_keeps_unknown_status_and_old_history():
    with pytest.raises(ValueError): src.parse(b'<html>bot check</html>')
    assert src.parse(page('Unexpected'))[1][0]['weight'] is None
    body = b'''<table><tr class="stathead"><td>Boston Bruins</td></tr>
      <tr class="oddrow"><td>Pat One</td><td>Out</td><td>Jan 2</td></tr>
      <tr class="evenrow"><td>Pat One</td><td>Day-To-Day</td><td>Jan 1</td></tr></table>'''
    kind, rows, teams = src.parse(body)
    assert kind == 'table' and len(rows) == 1 and rows[0]['weight'] == 1.
    assert teams == ['Boston Bruins']


def fixture_model():
    players = {'nhl:6': [{'id': '1', 'name': 'Pat One', 'minutes': 20., 'plus_minus': 2, 'goalie': False}],
               'nhl:10': [{'id': '2', 'name': 'Pat Two', 'minutes': 20., 'plus_minus': -2, 'goalie': False}]}
    snap = {'teams': ['Boston Bruins', 'Toronto Maple Leafs'], 'rows': [
        {'team': 'Boston Bruins', 'player': 'Pat One', 'weight': 1.}]}
    inputs = SimpleNamespace(boxes={'nhl:1': {'teams': players, 'names': {
        'nhl:6': 'Boston Bruins', 'nhl:10': 'Toronto Maple Leafs'}}},
        snapshot=lambda cutoff: (snap, 2.), digest='digest', snapshots=[snap])
    g = PregameGame('nhl:1', 2024, 'regular', '2024-01-01T23:00:00Z', 'nhl:6', 'nhl:10', False,
                    '2024-01-01T21:00:00Z')
    model = pr.NhlPlayersInjuries(HockeyEloConfig(), inputs=inputs)
    return model, inputs, snap, g


def test_nhl_only_delivered_boxes_and_unknown_players_are_missing():
    model, inputs, snap, g = fixture_model()
    first = model.player_features(g)
    assert first['report_missing'] == 1 and first['player_home'] == 0
    rows = [HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG')]
    backtest.run(model, rows, deliver_until='2024-01-02T03:00:00Z')
    next_game = replace(g, game_id='nhl:2', tipoff_at='2024-01-03T23:00:00Z', cutoff_at='2024-01-03T21:00:00Z')
    f = model.player_features(next_game)
    assert f['report_missing'] == 0 and f['absent_home'] == pytest.approx(1/3)
    assert f['player_home'] == 0 and f['player_away'] < 0
    snap['rows'][0]['player'] = 'Unmatched Person'
    assert model.player_features(next_game)['unresolved'] == 1
    assert 'unresolved' in model.stale_inputs(next_game, {}, 5)
    snap['rows'] = []; snap['teams'] = ['Toronto Maple Leafs']
    assert model.player_features(next_game)['report_missing'] == 1
    # External cache never enters the pickle; actual player state survives.
    restored = adaptive.thaw(adaptive.Adaptive(pr.NhlPlayersInjuries(HockeyEloConfig())),
                             adaptive.freeze(adaptive.Adaptive(model)))
    assert restored.member.player_stats == model.player_stats and restored.member._inputs is None


def test_nhl_score_distribution_supports_every_market_and_refreshes_features():
    import numpy as np
    model, inputs, snap, g = fixture_model()
    backtest.run(model, [HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG')],
                 deliver_until='2024-01-02T03:00:00Z')
    g = replace(g, game_id='nhl:2', tipoff_at='2024-01-03T23:00:00Z', cutoff_at='2024-01-03T20:00:00Z')
    class Mean:
        def predict(self, x): return np.array([3. + row[8] for row in x])
    model.fitted = (np.zeros(8 + len(pr.FEATURES)), np.ones(8 + len(pr.FEATURES)), [Mean(), Mean()])
    d = model.distribution(g)
    snap['rows'] = []
    fresh = model.distribution(replace(g, cutoff_at='2024-01-03T21:00:00Z'))
    assert fresh.params != d.params
    for market, line in [('winner', None), ('reg3', None), ('spread', -1.5), ('total', 6.5),
                         ('team_total_home', 3.5), ('team_total_away', 3.5)]:
        p = d.winner() if market == 'winner' else d.market(market, line)
        assert sum(p) == pytest.approx(1.)


def test_team_key_folds_accents_and_relocations():
    # ESPN displayName vs NHL-API placeName+commonName differ by accents, punctuation and franchise moves.
    assert pr.team_key('Montréal Canadiens') == pr.team_key('Montreal Canadiens')
    assert pr.team_key('St. Louis Blues') == pr.team_key('St Louis Blues')
    assert pr.team_key('Arizona Coyotes') == pr.team_key('Utah Hockey Club') == pr.team_key('Utah Mammoth')
    assert pr.team_key(None) is None


def test_accented_box_team_matches_unaccented_snapshot_and_applies_injuries():
    # Regression: NHL-API box names carry accents ("Montréal"); the ESPN snapshot does not.
    # Raw string equality left the team unlisted, forcing report_missing and dropping the injury signal.
    players = {'nhl:8': [{'id': '11', 'name': 'Habs Skater', 'minutes': 20., 'plus_minus': 1, 'goalie': False}],
               'nhl:6': [{'id': '12', 'name': 'Bruin Skater', 'minutes': 20., 'plus_minus': 1, 'goalie': False}]}
    box = {'teams': players, 'names': {'nhl:8': 'Montréal Canadiens', 'nhl:6': 'Boston Bruins'}}
    snap = {'teams': ['Montreal Canadiens', 'Boston Bruins'],
            'rows': [{'team': 'Montreal Canadiens', 'player': 'Habs Skater', 'weight': 1.}]}
    inputs = SimpleNamespace(boxes={'nhl:1': box}, snapshot=lambda cutoff: (snap, 2.),
                             digest='d', snapshots=[snap])
    g = PregameGame('nhl:1', 2024, 'regular', '2024-01-01T23:00:00Z', 'nhl:8', 'nhl:6', False,
                    '2024-01-01T21:00:00Z')
    model = pr.NhlPlayersInjuries(HockeyEloConfig(), inputs=inputs)
    backtest.run(model, [HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG')],
                 deliver_until='2024-01-02T03:00:00Z')
    nxt = replace(g, game_id='nhl:2', tipoff_at='2024-01-03T23:00:00Z', cutoff_at='2024-01-03T21:00:00Z')
    f = model.player_features(nxt)
    assert f['report_missing'] == 0 and f['unresolved'] == 0
    assert f['absent_home'] == pytest.approx(1 / 3)
    # An unknown team still abstains (never silently "healthy").
    snap['teams'] = ['Boston Bruins']; snap['rows'] = []
    assert model.player_features(nxt)['report_missing'] == 1


def delivered_history_with_long_absence():
    """One prior appearance followed by six delivered games without the injured skater."""
    model, inputs, snap, first = fixture_model()
    from copy import deepcopy
    replacement = {'id': '3', 'name': 'Replacement Skater', 'minutes': 20., 'plus_minus': 0, 'goalie': False}
    for i in range(7):
        g = replace(first, game_id=f'nhl:{i+1}', tipoff_at=f'2024-01-{1+i*2:02d}T23:00:00Z',
                    cutoff_at=f'2024-01-{1+i*2:02d}T21:00:00Z')
        box = deepcopy(inputs.boxes['nhl:1'])
        if i: box['teams']['nhl:6'] = [dict(replacement)]
        inputs.boxes[g.game_id] = box
        model.predict(g)
        model.update(HockeyRow(g, g.cutoff_at, 4, 2, f'2024-01-{2+i*2:02d}T03:00:00Z', 'REG').result)
    nxt = replace(first, game_id='nhl:next', tipoff_at='2024-01-20T23:00:00Z', cutoff_at='2024-01-20T21:00:00Z')
    return model, inputs, snap, nxt


def test_long_absence_preserves_identity_and_preinjury_minutes_after_five_games():
    model, inputs, snap, g = delivered_history_with_long_absence()
    f = model.player_features(g)
    assert f['unresolved'] == 0 and f['report_missing'] == 0
    assert f['absent_home'] == pytest.approx(20/60)
    assert f['player_home'] == 0  # replacement's rating, not a phantom returning star
    restored = adaptive.thaw(adaptive.Adaptive(pr.NhlPlayersInjuries(HockeyEloConfig())),
                            adaptive.freeze(adaptive.Adaptive(model))).member
    restored._inputs = inputs
    assert restored.player_features(g) == f


def test_future_cached_identity_cannot_resolve_an_earlier_injury():
    from copy import deepcopy
    model, inputs, snap, g = delivered_history_with_long_absence()
    snap['rows'][0]['player'] = 'Future Player'
    earlier = model.player_features(g)
    future = deepcopy(inputs.boxes['nhl:1'])
    future['teams']['nhl:6'][0].update(id='999', name='Future Player')
    inputs.boxes['nhl:future'] = future
    assert model.player_features(g) == earlier
    assert earlier['unresolved'] == 1 and earlier['report_missing'] == 1


def test_observed_trade_removes_player_from_former_expected_roster():
    from copy import deepcopy
    model, inputs, snap, g = fixture_model()
    model.predict(g)
    model.update(HockeyRow(g,g.cutoff_at,4,2,'2024-01-02T03:00:00Z','REG').result)
    traded = replace(g, game_id='nhl:trade', tipoff_at='2024-01-03T23:00:00Z',cutoff_at='2024-01-03T21:00:00Z')
    box=deepcopy(inputs.boxes['nhl:1'])
    box['teams']['nhl:10'].append(box['teams']['nhl:6'].pop())
    box['teams']['nhl:6']=[{'id':'3','name':'Replacement','minutes':20.,'plus_minus':0,'goalie':False}]
    inputs.boxes[traded.game_id]=box
    model.predict(traded)
    model.update(HockeyRow(traded,traded.cutoff_at,4,2,'2024-01-04T03:00:00Z','REG').result)
    snap['rows']=[]
    nxt=replace(traded,game_id='nhl:next',tipoff_at='2024-01-05T23:00:00Z',cutoff_at='2024-01-05T21:00:00Z')
    assert model.player_features(nxt)['player_home'] == 0


def test_report_listing_a_player_on_another_team_removes_him_from_the_former_expected_roster():
    # Traded while injured: no box shows him with his new team yet, but the pre-cutoff report lists him there.
    model, inputs, snap, g = fixture_model()
    model.predict(g)
    model.update(HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG').result)
    nxt = replace(g, game_id='nhl:next', tipoff_at='2024-01-05T23:00:00Z', cutoff_at='2024-01-05T21:00:00Z')
    snap['rows'] = []
    assert model.player_features(nxt)['player_home'] > 0
    snap['rows'] = [{'team': 'Toronto Maple Leafs', 'player': 'Pat One', 'weight': 1.}]
    f = model.player_features(nxt)
    assert f['absent_away'] == pytest.approx(20 / 60)
    assert f['player_home'] == 0  # not simultaneously expected to play for Boston
    # The former team's game against a third team uses the same report evidence.
    other = replace(nxt, game_id='nhl:other', away_team_id='nhl:8')
    assert model.player_features(other)['player_home'] == 0


def test_bulk_player_box_rejects_incomplete_or_duplicate_rows():
    game = {'game_id': 'nhl:2023020001', 'home_team_id': 'nhl:6', 'away_team_id': 'nhl:10'}
    skaters = [{'gameId':2023020001,'homeRoad':side,'playerId':base+i,'skaterFullName':f'Player {base+i}',
                'timeOnIcePerGame':1200.,'plusMinus':1} for side,base in [('H',0),('R',100)] for i in range(18)]
    goalies = [{'gameId':2023020001,'homeRoad':side,'playerId':base,'goalieFullName':f'Goalie {base}',
                'timeOnIce':3600.} for side,base in [('H',200),('R',300)]]
    teams=pr.bulk_players(game,skaters,goalies)
    assert len(teams['nhl:6']) == 19 and teams['nhl:6'][0]['minutes'] == 20.
    with pytest.raises(ValueError,match='incomplete'): pr.bulk_players(game,skaters,[])
    with pytest.raises(ValueError,match='duplicate'): pr.bulk_players(game,skaters+[skaters[0]],goalies)
    # The same player cannot appear on opposing teams in one game.
    goalies[1]['playerId'] = skaters[0]['playerId']
    with pytest.raises(ValueError, match='duplicate'): pr.bulk_players(game, skaters, goalies)


def test_bulk_collection_continues_after_an_incomplete_game(tmp_path, monkeypatch):
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""CREATE TABLE games(game_id,season,home_team_id,away_team_id,status,season_type);
        CREATE TABLE teams(team_id,name);
        INSERT INTO teams VALUES ('nhl:6','Boston Bruins'),('nhl:10','Toronto Maple Leafs');
        INSERT INTO games VALUES ('nhl:2023020001',2024,'nhl:6','nhl:10','final','regular'),
                                 ('nhl:2023020002',2024,'nhl:6','nhl:10','final','regular');""")
    # The first requested game is absent from the response; the second is complete.
    skaters = [{'gameId':2023020002,'homeRoad':side,'playerId':base+i,
                'skaterFullName':f'Player {base+i}','timeOnIcePerGame':1200.,'plusMinus':0}
               for side,base in [('H',0),('R',100)] for i in range(18)]
    goalies = [{'gameId':2023020002,'homeRoad':side,'playerId':base,
                'goalieFullName':f'Goalie {base}','timeOnIce':3600.}
               for side,base in [('H',200),('R',300)]]
    def serve(request):
        rows = goalies if '/goalie/' in request.url.path else skaters
        return httpx.Response(200, json={'data':rows,'total':len(rows)})
    client = httpx.Client(transport=httpx.MockTransport(serve))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client)
    monkeypatch.setattr(pr.time, 'sleep', lambda seconds: None)
    result = pr.collect_bulk_boxes(conn, 2024, 2024, directory=tmp_path, log=lambda value: None)
    assert result['stored'] == 1 and result['failed'] == 1
    assert result['errors'][0]['game_id'] == 'nhl:2023020001'
    assert not (tmp_path / '2023020001.json').exists()
    saved = json.loads((tmp_path / '2023020002.json').read_text())
    assert saved['game_id'] == 'nhl:2023020002'
    assert len(saved['teams']['nhl:6']) == 19
    assert all((tmp_path / name).exists() for name in saved['raw_files'])
    conn.close()


def test_injury_inputs_reject_metadata_that_disagrees_with_raw(tmp_path):
    stamp = '20240101120000'
    meta = src.save_capture(response(), stamp, tmp_path / 'injuries')
    meta['rows'][0]['weight'] = 0.  # raw source still says Out
    (tmp_path / 'injuries' / f'{stamp}.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='parsed injury metadata'):
        pr.Inputs(tmp_path)


def test_rejected_injury_page_retains_raw_evidence_without_usable_metadata(tmp_path):
    body = b'<html>unrecognized source layout</html>'
    with pytest.raises(ValueError, match='unrecognized'):
        src.save_capture(response(body=body), '20240101120000', tmp_path)
    assert [p.read_bytes() for p in tmp_path.glob('*.html')] == [body]
    assert list(tmp_path.glob('*.json')) == []


def cached_box_fixture(tmp_path, bulk):
    import hashlib
    folder = tmp_path / 'boxes'
    folder.mkdir(exist_ok=True)
    payload, roster = official_box_fixture()
    payload['id'] = 2023020001
    teams, names = pr.box_players(payload, roster)
    if bulk:
        skaters, goalies = [], []
        for side, tid in [('H', 'nhl:100'), ('R', 'nhl:200')]:
            for p in teams[tid]:
                row = {'gameId':2023020001, 'homeRoad':side, 'playerId':int(p['id'])}
                if p['goalie']:
                    row.update(goalieFullName=p['name'], timeOnIce=p['minutes']*60)
                    goalies.append(row)
                else:
                    row.update(skaterFullName=p['name'], timeOnIcePerGame=p['minutes']*60, plusMinus=p['plus_minus'])
                    skaters.append(row)
        payloads = [{'data': skaters}, {'data': goalies}]
    else:
        payloads = [payload, roster]
    bodies = [json.dumps(p).encode() for p in payloads]
    hashes = [hashlib.sha256(body).hexdigest() for body in bodies]
    files = ([f'bulk-{i}-{sha}.raw' for i, sha in enumerate(hashes)] if bulk else
             [f'2023020001-{kind}-{sha}.raw' for kind, sha in zip(('box', 'pbp'), hashes)])
    for name, body in zip(files, bodies):
        (folder / name).write_bytes(body)
    meta = {'game_id': 'nhl:2023020001', 'teams': teams, 'names': names, 'raw_hashes': hashes}
    if bulk:
        meta['raw_files'] = files
    path = folder / '2023020001.json'
    path.write_text(json.dumps(meta))
    return path, meta, files


@pytest.mark.parametrize('bulk', [False, True])
def test_box_inputs_verify_original_raw_files(tmp_path, bulk):
    path, meta, files = cached_box_fixture(tmp_path, bulk)
    assert meta['game_id'] in pr.Inputs(tmp_path).boxes
    (path.parent / files[0]).write_bytes(b'corrupted source')
    with pytest.raises(ValueError, match='player box raw hash mismatch'):
        pr.Inputs(tmp_path)


@pytest.mark.parametrize('bulk', [False, True])
def test_box_inputs_reject_normalized_minutes_that_disagree_with_source(tmp_path, bulk):
    path, meta, files = cached_box_fixture(tmp_path, bulk)
    meta['teams']['nhl:100'][0]['minutes'] += 10.
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='normalized player box'):
        pr.Inputs(tmp_path)


def test_nhl_staleness_requires_the_team_in_recent_boxes():
    model, inputs, snap, game = delivered_history_with_long_absence()
    inputs.boxes['nhl:partial'] = {'teams': {'nhl:10': []}}
    recent = {(game.season, game.home_team_id): ['nhl:partial']}
    assert model.stale_inputs(game, recent, 5) is not None


def test_delayed_old_box_does_not_replace_recent_injury_minute_history():
    from copy import deepcopy
    model, inputs, snap, first = fixture_model()
    for i in [*range(1, 11), 0]:  # oldest result arrives last
        g = replace(first, game_id=f'nhl:delay{i}', tipoff_at=f'2024-01-{i+1:02d}T23:00:00Z',
                    cutoff_at=f'2024-01-{i+1:02d}T21:00:00Z')
        box = deepcopy(inputs.boxes['nhl:1'])
        box['teams']['nhl:6'][0]['minutes'] = 100. if i == 0 else 20.
        inputs.boxes[g.game_id] = box
        model.predict(g)
        model.update(HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-15T03:00:00Z', 'REG').result)
    nxt = replace(first, game_id='nhl:next', tipoff_at='2024-01-20T23:00:00Z', cutoff_at='2024-01-20T21:00:00Z')
    assert model.player_features(nxt)['absent_home'] == pytest.approx(20 / 60)


def test_delayed_old_box_does_not_restore_an_obsolete_expected_player():
    from copy import deepcopy
    model, inputs, snap, nxt = delivered_history_with_long_absence()
    old = replace(nxt, game_id='nhl:old', tipoff_at='2023-12-30T23:00:00Z', cutoff_at='2023-12-30T21:00:00Z')
    inputs.boxes[old.game_id] = deepcopy(inputs.boxes['nhl:1'])
    model.predict(old)
    model.update(HockeyRow(old, old.cutoff_at, 4, 2, '2024-01-19T03:00:00Z', 'REG').result)
    snap['rows'] = []
    assert model.player_features(nxt)['player_home'] == 0.


def official_box_fixture():
    payload = {'gameState': 'OFF', 'playerByGameStats': {}}
    roster = {'rosterSpots': []}
    for side, base in [('homeTeam', 100), ('awayTeam', 200)]:
        payload[side] = {'id': base, 'placeName': {'default': side}, 'commonName': {'default': 'Team'}}
        players = [{'playerId': base+i, 'toi': '20:00', 'plusMinus': 0} for i in range(15)]
        goalie = {'playerId': base+15, 'toi': '60:00'}
        payload['playerByGameStats'][side] = {'forwards': players, 'defense': [], 'goalies': [goalie]}
        roster['rosterSpots'].extend({'playerId': p['playerId'], 'firstName': {'default': 'Player'},
                                     'lastName': {'default': str(p['playerId'])}} for p in [*players, goalie])
    return payload, roster


@pytest.mark.parametrize('problem', ['duplicate', 'incomplete', 'invalid_seconds'])
def test_individual_box_rejects_bad_rosters_and_time(problem):
    payload, roster = official_box_fixture()
    assert len(pr.box_players(payload, roster)[0]['nhl:100']) == 16
    home = payload['playerByGameStats']['homeTeam']
    if problem == 'duplicate':
        home['forwards'].append(home['forwards'][0])
    elif problem == 'incomplete':
        home['goalies'] = []
    else:
        home['forwards'][0]['toi'] = '19:99'
    with pytest.raises(ValueError):
        pr.box_players(payload, roster)


@pytest.mark.parametrize('problem', ['wrong_team', 'swapped_sides'])
def test_nhl_update_rejects_box_for_wrong_teams_before_mutating_player_state(problem):
    model, inputs, snap, game = fixture_model()
    box = inputs.boxes[game.game_id]
    if problem == 'wrong_team':
        box['teams']['nhl:999'] = box['teams'].pop(game.home_team_id)
        box['names']['nhl:999'] = box['names'].pop(game.home_team_id)
    else:
        box.update(home_team_id=game.away_team_id, away_team_id=game.home_team_id)
    model.predict(game)
    with pytest.raises(ValueError, match='box teams disagree'):
        model.update(HockeyRow(game, game.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG').result)
    assert not model.player_stats and not model.delivered


def test_bulk_cache_explicit_sides_do_not_depend_on_json_key_order(tmp_path):
    path, meta, files = cached_box_fixture(tmp_path, True)
    meta.update(home_team_id='nhl:100', away_team_id='nhl:200')
    meta['teams'] = dict(reversed(list(meta['teams'].items())))
    path.write_text(json.dumps(meta))
    assert pr.Inputs(tmp_path).boxes[meta['game_id']]['teams'] == meta['teams']


def test_a_five_overtime_game_is_a_valid_box_but_an_impossible_time_is_not():
    """Cache rebuild 2026-10-13: nhl:2019030121 (2020 TBL-CBJ, five overtimes) has both goalies at ~150.4 minutes, above the
    old 150-minute cap, so the real game was rejected and the next few games of both teams looked stale."""
    game = {'game_id': 'nhl:2019030121', 'home_team_id': 'nhl:14', 'away_team_id': 'nhl:29'}
    skaters = [{'gameId': 2019030121, 'homeRoad': side, 'playerId': base + i, 'skaterFullName': f'Player {base + i}',
                'timeOnIcePerGame': 2400., 'plusMinus': 0} for side, base in [('H', 0), ('R', 100)] for i in range(18)]
    goalies = [{'gameId': 2019030121, 'homeRoad': side, 'playerId': base, 'goalieFullName': f'Goalie {base}',
                'timeOnIce': 9024.} for side, base in [('H', 200), ('R', 300)]]           # 150.4 minutes
    teams = pr.bulk_players(game, skaters, goalies)
    assert max(p['minutes'] for p in teams['nhl:14']) == pytest.approx(150.4)
    goalies[0]['timeOnIce'] = 201 * 60.                                                     # longer than any game played
    with pytest.raises(ValueError, match='invalid bulk time on ice'):
        pr.bulk_players(game, skaters, goalies)


# --- Live capture (player/injury fix: live NHL games were always report_missing; only the archive existed) --------------
def live_response(body=None, url=src.LIVE_PAGE):
    return httpx.Response(200, content=body or page(), request=httpx.Request('GET', url))


def test_a_live_capture_is_stamped_at_retrieval_and_read_like_an_archive_capture(tmp_path):
    meta = src.capture_live(get=lambda url: live_response(), directory=tmp_path / 'injuries',
                            clock=lambda: '2026-10-20T21:30:05Z')
    assert meta['source'] == 'live' and meta['captured_at'] == '2026-10-20T21:30:05Z' and meta['rows']
    inputs = pr.Inputs(tmp_path)
    assert inputs.snapshot('2026-10-20T21:30:04Z')[0] is None                       # never before it was retrieved
    assert inputs.snapshot('2026-10-20T23:00:00Z')[0]['captured_at'] == '2026-10-20T21:30:05Z'
    assert inputs.snapshot('2026-10-21T21:30:06Z')[0] is None                       # older than MAX_AGE_HOURS
    src.capture_live(get=lambda url: live_response(), directory=tmp_path / 'injuries', clock=lambda: '2026-10-20T22:00:00Z')
    assert len(pr.Inputs(tmp_path).snapshots) == 2                                  # each retrieval is its own snapshot
    (tmp_path / 'injuries' / meta['raw_file']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        pr.Inputs(tmp_path)


def test_a_live_capture_from_another_source_or_an_unparseable_page_is_never_usable(tmp_path):
    with pytest.raises(ValueError, match='not the ESPN NHL injuries page'):
        src.capture_live(get=lambda url: live_response(url='https://example.com/nhl/injuries'), directory=tmp_path / 'injuries',
                         clock=lambda: '2026-10-20T21:30:05Z')
    with pytest.raises(ValueError):
        src.capture_live(get=lambda url: live_response(body=b'<html>bot check</html>'), directory=tmp_path / 'injuries',
                         clock=lambda: '2026-10-20T21:31:00Z')
    assert pr.Inputs(tmp_path).snapshots == []                                       # raw evidence kept, nothing usable


def test_the_nhl_cycle_captures_injuries_only_when_switched_on(monkeypatch, tmp_path):
    from app import config, jobs
    calls = []
    monkeypatch.setattr(src, 'capture_live', lambda **kw: calls.append(kw) or {'captured_at': 'x', 'rows': [1]})
    monkeypatch.delenv('NHL_INJURY_CAPTURE', raising=False)
    assert jobs.nhl_injury_capture()['status'] == 'disabled' and calls == []
    monkeypatch.setenv('NHL_INJURY_CAPTURE', '1')
    assert config.nhl_injury_capture_enabled()
    out = jobs.nhl_injury_capture()
    assert out['status'] == 'success' and len(calls) == 1
    monkeypatch.setattr(src, 'capture_live', lambda **kw: (_ for _ in ()).throw(ValueError('bot check')))
    assert jobs.nhl_injury_capture()['status'] == 'failed'                         # never fatal to the cycle


def test_a_live_capture_must_carry_its_retrieval_time_and_a_torn_metadata_file_is_skipped(tmp_path):
    meta = src.capture_live(get=lambda url: live_response(), directory=tmp_path / 'injuries', clock=lambda: '2026-10-20T21:30:05Z')
    path = tmp_path / 'injuries' / 'live-20261020213005.json'
    path.write_text(json.dumps({**meta, 'captured_at': '2026-10-20T20:00:00Z'}))          # back-dated: rejected
    with pytest.raises(ValueError, match='live injury capture'):
        pr.Inputs(tmp_path)
    path.write_text(json.dumps(meta)[:40])                                                 # a write torn by a crash
    inputs = pr.Inputs(tmp_path)
    assert inputs.snapshots == [] and inputs.skipped_live == 1                             # skipped and counted, never fatal
    assert not list((tmp_path / 'injuries').glob('*.tmp'))
    src.save_capture(response(), '20240101120000', tmp_path / 'injuries')                  # archive files stay strict
    assert not list((tmp_path / 'injuries').glob('.*.tmp'))
    (tmp_path / 'injuries' / '20240101120000.json').write_text('{"torn')
    with pytest.raises(ValueError):
        pr.Inputs(tmp_path)


# --- Name variants (2026-10-16: ESPN "Matt Grzelcyk" never matched the NHL box "Matthew Grzelcyk", so every game of his team
# was report_missing. In the 2016-2026 archive 4,246 of 12,118 unresolved rows had a same-team, same-surname box player;
# review: a shared initial is not enough, the reported player may not be in any delivered box yet) -----------------
def delivered_with_names(home_names, report_player, report_team='Boston Bruins'):
    model, inputs, snap, g = fixture_model()
    inputs.boxes['nhl:1']['teams']['nhl:6'] = [
        {'id': str(10 + i), 'name': n, 'minutes': 20., 'plus_minus': 0, 'goalie': False} for i, n in enumerate(home_names)]
    model.predict(g)
    model.update(HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG').result)
    snap['rows'] = [{'team': report_team, 'player': report_player, 'weight': 1.}]
    nxt = replace(g, game_id='nhl:next', tipoff_at='2024-01-05T23:00:00Z', cutoff_at='2024-01-05T21:00:00Z')
    return model.player_features(nxt)


@pytest.mark.parametrize('box, report', [('Matthew Grzelcyk', 'Matt Grzelcyk'), ('Joseph Veleno', 'Joe Veleno'),
                                         ('Egor Chinakhov', 'Yegor Chinakhov'), ('Tony DeAngelo', 'Anthony DeAngelo'),
                                         ('Pierre-Olivier Joseph', 'P.O Joseph'), ('Matthew Grzelcyk', 'Matt  Grzelcyk'),
                                         ('Mathew Dumba', 'Matt Dumba'), ('JJ Peterka', 'John-Jason Peterka'),
                                         ('Joshua Brown', 'Josh Brown'), ('Mike Matheson', 'Michael Matheson')])
def test_a_first_name_variant_of_a_unique_teammate_resolves(box, report):
    f = delivered_with_names([box, 'Other Skater'], report)
    assert f['unresolved'] == 0 and f['report_missing'] == 0
    assert f['absent_home'] == pytest.approx(20 / 60)


@pytest.mark.parametrize('names, report', [(['Matt Savoie'], 'Carter Savoie'),          # brothers: another first name
                                           (['Emile Poirier'], 'Jeremie Poirier'),
                                           (['Jordan Brown'], 'Josh Brown'),      # the real Josh not delivered yet
                                           (['Jordie Benn'], 'Jamie Benn'),
                                           (['Joshua Brown', 'Josh Browne'], 'Jo Brown')])        # too short to be a prefix
def test_a_variant_needs_a_compatible_first_name_and_a_single_candidate(names, report):
    f = delivered_with_names(names, report)
    assert f['unresolved'] == 1 and f['report_missing'] == 1


def test_a_variant_is_matched_only_among_players_last_seen_on_the_listed_team():
    f = delivered_with_names(['Matthew Grzelcyk'], 'Matt Grzelcyk', report_team='Toronto Maple Leafs')
    assert f['unresolved'] == 1 and f['report_missing'] == 1


def test_two_compatible_teammates_stay_unresolved():
    f = delivered_with_names(['Joshua Brown', 'Josh Brown2'], 'Josh Brown')   # exact miss; one compatible candidate only
    assert f['unresolved'] == 0
    f = delivered_with_names(['Alexander Smith', 'Alexandre Smith'], 'Alex Smith')
    assert f['unresolved'] == 1 and f['report_missing'] == 1


def test_an_exact_name_on_another_team_still_takes_precedence_over_a_variant():
    # Documented choice (unchanged): an exact match elsewhere is a traded-while-injured player, not a variant.
    model, inputs, snap, g = fixture_model()
    inputs.boxes['nhl:1']['teams']['nhl:6'] = [{'id': '10', 'name': 'Joseph Veleno', 'minutes': 20., 'plus_minus': 0,
                                                 'goalie': False}]
    inputs.boxes['nhl:1']['teams']['nhl:10'] = [{'id': '11', 'name': 'Joe Veleno', 'minutes': 20., 'plus_minus': 0,
                                                  'goalie': False}]
    model.predict(g)
    model.update(HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG').result)
    snap['rows'] = [{'team': 'Boston Bruins', 'player': 'Joe Veleno', 'weight': 1.}]
    nxt = replace(g, game_id='nhl:next', tipoff_at='2024-01-05T23:00:00Z', cutoff_at='2024-01-05T21:00:00Z')
    f = model.player_features(nxt)
    assert f['absent_home'] == pytest.approx(20 / 60) and f['player_away'] == 0


def test_variant_indexes_survive_freeze_and_thaw():
    model, inputs, snap, g = fixture_model()
    inputs.boxes['nhl:1']['teams']['nhl:6'][0]['name'] = 'Matthew Grzelcyk'
    model.predict(g)
    model.update(HockeyRow(g, g.cutoff_at, 4, 2, '2024-01-02T03:00:00Z', 'REG').result)
    restored = adaptive.thaw(adaptive.Adaptive(pr.NhlPlayersInjuries(HockeyEloConfig())),
                             adaptive.freeze(adaptive.Adaptive(model))).member
    assert restored.surname_index == model.surname_index and restored.first_names == model.first_names
    assert restored.variant_ids('Matt Grzelcyk', 'nhl:6') == {'1'}
