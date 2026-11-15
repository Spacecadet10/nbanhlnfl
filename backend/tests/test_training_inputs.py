"""Training inputs (training_inputs.py): collect, then check, before a scheduled refit. Synthetic NHL store; no network."""
import json
import sqlite3

import pytest

from app import adaptive, cli, config, jobs, training_inputs
from app.sources import nhl_injuries, nhl_pbp
from test_adaptive import TRAIN_AT, store  # noqa: F401  (fixture)
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)

NOW = "2026-10-12T20:40:00Z"


def _fresh_refresh(conn, at="2026-10-12T20:30:00Z"):
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('refresh', ?, ?, 'success')", (at, at))


def _recent_finals(conn, now=NOW):
    return [r["game_id"] for r in conn.execute(
        "SELECT game_id FROM games WHERE status='final' AND result_available_at > ? AND result_available_at <= ?",
        (training_inputs._fmt(training_inputs._parse(now) - training_inputs.RECENT), now))]


def _box(conn, gid):
    box = nhl_injuries.root() / "boxes"
    box.mkdir(parents=True, exist_ok=True)
    (box / f"{nhl_pbp.api_id(gid)}.json").write_text(json.dumps({"game_id": gid}))


def _complete_shots(conn, gid, at="2026-10-12T10:00:00Z"):
    conn.execute("INSERT INTO raw_responses (source, endpoint, request_key, content_hash, byte_size, path, importer_version, "
                 "first_retrieved_at) VALUES ('t', 't', ?, 'h', 1, 'p', 't', ?)", (gid, at))
    raw = conn.execute("SELECT max(raw_id) FROM raw_responses").fetchone()[0]
    conn.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (?, ?)", (raw, at))
    fetch = conn.execute("SELECT max(fetch_id) FROM raw_fetches").fetchone()[0]
    conn.execute("INSERT INTO nhl_pbp_imports (fetch_id, raw_id, game_id, retrieved_at, available_at, policy, complete, shots, "
                 "importer_version) VALUES (?, ?, ?, ?, ?, 'observed', 1, 0, 't')", (fetch, raw, gid, at, at))


def _settle_recent(conn):
    """One final whose result became available inside the recent window."""
    add_upcoming(conn, gid=2026020050, start="2026-10-11T19:00:00", state=7, hs=3, vs=1, period=3, at="2026-10-12T03:00:00Z")
    return "nhl:2026020050"


def test_check_blocks_training_on_stale_results_and_on_recent_finals_without_shots_and_reports_missing_boxes(store, monkeypatch):
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    gid = _settle_recent(store)
    c = training_inputs.check(store, NOW)
    assert not c["ready"] and "results" in c["blocking"], c          # the fixture's last refresh is older than REFRESH_HOURS
    _fresh_refresh(store)
    c = training_inputs.check(store, NOW)
    assert not c["ready"] and gid in c["blocking"]["nhl_shots"]["first"], c
    # Boxes never gate: they are collected last, right before training, and once one has arrived the NHL injury member is
    # retired until the next fit anyway; a box the source has not published is reported (the run shows as failed).
    assert "nhl_player_boxes" not in c["blocking"] and c["report"]["nhl_player_boxes"]["recent_missing"] >= 1
    for g in _recent_finals(store):
        _complete_shots(store, g)
        _box(store, g)
    c = training_inputs.check(store, NOW)
    assert c["ready"] and c["report"]["nhl_player_boxes"]["recent_missing"] == 0, c
    assert c["report"]["nhl_shots"]["older_missing"] >= 0 and "nhl_player_boxes" in c["report"]


def test_without_the_box_collector_missing_boxes_are_reported_not_blocking(store, monkeypatch):
    monkeypatch.delenv("NHL_PLAYER_BOX_REFRESH", raising=False)
    _settle_recent(store)
    _fresh_refresh(store)
    for g in _recent_finals(store):
        _complete_shots(store, g)
    c = training_inputs.check(store, NOW)
    assert c["ready"] and "nhl_player_boxes" not in c["blocking"], c
    assert "NHL_PLAYER_BOX_REFRESH" in c["report"]["nhl_player_boxes"]["note"]


