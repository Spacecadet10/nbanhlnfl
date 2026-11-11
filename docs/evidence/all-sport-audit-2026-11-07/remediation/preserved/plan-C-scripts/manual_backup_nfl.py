"""Interim NFL store backup (plan C3). Stdlib only. Reads the source with mode=ro (never writes it); refuses an existing
destination; online backup API; integrity + foreign-key check; sha256; manifest compatible with app.backup's format.
usage: python3 -I manual_backup_nfl.py <source nfl.sqlite> <new dest dir>"""
import hashlib, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
src, dest = Path(sys.argv[1]), Path(sys.argv[2])
if dest.exists(): sys.exit(f"refused: {dest} exists")
dest.mkdir(parents=True)
target = dest / "nfl.sqlite"
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True); d = sqlite3.connect(target)
s.backup(d); d.close(); s.close()
c = sqlite3.connect(f"file:{target}?mode=ro&immutable=1", uri=True)
info = {"integrity": c.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_violations": len(c.execute("PRAGMA foreign_key_check").fetchall()),
        "schema_version": c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
        "identity": c.execute("SELECT sport FROM store_identity").fetchone()[0],
        "rows": {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                 ("games", "result_revisions", "game_forecasts", "market_quotes", "raw_responses", "raw_fetches", "job_runs", "teams")}}
c.close()
h = hashlib.sha256(target.read_bytes()).hexdigest()
man = {"created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data_dir": str(src.parent),
       "stores": {"nfl": {"file": "nfl.sqlite", "source": str(src), "bytes": target.stat().st_size, "sha256": h, **info}}}
(dest / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True))
print(json.dumps(man, indent=1))
if info["integrity"] != "ok" or info["identity"] != "nfl": sys.exit("BACKUP NOT USABLE")
