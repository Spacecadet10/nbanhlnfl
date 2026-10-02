"""Near-tipoff forecasts: separate policy, refusals, freshness record, display and scoring (synthetic data, no network)."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app import backtest, jobs, live, near_tipoff
from test_live import T_GAME, client, event, ingest_events, store  # noqa: F401  (fixtures)

NOW = "2026-10-21T22:30:00Z"        # one hour before g1 (teams 1 v 2) tips off at 23:30Z
REFRESHED = "2026-10-21T22:20:00Z"


def refreshed(conn, at=REFRESHED):
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('refresh', ?, ?, 'success')", (at, at))


def near_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM predictions WHERE model_version LIKE '%policy=near_tipoff' "
                                          "ORDER BY prediction_id")]


@pytest.fixture
def ready(store):  # noqa: F811
    """The other night's game is final and the results were refreshed ten minutes ago."""
    ingest_events(store, [event("g0", "2026-10-20T23:00:00Z", "3", "4", "STATUS_FINAL", 101, 99)], "2026-10-21T03:00:00Z")
    refreshed(store)
    return store


def test_forecast_is_recorded_before_tipoff_with_its_freshness_and_market(ready):
    out = near_tipoff.predict_now(ready, now=NOW)
    assert out["created"] == 1 and out["refused"] == [] and out["results_refreshed_at"] == REFRESHED
    f = out["forecasts"][0]
    assert (f["game_id"], f["minutes_before_tipoff"]) == ("espn:g1", 60.0) and 0 < f["home_win_prob"] < 1
    assert f["market_home_prob"] == pytest.approx(0.5798, abs=1e-3)       # quote retrieved 2026-10-19, before the forecast
    (row,) = near_rows(ready)
    assert (row["run_type"], row["cutoff_at"], row["generated_at"]) == ("live", T_GAME, NOW)
    assert row["model_version"] == near_tipoff.current_version(ready) != backtest.model_version(live.primary_model(ready)[1])
    assert row["feature_version"] == "pregame-ids-v1+tipoff-cutoff+two-hour-v1"
    detail = dict(ready.execute("SELECT * FROM near_tipoff_forecasts").fetchone())
    assert detail["prediction_id"] == row["prediction_id"] and detail["results_refreshed_at"] == REFRESHED
    assert detail["latest_result_available_at"] == "2026-10-21T03:00:00Z" and detail["market_quote_id"] is not None
    assert detail["results_used"] == out["results_used"] > 0


def test_uses_results_the_night_before_forecast_could_not(ready):
    # The night-before Elo forecast for g1 was cut off at 04:00Z; a result that arrived at 20:00Z is new information.
    night = live.predict(ready, now="2026-10-21T03:30:00Z")
    ingest_events(ready, [event("9001", "2026-10-21T17:00:00Z", "1", "5", "STATUS_FINAL", 130, 90)], "2026-10-21T20:00:00Z")
    out = near_tipoff.predict_now(ready, now=NOW)
    mv = night["model_version"]
    night_p = ready.execute("SELECT home_win_prob FROM predictions WHERE model_version = ? AND game_id = 'espn:g1'", (mv,)).fetchone()[0]
    assert out["forecasts"][0]["home_win_prob"] > night_p          # team 1 just won by 40
    assert out["dataset_version"] != night["dataset_version"]


def test_refused_after_tipoff_and_the_ledger_rejects_it_too(ready):
    from app.ledger import Forecast, record
    refreshed(ready, "2026-10-21T23:25:00Z")
    out = near_tipoff.predict_now(ready, now="2026-10-21T23:30:00Z")     # exactly at tipoff
    assert out["forecasts"] == [] and out["refused"][0]["reason"] == "after_tipoff" and near_rows(ready) == []
    late = near_tipoff.predict_now(ready, now="2026-10-22T00:10:00Z")
    assert [r["reason"] for r in late["refused"]] == ["after_tipoff"] and near_rows(ready) == []
    with pytest.raises(sqlite3.IntegrityError, match="before cutoff and tipoff"):
        record(ready, Forecast("espn:g1", "live", near_tipoff.current_version(ready), "d", near_tipoff.FEATURE_VERSION,
                               T_GAME, "2026-10-21T23:31:00Z", 0.6))


