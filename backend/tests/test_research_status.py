"""/api/research/autoresearch: read-only status of the sports autoresearch experiment directory."""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    return TestClient(main.app)


def _w(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj) if isinstance(obj, dict) else "".join(json.dumps(x) + "\n" for x in obj))


def test_not_configured_and_missing_directory_are_explicit(client, monkeypatch, tmp_path):
    monkeypatch.delenv("NBA_AUTORESEARCH_DIR", raising=False)
    r = client.get("/api/research/autoresearch").json()
    assert r["configured"] is False and "not set" in r["reason"] and "Research only" in r["note"]
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(tmp_path / "nope"))
    assert "does not exist" in client.get("/api/research/autoresearch").json()["reason"]


def test_demo_mode_shows_no_research(monkeypatch, tmp_path):
    monkeypatch.setenv("NBA_API_MODE", "demo")
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(tmp_path))
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    r = TestClient(main.app).get("/api/research/autoresearch").json()
    assert r["mode"] == "demo" and r["configured"] is False


def test_trials_failures_incumbents_runs_and_stop_are_reported(client, monkeypatch, tmp_path):
    exp = tmp_path / "exp"
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    meta = {"protocol_id": "p-new", "sport": "nhl", "registered_at": "2026-10-14T10:00:00Z",
            "protocol": {"scored_seasons": {"nhl": [2022]}, "holdout_seasons": {"nhl": [2025]}}, "cohort": {"reg3": 10},
            "start_incumbent": {"reg3": {"member": "goals_draw", "log_loss": 1.03}, "winner": {"member": "x", "log_loss": 0.65}}}
    _w(exp / "p-new" / "nhl" / "protocol.json", meta)
    _w(exp / "p-old" / "nhl" / "protocol.json", {**meta, "protocol_id": "p-old", "registered_at": "2026-10-01T00:00:00Z"})
    mk = {"reg3": {"decision": "keep", "vs_incumbent": {"mean": -0.008, "ci95": [-0.01, -0.006]}, "forecast": 10, "cohort": 10},
          "winner": {"decision": "reject", "vs_incumbent": {"mean": 0.001, "ci95": [-0.001, 0.003]}}}
    trials = [{"event": "started", "trial_id": "t1"}, {"event": "finished", "trial_id": "t1", "commit": "a" * 40, "status": "keep",
                                                       "markets": mk, "agent_run": "r1", "description": "poisson draw"},
              {"event": "started", "trial_id": "t2"}, {"event": "finished", "trial_id": "t2", "commit": "b" * 40, "status": "crash",
                                                       "reason": "candidate process ended"},
              {"event": "finished", "trial_id": "t3", "commit": "c" * 40, "status": "invalid", "reason": "import os"},
              {"event": "started", "trial_id": "t4"}]
    p = exp / "p-new" / "nhl" / "trials.jsonl"
    _w(p, trials)
    with p.open("a") as fh:
        fh.write('{"event": "fini')                                # torn last line of a writer still appending
    _w(exp / "agent" / "runs.jsonl", [{"event": "started", "run_id": "r1", "at": "2026-10-14T11:00:00Z", "budget": {"max_trials": 3}},
                                       {"event": "finished", "run_id": "r1", "at": "2026-10-14T11:20:00Z", "stopped": "agent finished",
                                        "outcome": "kept vs incumbent (exploratory, not a promotion): nhl:reg3", "cost_usd": 1.2},
                                       {"event": "started", "run_id": "r0", "at": "2026-10-14T10:00:00Z"},
                                       {"event": "finished", "run_id": "r0", "at": "2026-10-14T10:01:00Z", "stopped": "stopped",
                                        "cost_usd": 0},
                                       {"event": "correction", "run_id": "r0", "at": "2026-10-14T12:00:00Z",
                                        "fields": {"cost_usd": None}, "reason": "killed agent: cost unknown, not 0"}])
    _w(exp / "agent" / "active.json", {"run_id": "r2", "driver_pid": 999999, "max_trials": 3})
    (exp / "agent" / "STOP").write_text("2026-10-14T12:00:00Z stopped by a person\n")
    r = client.get("/api/research/autoresearch").json()
    assert r["configured"] and r["mode"] == "real" and r["stopped"].endswith("stopped by a person")
    assert r["active_run"]["run_id"] == "r2" and r["active_run"]["driver_alive"] is False
    run, r0 = r["agent_runs"]
    assert run["end"] == "finished" and run["cost_usd"] == 1.2 and "nhl:reg3" in run["outcome"]
    assert r0["cost_usd"] is None and r0["end"] == "finished" and r0["corrections"][0]["reason"].startswith("killed agent")
    s = r["sports"]["nhl"]
    assert s["protocol_id"] == "p-new"                                # the latest registered protocol
    assert s["by_status"] == {"keep": 1, "crash": 1, "invalid": 1} and s["failures"] == 2
    assert s["running"] == ["t4"] and s["unreadable_lines"] == 1
    assert s["incumbents"]["reg3"]["source"] == "kept trial" and s["incumbents"]["winner"]["label"] == "x"
    assert [t["trial_id"] for t in s["recent"]] == ["t3", "t2", "t1"]
    assert s["recent"][2]["markets"]["reg3"]["delta"] == -0.008 and s["recent"][2]["agent_run"] == "r1"


def test_the_endpoint_writes_nothing(client, monkeypatch, tmp_path):
    exp = tmp_path / "exp"
    _w(exp / "p" / "nba" / "protocol.json", {"protocol_id": "p", "sport": "nba", "start_incumbent": {}})
    before = {q: q.stat().st_mtime_ns for q in exp.rglob("*")}
    os.chmod(exp / "p" / "nba", 0o555)
    try:
        monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
        assert client.get("/api/research/autoresearch").status_code == 200
    finally:
        os.chmod(exp / "p" / "nba", 0o755)
    assert {q: q.stat().st_mtime_ns for q in exp.rglob("*")} == before


def test_a_reused_driver_pid_is_not_reported_as_running(client, monkeypatch, tmp_path):
    # Review finding 7: a live process with the recorded pid but a different start time is not the driver.
    exp = tmp_path / "exp"
    _w(exp / "agent" / "active.json", {"run_id": "r9", "driver_pid": os.getpid(), "driver_token": "Thu Jan  1 00:00:00 1970"})
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    assert client.get("/api/research/autoresearch").json()["active_run"]["driver_alive"] is False


def test_no_absolute_paths_are_exposed(client, monkeypatch, tmp_path):
    exp = tmp_path / "exp"
    _w(exp / "agent" / "runs.jsonl", [{"event": "started", "run_id": "r1", "at": "2026-10-14T11:00:00Z"},
                                       {"event": "finished", "run_id": "r1", "at": "2026-10-14T11:01:00Z",
                                        "report": str(exp / "agent" / "runs" / "r1" / "report.json")}])
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    assert str(tmp_path) not in client.get("/api/research/autoresearch").text
