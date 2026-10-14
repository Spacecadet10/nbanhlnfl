"""Sports adaptation of karpathy/autoresearch (upstream 228791fb499afffb54b46200aca536f79142f117): the FIXED side.

Upstream splits a research loop into `prepare.py` (fixed data, constants and evaluator), `train.py` (the one file an agent
edits) and `program.md` (the human-written agent instructions): an agent edits train.py, commits, runs, and keeps the commit
only when the fixed metric improves. This module is the fixed side for NBA/NHL. It builds the frozen dataset from the
existing pipeline (study.existing_tables + features.walk) and drives the existing season-start walk itself: the agent's
train.py runs in a sandboxed process (no file writes, no reads of the data, experiments or workspace, no network) that only
answers "fit these rows" and "forecast these rows", one cutoff at a time and in time order, so it never holds a label or a
feature row from after the cutoff it forecasts. Its answers are parsed as JSON, validated, and scored per market against the
incumbent (at the start: the app's best existing member) on a fixed matched cohort with the MODEL_SELECTION §6 test. Every
trial, also failures, is appended to a log. docs/AUTORESEARCH.md has the protocol.

Research only. It never writes a store, a forecast table or an artifact the live jobs read, and nothing here approves or
promotes anything (coverage-v2 rejects every adaptive definition; promotion stays manual, §6, on the forward record).
Run with `python -m app.autoresearch <command>`; NBA_DATA_DIR must point at a read-only snapshot, never at data/.
"""
import argparse
import ast
import fcntl
import gzip
import hashlib
import itertools
import json
import math
import os
import pickle
import platform
import random
import select
import signal
import struct
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import config, sports

UPSTREAM = {"repo": "https://github.com/karpathy/autoresearch", "commit": "228791fb499afffb54b46200aca536f79142f117"}
REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = Path(__file__).resolve().parent
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
SANDBOX = "/usr/bin/sandbox-exec"

# Fixed before any trial. Changing anything here, in the app's code or in the library versions changes the protocol id: a new
# experiment with empty evidence; earlier trials stay under their own id.
PROTOCOL = {
    "name": "sports-autoresearch-v2",
    "search_period": "validation",            # existing loaders filter seasons in SQL: reserved-season rows are never loaded
    "scored_seasons": {"nba": [2022, 2023, 2024], "nhl": [2022, 2023, 2024]},   # = backtest/nhl_eval PERIODS["validation"]
    "holdout_period": "test",
    "holdout_seasons": {"nba": [2025, 2026], "nhl": [2025, 2026]},
    "holdout_note": "Reserved test seasons. Earlier studies already used them once (combination_research.STORED_NOTE), so "
                    "they are protected from this search but not pristine. Used once per protocol and sport, for the kept "
                    "per-market incumbents together, after a confirmation typed at a terminal; the access is logged first.",
    "cutoff": "night-before 00:00 ET (backtest.cutoff_for); results and box/shot events are delivered to the feature walk "
              "only when available at that cutoff (backtest.run raises LeakageError otherwise)",
    "fit_schedule": "season start: the fit for season s gets only rows of earlier seasons whose results were available before "
                    "the first cutoff of s (engine.training_rows); forecasts are requested one cutoff at a time in time "
                    "order with labels removed, in a separate sandboxed process",
    "line": "line markets are settled at the primary model's own line (no historical bookmaker lines exist). The primary is "
            "therefore close to 50/50 there by construction (NBA: log loss ln 2), so the bar is the best existing member",
    "cohort": "per market: scored-season games with a settled outcome and a primary forecast; fixed for the protocol",
    "start_incumbent": "per market, the existing app member with a forecast for every cohort game and the lowest cohort log "
                       "loss; the primary is reported for information",
    "keep_rule": "per market, vs the incumbent: every cohort game forecast, paired log-loss difference week-block bootstrap "
                 "95% upper bound < 0, matched pick accuracy not lower (MODEL_SELECTION §6 test, applied to validation: "
                 "exploratory search, not promotion evidence)",
    "excluded_inputs": {
        "nhl goalie features": "historical rows use the actual starter, known only after the start (study.py, adaptive.py); "
                               "stripped from every row and refused in a spec",
        "injury reports / player ratings": "no timestamped history for the search seasons (PLAN rule 8); players-v1 found "
                                           "no information beyond the team model; live-only members are out of scope",
    },
    "seed": 20261014,
}
BUDGETS = {"trial_timeout_s": 1800, "max_trials": 4, "max_minutes": 120, "max_retries": 2, "concurrency": 1}
FORBIDDEN_FEATURES = ("goalie_home", "goalie_away", "goalie_diff", "goalie_exp_home", "goalie_exp_away")
PROTECTED_WORKSPACE = ("program.md", "prepare.py")
EDITABLE = "train.py"
# Defence in depth (the sandbox is the boundary): train.py may import only these.
ALLOWED_MODULES = ("math", "statistics", "random", "dataclasses", "typing", "functools", "itertools", "collections",
                   "numpy", "scipy", "sklearn", "torch", "tensorflow", "keras")
ALLOWED_APP = ("engine", "registry", "learners", "deep", "features")      # from app.candidates import <these>
BANNED_NAMES = ("open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "breakpoint", "input",
                "getattr", "setattr", "delattr", "__builtins__", "__loader__", "__spec__", "__file__")
OUTPUT_KEYS = {"binary": {"head", "p"}, "normal": {"head", "m", "sm", "t", "st"}, "reg3": {"head", "p", "ot"},
               "goals": {"head", "lh", "la", "w", "ot"}}


class Refused(RuntimeError):
    """An environment precondition failed; nothing was run or logged as a trial."""


class TrialInvalid(RuntimeError):
    """The candidate broke the contract (outputs, specs, files): logged as `invalid`."""


# --- Identity and guards ----------------------------------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_roots() -> list:
    """Every data directory a live job uses (installed and repository launchd jobs), the release checkout and this
    checkout's data/. Research refuses all of them as a source of truth or an output location."""
    import plistlib
    roots = {REPO_ROOT / "data", Path.home() / "nba-predictor-live"}
    for d in (LAUNCH_AGENTS, REPO_ROOT / "scripts" / "launchd"):
        for p in sorted(d.glob("com.nba-predictor.*.plist")) if d.is_dir() else []:
            try:
                env = plistlib.loads(p.read_bytes()).get("EnvironmentVariables", {})
            except Exception:                 # an unreadable job definition must not silently widen what is allowed
                raise RuntimeError(f"cannot read launchd job {p}")
            if env.get("NBA_DATA_DIR"):
                roots.add(Path(env["NBA_DATA_DIR"]))
    return sorted(roots)


def libraries() -> dict:
    from importlib.metadata import PackageNotFoundError, version
    out = {"python": platform.python_version()}
    for p in ("numpy", "scipy", "scikit-learn", "torch", "tensorflow", "ngboost"):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = None
    return out


def app_identity() -> dict:
    """Every source and model file of the app the evaluator could execute, plus library versions."""
    files = sorted(p for p in APP_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                   and not any(part.startswith(".") for part in p.relative_to(APP_DIR).parts)   # tooling state, e.g. .omc/
                   and p.suffix in (".py", ".json", ".sql", ".csv"))
    return {"files": _sha("\n".join(f"{p.relative_to(APP_DIR)} {_sha(p.read_bytes())}" for p in files).encode())[:16],
            "libraries": libraries()}


def protocol_id(snapshot: dict) -> str:
    snap = {s: {k: v for k, v in d.items() if k != "file"} for s, d in snapshot.items()}
    blob = json.dumps({"protocol": PROTOCOL, "app": app_identity(), "snapshot": snap, "upstream": UPSTREAM}, sort_keys=True)
    return f"{PROTOCOL['name']}-{_sha(blob.encode())[:12]}"


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def guard_paths(exp_root: Path) -> Path:
    """Refuses the live data, the repository and anything writable as the source of truth."""
    if not os.environ.get("NBA_DATA_DIR"):
        raise Refused("NBA_DATA_DIR is not set; point it at a read-only snapshot copy, never at data/")
    data = config.data_dir().resolve()
    live_dirs = live_roots()
    for live in live_dirs:
        if _inside(data, live) or _inside(live, data):
            raise Refused(f"NBA_DATA_DIR {data} is (or contains) the live store {live}")
    if _inside(exp_root, REPO_ROOT) or any(_inside(exp_root, live) for live in live_dirs) or _inside(exp_root, data):
        raise Refused(f"experiment root {exp_root} must be outside the repository, the live stores and the snapshot")
    return data


