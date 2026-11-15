"""Store backup and restore around a release migration (README "Release"). A backup is SQLite's online backup of each sport's
store (consistent while other readers run), checked with `PRAGMA integrity_check`, with a manifest: sha256, schema
version, store identity and row counts. A restore copies a backup back only if its file matches the manifest, while holding
the store's job lock (no scheduled job can run against it), and moves the replaced files aside, never deleting them.
The raw response cache (`raw/`) is append-only and untouched by migrations; it is not copied.

    NBA_DATA_DIR=<store dir> python -m app.backup --dest <dir>            # backup of every store present (reads them only)
    NBA_DATA_DIR=<store dir> python -m app.backup --restore <backup dir> --sport nhl
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


def _db_file(sport: str) -> str:
    return sport_defs.store(sport).db_file


def main(argv: Optional[list] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, help="Directory for a new backup (a timestamped folder is created inside)")
    ap.add_argument("--restore", type=Path, help="A backup folder (with manifest.json) to restore from")
    ap.add_argument("--sport", choices=SPORTS, action="append", help="Default for a backup: every store present; required for a restore")
    args = ap.parse_args(argv)
    if bool(args.dest) == bool(args.restore):
        ap.error("give exactly one of --dest or --restore")
    if args.restore and not args.sport:
        ap.error("--restore needs --sport")
    result = (run(config.data_dir(), args.dest, sports=args.sport) if args.dest else
              restore(args.restore, config.data_dir(), sports=args.sport))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