def test_refused_when_results_are_stale_or_never_refreshed(store):  # noqa: F811
    never = near_tipoff.predict_now(store, now=NOW)
    assert never["refused"][0]["reason"] == "stale_results" and "last: None" in never["refused"][0]["detail"]
    refreshed(store, "2026-10-21T21:29:00Z")                             # 61 minutes old
    old = near_tipoff.predict_now(store, now=NOW)
    assert old["refused"][0]["reason"] == "stale_results" and near_rows(store) == []
    refreshed(store, "2026-10-21T21:30:00Z")                             # exactly 60 minutes: still fresh
    assert near_tipoff.predict_now(store, now=NOW)["created"] == 1


def test_refused_while_a_teams_earlier_result_is_missing(ready):
    ingest_events(ready, [event("9001", "2026-10-21T19:00:00Z", "1", "5")], "2026-10-21T12:00:00Z")   # tipped 3.5 h ago
    out = near_tipoff.predict_now(ready, now=NOW)
    reasons = {r["game_id"]: r for r in out["refused"]}
    assert out["forecasts"] == [] and reasons["espn:g1"]["reason"] == "missing_result"
    assert reasons["espn:9001"]["reason"] == "after_tipoff"          # the unfinished game itself is listed too
    assert "espn:9001" in reasons["espn:g1"]["detail"] and near_rows(ready) == []
    ingest_events(ready, [event("9001", "2026-10-21T19:00:00Z", "1", "5", "STATUS_FINAL", 100, 99)], "2026-10-21T22:25:00Z")
    refreshed(ready, "2026-10-21T22:26:00Z")
    assert near_tipoff.predict_now(ready, now=NOW)["created"] == 1


def test_only_games_within_two_hours_and_not_preseason(ready):
    ingest_events(ready, [event("9002", "2026-10-22T23:00:00Z", "3", "4"),        # 24.5 h away
                          event("9003", "2026-10-22T02:00:00Z", "5", "6")], "2026-10-21T12:00:00Z")   # 3.5 h away
    out = near_tipoff.predict_now(ready, now=NOW)
    assert sorted(f["game_id"] for f in out["forecasts"]) == ["espn:g1"] and out["refused"] == []


def test_rerun_keeps_the_first_forecast_and_new_data_adds_a_version(ready):
    first = near_tipoff.predict_now(ready, now=NOW)
    again = near_tipoff.predict_now(ready, now="2026-10-21T22:45:00Z")
    assert (again["created"], again["already_recorded"]) == (0, 1) and len(near_rows(ready)) == 1
    assert near_rows(ready)[0]["generated_at"] == NOW
    ingest_events(ready, [event("9001", "2026-10-21T19:00:00Z", "1", "5", "STATUS_FINAL", 90, 120)], "2026-10-21T22:50:00Z")
    refreshed(ready, "2026-10-21T22:50:00Z")
    newer = near_tipoff.predict_now(ready, now="2026-10-21T23:00:00Z")
    rows = near_rows(ready)
    assert newer["created"] == 1 and len(rows) == 2 and rows[0]["generated_at"] == NOW     # earlier forecast preserved
    assert rows[1]["home_win_prob"] < rows[0]["home_win_prob"] and first["dataset_version"] != newer["dataset_version"]
    assert ready.execute("SELECT COUNT(*) FROM near_tipoff_forecasts").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ready.execute("UPDATE near_tipoff_forecasts SET results_used = 0")
    with pytest.raises(sqlite3.IntegrityError, match="conflicting insert rejected"):
        ready.execute("INSERT OR REPLACE INTO near_tipoff_forecasts (prediction_id, game_id, generated_at, tipoff_at, "
                      "results_refreshed_at, results_used) VALUES (?, 'espn:g1', ?, ?, ?, 1)",
                      (rows[0]["prediction_id"], NOW, T_GAME, REFRESHED))


