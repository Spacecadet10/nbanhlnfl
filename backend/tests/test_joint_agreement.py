"""Joint vote-fraction and probability filters: distinct axes with chronological rule selection."""
import pytest
from app import agreement_tracking as a


def vectors():
    return {"a": (.70, 0, .30), "b": (.68, 0, .32), "c": (.66, 0, .34),
            "d": (.64, 0, .36), "e": (.20, 0, .80)}


def test_four_of_five_votes_is_not_eighty_percent_probability():
    r = a.joint_classify(vectors(), list(vectors()), "winner")
    assert r['agreement'] == .8
    assert r['statistic'] == pytest.approx(.67)
    assert r['votes']['a'] == 4 and r['votes']['n'] == 5
    assert a.joint_classify(vectors(), list(vectors()), 'winner', 'all_mean')['statistic'] == pytest.approx(.576)
    assert a.joint_classify(vectors(), list(vectors()), 'winner', 'supporters_min')['statistic'] == .64


def test_joint_boundaries_and_coverage_include_disagreement_and_missing():
    r = {**a.joint_classify(vectors(), list(vectors()), 'winner'), 'game_id': 'x', 'state': 'settled', 'realised': 0}
    missing = {**r, 'status': 'incomplete', 'side': None, 'agreement': None}
    cell = a.joint_cell([r, missing], 'winner', .8, None, .65, .70)
    assert cell['wins'] == 1 and cell['coverage'] == .5
    assert a.joint_cell([r], 'winner', .85, None, .65, .70)['selected'] == 0
    assert a.joint_cell([r], 'winner', .6, .8, .65, .70)['selected'] == 0
    assert a.joint_cell([r], 'winner', .8, None, .67, None)['selected'] == 1
    assert a.joint_cell([r], 'winner', .8, None, .6, .67)['selected'] == 0


def test_missing_ties_and_three_way_votes():
    assert a.joint_classify(vectors(), ['a','missing'], 'winner')['status'] == 'incomplete'
    v = {'a':(.3,.4,.3), 'b':(.2,.5,.3), 'c':(.5,.2,.3)}
    r = a.joint_classify(v,list(v),'reg3')
    assert r['side'] == 1 and r['agreement'] == pytest.approx(2/3) and r['statistic'] == .45
    v['d'] = (.5,0,.5)
    r = a.joint_classify(v,list(v),'reg3')
    assert r['status'] == 'no_majority' and r['votes']['abstain'] == 1
    assert a.joint_classify({'a':(.9,0,.9),'b':(.6,0,.4)},['a','b'],'winner')['status'] == 'incomplete'


def test_future_outcomes_do_not_change_earlier_rule_or_comparator():
    rows=[]
    for season in range(2019,2024):
        for n in range(120):
            rows.append({'game_id':f'{season}-{n}', 'season':season, 'state':'settled', 'realised':0 if n%5 else 2,
                         'vectors': {'a':(.75,0,.25),'b':(.65,0,.35),'c':(.2,0,.8)}})
    def walk(rs):
        items=[{**r, **a.joint_classify(r['vectors'], ['a','b','c'], 'winner')} for r in rs]
        return a.joint_walk(items,rs,['a','b','c'],'winner')
    before=walk(rows)
    after=walk([{**r,'realised':2-r['realised']} if r['season']>=2022 else r for r in rows])
    assert before['folds'][0] == after['folds'][0]
    assert before['folds'][1]['chosen'] == after['folds'][1]['chosen']
    assert before['folds'][1]['earlier'] == after['folds'][1]['earlier']
    assert before['folds'][1]['later']['win_rate'] != after['folds'][1]['later']['win_rate']


def test_joint_search_top_confidence_band_includes_one(monkeypatch):
    monkeypatch.setattr(a, 'report_thresholds', lambda market: ())
    monkeypatch.setattr(a, 'report_bands', lambda market: [[.9, 1.0 + 1e-9]])
    rows = [{'game_id': f'{season}-{n}', 'season': season, 'state': 'settled', 'realised': 0,
             'vectors': {'a': (1., 0., 0.), 'b': (1., 0., 0.)}}
            for season in range(2020, 2023) for n in range(120)]
    items = [{**r, **a.joint_classify(r['vectors'], ['a', 'b'], 'winner')} for r in rows]
    fold = a.joint_walk(items, rows, ['a', 'b'], 'winner')['folds'][0]
    assert fold['chosen'] is not None
    assert fold['chosen']['confidence_max'] is None
    assert fold['later']['wins'] == 120


def test_historical_goalie_subset_refused(monkeypatch):
    monkeypatch.setattr(a,'joint_rows', lambda *args: [{'vectors':{'a':(.6,0,.4),'x:nhl_goalie':(.6,0,.4)},
        'line':None,'definition':'historical-oof'}])
    monkeypatch.setattr(a,'load_registry',lambda: {'markets':{}})
    assert a.joint_report(None,'nhl','winner',members=['a','x:nhl_goalie'])['status']=='invalid_historical_inputs'


