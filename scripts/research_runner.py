#!/usr/bin/env -S python3 -I
"""The only scheduled way into the research workspace: runs its prepare.py with the PINNED code, interpreter, packages and snapshot.

Why (Nov 23 2026, 06:00): prepare.py sets PYTHONPATH to the pinned checkout, but `python -m` puts the CURRENT DIRECTORY before
PYTHONPATH, and the scheduled job starts in the live checkout. A history/UI release therefore changed the code, and the study
identity, under a running study. prepare.py is itself a registered, hash-protected file of the existing protocols, so it stays as it
is; this runner, outside the fingerprinted app, makes its environment right from any directory and refuses otherwise:
- working directory = the pinned checkout's backend; only the variables in ALLOW are passed on; no user site-packages; a set
  AUTORESEARCH_BROKER refuses (it would send the request to another process);
- before anything runs, PIN.json is verified: checkout commit and clean tree, app file hash, interpreter, installed packages,
  snapshot identity and store hash, prepare.py/program.md hashes, and that a fresh process really loads the pinned code;
- records blocked in the audit file (app/research_audit.py) refuse the sport: a blocked protocol, an unverified run, or a single
  invalid trial of the active protocol when the pinned evaluator cannot leave it out itself (`enforces_audit` in PIN.json);
- after the run everything is verified again: a change during the run makes the outcome `refused`.
The research home is always ~/autoresearch-sports (prepare.py writes there whatever a caller's AUTORESEARCH_HOME says).

Use:  research_runner.py <workspace>/prepare.py <prepare.py arguments>      (a drop-in for `python prepare.py ...`)
      research_runner.py verify [--sport S]                                 prints what would run, runs nothing
      research_runner.py prepare-cache --sport S                            builds the pinned study tables once (nba, nhl)
      research_runner.py write-pin --reason R                               records PIN.json from prepare.py's constants; never overwrites
Label: AUTORESEARCH_RUN_LABEL=<text> is stored with the run in runner-runs.jsonl (e.g. a recovery attempt).
Exit 2 = refused; `daily` then also prints `sport-outcome: <sport> refused v3=refused (<why>)`.
"""
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME_AR = Path.home() / "autoresearch-sports"
PIN = HOME_AR / "PIN.json"
EXP = HOME_AR / "experiments"
ALLOW = ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "OMP_NUM_THREADS",
         "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "__CF_USER_TEXT_ENCODING")   # = research_agent.AGENT_ENV + 2
FIVE = ("nba", "nhl", "ncaab", "nfl", "soccer")
COMMANDS = ("daily", "trial", "status", "prepare", "holdout", "agent", "stop")
V2_SPORTS = ("nba", "nhl")

PROBE = r"""
import hashlib, json, sys
from importlib.metadata import distributions
from pathlib import Path
import app
from app import autoresearch as a
out = {"app": str(Path(a.__file__).resolve().parents[1]), "files": a.app_identity()["files"], "libraries": a.libraries(),
       "python": sys.executable,
       "packages": hashlib.sha256("\n".join(sorted(f"{d.metadata['Name'].lower()}=={d.version}" for d in distributions())).encode()).hexdigest(),
       "protocols": {}}
for s in json.loads(sys.argv[1]):
    p = out["protocols"][s] = {}
    if s in ("nba", "nhl"):
        try:
            p["v2"] = a.protocol_id({s: a.snapshot_identity(s)})
        except Exception as exc:
            p["v2_error"] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        ad = a.normalise_adapter(a.get_adapter(s))
        p["v3"] = a.v3_protocol_id(ad, a._data_id(ad, s))
    except Exception as exc:
        p["v3_error"] = f"{type(exc).__name__}: {exc}"[:200]
print("PROBE " + json.dumps(out))
"""