def snapshot_identity(sport: str) -> dict:
    """The store and every other snapshot file (raw responses by name, derived caches by content); all read-only."""
    p = config.db_path(sport)
    if not p.is_file():
        raise Refused(f"no {sport} snapshot at {p}")
    root = p.parent
    writable = [str(x) for x in [root, *root.rglob("*")] if os.access(x, os.W_OK)]
    if writable:
        raise Refused(f"snapshot is writable ({writable[:3]}); make it read-only (chmod -R a-w) so it cannot drift")
    raw = root / "raw"
    other = sorted(x for x in root.rglob("*") if x.is_file() and not _inside(x, raw) and x.suffix != ".sqlite")
    return {"file": str(p), "sha256": _sha(p.read_bytes()),
            "raw_manifest": _sha("\n".join(sorted(str(f.relative_to(raw)) for f in raw.rglob("*") if f.is_file())).encode())
            if raw.is_dir() else None,
            "derived": {str(x.relative_to(root)): _sha(x.read_bytes())[:16] for x in other}}


def _snapshot_conn(sport: str):
    import sqlite3
    conn = sqlite3.connect(f"file:{config.db_path(sport)}?immutable=1", uri=True)   # static read-only copy only
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


class Lock:
    """Concurrency limit 1 per experiment root and sport (non-blocking): a second runner is refused, not queued."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.fh = (root / "run.lock").open("w")

    def __enter__(self):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise Refused("another research run holds the lock (concurrency limit 1)")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


# --- Workspace (the upstream repo layout; read from git objects, never checked out) ------------------------------------
# Every git call here runs on a workspace an agent works in. Programs a repository can configure are switched off where
# git allows it (fsmonitor, hooks, external diff, pager, ssh) and system/global config is ignored. Filter drivers
# (filter.<x>.clean/smudge with a .gitattributes entry) cannot be switched off generally, so before EVERY git call on a
# registered workspace the files that configure git (workspace_git_sha) must equal those registered with the protocol; a
# change refuses the call. (The agent's sandbox also denies writing all of them.)
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.external=", "-c", "core.pager=cat",
            "-c", "core.sshCommand=false", "-c", "protocol.allow=never")
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
           "GIT_AUTHOR_NAME": "autoresearch driver", "GIT_AUTHOR_EMAIL": "driver@localhost",
           "GIT_COMMITTER_NAME": "autoresearch driver", "GIT_COMMITTER_EMAIL": "driver@localhost"}


_TRUSTED_GIT = {}                                  # workspace -> registered workspace_git_sha


def workspace_git_sha(ws: Path) -> str:
    """Identity of every file that configures git in this workspace: .git/config(.worktree), hooks, info, objects/info
    (alternates) and every .gitattributes in the working tree."""
    ws = Path(ws)
    g = ws / ".git"
    files = [g / "config", g / "config.worktree", *g.joinpath("hooks").glob("*"), *g.joinpath("info").glob("*"),
             *g.joinpath("objects", "info").glob("*"),
             *(p for p in ws.rglob("*") if p.name.lower() == ".gitattributes" and ".git" not in p.relative_to(ws).parts[:1])]
    h = hashlib.sha256()
    for p in sorted(f for f in files if f.is_file()):
        h.update(f"{p.relative_to(ws)}\0".encode() + p.read_bytes() + b"\0")
    return h.hexdigest()


# Local config keys (lowercase prefixes) that make git run a program or read another file; none is allowed in a workspace
# an agent works in, so registration never trusts one silently.
PROGRAM_KEYS = ("core.fsmonitor", "core.hookspath", "core.pager", "core.editor", "core.sshcommand", "core.askpass",
                "core.gitproxy", "core.attributesfile", "filter.", "diff.", "merge.", "include.", "includeif.", "credential.",
                "gpg.", "sequence.", "alias.", "pager.", "uploadpack.", "receive.", "difftool.", "mergetool.", "submodule.",
                "web.", "browser.", "url.", "http.", "ssh.", "protocol.")


def inspect_workspace_git(ws: Path) -> list:
    """What would make git run something in this workspace: program-valued local config, hooks (other than the samples git
    installs), info files other than `exclude`, alternates, config.worktree, any .gitattributes."""
    ws = Path(ws)
    g = ws / ".git"
    keys = [line.split("=", 1)[0].lower() for line in _git(ws, "config", "--local", "--list").splitlines()]
    found = [f"config {k}" for k in keys if k.startswith(PROGRAM_KEYS)]
    found += [f"hook {p.name}" for p in g.joinpath("hooks").glob("*") if p.is_file() and not p.name.endswith(".sample")]
    found += [f"info/{p.name}" for p in g.joinpath("info").glob("*") if p.is_file() and p.name != "exclude"]
    found += [f"objects/info/{p.name}" for p in g.joinpath("objects", "info").glob("*") if p.is_file()]
    found += ["config.worktree"] if (g / "config.worktree").is_file() else []
    found += [str(p.relative_to(ws)) for p in ws.rglob("*") if p.name.lower() == ".gitattributes"
              and ".git" not in p.relative_to(ws).parts[:1]]
    return found


def trust_workspace(ws: Path, sha: Optional[str]) -> None:
    if not sha:
        raise Refused("this protocol registered no workspace git identity; run `prepare` again")
    _TRUSTED_GIT[str(Path(ws).resolve())] = sha


def _git_run(ws: Path, args, text: bool):
    trusted = _TRUSTED_GIT.get(str(Path(ws).resolve()))
    if trusted is not None and workspace_git_sha(ws) != trusted:
        raise Refused(f"the workspace's git configuration changed since it was registered ({ws}); no git command is run. "
                      "Inspect .git/config, hooks, info, objects/info and .gitattributes, then register again (`prepare`)")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | GIT_ENV
    return subprocess.run(["git", "-C", str(ws), *GIT_SAFE, *args], check=True, capture_output=True, text=text, env=env).stdout


def _git(ws: Path, *args) -> str:
    return _git_run(ws, args, True).strip()


def _git_bytes(ws: Path, *args) -> bytes:
    return _git_run(ws, args, False)


def commit_state(ws: Path, base: str, commit: str) -> dict:
    commit = _git(ws, "rev-parse", "--verify", f"{commit}^{{commit}}")
    src = _git_bytes(ws, "show", f"{commit}:{EDITABLE}")
    return {"commit": commit, "changed": sorted(set(_git(ws, "diff", "--no-ext-diff", "--name-only", base, commit).split())),
            "protected": {f: _sha(_git_bytes(ws, "show", f"{commit}:{f}")) for f in PROTECTED_WORKSPACE},
            "train_src": src.decode(), "train_sha256": _sha(src)}


def static_check(src: str) -> list:
    """Problems that make train.py inadmissible before it runs. Defence in depth only: the sandbox is the boundary."""
    problems = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    aliases = {(a.asname or a.name): f"app.candidates.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and n.module == "app.candidates" and not n.level for a in n.names}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not any(a.name == m or a.name.startswith(m + ".") for m in ALLOWED_MODULES):
                    problems.append(f"import of {a.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            if mod == "app.candidates":
                bad = [a.name for a in node.names if a.name not in ALLOWED_APP]
                if bad:
                    problems.append(f"from app.candidates import {bad} is not allowed (only {list(ALLOWED_APP)})")
            elif any(mod == f"app.candidates.{m}" for m in ALLOWED_APP):
                bad = [a.name for a in node.names if not _app_defined(mod, a.name)]
                if bad:
                    problems.append(f"from {mod} import {bad} is not allowed (not defined by the app)")
            elif not any(mod == m or mod.startswith(m + ".") for m in ALLOWED_MODULES):
                problems.append(f"import from {mod!r} is not allowed")
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            problems.append(f"use of {node.id!r} is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_") and node.attr != "__init__" \
                and not (isinstance(node.value, ast.Name) and node.value.id == "self" and not node.attr.startswith("__")):
            problems.append(f"private attribute {node.attr!r} is not allowed")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases \
                and not _app_defined(aliases[node.value.id], node.attr):
            problems.append(f"{node.value.id}.{node.attr} is not defined by the app")
    return sorted(set(problems))


def _app_defined(module: str, name: str) -> bool:
    """A name an app module itself defines (function, class, constant), not a re-exported module or library object."""
    import importlib
    if name.startswith("_") or name == "*":
        return False
    try:
        obj = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        return False
    if isinstance(obj, (int, float, str, bool, tuple, frozenset, dict, list, type(None))):
        return True
    return str(getattr(obj, "__module__", "") or "").startswith("app.")


# --- Frozen dataset (upstream: `uv run prepare.py`) --------------------------------------------------------------------
def build_dataset(sport: str, period: str) -> dict:
    """Existing members' tables (lines, settled outcomes, member vectors) and the shared point-in-time feature walk."""
    from .candidates import features, study
    conn = _snapshot_conn(sport)
    try:
        tables, _first, members, rows, events, recorder, dataset = study.existing_tables(conn, sport, period)
        walk = features.walk(recorder, rows, events)
    finally:
        conn.close()
    feature_rows = [replace(r, x={k: v for k, v in r.x.items() if k not in FORBIDDEN_FEATURES}, meta={}) for r in walk.values()]
    primary = {m: (members["winner"][0] if m == "winner" else members["lines"][0]) for m in tables}
    return {"sport": sport, "period": period, "rows": feature_rows, "tables": tables, "members": members,
            "primary": primary, "dataset_version": dataset, "feature_table": features.table_version(walk)}