def test_collect_runs_refresh_shots_and_boxes_in_order_and_logs_a_visible_job_run(store, monkeypatch):
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    calls = []
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: calls.append("refresh") or {"status": "success"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: calls.append("shots") or {"status": "skipped"})
    from app import nhl_player_ratings
    monkeypatch.setattr(nhl_player_ratings, "collect_bulk_boxes",
                        lambda conn, a, b, **kw: calls.append(("boxes", a, b)) or {"stored": 0, "cached": 3, "failed": 0, "errors": []})
    monkeypatch.setattr(training_inputs, "collect_recent_shots", lambda conn, now=None: calls.append("recent") or {"status": "skipped"})
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": True, "blocking": {}, "report": {}})
    out = training_inputs.run(store, now=NOW)
    assert calls[:3] == ["refresh", "recent", "shots"] and calls[3][0] == "boxes", calls      # boxes last, after the pre-check
    row = store.execute("SELECT * FROM job_runs WHERE job='training_inputs' ORDER BY run_id DESC LIMIT 1").fetchone()
    assert row is not None and row["status"] == out["status"]
    assert out["status"] in ("success", "failed") and "check" in out


def test_a_failing_collector_is_reported_and_the_check_still_runs(store, monkeypatch):
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "failed", "error": "ConnectError"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    from app import nhl_player_ratings

    def down(*a, **kw):
        raise OSError("network down")
    monkeypatch.setattr(nhl_player_ratings, "collect_bulk_boxes", down)
    monkeypatch.setattr(training_inputs, "collect_recent_shots", lambda conn, now=None: {"status": "skipped"})
    checks = iter([{"ready": True, "blocking": {}, "report": {}},                          # pre-check passes; final does not
                   {"ready": False, "blocking": {"nhl_shots": "missing"}, "report": {}}])
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: next(checks))
    out = training_inputs.run(store, now=NOW)
    assert out["steps"]["refresh"]["status"] == "failed"
    assert out["steps"]["nhl_player_boxes"]["status"] == "failed" and "network down" in out["steps"]["nhl_player_boxes"]["error"]
    assert out["status"] == "failed" and not out["check"]["ready"]


