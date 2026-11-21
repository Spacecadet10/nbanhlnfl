#!/usr/bin/env python3
"""Nightly backup of every sport store plus the forecast journal, mirrored to a second place (owner request, Nov 15).

1. Appends new forecast and odds-quote rows to the journal (app.journal; reads the stores only) and verifies it.
2. Makes a verified online backup of every store (app.backup) under `<root>/stores/<UTC stamp>/`.
3. Copies the Telegram ledger (`ledger.sqlite`) and the non-secret rule, history, model and config files (`files.tar.gz`).
4. Puts a compressed copy of the whole journal in that backup folder (`journal.tar.gz`).
5. Mirrors the folder to `--mirror` (another disk or a synced cloud folder), checked against the manifest.

Nothing is ever deleted or overwritten. Without `--mirror` the backup still runs but exits 2: it is not off this machine.
A journal that disagrees with a store (a lost or changed row) exits 1 after the backup, so the log shows it.

    PYTHONPATH=backend NBA_DATA_DIR=<data> backend/.venv/bin/python scripts/backup_nightly.py --root <dir> [--mirror <dir>]
"""
import argparse
import json
import os
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import backup, config, journal  # noqa: E402


# Non-secret files kept beside the stores (patterns are relative to the repo root, the data folder, the LaunchAgents folder).
# An allowlist, so a credential file (.env*) is never picked up. The adaptive model pickles (hundreds of MB, retrained by
# the adaptive job) are not copied nightly.
EXTRA_PATTERNS = (("repo", "docs/telegram_record.json"), ("repo", "docs/preregistration/**/*"),
                  ("data", "research/telegram_history.json"), ("data", "research/line-state-*.pkl"),
                  ("data", "research/candidates/**/*"), ("data", "research/learning-runs.jsonl"),
                  ("data", "reports/weekly_report_review.json"), ("launch_agents", "com.nba-predictor.*.plist"))


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


def nightly(data_dir: Path, root: Path, *, mirror, stamp=None, ledger=None, repo_root=None, launch_agents=None) -> dict:
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
    args = ap.parse_args(argv)
    data_dir = config.data_dir()
    out = nightly(data_dir, args.root, mirror=args.mirror, ledger=args.ledger or data_dir.parent / ".telegram-delivery.sqlite",
                  repo_root=Path(__file__).resolve().parents[1], launch_agents=Path.home() / "Library" / "LaunchAgents")
    print(json.dumps({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **out}, sort_keys=True))
    sys.exit(1 if not out["journal_verify_ok"] else 2 if out["status"] == "no_mirror" else 0)


if __name__ == "__main__":
    main()
