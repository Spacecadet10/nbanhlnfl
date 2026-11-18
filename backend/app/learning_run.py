"""Guard every heavy learning job goes through: `python -m app.learning_run --name X --max-minutes M --max-rss-gb G -- cmd...`.

One heavy job per machine (non-blocking flock: a second one is refused, never queued), lowered priority, capped BLAS
threads, a wall-clock limit and a resident-memory cap enforced on the child's whole process group, and no start within a
window before any game tips off. Every start, end and refusal is appended to <data dir>/research/learning-runs.jsonl.
Reads game times with read-only queries; writes nothing else. Exit codes: the child's own, or 75 refused, 124 wall-clock kill,
125 memory kill, 126 memory unreadable (killed: fail closed), 127 command not started, 64 usage."""
import argparse
import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import config

EXIT_USAGE, EXIT_REFUSED, EXIT_TIMEOUT, EXIT_MEMORY, EXIT_UNMEASURABLE, EXIT_NOT_STARTED = 64, 75, 124, 125, 126, 127
FMT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_DEFER_HOURS = 2.5
DEFAULT_SPORTS = ("nba", "nhl", "ncaab")
GAME_TABLES = {"nba": ("games", "tipoff_at"), "nhl": ("games", "tipoff_at"), "ncaab": ("games", "tipoff_at"),
               "nfl": ("games", "tipoff_at"), "soccer": ("soccer_matches", "kickoff_at")}
THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
GRACE_S = 5.0


def lock_path(data_dir: Path) -> Path:
    return Path(os.environ.get("LEARNING_HEAVY_LOCK") or Path(data_dir) / ".learning-heavy.lock")


def log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "research" / "learning-runs.jsonl"


def _log(path: Path, **event) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": datetime.now(timezone.utc).strftime(FMT), **event}, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def games_in_window(data_dir: Path, sports: Sequence[str], hours: float, now: datetime) -> list:
    """(sport, game id, tipoff) of games tipping off in (now, now + hours], from read-only queries. A store that does not
    exist is not a window; a store that exists but cannot be read raises, so the caller refuses (fail closed)."""
    lo, hi = now.strftime(FMT), (now + timedelta(hours=hours)).strftime(FMT)
    found = []
    for sport in sports:
        path = Path(data_dir) / f"{sport}.sqlite"
        if not path.is_file():
            continue
        table, col = GAME_TABLES[sport]
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise RuntimeError(f"{sport} store: {exc}") from exc
        try:
            conn.execute("PRAGMA query_only = ON")
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            idcol = "match_id" if table == "soccer_matches" else "game_id"
            found += [(sport, r[0], r[1]) for r in conn.execute(
                f"SELECT {idcol}, {col} FROM {table} WHERE {col} > ? AND {col} <= ? "
                f"AND status NOT IN ('postponed','canceled','final') ORDER BY {col}", (lo, hi))]
        except sqlite3.Error as exc:
            raise RuntimeError(f"{sport} store: {exc}") from exc
        finally:
            conn.close()
    return found


def group_rss_bytes(pgid: int) -> Optional[int]:
    """Resident memory summed over every process in the group, or None when it cannot be read."""
    try:
        out = subprocess.run(["ps", "-axo", "pgid=,rss="], capture_output=True, text=True, timeout=10, check=True).stdout
        total = 0
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and int(parts[0]) == pgid:
                total += int(parts[1]) * 1024
        return total
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + GRACE_S
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)       # also reaps grandchildren that outlived the leader
    except ProcessLookupError:
        pass
    proc.wait()


