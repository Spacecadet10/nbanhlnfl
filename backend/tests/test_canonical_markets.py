"""The canonical forecast for both sports and every market: the NHL winner through Today, Game, History and Performance,
and (as the all-markets integration lands) every NBA and NHL market. Synthetic NHL store, no network."""
import pytest

from app import coverage
from test_adaptive import CYCLE_AT, TRAIN_AT, store  # noqa: F401  (fixture)
from test_coverage import _settle
from test_live_markets import add_upcoming, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, game, run  # noqa: F401  (fixture)

DAY = "2026-10-12"                   # GID starts 2026-10-12T23:00Z (19:00 ET); CYCLE_AT is 90 minutes before
AFTER = "2026-10-13T04:00:00Z"       # GID is final by then (_settle retrieves the result at 03:00Z)


@pytest.fixture
def nhl(game, monkeypatch):
    """(store, client): the real API on the synthetic NHL store, a fresh connection per request like production."""
    from fastapi.testclient import TestClient
    from app import db, main
    from app.providers.real import RealProvider
    path = game.execute("PRAGMA database_list").fetchone()["file"]

    def provider():
        conn = db.connect(path, check_same_thread=False)
        try:
            yield RealProvider(conn, now=client.now)
        finally:
            conn.close()
    monkeypatch.setitem(main.app.dependency_overrides, main.get_provider, provider)
    client = TestClient(main.app)
    client.now = CYCLE_AT
    return game, client


def _rows(conn, market):
    return [dict(r) for r in conn.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market=? ORDER BY forecast_id",
                                          (GID, market))]


def test_the_nhl_winner_is_one_canonical_record_from_forecast_to_grade_and_performance(nhl):
    conn, client = nhl
    run(conn, CYCLE_AT)
    assert coverage.record(conn, now=CYCLE_AT)["status"] == "success"
    conn.commit()
    [row] = _rows(conn, "winner")
    assert row["status"] == "forecast" and row["source"] == "primary_recorded"
    q = {"sport": "nhl"}
    body = client.get("/api/games", params={**q, "date": DAY, "policy": "canonical"}).json()
    card = next(g for g in body["games"] if g["game_id"] == GID)
    assert card["canonical"]["state"] == "forecast" and card["canonical"]["record"]["forecast_id"] == row["forecast_id"]
    assert card["canonical"]["grade"] == "pending" and card["canonical"]["record"]["minutes_before_start"] == 90.0
    assert card["slate_pick"]["home_win_prob"] == pytest.approx(row["p_a"])
    detail = client.get(f"/api/games/{GID}", params=q).json()["canonical"]
    assert detail["record"]["forecast_id"] == row["forecast_id"]
    hist = client.get("/api/canonical/forecasts", params=q).json()
    h = next(r for r in hist["rows"] if r["game_id"] == GID)
    assert h["canonical"]["record"]["forecast_id"] == row["forecast_id"]
    # The game is played (a regulation home win): the same record is graded once, identically everywhere.
    _settle(conn, hs=4, vs=1)
    conn.commit()
    client.now = AFTER
    expected = "win" if row["p_a"] >= row["p_b"] else "loss"
    card = next(g for g in client.get("/api/games", params={**q, "date": DAY, "policy": "canonical"}).json()["games"]
                if g["game_id"] == GID)
    assert card["canonical"]["grade"] == expected
    assert client.get(f"/api/games/{GID}", params=q).json()["canonical"]["grade"] == expected
    hist = client.get("/api/canonical/forecasts", params=q).json()
    assert next(r for r in hist["rows"] if r["game_id"] == GID)["canonical"]["grade"] == expected
    perf = client.get("/api/canonical/performance", params=q).json()
    comp = perf["competitive"]
    assert comp["forecast"] == 1 and comp["missing"] == 0 and comp["graded"] == 1
    assert comp["accuracy"] == (1.0 if expected == "win" else 0.0)
    assert comp["model_versions"] == [{"model_version": row["model_version"], "forecasts": 1}]
    assert perf["market_comparison"]["matched_games"] == 1      # the moneyline retrieved at 21:05Z, before the forecast


NHL_MARKETS = ("winner", "reg3", "spread", "total", "team_total_home", "team_total_away")


def _card(client, market, **params):
    body = client.get("/api/games", params={"sport": "nhl", "date": DAY, "policy": "canonical", "market": market,
                                            **params}).json()
    return body, next(g for g in body["games"] if g["game_id"] == GID)


