import copy
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from app import adaptive, combination_thresholds as ct


def rows(models=12, games=180):
    return [{"game_id": f"g{i}", "season": 2026, "tipoff_at": f"2026-01-{1 + i // 6:02d}T23:00:00Z",
             "cutoff_at": f"2026-01-{1 + i // 6:02d}T12:00:00Z",
             "result_available_at": f"2026-01-{2 + i // 6:02d}T02:00:00Z", "line": None,
             "realised": 2 if i % 8 == 0 else 0,
             "vectors": {f"m{j}": [0.70 + j * 0.01, 0, 0.30 - j * 0.01] for j in range(models)}}
            for i in range(games)]


def table(data):
    t = ct.Table("winner", data, sorted(data[0]["vectors"]), later_seasons=[])
    split = len(t.rows) * 2 // 3
    t.periods = {"earlier": np.arange(len(t.rows)) < split, "later": np.arange(len(t.rows)) >= split}
    return t


def test_large_search_reaches_every_size_with_a_real_budget():
    result = ct.search_large_subsets(table(rows()), budget_per_size=32, beam_width=3, min_picks=20)
    assert result["search"]["sizes"] == list(range(2, 13))
    assert not result["search"]["exhaustive"]
    assert all(0 < count <= 32 for count in result["search"]["evaluated_by_size"].values())
    assert {len(r["members"]) for r in result["rules"]} == set(range(2, 13))
    assert any(len(r["members"]) == 12 for r in result["rules"])
    assert any(len(set(r["thresholds"])) > 1 for r in result["rules"])


def test_large_search_freezes_before_any_later_presence_or_result_changes():
    data = rows()
    before = ct.search_large_subsets(table(data), budget_per_size=32, beam_width=3, min_picks=20)
    changed = copy.deepcopy(data)
    for row in changed[120:]:
        row["realised"] = 2
        row["vectors"].pop("m5")
    after = ct.search_large_subsets(table(changed), budget_per_size=32, beam_width=3, min_picks=20)
    signature = lambda r: [(x["members"], x["thresholds"]) for x in r["rules"]]
    assert signature(before) == signature(after)
    assert before["search"] == after["search"]
    assert before["rules"][0]["later"] != after["rules"][0]["later"]


def test_daily_cadence_is_scoped_and_thread_isolated():
    weekly = adaptive.period_of("2026-10-12T23:00:00Z", 2027)
    with adaptive.cadence("daily"):
        assert adaptive.period_of("2026-10-12T23:00:00Z", 2027) == "2027:2026-10-12"
        assert adaptive.period_of("2026-10-13T23:00:00Z", 2027) != "2027:2026-10-12"
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(adaptive.period_of, "2026-10-12T23:00:00Z", 2027).result() == weekly
    assert adaptive.period_of("2026-10-12T23:00:00Z", 2027) == weekly
    with pytest.raises(ValueError):
        with adaptive.cadence("hourly"):
            pass


def test_daily_assessment_excludes_results_at_or_after_cutoff():
    from app import daily_agreement as da
    data = rows(games=180)
    cutoff = '2026-01-21T00:00:00Z'
    before = da.assess('winner', data, cutoff)
    changed = copy.deepcopy(data)
    for r in changed:
        if r['result_available_at'] >= cutoff:
            r['realised'] = 0
            r['vectors'] = {'future_only': [.99, 0, .01]}
    assert da.assess('winner', changed, cutoff) == before
    assert 'future_only' not in (before['search'] or {}).get('models', [])


def test_frozen_unanimous_rule_abstains_without_all_members_and_thresholds():
    from app import daily_agreement as da
    decision = {'rule': {'members': ['a', 'b'], 'thresholds': [.6, .7], 'rule': 'a@.6,b@.7'}}
    assert da.apply(decision, {'a': [.65, 0, .35]}, 'winner')['status'] == 'no_pick'
    assert da.apply(decision, {'a': [.65, 0, .35], 'b': [.69, 0, .31]}, 'winner')['status'] == 'no_pick'
    assert da.apply(decision, {'a': [.65, 0, .35], 'b': [.25, 0, .75]}, 'winner')['status'] == 'no_pick'
    pick = da.apply(decision, {'a': [.65, 0, .35], 'b': [.75, 0, .25]}, 'winner')
    assert pick['side'] == 0 and pick['status'] == 'pick'
    assert da.apply(decision, {'a': [.5, 0, .5], 'b': [.75, 0, .25]}, 'winner')['status'] == 'no_pick'


