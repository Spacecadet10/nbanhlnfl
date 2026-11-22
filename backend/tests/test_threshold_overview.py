"""Confidence thresholds by market (app/threshold_overview.py, GET /api/canonical/thresholds): the declared best-threshold
rule, the live rows per threshold with the market's probability for the same picks, and the API over every market of a
sport. Synthetic data, no network."""
import pytest

from app import threshold_overview as to
from test_canonical import AFTER, record_now
from test_live import T_GAME, event, ingest_events  # noqa: F401
from test_live import client, store  # noqa: F401  (fixtures)
from test_near_tipoff import ready  # noqa: F401  (fixture)


def percentage_item(gid, grade, conf=.5181, **changes):
    rec = dict(confidence=conf, side='a', source='primary_recorded', model_version='test-model',
               line_source='bookmaker', generated_at='2026-10-20T22:00:00Z', start_at='2026-10-20T23:00:00Z')
    rec.update(changes)
    g = dict(game_id=gid, tipoff_at='2026-10-20T23:00:00Z', result_available_at='2026-10-21T03:00:00Z')
    return g, dict(state='forecast', record=rec, grade=grade)


def test_exact_displayed_record_separates_direction_model_source_and_line_source_and_excludes_self():
    target = percentage_item('self', 'win')
    items = [target, percentage_item('w', 'win', .5179), percentage_item('l1', 'loss'),
             percentage_item('l2', 'loss'), percentage_item('push', 'push'), percentage_item('pending', 'pending'),
             percentage_item('higher', 'win', .519), percentage_item('opposite', 'win', side='b'),
             percentage_item('version', 'win', model_version='other'), percentage_item('source', 'win', source='primary_direct'),
             percentage_item('line', 'win', line_source='model')]
    idx = to.percentage_index(items, AFTER)
    r = to.percentage_record('nba', 'total', target[1]['record'], 'self', idx, AFTER)
    assert (r['percentage'], r['direction'], r['wins'], r['losses'], r['settled']) == ('51.8', 'over', 1, 2, 3)
    assert r['state'] == 'more_wrong' and r['small_sample'] is True
    assert r['win_rate'] == pytest.approx(1/3) and r['loss_rate'] == pytest.approx(2/3)
    assert r['ci95'] == to.wilson(1, 3) and r['excluded'] == {'push': 1, 'pending': 1}
    # A different market's index is independent; side a is home for spread, not over.
    spread = to.percentage_record('nhl', 'spread', target[1]['record'], 'self', {}, AFTER)
    assert (spread['sport'], spread['market'], spread['direction'], spread['state']) == ('nhl', 'spread', 'home', 'no_history')
    assert spread['win_rate'] is None and spread['loss_rate'] is None


@pytest.mark.parametrize('grades,state', [(['win'],'more_right'), (['loss'],'more_wrong'), (['win','loss'],'neutral'), ([], 'no_history')])
def test_percentage_tendency_is_independent_of_sample_size(grades, state):
    rec = percentage_item('target', 'pending')[1]['record']
    idx = to.percentage_index([percentage_item(str(i), g) for i, g in enumerate(grades)], AFTER)
    r = to.percentage_record('nhl', 'reg3', {**rec, 'side':'a'}, 'target', idx, AFTER)
    assert r['state'] == state and r['small_sample'] is True


def test_percentage_history_rejects_late_forecasts_and_future_results_and_separates_three_way_draw():
    rec = percentage_item('target', 'pending', side='mid')[1]['record']
    late = percentage_item('late', 'win', side='mid', generated_at='2026-10-20T23:00:00Z')
    future = percentage_item('future', 'win', side='mid')
    future[0]['result_available_at'] = '2026-10-23T03:00:00Z'
    items = [late, future, percentage_item('draw', 'loss', side='mid'), percentage_item('home', 'win')]
    r = to.percentage_record('nhl', 'reg3', rec, 'target', to.percentage_index(items, AFTER), AFTER)
    assert (r['direction'],r['wins'],r['losses']) == ('draw',0,1)
    assert r['excluded'] == {'not_yet_available':1}


def test_percentage_rounding_uses_card_precision_without_cumulative_matching():
    assert to.displayed_percentage(.51849) == '51.8'
    assert to.displayed_percentage(.51851) == '51.9'
    # Binary float products on either side of a decimal tie, as in JavaScript toFixed(1).
    assert to.displayed_percentage(.5005) == '50.0'
    assert to.displayed_percentage(.5015) == '50.1'


