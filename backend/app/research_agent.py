"""Bounded agent loop for the sports autoresearch (app/autoresearch.py is the fixed evaluator).

Upstream karpathy/autoresearch has a person start a coding agent on program.md, which then loops edit train.py -> commit ->
run -> keep or reset. `run_agent` does that unattended and bounded:

* Setup (upstream's step with the human): checks the workspace is clean, registers or reuses the protocol, and creates a
  fresh branch autoresearch/<tag> from the workspace master.
* Starts ONE headless Claude Code session (`claude -p`) in the workspace on program.md, without bypassing permissions:
  restricted mode, a tool allowlist (edit train.py, a few git commands, `python prepare.py trial|status`), every other tool
  call denied without a prompt, a spending cap, and an OS sandbox (sandbox-exec) on the whole process tree that denies
  reading or writing every data copy, the live stores, the repository and the experiment log.
* The agent's trials cannot touch the data inside that sandbox, so `python prepare.py trial` hands the request to a broker in
  this (unsandboxed) driver over a unix socket. The broker accepts only `trial`/`status` with --sport, --description and
  --retry, then runs the fixed evaluator.
* Budgets live outside the agent. `trial_gate` (called by the evaluator for every trial) refuses a trial past the run's trial
  or time budget, after a stop, or when the driver is gone. The driver kills the agent's whole process tree at the deadline
  or on `stop`. One agent run at a time (lock); one trial at a time across sports (lock).
* Afterwards: interrupted trials are recorded, unevaluated commits on the branch are evaluated within what is left of the
  budget, and the run is appended to agent/runs.jsonl with its trials, decisions, stop reason and cost. The workspace goes
  back to master; uncommitted edits are saved as a diff, never evaluated.

Nothing here promotes anything: a keep is exploratory evidence against the incumbent on validation seasons. The holdout
stays a human one-shot (`holdout` is not reachable through the broker and asks at a terminal).
"""
import fcntl
import json
import os
import hashlib
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import autoresearch as ar
from . import config
from .research_status import proc_token as _proc_token

AGENT = {"max_trials": 3, "max_minutes": 90, "max_usd": 5.0, "launch_retries": 1, "launch_grace_s": 120, "kill_grace_s": 10,
         "min_trial_s": 30, "trial_timeout_s": 540}   # a trial must end inside one agent shell call (Claude Code's Bash limit is 10 minutes)
CLAUDE = str(Path.home() / ".local" / "bin" / "claude")
TOOLS = "Read,Edit,Grep,Glob,Bash"
ALLOWED_TOOLS = ("Read", "Grep", "Glob", "Edit(./train.py)",
                 "Bash(git status)", "Bash(git status --short)", "Bash(git diff)", "Bash(git diff HEAD)",
                 "Bash(git diff HEAD~1)", "Bash(git log --oneline -*)", "Bash(git show HEAD)", "Bash(git show *:train.py)",
                 "Bash(git add train.py)", "Bash(git commit *)", "Bash(git reset --hard HEAD~1)",
                 "Bash(git reset --hard *)", "Bash(python prepare.py trial *)", "Bash(python prepare.py status)",
                 "Bash(python prepare.py status *)")
# Defence in depth only (the OS sandbox is the boundary): options that write files or run configured programs.
DENIED_TOOLS = ("Bash(git * --output*)", "Bash(git * --ext-diff*)", "Bash(git * --exec*)", "Bash(git * -c *)",
                "Bash(git * --config*)", "Bash(git * --template*)", "Bash(git * -F*)", "Bash(git * --file*)", "Write",
                "NotebookEdit", "WebFetch", "WebSearch")
# The agent's environment is built from these names only (plus PATH and the broker socket), never the driver's whole one.
AGENT_ENV = ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "ANTHROPIC_API_KEY",
             "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "__CF_USER_TEXT_ENCODING")