def _forecast_everywhere(client, market) -> list:
    """The canonical forecast of one market as Today, the game page, History and Performance return it."""
    _, card = _card(client, market)
    detail = {c["market"]: c for c in client.get(f"/api/games/{GID}", params={"sport": "nhl"}).json()["canonical_markets"]}
    hist = client.get("/api/canonical/forecasts", params={"sport": "nhl", "market": market}).json()
    assert hist["market"] == market
    row = next(r for r in hist["rows"] if r["game_id"] == GID)
    return [card["canonical"], detail[market], row["canonical"]]


def test_every_nhl_market_is_one_canonical_record_on_every_page_graded_identically(nhl):
    conn, client = nhl
    run(conn, CYCLE_AT)
    assert coverage.record(conn, now=CYCLE_AT)["status"] == "success"
    conn.commit()
    detail = client.get(f"/api/games/{GID}", params={"sport": "nhl"}).json()
    assert [c["market"] for c in detail["canonical_markets"]] == list(NHL_MARKETS)
    for market in NHL_MARKETS:
        [row] = [r for r in _rows(conn, market) if r["status"] == "forecast"]
        views = _forecast_everywhere(client, market)
        assert {v["market"] for v in views} == {market}
        assert {v["record"]["forecast_id"] for v in views} == {row["forecast_id"]}, market
        assert {v["record"]["line"] for v in views} == {row["line"]} and {v["grade"] for v in views} == {"pending"}
        rec = views[0]["record"]
        assert rec["side"] == coverage.pick_side(market, (row["p_a"], row["p_mid"], row["p_b"])) and rec["pick_label"]
    _settle(conn, hs=4, vs=1)                     # a regulation home win, 5 goals
    conn.commit()
    client.now = AFTER
    for market in NHL_MARKETS:
        views = _forecast_everywhere(client, market)
        grades = {v["grade"] for v in views}
        assert len(grades) == 1, (market, grades)                   # one grade, the same on every page
        [g] = grades
        # The regulation three-way market is graded from the regulation decision (never 'unscorable' for a REG final).
        assert g in ("win", "loss", "push"), (market, g)
        assert {v["result_side"] for v in views} != {None}
        perf = client.get("/api/canonical/performance", params={"sport": "nhl", "market": market}).json()
        comp = perf["competitive"]
        assert perf["market"] == market and comp["games"] == 1 and comp["forecast"] == 1
        assert comp["grades"][g] == 1 and comp["graded"] == (1 if g in ("win", "loss") else 0)
        grid = [t["threshold"] for t in comp["thresholds"]]
        assert grid == ([0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7] if market == "reg3"
                        else [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9])
        conf = views[0]["record"]["confidence"]
        for t in comp["thresholds"]:                                   # a filter of the record, never of recording
            assert t["forecasts"] == (1 if conf >= t["threshold"] - 1e-12 else 0)
        if g in ("win", "loss"):
            assert comp["brier_score"] is not None and comp["accuracy"] == (1.0 if g == "win" else 0.0)


def test_the_canonical_slate_of_a_market_counts_and_filters_that_market_only(nhl):
    conn, client = nhl
    run(conn, CYCLE_AT)
    coverage.record(conn, now=CYCLE_AT)
    conn.commit()
    body, card = _card(client, "total")
    assert body["selection"]["market"] == "total" and body["selection"]["policy"] == "canonical"
    assert card["canonical"]["market"] == "total" and card["market_forecast"] is None     # never a legacy ledger's row
    conf = card["canonical"]["record"]["confidence"]
    above = _card(client, "total", min_confidence=0.5)[0]["selection"]
    assert above["with_forecast"] == above["selected"] + above["below_threshold"] and above["selected"] == 1
    too_high = _card(client, "total", min_confidence=min(1.0, round(conf + 0.01, 2)))[0]["selection"]
    assert too_high["selected"] == 0 and too_high["below_threshold"] == 1 and too_high["with_forecast"] == 1
    assert client.get("/api/games", params={"sport": "nhl", "date": DAY, "policy": "canonical", "market": "total",
                                            "min_confidence": 0.4}).status_code == 422     # two-way: at least 50%
    assert client.get("/api/games", params={"sport": "nhl", "date": DAY, "policy": "canonical", "market": "reg3",
                                            "min_confidence": 0.35}).status_code == 200    # three-way grid starts at 35%