def test_card_record_refreshes_after_result_ingestion_and_agrees_across_routes(ready, client):
    from app import freshness
    record_now(ready)
    ingest_events(ready, [event('matching', T_GAME, '1', '2')], '2026-10-21T22:30:00Z')
    row = dict(ready.execute("SELECT * FROM game_forecasts WHERE game_id='espn:g1' AND market='winner'").fetchone())
    row.update(game_id='espn:matching', idempotency_key='percentage-history-target')
    cols = [k for k in row if k != 'forecast_id']
    ready.execute(f"INSERT INTO game_forecasts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    client.now = '2026-10-21T23:00:00Z'
    before = client.get('/api/games/espn:matching').json()['canonical']['percentage_history']
    assert before['state'] == 'no_history' and before['settled'] == 0
    revision = freshness.revision(ready)
    ingest_events(ready, [event('g1', T_GAME, '1', '2', 'STATUS_FINAL', 110, 100)], '2026-10-22T03:00:00Z')
    assert freshness.revision(ready) != revision  # visible pages refetch when results change
    client.now = AFTER
    detail = client.get('/api/games/espn:matching').json()['canonical']['percentage_history']
    assert detail['settled'] == 1 and detail['wins'] + detail['losses'] == 1
    assert detail['state'] in ('more_right', 'more_wrong')
    games = client.get('/api/games', params={'date':'2026-10-21','policy':'canonical'}).json()['games']
    history = client.get('/api/canonical/forecasts').json()['rows']
    ranked = client.get('/api/canonical/ranked', params={'date':'2026-10-21'}).json()['picks']
    assert next(g for g in games if g['game_id']=='espn:matching')['canonical']['percentage_history'] == detail
    assert next(g for g in history if g['game_id']=='espn:matching')['canonical']['percentage_history'] == detail
    assert next(g for g in ranked if g['game_id']=='espn:matching' and g['market']=='winner')['percentage_history'] == detail
    # Reading it again does not accumulate counts, and a settled card never counts its own result.
    assert client.get('/api/games/espn:matching').json()['canonical']['percentage_history'] == detail
    assert client.get('/api/games/espn:g1').json()['canonical']['percentage_history']['settled'] == 0


def test_opposite_labels_keep_the_stored_line_and_original_pick(ready, client):
    record_now(ready)
    client.now = '2026-10-21T22:30:00Z'
    detail = client.get('/api/games/espn:g1').json()
    for c in detail['canonical_markets']:
        rec = c['record']
        assert rec['pick_label'] != rec['opposite_pick_label']
        if c['market'] == 'winner':
            other = detail['away'] if rec['side'] == 'a' else detail['home']
            assert rec['opposite_pick_label'] == other['abbr'] + ' win'
        elif c['market'] in ('total','team_total_home','team_total_away'):
            opposite_direction = 'under' if rec['side'] == 'a' else 'over'
            assert opposite_direction in rec['opposite_pick_label'].lower()
            assert f"{abs(rec['line']):g}" in rec['opposite_pick_label']
        else:
            other = detail['away'] if rec['side'] == 'a' else detail['home']
            assert rec['opposite_pick_label'].startswith(other['abbr'] + ' ')


def hist(t, n, w):
    return {"threshold": t, "graded": n, "win_rate": w / n if n else None, "ci95": to.wilson(w, n)}


def test_the_best_threshold_is_the_highest_reliable_win_rate_on_earlier_seasons_with_enough_picks():
    earlier = [hist(0.5, 4000, 2560), hist(0.6, 2400, 1680), hist(0.7, 1100, 840), hist(0.8, 150, 135)]
    later = [hist(0.5, 2600, 1690), hist(0.6, 1700, 1230), hist(0.7, 900, 710), hist(0.8, 120, 105)]
    v = to.choose(earlier, later)
    # 80%+ has the highest win rate but fewer than MIN_HISTORY_PICKS: never a candidate.
    assert v["best"] == 0.7 and v["better_than_all"] is True and v["held_up"] is True


def test_a_best_threshold_that_does_not_beat_its_later_all_picks_rate_did_not_hold_up():
    earlier = [hist(0.5, 4000, 2560), hist(0.7, 1100, 840)]
    later = [hist(0.5, 2600, 1690), hist(0.7, 900, 600)]          # 66.7% later, all picks 65%: lower bound below 65%
    assert to.choose(earlier, later)["held_up"] is False
    assert to.choose(earlier, [hist(0.5, 2600, 1690), hist(0.7, 50, 45)])["held_up"] is None    # too few later picks


def test_all_picks_wins_when_no_threshold_is_more_reliable():
    earlier = [hist(0.5, 4000, 2600), hist(0.6, 300, 196)]
    v = to.choose(earlier, [])
    assert v["best"] == 0.5 and v["better_than_all"] is False
    assert to.choose([hist(0.5, 100, 60)], [])["best"] is None   # nothing has enough picks


def test_calibration_compares_the_win_rate_interval_with_the_stated_probability():
    assert to.calibration_state(0.6, to.wilson(60, 100), 0.62, 100, 30) == "holds"
    assert to.calibration_state(0.4, to.wilson(400, 1000), 0.6, 1000, 30) == "overconfident"
    assert to.calibration_state(0.8, to.wilson(800, 1000), 0.6, 1000, 30) == "underconfident"
    assert to.calibration_state(0.9, to.wilson(9, 10), 0.6, 10, 30) == "too_few"


def canon(conf, grade, side="a"):
    return {"state": "forecast", "grade": grade, "record": {"confidence": conf, "side": side}}


def test_live_rows_are_cumulative_and_give_the_market_probability_of_the_picked_side():
    items = [(None, canon(0.72, "win", "a")), (None, canon(0.62, "loss", "b")), (None, canon(0.55, "pending", "a")),
             (None, {"state": "missing", "grade": None, "record": None})]
    quotes = {0.72: 0.70, 0.62: 0.45}                              # P(side a) per forecast; the pending one has none
    rows = to.live_rows(items, "winner", (0.5, 0.6, 0.7, 0.8), lambda g, c: quotes.get(c["record"]["confidence"]))
    r50, r60, r70, r80 = rows
    assert (r50["forecasts"], r50["graded"], r50["win"], r50["unsettled"]) == (3, 2, 1, 1)
    assert r50["stated"] == pytest.approx((0.72 + 0.62) / 2)
    # The 62% pick is side b: the market gave b 1 - 0.45 = 55%.
    assert r50["market_matched"] == 2 and r50["market_implied"] == pytest.approx((0.70 + 0.55) / 2)
    assert (r70["graded"], r70["win"], r70["market_implied"]) == (1, 1, pytest.approx(0.70))
    assert r80["graded"] == 0 and r80["win_rate"] is None and r80["ci95"] is None
    assert r50["break_even"] is None and r60["calibration"] == "too_few"


def test_a_line_market_is_judged_against_break_even_and_never_chooses_from_its_own_line_history():
    items = [(None, canon(0.6, "loss")) for _ in range(40)] + [(None, canon(0.6, "win")) for _ in range(10)]
    [row] = to.live_rows(items, "spread", (0.5,), lambda g, c: None)
    assert row["win_rate"] == 0.2 and row["break_even"] == "below" and row["market_matched"] == 0
    h = to.history("nba", "spread", (0.5,))
    assert h["status"] == "not_comparable" and "own line" in h["reason"]


def test_the_api_reports_every_market_of_the_sport_with_the_same_record_as_the_canonical_performance(ready, client):
    record_now(ready)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = AFTER
    body = client.get("/api/canonical/thresholds").json()
    assert [m["market"] for m in body["markets"]] == ["winner", "spread", "total", "team_total_home", "team_total_away"]
    for m in body["markets"]:
        perf = client.get("/api/canonical/performance", params={"market": m["market"]}).json()["competitive"]
        assert [r["threshold"] for r in m["live"]] == [t["threshold"] for t in perf["thresholds"]] == m["grid"]
        for mine, theirs in zip(m["live"], perf["thresholds"]):
            assert (mine["forecasts"], mine["win"], mine["loss"]) == (theirs["forecasts"], theirs["win"], theirs["loss"])
        if m["market"] != "winner":
            assert m["history"]["status"] == "not_comparable" and m["verdict"]["best"] is None
    winner = body["markets"][0]
    assert winner["live"][0]["market_matched"] == 1          # the moneyline retrieved before the forecast
    assert winner["history"]["status"] in ("available", "unavailable")    # no saved pool tables in the synthetic store


def test_the_threshold_overview_is_not_part_of_the_demo(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("NBA_API_MODE", "demo")
    assert TestClient(app).get("/api/canonical/thresholds").status_code == 404


# --- Ranked picks of one date -----------------------------------------------------------------------------------------
GRID = (0.5, 0.55, 0.6, 0.65, 0.7)


def test_a_pick_is_judged_by_its_own_probability_band_and_falls_back_to_the_broader_row_when_the_band_is_thin():
    ladder = {"source": "history", "rows": {0.5: (700, 1000), 0.55: (560, 800), 0.6: (420, 600), 0.65: (300, 400),
                                            0.7: (20, 25)}}
    e = to.pick_evidence(0.62, ladder, GRID)          # band 60-65%: 420-300 wins of 600-400 picks
    assert (e["lo"], e["hi"], e["settled"]) == (0.6, 0.65, 200) and e["win_rate"] == pytest.approx(0.6)
    assert e["lower"] == pytest.approx(to.wilson(120, 200)[0])
    e = to.pick_evidence(0.72, ladder, GRID)          # 70%+ has 25 picks (< 30): the highest row with enough is 65%+
    assert (e["lo"], e["hi"], e["settled"]) == (0.65, None, 400)
    assert to.pick_evidence(0.45, ladder, GRID) is None                              # below the grid
    assert to.pick_evidence(0.6, {"source": "season", "rows": {0.5: (5, 9)}}, GRID) is None


def pick(gid, market, p, lower):
    return {"game_id": gid, "market": market, "probability": p,
            "evidence": None if lower is None else {"lower": lower}}


def test_picks_are_ranked_for_the_day_and_within_each_game_and_unsupported_picks_are_not_ranked():
    out = to.rank([pick("g1", "spread", 0.70, 0.40), pick("g1", "winner", 0.66, 0.65), pick("g2", "winner", 0.69, 0.65),
                   pick("g2", "total", 0.60, None), pick("g1", "total", 0.55, 0.45)])
    assert [(p["game_id"], p["market"], p["day_rank"], p["game_rank"]) for p in out] == [
        ("g2", "winner", 1, 1),            # equal evidence: the higher model probability first
        ("g1", "winner", 2, 1), ("g1", "total", 3, 2), ("g1", "spread", 4, 3),
        ("g2", "total", None, None)]
    assert out[1]["game_ranked"] == 3 and out[-1]["game_ranked"] == 1
    assert [p["strong"] for p in out] == [True, True, False, False, False]       # lower bound above 50% only
    # A line market never gives a best pick, however strong its record looks: it has shown no skill against the line.
    [line] = to.rank([pick("g3", "spread", 0.7, 0.6)])
    assert line["day_rank"] == 1 and line["skill_proven"] is False and line["strong"] is False
    assert to.rank([pick("g3", "reg3", 0.6, 0.55)])[0]["strong"] is True


def test_the_ranked_endpoint_lists_every_market_pick_of_the_date_and_never_counts_the_date_itself_as_evidence(
        ready, client, monkeypatch):
    record_now(ready)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = AFTER
    monkeypatch.setattr(to, "MIN_LIVE_GRADED", 1)        # one settled pick would be enough evidence...
    body = client.get("/api/canonical/ranked", params={"date": "2026-10-21"}).json()
    picks = body["picks"]
    assert sorted(p["market"] for p in picks) == sorted(["winner", "spread", "total", "team_total_home", "team_total_away"])
    for p in picks:
        assert p["pick_label"] and p["threshold"] is not None and p["grade"] in ("win", "loss", "push")
        assert p["evidence"] is None and p["day_rank"] is None and p["strong"] is False   # ...but g1 is the date's own game
    assert next(p for p in picks if p["market"] == "winner")["market_prob"] is not None
    assert client.get("/api/canonical/ranked", params={"date": "2026-10-25"}).json()["picks"] == []
    assert client.get("/api/canonical/ranked").status_code == 422                       # the date is required


def _telegram_file(tmp_path, monkeypatch, rows):
    import json
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    path = to.telegram_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'source': 'telegram_cards', 'period': {'from': '2026-11-02', 'to': '2026-11-14'}, 'rows': rows}))


