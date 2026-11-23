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
MANUAL = ROOT / "scripts" / "autoresearch_agent_manual.sh"
FMT = "%Y-%m-%dT%H:%M:%SZ"
NOW = datetime.now(timezone.utc)


def stamp(delta):
    return (NOW + delta).strftime(FMT)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "hm" / "autoresearch-sports"          # the one home the agent wrappers accept: $HOME/autoresearch-sports
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


def sport_args(calls):
    return [c[c.index("--sport") + 1] for c in calls]


def on(home):
    (home / "SCHEDULE_DAILY_ENABLED").write_text("")


def state(home):
    return {r.split("\t")[0]: r.split("\t") for r in (home / "daily-state.tsv").read_text().splitlines()}


def test_daily_runs_prepare_daily_per_sport_through_learning_run_when_switched_on(home, tmp_path):
    on(home)
    r, calls = run(DAILY, home, tmp_path)
    assert r.returncode == 0, r.stderr
    assert calls == [["daily", "--sport", s, "--max-trials", "3", "--max-minutes", "12", "--timeout", "600"]
                     for s in ("nba", "nhl", "ncaab", "nfl", "soccer")]
    ev = [json.loads(x) for x in (tmp_path / "data" / "research" / "learning-runs.jsonl").read_text().splitlines()]
    assert [e["name"] for e in ev if e["event"] == "start"] == [f"autoresearch-daily-{s}" for s in ("nba", "nhl", "ncaab", "nfl", "soccer")]
    assert all(e["rc"] == 0 for e in ev if e["event"] == "end")
    lines = [x for x in r.stdout.splitlines() if x.startswith("autoresearch-daily: sport=")]
    assert [x.split()[1:3] for x in lines] == [[f"sport={s}", "outcome=evaluated"] for s in ("nba", "nhl", "ncaab", "nfl", "soccer")]
    st = state(home)
    assert set(st) == {"nba", "nhl", "ncaab", "nfl", "soccer"} and st["soccer"][2] == "evaluated" and st["soccer"][3] == st["soccer"][1]


def test_a_failed_refused_or_skipped_sport_does_not_stop_the_others_and_is_reported(tmp_path, home, monkeypatch):
    on(home)
    (home / "workspace" / "prepare.py").write_text(
        "import json, sys, os\nopen(os.environ['FAKE_OUT'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "s = sys.argv[sys.argv.index('--sport') + 1]\n"
        "if s == 'nhl': sys.exit(3)\n"
        "if s == 'nfl': print('sport-outcome: nfl no_candidates v3=no_candidates (nothing kept)')\n"
        "if s == 'ncaab': print('sport-outcome: ncaab evaluated v3=evaluated')\n")
    data = tmp_path / "data"
    data.mkdir()
    import sqlite3
    c = sqlite3.connect(data / "soccer.sqlite")
    c.execute("CREATE TABLE soccer_matches (match_id TEXT PRIMARY KEY, kickoff_at TEXT, status TEXT)")
    c.execute("INSERT INTO soccer_matches VALUES ('m', ?, 'scheduled')", ((datetime.now(timezone.utc) + timedelta(minutes=40)).strftime(FMT),))
    c.commit()
    c.close()
    r, calls = run(DAILY, home, tmp_path)
    out = {x.split()[1].split("=")[1]: x.split()[2].split("=")[1] for x in r.stdout.splitlines() if x.startswith("autoresearch-daily: sport=")}
    assert out == {"nba": "evaluated", "nhl": "failed", "ncaab": "evaluated", "nfl": "no_candidates", "soccer": "refused"}
    assert sport_args(calls) == ["nba", "nhl", "ncaab", "nfl"]               # soccer's own 0.75 h window refused it; the others ran
    assert r.returncode == 1 and "failed=1" in r.stdout
    assert state(home)["nhl"][2] == "failed" and state(home)["nhl"][3] == ""   # no success recorded for a failure
    assert state(home)["nfl"][2] == "no_candidates" and state(home)["nfl"][3] == ""   # no_candidates is not a success
    assert state(home)["ncaab"][2] == "evaluated" and state(home)["ncaab"][3] == state(home)["ncaab"][1]
    assert "scored=2 no_candidates=1" in r.stdout
    assert state(home)["soccer"][2] == "refused"


