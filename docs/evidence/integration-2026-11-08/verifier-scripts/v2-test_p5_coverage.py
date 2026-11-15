"""Item 5 independent reproduction (scratch). Network blocked; fixtures reused from backend/tests."""
import socket
def _blocked(*a, **k): raise RuntimeError('network blocked')
socket.socket.connect = _blocked; socket.create_connection = _blocked
import sys
sys.path.insert(0, '/Users/andry/Desktop/nba-predictor-sprint1/backend/tests')
import json
import pytest
from app import adaptive, coverage
from test_adaptive import CYCLE_AT, TRAIN_AT, store  # noqa: F401
from test_live_markets import add_upcoming, sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401
from test_research_live import GID, game, run  # noqa: F401
from test_coverage import _preseason, _no_network, PRE


@pytest.fixture(autouse=True)
def _no_pbp(monkeypatch):
    from app.sources import nhl_pbp
    monkeypatch.setattr(nhl_pbp, 'fetch', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('net')))


def _count(c, gid=None):
    q = "SELECT COUNT(*) FROM game_forecasts" + (" WHERE game_id=?" if gid else "")
    return c.execute(q, (gid,) if gid else ()).fetchone()[0]


def _latest(c, gid, market):
    return c.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market=? ORDER BY generated_at DESC, forecast_id DESC LIMIT 1",
                     (gid, market)).fetchone()


def test_5b_explanation_only_then_substantive(store):
    run(store, CYCLE_AT)
    a = coverage.record(store, now=CYCLE_AT)
    n1 = _count(store, GID)
    reasons_a = {r['market']: r['fallback_reason'] for r in store.execute("SELECT * FROM game_forecasts WHERE game_id=?", (GID,))}
    print('A created', a['created'], 'rows', n1, 'sample reason A:', reasons_a['winner'])
    adaptive.train(store, now=TRAIN_AT)
    assert adaptive.record(store, game_ids=[GID], now=CYCLE_AT)['created'] == 6
    b = coverage.record(store, now='2026-10-12T21:31:00Z')
    new_reason = {r['market']: r['fallback_reason'] for r in b['rows'] if r['game_id'] == GID}
    print('B created', b['created'], 'already', b['already_recorded'], 'reason B (computed):', new_reason['winner'])
    assert new_reason['winner'] != reasons_a['winner']        # the explanation really changed
    assert b['created'] == 0 and _count(store, GID) == n1     # no new identity
    assert _latest(store, GID, 'winner')['fallback_reason'] == reasons_a['winner']   # stored row keeps EARLY explanation
    base = dict(_latest(store, GID, 'spread'))
    base.pop('forecast_id'); base.pop('idempotency_key')
    def ins(**chg):
        row = {**base, **chg}
        row['generated_at'] = chg.get('generated_at', '2026-10-12T21:32:00Z')
        return coverage._insert(store, row)
    assert ins(fallback_reason='only explanation C')[1] is False
    results = {}
    results['probability'] = ins(p_a=base['p_a'] - 0.01, p_b=base['p_b'] + 0.01)[1]
    results['line'] = ins(p_a=base['p_a'] - 0.01, p_b=base['p_b'] + 0.01, line=base['line'] + 1)[1]
    results['model'] = ins(p_a=base['p_a'] - 0.01, p_b=base['p_b'] + 0.01, line=base['line'] + 1, model_version='other')[1]
    results['source'] = ins(p_a=base['p_a'] - 0.01, p_b=base['p_b'] + 0.01, line=base['line'] + 1, model_version='other',
                            source='primary_direct', source_table=None, source_id=None)[1]
    miss = dict(status='missing', source='none', reason='r1', line_source='none', p_a=None, p_mid=None, p_b=None, line=None,
                model_version=None, source_table=None, source_id=None, fallback_reason=None)
    results['to_missing'] = coverage._insert(store, {**base, **miss, 'generated_at': '2026-10-12T21:33:00Z'})[1]
    results['missing_same_retry'] = coverage._insert(store, {**base, **miss, 'generated_at': '2026-10-12T21:34:00Z'})[1]
    results['missing_reason_change'] = coverage._insert(store, {**base, **miss, 'reason': 'r2', 'generated_at': '2026-10-12T21:35:00Z'})[1]
    print('substantive created flags', results)
    assert results == dict(probability=True, line=True, model=True, source=True, to_missing=True,
                           missing_same_retry=False, missing_reason_change=True)
    trig = store.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='game_forecasts'").fetchall()
    print('game_forecasts triggers', [t[0] for t in trig])


