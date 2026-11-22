"""Forecast journal: an append-only JSONL copy of every forecast and odds-quote row, kept outside the data folder.

The stores are append-only, but a store can still be lost as a whole (the Nov 15 reset lost the live ledger and the odds
history). The journal keeps each row's exact values, with a checksum, in plain files that a backup can copy anywhere.
It only reads the stores (`mode=ro`, `query_only`); it never writes, migrates or locks them.

    NBA_DATA_DIR=<store dir> python -m app.journal export --journal <dir>   # appends rows not yet journaled
    NBA_DATA_DIR=<store dir> python -m app.journal verify --journal <dir>   # every row journaled, unchanged, none lost

Layout: `<journal>/<sport>/<table>.jsonl`, one line per row: {"pk", "row", "sha256", "exported_at"}. Lines are only ever
appended. `verify` reports rows missing from the journal, rows that differ, lines whose checksum fails ("tampered") and
rows the journal holds but the store no longer has ("journal_only": evidence that a store lost data).
"""
import argparse
import base64
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
from .backup import SPORTS, _db_file


class JournalError(RuntimeError):
    pass


def _journaled(name: str) -> bool:
    return name == "predictions" or "forecast" in name or name.endswith("quotes")


def _value(v):
    return {"$base64": base64.b64encode(v).decode()} if isinstance(v, (bytes, memoryview)) else v


def _sha(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _tables(conn) -> dict:
    """Journaled table -> its single integer primary key column."""
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if not _journaled(name):
            continue
        pk = [r["name"] for r in conn.execute(f'PRAGMA table_info("{name}")') if r["pk"] > 0]
        if len(pk) == 1:
            out[name] = pk[0]
    return out


def _check_dirs(data_dir: Path, journal_dir: Path) -> None:
    d, j = Path(data_dir).expanduser().resolve(), Path(journal_dir).expanduser().resolve()
    if j == d or d in j.parents or j in d.parents or config.protected_path(j):
        raise JournalError(f"The journal ({j}) must be outside the data folder ({d}), and not contain it")


def _read(path: Path) -> list:
    if not path.is_file():
        return []
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            raise JournalError(f"{path} line {n} is not valid JSON (an interrupted write?); fix it by hand first") from None
    return out


def _stores(data_dir: Path) -> dict:
    return {s: Path(data_dir) / _db_file(s) for s in SPORTS if (Path(data_dir) / _db_file(s)).is_file()}


def export(data_dir: Path, journal_dir: Path, *, now: Optional[str] = None) -> dict:
    """Appends every journaled row not yet in the journal. Returns the number of new lines per sport and table."""
    _check_dirs(data_dir, journal_dir)
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock = os.open(journal_dir / ".journal.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JournalError("Another journal export is running") from None
        result = {}
        for sport, path in _stores(data_dir).items():
            conn = _open(path)
            try:
                counts = {}
                for table, pk in _tables(conn).items():
                    target = journal_dir / sport / f"{table}.jsonl"
                    have = {e["pk"] for e in _read(target)}
                    rows = [r for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY "{pk}"') if r[pk] not in have]
                    if rows:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        text = "".join(json.dumps({"pk": r[pk], "row": row, "sha256": _sha(row), "exported_at": stamp},
                                                  sort_keys=True) + "\n"
                                       for r in rows for row in [{k: _value(r[k]) for k in r.keys()}])
                        with open(target, "a") as f:
                            f.write(text)
                            f.flush()
                            os.fsync(f.fileno())
                    counts[table] = len(rows)
                result[sport] = counts
            finally:
                conn.close()
        return {"status": "ok", "journal": str(journal_dir), "sports": result}
    finally:
        os.close(lock)


def verify(data_dir: Path, journal_dir: Path) -> dict:
    """Compares the journal with the stores, row by row."""
    _check_dirs(data_dir, journal_dir)
    ok, result = True, {}
    stores = _stores(data_dir)
    sports = set(stores) | {p.name for p in Path(journal_dir).iterdir() if p.is_dir()} if Path(journal_dir).is_dir() else set(stores)
    for sport in sorted(sports):
        tables, db_rows = {}, {}
        if sport in stores:
            conn = _open(stores[sport])
            try:
                for table, pk in _tables(conn).items():
                    db_rows[table] = {r[pk]: {k: _value(r[k]) for k in r.keys()}
                                      for r in conn.execute(f'SELECT * FROM "{table}"')}
            finally:
                conn.close()
        names = set(db_rows) | {p.stem for p in (Path(journal_dir) / sport).glob("*.jsonl")}
        for table in sorted(names):
            lines = {e["pk"]: e for e in _read(Path(journal_dir) / sport / f"{table}.jsonl")}
            live = db_rows.get(table, {})
            tampered = sorted(pk for pk, e in lines.items() if _sha(e["row"]) != e["sha256"])
            report = {"journaled": len(lines), "in_store": len(live),
                      "missing": sorted(set(live) - set(lines)),
                      "journal_only": sorted(set(lines) - set(live)),
                      "tampered": tampered,
                      "differs": sorted(pk for pk in set(lines) & set(live)
                                        if pk not in tampered and lines[pk]["sha256"] != _sha(live[pk]))}
            if any(report[k] for k in ("missing", "journal_only", "tampered", "differs")):
                ok = False
            tables[table] = report
        result[sport] = tables
    return {"ok": ok, "sports": result}


def main(argv: Optional[list] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("export", "verify"))
    ap.add_argument("--journal", type=Path, required=True, help="Journal folder, outside the data folder")
    args = ap.parse_args(argv)
    out = export(config.data_dir(), args.journal) if args.action == "export" else verify(config.data_dir(), args.journal)
    if args.action == "verify":
        # Only problems are printed in full; a clean table is summarised.
        for tables in out["sports"].values():
            for name, r in tables.items():
                for k in ("missing", "journal_only", "tampered", "differs"):
                    r[k] = r[k] if len(r[k]) <= 20 else r[k][:20] + [f"... {len(r[k])} total"]
    print(json.dumps(out, indent=2, sort_keys=True))
    if args.action == "verify" and not out["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
