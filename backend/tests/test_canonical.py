"""The canonical forecast (one record per game and market, game_forecasts): the same record on Today, Game, History and
Performance; selection of the pregame version; later failed attempts; legacy ledgers kept apart; preseason shown as
missing (no validated model); missing forecasts counted as coverage gaps. Synthetic NBA store, no network."""
import pytest

from app import coverage, near_tipoff
from test_live import T_GAME, event, ingest_events  # noqa: F401
from test_live import client, store  # noqa: F401  (fixtures)
from test_near_tipoff import NOW, ready, refreshed  # noqa: F401  (fixture)

G1 = "espn:g1"                       # regular season, tips off 2026-10-21T23:30Z; NOW is one hour before
PRE = "espn:pre"                     # preseason, 2026-10-22T23:00Z
AFTER = "2026-10-22T04:00:00Z"       # g1 is final by then


def winner_rows(conn, gid=G1):
    return [dict(r) for r in conn.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market='winner' "
                                          "ORDER BY generated_at, forecast_id", (gid,))]


def card(client, date, gid, **params):
    body = client.get("/api/games", params={"date": date, **params}).json()
    return body, next(g for g in body["games"] if g["game_id"] == gid)


def history_row(client, gid):
    body = client.get("/api/canonical/forecasts").json()
    return body, next((r for r in body["rows"] if r["game_id"] == gid), None)


def record_now(conn, now=NOW):
    near_tipoff.predict_now(conn, now=now)          # the primary two-hour writer
    out = coverage.record(conn, now=now)            # the canonical ledger, last step of the cycle
    assert out["status"] == "success", out


def test_one_canonical_record_from_forecast_to_result_grade_and_performance(ready, client):
    record_now(ready)
    [row] = winner_rows(ready)
    assert row["status"] == "forecast" and row["source"] == "primary_recorded"
    client.now = NOW
    body, c = card(client, "2026-10-21", G1, policy="canonical")
    canon = c["canonical"]
    assert canon["state"] == "forecast" and canon["record"]["forecast_id"] == row["forecast_id"]
    assert canon["record"]["model_version"] == row["model_version"] and canon["record"]["source"] == "primary_recorded"
    assert canon["record"]["fallback_reason"].startswith("adaptive:")       # why the intended primary was not used
    assert canon["record"]["minutes_before_start"] == 60.0 and canon["grade"] == "pending"
    assert c["slate_pick"]["policy"] == "canonical"
    assert c["slate_pick"]["home_win_prob"] == pytest.approx(row["p_a"])
    assert body["selection"]["policy"] == "canonical" and body["selection"]["with_forecast"] == 1
    detail = client.get(f"/api/games/{G1}").json()["canonical"]
    assert detail["record"]["forecast_id"] == row["forecast_id"] and detail["state"] == "forecast"
    _, h = history_row(client, G1)
    assert h["canonical"]["record"]["forecast_id"] == row["forecast_id"] and h["canonical"]["grade"] == "pending"
    # The game is played: the same record is graded on every page and counted once in performance.
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = AFTER
    expected = "win" if row["p_a"] >= 0.5 else "loss"
    assert card(client, "2026-10-21", G1, policy="canonical")[1]["canonical"]["grade"] == expected
    assert client.get(f"/api/games/{G1}").json()["canonical"]["grade"] == expected
    hist, h = history_row(client, G1)
    assert h["canonical"]["record"]["forecast_id"] == row["forecast_id"] and h["canonical"]["grade"] == expected
    perf = client.get("/api/canonical/performance").json()
    comp = perf["competitive"]
    assert comp["games"] == 1 and comp["forecast"] == 1 and comp["missing"] == 0 and comp["coverage"] == 1.0
    assert comp["grades"]["win"] + comp["grades"]["loss"] == 1 and comp["graded"] == 1
    assert comp["accuracy"] == (1.0 if expected == "win" else 0.0)
    assert comp["by_source"] == {"primary_recorded": 1}
    assert comp["model_versions"] == [{"model_version": row["model_version"], "forecasts": 1}]
    assert perf["market_comparison"]["matched_games"] == 1          # the bookmaker quote retrieved before the forecast
    assert perf["recording_began_at"] == NOW and hist["record"]["graded"] == 1


def test_preseason_is_missing_with_the_same_reason_on_every_page_and_never_a_probability(ready, client):
    record_now(ready)                                 # recording began before the preseason game (g1's cycle)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    refreshed(ready, "2026-10-22T21:50:00Z")
    coverage.record(ready, now="2026-10-22T22:00:00Z")
    stored = winner_rows(ready, PRE)
    assert stored and stored[-1]["status"] == "forecast"   # the experimental fallback row is kept, unchanged
    client.now = "2026-10-22T22:05:00Z"
    _, c = card(client, "2026-10-22", PRE, policy="canonical")
    canon = c["canonical"]
    assert canon["state"] == "unvalidated" and canon["record"] is None and canon["grade"] is None
    assert "preseason" in canon["reason"].lower() and "validated" in canon["reason"]
    assert c["slate_pick"] is None
    assert client.get(f"/api/games/{PRE}").json()["canonical"] == canon
    _, h = history_row(client, PRE)
    assert h["canonical"] == canon
    client.now = "2026-10-23T03:00:00Z"
    perf = client.get("/api/canonical/performance").json()
    assert perf["preseason"]["games"] == 1 and perf["preseason"]["unvalidated"] == 1
    assert perf["preseason"]["experimental_rows"] >= 1
    assert perf["competitive"]["games"] == 1                       # g1 only: preseason never enters the cohort


