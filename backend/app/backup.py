"""Store backup and restore around a release migration (README "Release"). A backup is SQLite's online backup of each sport's
store (consistent while other readers run), checked with `PRAGMA integrity_check`, with a manifest: sha256, schema
version, store identity and row counts. A restore copies a backup back only if its file matches the manifest, while holding
the store's job lock (no scheduled job can run against it), and moves the replaced files aside, never deleting them.
The raw response cache (`raw/`) is append-only and untouched by migrations; it is not copied.

    NBA_DATA_DIR=<store dir> python -m app.backup --dest <dir>            # backup of every store present (reads them only)
    NBA_DATA_DIR=<store dir> python -m app.backup --restore <backup dir> --sport nhl
    NBA_DATA_DIR=<store dir> python -m app.backup --restore <backup dir> --database research/forward-candidates.sqlite
"""
import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from . import sports as sport_defs

# Every sport with a store: the served ones and the data-only ones (NFL, soccer).
SPORTS = (*sport_defs.SPORT_IDS, *sport_defs.DATA_ONLY)
COUNTED = ("games", "predictions", "market_forecasts", "game_forecasts", "result_revisions", "research_cohorts",
           "agreement_observations", "adaptive_forecasts", "job_runs", "teams", "soccer_matches", "market_quotes",
           "raw_responses", "raw_fetches")


class BackupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _describe(path: Path) -> dict:
    """Reads a store that no one is writing (a backup copy, or a just-restored store under the job lock). `immutable=1`
    opens it without creating -wal/-shm files next to it."""
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] if "schema_migrations" in tables else None
        identity = conn.execute("SELECT sport FROM store_identity").fetchone() if "store_identity" in tables else None
        return {"integrity": conn.execute("PRAGMA integrity_check").fetchone()[0], "schema_version": version,
                "identity": identity[0] if identity else None,
                "rows": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNTED if t in tables}}
    finally:
        conn.close()


def run(data_dir: Path, dest: Path, *, stamp: Optional[str] = None, sports: Optional[list] = None) -> dict:
    """Backs up each sport's store under `dest/<stamp>/`. Reads the source stores only; refuses an existing destination.
    Without `sports`, every sport whose store file exists is backed up; each other sport is listed as `skipped` and named
    in a warning on stderr. A sport named in `sports` must have a store."""
    if sports:
        chosen, skipped = list(dict.fromkeys(sports)), []
    else:
        chosen = [s for s in SPORTS if (Path(data_dir) / _db_file(s)).is_file()]
        skipped = [s for s in SPORTS if s not in chosen]
        if not chosen:
            raise BackupError(f"No store in {data_dir} (looked for {', '.join(_db_file(s) for s in SPORTS)})")
        for sport in skipped:
            print(f"WARNING: no {sport} store at {Path(data_dir) / _db_file(sport)}; not backed up", file=sys.stderr)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(dest) / stamp
    if out_dir.exists():
        raise BackupError(f"Backup destination {out_dir} exists; choose a new one")
    sources = {}
    for sport in chosen:
        src = Path(data_dir) / _db_file(sport)
        if not src.is_file():
            raise BackupError(f"No {sport} store at {src}")
        sources[sport] = src
    out_dir.mkdir(parents=True)
    manifest = {"created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data_dir": str(data_dir), "stores": {}}
    for sport, src in sources.items():
        target = out_dir / src.name
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
            source.close()
        info = _describe(target)
        if info["integrity"] != "ok":
            raise BackupError(f"{sport} backup failed the integrity check: {info['integrity']}")
        manifest["stores"][sport] = {"file": target.name, "source": str(src), "bytes": target.stat().st_size,
                                     "sha256": sha256(target), **info}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return {"status": "ok", "dir": str(out_dir), "skipped": skipped, **manifest}


