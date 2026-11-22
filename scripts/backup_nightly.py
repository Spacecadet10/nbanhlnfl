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
fails its manifest check never displaces a verified one). Without `--mirror` the backup still runs but exits 2: it is not off this machine.
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
# the adaptive job, content-addressed, so a run's *.pkl and its .sha256 are immutable) are copied; lock files and
# the adaptive snapshots (-wal/-shm of a live SQLite copy) are not. SQLite files below are copied with the backup API.
EXTRA_PATTERNS = (("data", "research/adaptive/**/*.pkl"), ("data", "research/adaptive/**/*.pkl.sha256"),("repo", "docs/telegram_record.json"), ("repo", "docs/preregistration/**/*"),
                  ("data", "research/telegram_history.json"), ("data", "research/line-state-*.pkl"),
                  ("data", "research/candidates/**/*"), ("data", "research/learning-runs.jsonl"),
                  ("data", "reports/weekly_report_review.json"), ("launch_agents", "com.nba-predictor.*.plist"))


# SQLite files beside the stores (relative to the data folder): the per-sport scorecards and the card-threshold log.
EXTRA_SQLITE = ("scorecard/*.sqlite", "card_thresholds.sqlite")


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


def nightly(data_dir: Path, root: Path, *, mirror, stamp=None, ledger=None, repo_root=None, launch_agents=None,
            prune_apply: bool = False) -> dict:
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
    extras = _extras(folder, data_dir, ledger, repo_root, launch_agents) if (ledger or repo_root or launch_agents) else {}
    out = {"stamp": stamp, "extras": extras, "backup": str(folder), "journal_new_rows": {s: sum(v.values()) for s, v in exported["sports"].items()},
           "journal_verify_ok": checked["ok"], "mirror": None}
    if not checked["ok"]:
        out["journal_problems"] = {s: {t: {k: r[k][:20] for k in ("missing", "journal_only", "tampered", "differs") if r[k]}
                                       for t, r in tables.items() if any(r[k] for k in ("missing", "journal_only", "tampered", "differs"))}
                                   for s, tables in checked["sports"].items()}
    out["prune"] = prune(Path(root) / "stores", apply=prune_apply)
    if mirror is None:
        out["status"] = "no_mirror"
        return out
    out["mirror"] = backup.mirror(folder, Path(mirror))["dir"]
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
    sys.exit(1 if not out["journal_verify_ok"] else 2 if out["status"] == "no_mirror" else 0)


if __name__ == "__main__":
    main()
