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
from datetime import datetime, timedelta, timezone
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
    if not config.db_path(sport).is_file():
        raise Refused(f"no {sport} store at {config.db_path(sport)}")      # never let sqlite create an empty file
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
                "web.", "browser.", "url.", "http.", "ssh.", "protocol.",
                # where git works and how it reads the repository; and keys inert for the driver's calls but refused anyway
                "core.worktree", "extensions.", "log.showsignature", "commit.gpgsign", "tag.gpgsign",
                "trailer.", "remote.", "core.alternaterefscommand")


def inspect_workspace_git(ws: Path) -> list:
    """What would make git run something in this workspace: program-valued local config, hooks (other than the samples git
    installs), info files other than `exclude`, alternates, config.worktree, any .gitattributes."""
    ws = Path(ws)
    g = ws / ".git"
    pairs = [(line.split("=", 1) + [""])[:2] for line in _git(ws, "config", "--local", "--list").splitlines()]
    found = [f"config {k.lower()}" for k, v in pairs if k.lower().startswith(PROGRAM_KEYS)
             or (k.lower() == "core.bare" and v.strip().lower() not in ("false", "no", "off", "0"))]   # init: false
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
    ws = Path(ws).resolve()               # git dir and work tree always explicit: core.worktree cannot point git elsewhere
    return subprocess.run(["git", f"--git-dir={ws / '.git'}", f"--work-tree={ws}", "-C", str(ws), *GIT_SAFE, *args],
                          check=True, capture_output=True, text=text, env=env).stdout


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
    return out | keep_decision(out["vs_incumbent"]["ci95"][1], out["candidate_matched"]["accuracy"],
                               out["incumbent_matched"]["accuracy"])


def keep_decision(upper: float, ca: Optional[float], ia: Optional[float]) -> dict:
    """The MODEL_SELECTION §6 keep rule (shared by the protocol-v2 trials and the v3 evaluator): the paired log-loss
    difference's 95% upper bound is below 0 and the matched pick accuracy is not lower."""
    if upper >= 0:
        return {"decision": "reject", "reason": f"log-loss difference upper bound {upper:+.5f} is not below 0"}
    if ca is None or ia is None:        # §6 needs both accuracies; an undefined one is not treated as a pass
        return {"decision": "reject", "reason": f"accuracy comparison undefined (candidate {ca}, incumbent {ia}: "
                                                "no decided picks)"}
    if ca < ia:
        return {"decision": "reject", "reason": f"matched accuracy {ca:.4f} below the incumbent's {ia:.4f}"}
    return {"decision": "keep", "reason": f"upper bound {upper:+.5f} < 0 and accuracy {ca:.4f} >= {ia:.4f}"}


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


# =============================================================================================================================
# Protocol v3: research evaluation for all five sports (NBA, NHL, NFL, Soccer, NCAAB). Adapter-driven: each sport supplies a
# `research_adapter()` (contract in docs/AUTORESEARCH.md "Adapter contract"); everything below is the consumer. Protocol v2
# (agent trials, NBA/NHL) above is untouched and keeps its own evidence. Nothing here writes an official store, forecast
# table, record or card; nothing promotes: the output is a recommendation object a person decides on.
# =============================================================================================================================
FIVE_SPORTS = ("nba", "nhl", "nfl", "soccer", "ncaab")
ISO = "%Y-%m-%dT%H:%M:%SZ"
SETTLE_LAG_HOURS = 6                      # a baseline may use a game's outcome only this long after its start
BINS = 10
MIN_SCORED = 50                           # fewer scored games than this in a comparison: no keep decision

# Promotion criteria, fixed BEFORE any forward result exists (docs/AUTORESEARCH.md "Promotion criteria"). Changing them changes
# the protocol name and starts a new experiment; the code only produces a recommendation, never a promotion.
PROMOTION = {
    "min_settled_forward": {"default": 200, "nfl": 100},     # per market, candidate and incumbent both graded
    "paired_test": "week-block bootstrap 95% upper bound of (candidate - incumbent) log loss < 0 on matched forward games",
    "calibration": "candidate ECE <= incumbent ECE + 0.01 (10 equal-width bins)",
    "accuracy": "candidate matched pick accuracy >= incumbent's (MODEL_SELECTION §6)",
    "sportsbook": {"min_quotes": 100, "rule": "where at least 100 matched, valid, non-synthetic quotes exist (retrieved at or "
                   "before the forecast cutoff, same line): the 95% lower bound of (candidate - book no-vig) log loss must be "
                   "<= 0 (not demonstrably worse); below 100 quotes the check is reported as not evaluable, not as passed"},
    "confirmatory_look": "the first look at which a market reaches its minimum settled count; every other look is exploratory",
    "never_promotes": True,
}
PROTOCOL_V3 = {
    "name": "sports-research-v3",
    "sports": list(FIVE_SPORTS),
    "stages": {"search": "chronological: candidates are ranked here", "validation": "chronological, after search: confirms",
               "holdout": "reserved, read once per protocol and sport (logged), never by search or daily runs",
               "forward": "prospective: candidate forecasts stored before the game, graded after it"},
    "keep_rule": "MODEL_SELECTION §6 via keep_decision(): paired log-loss difference week-block bootstrap 95% upper bound < 0 "
                 f"and matched pick accuracy not lower, against the best existing member (incumbent), on BOTH the search "
                 f"and the validation stage, at least {MIN_SCORED} scored games, every incumbent game forecast",
    "metrics": ["n", "coverage", "brier (3-vector convention)", "log_loss", "calibration bins + ECE", "score MAE/RMSE where "
                "point predictions exist", "paired comparison vs incumbent, baselines and sportsbook no-vig"],
    "baselines": {"baseline:uniform": "equal probability per allowed side",
                  "baseline:base_rate": f"smoothed expanding outcome frequency, outcomes used {SETTLE_LAG_HOURS} h after the "
                                        "game start"},
    "returns": "only at executable, non-synthetic quotes retrieved at or before the forecast cutoff with the same line: flat "
               "1 unit on the model's pick when model probability x decimal price > 1; pushes refund, voids excluded; "
               "synthetic or non-executable quotes are counted, never returned",
    "seed": 20261122,
    "promotion": PROMOTION,
}
BUDGET_V3 = {"max_items_per_sport": 6, "max_seconds_per_sport": 900}


class AdapterInvalid(RuntimeError):
    """An adapter or its records break the contract (shape, probabilities, information cutoff): the sport run fails."""


def _iso(ts) -> str:
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(ISO)


def _key(r: dict) -> tuple:
    line = r.get("line")
    return (r["game_id"], r["market"], None if line is None else round(float(line), 4))


def _vec3(r: dict) -> tuple:
    p = r["probs"]
    return (float(p["a"]), float(p.get("mid", 0.0)), float(p["b"]))


def _outcome_idx(r: dict, kind: str) -> Optional[int]:
    """Index into the 3-vector of a scoreable outcome, else None (unsettled, void, or a push nobody priced)."""
    o = r.get("outcome")
    if o == "a":
        return 0
    if o == "b":
        return 2
    if o == "mid" and kind == "three_way":
        return 1
    if o == "push" and kind == "line" and "mid" in r["probs"]:
        return 1
    return None


def _sides(kind: str) -> tuple:
    return (0, 1, 2) if kind == "three_way" else (0, 2)


def _pick(vec, kind):
    allowed = _sides(kind)
    best = max(vec[i] for i in allowed)
    top = [i for i in allowed if abs(vec[i] - best) <= 1e-12]
    return top[0] if len(top) == 1 else None


def _ll(vec, idx: int) -> float:
    return -math.log(max(1e-15, vec[idx]))


def _brier(vec, idx: int) -> float:
    return sum((vec[i] - (1.0 if i == idx else 0.0)) ** 2 for i in range(3))


def validate_adapter(adapter: dict) -> None:
    for k in ("sport", "data_version", "markets", "unsupported", "periods", "models", "walk"):
        if k not in adapter:
            raise AdapterInvalid(f"adapter lacks {k!r}")
    if adapter["sport"] not in FIVE_SPORTS:
        raise AdapterInvalid(f"unknown sport {adapter['sport']!r}")
    for m, spec in adapter["markets"].items():
        if spec.get("kind") not in ("binary", "three_way", "line"):
            raise AdapterInvalid(f"market {m}: kind must be binary, three_way or line")
    per = adapter["periods"]
    if not all(k in per for k in ("search", "validation", "holdout")):
        raise AdapterInvalid("periods need search, validation and holdout")
    seen = {}
    for name in ("search", "validation", "holdout"):
        for s in per[name]:
            if s in seen:
                raise AdapterInvalid(f"season {s} is in both {seen[s]} and {name}")
            seen[s] = name
    if max(max(per["search"], default=0), max(per["validation"], default=0)) >= min(per["holdout"], default=10 ** 9):
        raise AdapterInvalid("holdout seasons must come after the search and validation seasons")
    for n, m in adapter["models"].items():
        if m.get("role") not in ("incumbent", "baseline", "candidate"):
            raise AdapterInvalid(f"model {n}: role must be incumbent, baseline or candidate")
        if not m.get("version"):
            raise AdapterInvalid(f"model {n}: no version")
    if not any(m["role"] == "incumbent" for m in adapter["models"].values()):
        raise AdapterInvalid("no incumbent model")