# Under $HOME the agent reads only these, each required (verified 2026-10-14 with Claude Code 2.1.299, see AUTORESEARCH.md):
# the Claude Code install; the login keychain, where macOS keeps Claude Code's login (encrypted; items are released by
# securityd, and the agent's shell has no command to ask for them); git's user config (commit identity). It writes nothing
# under $HOME: the user's own Claude Code sessions load hooks and MCP commands from ~/.claude*, so those are never
# writable, and other sessions' transcripts there are not readable.
HOME_READ = (".local/bin", ".local/share/claude", "Library/Keychains", ".gitconfig", ".config/git")
HOME_WRITE = ()
# Inside the workspace's .git the agent may write only what commit/reset need; config, hooks, info, the master ref and the
# fixed files stay read-only, so nothing it writes is executed by the driver's or evaluator's own git calls.
GIT_WRITABLE = ("objects", "refs/heads/autoresearch", "logs", "index", "index.lock", "HEAD", "HEAD.lock", "ORIG_HEAD",
                "ORIG_HEAD.lock", "COMMIT_EDITMSG", "AUTO_MERGE", "AUTO_MERGE.lock", "packed-refs.lock")   # packed-refs itself: no
GIT_READONLY_INSIDE = ("objects/info",)          # alternates would let the repository borrow objects from elsewhere
BROKER_COMMANDS = ("trial", "status")


# --- Files ----------------------------------------------------------------------------------------------------------------
def agent_dir(exp_root: Path) -> Path:
    return exp_root / "agent"


