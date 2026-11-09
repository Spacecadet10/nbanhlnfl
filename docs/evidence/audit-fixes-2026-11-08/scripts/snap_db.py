"""Consistent read-only copy of a live SQLite store: python3 snap_db.py SRC DST (retries while the writer swaps its WAL)."""
import sqlite3, sys, time
src, dst = sys.argv[1], sys.argv[2]
for attempt in range(40):
    try:
        a = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=5)
        a.execute("select count(*) from sqlite_master").fetchone()
        b = sqlite3.connect(dst)
        a.backup(b); b.close(); a.close()
        print("copied", src, "->", dst, "attempt", attempt + 1); break
    except sqlite3.OperationalError as exc:
        time.sleep(0.5); err = exc
else:
    raise SystemExit(f"could not open {src}: {err}")