def test_codex_retry_reports_the_retained_forecast_not_the_recomputed_one(ready):
    # Codex review: a retry at 22:45 reported "45 minutes before tipoff" for the forecast recorded at 22:30.
    first = near_tipoff.predict_now(ready, now=NOW)["forecasts"][0]
    assert (first["status"], first["generated_at"], first["minutes_before_tipoff"]) == ("created", NOW, 60.0)
    # The odds move, but no result changes: the stored forecast and its market snapshot are retained.
    ingest_events(ready, [event("g1", T_GAME, "1", "2", odds=("-400", "+300"))], "2026-10-21T22:40:00Z")
    retry = near_tipoff.predict_now(ready, now="2026-10-21T22:45:00Z")
    kept = retry["forecasts"][0]
    assert retry["created"] == 0 and retry["already_recorded"] == 1 and kept["status"] == "already_recorded"
    assert (kept["generated_at"], kept["minutes_before_tipoff"]) == (NOW, 60.0)
    assert kept["prediction_id"] == first["prediction_id"] and kept["home_win_prob"] == first["home_win_prob"]
    assert kept["market_home_prob"] == first["market_home_prob"] == pytest.approx(0.5798, abs=1e-3)   # not the new -400 line
    assert kept["market_retrieved_at"] == first["market_retrieved_at"] == "2026-10-19T12:00:00Z"
    stored = dict(ready.execute("SELECT market_home_prob, market_retrieved_at, generated_at FROM near_tipoff_forecasts").fetchone())
    assert stored == {"market_home_prob": first["market_home_prob"], "market_retrieved_at": "2026-10-19T12:00:00Z",
                      "generated_at": NOW}
    # A dry run is labelled as a fresh calculation and does see the newer quote.
    dry = near_tipoff.predict_now(ready, now="2026-10-21T22:45:00Z", write=False)["forecasts"][0]
    assert (dry["status"], dry["minutes_before_tipoff"]) == ("dry_run", 45.0) and "prediction_id" not in dry
    assert dry["market_home_prob"] == pytest.approx(0.7619, abs=1e-3) and dry["market_retrieved_at"] == "2026-10-21T22:40:00Z"


def test_dry_run_stores_nothing(ready):
    before = ready.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    out = near_tipoff.predict_now(ready, now=NOW, write=False)
    assert len(out["forecasts"]) == 1 and out["created"] == 0 and out["written"] is False
    assert ready.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == before
    assert ready.execute("SELECT COUNT(*) FROM near_tipoff_forecasts").fetchone()[0] == 0


def test_night_before_forecast_scoring_and_default_views_are_untouched(ready, client):  # noqa: F811
    live.predict(ready, now="2026-10-21T03:30:00Z")
    mv = backtest.model_version(live.primary_model(ready)[1])
    elo_before = [dict(r) for r in ready.execute("SELECT * FROM predictions WHERE model_version = ?", (mv,))]
    score_before = live.score(ready)
    client.now = NOW
    history_before = client.get("/api/predictions", params={"run_type": "live"}).json()
    near_tipoff.predict_now(ready, now=NOW)
    assert [dict(r) for r in ready.execute("SELECT * FROM predictions WHERE model_version = ?", (mv,))] == elo_before
    assert live.score(ready) == score_before
    assert client.get("/api/predictions", params={"run_type": "live"}).json() == history_before
    assert all(r["model_version"] == mv for r in history_before["predictions"])
    assert live.predict(ready, now="2026-10-21T03:45:00Z")["created"] == 0
    game = client.get("/api/games/espn:g1").json()
    assert game["prediction"]["model_version"] == mv                    # the night-before forecast is still the forecast
    assert game["near_tipoff"]["model_version"] == mv + "-window=2h-policy=near_tipoff"