def validate_records(adapter: dict, recs: list, seasons) -> list:
    """Contract checks on one model's walk. Returns the records normalised (ISO times). Raises AdapterInvalid."""
    out, seen = [], set()
    for r in recs:
        for k in ("game_id", "season", "start_at", "forecast_cutoff", "market", "probs"):
            if k not in r:
                raise AdapterInvalid(f"record lacks {k!r}: {str(r)[:120]}")
        spec = adapter["markets"].get(r["market"])
        if spec is None:
            raise AdapterInvalid(f"record for market {r['market']!r}, which the adapter does not declare")
        if r["season"] not in seasons:
            raise AdapterInvalid(f"{r['game_id']}: season {r['season']} outside the requested {list(seasons)}")
        cut, start = _iso(r["forecast_cutoff"]), _iso(r["start_at"])
        if cut > start:
            raise AdapterInvalid(f"{r['game_id']} {r['market']}: forecast_cutoff {cut} is after the start {start}")
        p, kind = r["probs"], spec["kind"]
        if "push" in p and kind == "line" and "mid" not in p:        # whole-number lines: push probability is the mid mass
            p = {("mid" if k == "push" else k): v for k, v in p.items()}
            r = {**r, "probs": p}
        keys = set(p)
        allowed = {"binary": ({"a", "b"},), "three_way": ({"a", "mid", "b"},), "line": ({"a", "b"}, {"a", "mid", "b"})}[kind]
        if keys not in allowed:
            raise AdapterInvalid(f"{r['game_id']} {r['market']}: probs keys {sorted(keys)} do not fit kind {kind}")
        vals = [p[k] for k in keys]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and -1e-9 <= v <= 1 + 1e-9 for v in vals) \
                or abs(sum(vals) - 1) > 1e-6:
            raise AdapterInvalid(f"{r['game_id']} {r['market']}: probabilities not finite, in [0, 1] and summing to 1")
        if r.get("outcome") not in (None, "a", "b", "mid", "push", "void"):
            raise AdapterInvalid(f"{r['game_id']} {r['market']}: unknown outcome {r.get('outcome')!r}")
        k = _key(r)
        if k in seen:
            raise AdapterInvalid(f"duplicate forecast for {k}")
        seen.add(k)
        out.append({**r, "forecast_cutoff": cut, "start_at": start})
    return out


# --- Metrics ----------------------------------------------------------------------------------------------------------------
def calibration(recs: list, kind: str) -> dict:
    """Reliability bins over the probability of side a (binary / line without push mass) or over every side (anything with a
    mid probability); ECE = n-weighted mean |observed - mean probability|."""
    pairs = []
    for r in recs:
        idx = _outcome_idx(r, kind)
        if idx is None:
            continue
        v = _vec3(r)
        cls = (0, 1, 2) if (kind == "three_way" or "mid" in r["probs"]) else (0,)
        pairs += [(v[c], 1.0 if idx == c else 0.0) for c in cls]
    bins = [{"lo": i / BINS, "hi": (i + 1) / BINS, "n": 0, "mean_p": 0.0, "observed": 0.0} for i in range(BINS)]
    for p, y in pairs:
        b = bins[min(BINS - 1, int(p * BINS))]
        b["n"] += 1
        b["mean_p"] += p
        b["observed"] += y
    n = len(pairs)
    ece = 0.0
    for b in bins:
        if b["n"]:
            b["mean_p"] /= b["n"]
            b["observed"] /= b["n"]
            ece += b["n"] / n * abs(b["observed"] - b["mean_p"])
    return {"bins": bins, "ece": ece if n else None, "n_pairs": n}


def point_errors(recs: list) -> Optional[dict]:
    """MAE/RMSE/bias of point predictions where both prediction and actual exist ({home, away} or one number)."""
    acc: dict = {}
    for r in recs:
        pp, pa = r.get("point_pred"), r.get("point_actual")
        if pp is None or pa is None:
            continue
        try:
            if isinstance(pp, dict):
                pairs = {"home": (pp["home"], pa["home"]), "away": (pp["away"], pa["away"]),
                         "margin": (pp["home"] - pp["away"], pa["home"] - pa["away"]),
                         "total": (pp["home"] + pp["away"], pa["home"] + pa["away"])}
            else:
                pairs = {"value": (pp, pa)}
        except (KeyError, TypeError):
            continue
        for name, (p, a) in pairs.items():
            acc.setdefault(name, []).append(p - a)
    if not acc:
        return None
    return {n: {"n": len(e), "mae": sum(abs(x) for x in e) / len(e), "rmse": math.sqrt(sum(x * x for x in e) / len(e)),
                "bias": sum(e) / len(e)} for n, e in acc.items()}


def model_metrics(recs: list, kind: str, universe: Optional[int] = None) -> dict:
    scored = [(r, _outcome_idx(r, kind)) for r in recs]
    scored = [(r, i) for r, i in scored if i is not None]
    n = len(scored)
    out = {"records": len(recs), "n": n, "unsettled": sum(r.get("outcome") is None for r in recs),
           "void": sum(r.get("outcome") == "void" for r in recs),
           "push_unscored": sum(r.get("outcome") in ("push",) and _outcome_idx(r, kind) is None for r in recs),
           "coverage": (len(recs) / universe) if universe else None}
    if not n:
        return out | {"log_loss": None, "brier": None, "accuracy": None, "calibration": calibration([], kind)}
    wins = losses = abst = 0
    for r, i in scored:
        s = _pick(_vec3(r), kind)
        if s is None:
            abst += 1
        elif i in _sides(kind):
            wins += s == i
            losses += s != i
    return out | {"log_loss": sum(_ll(_vec3(r), i) for r, i in scored) / n,
                  "brier": sum(_brier(_vec3(r), i) for r, i in scored) / n,
                  "accuracy": wins / (wins + losses) if wins + losses else None, "wins": wins, "losses": losses,
                  "abstentions": abst, "calibration": calibration(recs, kind), "score_errors": point_errors(recs)}


def pair_compare(cand: list, ref: list, kind: str) -> dict:
    """Paired log-loss difference (candidate minus reference) on the games both scored, with the same seeded week-block
    bootstrap as the app's other studies, plus the matched pick accuracies."""
    from . import backtest
    rmap = {_key(r): r for r in ref if _outcome_idx(r, kind) is not None}
    pairs = [(c, rmap[_key(c)], _outcome_idx(c, kind)) for c in cand if _key(c) in rmap and _outcome_idx(c, kind) is not None]
    if not pairs:
        return {"n": 0}
    diffs = [_ll(_vec3(c), i) - _ll(_vec3(r), i) for c, r, i in pairs]
    boot = backtest.block_bootstrap(diffs, [c["forecast_cutoff"] for c, _r, _i in pairs])

    def acc(side):
        w = l = 0
        for c, r, i in pairs:
            s = _pick(_vec3(c if side == 0 else r), kind)
            if s is not None and i in _sides(kind):
                w += s == i
                l += s != i
        return w / (w + l) if w + l else None
    return {"n": len(pairs), "mean": boot["mean"], "ci95": boot["ci95"], "weeks": boot["weeks"],
            "candidate_accuracy": acc(0), "reference_accuracy": acc(1)}


def baselines_for(ref: list, kind: str) -> dict:
    """Simple baselines on the reference's own games: uniform, and a smoothed expanding outcome frequency that only uses
    outcomes of games that started SETTLE_LAG_HOURS before the forecast cutoff."""
    import heapq
    allowed = _sides(kind)
    three = any("mid" in r["probs"] for r in ref)
    classes = (0, 1, 2) if three else (0, 2)
    uni = {0: 1 / len(classes), 1: 1 / len(classes) if three else 0.0, 2: 1 / len(classes)}
    if kind == "line" and three:        # a line market's push mass is small: equal sides around the incumbent's mean push mass
        m = sum(r["probs"].get("mid", 0.0) for r in ref) / len(ref)
        uni = {0: (1 - m) / 2, 1: m, 2: (1 - m) / 2}
    out_u, out_b = [], []
    counts = {c: 0 for c in classes}
    pending, order = [], sorted(ref, key=lambda r: (r["forecast_cutoff"], r["game_id"], r["market"]))
    for r in order:
        while pending and pending[0][0] <= r["forecast_cutoff"]:
            counts[heapq.heappop(pending)[2]] += 1
        base = {k: r[k] for k in ("game_id", "season", "start_at", "forecast_cutoff", "market", "line", "outcome") if k in r}
        key = ("a", "mid", "b") if three else ("a", "b")
        out_u.append({**base, "probs": {k: uni[i] for k, i in zip(("a", "mid", "b"), (0, 1, 2)) if k in key}})
        tot = sum(counts.values())
        pr = {"a": (counts[0] + 1) / (tot + len(classes)), "b": (counts[2] + 1) / (tot + len(classes))}
        if three:
            pr["mid"] = (counts[1] + 1) / (tot + len(classes))
        out_b.append({**base, "probs": pr})
        idx = _outcome_idx(r, kind)
        if idx is not None and idx in classes:
            start = datetime.strptime(r["start_at"], ISO) + timedelta(hours=SETTLE_LAG_HOURS)
            heapq.heappush(pending, (start.strftime(ISO), r["game_id"], idx))
    return {"baseline:uniform": out_u, "baseline:base_rate": out_b}