def cohort(data: dict, market: str, scored) -> list:
    prim = data["primary"][market]
    return [r for r in data["tables"][market] if r["season"] in scored and r["realised"] is not None and prim in r["vectors"]]


def best_existing(data: dict, scored) -> dict:
    """Per market: the existing member with a forecast for every cohort game and the lowest cohort log loss (the bar)."""
    from . import combine
    out = {}
    for market in sports.SPORTS[data["sport"]].markets:
        rows = cohort(data, market, scored)
        members = data["members"]["winner" if market == "winner" else "lines"]
        scores = {}
        for m in members:
            have = [r for r in rows if m in r["vectors"]]
            ll = sum(combine.log_loss(r["vectors"][m], r["realised"]) for r in have) / len(have) if have else None
            scores[m] = {"coverage": len(have) / len(rows) if rows else 0.0, "log_loss": ll}
        full = {m: s for m, s in scores.items() if s["coverage"] == 1.0}
        best = min(full, key=lambda m: (full[m]["log_loss"], m))
        out[market] = {"member": best, "log_loss": full[best]["log_loss"], "members": scores}
    return out


def _paths(exp_root: Path, sport: str) -> tuple[str, Path, dict]:
    snap = snapshot_identity(sport)
    pid = protocol_id({sport: snap})
    return pid, exp_root / pid / sport, snap


def _registered(meta_path: Path, workspace: Path) -> dict:
    meta = json.loads(meta_path.read_text())
    trust_workspace(workspace, meta.get("workspace_git"))
    return meta


def registered(sport: str, exp_root: Path, workspace: Path) -> Optional[dict]:
    """The protocol already registered for the current code and snapshot (and its trusted workspace identity), or None."""
    _pid, root, _snap = _paths(exp_root, sport)
    return _registered(root / "protocol.json", workspace) if (root / "protocol.json").is_file() else None


def prepare(sport: str, exp_root: Path, workspace: Path) -> dict:
    guard_paths(exp_root)
    pid, root, snap = _paths(exp_root, sport)
    meta_path = root / "protocol.json"
    if meta_path.is_file():
        return _registered(meta_path, workspace)
    with Lock(root):
        if meta_path.is_file():
            return _registered(meta_path, workspace)
        t0 = time.time()
        data = build_dataset(sport, PROTOCOL["search_period"])
        loaded = sorted({r.season for r in data["rows"]})
        if set(loaded) & set(PROTOCOL["holdout_seasons"][sport]):
            raise Refused(f"search dataset loaded holdout seasons {loaded}")
        blob = pickle.dumps(data, protocol=4)
        cache = root / "search.pkl"
        tmp = root / "search.pkl.tmp"
        tmp.write_bytes(blob)
        os.chmod(tmp, 0o444)
        tmp.replace(cache)                    # atomic: a crash leaves no half-registered protocol
        found = inspect_workspace_git(workspace)          # registration never trusts a program-running configuration
        if found:
            raise Refused(f"workspace git configuration not accepted for registration: {found}; remove them, then prepare")
        git_sha = workspace_git_sha(workspace)            # registration: the workspace as a person prepared it
        trust_workspace(workspace, git_sha)
        base = _git(workspace, "rev-parse", "HEAD")
        scored = PROTOCOL["scored_seasons"][sport]
        meta = {"protocol_id": pid, "sport": sport, "registered_at": _now(), "protocol": PROTOCOL, "upstream": UPSTREAM,
                "app": app_identity(), "snapshot": snap, "cache": {"file": str(cache), "sha256": _sha(blob)},
                "dataset_version": data["dataset_version"], "feature_table": data["feature_table"], "seasons_loaded": loaded,
                "primary": data["primary"], "cohort": {m: len(cohort(data, m, scored)) for m in sports.SPORTS[sport].markets},
                "start_incumbent": best_existing(data, scored), "workspace": str(workspace), "workspace_base": base,
                "protected": commit_state(workspace, base, base)["protected"], "workspace_git": git_sha,
                "workspace_git_inspection": "no program-valued local config, hooks, info files, alternates or attributes",
                "build_seconds": round(time.time() - t0, 1)}
        tmp = root / "protocol.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1, sort_keys=True))
        os.chmod(tmp, 0o444)
        tmp.replace(meta_path)
        return meta


