"""Forward-only agreement: cohort integrity, abstentions, retries, failures, and primary isolation."""
from app import agreement, backtest, live
from app.ledger import Forecast, record
from test_live import store, live_rows, T_GAME
CUTOFF = T_GAME


def put(conn, name, p, dataset='a', at='2026-10-21T21:30:00Z', feature=agreement.FEATURE_VERSION):
    return record(conn, Forecast('espn:g1', 'live', f'research-v2:{name}:test', dataset, feature, CUTOFF, at, p))


def test_cohort_missing_members_never_filled_from_old_or_other_policy(store):
    for n in agreement.NAMES['nba']:
        put(store, n, .8)
    put(store, 'elo', .4, 'b', '2026-10-21T22:30:00Z')
    put(store, 'logistic', .9, 'b', feature='other-policy')
    got = agreement.summary(store, 'espn:g1', 'nba', '2026-10-21T22:45:00Z')
    assert got['status'] == 'partial' and got['available'] == 1
    assert got['pick'] == 'away' and got['agreeing'] == 1
    old = agreement.summary(store, 'espn:g1', 'nba', '2026-10-21T21:45:00Z')
    assert old['available'] == 6 and old['pick'] == 'home'


def test_ties_abstain_and_split_vote_has_no_pick(store):
    for n, p in zip(agreement.NAMES['nba'], [.5, .6, .4, .7, .3, .5]):
        put(store, n, p)
    got = agreement.summary(store, 'espn:g1', 'nba', '2026-10-21T22:45:00Z')
    assert got['pick'] is None and got['abstentions'] == 2
    assert got['home_votes'] == got['away_votes'] == 2


def test_no_rows_explicitly_unavailable(store):
    assert agreement.summary(store, 'espn:g1', 'nba', CUTOFF)['status'] == 'unavailable'


def test_recording_retry_and_primary_isolation(store, monkeypatch):
    from app.models.elo import Elo
    _, cfg = backtest.load_frozen_decision(store)
    monkeypatch.setattr(agreement, 'models', lambda _: [Elo(cfg)])
    before = live.predict(store, now='2026-10-21T21:30:00Z')
    primary = live_rows(store)
    dry = agreement.predict(store, now='2026-10-21T21:30:00Z', write=False)
    assert len(dry['forecasts']) == 1 and live_rows(store) == primary
    first = agreement.predict(store, now='2026-10-21T21:30:00Z')
    retry = agreement.predict(store, now='2026-10-21T21:45:00Z')
    assert first['created'] == 1 and retry['created'] == 0 and retry['already_recorded'] == 1
    assert [r for r in live_rows(store) if r['model_version'] == before['model_version']] == primary
    assert agreement.predict(store, now='2026-10-21T23:30:00Z')['created'] == 0


def test_failure_is_visible_and_not_replaced_with_primary(store, monkeypatch):
    from app.models.elo import Elo
    _, cfg = backtest.load_frozen_decision(store)
    class Broken(Elo):
        name = 'broken'
        def predict(self, game):
            raise RuntimeError('unavailable input')
    monkeypatch.setattr(agreement, 'models', lambda _: [Broken(cfg)])
    out = agreement.predict(store, now='2026-10-21T21:30:00Z')
    assert out['status'] == 'failed' and 'unavailable input' in out['errors']['broken']
    assert out['created'] == 0


def test_forward_record_requires_complete_cohort_and_strict_majority(store):
    from test_live import ingest_events, event, T_GAME
    for n, p in zip(agreement.NAMES['nba'], [.8, .6, .7, .3, .5, .8]):
        put(store, n, p)
    ingest_events(store, [event('g1', T_GAME, '1', '2', 'STATUS_FINAL', 110, 100)], '2026-10-22T03:00:00Z')
    got = agreement.performance(store, '2026-10-22T04:00:00Z')['groups'][0]
    assert got['majority']['picks'] == got['majority']['wins'] == 1
    assert got['unanimous']['picks'] == 0
    assert agreement.performance(store, '2026-10-22T02:00:00Z')['groups'][0]['pending'] == 1
    # Missing new cohort must not be repaired with four older votes or count in the record.
    put(store, 'elo', .4, 'new', '2026-10-21T22:30:00Z')
    assert agreement.performance(store, '2026-10-22T04:00:00Z')['groups'] == []


