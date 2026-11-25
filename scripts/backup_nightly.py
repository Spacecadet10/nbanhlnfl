#!/usr/bin/env python3
"""Nightly backup of every sport store plus the forecast journal, mirrored to a second place (owner request, Nov 15).

1. Appends new forecast and odds-quote rows to the journal (app.journal; reads the stores only) and verifies it.
2. Makes a verified online backup of every store (app.backup) under `<root>/stores/<UTC stamp>/`.
3. Copies the Telegram ledger (`ledger.sqlite`) and the non-secret rule, history, model and config files (`files.tar.gz`).
4. Puts a compressed copy of the whole journal in that backup folder (`journal.tar.gz`).
5. Mirrors the folder to `--mirror` (another disk or a synced cloud folder), checked against the manifest.

Backups are never overwritten. Nothing is deleted unless `--prune` is given: every run prints the retention plan
(`prune`, mode report-only: keep every backup of the last 14 days, the newest per ISO week for 8 weeks and the newest per
month for 12 months; the newest backup, and a backup that is the only copy in its bucket, are always kept, a backup that
fails its manifest check never displaces a verified one). Without `--mirror` the backup still runs but exits 2: it is not off this machine. A mirror that cannot be made (drive
unplugged, copy failed, a copied file differing from the local one) keeps the local backup and exits 3. Both cases post a
macOS notification and a WARNING line in the job log (owner, Nov 22).
A journal that disagrees with a store (a lost or changed row) exits 1 after the backup, so the log shows it.

    PYTHONPATH=backend NBA_DATA_DIR=<data> backend/.venv/bin/python scripts/backup_nightly.py --root <dir> [--mirror <dir>]
"""
import argparse
import json
import os
import sqlite3
import sys
import re
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import backup, config, journal  # noqa: E402


# Non-secret files kept beside the stores (patterns are relative to the repo root, the data folder, the LaunchAgents folder).
# An allowlist, so a credential file (.env*) is never picked up. The adaptive model pickles (hundreds of MB, retrained by
# the adaptive job, content-addressed, so a run's *.pkl and its .sha256 are immutable) are NOT in files.tar.gz: they go to
# the shared pool `<root>/pool/adaptive/` once (see _pool); lock files and the adaptive snapshots are not copied.
# SQLite files below are copied with the backup API.
EXTRA_PATTERNS = (("repo", "docs/telegram_record.json"), ("repo", "docs/preregistration/**/*"),
                  ("data", "research/telegram_history.json"), ("data", "research/line-state-*.pkl"),
                  ("data", "research/candidates/**/*"), ("data", "research/learning-runs.jsonl"),
                  ("data", "reports/weekly_report_review.json"), ("launch_agents", "com.nba-predictor.*.plist"))


# SQLite files beside the stores (relative to the data folder): the per-sport scorecards, the card-threshold log and the saved pick colours.
EXTRA_SQLITE = ("scorecard/*.sqlite", "card_thresholds.sqlite", "pick_snapshots.sqlite")


def _copy_sqlite(src: Path, target: Path) -> dict:
    """Consistent copy (SQLite backup API) of a database that jobs may be writing, integrity checked, row counts per table."""
    target.parent.mkdir(parents=True, exist_ok=True)
    s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(target)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    os.chmod(target, 0o600)
    c = sqlite3.connect(f"file:{target}?mode=ro&immutable=1", uri=True)
    try:
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise backup.BackupError(f"{src.name} backup failed the integrity check: {integrity}")
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {"integrity": integrity, "sha256": backup.sha256(target), "bytes": target.stat().st_size,
                "rows": {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in sorted(tables)}}
    finally:
        c.close()


