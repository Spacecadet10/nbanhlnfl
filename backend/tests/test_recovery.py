from app import recovery, near_tipoff
from test_near_tipoff import ready, store, NOW  # noqa: F401
from test_live import event, ingest_events


def test_missed_nba_winner_is_reconstructed_without_changing_live_records(ready, monkeypatch):
    # Record g1 on time, then miss g2. Use the sealed winner while testing other markets separately.
    near_tipoff.predict_now(ready, now=NOW)
    from app import sports
    from dataclasses import replace
    real = sports.get
    monkeypatch.setattr(sports, 'get', lambda s: replace(real(s), markets=('winner',)))
    ingest_events(ready, [event('g2', '2026-10-22T23:30:00Z', '1', '2', 'STATUS_FINAL', 100, 90)], '2026-10-23T03:00:00Z')
    before = ready.execute('select count(*) from predictions').fetchone()[0]
    first = recovery.recover(ready, now='2026-10-23T04:00:00Z')
    assert first['created'] == 1 and not first['errors'], first
    row = ready.execute('select * from recovered_forecasts').fetchone()
    assert row['game_id'] == 'espn:g2' and row['cutoff_at'] < row['tipoff_at'] <= row['reconstructed_at']
    assert row['latest_result_at'] <= row['cutoff_at']
    assert recovery.recover(ready, now='2026-10-23T05:00:00Z')['created'] == 0
    assert ready.execute('select count(*) from predictions').fetchone()[0] == before
    report = recovery.report(ready, '2026-10-23T05:00:00Z')
    assert len(report['records']) == 1 and report['records'][0]['outcome'] in ('win','loss')


def test_nhl_all_markets_recover_with_no_odds_and_ignore_own_outcome(sealed, monkeypatch):
    from test_live_markets import add_upcoming
    from test_research_live import GID
    add_upcoming(sealed, start='2026-10-12T19:00:00', at='2026-10-12T20:30:00Z')
    sealed.execute("INSERT INTO job_runs(job,started_at,finished_at,status) VALUES('refresh','2026-10-12T21:00:00Z','2026-10-12T21:00:00Z','success')")
    near_tipoff.predict_now(sealed, now='2026-10-12T21:30:00Z')
    add_upcoming(sealed, gid=2026020002, start='2026-10-13T19:00:00', state=7, hs=4, vs=2, period=3, at='2026-10-14T03:00:00Z')
    import sqlite3
    clone = sqlite3.connect(':memory:', isolation_level=None)
    clone.row_factory = sqlite3.Row
    sealed.backup(clone)
    clone.execute("UPDATE games SET home_score=0, away_score=10 WHERE game_id='nhl:2026020002'")
    recovery.recover(clone, now='2026-10-14T04:00:00Z')
    out = recovery.recover(sealed, now='2026-10-14T04:00:00Z')
    assert not out['errors'], out
    rows = sealed.execute("select * from recovered_forecasts where game_id='nhl:2026020002'").fetchall()
    assert len(rows) == 6
    altered = {r['market']: (r['p_a'], r['p_mid'], r['p_b']) for r in clone.execute("SELECT * FROM recovered_forecasts WHERE game_id='nhl:2026020002'")}
    assert altered == {r['market']: (r['p_a'], r['p_mid'], r['p_b']) for r in rows}
    clone.close()
    assert all(r['quote_id'] is None and r['line_source'] in ('none','model') for r in rows)
    assert all(r['latest_result_at'] < '2026-10-13T23:00:00Z' for r in rows)
    assert not recovery.report(sealed, '2026-10-13T22:00:00Z')['records']

from test_live_markets import sealed  # noqa: F401, E402
from test_nhl_eval import nhl_store  # noqa: F401, E402


def test_periodic_cycle_finishes_live_work_before_recovery(monkeypatch):
    from app import jobs
    calls = []
    def live_cycle(conn):
        calls.append('live')
        return {'status': 'skipped', 'reason': 'no upcoming game'}
    def catch_up(conn):
        calls.append('recovery')
        return {'status': 'success', 'created': 6}
    monkeypatch.setattr(jobs, '_forecast_due', live_cycle)
    monkeypatch.setattr(jobs, 'recover_missed', catch_up)
    result = jobs.forecast_due(None)
    assert calls == ['live', 'recovery']
    assert result['recovery']['created'] == 6