def _cycle_truth(c):
    return {(r['game_id'], r['market']): r['forecast_id'] for r in c.execute(
        "SELECT g.* FROM game_forecasts g WHERE forecast_id = (SELECT forecast_id FROM game_forecasts h WHERE h.game_id=g.game_id "
        "AND h.market=g.market ORDER BY generated_at DESC, forecast_id DESC LIMIT 1)")}


def test_5c_counts_when_final_adds_new_row(game, monkeypatch):
    jobs = _no_network(monkeypatch)
    _preseason(game)
    first = jobs.predict_now(game, now=CYCLE_AT)
    print('cycle1 coverage', first['coverage'], 'early', first.get('early_coverage'))
    assert first['coverage']['created'] == _count(game) == 12 and first['coverage']['already_recorded'] == 0
    T2 = '2026-10-12T21:40:00Z'
    before_n, before = _count(game), _cycle_truth(game)
    def inject(conn, market, key, delta):
        cols = [r[1] for r in conn.execute('PRAGMA table_info(market_forecasts)')]
        r = dict(conn.execute("SELECT * FROM market_forecasts WHERE game_id=? AND market=? AND model_version NOT LIKE 'research-%' "
                              "ORDER BY generated_at DESC, forecast_id DESC LIMIT 1", (GID, market)).fetchone())
        r.update(forecast_id=None, idempotency_key=key, generated_at=T2, p_a=r['p_a'] - delta, p_b=r['p_b'] + delta)
        conn.execute(f"INSERT INTO market_forecasts ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", [r[k] for k in cols])
    orig_m, orig_r = jobs.market_forecasts, jobs.research_forecasts
    def markets(conn, **kw):            # before the EARLY pass: the early pass creates a new 'total' row
        out = orig_m(conn, **kw); inject(conn, 'total', 'scratch-early', 0.01); return out
    def research(conn, **kw):           # between early and final: the final pass creates another new 'total' version
        out = orig_r(conn, **kw); inject(conn, 'total', 'scratch-final', 0.02); return out
    monkeypatch.setattr(jobs, 'market_forecasts', markets)
    monkeypatch.setattr(jobs, 'research_forecasts', research)
    second = jobs.predict_now(game, now=T2)
    after_n, after = _count(game), _cycle_truth(game)
    truth_created = after_n - before_n
    truth_unchanged_preexisting = sum(1 for k, v in after.items() if before.get(k) == v)
    print('cycle2 early', second.get('early_coverage'), 'final(reported)', {k: second['coverage'][k] for k in ('created', 'already_recorded')})
    print('cycle2 truth: rows inserted', truth_created, 'pre-existing unchanged slots', truth_unchanged_preexisting)
    assert second['coverage']['created'] == truth_created
    assert second['coverage']['already_recorded'] == truth_unchanged_preexisting


def test_5c_early_raises(game, monkeypatch):
    jobs = _no_network(monkeypatch)
    calls = []
    real = coverage.record
    def flaky(conn, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('early coverage boom')
        return real(conn, **kw)
    monkeypatch.setattr(jobs.coverage, 'record', flaky)
    try:
        out = jobs.predict_now(game, now=CYCLE_AT)
        print('predict_now returned', out.get('status'), out.get('coverage'))
    except Exception as exc:
        out = None
        print('predict_now raised', type(exc).__name__, exc)
    runs = game.execute("SELECT run_id, job, status, finished_at FROM job_runs WHERE job='near_tipoff'").fetchall()
    print('near_tipoff job_runs', [tuple(r) for r in runs], 'coverage rows', _count(game), 'record calls', len(calls))
    assert out is not None, 'early coverage exception escapes predict_now: research + final coverage skipped, job_runs left running'