def test_the_sport_that_has_gone_longest_without_a_success_runs_first_and_other_rows_survive(tmp_path, home):
    on(home)
    (home / "daily-state.tsv").write_text(
        "nba\t2026-11-20T11:00:00Z\tevaluated\t2026-11-20T11:00:00Z\t-\n"
        "nhl\t2026-11-20T11:00:00Z\tevaluated\t2026-11-20T11:00:00Z\t-\n"
        "soccer\t2026-11-20T11:00:00Z\trefused\t2026-11-10T11:00:00Z\t-\n"
        "extra\t2026-11-01T00:00:00Z\tevaluated\t2026-11-01T00:00:00Z\t-\n")
    env_sports = "nba nhl soccer ncaab"
    os.environ["AUTORESEARCH_SPORTS"] = env_sports
    try:
        r, calls = run(DAILY, home, tmp_path)
    finally:
        del os.environ["AUTORESEARCH_SPORTS"]
    assert sport_args(calls) == ["ncaab", "soccer", "nba", "nhl"]            # never succeeded, then oldest success, then ties in list order
    st = state(home)
    assert set(st) == {"nba", "nhl", "soccer", "ncaab", "extra"}


def test_the_total_wall_cap_skips_the_sports_that_no_longer_fit(tmp_path, home):
    on(home)
    (home / "workspace" / "prepare.py").write_text(
        "import json, sys, os, time\nopen(os.environ['FAKE_OUT'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\ntime.sleep(2)\n")
    os.environ.update(AUTORESEARCH_TOTAL_SECONDS="8", AUTORESEARCH_MIN_START_SECONDS="5")
    try:
        r, calls = run(DAILY, home, tmp_path)
    finally:
        del os.environ["AUTORESEARCH_TOTAL_SECONDS"], os.environ["AUTORESEARCH_MIN_START_SECONDS"]
    assert 1 <= len(calls) < 5 and r.returncode == 0
    assert "outcome=skipped" in r.stdout and "total wall cap" in r.stdout


