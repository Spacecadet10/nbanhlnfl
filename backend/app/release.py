"""Pinned release for the scheduled jobs (HANDOFF 2026-10-03, operations).

The launchd jobs run a separate git worktree (branch `release`) that is only moved to a reviewed commit, never edited, so
work in the development checkout can no longer reach the live jobs or change a research definition mid-evening. With
NBA_RELEASE_GUARD=1 (set only in the release plists) every scheduled command first checks that:
- the code being imported is the release checkout named by NBA_RELEASE_ROOT;
- that checkout has no uncommitted changes;
- that checkout is at the pinned commit (the release-pin file in its git folder, written by `python -m app.release pin`
  after each reviewed fast-forward): a checkout moved without a release decision is refused;
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
PIN_FILE = "release-pin"   # in the release checkout's git folder: the commit the scheduled jobs may run
# Scheduled commands and the job_runs name a refusal is recorded under (job_runs.job has a fixed set of names).
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


def pin_path(root: Path) -> Path:
    """Where a release's pinned commit is written: inside the checkout's own git folder, so it is never part of a commit
    (a commit cannot name itself) and never makes the checkout look changed. Releasing = fast-forward + write the pin."""
    path = Path(_git(root, "rev-parse", "--git-path", PIN_FILE).strip())
    return path if path.is_absolute() else root / path


def pinned_commit(root: Path) -> Optional[str]:
    """The pin file's commit; at least 7 hex characters, else None. NBA_RELEASE_COMMIT is NOT an input: the CLI sets it
    to the commit that passed the check, so it only records which commit ran (jobs.py) and can never pin anything."""
    path = pin_path(root)
    value = path.read_text().strip() if path.is_file() else ""
    return value if len(value) >= 7 and all(c in "0123456789abcdefABCDEF" for c in value) else None


def write_pin(root: Path) -> str:
    """Pins the checkout's current commit (the release procedure's last step); returns it."""
    commit = _git(root, "rev-parse", "HEAD").strip()
    pin_path(root).write_text(commit + "\n")
    return commit


def _checkout(root: Path) -> tuple[Optional[str], list[str]]:
    """The commit and every reason this is not the clean release checkout using the shared data folder."""
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
        pinned = pinned_commit(root)
        if not pinned:
            problems.append(f"no pinned release commit ({PIN_FILE} in the checkout's git folder; `python -m app.release pin`)")
        elif not commit.startswith(pinned.lower()):
            problems.append(f"the release checkout is at {commit[:12]}, not the pinned commit {pinned[:12]}")
    except (OSError, subprocess.SubprocessError) as exc:
        problems.append(f"cannot verify the release checkout with git: {type(exc).__name__}")
    if not os.environ.get("NBA_DATA_DIR"):
        problems.append("NBA_DATA_DIR is not set: the release would use its own empty data folder")
    return commit, problems


def check(sport: str, root: Optional[Path] = None) -> dict:
    """The release facts and every reason the scheduled jobs must not run from here (empty `problems`: they may)."""
    root = (root or code_root()).resolve()
    commit, problems = _checkout(root)
    shipped, stored = shipped_schema(), store_schema(sport)
    if stored is None:
        problems.append(f"no migrated {sport} store at {config.db_path(sport)}")
    elif stored != shipped:
        problems.append(f"store schema {stored} differs from this release's schema {shipped}: update the release deliberately")
    return {"commit": commit, "root": str(root), "data_dir": str(config.data_dir()), "store_schema": stored,
            "release_schema": shipped, "problems": problems}


def migration_context(sport: str, info: Optional[dict] = None) -> list[str]:
    """Why this process is not the verified release environment, the only one that may write to a protected live data
    folder (config.protected_data_dir) and, with the explicit `migrate` command, migrate it: NBA_RELEASE_GUARD unset, or
    any `check` problem other than the store-schema line (README release procedure, step 4). Empty: it may. `info` is a
    `check(sport)` result already computed by the caller; `check` itself is unchanged."""
    if not enabled():
        return [f"{GUARD_ENV} is not 1 (not the release environment)"]
    info = info if info is not None else check(sport)
    problems = [p for p in info["problems"] if not (p.startswith("store schema ") and "differs from this release's schema" in p)]
    root = code_root().resolve()
    held = []
    try:            # by file identity: another spelling of the same folder (case variant, firmlink) is the same folder
        st = root.stat()
        for t in (config.data_dir().expanduser().resolve(), config.db_path(sport).resolve()):
            for a in (t, *t.parents):
                try:
                    s = a.stat()
                except FileNotFoundError:   # not created yet: cannot be the existing code root
                    continue
                if (s.st_dev, s.st_ino) == (st.st_dev, st.st_ino):
                    held.append(t)
                    break
    except OSError as exc:
        problems.append(f"cannot tell whether the checkout {root} holds this data folder: {type(exc).__name__}: {exc}")
    if held:       # the development main checkout with the release variables set is still not a release
        problems.append(f"the code runs from {root}, the checkout that holds this data folder ({held[0]}); a release runs "
                        "from its own checkout")
    return problems


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
            run_id = jobs._start(conn, GUARDED.get(cmd, "odds"))
            return jobs._finish(conn, run_id, "failed", 0, {"reason": "release guard refused the scheduled command",
                                                            "command": cmd, "release_check": info})["run_id"]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


# Telegram sender (`app.cli telegram`) and ticket helper (`python -m app.tickets`): they read the stores and write only the
# receipt ledger, so the store-schema rule of the scheduled jobs does not apply. They must run from the clean release
# checkout and use the production ledger and credentials, named explicitly and already present: a release folder's own
# empty ledger would forget every receipt, duplicate guard and $5 reservation (telegram.ledger never creates it here).
TELEGRAM_LEDGER_ENV_NAME = config.TELEGRAM_LEDGER_ENV
TELEGRAM_CREDENTIALS_ENV_NAME = config.TELEGRAM_CREDENTIALS_ENV


def delivery_check(root: Optional[Path] = None) -> dict:
    """Every reason the guarded sender/helper must not run from here (empty `problems`: they may). No network, no writes."""
    root = (root or code_root()).resolve()
    commit, problems = _checkout(root)
    named = {}
    for env, what in ((TELEGRAM_LEDGER_ENV_NAME, "receipt ledger"), (TELEGRAM_CREDENTIALS_ENV_NAME, "Telegram credentials")):
        value = os.environ.get(env)
        named[env] = value
        if not value:
            problems.append(f"{env} is not set: the {what} must be named explicitly in the release environment")
            continue
        path = Path(value)
        if path.is_symlink():
            problems.append(f"the {what} {path} is a symlink")
        elif not path.is_file():
            problems.append(f"the {what} {path} does not exist; it is never created by a release")
    return {"commit": commit, "root": str(root), "data_dir": str(config.data_dir()),
            "ledger": named[TELEGRAM_LEDGER_ENV_NAME], "credentials": named[TELEGRAM_CREDENTIALS_ENV_NAME], "problems": problems}


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["pin"]:
        raise SystemExit("usage: python -m app.release pin   (pins this checkout's current commit for the scheduled jobs)")
    print(write_pin(code_root().resolve()))