def _tg(gid, grade, pct='51.8', side='a', market='total', sport='nba', line_source='bookmaker'):
    return dict(sport=sport, market=market, percentage=pct, side=side, line_source=line_source, game_id=gid, grade=grade)


def test_telegram_history_counts_the_same_exact_percentage_apart_from_the_live_record(tmp_path, monkeypatch):
    """Owner (Nov 16): the Nov 2-14 picks exist only in the Telegram export; show them as their own line (option A) at the
    same sport, market, exact displayed percentage, side and line source, never added to the live record."""
    _telegram_file(tmp_path, monkeypatch, [
        _tg('w1', 'win'), _tg('w2', 'win'), _tg('l1', 'loss'), _tg('p1', 'push'),
        _tg('self', 'loss'),                                   # the target game itself is excluded
        _tg('near', 'win', pct='51.9'),                        # 51.9 is not 51.8: exact percentages only
        _tg('side', 'win', side='b'), _tg('line', 'win', line_source='model'),
        _tg('mkt', 'win', market='spread'), _tg('sport', 'win', sport='nhl')])
    target = percentage_item('self', 'win')
    idx = to.percentage_index([target, percentage_item('live', 'win')], AFTER)
    r = to.percentage_record('nba', 'total', target[1]['record'], 'self', idx, AFTER)
    assert (r['wins'], r['losses'], r['settled']) == (1, 0, 1)              # live record unchanged
    t = r['telegram']
    assert (t['source'], t['wins'], t['losses'], t['pushes'], t['settled']) == ('telegram_cards', 2, 1, 1, 3)
    assert t['period'] == {'from': '2026-11-02', 'to': '2026-11-14'} and t['small_sample'] is True


