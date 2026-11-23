"""scripts/research_runner.py: the scheduled research uses the pinned code, interpreter and snapshot from any directory and any
environment, and an unrelated app release cannot change the study identity (Nov 23 2026, 06:00). The real app tree is copied into
a "pinned" and a "live" checkout; only prepare.py is a stand-in (same shape and environment handling as the real shim)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_runner.py"
sys.path.insert(0, str(ROOT / "backend"))
from app import research_audit as ra  # noqa: E402

SHIM = '''"""stand-in for the workspace shim: same constants, same environment, reports what it loaded."""
import os
import sys
from pathlib import Path

HOME = Path.home() / "autoresearch-sports"
PY = "{py}"
BACKEND = "{backend}"
SNAPSHOT = "{snapshot}"
CODE = """
import json, sys, os
from pathlib import Path
from app import autoresearch as a
print('LOADED ' + json.dumps({{'app': str(Path(a.__file__).resolve().parents[1]), 'files': a.app_identity()['files'],
      'v2': a.protocol_id({{'nba': a.snapshot_identity('nba')}}), 'data': os.environ['NBA_DATA_DIR'],
      'env': sorted(k for k in os.environ if k.startswith(('PYTHON', 'NBA_', 'AUTORESEARCH_')))}}))
if 'daily' in sys.argv: print('sport-outcome: nba evaluated v3=refused v2=evaluated (study tables not cached)')
"""
env = dict(os.environ, PYTHONPATH=BACKEND, NBA_DATA_DIR=SNAPSHOT, PYTHONDONTWRITEBYTECODE="1")
os.execve(PY, [PY, "-c", CODE, *sys.argv[1:]], env)
'''


def git(path, *a):
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True, text=True).stdout


def checkout(dst):
    shutil.copytree(ROOT / "backend" / "app", dst / "backend" / "app", ignore=shutil.ignore_patterns("__pycache__", ".*"))
    (dst / "frontend").mkdir()
    (dst / "frontend" / "HistoryPage.tsx").write_text("v1\n")
    git(dst, "init", "-q")
    git(dst, "add", "-A")
    git(dst, "commit", "-qm", "base")


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    base = tmp_path_factory.mktemp("trees")
    for name in ("pinned", "live"):
        (base / name).mkdir()
        checkout(base / name)
    return base


@pytest.fixture
def lab(trees, tmp_path):
    for name in ("pinned", "live"):
        git(trees / name, "reset", "-q", "--hard")
        git(trees / name, "clean", "-qfd")
    home = tmp_path / "autoresearch-sports"
    (home / "workspace").mkdir(parents=True)
    (home / "experiments").mkdir()
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / "nba.sqlite").write_bytes(b"synthetic")
    (snap / "IDENTITY.json").write_text("{}")
    for p in (snap / "nba.sqlite", snap / "IDENTITY.json"):
        p.chmod(0o444)
    snap.chmod(0o555)
    backend = trees / "pinned" / "backend"
    (home / "workspace" / "prepare.py").write_text(SHIM.format(py=sys.executable, backend=backend, snapshot=snap))
    (home / "workspace" / "program.md").write_text("x\n")
    git(home / "workspace", "init", "-q")
    git(home / "workspace", "add", "-A")
    git(home / "workspace", "commit", "-qm", "ws")
    (home / "experiments" / "another-protocol" / "nhl").mkdir(parents=True)
    ra.append(home / "experiments", status="invalid", protocol_id="another-protocol", sport="nhl", reason="other study")
    out = {"home": home, "snap": snap, "pinned": trees / "pinned", "live": trees / "live", "prepare": home / "workspace" / "prepare.py"}
    r = run(out, "write-pin", "--reason", "test")
    assert r.returncode == 0, r.stderr
    yield out
    snap.chmod(0o755)
    for p in snap.iterdir():
        p.chmod(0o644)


def run(lab, *argv, cwd="/", **env):
    return subprocess.run([sys.executable, "-I", str(RUNNER), *map(str, argv)], cwd=cwd, capture_output=True, text=True, timeout=300,
                          env=dict(os.environ, HOME=str(lab["home"].parent)) | env)


def daily(lab, cwd="/", **env):
    r = run(lab, lab["prepare"], "daily", "--sport", "nba", cwd=cwd, **env)
    line = next((x for x in r.stdout.splitlines() if x.startswith("LOADED ")), None)
    return r, json.loads(line[7:]) if line else None


def bare(lab, cwd):
    """The repaired bug: the shim called directly from another directory."""
    r = subprocess.run([sys.executable, str(lab["prepare"])], cwd=cwd, capture_output=True, text=True, env=dict(os.environ))
    return json.loads(r.stdout.split("LOADED ", 1)[1].splitlines()[0])


def release(live):
    """An unrelated history/UI release of the live app."""
    (live / "backend" / "app" / "some_history_page.py").write_text('"""unrelated"""\n')
    (live / "backend" / "app" / "main.py").write_text((live / "backend" / "app" / "main.py").read_text() + "\n# new route\n")
    (live / "frontend" / "HistoryPage.tsx").write_text("v2\n")
    git(live, "add", "-A")
    git(live, "commit", "-qm", "history release")


def test_an_unrelated_release_cannot_change_the_pinned_study_identity(lab):
    want = json.loads((lab["home"] / "PIN.json").read_text())
    _, before = daily(lab, cwd=lab["live"] / "backend")
    control_before = bare(lab, lab["live"] / "backend")
    release(lab["live"])
    _, after = daily(lab, cwd=lab["live"] / "backend")
    control_after = bare(lab, lab["live"] / "backend")
    # the bug, reproduced: without the runner the live checkout is loaded and its release changes the study identity
    assert control_after["app"] == str(lab["live"] / "backend") and control_after["v2"] != control_before["v2"]
    # repaired: same code, same identity, same study, before and after the release
    assert after == before and after["app"] == str(lab["pinned"] / "backend")
    assert after["files"] == want["app"]["files"] and after["v2"] == want["protocols"]["nba"]["v2"]


def test_the_pinned_code_is_used_from_any_directory_and_with_a_hostile_environment(lab, tmp_path):
    rogue = tmp_path / "rogue"
    (rogue / "app").mkdir(parents=True)
    (rogue / "app" / "__init__.py").write_text("raise SystemExit('rogue app loaded')\n")
    (rogue / "json.py").write_text("raise SystemExit('rogue json loaded')\n")
    (rogue / "start.py").write_text("raise SystemExit('startup ran')\n")
    seen = []
    for cwd in ("/", rogue, lab["live"] / "backend", lab["home"] / "workspace"):
        r, got = daily(lab, cwd=cwd)
        assert r.returncode == 0, r.stderr
        seen.append(got)
    r, got = daily(lab, cwd=rogue, PYTHONPATH=str(rogue), PYTHONSTARTUP=str(rogue / "start.py"), NBA_DATA_DIR=str(rogue),
                   AUTORESEARCH_APP=str(rogue), VIRTUAL_ENV=str(rogue), NBA_API_MODE="demo", PYTHONHOME="/nonexistent",
                   PYTHONINSPECT="1", PYTHONSAFEPATH="1")
    assert r.returncode == 0 and got == seen[0], r.stderr
    seen.append(daily(lab, cwd=lab["live"] / "backend", PYTHONPATH=str(lab["live"] / "backend"), PYTHONUSERBASE=str(rogue),
                      NBA_DATA_DIR=str(rogue), AUTORESEARCH_APP=str(rogue))[1])
    assert all(x == seen[0] for x in seen) and seen[0]["app"] == str(lab["pinned"] / "backend") and seen[0]["data"] == str(lab["snap"])
    assert "AUTORESEARCH_APP" not in seen[0]["env"] and "PYTHONUSERBASE" not in seen[0]["env"]


def test_the_environment_given_to_the_study_drops_python_and_app_variables(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("research_runner", RUNNER)
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)
    for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONSAFEPATH", "VIRTUAL_ENV", "DYLD_INSERT_LIBRARIES", "NBA_DATA_DIR",
              "AUTORESEARCH_APP", "__PYVENV_LAUNCHER__", "CONDA_PREFIX", "KERAS_BACKEND", "TF_CONFIG", "OPENBLAS_NUM_THREADS", "TZ", "GIT_DIR"):
        monkeypatch.setenv(k, "/evil")
    monkeypatch.setenv("AUTORESEARCH_BROKER", "/sock")
    env = rr.clean_env({})
    assert env["PYTHONNOUSERSITE"] == "1" and env["HOME"] == os.environ["HOME"]
    assert not [k for k, v in env.items() if v == "/evil"] and "AUTORESEARCH_BROKER" not in env


@pytest.mark.parametrize("drift,why", [
    ("edit", "uncommitted changes"), ("commit", "not at"), ("prepare", "prepare file differs"), ("identity", "IDENTITY.json differs"),
    ("store", "store differs"), ("packages", "packages differ"), ("study", "study identity"), ("files", "differ from the recorded identity")])
def test_any_drift_from_the_pin_refuses_before_anything_runs(lab, drift, why):
    pinfile = lab["home"] / "PIN.json"
    pinfile.chmod(0o644)
    pin = json.loads(pinfile.read_text())
    if drift == "edit":
        (lab["pinned"] / "backend" / "app" / "main.py").write_text("# changed\n")
    elif drift == "commit":
        (lab["pinned"] / "x.txt").write_text("x")
        git(lab["pinned"], "add", "-A")
        git(lab["pinned"], "commit", "-qm", "moved")
    elif drift == "prepare":
        lab["prepare"].write_text(lab["prepare"].read_text() + "# x\n")
    elif drift in ("identity", "store"):
        f = lab["snap"] / ("IDENTITY.json" if drift == "identity" else "nba.sqlite")
        f.chmod(0o644)
        f.write_text("{1}")
        f.chmod(0o444)
    elif drift == "packages":
        pin["packages"] = "0" * 64
    elif drift == "files":
        pin["app"]["files"] = "0" * 16
    else:
        pin["protocols"]["nba"]["v2"] = "sports-autoresearch-v2-000000000000"
    pinfile.write_text(json.dumps(pin))
    try:
        r, got = daily(lab)
        assert r.returncode == 2 and got is None and why in r.stderr, r.stderr
        assert "sport-outcome: nba refused v3=refused (runner: " in r.stdout
        ev = [json.loads(x) for x in (lab["home"] / "runner-runs.jsonl").read_text().splitlines()]
        assert [e["event"] for e in ev] == ["refused"] and why in ev[0]["reason"]
    finally:
        if drift == "commit":
            git(lab["pinned"], "reset", "-q", "--hard", "HEAD~1")


def test_without_a_pin_nothing_runs_and_a_pin_is_never_overwritten(lab):
    r = run(lab, "write-pin", "--reason", "again")
    assert r.returncode == 2 and "never overwritten" in r.stderr
    (lab["home"] / "PIN.json").unlink()
    r, got = daily(lab)
    assert r.returncode == 2 and got is None and "no usable pin" in r.stderr


def test_the_run_is_logged_with_its_label_outcome_and_identity(lab):
    daily(lab, AUTORESEARCH_RUN_LABEL="recovery-2026-11-23")
    ev = [json.loads(x) for x in (lab["home"] / "runner-runs.jsonl").read_text().splitlines()]
    assert [e["event"] for e in ev] == ["start", "end"] and {e["label"] for e in ev} == {"recovery-2026-11-23"}
    assert ev[1]["outcomes"] == ["sport-outcome: nba evaluated v3=refused v2=evaluated (study tables not cached)"]
    assert ev[1]["files"] == json.loads((lab["home"] / "PIN.json").read_text())["app"]["files"] and ev[1]["rc"] == 0


def records(exp, pid, trial=None):
    (exp / pid / "nba").mkdir(parents=True, exist_ok=True)
    (exp / pid / "nba" / "trials.jsonl").write_text(json.dumps({"trial_id": trial}) + "\n")


def v2_pid(lab):
    return json.loads((lab["home"] / "PIN.json").read_text())["protocols"]["nba"]["v2"]


@pytest.mark.parametrize("trial", [None, "20261123T110757Z-ab15fe2f"])
def test_a_blocked_protocol_or_an_invalid_trial_of_it_stops_the_pinned_study(lab, trial):
    exp = lab["home"] / "experiments"
    assert daily(lab)[0].returncode == 0                                             # an unrelated entry (in `lab`) does not stop it
    records(exp, v2_pid(lab), trial)
    ra.append(exp, status="invalid", protocol_id=v2_pid(lab), sport="nba", reason="unintended code", trial_id=trial)
    r, got = daily(lab)
    assert r.returncode == 2 and got is None and "unintended code" in r.stderr


def test_unverified_stops_the_sport_until_verified_and_a_damaged_audit_stops_everything(lab):
    exp = lab["home"] / "experiments"
    records(exp, v2_pid(lab))
    ra.append(exp, status="unverified", protocol_id=v2_pid(lab), sport="nba", reason="made by live code")
    assert daily(lab)[0].returncode == 2
    ra.append(exp, status="verified", protocol_id=v2_pid(lab), sport="nba", reason="recomputed: identical")
    assert daily(lab)[0].returncode == 0
    (exp / ra.FILE).write_text("{oops\n")
    r, _ = daily(lab)
    assert r.returncode == 2 and "audit file is unusable" in r.stderr


def test_an_audit_file_deleted_after_the_pin_was_written_is_noticed_and_a_pin_needs_an_audit(lab):
    exp, pinfile = lab["home"] / "experiments", lab["home"] / "PIN.json"
    assert json.loads(pinfile.read_text())["audit_head"] == ra.load(exp)[0]["sha"]
    (exp / ra.FILE).unlink()
    (exp / ra.HEAD).unlink()
    assert daily(lab)[0].returncode == 2
    pinfile.unlink()
    r = run(lab, "write-pin", "--reason", "no audit")
    assert r.returncode == 2 and "write the audit first" in r.stderr and not pinfile.exists()


@pytest.mark.parametrize("args", [["daily", "--sport=nba"], ["daily", "--spo", "nba"], ["daily", "--sport", "nba", "--sport=nfl"], ["daily", "--sport"],
                                  ["daily", "--sport", "cricket"], ["frobnicate"], []])
def test_an_argument_the_runner_would_not_check_is_refused(lab, args):
    r = run(lab, lab["prepare"], *args)
    assert r.returncode == 2 and "LOADED" not in r.stdout, (r.stdout, r.stderr)


def test_another_prepare_a_broker_and_a_sport_the_pin_does_not_hold_are_refused(lab, tmp_path):
    other = tmp_path / "prepare.py"
    other.write_text(lab["prepare"].read_text())
    assert run(lab, other, "daily", "--sport", "nba").returncode == 2
    assert run(lab, other, "stop").returncode == 2
    r, got = daily(lab, AUTORESEARCH_BROKER="/tmp/x.sock")
    assert r.returncode == 2 and got is None and "AUTORESEARCH_BROKER" in r.stderr
    assert "the pin holds no nhl store" in run(lab, lab["prepare"], "daily", "--sport", "nhl").stderr


def test_another_home_in_the_environment_changes_nothing(lab, tmp_path):
    (tmp_path / "evil" / "experiments").mkdir(parents=True)
    r, got = daily(lab, AUTORESEARCH_HOME=str(tmp_path / "evil"), NBA_AUTORESEARCH_HOME=str(tmp_path / "evil"))
    assert r.returncode == 0 and got["app"] == str(lab["pinned"] / "backend") and not list((tmp_path / "evil" / "experiments").iterdir())


def test_the_kill_switch_works_without_a_pin(lab):
    (lab["home"] / "PIN.json").unlink()
    assert run(lab, lab["prepare"], "stop").returncode == 0


def test_the_kill_switch_takes_a_relative_path_and_prepare_cache_takes_only_nba_and_nhl(lab):
    assert run(lab, "prepare.py", "stop", cwd=lab["home"] / "workspace").returncode == 0
    for args in (["--sport", "nfl"], ["--sport", "nba", "--exp-root", "/tmp/x"], [], ["--sport"]):
        r = run(lab, "prepare-cache", *args)
        assert r.returncode == 2 and "prepare-cache takes" in r.stderr


def test_a_finder_file_in_the_pinned_checkout_does_not_stop_research(lab):
    (lab["pinned"] / "backend" / ".DS_Store").write_text("x")
    try:
        assert daily(lab)[0].returncode == 0
    finally:
        (lab["pinned"] / "backend" / ".DS_Store").unlink()


@pytest.mark.parametrize("name", ["PY", "BACKEND", "SNAPSHOT"])
def test_prepare_and_the_pin_must_name_the_same_paths(lab, name):
    pinfile = lab["home"] / "PIN.json"
    pinfile.chmod(0o644)
    pin = json.loads(pinfile.read_text())
    text = lab["prepare"].read_text()
    line = next(x for x in text.splitlines() if x.startswith(name + " = "))
    lab["prepare"].write_text(text.replace(line, f'{name} = "/elsewhere"'))
    pin["workspace"]["prepare_sha256"] = hashlib.sha256(lab["prepare"].read_bytes()).hexdigest()      # only the paths disagree
    pinfile.write_text(json.dumps(pin))
    r, got = daily(lab)
    assert r.returncode == 2 and got is None and "other paths than the pin" in r.stderr


def test_a_check_that_cannot_be_made_is_a_logged_refusal(lab):
    (lab["snap"]).chmod(0o755)
    (lab["snap"] / "IDENTITY.json").chmod(0o644)
    (lab["snap"] / "IDENTITY.json").unlink()
    r, got = daily(lab)
    assert r.returncode == 2 and got is None and "sport-outcome: nba refused" in r.stdout
    assert json.loads((lab["home"] / "runner-runs.jsonl").read_text().splitlines()[-1])["event"] == "refused"


def test_an_ignored_file_in_the_pinned_checkout_is_refused(lab):
    (lab["pinned"] / ".git" / "info" / "exclude").write_text("sitecustomize.py\n")
    (lab["pinned"] / "backend" / "sitecustomize.py").write_text("raise SystemExit('ran')\n")
    try:
        r, got = daily(lab)
        assert r.returncode == 2 and got is None and "ignored files" in r.stderr
    finally:
        (lab["pinned"] / "backend" / "sitecustomize.py").unlink()
        (lab["pinned"] / ".git" / "info" / "exclude").write_text("")


@pytest.mark.parametrize("during", ["audit", "store"])
def test_a_change_during_the_run_makes_it_refused(lab, during):
    hook = (f"open({str(lab['home'] / 'experiments' / ra.FILE)!r}, 'a').write('{{oops')" if during == "audit" else
            f"import os; os.chmod({str(lab['snap'] / 'nba.sqlite')!r}, 0o644); open({str(lab['snap'] / 'nba.sqlite')!r}, 'w').write('x')")
    pinfile = lab["home"] / "PIN.json"
    lab["prepare"].write_text(lab["prepare"].read_text().replace("import os\n", "import os\nimport sys\n" + f"if 'daily' in sys.argv: exec({hook!r})\n", 1))
    pinfile.chmod(0o644)
    pin = json.loads(pinfile.read_text())
    pin["workspace"]["prepare_sha256"] = hashlib.sha256(lab["prepare"].read_bytes()).hexdigest()
    pinfile.write_text(json.dumps(pin))
    r, _ = daily(lab)
    assert r.returncode == 2 and "changed while it ran" in r.stderr
    assert r.stdout.strip().splitlines()[-1].startswith("sport-outcome: nba refused v3=refused")


def test_output_that_is_not_utf8_and_a_killed_runner_leave_no_study_running(lab):
    import signal
    import time
    marker = lab["home"] / "child.pid"
    lab["prepare"].write_text(lab["prepare"].read_text().replace("os.execve(", f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
                              "sys.stdout.buffer.write(b'\\xff bad byte\\n'); sys.stdout.flush()\n"
                              "if 'sleep' in sys.argv:\n    import time; time.sleep(60)\nos.execve(", 1))
    pinfile = lab["home"] / "PIN.json"
    pinfile.chmod(0o644)
    pin = json.loads(pinfile.read_text())
    pin["workspace"]["prepare_sha256"] = hashlib.sha256(lab["prepare"].read_bytes()).hexdigest()
    pinfile.write_text(json.dumps(pin))
    r, got = daily(lab)
    assert r.returncode == 0 and got, r.stderr
    marker.unlink()
    p = subprocess.Popen([sys.executable, "-I", str(RUNNER), str(lab["prepare"]), "daily", "--sport", "nba", "sleep"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=dict(os.environ, HOME=str(lab["home"].parent)), start_new_session=True)
    for _ in range(300):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.1)
    child = int(marker.read_text())
    p.send_signal(signal.SIGTERM)
    p.wait(30)
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_the_study_inherits_the_supervisors_lock(lab, tmp_path):
    import fcntl
    lock = tmp_path / "heavy.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    os.set_inheritable(fd, True)
    fcntl.flock(fd, fcntl.LOCK_EX)
    lab["prepare"].write_text(lab["prepare"].read_text().replace("os.execve(", f"print('FD', os.fstat({fd}).st_ino == os.stat({str(lock)!r}).st_ino, flush=True)\nos.execve(", 1))
    pinfile = lab["home"] / "PIN.json"
    pinfile.chmod(0o644)
    pin = json.loads(pinfile.read_text())
    pin["workspace"]["prepare_sha256"] = hashlib.sha256(lab["prepare"].read_bytes()).hexdigest()
    pinfile.write_text(json.dumps(pin))
    r = subprocess.run([sys.executable, "-I", str(RUNNER), str(lab["prepare"]), "daily", "--sport", "nba"], capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(lab["home"].parent)), pass_fds=(fd,), timeout=300)
    os.close(fd)
    assert "FD True" in r.stdout, r.stdout + r.stderr


def test_nothing_in_the_pinned_checkout_or_snapshot_is_written(lab):
    def tree(p):
        return {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob("*") if f.is_file() and ".git/" not in str(f)}
    before = (tree(lab["pinned"]), tree(lab["snap"]))
    daily(lab)
    assert (tree(lab["pinned"]), tree(lab["snap"])) == before