# --- Sportsbook no-vig and returns --------------------------------------------------------------------------------------------
def valid_quote(r: dict) -> Optional[dict]:
    """The record's quote when it may be compared with the forecast: not synthetic, retrieved at or before the information
    cutoff, same line, priced above 1. Decimal prices."""
    q = r.get("quote")
    if not q or q.get("synthetic"):
        return None
    try:
        if _iso(q["retrieved_at"]) > r["forecast_cutoff"]:
            return None
        ql, rl = q.get("line"), r.get("line")
        if (ql is None) != (rl is None) or (rl is not None and abs(float(ql) - float(rl)) > 1e-9):
            return None
        prices = [q["price_a"], q["price_b"]] + ([q["price_mid"]] if q.get("price_mid") else [])
        return q if all(isinstance(p, (int, float)) and p > 1 for p in prices) else None
    except (KeyError, TypeError, ValueError):
        return None


def no_vig(q: dict) -> tuple:
    """Proportional no-vig probabilities (a, mid or 0, b) from decimal prices."""
    inv = [1 / q["price_a"], 1 / q["price_mid"] if q.get("price_mid") else 0.0, 1 / q["price_b"]]
    s = sum(inv)
    return tuple(x / s for x in inv)


def book_compare(recs: list, kind: str) -> dict:
    """Model vs the sportsbook's no-vig probabilities on records with a valid quote. A two-way book has no push mass, so
    pushed games are dropped and the model's probabilities are renormalised over the two sides."""
    from . import backtest
    rows = []
    for r in recs:
        q = valid_quote(r)
        i = _outcome_idx(r, kind)
        if q is None or i is None:
            continue
        book = no_vig(q)
        v = _vec3(r)
        if not q.get("price_mid"):
            if i == 1 and kind != "three_way":
                continue
            if kind != "three_way":
                s = v[0] + v[2]
                v = (v[0] / s, 0.0, v[2] / s) if s > 0 else v
        rows.append((r, i, v, book))
    quoted = sum(1 for r in recs if r.get("quote"))
    out = {"quoted": quoted, "valid": len(rows), "synthetic": sum(1 for r in recs if (r.get("quote") or {}).get("synthetic")),
           "invalid_or_late": quoted - len(rows)}
    if not rows:
        return out | {"status": "unavailable", "reason": "no valid non-synthetic quote at or before the forecast cutoff on "
                                                          "the same line"}
    diffs = [_ll(v, i) - _ll(b, i) for _r, i, v, b in rows]
    boot = backtest.block_bootstrap(diffs, [r["forecast_cutoff"] for r, *_ in rows])
    return out | {"status": "ok", "n": len(rows), "model_log_loss": sum(_ll(v, i) for _r, i, v, _b in rows) / len(rows),
                  "book_log_loss": sum(_ll(b, i) for _r, i, _v, b in rows) / len(rows),
                  "diff_mean": boot["mean"], "diff_ci95": boot["ci95"]}


def roi(recs: list, kind: str) -> dict:
    """Flat 1 unit on the model's pick when model probability x decimal price > 1, at executable non-synthetic matching
    quotes only. Pushes refund (profit 0, stake counted), voids are excluded. Anything else is counted, never returned."""
    synthetic = sum(1 for r in recs if (r.get("quote") or {}).get("synthetic"))
    not_exec = sum(1 for r in recs if r.get("quote") and not r["quote"].get("synthetic") and not r["quote"].get("executable"))
    stake = profit = bets = 0.0
    for r in recs:
        q = valid_quote(r)
        if q is None or not q.get("executable") or r.get("outcome") in (None, "void"):
            continue
        v = _vec3(r)
        side = _pick(v, kind)
        if side is None:
            continue
        price = (q["price_a"], q.get("price_mid"), q["price_b"])[side]
        if not price or v[side] * price <= 1:
            continue
        bets += 1
        stake += 1
        o = r["outcome"]
        won = (o == "a" and side == 0) or (o == "b" and side == 2) or (o == "mid" and side == 1)
        profit += (price - 1) if won else (0.0 if o == "push" else -1.0)
    out = {"synthetic_quotes_excluded": synthetic, "non_executable_quotes_excluded": not_exec}
    if not bets:
        return out | {"status": "unavailable", "reason": "no executable, non-synthetic, matching quote with a bet signal"}
    return out | {"status": "ok", "bets": int(bets), "staked": stake, "profit": profit, "roi": profit / stake,
                  "note": "exploratory: flat stake at the quoted price, no bankroll model"}


# --- Evaluator (pure: records in, metrics and decisions out) --------------------------------------------------------------
def _choose_incumbent(adapter: dict, by_model: dict, market: str, kind: str) -> Optional[str]:
    """The best existing member per market (lowest log loss among incumbents with the widest coverage): the bar, as in v2."""
    best = None
    rows = {}
    for n, m in adapter["models"].items():
        if m["role"] != "incumbent":
            continue
        recs = [r for r in by_model.get(n, []) if r["market"] == market]
        scored = [r for r in recs if _outcome_idx(r, kind) is not None]
        if scored:
            rows[n] = (len(scored), sum(_ll(_vec3(r), _outcome_idx(r, kind)) for r in scored) / len(scored))
    if rows:
        widest = max(c for c, _ in rows.values())
        best = min((n for n, (c, _) in rows.items() if c == widest), key=lambda n: (rows[n][1], n))
    return best


def decide_candidate(cand: list, inc: list, kind: str) -> dict:
    """Keep/discard of one candidate against the incumbent on one stage's records (MODEL_SELECTION §6 via keep_decision)."""
    cohort_ = [r for r in inc if _outcome_idx(r, kind) is not None]
    have = {_key(r) for r in cand if _outcome_idx(r, kind) is not None}
    covered = [r for r in cohort_ if _key(r) in have]
    out = {"cohort": len(cohort_), "forecast": len(covered), "coverage": len(covered) / len(cohort_) if cohort_ else 0.0}
    if not cohort_:
        return out | {"decision": "reject", "reason": "empty cohort"}
    if len(covered) < len(cohort_):
        return out | {"decision": "reject", "reason": f"coverage loss: {len(cohort_) - len(covered)} of {len(cohort_)} incumbent "
                                                      "games without a candidate forecast"}
    if len(cohort_) < MIN_SCORED:
        return out | {"decision": "reject", "reason": f"only {len(cohort_)} scored games (need {MIN_SCORED})"}
    cmp_ = pair_compare(cand, inc, kind)
    return out | {"vs_incumbent": cmp_} | keep_decision(cmp_["ci95"][1], cmp_["candidate_accuracy"], cmp_["reference_accuracy"])


def _stage(recs: list, seasons) -> list:
    return [r for r in recs if r["season"] in seasons]


def evaluate_records(adapter: dict, by_model: dict, stages: tuple = ("search", "validation")) -> dict:
    """Everything the owner asked for, per market: per-model n/coverage/Brier/log loss/calibration/score errors; the
    incumbent (best existing member); simple baselines; sportsbook no-vig; returns; each candidate vs incumbent, baselines and
    book, with a keep/discard on the search AND validation stages. Pure; reads nothing."""
    per = adapter["periods"]                        # stage name -> seasons (the forward stage passes its own periods)
    cands = [n for n, m in adapter["models"].items() if m["role"] == "candidate"]
    out = {"sport": adapter["sport"], "data_version": adapter["data_version"], "candidates": cands,
           "unsupported": dict(adapter["unsupported"]), "markets": {}, "exploratory": True}
    for market, spec in adapter["markets"].items():
        kind = spec["kind"]
        mk = {n: [r for r in recs if r["market"] == market] for n, recs in by_model.items()}
        inc = _choose_incumbent(adapter, by_model, market, kind)
        entry = {"kind": kind, "settlement": spec.get("settlement"), "line_basis": spec.get("line_basis")}
        if inc is None:
            out["markets"][market] = entry | {"status": "no_reference", "reason": "no incumbent forecasts for this market"}
            continue
        universe = len({_key(r) for recs in mk.values() for r in recs})
        entry |= {"status": "evaluated", "incumbent": inc, "universe": universe, "models": {}}
        srcs: dict = {}
        for r in mk[inc]:
            srcs[r.get("line_source")] = srcs.get(r.get("line_source"), 0) + 1
        if kind == "line" and any(k is not None for k in srcs):
            entry["line_sources"] = {str(k): v for k, v in srcs.items()}
            if "reference_model" in srcs:       # graded at the model's own reference line: no skill vs a bookmaker is claimed
                entry["reference_line_note"] = ("records with line_source=reference_model are graded at a reference line, ~50/50 "
                                                "by construction: reported apart from bookmaker-line results, never as skill "
                                                "against a bookmaker")
                entry["by_line_source"] = {str(k): {kk: v for kk, v in model_metrics(
                    [r for r in mk[inc] if r.get("line_source") == k], kind).items() if kk in ("n", "log_loss", "brier", "accuracy")}
                    for k in srcs}
        base = baselines_for(mk[inc], kind)
        for n in [*mk, *base]:
            recs = mk.get(n) if n in mk else base[n]
            if not recs and n in mk:
                continue
            role = adapter["models"][n]["role"] if n in adapter["models"] else "baseline"
            met = model_metrics(recs, kind, universe)
            met["role"] = role
            met["by_stage"] = {st: {k: v for k, v in model_metrics(_stage(recs, per[st]), kind).items()
                                    if k in ("n", "log_loss", "brier", "accuracy")} for st in stages}
            met["vs_book"] = book_compare(recs, kind)
            met["returns"] = roi(recs, kind)
            entry["models"][n] = met
        entry["comparisons"] = {}
        for n in [*([inc] if inc else []), *[c for c in cands if mk.get(c)]]:
            recs = mk[n]
            cmp_ = {"vs_baselines": {b: pair_compare(recs, brecs, kind) for b, brecs in base.items()}}
            if n != inc:
                stages = {st: decide_candidate(_stage(recs, per[st]), _stage(mk[inc], per[st]), kind)
                          for st in stages}
                kept = all(s["decision"] == "keep" for s in stages.values())
                cmp_ |= {"vs_incumbent": stages, "decision": "keep" if kept else "reject",
                         "reason": "kept on search and validation (exploratory, repeated search; not a promotion)" if kept else
                                   "; ".join(f"{st}: {s['reason']}" for st, s in stages.items() if s["decision"] != "keep")}
            entry["comparisons"][n] = cmp_
        out["markets"][market] = entry
    out["evaluated_markets"] = sorted(m for m, e in out["markets"].items() if e["status"] == "evaluated")
    out["kept"] = sorted(f"{n}:{m}" for m, e in out["markets"].items() for n, c in e.get("comparisons", {}).items()
                         if c.get("decision") == "keep")
    return out


