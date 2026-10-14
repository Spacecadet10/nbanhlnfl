"""Training-run status (training_monitor.run_status, /api/training-run): reports only evidence — a process holding the
training lock, artifacts actually written, published fit rows, the latest live forecasts — and never estimates progress."""
import calendar
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import db, training_monitor
from app.main import api_mode, app

NOW = "2026-10-11T12:00:00Z"


@pytest.fixture
def live(tmp_path):
    conn = db.connect(tmp_path / "nhl.sqlite")
    db.migrate(conn)
    return conn


def _fit(conn, defn, kind, name, status, period="2027:2026-W41", at="2026-10-10T04:23:06Z", detail=None):
    conn.execute("INSERT INTO adaptive_fits (sport, kind, name, definition, period, schedule, status, training_cutoff, "
                 "training_games, detail, started_at, finished_at, idempotency_key) VALUES ('nhl', ?, ?, ?, ?, 'weekly', ?, "
                 "'2026-10-10T02:10:26Z', 100, ?, '2026-10-10T04:00:00Z', ?, ?)",
                 (kind, name, defn, period, status, json.dumps(detail or {}), at, f"{defn}-{kind}-{name}-{status}-{at}"))


def _artifact(root, defn, member, mtime=None):
    d = root / "members" / member
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{defn}-2027_2026-W41-abc.pkl").write_bytes(b"x")
    digest = d / f"{defn}-2027_2026-W41-abc.pkl.sha256"
    digest.write_text("0" * 64)
    if mtime:
        os.utime(digest, (mtime, mtime))


def test_finished_runs_failures_and_published_counts_come_from_the_ledger(live, tmp_path):
    for i in range(3):
        _fit(live, "old", "member", f"m{i}", "success")
    _fit(live, "old", "combiner", "combiner", "success")
    _fit(live, "new", "member", "m0", "success", at="2026-10-11T03:00:00Z")
    _fit(live, "new", "member", "m1", "failed", at="2026-10-11T03:00:00Z", detail={"error": "ValueError: boom"})
    _fit(live, "new", "combiner", "combiner", "partial", at="2026-10-11T03:00:00Z", detail={"partial_reason": "m1 failed"})
    _fit(live, "later", "combiner", "combiner", "success", at="2026-10-12T00:00:00Z")      # after `now`: invisible
    out = training_monitor.run_status(live, tmp_path / "train", "nhl", NOW, code_definition="new", expected_members=3,
                                      holders=lambda _p: [])
    assert out["running"] is False and out["current_run"] is None
    assert out["last_run"]["definition"] == "new" and out["last_run"]["status"] == "partial"
    assert (out["last_run"]["members_succeeded"], out["last_run"]["members_failed"]) == (1, 1)
    assert out["last_run"]["error"] == "m1 failed"
    assert out["last_success"]["definition"] == "new"          # partial is a usable fit
    assert out["last_failure"] == {"definition": "new", "kind": "member", "name": "m1", "at": "2026-10-11T03:00:00Z",
                                   "error": "ValueError: boom"}
    assert out["code_definition_published"]["members_succeeded"] == 1
    assert out["code_definition_published"]["combiner"]["status"] == "partial"
    assert out["serving"] == {"adaptive": None, "primary": None}   # no live forecast recorded: nothing claimed


def test_a_running_trainer_reports_only_artifacts_written_since_its_snapshot(live, tmp_path):
    root = tmp_path / "train"
    (root / "snapshots").mkdir(parents=True)
    (root / "snapshots" / "2026-10-11T030000Z-4242.sqlite").write_bytes(b"")
    (root / "snapshots" / "2026-10-10T191745Z-1.sqlite").write_bytes(b"")      # leftover of another pid: not this run
    start = calendar.timegm(time.strptime("2026-10-11T03:00:00", "%Y-%m-%dT%H:%M:%S"))
    _artifact(root, "new", "goals_poisson", mtime=start - 3600)                 # written before this run started
    _artifact(root, "new", "hockey_elo", mtime=start + 60)
    _artifact(root, "new", "hockey_gbt", mtime=start + 120)
    out = training_monitor.run_status(live, root, "nhl", NOW, code_definition="new", expected_members=15,
                                      holders=lambda _p: [4242])
    cur = out["current_run"]
    assert out["running"] is True and cur["started_at"] == "2026-10-11T03:00:00Z"
    assert cur["by_definition"]["new"]["members_written"] == 2
    assert cur["last_progress_at"] == "2026-10-11T03:02:00Z"
    assert out["code_definition_on_disk"]["members_written"] == 3              # all on disk, any run
    keys = set(out) | set(cur) | set(cur["by_definition"]["new"])
    assert not {k for k in keys if "percent" in k or "eta" in k or "remaining" in k}