def test_embargo_removes_late_results_from_search_period():
    from app import daily_agreement as da
    data = rows()
    data[0]['result_available_at'] = '2026-02-01T00:00:00Z'
    t = da.make_table('winner', data)
    assert not t.periods['earlier'][0]
    assert not (t.periods['earlier'] & t.periods['later']).any()


def test_empty_high_thresholds_are_reported():
    t = table(rows())
    ladder = ct.thresholds(t, t.models)
    assert ladder['levels'][-1] == .9
    for source in ladder['sources']:
        assert source['later']['cumulative'][-1]['picks'] == 0
        assert source['later']['bands'][-1]['picks'] == 0


def test_daily_nba_cycle_trains_records_settles_and_reassesses(monkeypatch, tmp_path):
    from test_adaptive import test_a_new_week_with_new_results_is_trained_and_published_for_nba
    from app import db, daily_agreement as da
    with adaptive.cadence('daily'):
        test_a_new_week_with_new_results_is_trained_and_published_for_nba(monkeypatch, tmp_path)
        conn = db.connect(tmp_path / 'nba.sqlite')
        fit = adaptive.latest_fit(conn, 'nba', 'combiner', 'combiner', adaptive.definition(conn))
        art = adaptive.load_fit(fit)
        assert fit['schedule'] == 'daily'
        assert set(art['daily_agreement']) == {'winner', 'spread', 'total', 'team_total_home', 'team_total_away'}
        assert art['agreement_history']['winner']
        import json
        saved = conn.execute('SELECT selection FROM adaptive_forecasts').fetchall()
        assert saved and all('daily_agreement' in json.loads(r[0]) for r in saved)
        # Same fit, same inputs after the window cannot append a late forecast.
        before = conn.execute('SELECT count(*) FROM adaptive_forecasts').fetchone()[0]
        adaptive.record(conn, now='2026-10-22T04:00:00Z')
        assert conn.execute('SELECT count(*) FROM adaptive_forecasts').fetchone()[0] == before
        report = da.report(conn, '2026-10-22T05:00:00Z')
        assert len(report['markets']) == 5
        assert all(m['history_games'] > 0 for m in report['markets'])
        assert report['fit']['fit_id'] == fit['fit_id']
        conn.close()


# Reuse the existing isolated NHL store and clock; no real-data training.
from test_adaptive import store, TRAIN_AT, CYCLE_AT, _settle  # noqa: E402,F401
from test_live_markets import sealed  # noqa: E402,F401
from test_nhl_eval import nhl_store  # noqa: E402,F401


