"""Milestone 4: live forecasting, jobs, and the real-data API provider (synthetic data, no network)."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import backtest, db, ingest, jobs, live
from app.main import app, get_provider
from app.providers.real import RealProvider
from test_backtest import synthetic_rows

T_GAME = "2026-10-21T23:30:00Z"     # regular-season game, Eastern date 2026-10-21 → cutoff 2026-10-21T04:00:00Z
CUTOFF = "2026-10-21T04:00:00Z"
T_OTHER = "2026-10-20T23:00:00Z"    # earlier game between other teams; result arrives before CUTOFF
T_PRE = "2026-10-22T23:00:00Z"      # preseason exhibition: never forecast


def event(eid, tipoff, home, away, status="STATUS_SCHEDULED", hs=0, as_=0, season=2027, stype=2, odds=None):
    comps = [{"homeAway": side, "team": {"id": tid, "abbreviation": f"T{tid}", "displayName": f"Team {tid}"},
              "score": str(score)} for side, tid, score in (("home", home, hs), ("away", away, as_))]
    comp = {"neutralSite": False, "status": {"type": {"name": status}}, "competitors": comps}
    if odds:
        comp["odds"] = [{"provider": {"name": "Book"}, "moneyline": {
            "home": {"close": {"odds": odds[0]}}, "away": {"close": {"odds": odds[1]}}}}]
    return {"id": eid, "date": tipoff, "season": {"year": season, "type": stype}, "competitions": [comp]}


def ingest_events(conn, events, retrieved_at, month="202610"):
    body = json.dumps({"events": events}).encode()
    return ingest.ingest_month(conn, month, fetch=lambda _m: body, retrieved_at=retrieved_at)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """History for seasons 2020-2026, a sealed validation decision, and the upcoming 2026-27 slate."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "live.sqlite")
    db.migrate(conn)
    history = [event(r.game.game_id, r.game.tipoff_at, r.game.home_team_id.split(":")[1],
                     r.game.away_team_id.split(":")[1], "STATUS_FINAL", r.home_score, r.away_score, r.game.season)
               for r in synthetic_rows(per_season=12)]
    ingest_events(conn, history, "2026-09-30T00:00:00Z", month="202601")
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    decision = backtest.decision_from_validation_report(val, "synthetic")
    (tmp_path / "decision.json").write_text(json.dumps(decision))
    monkeypatch.setattr(backtest, "FROZEN_DECISION_PATH", tmp_path / "decision.json")
    ingest_events(conn, [event("g1", T_GAME, "1", "2", odds=("-150", "+130")), event("g0", T_OTHER, "3", "4"),
                         event("pre", T_PRE, "1", "5", stype=1)], "2026-10-19T12:00:00Z")
    yield conn
    conn.close()


def live_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM predictions WHERE run_type='live' ORDER BY prediction_id")]