def test_a_later_failed_attempt_keeps_the_earlier_forecast_selected_and_is_shown(ready, client, monkeypatch):
    record_now(ready)
    [first] = winner_rows(ready)
    # 55 minutes later the results are stale and no source can forecast: a 'missing' row is recorded.
    monkeypatch.setattr(coverage, "_primary_recorded", lambda *a: (None, "primary: no recorded two-hour winner forecast"))
    coverage.record(ready, now="2026-10-21T23:25:00Z")
    later = winner_rows(ready)[-1]
    assert later["status"] == "missing" and "stale results" in later["reason"]
    client.now = "2026-10-21T23:26:00Z"
    canon = client.get(f"/api/games/{G1}").json()["canonical"]
    assert canon["state"] == "forecast" and canon["record"]["forecast_id"] == first["forecast_id"]
    assert canon["later_failure"]["forecast_id"] == later["forecast_id"]
    assert "stale results" in canon["later_failure"]["reason"] and canon["attempts"] == 2


def test_a_source_that_returns_to_an_earlier_forecast_is_recorded_again_and_selected(ready, client, monkeypatch):
    """A -> B -> A: the third cycle's forecast must be its own latest record, not a retry of the first one."""
    record_now(ready)                                                        # A: the recorded primary
    real = coverage._primary_recorded
    monkeypatch.setattr(coverage, "_primary_recorded", lambda *a: (None, "primary: no recorded two-hour winner forecast"))
    coverage.record(ready, now="2026-10-21T22:35:00Z")                       # B: computed here
    monkeypatch.setattr(coverage, "_primary_recorded", real)
    coverage.record(ready, now="2026-10-21T22:40:00Z")                       # A again
    coverage.record(ready, now="2026-10-21T22:41:00Z")                       # a plain retry of A: no new row
    rows = winner_rows(ready)
    assert [r["source"] for r in rows] == ["primary_recorded", "primary_direct", "primary_recorded"]
    client.now = "2026-10-21T22:45:00Z"
    canon = client.get(f"/api/games/{G1}").json()["canonical"]
    assert canon["record"]["forecast_id"] == rows[-1]["forecast_id"] and canon["record"]["source"] == "primary_recorded"
    assert canon["later_failure"] is None and canon["attempts"] == 3


def test_legacy_forecasts_stay_in_their_views_and_a_missed_cycle_is_a_coverage_gap(ready, client):
    near_tipoff.predict_now(ready, now=NOW)                 # a legacy two-hour forecast only: no canonical ledger row yet
    client.now = NOW
    _, c = card(client, "2026-10-21", G1, policy="canonical")
    assert c["canonical"]["state"] == "not_recorded" and c["canonical"]["record"] is None
    assert "legacy" in c["canonical"]["reason"].lower() and c["slate_pick"] is None
    legacy = card(client, "2026-10-21", G1, policy="near_tipoff")[1]
    assert legacy["slate_pick"]["policy"] == "near_tipoff" and legacy["near_tipoff"] is not None
    # Canonical recording starts with g1's cycle; g2 starts later and no cycle runs within its two hours.
    ingest_events(ready, [event("g2", "2026-10-22T01:00:00Z", "7", "8")], "2026-10-21T12:00:00Z")
    coverage.record(ready, now=NOW)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = AFTER
    g2 = client.get("/api/games/espn:g2").json()["canonical"]
    assert g2["state"] == "missing" and "no forecast cycle ran" in g2["reason"] and g2["record"] is None
    _, h = history_row(client, "espn:g2")
    assert h["canonical"] == g2
    comp = client.get("/api/canonical/performance").json()["competitive"]
    assert (comp["games"], comp["forecast"], comp["missing"], comp["coverage"]) == (2, 1, 1, 0.5)
    assert comp["graded"] == 1 and comp["grades"]["loss"] + comp["grades"]["win"] == 1   # the gap is never a loss
    assert comp["missing_reasons"][0]["games"] == 1 and "no forecast cycle ran" in comp["missing_reasons"][0]["reason"]
    # The legacy record is unchanged and separate.
    assert client.get("/api/performance", params={"run_type": "live", "policy": "near_tipoff"}).json()["sample_size"] == 1


def test_a_forecast_for_a_moved_start_is_void_and_a_pending_game_is_pending(ready, client):
    record_now(ready)
    client.now = "2026-10-21T23:40:00Z"                      # started, not final
    assert client.get(f"/api/games/{G1}").json()["canonical"]["grade"] == "pending"
    ingest_events(ready, [event("g1", "2026-10-22T00:30:00Z", "1", "2")], "2026-10-21T23:41:00Z")   # start moved
    client.now = "2026-10-21T23:45:00Z"
    canon = client.get(f"/api/games/{G1}").json()["canonical"]
    assert canon["state"] == "forecast" and canon["grade"] == "void"


