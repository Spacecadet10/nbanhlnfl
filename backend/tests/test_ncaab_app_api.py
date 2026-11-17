"""NCAAB through the API: slate, detail, history, performance, ranked picks, the conference filter, the sportsbook
comparison and the refusals (synthetic scratch store, stand-in model)."""
import pytest
from fastapi.testclient import TestClient

from app import coverage, db, jobs, main
from app.providers.real import RealProvider
from ncaab_support import GAME, GAME_START, NOW, add_game, add_quote, install_college, make_store, seed

AFTER = "2026-11-18T05:00:00Z"            # GAME is final by then
DAY = "2026-11-17"


@pytest.fixture
def api(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)
    install_college(monkeypatch)
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    state = {"now": NOW}

    def provider(mode, sport):     # main.get_provider stays in force: sport validation and the NCAAB refusals are tested
        if sport != "ncaab":
            raise main.HTTPException(status_code=503, detail=f"no {sport} store in this test")
        c = db.connect(path, check_same_thread=False)
        try:
            yield RealProvider(c, now=state["now"])
        finally:
            c.close()
    monkeypatch.setattr(main, "_provider", provider)
    client = TestClient(main.app)
    client.state = state
    yield conn, client
    conn.close()


def forecast(conn, now=NOW):
    out = jobs.predict_now(conn, now=now)
    assert out["status"] == "success", out
    conn.commit()


def settle(conn, home=80, away=75, status="final"):
    conn.execute("UPDATE games SET status=?, home_score=?, away_score=?, result_first_seen_at=?, result_available_at=?, "
                 "result_availability_basis='observed_retrieval' WHERE game_id=?",
                 (status, home if status == "final" else None, away if status == "final" else None,
                  AFTER if status == "final" else None, AFTER if status == "final" else None, GAME))
    conn.commit()


Q = {"sport": "ncaab"}


def test_slate_lists_tracked_division_one_games_only_with_the_nba_card_layout(api):
    conn, client = api
    forecast(conn)
    body = client.get("/api/games", params={**Q, "date": DAY, "policy": "canonical"}).json()
    assert [g["game_id"] for g in body["games"]] == [GAME]                 # the non-D-I game on the date is never listed
    card = body["games"][0]
    assert card["home"]["abbr"] == "DUKE" and card["canonical"]["state"] == "forecast"
    assert card["canonical"]["record"]["minutes_before_start"] == 60.0
    assert card["near_tipoff"]["limitation"].startswith("The NCAAB model uses results only")
    assert card["agreement"]["status"] == "unavailable" and card["agreement"]["expected"] == 0
    assert client.get(f"/api/games/{'ncaab:402'}", params=Q).status_code == 404       # nor is its detail served


def test_meta_dates_and_sources_name_ncaab_and_never_the_nba_inputs(api):
    _, client = api
    meta = client.get("/api/meta", params=Q).json()
    assert meta["sport"] == "ncaab" and meta["score_unit"] == "points" and "2026-11-17" in meta["available_dates"]
    assert {s["id"] for s in meta["sports"]} >= {"nba", "nhl", "ncaab"}
    assert [m["id"] for m in meta["markets"]] == ["winner", "spread", "total", "team_total_home", "team_total_away"]
    assert meta["primary_model"].startswith("ncaab-")
    names = [s["name"] for s in client.get("/api/sources", params=Q).json()]
    assert "ESPN schedule & results (NCAA D-I)" in names and not any("NBA" in n or "NHL" in n for n in names)


def test_detail_has_every_market_and_the_model_line_label(api):
    conn, client = api
    forecast(conn)
    detail = client.get(f"/api/games/{GAME}", params=Q).json()
    assert {c["market"] for c in detail["canonical_markets"]} == {"winner", "spread", "total", "team_total_home", "team_total_away"}
    spread = next(c for c in detail["canonical_markets"] if c["market"] == "spread")
    assert spread["record"]["line_source"] == "model" and spread["record"]["line"] == -3.5
    assert spread["sportsbook"]["status"] == "model_line"