def test_full_game_lifecycle_with_safe_reruns(store):
    # 1. Pregame forecast: only the eligible regular-season game inside the horizon; preseason is skipped.
    first = live.predict(store, now="2026-10-19T15:00:00Z")
    assert (first["eligible_games"], first["created"]) == (2, 2)  # g0 and g1
    rows = live_rows(store)
    g1 = next(r for r in rows if r["game_id"] == "espn:g1")
    assert g1["cutoff_at"] == CUTOFF and g1["generated_at"] == "2026-10-19T15:00:00Z" and g1["run_type"] == "live"

    # 2. Reruns with unchanged data are idempotent, even at a later clock time.
    assert live.predict(store, now="2026-10-19T15:00:00Z")["created"] == 0
    assert live.predict(store, now="2026-10-20T12:00:00Z")["created"] == 0
    assert len(live_rows(store)) == 2

    # 3. A new result (g0 final, observed 03:00Z) before g1's cutoff → a genuinely new g1 forecast version.
    ingest_events(store, [event("g0", T_OTHER, "3", "4", "STATUS_FINAL", 101, 99)], "2026-10-21T03:00:00Z")
    third = live.predict(store, now="2026-10-21T03:30:00Z")
    assert third["created"] == 1 and third["dataset_version"] != first["dataset_version"]
    versions = [r for r in live_rows(store) if r["game_id"] == "espn:g1"]
    assert len(versions) == 2 and versions[0] == g1  # the earlier forecast is unchanged

    # 4. After the cutoff nothing more is recorded for g1.
    assert live.predict(store, now="2026-10-21T05:00:00Z")["created"] == 0

    # 5. Result arrives; scoring uses the latest pre-cutoff version.
    ingest_events(store, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    scored = live.score(store)
    assert scored["live_forecasts"] == 2 and scored["scored"] == 2 and scored["pending"] == 0
    latest_p = versions[1]["home_win_prob"]
    g0_p = next(r for r in live_rows(store) if r["game_id"] == "espn:g0")["home_win_prob"]
    assert scored["brier"] == pytest.approx(((latest_p - 1) ** 2 + (g0_p - 1) ** 2) / 2)

    # 6. Live forecasts remain immutable.
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("UPDATE predictions SET home_win_prob = 0.5 WHERE game_id='espn:g1'")


def test_live_forecast_uses_only_results_available_at_generation(store):
    before = live.predict(store, now="2026-10-19T15:00:00Z")
    # A result for g0 retrieved *after* the forecast time must not be in the model state for that time.
    ingest_events(store, [event("g0", T_OTHER, "3", "4", "STATUS_FINAL", 130, 80)], "2026-10-21T03:00:00Z")
    assert all(r.game.game_id != "espn:g0" for r in live.available_rows(store, "2026-10-19T15:00:00Z"))
    again = live.predict(store, now="2026-10-19T15:00:00Z")
    assert again["dataset_version"] == before["dataset_version"] and again["created"] == 0


def test_ledger_rejects_a_live_forecast_after_cutoff(store):
    from app.ledger import Forecast, record
    with pytest.raises(sqlite3.IntegrityError, match="before cutoff"):
        record(store, Forecast("espn:g1", "live", "m", "d", "f", CUTOFF, "2026-10-21T05:00:00Z", 0.6))


# ---- jobs -----------------------------------------------------------------------------------------------------

def test_refresh_retries_then_succeeds_and_logs(store):
    calls = []

    def flaky(month):
        calls.append(month)
        if len(calls) < 3:
            raise ConnectionError("temporary")
        return json.dumps({"events": []}).encode()
    from datetime import datetime, timezone
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    out = jobs.refresh(store, fetch=flaky, sleep=lambda _s: None, now=now)
    assert out["status"] == "success" and out["attempts"] == 3 and out["months"] == ["202610"]
    assert jobs.last_success(store, "refresh") is not None
    # Quota uses the real clock of the job log: an immediate second refresh is skipped.
    skipped = jobs.refresh(store, fetch=flaky, sleep=lambda _s: None)
    assert skipped["status"] == "skipped" and "quota" in skipped["reason"]


def test_nightly_refresh_failure_is_visible_without_early_forecasts(store, monkeypatch):
    def down(_m):
        raise ConnectionError("down")
    out = jobs.daily(store, fetch=down, sleep=lambda _s: None)
    assert out["refresh"]["status"] == "failed" and out["refresh"]["attempts"] == jobs.RETRIES * 1
    assert "predict" not in out and out["score"]["status"] == "success"
    statuses = [r[0] for r in store.execute("SELECT status FROM job_runs ORDER BY run_id")]
    assert statuses == ["failed", "success"]
    assert "markets" not in out and "agreement" not in out


def test_refresh_months_cover_late_results_and_next_week():
    from datetime import datetime, timezone
    assert jobs.refresh_months(datetime(2026, 10, 30, 12, tzinfo=timezone.utc)) == ["202610", "202611"]
    assert jobs.refresh_months(datetime(2026, 11, 1, 3, tzinfo=timezone.utc)) == ["202610", "202611"]
    assert jobs.refresh_months(datetime(2026, 11, 15, 12, tzinfo=timezone.utc)) == ["202611"]


def test_scheduler_is_bounded_and_enforces_interval(store, monkeypatch):
    with pytest.raises(ValueError):
        jobs.scheduler(store, interval_minutes=5, max_runs=1)
    with pytest.raises(ValueError):
        jobs.scheduler(store, interval_minutes=60, max_runs=0)
    runs, sleeps = [], []
    monkeypatch.setattr(jobs, "daily", lambda conn: runs.append(1) or {"refresh": {"status": "success"}})
    jobs.scheduler(store, interval_minutes=60, max_runs=3, log=lambda _: None, sleep=sleeps.append)
    assert len(runs) == 3 and sleeps == [3600, 3600]


def test_odds_snapshot_fetches_only_days_with_games_soon_and_respects_quota(store):
    from datetime import datetime, timezone
    now = datetime(2026, 10, 21, 20, tzinfo=timezone.utc)
    # g1 tips off 2026-10-21 (ET) and the preseason game 2026-10-22; g0 has already started.
    assert jobs.odds_dates(store, now) == ["20261021", "20261022"]
    calls = []
    body = json.dumps({"events": [event("g1", T_GAME, "1", "2", odds=("-170", "+150"))]}).encode()
    empty = json.dumps({"events": []}).encode()
    out = jobs.odds_snapshot(store, fetch=lambda d: calls.append(d) or (body if d == "20261021" else empty),
                             sleep=lambda _s: None, now=now)
    assert out["status"] == "success" and calls == ["20261021", "20261022"]
    prices = [r[0] for r in store.execute("SELECT home_american FROM odds_quotes WHERE game_id='espn:g1' "
                                          "AND price_label='current' ORDER BY quote_id")]
    assert prices == [-150, -170]  # appended, never overwritten
    again = jobs.odds_snapshot(store, fetch=lambda d: body, sleep=lambda _s: None)
    assert again["status"] == "skipped" and "quota" in again["reason"]
    later = jobs.odds_snapshot(store, force=True, fetch=lambda d: body, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert later["status"] == "skipped" and "no scheduled game" in later["reason"]


def test_odds_snapshot_retries_then_fails_visibly(store):
    from datetime import datetime, timezone

    def down(_d):
        raise ConnectionError("down")
    out = jobs.odds_snapshot(store, fetch=down, sleep=lambda _s: None,
                             now=datetime(2026, 10, 21, 20, tzinfo=timezone.utc))
    assert out["status"] == "failed" and out["attempts"] == jobs.RETRIES and out["failed_date"] == "20261021"


def test_closing_summary_uses_last_quote_confirmed_before_tipoff(store):
    from app import coverage
    pre = json.dumps({"events": [event("g1", T_GAME, "1", "2", odds=("-170", "+150"))]}).encode()
    ingest.ingest_day(store, "20261021", fetch=lambda _d: pre, retrieved_at="2026-10-21T22:45:00Z")
    # Identical bytes re-fetched later: no new quote row, but the fetch confirms the price closer to tipoff.
    ingest.ingest_day(store, "20261021", fetch=lambda _d: pre, retrieved_at="2026-10-21T23:10:00Z")
    # A late start still shows 'scheduled' after the listed tipoff: that quote is not pregame and is excluded.
    late = json.dumps({"events": [event("g1", T_GAME, "1", "2", odds=("-400", "+300"))]}).encode()
    ingest.ingest_day(store, "20261021", fetch=lambda _d: late, retrieved_at="2026-10-21T23:40:00Z")
    ingest_events(store, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    rows = [dict(r) for r in store.execute(coverage.LAST_PREGAME_SQL)]
    assert [(r["game_id"], r["home_american"], r["confirmed_at"]) for r in rows] == \
        [("espn:g1", -170, "2026-10-21T23:10:00Z")]
    summary = coverage.closing_summary(store)
    assert summary["games_with_pregame_quote"] == 1 and summary["games_with_quote_within_60_min"] == 1
    assert summary["median_minutes_before_tipoff"] == 20.0


def test_odds_scheduler_is_bounded_with_its_own_minimum(store, monkeypatch):
    with pytest.raises(ValueError):
        jobs.scheduler(store, interval_minutes=5, max_runs=1, job="odds")
    with pytest.raises(ValueError):
        jobs.scheduler(store, interval_minutes=60, max_runs=1, job="refresh")
    runs, sleeps = [], []
    monkeypatch.setattr(jobs, "odds_snapshot", lambda conn: runs.append(1) or {"status": "success"})
    jobs.scheduler(store, interval_minutes=15, max_runs=2, job="odds", log=lambda _: None, sleep=sleeps.append)
    assert len(runs) == 2 and sleeps == [900]


def test_odds_job_migration_keeps_existing_job_runs(tmp_path):
    conn = db.connect(tmp_path / "m.sqlite")
    db.migrate(conn, upto=3)
    conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('refresh', '2026-09-30T00:00:00Z', 'success')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('odds', '2026-09-30T00:00:00Z', 'success')")
    assert db.migrate(conn) == ["0004_odds_job.sql", "0005_team_possession_stats.sql",
                                  "0006_possession_observations.sql", "0007_possession_fetch_events.sql", "0008_shadow_jobs.sql",
         "0009_box_fetch_attempts.sql", "0010_injury_report_attempts.sql", "0011_near_tipoff.sql",
         "0012_markets.sql", "0013_market_forecast_checks.sql",
         "0014_goalie_snapshots.sql", "0015_two_hour_forecasts.sql",
         "0016_research_selection.sql", "0017_nhl_shots.sql", "0018_model_fits.sql",
         "0019_candidate_forecasts.sql", "0020_pool_selection.sql", "0021_agreement_observations.sql", "0022_agreement_observation_inputs.sql",
         "0023_two_hour_guards.sql"]
    conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('odds', '2026-09-30T00:01:00Z', 'success')")
    assert [r[0] for r in conn.execute("SELECT job FROM job_runs ORDER BY run_id")] == ["refresh", "odds"]
    conn.close()


def test_cli_failure_detection():
    from app.cli import _any_failed
    assert _any_failed({"refresh": {"status": "failed"}, "predict": {"status": "success"}})
    assert not _any_failed({"refresh": {"status": "skipped", "imports": [{"games_seen": 1}]}})
    assert _any_failed([{"refresh": {"status": "success"}}, {"refresh": {"status": "failed"}}])


# ---- real-data API provider -----------------------------------------------------------------------------------

@pytest.fixture
def client(store):
    path = store.execute("PRAGMA database_list").fetchone()["file"]

    def provider():  # like production: a fresh connection per request
        conn = db.connect(path, check_same_thread=False)
        try:
            yield RealProvider(conn, now=client.now)
        finally:
            conn.close()
    client = TestClient(app)
    client.now = "2026-10-19T15:00:00Z"
    app.dependency_overrides[get_provider] = provider
    yield client
    app.dependency_overrides.pop(get_provider, None)


def test_real_api_shows_live_forecast_market_and_notes(store, client):
    live.predict(store, now="2026-10-19T15:00:00Z")
    body = client.get("/api/games", params={"date": "2026-10-21"}).json()
    assert body["mode"] == "real"
    (g1,) = body["games"]
    assert g1["prediction"]["run_type"] == "live" and g1["forecast_note"] is None
    assert g1["market"]["price_label"] == "current" and g1["market"]["quote_time"] is None
    assert g1["market"]["home_win_prob"] == pytest.approx(0.5798, abs=1e-3)
    pre = client.get("/api/games", params={"date": "2026-10-22"}).json()["games"][0]
    assert pre["season_type"] == "preseason" and pre["prediction"] is None and "preseason" in pre["forecast_note"]
    kinds = {s["kind"]: s["freshness"] for s in body["sources"]}
    assert kinds == {"schedule": "fresh", "odds": "fresh", "forecasts": "missing"}  # predict job not run via jobs


def test_real_api_backtest_label_and_warmup_note(store, client):
    past = client.get("/api/predictions", params={"run_type": "backtest"}).json()
    assert past["total"] == 0  # validation was not recorded to the ledger in this fixture
    old = store.execute("SELECT game_id, tipoff_at FROM games WHERE season = 2020 LIMIT 1").fetchone()
    from app.providers.real import eastern_date
    games = client.get("/api/games", params={"date": str(eastern_date(old["tipoff_at"]))}).json()["games"]
    assert games[0]["prediction"] is None and "warm-up" in games[0]["forecast_note"]


def test_real_api_team_stats_are_point_in_time(store, client):
    # Team 3 wins g0 (tipoff 2026-10-20T23:00Z); the result is observed at 2026-10-21T03:00Z, before the
    # 2026-10-21 cutoff (04:00Z). So it counts for team 3's game on 2026-10-21 (g2), but never for g0 itself.
    ingest_events(store, [event("g0", T_OTHER, "3", "4", "STATUS_FINAL", 101, 99)], "2026-10-21T03:00:00Z")
    ingest_events(store, [event("g2", "2026-10-21T23:00:00Z", "3", "5")], "2026-10-21T03:05:00Z")
    g0 = client.get("/api/games/espn:g0").json()
    g2 = client.get("/api/games/espn:g2").json()
    assert (g0["home_stats"]["wins"], g0["home_stats"]["last10_games"]) == (0, 0)
    assert (g2["home_stats"]["wins"], g2["home_stats"]["losses"], g2["home_stats"]["last10_games"]) == (1, 0, 1)
    assert g2["home_stats"]["rest_days"] == 1.0 and g2["stats_as_of"] == "2026-10-21T04:00:00Z"
    assert client.get("/api/games/espn:nope").status_code == 404


def test_real_api_history_and_performance_keep_run_types_separate(store, client):
    live.predict(store, now="2026-10-19T15:00:00Z")
    ingest_events(store, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    hist = client.get("/api/predictions", params={"run_type": "live"}).json()
    assert hist["run_type"] == "live" and hist["total"] == 2
    row = next(r for r in hist["predictions"] if r["game_id"] == "espn:g1")
    assert row["correct"] == (row["home_win_prob"] >= 0.5) and row["cutoff_at"] == CUTOFF
    perf = client.get("/api/performance", params={"run_type": "live"}).json()
    assert perf["run_type"] == "live" and perf["sample_size"] == 1 and perf["baselines"] == []
    assert perf["market"]["matched_games"] == 1  # quote retrieved 2026-10-19T12:00Z, before the 15:00Z forecast


def test_market_comparison_ignores_quotes_retrieved_after_the_forecast(store, client):
    """A quote retrieved after the forecast (even if before the cutoff) holds information the forecast lacked."""
    ingest_events(store, [event("g0", T_OTHER, "3", "4", odds=("-300", "+250"))], "2026-10-19T18:00:00Z")
    live.predict(store, now="2026-10-19T15:00:00Z")  # g0 has no quote at 15:00Z; g1 has one from 12:00Z
    ingest_events(store, [event("g0", T_OTHER, "3", "4", "STATUS_FINAL", 101, 99),
                          event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    perf = client.get("/api/performance", params={"run_type": "live"}).json()
    assert perf["sample_size"] == 2 and perf["market"]["matched_games"] == 1
    bt = client.get("/api/performance", params={"run_type": "backtest"}).json()
    assert bt["run_type"] == "backtest" and bt["sample_size"] == 0 and "Final test" in bt["title"]
    assert client.get("/api/performance", params={"run_type": "demo"}).status_code == 422


def test_real_api_meta_defaults_to_next_game_date(store, client):
    m = client.get("/api/meta").json()
    assert m["mode"] == "real" and m["default_date"] == "2026-10-20"
    assert [t["abbr"] for t in m["teams"]] == sorted(t["abbr"] for t in m["teams"]) and len(m["teams"]) == 6


def test_same_second_live_versions_resolve_to_one_forecast(store, client):
    """Two live versions of one game recorded in the same second (1 s timestamp precision) must yield exactly one
    'latest' forecast, the later-inserted one, everywhere: game detail, history, performance and scoring."""
    from app.ledger import Forecast, record
    mv = live.predict(store, now="2026-10-19T15:00:00Z")["model_version"]
    same_second = "2026-10-20T12:00:00Z"
    record(store, Forecast("espn:g1", "live", mv, "dataset-a", backtest.FEATURE_VERSION, CUTOFF, same_second, 0.61))
    later_id, _ = record(store, Forecast("espn:g1", "live", mv, "dataset-b", backtest.FEATURE_VERSION, CUTOFF,
                                         same_second, 0.72))
    ingest_events(store, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")

    assert client.get("/api/games/espn:g1").json()["prediction"]["home_win_prob"] == 0.72
    hist = client.get("/api/predictions", params={"run_type": "live"}).json()
    g1_rows = [r for r in hist["predictions"] if r["game_id"] == "espn:g1"]
    assert len(g1_rows) == 1 and g1_rows[0]["home_win_prob"] == 0.72 and hist["total"] == 2  # g1 + g0
    perf = client.get("/api/performance", params={"run_type": "live"}).json()
    assert perf["sample_size"] == 1 and perf["brier_score"] == pytest.approx((0.72 - 1) ** 2)
    scored = live.score(store)
    assert (scored["live_forecasts"], scored["scored"]) == (2, 1)
    assert scored["brier"] == pytest.approx((0.72 - 1) ** 2)
    assert store.execute(live.LATEST_LIVE_SQL + " AND p.game_id = 'espn:g1'", (mv,)).fetchall()[0]["prediction_id"] == later_id


# ---- confidence filter (max(p_home, 1 - p_home)) ------------------------------------------------------------------------

def confidence_store(store):
    """Six games with live forecasts at chosen probabilities; four are settled, two are still scheduled."""
    from app.ledger import Forecast, record
    mv = backtest.model_version(live.primary_model(store)[1])
    games = [("c1", "1", "2", 0.70, (110, 100)),    # home pick at exactly 70%, wins
             ("c2", "3", "4", 0.30, (110, 100)),    # away pick at exactly 70% (1 - 0.30), loses
             ("c3", "5", "6", 0.6999, (110, 100)),  # just under the threshold
             ("c4", "1", "3", 0.20, (90, 100)),     # away pick at 80%, wins
             ("c5", "2", "4", 0.85, None),          # 85% home pick, not played yet
             ("c6", "5", "1", 0.50, None)]          # coin flip, not played yet
    ingest_events(store, [event(g, f"2026-11-{10 + i:02d}T00:30:00Z", h, a) for i, (g, h, a, _, _) in enumerate(games)],
                  "2026-11-01T12:00:00Z", month="202611")
    for i, (g, _, _, p, _) in enumerate(games):
        tip = f"2026-11-{10 + i:02d}T00:30:00Z"
        record(store, Forecast(f"espn:{g}", "live", mv, "d", backtest.FEATURE_VERSION, backtest.cutoff_for(tip),
                               "2026-11-02T12:00:00Z", p))
    ingest_events(store, [event(g, f"2026-11-{10 + i:02d}T00:30:00Z", h, a, "STATUS_FINAL", *score)
                          for i, (g, h, a, _, score) in enumerate(games) if score], "2026-11-20T12:00:00Z", month="202611")


def history(client, **params):
    return client.get("/api/predictions", params={"run_type": "live", **params}).json()


def test_confidence_filter_boundaries_for_home_and_away_picks(store, client):
    confidence_store(store)
    everything = history(client)
    assert everything["total"] == 6 and everything["record"]["min_confidence"] is None
    by_id = {r["game_id"]: r for r in everything["predictions"]}
    assert (by_id["espn:c1"]["pick"], by_id["espn:c1"]["confidence"]) == ("home", 0.70)
    assert by_id["espn:c2"]["pick"] == "away" and by_id["espn:c2"]["confidence"] == pytest.approx(0.70)
    assert (by_id["espn:c6"]["pick"], by_id["espn:c6"]["confidence"]) == ("home", 0.5)

    def selected(threshold):
        return sorted(r["game_id"][5:] for r in history(client, min_confidence=threshold)["predictions"])
    assert selected(0.70) == ["c1", "c2", "c4", "c5"]        # exactly 70% counts, on either side; 69.99% does not
    assert selected(0.65) == ["c1", "c2", "c3", "c4", "c5"]
    assert selected(0.75) == ["c4", "c5"] and selected(0.80) == ["c4", "c5"] and selected(0.60) == selected(0.65)
    assert selected(0.5) == ["c1", "c2", "c3", "c4", "c5", "c6"] and selected(0.9) == []
    assert client.get("/api/predictions", params={"min_confidence": 0.4}).status_code == 422
    assert client.get("/api/predictions", params={"min_confidence": 1.1}).status_code == 422


def test_confidence_record_counts_only_settled_games_and_ignores_the_row_limit(store, client):
    confidence_store(store)
    rec = history(client, min_confidence=0.70)["record"]
    assert rec == {"min_confidence": 0.7, "selected": 4, "settled": 3, "wins": 2, "losses": 1, "pending": 1, "void": 0,
                   "unscorable": 0, "win_rate": pytest.approx(2 / 3)}                       # c5 is selected but not settled
    limited = history(client, min_confidence=0.70, limit=1)
    assert len(limited["predictions"]) == 1 and limited["total"] == 4 and limited["record"] == rec
    assert history(client)["record"] == {"min_confidence": None, "selected": 6, "settled": 4, "wins": 3, "losses": 1,
                                         "pending": 2, "void": 0, "unscorable": 0, "win_rate": 0.75}
    empty = history(client, min_confidence=0.9)
    assert empty["total"] == 0 and empty["predictions"] == []
    assert empty["record"] == {"min_confidence": 0.9, "selected": 0, "settled": 0, "wins": 0, "losses": 0, "pending": 0, "void": 0,
                               "unscorable": 0, "win_rate": None}
    unsettled = history(client, min_confidence=0.85)["record"]
    assert (unsettled["selected"], unsettled["settled"], unsettled["win_rate"]) == (1, 0, None)


def test_confidence_record_keeps_live_and_backtest_separate_and_predictions_unchanged(store, client):
    from app.ledger import Forecast, record
    confidence_store(store)
    before = [dict(r) for r in store.execute("SELECT * FROM predictions ORDER BY prediction_id")]
    mv = backtest.model_version(live.primary_model(store)[1])
    record(store, Forecast("espn:c1", "backtest", mv, "d", backtest.FEATURE_VERSION, "2026-11-10T05:00:00Z",
                           "2026-12-01T00:00:00Z", 0.90))
    live_rec = history(client, min_confidence=0.70)["record"]
    back = client.get("/api/predictions", params={"run_type": "backtest", "min_confidence": 0.70}).json()
    assert live_rec["selected"] == 4 and back["run_type"] == "backtest"
    assert back["record"] == {"min_confidence": 0.7, "selected": 1, "settled": 1, "wins": 1, "losses": 0, "pending": 0, "void": 0,
                              "unscorable": 0, "win_rate": 1.0}
    after = [dict(r) for r in store.execute("SELECT * FROM predictions WHERE run_type = 'live' ORDER BY prediction_id")]
    assert after == before        # filtering reads forecasts; it never changes them