# --- Adapters (NBA / NHL here, on the existing study path; the others live with their sports) -------------------------------
_STUDY: dict = {}
_OUTCOME = {0: "a", 1: "push", 2: "b"}
NBA_NHL_MARKETS = {
    "nba": {"winner": ("binary", "incl. overtime"), "spread": ("line", "push refunds, incl. OT"),
            "total": ("line", "push refunds, incl. OT"), "team_total_home": ("line", "push refunds, incl. OT"),
            "team_total_away": ("line", "push refunds, incl. OT")},
    "nhl": {"winner": ("binary", "incl. OT/shootout"), "reg3": ("three_way", "regulation 60 min; draw = tied after 60"),
            "spread": ("line", "puck line, push refunds"), "total": ("line", "push refunds"),
            "team_total_home": ("line", "push refunds"), "team_total_away": ("line", "push refunds")}}
NBA_NHL_NOTES = ("line markets use the primary model's own line (no historical bookmaker lines): ~50/50 by construction, no "
                 "sportsbook comparison or returns are possible")


def _study_tables(conn, sport: str, period: str) -> dict:
    key = (sport, period, str(config.db_path(sport)))
    if key not in _STUDY:
        from .candidates import features, study
        tables, _first, members, rows, events, recorder, dataset = study.existing_tables(conn, sport, period)
        _STUDY[key] = {"tables": tables, "members": members, "rows": rows, "events": events, "recorder": recorder,
                       "dataset": dataset, "features": features, "walk": None, "specs": {}}
    return _STUDY[key]


def _period_for(adapter_periods: dict, seasons) -> str:
    return "test" if set(seasons) & set(adapter_periods["holdout"]) else "validation"


def _study_record(sport: str, market: str, r: dict, vec) -> dict:
    kind = NBA_NHL_MARKETS[sport][market][0]
    pa, pm, pb = (float(x) for x in vec)
    probs = {"a": pa, "b": pb} if kind == "binary" else {"a": pa, "mid": pm, "b": pb}
    idx = r["realised"]
    outcome = None if idx is None else ("mid" if (idx == 1 and kind == "three_way") else _OUTCOME[idx])
    return {"game_id": r["game_id"], "season": r["season"], "start_at": _iso(r["tipoff_at"]), "forecast_cutoff": _iso(r["cutoff_at"]),
            "market": market, "line": r["line"], "probs": probs, "outcome": outcome}


def _study_walk(sport: str, adapter_periods: dict):
    def walk(conn, seasons, model_name):
        st = _study_tables(conn, sport, _period_for(adapter_periods, seasons))
        seasons = set(seasons)
        out = []
        if model_name in st["members"]["winner"] or model_name in st["members"]["lines"]:
            for market, rows in st["tables"].items():
                for r in rows:
                    if r["season"] in seasons and model_name in r["vectors"]:
                        out.append(_study_record(sport, market, r, r["vectors"][model_name]))
            return out
        from .candidates import engine, registry
        spec = next((s for s in registry.for_sport(sport) if s.name == model_name), None)
        if spec is None:
            raise AdapterInvalid(f"{sport}: unknown model {model_name}")
        if st["walk"] is None:
            walked = st["features"].walk(st["recorder"], st["rows"], st["events"])
            # NHL starters are known only after the start: stripped from every row, as in protocol v2 (FORBIDDEN_FEATURES)
            st["walk"] = {g: replace(r, x={k: v for k, v in r.x.items() if k not in FORBIDDEN_FEATURES}, meta={})
                          for g, r in walked.items()}
        if model_name not in st["specs"]:
            outs, _fits, _sec = engine.walk(spec, list(st["walk"].values()))
            st["specs"][model_name] = outs
        outs = st["specs"][model_name]
        for market, rows in st["tables"].items():
            if market not in spec.markets:
                continue
            for r in rows:
                o = outs.get(r["game_id"])
                if r["season"] in seasons and o is not None and engine.ready(o):
                    v = engine.vector(o, market, r["line"])
                    if v is not None:
                        out.append(_study_record(sport, market, r, v))
        return out
    return walk


def _study_upcoming(sport: str):
    def forecast_upcoming(conn, now, model_name):
        """Forward forecasts of a registry candidate: its recorded pregame rows in the official store's candidate-forecast ledger
        (read-only here; written by the existing candidate recorder, never by research)."""
        from .candidates import registry
        names = {s.name for s in registry.for_sport(sport)}
        if model_name not in names:
            return []
        from . import candidates_view
        rows = candidates_view.upcoming(conn, model_name, now)
        out = []
        for f in rows:
            kind = NBA_NHL_MARKETS[sport].get(f["market"], ("binary",))[0]
            probs = {"a": f["p_a"], "b": f["p_b"]} if kind == "binary" else {"a": f["p_a"], "mid": f["p_mid"], "b": f["p_b"]}
            out.append({"game_id": f["game_id"], "season": f["season"], "start_at": _iso(f["tipoff_at"]),
                        "forecast_cutoff": _iso(f["generated_at"]), "forecast_at": _iso(f["generated_at"]),
                        "market": f["market"], "line": f["line"], "probs": probs, "outcome": None})
        return out
    return forecast_upcoming


def _study_settle(sport: str):
    def settle(conn, records):
        """Official results (read-only) -> outcomes for forward records, through the app's own settlement (markets.settle)."""
        from . import markets
        out = []
        for r in records:
            g = conn.execute("SELECT status, home_score, away_score FROM games WHERE game_id=?", (r["game_id"],)).fetchone()
            if g is None:
                continue
            dec = None
            if sport == "nhl":
                d = conn.execute("SELECT decision FROM game_decisions WHERE game_id=?", (r["game_id"],)).fetchone()
                dec = d["decision"] if d else None
            s = markets.settle(r["market"], r["line"], status=g["status"], home=g["home_score"], away=g["away_score"], decision=dec)
            o = {"a": "a", "b": "b", "void": "void", "mid": "mid" if r["market"] == "reg3" else "push"}.get(s)
            if o is None:                         # pending / unscorable: not graded yet
                continue
            out.append({"game_id": r["game_id"], "market": r["market"], "line": r["line"], "outcome": o,
                        "point_actual": {"home": g["home_score"], "away": g["away_score"]}})
        return out
    return settle


def nba_nhl_adapter(sport: str, candidates: Optional[list] = None) -> dict:
    """NBA/NHL on the existing study path: existing members are the incumbents (the best per market is the bar), registry
    candidates (non-deep, installed) the candidates. Search seasons are the first validation seasons, validation the last:
    protocol v2 already looked at all of them, so they are not pristine (stated in `periods_note`)."""
    from .candidates import registry
    from . import backtest, nhl_eval
    per = (backtest.PERIODS if sport == "nba" else nhl_eval.PERIODS)
    val = sorted(per["validation"])
    periods = {"search": val[:-1], "validation": val[-1:], "holdout": sorted(per["test"])}
    members = {"nba": {"winner": ["elo", "logistic", "logistic_v2", "efficiency_margin", "gbt_v1"],
                       "lines": ["points_normal", "ridge_points", "gbt_points", "ridge_points@weekly", "gbt_points@weekly"]},
               "nhl": {"winner": ["hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt", "hockey_logistic_xg"],
                       "lines": ["goals_poisson", "goals_draw", "goals_glm"]}}[sport]
    models = {n: {"role": "incumbent", "description": f"existing {sport.upper()} member", "version": "app@" + app_identity()["files"]}
              for n in dict.fromkeys(members["winner"] + members["lines"])}
    for spec in registry.for_sport(sport):
        from .candidates import deep
        from .candidates.features import FEATURE_SETS
        if deep.is_deep(spec) or not registry.available(spec) or (candidates is not None and spec.name not in candidates) \
                or set(FEATURE_SETS[spec.features]) & set(FORBIDDEN_FEATURES):      # postgame goalie inputs: excluded
            continue
        models[spec.name] = {"role": "candidate", "description": f"registry candidate {spec.family}/{spec.features}",
                             "version": registry.definition(spec)}
    walk = _study_walk(sport, periods)

    def study_adapter_walk(conn, seasons, model_name):
        if model_name not in models:
            raise AdapterInvalid(f"{sport}: unknown model {model_name}")
        return walk(conn, seasons, model_name)
    return {"sport": sport, "data_version": "study:" + _sha(json.dumps(
                {"periods": periods, "protocol": "existing_tables", "libs": libraries()}, sort_keys=True).encode())[:16],
            "markets": {m: {"kind": k, "settlement": s, "line_basis": "model line (no historical bookmaker lines)"
                            if k == "line" else None} for m, (k, s) in NBA_NHL_MARKETS[sport].items()},
            "unsupported": {"player_props": "no timestamped player history for the search seasons (PLAN rule 8)",
                            "goalie-dependent NHL features": "historical rows know the starter only after the start"},
            "periods": periods, "periods_note": "seasons 2022-2024 were already searched under protocol v2: not pristine; the "
            "holdout was used by earlier studies (combination_research.STORED_NOTE)", "notes": NBA_NHL_NOTES,
            "models": models, "walk": study_adapter_walk, "forecast_upcoming": _study_upcoming(sport),
            "settle": _study_settle(sport)}