def _read(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_atomic(p: Path, obj: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    tmp.replace(p)


def _log(exp_root: Path, rec: dict) -> None:
    d = agent_dir(exp_root)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "runs.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def runs(exp_root: Path) -> list:
    p = agent_dir(exp_root) / "runs.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.is_file() else []


def active(exp_root: Path) -> Optional[dict]:
    a = _read(agent_dir(exp_root) / "active.json")
    if a is not None:
        a["driver_alive"] = _proc_token(a["driver_pid"]) == a["driver_token"]
    return a


# --- Gate for every trial (called by ar.run_trial) ------------------------------------------------------------------------
class _Slot:
    """One trial at a time per experiment root, across sports (memory: one fit at a time on this machine)."""

    def __init__(self, exp_root: Path):
        exp_root.mkdir(parents=True, exist_ok=True)
        self.fh = (exp_root / "trial.lock").open("w")

    def __enter__(self):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise ar.Refused("another trial is running (concurrency limit 1 across sports)")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _run_trials(exp_root: Path, run_id: str) -> list:
    """Every trial (started record) that ran under an agent run, across sports and protocols."""
    out = []
    for p in sorted(exp_root.glob("*/*/trials.jsonl")):
        out += [r for r in ar._records(p.parent) if r.get("agent_run") == run_id]
    return out


def trial_gate(exp_root: Path) -> Optional[dict]:
    """None when no agent run is active; else the run's id and the time left. Refuses after a stop, past the run's budget, or
    when the driver that owns the run is gone (an orphaned agent cannot keep running trials)."""
    stop = agent_dir(exp_root) / "STOP"
    if stop.is_file():
        raise ar.Refused(f"research is stopped ({stop.read_text().strip()}); a person clears it with `stop --clear`")
    a = active(exp_root)
    if a is None:
        return None
    if not a["driver_alive"]:
        raise ar.Refused(f"agent run {a['run_id']} has no live driver; trials are refused until `stop --clear`")
    used = len({r["trial_id"] for r in _run_trials(exp_root, a["run_id"]) if r["event"] == "started"})
    if used >= a["max_trials"]:
        raise ar.Refused(f"agent run {a['run_id']}: trial budget spent ({used}/{a['max_trials']})")
    left = a["deadline"] - time.time()
    if left < a.get("min_trial_s", AGENT["min_trial_s"]):
        raise ar.Refused(f"agent run {a['run_id']}: time budget spent")
    return {"run_id": a["run_id"], "timeout_s": int(min(left, a["trial_timeout_s"]))}


# --- Stop ---------------------------------------------------------------------------------------------------------------
def descendants(pid: int) -> list:
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout.split()
    kids = {}
    for c, p in zip(out[::2], out[1::2]):
        kids.setdefault(int(p), []).append(int(c))
    todo, seen = [pid], []
    while todo:
        for c in kids.get(todo.pop(), []):
            if c not in seen:
                seen.append(c)
                todo.append(c)
    return seen


def kill_tree(pid: int, grace_s: float) -> list:
    """SIGTERM the process, its group and every descendant (also ones in their own session), then SIGKILL what is left."""
    tree = [pid, *descendants(pid)]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        tree = sorted(set(tree) | {d for p in tree for d in descendants(p)})
        for p in tree:
            try:
                os.kill(p, sig)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        end = time.time() + (grace_s if sig == signal.SIGTERM else 2)
        while time.time() < end and any(_alive(p) for p in tree):
            time.sleep(0.2)
    return [p for p in tree if _alive(p)]


def _alive(pid: int) -> bool:
    try:
        r, _ = os.waitpid(pid, os.WNOHANG)              # reap our own children so they stop counting as alive
        if r == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def stop(exp_root: Path, reason: str = "stopped by a person", clear: bool = False) -> dict:
    """Kill switch. Writes STOP (refuses every trial and new run until cleared) and signals the driver of the active run,
    which kills the agent's process tree. Without a live driver, kills the recorded agent tree itself."""
    d = agent_dir(exp_root)
    d.mkdir(parents=True, exist_ok=True)
    stop_file = d / "STOP"
    if clear:
        a = active(exp_root)
        if a and a["driver_alive"]:
            raise ar.Refused(f"agent run {a['run_id']} is still running; stop it first")
        if a:                                       # a driver that died without cleaning up
            _log(exp_root, {"event": "abandoned", "run_id": a["run_id"], "at": ar._now(), "reason": "cleared after the driver died"})
            (d / "active.json").unlink()
        stop_file.unlink(missing_ok=True)
        return {"stopped": False, "cleared": True}
    stop_file.write_text(f"{ar._now()} {reason}\n")
    a = active(exp_root)
    out = {"stopped": True, "stop_file": str(stop_file), "active_run": a and a["run_id"]}
    if a and a["driver_alive"]:
        os.kill(a["driver_pid"], signal.SIGTERM)
        out["signalled_driver"] = a["driver_pid"]
    elif a and a.get("agent_pid") and _proc_token(a["agent_pid"]) == a.get("agent_token"):
        out["left_alive"] = kill_tree(a["agent_pid"], AGENT["kill_grace_s"])
    return out


# --- Agent sandbox --------------------------------------------------------------------------------------------------------
def _slug(p: Path) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(p))


def claude_tmp(workspace: Path) -> Path:
    """Where Claude Code keeps a session's task output for this working directory."""
    return Path(f"/private/tmp/claude-{os.getuid()}") / _slug(workspace.resolve())


def protected_paths(exp_root: Path) -> list:
    """Locations outside $HOME that the agent may neither read nor write (everything under $HOME is denied unless listed
    in HOME_READ/HOME_WRITE): the data dir, the experiment log, the live stores and release, the repository, and other
    sessions' temp files."""
    paths = {config.data_dir(), exp_root, *ar.live_roots(), ar.REPO_ROOT, Path(f"/private/tmp/claude-{os.getuid()}"),
             Path("/Volumes")}
    return sorted({str(Path(p).resolve()) for p in paths})


def _regex_literal(text: str) -> str:
    """A path as a literal inside a sandbox (POSIX) regex: every character but [A-Za-z0-9/_-] in a bracket expression."""
    return "".join(c if c.isalnum() or c in "/_-" else ("\\^" if c == "^" else f"[{c}]") for c in text)


ATTRIBUTES_ANY_CASE = "[.]" + "".join(f"[{c.lower()}{c.upper()}]" for c in "gitattributes")


def agent_profile(exp_root: Path, workspace: Path, sock: str, bin_dir: Optional[Path] = None) -> str:
    """Deny everything under $HOME, the protected paths and the network; allow back only what the agent needs. `bin_dir`
    (the run's `python` shim, inside the experiment root) is readable, never writable."""
    q = lambda p: json.dumps(str(p))                                    # noqa: E731
    sub = lambda ps: " ".join(f"(subpath {q(p)})" for p in ps)          # noqa: E731
    home, ws = Path.home().resolve(), workspace.resolve()
    denied = [str(home), *protected_paths(exp_root)]
    readable = sorted({str(ar.APP_DIR), sys.prefix, sys.base_prefix, str(ws), str(claude_tmp(workspace)),
                       *(str(home / h) for h in HOME_READ), *([str(bin_dir.resolve())] if bin_dir else [])})
    writable = sorted({str(ws), str(claude_tmp(workspace)), *(str(home / h) for h in HOME_WRITE)})
    git_ok = [str(ws / ".git" / g) for g in GIT_WRITABLE]
    return "\n".join([
        "(version 1)", "(allow default)",
        f"(deny file-read* {sub(denied)})",
        "(allow file-read-metadata)",
        f"(allow file-read* {sub(readable)})",
        f"(allow file-read* (literal {q(claude_tmp(workspace).parent)}) (literal {q(home)}))",   # directory entries only
        f"(deny file-write* {sub(denied + [str(ar.APP_DIR.parent.parent)])})",
        f"(allow file-write* {sub(writable)})",
        f"(deny file-write* (subpath {q(ws / '.git')}) (literal {q(ws / 'prepare.py')}) (literal {q(ws / 'program.md')}))",
        f"(allow file-write* {sub(git_ok)})",
        f"(deny file-write* {sub([str(ws / '.git' / g) for g in GIT_READONLY_INSIDE])} (regex {q('^' + _regex_literal(str(ws)) + '/(.*/)?' + ATTRIBUTES_ANY_CASE + '$')}))",
        "(deny network*)",
        '(allow network-outbound (remote tcp "*:443"))',                   # the model API; nothing else
        '(allow network-outbound (literal "/private/var/run/mDNSResponder"))',
        f"(allow network-outbound (literal {q(sock)}))",
        f"(allow file-read* file-write* (literal {q(sock)}))"]) + "\n"


def claude_command(claude: str, usd: float, prompt: str, model: Optional[str]) -> list:
    """Headless Claude Code. No bypass: restricted mode, an allowlist, and every other permission request denied."""
    cmd = [claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--restricted", "--tools", TOOLS,
           "--allowedTools", *ALLOWED_TOOLS, "--disallowedTools", *DENIED_TOOLS, "--permission-mode", "default",
           "--permission-prompts", "none", "--max-budget-usd", f"{usd:.2f}", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence", "--add-dir", str(ar.APP_DIR)]
    return cmd + (["--model", model] if model else [])


# --- Broker (runs the fixed evaluator for the sandboxed agent) -----------------------------------------------------------
def parse_request(argv: list) -> list:
    """Only `trial`/`status` with --sport, --description and --retry; every other option (experiment root, workspace,
    timeout, holdout, prepare, daily, agent, stop) is refused."""
    if not argv or argv[0] not in BROKER_COMMANDS:
        raise ar.Refused(f"only {BROKER_COMMANDS} are available to the agent")
    out, it = [argv[0]], iter(argv[1:])
    for a in it:
        if a == "--retry" and argv[0] == "trial":
            out.append(a)
        elif a in ("--sport", "--description"):
            v = next(it, None)
            if v is None or (a == "--sport" and v not in ("nba", "nhl")):
                raise ar.Refused(f"bad value for {a}")
            out += [a, v]
        else:
            raise ar.Refused(f"option {a!r} is not available to the agent")
    return out


class Broker:
    """Unix-socket server: one request at a time, one JSON line in ({"argv": [...]}) and one out ({"exit", "output"})."""

    def __init__(self, path: str, handle: Callable[[list], tuple]):
        self.path, self.handle = path, handle
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        Path(path).unlink(missing_ok=True)
        self.sock.bind(path)
        os.chmod(path, 0o600)
        self.sock.listen(4)
        self.requests = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    buf = b""
                    while not buf.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                    argv = json.loads(buf)["argv"]
                    rc, out = self.handle(argv)
                except Exception as exc:                  # a bad request is answered, never fatal to the driver
                    argv, rc, out = None, 2, f"REFUSED: {type(exc).__name__}: {exc}"
                self.requests.append({"at": ar._now(), "argv": argv, "exit": rc})
                try:
                    conn.sendall((json.dumps({"exit": rc, "output": out}) + "\n").encode())
                except OSError:
                    pass

    def close(self):
        self.sock.close()
        Path(self.path).unlink(missing_ok=True)
        shutil.rmtree(Path(self.path).parent, ignore_errors=True)        # the run's private socket directory


def request(path: str, argv: list, timeout_s: float = 900) -> tuple:
    """Client side (the workspace prepare.py has a copy of this, standard library only)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout_s)
        s.connect(path)
        s.sendall((json.dumps({"argv": argv}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    r = json.loads(buf)
    return r["exit"], r["output"]


class Evaluator:
    """Broker handler: runs the fixed evaluator CLI outside the agent's sandbox. `kill()` stops a trial in flight (on a stop
    or at the deadline) so that it is recorded as interrupted."""

    def __init__(self, exp_root: Path, workspace: Path, python: str, backend: Path, data_dir: Path):
        self.exp_root, self.workspace, self.python, self.backend, self.data_dir = exp_root, workspace, python, backend, data_dir
        self.current = None
        self.closed = False

    def __call__(self, argv):
        try:
            argv = parse_request(argv)
        except ar.Refused as exc:
            return 2, f"REFUSED: {exc}"
        if self.closed:
            return 2, "REFUSED: the agent run is ending"
        env = {**{k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NBA_DATA_DIR")},
               "NBA_DATA_DIR": str(self.data_dir), "PYTHONPATH": str(self.backend), "OMP_NUM_THREADS": "4"}
        self.current = subprocess.Popen([self.python, "-m", "app.autoresearch", *argv, "--exp-root", str(self.exp_root),
                                         "--workspace", str(self.workspace)], cwd=str(self.backend), env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        out, _ = self.current.communicate()
        rc, self.current = self.current.returncode, None
        return rc, out[-20000:]

    def kill(self) -> list:
        self.closed = True
        proc = self.current
        return kill_tree(proc.pid, AGENT["kill_grace_s"]) if proc and proc.poll() is None else []


# --- The run --------------------------------------------------------------------------------------------------------------
def build_prompt(branch: str, sports_: list, budget: dict, status_: dict) -> str:
    return f"""You are running the autoresearch experiment loop in this directory, unattended. Read program.md and follow its
experiment loop. Setup is already done by the human's driver: the branch {branch} exists and is checked out, the protocol is
prepared. Do not create or switch branches, and do not run prepare, daily, holdout or anything else.

Budget, enforced outside your control: at most {budget['max_trials']} trials and {budget['max_minutes']:.0f} minutes in this
run; a trial beyond it is REFUSED. When a trial is REFUSED for the budget or a stop, stop and write the summary.
Sports in scope: {', '.join(sports_)}.

Only these shell commands are permitted, one per call (no `&&`): `git add train.py`, then `git commit -m "<one line>"`;
git status/diff/log --oneline/show/reset --hard; and
`python prepare.py trial --sport <nba|nhl> [--description "..."]` and `python prepare.py status`. Run a trial WITHOUT output
redirection and read its printed summary (the evaluator keeps the full log). Edit only train.py. Commit each idea before its
trial with a one-line message that states the hypothesis. Every other tool use is denied.

Current state (incumbents per market and trial counts): {json.dumps(status_, sort_keys=True)}

Finish with a short per-market summary: what you tried, what was kept or rejected, and why. "No improvement found" is a
valid result."""


def _parse_stream(p: Path) -> dict:
    out = {"result": None, "events": 0, "tool_uses": 0}
    if not p.is_file():
        return out
    for line in p.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        out["events"] += 1
        if e.get("type") == "assistant":
            out["tool_uses"] += sum(1 for c in (e.get("message") or {}).get("content", []) if c.get("type") == "tool_use")
        if e.get("type") == "result":
            out["result"] = {k: e.get(k) for k in ("subtype", "is_error", "num_turns", "total_cost_usd", "duration_ms",
                                                   "permission_denials", "result")}
    return out


class _Lock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("w")

    def __enter__(self):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise ar.Refused("another agent run is in progress (one at a time)")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def run_agent(sports_: list, exp_root: Path, workspace: Path, max_trials: int = AGENT["max_trials"],
              max_minutes: float = AGENT["max_minutes"], max_usd: float = AGENT["max_usd"], model: Optional[str] = None,
              claude: str = CLAUDE, command: Optional[Callable[[str, float], list]] = None, python: str = sys.executable,
              sandbox: bool = True) -> dict:
    """One bounded agent run. `command(prompt, usd)` builds one attempt's agent argv with that attempt's spending cap
    (default: headless Claude Code); tests pass a script."""
    data_dir = ar.guard_paths(exp_root)
    d = agent_dir(exp_root)
    d.mkdir(parents=True, exist_ok=True)
    _check_stop(d)
    with _Lock(d / "agent.lock"):
        _check_stop(d)
        a = active(exp_root)
        if a:
            raise ar.Refused(f"agent run {a['run_id']} left no terminal record; inspect it, then `stop --clear`")
        for s in sports_:                                 # an existing protocol's workspace git identity, before any git call
            ar.registered(s, exp_root, workspace)
        dirty = [x for x in ar._git(workspace, "status", "--porcelain", "--untracked-files=no").splitlines() if x.strip()]
        if dirty:
            raise ar.Refused(f"workspace has uncommitted changes {dirty}")
        ar._git(workspace, "checkout", "-q", "master")
        base = ar._git(workspace, "rev-parse", "HEAD")
        metas = {s: ar.prepare(s, exp_root, workspace) for s in sports_}   # may take minutes
        _check_stop(d)                                    # a stop pressed during setup starts nothing
        stale = {s: m["workspace_base"][:7] for s, m in metas.items() if m["workspace_base"] != base}
        if stale:
            raise ar.Refused(f"workspace master {base[:7]} is not the registered protocol base {stale}")
        t0 = time.time()
        run_id = f"{ar._now().replace(':', '').replace('-', '')}-{secrets.token_hex(2)}"
        branch = f"autoresearch/agent-{run_id}"
        ar._git(workspace, "checkout", "-q", "-b", branch, base)
        rdir = d / "runs" / run_id
        rdir.mkdir(parents=True)
        budget = {"max_trials": max_trials, "max_minutes": max_minutes, "max_usd": max_usd,
                  "trial_timeout_s": min(AGENT["trial_timeout_s"], ar.BUDGETS["trial_timeout_s"])}
        status_ = {s: ar.status(exp_root, s) for s in sports_}
        prompt = build_prompt(branch, sports_, budget, {s: {k: v[k] for k in ("incumbents", "by_status")}
                                                       for s, v in status_.items()})
        (rdir / "prompt.txt").write_text(prompt)
        sock = str(Path(tempfile.mkdtemp(prefix=f"ar-{secrets.token_hex(8)}-", dir="/private/tmp")) / "broker.sock")
        per_attempt = round(max_usd / (1 + AGENT["launch_retries"]), 2)   # one cap shared by the launch and its retry
        build = command or (lambda p, usd: claude_command(claude, usd, p, model))
        argv = build(prompt, per_attempt)
        version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip() if command is None else "test"
        state = {"run_id": run_id, "branch": branch, "base": base, "sports": sports_, "git_config": ar.workspace_git_sha(workspace),
                 "protocols": {s: m["protocol_id"] for s, m in metas.items()}, "max_trials": max_trials,
                 "deadline": t0 + max_minutes * 60, "trial_timeout_s": budget["trial_timeout_s"], "min_trial_s": AGENT["min_trial_s"], "driver_pid": os.getpid(),
                 "driver_token": _proc_token(os.getpid()), "agent_pid": None, "socket": sock, "started_at": ar._now()}
        _write_atomic(d / "active.json", state)
        _log(exp_root, {"event": "started", "run_id": run_id, "at": state["started_at"], "branch": branch, "base": base,
                        "budget": budget, "sports": sports_, "protocols": state["protocols"], "agent": version,
                        "model": model, "sandbox": sandbox, "argv": [x if x != prompt else "<prompt.txt>" for x in argv]})
        terminated = []
        old = signal.signal(signal.SIGTERM, lambda *_: terminated.append(ar._now()))
        evaluator = Evaluator(exp_root, workspace, python, ar.APP_DIR.parent, data_dir)
        broker = Broker(sock, evaluator)
        report = {"run_id": run_id, "branch": branch, "base": base, "budget": budget | {"usd_per_attempt": per_attempt},
                  "attempts": []}
        try:
            report |= _supervise(argv, workspace, exp_root, rdir, state, terminated, sandbox, sock,
                                 lambda: build(prompt, per_attempt))
        finally:
            report["trial_left_alive"] = evaluator.kill()        # a trial still running when the agent is gone
            broker.close()
            report["broker_requests"] = broker.requests
            try:                                                  # a stop during the finish is recorded, not fatal
                report |= _finish(exp_root, workspace, rdir, state, sports_, report, t0, terminated)
            finally:
                signal.signal(signal.SIGTERM, old)
        return report


def _check_stop(d: Path) -> None:
    if (d / "STOP").is_file():
        raise ar.Refused(f"research is stopped ({(d / 'STOP').read_text().strip()}); a person clears it with `stop --clear`")





def _supervise(argv, workspace, exp_root, rdir, state, terminated, sandbox, sock, rebuild) -> dict:
    d = agent_dir(exp_root)
    attempts = []
    stop_reason = None
    tmp = claude_tmp(workspace)
    tmp.mkdir(parents=True, exist_ok=True)
    (rdir / "bin").mkdir()
    profile = rdir / "agent.sb"
    profile.write_text(agent_profile(exp_root, workspace, sock, rdir / "bin"))
    env = {k: os.environ[k] for k in AGENT_ENV if k in os.environ}           # explicit, never the driver's whole env
    env |= {"AUTORESEARCH_BROKER": sock, "PATH": f"{rdir / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin"}
    shim = rdir / "bin" / "python"                                 # `python prepare.py ...` as program.md says (a symlink
    shim.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"\n')      # fails: macOS's python3 stub resolves its own name)
    shim.chmod(0o755)
    for attempt in range(1 + AGENT["launch_retries"]):
        if attempt:
            argv = rebuild()
        out = rdir / f"agent-{attempt + 1}.jsonl"
        launched = time.time()
        with out.open("wb") as fo, (rdir / f"agent-{attempt + 1}.err").open("wb") as fe:
            proc = subprocess.Popen(([ar.SANDBOX, "-f", str(profile)] if sandbox else []) + argv, cwd=str(workspace),
                                    stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, env=env, start_new_session=True)
        state |= {"agent_pid": proc.pid, "agent_token": _proc_token(proc.pid)}
        _write_atomic(d / "active.json", state)
        reason = None
        while proc.poll() is None:
            if terminated or (d / "STOP").is_file():
                reason = "stopped"
            elif time.time() >= state["deadline"]:
                reason = "time budget reached"
            if reason:
                left = kill_tree(proc.pid, AGENT["kill_grace_s"])
                proc.wait()
                if left:
                    reason += f" (processes still alive: {left})"
                break
            time.sleep(1)
        parsed = _parse_stream(out)
        try:
            trials = len({r["trial_id"] for r in _run_trials(exp_root, state["run_id"]) if r["event"] == "started"})
        except Exception:                                 # an unreadable log: counted as "unknown", never as a launch failure
            trials = None
        attempts.append({"attempt": attempt + 1, "exit": proc.returncode, "seconds": round(time.time() - launched, 1),
                         "killed": reason, "trials_so_far": trials, "result": parsed["result"],
                         "tool_uses": parsed["tool_uses"], "transcript": str(out)})
        stop_reason = reason
        launch_failed = proc.returncode != 0 and not reason and trials == 0 and time.time() - launched < AGENT["launch_grace_s"]
        if not launch_failed:
            break
        stop_reason = "agent failed to start"
        time.sleep(1)
    return {"attempts": attempts, "stopped": stop_reason or "agent finished"}


def _finish(exp_root, workspace, rdir, state, sports_, report, t0, terminated=()) -> dict:
    run_id = state["run_id"]
    out = {"interrupted": [], "sweep": None, "errors": []}
    git_safe = ar.workspace_git_sha(workspace) == state["git_config"]
    if not git_safe:                                      # never run git in a workspace whose configuration changed: stop
        out["errors"].append(".git/config changed during the run; no git command was run in the workspace afterwards")
        (agent_dir(exp_root) / "STOP").write_text(f"{ar._now()} workspace .git configuration changed during run {run_id}\n")
    for s in sports_:                                     # a trial killed with the agent gets its terminal record now
        root = exp_root / state["protocols"][s] / s
        try:
            with ar.Lock(root):
                out["interrupted"] += [r["trial_id"] for r in ar.mark_interrupted(root)]
        except ar.Refused:
            pass
        except Exception as exc:                          # e.g. an unreadable log: recorded; the run still ends cleanly
            out["errors"].append(f"{s} interrupted-trial check: {type(exc).__name__}: {exc}"[:300])
    if report.get("stopped") != "stopped" and git_safe and not terminated:   # proposals committed but not run (budget permitting)
        left = state["deadline"] - time.time()
        if left > 30:
            try:
                out["sweep"] = {k: v for k, v in ar.daily(sports_, exp_root, workspace, max_trials=state["max_trials"],
                                                          max_minutes=left / 60, timeout_s=state["trial_timeout_s"],
                                                          ref=state["branch"]).items() if k in ("outcome", "stopped", "trials", "refused")}
            except ar.Refused as exc:
                out["sweep"] = {"refused": str(exc)}
            except Exception as exc:
                out["errors"].append(f"sweep: {type(exc).__name__}: {exc}"[:300])
    if git_safe:
        diff = ar._git(workspace, "diff", "HEAD", "--no-ext-diff", "--no-textconv")   # staged and unstaged edits
        if diff:
            (rdir / "uncommitted.diff").write_text(diff + "\n")
            out["uncommitted_diff"] = str(rdir / "uncommitted.diff")
        ar._git(workspace, "reset", "-q", "--hard", "HEAD")      # index and working tree: nothing is carried to master
        ar._git(workspace, "checkout", "-q", "master")
    try:
        recs = [r for r in _run_trials(exp_root, run_id) if r["event"] == "finished"]
    except Exception as exc:
        recs = []
        out["errors"].append(f"trial log unreadable: {type(exc).__name__}: {exc}"[:300])
    out["trials"] = [{"trial_id": r["trial_id"], "sport": r["sport"], "commit": r["commit"][:7], "status": r["status"],
                      "description": r.get("description"), "reason": r.get("reason"),
                      "markets": {m: {"decision": s["decision"], "delta": (s.get("vs_incumbent") or {}).get("mean"),
                                      "ci95": (s.get("vs_incumbent") or {}).get("ci95")}
                                  for m, s in (r.get("markets") or {}).items()}} for r in recs]
    kept = [f"{t['sport']}:{m}" for t in out["trials"] for m, s in t["markets"].items() if s["decision"] == "keep"]
    out["outcome"] = (f"kept vs incumbent (exploratory, not a promotion): {', '.join(kept)}" if kept
                      else "no improvement found")
    out["commits"] = (ar._git(workspace, "log", "--format=%h %s", f"{state['base']}..{state['branch']}").splitlines()
                      if git_safe else None)
    if terminated:
        out["errors"].append(f"stop signal during the finish at {terminated[-1]}; remaining evaluation skipped")
    costs = [(a.get("result") or {}).get("total_cost_usd") for a in report.get("attempts", [])]
    out["cost_usd"] = sum(costs) if costs and None not in costs else None     # a killed agent reports none: unknown, not 0
    if out["cost_usd"] is None:
        out["cost_note"] = "unknown: the agent ended without reporting its cost (its --max-budget-usd cap still applied)"
    out["finished_at"] = ar._now()
    out["minutes"] = round((time.time() - t0) / 60, 1)
    full = {**report, **out}
    (rdir / "report.json").write_text(json.dumps(full, indent=1, default=str))
    _log(exp_root, {"event": "finished", "run_id": run_id, "at": out["finished_at"], "stopped": report.get("stopped"),
                    "outcome": out["outcome"] if not out["errors"] else f"{out['outcome']} (with errors: {out['errors']})", "trials": len(out["trials"]), "cost_usd": out["cost_usd"],
                    "report": str(rdir.relative_to(exp_root) / "report.json")})
    (agent_dir(exp_root) / "active.json").unlink(missing_ok=True)
    return out