def _extras(folder: Path, data_dir: Path, ledger, repo_root, launch_agents) -> dict:
    """Ledger copy (SQLite backup API, integrity checked) and one tarball of the allowlisted files, into the snapshot folder."""
    out = {}
    if ledger is not None and Path(ledger).is_file():
        target = folder / "ledger.sqlite"
        src, dst = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True), sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.chmod(target, 0o600)
        check = sqlite3.connect(f"file:{target}?mode=ro&immutable=1", uri=True)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise backup.BackupError(f"ledger backup failed the integrity check: {integrity}")
            out["ledger"] = {"file": target.name, "sha256": backup.sha256(target), "integrity": integrity,
                             "deliveries": check.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]}
        finally:
            check.close()
    databases = {}
    for pattern in EXTRA_SQLITE:
        for path in sorted(Path(data_dir).glob(pattern)):
            if path.is_file() and not path.is_symlink() and ".env" not in path.name:
                rel = path.relative_to(data_dir)
                databases[str(rel)] = _copy_sqlite(path, folder / "databases" / rel)
    if databases:
        out["databases"] = databases
    roots = {"repo": repo_root, "data": data_dir, "launch_agents": launch_agents}
    tmp = folder / "files.tar.gz.partial"
    count = 0
    with tarfile.open(tmp, "w:gz") as t:
        for name, pattern in EXTRA_PATTERNS:
            base = roots[name]
            if base is None or not Path(base).is_dir():
                continue
            for path in sorted(Path(base).glob(pattern)):
                if path.is_file() and not path.is_symlink() and ".env" not in path.name:
                    t.add(path, arcname=f"{name}/{path.relative_to(base)}")
                    count += 1
    os.rename(tmp, folder / "files.tar.gz")
    out["files"] = {"file": "files.tar.gz", "count": count, "sha256": backup.sha256(folder / "files.tar.gz")}
    return out


STAMP = re.compile(r"^\d{8}T\d{6}Z$")
RECENT_DAYS, WEEKS, MONTHS = 14, 8, 12