# The one place that names where each sport's adapter lives ("module:function"; the NFL and NCAAB adapters are NOT in the
# byte-pinned nfl_eval / ncaab_eval). Update here when an adapter agent moves one.
ADAPTER_FUNCTIONS = {"nfl": "nfl_challengers:research_adapter", "ncaab": "ncaab_cards:research_adapter",
                     "soccer": "soccer_markets_eval:research_adapter"}


def get_adapter(sport: str) -> Optional[dict]:
    """The sport's adapter, or None when the adapter agent has not delivered one (reported as `refused`, never skipped)."""
    import importlib
    if sport in ("nba", "nhl"):
        return nba_nhl_adapter(sport)
    target = ADAPTER_FUNCTIONS[sport]
    mod, _, func = target.partition(":")
    try:
        fn = getattr(importlib.import_module(f"{__package__ or 'app'}.{mod}"), func)
    except (ImportError, AttributeError):
        return None
    return fn()


# --- Runner: bounded, resumable per sport, explicit outcomes ---------------------------------------------------------------
OUTCOMES = ("evaluated", "no_candidates", "refused", "failed", "skipped")


def v3_protocol_id(adapter: dict) -> str:
    blob = json.dumps({"protocol": PROTOCOL_V3, "sport": adapter["sport"], "data_version": adapter["data_version"],
                       "periods": adapter["periods"], "markets": adapter["markets"]}, sort_keys=True, default=str)
    return f"{PROTOCOL_V3['name']}-{_sha(blob.encode())[:12]}"


def _item_key(pid: str, model: str, version: str) -> str:
    return _sha(json.dumps([pid, model, version]).encode())[:16]


def _jsonl_read(p: Path) -> list:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.is_file() else []


def _jsonl_add(p: Path, rec: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _last_success(exp_root: Path, sport: str) -> Optional[str]:
    best = None
    for p in (exp_root / "v3").glob(f"*/{sport}/attempts.jsonl"):
        for a in _jsonl_read(p):
            if a.get("outcome") in ("evaluated", "no_candidates") and (best is None or a["at"] > best):
                best = a["at"]
    return best


def snapshot_cutoff(sport: str) -> Optional[str]:
    """`last_final` of this sport from the snapshot's IDENTITY.json (the snapshot holds future scheduled rows): nothing that
    starts after it is evaluated. None when the data directory has no identity file."""
    p = config.data_dir() / "IDENTITY.json"
    try:
        ident = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    node = (ident.get("sports") or ident).get(sport) or {}
    v = node.get("last_final") if isinstance(node, dict) else None
    return _iso(v) if v else None


def _write_state(root: Path, state: dict) -> None:
    tmp = root / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True, default=str))
    tmp.replace(root / "state.json")


def run_sport_v3(sport: str, exp_root: Path, *, max_items: int = BUDGET_V3["max_items_per_sport"],
                 max_seconds: float = BUDGET_V3["max_seconds_per_sport"], adapter_fn: Callable = None) -> dict:
    """One sport's bounded, resumable attempt. Scores each queue item (every incumbent, then every candidate) once on the
    search and validation seasons; an item already stored is never re-scored. Returns the sport's status dict with an explicit
    outcome: evaluated, no_candidates, refused (reason), failed (error) or skipped (budget/time)."""
    adapter_fn = adapter_fn or get_adapter
    t0, at = time.time(), _now()
    status = {"sport": sport, "at": at, "outcome": "failed", "reason": None, "protocol_id": None, "candidates": [],
              "evaluated_markets": [], "blockers": [], "queue": None, "next_run": None}
    root = None
    try:
        guard_paths(exp_root)
        adapter = adapter_fn(sport)
        if adapter is None:
            raise Refused(f"no research adapter for {sport} has been delivered yet")
        validate_adapter(adapter)
        pid = v3_protocol_id(adapter)
        root = exp_root / "v3" / pid / sport
        status["caveats"] = adapter.get("caveats")
        status |= {"protocol_id": pid, "data_version": adapter["data_version"], "periods": adapter["periods"],
                   "periods_note": adapter.get("periods_note"), "markets": adapter["markets"],
                   "unsupported": adapter["unsupported"], "models": {n: {k: m.get(k) for k in ("role", "version", "description")}
                                                                  for n, m in adapter["models"].items()}}
        status["blockers"] += [f"{m}: {why}" for m, why in adapter["unsupported"].items()]
        with Lock(root):
            _run_queue(status, adapter, root, pid, sport, t0, max_items, max_seconds)
    except Refused as exc:
        status |= {"outcome": "refused", "reason": str(exc)}
        status["blockers"].append(f"refused: {exc}")
    except Exception as exc:                                  # a bug or a broken adapter: recorded, never hidden
        status |= {"outcome": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}
        status["blockers"].append(f"failed: {status['reason']}")
    status["seconds"] = round(time.time() - t0, 1)
    status["last_success_at"] = at if status["outcome"] in ("evaluated", "no_candidates") else _last_success(exp_root, sport)
    if status["outcome"] == "failed" and status["reason"] is None:
        status["reason"] = "unknown"
    if root is None:
        root = exp_root / "v3" / "unregistered" / sport
        root.mkdir(parents=True, exist_ok=True)
    _jsonl_add(root / "attempts.jsonl", {"at": at, "outcome": status["outcome"], "reason": status["reason"]})
    status["last_attempt"] = {"at": at, "outcome": status["outcome"], "reason": status["reason"]}
    _write_state(root, status)
    return status


def _run_queue(status: dict, adapter: dict, root: Path, pid: str, sport: str, t0: float, max_items: int, max_seconds: float):
    per = adapter["periods"]
    seasons = sorted(set(per["search"]) | set(per["validation"]))
    if set(seasons) & set(per["holdout"]):
        raise Refused("search/validation seasons overlap the holdout")
    models = adapter["models"]
    order = [n for r in ("incumbent", "baseline", "candidate") for n, m in models.items() if m["role"] == r]
    items = root / "items"
    items.mkdir(parents=True, exist_ok=True)
    log = root / "items.jsonl"
    events = _jsonl_read(log)
    done = {e["key"] for e in events if e["event"] == "scored"}
    fails: dict = {}
    for e in events:
        if e["event"] == "item_failed":
            fails[e["key"]] = fails.get(e["key"], 0) + 1
    keys = {n: _item_key(pid, n, models[n]["version"]) for n in order}
    conn = adapter["connect"]() if adapter.get("connect") else _snapshot_conn(sport)
    if adapter.get("validate_data"):                          # optional adapter check of the snapshot it is about to read
        problems = adapter["validate_data"](conn)
        if problems:
            raise Refused(f"adapter data validation failed: {problems if isinstance(problems, str) else list(problems)[:3]}")
    ran = dropped = 0
    cutoff = snapshot_cutoff(sport)
    stopped = None
    try:
        for n in order:
            k = keys[n]
            if k in done or fails.get(k, 0) > BUDGETS["max_retries"]:
                continue
            if ran >= max_items or time.time() - t0 >= max_seconds:
                stopped = "item budget reached" if ran >= max_items else "time budget reached"
                break
            s0 = time.time()
            try:
                recs = _walk_item(adapter, conn, seasons, n, items / f"{k}.partial.json", t0, max_seconds)
                if recs is None:                                  # bounded walk not finished: resumes next run
                    stopped = "time budget reached (partial progress stored)"
                    break
                if cutoff:
                    kept_recs = [r for r in recs if _iso(r["start_at"]) <= cutoff]
                    dropped += len(recs) - len(kept_recs)
                    recs = kept_recs
                recs = validate_records(adapter, recs, seasons)
            except AdapterInvalid as exc:
                _jsonl_add(log, {"event": "item_failed", "key": k, "model": n, "at": _now(), "error": str(exc)[:400]})
                fails[k] = fails.get(k, 0) + 1
                if models[n]["role"] == "incumbent":
                    raise
                continue
            except Exception as exc:
                _jsonl_add(log, {"event": "item_failed", "key": k, "model": n, "at": _now(),
                                 "error": f"{type(exc).__name__}: {exc}"[:400]})
                fails[k] = fails.get(k, 0) + 1
                if models[n]["role"] == "incumbent":
                    raise
                continue
            tmp = items / f"{k}.json.gz.tmp"
            tmp.write_bytes(gzip.compress(json.dumps(recs, default=str).encode()))
            tmp.replace(items / f"{k}.json.gz")
            _jsonl_add(log, {"event": "scored", "key": k, "model": n, "version": models[n]["version"], "records": len(recs),
                             "seconds": round(time.time() - s0, 1), "at": _now()})
            done.add(k)
            ran += 1
    finally:
        if hasattr(conn, "close"):
            conn.close()
    remaining = [n for n in order if keys[n] not in done]
    failed = [n for n in remaining if fails.get(keys[n], 0) > BUDGETS["max_retries"]]
    pending = [n for n in remaining if n not in failed]
    cands = [n for n, m in models.items() if m["role"] == "candidate"]
    status["candidates"] = cands
    status["data_cutoff"] = {"last_final": cutoff, "records_after_cutoff_dropped_now": dropped}
    status["queue"] = {"total": len(order), "done": len(order) - len(remaining), "remaining": len(pending), "failed": failed,
                       "ran_now": ran, "next_item": pending[0] if pending else None}
    status["blockers"] += [f"{n}: scoring failed {fails[keys[n]]} times (see items.jsonl)" for n in failed]
    status["next_run"] = (f"resumes with {pending[0]} at the next daily run" if pending else
                          "queue complete; the next daily run re-checks for new models or a new snapshot")
    have_inc = all(keys[n] in done for n, m in models.items() if m["role"] == "incumbent")
    if not have_inc:
        status |= {"outcome": "skipped", "reason": stopped or "incumbent scoring incomplete"}
        return
    by_model = {n: json.loads(gzip.decompress((items / f"{keys[n]}.json.gz").read_bytes()))
                for n in order if keys[n] in done}
    ev = evaluate_records(adapter, by_model)
    ev["protocol_id"] = pid
    (root / "evaluation.json").write_text(json.dumps(ev, indent=1, default=str))
    status["evaluated_markets"] = ev["evaluated_markets"]
    status["evaluation"] = _brief(ev)
    status["kept"] = ev["kept"]
    status["forward_candidates"] = sorted({k.rsplit(":", 1)[0] for k in ev["kept"]})   # for the Ops driver to register forward
    scored_c = [n for n in cands if keys[n] in done]
    if not cands:
        status |= {"outcome": "no_candidates", "reason": "the adapter lists no candidate model: nothing was searched (reference "
                                                          "metrics only; this is not a successful search)"}
    elif not scored_c:
        status |= {"outcome": "failed" if not pending else "skipped",
                   "reason": ("every candidate failed to score" if not pending else stopped or "candidates pending")}
    else:
        status |= {"outcome": "evaluated", "reason": (f"{len(scored_c)} of {len(cands)} candidates scored"
                                                      + (f"; {len(pending)} queued" if pending else "")) +
                   (f"; kept: {', '.join(ev['kept'])}" if ev["kept"] else "; no candidate kept (no improvement found)")}