def mirror(backup_dir: Path, mirror_root: Path) -> dict:
    """Copies one backup folder to `mirror_root/<same name>` (a second disk, a synced cloud folder), never overwriting.
    Every store file is checked against the manifest before it is copied and again after, so a damaged backup is never
    mirrored and a damaged copy is reported. Other files in the folder (a journal, a ledger copy) are copied as they are."""
    src = Path(backup_dir)
    try:
        manifest = json.loads((src / "manifest.json").read_text())
        stores = manifest["stores"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError(f"Unreadable backup manifest in {src}: {type(exc).__name__}: {exc}") from None
    for m in stores.values():
        if sha256(src / m["file"]) != m["sha256"]:
            raise BackupError(f"{src / m['file']} does not match its manifest; not mirrored")
    if not Path(mirror_root).is_dir():
        raise BackupError(f"Mirror folder {mirror_root} does not exist (disk unplugged? cloud folder missing?); "
                          "it is not created here, so a mirror never silently lands on the same disk")
    out = Path(mirror_root) / src.name
    if os.path.lexists(out):
        raise BackupError(f"Mirror destination {out} exists; not overwritten")
    tmp = Path(mirror_root) / f".{src.name}.partial"
    import re
    import shutil
    # A `.<stamp>.partial` folder is only ever this job's own unfinished copy: one for this stamp or an older one is stale
    # (an interrupted mirror) and removed so the night can run. A newer stamp's, and every completed folder, stay untouched.
    for stale in Path(mirror_root).glob(".*.partial"):
        m = re.fullmatch(r"\.(\d{8}T\d{6}Z)\.partial", stale.name)
        if m and m.group(1) <= src.name and stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
    shutil.copytree(src, tmp)
    for m in stores.values():
        if sha256(tmp / m["file"]) != m["sha256"]:
            raise BackupError(f"The mirrored {m['file']} in {tmp} does not match the manifest; left there for inspection")
    tmp.rename(out)
    return {"status": "ok", "dir": str(out), "stores": sorted(stores)}


def restore(backup_dir: Path, data_dir: Path, *, sports: list) -> dict:
    """Puts the backed-up store of each sport back in `data_dir`, under the store's job lock, only if the manifest names
    that sport's own store file, the file matches the manifest and its identity is that sport (a data-only store, always
    claimed on its first write, must carry it; a served store from before identities may have none). The new file is
    written and synced beside the store first; only then is the replaced store (and its -wal/-shm) moved to
    `<name>.replaced-<UTC>` (a name no earlier safety copy uses), never deleted. Stop the scheduled jobs and the API
    first: a connection kept open across the swap writes to the replaced file.

    Steps 1-2 (checks, locks, staged copies) touch no store: a failure there leaves every store as it was. Step 3 renames
    files one at a time, so it can stop half-way:
    - An exception during step 3 (a failed rename, a safety-copy name in use, ...) is rolled back in this process: every
      rename already done, across all sports, is undone in reverse order (never overwriting a file), the folder is synced,
      the staged `<name>.restoring` copies are removed and the original exception is raised. If the rollback itself
      fails, nothing is deleted and a BackupError lists, per sport, the active path, its safety copies and its staged
      copy, and the renames still to be done by hand, in order.
    - A crash or power loss during step 3 cannot be rolled back automatically. Per sport the folder may then hold
      `<name>.replaced-<suffix>` (the original store), `<name>-wal.replaced-<suffix>` / `<name>-shm.replaced-<suffix>`
      (its WAL files, if it had them), `<name>.restoring` (the backup copy, not yet installed) and either no `<name>` or
      a `<name>` that is already the restored copy (its sha256 equals the manifest's; then there is no `.restoring`).
      With the jobs and the API stopped: to put the original back, move a restored `<name>` (if any) to
      `<name>.restoring`, then move `<name>.replaced-<suffix>`, `<name>-wal.replaced-<suffix>` and
      `<name>-shm.replaced-<suffix>` (the newest suffix) back to `<name>`, `<name>-wal` and `<name>-shm`; to finish the
      restore instead, move the remaining live `<name>-wal`/`<name>-shm` aside too and then `<name>.restoring` to
      `<name>`. The other sports of the same restore may each be in either state: check every one."""
    try:
        manifest = json.loads((Path(backup_dir) / "manifest.json").read_text())
        stores = manifest["stores"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError(f"Unreadable backup manifest in {backup_dir}: {type(exc).__name__}: {exc}") from None
    checked, locks = [], []
    try:
        # 1. Every sport is checked and locked before any store is touched (a failure in steps 1-2 changes no store).
        for sport in dict.fromkeys(sports):
            m = stores.get(sport)
            if m is None:
                raise BackupError(f"The backup has no {sport} store")
            if m.get("file") != _db_file(sport):
                raise BackupError(f"The manifest entry for {sport} names {m.get('file')!r}, not the {sport} store "
                                  f"({_db_file(sport)}); not restored")
            src = Path(backup_dir) / m["file"]
            if not src.is_file() or sha256(src) != m.get("sha256"):
                raise BackupError(f"{src} does not match the manifest (sha256); not restored")
            identity = _describe(src)["identity"]
            if identity != sport and (identity is not None or sport in sport_defs.DATA_ONLY):
                raise BackupError(f"{src} is not the {sport} store (store identity); not restored")
            lock = os.open(Path(data_dir) / f".{sport}-jobs.lock", os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(lock)
                raise BackupError(f"A job holds the {sport} store lock; stop the scheduled jobs first") from None
            locks.append(lock)
            checked.append((sport, m, src, Path(data_dir) / m["file"]))
        # 2. Each new file is written and synced beside its store and verified before the swap.
        written = []
        try:
            for sport, m, src, target in checked:
                tmp = Path(f"{target}.restoring")
                written.append(tmp)
                with open(src, "rb") as fin, open(tmp, "wb") as fout:
                    for chunk in iter(lambda: fin.read(1 << 20), b""):
                        fout.write(chunk)
                    fout.flush()
                    os.fsync(fout.fileno())
                if sha256(tmp) != m["sha256"]:
                    raise BackupError(f"The copy of {src} does not match the manifest; no store was touched")
        except BaseException:
            for tmp in written:
                tmp.unlink(missing_ok=True)
            raise
        # 3. Swap: the replaced store and its -wal/-shm are moved aside (a stale WAL must never be replayed onto the
        # restored file), never deleted; the directory entry is synced. Every rename is recorded so that an exception
        # undoes all of them, across all sports (a crash cannot be undone here: see the docstring).
        out, done = {}, []
        where = {sport: {"active": target, "safety": [], "staged": Path(f"{target}.restoring")}
                 for sport, m, src, target in checked}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        try:
            for sport, m, src, target in checked:
                parts = ("", "-wal", "-shm")
                # An earlier safety copy is never overwritten (rename replaces silently): a name no part uses yet.
                n, suffix = 0, stamp
                while any(os.path.lexists(f"{target}{part}.replaced-{suffix}") for part in parts):
                    n += 1
                    suffix = f"{stamp}-{n}"
                for part in parts:
                    p, aside = Path(f"{target}{part}"), Path(f"{target}{part}.replaced-{suffix}")
                    if p.exists():
                        if os.path.lexists(aside):
                            raise BackupError(f"{aside} already exists; the earlier safety copy is not overwritten")
                        p.rename(aside)
                        done.append((p, aside))
                        where[sport]["safety"].append(aside)
                where[sport]["staged"].rename(target)
                done.append((where[sport]["staged"], target))
                out[sport] = {"path": str(target), "sha256": m["sha256"], **_describe(target)}
        except BaseException as exc:
            _undo_swap(done, where, data_dir, exc)      # raises a BackupError if the rollback is incomplete
            raise
        _fsync_dir(data_dir)
        return out
    finally:
        for lock in locks:
            os.close(lock)


DATABASES_MANIFEST = "databases-manifest.json"


def describe_database(path: Path) -> dict:
    """integrity_check and the row count of every table of a database nobody is writing (a backup copy or a just-restored
    file). `immutable=1` opens it without creating -wal/-shm files."""
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {"integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
                "rows": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in sorted(tables)}}
    finally:
        conn.close()


def restore_databases(backup_dir: Path, data_dir: Path, paths: list) -> dict:
    """Puts back databases that sit beside the stores (`research/forward-candidates.sqlite`, `scorecard/<sport>.sqlite`,
    `card_thresholds.sqlite`, ...), copied by the nightly backup into `<backup dir>/databases/<path>` and listed with their
    sha256, bytes and row counts in `databases-manifest.json`. Each copy must match the manifest (sha256), pass
    integrity_check and have exactly the manifest's row count in every table before any file is touched; the new file is
    written and synced beside the old one, the replaced file (and its -wal/-shm/-journal) is moved to
    `<name>.replaced-<UTC>`, never deleted, and the result is read back. A failed swap is rolled back like a store's. Stop
    the jobs that write the database first (forward-record and forward-grade for the forward store): an open connection
    keeps writing to the replaced file."""
    try:
        listed = json.loads((Path(backup_dir) / DATABASES_MANIFEST).read_text())["databases"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError(f"Unreadable {DATABASES_MANIFEST} in {backup_dir} (a backup from before it existed holds no "
                          f"verifiable database copy): {type(exc).__name__}: {exc}") from None
    checked = []
    for rel in dict.fromkeys(paths):
        parts = Path(rel).parts
        if Path(rel).is_absolute() or not parts or ".." in parts:
            raise BackupError(f"{rel!r} is not a path inside the data folder; not restored")
        m = listed.get(rel)
        if m is None:
            raise BackupError(f"The backup has no {rel} (it holds: {', '.join(sorted(listed)) or 'none'})")
        src = Path(backup_dir) / "databases" / rel
        if not src.is_file() or sha256(src) != m.get("sha256"):
            raise BackupError(f"{src} does not match the manifest (sha256); not restored")
        info = describe_database(src)
        if info["integrity"] != "ok" or info["rows"] != m.get("rows"):
            raise BackupError(f"{src} fails its check (integrity {info['integrity']}, rows {info['rows']} != manifest "
                              f"rows {m.get('rows')}); not restored")
        checked.append((rel, m, src, Path(data_dir) / rel))
    written = []
    try:
        for rel, m, src, target in checked:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(f"{target}.restoring")
            written.append(tmp)
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                for chunk in iter(lambda: fin.read(1 << 20), b""):
                    fout.write(chunk)
                fout.flush()
                os.fsync(fout.fileno())
            if sha256(tmp) != m["sha256"]:
                raise BackupError(f"The copy of {src} does not match the manifest; nothing was touched")
    except BaseException:
        for tmp in written:
            tmp.unlink(missing_ok=True)
        raise
    out, done = {}, []
    where = {rel: {"active": target, "safety": [], "staged": Path(f"{target}.restoring")} for rel, m, src, target in checked}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        for rel, m, src, target in checked:
            parts = ("", "-wal", "-shm", "-journal")
            n, suffix = 0, stamp
            while any(os.path.lexists(f"{target}{part}.replaced-{suffix}") for part in parts):
                n += 1
                suffix = f"{stamp}-{n}"
            for part in parts:
                p, aside = Path(f"{target}{part}"), Path(f"{target}{part}.replaced-{suffix}")
                if p.exists():
                    p.rename(aside)
                    done.append((p, aside))
                    where[rel]["safety"].append(aside)
            where[rel]["staged"].rename(target)
            done.append((where[rel]["staged"], target))
            info = describe_database(target)
            if sha256(target) != m["sha256"] or info["integrity"] != "ok" or info["rows"] != m["rows"]:
                raise BackupError(f"The restored {target} does not match the manifest")
            out[rel] = {"path": str(target), "sha256": m["sha256"], **info}
    except BaseException as exc:
        _undo_swap(done, where, data_dir, exc)
        raise
    _fsync_dir(data_dir)
    return out


def _fsync_dir(data_dir: Path) -> None:
    folder = os.open(data_dir, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def _undo_swap(done: list, where: dict, data_dir: Path, exc: BaseException) -> None:
    """Rolls back the renames of a failed swap (restore step 3), newest first, never overwriting a file. Complete: the
    folder is synced and the staged copies are removed; the caller re-raises `exc`. Incomplete: nothing is deleted and a
    BackupError (chained to `exc`) lists every path and the renames left to do by hand, in order."""
    left, errors = [], []
    for original, moved in reversed(done):
        try:
            if os.path.lexists(original):
                raise FileExistsError(f"{original} exists; not overwritten")
            moved.rename(original)
        except OSError as err:
            errors.append(f"{moved} -> {original}: {type(err).__name__}: {err}")
            left.append((moved, original))
    if not errors:
        for w in where.values():
            try:
                w["staged"].unlink(missing_ok=True)
            except OSError:
                pass                                    # a leftover copy of the backup is harmless
        try:
            _fsync_dir(data_dir)
        except OSError as err:
            raise BackupError(f"The restore failed ({type(exc).__name__}: {exc}); every original store is back in place, "
                              f"but syncing {data_dir} failed: {type(err).__name__}: {err}") from exc
        return
    try:
        _fsync_dir(data_dir)
    except OSError as err:
        errors.append(f"directory sync: {type(err).__name__}: {err}")
    lines = [f"The restore failed ({type(exc).__name__}: {exc}) and the automatic rollback did not complete "
             f"({'; '.join(errors)}). Nothing was deleted. Stop every job and the API, then finish the rollback by hand."]
    for sport, w in where.items():
        lines.append(f"  {sport}: active store {w['active']}; safety copies "
                     f"{', '.join(map(str, w['safety'])) or 'none'}; staged backup copy {w['staged']}")
    lines.append("Renames still to do, in this order (never overwrite an existing file):")
    lines += [f"  {i}. move {moved} to {original}" for i, (moved, original) in enumerate(left, 1)]
    lines.append("Afterwards each staged .restoring file is only a copy of the backup and may be deleted.")
    raise BackupError("\n".join(lines)) from exc


# --- Research snapshot (five-sport-snapshot-v1) ---------------------------------------------------------------------------
# The frozen, read-only data the autoresearch evaluator scores against (docs/AUTORESEARCH.md "Snapshots"). Reads the live
# stores only (online backup, consistent while jobs write). Layout under `dest`:
#   <name>/<sport>.sqlite ...        online backup of each store, integrity_check + quick_check ok
#   <name>/IDENTITY.json             sha256 + bytes per file, per-store schema version, row counts, per-sport coverage
#   <name>/research/...              allowlisted derived files (default: the NHL goalie parse cache), by content hash
#   <name>/raw/                      only with include_raw (the raw response cache), identified by a manifest of file names
#   IDENTITY-<name>.json             copy of IDENTITY.json beside the snapshot (the identity the protocol pins)
# Then `chmod -R a-w`: autoresearch.snapshot_identity refuses a writable snapshot. `verify_snapshot` re-checks all of it.
SNAPSHOT_VERSION = "five-sport-snapshot-v1"
SNAPSHOT_DERIVED = ("research/candidates/goalies-v1.json.gz",)


def _coverage(conn, sport: str) -> dict:
    """Date range and status counts of the sport's games/matches, so a snapshot says what it can and cannot score."""
    from .learning_run import GAME_TABLES
    table, col = GAME_TABLES[sport]
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return {"table": table, "rows": 0}
    lo, hi, n = conn.execute(f"SELECT MIN({col}), MAX({col}), COUNT(*) FROM {table}").fetchone()
    status = dict(conn.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status").fetchall())
    final_hi = conn.execute(f"SELECT MAX({col}) FROM {table} WHERE status='final'").fetchone()[0]
    out = {"table": table, "time_column": col, "rows": n, "first": lo, "last": hi, "last_final": final_hi, "status": status}
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "season" in cols:
        out["seasons"] = [r[0] for r in conn.execute(f"SELECT DISTINCT season FROM {table} ORDER BY season")]
    return out


def _raw_manifest(raw: Path) -> str:
    return hashlib.sha256("\n".join(sorted(str(f.relative_to(raw)) for f in raw.rglob("*") if f.is_file())).encode()).hexdigest()


SOCCER_BACKFILL = {"from": "201008", "to": "202407",
                   "competitions": ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "uefa.europa"]}


def _soccer_backfill(stage: Path, cfg: dict, fetch=None) -> dict:
    """Free ESPN month scoreboards (`soccer history-months`: one request per competition and month) written ONLY into the
    snapshot's staged copy of the soccer store (and its raw cache under the stage); never the live store. Resumable: a month
    already in the stage's raw_responses is skipped, so a rerun asks only for what is missing; `max_requests` bounds one run.
    Cumulative request and failure counts are kept in `<stage>/.backfill-progress.json`."""
    from . import config as cfg_mod, db, soccer
    from .sources import espn
    fetch = fetch or espn.fetch_soccer_month
    prog_path = stage / ".backfill-progress.json"
    prog = json.loads(prog_path.read_text()) if prog_path.exists() else {"requested": 0, "runs": 0, "failed": []}
    old = os.environ.get("NBA_DATA_DIR")
    os.environ["NBA_DATA_DIR"] = str(stage)                      # raw cache and every write land in the stage
    conn = db.connect(stage / _db_file("soccer"))
    try:
        done = {r[0] for r in conn.execute("SELECT request_key FROM raw_responses WHERE source = ?", (espn.SOCCER_MONTH_SOURCE,))}
        todo = [(c, m) for c in cfg["competitions"] for m in soccer.months(cfg["from"], cfg["to"])
                if espn.soccer_month_request(c, m)[0] not in done]
        budget = cfg.get("max_requests")
        batch = todo if budget is None else todo[:budget]
        failed = []
        for c, m in batch:
            res = soccer.ingest_months(conn, [c], m, m, sleep=cfg.get("sleep", 1.0), fetch=fetch, log=lambda _x: None)
            prog["requested"] += len(res)
            failed += [f"{r['competition']}:{r['month']}" for r in res if "error" in r]
        prog.update(runs=prog["runs"] + 1, failed=failed)
        prog_path.write_text(json.dumps(prog))
        return {"pending": len(todo) - len(batch) + len(failed), "requested": prog["requested"], "failed": failed, "runs": prog["runs"]}
    finally:
        conn.close()
        if old is None:
            del os.environ["NBA_DATA_DIR"]
        else:
            os.environ["NBA_DATA_DIR"] = old


def snapshot(data_dir: Path, dest: Path, name: str, *, sports: Optional[list] = None, derived=SNAPSHOT_DERIVED,
             include_raw: bool = False, soccer_backfill: Optional[dict] = None, soccer_fetch=None) -> dict:
    """Builds `dest/<name>` as described above; refuses an existing name, a missing store or a failed integrity check.
    Built in `dest/<name>.partial` and renamed when complete. `soccer_backfill` (e.g. SOCCER_BACKFILL, optionally with
    `max_requests` and `sleep`) adds the free ESPN history to the SNAPSHOT copy of the soccer store (the live ESPN rows start in
    2024, all holdout or prospective). If it does not finish within `max_requests` the stage is kept and
    {"status": "partial", ...} is returned; calling again with the same arguments resumes (stores already copied are not
    copied again, months already fetched are not asked for again). The request count goes into IDENTITY.json."""
    import shutil
    data_dir, dest = Path(data_dir), Path(dest)
    out_dir, stage = dest / name, dest / f"{name}.partial"
    if out_dir.exists() or (dest / f"IDENTITY-{name}.json").exists():
        raise BackupError(f"snapshot {name} already exists in {dest}; choose a new name")
    chosen = list(sports or SPORTS)
    if soccer_backfill and "soccer" not in chosen:
        raise BackupError("soccer_backfill needs soccer in the snapshot's sports")
    missing = [s for s in chosen if not (data_dir / _db_file(s)).is_file()]
    if missing:
        raise BackupError(f"no store for {', '.join(missing)} in {data_dir}")
    marker = stage / ".stores-copied"
    if stage.exists() and not marker.exists():
        shutil.rmtree(stage)                                      # an interrupted copy is not trusted; start again
    if not stage.exists():
        stage.mkdir(parents=True)
        for sport in chosen:
            src, target = data_dir / _db_file(sport), stage / _db_file(sport)
            source, copy = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(target)
            try:
                source.backup(copy)
            finally:
                copy.close()
                source.close()
        marker.write_text("")
    backfill = None
    if soccer_backfill:
        backfill = _soccer_backfill(stage, soccer_backfill, soccer_fetch)
        if backfill["pending"]:
            return {"status": "partial", "stage": str(stage), **backfill}
    ident = {"snapshot_version": SNAPSHOT_VERSION, "name": name, "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "source": f"{data_dir} (online backup)", "files": {}, "stores": {}}
    if backfill:
        ident["soccer_backfill"] = {**{k: soccer_backfill[k] for k in ("from", "to", "competitions")}, "requested": backfill["requested"],
                                    "runs": backfill["runs"], "failed": backfill["failed"],
                                    "source": "ESPN soccer scoreboard, one request per competition and month; written to the snapshot copy only"}
    for sport in chosen:
        target = stage / _db_file(sport)
        conn = sqlite3.connect(f"file:{target}?mode=ro&immutable=1", uri=True)
        try:
            integrity, quick = conn.execute("PRAGMA integrity_check").fetchone()[0], conn.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok" or quick != "ok":
                raise BackupError(f"{sport} snapshot failed the integrity check: {integrity} / {quick}")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ident["stores"][sport] = {
                "integrity_check": integrity, "quick_check": quick, "coverage": _coverage(conn, sport),
                "schema_version": conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] if "schema_migrations" in tables else None,
                "rows": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNTED if t in tables}}
        finally:
            conn.close()
        ident["files"][target.name] = {"sha256": sha256(target), "bytes": target.stat().st_size}
        c = ident["stores"][sport]["coverage"]
        ident.setdefault("sports", {})[sport] = {k: c.get(k) for k in ("first", "last", "last_final", "rows")}   # what autoresearch.snapshot_cutoff reads
    for rel in derived:
        src = data_dir / rel
        if src.is_file():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, stage / rel)
            ident["files"][rel] = {"sha256": sha256(stage / rel), "bytes": (stage / rel).stat().st_size}
        else:
            ident.setdefault("derived_absent", []).append(rel)
    if include_raw and (data_dir / "raw").is_dir():
        shutil.copytree(data_dir / "raw", stage / "raw", dirs_exist_ok=True)
    if (stage / "raw").is_dir():                                  # live raw copy and/or the backfill's raw cache
        ident["raw_manifest"] = _raw_manifest(stage / "raw")
        ident["raw_files"] = sum(1 for f in (stage / "raw").rglob("*") if f.is_file())
    for leftover in (marker, stage / ".backfill-progress.json"):
        if leftover.exists():
            leftover.unlink()
    text = json.dumps(ident, indent=1, sort_keys=True)
    (stage / "IDENTITY.json").write_text(text)
    (dest / f"IDENTITY-{name}.json").write_text(text)
    stage.rename(out_dir)
    for p in sorted(out_dir.rglob("*"), reverse=True):
        os.chmod(p, 0o444 if p.is_file() else 0o555)
    os.chmod(out_dir, 0o555)
    os.chmod(dest / f"IDENTITY-{name}.json", 0o444)
    return ident


def verify_snapshot(snap: Path) -> dict:
    """Re-hashes every file named by IDENTITY.json, re-runs integrity_check, and checks nothing is writable or extra.
    Returns {"ok": bool, "problems": [...]}."""
    snap = Path(snap)
    problems = []
    ident = json.loads((snap / "IDENTITY.json").read_text())
    for rel, m in ident["files"].items():
        f = snap / rel
        if not f.is_file():
            problems.append(f"missing {rel}")
        elif sha256(f) != m["sha256"]:
            problems.append(f"sha256 differs: {rel}")
    for sport in ident["stores"]:
        f = snap / _db_file(sport)
        try:
            if f.is_file() and _describe(f)["integrity"] != "ok":
                problems.append(f"integrity_check failed: {sport}")
        except sqlite3.DatabaseError as exc:
            problems.append(f"integrity_check failed: {sport} ({exc})")
    if (snap / "raw").is_dir() and _raw_manifest(snap / "raw") != ident.get("raw_manifest"):
        problems.append("raw manifest differs")
    problems += [f"writable: {p}" for p in [snap, *snap.rglob("*")] if os.access(p, os.W_OK)]
    named = set(ident["files"]) | {"IDENTITY.json"}
    problems += [f"not in IDENTITY: {p.relative_to(snap)}" for p in snap.rglob("*")
                 if p.is_file() and str(p.relative_to(snap)) not in named and "raw" not in p.relative_to(snap).parts[:1]]
    return {"ok": not problems, "problems": problems, "name": ident["name"], "snapshot_version": ident["snapshot_version"]}


def _db_file(sport: str) -> str:
    return sport_defs.store(sport).db_file


def main(argv: Optional[list] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, help="Directory for a new backup (a timestamped folder is created inside)")
    ap.add_argument("--restore", type=Path, help="A backup folder (with manifest.json) to restore from")
    ap.add_argument("--sport", choices=SPORTS, action="append", help="Default for a backup: every store present; a restore needs --sport or --database")
    ap.add_argument("--database", action="append", metavar="PATH", help="Restore: a database beside the stores, relative to the data folder (e.g. research/forward-candidates.sqlite)")
    ap.add_argument("--snapshot", type=Path, metavar="DEST", help="Build a read-only research snapshot DEST/<--name> (five-sport-snapshot-v1)")
    ap.add_argument("--name", help="Snapshot name, e.g. data-snapshot-2026-11-22-5sport")
    ap.add_argument("--raw", action="store_true", help="Snapshot: also copy the raw response cache")
    ap.add_argument("--soccer-backfill", action="store_true", help="Snapshot: add the free ESPN soccer history 2010-08..2024-07 (7 competitions) to the snapshot's soccer store only")
    ap.add_argument("--max-requests", type=int, help="Snapshot backfill: stop after this many ESPN requests in this run (rerun the same command to resume)")
    ap.add_argument("--verify", type=Path, metavar="SNAPSHOT_DIR", help="Re-check a snapshot against its IDENTITY.json")
    args = ap.parse_args(argv)
    if args.verify:
        res = verify_snapshot(args.verify)
        print(json.dumps(res, indent=2, sort_keys=True))
        sys.exit(0 if res["ok"] else 1)
    if args.snapshot:
        if not args.name:
            ap.error("--snapshot needs --name")
        bf = {**SOCCER_BACKFILL, "max_requests": args.max_requests} if args.soccer_backfill else None
        res = snapshot(config.data_dir(), args.snapshot, args.name, sports=args.sport or list(SPORTS), include_raw=args.raw, soccer_backfill=bf)
        print(json.dumps(res, indent=2, sort_keys=True))
        sys.exit(2 if res.get("status") == "partial" else 0)
    if bool(args.dest) == bool(args.restore):
        ap.error("give exactly one of --dest or --restore")
    if args.restore and not (args.sport or args.database):
        ap.error("--restore needs --sport or --database")
    if args.database and not args.restore:
        ap.error("--database is for --restore")
    if args.database:
        result = {"stores": restore(args.restore, config.data_dir(), sports=args.sport) if args.sport else {},
                  "databases": restore_databases(args.restore, config.data_dir(), args.database)}
    else:
        result = (run(config.data_dir(), args.dest, sports=args.sport) if args.dest else
                  restore(args.restore, config.data_dir(), sports=args.sport))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