def test_canonical_endpoints_are_not_part_of_the_demo(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("NBA_API_MODE", "demo")
    c = TestClient(app)
    assert c.get("/api/canonical/forecasts").status_code == 404
    assert c.get("/api/canonical/performance").status_code == 404
    games = c.get("/api/games", params={"date": "2026-10-21", "policy": "canonical"})
    assert games.status_code == 200 and all(g["canonical"] is None for g in games.json()["games"])


# --- Review follow-up (findings 1, 3, 4): the Coverage card and the slate counts agree with the canonical record -------
def coverage_cell(client, date, gid, market="winner"):
    body = client.get("/api/coverage", params={"date_from": date, "date_to": date}).json()
    game = next(g for g in body["games"] if g["game_id"] == gid)
    return body, next(c for c in game["markets"] if c["market"] == market)


def test_the_coverage_card_shows_the_canonical_record_and_the_later_failure_apart(ready, client, monkeypatch):
    record_now(ready)
    [first] = winner_rows(ready)
    monkeypatch.setattr(coverage, "_primary_recorded", lambda *a: (None, "primary: no recorded two-hour winner forecast"))
    coverage.record(ready, now="2026-10-21T23:25:00Z")                       # stale results: a missing attempt
    later = winner_rows(ready)[-1]
    assert later["status"] == "missing"
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = AFTER
    canon = client.get(f"/api/games/{G1}").json()["canonical"]
    body, cell = coverage_cell(client, "2026-10-21", G1)
    assert cell["status"] == canon["grade"] and cell["forecast_id"] == canon["record"]["forecast_id"] == first["forecast_id"]
    assert cell["later_failure"]["forecast_id"] == later["forecast_id"] and "stale results" in cell["later_failure"]["reason"]
    w = body["by_market"]["winner"]
    assert (w["forecast"], w["missing"], w["graded"]) == (1, 0, 1)      # the later attempt is not a coverage gap


def test_preseason_is_unvalidated_on_the_coverage_card_and_its_rows_are_experimental(ready, client):
    record_now(ready)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    refreshed(ready, "2026-10-22T21:50:00Z")
    coverage.record(ready, now="2026-10-22T22:00:00Z")
    [exp] = [r for r in winner_rows(ready, PRE) if r["status"] == "forecast"]
    ingest_events(ready, [event("pre", "2026-10-22T23:00:00Z", "1", "5", "STATUS_FINAL", 99, 98, stype=1)], "2026-10-23T02:00:00Z")
    client.now = "2026-10-23T03:00:00Z"
    canon = client.get(f"/api/games/{PRE}").json()["canonical"]
    body, cell = coverage_cell(client, "2026-10-22", PRE)
    assert cell["status"] == "unvalidated" and cell["reason"] == canon["reason"] and "p" not in cell
    assert cell["experimental"]["forecast_id"] == exp["forecast_id"]
    assert cell["experimental"]["label"] == "Experimental / unvalidated" and cell["experimental"]["grade"] in ("win", "loss")
    assert body["by_market"]["winner"]["unvalidated"] == 1 and body["by_market"]["winner"]["forecast"] == 0
    others = [c for g in body["games"] if g["season_type"] == "preseason" for c in g["markets"] if c["market"] != "winner"]
    assert others and all(c.get("label") == "Experimental / unvalidated" for c in others if c["status"] != "missing")
    assert body["record"]["all"]["forecasts"] == 0                 # nothing preseason in the accepted (competitive) record
    assert body["preseason_record"]["label"] == "Experimental / unvalidated"


def test_the_coverage_card_does_not_fail_on_a_preseason_game_that_has_not_started(ready, client):
    client.now = "2026-10-22T20:00:00Z"                             # PRE starts at 23:00Z; nothing recorded yet
    r = client.get("/api/coverage", params={"date_from": "2026-10-22", "date_to": "2026-10-22"})
    assert r.status_code == 200
    cell = next(c for g in r.json()["games"] if g["game_id"] == PRE for c in g["markets"] if c["market"] == "winner")
    assert cell["status"] == "unvalidated"


def test_slate_counts_keep_games_without_a_forecast_apart_from_picks_below_the_threshold(ready, client):
    record_now(ready)
    ingest_events(ready, [event("g2", "2026-10-22T01:00:00Z", "7", "8")], "2026-10-21T12:00:00Z")   # same ET date, later
    client.now = NOW
    sel = client.get("/api/games", params={"date": "2026-10-21", "policy": "canonical", "min_confidence": 0.99}).json()["selection"]
    assert (sel["games"], sel["with_forecast"], sel["selected"], sel["below_threshold"]) == (2, 1, 0, 1)
    assert sel["without_forecast"] == {"upcoming": 1}
    pre = client.get("/api/games", params={"date": "2026-10-22", "policy": "canonical", "min_confidence": 0.7}).json()["selection"]
    assert pre["without_forecast"] == {"unvalidated": 1} and pre["below_threshold"] == 0 and pre["with_forecast"] == 0
