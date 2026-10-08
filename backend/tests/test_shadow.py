"""Step 6: sealed challenger, in-season possession refresh, and shadow forecasts kept apart from the live Elo."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app import backtest, ingest, jobs, live, shadow
from app.models import efficiency
from app.models.logistic import LogisticV2
from app.sources import espn
from test_backtest import synthetic_rows
from test_live import client, event, ingest_events, store  # noqa: F401  (fixtures)

NOW = "2026-10-19T15:00:00Z"
CSV_HEADER = ("game_id,season,season_type,team_id,team_home_away,team_score,field_goals_attempted,"
              "free_throws_attempted,offensive_rebounds,defensive_rebounds,total_turnovers\n")


def history_boxes() -> bytes:
    lines = []
    for r in synthetic_rows(per_season=12):
        for side, team, pts in (("home", r.game.home_team_id, r.home_score), ("away", r.game.away_team_id, r.away_score)):
            lines.append(f"{r.game.game_id},{r.game.season},2,{team.split(':')[1]},{side},{pts},88,22,10,33,13\n")
    return (CSV_HEADER + "".join(lines)).encode()


def summary(event_id, home, away, completed=True, home_tov="14", away_tov="12"):
    def team(tid, side, tov):
        return {"team": {"id": tid}, "homeAway": side, "statistics": [
            {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "40-88"},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "18-22"},
            {"name": "offensiveRebounds", "displayValue": "10"}, {"name": "defensiveRebounds", "displayValue": "33"},
            {"name": "totalTurnovers", "displayValue": tov}]}
    return json.dumps({
        "header": {"id": event_id, "competitions": [{"status": {"type": {"completed": completed}}, "competitors": [
            {"id": home[0], "homeAway": "home", "score": home[1]}, {"id": away[0], "homeAway": "away", "score": away[1]}]}]},
        "boxscore": {"teams": [team(home[0], "home", home_tov), team(away[0], "away", away_tov)]}}).encode()


@pytest.fixture
def sealed(store, tmp_path, monkeypatch):  # noqa: F811
    """The live-test store plus historical box scores and a sealed challenger."""
    ingest.ingest_team_box(store, 2026, fetch=lambda _s: history_boxes(), retrieved_at="2026-09-30T01:00:00Z",
                           historical_backfill=True)
    report = backtest.evaluate_screening(store, out_dir=tmp_path / "screen", log=lambda _: None)
    report["screening"]["shadow_shortlist"] = ["logistic_v2"]  # synthetic data: force the shortlist for the fixture
    path = tmp_path / "shadow_decision.json"
    path.write_text(json.dumps(shadow.build_decision(store, report, "synthetic")))
    monkeypatch.setattr(shadow, "SHADOW_DECISION_PATH", path)
    (tmp_path / "screening_report.json").write_text(json.dumps(report))
    return store


def screening_report() -> dict:
    return json.loads((shadow.SHADOW_DECISION_PATH.parent / "screening_report.json").read_text())


def rows_of(conn, where="1=1"):
    return [dict(r) for r in conn.execute(f"SELECT * FROM predictions WHERE {where} ORDER BY prediction_id")]


def elo_version(conn):
    return backtest.model_version(live.primary_model(conn)[1])


# ---- sealing ---------------------------------------------------------------------------------------------------

def test_seal_holds_the_complete_definition_and_promotion_rule(sealed, tmp_path):
    decision, cfg = shadow.load_shadow_decision(sealed)
    d = decision["definition"]
    assert decision["candidate"] == "logistic_v2" and decision["model_version"].startswith("logistic_v2-shadow-")
    assert d["logistic"]["features"] == ["elo_diff", "net_diff", "rest_diff", "b2b_home", "b2b_away", "neutral"]
    assert d["efficiency"]["half_life_days"] == 30.0 and d["stale_inputs"]["recent_games"] == 5
    assert d["elo"] == {"k": cfg.k, "home_advantage": cfg.home_advantage, "carry": cfg.carry, "mov": cfg.mov}
    assert decision["promotion_rule"]["minimum_games"] == 800 and "log loss" in decision["promotion_rule"]["promote_if"]
    assert decision["frozen_elo_sha256"] == json.loads(backtest.FROZEN_DECISION_PATH.read_text())["sha256"]


def test_seal_requires_the_shortlist_and_the_frozen_elo_config(sealed):
    report = screening_report()
    report["screening"]["shadow_shortlist"] = ["logistic"]
    with pytest.raises(shadow.ShadowDecisionError, match="not on the shadow shortlist"):
        shadow.build_decision(sealed, report, "x")
    report = screening_report()
    report["elo_config"] = {**report["elo_config"], "k": 99}
    with pytest.raises(shadow.ShadowDecisionError, match="frozen Elo configuration"):
        shadow.build_decision(sealed, report, "x")


def test_tampered_seal_or_drifted_code_is_rejected(sealed, monkeypatch):
    path = shadow.SHADOW_DECISION_PATH
    original = path.read_text()
    edited = json.loads(original)
    edited["promotion_rule"]["minimum_games"] = 100
    path.write_text(json.dumps(edited))
    with pytest.raises(shadow.ShadowDecisionError, match="digest mismatch"):
        shadow.predict(sealed, now=NOW)
    path.write_text(original)
    monkeypatch.setattr(efficiency, "HALF_LIFE_DAYS", 45.0)
    with pytest.raises(shadow.ShadowDecisionError, match="no longer matches the sealed challenger"):
        shadow.predict(sealed, now=NOW)
    monkeypatch.setattr(efficiency, "HALF_LIFE_DAYS", 30.0)
    assert shadow.predict(sealed, now=NOW)["eligible_games"] == 2   # restored code matches the seal again
    with pytest.raises(shadow.ShadowDecisionError, match="No sealed shadow decision"):
        shadow.predict(sealed, now=NOW, decision_path=path.parent / "missing.json")


# ---- shadow forecasts ------------------------------------------------------------------------------------------

def test_dry_run_writes_nothing(sealed):
    before = (rows_of(sealed), sealed.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0])
    out = shadow.predict(sealed, now=NOW)
    assert out["written"] is False and out["eligible_games"] == 2 and len(out["forecasts"]) == 2
    assert all(0 < f["home_win_prob"] < 1 for f in out["forecasts"])
    assert (rows_of(sealed), sealed.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0]) == before


def test_shadow_rows_are_separate_and_elo_is_unchanged(sealed, client):  # noqa: F811
    live.predict(sealed, now=NOW)
    mv = elo_version(sealed)
    elo_before, score_before = rows_of(sealed, f"model_version = '{mv}'"), live.score(sealed)
    api_before = client.get("/api/predictions", params={"run_type": "live"}).json()
    out = shadow.predict(sealed, now=NOW, write=True)
    assert out["created"] == 2 and out["model_version"] != mv
    shadow_rows = rows_of(sealed, f"model_version = '{out['model_version']}'")
    assert len(shadow_rows) == 2 and {r["run_type"] for r in shadow_rows} == {"live"}
    assert {r["feature_version"] for r in shadow_rows} == {backtest.SCREEN_FEATURE_VERSION}
    assert all(r["generated_at"] == NOW and r["generated_at"] < r["cutoff_at"] for r in shadow_rows)
    # The Elo forecasts, their scoring and everything the app shows are exactly as before.
    assert rows_of(sealed, f"model_version = '{mv}'") == elo_before and live.score(sealed) == score_before
    assert client.get("/api/predictions", params={"run_type": "live"}).json() == api_before
    assert all(p["model_version"] == mv for p in api_before["predictions"])
    assert client.get("/api/games/espn:g1").json()["prediction"]["model_version"] == mv
    assert client.get("/api/meta").json()["primary_model"] == mv
    assert live.predict(sealed, now=NOW)["created"] == 0  # the Elo job is unaffected by shadow rows


def test_rerun_is_idempotent_and_new_data_makes_a_new_version(sealed):
    first = shadow.predict(sealed, now=NOW, write=True)
    again = shadow.predict(sealed, now="2026-10-19T18:00:00Z", write=True)
    assert (again["created"], again["already_recorded"]) == (0, 2) and again["dataset_version"] == first["dataset_version"]
    ingest_events(sealed, [event("g0", "2026-10-20T23:00:00Z", "3", "4", "STATUS_FINAL", 101, 99)], "2026-10-21T03:00:00Z")
    third = shadow.predict(sealed, now="2026-10-21T03:30:00Z", write=True)
    assert third["created"] == 1 and third["dataset_version"] != first["dataset_version"]
    versions = rows_of(sealed, f"model_version = '{first['model_version']}' AND game_id = 'espn:g1'")
    assert len(versions) == 2 and versions[0]["generated_at"] == NOW  # the earlier forecast is unchanged
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        sealed.execute("UPDATE predictions SET home_win_prob = 0.5 WHERE model_version = ?", (first["model_version"],))


def test_shadow_forecast_equals_the_backtest_walk_including_scheduled_rest_days(sealed):
    # Team 1 also plays the night before g1, so g1 is a back-to-back that only the schedule reveals.
    ingest_events(sealed, [event("9002", "2026-10-20T23:30:00Z", "1", "6")], "2026-10-19T13:00:00Z")
    out = shadow.predict(sealed, now=NOW)
    p_shadow = {f["game_id"]: f["home_win_prob"] for f in out["forecasts"]}
    _, cfg = backtest.load_frozen_decision(sealed)
    rows = live.available_rows(sealed, NOW)
    events = backtest.load_box_events(sealed, "live")

    def walk(game_ids):
        future = [backtest.Row(backtest._pregame(g), backtest.cutoff_for(g["tipoff_at"]), 100, 99, "2027-06-01T00:00:00Z")
                  for g in sealed.execute("SELECT * FROM games WHERE status = 'scheduled' AND season_type = 'regular'")
                  if g["game_id"] in game_ids]
        return {p["game_id"]: round(p["p_home"], 9) for p in backtest.run(LogisticV2(cfg), rows + future, events)}
    full = walk({"espn:g0", "espn:g1", "espn:9002"})
    assert all(p_shadow[g] == full[g] for g in ("espn:g0", "espn:g1", "espn:9002"))
    assert walk({"espn:g0", "espn:g1"})["espn:g1"] != full["espn:g1"]  # without the scheduled game the rest differs


def test_stale_inputs_skip_the_forecast_until_the_box_score_is_available(sealed):
    ingest_events(sealed, [event("9001", "2026-10-19T23:00:00Z", "1", "5", "STATUS_FINAL", 110, 100)], "2026-10-20T05:00:00Z")
    out = shadow.predict(sealed, now="2026-10-20T12:00:00Z", write=True)
    assert out["eligible_games"] == 1 and out["created"] == 0 and out["forecasts"] == []
    assert out["skipped_stale_inputs"] == [{"game_id": "espn:g1", "missing_box_scores": {"espn:1": ["espn:9001"]}}]
    body = summary("9001", ("1", "110"), ("5", "100"))
    ingest.ingest_box_summary(sealed, "espn:9001", fetch=lambda _g: body, retrieved_at="2026-10-20T13:00:00Z")
    early = shadow.predict(sealed, now="2026-10-20T12:30:00Z", write=True)   # before the box was retrieved
    assert early["created"] == 0 and len(early["skipped_stale_inputs"]) == 1
    late = shadow.predict(sealed, now="2026-10-20T14:00:00Z", write=True)
    assert late["created"] == 1 and late["skipped_stale_inputs"] == []


def test_discontinued_shadow_job_refuses_before_writing(sealed):
    """Night-before shadow recording is discontinued under the two-hour policy (review finding 6): the job path refuses
    before logging a run or writing a forecast. Stale-input reporting of shadow.predict itself is tested above."""
    before = sealed.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    with pytest.raises(RuntimeError, match="discontinued"):
        jobs.shadow_forecasts(sealed, now=NOW)
    assert sealed.execute("SELECT COUNT(*) FROM job_runs WHERE job = 'shadow'").fetchone()[0] == 0
    assert sealed.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == before


def test_daily_runs_shadow_steps_only_when_enabled(sealed, monkeypatch):
    empty = json.dumps({"events": []}).encode()
    monkeypatch.delenv("NBA_SHADOW_RECORDING", raising=False)
    out = jobs.daily(sealed, fetch=lambda _m: empty, sleep=lambda _s: None)
    assert list(out) == ["refresh", "score", "two_hour_score", "market_score", "forecast_policy", "training_check", "adaptive_train"]
    assert out["training_check"].get("status") != "failed"
    assert sealed.execute("SELECT COUNT(*) FROM job_runs WHERE job IN ('boxscores', 'shadow')").fetchone()[0] == 0
    monkeypatch.setenv("NBA_SHADOW_RECORDING", "1")
    monkeypatch.setattr(jobs, "boxscores", lambda conn: {"status": "skipped"})
    monkeypatch.setattr(jobs, "shadow_forecasts", lambda conn: {"status": "success"})
    out = jobs.daily(sealed, force_refresh=True, fetch=lambda _m: empty, sleep=lambda _s: None)
    assert list(out) == ["refresh", "boxscores", "score", "two_hour_score", "market_score", "forecast_policy", "training_check",
                         "adaptive_train"]


# ---- possession refresh ----------------------------------------------------------------------------------------

def test_parse_summary_box_reads_lines_and_never_guesses_missing_values():
    box = espn.parse_summary_box(json.loads(summary("9001", ("1", "110"), ("5", "100"), away_tov="--")))
    assert box.game_id == "espn:9001" and box.completed
    home, away = box.lines
    assert (home.team_id, home.is_home, home.points, home.fga, home.fta, home.oreb, home.dreb, home.tov) == \
        ("espn:1", 1, 110, 88, 22, 10, 33, 14)
    assert away.tov is None and away.points == 100
    assert espn.parse_summary_box({}).lines == [] and not espn.parse_summary_box({}).completed
    with pytest.raises(ValueError):
        espn.summary_request("espn:not-a-number")


def at(day_hour: str) -> datetime:
    return datetime.strptime(f"2026-10-{day_hour}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc)


def test_refresh_fetches_recent_finals_with_true_retrieval_times_then_rechecks_once(sealed):
    ingest_events(sealed, [event("9001", "2026-10-19T23:00:00Z", "1", "5", "STATUS_FINAL", 110, 100)], "2026-10-20T05:00:00Z")
    calls = []

    def fetch(game_id):
        calls.append(game_id)
        return summary("9001", ("1", "110"), ("5", "100"), home_tov=str(13 + len(calls)))
    first = jobs.boxscores(sealed, fetch=fetch, sleep=lambda _s: None, now=at("20 06"))
    assert first["status"] == "success" and first["requested"] == 1 and first["still_missing"] == []
    assert first["fetched"][0]["complete_lines"] == 2 and calls == ["espn:9001"]
    obs = [dict(r) for r in sealed.execute(
        "SELECT o.*, f.retrieved_at FROM team_possession_observations o JOIN raw_fetches f USING (fetch_id) "
        "WHERE o.game_id = 'espn:9001'")]
    assert len(obs) == 2 and all(o["observed_at"] == o["retrieved_at"] == o["available_at"] for o in obs)
    assert {o["availability_basis"] for o in obs} == {"observed_retrieval"}
    # Historical games outside the lookback window are never requested; nothing more is needed yet.
    assert jobs.boxscores(sealed, fetch=fetch, sleep=lambda _s: None, now=at("20 12"))["status"] == "skipped"
    # 24 h after tipoff the box is re-fetched once (corrections); the revised value is a new observation.
    again = jobs.boxscores(sealed, fetch=fetch, sleep=lambda _s: None, now=at("21 00"))
    assert again["requested"] == 1 and again["fetched"][0]["changed_vs_previous"] == 1 and len(calls) == 2
    assert sealed.execute("SELECT COUNT(*) FROM team_possession_observations WHERE game_id='espn:9001'").fetchone()[0] == 4


def test_refresh_reports_incomplete_unfinished_and_failed_games_and_gives_up(sealed):
    ingest_events(sealed, [event("9001", "2026-10-19T23:00:00Z", "1", "5", "STATUS_FINAL", 110, 100),
                           event("9003", "2026-10-19T23:30:00Z", "2", "6", "STATUS_FINAL", 99, 98)], "2026-10-20T05:00:00Z")

    def fetch(game_id):
        if game_id == "espn:9003":
            raise ConnectionError("down")
        return summary("9001", ("1", "110"), ("5", "100"), away_tov="--")  # away turnovers missing
    out = jobs.boxscores(sealed, fetch=fetch, sleep=lambda _s: None, now=at("20 06"))
    assert out["status"] == "failed" and out["failed"][0]["game_id"] == "espn:9003"
    assert out["fetched"][0]["complete_lines"] == 1 and sorted(out["still_missing"]) == ["espn:9001", "espn:9003"]
    # A summary ESPN has not marked completed records nothing.
    pending = summary("9001", ("1", "110"), ("5", "100"), completed=False)
    res = ingest.ingest_box_summary(sealed, "espn:9001", fetch=lambda _g: pending)
    assert res["recorded"] == 0 and res["skipped_not_completed"] == 2
    for _ in range(jobs.MAX_BOX_ATTEMPTS):
        jobs.boxscores(sealed, fetch=lambda g: pending, sleep=lambda _s: None, now=at("20 08"))
    final = jobs.boxscores(sealed, fetch=lambda g: pending, sleep=lambda _s: None, now=at("20 09"))
    assert "espn:9001" in final["gave_up"] and final["requested"] <= 1


def test_replay_rebuilds_summary_observations(sealed):
    ingest_events(sealed, [event("9001", "2026-10-19T23:00:00Z", "1", "5", "STATUS_FINAL", 110, 100)], "2026-10-20T05:00:00Z")
    body = summary("9001", ("1", "110"), ("5", "100"))
    ingest.ingest_box_summary(sealed, "espn:9001", fetch=lambda _g: body, retrieved_at="2026-10-20T13:00:00Z")
    before = [dict(r) for r in sealed.execute("SELECT * FROM team_possession_observations ORDER BY game_id, team_id, fetch_id")]
    assert any(o["game_id"] == "espn:9001" and o["observed_at"] == "2026-10-20T13:00:00Z" for o in before)
    totals = ingest.replay(sealed)   # replays scoreboards, then season files and game summaries in time order
    assert totals["responses"] >= 3
    assert [dict(r) for r in sealed.execute(
        "SELECT * FROM team_possession_observations ORDER BY game_id, team_id, fetch_id")] == before


# ---- request budget (Codex step 6 review): every HTTP attempt counts, including retries and failures ----------------

def recent_finals(conn, n, first_id=9100):
    ingest_events(conn, [event(str(first_id + i), "2026-10-19T23:00:00Z", str(1 + i % 6), str(1 + (i + 1) % 6),
                               "STATUS_FINAL", 110, 100) for i in range(n)], "2026-10-20T05:00:00Z")


def test_codex_outage_never_exceeds_the_per_run_attempt_budget(sealed):
    recent_finals(sealed, 35)
    calls = []

    def down(game_id):
        calls.append(game_id)
        raise ConnectionError("down")
    out = jobs.boxscores(sealed, fetch=down, sleep=lambda _s: None, now=at("20 06"))
    assert len(calls) == jobs.MAX_BOX_REQUESTS == 30 and out["http_attempts"] == 30 and out["attempts"] == 30
    assert out["status"] == "failed" and len(out["failed"]) == 10 and out["deferred"] == 25
    assert all(f["attempts"] == jobs.RETRIES for f in out["failed"])
    rows = sealed.execute("SELECT outcome, COUNT(*) FROM box_fetch_attempts GROUP BY 1").fetchall()
    assert [tuple(r) for r in rows] == [("failed", 30)]
    assert sealed.execute("SELECT COUNT(*) FROM raw_fetches f JOIN raw_responses r USING (raw_id) "
                          "WHERE r.source = 'espn_summary'").fetchone()[0] == 0


def test_failed_attempts_use_the_lifetime_allowance_across_runs(sealed):
    recent_finals(sealed, 1)
    calls = []

    def down(game_id):
        calls.append(game_id)
        raise ConnectionError("down")
    first = jobs.boxscores(sealed, fetch=down, sleep=lambda _s: None, now=at("20 06"))
    assert len(calls) == 3 and first["failed"][0]["attempts"] == 3 and first["gave_up"] == []
    second = jobs.boxscores(sealed, fetch=down, sleep=lambda _s: None, now=at("20 07"))
    assert len(calls) == 5 and second["failed"][0]["attempts"] == 2      # allowance exhausted mid-retry
    assert second["gave_up"] == ["espn:9100"] and second["still_missing"] == []
    third = jobs.boxscores(sealed, fetch=down, sleep=lambda _s: None, now=at("20 08"))
    assert len(calls) == 5 and third["status"] == "skipped" and third["gave_up"] == ["espn:9100"]
    assert jobs.box_attempts(sealed, "espn:9100") == jobs.MAX_BOX_ATTEMPTS


def test_run_budget_exhausted_mid_retry_stops_immediately(sealed, monkeypatch):
    recent_finals(sealed, 3)
    monkeypatch.setattr(jobs, "MAX_BOX_REQUESTS", 4)
    calls = []

    def down(game_id):
        calls.append(game_id)
        raise ConnectionError("down")
    out = jobs.boxscores(sealed, fetch=down, sleep=lambda _s: None, now=at("20 06"))
    assert calls == ["espn:9100"] * 3 + ["espn:9101"] and out["http_attempts"] == 4
    assert [f["attempts"] for f in out["failed"]] == [3, 1] and out["deferred"] == 1


def test_codex_slow_response_is_stamped_when_it_arrives_not_when_requested(sealed):
    # Codex re-review: request starts 03:59:59Z, response arrives 04:00:05Z (after a 04:00:00Z cutoff).
    recent_finals(sealed, 1)
    ticks = iter(["2026-10-21T03:59:59Z", "2026-10-21T04:00:05Z"])
    body = summary("9100", ("1", "110"), ("2", "100"))
    out = jobs.boxscores(sealed, fetch=lambda _g: body, sleep=lambda _s: None, clock=lambda: next(ticks),
                         now=datetime(2026, 10, 21, 3, 59, 59, tzinfo=timezone.utc))
    assert out["status"] == "success" and out["http_attempts"] == 1
    obs = [dict(r) for r in sealed.execute(
        "SELECT o.observed_at, o.available_at, f.retrieved_at FROM team_possession_observations o "
        "JOIN raw_fetches f USING (fetch_id) WHERE o.game_id = 'espn:9100'")]
    assert len(obs) == 2 and all(set(o.values()) == {"2026-10-21T04:00:05Z"} for o in obs)
    assert sealed.execute("SELECT attempted_at FROM box_fetch_attempts").fetchone()[0] == "2026-10-21T03:59:59Z"
    cutoff = "2026-10-21T04:00:00Z"
    assert not [k for k in ingest.possession_as_of(sealed, cutoff) if k[0] == "espn:9100"]   # unseen at the cutoff
    assert len([k for k in ingest.possession_as_of(sealed, "2026-10-21T04:00:05Z") if k[0] == "espn:9100"]) == 2


def test_retry_then_success_and_import_failure_are_both_counted(sealed):
    recent_finals(sealed, 2)
    calls = []

    def flaky(game_id):
        calls.append(game_id)
        if game_id == "espn:9101":
            return b"not json"                     # the request succeeds but the import fails
        if len(calls) == 1:
            raise ConnectionError("temporary")
        return summary("9100", ("1", "110"), ("2", "100"))
    out = jobs.boxscores(sealed, fetch=flaky, sleep=lambda _s: None, now=at("20 06"))
    assert calls == ["espn:9100", "espn:9100", "espn:9101"] and out["http_attempts"] == 3
    assert out["fetched"][0]["game_id"] == "espn:9100" and out["fetched"][0]["complete_lines"] == 2
    assert out["failed"][0]["game_id"] == "espn:9101" and out["failed"][0]["error"].startswith("import ")
    log = [tuple(r) for r in sealed.execute(
        "SELECT game_id, outcome, fetch_id IS NOT NULL FROM box_fetch_attempts ORDER BY attempt_id")]
    assert log == [("espn:9100", "failed", 0), ("espn:9100", "fetched", 1), ("espn:9101", "failed", 0)]
    assert jobs.box_attempts(sealed, "espn:9101") == 1   # counted although its import was rolled back
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        sealed.execute("DELETE FROM box_fetch_attempts")


# ---- shadow report ---------------------------------------------------------------------------------------------

from app import shadow_report  # noqa: E402


def played_season(conn, play_in=0, extra=(), postseason=False, play_in_status="STATUS_FINAL"):
    """Elo and shadow forecasts for g0, g1 and 9001; only an Elo forecast for 9004; then all four results.
    `extra` adds regular-season finals, `play_in` that many play-in games, `postseason` a scheduled playoff game."""
    ingest_events(conn, [event("9001", "2026-10-22T23:00:00Z", "1", "5")], "2026-10-19T13:00:00Z")
    live.predict(conn, now=NOW)
    shadow.predict(conn, now=NOW, write=True)
    ingest_events(conn, [event("9004", "2026-10-23T23:00:00Z", "2", "6")], "2026-10-19T16:00:00Z")
    live.predict(conn, now="2026-10-19T17:00:00Z")   # the shadow job did not run for this one
    finals = [event("g0", "2026-10-20T23:00:00Z", "3", "4", "STATUS_FINAL", 101, 99),
              event("g1", "2026-10-21T23:30:00Z", "1", "2", "STATUS_FINAL", 110, 100),
              event("9001", "2026-10-22T23:00:00Z", "1", "5", "STATUS_FINAL", 95, 105),
              event("9004", "2026-10-23T23:00:00Z", "2", "6", "STATUS_FINAL", 120, 90)]
    finals += [event(str(9005 + i), f"2026-10-2{4 + i}T23:00:00Z", h, a, "STATUS_FINAL", 100, 97)
               for i, (h, a) in enumerate(extra)]
    finals += [event(str(9500 + i), f"2027-04-1{3 + i}T23:00:00Z", "3", "5", play_in_status, 100, 98, stype=5)
               for i in range(play_in)]
    if postseason:
        finals.append(event("9700", "2027-04-18T23:00:00Z", "1", "3", stype=3))
    ingest_events(conn, finals, "2027-04-16T05:00:00Z")


MINI_SEASON = dict(extra=(("3", "5"), ("4", "6")), play_in=2, postseason=True)   # 6 teams × 2 games, 2 play-in games


@pytest.fixture
def mini_format(monkeypatch):
    """A season format small enough to build in a test; the completion logic itself is unchanged."""
    monkeypatch.setattr(shadow_report, "EXPECTED_TEAMS", 6)
    monkeypatch.setattr(shadow_report, "REGULAR_SEASON_GAMES_PER_TEAM", 2)
    monkeypatch.setattr(shadow_report, "PLAY_IN_GAMES", 2)


def test_interim_report_is_operational_and_never_evaluates_the_rule(sealed, tmp_path):
    played_season(sealed)
    before = (rows_of(sealed), shadow.SHADOW_DECISION_PATH.read_bytes(), backtest.FROZEN_DECISION_PATH.read_bytes())
    out = shadow_report.report(sealed, log_path=tmp_path / "promotion.jsonl")
    assert out["mode"] == "interim" and out["promotion"]["evaluated"] is False
    assert "brier_diff_candidate_minus_elo" not in json.dumps(out)
    cov = out["coverage"]
    assert (cov["final_games"], cov["with_elo_forecast"], cov["with_candidate_forecast"], cov["decision_set"]) == (4, 4, 3, 3)
    assert (cov["excluded_missing_candidate_only"], cov["excluded_missing_elo_only"], cov["excluded_missing_both"]) == (1, 0, 0)
    assert {k: v["n"] for k, v in out["scores"].items()} == {"constant_0.5": 3, "home_rate": 3, "elo": 3, "candidate": 3}
    assert out["scores"]["constant_0.5"]["brier"] == 0.25 and 0 < out["scores"]["home_rate"]["brier"] < 1
    assert out["market"]["matched_games"] == 1 and out["market"]["market"]["n"] == 1   # g1's quote predates both forecasts
    assert "predict" in out["jobs"] and "shadow" in out["jobs"] and "Elo job" in out["jobs"]["note"]
    assert not (tmp_path / "promotion.jsonl").exists()
    assert (rows_of(sealed), shadow.SHADOW_DECISION_PATH.read_bytes(), backtest.FROZEN_DECISION_PATH.read_bytes()) == before


def test_codex_partial_schedule_with_every_known_game_final_cannot_finalize(sealed, tmp_path):
    # Codex re-review: four regular games and one play-in game, all final and nothing pending, used to be accepted.
    played_season(sealed, play_in=1)
    assert sealed.execute("SELECT COUNT(*) FROM games WHERE season = 2027 AND season_type IN ('regular', 'play_in') "
                          "AND status != 'final'").fetchone()[0] == 0
    with pytest.raises(shadow_report.ShadowReportError) as err:
        shadow_report.report(sealed, final=True, log_path=tmp_path / "promotion.jsonl")
    msg = str(err.value)
    assert "not verified complete" in msg and "all_teams_have_a_full_regular_season" in msg
    assert "full_play_in_slate_is_final" in msg and "playoffs_scheduled_after_the_play_in" in msg
    assert "no_regular_or_play_in_game_pending" not in msg   # nothing is pending, and it is still refused
    assert not (tmp_path / "promotion.jsonl").exists()
    interim = shadow_report.report(sealed, log_path=tmp_path / "promotion.jsonl")
    assert interim["season_completion"]["complete"] is False
    assert interim["season_completion"]["found"]["teams_short_of_a_full_season"] == 30


@pytest.mark.parametrize("change, failed_check", [
    (dict(play_in=1), "full_play_in_slate_is_final"),                       # first play-in game only
    (dict(postseason=False), "playoffs_scheduled_after_the_play_in"),
    (dict(extra=(("3", "5"),)), "all_teams_have_a_full_regular_season"),     # teams 4 and 6 are a game short
    (dict(play_in_status="STATUS_POSTPONED"), "no_unresolved_play_in_postponement"),
])
def test_each_missing_piece_of_a_finished_season_blocks_the_final_report(sealed, tmp_path, mini_format, change,
                                                                         failed_check):
    played_season(sealed, **{**MINI_SEASON, **change})
    with pytest.raises(shadow_report.ShadowReportError, match=failed_check):
        shadow_report.report(sealed, final=True, log_path=tmp_path / "promotion.jsonl")
    assert not (tmp_path / "promotion.jsonl").exists()


def test_pending_games_block_the_final_report(sealed, tmp_path, mini_format):
    played_season(sealed, **MINI_SEASON)
    ingest_events(sealed, [event("9800", "2027-04-10T23:00:00Z", "1", "2")], "2027-04-16T06:00:00Z")
    with pytest.raises(shadow_report.ShadowReportError, match="no_regular_or_play_in_game_pending"):
        shadow_report.report(sealed, final=True, log_path=tmp_path / "promotion.jsonl")


def test_final_report_applies_the_rule_once_and_changes_nothing(sealed, tmp_path, mini_format):
    played_season(sealed, **MINI_SEASON)
    log = tmp_path / "promotion.jsonl"
    before = (rows_of(sealed), shadow.SHADOW_DECISION_PATH.read_bytes(), backtest.FROZEN_DECISION_PATH.read_bytes())
    out = shadow_report.report(sealed, final=True, log_path=log)
    assert out["mode"] == "final" and out["previously_applied"] is False and out["coverage"]["decision_set"] == 3
    assert out["season_completion"]["complete"] is True and all(out["season_completion"]["checks"].values())
    promo = out["promotion"]
    assert promo["evaluated"] and promo["outcome"] == "inconclusive" and promo["elo_stays"] is True
    assert promo["rule"]["minimum_games"] == 800 and len(promo["brier_diff_candidate_minus_elo"]["ci95"]) == 2
    # A later call, even with more data, returns the logged outcome instead of re-evaluating.
    ingest_events(sealed, [event("9600", "2027-04-10T23:00:00Z", "1", "2", "STATUS_FINAL", 100, 90)], "2027-04-17T05:00:00Z")
    again = shadow_report.report(sealed, final=True, log_path=log)
    assert again["previously_applied"] is True and again["generated_at"] == out["generated_at"]
    assert again["coverage"] == out["coverage"] and len(log.read_text().splitlines()) == 1
    assert (rows_of(sealed), shadow.SHADOW_DECISION_PATH.read_bytes(), backtest.FROZEN_DECISION_PATH.read_bytes()) == before


def test_real_season_format_constants():
    assert (shadow_report.EXPECTED_TEAMS, shadow_report.REGULAR_SEASON_GAMES_PER_TEAM, shadow_report.PLAY_IN_GAMES) == \
        (30, 82, 6)


def test_promotion_rule_logic():
    rule = shadow_report.apply_promotion_rule
    assert rule(799, -0.01, 0.60, 0.62, 800)["outcome"] == "inconclusive"
    assert rule(800, -0.0001, 0.60, 0.60, 800) == {"outcome": "candidate_qualifies", "elo_stays": False,
                                                    "reason": "interval upper bound below 0 and log loss not higher than Elo's"}
    assert rule(1200, 0.0, 0.60, 0.62, 800)["outcome"] == "elo_stays"            # upper bound must be strictly below 0
    worse_ll = rule(1200, -0.001, 0.63, 0.62, 800)
    assert worse_ll["outcome"] == "elo_stays" and "log loss" in worse_ll["reason"]
    assert shadow_report.season_of({"season": "2026-27"}) == 2027