def test_compute_crossing_deadline_records_nothing(store, monkeypatch):
    from app.models.elo import Elo
    _, cfg = backtest.load_frozen_decision(store)
    monkeypatch.setattr(agreement, 'models', lambda _: [Elo(cfg)])
    times = iter(['2026-10-21T23:29:59Z', '2026-10-21T23:30:05Z'])
    monkeypatch.setattr(agreement, 'utc_now', lambda: next(times))
    out = agreement.predict(store)
    assert out['created'] == 0 and out['status'] == 'failed'
    assert not live_rows(store)


def test_two_hour_guard_rejects_early_direct_insert_and_accepts_boundary(store):
    import sqlite3
    import pytest
    with pytest.raises(sqlite3.IntegrityError, match='two-hour'):
        put(store, 'elo', .8, at='2026-10-21T21:29:59Z')
    assert put(store, 'elo', .8, at='2026-10-21T21:30:00Z')[1]


def test_thresholds_keep_pushes_out_of_win_rate_and_show_small_samples():
    from app.tracking import table
    rows = table([(.8,'win'), (.7,'loss'), (.8,'push'), (.9,'pending'), (.9,'void')])
    all_picks = rows[0]
    assert all_picks['settled'] == 2 and all_picks['accuracy'] == .5 and all_picks['coverage'] == 1
    selected = next(r for r in rows if r['threshold'] == .8)
    assert selected['win'] == 1 and selected['loss'] == 0 and selected['push'] == 1
    assert selected['coverage'] == .5 and selected['interval'][0] < .3


def test_two_hour_market_guard_rejects_early_inserts(store):
    import sqlite3
    import pytest
    from app.ledger import MarketForecast, record_market
    f = MarketForecast('nba','espn:g1','total',200.5,'model',None,'near_tipoff','live',
        'test-window=2h-policy=near_tipoff','data','feature',T_GAME,'2026-10-21T21:29:59Z',T_GAME,.6,0,.4)
    with pytest.raises(sqlite3.IntegrityError, match='two-hour'):
        record_market(store, f)


def test_thresholds_keep_different_primary_versions(store):
    from app import tracking
    for version in ['elo-a-window=2h-policy=near_tipoff','elo-b-window=2h-policy=near_tipoff']:
        record(store, Forecast('espn:g1','live',version,'data','primary-two-hour',T_GAME,'2026-10-21T21:30:00Z',.8))
    groups=tracking.report(store,'2026-10-21T22:00:00Z')['groups']
    assert len(groups)==2 and all(g['thresholds'][0]['pending']==1 for g in groups)


def test_agreement_reports_each_definition_separately(store):
    for definition, dataset in [('one','a'),('two','b')]:
        for name in agreement.NAMES['nba']:
            record(store,Forecast('espn:g1','live',f'research-v2:{name}:{definition}',dataset,
                agreement.FEATURE_VERSION,T_GAME,'2026-10-21T21:30:00Z',.8))
    got=agreement.performance(store,'2026-10-21T22:00:00Z')
    assert {g['definition'] for g in got['groups']}=={'one','two'}
    assert all(g['pending']==1 for g in got['groups'])


def test_performance_as_of_ignores_forecasts_made_after_it(monkeypatch):
    """A game whose complete cohort was forecast after `as_of` was not yet forecast at `as_of`: it is not a partial
    cohort in the record as of that time."""
    import sqlite3
    import types
    from app import agreement, live
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE games (game_id TEXT, status TEXT, tipoff_at TEXT, home_score INT, away_score INT, "
                 "result_available_at TEXT)")
    conn.execute("CREATE TABLE predictions (prediction_id INTEGER PRIMARY KEY, game_id TEXT, run_type TEXT, feature_version "
                 "TEXT, model_version TEXT, dataset_version TEXT, generated_at TEXT, cutoff_at TEXT, home_win_prob REAL)")
    conn.execute("INSERT INTO games VALUES ('g1','scheduled','2026-10-20T00:00:00Z',NULL,NULL,NULL)")
    for n in agreement.NAMES["nhl"]:
        conn.execute("INSERT INTO predictions (game_id,run_type,feature_version,model_version,dataset_version,generated_at,"
                     "cutoff_at,home_win_prob) VALUES ('g1','live',?,?,'d1','2026-10-19T23:00:00Z','2026-10-20T00:00:00Z',0.6)",
                     (agreement.FEATURE_VERSION, f"research-v2:{n}:abc"))
    monkeypatch.setattr(live, "engine", lambda c: types.SimpleNamespace(sport="nhl"))
    assert agreement.performance(conn, as_of="2026-10-19T12:00:00Z")["partial_games"] == 0
    after = agreement.performance(conn, as_of="2026-10-19T23:30:00Z")
    assert after["partial_games"] == 0 and after["groups"][0]["games"] == 1