def _walk_item(adapter: dict, conn, seasons, model: str, partial: Path, t0: float, max_seconds: float) -> Optional[list]:
    """One model's walk. An adapter whose `walk` takes max_work/resume/state (Soccer) is called in chunks of cutoff dates;
    progress (records and the resume token) is stored in `partial`, so an interrupted item continues, never restarts. Returns
    None while unfinished."""
    import inspect
    params = inspect.signature(adapter["walk"]).parameters
    if not {"max_work", "resume", "state"} <= set(params):
        return adapter["walk"](conn, seasons, model)
    saved = json.loads(partial.read_text()) if partial.is_file() else {"records": [], "next": None}
    while True:
        state: dict = {}
        saved["records"] += adapter["walk"](conn, seasons, model, max_work=adapter.get("chunk_work", 40),
                                            resume=saved["next"], state=state)
        saved["next"] = state.get("next")
        if state.get("done", True):
            return saved["records"]
        partial.write_text(json.dumps(saved, default=str))
        if time.time() - t0 >= max_seconds:
            return None


def _brief(ev: dict) -> dict:
    """Per market: incumbent, n, log loss, and each candidate's decision and delta: what the status page shows."""
    out = {}
    for m, e in ev["markets"].items():
        if e["status"] != "evaluated":
            out[m] = {"status": e["status"], "reason": e.get("reason")}
            continue
        inc = e["models"][e["incumbent"]]
        out[m] = {"status": "evaluated", "kind": e["kind"], "incumbent": e["incumbent"], "n": inc["n"],
                  "log_loss": inc["log_loss"], "ece": (inc.get("calibration") or {}).get("ece"),
                  "baselines": {b: e["models"][b]["log_loss"] for b in e["models"] if b.startswith("baseline:")},
                  "vs_book": inc["vs_book"].get("status"),
                  "candidates": {n: {"decision": c["decision"], "reason": c["reason"],
                                     "validation_delta": ((c["vs_incumbent"].get("validation") or {}).get("vs_incumbent") or {}).get("mean"),
                                     "validation_ci95": ((c["vs_incumbent"].get("validation") or {}).get("vs_incumbent") or {}).get("ci95")}
                                 for n, c in e["comparisons"].items() if "decision" in c}}
    return out


def daily_v3(sports_: Optional[list], exp_root: Path, max_items: int = BUDGET_V3["max_items_per_sport"],
             max_seconds: float = BUDGET_V3["max_seconds_per_sport"], adapter_fn: Callable = None) -> dict:
    """Every requested sport gets its own item and time budget (none can monopolise the run); the start rotates after the
    last sport attempted. Writes reports/daily5-<time>.json with each sport's explicit outcome."""
    sports_ = list(sports_ or FIVE_SPORTS)
    rot = exp_root / "v3" / "rotation.json"
    last = json.loads(rot.read_text()).get("last") if rot.is_file() else None
    if last in sports_:
        i = sports_.index(last) + 1
        sports_ = sports_[i:] + sports_[:i]
    report = {"protocol": PROTOCOL_V3["name"], "started_at": _now(), "budget_per_sport": {"max_items": max_items,
              "max_seconds": max_seconds}, "sports": {}, "research_only": True,
              "agent": "not used (no paid generation path in this runner)"}
    for sport in sports_:
        report["sports"][sport] = run_sport_v3(sport, exp_root, max_items=max_items, max_seconds=max_seconds, adapter_fn=adapter_fn)
    rot.parent.mkdir(parents=True, exist_ok=True)
    rot.write_text(json.dumps({"last": sports_[0], "at": _now()}))     # next run starts one sport later
    report["outcomes"] = {s: r["outcome"] for s, r in report["sports"].items()}
    report["finished_at"] = _now()
    out = exp_root / "reports" / f"daily5-{report['started_at'].replace(':', '')}-{os.getpid()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    report["report_file"] = str(out)
    return report


# --- Protected holdout (v3): once per protocol and sport, logged first, never by search or daily -------------------------------
def holdout_v3(sport: str, exp_root: Path, ask: Callable[[str], bool] = _ask_tty, reuse_reason: Optional[str] = None,
               adapter_fn: Callable = None) -> dict:
    guard_paths(exp_root)
    adapter = (adapter_fn or get_adapter)(sport)
    if adapter is None:
        raise Refused(f"no research adapter for {sport}")
    validate_adapter(adapter)
    pid = v3_protocol_id(adapter)
    root = exp_root / "v3" / pid / sport
    ev_path = root / "evaluation.json"
    if not ev_path.is_file():
        raise Refused("no search/validation evaluation for this protocol: nothing is kept, so nothing to test on the holdout")
    ev = json.loads(ev_path.read_text())
    kept = sorted({k.rsplit(":", 1)[0] for k in ev["kept"]})      # "<candidate>:<market>"; candidate names may hold ':"
    if not kept:
        raise Refused("no candidate was kept on search and validation: nothing to test on the holdout (negative result stands)")
    with Lock(root):
        log = _holdout_log(root)
        if any(h["event"] == "completed" for h in log):
            raise Refused("holdout already used for this protocol and sport")
        earlier = [h for p in sorted((exp_root / "v3").glob(f"*/{sport}/holdout_access.jsonl")) for h in _holdout_log(p.parent)
                   if h["event"] == "completed"] + _sport_ledger(exp_root, sport)
        if earlier and not reuse_reason:
            raise Refused(f"the {sport} holdout was already used ({len(earlier)} time(s)); reuse needs --reuse-reason (logged; "
                          "the result is then not an independent check)")
        if not ask(f"spend holdout {sport} {pid}"):
            raise Refused("holdout not confirmed at a terminal")
        _holdout_append(root, {"event": "pending", "at": _now(), "kept": kept, "reuse_reason": reuse_reason})
        try:
            seasons = adapter["periods"]["holdout"]
            names = [n for n, m in adapter["models"].items() if m["role"] == "incumbent"] + kept
            conn = adapter["connect"]() if adapter.get("connect") else _snapshot_conn(sport)
            try:
                by_model = {n: validate_records(adapter, adapter["walk"](conn, seasons, n), seasons) for n in names}
            finally:
                if hasattr(conn, "close"):
                    conn.close()
            sub = {**adapter, "models": {n: adapter["models"][n] for n in names},
                   "periods": {"holdout": seasons}}
            res = evaluate_records(sub, by_model, stages=("holdout",))
            res |= {"protocol_id": pid, "seasons": seasons, "earlier_uses": earlier,
                    "note": "Report only: nothing is promoted; a holdout look is one-shot and a keep here is not a promotion."}
            p = root / "holdout" / "result.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(res, indent=1, default=str))
            _holdout_append(root, {"event": "completed", "at": _now(), "result": str(p)})
            return res
        except Exception as exc:
            _holdout_append(root, {"event": "failed", "at": _now(), "reason": f"{type(exc).__name__}: {exc}"[:500]})
            raise