def test_full_chain_fresh_data_to_displayed_pick_to_scored_result(ready, client):  # noqa: F811
    live.predict(ready, now="2026-10-21T03:30:00Z")
    near_tipoff.predict_now(ready, now=NOW)
    client.now = NOW
    card = next(g for g in client.get("/api/games", params={"date": "2026-10-21"}).json()["games"] if g["game_id"] == "espn:g1")
    nt = card["near_tipoff"]
    assert nt["minutes_before_tipoff"] == 60.0 and nt["pick"] in ("home", "away") and nt["correct"] is None
    assert nt["confidence"] == pytest.approx(max(nt["home_win_prob"], 1 - nt["home_win_prob"]))
    assert nt["results_refreshed_at"].startswith("2026-10-21T22:20") and "injuries" in nt["limitation"]
    assert nt["market_home_prob"] == pytest.approx(0.5798, abs=1e-3)
    hist = client.get("/api/predictions", params={"run_type": "live", "policy": "near_tipoff"}).json()
    assert hist["policy"] == "near_tipoff" and hist["total"] == 1 and hist["record"]["settled"] == 0
    # The game is played; the pick is scored under the near-tipoff record only.
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = "2026-10-22T04:00:00Z"
    after = client.get("/api/games/espn:g1").json()["near_tipoff"]
    assert after["correct"] == (after["home_win_prob"] >= 0.5)
    hist = client.get("/api/predictions", params={"run_type": "live", "policy": "near_tipoff"}).json()
    assert (hist["record"]["settled"], hist["record"]["wins"] + hist["record"]["losses"]) == (1, 1)
    perf = client.get("/api/performance", params={"run_type": "live", "policy": "near_tipoff"}).json()
    assert perf["policy"] == "near_tipoff" and perf["sample_size"] == 1 and "Near-tipoff" in perf["title"]
    assert perf["model_version"].endswith("-policy=near_tipoff") and perf["market"]["matched_games"] == 1
    night = client.get("/api/performance", params={"run_type": "live"}).json()
    assert night["policy"] == "pregame" and not night["model_version"].endswith("near_tipoff")
    s = near_tipoff.score(ready)
    assert s["scored"]["games"] == 1 and s["same_games_as_night_before"]["night_before"]["games"] == 1
    assert s["same_games_as_market"]["coverage"] == 1.0 and s["pending"] == 0
    assert client.get("/api/predictions", params={"run_type": "backtest", "policy": "near_tipoff"}).status_code == 422
    assert client.get("/api/performance", params={"run_type": "backtest", "policy": "near_tipoff"}).status_code == 422


def slate(client, **params):  # noqa: F811
    body = client.get("/api/games", params={"date": "2026-11-10", **params}).json()
    picks = {g["game_id"][5:]: g["slate_pick"] for g in body["games"]}
    return body["selection"], picks


def test_codex_slate_filter_uses_only_the_selected_policy(ready, client):  # noqa: F811
    # Codex review: night-before 65% with near-tipoff 80% vanished at 70%+, and 80% / 65% showed a below-range near pick.
    from app.ledger import Forecast, record
    elo, near = backtest.model_version(live.primary_model(ready)[1]), near_tipoff.current_version(ready)
    tip = "2026-11-10T23:30:00Z"       # Eastern date 2026-11-10; night-before cutoff 05:00Z, near-tipoff cutoff = tipoff
    ingest_events(ready, [event("s1", tip, "1", "2"), event("s2", tip, "3", "4"), event("s3", tip, "5", "6"),
                          event("s4", tip, "1", "3")], "2026-11-01T12:00:00Z", month="202611")
    night_cutoff = backtest.cutoff_for(tip)
    for game, night_p, near_p in (("s1", 0.65, 0.80), ("s2", 0.80, 0.65), ("s3", None, 0.25), ("s4", 0.30, None)):
        if night_p is not None:
            record(ready, Forecast(f"espn:{game}", "live", elo, "d", backtest.FEATURE_VERSION, night_cutoff,
                                   "2026-11-09T12:00:00Z", night_p))
        if near_p is not None:
            record(ready, Forecast(f"espn:{game}", "live", near, "d", near_tipoff.FEATURE_VERSION, tip,
                                   "2026-11-10T22:30:00Z", near_p))
    client.now = "2026-11-10T22:35:00Z"
    sel, picks = slate(client, policy="pregame", min_confidence=0.7)
    assert sel == {"market": "winner", "policy": "pregame", "min_confidence": 0.7, "games": 4, "with_forecast": 3, "selected": 2}
    assert {g: p and p["meets_confidence"] for g, p in picks.items()} == {"s1": False, "s2": True, "s3": None, "s4": True}
    assert picks["s4"]["pick"] == "away" and picks["s4"]["confidence"] == pytest.approx(0.70)      # away pick at exactly 70%
    assert all(p["policy"] == "pregame" for p in picks.values() if p)
    sel, picks = slate(client, policy="near_tipoff", min_confidence=0.7)
    assert sel == {"market": "winner", "policy": "near_tipoff", "min_confidence": 0.7, "games": 4, "with_forecast": 3, "selected": 2}
    assert {g: p and p["meets_confidence"] for g, p in picks.items()} == {"s1": True, "s2": False, "s3": True, "s4": None}
    assert picks["s1"]["confidence"] == 0.80 and picks["s3"]["pick"] == "away"
    # No threshold: every game with a forecast under the policy is selected; the other policy never fills a gap.
    sel, picks = slate(client, policy="near_tipoff")
    assert (sel["selected"], sel["with_forecast"], picks["s4"]) == (3, 3, None)
    sel, picks = slate(client)
    assert sel["policy"] == "pregame" and sel["selected"] == 3 and picks["s3"] is None
    # Both forecasts stay on the payload whichever policy is selected.
    body = client.get("/api/games", params={"date": "2026-11-10", "policy": "near_tipoff"}).json()
    s1 = next(g for g in body["games"] if g["game_id"] == "espn:s1")
    assert s1["prediction"]["home_win_prob"] == 0.65 and s1["near_tipoff"]["home_win_prob"] == 0.80
    assert client.get("/api/games", params={"date": "2026-11-10", "policy": "closing"}).status_code == 422
    assert client.get("/api/games", params={"date": "2026-11-10", "min_confidence": 0.3}).status_code == 422


