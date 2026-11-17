#!/usr/bin/env python3
"""Nightly backup of every sport store plus the forecast journal, mirrored to a second place (owner request, Nov 15).

1. Appends new forecast and odds-quote rows to the journal (app.journal; reads the stores only) and verifies it.
2. Makes a verified online backup of every store (app.backup) under `<root>/stores/<UTC stamp>/`.
3. Puts a compressed copy of the whole journal in that backup folder (`journal.tar.gz`).
4. Mirrors the folder to `--mirror` (another disk or a synced cloud folder), checked against the manifest.

Nothing is ever deleted or overwritten. Without `--mirror` the backup still runs but exits 2: it is not off this machine.
A journal that disagrees with a store (a lost or changed row) exits 1 after the backup, so the log shows it.

    PYTHONPATH=backend NBA_DATA_DIR=<data> backend/.venv/bin/python scripts/backup_nightly.py --root <dir> [--mirror <dir>]
"""
import argparse
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import backup, config, journal  # noqa: E402


def nightly(data_dir: Path, root: Path, *, mirror, stamp=None) -> dict:
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
    out = {"stamp": stamp, "backup": str(folder), "journal_new_rows": {s: sum(v.values()) for s, v in exported["sports"].items()},
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
    args = ap.parse_args(argv)
    out = nightly(config.data_dir(), args.root, mirror=args.mirror)
    print(json.dumps({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **out}, sort_keys=True))
    sys.exit(1 if not out["journal_verify_ok"] else 2 if out["status"] == "no_mirror" else 0)


if __name__ == "__main__":
    main()