# --- Forward test store (separate file; candidate forecasts are never official forecasts) -----------------------------------
FORWARD_FILE = "forward-candidates.sqlite"
FORWARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS rf_forecasts (
  id INTEGER PRIMARY KEY, sport TEXT NOT NULL, protocol_id TEXT NOT NULL, model TEXT NOT NULL, role TEXT NOT NULL
    CHECK (role IN ('incumbent','baseline','candidate')), model_version TEXT NOT NULL, data_version TEXT NOT NULL,
  game_id TEXT NOT NULL, season INTEGER, market TEXT NOT NULL, line REAL, line_key TEXT NOT NULL,
  start_at TEXT NOT NULL, forecast_cutoff TEXT NOT NULL, forecasted_at TEXT NOT NULL,
  p_a REAL NOT NULL, p_mid REAL, p_b REAL NOT NULL, quote TEXT, recorded_at TEXT NOT NULL,
  CHECK (forecasted_at < start_at), CHECK (forecast_cutoff <= forecasted_at),
  CHECK (p_a >= 0 AND p_b >= 0 AND p_a <= 1 AND p_b <= 1),
  UNIQUE (protocol_id, model, model_version, game_id, market, line_key, forecasted_at));
CREATE TABLE IF NOT EXISTS rf_grades (
  id INTEGER PRIMARY KEY, sport TEXT NOT NULL, game_id TEXT NOT NULL, market TEXT NOT NULL, line_key TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('a','b','mid','push','void')), point_actual TEXT, graded_at TEXT NOT NULL,
  UNIQUE (sport, game_id, market, line_key));