def test_job_refreshes_first_and_reports_stale_data_as_failed(ready):
    empty = json.dumps({"events": []}).encode()
    at = datetime(2026, 10, 21, 22, 28, tzinfo=timezone.utc)
    kw = dict(refresh_kw={"fetch": lambda m: empty, "sleep": lambda s: None, "now": at},
              odds_kw={"fetch": lambda d: empty, "sleep": lambda s: None, "now": at},
              box_kw={"fetch": lambda g: b"{}", "sleep": lambda s: None, "now": at})
    out = jobs.predict_now(ready, now=NOW, **kw)
    assert out["status"] == "failed" and out["created"] == 1 and out["agreement"]["status"] == "failed" and set(out["steps"]) == {"refresh", "odds", "boxscores", "markets"}
    assert out["steps"]["markets"] == "success" and out["markets"]["created"] == 4   # spread, total, two team totals
    assert [r[0] for r in ready.execute("SELECT status FROM job_runs WHERE job = 'near_tipoff'")] == ["failed"]
    stale = jobs.predict_now(ready, now="2026-10-22T02:00:00Z", **kw)      # g1 has tipped off; nothing else is due
    assert stale["status"] == "skipped" or stale["refused"]
    ingest_events(ready, [event("9003", "2026-10-23T02:00:00Z", "5", "6")], "2026-10-22T12:00:00Z")
    blocked = jobs.predict_now(ready, now="2026-10-23T01:00:00Z", **kw)    # last successful refresh is a day old
    assert blocked["status"] == "failed" and blocked["refused"][0]["reason"] == "stale_results"
    dry = jobs.predict_now(ready, dry_run=True, now="2026-10-23T01:00:00Z", **kw)
    assert dry["status"] == "failed" and ready.execute("SELECT COUNT(*) FROM job_runs WHERE job = 'near_tipoff'").fetchone()[0] == 3


def test_demo_api_accepts_the_policy_parameter():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.providers.demo import DemoProvider
    from app.main import get_provider
    app.dependency_overrides[get_provider] = lambda: DemoProvider()
    try:
        c = TestClient(app)
        assert c.get("/api/predictions", params={"policy": "near_tipoff"}).json()["mode"] == "demo"
        assert all(g["near_tipoff"] is None for g in c.get("/api/games", params={"date": c.get("/api/meta").json()["default_date"]}).json()["games"])
    finally:
        app.dependency_overrides.pop(get_provider, None)


def test_rescheduled_game_is_void_in_every_near_tipoff_record(ready, client):  # noqa: F811
    """Audit P11: a forecast made for a start that later changed is void — never scored as a win or loss."""
    near_tipoff.predict_now(ready, now=NOW)
    # The game is moved 90 minutes later after the forecast was made, then played.
    moved = "2026-10-22T01:00:00Z"
    ingest_events(ready, [event("g1", moved, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T04:00:00Z")
    s = near_tipoff.score(ready)
    assert s["forecasts"] == 1 and s["scored"]["games"] == 0 and s["start_changed"] == 1 and s["pending"] == 0
    client.now = "2026-10-22T05:00:00Z"
    assert client.get("/api/games/espn:g1").json()["near_tipoff"]["correct"] is None
    hist = client.get("/api/predictions", params={"run_type": "live", "policy": "near_tipoff"}).json()
    assert hist["total"] == 1 and hist["record"]["settled"] == 0 and hist["predictions"][0]["correct"] is None
    perf = client.get("/api/performance", params={"run_type": "live", "policy": "near_tipoff"}).json()
    assert perf["sample_size"] == 0