def test_no_telegram_history_file_means_no_telegram_line(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    target = percentage_item('self', 'win')
    r = to.percentage_record('nba', 'total', target[1]['record'], 'self', {}, AFTER)
    t = r['telegram']                       # never silently absent, never 0-0: an explicit unavailable record
    assert (t['available'], t['reason'], t['wins'], t['losses'], t['settled'], t['win_rate']) == (False, 'file_missing', None, None, None, None)
    assert (t['label'], t['model_version_known'], t['colour'], t['period']) == ('Recovered Telegram cards', False, 'neutral', None)


# --- Nov 17: contract fields (pushes, telegram label/period/model_version_known, sport coverage) ---------------------------
def test_live_record_reports_pushes_apart_and_keeps_excluded():
    target = percentage_item('self', 'pending')
    idx = to.percentage_index([percentage_item('w', 'win'), percentage_item('l', 'loss'), percentage_item('p', 'push'),
                               percentage_item('p2', 'push'), percentage_item('x', 'pending')], AFTER)
    r = to.percentage_record('nba', 'spread', target[1]['record'], 'self', idx, AFTER)
    assert (r['wins'], r['losses'], r['pushes'], r['settled']) == (1, 1, 2, 2)
    assert r['excluded'] == {'push': 2, 'pending': 1}                     # unchanged, backward compatible


def test_57_3_and_57_4_are_different_exact_buckets_in_live_and_telegram(tmp_path, monkeypatch):
    _telegram_file(tmp_path, monkeypatch, [_tg('t73', 'win', pct='57.3'), _tg('t74', 'loss', pct='57.4')])
    target = percentage_item('self', 'pending', conf=.5731)
    idx = to.percentage_index([percentage_item('a', 'win', .5731), percentage_item('b', 'loss', .5739)], AFTER)
    r = to.percentage_record('nba', 'total', target[1]['record'], 'self', idx, AFTER)
    assert r['percentage'] == '57.3' and (r['wins'], r['losses']) == (1, 0)
    assert (r['telegram']['wins'], r['telegram']['losses']) == (1, 0)


def test_telegram_record_carries_label_period_and_unknown_model_version(tmp_path, monkeypatch):
    _telegram_file(tmp_path, monkeypatch, [_tg('w1', 'win')])
    target = percentage_item('self', 'win')
    t = to.percentage_record('nba', 'total', target[1]['record'], 'self', {}, AFTER)['telegram']
    assert t['label'] == 'Recovered Telegram cards' and t['model_version_known'] is False
    assert t['period'] == {'from': '2026-11-02', 'to': '2026-11-14'}


def test_a_sport_with_no_recovered_telegram_rows_has_no_telegram_line_and_says_so_in_coverage(tmp_path, monkeypatch):
    _telegram_file(tmp_path, monkeypatch, [_tg('w1', 'win')])             # only NBA rows exist
    target = percentage_item('self', 'win')
    r = to.percentage_record('ncaab', 'winner', target[1]['record'], 'self', {}, AFTER, live_since='2026-11-17T15:00:54Z')
    assert (r['telegram']['available'], r['telegram']['reason'], r['telegram']['wins']) == (False, 'no_rows_for_sport', None)
    assert r['coverage']['live_since'] == '2026-11-17T15:00:54Z' and r['coverage']['telegram_period'] is None
    assert 'No recovered Telegram history for NCAAB' in r['coverage']['history_note']
    assert '2026-11-17' in r['coverage']['history_note']                  # recording began <date>


def test_coverage_names_the_telegram_period_when_the_sport_has_rows(tmp_path, monkeypatch):
    _telegram_file(tmp_path, monkeypatch, [_tg('w1', 'win')])
    target = percentage_item('self', 'win')
    c = to.percentage_record('nba', 'total', target[1]['record'], 'self', {}, AFTER, live_since='2026-11-15T16:50:42Z')['coverage']
    assert c['telegram_period'] == {'from': '2026-11-02', 'to': '2026-11-14'} and c['live_since'] == '2026-11-15T16:50:42Z'
    assert 'Recovered Telegram cards' in c['history_note']


def test_per_sport_telegram_period_comes_from_the_file_summary(tmp_path, monkeypatch):
    import json
    _telegram_file(tmp_path, monkeypatch, [])
    to.telegram_history_path().write_text(json.dumps({
        'period': {'from': '2026-11-03', 'to': '2026-11-14'},
        'summary': {'soccer': {'period': {'from': '2026-11-14', 'to': '2026-11-14'}}},
        'rows': [_tg('m', 'win', sport='soccer', market='1x2_regulation', side='home', line_source='none', pct='71.5')]}))
    rec = dict(percentage_item('self', 'win')[1]['record'], confidence=.7144, side='home', line_source='none')
    r = to.telegram_record('soccer', '1x2_regulation', rec, 'self')
    assert r['period'] == {'from': '2026-11-14', 'to': '2026-11-14'} and r['wins'] == 0     # 71.4 != 71.5
    rec['confidence'] = .7151
    assert to.telegram_record('soccer', '1x2_regulation', rec, 'self')['wins'] == 1


def test_every_ranked_pick_carries_the_card_contract_fields(ready, client, monkeypatch):
    """Nov 17 contract: line, side, model_version, sportsbook, percentage_history (pushes, coverage) and record_basis."""
    record_now(ready)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = AFTER
    picks = client.get("/api/canonical/ranked", params={"date": "2026-10-21"}).json()["picks"]
    assert picks
    for p in picks:
        assert {"line", "side", "model_version", "sportsbook", "record_basis", "forecast_id"} <= set(p)
        b = p["record_basis"]
        assert b["forecast_id"] == p["forecast_id"] and b["pregame_forecasts"] >= 1 and b["published"] is None
        assert b["probability"] == pytest.approx(p["probability"]) and b["side"] == p["side"] and b["model_version"] == p["model_version"]
        h = p["percentage_history"]
        assert h["pushes"] == 0 and h["coverage"]["history_note"] and "telegram_period" in h["coverage"]


# --- Bucket colouring (owner brief Nov 22): each exact-% bucket is coloured by its OWN W/L record -------------------------
@pytest.mark.parametrize('w,l,colour', [(1, 0, 'green'), (2, 0, 'green'), (8, 1, 'green'), (3, 1, 'green'), (3, 2, 'neutral'),
                                        (0, 2, 'red'), (1, 1, 'neutral'), (2, 1, 'neutral'), (0, 0, 'neutral'), (0, 1, 'red'),
                                        (6, 2, 'green'), (5, 2, 'neutral'), (1, 2, 'red')])
def test_bucket_colour_rule(w, l, colour):
    assert to.COLOUR_RULE == 'bucket-record-v1'
    assert to.bucket_colour(w, l) == colour


def _colour_of(grades, conf=.5731, **kw):
    target = percentage_item('self', 'pending', conf=conf)
    items = [percentage_item(f'g{i}', g, conf) for i, g in enumerate(grades)]
    return to.percentage_record('nba', 'total', target[1]['record'], 'self', to.percentage_index(items, AFTER), AFTER, **kw)


def test_record_colour_uses_the_live_record_and_excludes_pushes_and_voids_from_the_denominator():
    r = _colour_of(['win'] * 3 + ['loss'] + ['push'] * 4 + ['void'] * 2)
    assert (r['wins'], r['losses'], r['pushes'], r['voids'], r['settled']) == (3, 1, 4, 2, 4)
    assert r['colour'] == 'green' and r['colour_rule'] == 'bucket-record-v1'
    only_pushes = _colour_of(['push', 'push', 'void'])
    assert only_pushes['colour'] == 'neutral' and only_pushes['settled'] == 0 and only_pushes['pushes'] == 2
    assert _colour_of([])['colour'] == 'neutral'


def test_a_newly_graded_game_moves_the_bucket_colour():
    assert _colour_of(['win', 'win', 'loss'])['colour'] == 'neutral'          # 2-1
    assert _colour_of(['win', 'win', 'loss', 'win'])['colour'] == 'green'     # 3-1


def test_same_game_graded_twice_counts_once():
    target = percentage_item('self', 'pending', conf=.5731)
    idx = to.percentage_index([percentage_item('a', 'loss', .5731), percentage_item('a', 'win', .5731)], AFTER)
    r = to.percentage_record('nba', 'total', target[1]['record'], 'self', idx, AFTER)
    assert (r['wins'], r['losses']) == (1, 0)


def test_bucket_colour_keeps_57_3_and_57_4_apart_including_a_binary_float_tie():
    wins = to.percentage_index([percentage_item('a', 'win', .5731), percentage_item('b', 'loss', .5739)], AFTER)
    low = percentage_item('self', 'pending', conf=.5731)[1]['record']
    high = percentage_item('self', 'pending', conf=.5739)[1]['record']
    assert to.percentage_record('nba', 'total', low, 'self', wins, AFTER)['colour'] == 'green'
    assert to.percentage_record('nba', 'total', high, 'self', wins, AFTER)['colour'] == 'red'
    assert to.displayed_percentage(.5735) == '57.4'      # the JS toFixed(1) result of the float 57.35000000000001


def test_recovered_telegram_record_is_coloured_on_its_own_and_never_merged(tmp_path, monkeypatch):
    _telegram_file(tmp_path, monkeypatch, [_tg('t1', 'loss', pct='57.3'), _tg('t2', 'loss', pct='57.3'), _tg('t3', 'void', pct='57.3')])
    r = _colour_of(['win'], conf=.5731, )
    r2 = _colour_of(['win', 'win'])
    t = r['telegram']
    assert (t['wins'], t['losses'], t['voids'], t['colour'], t['model_version_known'], t['label']) == \
        (0, 2, 1, 'red', False, 'Recovered Telegram cards')
    assert r['colour'] == 'green' and r2['wins'] == 2 and r['wins'] == 1      # the headline stays the live record


def test_history_subrecord_has_its_own_colour_and_voids():
    rec = percentage_item('self', 'pending', conf=.5731)[1]['record']
    key = ('57.3', 'a', to.HISTORY_SOURCE, 'test-model', 'bookmaker')
    h = to.percentage_record('nba', 'total', rec, 'self', {}, AFTER, history_index={key: {'a': 'win', 'b': 'win', 'c': 'void'}})['history']
    assert (h['wins'], h['voids'], h['colour']) == (2, 1, 'green')


def test_record_line_text_is_shared_by_telegram_and_web():
    assert to.record_line('57.3', 8, 1, 1, 9) == '57.3% · this exact % has gone 8–1 (89%) · 1 push · small sample (9 settled)'
    assert to.record_line('57.3', 40, 10, 0, 50) == '57.3% · this exact % has gone 40–10 (80%)'
    assert to.record_line('57.3', 0, 0, 0, 0) == '57.3% · no settled history at this exact %'
    assert to.record_line('57.3', 0, 0, 2, 0) == '57.3% · no settled history at this exact % · 2 pushes'
    assert to.record_line('57.3', 0, 2, 0, 2, voids=1) == '57.3% · this exact % has gone 0–2 (0%) · 1 void · small sample (2 settled)'


def _rows_file(tmp_path, monkeypatch, rows, summary=None):
    import json
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    path = to.telegram_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'source': 'telegram_cards', 'period': {'from': '2026-11-01', 'to': '2026-11-30'},
                                'summary': summary or {}, 'rows': rows}))


