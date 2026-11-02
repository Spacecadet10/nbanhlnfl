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


def test_inactive_coachs_decision_player_with_dash_minutes_played_zero_minutes(monkeypatch):
    """ESPN summaries since 2026-10-24 (events 401909095, 401909855, 401909877) list unused players with MIN '-' or '--',
    active=false and a reason, but didNotPlay=false. They played 0 minutes; every NBA forecast run failed on them."""
    game=dict(game_id='espn:g',home_team_id='espn:1',away_team_id='espn:2')
    monkeypatch.setattr(al.espn,'parse_summary_box',lambda _:SimpleNamespace(completed=True,game_id='espn:g'))
    body=summary()
    athletes=body['boxscore']['players'][0]['statistics'][0]['athletes']
    for i, dash in ((7, '-'), (8, '--')):
        athletes.append(dict(athlete=dict(id=str(i),displayName=f'Bench {i}'), active=False, starter=False,
                             didNotPlay=False, reason="COACH'S DECISION", ejected=False, stats=[dash]))
    out=al.summary_players(body,game)
    assert out['1']['7']==('Bench 7',0.) and out['1']['8']==('Bench 8',0.)


def test_dash_minutes_without_an_inactive_reason_are_still_invalid(monkeypatch):
    """Missing/malformed minutes are not zero minutes: a dash counts as 0 only when ESPN also says the player was
    inactive with a reason."""
    game=dict(game_id='espn:g',home_team_id='espn:1',away_team_id='espn:2')
    monkeypatch.setattr(al.espn,'parse_summary_box',lambda _:SimpleNamespace(completed=True,game_id='espn:g'))
    for extra in (dict(active=True, reason="COACH'S DECISION"), dict(active=False), dict(), dict(active=False, reason='')):
        body=summary()
        body['boxscore']['players'][0]['statistics'][0]['athletes'].append(
            dict(athlete=dict(id='9',displayName='Bench 9'), didNotPlay=False, stats=['--'], **extra))
        with pytest.raises(av.AvailabilityError,match='Invalid player minutes'):
            al.summary_players(body,game)
    body=summary()
    body['boxscore']['players'][0]['statistics'][0]['athletes'].append(
        dict(athlete=dict(id='9',displayName='Bench 9'), active=False, reason="COACH'S DECISION", stats=['abc']))
    with pytest.raises(av.AvailabilityError,match='Invalid player minutes'):
        al.summary_players(body,game)
