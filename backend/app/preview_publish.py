"""Republish read-only development copies of the live stores so a preview API does not serve one startup copy forever
(docs/REFRESH_PLAN.md, "Publish fresh data"). Run by scripts/dev.sh next to the API it starts; never writes the live
directory.

Per sport, each check (default every 60 s) reads the live store query-only and compares its data revision and last
successful refresh with the served copy. Only when the revision changed, or the copy's source time lags live by more
than `max_lag`, is the sport republished:
  1. consistent online backup of the live store (sqlite3 backup API over a mode=ro connection: committed WAL content
     included, nothing written to the live files) into a staging file inside the development directory;
  2. migrate the staging copy to this checkout's schema and check it (quick_check, store identity, job_runs present);
  3. publish: the staging database is copied into the served file through the SQLite backup API, which replaces its
     content in one transaction -- API readers see the previous revision or the new one, never a mix, and no open file is
     overwritten underneath a reader (WAL/-shm stay consistent).
A failure leaves the previous revision in place and is reported in PUBLISH_STATUS.json (shown by /api/freshness).
Research artifacts (research/, eval/, raw/) are copied once at startup by dev.sh and are not republished here.
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import db, devpaths, freshness, sports

STATUS_FILE = "PUBLISH_STATUS.json"
SPORTS = ("nba", "nhl", "nfl")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _state(path: Path) -> Optional[dict]:
    """(revision, last successful refresh) of a store, read-only; None if it cannot be read."""
    if not path.is_file():
        return None
    conn = _ro(path)
    try:
        ok = conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job='refresh' AND status='success'").fetchone()[0]
        return {"revision": freshness.revision(conn), "source_fetched_at": ok}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def needs_publish(live: Optional[dict], published: Optional[dict], served_exists: bool, max_lag: timedelta) -> Optional[str]:
    """Why a sport's copy must be republished now, or None. `published` is the live state recorded at the last
    successful publication (the served copy itself is migrated, so its own revision may legitimately differ)."""
    if live is None:
        return None
    if not served_exists or not published:
        return "no served copy"
    if live["revision"] != published.get("revision"):
        return "data changed"
    a, b = live["source_fetched_at"], published.get("source_fetched_at")
    if a and (not b or freshness._parse(a) - freshness._parse(b) > max_lag):
        return f"source time lags live by more than {int(max_lag.total_seconds() // 60)} min"
    return None


def _guard(live_dir: Path, dev_dir: Path) -> None:
    """The same proof dev.sh runs (app/devpaths.py): the development directory is separate from the live one, carries the
    launcher's marker and contains no symlink; files written in place are not hard links. Raises UnsafePaths."""
    devpaths.check(str(live_dir), str(dev_dir))


def _not_the_source(dst: Path, src: Path) -> None:
    """Last check right before the served file is written: it must be a plain, singly linked file that is not the source."""
    try:
        st = os.lstat(dst)
    except FileNotFoundError:
        return
    if os.path.islink(dst) or not os.path.isfile(dst) or st.st_nlink != 1 or (src.exists() and os.path.samefile(dst, src)):
        raise devpaths.UnsafePaths(f"refusing to write {dst}: it is a link or the live store itself")


def publish_sport(sport: str, live_dir: Path, dev_dir: Path) -> dict:
    name = sports.store(sport).db_file
    src, dst = live_dir / name, dev_dir / name
    _guard(live_dir, dev_dir)
    _not_the_source(dst, src)
    stage_dir = dev_dir / ".publish-staging"
    stage_dir.mkdir(exist_ok=True)
    stage = stage_dir / name
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{stage}{suffix}").unlink(missing_ok=True)
    started = _now()
    s = _ro(src)
    d = sqlite3.connect(stage)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    conn = db.connect(stage)
    try:
        applied = db.migrate(conn)
        db.claim_store(conn, sport)
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("quick_check failed on the staged copy")
        if not db.has_table(conn, "job_runs"):
            raise sqlite3.DatabaseError("staged copy has no job_runs table")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    _guard(live_dir, dev_dir)                    # again after the (slow) backup, right before the served file is written
    _not_the_source(dst, src)
    if dst.is_file():
        st = sqlite3.connect(stage)
        out = sqlite3.connect(dst, timeout=30)
        try:
            st.backup(out)           # one transaction on the served file: readers see old or new, never a mix
        finally:
            out.close()
            st.close()
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{stage}{suffix}").unlink(missing_ok=True)
    else:                            # first copy of this sport: nothing is reading it yet
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{dst}{suffix}").unlink(missing_ok=True)
        os.replace(stage, dst)
    if sport == "nfl":
        # The NFL activation is evidence, not executable code or a trained artifact. Publish it beside its store.
        source_activation = live_dir / "research/nfl_live/activation.json"
        target_activation = dev_dir / "research/nfl_live/activation.json"
        if source_activation.is_file():
            target_activation.parent.mkdir(parents=True, exist_ok=True)
            _not_the_source(target_activation, source_activation)
            content = json.loads(source_activation.read_text())
            temp = target_activation.with_suffix(".tmp")
            _not_the_source(temp, source_activation)
            temp.write_text(json.dumps(content, indent=1))
            os.replace(temp, target_activation)
    return {"status": "published", "source_backup_started_at": _fmt(started), "migrations_applied": applied,
            "seconds": round((_now() - started).total_seconds(), 1)}