def _dated(gid, grade, day, pct='57.3', **kw):
    return dict(_tg(gid, grade, pct=pct, **kw), start_date_local=day, start_at=day + 'T23:00:00Z')


def test_recovered_record_has_win_rate_colour_voids_actual_dates_and_the_available_flag(tmp_path, monkeypatch):
    _rows_file(tmp_path, monkeypatch, [_dated('a', 'win', '2026-11-05'), _dated('b', 'win', '2026-11-09'), _dated('c', 'win', '2026-11-12'),
                                       _dated('d', 'loss', '2026-11-12'), _dated('e', 'push', '2026-11-12'), _dated('f', 'void', '2026-11-13'),
                                       _dated('x', 'win', '2026-11-16', pct='57.4'), _dated('self', 'win', '2026-11-02')])
    rec = percentage_item('self', 'pending', conf=.5731)[1]['record']
    t = to.percentage_record('nba', 'total', rec, 'self', {}, AFTER)['telegram']
    assert (t['available'], t['wins'], t['losses'], t['pushes'], t['voids'], t['settled']) == (True, 3, 1, 1, 1, 4)
    assert t['win_rate'] == 0.75 and t['colour'] == 'green' and t['model_version_known'] is False
    assert t['period'] == {'from': '2026-11-02', 'to': '2026-11-16'}      # the sport's dates present in the file, from its rows
    assert t['bucket_period'] == {'from': '2026-11-05', 'to': '2026-11-13'}