def test_daily_nhl_all_markets_idempotency_and_real_api(store, monkeypatch):
    import json
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app import daily_agreement as da
    from app.main import app, get_provider
    from app.candidates import registry
    from test_research_live import run, GID
    # Small non-neural fixture roster keeps verification inexpensive; production roster is unchanged.
    monkeypatch.setattr(registry, 'SPECS', tuple(s for s in registry.SPECS if not s.name.startswith('torch')))
    with adaptive.cadence('daily'):
        trained = adaptive.train(store, now=TRAIN_AT)
        assert trained['status'] in ('success', 'partial'), trained
        fit = adaptive.latest_fit(store, 'nhl', 'combiner', 'combiner', adaptive.definition(store))
        art = adaptive.load_fit(fit)
        assert len(art['daily_agreement']) == 6
        run(store, CYCLE_AT)
        first = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
        assert len(first['forecasts']) == 6
        second = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
        assert second['already_recorded'] == 6 and second['created'] == 0
        assert store.execute('SELECT count(*) FROM adaptive_forecasts WHERE definition=?', (trained['definition'],)).fetchone()[0] == 6
        assert all('daily_agreement' in json.loads(r[0]) for r in store.execute('SELECT selection FROM adaptive_forecasts'))
        # Freeze a synthetic rule using only already-known pregame member probabilities.
        # This tests append-only rule versions and settlement independently of search-quality gates.
        winner = store.execute("SELECT * FROM adaptive_forecasts WHERE market='winner'").fetchone()
        by_side = {0: [], 2: []}
        for member in json.loads(winner['members']):
            v = member['p']
            by_side[0 if v[0] > v[2] else 2].append(member['name'])
        side, names = next((side, ns[:2]) for side, ns in by_side.items() if len(ns) >= 2)
        art['daily_agreement']['winner']['rule'] = {'members': names, 'thresholds': [.5, .5], 'rule': ','.join(f'{n}@0.5' for n in names)}
        from test_adaptive import _test_clock
        monkeypatch.setattr(adaptive, 'utc_now', _test_clock('2026-10-12T21:35:00Z', '2026-10-12T21:39:00Z'))
        from pathlib import Path
        path, sha = adaptive._save(Path(fit['artifact_path']).with_name('synthetic-rule.pkl'), art)
        newer_id = adaptive._insert_fit(store, 'nhl', 'combiner', 'combiner', trained['definition'], fit['period'],
                                       'success', '2026-10-12T21:35:00Z', {}, cutoff=fit['training_cutoff'], path=str(path), sha=sha)
        third = adaptive.record(store, game_ids=[GID], now='2026-10-12T21:40:00Z')
        assert third['created'] + third['unavailable'] == 6
        saved = store.execute("SELECT selection FROM adaptive_forecasts WHERE market='winner' AND combiner_fit_id=?", (newer_id,)).fetchone()
        assert json.loads(saved[0])['daily_agreement']['status'] == 'pick'
        assert store.execute('SELECT count(*) FROM adaptive_forecasts').fetchone()[0] == 12
    _settle(store)
    as_of = '2026-10-13T04:00:00Z'
    report = da.report(store, as_of)
    assert len(report['markets']) == 6
    winner_record = next(m['prospective'] for m in report['markets'] if m['market'] == 'winner')
    assert winner_record['picks'] == 1 and winner_record['wins'] + winner_record['losses'] == 1
    assert all(m['history_games'] > 0 for m in report['markets'])
    old = app.dependency_overrides.copy()
    import sqlite3
    path = store.execute('PRAGMA database_list').fetchone()[2]
    api_conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
    api_conn.row_factory = sqlite3.Row
    app.dependency_overrides[get_provider] = lambda: SimpleNamespace(conn=api_conn, now=as_of)
    try:
        client = TestClient(app)
        response = client.get('/api/research/daily-agreement?sport=nhl')
        assert response.status_code == 200, response.text
        assert len(response.json()['markets']) == 6
        m = report['markets'][0]
        name = m['thresholds']['sources'][0]['key']
        evidence = client.get('/api/research/daily-agreement/evidence', params={'market': m['market'], 'rule': f'{name}@0.5'})
        assert evidence.status_code == 200, evidence.text
        assert len(evidence.json()['ladder']['cumulative']) == len(m['levels'])
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(old)
        api_conn.close()


