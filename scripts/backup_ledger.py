#!/usr/bin/env python3
"""Daily backup of the Telegram delivery ledger (.telegram-delivery.sqlite), with a verified restore (owner, Nov 10).

The ledger holds every sent card and ticket receipt, ticket batches and grades; losing it would resend or forget them.
Each run copies it with the SQLite backup API (a consistent copy while the jobs keep writing), writes the copy and its
SHA-256 under the backup folder (never overwriting), then verifies a restore: the copy is restored into a temporary
folder, opened with the app's own ledger code (app.telegram.ledger), checked with PRAGMA integrity_check, and every
table's row count is compared with the counts read inside the same backup. Nothing is ever deleted or sent.

    PYTHONPATH=backend backend/.venv/bin/python scripts/backup_ledger.py --ledger <path> --dest <folder>

Restore by hand (stop the telegram job first): copy a verified backup over .telegram-delivery.sqlite (chmod 600).
"""
import argparse, hashlib, json, os, shutil, sqlite3, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path


def counts(conn):
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def backup(ledger: Path, dest: Path, now: datetime) -> dict:
    if not ledger.is_file() or ledger.is_symlink():
        raise SystemExit(f"ledger missing or not a regular file: {ledger}")
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    out = dest / f"telegram-delivery-{now:%Y%m%dT%H%M%SZ}.sqlite"
    if out.exists():
        raise SystemExit(f"backup already exists: {out}")
    tmp = out.with_suffix(".partial")
    src = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True, timeout=30, isolation_level=None)
    dst = sqlite3.connect(tmp)
    try:
        src.execute("BEGIN")                # one read transaction: the SOURCE's counts and the copy see the same moment
        expected = counts(src)
        src.backup(dst)
        src.execute("COMMIT")
    finally:
        dst.close(); src.close()
    os.chmod(tmp, 0o600)
    tmp.rename(out)                         # a reader never sees half a backup
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / (out.name + ".sha256")).write_text(f"{digest}  {out.name}\n")
    return dict(backup=str(out), sha256=digest, expected=expected)


def verify_restore(path: Path, expected: dict, sha256: str = None) -> dict:
    """Restore the backup into a temporary folder and open it the way the app does. `expected` = the source ledger's row
    counts read in the same transaction as the copy."""
    from app import telegram
    with tempfile.TemporaryDirectory(prefix="ledger-restore-") as d:
        restored = Path(d) / ".telegram-delivery.sqlite"
        shutil.copy2(path, restored)
        same = sha256 is None or hashlib.sha256(restored.read_bytes()).hexdigest() == sha256
        raw = sqlite3.connect(restored)     # before the app's ledger() can create a table the copy lacks
        before = set(counts(raw))
        raw.close()
        with telegram.ledger(restored) as conn:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            got = counts(conn)
            sent = conn.execute("SELECT COUNT(*) FROM deliveries WHERE status='sent'").fetchone()[0]
    problems = [] if ok == "ok" else [f"integrity_check: {ok}"]
    problems += [] if same else ["restored file differs from the backup's sha256"]
    problems += [f"{t}: table missing from the backup" for t in expected if t not in before]
    problems += [f"{t}: {got.get(t)} rows restored, {n} backed up" for t, n in expected.items() if got.get(t) != n]
    return dict(status="verified" if not problems else "FAILED", integrity=ok, tables=len(expected), rows=sum(expected.values()),
                sent_deliveries=sent, problems=problems)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--dest", required=True)
    a = ap.parse_args()
    os.umask(0o077)                                # every file this writes is private (the ledger holds chat ids)
    os.environ.pop("NBA_RELEASE_GUARD", None)      # the restore check opens a temporary copy, never the live ledger
    now = datetime.now(timezone.utc).replace(microsecond=0)
    made = backup(Path(a.ledger), Path(a.dest), now)
    check = verify_restore(Path(made["backup"]), made["expected"], made["sha256"])
    result = dict(at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), backup=made["backup"], sha256=made["sha256"], **check)
    with open(Path(a.dest) / "verify-log.jsonl", "a") as fh:     # append-only record of every backup and its restore check
        fh.write(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result))
    sys.exit(0 if check["status"] == "verified" else 1)


if __name__ == "__main__":
    main()