def test_a_bucket_with_no_recovered_rows_is_no_history_not_0_0(tmp_path, monkeypatch):
    _rows_file(tmp_path, monkeypatch, [_dated('a', 'win', '2026-11-05', pct='60.0')])
    rec = percentage_item('self', 'pending', conf=.5731)[1]['record']
    t = to.percentage_record('nba', 'total', rec, 'self', {}, AFTER)['telegram']
    assert (t['available'], t['no_history'], t['settled'], t['win_rate'], t['colour']) == (True, True, 0, None, 'neutral')
    assert t['period'] == {'from': '2026-11-05', 'to': '2026-11-05'}


def test_an_unreadable_file_is_reported_as_unavailable(tmp_path, monkeypatch):
    _rows_file(tmp_path, monkeypatch, [])
    to.telegram_history_path().write_text('{not json')
    rec = percentage_item('self', 'pending')[1]['record']
    t = to.percentage_record('nba', 'total', rec, 'self', {}, AFTER)['telegram']
    assert (t['available'], t['reason'], t['settled']) == (False, 'unreadable', None)


def test_a_game_in_both_records_counts_once_in_each_and_is_reported_never_summed(tmp_path, monkeypatch):
    _rows_file(tmp_path, monkeypatch, [_dated('both', 'win', '2026-11-15'), _dated('old', 'loss', '2026-11-08')])
    rec = percentage_item('self', 'pending', conf=.5731)[1]['record']
    idx = to.percentage_index([percentage_item('both', 'win', .5731), percentage_item('livefirst', 'loss', .5731)], AFTER)
    r = to.percentage_record('nba', 'total', rec, 'self', idx, AFTER)
    assert (r['wins'], r['losses']) == (1, 1) and (r['telegram']['wins'], r['telegram']['losses']) == (1, 1)
    assert r['telegram']['overlap_live'] == 1                       # 'both' is in both displayed records, never added: 1-1 and 1-1, not 2-2