def test_pick_side_lets_a_draw_be_the_pick_and_breaks_ties_in_a_fixed_order():
    assert coverage.pick_side("reg3", (0.3, 0.4, 0.3)) == "mid"
    assert coverage.pick_side("reg3", (0.4, 0.2, 0.4)) == "a" and coverage.pick_side("reg3", (0.3, 0.35, 0.35)) == "mid"
    assert coverage.pick_side("total", (0.45, 0.1, 0.45)) == "a"          # two-way tie: side a; the push is never a pick
    assert coverage.pick_side("spread", (0.3, 0.2, 0.5)) == "b"


def test_a_regulation_draw_is_a_win_for_a_draw_pick_and_a_loss_otherwise():
    game = {"status": "final", "home_score": 3, "away_score": 2, "tipoff_at": "2026-10-12T23:00:00Z", "decision": "OT"}

    class Row(dict):
        def keys(self):
            return super().keys()
    g = Row(game)
    assert coverage.grade("reg3", None, (0.3, 0.4, 0.3), g, AFTER) == "win"
    assert coverage.grade("reg3", None, (0.45, 0.25, 0.3), g, AFTER) == "loss"
    nodec = Row({k: v for k, v in game.items() if k != "decision"})
    assert coverage.grade("reg3", None, (0.45, 0.25, 0.3), nodec, AFTER) == "unscorable"          # no decision known
    assert coverage.grade("reg3", None, (0.45, 0.25, 0.3), nodec, AFTER, decision="REG") == "win"   # passed in


def test_a_bookmaker_comparison_needs_the_same_market_line_and_a_quote_before_the_forecast(nhl):
    conn, client = nhl
    run(conn, CYCLE_AT)
    coverage.record(conn, now=CYCLE_AT)
    _settle(conn, hs=4, vs=1)
    conn.commit()
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    c = coverage.canonical(conn, [g], "spread", AFTER)[GID]
    assert c["record"]["line_source"] == "bookmaker" and c["record"]["market_quote_id"] is not None
    m, why = coverage.matched_quote(conn, "spread", g, c)
    assert why is None and m[2] == (c["result_side"] == "a")
    shifted = {**c, "record": {**c["record"], "line": c["record"]["line"] + 1}}
    assert coverage.matched_quote(conn, "spread", g, shifted) == (None, "quote line differs from the forecast line")
    early = {**c, "record": {**c["record"], "generated_at": "2026-10-12T21:00:00Z"}}       # before the 21:05Z quote
    assert coverage.matched_quote(conn, "spread", g, early) == (None, "quote retrieved after the forecast or the start")
    other = {**c, "market": "total"}
    assert coverage.matched_quote(conn, "total", g, other)[1] == "quote is not of this game and market"
    model = {**c, "record": {**c["record"], "line_source": "model"}}
    assert coverage.matched_quote(conn, "spread", g, model)[1].startswith("model line")
    client.now = AFTER
    perf = client.get("/api/canonical/performance", params={"sport": "nhl", "market": "team_total_home"}).json()
    mc = perf["market_comparison"]
    assert mc["matched_games"] == 0 and sum(mc["unmatched"].values()) == perf["competitive"]["graded"]


def test_canonical_endpoints_reject_a_market_the_sport_does_not_define(nhl):
    _, client = nhl
    assert client.get("/api/canonical/forecasts", params={"sport": "nhl", "market": "reg3"}).status_code == 200
    assert client.get("/api/canonical/performance", params={"sport": "nhl", "market": "bogus"}).status_code == 422


def test_the_coverage_record_is_per_market_and_reconciles_with_that_markets_threshold_rows(nhl):
    """Review 2026-10-14 (finding 1), reproduced: the card's record pooled all six markets (reg3 with two-way markets)
    while its per-market threshold table said it filtered 'that market's forecasts in the record above'."""
    conn, _ = nhl
    run(conn, CYCLE_AT)
    coverage.record(conn, now=CYCLE_AT)
    _settle(conn, hs=4, vs=1)
    rep = coverage.games_report(conn, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of=AFTER, with_source=False)
    for m in NHL_MARKETS:
        rec = rep["record_by_market"][m]
        assert rec["all"]["forecasts"] == 1 and rec["all"]["win"] + rec["all"]["loss"] + rec["all"]["push"] == rec["all"]["graded"]
        lowest = rep["thresholds_by_market"][m][0]["all"]
        assert lowest["forecasts"] <= rec["all"]["forecasts"]          # a filter of THIS market's record
        assert rep["thresholds_by_market"][m][0]["threshold"] == (0.35 if m == "reg3" else 0.5)
        assert lowest == rec["all"]                                    # every forecast clears its grid's lowest level
    assert sum(r["all"]["forecasts"] for r in rep["record_by_market"].values()) == rep["record"]["all"]["forecasts"]


