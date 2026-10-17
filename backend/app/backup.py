"""Store backup and restore around a release migration (README "Release"). A backup is SQLite's online backup of each sport's
store (consistent while other readers run), checked with `PRAGMA integrity_check`, with a manifest: sha256, schema
version, store identity and row counts. A restore copies a backup back only if its file matches the manifest, while holding
the store's job lock (no scheduled job can run against it), and moves the replaced files aside, never deleting them.
The raw response cache (`raw/`) is append-only and untouched by migrations; it is not copied.

    NBA_DATA_DIR=<store dir> python -m app.backup --dest <dir>            # backup (reads the stores only)
    NBA_DATA_DIR=<store dir> python -m app.backup --restore <backup dir> --sport nhl
"""
import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from . import sports as sport_defs

SPORTS = ("nba", "nhl")
COUNTED = ("games", "predictions", "market_forecasts", "game_forecasts", "result_revisions", "research_cohorts",
           "agreement_observations", "adaptive_forecasts", "early_forecasts", "job_runs", "teams")


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
    """Backs up each sport's store under `dest/<stamp>/`. Reads the source stores only; refuses an existing destination."""
    chosen = sports or list(SPORTS)
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
    return {"status": "ok", "dir": str(out_dir), **manifest}


def restore(backup_dir: Path, data_dir: Path, *, sports: list) -> dict:
    """Puts the backed-up store of each sport back in `data_dir`, under the store's job lock, only if the manifest names
    that sport's own store file, the file matches the manifest and its identity is that sport. The new file is written and
    synced beside the store first; only then is the replaced store (and its -wal/-shm) moved to `<name>.replaced-<UTC>`,
    never deleted. Stop the scheduled jobs and the API first: a connection kept open across the swap writes to the
    replaced file."""
    try:
        manifest = json.loads((Path(backup_dir) / "manifest.json").read_text())
        stores = manifest["stores"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError(f"Unreadable backup manifest in {backup_dir}: {type(exc).__name__}: {exc}") from None
    checked, locks = [], []
    try:
        # 1. Every sport is checked and locked before any store is touched (a two-sport restore never half-completes).
        for sport in sports:
            m = stores.get(sport)
            if m is None:
                raise BackupError(f"The backup has no {sport} store")
            if m.get("file") != _db_file(sport):
                raise BackupError(f"The manifest entry for {sport} names {m.get('file')!r}, not the {sport} store "
                                  f"({_db_file(sport)}); not restored")
            src = Path(backup_dir) / m["file"]
            if not src.is_file() or sha256(src) != m.get("sha256"):
                raise BackupError(f"{src} does not match the manifest (sha256); not restored")
            if _describe(src)["identity"] not in (sport, None):
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
        # restored file), never deleted; the directory entry is synced.
        out = {}
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for sport, m, src, target in checked:
            for part in ("", "-wal", "-shm"):
                p = Path(f"{target}{part}")
                if p.exists():
                    p.rename(Path(f"{target}{part}.replaced-{suffix}"))
            Path(f"{target}.restoring").rename(target)
            out[sport] = {"path": str(target), "sha256": m["sha256"], **_describe(target)}
        folder = os.open(data_dir, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
        return out
    finally:
        for lock in locks:
            os.close(lock)


def _db_file(sport: str) -> str:
    return sport_defs.get(sport).db_file


def main(argv: Optional[list] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, help="Directory for a new backup (a timestamped folder is created inside)")
    ap.add_argument("--restore", type=Path, help="A backup folder (with manifest.json) to restore from")
    ap.add_argument("--sport", choices=SPORTS, action="append", help="Default: both for a backup; required for a restore")
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