def test_history_subrecord_also_carries_win_rate_and_available():
    rec = percentage_item('self', 'pending', conf=.5731)[1]['record']
    key = ('57.3', 'a', to.HISTORY_SOURCE, 'test-model', 'bookmaker')
    h = to.percentage_record('nba', 'total', rec, 'self', {}, AFTER, history_index={key: {'a': 'win', 'b': 'loss', 'c': 'win'}})['history']
    assert h['available'] is True and h['win_rate'] == pytest.approx(2 / 3)


def test_record_lines_are_one_text_for_telegram_and_the_web(tmp_path, monkeypatch):
    """percentage_history.telegram.line / history.line are exactly what Telegram prints (threshold_overview.telegram_line /
    history_line); the web renders them verbatim, so the two cannot drift."""
    import json
    from app import telegram as tg
    def rec_for(wins, losses, pushes=0, voids=0, overlap_game=None):
        rows = [_dated(f'w{i}', 'win', '2026-11-05') for i in range(wins)] + [_dated(f'l{i}', 'loss', '2026-11-09') for i in range(losses)] + \
               [_dated(f'p{i}', 'push', '2026-11-09') for i in range(pushes)] + [_dated(f'v{i}', 'void', '2026-11-10') for i in range(voids)]
        _rows_file(tmp_path, monkeypatch, rows + [_dated('far', 'win', '2026-11-16', pct='60.0'), _dated('early', 'win', '2026-11-05', pct='60.0')])
        target = percentage_item('self', 'pending', conf=.5731)[1]['record']
        idx = to.percentage_index([percentage_item('w0', 'win', .5731)] if overlap_game else [], AFTER)
        return to.percentage_record('nba', 'total', target, 'self', idx, AFTER, live_since='2026-11-15T16:50:42Z')
    got = rec_for(8, 1, pushes=1, overlap_game=True)['telegram']['line']
    assert got == ('Recovered Telegram cards 11-05 to 11-16: this exact % has gone 8–1 (89%) · 1 push · small sample (9 settled) · green record · '
                   '1 game also in the live record, not added · model version not recorded')
    assert rec_for(3, 2)['telegram']['line'] == ('Recovered Telegram cards 11-05 to 11-16: this exact % has gone 3–2 (60%) · small sample (5 settled) · '
                                                  'model version not recorded')
    assert rec_for(0, 2)['telegram']['line'] == ('Recovered Telegram cards 11-05 to 11-16: this exact % has gone 0–2 (0%) · small sample (2 settled) · '
                                                  'red record · model version not recorded')
    assert rec_for(0, 0)['telegram']['line'] == 'Recovered Telegram cards 11-05 to 11-16: no recovered history at this exact % · model version not recorded'
    r = rec_for(0, 2)
    assert tg.telegram_history_line('nba', {k: r[k] for k in ('telegram', 'coverage')}) == r['telegram']['line']     # Telegram prints the same string
    to.telegram_history_path().unlink()
    target = percentage_item('self', 'pending', conf=.5731)[1]['record']
    assert to.percentage_record('nba', 'total', target, 'self', {}, AFTER)['telegram']['line'] == \
        'Recovered Telegram cards: recovered history unavailable (file missing)'
    ncaab = to.percentage_record('ncaab', 'winner', target, 'self', {}, AFTER, live_since='2026-11-17T15:00:54Z')
    _rows_file(tmp_path, monkeypatch, [_dated('a', 'win', '2026-11-05')])
    ncaab = to.percentage_record('ncaab', 'winner', target, 'self', {}, AFTER, live_since='2026-11-17T15:00:54Z')
    assert ncaab['telegram']['line'] == 'No recovered Telegram history for NCAAB (recording began 2026-11-17)'


def test_history_line_is_null_when_telegram_hides_it_and_otherwise_the_printed_text():
    rec = percentage_item('self', 'pending', conf=.5731)[1]['record']
    key = ('57.3', 'a', to.HISTORY_SOURCE, 'test-model', 'bookmaker')
    h = to.percentage_record('nba', 'total', rec, 'self', {}, AFTER, history_index={key: {'a': 'win', 'b': 'win'}})['history']
    assert h['line'] == 'Past seasons 2024-26, recomputed, model line: 2W–0L · Small sample. · green record'
    none = to.percentage_record('nba', 'total', rec, 'self', {}, AFTER, history_index={})['history']
    assert none['line'] is None
    from app.schemas import PercentageHistorySource
    assert PercentageHistorySource(**h).line == h['line']