def test_adaptive_train_command_defers_training_when_inputs_are_not_ready(store, monkeypatch, capsys):
    """`--force` does not bypass it; nothing is trained; the fits in force are unchanged (exit 1, visible)."""
    called = []
    monkeypatch.setattr(adaptive, "run_scheduled", lambda *a, **k: called.append(1) or {"status": "success"})
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": False, "blocking": {"results": "stale"}, "report": {}})
    assert cli.main(["--sport", "nhl", "adaptive-train", "--force"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "deferred" and not called and res["inputs"]["blocking"] == {"results": "stale"}, res
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": True, "blocking": {}, "report": {}})
    assert cli.main(["--sport", "nhl", "adaptive-train", "--force"]) == 0 and called


def test_training_inputs_command_exits_1_when_not_ready(store, monkeypatch, capsys):
    monkeypatch.setattr(training_inputs, "run", lambda conn, now=None, collect=True: {"status": "failed", "check": {"ready": False}})
    assert cli.main(["--sport", "nhl", "training-inputs"]) == 1
    monkeypatch.setattr(training_inputs, "run", lambda conn, now=None, collect=True: {"status": "success", "check": {"ready": True}})
    assert cli.main(["--sport", "nhl", "training-inputs"]) == 0


def test_a_failed_input_run_is_visible_in_the_status_panel_at_once(store, monkeypatch):
    from app import training_monitor
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "failed", "error": "ConnectError"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.delenv("NHL_PLAYER_BOX_REFRESH", raising=False)
    training_inputs.run(store, now=NOW)
    j = training_monitor.status(store)["inputs"]["training_inputs"]
    assert j["last_status"] == "failed" and "results" in j["last_failure"]["reason"], j


def test_recent_finals_are_imported_first_so_an_old_backlog_cannot_starve_the_gate(store, monkeypatch):
    from app import nhl_shots
    old = [{"game_id": f"nhl:old{i}", "tipoff_at": "2026-01-01T00:00:00Z", "result_available_at": "2026-01-01T03:00:00Z"}
           for i in range(80)]
    new = {"game_id": "nhl:recent", "tipoff_at": "2026-10-11T23:00:00Z", "result_available_at": "2026-10-12T03:00:00Z"}
    monkeypatch.setattr(nhl_shots, "pending", lambda conn, seasons=None: old + [new])
    fetched = []
    monkeypatch.setattr(nhl_pbp, "fetch", lambda gid: fetched.append(gid) or b"{}")
    monkeypatch.setattr(nhl_shots, "import_game", lambda conn, g, body, at: {"complete": True})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    out = training_inputs.collect_recent_shots(store, NOW)
    assert fetched == ["nhl:recent"] and out["imported"] == 1, (fetched, out)


def test_boxes_are_not_collected_when_another_input_already_defers_training(store, monkeypatch):
    """A box added after a fit retires the NHL injury member until the next fit: never add one when training will not run."""
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "failed", "error": "ConnectError"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    from app import nhl_player_ratings
    monkeypatch.setattr(nhl_player_ratings, "collect_bulk_boxes", lambda *a, **k: pytest.fail("boxes collected"))
    out = training_inputs.run(store, now=NOW)
    assert out["steps"]["nhl_player_boxes"]["status"] == "deferred" and "results" in out["check"]["blocking"], out


def test_a_game_still_missing_shots_after_three_collection_runs_stops_gating_and_is_reported(store, monkeypatch):
    gid = _settle_recent(store)
    _fresh_refresh(store)
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(training_inputs, "collect_recent_shots",          # the source never delivers this game's shots
                        lambda conn, now=None: {"status": "failed", "errors": {gid: "x"}})
    results = [training_inputs.run(store, now=NOW)["check"] for _ in range(3)]
    assert [r["ready"] for r in results] == [False, False, True], results
    assert gid in results[-1]["report"]["nhl_shots"]["given_up"]


def test_a_failed_box_collection_is_visible_but_does_not_stop_training(store, monkeypatch):
    """Boxes that did arrive have already retired the NHL injury member; training is how it comes back."""
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    _settle_recent(store)
    _fresh_refresh(store)
    for g in _recent_finals(store):
        _complete_shots(store, g)
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(training_inputs, "collect_recent_shots", lambda conn, now=None: {"status": "skipped"})
    from app import nhl_player_ratings
    monkeypatch.setattr(nhl_player_ratings, "collect_bulk_boxes",
                        lambda conn, a, b, **kw: {"stored": 0, "cached": 0, "failed": 1, "errors": [{"game_id": "x", "error": "y"}]})
    out = training_inputs.run(store, now=NOW)
    assert out["check"]["ready"] and out["status"] == "failed", out          # train, but show the failed step
    from app import training_monitor
    j = training_monitor.status(store)["inputs"]["training_inputs"]
    assert "nhl_player_boxes" in j["last_failure"]["reason"], j


def test_a_deferred_training_run_leaves_a_visible_failure(store, monkeypatch, capsys):
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": False, "blocking": {"results": "stale"}, "report": {}})
    assert cli.main(["--sport", "nhl", "adaptive-train", "--cadence", "daily"]) == 1
    row = store.execute("SELECT * FROM job_runs WHERE job='training_inputs' ORDER BY run_id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed" and "adaptive-train --cadence daily" in json.loads(row["detail"])["deferred_by"]


def test_a_deferral_on_a_store_that_cannot_record_it_still_reports_deferred(store, monkeypatch, capsys):
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": False, "blocking": {"results": "stale"}, "report": {}})

    def broken(*a, **k):
        raise sqlite3.IntegrityError("CHECK constraint failed: job IN (...)")
    monkeypatch.setattr(training_inputs, "record_deferral", broken)
    assert cli.main(["--sport", "nhl", "adaptive-train"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "deferred" and "not recorded" in res["deferral_record"], res


def test_a_run_that_never_finished_shows_as_a_failure_after_two_hours(store):
    from app import training_monitor
    store.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('training_inputs', '2026-10-12T17:00:00Z', 'running')")
    j = training_monitor.status(store, now="2026-10-12T20:00:00Z")["inputs"]["training_inputs"]
    assert j["last_failure"] and "did not finish" in j["last_failure"]["reason"], j


def test_every_launchd_template_parses_with_the_strict_xml_parser():
    """plutil tolerates '--' inside an XML comment; plistlib (used by the research-agent guards) does not."""
    import plistlib
    for p in sorted((config.REPO_ROOT / "scripts" / "launchd").glob("*.plist")):
        plistlib.loads(p.read_bytes())


def test_training_inputs_command_exits_0_when_ready_although_a_collector_failed(store, monkeypatch, capsys):
    """A failed box step is visible (job_runs 'failed') but must not stop training: the plist trains on exit 0."""
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(training_inputs, "collect_recent_shots", lambda conn, now=None: {"status": "skipped"})
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": True, "blocking": {}, "report": {}})
    from app import nhl_player_ratings

    def down(*a, **kw):
        raise OSError("stats API down")
    monkeypatch.setattr(nhl_player_ratings, "collect_bulk_boxes", down)
    assert cli.main(["--sport", "nhl", "training-inputs"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and out["steps"]["nhl_player_boxes"]["status"] == "failed", out


def test_an_unreadable_recent_box_file_is_quarantined_so_it_is_fetched_again(store, monkeypatch):
    """A box written by an interrupted run is truncated: it would count as present and make the member's loader raise."""
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    folder = nhl_injuries.root() / "boxes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026020050.json").write_text('{"game_id": "nhl:2026020050", "teams": {')
    (folder / "2026020051.json").write_text(json.dumps({"game_id": "nhl:2026020051"}))
    import os
    import time
    for f in folder.iterdir():                                   # written an hour ago (not still being written)
        os.utime(f, (time.time() - 3600, time.time() - 3600))
    seen = []
    from app import nhl_player_ratings
    monkeypatch.setattr(nhl_player_ratings, "collect_bulk_boxes",
                        lambda conn, a, b, **kw: seen.append(sorted(p.name for p in folder.glob("[0-9]*.json"))) or
                        {"stored": 0, "cached": 1, "failed": 0, "errors": []})
    out = training_inputs.collect_boxes(store)
    assert seen == [["2026020051.json"]] and out["quarantined"] == ["2026020050.json"], (seen, out)
    assert any(p.name.startswith("2026020050.json.corrupt-") for p in folder.iterdir())


def test_a_truncated_raw_bulk_file_is_quarantined_and_a_file_being_written_is_left_alone(store, monkeypatch):
    """bulk-*.raw names carry the sha256 of their bytes; a truncated one would make every later load fail its hash check.
    Files modified in the last few minutes may still be open by another collector (`nhl-player-inputs`): left alone."""
    import hashlib
    import os
    import time
    folder = nhl_injuries.root() / "boxes"
    folder.mkdir(parents=True, exist_ok=True)
    good = b'{"data": []}'
    (folder / f"bulk-skater-1-2-{hashlib.sha256(good).hexdigest()}.raw").write_bytes(good)
    bad = folder / f"bulk-goalie-1-2-{hashlib.sha256(good + b'x').hexdigest()}.raw"
    bad.write_bytes(good)                                       # truncated: its bytes no longer match the name
    old = time.time() - 3600
    for p in folder.iterdir():
        os.utime(p, (old, old))
    fresh = folder / "2026020052.json"
    fresh.write_text('{"game_id": ')                            # being written right now
    out = training_inputs._quarantine_unreadable_boxes(folder)
    assert out == [bad.name] and fresh.exists(), out


def test_game_files_that_point_at_a_quarantined_raw_file_are_quarantined_too(store):
    """Otherwise the game counts as collected (its JSON exists) while its raw is gone: never fetched again, and the
    member's loader fails on the missing file."""
    import hashlib
    import os
    import time
    folder = nhl_injuries.root() / "boxes"
    folder.mkdir(parents=True, exist_ok=True)
    good = b'{"data": []}'
    ok_raw = f"bulk-skater-1-2-{hashlib.sha256(good).hexdigest()}.raw"
    bad_raw = f"bulk-goalie-1-2-{hashlib.sha256(good + b'x').hexdigest()}.raw"
    (folder / ok_raw).write_bytes(good)
    (folder / bad_raw).write_bytes(good)
    (folder / "2026020060.json").write_text(json.dumps({"game_id": "nhl:2026020060", "raw_files": [ok_raw, bad_raw]}))
    (folder / "2026020061.json").write_text(json.dumps({"game_id": "nhl:2026020061", "raw_files": [ok_raw, ok_raw]}))
    for p in folder.iterdir():
        os.utime(p, (time.time() - 3600, time.time() - 3600))
    out = training_inputs._quarantine_unreadable_boxes(folder)
    assert sorted(out) == sorted([bad_raw, "2026020060.json"]) and (folder / "2026020061.json").exists(), out



def test_the_runs_own_results_refresh_counts_for_its_check_and_its_shot_collection(store, monkeypatch):
    # Box-timing rehearsal (2026-10-16 13:35Z): the run's refresh finished one second after the run started, and the check,
    # bounded by the start time, called the results stale, deferring the boxes and training. Without an explicit `now`,
    # recent shots and the checks read the clock after the refresh (review: a final it settles must not be skipped by
    # the shot collector and then counted missing by the check).
    clock = ["2026-10-12T20:40:00Z"]
    monkeypatch.setattr(training_inputs, "_now", lambda: clock[0])

    def refresh(conn, **kw):
        _fresh_refresh(conn, at="2026-10-12T20:40:03Z")
        clock[0] = "2026-10-12T20:40:04Z"
        return {"status": "success"}
    monkeypatch.setattr(jobs, "refresh", refresh)
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    seen = []
    monkeypatch.setattr(training_inputs, "collect_recent_shots",
                        lambda conn, now=None: seen.append(now or training_inputs._now()) or {"status": "skipped"})
    out = training_inputs.run(store)
    assert seen == ["2026-10-12T20:40:04Z"]
    assert "results" not in out["check"]["blocking"], out["check"]
    assert out["check"]["report"]["results"]["last_refresh"] == "2026-10-12T20:40:03Z"
    assert out["check"]["as_of"] == "2026-10-12T20:40:04Z"