def check_once(live_dir: Path, dev_dir: Path, *, max_lag: timedelta, interval: int,
               publish: Callable[[str, Path, Path], dict] = publish_sport) -> dict:
    """One check of every sport; writes SNAPSHOT_AT (when anything was published) and PUBLISH_STATUS.json. When the
    directories fail the path proof nothing at all is read or written (not even the status file, which could be a link):
    the refusal is returned, logged by `run`, and the API keeps reporting the copy as frozen."""
    now = _now()
    try:
        _guard(live_dir, dev_dir)
    except devpaths.UnsafePaths as exc:
        err = f"refused: {exc}"
        return {"last_attempt_at": _fmt(now), "last_error": err, "this_check": {s: "failed" for s in SPORTS}, "sports": {}}
    status_path = dev_dir / STATUS_FILE
    try:
        status = json.loads(status_path.read_text())
    except (OSError, ValueError):
        status = {}
    per = status.get("sports", {})
    initial = status.get("initial_snapshot_at")          # the startup copy time (dev.sh), before any republication
    if not initial:
        try:
            initial = (dev_dir / "SNAPSHOT_AT").read_text().strip() or None
        except OSError:
            initial = None
    ran, errors = {}, []
    for sport in SPORTS:
        name = sports.store(sport).db_file
        live = _state(live_dir / name)       # read before the backup: a change made meanwhile is caught next check
        why = needs_publish(live, per.get(sport, {}).get("live"), (dev_dir / name).is_file(), max_lag)
        if why is None:
            continue
        try:
            result = publish(sport, live_dir, dev_dir)
            per[sport] = {**result, "reason": why, "live": live, "published_at": _fmt(_now())}
            ran[sport] = "published"
        except Exception as exc:     # the previous revision stays served; the failure is visible
            err = f"{sport}: {type(exc).__name__}: {exc}"
            errors.append(err)
            per[sport] = {**per.get(sport, {}), "status": "failed", "error": err, "failed_at": _fmt(_now())}
            ran[sport] = "failed"
    if ran and not errors:
        tmp = dev_dir / ".SNAPSHOT_AT.tmp"
        tmp.write_text(_fmt(_now()) + "\n")
        os.replace(tmp, dev_dir / "SNAPSHOT_AT")
    status = {"interval_seconds": interval, "max_lag_seconds": int(max_lag.total_seconds()), "last_attempt_at": _fmt(now),
              "next_attempt_at": _fmt(now + timedelta(seconds=interval)), "last_error": "; ".join(errors) or None,
              "initial_snapshot_at": initial, "sports": per, "this_check": ran}
    tmp = dev_dir / f".{STATUS_FILE}.tmp"
    tmp.write_text(json.dumps(status, indent=1))
    os.replace(tmp, status_path)
    return status


def run(live_dir: Path, dev_dir: Path, *, interval: int = 60, max_lag_minutes: float = 10, once: bool = False,
        log: Callable[[str], None] = lambda m: print(m, flush=True)) -> int:
    _guard(live_dir, dev_dir)    # UnsafePaths (a ValueError): same/nested/linked directories, missing marker, symlinks
    while True:
        st = check_once(live_dir, dev_dir, max_lag=timedelta(minutes=max_lag_minutes), interval=interval)
        log(json.dumps({"at": st["last_attempt_at"], "error": st["last_error"], "this_check": st["this_check"]}))
        if once:
            return 1 if st["last_error"] else 0
        time.sleep(interval)
