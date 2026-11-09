"""Live soccer forecasts (app/soccer_live.py) and their ticket path (app/tickets.py), on temporary stores.

ESPN payloads are synthetic (tests/test_soccer.py builders); the model is the real sealed candidate; times are the real
clock because the ledger refuses backdated rows (database clock). Matches are placed tomorrow (UTC) so tomorrow's 06:00
UTC cutoff is always ahead of the test run."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import db, soccer, soccer_live, tickets
from test_soccer import event, payload

NOW = datetime.now(timezone.utc)
TODAY, TOMORROW = NOW.date(), NOW.date() + timedelta(days=1)
YESTERDAY = TODAY - timedelta(days=1)
STAMP = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


def at(day, hhmm):
    return f"{day.isoformat()}T{hhmm}Z"


ARS, CHE, LIV, MCI = ("359", "ARS", "Arsenal", "0"), ("363", "CHE", "Chelsea", "0"), ("364", "LIV", "Liverpool", "0"), \
    ("382", "MNC", "Manchester City", "0")


def played(eid, day, home, away, hs, as_, status="STATUS_FULL_TIME"):
    return event(eid=eid, date=at(day, "15:00"), status=status, state="post", home=(*home[:3], str(hs)),
                 away=(*away[:3], str(as_)))


def upcoming(eid, day, home, away, hhmm="15:00"):
    return event(eid=eid, date=at(day, hhmm), status="STATUS_SCHEDULED", state="pre", home=home, away=away)


def ingest(c, day, *events, retrieved=STAMP):
    soccer.ingest_day(c, "eng.1", day.strftime("%Y%m%d"), fetch=lambda *_: payload("eng.1", *events), retrieved_at=retrieved)


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(soccer_live, "LAUNCH", ("eng.1",))
    monkeypatch.setattr(soccer_live, "INPUT_ONLY", ())
    monkeypatch.setattr(soccer_live, "SEASON", TODAY.year if NOW.month >= 7 else TODAY.year - 1)
    monkeypatch.setattr(soccer_live, "SEASON_FETCH_FROM", YESTERDAY.isoformat())
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    ingest(c, YESTERDAY, played("1", YESTERDAY, ARS, CHE, 2, 0), played("2", YESTERDAY, LIV, MCI, 1, 1))
    ingest(c, TODAY)
    ingest(c, TOMORROW, upcoming("3", TOMORROW, ARS, LIV), upcoming("4", TOMORROW, MCI, CHE, "19:30"))
    teams = {r["team_id"]: r["name"] for r in c.execute("SELECT team_id, name FROM teams")}
    monkeypatch.setattr(soccer_live, "clubs", lambda: {"version": "test", "espn": {t: f"ENG:{n} FC" for t, n in teams.items()}})
    yield c
    c.close()


def test_complete_inputs_record_append_only_forecasts_once(live):
    rep = soccer_live.run(live, day=TOMORROW.isoformat())
    assert rep["status"] == "ok" and rep["competitions"]["eng.1"]["recorded"] == 2
    rows = live.execute("SELECT * FROM soccer_live_forecasts ORDER BY match_id").fetchall()
    assert len(rows) == 2 and all(abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1) < 1e-9 for r in rows)
    assert all(r["candidate_sha256"] == soccer_live.CANDIDATE_SHA256 and r["cutoff_at"] == at(TOMORROW, "06:00:00")[:-1] + "Z"
               for r in rows)
    assert all(r["generated_at"] <= r["cutoff_at"] and r["source_retrieved_at"] == STAMP for r in rows)
    again = soccer_live.run(live, day=TOMORROW.isoformat())                 # same inputs: a retry, not a new version
    assert again["competitions"]["eng.1"]["recorded"] == 0
    assert live.execute("SELECT COUNT(*) FROM soccer_live_forecasts").fetchone()[0] == 2
    assert live.execute("SELECT ok FROM soccer_live_checks ORDER BY check_id DESC").fetchone()[0] == 1
    with pytest.raises(Exception, match="append-only"):
        live.execute("UPDATE soccer_live_forecasts SET p_home = 0.5")


def test_the_live_loop_is_the_frozen_walk(live):
    """Yesterday's results move the ratings exactly as the evaluation's walk-forward would (no other input)."""
    soccer_live.run(live, day=TOMORROW.isoformat())
    f = {r["match_id"]: r for r in live.execute("SELECT * FROM soccer_live_forecasts")}
    ars_liv, mci_che = f["soccer:eng.1:3"], f["soccer:eng.1:4"]
    assert ars_liv["p_home"] > ars_liv["p_away"]           # Arsenal won, Liverpool drew: Arsenal now rated higher
    assert mci_che["p_home"] > mci_che["p_away"]           # Chelsea lost


@pytest.mark.parametrize("problem", ["unsettled", "stale", "unfetched", "unmapped", "extra_time"])
def test_incomplete_inputs_refuse_visibly_and_record_nothing(live, monkeypatch, problem):
    if problem == "unsettled":
        ingest(live, TODAY, event(eid="9", date=at(TODAY, "12:00"), status="STATUS_SCHEDULED", state="pre", home=ARS, away=MCI))
    if problem == "stale":
        monkeypatch.setattr(soccer, "last_refresh_at", lambda *a: "2000-01-01T00:00:00Z")
    if problem == "unfetched":
        monkeypatch.setattr(soccer_live, "SEASON_FETCH_FROM", (YESTERDAY - timedelta(days=3)).isoformat())
    if problem == "unmapped":
        monkeypatch.setattr(soccer_live, "clubs", lambda: {"version": "test", "espn": {}})
    if problem == "extra_time":
        ingest(live, YESTERDAY, played("1", YESTERDAY, ARS, CHE, 2, 0, status="STATUS_FINAL_AET"),
               played("2", YESTERDAY, LIV, MCI, 1, 1))
    rep = soccer_live.run(live, day=TOMORROW.isoformat())
    assert rep["status"] == "refused" and rep["competitions"]["eng.1"]["problems"]
    assert live.execute("SELECT COUNT(*) FROM soccer_live_forecasts").fetchone()[0] == 0
    chk = live.execute("SELECT ok, problems_json FROM soccer_live_checks").fetchone()
    assert chk["ok"] == 0 and json.loads(chk["problems_json"])


def test_never_after_the_cutoff_and_never_backdated(live):
    rep = soccer_live.run(live, day=YESTERDAY.isoformat())
    assert rep["status"] == "refused" and "cutoff" in rep["reason"]
    soccer_live.run(live, day=TOMORROW.isoformat())
    r = dict(live.execute("SELECT * FROM soccer_live_forecasts").fetchone())
    with pytest.raises(Exception, match="never backdated"):
        live.execute("INSERT INTO soccer_live_forecasts (match_id, competition_id, market, version, candidate_sha256, model, "
                     "idempotency_key, inputs_digest, source_retrieved_at, p_home, p_draw, p_away, cutoff_at, generated_at) "
                     "VALUES (?, 'eng.1', '1x2_regulation', 2, ?, 'elo', 'k2', 'd', ?, .4, .3, .3, ?, '2001-01-01T00:00:00Z')",
                     (r["match_id"], r["candidate_sha256"], r["source_retrieved_at"], r["cutoff_at"]))


def test_ninety_minute_settlement(live):
    soccer_live.run(live, day=TOMORROW.isoformat())
    fid = live.execute("SELECT forecast_id FROM soccer_live_forecasts WHERE match_id = 'soccer:eng.1:3'").fetchone()[0]
    assert soccer_live.grade(live, fid, "home", STAMP)["grade"] == "pending"
    later = (NOW + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ingest(live, TOMORROW, played("3", TOMORROW, ARS, LIV, 1, 1), upcoming("4", TOMORROW, MCI, CHE, "19:30"), retrieved=later)
    g = {o: soccer_live.grade(live, fid, o, later)["grade"] for o in soccer_live.OUTCOMES}
    assert g == {"home": "loss", "draw": "win", "away": "loss"}
    assert soccer_live.grade(live, fid, "draw", STAMP)["grade"] == "pending"      # not known yet at that time
    with pytest.raises(ValueError):
        soccer_live.grade(live, fid, "both teams to score", later)                  # no other market exists


def test_research_rows_never_appear_as_live(live):
    soccer_live.run(live, day=TOMORROW.isoformat())
    assert {f["label"] for f in soccer_live.forecasts(live)} == {soccer_live.LABEL}
    assert live.execute("SELECT COUNT(*) FROM soccer_forecasts").fetchone()[0] == 0


def test_ticket_snapshot_offers_soccer_picks_with_their_rules(live, monkeypatch):
    from app import intl_live
    monkeypatch.setattr(intl_live, "LAUNCH", ())                 # this fixture holds club data only
    soccer_live.run(live, day=TOMORROW.isoformat())
    kickoff = datetime.fromisoformat(at(TOMORROW, "15:00").replace("Z", "+00:00"))
    section = tickets.soccer_section(kickoff - timedelta(hours=1))
    assert section["freshness"]["source_state"] == "fresh" and not section["refused"]
    picks = {p["game_id"]: p for p in section["report"]["picks"]}
    p = picks["soccer:eng.1:3"]
    assert p["market"] == "1x2_regulation" and p["pick_label"].endswith("(90 min)")
    assert p["probability"] == max(p["probabilities"].values()) and p["outcome"] in soccer_live.OUTCOMES
    assert tickets.eligible(p, kickoff - timedelta(hours=1))
    assert not tickets.eligible(p, kickoff - timedelta(hours=3))      # same 2-hour ticket window as every sport
    assert not tickets.eligible(p, kickoff + timedelta(minutes=1))
    leg = {**p, "sport": "soccer"}
    assert tickets.grade_leg(leg, STAMP)["grade"] == "pending"
    text = tickets.ticket_text("2026-11-09", [{"label": "Mixed", "ticket_id": "x", "stake_usd": 1, "legs": [
        leg, {**leg, "game_id": "g2", "forecast_id": 99, "sport": "nba", "matchup": "A @ B"}]}])
    assert "90-minute result" in text and "extra time and penalties do not count" in text


def test_refused_competitions_alert_in_the_ticket_health_check(live, monkeypatch):
    monkeypatch.setattr(soccer_live, "clubs", lambda: {"version": "test", "espn": {}})
    soccer_live.run(live, day=TOMORROW.isoformat())
    section = tickets.soccer_section(datetime.fromisoformat(at(TOMORROW, "10:00")[:-1] + "+00:00"))
    assert section["refused"] and section["freshness"]["source_state"] == "stale"
    alerts = tickets.health_alerts({"sports": {"soccer": section}})
    assert any(a["issue"] in ("inputs_refused", "source_unavailable_or_stale") for a in alerts)


def test_without_a_live_store_soccer_is_reported_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "empty"))
    section = tickets.soccer_section(NOW)
    assert section["error"] == "forecasts_unavailable"
    assert tickets.health_alerts({"sports": {"soccer": section}}) == []


def test_an_early_look_ahead_waits_silently_and_a_missing_run_alerts_only_after_the_cutoff(live, monkeypatch):
    ingest(live, TODAY, event(eid="9", date=at(TODAY, "23:30"), status="STATUS_SCHEDULED", state="pre", home=ARS, away=MCI))
    rep = soccer_live.run(live, day=TOMORROW.isoformat(), record_refusal=False)   # today's late match not played yet
    assert rep["status"] == "refused" and live.execute("SELECT COUNT(*) FROM soccer_live_checks").fetchone()[0] == 0
    before = datetime.fromisoformat(at(TOMORROW, "05:00")[:-1] + "+00:00")
    after = datetime.fromisoformat(at(TOMORROW, "07:00")[:-1] + "+00:00")
    assert tickets.soccer_section(before)["refused"] == {}                           # the run may still come
    assert "eng.1" in tickets.soccer_section(after)["refused"]                       # it did not: visible and alerted


def test_the_api_serves_national_team_competitions(live, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})     # other API tests may leave a demo override set
    client = TestClient(main.app)
    for comp in ("uefa.nations", "fifa.friendly", "caf.nations_qual"):
        r = client.get("/api/soccer/live", params={"competition": comp})
        assert r.status_code == 200 and r.json()["in_scope"] is True, (comp, r.text[:200])
        m = client.get("/api/soccer/meta", params={"competition": comp})
        assert m.status_code == 200, (comp, m.text[:200])
    w = client.get("/api/soccer/live", params={"competition": "fifa.world"}).json()
    assert w["in_scope"] is False and "held-out test" in w["scope_note"]


def test_a_failing_international_run_never_stops_the_club_run(live, monkeypatch, capsys):
    from app import cli, intl_live
    def broken(*a, **k):
        raise FileNotFoundError("international history not downloaded")
    monkeypatch.setattr(intl_live, "run", broken)
    assert cli.main(["soccer", "live-forecast", "--day", TOMORROW.isoformat()]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["clubs"]["status"] == "ok" and out["international"] == {"status": "failed", "error": "FileNotFoundError"}
    assert live.execute("SELECT COUNT(*) FROM soccer_live_forecasts").fetchone()[0] == 2


# --- Audit 2026-11-08: the page text follows what is stored (recorded / awaiting / outside the tested scope / stale) ---
def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    return TestClient(main.app)


def test_the_api_says_whether_forecasts_are_recorded_awaited_or_out_of_scope(live, monkeypatch):
    client = _client(monkeypatch)
    get = lambda path, **params: client.get(path, params=params).json()
    before = get("/api/soccer/meta", competition="eng.1")
    cap = before["capabilities"][0]
    assert before["forecast_state"] == "awaiting" and cap["available"] is False
    assert "no forecast has been recorded" in cap["reason"] and "No soccer model" not in cap["reason"]
    day = get("/api/soccer/matches", competition="eng.1", date=TOMORROW.isoformat())
    assert len(day["matches"]) == 2 and day["live_forecasts"] == {}

    soccer_live.run(live, day=TOMORROW.isoformat())
    after = get("/api/soccer/meta", competition="eng.1")
    assert after["forecast_state"] == "recorded" and after["capabilities"][0]["available"] is True
    reasons = " ".join(c["reason"] for c in after["capabilities"])
    assert "No soccer forecast has been recorded" not in reasons and "no soccer forecasts to grade" not in reasons
    day = get("/api/soccer/matches", competition="eng.1", date=TOMORROW.isoformat())
    assert set(day["live_forecasts"]) == {m["match_id"] for m in day["matches"]}
    for match_id, f in day["live_forecasts"].items():
        assert f["match_id"] == match_id and abs(sum(f["probabilities"].values()) - 1) < 1e-9 and f["result"] is None
    assert "forecast" not in json.dumps(day["matches"])                # match records stay facts only
    first = day["matches"][0]["match_id"]
    detail = get("/api/soccer/matches/" + first)
    assert detail["live_forecast"]["forecast_id"] == day["live_forecasts"][first]["forecast_id"]
    assert detail["capabilities"][0]["available"] is True and "forecast" not in json.dumps(detail["match"])
    assert detail["forecast_state"] == "recorded"

    world = get("/api/soccer/meta", competition="fifa.world")           # sealed scope excludes it: say that, not "no model"
    assert world["forecast_state"] == "out_of_scope" and "held-out test" in world["capabilities"][0]["reason"]


def test_the_live_response_reports_the_age_of_the_source_data(live, monkeypatch):
    client = _client(monkeypatch)
    fresh = client.get("/api/soccer/live", params={"competition": "eng.1"}).json()["refresh"]
    assert fresh == {"last_refresh_at": STAMP, "max_age_hours": 26, "stale": False}
    old = (NOW - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(soccer, "last_refresh_at", lambda conn, competition: old)
    assert client.get("/api/soccer/live", params={"competition": "eng.1"}).json()["refresh"] == {
        "last_refresh_at": old, "max_age_hours": 26, "stale": True}


def test_a_store_without_live_tables_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    monkeypatch.setattr(soccer_live, "has_tables", lambda conn: False)
    assert soccer.forecast_state(c, "eng.1") == "no_live_store"
    assert "records no live soccer forecasts" in soccer.capabilities("no_live_store")[0]["reason"]
    c.close()


# --- Consolidated review: a match past its forecast cutoff must not be described as still awaiting a forecast ----------
def test_the_api_reports_each_match_forecast_cutoff(live, monkeypatch):
    client = _client(monkeypatch)
    day = client.get("/api/soccer/matches", params={"competition": "eng.1", "date": TOMORROW.isoformat()}).json()
    assert day["forecast_cutoffs"] == {m["match_id"]: f"{TOMORROW.isoformat()}T06:00:00Z" for m in day["matches"]}
    detail = client.get("/api/soccer/matches/" + day["matches"][0]["match_id"]).json()
    assert detail["forecast_cutoff_at"] == f"{TOMORROW.isoformat()}T06:00:00Z" and "cutoff" not in json.dumps(detail["match"])
    # national teams: the match day runs 06:00–06:00 UTC, so a 02:00 UTC kickoff belongs to the day before
    assert soccer.forecast_cutoff("uefa.nations", "2026-11-12T17:00:00Z") == "2026-11-12T06:00:00Z"
    assert soccer.forecast_cutoff("uefa.nations", "2026-11-13T02:00:00Z") == "2026-11-12T06:00:00Z"
    assert soccer.forecast_cutoff("fifa.world", "2026-11-12T17:00:00Z") is None       # never forecast: no cutoff to report
    world = client.get("/api/soccer/matches", params={"competition": "fifa.world", "date": TOMORROW.isoformat()}).json()
    assert world["forecast_cutoffs"] == {}
