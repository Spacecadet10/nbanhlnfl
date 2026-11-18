"""Learning operations status (app/learning_ops.py): read-only, per sport, never says the system is forecasting with fits."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import learning_ops, main
from learning_support import T0, add_fit, combiner_detail, make_store, write_agents


@pytest.fixture
def env(tmp_path):
    data, agents = tmp_path / "data", tmp_path / "agents"
    conn = make_store(data, "nba")
    add_fit(conn, "nba", "member", "ridge", "success", detail={"fit": {}})
    add_fit(conn, "nba", "member", "cand:torch_mlp@adaptive", "skipped", detail={"reason": "torch==2.14.1 is not installed here and no fit exists yet"})
    add_fit(conn, "nba", "member", "cand:bad@adaptive", "failed", detail={"error": "boom"})
    add_fit(conn, "nba", "combiner", "combiner", "success", detail=combiner_detail("winner"))
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status, attempts, detail) VALUES ('training_inputs', ?, ?, 'failed', 0, ?)",
                 (T0, T0, '{"deferred_by": "adaptive-train --cadence daily"}'))
    conn.close()
    write_agents(agents, odds_env={"NBA_DAILY_AGREEMENT": "1"})
    return data, agents


def test_trainer_schedule_is_parsed_from_the_plist(env):
    data, agents = env
    t = learning_ops.status("nba", data, agents)["trainer"]
    assert t["installed"] is True and t["label"] == "com.nba-predictor.adaptive-train"
    assert t["times"] == ["09:30", "15:30"] and t["sports"] == ["nhl", "nba"] and t["covers_sport"] is True
    assert "adaptive-train" in t["program_summary"] and "training-inputs" in t["program_summary"]


def test_no_plist_is_reported_not_installed_and_a_sport_outside_the_loop_is_not_covered(tmp_path, env):
    data, _ = env
    assert learning_ops.status("nba", data, tmp_path / "empty")["trainer"]["installed"] is False
    other = tmp_path / "other"
    write_agents(other, trainer_sports="nhl")
    assert learning_ops.status("nba", data, other)["trainer"]["covers_sport"] is False


def test_recording_configured_comes_from_the_odds_job_env_and_unknown_when_absent(tmp_path, env):
    data, agents = env
    assert learning_ops.status("nba", data, agents)["recording"]["configured"] is True
    off, none = tmp_path / "off", tmp_path / "none"
    write_agents(off, odds_env={"NBA_DAILY_AGREEMENT": "0"})
    write_agents(none, trainer=False)
    assert learning_ops.status("nba", data, off)["recording"]["configured"] is False
    r = learning_ops.status("nba", data, none)["recording"]
    assert r["configured"] is None and "not found" in r["note"]


def test_latest_fits_count_members_and_list_skip_reasons(env):
    data, agents = env
    d = learning_ops.status("nba", data, agents)["fits"][0]
    assert d["combiner"]["status"] == "success" and d["combiner"]["finished_at"] == T0
    assert d["members"] == {"success": 1, "skipped": 1, "failed": 1, "partial": 0}
    assert d["skipped_reasons"] == ["torch==2.14.1 is not installed here and no fit exists yet"]


def test_deep_members_are_unverified_when_skipped_and_forecasting_is_never_inferred(env):
    data, agents = env
    s = learning_ops.status("nba", data, agents)
    assert s["deep_members"] == "unverified"
    assert "forecasting_with_fits" not in s and "forecasting" not in str(s.keys())
    assert "not" in s["note"].lower()


def test_training_inputs_runs_are_listed_with_the_deferring_command(env):
    data, agents = env
    r = learning_ops.status("nba", data, agents)["job_runs"][0]
    assert r["job"] == "training_inputs" and r["status"] == "failed" and r["deferred_by"] == "adaptive-train --cadence daily"


def test_unscheduled_sport_and_missing_store(tmp_path, env):
    data, agents = env
    s = learning_ops.status("ncaab", data, agents)
    assert s["trainer"]["covers_sport"] is False and "not scheduled" in s["trainer"]["reason"]
    assert s["fits"] == [] and s["store"] == "missing"


def test_read_only_never_changes_the_store(env):
    data, agents = env
    before = (data / "nba.sqlite").read_bytes()
    learning_ops.status("nba", data, agents)
    assert (data / "nba.sqlite").read_bytes() == before


def test_endpoint(env, monkeypatch):
    data, agents = env
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    monkeypatch.setenv("LEARNING_LAUNCHAGENTS_DIR", str(agents))
    r = TestClient(main.app).get("/api/learning/ops", params={"sport": "nba"})
    assert r.status_code == 200 and r.json()["trainer"]["installed"] is True
    assert TestClient(main.app).get("/api/learning/ops", params={"sport": "zzz"}).status_code == 422


def test_open_ro_closes_the_connection_when_the_probe_fails(tmp_path, monkeypatch):
    (tmp_path / "nba.sqlite").write_bytes(b"")
    closed = []

    class Conn:
        def execute(self, *a):
            raise sqlite3.OperationalError("boom")

        def close(self):
            closed.append(1)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: Conn())
    assert learning_ops.open_ro(tmp_path, "nba") == (None, "unreadable")
    assert len(closed) == 2        # one per mode tried