def _verifies(folder: Path) -> bool:
    """The manifest names each store file and its sha256 and every file matches: a backup that can be restored."""
    try:
        stores = json.loads((folder / "manifest.json").read_text())["stores"]
        return all(backup.sha256(folder / m["file"]) == m["sha256"] for m in stores.values())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prune(stores_dir: Path, *, now=None, apply: bool = False) -> dict:
    """Retention plan for `<root>/stores/<UTC stamp>/`: keep every backup newer than 14 days, the newest of each ISO week for
    8 weeks and the newest of each calendar month for 12 months. A bucket's keeper is its newest backup that verifies
    (manifest sha256); a bucket with none that verifies keeps its newest (the only copy is never deleted). The newest backup
    and the newest verified backup are always kept. Folders whose names are not a backup stamp are ignored. Report-only
    unless `apply`: then exactly `would_delete` is removed (whole folders, nothing else). Never touches the journal."""
    now = now or datetime.now(timezone.utc)
    found = {}
    for p in sorted(Path(stores_dir).glob("*")) if Path(stores_dir).is_dir() else []:
        if p.is_dir() and not p.is_symlink() and STAMP.match(p.name):
            found[p.name] = datetime.strptime(p.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    order = sorted(found, reverse=True)                                  # newest first
    cache = {}
    ok = lambda n: cache.setdefault(n, _verifies(Path(stores_dir) / n))   # noqa: E731
    keep, buckets = set(), {}
    for n in order:
        t = found[n]
        age = (now - t).total_seconds() / 86400
        if age < RECENT_DAYS:
            keep.add(n)
        iso = t.isocalendar()
        if age <= 7 * WEEKS:
            buckets.setdefault(("week", iso[0], iso[1]), []).append(n)
        if (now.year * 12 + now.month) - (t.year * 12 + t.month) < MONTHS:
            buckets.setdefault(("month", t.year, t.month), []).append(n)
    for members in buckets.values():                                      # members are newest first
        keep.add(next((n for n in members if n in keep and ok(n)), None) or next((n for n in members if ok(n)), members[0]))
    if order:
        keep.add(order[0])
        newest_ok = next((n for n in order if ok(n)), None)
        if newest_ok:
            keep.add(newest_ok)
    would_delete = [n for n in order if n not in keep]
    deleted = []
    if apply:
        for n in would_delete:
            shutil.rmtree(Path(stores_dir) / n)
            deleted.append(n)
    return {"mode": "apply" if apply else "report-only", "keep": sorted(keep), "would_delete": would_delete,
            "deleted": deleted, "unverified_kept": sorted(n for n in keep if cache.get(n) is False)}


POOL_GLOBS = ("**/*.pkl", "**/*.pkl.sha256")


def _file_sha(path: Path) -> str:
    return backup.sha256(path)


def _pool(data_dir: Path, root: Path, folder: Path) -> dict:
    """Copies each adaptive pickle (and its .sha256 sidecar) to `<root>/pool/adaptive/<relative path>` only if absent, checked
    against the sidecar (pickles) and a fresh hash of the copy. A file already in the pool is never overwritten: one whose
    content differs from the source, or a pickle that does not match its own sidecar, is listed in `conflicts`. Writes
    `pool-manifest.json` in the night's folder: every pool path this night references with its sha256 (a restore's exact set).
    Pool files are never deleted here."""
    base, pool = Path(data_dir) / "research" / "adaptive", Path(root) / "pool" / "adaptive"
    copied = present = nbytes = 0
    conflicts, files = [], {}
    sources = sorted({p for g in POOL_GLOBS for p in base.glob(g)}) if base.is_dir() else []
    for src in sources:
        if not src.is_file() or src.is_symlink() or ".env" in src.name:
            continue
        rel, key = src.relative_to(base), f"adaptive/{src.relative_to(base)}"
        digest = _file_sha(src)
        side = src.with_name(src.name + ".sha256")
        if src.suffix == ".pkl" and side.is_file() and (side.read_text().split() or [""])[0] != digest:
            conflicts.append(key)                       # the pickle changed after its sidecar was written: not trusted
            continue
        dest = pool / rel
        if dest.exists():
            if dest.stat().st_size != src.stat().st_size or _file_sha(dest) != digest:
                conflicts.append(key)
                continue
            present += 1
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name("." + dest.name + ".partial")
            shutil.copyfile(src, tmp)
            if _file_sha(tmp) != digest:
                tmp.unlink()
                raise backup.BackupError(f"The pool copy of {key} does not match its source hash; not kept")
            os.chmod(tmp, 0o600)
            tmp.rename(dest)
            copied += 1
            nbytes += dest.stat().st_size
        files[key] = digest
    (folder / "pool-manifest.json").write_text(json.dumps({"pool": "pool", "files": files}, indent=2, sort_keys=True))
    return {"copied": copied, "present": present, "bytes_copied": nbytes, "referenced": len(files), "conflicts": conflicts}


def _mirror_pool(root: Path, mirror: Path) -> dict:
    """Mirrors `<root>/pool` to `<mirror>/pool` incrementally: only files missing there are copied (temp name, hash-checked,
    renamed). A file already there with another size or hash is an error, never overwritten."""
    src, dst = Path(root) / "pool", Path(mirror) / "pool"
    copied = nbytes = 0
    for p in sorted(src.rglob("*")) if src.is_dir() else []:
        if not p.is_file() or p.name.endswith(".partial"):
            continue
        target = dst / p.relative_to(src)
        if target.exists():
            if target.stat().st_size != p.stat().st_size:
                raise backup.BackupError(f"{target} differs from the pool file {p}; not overwritten")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("." + target.name + ".partial")
        shutil.copyfile(p, tmp)
        if _file_sha(tmp) != _file_sha(p):
            tmp.unlink()
            raise backup.BackupError(f"The mirrored pool file {target} does not match the local pool")
        tmp.rename(target)
        copied += 1
        nbytes += target.stat().st_size
    return {"copied": copied, "bytes_copied": nbytes}


def collect_pool(backup_folder: Path, pool_root: Path, dest: Path) -> int:
    """Restore helper: copies every pool file the night's `pool-manifest.json` references from `pool_root` (the local
    `<root>/pool` or the mirror's `<mirror>/pool`) to `dest/<path>`, each verified against the manifest sha256."""
    files = json.loads((Path(backup_folder) / "pool-manifest.json").read_text())["files"]
    for key, digest in files.items():
        src = Path(pool_root) / key
        if not src.is_file() or _file_sha(src) != digest:
            raise backup.BackupError(f"Pool file {key} is missing or does not match the manifest ({src})")
    for key in files:
        target = Path(dest) / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(pool_root) / key, target)
    return len(files)


OFFSITE_WARNING = "WARNING: tonight's backup was NOT copied off this Mac"


def notify_owner(message: str) -> None:
    """The warning the owner sees: a macOS notification (best effort) and a WARNING line in the job log (stderr)."""
    print(message, file=sys.stderr, flush=True)
    try:
        import subprocess
        text = message.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "NBA predictor backup"'],
                       timeout=20, check=False, capture_output=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass


