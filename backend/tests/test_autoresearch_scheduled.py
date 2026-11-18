"""Scheduled autoresearch wrappers (scripts/autoresearch_daily.sh, scripts/autoresearch_agent_scheduled.sh) and their templates.
Only a fake prepare.py is ever run here, never the real one; the real ~/autoresearch-sports is never read."""
import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DAILY, AGENT = ROOT / "scripts" / "autoresearch_daily.sh", ROOT / "scripts" / "autoresearch_agent_scheduled.sh"
FMT = "%Y-%m-%dT%H:%M:%SZ"
NOW = datetime.now(timezone.utc)


def stamp(delta):
    return (NOW + delta).strftime(FMT)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "ar"
    (h / "experiments" / "agent").mkdir(parents=True)
    (h / "workspace").mkdir()
    fake = h / "workspace" / "prepare.py"
    fake.write_text("import json, sys, os\nopen(os.environ['FAKE_OUT'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
    return h


def run(script, home, tmp_path, rc_expected=None):
    env = dict(os.environ, AUTORESEARCH_HOME=str(home), AUTORESEARCH_PREPARE=str(home / "workspace" / "prepare.py"),
               AUTORESEARCH_PYTHON=sys.executable, LEARNING_PYTHON=sys.executable, LEARNING_BACKEND=str(ROOT / "backend"),
               NBA_DATA_DIR=str(tmp_path / "data"), FAKE_OUT=str(tmp_path / "calls.jsonl"), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=120)
    calls = (tmp_path / "calls.jsonl")
    return r, [json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []


# ---- evaluation (free) -----------------------------------------------------------------------------------------------
def test_daily_refuses_without_the_switch_file_and_runs_nothing(home, tmp_path):
    r, calls = run(DAILY, home, tmp_path)
    assert r.returncode == 78 and calls == [] and "SCHEDULE_DAILY_ENABLED" in r.stdout + r.stderr


def test_daily_runs_prepare_daily_through_learning_run_when_switched_on(home, tmp_path):
    (home / "SCHEDULE_DAILY_ENABLED").write_text("")
    r, calls = run(DAILY, home, tmp_path)
    assert r.returncode == 0, r.stderr
    assert calls == [["daily", "--sport", "nba", "--sport", "nhl", "--max-trials", "4", "--max-minutes", "60", "--timeout", "900"]]
    ev = [json.loads(x) for x in (tmp_path / "data" / "research" / "learning-runs.jsonl").read_text().splitlines()]
    assert ev[0]["name"] == "autoresearch-daily" and ev[0]["event"] == "start" and ev[-1]["rc"] == 0


def test_daily_never_names_the_agent_command():
    text = DAILY.read_text()
    assert "prepare.py agent" not in text and '"$PREPARE" agent' not in text and "--max-usd" not in text


# ---- generation (paid) -----------------------------------------------------------------------------------------------
def budget(**over):
    b = {"approved_by": "owner", "approved_at": stamp(-timedelta(days=1)), "period": "weekly", "max_usd_per_run": 1.5,
         "max_runs_per_period": 2, "model": "claude-opus-5-5", "max_trials": 3, "sports": ["nba"], "expires_at": stamp(timedelta(days=30))}
    b.update(over)
    return b


def put(home, b):
    (home / "AGENT_BUDGET.json").write_text(json.dumps(b))


def spend(home, *runs):
    """runs: (started_at, cost or None). None = started, never finished."""
    lines = []
    for i, (at, cost) in enumerate(runs):
        lines.append({"event": "started", "run_id": f"r{i}", "at": at})
        if cost is not None:
            lines.append({"event": "finished", "run_id": f"r{i}", "at": at, "cost_usd": cost})
    (home / "experiments" / "agent" / "runs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))


def refused(home, tmp_path, fragment):
    r, calls = run(AGENT, home, tmp_path)
    assert r.returncode == 78 and calls == [], (r.returncode, r.stdout, r.stderr)
    assert fragment in r.stdout + r.stderr, r.stdout + r.stderr


def test_agent_refuses_without_a_budget_file(home, tmp_path):
    refused(home, tmp_path, "AGENT_BUDGET.json")


@pytest.mark.parametrize("over,fragment", [
    ({"approved_by": ""}, "approved_by"), ({"approved_at": "yesterday"}, "approved_at"),
    ({"approved_at": stamp(timedelta(days=1))}, "approved_at"), ({"period": "monthly"}, "period"),
    ({"max_usd_per_run": 0}, "max_usd_per_run"), ({"max_usd_per_run": True}, "max_usd_per_run"),
    ({"max_runs_per_period": 0}, "max_runs_per_period"), ({"model": ""}, "model"), ({"model": "latest opus"}, "model"),
    ({"max_trials": 0}, "max_trials"), ({"sports": []}, "sports"), ({"sports": ["soccer"]}, "sports"),
    ({"expires_at": stamp(-timedelta(hours=1))}, "expired"), ({"expires_at": "soon"}, "expires_at"),
])
def test_agent_refuses_an_invalid_budget(home, tmp_path, over, fragment):
    put(home, budget(**over))
    refused(home, tmp_path, fragment)


def test_agent_refuses_a_budget_missing_a_field_or_not_json(home, tmp_path):
    b = budget()
    del b["model"]
    put(home, b)
    refused(home, tmp_path, "model")
    (home / "AGENT_BUDGET.json").write_text("{not json")
    refused(home, tmp_path, "AGENT_BUDGET.json")


def test_agent_refuses_when_the_period_has_no_runs_left(home, tmp_path):
    put(home, budget())
    spend(home, (stamp(-timedelta(minutes=5)), 0.1), (stamp(-timedelta(minutes=4)), 0.1))
    refused(home, tmp_path, "runs")


def test_agent_refuses_when_spend_leaves_no_room_for_a_full_run(home, tmp_path):
    put(home, budget())                                    # cap 1.5 x 2 = 3.0
    spend(home, (stamp(-timedelta(minutes=5)), 1.9))
    refused(home, tmp_path, "room")


def test_a_started_run_without_a_finish_counts_at_the_full_per_run_amount(home, tmp_path):
    put(home, budget(max_runs_per_period=4))               # cap 6.0; 2 x 1.5 unfinished + 1.6 + a full run = 6.1
    spend(home, (stamp(-timedelta(minutes=9)), None), (stamp(-timedelta(minutes=8)), None), (stamp(-timedelta(minutes=7)), 1.6))
    refused(home, tmp_path, "room")


def test_unreadable_spend_log_refuses(home, tmp_path):
    put(home, budget())
    (home / "experiments" / "agent" / "runs.jsonl").write_text("{broken\n")
    refused(home, tmp_path, "runs.jsonl")


def test_agent_runs_through_learning_run_with_explicit_model_and_limits(home, tmp_path):
    put(home, budget(sports=["nba", "nhl"]))
    spend(home, (stamp(-timedelta(days=30)), 9.0))         # old spend outside the weekly period does not count
    r, calls = run(AGENT, home, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == [["agent", "--sport", "nba", "--sport", "nhl", "--max-usd", "1.5", "--model", "claude-opus-5-5",
                      "--max-trials", "3", "--max-minutes", "30"]]
    ev = [json.loads(x) for x in (tmp_path / "data" / "research" / "learning-runs.jsonl").read_text().splitlines()]
    assert ev[0]["name"] == "autoresearch-agent" and ev[0]["event"] == "start"


@pytest.mark.parametrize("cost", [-1.0, float("nan"), float("inf"), "1.0", True])
def test_a_negative_or_non_finite_logged_cost_fails_closed(home, tmp_path, cost):
    put(home, budget())
    (home / "experiments" / "agent" / "runs.jsonl").write_text(
        json.dumps({"event": "started", "run_id": "r0", "at": stamp(-timedelta(minutes=5))}) + "\n" +
        json.dumps({"event": "finished", "run_id": "r0", "at": stamp(-timedelta(minutes=4)), "cost_usd": cost}) + "\n")
    refused(home, tmp_path, "cost_usd")


def test_a_null_cost_counts_at_the_full_per_run_amount(home, tmp_path):
    put(home, budget(max_runs_per_period=4))               # cap 6.0; 2 x null = 3.0 + 1.6 + a full run 1.5 = 6.1
    (home / "experiments" / "agent" / "runs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [
        {"event": "started", "run_id": "a", "at": stamp(-timedelta(minutes=9))},
        {"event": "finished", "run_id": "a", "at": stamp(-timedelta(minutes=8)), "cost_usd": None},
        {"event": "started", "run_id": "b", "at": stamp(-timedelta(minutes=7))},
        {"event": "finished", "run_id": "b", "at": stamp(-timedelta(minutes=6)), "cost_usd": None},
        {"event": "started", "run_id": "c", "at": stamp(-timedelta(minutes=5))},
        {"event": "finished", "run_id": "c", "at": stamp(-timedelta(minutes=4)), "cost_usd": 1.6}]))
    refused(home, tmp_path, "room")


def test_the_guard_wall_limit_leaves_a_margin_over_the_agent_minutes():
    text = AGENT.read_text()
    import re
    agent_min = int(re.search(r'"--max-minutes", "(\d+)"', text).group(1))
    guard_min = int(re.search(r"--name autoresearch-agent --max-minutes (\d+)", text).group(1))
    assert guard_min >= agent_min + 15


def test_daily_period_counts_only_today(home, tmp_path):
    put(home, budget(period="daily", max_runs_per_period=1))
    spend(home, (stamp(-timedelta(days=2)), 1.0))
    r, calls = run(AGENT, home, tmp_path)
    assert r.returncode == 0 and len(calls) == 1


# ---- templates (not installed) ----------------------------------------------------------------------------------------
def template(name):
    return plistlib.loads((ROOT / "scripts" / "launchd" / f"com.nba-predictor.{name}.plist").read_bytes())


def test_daily_template_schedule_and_wrapper():
    p = template("autoresearch-daily")
    assert p["StartCalendarInterval"] == {"Hour": 11, "Minute": 0} and p["Nice"] == 15 and p["ProcessType"] == "Background"
    assert p["ProgramArguments"][-1].endswith("scripts/autoresearch_daily.sh")
    assert p["StandardOutPath"].startswith("/Users/andry/Desktop/nbanhlnfl/data/") and p["RunAtLoad"] is False
    text = (ROOT / "scripts" / "launchd" / "com.nba-predictor.autoresearch-daily.plist").read_text()
    assert "never starts an agent" in text and "never spends" in text and "NOT installed" in text


def test_agent_template_is_commented_as_requiring_the_owner_budget():
    path = ROOT / "scripts" / "launchd" / "com.nba-predictor.autoresearch-agent.plist"
    p = plistlib.loads(path.read_bytes())
    assert p["ProgramArguments"][-1].endswith("scripts/autoresearch_agent_scheduled.sh") and p["RunAtLoad"] is False
    assert "requires owner budget file" in path.read_text() and "NOT installed" in path.read_text()