# --- The sandboxed candidate process ------------------------------------------------------------------------------------
def _read_exact(f, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            raise EOFError("parent closed the pipe")
        buf += chunk
    return buf


def _jsonable(o):
    return o.tolist() if hasattr(o, "tolist") else float(o)


def serve(resp_fd: int) -> None:
    """Child side: imports train.py from source and answers fit/forecast requests. Holds nothing it was not sent."""
    import types
    inp, out = sys.stdin.buffer, os.fdopen(resp_fd, "wb")

    def recv():
        return pickle.loads(_read_exact(inp, struct.unpack(">Q", _read_exact(inp, 8))[0]))

    def send(obj):
        b = json.dumps(obj, default=_jsonable).encode()
        out.write(struct.pack(">Q", len(b)) + b)
        out.flush()

    hello = recv()
    random.seed(hello["seed"])
    try:
        import numpy
        numpy.random.seed(hello["seed"] % 2 ** 32)
    except ImportError:
        pass
    mod = types.ModuleType("train")
    exec(compile(hello["source"], "train.py", "exec"), mod.__dict__)
    specs = list(mod.candidates(hello["sport"]))
    send({"specs": [asdict(s) for s in specs], "markets": dict(getattr(mod, "MARKETS", {}).get(hello["sport"], {}))})
    models = {}
    while True:
        m = recv()
        if m["op"] == "fit":
            models[m["spec"].name] = mod.fit(m["spec"], m["rows"])
            send({"fitted": models[m["spec"].name] is not None})
        elif m["op"] == "predict":
            send({"outputs": list(mod.outputs(m["spec"], models[m["spec"].name], m["rows"], m["sd"]))})
        else:
            send({"ok": True})
            return


class _Timeout(RuntimeError):
    pass


class _ChildGone(RuntimeError):
    pass


class _Remote:
    """Parent side of the pipe protocol, with one deadline for the whole trial."""

    def __init__(self, w_fd: int, r_fd: int, deadline: float):
        self.w, self.r, self.deadline = w_fd, r_fd, deadline
        os.set_blocking(self.w, False)

    def _left(self) -> float:
        left = self.deadline - time.time()
        if left <= 0:
            raise _Timeout()
        return left

    def call(self, msg) -> dict:
        b = pickle.dumps(msg, protocol=4)
        data = memoryview(struct.pack(">Q", len(b)) + b)
        while data:
            if not select.select([], [self.w], [], self._left())[1]:
                raise _Timeout()
            try:
                n = os.write(self.w, data)
            except BrokenPipeError:
                raise _ChildGone()
            data = data[n:]
        head = self._recv(8)
        return json.loads(self._recv(struct.unpack(">Q", head)[0]))

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            if not select.select([self.r], [], [], self._left())[0]:
                raise _Timeout()
            chunk = os.read(self.r, n - len(buf))
            if not chunk:
                raise _ChildGone()
            buf += chunk
        return buf


class _Local:
    """In-process stand-in for the sandboxed child (tests only): the same messages, a module instead of a process."""

    def __init__(self, train):
        self.train, self.models, self.sent = train, {}, []

    def call(self, msg) -> dict:
        self.sent.append(msg)
        if msg["op"] == "fit":
            self.models[msg["spec"].name] = self.train.fit(msg["spec"], msg["rows"])
            return {"fitted": self.models[msg["spec"].name] is not None}
        return {"outputs": list(self.train.outputs(msg["spec"], self.models[msg["spec"].name], msg["rows"], msg["sd"]))}


def _strip(r):
    return replace(r, y=None, result_available_at=None)


def _clean_output(o, head: str) -> Optional[dict]:
    """A candidate's output dict, checked against its head's format (anything else makes the trial invalid)."""
    if o is None:
        return None
    if not isinstance(o, dict) or o.get("head") != head or set(o) != OUTPUT_KEYS[head]:
        raise TrialInvalid(f"output {str(o)[:120]} is not a {head} output with keys {sorted(OUTPUT_KEYS[head])}")
    for k, v in o.items():
        vals = v if isinstance(v, list) else [v]
        if k != "head" and not all(x is None or (isinstance(x, (int, float)) and not isinstance(x, bool)) for x in vals):
            raise TrialInvalid(f"output field {k}={str(v)[:60]} is not numeric")
    return o


def walk(conn, specs: list, rows: list, last_season: int) -> tuple[dict, dict, dict]:
    """engine.walk's season-start schedule for every spec at once, driven from here (season-major, so the candidate process
    never holds a label of a season it is forecasting, whichever spec it came with): for each season, every spec's fit gets
    only engine.training_rows; then forecasts are asked one cutoff at a time in time order, labels removed. Seasons after
    `last_season` are never sent."""
    from .candidates import engine, registry
    rows = sorted(rows, key=lambda r: (r.cutoff_at, r.tipoff_at, r.game_id))
    out = {s.name: {} for s in specs}
    fits = {s.name: [] for s in specs}
    seconds = {s.name: 0.0 for s in specs}
    residuals = {s.name: [] for s in specs}
    for season in sorted({r.season for r in rows if r.season <= last_season}):
        season_rows = [r for r in rows if r.season == season]
        train_rows = engine.training_rows(rows, season, season_rows[0].cutoff_at)
        active = {}
        for spec in specs:
            if len(train_rows) < registry.MIN_TRAIN[spec.sport]:
                continue
            t0 = time.time()
            fitted = _field(conn.call({"op": "fit", "spec": spec, "rows": train_rows}), "fitted")
            seconds[spec.name] += time.time() - t0
            fits[spec.name].append({"season": season, "fitted": bool(fitted), "training_games": len(train_rows),
                                    "results_through": max(r.result_available_at for r in train_rows)})
            if fitted:
                active[spec.name] = (spec, engine.residual_sd(residuals[spec.name], season) if spec.head == "normal" else None)
        for _cut, group in itertools.groupby(season_rows, key=lambda r: r.cutoff_at):
            group = list(group)
            stripped = [_strip(r) for r in group]
            for name, (spec, sd) in active.items():
                t0 = time.time()
                outs = _field(conn.call({"op": "predict", "spec": spec, "rows": stripped, "sd": sd}), "outputs")
                seconds[name] += time.time() - t0
                if not isinstance(outs, list) or len(outs) != len(group):
                    raise TrialInvalid(f"{name}: {len(outs) if isinstance(outs, list) else outs!r} outputs for {len(group)} rows")
                for r, o in zip(group, outs):
                    o = _clean_output(o, spec.head)
                    if o is not None and spec.head == "normal" and r.y is not None and o.get("m") is not None:
                        residuals[name].append((season, r.y["margin"] - o["m"], r.y["total"] - o["t"]))
                    if engine.ready(o):
                        out[name][r.game_id] = o
    return out, fits, {k: round(v, 1) for k, v in seconds.items()}


def _field(reply, key):
    if not isinstance(reply, dict) or key not in reply:
        raise TrialInvalid(f"malformed reply from the candidate process (no {key!r}): {str(reply)[:120]}")
    return reply[key]


def check_specs(sport: str, hello: dict) -> tuple[list, dict]:
    from .candidates import deep, learners, registry
    from .candidates.features import FEATURE_SETS
    from .candidates.registry import Spec
    specs = []
    for d in hello.get("specs") or []:
        try:
            s = Spec(**d)
        except TypeError as exc:
            raise TrialInvalid(f"not a registry.Spec: {exc}")
        if s.sport != sport:
            raise TrialInvalid(f"{s.name} is for {s.sport}, not {sport}")
        if s.head not in registry.HEAD_MARKETS or s.features not in FEATURE_SETS:
            raise TrialInvalid(f"{s.name}: unknown head {s.head} or feature set {s.features}")
        if set(FEATURE_SETS[s.features]) & set(FORBIDDEN_FEATURES):
            raise TrialInvalid(f"{s.name}: feature set {s.features} uses postgame goalie inputs (excluded by the protocol)")
        if s.family not in learners.SETTINGS and not deep.is_deep(s):
            raise TrialInvalid(f"{s.name}: unknown family {s.family}")
        specs.append(s)
    if not specs or len({s.name for s in specs}) != len(specs):
        raise TrialInvalid("candidates() must return at least one spec, with unique names")
    markets = hello.get("markets") or {}
    by_name = {s.name: s for s in specs}
    for m, name in markets.items():
        if m not in sports.SPORTS[sport].markets or name not in by_name or m not in by_name[name].markets:
            raise TrialInvalid(f"MARKETS maps {m} to {name}, which does not forecast it")
    return specs, markets


READ_DENIED = ("/Users", "/Volumes", "/private/tmp", "/private/var/folders")   # every user file, mount and temp dir


def _profile(tmp: Path) -> str:
    """Reads: everything outside READ_DENIED (the OS and its libraries) plus, inside it, only the Python install, its
    virtualenv, the app source and the trial's own tmp dir. Writes: only that tmp dir. No network."""
    q = lambda p: json.dumps(str(Path(p).resolve()))           # noqa: E731  (SBPL strings use the same escaping)
    allowed = sorted({sys.prefix, sys.base_prefix, str(Path(os.path.realpath(sys.executable)).parents[1]), str(APP_DIR), str(tmp)})
    lines = ["(version 1)", "(allow default)", "(deny network*)",
             "(deny file-write* (subpath \"/\"))",
             f"(allow file-write* (subpath {q(tmp)}) (literal \"/dev/null\") (regex #\"^/private/tmp/__KMP_REGISTERED_LIB_\"))",
             "(deny file-read* " + " ".join(f"(subpath {q(p)})" for p in READ_DENIED) + ")",
             "(allow file-read-metadata)",                       # stat only; contents stay denied
             "(allow file-read* " + " ".join(f"(subpath {q(p)})" for p in allowed) + f" (literal {q(APP_DIR.parent)}))"]
    return "\n".join(lines) + "\n"


def run_candidate(src: str, sport: str, data: dict, scored, timeout_s: float, tdir: Path) -> dict:
    """Runs train.py in the sandbox through the whole walk; returns per-market vectors or a failure status."""
    from .candidates import engine, registry
    if not Path(SANDBOX).is_file():
        raise Refused("no OS sandbox (sandbox-exec) on this machine; trials need one")
    tmp = tdir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    profile = tdir / "sandbox.sb"
    profile.write_text(_profile(tmp))
    r_req, w_req = os.pipe()
    r_resp, w_resp = os.pipe()
    boot = f"import sys; sys.path.insert(0, {str(APP_DIR.parent)!r}); from app.autoresearch import serve; serve({w_resp})"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp), "TMPDIR": str(tmp), "OMP_NUM_THREADS": "4",
           "PYTHONDONTWRITEBYTECODE": "1", "MPLCONFIGDIR": str(tmp)}
    t0 = time.time()
    log = (tdir / "run.log").open("w")
    proc = subprocess.Popen([SANDBOX, "-f", str(profile), sys.executable, "-I", "-B", "-c", boot], stdin=r_req, stdout=log,
                            stderr=subprocess.STDOUT, pass_fds=(w_resp,), env=env, cwd=str(tmp), start_new_session=True)
    os.close(r_req)
    os.close(w_resp)
    conn = _Remote(w_req, r_resp, t0 + timeout_s)
    res = {"status": "ok"}
    try:
        specs, mapping = check_specs(sport, conn.call({"source": src, "sport": sport, "seed": PROTOCOL["seed"]}))
        outs, fits, seconds = walk(conn, specs, data["rows"], max(scored))
        spec_info = [{**asdict(spec), "definition": registry.definition(spec), "seconds": seconds[spec.name],
                      "games": len(outs[spec.name])} for spec in specs]
        conn.call({"op": "end"})
        markets = {}
        for market in sports.SPORTS[sport].markets:
            name = mapping.get(market) or next((s.name for s in specs if market in s.markets), None)
            if name is None:
                continue
            vecs = {}
            for r in data["tables"][market]:
                o = outs[name].get(r["game_id"])
                if r["season"] in scored and o is not None:
                    try:
                        v = engine.vector(o, market, r["line"])
                    except Exception as exc:
                        raise TrialInvalid(f"{market} {r['game_id']}: output cannot be turned into a forecast ({exc})")
                    if v is not None:
                        vecs[r["game_id"]] = [float(x) for x in v]
            markets[market] = {"spec": name, "vectors": vecs}
        res |= {"markets": markets, "fits": fits, "specs": spec_info}
    except _Timeout:
        res = {"status": "timeout", "reason": "trial time budget exceeded"}
    except _ChildGone:
        res = {"status": "crash", "reason": "candidate process ended"}
    except TrialInvalid as exc:
        res = {"status": "invalid", "reason": str(exc)}
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)               # the whole group: no stray worker processes survive
        except (ProcessLookupError, PermissionError):         # macOS: EPERM once the group leader has already exited
            pass
        _, status, ru = os.wait4(proc.pid, 0)
        os.close(w_req)
        os.close(r_resp)
        log.close()
    if res["status"] == "crash":
        res["reason"] += f" (exit {os.waitstatus_to_exitcode(status)}): {(tdir / 'run.log').read_text()[-400:]}"
    res["resources"] = {"wall_s": round(time.time() - t0, 1), "cpu_s": round(ru.ru_utime + ru.ru_stime, 1),
                        "max_rss_gb": round(ru.ru_maxrss / (1024 ** 3 if sys.platform == "darwin" else 1024 ** 2), 2),
                        "timed_out": res["status"] == "timeout"}
    return res