def test_daily_training_survives_an_unreadable_previous_combiner_artifact(monkeypatch, tmp_path):
    """Only the daily path reads the previous combiner ARTIFACT (for the incumbent rule). A corrupt or missing file must
    cost the incumbent, visibly, never the day's training: otherwise one bad file fails every later daily run."""
    from pathlib import Path
    from test_adaptive import test_a_new_week_with_new_results_is_trained_and_published_for_nba, _test_clock
    from test_live import event, ingest_events
    from app import db
    with adaptive.cadence('daily'):
        test_a_new_week_with_new_results_is_trained_and_published_for_nba(monkeypatch, tmp_path)
        conn = db.connect(tmp_path / 'nba.sqlite')
        defn = adaptive.definition(conn)
        prev = adaptive.latest_fit(conn, 'nba', 'combiner', 'combiner', defn)
        Path(prev['artifact_path']).write_bytes(b'not the published artifact')     # the digest check now rejects it
        with pytest.raises(Exception):
            adaptive.load_fit(prev)
        ingest_events(conn, [event('g2', '2026-10-28T23:30:00Z', '3', '1', 'STATUS_FINAL', 101, 97)], '2026-10-29T03:00:00Z')
        ingest_events(conn, [event('g3', '2026-11-04T23:30:00Z', '2', '3')], '2026-10-29T03:30:00Z')
        monkeypatch.setattr(adaptive, 'utc_now', _test_clock('2026-10-29T04:00:00Z', '2026-10-29T05:00:00Z'))
        third = adaptive.run_scheduled('nba', now='2026-10-29T04:00:00Z')
        assert third['status'] == 'success', third
        fit = adaptive.latest_fit(conn, 'nba', 'combiner', 'combiner', defn)
        assert fit['fit_id'] != prev['fit_id']
        art = adaptive.load_fit(fit)
        assert set(art['daily_agreement']) == {'winner', 'spread', 'total', 'team_total_home', 'team_total_away'}
        assert str(prev['fit_id']) in art['daily_incumbent_error']        # the lost incumbent is recorded, not hidden
        conn.close()


def test_three_way_market_is_not_rejected_by_the_two_way_break_even_floor():
    """A sizeable sub-50% season must reject a TWO-way rule (below break-even) but not a THREE-way one (the draw is a
    third outcome, so 50% is not break-even). Before the market-aware floor, reg3 rules were silently all rejected."""
    from app import daily_agreement as da
    rule = {'later': {'record': {'wins': 40, 'losses': 20, 'coverage': 0.2, 'win_rate': 40 / 60,
                                 'win_rate_ci95': [0.55, 0.78]},
                      'coverage_matched_best_single': {'win_rate': 0.60},
                      'by_season': [{'wins': 8, 'losses': 12, 'win_rate': 0.40}]}}
    assert da._score(rule, 'winner') is None          # two-way: a 40% season is below break-even -> rejected
    assert da._score(rule, 'reg3') == 0.55             # three-way: no 50% floor -> selectable on the other gates


def test_apply_is_pick_for_pick_consistent_with_the_searched_rule_semantics():
    """The frozen rule applied pregame must select exactly the games the search counted: every member present, one
    shared side, each member at its own threshold (confidence among the market's allowed sides), ties abstaining."""
    from app import daily_agreement as da
    rng = np.random.default_rng(3)
    for market in ('winner', 'reg3', 'spread'):
        allowed, grid = ct.combine.sides(market), ct._levels(market)
        data = []
        for i in range(300):
            vecs = {}
            for name in ('a', 'b', 'c'):
                if rng.random() < 0.1:
                    continue                                                    # member missing
                v = rng.dirichlet([1, 1, 1]) if market == 'reg3' else np.array([p := rng.random(), 0.0, 1 - p])
                if rng.random() < 0.05:                                         # exact tie of the leading allowed sides
                    v = np.array([0.4, 0.4, 0.2]) if market == 'reg3' else np.array([0.5, 0.0, 0.5])
                if rng.random() < 0.05:                                         # exactly on a grid value
                    g = float(rng.choice(grid))
                    v = np.array([g, (1 - g) / 2, (1 - g) / 2]) if market == 'reg3' else np.array([g, 0.0, 1 - g])
                vecs[name] = tuple(float(x) for x in v)
            data.append({'game_id': f'g{i}', 'season': 2025, 'tipoff_at': f'2025-01-{1 + i // 20:02d}T23:00:00Z',
                         'cutoff_at': f'2025-01-{1 + i // 20:02d}T05:00:00Z', 'line': None,
                         'result_available_at': f'2025-01-{2 + i // 20:02d}T02:00:00Z',
                         'realised': int(rng.choice(list(allowed))), 'vectors': vecs})
        t = ct.Table(market, data, ['a', 'b', 'c'], later_seasons=[])
        for members in (['a', 'b'], ['a', 'b', 'c'], ['c', 'a']):
            for _ in range(10):
                ths = [float(rng.choice(grid)) for _ in members]
                _, side, qual = ct._qualifying(t, members, ths)
                decision = {'rule': {'members': members, 'thresholds': ths, 'rule': 'x'}}
                for i, r in enumerate(t.rows):
                    out = da.apply(decision, r['vectors'], market)
                    assert (out['status'] == 'pick') == bool(qual[i]), (market, members, ths, r['vectors'], out)
                    assert out['status'] != 'pick' or out['side'] == side[i]