CREATE TABLE IF NOT EXISTS rf_looks (
  id INTEGER PRIMARY KEY, protocol_id TEXT NOT NULL, sport TEXT NOT NULL, candidate TEXT NOT NULL, market TEXT NOT NULL,
  look_at TEXT NOT NULL, n_matched INTEGER NOT NULL, status TEXT NOT NULL, confirmatory INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS rf_forecasts_no_update BEFORE UPDATE ON rf_forecasts
  BEGIN SELECT RAISE(ABORT, 'rf_forecasts is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rf_forecasts_no_delete BEFORE DELETE ON rf_forecasts
  BEGIN SELECT RAISE(ABORT, 'rf_forecasts is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rf_grades_no_update BEFORE UPDATE ON rf_grades
  BEGIN SELECT RAISE(ABORT, 'rf_grades is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rf_grades_no_delete BEFORE DELETE ON rf_grades
  BEGIN SELECT RAISE(ABORT, 'rf_grades is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rf_looks_no_update BEFORE UPDATE ON rf_looks
  BEGIN SELECT RAISE(ABORT, 'rf_looks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rf_looks_no_delete BEFORE DELETE ON rf_looks
  BEGIN SELECT RAISE(ABORT, 'rf_looks is append-only'); END;
"""


def forward_path() -> Path:
    """research/forward-candidates.sqlite under the data directory. It holds only candidate research forecasts; it is not
    an official store, and nothing official reads it."""
    return config.data_dir() / "research" / FORWARD_FILE


def forward_connect(path: Optional[Path] = None):
    import sqlite3
    path = Path(path or forward_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(FORWARD_SCHEMA)
    return conn


def _lk(line) -> str:
    return "" if line is None else repr(round(float(line), 4))


def forward_record(path: Optional[Path], *, sport: str, protocol_id: str, model: str, role: str, model_version: str,
                   data_version: str, forecasted_at: str, records: list) -> dict:
    """Appends forecasts made at `forecasted_at` (before the game starts, from inputs at or before it). Rejected records are
    listed with the reason, never stored; an identical forecast (same model version, game, market, line and time) is a no-op."""
    default_at = _iso(forecasted_at)
    conn = forward_connect(path)
    ins, dup, rejected = 0, 0, []
    try:
        for r in records:
            try:
                forecasted_at = _iso(r["forecast_at"]) if r.get("forecast_at") else default_at   # when the model actually ran
                cut, start = _iso(r["forecast_cutoff"]), _iso(r["start_at"])
                if r.get("outcome") is not None:
                    raise AdapterInvalid("a forward forecast must not carry an outcome")
                if start <= forecasted_at:
                    raise AdapterInvalid(f"forecast at {forecasted_at} is not before the start {start}")
                if cut > forecasted_at:
                    raise AdapterInvalid(f"information cutoff {cut} is after the forecast time {forecasted_at}")
                p = r["probs"]
                cur = conn.execute(
                    "INSERT OR IGNORE INTO rf_forecasts (sport, protocol_id, model, role, model_version, data_version, "
                    "game_id, season, market, line, line_key, start_at, forecast_cutoff, forecasted_at, p_a, p_mid, p_b, quote, "
                    "recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sport, protocol_id, model, role, model_version, data_version, r["game_id"], r.get("season"), r["market"],
                     r.get("line"), _lk(r.get("line")), start, cut, forecasted_at, p["a"], p.get("mid"), p["b"],
                     json.dumps(r["quote"]) if r.get("quote") else None, _now()))
                ins += cur.rowcount
                dup += 1 - cur.rowcount
            except (AdapterInvalid, KeyError, TypeError, ValueError) as exc:
                rejected.append({"game_id": r.get("game_id"), "market": r.get("market"), "reason": str(exc)[:200]})
        conn.commit()
    finally:
        conn.close()
    return {"inserted": ins, "already_recorded": dup, "rejected": rejected}


def forward_grade(path: Optional[Path], adapter: dict, official, now: Optional[str] = None) -> dict:
    """Grades stored forecasts whose game has started, from the official store read-only (`official` is only ever queried;
    PRAGMA query_only is forced on it). Without an adapter `settle`, nothing is graded and that is said."""
    if not adapter.get("settle"):
        return {"graded": 0, "status": "unavailable", "reason": "the adapter has no settle(): grading is not possible yet"}
    official.execute("PRAGMA query_only = ON")
    now = _iso(now or _now())
    conn = forward_connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT f.game_id, f.market, f.line, f.line_key FROM rf_forecasts f LEFT JOIN rf_grades g "
            "ON g.sport=f.sport AND g.game_id=f.game_id AND g.market=f.market AND g.line_key=f.line_key "
            "WHERE f.sport=? AND g.id IS NULL AND f.start_at < ?", (adapter["sport"], now)).fetchall()
        recs = [{"game_id": r["game_id"], "market": r["market"], "line": r["line"]} for r in rows]
        graded = 0
        for g in adapter["settle"](official, recs):
            if g.get("outcome") not in ("a", "b", "mid", "push", "void"):
                continue
            cur = conn.execute("INSERT OR IGNORE INTO rf_grades (sport, game_id, market, line_key, outcome, point_actual, "
                               "graded_at) VALUES (?,?,?,?,?,?,?)",
                               (adapter["sport"], g["game_id"], g["market"], _lk(g.get("line")), g["outcome"],
                                json.dumps(g.get("point_actual")) if g.get("point_actual") is not None else None, _now()))
            graded += cur.rowcount
        conn.commit()
        return {"graded": graded, "ungraded_started": len(recs) - graded, "status": "ok"}
    finally:
        conn.close()


def forward_records(path: Optional[Path], sport: str, protocol_id: Optional[str] = None) -> tuple:
    """(by_model records with outcomes, {model: {role, version}}): per model, game, market and line the LAST forecast made
    before the start (every model by the same rule), joined to its grade."""
    conn = forward_connect(path)
    try:
        q = ("SELECT f.*, g.outcome, g.point_actual FROM rf_forecasts f LEFT JOIN rf_grades g ON g.sport=f.sport AND "
             "g.game_id=f.game_id AND g.market=f.market AND g.line_key=f.line_key WHERE f.sport=?"
             + (" AND f.protocol_id=?" if protocol_id else "") + " ORDER BY f.forecasted_at, f.id")
        rows = conn.execute(q, (sport, protocol_id) if protocol_id else (sport,)).fetchall()
    finally:
        conn.close()
    latest, info = {}, {}
    for r in rows:
        latest[(r["model"], r["model_version"], r["game_id"], r["market"], r["line_key"])] = r
        info[r["model"]] = {"role": r["role"], "version": r["model_version"], "description": "forward forecasts",
                            "data_version": r["data_version"]}
    by_model: dict = {}
    for (model, *_), r in latest.items():
        probs = {"a": r["p_a"], "b": r["p_b"]} if r["p_mid"] is None else {"a": r["p_a"], "mid": r["p_mid"], "b": r["p_b"]}
        pa = json.loads(r["point_actual"]) if r["point_actual"] else None
        by_model.setdefault(model, []).append({
            "game_id": r["game_id"], "season": r["season"] if r["season"] is not None else 0, "start_at": r["start_at"],
            "forecast_cutoff": r["forecast_cutoff"], "market": r["market"], "line": r["line"], "probs": probs,
            "outcome": r["outcome"], "point_actual": pa, "quote": json.loads(r["quote"]) if r["quote"] else None})
    return by_model, info


def min_forward(sport: str) -> int:
    return PROMOTION["min_settled_forward"].get(sport, PROMOTION["min_settled_forward"]["default"])


def promotion_recommendation(sport: str, ev: dict, prior_looks: Optional[dict] = None) -> dict:
    """The recommendation object: per candidate and market, each pre-registered criterion as passed / failed / not_evaluable.
    It never promotes: a person decides, on the forward record, under MODEL_SELECTION §6."""
    prior_looks = prior_looks or {}
    cands = ev.get("candidates") or []
    out = {"protocol": PROTOCOL_V3["name"], "sport": sport, "never_promotes": True, "manual_decision_required": True,
           "criteria_version": PROMOTION, "candidates": {}}
    if not cands:
        return out | {"status": "no_candidates", "reason": "no candidate forecasts exist: there is nothing to recommend "
                                                           "(this is not a successful search)"}
    need = min_forward(sport)
    for c in cands:
        per_market = {}
        for m, e in ev["markets"].items():
            cmp_ = e.get("comparisons", {}).get(c) if e.get("status") == "evaluated" else None
            if cmp_ is None:
                continue
            fw = cmp_["vs_incumbent"]["forward"]
            vi = fw.get("vs_incumbent") or {}
            n = vi.get("n", 0)
            crit = {"min_settled_forward": {"status": "passed" if n >= need else "failed", "n": n, "need": need},
                    "full_coverage": {"status": "passed" if fw["coverage"] == 1.0 else "failed", "coverage": fw["coverage"]}}
            if n >= need and vi:
                inc_ece = (e["models"][e["incumbent"]].get("calibration") or {}).get("ece")
                c_ece = (e["models"][c].get("calibration") or {}).get("ece")
                crit["paired_log_loss"] = {"status": "passed" if vi["ci95"][1] < 0 else "failed", "mean": vi["mean"],
                                           "ci95": vi["ci95"]}
                crit["calibration"] = {"status": "passed" if (c_ece is not None and inc_ece is not None and c_ece <= inc_ece + 0.01)
                                       else "failed", "candidate_ece": c_ece, "incumbent_ece": inc_ece}
                ca, ia = vi.get("candidate_accuracy"), vi.get("reference_accuracy")
                crit["accuracy"] = {"status": "passed" if ca is not None and ia is not None and ca >= ia else "failed",
                                    "candidate": ca, "incumbent": ia}
                book = e["models"][c]["vs_book"]
                if book.get("status") == "ok" and book["valid"] >= PROMOTION["sportsbook"]["min_quotes"]:
                    crit["sportsbook"] = {"status": "passed" if book["diff_ci95"][0] <= 0 else "failed", "n": book["valid"],
                                          "diff_ci95": book["diff_ci95"]}
                else:
                    crit["sportsbook"] = {"status": "not_evaluable", "valid_quotes": book.get("valid", 0),
                                          "reason": "fewer than the minimum matched valid quotes (or none)"}
            else:
                for k in ("paired_log_loss", "calibration", "accuracy", "sportsbook"):
                    crit[k] = {"status": "not_evaluable", "reason": f"only {n} of the required {need} settled matched games"}
            failed = [k for k, v in crit.items() if v["status"] == "failed"]
            if n < need:
                status = "insufficient_evidence"
            else:
                status = "not_recommended" if failed else "recommend_for_review"
            per_market[m] = {"status": status, "criteria": crit, "failed": failed,
                             "confirmatory_look": n >= need and not prior_looks.get((c, m)),
                             "look_label": "confirmatory (first look at the minimum count)" if n >= need and not prior_looks.get((c, m))
                             else "exploratory (repeated or early look)"}
        order = [v["status"] for v in per_market.values()]
        out["candidates"][c] = {"status": ("recommend_for_review" if "recommend_for_review" in order and "not_recommended" not in order
                                           else "not_recommended" if "not_recommended" in order else "insufficient_evidence"),
                                "markets": per_market}
    return out


def forward_evaluate(path: Optional[Path], adapter: dict, protocol_id: Optional[str] = None, record_look: bool = True) -> dict:
    """Evaluates the stored forward forecasts with the same evaluator (stage `forward`) and builds the promotion
    recommendation. Each call is a look: logged; only the first look at a market's minimum count is confirmatory."""
    by_model, info = forward_records(path, adapter["sport"], protocol_id)
    if not by_model:
        return {"status": "no_forward_forecasts", "recommendation": promotion_recommendation(
            adapter["sport"], {"candidates": [], "markets": {}})}
    seasons = sorted({r["season"] for recs in by_model.values() for r in recs})
    fw = {**adapter, "models": {n: {"role": i["role"], "version": i["version"], "description": i["description"]}
                                for n, i in info.items()}, "periods": {"forward": seasons}}
    ev = evaluate_records(fw, by_model, stages=("forward",))
    conn = forward_connect(path)
    try:
        prior = {}
        for r in conn.execute("SELECT candidate, market, n_matched FROM rf_looks WHERE sport=? AND protocol_id=?",
                              (adapter["sport"], protocol_id or "")):
            if r["n_matched"] >= min_forward(adapter["sport"]):
                prior[(r["candidate"], r["market"])] = True
        rec = promotion_recommendation(adapter["sport"], ev, prior)
        if record_look:
            for c, cv in rec["candidates"].items():
                for m, mv in cv["markets"].items():
                    n = mv["criteria"]["min_settled_forward"]["n"]
                    conn.execute("INSERT INTO rf_looks (protocol_id, sport, candidate, market, look_at, n_matched, status, "
                                 "confirmatory) VALUES (?,?,?,?,?,?,?,?)", (protocol_id or "", adapter["sport"], c, m, _now(), n,
                                                                            mv["status"], int(mv["confirmatory_look"])))
            conn.commit()
    finally:
        conn.close()
    return {"status": "evaluated", "evaluation": ev, "recommendation": rec,
            "graded": sum(1 for recs in by_model.values() for r in recs if r["outcome"] is not None),
            "ungraded": sum(1 for recs in by_model.values() for r in recs if r["outcome"] is None)}


# --- CLI for protocol v3 (python -m app.autoresearch v3 <cmd>) -----------------------------------------------------------------
def main_v3(argv: list) -> int:
    p = argparse.ArgumentParser(prog="python -m app.autoresearch v3")
    p.add_argument("cmd", choices=("daily", "holdout", "forward-record", "forward-grade", "forward-eval"))
    p.add_argument("--exp-root", type=Path, help="experiment root (outside the repository and the live data)")
    p.add_argument("--sport", choices=FIVE_SPORTS, action="append")
    p.add_argument("--model", action="append")
    p.add_argument("--max-items", type=int, default=BUDGET_V3["max_items_per_sport"])
    p.add_argument("--max-seconds", type=float, default=BUDGET_V3["max_seconds_per_sport"])
    p.add_argument("--reuse-reason")
    p.add_argument("--now")
    a = p.parse_args(argv)
    chosen = a.sport or list(FIVE_SPORTS)
    try:
        if a.cmd == "daily":
            r = daily_v3(chosen, a.exp_root, a.max_items, a.max_seconds)
            print(json.dumps({"outcomes": r["outcomes"], "report_file": r["report_file"],
                              "reasons": {s: v["reason"] for s, v in r["sports"].items()}}, indent=1))
        elif a.cmd == "holdout":
            print(json.dumps(holdout_v3(chosen[0], a.exp_root, reuse_reason=a.reuse_reason), indent=1, default=str))
        else:
            import sqlite3
            adapter = get_adapter(chosen[0])
            if adapter is None:
                raise Refused(f"no research adapter for {chosen[0]}")
            pid = v3_protocol_id(adapter)
            uri = f"file:{config.db_path(chosen[0])}?mode=ro"          # official store: read-only, only ever queried
            if a.cmd == "forward-eval":
                r = forward_evaluate(None, adapter, pid)
                print(json.dumps(r["recommendation"], indent=1, default=str))
            elif a.cmd == "forward-grade":
                oc = sqlite3.connect(uri, uri=True)
                oc.row_factory = sqlite3.Row
                print(json.dumps(forward_grade(None, adapter, oc, a.now)))
            else:
                oc = sqlite3.connect(uri, uri=True)
                oc.row_factory = sqlite3.Row
                oc.execute("PRAGMA query_only = ON")
                now = _iso(a.now or _now())
                for name in a.model or [n for n in adapter["models"]]:
                    m = adapter["models"][name]
                    recs = adapter["forecast_upcoming"](oc, now, name) if adapter.get("forecast_upcoming") else []
                    print(json.dumps({name: forward_record(None, sport=chosen[0], protocol_id=pid, model=name, role=m["role"],
                                                           model_version=m["version"], data_version=adapter["data_version"],
                                                           forecasted_at=now, records=recs)}))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


# --- CLI -------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "v3":
        return main_v3(list(argv[1:]))
    p = argparse.ArgumentParser(prog="python -m app.autoresearch", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "trial", "status", "daily", "holdout", "agent", "stop"):
        s = sub.add_parser(name)
        s.add_argument("--exp-root", type=Path, required=True)
        s.add_argument("--workspace", type=Path, required=(name != "daily"))
        s.add_argument("--sport", choices=sorted(FIVE_SPORTS), action="append")
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
        if a.cmd == "daily" and a.sport and set(a.sport) - {"nba", "nhl"}:
            # NFL, NCAAB, Soccer (and any sport named with them) run through protocol v3; one `sport-outcome:` line per sport
            r = daily_v3(a.sport, a.exp_root, a.max_trials if a.max_trials != BUDGETS["max_trials"] else BUDGET_V3["max_items_per_sport"],
                         a.max_minutes * 60 if a.max_minutes != BUDGETS["max_minutes"] else BUDGET_V3["max_seconds_per_sport"])
            for sp, v in r["sports"].items():
                print(f"sport-outcome: {sp} {v['outcome']} {v['reason'] or ''}".rstrip())
            print(json.dumps({"outcomes": r["outcomes"], "report_file": r["report_file"]}))
            return 1 if "failed" in r["outcomes"].values() else 0
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
            for sp in chosen:
                print(f"sport-outcome: {sp} " + ("refused " + r["refused"][sp] if sp in r.get("refused", {}) else
                      "evaluated" if any(t["sport"] == sp and t["status"] in ("keep", "discard") for t in r["trials"]) else
                      "no_candidates" if not any(t["sport"] == sp for t in r["trials"]) else "failed"))
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
