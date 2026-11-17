"""Forward availability inputs: retrieval/publication cutoffs and fail-closed missing data."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from app import availability as av, availability_live as al, raw_cache
from test_live import store

NOW = '2026-10-21T22:30:00Z'


def report(store, completed=NOW, slot='2026-10-21T22:00:00Z'):
    _, fid = raw_cache.store_fetch(store, source='nba_injury_report', endpoint='test', request_key='report',
        body=b'pdf', retrieved_at=completed, importer_version='test', suffix='.pdf')
    store.execute("INSERT INTO injury_report_attempts (file_name,slot_at,method,purpose,started_at,completed_at,outcome,fetch_id) "
                  "VALUES ('report',?,'GET','collect',?,?,'stored',?)", (slot,completed,completed,fid))
    return fid


def parsed(at='2026-10-21T22:00:00Z'):
    return av.ParsedReport('report', datetime.fromisoformat(at.replace('Z','+00:00')), entries=[
        dict(game_date='2026-10-21',team='Team 1',last='Player',first='One',status='Out')],loose_lines=1)


def minute_box():
    return {2026: ({'p1':[av.Appearance('2026-04-01',2026,'1',30,True)]}, {'p1':'One Player'})}


def test_report_received_after_cutoff_is_not_read(store, monkeypatch):
    report(store, '2026-10-21T22:30:05Z')
    monkeypatch.setattr(al.av, 'parse_report', lambda *a: pytest.fail('future PDF read'))
    with pytest.raises(av.AvailabilityError, match='actually received'):
        al.current_facts(store,NOW)


def test_recent_download_of_old_report_is_stale(store, monkeypatch):
    report(store)
    monkeypatch.setattr(al.av,'parse_report',lambda *a:parsed('2026-10-20T22:00:00Z'))
    with pytest.raises(av.AvailabilityError, match='publication'):
        al.current_facts(store,NOW)


def test_current_report_uses_prior_minutes_and_fails_on_unknown_player(store, monkeypatch):
    report(store)
    monkeypatch.setattr(al.av,'parse_report',lambda *a:parsed())
    monkeypatch.setattr(al,'live_box',lambda *a:(minute_box(),[99]))
    monkeypatch.setattr(al.av,'load_aliases',lambda:{})
    facts, fetches=al.current_facts(store,NOW)
    assert facts['2026-10-21'].absent['1']['all']==30 and facts['2026-10-21'].published_at==NOW
    assert fetches[-1]==99
    monkeypatch.setattr(al,'live_box',lambda *a:({2026:({}, {})},[]))
    with pytest.raises(av.AvailabilityError, match='Unresolved'):
        al.current_facts(store,NOW)


def test_future_minutes_seed_is_not_read(store, monkeypatch):
    raw_cache.store_fetch(store, source=al.SEED_SOURCE, endpoint='test', request_key='player_box_2026.csv',body=b'bad',
                         retrieved_at='2026-10-21T22:30:05Z',importer_version='test')
    with pytest.raises(av.AvailabilityError,match='No prior-season'):
        al.live_box(store,NOW)


def summary():
    athletes=[dict(athlete=dict(id=str(i),displayName=f'Player {i}'), stats=['48']) for i in range(5)]
    return dict(boxscore=dict(players=[dict(team=dict(id=t), statistics=[dict(labels=['MIN'],athletes=athletes)]) for t in ['1','2']]))


def test_player_minutes_require_both_teams_correct_game_and_complete_values(monkeypatch):
    game=dict(game_id='espn:g',home_team_id='espn:1',away_team_id='espn:2')
    monkeypatch.setattr(al.espn,'parse_summary_box',lambda _:SimpleNamespace(completed=True,game_id='espn:g'))
    body=summary()
    assert len(al.summary_players(body,game))==2
    body['boxscore']['players'][0]['statistics'][0]['athletes'][0]['stats']=[]
    with pytest.raises(av.AvailabilityError,match='Missing player minutes'):
        al.summary_players(body,game)
    monkeypatch.setattr(al.espn,'parse_summary_box',lambda _:SimpleNamespace(completed=True,game_id='espn:other'))
    with pytest.raises(av.AvailabilityError,match='another game'):
        al.summary_players(summary(),game)


def test_live_definition_uses_two_hour_training_cutoff_and_unsubmitted_fails(store,tmp_path,monkeypatch):
    import hashlib
    from app.models.elo import EloConfig
    from app import live
    historical={'2026-10-21':dict(usable=True,reason='test',published_at='2026-10-21T22:00:00Z',
        absent={'2':dict(new=48,all=48)},unsubmitted=[],teams_on_report=[])}
    payload=dict(source_hash='test',facts=historical)
    payload['sha256']=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    monkeypatch.setattr(av,'research_dir',lambda:tmp_path)
    (tmp_path/'forward-facts.json').write_text(json.dumps(payload))
    current=av.ReportFacts(True,'test',NOW,absent={'2':dict(new=24,all=24)})
    monkeypatch.setattr(al,'current_facts',lambda *a:({'2026-10-21':current},[1]))
    g=live.engine(store).pregame(store.execute("SELECT * FROM games WHERE game_id='espn:g1'").fetchone())
    m=al.build(store,EloConfig(),NOW,set())
    assert m.training_two_hour and m._features(g)['report_missing']==1 # historical 22:00 report after 21:30 cutoff
    m=al.build(store,EloConfig(),NOW,{g.game_id})
    assert m._features(g)['all_absent_diff']==.5
    current.unsubmitted.add('1')
    with pytest.raises(av.AvailabilityError,match='not submitted'):
        m._features(g)


# --- Owner (Nov 17): confirmed-Out players who have never played, and blockers that stay visible -------------------------
def roster(store, team='1', athletes=(), at='2026-10-21T21:00:00Z'):
    body = json.dumps({'athletes': [dict(id=i, fullName=n, experience=dict(years=y)) for i, n, y in athletes]}).encode()
    return raw_cache.store_fetch(store, source=al.ROSTER_SOURCE, endpoint='test', request_key=f'team={team}', body=body,
                                 retrieved_at=at, importer_version='test', suffix='.json')[1]


def rookie_report(store, monkeypatch, status='Out', last='Rookie', first='Ronnie'):
    report(store)
    monkeypatch.setattr(al.av, 'parse_report', lambda *a: av.ParsedReport('report', datetime.fromisoformat(
        '2026-10-21T22:00:00+00:00'), entries=[dict(game_date='2026-10-21', team='Team 1', last=last, first=first,
                                                    status=status)], loose_lines=1))
    monkeypatch.setattr(al.av, 'load_aliases', lambda: {})


def test_a_confirmed_out_player_who_has_never_played_gets_zero_minutes(store, monkeypatch):
    rookie_report(store, monkeypatch)
    monkeypatch.setattr(al, 'live_box', lambda *a: ({2026: ({}, {})}, []))
    fid = roster(store, athletes=[('r1', 'Ronnie Rookie', 0)])
    facts, fetches = al.current_facts(store, NOW)
    day = facts['2026-10-21']
    assert day.absent['1'] == {'new': 0.0, 'all': 0.0}
    assert day.zero_minute_out == [{'athlete_id': 'r1', 'name': 'Ronnie Rookie', 'team': '1', 'basis': 'roster_experience_0_no_minutes'}]
    assert fid in fetches


def test_a_listed_rookie_who_never_played_is_identified_from_the_box_and_the_roster(store, monkeypatch):
    rookie_report(store, monkeypatch)
    box = {2027: ({'r1': [av.Appearance('2026-10-19', 2027, '1', 0.0, False)]}, {'r1': 'Ronnie Rookie'})}
    monkeypatch.setattr(al, 'live_box', lambda *a: (box, []))
    roster(store, athletes=[('r1', 'Ronnie Rookie', 0)])
    facts, _ = al.current_facts(store, NOW)
    assert facts['2026-10-21'].absent['1']['all'] == 0.0


@pytest.mark.parametrize('case,match', [
    ('not_on_roster', 'Unresolved unavailable player.*Ronnie Rookie'),
    ('veteran', 'no trustworthy minute history.*Ronnie Rookie'),
    ('questionable', 'no trustworthy minute history.*Ronnie Rookie'),
    ('played_once', 'no trustworthy minute history.*Ronnie Rookie'),
    ('roster_after_cutoff', 'Unresolved unavailable player.*Ronnie Rookie'),
])
def test_genuinely_missing_identity_or_minutes_stays_an_explicit_blocker(store, monkeypatch, case, match):
    rookie_report(store, monkeypatch, status='Questionable' if case == 'questionable' else 'Out')
    box = {2026: ({}, {})}
    if case == 'played_once':
        box = {2027: ({'r1': [av.Appearance('2026-10-19', 2027, '1', 4.0, True)]}, {'r1': 'Ronnie Rookie'})}   # this season
    monkeypatch.setattr(al, 'live_box', lambda *a: (box, []))
    athletes = [] if case == 'not_on_roster' else [('r1', 'Ronnie Rookie', 3 if case == 'veteran' else 0)]
    roster(store, athletes=athletes, at='2026-10-21T22:30:05Z' if case == 'roster_after_cutoff' else '2026-10-21T21:00:00Z')
    with pytest.raises(av.AvailabilityError, match=match):
        al.current_facts(store, NOW)


def test_a_benched_player_with_dashes_and_no_stats_has_zero_minutes_but_any_counted_stat_fails(monkeypatch):
    """espn:401909985: Caleb Love listed with MIN '--', didNotPlay False, reason COACH'S DECISION."""
    game = dict(game_id='espn:g', home_team_id='espn:1', away_team_id='espn:2')
    monkeypatch.setattr(al.espn, 'parse_summary_box', lambda _: SimpleNamespace(completed=True, game_id='espn:g'))
    body = summary()
    stats = body['boxscore']['players'][0]['statistics'][0]
    stats['labels'] = ['MIN', 'PTS']
    for a in stats['athletes']:
        a['stats'] = ['48', '10']
    stats['athletes'].append(dict(athlete=dict(id='99', displayName='Benched Player'), stats=['--', '--'],
                                  didNotPlay=False, reason="COACH'S DECISION"))
    assert al.summary_players(body, game)['1']['99'] == ('Benched Player', 0.)
    stats['athletes'][-1]['stats'] = ['--', '2']
    with pytest.raises(av.AvailabilityError, match='Invalid player minutes'):
        al.summary_players(body, game)
    stats['athletes'][-1]['stats'] = ['--', '--']
    stats['athletes'][-1].pop('reason')
    with pytest.raises(av.AvailabilityError, match='Invalid player minutes'):
        al.summary_players(body, game)