def test_search_can_choose_non_unanimous_agreement_band():
    rows = []
    for season in range(2019, 2023):
        for n in range(240):
            # The unanimous games lose; the 2/3 games win. The best individual wins half.
            v = {'a': (.8, 0, .2), 'b': (.7, 0, .3), 'c': ((.2, 0, .8) if n < 120 else (.75, 0, .25))}
            rows.append({'game_id': f'{season}-{n}', 'season': season, 'state': 'settled',
                         'realised': 0 if n < 120 else 2, 'vectors': v})
    items = [{**r, **a.joint_classify(r['vectors'], ['a', 'b', 'c'], 'winner')} for r in rows]
    out = a.joint_walk(items, rows, ['a', 'b', 'c'], 'winner')
    assert out['folds'][0]['chosen']['agreement_min'] == pytest.approx(2 / 3)
    assert out['folds'][0]['chosen']['agreement_max'] == 1
    assert out['folds'][0]['later']['wins'] == 120
    assert out['folds'][0]['later']['losses'] == 0
    assert out['folds'][0]['matched']['games'] == 120


def test_member_versions_remain_separate_and_pushes_are_not_losses(monkeypatch):
    rows = [{'game_id': str(n), 'tipoff_at': f'2026-10-0{n+1}T23:00:00Z', 'season': 2027, 'line': 6,
             'state': 'settled', 'realised': n, 'definition': 'same', 'versions': {'a': str(n), 'b': '1'},
             'vectors': {'a': (.7, .1, .2), 'b': (.6, .1, .3)}} for n in range(3)]
    monkeypatch.setattr(a, 'joint_rows', lambda *args: rows)
    monkeypatch.setattr(a, 'load_registry', lambda: {'markets': {}})
    r = a.joint_report(None, 'nhl', 'total', evidence='prospective', members=['a', 'b'])
    assert len(r['by_definition']) == 3
    assert sum(g['cell']['wins'] for g in r['by_definition']) == 1
    assert sum(g['cell']['losses'] for g in r['by_definition']) == 1
    assert sum(g['cell']['pushes'] for g in r['by_definition']) == 1


@pytest.mark.parametrize('sport,market', [('nba', 'winner'), ('nba', 'total'), ('nhl', 'reg3'), ('nhl', 'total')])
def test_joint_api_contract_and_range_validation(monkeypatch, sport, market):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app import main
    p = SimpleNamespace(mode='real', sport=sport, conn=None,
                        market_infos=lambda: [SimpleNamespace(id=market, available=True)])
    monkeypatch.setattr(a, 'joint_rows', lambda *args: [{'game_id': 'g', 'tipoff_at': '2026-01-01T23:00:00Z',
        'season': 2026, 'line': None, 'state': 'settled', 'realised': 0, 'definition': 'historical-oof',
        'vectors': {'a': (.7, 0, .3), 'b': (.6, 0, .4)}}])
    monkeypatch.setattr(a, 'load_registry', lambda: {'markets': {}})
    main.app.dependency_overrides[main.get_provider] = lambda: p
    try:
        c = TestClient(main.app)
        params = {'sport': sport, 'market': market, 'members': 'a,b', 'agreement_min': 1, 'confidence_min': .65}
        response = c.get('/api/research/tracking/joint', params=params)
        assert response.status_code == 200, response.text
        r = response.json()
        assert r['recommendation'] is False
        assert r['by_definition'][0]['cell']['wins'] == 1
        assert r['recent'][0]['agreement'] == 1 and r['confidence'] == 'supporters_mean'
        assert c.get('/api/research/tracking/joint', params={**params, 'agreement_max': .5}).status_code == 422
        assert c.get('/api/research/tracking/joint', params={**params, 'members': 'a'}).json()['status'] == 'choose_models'
        assert c.get('/api/research/tracking/joint', params={**params, 'members': 'a,unknown'}).status_code == 422
    finally:
        main.app.dependency_overrides.pop(main.get_provider, None)


# Reuse the existing full synthetic NHL pipeline fixtures (no duplicate forecast fixtures).
from test_nhl_eval import nhl_store  # noqa: E402,F401
from test_live_markets import sealed  # noqa: E402,F401
from test_research_live import game, run, GID  # noqa: E402,F401
from test_agreement_tracking import candidate, core_winner  # noqa: E402


def test_prospective_reads_exact_cohort_line_and_preserves_voids_without_writes(game, monkeypatch):
    monkeypatch.setattr(a, 'utc_now', lambda: '2026-10-12T22:00:00Z')
    run(game, '2026-10-12T21:30:00Z')
    core = core_winner(game)
    candidate(game, core['cohort_id'], name='right')
    candidate(game, core['cohort_id'] + 999, name='wrong_cohort')
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        candidate(game, core['cohort_id'], line=1, name='wrong_line')
    candidate(game, core['cohort_id'], name='later', at_='2026-10-12T22:30:00Z')
    before = game.total_changes
    row = a.joint_rows(game, 'nhl', 'winner', 'prospective')[0]
    assert row['state'] == 'pending'
    assert 'right' in row['vectors']
    assert not {'wrong_cohort', 'wrong_line', 'later'} & row['vectors'].keys()
    assert a.joint_rows(game, 'nhl', 'winner', 'prospective', version='unknown') == []
    assert a.joint_rows(game, 'nhl', 'winner', 'prospective', line=1) == []
    assert game.total_changes == before
    game.execute("UPDATE games SET tipoff_at='2026-10-12T23:01:00Z' WHERE game_id=?", (GID,))
    row = a.joint_rows(game, 'nhl', 'winner', 'prospective')[0]
    assert row['state'] == 'void' and row['realised'] is None
