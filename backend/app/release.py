"""Pinned release for the scheduled jobs (HANDOFF 2026-10-03, operations).

The launchd jobs run a separate git worktree (branch `release`) that is only moved to a reviewed commit, never edited, so
work in the development checkout can no longer reach the live jobs or change a research definition mid-evening. With
NBA_RELEASE_GUARD=1 (set only in the release plists) every scheduled command first checks that:
- the code being imported is the release checkout named by NBA_RELEASE_ROOT;
- that checkout has no uncommitted changes;
- NBA_DATA_DIR is set (otherwise the release would use its own, empty data folder);
- the store's schema version equals the highest migration this release ships (a newer store was migrated by newer code; an
  older one would be migrated by this release: both are release decisions, never a side effect of a scheduled job).
A failed check refuses before any network request, lock or migration, and is recorded as a failed job run. Every job run of
a release records the commit it ran (`release` in job_runs.detail).
"""
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from . import config, db

GUARD_ENV = "NBA_RELEASE_GUARD"
ROOT_ENV = "NBA_RELEASE_ROOT"
COMMIT_ENV = "NBA_RELEASE_COMMIT"
# Scheduled commands and the job_runs name a refusal is recorded under (job_runs.job has a fixed set of names).
REFUSAL_REPEAT = {"tick": 15 * 60}   # seconds: a once-a-minute command records a repeated refusal at most this often
GUARDED = {"odds": "odds", "tick": "odds", "daily": "refresh", "refresh": "refresh", "injuries": "injuries", "score": "score",
           "predict-now": "near_tipoff", "predict": "near_tipoff", "markets": "markets", "boxscores": "boxscores"}


def enabled() -> bool:
    return os.environ.get(GUARD_ENV) == "1"


def code_root() -> Path:
    """The checkout this `app` package was imported from."""
    return Path(__file__).resolve().parents[2]


def shipped_schema() -> int:
    return max(int(f.name[:4]) for f in db.MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def store_schema(sport: str) -> Optional[int]:
    path = config.db_path(sport)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        return conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True, timeout=30).stdout


def check(sport: str, root: Optional[Path] = None) -> dict:
    """The release facts and every reason the scheduled jobs must not run from here (empty `problems`: they may)."""
    root = (root or code_root()).resolve()
    problems = []
    expected = os.environ.get(ROOT_ENV)
    if not expected:
        problems.append(f"{ROOT_ENV} is not set")
    elif Path(expected).resolve() != root:
        problems.append(f"code imported from {root}, not the release checkout {Path(expected).resolve()}")
    commit = None
    try:
        commit = _git(root, "rev-parse", "HEAD").strip()
        changed = [line for line in _git(root, "status", "--porcelain").splitlines() if line.strip()]
        if changed:
            problems.append(f"the release checkout has uncommitted changes: {', '.join(c[3:] for c in changed[:5])}")
    except (OSError, subprocess.SubprocessError) as exc:
        problems.append(f"cannot verify the release checkout with git: {type(exc).__name__}")
    if not os.environ.get("NBA_DATA_DIR"):
        problems.append("NBA_DATA_DIR is not set: the release would use its own empty data folder")
    shipped, stored = shipped_schema(), store_schema(sport)
    if stored is None:
        problems.append(f"no migrated {sport} store at {config.db_path(sport)}")
    elif stored != shipped:
        problems.append(f"store schema {stored} differs from this release's schema {shipped}: update the release deliberately")
    return {"commit": commit, "root": str(root), "data_dir": str(config.data_dir()), "store_schema": stored,
            "release_schema": shipped, "problems": problems}


def record_refusal(sport: str, cmd: str, info: dict) -> Optional[int]:
    """A failed job_runs row for a refused scheduled command; never migrates, never raises."""
    from . import jobs
    if not config.db_path(sport).is_file():
        return None
    try:
        conn = db.connect(sport=sport)
        try:
            if not db.has_table(conn, "job_runs"):
                return None
            if cmd in REFUSAL_REPEAT:
                from datetime import datetime, timedelta, timezone
                since = (datetime.now(timezone.utc) - timedelta(seconds=REFUSAL_REPEAT[cmd])).strftime("%Y-%m-%dT%H:%M:%SZ")
                if conn.execute("SELECT 1 FROM job_runs WHERE job = ? AND status = 'failed' AND started_at > ? "
                                "AND json_extract(detail, '$.command') = ?", (GUARDED[cmd], since, cmd)).fetchone():
                    return None
            run_id = jobs._start(conn, GUARDED.get(cmd, "odds"))
            return jobs._finish(conn, run_id, "failed", 0, {"reason": "release guard refused the scheduled command",
                                                            "command": cmd, "release_check": info})["run_id"]
        finally:
            conn.close()
    except sqlite3.Error:
        return None