def test_history_performance_and_ranked_use_the_canonical_record_after_the_result(api):
    conn, client = api
    forecast(conn)
    settle(conn)
    client.state["now"] = AFTER
    hist = client.get("/api/canonical/forecasts", params=Q).json()
    row = next(r for r in hist["rows"] if r["game_id"] == GAME)
    assert row["canonical"]["grade"] in ("win", "loss") and row["outcome"]["home_won"] is True
    perf = client.get("/api/canonical/performance", params={**Q, "market": "winner"}).json()
    assert perf["competitive"]["graded"] == 1 and perf["recording_began_at"]
    ranked = client.get("/api/canonical/ranked", params={**Q, "date": DAY}).json()
    assert {p["market"] for p in ranked["picks"]} == {"winner", "spread", "total", "team_total_home", "team_total_away"}
    assert all(p["game_id"] == GAME for p in ranked["picks"])
    legacy = client.get("/api/predictions", params={**Q, "run_type": "live", "policy": "near_tipoff"}).json()
    assert legacy["total"] == 1 and legacy["predictions"][0]["correct"] is True
    near = client.get("/api/performance", params={**Q, "run_type": "live", "policy": "near_tipoff"}).json()
    assert near["sample_size"] == 1 and near["model_version"].startswith("ncaab-")


@pytest.mark.parametrize("status", ["postponed", "canceled"])
def test_a_postponed_or_canceled_game_is_void_and_never_graded(api, status):
    conn, client = api
    forecast(conn)
    settle(conn, status=status)
    client.state["now"] = AFTER
    hist = client.get("/api/canonical/forecasts", params=Q).json()
    assert {c["grade"] for c in [r["canonical"] for r in hist["rows"] if r["game_id"] == GAME]} == {"void"}
    legacy = client.get("/api/predictions", params={**Q, "run_type": "live", "policy": "near_tipoff"}).json()
    assert legacy["predictions"][0]["void"] is True and legacy["predictions"][0]["correct"] is None


def test_a_start_change_after_the_forecast_voids_it(api):
    conn, client = api
    forecast(conn)
    conn.execute("UPDATE games SET tipoff_at='2026-11-17T23:45:00Z' WHERE game_id=?", (GAME,))
    settle(conn)
    client.state["now"] = AFTER
    hist = client.get("/api/canonical/forecasts", params=Q).json()
    assert {r["canonical"]["grade"] for r in hist["rows"] if r["game_id"] == GAME} == {"void"}
    legacy = client.get("/api/predictions", params={**Q, "run_type": "live", "policy": "near_tipoff"}).json()
    assert legacy["predictions"][0]["void"] is True


def test_settlement_rules_overtime_counts_ties_void_whole_lines_push():
    from app import markets
    assert markets.settle("winner", None, status="final", home=70, away=70) == "void"
    assert markets.settle("total", 146.5, status="final", home=75, away=70) == "b"          # a full-game score, overtime included
    assert markets.settle("total", 145.0, status="final", home=75, away=70) == "mid"
    assert markets.settle("spread", -5.0, status="final", home=75, away=70) == "mid"
    assert markets.settle("spread", -3.5, status="final", home=75, away=70) == "a"
    assert markets.spec("ncaab", "winner").overtime == "overtime counts; winner ties void"


def test_sportsbook_comparison_needs_an_exact_quote_saved_at_or_before_the_forecast(api):
    conn, client = api
    add_quote(conn, GAME, "total", 142.5, -110, -110, "2026-11-17T22:00:00Z")
    add_quote(conn, GAME, "winner", None, -150, 130, "2026-11-17T22:10:00Z")
    add_quote(conn, GAME, "winner", None, -400, 300, "2026-11-17T22:40:00Z")      # after the forecast: never used
    forecast(conn)
    detail = client.get(f"/api/games/{GAME}", params=Q).json()
    by = {c["market"]: c["sportsbook"] for c in detail["canonical_markets"]}
    assert by["total"]["status"] == "exact" and by["total"]["line"] == 142.5 and by["total"]["gap_pp"] is not None
    assert by["winner"]["status"] == "exact" and by["winner"]["retrieved_at"] == "2026-11-17T22:10:00Z"
    assert by["spread"]["status"] == "model_line"
    assert by["team_total_home"]["status"] == "unsupported"          # no free feed carries team totals (as for the NBA)


