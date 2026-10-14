"""Bounded agent loop (app/research_agent.py) around the sports autoresearch evaluator.

The agent here is a scripted stand-in for `claude -p` (a Python script following a plan), started exactly like the real
one: under the agent sandbox profile, in the workspace, with the broker socket. Its trials go through the broker into the
real evaluator (sandboxed fits on the synthetic fixture of test_autoresearch). One test checks the real Claude Code argv.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import autoresearch as ar
from app import research_agent as ra
from test_autoresearch import BASELINE, OUT, edit, env, git, records  # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.skipif(not Path(ar.SANDBOX).is_file(), reason="needs macOS sandbox-exec")

AGENT_SCRIPT = r'''
import json, os, socket, subprocess, sys, time
plan = json.loads(sys.argv[1])

def broker(argv):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(os.environ["AUTORESEARCH_BROKER"])
        s.sendall((json.dumps({"argv": argv}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            c = s.recv(65536)
            if not c:
                break
            buf += c
    return json.loads(buf)

def git(*a):
    return subprocess.run(["git", "-c", "user.name=agent", "-c", "user.email=a@a", *a], capture_output=True, text=True)

for step in plan:
    op, arg = step[0], step[1] if len(step) > 1 else None
    if op == "commit":
        open("train.py", "w").write(arg)
        git("commit", "-qam", step[2])
    elif op == "dirty":
        open("train.py", "a").write("\n# not committed\n")
    elif op == "broker":
        r = broker(arg)
        print(json.dumps({"type": "tool", "argv": arg, "exit": r["exit"], "output": r["output"][-3000:]}), flush=True)
    elif op == "read":
        try:
            open(arg, "rb").read(1)
            print(json.dumps({"type": "probe", "op": "read", "path": arg, "ok": True}), flush=True)
        except OSError as exc:
            print(json.dumps({"type": "probe", "op": "read", "path": arg, "ok": False, "err": exc.errno}), flush=True)
    elif op == "write":
        try:
            open(arg, "w").write("x")
            print(json.dumps({"type": "probe", "op": "write", "path": arg, "ok": True}), flush=True)
        except OSError as exc:
            print(json.dumps({"type": "probe", "op": "write", "path": arg, "ok": False, "err": exc.errno}), flush=True)
    elif op == "sleeper":
        p = subprocess.Popen(["/bin/sleep", "600"], start_new_session=True)
        open(arg, "w").write(str(p.pid))
    elif op == "shell":
        r = subprocess.run(arg, shell=True, capture_output=True, text=True)
        print(json.dumps({"type": "tool", "argv": arg, "exit": r.returncode, "output": (r.stdout + r.stderr)[-3000:]}), flush=True)
    elif op == "env":
        print(json.dumps({"type": "probe", "op": "env", "keys": sorted(os.environ)}), flush=True)
    elif op == "connect":
        try:
            socket.create_connection(("127.0.0.1", arg), timeout=3).close()
            print(json.dumps({"type": "probe", "op": "connect", "path": arg, "ok": True}), flush=True)
        except OSError as exc:
            print(json.dumps({"type": "probe", "op": "connect", "path": arg, "ok": False, "err": exc.errno}), flush=True)
    elif op == "sleep":
        time.sleep(arg)
    elif op == "exit":
        sys.exit(arg)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": len(plan), "total_cost_usd": 0.0,
                  "permission_denials": [], "result": "done"}), flush=True)
'''


@pytest.fixture
def agent(env, tmp_path, monkeypatch):
    git(env["ws"], "branch", "-M", "master")
    assert ar.run_trial("nba", env["exp"], env["ws"])["status"] == "keep"     # the baseline, before any agent run
    script = tmp_path / "agent.py"          # outside the workspace (a stand-in for the claude binary)
    script.write_text(AGENT_SCRIPT)
    monkeypatch.setitem(ra.AGENT, "min_trial_s", 1)
    monkeypatch.setitem(ra.AGENT, "kill_grace_s", 2)

    budgets = []

    def go(plan, **kw):
        kw.setdefault("max_trials", 3)
        kw.setdefault("max_minutes", 5)

        def command(prompt, usd):
            budgets.append(usd)
            return [sys.executable, str(script), json.dumps(plan)]
        return ra.run_agent(["nba"], env["exp"], env["ws"], command=command, **kw)
    return {**env, "go": go, "budgets": budgets, "tmp": tmp_path}


def tool_lines(report):
    out = []
    for a in report["attempts"]:
        for line in Path(a["transcript"]).read_text().splitlines():
            e = json.loads(line)
            if e["type"] in ("tool", "probe"):
                out.append(e)
    return out


SLOW = edit("return engine.fit(spec, rows)", "for _ in range(10 ** 10):\n        pass\n    return engine.fit(spec, rows)")


WORSE = edit(OUT, "o = list(engine.outputs(spec, model, rows, sd))\n    return [x if spec.head != 'binary' else "
                  "{**x, 'p': 0.5} for x in o]")


# --- The loop: proposal -> real fit -> fixed evaluation -> recorded decision -----------------------------------------
def test_an_agent_run_proposes_runs_and_records_each_decision(agent):
    rep = agent["go"]([("commit", WORSE, "winner at 0.5: expect worse"), ("broker", ["trial", "--sport", "nba"])])
    assert rep["stopped"] == "agent finished" and rep["attempts"][0]["exit"] == 0, rep
    assert len(rep["trials"]) == 1, json.dumps(rep, default=str)[:5000]
    [t] = rep["trials"]
    assert t["status"] == "discard" and t["description"] == "winner at 0.5: expect worse"
    assert t["markets"]["winner"]["decision"] == "reject" and t["markets"]["winner"]["delta"] > 0
    recs = [r for r in records(agent["root"]) if r.get("agent_run") == rep["run_id"]]
    assert [r["event"] for r in recs] == ["started", "finished"] and recs[1]["fits"]          # really fitted
    assert rep["commits"] == [f"{t['commit']} winner at 0.5: expect worse"]
    log = ra.runs(agent["exp"])
    assert [r["event"] for r in log] == ["started", "finished"] and log[1]["outcome"] == "no improvement found"
    assert git(agent["ws"], "rev-parse", "--abbrev-ref", "HEAD") == "master"
    assert git(agent["ws"], "status", "--porcelain", "--untracked-files=no") == ""
    assert rep["branch"] in git(agent["ws"], "branch", "--list", "autoresearch/*")
    assert not (agent["exp"] / "agent" / "active.json").exists()
    assert ra.trial_gate(agent["exp"]) is None                 # no active run: manual trials are not gated any more


def test_commits_the_agent_did_not_run_are_evaluated_after_it_ends(agent):
    rep = agent["go"]([("commit", WORSE, "proposal never run by the agent")])
    assert [t["description"] for t in rep["trials"]] == ["proposal never run by the agent"]
    assert rep["sweep"]["trials"][0]["status"] == "discard"


def test_the_agent_shell_runs_python_from_path_like_program_md_says(agent):
    # Regression (real run 20261014T133022Z-cd1c): the `python` shim sat in the run directory, which the sandbox denies, so
    # every `python prepare.py ...` failed with "Operation not permitted" and the agent could not run a single trial.
    rep = agent["go"]([("shell", "python -c 'print(41 + 1)'"), ("shell", 'echo "exit 0" > "$(command -v python)"')])
    run_python, tamper = tool_lines(rep)
    assert run_python["exit"] == 0 and run_python["output"].strip() == "42", run_python
    assert tamper["exit"] != 0 and "not permitted" in tamper["output"]        # the shim is readable, never writable


# --- Budgets, stop, timeout, retries, concurrency --------------------------------------------------------------------
def test_trials_beyond_the_run_budget_are_refused_by_the_evaluator(agent):
    rep = agent["go"]([("commit", WORSE, "first"), ("broker", ["trial", "--sport", "nba"]),
                       ("commit", WORSE + "\n# second\n", "second"), ("broker", ["trial", "--sport", "nba"])], max_trials=1)
    tools = tool_lines(rep)
    assert tools[0]["exit"] == 0 and tools[1]["exit"] == 2 and "trial budget spent (1/1)" in tools[1]["output"], tools
    started = [r for r in records(agent["root"]) if r.get("agent_run") == rep["run_id"] and r["event"] == "started"]
    assert len(started) == 1
    assert "trial budget spent" in json.dumps(rep["sweep"])     # the unrun second commit is not evaluated either
    assert [t["description"] for t in rep["trials"]] == ["first"]


def test_the_deadline_kills_the_agent_tree_and_the_trial_in_flight(agent, tmp_path):
    pidfile = tmp_path / "sleeper.pid"
    rep = agent["go"]([("sleeper", str(pidfile)), ("commit", SLOW, "slow fit"), ("broker", ["trial", "--sport", "nba"]),
                       ("sleep", 600)], max_minutes=0.25)
    assert rep["stopped"].startswith("time budget reached") and "still alive" not in rep["stopped"]
    sleeper = int(pidfile.read_text())
    assert not ra._alive(sleeper)                               # a grandchild in its own session is gone too
    [t] = rep["trials"]
    assert t["status"] == "timeout" and t["description"] == "slow fit"     # the trial's limit is capped at the run deadline
    assert rep["minutes"] < 1.5


def test_a_stop_during_a_trial_kills_it_and_records_it_interrupted(agent):
    def press_stop_mid_trial():
        for _ in range(600):
            if any(r["event"] == "started" and r.get("agent_run") for r in records(agent["root"])):
                time.sleep(2)
                ra.stop(agent["exp"], "test stop mid-trial")
                return
            time.sleep(0.1)
    threading.Thread(target=press_stop_mid_trial, daemon=True).start()
    rep = agent["go"]([("commit", SLOW, "slow fit"), ("broker", ["trial", "--sport", "nba"]), ("sleep", 600)])
    assert rep["stopped"] == "stopped" and rep["trial_left_alive"] == []
    [t] = rep["trials"]
    assert t["status"] == "interrupted" and t["description"] == "slow fit"
    assert rep["interrupted"] == [t["trial_id"]]
    ps = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True, text=True).stdout
    assert str(agent["exp"]) not in ps                          # no evaluator or sandboxed fit left running
    ra.stop(agent["exp"], clear=True)


def test_stop_kills_a_running_agent_and_blocks_trials_and_runs_until_cleared(agent):
    def press_stop():
        for _ in range(200):
            a = ra.active(agent["exp"])
            if a and a.get("agent_pid"):
                time.sleep(1)
                ra.stop(agent["exp"], "test stop")
                return
            time.sleep(0.1)
    threading.Thread(target=press_stop, daemon=True).start()
    rep = agent["go"]([("sleep", 600)])
    assert rep["stopped"] == "stopped" and rep["sweep"] is None
    # Regression (real run 20261014T134010Z-5f7f): a killed agent reports no cost; that is unknown, never $0.
    assert rep["cost_usd"] is None and ra.runs(agent["exp"])[-1]["cost_usd"] is None
    assert ra.runs(agent["exp"])[-1]["stopped"] == "stopped"
    with pytest.raises(ar.Refused, match="stopped"):
        ar.run_trial("nba", agent["exp"], agent["ws"])
    with pytest.raises(ar.Refused, match="stopped"):
        agent["go"]([])
    ra.stop(agent["exp"], clear=True)
    assert agent["go"]([])["stopped"] == "agent finished"


def test_a_launch_failure_is_retried_once_then_recorded(agent):
    rep = agent["go"]([("exit", 3)])
    assert [a["exit"] for a in rep["attempts"]] == [3, 3] and rep["stopped"] == "agent failed to start"
    assert ra.runs(agent["exp"])[-1]["event"] == "finished"


def test_one_agent_run_at_a_time(agent):
    with ra._Lock(agent["exp"] / "agent" / "agent.lock"):
        with pytest.raises(ar.Refused, match="one at a time"):
            agent["go"]([])


def test_a_dead_driver_blocks_trials_until_a_person_clears_it(agent):
    p = subprocess.Popen(["/usr/bin/true"])
    token = ra._proc_token(p.pid)
    p.wait()
    (agent["exp"] / "agent").mkdir(exist_ok=True)
    (agent["exp"] / "agent" / "active.json").write_text(json.dumps(
        {"run_id": "r-dead", "driver_pid": p.pid, "driver_token": token or "gone", "max_trials": 3,
         "deadline": time.time() + 600, "trial_timeout_s": 60}))
    with pytest.raises(ar.Refused, match="no live driver"):
        ar.run_trial("nba", agent["exp"], agent["ws"])
    with pytest.raises(ar.Refused, match="left no terminal record"):
        agent["go"]([])
    ra.stop(agent["exp"], clear=True)
    assert ra.runs(agent["exp"])[-1]["event"] == "abandoned"
    assert ra.trial_gate(agent["exp"]) is None


def test_uncommitted_edits_are_saved_not_evaluated_and_the_workspace_is_restored(agent):
    rep = agent["go"]([("dirty",)])
    assert "# not committed" in Path(rep["uncommitted_diff"]).read_text()
    assert rep["trials"] == [] and (agent["ws"] / "train.py").read_text() == BASELINE


def test_an_unreadable_trial_log_still_ends_the_run_cleanly(agent):
    # Only possible without the sandbox (the agent cannot write the log); the driver must still clean up and say so.
    rep = agent["go"]([("write", str(agent["root"] / "trials.jsonl"))], sandbox=False)
    assert "trial log unreadable" in " ".join(rep["errors"])
    assert ra.runs(agent["exp"])[-1]["event"] == "finished" and "with errors" in ra.runs(agent["exp"])[-1]["outcome"]
    assert not (agent["exp"] / "agent" / "active.json").exists()
    assert git(agent["ws"], "rev-parse", "--abbrev-ref", "HEAD") == "master"
    with pytest.raises(Exception):                         # trials stay impossible on a broken log (fail closed)
        ar.run_trial("nba", agent["exp"], agent["ws"])


# --- What the agent can reach ------------------------------------------------------------------------------------------
def test_the_broker_only_offers_trial_and_status():
    assert ra.parse_request(["trial", "--sport", "nhl", "--description", "x", "--retry"])[0] == "trial"
    assert ra.parse_request(["status"]) == ["status"]
    for bad in (["holdout", "--sport", "nba"], ["prepare"], ["daily"], ["agent"], ["stop", "--clear"],
                ["trial", "--exp-root", "/tmp/x"], ["trial", "--workspace", "/tmp"], ["trial", "--timeout", "99999"],
                ["trial", "--sport", "mlb"], ["trial", "--reuse-reason", "x"], []):
        with pytest.raises(ar.Refused):
            ra.parse_request(bad)


def test_holdout_through_the_broker_is_refused(agent):
    rep = agent["go"]([("broker", ["holdout", "--sport", "nba"]), ("broker", ["trial", "--exp-root", "/tmp/elsewhere"])])
    assert all(t["exit"] == 2 and "REFUSED" in t["output"] for t in tool_lines(rep))
    assert not (agent["root"] / "holdout_access.jsonl").exists()


def test_the_agent_process_cannot_read_or_write_data_logs_or_the_repository(agent):
    exp, data, ws = agent["exp"], agent["data"], agent["ws"]
    assert not (ar.APP_DIR / "x.py").exists()
    try:
        rep = agent["go"]([("read", str(data / "nba.sqlite")), ("read", str(agent["root"] / "protocol.json")),
                           ("read", str(agent["root"] / "search.pkl")), ("read", str(ar.REPO_ROOT / "HANDOFF.md")),
                           ("write", str(exp / "agent" / "STOP")), ("write", str(agent["root"] / "trials.jsonl")),
                           ("write", str(ar.APP_DIR / "x.py")), ("read", str(ws / "program.md")),
                           ("read", str(ar.APP_DIR / "autoresearch.py")), ("write", str(ws / "scratch.txt"))])
    finally:
        leaked = (ar.APP_DIR / "x.py").exists()
        (ar.APP_DIR / "x.py").unlink(missing_ok=True)           # never leave a probe in the source tree
    probes = {(p["op"], p["path"]): p["ok"] for p in tool_lines(rep)}
    assert [ok for (op, path), ok in probes.items()] == [False] * 7 + [True] * 3, probes
    assert not (exp / "agent" / "STOP").exists() and not leaked


def test_the_real_agent_command_keeps_permission_checks():
    cmd = ra.claude_command("claude", 2.0, "PROMPT", None)
    joined = " ".join(cmd)
    assert "skip-permissions" not in joined and "bypassPermissions" not in joined
    assert cmd[cmd.index("--permission-prompts") + 1] == "none" and "--restricted" in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == "2.00" and cmd[cmd.index("--tools") + 1] == "Read,Edit,Grep,Glob,Bash"
    allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--disallowedTools")]
    denied = cmd[cmd.index("--disallowedTools") + 1: cmd.index("--permission-mode")]
    assert "Bash(git * --output*)" in denied and "Bash(git * -c *)" in denied
    assert "Edit(./train.py)" in allowed and not [a for a in allowed if a.startswith("Edit") and a != "Edit(./train.py)"]
    assert not [a for a in allowed if "holdout" in a or "prepare.py prepare" in a or a in ("Bash", "Bash(*)", "Write")]


# --- Review findings (2026-10-14, patch C review) -----------------------------------------------------------------------
GIT = "git -c user.name=a -c user.email=a@a"


def test_the_agent_can_commit_and_reset_on_its_branch_without_sandbox_errors(agent):
    # Real run 20261014T150510Z-0809: commit and reset printed "cannot lock ref 'AUTO_MERGE' ... Operation not permitted".
    rep = agent["go"]([("shell", f"{GIT} commit -qm idea --allow-empty"), ("shell", "git reset -q --hard HEAD~1")])
    assert [(t["exit"], "not permitted" in t["output"]) for t in tool_lines(rep)] == [(0, False), (0, False)], tool_lines(rep)


def test_f1_the_agent_cannot_rewrite_git_config_hooks_or_the_fixed_files(agent):
    # Reviewer's escape: write .git/config through `git log --output` (an allowed command), so the driver's next unsandboxed
    # git call runs core.fsmonitor. The agent's sandbox must deny those writes.
    pwned = agent["tmp"] / "pwned"
    payload = f'[core]\n\tfsmonitor = "echo RAN > {pwned}; false"\n'
    rep = agent["go"]([("shell", f"{GIT} commit -q --allow-empty -m '{payload}'"),
                       ("shell", "git log -1 --format=%B --output=.git/config"),
                       ("shell", "printf x > .git/hooks/post-checkout"), ("shell", "printf x > .git/info/attributes"),
                       ("shell", "printf x > .git/config.worktree"), ("shell", "printf x >> prepare.py"),
                       ("shell", "printf x >> program.md")])
    tools = tool_lines(rep)
    assert tools[0]["exit"] == 0                                  # an ordinary commit still works
    assert all(t["exit"] != 0 for t in tools[1:]), tools          # every write to git config/hooks/info or fixed files fails
    assert not pwned.exists()
    assert "fsmonitor" not in (agent["ws"] / ".git" / "config").read_text()
    assert git(agent["ws"], "show", "master:prepare.py") == (agent["ws"] / "prepare.py").read_text().rstrip("\n")


def test_f1_driver_and_evaluator_git_calls_never_run_repository_configured_commands(agent, monkeypatch):
    # Second layer: even without the registered-identity check (patch E), the hardened git calls run none of these.
    monkeypatch.setattr(ar, "_TRUSTED_GIT", {})
    pwned = agent["tmp"] / "pwned"
    hooks = agent["tmp"] / "hooks"
    hooks.mkdir()
    (hooks / "post-checkout").write_text(f"#!/bin/sh\necho hook > {pwned}\n")
    (hooks / "post-checkout").chmod(0o755)
    ext = agent["tmp"] / "ext.sh"
    ext.write_text(f"#!/bin/sh\necho ext > {pwned}\n")
    ext.chmod(0o755)
    with (agent["ws"] / ".git" / "config").open("a") as fh:       # as if an attacker had already changed the config
        fh.write(f'[core]\n\tfsmonitor = "echo fsm > {pwned}; false"\n\thooksPath = {hooks}\n[diff]\n\texternal = {ext}\n')
    (agent["ws"] / "train.py").write_text(BASELINE + "\n# dirty\n")
    ar._git(agent["ws"], "status", "--porcelain")
    ar._git(agent["ws"], "diff", "--no-ext-diff", "--no-textconv")        # as the driver calls it
    try:
        ar._git(agent["ws"], "diff")                               # a plain diff fails closed instead of running the program
    except subprocess.CalledProcessError:
        pass
    ar._git(agent["ws"], "checkout", "-q", "--", ".")
    ar._git(agent["ws"], "checkout", "-q", "master")
    assert not pwned.exists()
    ar.trust_workspace(agent["ws"], agent["meta"]["workspace_git"])     # first layer: the changed identity refuses outright
    with pytest.raises(ar.Refused, match="git configuration changed"):
        ar._git(agent["ws"], "status")


def test_f1_a_changed_git_config_after_the_agent_stops_the_sweep(agent):
    rep = agent["go"]([("commit", WORSE, "unrun proposal"), ("shell", "printf '\\n# changed\\n' >> .git/config")],
                      sandbox=False)                              # only possible without the sandbox
    assert any(".git/config changed" in e for e in rep["errors"]) and rep["sweep"] is None


def test_f2_the_agent_cannot_read_files_elsewhere_in_home_or_open_local_ports(agent):
    import socket
    secret = Path.home() / f".ar-review-probe-{os.getpid()}.sqlite"
    secret.write_text("store copy")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        rep = agent["go"]([("read", str(secret)), ("connect", srv.getsockname()[1])])
    finally:
        secret.unlink()
        srv.close()
    assert [p["ok"] for p in tool_lines(rep)] == [False, False]


def test_f3_the_agent_gets_an_explicit_environment(agent, monkeypatch):
    monkeypatch.setenv("AR_REVIEW_SECRET", "x")
    monkeypatch.setenv("NBA_DATA_DIR", os.environ["NBA_DATA_DIR"])
    rep = agent["go"]([("env",)])
    [e] = tool_lines(rep)
    assert "AR_REVIEW_SECRET" not in e["keys"] and "NBA_DATA_DIR" not in e["keys"]
    assert set(e["keys"]) <= set(ra.AGENT_ENV) | {"AUTORESEARCH_BROKER", "PATH", "__CF_USER_TEXT_ENCODING", "PWD", "SHLVL", "_"}


def test_f4_a_stop_during_setup_starts_no_agent(agent, monkeypatch):
    real = ar.prepare

    def slow_prepare(sport, exp_root, ws):
        (exp_root / "agent").mkdir(exist_ok=True)
        (exp_root / "agent" / "STOP").write_text("stopped during setup\n")
        return real(sport, exp_root, ws)
    monkeypatch.setattr(ar, "prepare", slow_prepare)
    with pytest.raises(ar.Refused, match="stopped"):
        agent["go"]([("sleep", 600)])
    assert agent["budgets"] == [] and ra.runs(agent["exp"]) == []      # nothing launched, nothing half-recorded


def test_f4_a_stop_file_alone_ends_a_running_agent(agent):
    def write_stop():
        for _ in range(200):
            a = ra.active(agent["exp"])
            if a and a.get("agent_pid"):
                (agent["exp"] / "agent" / "STOP").write_text("file only, no signal\n")
                return
            time.sleep(0.1)
    threading.Thread(target=write_stop, daemon=True).start()
    rep = agent["go"]([("sleep", 600)])
    assert rep["stopped"] == "stopped" and rep["minutes"] < 1
    ra.stop(agent["exp"], clear=True)


def test_f5_the_stop_handler_stays_installed_while_the_run_finishes(agent, monkeypatch):
    import signal
    seen = []
    real = ar.daily

    def daily(*a, **kw):
        seen.append(signal.getsignal(signal.SIGTERM))
        return real(*a, **kw)
    monkeypatch.setattr(ar, "daily", daily)
    before = signal.getsignal(signal.SIGTERM)
    agent["go"]([("commit", WORSE, "unrun proposal")])
    assert seen and seen[0] is not before and seen[0] not in (signal.SIG_DFL, None)
    assert signal.getsignal(signal.SIGTERM) is before


def test_f8_stop_works_without_a_data_dir(agent, monkeypatch):
    monkeypatch.delenv("NBA_DATA_DIR")
    assert ar.main(["stop", "--exp-root", str(agent["exp"]), "--workspace", str(agent["ws"])]) == 0
    assert (agent["exp"] / "agent" / "STOP").is_file()
    assert ar.main(["stop", "--clear", "--exp-root", str(agent["exp"]), "--workspace", str(agent["ws"])]) == 0


def test_f8_the_broker_socket_is_private_and_unguessable(agent):
    socks = []

    def grab():
        for _ in range(200):
            a = ra.active(agent["exp"])
            if a and a.get("socket"):
                socks.append(a["socket"])
                return
            time.sleep(0.05)
    threading.Thread(target=grab, daemon=True).start()
    agent["go"]([("sleep", 1)])
    [sock] = socks
    d = Path(sock).parent
    assert len(d.name) >= 20 and not d.exists()                  # random private dir, removed afterwards


def test_f8_a_retried_launch_shares_one_spending_cap(agent):
    rep = agent["go"]([("exit", 3)], max_usd=2.0)
    assert len(rep["attempts"]) == 2 and agent["budgets"] == [1.0, 1.0]


# --- Re-review findings (patch D review) --------------------------------------------------------------------------------
def test_n3_the_agent_cannot_touch_claude_code_config_transcripts_or_preferences(agent):
    # The user's own (unsandboxed) Claude Code sessions load hooks and MCP commands from ~/.claude*: never writable, and
    # other sessions' transcripts are not readable. Only the Claude Code install and the login keychain are read.
    home = Path.home()
    probe = home / ".claude" / f"ar-review-probe-{os.getpid()}"
    try:
        rep = agent["go"]([("write", str(probe)), ("read", str(home / ".claude.json")),
                           ("read", str(home / "Library" / "Preferences" / ".GlobalPreferences.plist")),
                           ("shell", f"ls {home / '.claude' / 'projects'}")])
    finally:
        leaked = probe.exists()
        probe.unlink(missing_ok=True)
    w, r1, r2, ls = tool_lines(rep)
    assert not leaked and [w["ok"], r1["ok"], r2["ok"]] == [False, False, False] and ls["exit"] != 0, tool_lines(rep)
    profile = (next((agent["exp"] / "agent" / "runs").iterdir()) / "agent.sb").read_text()
    assert ".claude" not in profile and "Preferences" not in profile


def test_n1_n2_the_agent_cannot_plant_attributes_alternates_or_packed_refs(agent):
    rep = agent["go"]([("shell", "printf '* filter=x\\n' > .gitattributes"), ("shell", "printf x > .git/objects/info/alternates"),
                       ("shell", "printf x > .git/packed-refs")])
    assert all(t["exit"] != 0 for t in tool_lines(rep)), tool_lines(rep)
    assert not (agent["ws"] / ".gitattributes").exists()


def test_n1_every_evaluator_git_call_refuses_a_changed_git_configuration(agent):
    # Filter drivers still run under hardened git, so any change to what configures git (config, attributes, alternates,
    # hooks, info) must stop every later git call, including the evaluator's own mid-run ones.
    marker = agent["tmp"] / "filter-ran"
    with (agent["ws"] / ".git" / "config").open("a") as fh:
        fh.write(f'[filter "x"]\n\tclean = "sh -c \'echo clean > {marker}; cat\'"\n\tsmudge = "sh -c \'echo smudge > {marker}; cat\'"\n')
    (agent["ws"] / ".gitattributes").write_text("* filter=x\n")
    (agent["ws"] / "train.py").write_text(BASELINE + "\n# touched\n")
    with pytest.raises(ar.Refused, match="git configuration changed"):
        ar.run_trial("nba", agent["exp"], agent["ws"])
    with pytest.raises(ar.Refused, match="git configuration changed"):
        agent["go"]([])
    assert not marker.exists()


def test_n4_a_stop_during_the_finish_skips_the_evaluation_sweep(agent, monkeypatch):
    import signal
    swept = []
    real_mark, real_daily = ar.mark_interrupted, ar.daily

    def mark(root):                       # the stop arrives while the run is finishing (the run's handler is installed)
        os.kill(os.getpid(), signal.SIGTERM)
        return real_mark(root)

    def daily(*a, **kw):
        swept.append(1)
        return real_daily(*a, **kw)
    monkeypatch.setattr(ar, "mark_interrupted", mark)
    monkeypatch.setattr(ar, "daily", daily)
    rep = agent["go"]([("commit", WORSE, "unrun proposal")])
    assert swept == [] and rep["sweep"] is None and any("stop signal during the finish" in e for e in rep["errors"])


def test_n1_a_new_gitattributes_alone_refuses_git_calls(agent):
    # A filter defined in the config when the workspace was registered becomes active only through an attributes file.
    marker = agent["tmp"] / "filter-ran"
    with (agent["ws"] / ".git" / "config").open("a") as fh:
        fh.write(f'[filter "x"]\n\tclean = "sh -c \'echo clean > {marker}; cat\'"\n')
    ar.trust_workspace(agent["ws"], ar.workspace_git_sha(agent["ws"]))   # registered with that config
    ar._git(agent["ws"], "status", "--porcelain")
    (agent["ws"] / "sub").mkdir()
    (agent["ws"] / "sub" / ".gitattributes").write_text("* filter=x\n")
    (agent["ws"] / "train.py").write_text(BASELINE + "\n# touched\n")
    with pytest.raises(ar.Refused, match="git configuration changed"):
        ar._git(agent["ws"], "status", "--porcelain")
    assert not marker.exists()


def test_a_staged_uncommitted_change_is_saved_and_never_left_on_master(agent):
    # Real run 20261014T155928Z-1593: the agent staged train.py but could not commit; the finish saved only unstaged edits
    # and the staged change was carried onto master (next run refused: dirty workspace).
    rep = agent["go"]([("shell", "printf '\\n# staged idea\\n' >> train.py && git add train.py")])
    assert "# staged idea" in Path(rep["uncommitted_diff"]).read_text()
    assert git(agent["ws"], "rev-parse", "--abbrev-ref", "HEAD") == "master"
    assert git(agent["ws"], "status", "--porcelain", "--untracked-files=no") == ""


def test_the_agent_may_commit_in_the_usual_forms():
    # Real run 20261014T155928Z-1593: `git commit -q -m ...` matched no allow rule, so the agent could not commit at all.
    allowed = ra.ALLOWED_TOOLS
    assert "Bash(git commit *)" in allowed and "Bash(git add train.py)" in allowed
    assert {"Bash(git * -F*)", "Bash(git * --file*)", "Bash(git * --template*)"} <= set(ra.DENIED_TOOLS)


# --- Patch E re-review LOW items (patch F) ------------------------------------------------------------------------------
def test_f_low1_a_differently_cased_attributes_file_counts_and_cannot_be_written(agent):
    before = ar.workspace_git_sha(agent["ws"])
    (agent["ws"] / ".GitAttributes").write_text("* filter=x\n")
    try:
        assert ar.workspace_git_sha(agent["ws"]) != before
    finally:
        (agent["ws"] / ".GitAttributes").unlink()
    rep = agent["go"]([("shell", "printf x > .GitAttributes"), ("shell", "mkdir -p d && printf x > d/.GITATTRIBUTES")])
    assert all(t["exit"] != 0 for t in tool_lines(rep)), tool_lines(rep)


def test_f_low2_registration_refuses_a_workspace_that_configures_programs(tmp_path, monkeypatch):
    # No silent trust on first use: a workspace whose git config defines programs, or that has hooks, attributes or
    # alternates, is refused at registration (prepare), with what was found.
    import test_autoresearch as t
    data = tmp_path / "snapshot"
    data.mkdir()
    (data / "nba.sqlite").write_bytes(b"synthetic snapshot")
    os.chmod(data / "nba.sqlite", 0o444)
    os.chmod(data, 0o555)
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    monkeypatch.setattr(ar, "build_dataset", lambda sport, period: t.synthetic(period))
    try:
        for i, plant in enumerate([lambda ws: git(ws, "config", "filter.x.clean", "cat"),
                                   lambda ws: git(ws, "config", "core.fsmonitor", "true"),
                                   lambda ws: git(ws, "config", "include.path", "/tmp/x"),
                                   lambda ws: (ws / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\n"),
                                   lambda ws: (ws / ".gitattributes").write_text("* text\n"),
                                   lambda ws: (ws / ".git" / "objects" / "info" / "alternates").write_text("/tmp\n")]):
            ws = tmp_path / f"ws{i}"
            ws.mkdir()
            git(ws, "init", "-q")
            for f in ("program.md", "prepare.py"):
                (ws / f).write_text("x\n")
            (ws / "train.py").write_text(t.BASELINE)
            git(ws, "add", "-A")
            git(ws, "commit", "-qm", "base")
            plant(ws)
            with pytest.raises(ar.Refused, match="workspace git"):
                ar.prepare("nba", tmp_path / f"exp{i}", ws)
    finally:
        os.chmod(data, 0o755)


def test_f_low3_the_attributes_rule_escapes_the_workspace_path(tmp_path):
    ws = tmp_path / "w.s+(1)"
    ws.mkdir()
    profile = ra.agent_profile(tmp_path / "exp", ws, str(tmp_path / "s.sock"))
    rule = next(line for line in profile.splitlines() if "regex" in line)
    assert "w[.]s[+][(]1[)]" in rule or "w\\\\.s\\\\+\\\\(1\\\\)" in rule, rule
    p = tmp_path / "p.sb"
    p.write_text(profile)
    r = subprocess.run([ar.SANDBOX, "-f", str(p), "/bin/sh", "-c", "printf x > .gitattributes; printf y > ok.txt"], cwd=ws,
                       capture_output=True, text=True)
    assert not (ws / ".gitattributes").exists() and (ws / "ok.txt").read_text() == "y", r.stderr


def test_f_low4_attached_file_options_are_denied():
    assert {"Bash(git * -F*)", "Bash(git * --file*)"} <= set(ra.DENIED_TOOLS)


def test_f_tooling_state_in_the_app_tree_does_not_change_the_protocol(tmp_path, monkeypatch):
    # The shared checkout holds `.omc/state/*.json` (editor plugin state) inside backend/app; it is not app code.
    before = ar.app_identity()["files"]
    d = ar.APP_DIR / ".omc-test-state"
    d.mkdir()
    try:
        (d / "state.json").write_text("{}")
        assert ar.app_identity()["files"] == before
    finally:
        (d / "state.json").unlink()
        d.rmdir()
