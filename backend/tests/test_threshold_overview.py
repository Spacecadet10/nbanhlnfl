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