def test_threshold_rows_count_graded_as_wins_plus_losses_like_the_summary():
    """Review 2026-10-14 (finding 2), reproduced: a push was 'graded' in the threshold rows but not in the summary."""
    c = {"market": "spread", "state": "forecast", "grade": "push", "result_side": "mid", "reason": None,
         "record": {"source": "primary_recorded", "model_version": "v", "confidence": 0.6, "p": [0.6, 0.0, 0.4]}}
    s = coverage.canonical_summary([(None, c)], "spread")
    assert s["graded"] == 0 and s["grades"]["push"] == 1
    assert s["thresholds"][0]["graded"] == 0 and s["thresholds"][0]["push"] == 1 and s["thresholds"][0]["win_rate"] is None


def test_a_reg3_comparison_without_a_quote_says_no_three_way_quotes_are_collected(nhl):
    """Review 2026-10-14 (finding 3): no regulation three-way bookmaker quote is ever collected, so the reason says so."""
    conn, _ = nhl
    run(conn, CYCLE_AT)
    coverage.record(conn, now=CYCLE_AT)
    _settle(conn, hs=4, vs=1)
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    c = coverage.canonical(conn, [g], "reg3", AFTER)[GID]
    assert coverage.matched_quote(conn, "reg3", g, c) == (None, "no regulation three-way bookmaker quotes are collected")


# --- /api/thresholds (tracking.report) picks, scores and filters a forecast as the canonical record does ---------------------
def _primary_variant(conn, market, **kw):
    """A later primary two-hour forecast of the game (same model version, still pregame) with the given fields changed."""
    from dataclasses import replace
    from app.ledger import MarketForecast, record_market
    r = conn.execute("SELECT * FROM market_forecasts WHERE market=? AND policy='near_tipoff' AND model_version NOT LIKE "
                     "'research-%' ORDER BY forecast_id DESC", (market,)).fetchone()
    f = MarketForecast(r["sport"], r["game_id"], r["market"], r["line"], r["line_source"], r["market_quote_id"], r["policy"],
                       r["run_type"], r["model_version"], r["dataset_version"], r["feature_version"], r["cutoff_at"],
                       r["generated_at"], r["tipoff_at"], r["p_a"], r["p_mid"], r["p_b"], r["params"])
    _, new = record_market(conn, replace(f, generated_at="2026-10-12T21:45:00Z", **kw))
    assert new


def _rows_with(groups, market, conf_grid):
    grp = next(x for x in groups if x["market"] == market)
    return [(t["threshold"], t["forecasts"], t["win"], t["loss"], t["push"]) for t in grp["thresholds"]]


@pytest.mark.parametrize("vec", [(0.31, 0.40, 0.29), (0.36, 0.38, 0.26)])
def test_the_threshold_table_picks_the_regulation_draw_as_the_canonical_forecast_does(game, vec):
    """reg3: the draw may be the pick (approved 2026-10-14). The threshold table used the two-way pick (never the draw) and
    graded a correct draw pick as a loss, or left it out of every row (audit F1)."""
    from app import tracking
    run(game, "2026-10-12T21:30:00Z")
    _primary_variant(game, "reg3", dataset_version="draw", p_a=vec[0], p_mid=vec[1], p_b=vec[2])
    coverage.record(game, now="2026-10-12T21:46:00Z", game_ids=[GID])
    add_upcoming(game, start="2026-10-12T19:00:00", state=7, hs=3, vs=2, period=4, at="2026-10-13T02:00:00Z")   # OT: a draw
    raw = game.execute("SELECT MAX(raw_id) FROM raw_responses").fetchone()[0]
    game.execute("INSERT OR REPLACE INTO game_decisions (game_id, decision, periods, last_raw_id) VALUES (?, 'OT', 4, ?)", (GID, raw))
    game.commit()
    g = game.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    canon = coverage.canonical(game, [g], "reg3", "2026-10-13T03:00:00Z")[GID]
    assert canon["record"]["side"] == "mid" and canon["grade"] == "win"
    want = [(t, int(canon["record"]["confidence"] >= t - 1e-9), int(canon["record"]["confidence"] >= t - 1e-9), 0, 0)
            for t in coverage.threshold_grid("reg3")]
    assert _rows_with(tracking.report(game, now="2026-10-13T03:00:00Z")["groups"], "reg3", None) == want


