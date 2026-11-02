"""Activity-aware data refresh (app/freshness.py, jobs.refresh/tick), the /api/freshness summary and the preview publisher
(docs/REFRESH_PLAN.md). Synthetic payloads and a controlled job clock; no network."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import cli, db, freshness, ingest, jobs, preview_publish
from app.main import api_mode, app
from test_live import event
from test_nfl import nfl_event, nfl_payload

UTC = timezone.utc
NOW = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)          # 13:00 EST (the day US daylight time ended)


def _fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return _fmt(self.at)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    from app.sources import espn, nhl_stats

    def blocked(*_a, **_k):
        raise RuntimeError("network disabled in tests")
    monkeypatch.setattr(espn, "_get", blocked)
    monkeypatch.setattr(nhl_stats, "_get", blocked)


@pytest.fixture
def clock(monkeypatch):
    c = Clock(NOW)
    monkeypatch.setattr(jobs, "utc_now", c)            # job_runs start/finish times follow the simulated clock
    return c


@pytest.fixture
def nba(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nba.sqlite")
    db.migrate(conn)
    db.claim_store(conn, "nba")
    return conn


def games(conn, *evs, at=NOW - timedelta(hours=1), month="202611"):
    body = json.dumps({"events": list(evs)}).encode()
    return ingest.ingest_month(conn, month, fetch=lambda _m: body, retrieved_at=_fmt(at))


def ok_refresh(conn, clock, at, scope="broad"):
    clock.at = at
    rid = jobs._start(conn, "refresh")
    jobs._finish(conn, rid, "success", 1, {"scope": scope, "imports": []})


def day_body(*evs):
    return json.dumps({"events": list(evs)}).encode()


# --- Activity ---------------------------------------------------------------------------------------------------
def test_activity_idle_pregame_active_and_awaiting(nba):
    assert freshness.activity(nba, NOW)[0] == "idle"
    games(nba, event("e1", _fmt(NOW + timedelta(hours=3)), "1", "2"))
    assert freshness.activity(nba, NOW)[0] == "idle"                       # 3 h away: not yet pregame
    act, gs = freshness.activity(nba, NOW + timedelta(hours=1, minutes=30))
    assert act == "pregame" and [g["game_id"] for g in gs] == ["espn:e1"]
    # Past its scheduled start, still 'scheduled' in the store: awaiting confirmation -> active cadence, status untouched.
    act, gs = freshness.activity(nba, NOW + timedelta(hours=3, minutes=5))
    assert act == "active" and gs[0]["status"] == "scheduled"
    assert nba.execute("SELECT status FROM games").fetchone()[0] == "scheduled"
    # An unfinished game more than AWAITING_LIMIT past its start no longer keeps the store active (stuck feed).
    assert freshness.activity(nba, NOW + timedelta(hours=3) + freshness.AWAITING_LIMIT + timedelta(minutes=1))[0] == "idle"


def test_postponed_and_canceled_games_never_make_a_store_active(nba):
    games(nba, event("p1", _fmt(NOW - timedelta(minutes=30)), "1", "2", status="STATUS_POSTPONED"),
          event("c1", _fmt(NOW + timedelta(minutes=30)), "3", "4", status="STATUS_CANCELED"))
    assert freshness.activity(nba, NOW)[0] == "idle"


def test_newly_final_game_keeps_active_cadence_for_one_confirmation(nba):
    tip = NOW - timedelta(hours=3)
    games(nba, event("f1", _fmt(tip), "1", "2", status="STATUS_IN_PROGRESS"), at=NOW - timedelta(hours=1))
    games(nba, event("f1", _fmt(tip), "1", "2", status="STATUS_FINAL", hs=101, as_=99), at=NOW - timedelta(minutes=2))
    assert freshness.activity(nba, NOW)[0] == "active"
    assert freshness.activity(nba, NOW + freshness.CONFIRM_FINAL)[0] == "idle"


def test_eastern_dates_cross_midnight_and_dst_end(nba):
    # 23:30 EST on Nov 1 is 04:30 UTC on Nov 2; the request date must be the Eastern one.
    games(nba, event("late", "2026-11-02T04:30Z", "1", "2"))
    _, gs = freshness.activity(nba, datetime(2026, 11, 2, 3, 0, tzinfo=UTC))
    assert freshness.eastern_dates(gs) == ["2026-11-01"]


# --- Plan: due times, scope, backoff ---------------------------------------------------------------------------
def test_idle_store_refreshes_hourly_and_broad(nba, clock):
    p = freshness.plan(nba, NOW)
    assert p["due"] and p["scope"] == "broad" and p["activity"] == "idle"         # never refreshed: due now
    ok_refresh(nba, clock, NOW)
    assert not freshness.plan(nba, NOW + timedelta(minutes=30))["due"]
    p = freshness.plan(nba, NOW + timedelta(minutes=59, seconds=45))              # within the tick slack
    assert p["due"] and p["scope"] == "broad"


def test_pregame_refreshes_every_two_minutes_with_dates_but_broad_hourly(nba, clock):
    games(nba, event("e1", _fmt(NOW + timedelta(minutes=90)), "1", "2"))
    ok_refresh(nba, clock, NOW - timedelta(minutes=10))                           # broad 10 min ago
    ok_refresh(nba, clock, NOW - timedelta(minutes=1), scope="dates")
    p = freshness.plan(nba, NOW)
    assert p["activity"] == "pregame" and not p["due"]
    p = freshness.plan(nba, NOW + timedelta(minutes=1))
    assert p["due"] and p["scope"] == "dates" and p["dates"] == ["2026-11-01"]
    p = freshness.plan(nba, NOW + timedelta(minutes=50))                           # broad due again (60 min)
    assert p["due"] and p["scope"] == "broad"


def test_failures_back_off_and_never_count_as_fresh(nba, clock):
    games(nba, event("e1", _fmt(NOW + timedelta(minutes=30)), "1", "2"))
    ok_refresh(nba, clock, NOW - timedelta(minutes=5))
    for i, wait in enumerate((2, 4, 8, 16, 30, 30), start=1):
        clock.at = NOW + timedelta(minutes=i)
        jobs._finish(nba, jobs._start(nba, "refresh"), "failed", 3, {"error": "HTTPStatusError: 429"})
        p = freshness.plan(nba, clock.at)
        assert p["failures"] == i and p["backoff_until"] == _fmt(clock.at + timedelta(minutes=wait)) and not p["due"]
        assert p["last_success"] == _fmt(NOW - timedelta(minutes=5))           # a failure never moves the source time


def test_restart_after_downtime_is_due_immediately(nba, clock):
    ok_refresh(nba, clock, NOW - timedelta(days=2))
    p = freshness.plan(nba, NOW)
    assert p["due"] and p["scope"] == "broad"


# --- refresh(): explicit per-sport dispatch and date-scoped requests -------------------------------------------
def test_nba_focused_refresh_fetches_only_the_game_day(nba, clock):
    games(nba, event("e1", _fmt(NOW + timedelta(minutes=60)), "1", "2"))
    ok_refresh(nba, clock, NOW - timedelta(minutes=20))
    calls = []

    def fetch(day):
        calls.append(day)
        return day_body(event("e1", _fmt(NOW + timedelta(minutes=75)), "1", "2"))   # start moved by 15 minutes
    clock.at = NOW
    out = jobs.refresh(nba, fetch=fetch, sleep=lambda _s: None, now=NOW)
    assert out["status"] == "success" and out["scope"] == "dates" and calls == ["20261101"] and out["dates"] == ["20261101"]
    assert jobs.refresh_changed(out)
    assert nba.execute("SELECT tipoff_at FROM games").fetchone()[0] == _fmt(NOW + timedelta(minutes=75))
    again = jobs.refresh(nba, fetch=fetch, sleep=lambda _s: None, now=NOW + timedelta(seconds=30))
    assert again["status"] == "skipped" and "not due" in again["reason"] and calls == ["20261101"]


def test_nhl_focused_refresh_uses_the_date_filtered_game_list(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nhl.sqlite")
    db.migrate(conn)
    row = {"id": 2026020227, "season": 20262027, "gameType": 2, "gameStateId": 1, "gameScheduleStateId": 1,
           "easternStartTime": "2026-11-01T13:00:00", "homeTeamId": 12, "visitingTeamId": 52, "homeScore": 0,
           "visitingScore": 0, "period": 0}
    ingest.ingest_nhl_season(conn, 2027, fetch=lambda _s: json.dumps({"data": [row]}).encode(),
                             retrieved_at=_fmt(NOW - timedelta(hours=2)))
    ok_refresh(conn, clock, NOW - timedelta(minutes=30))
    calls = []

    def fetch(season, day):
        calls.append((season, day))
        return json.dumps({"data": [{**row, "gameStateId": 3}]}).encode()
    out = jobs.refresh(conn, fetch=fetch, sleep=lambda _s: None, now=NOW + timedelta(minutes=5))
    assert out["status"] == "success" and out["scope"] == "dates" and calls == [(2027, "2026-11-01")]
    assert conn.execute("SELECT status FROM games").fetchone()[0] == "in_progress"
    from app.sources import nhl_stats
    key, params = nhl_stats.day_request(2027, "2026-11-01")
    assert key == "season=20262027&gameDate=2026-11-01" and params == {"cayenneExp": 'season=20262027 and gameDate="2026-11-01"'}
    with pytest.raises(ValueError):
        nhl_stats.day_request(2027, "20261101")


def test_nfl_store_refreshes_through_nfl_adapters_and_tick_never_forecasts(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nfl.sqlite")
    db.migrate(conn)
    db.claim_store(conn, "nfl")
    months = []
    out = jobs.refresh(conn, fetch=lambda m: months.append(m) or nfl_payload(
        nfl_event(eid="9", date="2026-11-01T18:00Z", year=2026, status="STATUS_SCHEDULED",
                  home=("16", "MIN", "Minnesota Vikings", "0"), away=("20", "NYJ", "New York Jets", "0"))),
        sleep=lambda _s: None, now=NOW)
    assert out["status"] == "success" and out["scope"] == "broad" and months == ["202610", "202611"]
    assert conn.execute("SELECT game_id FROM games").fetchone()[0] == "nfl:9"

    def boom():
        raise AssertionError("NFL must never reach forecast code")
    days = []
    out = jobs.tick(conn, now=NOW + timedelta(minutes=5), cycle=boom, forecast=boom,
                    refresh_kw={"fetch": lambda d: days.append(d) or nfl_payload(), "sleep": lambda _s: None})
    assert out["forecasts"]["status"] == "unsupported" and out["refresh"]["scope"] == "dates" and days == ["20261101"]


def test_refresh_refuses_an_unknown_store(nba, clock, monkeypatch):
    monkeypatch.setattr(jobs, "store_sport", lambda _c: "mlb")
    out = jobs.refresh(nba, fetch=lambda _m: b"{}", now=NOW)
    assert out["status"] == "failed" and "mlb" in out["error"]
    with pytest.raises(ValueError):
        jobs.tick(nba, now=NOW)


# --- tick: no work when nothing is due; forecast wake-ups ---------------------------------------------------------
def _cycle_ran(conn, clock, at):
    clock.at = at
    jobs._finish(conn, jobs._start(conn, "odds"), "skipped", 0, {"reason": "test"})


def test_tick_with_nothing_due_makes_no_request_and_writes_no_row(nba, clock):
    ok_refresh(nba, clock, NOW - timedelta(minutes=5))
    _cycle_ran(nba, clock, NOW - timedelta(minutes=5))
    rows = nba.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0]

    def boom(*_a, **_k):
        raise AssertionError("no work expected")
    out = jobs.tick(nba, now=NOW, cycle=boom, forecast=boom, refresh_kw={"fetch": boom})
    assert out["refresh"]["status"] == "not_due" and out["forecasts"]["status"] == "not_due" and out["trigger"] is None
    assert nba.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == rows


def test_tick_runs_the_odds_cycle_at_its_usual_cadence(nba, clock):
    ok_refresh(nba, clock, NOW - timedelta(minutes=5))
    _cycle_ran(nba, clock, NOW - timedelta(minutes=15))
    out = jobs.tick(nba, now=NOW, cycle=lambda: {"odds": {"status": "skipped"}, "forecasts": {"status": "skipped"}})
    assert out["trigger"] == "cycle"


def test_forecast_window_opening_wakes_the_forecast_step_without_waiting(nba, clock):
    games(nba, event("e1", _fmt(NOW + timedelta(hours=2)), "1", "2"))           # window opens exactly now
    ok_refresh(nba, clock, NOW - timedelta(minutes=1), scope="dates")
    ok_refresh(nba, clock, NOW - timedelta(minutes=10))
    _cycle_ran(nba, clock, NOW - timedelta(minutes=5))                            # next cycle would be 10 min away
    clock.at = NOW - timedelta(minutes=5)
    jobs._finish(nba, jobs._start(nba, "near_tipoff"), "skipped", 1, {})          # last attempt before the window
    calls = []
    out = jobs.tick(nba, now=NOW + timedelta(seconds=5), forecast=lambda: calls.append(1) or {"status": "success"})
    assert out["trigger"] == "window_opened" and calls == [1]
    # Attempted after the window opened and nothing new arrived: no retry each minute.
    clock.at = NOW + timedelta(seconds=10)
    jobs._finish(nba, jobs._start(nba, "near_tipoff"), "failed", 1, {})
    out = jobs.tick(nba, now=NOW + timedelta(seconds=30), forecast=lambda: calls.append(2) or {})
    assert out["trigger"] is None and calls == [1]


def test_blocked_forecast_is_retried_when_the_refresh_brings_new_data(nba, clock):
    games(nba, event("e1", _fmt(NOW + timedelta(minutes=90)), "1", "2"))
    ok_refresh(nba, clock, NOW - timedelta(minutes=20))
    _cycle_ran(nba, clock, NOW - timedelta(minutes=5))
    clock.at = NOW - timedelta(minutes=4)
    jobs._finish(nba, jobs._start(nba, "near_tipoff"), "failed", 1, {"refused": "missing_result"})
    clock.at = NOW
    unchanged = jobs.tick(nba, now=NOW, forecast=lambda: {"status": "success"},
                          refresh_kw={"fetch": lambda d: day_body(event("e1", _fmt(NOW + timedelta(minutes=90)), "1", "2")),
                                      "sleep": lambda _s: None})
    assert unchanged["refresh"]["status"] == "success" and unchanged["trigger"] is None
    clock.at = NOW + timedelta(minutes=2)
    changed = jobs.tick(nba, now=clock.at, forecast=lambda: {"status": "success"},
                        refresh_kw={"fetch": lambda d: day_body(event("e1", _fmt(NOW + timedelta(minutes=100)), "1", "2")),
                                    "sleep": lambda _s: None})
    assert changed["trigger"] == "new_data"


def test_cli_tick_never_queues_behind_a_running_job(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "nba.sqlite")
    db.migrate(c)
    c.close()
    monkeypatch.setattr(cli, "TICK_LOCK_WAIT_SECONDS", 0)
    with db.store_lock("nba", wait_seconds=0) as held:
        assert held
        assert cli.main(["--sport", "nba", "tick"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "skipped"
    c = db.connect(tmp_path / "nba.sqlite")
    assert c.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 0


# --- Summary and API ----------------------------------------------------------------------------------------------
def test_summary_states_and_revision(nba, clock):
    s = freshness.summary(nba, "nba", NOW)
    assert s["source_state"] == "never" and s["mode"] == "live" and s["forecasts_supported"]
    games(nba, event("e1", _fmt(NOW + timedelta(hours=5)), "1", "2"))
    ok_refresh(nba, clock, NOW - timedelta(minutes=30))
    s = freshness.summary(nba, "nba", NOW)
    assert s["source_state"] == "fresh" and s["next_check_at"] == _fmt(NOW + timedelta(minutes=30))
    rev = s["revision"]
    ok_refresh(nba, clock, NOW)                                                   # a refresh that changed nothing
    assert freshness.summary(nba, "nba", NOW)["revision"] == rev
    games(nba, event("e1", _fmt(NOW + timedelta(hours=5)), "1", "2", status="STATUS_POSTPONED"), at=NOW)
    assert freshness.summary(nba, "nba", NOW)["revision"] != rev
    assert freshness.summary(nba, "nba", NOW + timedelta(hours=3))["source_state"] == "stale"
    clock.at = NOW + timedelta(minutes=1)
    jobs._finish(nba, jobs._start(nba, "refresh"), "failed", 3, {"error": "ConnectError: down"})
    s = freshness.summary(nba, "nba", NOW + timedelta(minutes=2))
    assert s["failing"] and s["last_error"] == "ConnectError: down" and s["last_failed_at"] == _fmt(NOW + timedelta(minutes=1))


def test_api_freshness_for_all_three_sports_and_snapshot_modes(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setitem(app.dependency_overrides, api_mode, lambda: "real")   # another module may leave a demo override
    for sport in ("nba", "nfl"):
        c = db.connect(tmp_path / f"{sport}.sqlite")
        db.migrate(c)
        db.claim_store(c, sport)
        c.close()
    client = TestClient(app)
    r = client.get("/api/freshness", params={"sport": "nfl"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "live" and not body["forecasts_supported"] and "not supported" in body["forecasts_note"]
    assert client.get("/api/freshness", params={"sport": "nhl"}).status_code == 503          # no NHL store here
    assert client.get("/api/freshness", params={"sport": "mlb"}).status_code == 422
    (tmp_path / "SNAPSHOT_AT").write_text("2026-11-01T17:00:00Z\n")
    assert client.get("/api/freshness").json()["mode"] == "frozen_snapshot"
    now = datetime.now(UTC)
    (tmp_path / "PUBLISH_STATUS.json").write_text(json.dumps({"interval_seconds": 60, "last_attempt_at": _fmt(now),
                                                              "next_attempt_at": _fmt(now + timedelta(seconds=60)),
                                                              "sports": {"nba": {"published_at": "2026-11-01T17:00:00Z"}}}))
    b = client.get("/api/freshness").json()
    assert b["mode"] == "auto_snapshot" and b["published_at"] == "2026-11-01T17:00:00Z" and b["next_check_at"]
    (tmp_path / "PUBLISH_STATUS.json").write_text(json.dumps({"interval_seconds": 60, "last_attempt_at": "2026-10-01T00:00:00Z"}))
    assert client.get("/api/freshness").json()["mode"] == "frozen_snapshot"             # publisher stopped: frozen


def test_api_freshness_refuses_a_store_of_another_sport(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setitem(app.dependency_overrides, api_mode, lambda: "real")   # another module may leave a demo override
    c = db.connect(tmp_path / "nfl.sqlite")
    db.migrate(c)
    db.claim_store(c, "nba")
    c.close()
    assert TestClient(app).get("/api/freshness", params={"sport": "nfl"}).status_code == 503


# --- Preview publisher --------------------------------------------------------------------------------------------
def _dev(tmp_path):
    live, dev = tmp_path / "live", tmp_path / "dev"
    live.mkdir()
    dev.mkdir()
    (dev / ".nba-dev-data").write_text(MARK_TEXT)      # the publisher checks the launcher's marker (app/devpaths.py)
    return live, dev


def _live_store(live, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(live))
    c = db.connect(live / "nba.sqlite")
    db.migrate(c)
    db.claim_store(c, "nba")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def test_publisher_republishes_only_changed_data_and_readers_see_whole_revisions(tmp_path, monkeypatch):
    live, dev = _dev(tmp_path)
    c = _live_store(live, monkeypatch)
    games(c, event("e1", "2026-11-01T23:00Z", "1", "2"))
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    assert st["this_check"] == {"nba": "published"} and (dev / "SNAPSHOT_AT").is_file() and st["last_error"] is None
    reader = sqlite3.connect(dev / "nba.sqlite")                     # an API connection open during the next publication
    assert reader.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    assert st["this_check"] == {}                                    # nothing changed: no copy made
    games(c, event("e2", "2026-11-01T23:30Z", "3", "4"), at=NOW)     # committed in the live WAL, not checkpointed
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    assert st["this_check"] == {"nba": "published"} and st["sports"]["nba"]["reason"] == "data changed"
    assert reader.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    reader.close()


def test_publisher_failure_keeps_the_previous_revision_and_is_reported(tmp_path, monkeypatch):
    live, dev = _dev(tmp_path)
    c = _live_store(live, monkeypatch)
    games(c, event("e1", "2026-11-01T23:00Z", "1", "2"))
    preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    stamp = (dev / "SNAPSHOT_AT").read_text()
    games(c, event("e2", "2026-11-01T23:30Z", "3", "4"), at=NOW)

    def broken(*_a):
        raise sqlite3.DatabaseError("disk full")
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60, publish=broken)
    assert st["this_check"] == {"nba": "failed"} and "disk full" in st["last_error"]
    assert (dev / "SNAPSHOT_AT").read_text() == stamp
    assert sqlite3.connect(dev / "nba.sqlite").execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)   # retried next check
    assert st["this_check"] == {"nba": "published"} and st["last_error"] is None


def test_publisher_refuses_the_live_directory_and_unmarked_directories(tmp_path):
    live, dev = _dev(tmp_path)
    with pytest.raises(ValueError):
        preview_publish.run(live, live, once=True)
    (dev / ".nba-dev-data").unlink()
    with pytest.raises(ValueError):
        preview_publish.run(live, dev, once=True)


def test_publisher_lag_rule():
    live = {"revision": "a", "source_fetched_at": "2026-11-01T18:20:00Z"}
    assert preview_publish.needs_publish(live, None, False, timedelta(minutes=10)) == "no served copy"
    assert preview_publish.needs_publish(live, {**live, "source_fetched_at": "2026-11-01T18:15:00Z"}, True,
                                         timedelta(minutes=10)) is None
    assert "lags" in preview_publish.needs_publish(live, {**live, "source_fetched_at": "2026-11-01T18:05:00Z"}, True,
                                                   timedelta(minutes=10))
    assert preview_publish.needs_publish(None, None, False, timedelta(minutes=10)) is None


# --- Review findings 2026-11-01 (Codex): regressions ----------------------------------------------------------------
MARK_TEXT = "nba-predictor development data copy; created by scripts/dev.sh, which may overwrite it\n"


def _schema(path):
    c = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    finally:
        c.close()


def _marked_dev(tmp_path):
    live, dev = tmp_path / "live", tmp_path / "dev"
    live.mkdir()
    dev.mkdir()
    (dev / ".nba-dev-data").write_text(MARK_TEXT)
    return live, dev


def _old_live_store(live):
    """A live store one migration behind this checkout, as the review reproduction used (schema 31 → 32)."""
    c = db.connect(live / "nba.sqlite")
    db.migrate(c)
    db.claim_store(c, "nba")
    top = c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    c.execute("DELETE FROM schema_migrations WHERE version = ?", (top,))
    c.commit()
    c.close()
    return top - 1


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_publisher_never_writes_through_a_destination_that_leads_to_the_live_store(tmp_path, monkeypatch, link):
    live, dev = _marked_dev(tmp_path)
    monkeypatch.setenv("NBA_DATA_DIR", str(dev))
    before = _old_live_store(live)
    if link == "symlink":
        (dev / "nba.sqlite").symlink_to(live / "nba.sqlite")
    else:
        import os
        os.link(live / "nba.sqlite", dev / "nba.sqlite")
    with pytest.raises(ValueError):                       # refused at startup, before any write
        preview_publish.run(live, dev, once=True)
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)   # and on every check
    assert st["this_check"]["nba"] == "failed" and "refused" in st["last_error"]
    assert not (dev / "PUBLISH_STATUS.json").exists() and not (dev / ".publish-staging").exists()   # nothing written
    assert _schema(live / "nba.sqlite") == before          # the source was not migrated or written


def test_publisher_refuses_a_development_directory_inside_the_live_one(tmp_path, monkeypatch):
    live = tmp_path / "live"
    dev = live / "dev"
    dev.mkdir(parents=True)
    (dev / ".nba-dev-data").write_text(MARK_TEXT)
    with pytest.raises(ValueError):
        preview_publish.run(live, dev, once=True)


def test_odds_only_change_moves_the_revision(nba):
    games(nba, event("e1", _fmt(NOW + timedelta(hours=5)), "1", "2", odds=("-150", "+130")))
    rev = freshness.revision(nba)
    out = games(nba, event("e1", _fmt(NOW + timedelta(hours=5)), "1", "2", odds=("-200", "+170")), at=NOW)
    assert nba.execute("SELECT COUNT(DISTINCT home_american) FROM odds_quotes").fetchone()[0] == 2
    assert freshness.revision(nba) != rev, out


def test_copy_freshness_is_per_sport(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setitem(app.dependency_overrides, api_mode, lambda: "real")
    for sport in ("nba", "nfl"):
        c = db.connect(tmp_path / f"{sport}.sqlite")
        db.migrate(c)
        db.claim_store(c, sport)
        c.close()
    now = datetime.now(UTC)
    (tmp_path / "SNAPSHOT_AT").write_text(_fmt(now) + "\n")          # the publisher just republished NBA
    (tmp_path / "PUBLISH_STATUS.json").write_text(json.dumps({
        "interval_seconds": 60, "last_attempt_at": _fmt(now), "next_attempt_at": _fmt(now + timedelta(seconds=60)),
        "initial_snapshot_at": "2026-11-01T17:00:00Z",
        "sports": {"nba": {"status": "published", "published_at": _fmt(now)}}}))
    client = TestClient(app)
    nba_b = client.get("/api/freshness", params={"sport": "nba"}).json()
    nfl_b = client.get("/api/freshness", params={"sport": "nfl"}).json()
    assert nba_b["mode"] == "auto_snapshot" and nba_b["published_at"] == _fmt(now)
    assert nfl_b["mode"] == "frozen_snapshot" and nfl_b["published_at"] == "2026-11-01T17:00:00Z"
    (tmp_path / "PUBLISH_STATUS.json").write_text(json.dumps({
        "interval_seconds": 60, "last_attempt_at": _fmt(now), "last_error": "nba: DatabaseError: disk full",
        "initial_snapshot_at": "2026-11-01T17:00:00Z",
        "sports": {"nba": {"status": "failed", "error": "nba: DatabaseError: disk full", "published_at": _fmt(now)},
                   "nfl": {"status": "published", "published_at": _fmt(now)}}}))
    nfl_b = client.get("/api/freshness", params={"sport": "nfl"}).json()
    nba_b = client.get("/api/freshness", params={"sport": "nba"}).json()
    assert nfl_b["mode"] == "auto_snapshot" and not nfl_b.get("publish_error")
    assert "disk full" in (nba_b.get("publish_error") or "")