class Refuse(Exception):
    pass


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def app_files(backend):
    """The `files` hash of app.autoresearch.app_identity, computed without importing the app."""
    d = Path(backend) / "app"
    files = sorted(p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                   and not any(part.startswith(".") for part in p.relative_to(d).parts)
                   and p.suffix in (".py", ".json", ".sql", ".csv"))
    text = "\n".join("%s %s" % (p.relative_to(d), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def git(path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env).stdout


def constants(prepare):
    """The paths prepare.py will use: PY, BACKEND, SNAPSHOT (its `HOME / "..."` form resolves against its own HOME)."""
    text = Path(prepare).read_text()
    out = {}
    for name in ("PY", "BACKEND", "SNAPSHOT"):
        m = re.search(r'^%s = (?:str\(HOME / "([^"]+)"\)|"([^"]+)")$' % name, text, re.M)
        if not m:
            raise Refuse("prepare.py: cannot read %s" % name)
        out[name] = m.group(2) or str(Path.home() / "autoresearch-sports" / m.group(1))
    return out


def clean_env(pin):
    env = {k: os.environ[k] for k in ALLOW if k in os.environ}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def child_env(pin):
    """What prepare.py gives the evaluator, for the probe."""
    return dict(clean_env(pin), PYTHONPATH=pin["app"]["backend"], NBA_DATA_DIR=pin["snapshot"]["path"])


def probe(pin, sports):
    r = subprocess.run([pin["python"], "-c", PROBE, json.dumps(sports)], cwd=pin["app"]["backend"], env=child_env(pin),
                       capture_output=True, text=True, timeout=300)
    line = next((x for x in r.stdout.splitlines() if x.startswith("PROBE ")), None)
    if r.returncode != 0 or line is None:
        raise Refuse("the pinned code does not start: %s" % (r.stderr.strip()[-300:] or r.returncode))
    return json.loads(line[6:])


def load_pin():
    try:
        pin = json.loads(PIN.read_text())
        pin["app"]["backend"], pin["app"]["commit"], pin["app"]["files"], pin["python"], pin["libraries"], pin["packages"]
        pin["snapshot"]["path"], pin["snapshot"]["identity_sha256"], pin["workspace"]["prepare_sha256"], pin["workspace"]["path"]
        return pin
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refuse("no usable pin at %s (%s)" % (PIN, exc))


def verify(pin, sports, prepare):
    """Raises Refuse unless everything the run would use is exactly what PIN.json names. Returns the probe."""
    ws, a = Path(pin["workspace"]["path"]), pin["app"]
    if Path(prepare).resolve() != (ws / "prepare.py").resolve():
        raise Refuse("%s is not the pinned workspace's prepare.py" % prepare)
    for f in ("prepare", "program"):
        if sha(ws / ("%s.%s" % (f, "py" if f == "prepare" else "md"))) != pin["workspace"][f + "_sha256"]:
            raise Refuse("the workspace's %s file differs from the pin" % f)
    c = constants(ws / "prepare.py")
    if (c["BACKEND"], c["SNAPSHOT"], c["PY"]) != (a["backend"], pin["snapshot"]["path"], pin["python"]):
        raise Refuse("prepare.py names other paths than the pin")
    top = Path(git(a["backend"], "rev-parse", "--show-toplevel").strip())
    if top.resolve() != Path(a["backend"]).resolve().parent:
        raise Refuse("the pinned backend is not a checkout of its own")
    if git(top, "rev-parse", "HEAD").strip() != a["commit"]:
        raise Refuse("the pinned checkout is not at %s" % a["commit"][:12])
    if git(top, "status", "--porcelain").strip():
        raise Refuse("the pinned checkout has uncommitted changes")
    ignored = [x[3:] for x in git(top, "status", "--porcelain", "--ignored").splitlines() if x.startswith("!! ")]
    ignored = [x for x in ignored if "__pycache__" not in x and not x.split("/")[0].startswith(".omc") and "/.omc" not in x]
    if ignored:
        raise Refuse("the pinned checkout holds ignored files: %s" % ", ".join(ignored[:3]))
    if app_files(a["backend"]) != a["files"]:
        raise Refuse("the pinned app's files differ from the recorded identity %s" % a["files"])
    snap = Path(pin["snapshot"]["path"])
    if sha(snap / "IDENTITY.json") != pin["snapshot"]["identity_sha256"]:
        raise Refuse("the snapshot's IDENTITY.json differs from the pin")
    for s in sports:
        want, store = pin["snapshot"].get("stores", {}).get(s), snap / ("%s.sqlite" % s)
        if not want:
            raise Refuse("the pin holds no %s store" % s)
        if not store.is_file() or sha(store) != want:
            raise Refuse("the %s snapshot store differs from the pin" % s)
    p = probe(pin, sports)
    if Path(p["app"]).resolve() != Path(a["backend"]).resolve():
        raise Refuse("a fresh process loads %s, not the pinned %s" % (p["app"], a["backend"]))
    if p["files"] != a["files"]:
        raise Refuse("the loaded app's identity is %s, the pin says %s" % (p["files"], a["files"]))
    if p["libraries"] != pin["libraries"] or p["packages"] != pin["packages"]:
        raise Refuse("the interpreter's packages differ from the pin")
    for s in sports:
        want = pin.get("protocols", {}).get(s, {})
        if "v3" not in want or (s in V2_SPORTS and "v2" not in want):
            raise Refuse("the pin holds no %s study identity" % s)
        for k, v in want.items():
            if p["protocols"][s].get(k) != v:
                raise Refuse("the %s %s study identity is %s, the pin says %s" % (
                    s, k, p["protocols"][s].get(k) or p["protocols"][s].get(k + "_error"), v))
    return p


def audit_module():
    path = HERE.parent / "backend" / "app" / "research_audit.py"
    spec = importlib.util.spec_from_file_location("research_audit", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_audit(pin, sports, p):
    ra = audit_module()
    try:
        entries = ra.load(EXP)
    except ra.AuditError as exc:
        raise Refuse("the audit file is unusable: %s" % exc)
    head = pin.get("audit_head")
    if not head:
        raise Refuse("the pin names no audit entry")
    if head not in [e["sha"] for e in entries]:
        raise Refuse("the audit file no longer holds the entry the pin was written with")
    for s in sports:
        ids = p["protocols"][s]
        for k in ("v2", "v3"):
            if k == "v2" and s not in V2_SPORTS:
                continue
            pid = ids.get(k)
            if pid is None:
                e = next((x for x in entries if x["scope"]["sport"] == s and x["status"] != "verified"), None)
                if e:
                    raise Refuse("the %s %s study identity is unknown (%s) and the audit file blocks %s records" % (s, k, ids.get(k + "_error"), s))
                continue
            e = ra.verdict(entries, pid, s)
            if e is None and not pin.get("enforces_audit"):
                e = next((ra.verdict(entries, pid, s, x["scope"]["trial_id"]) for x in entries
                          if x["scope"]["protocol_id"] == pid and x["scope"]["sport"] == s and x["scope"].get("trial_id")
                          and ra.verdict(entries, pid, s, x["scope"]["trial_id"])), None)
                if e:
                    raise Refuse("%s: %s; the pinned evaluator cannot leave one trial out, so it may not run" % (s, ra.why(e)))
            if e:
                raise Refuse("%s %s: %s" % (s, k, ra.why(e)))


def log(rec):
    with (HOME_AR / "runner-runs.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def sports_of(argv):
    """The sports of `--sport S` arguments; any other spelling argparse would accept is refused, so nothing unverified can run."""
    for x in argv:
        if x.startswith("--s") and x != "--sport":
            raise Refuse("write --sport <sport> in full, got %s" % x)
    out = [argv[i + 1] for i, x in enumerate(argv[:-1]) if x == "--sport"]
    if any(x not in FIVE for x in out) or argv[-1:] == ["--sport"]:
        raise Refuse("unknown sport in %s" % argv)
    return out or list(FIVE)


def write_pin(argv):
    if "--reason" not in argv:
        raise Refuse("write-pin needs --reason")
    if PIN.exists():
        raise Refuse("%s exists; a pin is never overwritten (move it aside deliberately, with a recorded reason)" % PIN)
    ws = HOME_AR / "workspace"
    c = constants(ws / "prepare.py")
    top = Path(git(c["BACKEND"], "rev-parse", "--show-toplevel").strip())
    snap = Path(c["SNAPSHOT"])
    stores = {s: sha(snap / ("%s.sqlite" % s)) for s in ("nba", "nhl", "ncaab", "nfl", "soccer") if (snap / ("%s.sqlite" % s)).is_file()}
    pin = {"written_at": now(), "reason": argv[argv.index("--reason") + 1],
           "app": {"backend": c["BACKEND"], "commit": git(top, "rev-parse", "HEAD").strip(), "files": app_files(c["BACKEND"])},
           "python": c["PY"], "snapshot": {"path": c["SNAPSHOT"], "identity_sha256": sha(snap / "IDENTITY.json"), "stores": stores},
           "workspace": {"path": str(ws), "commit": git(ws, "rev-parse", "HEAD").strip(), "prepare_sha256": sha(ws / "prepare.py"),
                         "program_sha256": sha(ws / "program.md")}, "enforces_audit": False}
    p = probe(pin, list(stores))
    if p["files"] != pin["app"]["files"]:
        raise Refuse("the probe loaded %s" % p["app"])
    bad = {s: x for s, x in p["protocols"].items() if "v3" not in x or (s in V2_SPORTS and "v2" not in x)}
    if bad:
        raise Refuse("study identities could not be read: %s" % bad)
    entries = audit_module().load(EXP)
    if not entries:
        raise Refuse("write the audit first: the pin names its newest entry, so that deleting the audit is noticed")
    pin.update(libraries=p["libraries"], packages=p["packages"], audit_head=entries[-1]["sha"],
               protocols={s: {k: v for k, v in x.items() if k in ("v2", "v3")} for s, x in p["protocols"].items()})
    PIN.write_text(json.dumps(pin, indent=1, sort_keys=True) + "\n")
    PIN.chmod(0o444)
    print(json.dumps(pin, indent=1, sort_keys=True))


def run_child(cmd, pin, capture):
    """Runs the study in this process group with the inherited lock descriptors (app.learning_run supervises both); never leaves it
    running when this process ends early. Returns (rc, its sport-outcome lines)."""
    proc = subprocess.Popen(cmd, cwd=pin["app"]["backend"], env=clean_env(pin), close_fds=False,
                            stdout=subprocess.PIPE if capture else None)

    def end(signum, frame):
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, end)
    outcomes = []
    try:
        if capture:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace")
                sys.stdout.write(line)
                sys.stdout.flush()
                if line.startswith("sport-outcome: "):
                    outcomes.append(line.strip())
        return proc.wait(), outcomes
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main(argv):
    if argv[:1] == ["write-pin"]:
        write_pin(argv[1:])
        return 0
    if not argv:
        raise Refuse("usage: research_runner.py <prepare.py> <arguments> | verify | prepare-cache --sport S | write-pin --reason R")
    own = argv[0] in ("verify", "prepare-cache")
    ws = HOME_AR / "workspace" / "prepare.py"
    prepare, args = (str(ws), argv[1:]) if own else (argv[0], argv[1:])
    if Path(prepare).resolve() != ws.resolve():
        raise Refuse("%s is not the workspace's prepare.py" % prepare)
    if args[:1] == ["stop"]:                # the kill switch works whatever else is wrong
        return subprocess.run([sys.executable, "-I", prepare, *args], cwd=constants(prepare)["BACKEND"], env=clean_env(None)).returncode
    if not own and args[:1] and args[0] not in COMMANDS or not own and not args:
        raise Refuse("unknown command %s" % args[:1])
    if os.environ.get("AUTORESEARCH_BROKER"):
        raise Refuse("AUTORESEARCH_BROKER is set: the request would go to another process")
    label = os.environ.get("AUTORESEARCH_RUN_LABEL") or ("scheduled" if args[:1] == ["daily"] else "manual")
    base = {"label": label, "args": argv if own else args, "cwd": os.getcwd(), "pid": os.getpid()}
    try:
        sports = base["sports"] = sports_of(args)
        if args[:1] == ["agent"] and "--sport" not in args:
            sports = base["sports"] = list(V2_SPORTS)
        pin = load_pin()
        p = verify(pin, sports, prepare)
        check_audit(pin, sports, p)
    except Refuse as exc:
        log({**base, "event": "refused", "at": now(), "reason": str(exc)})
        raise
    except Exception as exc:                # nothing ran: a check that cannot be made is a refusal
        log({**base, "event": "refused", "at": now(), "reason": "%s: %s" % (type(exc).__name__, exc)})
        raise Refuse("%s: %s" % (type(exc).__name__, exc))
    ident = {"app": p["app"], "files": p["files"], "commit": pin["app"]["commit"], "packages": p["packages"][:16],
             "snapshot": pin["snapshot"]["path"], "protocols": p["protocols"], "label": label}
    if argv[0] == "verify":
        print(json.dumps(ident, indent=1, sort_keys=True))
        return 0
    print("research-runner: label=%s app=%s commit=%s files=%s" % (label, p["app"], pin["app"]["commit"][:12], p["files"]), flush=True)
    log({**base, "event": "start", "at": now(), **ident})
    if own:
        env = child_env(pin)
        rc, outcomes = subprocess.run([pin["python"], "-m", "app.autoresearch", "v3", "prepare-cache", "--exp-root", str(EXP), *args],
                                      cwd=pin["app"]["backend"], env=env, close_fds=False).returncode, []
    else:
        rc, outcomes = run_child([pin["python"], "-I", prepare, *args], pin, args[0] == "daily")
    problem = None
    try:
        check_audit(pin, sports, verify(pin, sports, prepare))
    except Exception as exc:
        problem = "changed while it ran: %s" % exc
    log({**base, "event": "end", "at": now(), "rc": rc, "outcomes": outcomes, **({"problem": problem} if problem else {}), **ident})
    if problem:
        raise Refuse(problem)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refuse as exc:
        print("research-runner: REFUSED: %s" % exc, file=sys.stderr)
        if sys.argv[2:3] == ["daily"]:
            why = str(exc).replace("(", "[").replace(")", "]").replace("\n", " ")
            named = [sys.argv[i + 1] for i, x in enumerate(sys.argv[:-1]) if x == "--sport" and sys.argv[i + 1] in FIVE]
            for s in named or FIVE:
                print("sport-outcome: %s refused v3=refused (runner: %s)" % (s, why))
        sys.exit(2)
