"""Activity-aware data refresh (app/freshness.py, jobs.refresh/tick; docs/REFRESH_PLAN.md). Synthetic payloads and a
controlled job clock; no network."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import cli, db, freshness, ingest, jobs
from test_live import event

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