# --- Validation and scoring ---------------------------------------------------------------------------------------------
def validate(data: dict, markets: dict, scored) -> list:
    problems = []
    for market, m in markets.items():
        if market not in data["tables"]:
            problems.append(f"{market}: not a {data['sport']} market")
            continue
        rows = {r["game_id"]: r for r in data["tables"][market] if r["season"] in scored}
        for gid, v in m["vectors"].items():
            r = rows.get(gid)
            if r is None:
                problems.append(f"{market}: forecast for unknown or out-of-scope game {gid}")
            elif not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)):
                problems.append(f"{market} {gid}: not a 3-way probability vector")
            elif not all(math.isfinite(x) and -1e-9 <= x <= 1 + 1e-9 for x in v) or abs(sum(v) - 1) > 1e-6:
                problems.append(f"{market} {gid}: probabilities not finite, in [0, 1] and summing to 1")
            elif market != "reg3" and (r["line"] is None or r["line"] % 1) and v[1] > 1e-9:
                problems.append(f"{market} {gid}: push probability at a line where a push is impossible")
            if len(problems) >= 20:
                return problems + ["(further problems not listed)"]
    return problems


def _summary(market, rows, key) -> dict:
    from . import combine
    wins = losses = ties = 0
    for r in rows:
        s = combine.pick(key(r), market)
        if s is None:
            ties += 1
        elif not (market != "reg3" and r["realised"] == 1):
            wins += s == r["realised"]
            losses += s != r["realised"]
    n = len(rows)
    return {"games": n, "log_loss": sum(combine.log_loss(key(r), r["realised"]) for r in rows) / n,
            "brier": sum(combine.brier(key(r), r["realised"]) for r in rows) / n, "wins": wins, "losses": losses,
            "abstentions": ties, "accuracy": wins / (wins + losses) if wins + losses else None}


def _vec_of(inc: dict) -> Callable:
    return (lambda r: inc["vectors"][r["game_id"]]) if inc.get("trial") else (lambda r: r["vectors"][inc["member"]])


def score_market(data: dict, market: str, cand: dict, incumbent: dict, scored) -> dict:
    """Candidate vs incumbent (and, for information, vs the primary) on the market's fixed cohort. Missing forecasts are
    a coverage failure, never dropped from the comparison."""
    from . import backtest, combine
    rows = cohort(data, market, scored)
    prim = data["primary"][market]
    inc_vec = _vec_of(incumbent)
    covered = [r for r in rows if r["game_id"] in cand]
    out = {"cohort": len(rows), "forecast": len(covered), "coverage": len(covered) / len(rows) if rows else 0.0,
           "incumbent": incumbent["label"], "primary": prim}
    if not rows:
        return out | {"decision": "reject", "reason": "empty cohort"}
    if covered:
        out["candidate_matched"] = _summary(market, covered, lambda r: cand[r["game_id"]])
        out["incumbent_matched"] = _summary(market, covered, inc_vec)
    if len(covered) < len(rows):
        return out | {"decision": "reject", "reason": f"coverage loss: {len(rows) - len(covered)} of {len(rows)} cohort games "
                                                      "without a forecast (matched metrics shown for information only)"}
    cuts = [r["cutoff_at"] for r in rows]
    diffs = [combine.log_loss(cand[r["game_id"]], r["realised"]) - combine.log_loss(inc_vec(r), r["realised"]) for r in rows]
    out["vs_incumbent"] = backtest.block_bootstrap(diffs, cuts)
    out["vs_primary"] = backtest.block_bootstrap(
        [combine.log_loss(cand[r["game_id"]], r["realised"]) - combine.log_loss(r["vectors"][prim], r["realised"]) for r in rows], cuts)
    out["per_season"] = {s: {"games": len(g), "minus_incumbent": sum(d for d, r in zip(diffs, rows) if r["season"] == s) / len(g)}
                         for s in sorted({r["season"] for r in rows}) for g in [[r for r in rows if r["season"] == s]]}
    upper = out["vs_incumbent"]["ci95"][1]
    ca, ia = out["candidate_matched"]["accuracy"], out["incumbent_matched"]["accuracy"]
    if upper >= 0:
        return out | {"decision": "reject", "reason": f"log-loss difference upper bound {upper:+.5f} is not below 0"}
    if ca is None or ia is None:        # §6 needs both accuracies; an undefined one is not treated as a pass
        return out | {"decision": "reject", "reason": f"accuracy comparison undefined (candidate {ca}, incumbent {ia}: "
                                                      "no decided picks)"}
    if ca < ia:
        return out | {"decision": "reject", "reason": f"matched accuracy {ca:.4f} below the incumbent's {ia:.4f}"}
    return out | {"decision": "keep", "reason": f"upper bound {upper:+.5f} < 0 and accuracy {ca:.4f} >= {ia:.4f}"}