def test_unknown_lock_state_is_reported_as_unknown(live, tmp_path):
    out = training_monitor.run_status(live, tmp_path, "nhl", NOW, holders=lambda _p: None)
    assert out["running"] is None and out["current_run"] is None
    assert "Could not determine" in out["running_note"]
    assert out["last_run"] is None and out["code_definition_published"] is None


def test_lock_holders_lists_a_process_with_the_lock_file_open_without_locking_it(tmp_path):
    lock = tmp_path / "train.lock"
    assert training_monitor.lock_holders(lock) == []                          # no file: nobody holds it
    lock.write_bytes(b"")
    assert training_monitor.lock_holders(lock) == []
    holder = subprocess.Popen([sys.executable, "-c", "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR); "
                               "fcntl.flock(fd, fcntl.LOCK_EX); print('ok', flush=True); time.sleep(30)", str(lock)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ok"
        found = training_monitor.lock_holders(lock)
        if found is None:
            pytest.skip("lsof unavailable in this environment")
        assert found == [holder.pid]
    finally:
        holder.kill()
        holder.wait()


def test_health_reports_the_snapshot_time_of_the_served_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    previous = app.dependency_overrides.get(api_mode)
    app.dependency_overrides[api_mode] = lambda: "demo"
    try:
        client = TestClient(app)
        assert client.get("/api/health").json()["data_snapshot_at"] is None
        (tmp_path / "SNAPSHOT_AT").write_text("2026-10-10T20:00:00Z\n")
        assert client.get("/api/health").json()["data_snapshot_at"] == "2026-10-10T20:00:00Z"
        assert client.get("/api/training-run").json()["mode"] == "demo"
    finally:
        if previous is None:
            app.dependency_overrides.pop(api_mode, None)
        else:
            app.dependency_overrides[api_mode] = previous


def test_trained_versions_not_making_forecasts_are_listed_from_disk_and_ledger(live, tmp_path):
    root = tmp_path / "train"
    _fit(live, "old", "member", "m0", "success")
    _fit(live, "old", "combiner", "combiner", "success")
    _artifact(root, "old", "m0")
    _artifact(root, "unpub", "m0")
    _artifact(root, "unpub", "m1")
    out = training_monitor.run_status(live, root, "nhl", NOW, holders=lambda _p: [])
    states = {x["definition"]: (x["state"], x["members_written"]) for x in out["not_serving"]}
    # no live forecast yet: the published run is listed as not serving; the unpublished files too; nothing else
    assert states == {"unpub": ("written, not published", 2), "old": ("published, no live forecast yet", 1)}


def _adaptive_row(conn, defn, gen, n, status):
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TRIGGER IF EXISTS adaptive_forecasts_two_hour_window")
    p = (0.6, 0.0, 0.4) if status == "forecast" else (None, None, None)
    conn.execute("INSERT INTO adaptive_forecasts (game_id, sport, market, line, line_source, odds_status, model_version, definition, "
                 "status, reason, p_a, p_mid, p_b, members, missing, weights, selection, information_at, generated_at, tipoff_at, "
                 "idempotency_key) VALUES (?, 'nhl', 'winner', NULL, 'none', 'none', ?, ?, ?, ?, ?, ?, ?, '[]', '{}', 'null', '{}', "
                 "?, ?, '2026-10-11T11:00:00Z', ?)",
                 (f"g{n}", f"adaptive-v1:{defn}", defn, status, None if status == "forecast" else "no combination", *p, gen, gen, f"k{n}"))


@pytest.mark.parametrize("weekly_status", ["forecast", "unavailable"])
def test_two_definitions_recording_in_one_cycle_are_judged_each_on_their_own_rows(live, tmp_path, weekly_status):
    """Reviewer reproduction (2026-10-13): with daily and weekly definitions recording in the same cycle, only the single
    newest row was looked at, so the newest published (weekly) combiner was listed as 'published, no live forecast yet'
    although it had just recorded; 'unavailable' rows were counted as forecasts."""
    _fit(live, "D", "combiner", "combiner", "success", period="2027:2026-10-09", at="2026-10-09T04:00:00Z")
    _fit(live, "W", "combiner", "combiner", "success", at="2026-10-10T04:23:06Z")
    _adaptive_row(live, "W", "2026-10-11T10:00:00Z", 1, weekly_status)
    _adaptive_row(live, "D", "2026-10-11T10:00:01Z", 2, "forecast")
    out = training_monitor.run_status(live, tmp_path / "train", "nhl", NOW, holders=lambda _p: [])
    states = {x["definition"]: x["state"] for x in out["not_serving"]}
    defs = {d["definition"]: d for d in out["serving"]["adaptive"]["definitions"]}
    assert set(defs) == {"W", "D"} and defs["D"]["forecasts"] == 1
    if weekly_status == "forecast":
        assert "W" not in states and defs["W"]["forecasts"] == 1
    else:
        assert states["W"].startswith("published, recording only 'unavailable' rows") and defs["W"]["forecasts"] == 0
        assert defs["W"]["unavailable"] == 1