def test_a_winner_with_no_quote_saved_by_the_forecast_says_so(api):
    conn, client = api
    add_quote(conn, GAME, "winner", None, -150, 130, "2026-11-17T22:40:00Z")      # later than the forecast
    forecast(conn)
    winner = next(c for c in client.get(f"/api/games/{GAME}", params=Q).json()["canonical_markets"] if c["market"] == "winner")
    assert winner["sportsbook"]["status"] == "no_quote" and winner["sportsbook"]["reason"] == "No moneyline saved by the forecast time."


def test_conference_filter_and_list_use_the_stored_membership_and_espn_names(api):
    conn, client = api
    assert client.get("/api/conferences", params=Q).json() == []          # no membership recorded yet: no options
    assert client.get("/api/games", params={**Q, "date": DAY, "conference": "ACC"}).json()["games"] == []
    conn.executemany("INSERT INTO ncaab_team_seasons (season, team_id, conference_id, is_d1, first_seen_at, updated_at) "
                     "VALUES (2027, ?, ?, ?, '2026-11-16T12:00:00Z', '2026-11-16T12:00:00Z')",
                     [("ncaab:1", "2", 1), ("ncaab:2", "2", 1), ("ncaab:3", "8", 1), ("ncaab:4", "23", 1), ("ncaab:5", None, 0)])
    conn.commit()
    assert client.get("/api/conferences", params=Q).json() == ["ACC", "Big 12", "SEC"]
    acc = client.get("/api/games", params={**Q, "date": DAY, "conference": "ACC"}).json()["games"]
    big12 = client.get("/api/games", params={**Q, "date": DAY, "conference": "Big 12"}).json()["games"]
    assert [g["game_id"] for g in acc] == [GAME] and big12 == []
    assert client.get("/api/games", params={"sport": "ncaab", "date": DAY, "conference": "x" * 81}).status_code == 422


def test_unknown_sports_are_still_refused_and_research_is_refused_for_ncaab(api):
    _, client = api
    assert client.get("/api/games", params={"sport": "nfl", "date": DAY}).status_code == 422
    assert client.get("/api/games", params={"sport": "cricket", "date": DAY}).status_code == 422
    for path in ("/api/research/slate?date=2026-11-17", "/api/agreement-performance", "/api/paper-rules", "/api/thresholds"):
        r = client.get(path, params=Q if "?" not in path else {"sport": "ncaab"})
        assert r.status_code == 404 and "NCAAB" in r.json()["detail"], path


def test_coverage_report_skips_the_raw_source_comparison_that_assumes_nba_keys(api):
    conn, client = api
    forecast(conn)
    body = client.get("/api/coverage", params={**Q, "date_from": DAY, "date_to": DAY}).json()
    assert body["source_schedule"]["available"] is False and "NCAAB" in body["source_schedule"]["reason"]
    assert body["sport"] == "ncaab"


def test_thresholds_market_history_and_performance_endpoints_work_for_every_ncaab_market(api):
    conn, client = api
    forecast(conn)
    settle(conn)
    client.state["now"] = AFTER
    thresholds = client.get("/api/canonical/thresholds", params=Q).json()
    assert thresholds["sport"] == "ncaab" and {m["market"] for m in thresholds["markets"]} == {
        "winner", "spread", "total", "team_total_home", "team_total_away"}
    for market in ("spread", "total", "team_total_home", "team_total_away"):
        hist = client.get("/api/market-predictions", params={**Q, "market": market, "policy": "near_tipoff"}).json()
        assert hist["total"] == 1 and hist["records"][0]["line_source"] == "model" if "records" in hist else hist["total"] == 1
        perf = client.get("/api/market-performance", params={**Q, "market": market, "policy": "near_tipoff"}).json()
        assert perf["market"] == market
        games = client.get("/api/games", params={**Q, "date": DAY, "market": market, "policy": "near_tipoff"}).json()
        assert games["games"][0]["market_forecast"]["line_source"] == "model"
        assert games["games"][0]["market_forecast"]["limitations"][0].startswith("No injury, line-up or travel")


def test_freshness_is_served_for_ncaab_and_unknown_leagues_are_refused(api, monkeypatch):
    conn, client = api
    from app import db
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.setattr(db, "connect_readonly", lambda sport: db.connect(path, check_same_thread=False))
    body = client.get("/api/freshness", params=Q).json()
    assert body["sport"] == "ncaab" and body["forecasts_supported"] is True
    assert client.get("/api/freshness", params={"sport": "cricket"}).status_code == 422