def agreement(data: dict, market: str, cand: dict, scored) -> dict:
    """Descriptive: agreement groups with and without the candidate at the sealed thresholds (agreement_tracking).
    A group needs every member (missing member -> incomplete, never a smaller group); same game, market and line."""
    from . import agreement_tracking as at
    rows = cohort(data, market, scored)
    prim = data["primary"][market]
    existing = data["members"]["winner" if market == "winner" else "lines"]
    groups = {"primary": {"kind": "single", "members": [prim]},
              "pair:candidate+primary": {"kind": "unanimous", "members": ["candidate", prim]},
              f"majority:existing({len(existing)})": {"kind": "majority", "members": list(existing)},
              f"majority:candidate+existing({len(existing) + 1})": {"kind": "majority", "members": ["candidate", *existing]}}
    out = {}
    for label, g in groups.items():
        res = []
        for r in rows:
            vecs = dict(r["vectors"])
            if r["game_id"] in cand:
                vecs["candidate"] = tuple(cand[r["game_id"]])
            res.append((r, at.classify(g, vecs, market)))
        complete = [(r, c) for r, c in res if c["status"] != "incomplete"]
        cells = {}
        for t in at.THRESHOLDS[at.kind_of(market)]:
            picks = [(r, c) for r, c in complete if c["side"] is not None and c["statistic"] >= t
                     and not (market != "reg3" and r["realised"] == 1)]
            wins = sum(c["side"] == r["realised"] for r, c in picks)
            cells[str(t)] = {"picks": len(picks), "coverage": len(picks) / len(rows) if rows else 0.0,
                             "accuracy": wins / len(picks) if picks else None}
        out[label] = {"members": g["members"], "kind": g["kind"], "complete": len(complete), "incomplete": len(res) - len(complete),
                      "thresholds": cells}
    return out


# --- Trial log (append-only) --------------------------------------------------------------------------------------------
def _records(root: Path) -> list:
    p = root / "trials.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.is_file() else []