def run_guarded(argv: Sequence[str], *, name: str, max_minutes: float, max_rss_gb: float,
                defer_hours: float = DEFAULT_DEFER_HOURS, defer_sports: Sequence[str] = DEFAULT_SPORTS, nice: int = 10,
                threads: int = 2, data_dir: Optional[Path] = None, poll_s: float = 1.0, now: Optional[datetime] = None,
                rss_reader: Callable[[int], Optional[int]] = group_rss_bytes) -> int:
    data_dir = Path(data_dir or config.data_dir())
    log = log_path(data_dir)
    run_id = uuid.uuid4().hex[:12]
    base = {"run_id": run_id, "name": name, "argv": list(argv)}
    if not argv or max_minutes <= 0 or max_rss_gb <= 0:
        _log(log, event="refused", reason="usage: a command, --max-minutes > 0 and --max-rss-gb > 0 are required", **base)
        return EXIT_USAGE
    lp = lock_path(data_dir)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _log(log, event="refused", reason=f"another heavy learning job holds the lock {lp}", **base)
            return EXIT_REFUSED
        try:
            window = games_in_window(data_dir, defer_sports, defer_hours, now or datetime.now(timezone.utc))
        except Exception as exc:
            _log(log, event="refused", reason=f"game schedule unreadable ({type(exc).__name__}: {exc}); refusing to start", **base)
            return EXIT_REFUSED
        if window:
            shown = ", ".join(f"{s} {g} {t}" for s, g, t in window[:5])
            _log(log, event="refused", reason=f"{len(window)} game(s) tip off within {defer_hours} h: {shown}", **base)
            return EXIT_REFUSED
        env = dict(os.environ)
        for v in THREAD_VARS:
            env.setdefault(v, str(threads))
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(list(argv), env=env, start_new_session=True,
                                    preexec_fn=lambda: os.nice(nice))
        except OSError as exc:
            _log(log, event="end", rc=EXIT_NOT_STARTED, outcome=f"not started: {exc}", wall_s=0.0, peak_rss_mb=0, **base)
            return EXIT_NOT_STARTED
        _log(log, event="start", pid=proc.pid, nice=nice, max_minutes=max_minutes, max_rss_gb=max_rss_gb,
             defer_hours=defer_hours, **base)
        return _supervise(proc, log, base, t0, max_minutes * 60, max_rss_gb * 1024 ** 3, poll_s, rss_reader)
    finally:
        os.close(fd)


def _supervise(proc, log, base, t0, limit_s, cap_bytes, poll_s, rss_reader) -> int:
    peak, rc, outcome = 0, None, "exited"

    def stop(signum, _frame):
        nonlocal outcome
        outcome = f"killed: signal {signum}"
        _kill_group(proc)
    old = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)} if _main_thread() else {}
    try:
        while proc.poll() is None:
            if time.monotonic() - t0 > limit_s:
                outcome, rc = "killed: wall-clock limit", EXIT_TIMEOUT
            else:
                rss = rss_reader(proc.pid)
                if rss is None:
                    outcome, rc = "killed: memory cannot be read (failing closed)", EXIT_UNMEASURABLE
                else:
                    peak = max(peak, rss)
                    if rss > cap_bytes:
                        outcome, rc = f"killed: memory {rss / 1024 ** 2:.0f} MB over the cap {cap_bytes / 1024 ** 2:.0f} MB", EXIT_MEMORY
            if rc is not None:
                _kill_group(proc)
                break
            try:
                proc.wait(timeout=poll_s)
            except subprocess.TimeoutExpired:
                pass
        child_rc = proc.returncode
        if rc is None:
            rc = child_rc if child_rc >= 0 else 128 - child_rc
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    _log(log, event="end", rc=rc, outcome=outcome, wall_s=round(time.monotonic() - t0, 2), peak_rss_mb=round(peak / 1024 ** 2, 1), **base)
    return rc


def _main_thread() -> bool:
    import threading
    return threading.current_thread() is threading.main_thread()


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    p = argparse.ArgumentParser(prog="python -m app.learning_run", description=__doc__.split("\n")[0])
    p.add_argument("--name", required=True)
    p.add_argument("--max-minutes", type=float, required=True)
    p.add_argument("--max-rss-gb", type=float, required=True)
    p.add_argument("--defer-hours", type=float, default=DEFAULT_DEFER_HOURS)
    p.add_argument("--defer-sports", default=",".join(DEFAULT_SPORTS), help="comma list of nba,nhl,ncaab,nfl,soccer")
    p.add_argument("--nice", type=int, default=10)
    p.add_argument("--threads", type=int, default=2)
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else 0
    sports = tuple(s for s in a.defer_sports.split(",") if s)
    if not cmd or any(s not in GAME_TABLES for s in sports):
        print("usage: a command after -- and known --defer-sports are required", file=sys.stderr)
        return EXIT_USAGE
    return run_guarded(cmd, name=a.name, max_minutes=a.max_minutes, max_rss_gb=a.max_rss_gb, defer_hours=a.defer_hours,
                       defer_sports=sports, nice=a.nice, threads=a.threads)


if __name__ == "__main__":
    sys.exit(main())
