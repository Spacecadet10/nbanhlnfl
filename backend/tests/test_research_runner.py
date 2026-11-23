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
    out = {"home": home, "snap": snap, "pinned": trees / "pinned", "live": trees / "live", "prepare": home / "workspace" / "prepare.py"}
    r = run(out, "write-pin", "--reason", "test")
    assert r.returncode == 0, r.stderr
    yield out
    snap.chmod(0o755)
    for p in snap.iterdir():
        p.chmod(0o644)


def run(lab, *argv, cwd="/", **env):
    return subprocess.run([sys.executable, "-I", str(RUNNER), *map(str, argv)], cwd=cwd, capture_output=True, text=True, timeout=300,
                          env=dict(os.environ, AUTORESEARCH_HOME=str(lab["home"]), **env))


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
              "AUTORESEARCH_APP", "__PYVENV_LAUNCHER__", "CONDA_PREFIX"):
        monkeypatch.setenv(k, "/evil")
    monkeypatch.setenv("AUTORESEARCH_BROKER", "/sock")
    env = rr.clean_env({})
    assert env["PYTHONNOUSERSITE"] == "1" and env["AUTORESEARCH_BROKER"] == "/sock" and env["HOME"] == os.environ["HOME"]
    assert not [k for k, v in env.items() if v == "/evil"]


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


def v2_pid(lab):
    return json.loads((lab["home"] / "PIN.json").read_text())["protocols"]["nba"]["v2"]


@pytest.mark.parametrize("trial", [None, "20261123T110757Z-ab15fe2f"])
def test_a_blocked_protocol_or_an_invalid_trial_of_it_stops_the_pinned_study(lab, trial):
    exp = lab["home"] / "experiments"
    ra.append(exp, status="invalid", protocol_id="another-protocol", sport="nhl", reason="other study")
    assert daily(lab)[0].returncode == 0                                             # an unrelated entry does not stop it
    ra.append(exp, status="invalid", protocol_id=v2_pid(lab), sport="nba", reason="unintended code", trial_id=trial)
    r, got = daily(lab)
    assert r.returncode == 2 and got is None and "unintended code" in r.stderr


def test_unverified_stops_the_sport_until_verified_and_a_damaged_audit_stops_everything(lab):
    exp = lab["home"] / "experiments"
    ra.append(exp, status="unverified", protocol_id=v2_pid(lab), sport="nba", reason="made by live code")
    assert daily(lab)[0].returncode == 2
    ra.append(exp, status="verified", protocol_id=v2_pid(lab), sport="nba", reason="recomputed: identical")
    assert daily(lab)[0].returncode == 0
    (exp / ra.FILE).write_text("{oops\n")
    r, _ = daily(lab)
    assert r.returncode == 2 and "audit file is unusable" in r.stderr


def test_an_audit_file_emptied_after_the_pin_was_written_is_noticed(lab):
    exp, pinfile = lab["home"] / "experiments", lab["home"] / "PIN.json"
    e = ra.append(exp, status="invalid", protocol_id="another-protocol", sport="nhl", reason="other study")
    pinfile.chmod(0o644)
    pinfile.write_text(json.dumps(json.loads(pinfile.read_text()) | {"audit_head": ra.load(exp)[0]["sha"]}))
    assert daily(lab)[0].returncode == 0 and e
    (exp / ra.FILE).unlink()
    (exp / ra.HEAD).unlink()
    r, _ = daily(lab)
    assert r.returncode == 2 and "no longer holds" in r.stderr


def test_nothing_in_the_pinned_checkout_or_snapshot_is_written(lab):
    def tree(p):
        return {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob("*") if f.is_file() and ".git/" not in str(f)}
    before = (tree(lab["pinned"]), tree(lab["snap"]))
    daily(lab)
    assert (tree(lab["pinned"]), tree(lab["snap"])) == before
