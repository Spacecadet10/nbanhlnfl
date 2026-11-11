import csv, sqlite3, sys
from pathlib import Path
store, raw, out = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
c = sqlite3.connect(f"file:{store}?immutable=1", uri=True)
rows = c.execute("SELECT raw_id, source, endpoint, request_key, content_hash, byte_size, path, first_retrieved_at, importer_version FROM raw_responses ORDER BY raw_id").fetchall()
miss = [r for r in rows if not (raw / r[6]).is_file()]
with open(out, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["raw_id","source","endpoint","request_key","content_hash","byte_size","path","first_retrieved_at","importer_version","fetch_count","games_with_last_raw_id"])
    for r in miss:
        fc = c.execute("select count(*) from raw_fetches where raw_id=?", (r[0],)).fetchone()[0]
        g = c.execute("select count(*) from games where last_raw_id=?", (r[0],)).fetchone()[0]
        w.writerow(list(r)+[fc,g])
print(len(rows), len(miss), min(r[0] for r in miss), max(r[0] for r in miss), min(r[7] for r in miss), max(r[7] for r in miss), sum(r[5] for r in miss))