def test_every_sport_refused_exits_75(tmp_path, home):
    on(home)
    import fcntl
    (tmp_path / "data").mkdir()
    fh = open(tmp_path / "data" / ".learning-heavy.lock", "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r, calls = run(DAILY, home, tmp_path)
    finally:
        fh.close()
    assert r.returncode == 75 and calls == [] and r.stdout.count("outcome=refused") == 5


# ---- same-day retries -------------------------------------------------------------------------------------------------
LOCAL_TODAY = datetime.now().astimezone().strftime("%Y-%m-%d")
LOCAL_YESTERDAY = (datetime.now().astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")


def seed(home, sport, outcome, reason="-", n=1, day=None, at=None):
    at = at or stamp(timedelta(minutes=-30))
    ok = at if outcome == "evaluated" else ""
    row = f"{sport}\t{at}\t{outcome}\t{ok}\t{reason}\t{day or LOCAL_TODAY}:{n}\n"
    with open(home / "daily-state.tsv", "a") as f:
        f.write(row)
    return row


def daily(home, tmp_path, monkeypatch, sports):
    on(home)
    monkeypatch.setenv("AUTORESEARCH_SPORTS", sports)
    return run(DAILY, home, tmp_path)


def test_a_sport_refused_earlier_today_is_retried_and_its_counter_increments(home, tmp_path, monkeypatch):
    seed(home, "nba", "refused", "guard refused (x)", n=1)
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert sport_args(calls) == ["nba"] and r.returncode == 0, r.stdout + r.stderr
    row = state(home)["nba"]
    assert row[2] == "evaluated" and row[5] == f"{LOCAL_TODAY}:2" and len(row) == 6


def test_a_sport_evaluated_earlier_today_is_done_today_not_run_and_keeps_its_row(home, tmp_path, monkeypatch):
    row = seed(home, "nba", "evaluated", n=1)
    r, calls = daily(home, tmp_path, monkeypatch, "nba nhl")
    assert sport_args(calls) == ["nhl"]
    assert "sport=nba outcome=done_today reason=evaluated at " + row.split("\t")[1] in r.stdout
    assert [x for x in (home / "daily-state.tsv").read_text().splitlines(keepends=True) if x.startswith("nba\t")] == [row]
    assert r.returncode == 0 and "done_today=1" in r.stdout


def test_a_sport_that_failed_earlier_today_is_not_retried(home, tmp_path, monkeypatch):
    row = seed(home, "nhl", "failed", "rc 3", n=1)
    r, calls = daily(home, tmp_path, monkeypatch, "nhl")
    assert calls == [] and "sport=nhl outcome=done_today reason=failed" in r.stdout
    assert (home / "daily-state.tsv").read_text() == row and r.returncode == 0 and "failed=0" in r.stdout


def test_the_attempt_cap_of_three_stops_a_fourth_attempt(home, tmp_path, monkeypatch):
    row = seed(home, "nba", "refused", "guard refused (x)", n=3)
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert calls == [] and 'sport=nba outcome=done_today reason=attempt cap (3) reached today' in r.stdout
    assert (home / "daily-state.tsv").read_text() == row and r.returncode == 0


def test_a_row_from_yesterday_runs_normally_and_the_counter_resets(home, tmp_path, monkeypatch):
    seed(home, "nba", "evaluated", n=3, day=LOCAL_YESTERDAY, at=stamp(timedelta(days=-2)))
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert sport_args(calls) == ["nba"]
    assert state(home)["nba"][5] == f"{LOCAL_TODAY}:1"


def test_a_wall_cap_skip_earlier_today_is_retried(home, tmp_path, monkeypatch):
    seed(home, "soccer", "skipped", "total wall cap reached (100s left)", n=1)
    seed(home, "nfl", "skipped", "prepare.py refused to run this sport", n=1)
    r, calls = daily(home, tmp_path, monkeypatch, "soccer nfl")
    assert sport_args(calls) == ["soccer"] and "sport=nfl outcome=done_today reason=skipped" in r.stdout
    assert state(home)["soccer"][2] == "evaluated" and state(home)["soccer"][5] == f"{LOCAL_TODAY}:2"


def test_when_every_sport_is_done_today_the_run_exits_0_without_running_anything(home, tmp_path, monkeypatch):
    seed(home, "nba", "evaluated")
    seed(home, "nhl", "no_candidates")
    seed(home, "nfl", "failed", "rc 3")
    before = (home / "daily-state.tsv").read_text()
    r, calls = daily(home, tmp_path, monkeypatch, "nba nhl nfl")
    assert r.returncode == 0 and calls == [] and r.stdout.count("outcome=done_today") == 3
    assert "done_today=3" in r.stdout and "refused=0" in r.stdout and (home / "daily-state.tsv").read_text() == before


def test_daily_never_names_the_agent_command():
    text = DAILY.read_text()
    assert "prepare.py agent" not in text and '"$PREPARE" agent' not in text and "--max-usd" not in text


# ---- generation (paid) -----------------------------------------------------------------------------------------------
# A fixed Tuesday 12:30Z so the night-window gate does not depend on the wall clock.
TUE = datetime(2026, 11, 17, 12, 30, tzinfo=timezone.utc)
CLOCK = TUE.strftime(FMT)
MODEL = "claude-opus-5-5"


def at(**delta):
    return (TUE + timedelta(**delta)).strftime(FMT)


def gate_sha():
    import hashlib
    files = [ROOT / "backend/app/autoresearch_budget.py", AGENT, MANUAL]
    return hashlib.sha256(b"".join(f.read_bytes() for f in files)).hexdigest()


def budget(**over):
    b = {"approved_by": "owner", "approved_at": at(days=-3), "period": "weekly", "period_usd_cap": 5.0, "max_runs_per_period": 2,
         "max_usd_per_run": 2.5, "max_total_runs": 4, "counting_from": at(days=-3), "model": MODEL, "max_trials": 3,
         "sports": ["nba"], "expires_at": at(days=30), "gate_sha256": gate_sha()}
    b.update(over)
    return b


def put(home, b):
    (home / "AGENT_BUDGET.json").write_text(json.dumps(b))


AGENT_DIR = lambda home: home / "experiments" / "agent"  # noqa: E731


def add_run(home, rid, started, cost="absent", finished=True, attempts=(), model=MODEL, models=None, trials=1, report=True):
    """One run in runs.jsonl (+ report.json and agent-N.jsonl transcripts holding `attempts` result costs).
    cost: recorded cost_usd of the finished event (None = unreported)."""
    d = AGENT_DIR(home)
    rdir = d / "runs" / rid
    rdir.mkdir(parents=True, exist_ok=True)
    ev = [{"event": "started", "run_id": rid, "at": started, "model": model}]
    if finished:
        ev.append({"event": "finished", "run_id": rid, "at": started, "cost_usd": None if cost == "absent" else cost, "trials": trials,
                   "outcome": "no improvement found", "report": f"agent/runs/{rid}/report.json"})
        if report:
            (rdir / "report.json").write_text(json.dumps({"run_id": rid, "trials": [{"markets": {"ml": {"decision": "discard"}}}]}))
    with (d / "runs.jsonl").open("a") as fh:
        fh.writelines(json.dumps(x) + "\n" for x in ev)
    for i, c in enumerate(attempts):
        recs = [{"type": "system", "subtype": "init", "model": (models or [model])[0]}, {"type": "result", "total_cost_usd": c}]
        (rdir / f"agent-{i + 1}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in recs))


WRAPPER = """#!{py}
import os, sys
from datetime import datetime, timezone
sys.path.insert(0, {backend!r})
if sys.argv[1:3] == ["-m", "app.autoresearch_budget"]:
    from app import autoresearch_budget as ab
    ab.now_utc = lambda: datetime.strptime(os.environ["FAKE_NOW"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    ab.trainer_running = lambda: os.environ["FAKE_TRAINER"] == "running"
    sys.exit(ab.main(sys.argv[3:]))
os.execv(sys.executable, [sys.executable] + sys.argv[1:])
"""


def gate_run(home, tmp_path, trainer="idle", now=CLOCK, script=AGENT, **extra):
    """Production code has no clock or trainer hooks: this test launcher (LEARNING_PYTHON) patches them in-process."""
    wrapper = tmp_path / "pywrap"
    wrapper.write_text(WRAPPER.format(py=sys.executable, backend=str(ROOT / "backend")))
    wrapper.chmod(0o755)
    return run_env(script, home, tmp_path, FAKE_NOW=now, FAKE_TRAINER=trainer, LEARNING_PYTHON=str(wrapper), **extra)


def run_env(script, home, tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("AUTORESEARCH_HOME", "AUTORESEARCH_PREPARE")}
    env.update(HOME=str(home.parent), AUTORESEARCH_PYTHON=sys.executable, LEARNING_PYTHON=sys.executable, LEARNING_BACKEND=str(ROOT / "backend"),
               NBA_DATA_DIR=str(tmp_path / "data"), FAKE_OUT=str(tmp_path / "calls.jsonl"), PYTHONDONTWRITEBYTECODE="1")
    env.update(extra)
    r = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=120)
    calls = tmp_path / "calls.jsonl"
    return r, [json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []


def refused(home, tmp_path, fragment, **kw):
    r, calls = gate_run(home, tmp_path, **kw)
    assert r.returncode == 78 and calls == [], (r.returncode, r.stdout, r.stderr)
    assert fragment in r.stdout + r.stderr, r.stdout + r.stderr


def test_agent_refuses_without_a_budget_file(home, tmp_path):
    refused(home, tmp_path, "AGENT_BUDGET.json")


@pytest.mark.parametrize("over,fragment", [
    ({"approved_by": ""}, "approved_by"), ({"approved_at": "yesterday"}, "approved_at"),
    ({"approved_at": at(days=1)}, "approved_at"), ({"period": "daily"}, "period"),
    ({"max_usd_per_run": 0}, "max_usd_per_run"), ({"max_usd_per_run": True}, "max_usd_per_run"), ({"max_usd_per_run": 6}, "exceed"),
    ({"period_usd_cap": 0}, "period_usd_cap"), ({"period_usd_cap": "5"}, "period_usd_cap"),
    ({"max_runs_per_period": 0}, "max_runs_per_period"), ({"max_total_runs": 0}, "max_total_runs"),
    ({"counting_from": "soon"}, "counting_from"), ({"model": ""}, "model"), ({"model": "latest opus"}, "model"),
    ({"max_trials": 0}, "max_trials"), ({"sports": []}, "sports"), ({"sports": ["soccer"]}, "sports"),
    ({"expires_at": at(hours=-1)}, "expired"), ({"expires_at": "soon"}, "expires_at"),
])
def test_agent_refuses_an_invalid_budget(home, tmp_path, over, fragment):
    put(home, budget(**over))
    refused(home, tmp_path, fragment)


@pytest.mark.parametrize("field", ["model", "period_usd_cap", "max_total_runs", "counting_from"])
def test_agent_refuses_a_budget_missing_a_field_or_not_json(home, tmp_path, field):
    b = budget()
    del b[field]
    put(home, b)
    refused(home, tmp_path, field)
    (home / "AGENT_BUDGET.json").write_text("{not json")
    refused(home, tmp_path, "AGENT_BUDGET.json")


def test_agent_runs_through_learning_run_with_explicit_model_and_limits_then_records(home, tmp_path):
    put(home, budget(sports=["nba", "nhl"]))
    r, calls = gate_run(home, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == [["agent", "--sport", "nba", "--sport", "nhl", "--max-usd", "2.5", "--model", MODEL, "--max-trials", "3", "--max-minutes", "30"]]
    ev = [json.loads(x) for x in (tmp_path / "data" / "research" / "learning-runs.jsonl").read_text().splitlines()]
    assert ev[0]["name"] == "autoresearch-agent" and ev[0]["event"] == "start"


def test_a_week_with_two_runs_refuses_a_third(home, tmp_path):
    put(home, budget())
    add_run(home, "a", at(hours=-30), 0.1)
    add_run(home, "b", at(hours=-2), 0.1)
    refused(home, tmp_path, "max_runs_per_period")


def test_spend_without_room_for_a_full_run_refuses(home, tmp_path):
    put(home, budget())                                    # 2.6 spent + 2.5 > 5.0
    add_run(home, "a", at(hours=-2), 2.6)
    refused(home, tmp_path, "no room")


def test_retry_cost_in_the_recorded_total_counts(home, tmp_path):
    put(home, budget(max_usd_per_run=2.5))                 # research_agent sums every attempt into cost_usd: 1.3 + 1.3 = 2.6
    add_run(home, "a", at(hours=-2), 2.6, attempts=(1.3, 1.3))
    refused(home, tmp_path, "no room")


def test_retry_cost_is_taken_from_transcripts_when_the_record_undercounts(home, tmp_path):
    put(home, budget())                                    # record says 0.5 but transcripts show 1.2 + 1.4 = 2.6
    add_run(home, "a", at(hours=-2), 0.5, attempts=(1.2, 1.4))
    refused(home, tmp_path, "no room")


def test_a_null_cost_counts_at_max_usd_per_run(home, tmp_path):
    put(home, budget(max_runs_per_period=3, max_usd_per_run=2.0, period_usd_cap=5.0))
    add_run(home, "a", at(hours=-3), None)                 # counts 2.0, not 0
    add_run(home, "b", at(hours=-2), 1.1)                  # 3.1 + 2.0 > 5.0 (would fit at 0)
    refused(home, tmp_path, "no room")


def test_a_started_run_without_a_finish_counts_at_max(home, tmp_path):
    put(home, budget(max_runs_per_period=3))
    add_run(home, "a", at(hours=-3), finished=False)
    add_run(home, "b", at(hours=-2), 0.1)
    refused(home, tmp_path, "no room")                     # 2.5 + 0.1 + 2.5 > 5.0


def test_unreadable_spend_log_or_report_refuses(home, tmp_path):
    put(home, budget())
    (AGENT_DIR(home) / "runs.jsonl").write_text("{broken\n")
    refused(home, tmp_path, "cost tracking failed")
    (AGENT_DIR(home) / "runs.jsonl").unlink()
    add_run(home, "a", at(hours=-2), 0.1, report=False)
    refused(home, tmp_path, "cost tracking failed")


@pytest.mark.parametrize("cost", [-1.0, float("nan"), float("inf"), "1.0", True])
def test_a_negative_or_non_finite_logged_cost_fails_closed(home, tmp_path, cost):
    put(home, budget())
    add_run(home, "a", at(hours=-2), cost)
    refused(home, tmp_path, "cost_usd")


def test_manual_and_scheduled_share_the_counter(home, tmp_path):
    put(home, budget(max_runs_per_period=1))
    r, calls = gate_run(home, tmp_path, script=MANUAL)
    assert r.returncode == 0 and len(calls) == 1
    (tmp_path / "calls.jsonl").unlink()
    add_run(home, "m1", at(hours=-1), 0.2)                 # that manual run is now in runs.jsonl
    refused(home, tmp_path, "max_runs_per_period", script=AGENT)
    refused(home, tmp_path, "max_runs_per_period", script=MANUAL)


def test_four_runs_stop_for_an_owner_assessment(home, tmp_path):
    put(home, budget(max_runs_per_period=9, period_usd_cap=100.0, max_total_runs=4))
    for i in range(4):
        add_run(home, f"r{i}", at(days=-2, hours=i), 0.1)
    refused(home, tmp_path, "four-run assessment required")


def test_counting_from_excludes_earlier_runs(home, tmp_path):
    put(home, budget(max_runs_per_period=1, counting_from=at(hours=-1)))
    add_run(home, "old", at(hours=-5), 4.9)                # the supervised run before the authorization
    r, calls = gate_run(home, tmp_path)
    assert r.returncode == 0 and len(calls) == 1, r.stdout


def test_last_week_does_not_count_toward_the_week_but_counts_toward_the_total(home, tmp_path):
    put(home, budget(counting_from=at(days=-10)))
    add_run(home, "w0", at(days=-8), 4.9)                  # Tuesday 11-17 is in the ISO week starting Mon 11-16; -8 days is the week before
    r, _ = gate_run(home, tmp_path)
    assert r.returncode == 0, r.stdout


def test_just_before_the_night_window_runs(home, tmp_path):
    put(home, budget())
    r, calls = gate_run(home, tmp_path, now="2026-11-17T20:44:00Z")
    assert r.returncode == 0 and len(calls) == 1, r.stdout


def test_trainer_running_refuses(home, tmp_path):
    put(home, budget())
    refused(home, tmp_path, "adaptive trainer", trainer="running")


@pytest.mark.parametrize("now", ["2026-11-17T20:45:00Z", "2026-11-17T21:30:00Z", "2026-11-17T23:59:00Z", "2026-11-18T06:59:00Z"])
def test_night_window_refuses(home, tmp_path, now):
    put(home, budget(approved_at="2026-11-10T00:00:00Z", counting_from="2026-11-10T00:00:00Z", expires_at="2026-12-31T00:00:00Z"))
    refused(home, tmp_path, "20:45Z", now=now)


def test_record_ledgers_a_mismatch_and_writes_the_marker(home):
    from app import autoresearch_budget as ab
    put(home, budget(max_runs_per_period=3))
    add_run(home, "a", at(hours=-2), 0.2, attempts=(0.2,), models=["claude-haiku-5"])
    rows = ab.record(home, TUE)
    ledger = [json.loads(x) for x in (AGENT_DIR(home) / "budget-ledger.jsonl").read_text().splitlines()]
    assert rows and ledger[0]["run_id"] == "a" and ledger[0]["model_requested"] == MODEL and ledger[0]["model_used"] == ["claude-haiku-5"]
    assert (AGENT_DIR(home) / "MODEL_MISMATCH_STOP").exists()


def test_a_marker_blocks_further_runs(home, tmp_path):
    put(home, budget())
    (AGENT_DIR(home) / "MODEL_MISMATCH_STOP").write_text("x")
    refused(home, tmp_path, "MODEL_MISMATCH_STOP")


def transcript(home, rid, *events):
    rdir = AGENT_DIR(home) / "runs" / rid
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "agent-1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in events))


INIT = {"type": "system", "subtype": "init", "model": MODEL}


def test_the_gate_itself_catches_a_mismatch_even_if_record_never_ran(home, tmp_path):
    put(home, budget(max_runs_per_period=3))
    add_run(home, "a", at(hours=-2), finished=False)                 # unfinished run: only the transcript says what ran
    transcript(home, "a", INIT, {"type": "assistant", "message": {"model": "claude-haiku-5"}})
    refused(home, tmp_path, "claude-haiku-5")
    assert (AGENT_DIR(home) / "MODEL_MISMATCH_STOP").exists()


def test_a_helper_model_does_not_stop_but_a_different_main_model_does(home, tmp_path):
    put(home, budget(max_runs_per_period=3))
    add_run(home, "a", at(hours=-2), 0.2)
    transcript(home, "a", INIT, {"type": "assistant", "message": {"model": MODEL}},
               {"type": "assistant", "parent_tool_use_id": "t1", "message": {"model": "claude-haiku-5"}},
               {"type": "result", "total_cost_usd": 0.2, "modelUsage": {MODEL: {}, "claude-haiku-5": {}}})
    r, _ = gate_run(home, tmp_path)
    assert r.returncode == 0 and not (AGENT_DIR(home) / "MODEL_MISMATCH_STOP").exists(), r.stdout
    (tmp_path / "calls.jsonl").unlink()
    transcript(home, "a", INIT, {"type": "assistant", "message": {"model": "claude-sonnet-5-5"}})
    refused(home, tmp_path, "claude-sonnet-5-5")


def test_gate_sha256_must_match_the_gate_files(home, tmp_path):
    put(home, budget(gate_sha256="0" * 64))
    refused(home, tmp_path, "gate_sha256")
    b = budget()
    del b["gate_sha256"]
    put(home, b)
    refused(home, tmp_path, "gate_sha256")
    put(home, budget(gate_sha256="xyz"))
    refused(home, tmp_path, "gate_sha256")


def test_a_different_home_or_prepare_than_the_gate_uses_is_refused(home, tmp_path):
    put(home, budget())
    refused(home, tmp_path, "AUTORESEARCH_HOME", AUTORESEARCH_HOME=str(tmp_path / "elsewhere"))
    refused(home, tmp_path, "AUTORESEARCH_PREPARE", AUTORESEARCH_PREPARE=str(tmp_path / "other_prepare.py"))


def test_matching_model_is_ledgered_without_a_marker_and_only_once(home, tmp_path):
    put(home, budget(max_runs_per_period=3))
    add_run(home, "a", at(hours=-2), 0.2, attempts=(0.2,), trials=2)
    gate_run(home, tmp_path)
    gate_run(home, tmp_path)
    ledger = [json.loads(x) for x in (AGENT_DIR(home) / "budget-ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 1 and ledger[0]["model_used"] == [MODEL] and ledger[0]["trials"] == 2 and ledger[0]["cost_usd"] == 0.2
    assert not (AGENT_DIR(home) / "MODEL_MISMATCH_STOP").exists()


def test_the_guard_wall_limit_leaves_a_margin_over_the_agent_minutes():
    import re
    agent_min = int(re.search(r'"--max-minutes", "(\d+)"', (ROOT / "backend/app/autoresearch_budget.py").read_text()).group(1))
    guard_min = int(re.search(r"--name autoresearch-agent --max-minutes (\d+)", AGENT.read_text()).group(1))
    assert guard_min >= agent_min + 15


def test_example_budget_file_validates():
    from app import autoresearch_budget as ab
    b = ab.load_budget(ROOT / "scripts" / "autoresearch_budget.example.json", datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert (b["period_usd_cap"], b["max_runs_per_period"], b["max_usd_per_run"], b["max_total_runs"]) == (5.0, 2, 2.5, 4)
    assert b["gate_sha256"] == gate_sha(), "example gate_sha256 is stale: recompute it after changing the gate files"


def test_manual_script_delegates_to_the_same_gate_and_never_names_prepare_agent():
    text = MANUAL.read_text()
    assert "autoresearch_agent_scheduled.sh" in text and '"$PREPARE"' not in text and "bypasses the budget" in text


# ---- templates (not installed) ----------------------------------------------------------------------------------------
def template(name):
    return plistlib.loads((ROOT / "scripts" / "launchd" / f"com.nba-predictor.{name}.plist").read_bytes())


def test_daily_template_schedule_and_wrapper():
    p = template("autoresearch-daily")
    assert p["StartCalendarInterval"] == [{"Hour": 6, "Minute": 0}, {"Hour": 11, "Minute": 10}, {"Hour": 13, "Minute": 10}]
    assert p["Nice"] == 15 and p["ProcessType"] == "Background"
    assert p["ProgramArguments"][-1].endswith("scripts/autoresearch_daily.sh")
    assert p["StandardOutPath"].startswith("/Users/andry/Desktop/nbanhlnfl/data/") and p["RunAtLoad"] is False
    assert p["ProgramArguments"] == ["/bin/bash", "/Users/andry/nba-predictor-delivery/scripts/autoresearch_daily.sh"]
    from pathlib import Path as _P
    script = (_P(__file__).resolve().parents[2] / "scripts" / "autoresearch_daily.sh").read_text()
    assert '--global-lookback-hours "${AUTORESEARCH_GLOBAL_LOOKBACK_HOURS:-4}"' in script   # no research while any game is on
    assert p["WorkingDirectory"] == "/Users/andry/nba-predictor-delivery/backend"
    assert p["EnvironmentVariables"]["LEARNING_PYTHON"] == "/Users/andry/nba-predictor-live/backend/.venv/bin/python"
    text = (ROOT / "scripts" / "launchd" / "com.nba-predictor.autoresearch-daily.plist").read_text()
    assert "never starts an agent" in text and "never spends" in text and "NOT installed" in text


def test_agent_template_is_commented_as_requiring_the_owner_budget():
    path = ROOT / "scripts" / "launchd" / "com.nba-predictor.autoresearch-agent.plist"
    p = plistlib.loads(path.read_bytes())
    assert p["ProgramArguments"][-1].endswith("scripts/autoresearch_agent_scheduled.sh") and p["RunAtLoad"] is False
    assert all("/nba-predictor-delivery/" in a or a == "/bin/bash" for a in p["ProgramArguments"])
    assert p["WorkingDirectory"] == "/Users/andry/nba-predictor-delivery/backend"
    assert [(d["Weekday"], d["Hour"], d["Minute"]) for d in p["StartCalendarInterval"]] == [(2, 12, 30), (5, 12, 30)]
    assert "requires owner budget file" in path.read_text() and "NOT installed" in path.read_text()


def test_the_wrapper_parses_the_real_daily_cli_output_of_app_autoresearch(home, tmp_path):
    """The child runs the REAL app.autoresearch._daily_cli (only the two protocol runners are stubbed), so the printed
    `sport-outcome: <sport> <combined> v3=.. [v2=..]` lines are the real format, including nba's v2 part."""
    on(home)
    (home / "workspace" / "prepare.py").write_text(f"""
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "backend")!r})
from app import autoresearch as ar
s = sys.argv[sys.argv.index('--sport') + 1]
V3 = {{'nba': 'evaluated', 'nhl': 'no_candidates', 'ncaab': 'no_candidates', 'nfl': 'failed', 'soccer': 'refused'}}
ar.daily_v3 = lambda chosen, root, items, secs, **kw: {{'sports': {{c: {{'outcome': V3[c], 'reason': 'why ' + c}} for c in chosen}},
                                                 'report_file': os.environ['FAKE_REPORT']}}
ar.daily = lambda *a, **k: {{'trials': [], 'refused': {{}}}}
ns = argparse.Namespace(sport=[s], exp_root=Path(os.environ['FAKE_REPORT']).parent, workspace=Path('.'), max_trials=ar.BUDGETS['max_trials'],
                        max_minutes=ar.BUDGETS['max_minutes'], timeout=ar.BUDGETS['trial_timeout_s'])
sys.exit(ar._daily_cli(ns))
""")
    os.environ["FAKE_REPORT"] = str(tmp_path / "report.json")
    try:
        r, _ = run(DAILY, home, tmp_path)
    finally:
        del os.environ["FAKE_REPORT"]
    out = {x.split()[1].split("=")[1]: x.split()[2].split("=")[1] for x in r.stdout.splitlines() if x.startswith("autoresearch-daily: sport=")}
    assert "sport-outcome: nba evaluated v3=evaluated v2=no_candidates" in r.stdout          # the real line, field 3 is the combined outcome
    assert out == {"nba": "evaluated", "nhl": "no_candidates", "ncaab": "no_candidates", "nfl": "failed", "soccer": "refused"}, r.stdout + r.stderr
    st = state(home)
    assert st["nba"][2] == "evaluated" and st["nba"][3] == st["nba"][1]                       # success only for evaluated
    assert st["nhl"][2] == "no_candidates" and st["nhl"][3] == "" and st["ncaab"][3] == ""
    assert st["nfl"][2] == "failed" and st["soccer"][2] == "refused" and st["soccer"][3] == ""
    assert "scored=1 no_candidates=2 refused=1 failed=1" in r.stdout and r.returncode == 1


def test_the_wrapper_keeps_a_reason_with_nested_parentheses_whole():
    """Review round 2: the reason extraction must start at the FIRST '(' (reasons contain '(no improvement found)')."""
    line = next(x for x in DAILY.read_text().splitlines() if "reason=$(sed -n" in x and "sport-outcome" in x)
    cmd = line.split("reason=$(", 1)[1].rsplit("| tail -1)", 1)[0].replace('"$tmp"', "")
    out = subprocess.run(["bash", "-c", f"s=ncaab; printf '%s\\n' 'sport-outcome: ncaab evaluated v3=evaluated "
                          f"(3 of 3 scored; no candidate kept (no improvement found))' | {cmd}"], capture_output=True, text=True)
    assert out.stdout.strip() == "3 of 3 scored; no candidate kept (no improvement found)"