def _cutoff_rows(models=8, games=240, seed=11):
    """Rows with realistic times: eight games per Eastern date (cutoff = midnight ET), two of them finishing after the
    next midnight, so a result can arrive after the next day's cutoff."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(games):
        day, late = 1 + i // 8, (i % 8) >= 6
        truth = rng.random() < 0.55
        vecs = {}
        for j in range(models):
            if rng.random() < 0.08:
                continue
            p = float(np.clip((0.62 if truth else 0.38) + rng.normal(0, 0.12 + 0.02 * j), 0.02, 0.98))
            vecs[f'm{j}'] = (p, 0.0, 1 - p)
        out.append({'game_id': f'g{i:04d}', 'season': 2026, 'tipoff_at': f'2026-01-{day:02d}T{19 + i % 4:02d}:00:00Z',
                    'cutoff_at': f'2026-01-{day:02d}T05:00:00Z', 'line': None, 'realised': 0 if truth else 2,
                    'result_available_at': f'2026-01-{day + 1:02d}T{"06" if late else "01"}:30:00Z', 'vectors': vecs})
    return out


def test_make_table_periods_are_disjoint_aligned_and_embargo_late_results():
    from app import daily_agreement as da
    rows = _cutoff_rows()
    t = da.make_table('winner', rows)
    srt = sorted(rows, key=lambda r: (r['tipoff_at'], r['game_id']))
    assert [r['game_id'] for r in t.rows] == [r['game_id'] for r in srt]           # masks are aligned with the table rows
    boundary = srt[len(srt) * 2 // 3]['cutoff_at']
    early, later = t.periods['earlier'], t.periods['later']
    assert not (early & later).any()
    assert all(r['cutoff_at'] >= boundary for r, ok in zip(t.rows, later) if ok)
    assert all(r['tipoff_at'] < boundary and r['result_available_at'] < boundary for r, ok in zip(t.rows, early) if ok)
    dropped = [r for r, a, b in zip(t.rows, early, later) if not a and not b]
    assert dropped and all(r['result_available_at'] >= boundary > r['tipoff_at'] for r in dropped)   # the embargo band
    with_late_only = [dict(r, vectors={**r['vectors'], **({'late_only': (0.9, 0.0, 0.1)} if r['cutoff_at'] >= boundary else {})})
                      for r in rows]
    assert 'late_only' not in da.make_table('winner', with_late_only).models    # a later-only member never enters the roster


def test_assess_frozen_rules_and_search_are_invariant_to_later_period_changes():
    """Which rules are frozen (and their earlier records, the search log, the baseline model and the collapse count) is
    decided on the earlier period only: flipping every later outcome and removing a member there changes nothing of it.
    The CHOICE among the frozen rules does use the later period (the disclosed selection bias)."""
    from app import daily_agreement as da
    rows = _cutoff_rows(models=6, games=300)
    before = da.assess('winner', rows, '2026-03-01T00:00:00Z')
    srt = sorted(rows, key=lambda r: (r['tipoff_at'], r['game_id']))
    boundary = srt[len(srt) * 2 // 3]['cutoff_at']
    changed = copy.deepcopy(rows)
    for r in changed:
        if r['cutoff_at'] >= boundary:
            r['realised'] = 2 if r['realised'] == 0 else 0
            r['vectors'].pop('m2', None)
    after = da.assess('winner', changed, '2026-03-01T00:00:00Z')
    frozen = lambda d: [(x['members'], x['thresholds'], x['earlier']['record']) for x in d['rules']]
    assert before['rules'] and frozen(before) == frozen(after)
    assert before['search'] == after['search'] and before['best_single'] == after['best_single']
    assert before['equivalent_earlier_rules_collapsed'] == after['equivalent_earlier_rules_collapsed']
    assert [x['later']['record'] for x in before['rules']] != [x['later']['record'] for x in after['rules']]


def test_assess_reports_pairwise_member_pick_overlap():
    """A member built on another's inputs (players_injuries_v1 extends logistic_v2) agrees with it largely by construction,
    so their unanimity is weak evidence. The earlier-period pairwise pick overlap is reported next to the search."""
    from app import daily_agreement as da
    data = rows(models=4)
    out = da.assess('winner', data, '2026-02-15T00:00:00Z')
    overlap = out['member_overlap']
    assert overlap['period'] == 'earlier' and len(overlap['pairs']) == 6
    assert all(p['share'] == 1.0 and p['games'] > 0 for p in overlap['pairs'])
    for r in data[::2]:
        r['vectors']['m1'] = [0.3, 0, 0.7]                                  # m1 disagrees on every other game
    out = da.assess('winner', data, '2026-02-15T00:00:00Z')
    pair = next(p for p in out['member_overlap']['pairs'] if {p['a'], p['b']} == {'m0', 'm1'})
    assert 0.4 < pair['share'] < 0.6 and pair['same_side'] < pair['games']
    assert 'not independent' in out['member_overlap']['note']


def test_weekly_game_view_shows_only_weekly_rows_and_daily_only_daily(store):
    """With NBA_DAILY_AGREEMENT=1 both cadences record the same game at the same generated_at; the daily row has the
    higher forecast_id. The weekly game view must not show the daily definition (and vice versa)."""
    import json
    conn = store
    g = conn.execute("SELECT * FROM games WHERE status='scheduled' ORDER BY tipoff_at LIMIT 1").fetchone()
    at = (np.datetime64(g['tipoff_at'].replace('Z', '')) - np.timedelta64(90, 'm')).astype(str) + 'Z'
    def insert(defn, selection, key):
        conn.execute("INSERT INTO adaptive_forecasts (game_id, sport, market, line, line_source, odds_status, model_version, "
                     "definition, status, reason, members, missing, weights, selection, information_at, generated_at, tipoff_at, "
                     "idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (g['game_id'], 'nhl', 'winner', None, 'none', 'unavailable: test', f'adaptive-v1:combiner:{defn}', defn,
                      'unavailable', 'no fit', '[]', '{}', 'null', json.dumps(selection), at, at, g['tipoff_at'], key))
    insert('weekly-def', {'status': 'no_pick', 'reason': 'x', 'side': None, 'probability': None}, 'k-weekly')
    insert('daily-def', {'status': 'no_pick', 'reason': 'x', 'side': None, 'probability': None,
                         'daily_agreement': {'status': 'no_pick'}}, 'k-daily')
    weekly = adaptive.game_detail(conn, g['game_id'], at)
    assert [m['definition'] for m in weekly['markets']] == ['weekly-def']
    with adaptive.cadence('daily'):
        daily = adaptive.game_detail(conn, g['game_id'], at)
    assert [m['definition'] for m in daily['markets']] == ['daily-def']
    assert 'daily_agreement' in daily['markets'][0]['selection'] and 'daily_agreement' not in weekly['markets'][0]['selection']


def test_assess_reports_member_overlap_even_when_evidence_is_insufficient():
    """Review finding D1: the early insufficient_evidence return omitted member_overlap, so a decision could be shown
    without the pseudo-agreement disclosure. Every decision carries it (no pairs when no table could be built)."""
    from app import daily_agreement as da
    none = da.assess('winner', [], '2026-02-15T00:00:00Z')
    assert none['status'] == 'insufficient_evidence' and none['rule'] is None
    assert none['member_overlap'] == {'period': 'earlier', 'pairs': [], 'note': ct.MEMBER_OVERLAP_NOTE}
    single = da.assess('winner', rows(models=1), '2026-02-15T00:00:00Z')        # one model: no search, still the key
    assert single['status'] == 'insufficient_evidence' and single['member_overlap']['pairs'] == []
    assert 'not independent' in single['member_overlap']['note']


def test_apply_rejects_truncated_or_nonfinite_rule_thresholds():
    from app import daily_agreement as daily
    vectors = {'a': (.9, 0., .1), 'b': (.1, 0., .9)}
    for thresholds in ([.6], [.6, float('nan')]):
        decision = {'rule': {'members': ['a', 'b'], 'thresholds': thresholds}}
        # A truncated zip must not turn a two-member rule into a single-member pick.
        assert daily.apply(decision, vectors, 'winner')['status'] != 'pick'
    decision = {'rule': {'members': ['a'], 'thresholds': [float('nan')]}}
    assert daily.apply(decision, vectors, 'winner')['status'] != 'pick'


def test_apply_rejects_invalid_vectors_across_all_markets():
    from app import daily_agreement as daily
    for market in ('winner', 'spread', 'total', 'team_total_home', 'team_total_away', 'reg3'):
        for bad in ((.9, float('nan'), .1), (.9, 0., float('inf')), (.9, .3, -.2), (.2, .2, .2)):
            decision = {'rule': {'members': ['a', 'b'], 'thresholds': [.5, .5]}}
            result = daily.apply(decision, {'a': bad, 'b': (.9, 0., .1)}, market)
            assert result['status'] == 'no_pick'
            assert 'Invalid probability' in result['reason']


def test_a_final_pick_that_can_never_be_scored_is_unscorable_not_pending():
    """A final reg3 game without a stored regulation decision can never be scored: it is counted 'unscorable' (as in
    tracking-v2), never left 'pending' forever. Scorable, pushed and void picks keep their outcomes."""
    from app import daily_agreement as da
    base = {'tipoff_at': 't', 'current_tipoff': 't', 'game_status': 'final', 'result_available_at': '2026-10-13T03:00:00Z',
            'line': None, 'home_score': 3, 'away_score': 2, 'decision': None}
    as_of = '2026-10-13T04:00:00Z'
    assert da._outcome('reg3', base, {'side': 0}, as_of) == 'unscorable'
    assert da._outcome('reg3', {**base, 'decision': 'REG'}, {'side': 0}, as_of) == 'wins'
    assert da._outcome('total', {**base, 'line': 5.0}, {'side': 0}, as_of) == 'pushes'
    assert da._outcome('reg3', {**base, 'game_status': 'in_progress'}, {'side': 0}, as_of) == 'pending'
    assert da._outcome('reg3', {**base, 'current_tipoff': 'u'}, {'side': 0}, as_of) == 'void'


def test_one_market_that_cannot_be_reported_never_fails_the_sports_report(store, monkeypatch):
    """Reviewer suspicion (2026-10-13), reproduced by injection: the report had no per-market guard, so one market's
    failure (e.g. an invalid saved vector in its history) made the whole sport's page fail."""
    from app import daily_agreement as da
    from test_adaptive import TRAIN_AT
    with adaptive.cadence('daily'):
        adaptive.train(store, now=TRAIN_AT)
    real = da.make_table

    def broken(market, rows):
        if market == "total":
            raise ValueError("invalid saved probability for x in g1")
        return real(market, rows)
    monkeypatch.setattr(da, "make_table", broken)
    rep = da.report(store, "2026-10-12T21:00:00Z")
    by = {m["market"]: m for m in rep["markets"]}
    assert set(by) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    assert "invalid saved probability" in by["total"]["error"] and "error" not in by["winner"]


def test_each_forecast_records_the_fit_whose_daily_rule_it_applied(store):
    """Reviewer recheck note (c), 2026-10-13: a market on an earlier combination applies the newest fit's daily rule, so the
    row's combiner_fit_id alone does not say which rule applied; selection.daily_agreement.rule_fit_id does."""
    import json
    from test_adaptive import CYCLE_AT, TRAIN_AT
    from test_research_live import GID, run
    with adaptive.cadence('daily'):
        adaptive.train(store, now=TRAIN_AT)
        fit = adaptive.latest_fit(store, 'nhl', 'combiner', 'combiner', adaptive.definition(store))
        run(store, CYCLE_AT)
        adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    rows = store.execute("SELECT selection FROM adaptive_forecasts").fetchall()
    assert rows and all(json.loads(r[0])['daily_agreement']['rule_fit_id'] == fit['fit_id'] for r in rows)