def _append(root: Path, rec: dict) -> None:
    with (root / "trials.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _tsv(root: Path, rec: dict) -> None:
    """upstream's results.tsv (commit, metric, memory, status, description), with sport and the kept markets' deltas."""
    p = root / "results.tsv"
    if not p.is_file():
        p.write_text("commit\tsport\tkept_vs_incumbent\tpeak_rss_gb\tstatus\tdescription\n")
    kept = [f"{m}:{s['vs_incumbent']['mean']:+.5f}" for m, s in (rec.get("markets") or {}).items() if s.get("decision") == "keep"]
    status = rec["status"] + (f" (cached {rec['cached_from']})" if rec.get("cached_from") else "")
    with p.open("a") as fh:
        fh.write(f"{rec['commit'][:7]}\t{rec['sport']}\t{','.join(kept) or '-'}\t"
                 f"{(rec.get('resources') or {}).get('max_rss_gb', 0.0):.2f}\t{status}\t"
                 f"{rec.get('description', '').replace(chr(9), ' ')}\n")


def _finish(root: Path, rec: dict) -> dict:
    _append(root, rec)
    _tsv(root, rec)
    return rec


def mark_interrupted(root: Path) -> list:
    """A `started` without a terminal record was interrupted (the lock is held, so no run is in progress)."""
    recs = _records(root)
    done = {r["trial_id"] for r in recs if r["event"] != "started"}
    return [_finish(root, {**{k: r[k] for k in ("trial_id", "key", "commit", "sport", "protocol_id", "agent_run", "description")
                              if k in r}, "event": "finished",
                           "status": "interrupted", "finished_at": _now(), "reason": "no terminal record (process stopped)"})
            for r in recs if r["event"] == "started" and r["trial_id"] not in done]


def incumbents(root: Path, sport: str, meta: dict) -> dict:
    """Per market: the last kept trial (replayed from the append-only log), else the best existing member."""
    cur = {m: {"trial": None, "member": s["member"], "label": f"existing member {s['member']}", "vectors": None}
           for m, s in meta["start_incumbent"].items()}
    for r in _records(root):
        if r["event"] == "finished" and r["status"] == "keep" and not r.get("cached_from"):
            for m, s in r["markets"].items():
                if s.get("decision") == "keep":
                    cur[m] = {"trial": r["trial_id"], "label": f"trial {r['trial_id']} ({r['commit'][:7]})",
                              "commit": r["commit"], "predictions": r["predictions"]}
    for m, c in cur.items():
        if c["trial"]:
            p = Path(c["predictions"])
            if not p.is_file():
                raise Refused(f"predictions of incumbent trial {c['trial']} are missing ({p})")
            c["vectors"] = json.loads(gzip.decompress(p.read_bytes()))["markets"][m]["vectors"]
    return cur


def _meta(exp_root: Path, sport: str) -> tuple[str, Path, dict]:
    pid, root, _snap = _paths(exp_root, sport)
    meta_path = root / "protocol.json"
    if not meta_path.is_file():
        raise Refused(f"protocol {pid} not prepared for {sport} (app code, libraries, snapshot or protocol changed?); "
                      "run `prepare`")
    return pid, root, json.loads(meta_path.read_text())


# --- Trial (upstream: one `uv run train.py`, then keep or reset) ------------------------------------------------------
def run_trial(sport: str, exp_root: Path, workspace: Path, description: str = "", retry: bool = False,
              timeout_s: Optional[int] = None, commit: Optional[str] = None) -> dict:
    """One trial of `commit` (default: the workspace HEAD, which must have no uncommitted changes). Reads the commit from
    git objects, so it never touches the workspace's working tree."""
    from . import research_agent
    guard_paths(exp_root)
    pid, root, meta = _meta(exp_root, sport)
    trust_workspace(workspace, meta.get("workspace_git"))    # before any git call on the workspace
    research_agent.trial_gate(exp_root)                   # stop switch, and an agent run's trial and time budget
    with research_agent._Slot(exp_root), Lock(root):
        mark_interrupted(root)
        gate = research_agent.trial_gate(exp_root)        # again under the lock: the budget count is now exact
        if gate:
            timeout_s = min(timeout_s or gate["timeout_s"], gate["timeout_s"])
        cache = Path(meta["cache"]["file"])
        if _sha(cache.read_bytes()) != meta["cache"]["sha256"]:
            raise Refused("frozen dataset changed since registration")
        if commit is None:
            dirty = [x for x in _git(workspace, "status", "--porcelain", "--untracked-files=no").splitlines() if x.strip()]
            if dirty:
                raise Refused(f"workspace has uncommitted changes ({dirty}); commit train.py first (upstream: commit, run)")
            commit = "HEAD"
        description = description or _git(workspace, "log", "-1", "--format=%s", commit)   # the proposal's own message
        try:
            state = commit_state(workspace, meta["workspace_base"], commit)
        except (subprocess.CalledProcessError, UnicodeDecodeError) as exc:
            sha = _git(workspace, "rev-parse", "--verify", f"{commit}^{{commit}}")
            if any(r["commit"] == sha for r in _records(root) if r["event"] == "finished"):
                return {**[r for r in _records(root) if r["commit"] == sha][-1], "cached": True}
            return _finish(root, {"trial_id": f"{_now().replace(':', '').replace('-', '')}-unreadable", "key": sha[:16],
                                  "commit": sha, "sport": sport, "protocol_id": pid, "event": "finished", "status": "invalid",
                                  "description": description, "finished_at": _now(),
                                  "reason": f"{EDITABLE} or a protected file is missing or not UTF-8 in this commit "
                                            f"({type(exc).__name__})"})
        inc = incumbents(root, sport, meta)
        inc_state = {m: c["trial"] or c["member"] for m, c in inc.items()}
        seed = PROTOCOL["seed"]
        key = _sha(json.dumps([pid, sport, state["train_sha256"], state["protected"], state["changed"], seed, inc_state],
                              sort_keys=True).encode())[:16]
        failed = ("crash", "timeout", "interrupted")
        done = [r for r in _records(root) if r["event"] == "finished"]
        mine = [r for r in done if r["commit"] == state["commit"]]
        if mine:                              # a commit is evaluated once (upstream); only a failed attempt may be retried
            if not retry or mine[-1]["status"] not in failed:
                return {**mine[-1], "cached": True}
            if sum(r["status"] in failed for r in mine) > BUDGETS["max_retries"]:
                return {**mine[-1], "cached": True, "note": "retry budget exhausted"}
        else:
            same = [r for r in done if r["key"] == key and not r.get("cached_from") and r["status"] not in failed]
            if same:                          # identical code, files and incumbents: identical evaluation; recorded, not re-run
                rec = {**{k: v for k, v in same[-1].items() if k not in ("started_at", "finished_at")},
                       "commit": state["commit"], "description": description, "cached_from": same[-1]["trial_id"],
                       "finished_at": _now()}
                return {**_finish(root, rec), "cached": True}
        prior = [r for r in mine if r["status"] in failed]
        timeout_s = min(timeout_s or BUDGETS["trial_timeout_s"], BUDGETS["trial_timeout_s"])
        trial_id = f"{_now().replace(':', '').replace('-', '')}-{key[:8]}"
        tdir = root / "trials" / trial_id
        tdir.mkdir(parents=True)
        base = {"trial_id": trial_id, "key": key, "commit": state["commit"], "sport": sport, "protocol_id": pid,
                **({"agent_run": gate["run_id"]} if gate else {})}
        _append(root, {**base, "event": "started", "started_at": _now(), "description": description,
                       "train_sha256": state["train_sha256"], "seed": seed, "incumbents": inc_state, "timeout_s": timeout_s,
                       "data": {"snapshot": meta["snapshot"]["sha256"], "cache": meta["cache"]["sha256"],
                                "dataset_version": meta["dataset_version"]},
                       "cutoff": PROTOCOL["cutoff"], "attempt": len(prior) + 1})
        rec = {**base, "event": "finished", "description": description, "train_sha256": state["train_sha256"], "seed": seed,
               "incumbents": inc_state}
        problems = [f"{f} differs from the registered protected file" for f, h in state["protected"].items()
                    if h != meta["protected"][f]]
        extra = [f for f in state["changed"] if f not in (EDITABLE, *PROTECTED_WORKSPACE)]
        problems += [f"files other than {EDITABLE} changed since the protocol base: {extra}"] if extra else []
        problems += static_check(state["train_src"])
        if problems:
            return _finish(root, rec | {"status": "invalid", "reason": "; ".join(problems), "finished_at": _now()})
        (tdir / EDITABLE).write_text(state["train_src"])
        data = pickle.loads(cache.read_bytes())
        scored = PROTOCOL["scored_seasons"][sport]
        res = run_candidate(state["train_src"], sport, data, scored, timeout_s, tdir)
        rec["resources"] = res["resources"]
        if res["status"] != "ok":
            return _finish(root, rec | {"status": res["status"], "reason": res["reason"], "finished_at": _now()})
        problems = validate(data, res["markets"], scored)
        if problems:
            return _finish(root, rec | {"status": "invalid", "reason": "; ".join(problems), "finished_at": _now()})
        packed = tdir / "predictions.json.gz"
        packed.write_bytes(gzip.compress(json.dumps({"markets": res["markets"]}).encode()))
        markets, agree = {}, {}
        for market in sports.SPORTS[sport].markets:
            cand = res["markets"].get(market, {}).get("vectors", {})
            markets[market] = score_market(data, market, cand, inc[market], scored) | {"spec": res["markets"].get(market, {}).get("spec")}
            agree[market] = agreement(data, market, cand, scored)
        kept = [m for m, s in markets.items() if s["decision"] == "keep"]
        n_trials = len({r["train_sha256"] for r in _records(root) if r["event"] == "started"})
        rec |= {"status": "keep" if kept else "discard", "markets": markets, "agreement": agree, "specs": res["specs"],
                "fits": res["fits"], "predictions": str(packed), "finished_at": _now(),
                "reason": f"kept in {kept} (vs the incumbent; exploratory)" if kept else
                          "no market passed the keep rule (no improvement found)",
                "search_note": f"exploratory: {n_trials} distinct train.py version(s) searched this protocol's validation "
                               "seasons; a keep is not independent evidence and never a promotion"}
        _finish(root, rec)
        (tdir / "result.json").write_text(json.dumps(rec, indent=1, sort_keys=True, default=str))
        return rec


def summary(rec: dict) -> str:
    """upstream's end-of-run block (`grep "^status:" run.log`)."""
    lines = ["---", f"status:           {rec['status']}" + (f" (cached from {rec['cached_from']})" if rec.get("cached_from") else ""),
             f"sport:            {rec['sport']}", f"commit:           {rec['commit'][:7]}", f"trial_id:         {rec['trial_id']}"]
    for m, s in (rec.get("markets") or {}).items():
        d, p = (s.get("vs_incumbent") or {}), (s.get("vs_primary") or {})
        lines.append(f"market_{m}: {s['decision']} coverage={s['forecast']}/{s['cohort']} "
                     f"delta={d.get('mean', float('nan')):+.5f} ci95={d.get('ci95')} vs={s['incumbent']} "
                     f"(vs primary {p.get('mean', float('nan')):+.5f})")
    res = rec.get("resources") or {}
    lines += [f"wall_seconds:     {res.get('wall_s')}", f"peak_rss_gb:      {res.get('max_rss_gb')}",
              f"reason:           {rec.get('reason')}"]
    return "\n".join(lines)


# --- Daily entry point (bounded; never launches an agent, never checks anything out) ----------------------------------
def daily(sports_: list, exp_root: Path, workspace: Path, max_trials: int, max_minutes: float, timeout_s: int,
          ref: str = "HEAD") -> dict:
    """Evaluates the commits between the protocol base and `ref` that have no record yet (oldest first) within the budget,
    then writes a status report. 'No improvement found' is a valid outcome. Starting an agent session is a separate step."""
    t0 = time.time()
    for sport in sports_:                                 # registers or loads each protocol: trusts the workspace's git first
        prepare(sport, exp_root, workspace)
    report = {"started_at": _now(), "ref": _git(workspace, "rev-parse", ref),
              "budget": {"max_trials": max_trials, "max_minutes": max_minutes, "trial_timeout_s": timeout_s},
              "sports": {}, "trials": [], "stopped": "queue empty"}
    runs = 0
    for sport in sports_:
        try:
            meta = prepare(sport, exp_root, workspace)
            root = exp_root / meta["protocol_id"] / sport
            commits = [meta["workspace_base"], *_git(workspace, "rev-list", "--reverse",
                                                    f"{meta['workspace_base']}..{report['ref']}").split()]
            seen = {r["commit"] for r in _records(root) if r["event"] == "finished"}
            for c in [c for c in commits if c not in seen]:
                if runs >= max_trials:
                    report["stopped"] = "trial budget reached"
                    break
                if (time.time() - t0) / 60 >= max_minutes:
                    report["stopped"] = "time budget reached"
                    break
                rec = run_trial(sport, exp_root, workspace, description=_git(workspace, "log", "-1", "--format=%s", c),
                                timeout_s=timeout_s, commit=c)
                runs += 1 if rec.get("resources") and not rec.get("cached") else 0   # only executed candidates count
                report["trials"].append({k: rec.get(k) for k in ("trial_id", "sport", "commit", "status", "reason", "cached_from")}
                                        | {"kept": [m for m, s in (rec.get("markets") or {}).items() if s.get("decision") == "keep"]})
        except Refused as exc:                    # this sport cannot run now (e.g. its lock is held); the others still can
            report.setdefault("refused", {})[sport] = str(exc)
            continue
        report["sports"][sport] = status(exp_root, sport)
    report["finished_at"] = _now()
    kept = [f"{t['sport']}:{m}" for t in report["trials"] if not t.get("cached_from") for m in t["kept"]]
    report["outcome"] = (f"kept vs incumbent (exploratory, not a promotion): {', '.join(kept)}" if kept
                         else "no improvement found")
    out = exp_root / "reports" / f"daily-{report['started_at'].replace(':', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    report["report_file"] = str(out)
    return report


def status(exp_root: Path, sport: str) -> dict:
    pid, root, meta = _meta(exp_root, sport)
    recs = [r for r in _records(root) if r["event"] == "finished"]
    counts = {}
    for r in recs:
        k = r["status"] + (" (cached)" if r.get("cached_from") else "")
        counts[k] = counts.get(k, 0) + 1
    return {"protocol_id": pid, "experiment_dir": str(root), "trials": len(recs), "by_status": counts,
            "incumbents": {m: c["label"] for m, c in incumbents(root, sport, meta).items()},
            "holdout": [h["event"] for h in _holdout_log(root)],
            "promotion": "none (research only; manual §6 on the forward record)"}


# --- Protected holdout (once per protocol and sport, for the kept incumbents together) --------------------------------
def _holdout_log(root: Path) -> list:
    p = root / "holdout_access.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.is_file() else []


def _holdout_append(root: Path, rec: dict) -> None:
    with (root / "holdout_access.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _ask_tty(phrase: str) -> bool:
    """A typed confirmation at a terminal. This stops accidental and non-interactive use; it does not stop an agent that
    drives a pseudo-terminal with the user's shell (only a separate OS user would). program.md forbids the agent to run it."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    print(f"This spends the one-shot holdout. Type exactly: {phrase}")
    return input("> ").strip() == phrase


def _sport_ledger(exp_root: Path, sport: str) -> list:
    """Holdout uses for a sport under EVERY protocol in this experiment root (a new protocol id is not a fresh holdout)."""
    return [{**h, "protocol_id": p.parent.parent.name} for p in sorted(exp_root.glob(f"*/{sport}/holdout_access.jsonl"))
            for h in _holdout_log(p.parent) if h["event"] == "completed"]


def holdout(sport: str, exp_root: Path, workspace: Path, ask: Callable[[str], bool] = _ask_tty,
            reuse_reason: Optional[str] = None) -> dict:
    """Scores every market's kept incumbent (each with its own trial's saved train.py) on the reserved seasons, once. The
    access is logged before anything is computed; a run that fails before scoring is logged as failed and may be repeated.
    A sport's holdout used under an earlier protocol needs a stated reason, which is logged."""
    guard_paths(exp_root)
    pid, root, meta = _meta(exp_root, sport)
    with Lock(root):
        log = _holdout_log(root)
        if any(h["event"] == "completed" for h in log):
            raise Refused(f"holdout already used for this protocol: {[h for h in log if h['event'] == 'completed'][0]}")
        earlier = _sport_ledger(exp_root, sport)
        if earlier and not reuse_reason:
            raise Refused(f"the {sport} holdout was already used under {[h['protocol_id'] for h in earlier]}; reusing it "
                          "needs --reuse-reason (logged; results then are not an independent check)")
        if log and log[-1]["event"] == "pending":
            _holdout_append(root, {"event": "failed", "at": _now(), "reason": "no terminal record (process stopped)"})
        if sum(h["event"] == "failed" for h in _holdout_log(root)) > BUDGETS["max_retries"]:
            raise Refused("holdout failed too often; investigate before another attempt")
        inc = incumbents(root, sport, meta)
        kept = {m: c for m, c in inc.items() if c["trial"]}
        if not kept:
            raise Refused("no market has a kept trial: nothing to test on the holdout")
        sources = {}
        for c in kept.values():
            saved = root / "trials" / c["trial"] / EDITABLE
            rec = next(r for r in _records(root) if r["trial_id"] == c["trial"] and r["event"] == "finished")
            if not saved.is_file() or _sha(saved.read_bytes()) != rec["train_sha256"]:
                raise Refused(f"saved train.py of trial {c['trial']} is missing or differs from the logged sha256")
            sources[c["trial"]] = saved.read_text()
        if not ask(f"spend holdout {sport} {pid}"):
            raise Refused("holdout not confirmed at a terminal")
        _holdout_append(root, {"event": "pending", "at": _now(), "markets": {m: c["trial"] for m, c in kept.items()},
                               "reuse_reason": reuse_reason, "earlier_uses": earlier})
        try:
            data = build_dataset(sport, PROTOCOL["holdout_period"])
            scored = PROTOCOL["holdout_seasons"][sport]
            hdir = root / "holdout"
            vectors = {}
            for trial, src in sources.items():
                res = run_candidate(src, sport, data, scored, BUDGETS["trial_timeout_s"], hdir / trial)
                problems = validate(data, res["markets"], scored) if res["status"] == "ok" else []
                if res["status"] != "ok" or problems:
                    raise RuntimeError(f"trial {trial} on the holdout: {res.get('reason') or problems[:3]}")
                for m, c in kept.items():
                    if c["trial"] == trial:
                        vectors[m] = res["markets"].get(m, {}).get("vectors", {})
            out = {"status": "scored", "protocol_id": pid, "seasons": scored, "markets": {}, "earlier_uses": earlier,
                   "note": PROTOCOL["holdout_note"] + " Report only: nothing is promoted."}
            for m, c in kept.items():
                bar = meta["start_incumbent"][m]["member"]
                rows = cohort(data, m, scored)
                if all(bar in r["vectors"] for r in rows):
                    ref = {"trial": None, "member": bar, "label": f"existing member {bar}"}
                else:                         # the bar member lacks holdout games: compare with the primary, said so
                    prim = data["primary"][m]
                    ref = {"trial": None, "member": prim, "label": f"primary {prim} (bar member {bar} lacks holdout games)"}
                out["markets"][m] = {"trial": c["trial"], "vs_reference": score_market(data, m, vectors[m], ref, scored)}
            p = root / "holdout" / "result.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(out, indent=1, default=str))
            _holdout_append(root, {"event": "completed", "at": _now(), "result": str(p)})
            return out
        except Exception as exc:
            _holdout_append(root, {"event": "failed", "at": _now(), "reason": f"{type(exc).__name__}: {exc}"[:500]})
            raise


# --- CLI -------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.autoresearch", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "trial", "status", "daily", "holdout", "agent", "stop"):
        s = sub.add_parser(name)
        s.add_argument("--exp-root", type=Path, required=True)
        s.add_argument("--workspace", type=Path, required=True)
        s.add_argument("--sport", choices=sorted(sports.SPORTS), action="append")
        s.add_argument("--description", default="")
        s.add_argument("--retry", action="store_true")
        s.add_argument("--timeout", type=int, default=BUDGETS["trial_timeout_s"])
        s.add_argument("--max-trials", type=int, default=BUDGETS["max_trials"])
        s.add_argument("--max-minutes", type=float, default=BUDGETS["max_minutes"])
        s.add_argument("--reuse-reason")
        s.add_argument("--max-usd", type=float)
        s.add_argument("--model")
        s.add_argument("--reason", default="stopped by a person")
        s.add_argument("--clear", action="store_true")
    a = p.parse_args(argv)
    chosen = a.sport or ["nba", "nhl"]
    try:
        if a.cmd == "prepare":
            for s in chosen:
                m = prepare(s, a.exp_root, a.workspace)
                print(json.dumps({k: m[k] for k in ("protocol_id", "sport", "cohort", "seasons_loaded", "build_seconds")}
                                 | {"bar": {k: (v["member"], round(v["log_loss"], 5)) for k, v in m["start_incumbent"].items()}}))
        elif a.cmd == "trial":
            for s in chosen:
                print(summary(run_trial(s, a.exp_root, a.workspace, a.description, a.retry, a.timeout)), flush=True)
        elif a.cmd == "status":
            print(json.dumps({s: status(a.exp_root, s) for s in chosen}, indent=1))
        elif a.cmd == "daily":
            r = daily(chosen, a.exp_root, a.workspace, a.max_trials, a.max_minutes, a.timeout)
            print(json.dumps({k: r[k] for k in ("outcome", "stopped", "trials", "report_file", "sports")}, indent=1, default=str))
        elif a.cmd == "agent":
            from . import research_agent as ra
            r = ra.run_agent(chosen, a.exp_root, a.workspace, max_trials=min(a.max_trials, ra.AGENT["max_trials"]),
                             max_minutes=min(a.max_minutes, ra.AGENT["max_minutes"]),
                             max_usd=min(a.max_usd or ra.AGENT["max_usd"], ra.AGENT["max_usd"]), model=a.model)
            print(json.dumps({k: r.get(k) for k in ("run_id", "branch", "stopped", "outcome", "trials", "commits", "cost_usd",
                                                    "minutes", "sweep")}, indent=1, default=str))
        elif a.cmd == "stop":                     # the kill switch must work without a data directory
            from . import research_agent as ra
            print(json.dumps(ra.stop(a.exp_root, a.reason, a.clear), indent=1))
        elif a.cmd == "holdout":
            print(json.dumps(holdout(chosen[0], a.exp_root, a.workspace, reuse_reason=a.reuse_reason), indent=1, default=str))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":                 # one module instance: research_agent imports app.autoresearch (its Refused)
    from app.autoresearch import main as _main
    raise SystemExit(_main())