def _verify_copy(src: Path, dst: Path) -> int:
    """Every file of the backup folder is present in the copy with the same SHA-256; returns the file count."""
    names = sorted(p.relative_to(src) for p in src.rglob("*") if p.is_file())
    for n in names:
        if not (dst / n).is_file() or backup.sha256(src / n) != backup.sha256(dst / n):
            raise backup.BackupError(f"The off-machine copy of {n} does not match the local backup ({dst / n})")
    return len(names)


def nightly(data_dir: Path, root: Path, *, mirror, stamp=None, ledger=None, repo_root=None, launch_agents=None,
            prune_apply: bool = False, notify=notify_owner) -> dict:
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jdir = Path(root) / "journal"
    exported = journal.export(data_dir, jdir)
    checked = journal.verify(data_dir, jdir)
    made = backup.run(data_dir, Path(root) / "stores", stamp=stamp)
    folder = Path(made["dir"])
    tmp = folder / "journal.tar.gz.partial"
    with tarfile.open(tmp, "w:gz") as t:
        t.add(jdir, arcname=".", filter=lambda info: None if info.name.endswith(".journal.lock") else info)
    os.rename(tmp, folder / "journal.tar.gz")
    try:
        pool = _pool(data_dir, Path(root), folder)
    except (backup.BackupError, OSError) as exc:          # the stores are backed up already; say so, never crash silently
        pool = {"error": f"{type(exc).__name__}: {exc}", "conflicts": []}
        notify(f"WARNING: the adaptive model pickles were NOT backed up tonight ({pool['error']}). Stores: {folder}")
    extras = _extras(folder, data_dir, ledger, repo_root, launch_agents) if (ledger or repo_root or launch_agents) else {}
    out = {"stamp": stamp, "extras": extras, "backup": str(folder), "journal_new_rows": {s: sum(v.values()) for s, v in exported["sports"].items()},
           "journal_verify_ok": checked["ok"], "mirror": None, "pool": pool}
    if pool["conflicts"]:
        notify(f"WARNING: adaptive pickle(s) changed under the same name, not overwritten in the backup pool: "
               f"{', '.join(pool['conflicts'][:5])}")
    if not checked["ok"]:
        out["journal_problems"] = {s: {t: {k: r[k][:20] for k in ("missing", "journal_only", "tampered", "differs") if r[k]}
                                       for t, r in tables.items() if any(r[k] for k in ("missing", "journal_only", "tampered", "differs"))}
                                   for s, tables in checked["sports"].items()}
    out["prune"] = prune(Path(root) / "stores", apply=prune_apply)
    if mirror is None:
        out["status"] = "no_mirror"
        out["warning"] = f"{OFFSITE_WARNING}: no --mirror drive is configured. Local copy: {folder}"
        notify(out["warning"])
        return out
    try:
        copied = Path(backup.mirror(folder, Path(mirror))["dir"])
        out["mirror_verified_files"] = _verify_copy(folder, copied)
        out["pool_mirrored"] = _mirror_pool(Path(root), Path(mirror))
    except (backup.BackupError, OSError) as exc:
        out["status"], out["mirror_error"] = "mirror_failed", f"{type(exc).__name__}: {exc}"
        out["warning"] = f"{OFFSITE_WARNING} ({mirror}): {exc}. Local copy: {folder}"
        notify(out["warning"])
        return out
    out["mirror"] = str(copied)
    out["status"] = "ok" if checked["ok"] else "journal_mismatch"
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="Local backup folder (outside the data folder)")
    ap.add_argument("--mirror", type=Path, help="Second place for each backup: another disk or a synced cloud folder")
    ap.add_argument("--ledger", type=Path, help="Telegram delivery ledger (default: .telegram-delivery.sqlite beside the data folder)")
    ap.add_argument("--prune", action="store_true", help="Delete the backups the retention plan lists (default: only report them)")
    args = ap.parse_args(argv)
    data_dir = config.data_dir()
    out = nightly(data_dir, args.root, mirror=args.mirror, ledger=args.ledger or data_dir.parent / ".telegram-delivery.sqlite",
                  repo_root=Path(__file__).resolve().parents[1], launch_agents=Path.home() / "Library" / "LaunchAgents", prune_apply=args.prune)
    print(json.dumps({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **out}, sort_keys=True))
    sys.exit(1 if not out["journal_verify_ok"] or out["pool"].get("error") else 3 if out["status"] == "mirror_failed" else 2 if out["status"] == "no_mirror" else 0)


if __name__ == "__main__":
    main()