def test_the_threshold_table_uses_the_canonical_confidence_on_a_line_that_can_push(game):
    """Two-way line markets: confidence is the favoured side's probability given no push (coverage._confidence). The table
    used the raw probability, so a forecast at a whole-number line fell below the 50% row (audit F2)."""
    from app import tracking
    run(game, "2026-10-12T21:30:00Z")
    _primary_variant(game, "total", dataset_version="int-line", line=6.0, line_source="model", market_quote_id=None,
                     p_a=0.4529, p_mid=0.1289, p_b=0.4182)
    coverage.record(game, now="2026-10-12T21:46:00Z", game_ids=[GID])
    g = game.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    conf = coverage.canonical(game, [g], "total", "2026-10-12T21:50:00Z")[GID]["record"]["confidence"]
    assert 0.5 < conf < 0.55
    rows = _rows_with(tracking.report(game, now="2026-10-12T21:50:00Z")["groups"], "total", None)
    assert [(t, n) for t, n, *_ in rows] == [(t, int(conf >= t - 1e-9)) for t in coverage.threshold_grid("total")]


def test_the_winner_agreement_card_is_void_once_the_start_moved_after_its_cohort(game):
    """The game card's research agreement kept reading 'complete, 4 of 4 pick …' after the start moved; every record
    treats such forecasts as void (near_tipoff.start_changed) (audit F3)."""
    from app.providers.real import RealProvider
    run(game, "2026-10-12T21:30:00Z")
    before = RealProvider(game, now="2026-10-12T21:45:00Z").game(GID).agreement
    assert before.available and before.void is False and before.void_reason is None
    add_upcoming(game, start="2026-10-14T19:00:00", at="2026-10-12T22:00:00Z")      # the start moved after the cohort
    after = RealProvider(game, now="2026-10-12T22:05:00Z").game(GID).agreement
    assert after.void is True and "start" in after.void_reason


def test_a_reconstructed_regulation_draw_pick_is_graded_as_the_canonical_record_grades_it():
    """recovery._settle (recovered and adaptive-recovered tables): combine.pick may pick the draw, but the outcome went
    through market_live.result_for_pick, which scores every regulation draw as a loss of the pick (same class as audit F1).
    Its confidence was the raw side probability instead of coverage._confidence."""
    from app import recovery
    row = {"p_a": 0.31, "p_mid": 0.40, "p_b": 0.29, "market": "reg3", "status": "final", "current_start": "2026-10-12T23:00:00Z",
           "tipoff_at": "2026-10-12T23:00:00Z", "result_available_at": "2026-10-13T02:00:00Z", "home_score": 3, "away_score": 2,
           "line": None, "decision": "OT"}
    side, outcome = recovery._settle(row, "2026-10-13T03:00:00Z")
    assert (side, outcome) == (1, "win")
    assert recovery._settle({**row, "decision": "REG"}, "2026-10-13T03:00:00Z") == (1, "loss")
    total = {**row, "market": "total", "line": 5.0, "p_a": 0.45, "p_mid": 0.13, "p_b": 0.42, "decision": "REG"}
    assert recovery._settle(total, "2026-10-13T03:00:00Z") == (0, "push")                 # 3 + 2 = 5 on the line
    assert recovery.confidence(total) == coverage._confidence("total", (0.45, 0.13, 0.42))



def test_the_winner_agreement_card_is_void_when_the_game_is_postponed(game):
    """Review of F3: a canceled game whose start did not change still read '4 of 4 pick ...'."""
    from app.providers.real import RealProvider
    run(game, "2026-10-12T21:30:00Z")
    add_upcoming(game, start="2026-10-12T19:00:00", sched=5, at="2026-10-12T22:00:00Z")      # postponed, same start
    status = game.execute("SELECT status FROM games WHERE game_id=?", (GID,)).fetchone()[0]
    a = RealProvider(game, now="2026-10-12T22:05:00Z").game(GID).agreement
    assert status in ("postponed", "canceled") and a.void is True and status in a.void_reason
