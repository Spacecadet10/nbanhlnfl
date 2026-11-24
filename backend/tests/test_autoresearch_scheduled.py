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
    fake.write_text("import json, sys, os\nopen(os.environ['FAKE_OUT'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    "if 'daily' in sys.argv: print('sport-outcome: ' + sys.argv[sys.argv.index('--sport') + 1] + ' evaluated v3=evaluated')\n")
    return h


def run(script, home, tmp_path, rc_expected=None):
    env = {"AUTORESEARCH_STOP_AT": "", **os.environ}
    env.update(AUTORESEARCH_HOME=str(home), AUTORESEARCH_PREPARE=str(home / "workspace" / "prepare.py"),
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
    assert calls == [["daily", "--sport", s, "--max-trials", "100000", "--max-minutes", "15", "--timeout", "600"]
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
        "if s in ('nba', 'ncaab'): print('sport-outcome: ' + s + ' evaluated v3=evaluated')\n")
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
        "import json, sys, os, time\nopen(os.environ['FAKE_OUT'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\ntime.sleep(2)\n"
        "print('sport-outcome: ' + sys.argv[sys.argv.index('--sport') + 1] + ' evaluated v3=evaluated')\n")
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
    at = at or stamp(timedelta(0))        # always today: 30 minutes ago is yesterday just after local midnight
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


def test_a_refusal_stated_by_the_evaluator_keeps_its_own_reason_and_is_retried_the_same_day(home, tmp_path, monkeypatch):
    (home / "workspace" / "prepare.py").write_text(
        "import json, sys, os\nopen(os.environ['FAKE_OUT'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print('sport-outcome: nba refused v3=refused v2=evaluated (the nba study tables are not cached (x.pkl): build them)')\n")
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    row = state(home)["nba"]
    assert row[2] == "refused" and row[3] == "" and "study tables are not cached" in row[4] and "guard" not in row[4], row
    assert "refused=1" in r.stdout and r.returncode == 75
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert len(calls) == 2 and state(home)["nba"][5] == f"{LOCAL_TODAY}:2"


@pytest.mark.parametrize("line,outcome", [
    ("evaluated v3=refused v2=evaluated (tables not cached)", "refused"), ("evaluated v3=failed v2=evaluated (boom)", "failed"),
    ("evaluated v3=skipped v2=evaluated (no model)", "skipped"), ("evaluated v3=no_candidates v2=evaluated", "evaluated"),
    ("refused v3=no_candidates v2=refused (x)", "refused"), ("no_candidates v3=no_candidates", "no_candidates"),
    ("evaluated v3=evaluated v2=failed (y)", "evaluated")])
def test_the_wrapper_reads_v3s_own_outcome_whatever_the_pinned_evaluator_combined(home, tmp_path, monkeypatch, line, outcome):
    """The pinned evaluator (23df530) still prints `evaluated` when only v2 ran: the wrapper must not repeat it (Nov 23 06:00)."""
    (home / "workspace" / "prepare.py").write_text(f"print('sport-outcome: nba {line}')\n")
    r, _ = daily(home, tmp_path, monkeypatch, "nba")
    row = state(home)["nba"]
    assert row[2] == outcome, r.stdout
    assert bool(row[3]) == (outcome == "evaluated")                 # a hidden refusal never counts as a success


def test_the_wrapper_uses_the_pinned_runner_and_refuses_a_python_override_for_the_real_home(tmp_path):
    text = DAILY.read_text()
    assert '-I "$HERE/research_runner.py"' in text and '"${RUN[@]}" "$PREPARE" daily' in text
    real = tmp_path / "autoresearch-sports"                         # $HOME/autoresearch-sports, without AUTORESEARCH_HOME
    real.mkdir()
    (real / "SCHEDULE_DAILY_ENABLED").write_text("")
    env = {k: v for k, v in os.environ.items() if k != "AUTORESEARCH_HOME"} | {"HOME": str(tmp_path), "AUTORESEARCH_PYTHON": sys.executable,
                                                                             "LEARNING_PYTHON": sys.executable}
    r = subprocess.run(["/bin/bash", str(DAILY)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 78 and "AUTORESEARCH_PYTHON" in r.stdout


@pytest.mark.parametrize("how", ["trailing slash", "symlink", "other home, real prepare"])
def test_no_spelling_of_the_real_home_lets_another_python_in(tmp_path, how):
    real = tmp_path / "autoresearch-sports"
    (real / "workspace").mkdir(parents=True)
    (real / "workspace" / "prepare.py").write_text("print('RAN')\n")
    other = tmp_path / "other"
    (other / "workspace").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    for h in (real, other):
        (h / "SCHEDULE_DAILY_ENABLED").write_text("")
    env = dict(os.environ, HOME=str(tmp_path), AUTORESEARCH_PYTHON=sys.executable, LEARNING_PYTHON=sys.executable,
               AUTORESEARCH_HOME={"trailing slash": f"{real}/", "symlink": str(tmp_path / "link")}.get(how, str(other)))
    if how == "other home, real prepare":
        env["AUTORESEARCH_PREPARE"] = str(real / "workspace" / "prepare.py")
    r = subprocess.run(["/bin/bash", str(DAILY)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 78 and "RAN" not in r.stdout, r.stdout


def test_rc_0_without_a_sport_outcome_line_is_not_a_result(home, tmp_path, monkeypatch):
    (home / "workspace" / "prepare.py").write_text("print('nothing said')\n")
    daily(home, tmp_path, monkeypatch, "nba")
    row = state(home)["nba"]
    assert row[2] == "failed" and row[3] == "" and "no sport-outcome line" in row[4]


def test_the_agent_template_goes_through_the_pinned_runner():
    d = plistlib.loads((ROOT / "scripts" / "launchd" / "com.nba-predictor.autoresearch-agent.plist").read_bytes())
    assert d["EnvironmentVariables"]["AUTORESEARCH_PYTHON"].endswith("/scripts/research_runner.py")
    assert os.access(ROOT / "scripts" / "research_runner.py", os.X_OK)


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


def test_there_is_no_attempt_cap_a_sport_refused_five_times_today_is_retried(home, tmp_path, monkeypatch):
    seed(home, "nba", "refused", "guard refused (x)", n=5)
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert sport_args(calls) == ["nba"] and "attempt cap" not in r.stdout and r.returncode == 0
    assert state(home)["nba"][5] == f"{LOCAL_TODAY}:6"                # column 6 only counts the guarded chunks started today


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
    env.update(AUTORESEARCH_STOP_AT="")
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
    assert p["StartCalendarInterval"] == [{"Hour": 4, "Minute": 0}, {"Hour": 5, "Minute": 0}, {"Hour": 6, "Minute": 0}, {"Hour": 7, "Minute": 30}]
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
    assert "11:10" not in text and "13:10" not in text and "attempts per sport" not in text


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


# ---- resumable chunks and `pending` (2026-11-23) -----------------------------------------------------------------------
def stub(home, body):
    """A fake prepare.py: logs argv (FAKE_OUT), then `body`; `k` = this sport's chunk number (1, 2, ...), `s` = the sport."""
    (home / "workspace" / "prepare.py").write_text(
        "import json, os, sys\nopen(os.environ['FAKE_OUT'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "s = sys.argv[sys.argv.index('--sport') + 1]\n"
        "cf = os.environ['FAKE_OUT'] + '.' + s\nk = (int(open(cf).read()) if os.path.exists(cf) else 0) + 1\nopen(cf, 'w').write(str(k))\n" + body)


def staged(need):
    """Chunks 1..need-1 stop on the item budget (a new reason each time), chunk `need` is a complete evaluation."""
    return (f"if k < {need}: print('sport-outcome: ' + s + ' skipped v3=skipped (item budget reached; resumes with item' + str(k) + ')')\n"
            "else: print('sport-outcome: ' + s + ' evaluated v3=evaluated (all scored)')\n")


def chunk_lines(r):
    return [dict(p.split("=", 1) for p in x.split()[1:4]) for x in r.stdout.splitlines() if x.startswith("autoresearch-daily: sport=") and " rc=" in x]


@pytest.mark.parametrize("reason", ["item budget reached", "time budget reached (partial progress stored)"])
def test_a_skipped_sport_with_work_left_earlier_today_is_retried_not_done_today(home, tmp_path, monkeypatch, reason):
    seed(home, "ncaab", "skipped", reason, n=1)
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert sport_args(calls) == ["ncaab"] and "outcome=done_today" not in r.stdout, r.stdout
    assert state(home)["ncaab"][2] == "evaluated" and state(home)["ncaab"][5] == f"{LOCAL_TODAY}:2"


@pytest.mark.parametrize("reason", ["2 of 9 candidates pending", "incumbent scoring incomplete"])
def test_only_the_old_wrappers_work_left_skips_are_retried_by_the_gate(home, tmp_path, monkeypatch, reason):
    seed(home, "ncaab", "skipped", reason, n=1)
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert calls == [] and "sport=ncaab outcome=done_today reason=skipped" in r.stdout


def test_evaluated_with_queued_work_is_pending_sets_no_last_success_and_is_retried_the_same_day(home, tmp_path, monkeypatch):
    stub(home, "print('sport-outcome: ' + s + ' evaluated v3=evaluated (2 of 9 candidates scored; 3 queued; next x)')\n")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert [c["outcome"] for c in chunk_lines(r)] == ["pending", "failed"] and len(calls) == 2, r.stdout     # the identical line twice is stuck
    row = state(home)["ncaab"]
    assert row[2] == "failed" and row[3] == "" and "no progress in a full chunk" in row[4]
    (home / "daily-state.tsv").write_text(f"ncaab\t{stamp(timedelta(0))}\tevaluated\t{stamp(timedelta(0))}\t2 of 9 candidates scored; 3 queued; next x\t{LOCAL_TODAY}:1\n")
    r, calls = run(DAILY, home, tmp_path)                                    # a legacy `evaluated` row with queued work is retried too
    assert len(calls) == 4 and "outcome=done_today" not in r.stdout


def test_a_run_pending_after_one_chunk_is_recorded_pending_with_no_last_success(home, tmp_path, monkeypatch):
    stub(home, "print('sport-outcome: ' + s + ' evaluated v3=evaluated (' + str(k) + ' of 9 candidates scored; 3 queued; next x)')\n"
               "import time; time.sleep(0)\n")
    monkeypatch.setenv("AUTORESEARCH_TOTAL_SECONDS", "8")
    monkeypatch.setenv("AUTORESEARCH_MIN_START_SECONDS", "5")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    lines = chunk_lines(r)
    assert lines[0]["outcome"] == "pending", r.stdout
    assert state(home)["ncaab"][3] == "" and r.returncode == 0


def test_three_chunks_are_finished_by_one_run_each_through_learning_run(home, tmp_path, monkeypatch):
    stub(home, staged(3))
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert len(calls) == 3 and r.returncode == 0, r.stdout + r.stderr
    ev = [json.loads(x) for x in (tmp_path / "data" / "research" / "learning-runs.jsonl").read_text().splitlines()]
    assert [e["name"] for e in ev if e["event"] == "start"] == ["autoresearch-daily-ncaab"] * 3
    assert [c["outcome"] for c in chunk_lines(r)] == ["pending", "pending", "evaluated"]
    row = state(home)["ncaab"]
    assert row[2] == "evaluated" and row[3] == row[1] and row[5] == f"{LOCAL_TODAY}:3"
    assert "scored=1 " in r.stdout and "pending=0" in r.stdout


def test_chunks_go_round_robin_across_sports(home, tmp_path, monkeypatch):
    stub(home, staged(2))
    r, calls = daily(home, tmp_path, monkeypatch, "nba nhl")
    assert sport_args(calls) == ["nba", "nhl", "nba", "nhl"], r.stdout
    assert state(home)["nba"][2] == state(home)["nhl"][2] == "evaluated"


def test_an_identical_pending_result_twice_is_failed_for_no_progress_and_the_run_ends(home, tmp_path, monkeypatch):
    stub(home, "print('sport-outcome: ' + s + ' skipped v3=skipped (item budget reached; resumes with item1)')\n")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert len(calls) == 2 and r.returncode == 1, r.stdout
    row = state(home)["ncaab"]
    assert row[2] == "failed" and "no progress in a full chunk at " in row[4] and "one item needs longer than a chunk" in row[4]
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")                          # failed stays final for the day
    assert len(calls) == 2 and "sport=ncaab outcome=done_today reason=failed" in r.stdout


def test_a_sport_that_was_refused_only_on_a_later_chunk_stays_pending(home, tmp_path, monkeypatch):
    """Chunk 1 runs; the data-dir lock is then taken, so chunk 2 is refused by the guard: pending, not retried again this run."""
    import fcntl
    stub(home, "print('sport-outcome: ' + s + ' skipped v3=skipped (item budget reached; resumes with item' + str(k) + ')')\n"
               "open(os.environ['LOCK_FLAG'], 'w').write('x')\nimport time; time.sleep(1.5)\n")
    monkeypatch.setenv("LOCK_FLAG", str(tmp_path / "flag"))
    (tmp_path / "data").mkdir()
    import threading
    stop = threading.Event()

    def holder():
        while not (tmp_path / "flag").exists():
            if stop.is_set():
                return
        fh = open(tmp_path / "data" / ".learning-heavy.lock", "w")
        import time
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(0.01)
        stop.wait(30)
        fh.close()
    th = threading.Thread(target=holder)
    th.start()
    try:
        r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    finally:
        stop.set()
        th.join()
    assert len(calls) == 1, r.stdout
    row = state(home)["ncaab"]
    assert row[2] == "pending" and "guard refused" in row[4] and "work remains" in row[4] and row[3] == ""


def test_the_wrapper_passes_no_item_cap_by_default(home, tmp_path, monkeypatch):
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert int(calls[0][calls[0].index("--max-trials") + 1]) >= 100000 and "4" != calls[0][calls[0].index("--max-trials") + 1]


def test_a_short_run_window_leaves_the_working_sport_pending_and_retryable(home, tmp_path, monkeypatch):
    stub(home, "import time; time.sleep(2)\n" + staged(6))
    monkeypatch.setenv("AUTORESEARCH_TOTAL_SECONDS", "9")
    monkeypatch.setenv("AUTORESEARCH_MIN_START_SECONDS", "5")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    row = state(home)["ncaab"]
    assert 1 <= len(calls) <= 3 and row[2] == "pending" and "window ended" in row[4] and row[3] == "", r.stdout
    assert r.returncode == 0 and "pending=1" in r.stdout
    monkeypatch.delenv("AUTORESEARCH_TOTAL_SECONDS")
    r, calls2 = run(DAILY, home, tmp_path)                                           # a later run the same day retries it
    assert len(calls2) > len(calls)


def window_of(r):
    return int(next(x for x in r.stdout.splitlines() if x.startswith("autoresearch-daily: window_s=")).split()[1].split("=")[1])


def test_stop_at_shortens_the_window_only_when_it_is_still_ahead_today(home, tmp_path, monkeypatch):
    soon = datetime.now() + timedelta(minutes=2)
    past = datetime.now() - timedelta(minutes=2)
    if soon.date() != datetime.now().date() or past.date() != datetime.now().date():
        pytest.skip("too close to local midnight")
    monkeypatch.setenv("AUTORESEARCH_STOP_AT", soon.strftime("%H:%M"))
    monkeypatch.setenv("AUTORESEARCH_RESUME_AT", "23:59")
    r, _ = daily(home, tmp_path, monkeypatch, "nba")
    assert 0 < window_of(r) <= 125
    monkeypatch.setenv("AUTORESEARCH_STOP_AT", "")
    r, _ = run(DAILY, home, tmp_path)
    assert window_of(r) == 170 * 60


def report_stub(home, tmp_path, monkeypatch, body, tail=""):
    """The stub writes a report file (`rep`, edited by `body`; k = chunk number, s = sport, exp = experiment root) and prints the
    evaluator's final JSON line naming it. Default: chunk 1 has 4 items left, later chunks none."""
    stub(home, "rep = {'sports': {s: {'queue': {'done': k, 'remaining': 4 if k == 1 else 0, 'next_item': 'cand' + str(k)}}},\n"
               "       'v2': {'stopped': 'queue empty', 'trials': []}}\nexp = os.path.dirname(os.environ['REPORT_DIR'])\n" + body +
               "rf = os.path.join(os.environ['REPORT_DIR'], 'r%d.json' % k)\njson.dump(rep, open(rf, 'w'))\n"
               "print('sport-outcome: ' + s + ' evaluated v3=evaluated (done)')\nprint(json.dumps({'outcomes': {s: 'evaluated'}, 'report_file': rf}))\n" + tail)
    (tmp_path / "reports").mkdir(exist_ok=True)
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))


def outcomes(r):
    return [c["outcome"] for c in chunk_lines(r)]


def test_the_report_file_decides_pending_v3_queue_remaining(home, tmp_path, monkeypatch):
    report_stub(home, tmp_path, monkeypatch, "")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert outcomes(r) == ["pending", "evaluated"] and len(calls) == 2, r.stdout
    assert state(home)["ncaab"][2] == "evaluated"


def test_the_report_file_decides_pending_v2_budget_stop_for_nba(home, tmp_path, monkeypatch):
    report_stub(home, tmp_path, monkeypatch, "rep['sports'][s]['queue']['remaining'] = 0\nrep['v2'] = {'stopped': 'time budget reached' if k == 1 else 'queue empty', 'trials': [{}]}\n")
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert outcomes(r) == ["pending", "evaluated"], r.stdout


def test_a_finished_report_is_evaluated_in_one_chunk(home, tmp_path, monkeypatch):
    report_stub(home, tmp_path, monkeypatch, "rep['sports'][s]['queue']['remaining'] = 0\n")
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert len(calls) == 1 and outcomes(r) == ["evaluated"] and state(home)["nba"][3]


def test_a_report_whose_queue_did_not_move_is_stuck_and_names_the_next_item(home, tmp_path, monkeypatch):
    report_stub(home, tmp_path, monkeypatch, "rep['sports'][s]['queue'] = {'done': 1, 'remaining': 4, 'next_item': 'cand9'}\n")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    row = state(home)["ncaab"]
    assert len(calls) == 2 and row[2] == "failed" and "no progress in a full chunk at cand9" in row[4], r.stdout


def test_a_failed_item_retry_that_only_grows_items_jsonl_is_progress_not_stuck(home, tmp_path, monkeypatch):
    """The evaluator logs an item that raised as item_failed in items.jsonl; it stays in queue.remaining until it has failed 3 times."""
    report_stub(home, tmp_path, monkeypatch,
                "rep['sports'][s] = {'protocol_id': 'P1', 'queue': {'done': 1, 'remaining': 0 if k >= 3 else 4, 'next_item': 'x'}}\n"
                "d = os.path.join(exp, 'v3', 'P1', s); os.makedirs(d, exist_ok=True)\n"
                "open(os.path.join(d, 'items.jsonl'), 'a').write('{\"event\": \"item_failed\"}\\n')\n")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert outcomes(r) == ["pending", "pending", "evaluated"], r.stdout
    assert state(home)["ncaab"][2] == "evaluated" and state(home)["ncaab"][3]


def test_a_soccer_walk_that_only_grows_its_partial_file_is_progress(home, tmp_path, monkeypatch):
    report_stub(home, tmp_path, monkeypatch,
                "rep['sports'][s] = {'protocol_id': 'P1', 'queue': {'done': 2, 'remaining': 0 if k >= 4 else 4, 'next_item': 'walk'}}\n"
                "d = os.path.join(exp, 'v3', 'P1', s, 'items'); os.makedirs(d, exist_ok=True)\n"
                "open(os.path.join(d, 'walk.partial.json'), 'w').write('x' * (100 * k))\n")
    r, calls = daily(home, tmp_path, monkeypatch, "soccer")
    assert outcomes(r) == ["pending", "pending", "pending", "evaluated"], r.stdout
    assert state(home)["soccer"][3]


def test_nba_with_v3_done_and_v2_running_trials_each_chunk_is_pending_until_the_v2_queue_is_empty(home, tmp_path, monkeypatch):
    report_stub(home, tmp_path, monkeypatch,
                "rep['sports'][s]['queue']['remaining'] = 0\nrep['sports'][s]['queue']['done'] = 5\n"
                "rep['v2'] = {'stopped': 'time budget reached' if k < 4 else 'queue empty', 'trials': [{}, {}]}\n")
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert outcomes(r) == ["pending", "pending", "pending", "evaluated"], r.stdout
    assert state(home)["nba"][2] == "evaluated" and state(home)["nba"][3]


def test_a_report_file_that_cannot_be_read_is_failed_not_a_guess(home, tmp_path, monkeypatch):
    stub(home, "print('sport-outcome: ' + s + ' evaluated v3=evaluated (done)')\n"
               "print(json.dumps({'outcomes': {s: 'evaluated'}, 'report_file': '/nonexistent/dir/r.json'}))\n")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    row = state(home)["ncaab"]
    assert len(calls) == 1 and row[2] == "failed" and row[3] == "" and "the evaluator's report file could not be read: /nonexistent/dir/r.json" in row[4]


def test_a_stray_brace_line_after_the_report_line_does_not_hide_it(home, tmp_path, monkeypatch):
    report_stub(home, tmp_path, monkeypatch, "", tail="print('{\"progress\": 1}')\nprint('{ not json')\n")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    assert outcomes(r) == ["pending", "evaluated"], r.stdout


def test_a_nonzero_rc_after_an_evaluated_line_without_a_report_is_failed(home, tmp_path, monkeypatch):
    stub(home, "print('sport-outcome: ' + s + ' evaluated v3=evaluated (done)')\nsys.exit(3)\n")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    row = state(home)["ncaab"]
    assert row[2] == "failed" and row[3] == "" and "rc 3 after an evaluated line without a report" in row[4]


def test_an_earlier_last_success_survives_a_later_pending_row(home, tmp_path, monkeypatch):
    old = stamp(timedelta(days=-2))
    seed(home, "ncaab", "evaluated", day=LOCAL_YESTERDAY, at=old)
    stub(home, "import time; time.sleep(1)\n" + staged(99))
    monkeypatch.setenv("AUTORESEARCH_TOTAL_SECONDS", "8")
    monkeypatch.setenv("AUTORESEARCH_MIN_START_SECONDS", "5")
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    row = state(home)["ncaab"]
    assert row[2] == "pending" and row[3] == old, r.stdout


def minutes_of(call):
    return call[call.index("--max-minutes") + 1]


def test_nba_and_nhl_soft_budget_is_halved_for_the_v2_queue_and_needs_a_timeout_of_room(home, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_TOTAL_SECONDS", "1500")
    r, calls = daily(home, tmp_path, monkeypatch, "nba ncaab")
    by = {c[c.index("--sport") + 1]: minutes_of(c) for c in calls}
    assert by == {"nba": "5", "ncaab": "15"}, (by, r.stdout)       # (24 - 4 - 10) / 2 = 5


def test_an_nba_chunk_is_not_started_without_the_minimum_plus_a_trial_timeout_but_other_sports_run(home, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_TOTAL_SECONDS", "900")
    r, calls = daily(home, tmp_path, monkeypatch, "nba ncaab")
    assert sport_args(calls) == ["ncaab"], r.stdout
    row = state(home)["nba"]
    assert row[2] == "skipped" and row[4].startswith("total wall cap reached") and state(home)["ncaab"][2] == "evaluated"
    monkeypatch.delenv("AUTORESEARCH_TOTAL_SECONDS")
    r, calls = run(DAILY, home, tmp_path)                                     # retryable the same day
    assert sport_args(calls) == ["ncaab", "nba"]


def test_a_short_chunk_with_no_progress_stays_pending_and_is_not_retried_in_this_run(home, tmp_path, monkeypatch):
    stub(home, "print('sport-outcome: ' + s + ' skipped v3=skipped (time budget reached; resumes with item1)')\n")
    monkeypatch.setenv("AUTORESEARCH_TOTAL_SECONDS", "600")             # window 10 min: soft budget 6 min < the full 15
    r, calls = daily(home, tmp_path, monkeypatch, "ncaab")
    row = state(home)["ncaab"]
    assert len(calls) == 2 and row[2] == "pending", r.stdout
    assert "no progress in a short chunk (6 min); not retried in this run" in row[4] and r.returncode == 0


def window_clock(minus=0, plus=0):
    n = datetime.now()
    a, b = n - timedelta(minutes=minus), n + timedelta(minutes=plus)
    if a.date() != n.date() or b.date() != n.date():
        pytest.skip("too close to local midnight")
    return a.strftime("%H:%M"), b.strftime("%H:%M")


def test_inside_the_quiet_period_nothing_starts_and_the_state_is_untouched(home, tmp_path, monkeypatch):
    stop, resume = window_clock(minus=1)[0], window_clock(plus=3)[1]
    monkeypatch.setenv("AUTORESEARCH_STOP_AT", stop)
    monkeypatch.setenv("AUTORESEARCH_RESUME_AT", resume)
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert r.returncode == 0 and calls == [] and f"autoresearch-daily: quiet period {stop}-{resume} local: nothing started" in r.stdout
    assert not (home / "daily-state.tsv").exists()


def test_after_resume_at_the_window_is_the_full_total(home, tmp_path, monkeypatch):
    stop, resume = window_clock(minus=5)[0], window_clock(minus=1)[0]
    monkeypatch.setenv("AUTORESEARCH_STOP_AT", stop)
    monkeypatch.setenv("AUTORESEARCH_RESUME_AT", resume)
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert window_of(r) == 170 * 60 and sport_args(calls) == ["nba"]


@pytest.mark.parametrize("stop,resume", [("25:00", "16:30"), ("8:5x", "16:30"), ("abc", "16:30"), ("08:60", "16:30"),
                                         ("08:55", "24:00"), ("08:55", "soon"), ("08:55", ""), ("16:30", "08:55")])
def test_a_malformed_quiet_period_refuses_loudly_and_starts_nothing(home, tmp_path, monkeypatch, stop, resume):
    monkeypatch.setenv("AUTORESEARCH_STOP_AT", stop)
    monkeypatch.setenv("AUTORESEARCH_RESUME_AT", resume)
    r, calls = daily(home, tmp_path, monkeypatch, "nba")
    assert r.returncode == 78 and calls == [] and "AUTORESEARCH_STOP_AT" in r.stdout + r.stderr
    assert not (home / "daily-state.tsv").exists()


def test_the_stuck_process_limit_covers_both_queues_of_nba_and_nhl(home, tmp_path):
    """The guard's hard limit is a stuck-process timeout, never a work limit: NBA/NHL run the v3 queue (soft budget, atomic items)
    and then the v2 queue with its own soft budget, whose last trial may run to its --timeout. A limit of soft + margin alone could
    kill a healthy chunk in the v2 queue (and `failed` is final for the day)."""
    on(home)
    r, calls = run(DAILY, home, tmp_path)
    assert r.returncode == 0, r.stderr
    ev = [json.loads(x) for x in (tmp_path / "data" / "research" / "learning-runs.jsonl").read_text().splitlines()]
    limit = {e["name"].rsplit("-", 1)[1]: e["max_minutes"] for e in ev if e["event"] == "start"}
    assert limit["ncaab"] == limit["nfl"] == limit["soccer"] == 15 + 30
    assert limit["nba"] == limit["nhl"] == 15 + 30 + 15 + 10          # + the v2 queue's soft budget + one trial timeout (600 s)
