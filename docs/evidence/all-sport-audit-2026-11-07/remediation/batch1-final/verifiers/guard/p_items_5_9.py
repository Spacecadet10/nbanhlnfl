"""Items 5-9: replay preflight fail-closed, read-only (mode=ro), nfl release-check without raw tables, <=20 ids, hand repro."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
sys.path.insert(1, CODE + "/tests")
from test_nfl import nfl_event, nfl_payload
from test_storage import event, payload
import threading
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16] if Path(p).exists() else None
def ls(d): return sorted(p.name for p in Path(d).iterdir())
NOREL = ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")

def nfl_store(d, months=2):
    os.environ["NBA_DATA_DIR"] = str(d)
    c = db.connect(d / "nfl.sqlite"); db.migrate(c)
    for i in range(months):
        y, m = 2003 + i // 12, 1 + i % 12
        ingest.ingest_nfl_month(c, f"{y}{m:02d}", fetch=lambda _m, i=i: nfl_payload(nfl_event(eid=str(401000000 + i), date=f"{y}-{m:02d}-05T17:00Z", year=y)),
                                retrieved_at=f"{y}-{m:02d}-07T12:00:00Z")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); c.execute("PRAGMA journal_mode = DELETE"); c.close()
    os.environ.pop("NBA_DATA_DIR")
    return d / "nfl.sqlite"

# ---- item 5: fail closed on db errors (unprotected copies, so only the preflight stands between replay and a write)
S = fresh("items_5_9")
cases = {}
d = S / "corrupt"; d.mkdir(); (d / "nfl.sqlite").write_bytes(b"not a database" * 100); cases["5a corrupt"] = d
d = S / "zero"; d.mkdir(); (d / "nfl.sqlite").write_bytes(b""); cases["5b zero-byte"] = d
d = S / "unreadable"; d.mkdir(); nfl_store(d); os.chmod(d / "nfl.sqlite", 0); cases["5d unreadable(chmod 000)"] = d
d = S / "droppedfetches"; d.mkdir(); nfl_store(d)
p = sqlite3.connect(d / "nfl.sqlite", isolation_level=None); p.execute("PRAGMA foreign_keys=OFF"); p.execute("DROP TABLE raw_fetches"); p.close(); cases["5e raw_fetches dropped"] = d
d = S / "header"; d.mkdir(); nfl_store(d); b = bytearray((d / "nfl.sqlite").read_bytes()); b[100:4096] = b"\xff" * (4096 - 100); (d / "nfl.sqlite").write_bytes(bytes(b)); cases["5f corrupted page 1"] = d
for label, d in cases.items():
    b_ls, b_sha = ls(d), sha(d / "nfl.sqlite") if os.access(d / "nfl.sqlite", os.R_OK) else "unreadable"
    rc, out = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, NOREL)
    a_sha = sha(d / "nfl.sqlite") if os.access(d / "nfl.sqlite", os.R_OK) else "unreadable"
    print(f"--- {label}\nrc: {rc}\nstdout: {out.strip()[:600]}\nfiles before/after: {b_ls} {ls(d)}  store sha before/after: {b_sha} {a_sha}")
os.chmod(S / "unreadable" / "nfl.sqlite", 0o644)
# 5c locked: another connection holds an EXCLUSIVE lock on a DELETE-journal store
d = S / "locked"; d.mkdir(); nfl_store(d)
holder = sqlite3.connect(d / "nfl.sqlite", isolation_level=None, check_same_thread=False); holder.execute("BEGIN EXCLUSIVE")
b_sha = sha(d / "nfl.sqlite"); b_ls = ls(d)
import time; t0 = time.time()
rc, out = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, NOREL)
print(f"--- 5c locked (EXCLUSIVE held by another connection)\nrc: {rc}  secs: {time.time()-t0:.1f}\nstdout: {out.strip()[:600]}\nfiles before/after: {b_ls} {ls(d)}  sha before/after: {b_sha} {sha(d/'nfl.sqlite')}")
holder.execute("ROLLBACK"); holder.close()
# 5g: ProtectedStoreError path: older-schema store reached through a symlink (protected), replay and score
fake = S / "fakerepo"; data = fake_main(fake); build_store(S, data / "nfl.sqlite", "nfl", upto=33); build_store(S, data / "nhl.sqlite", "nhl", upto=31)
copy = S / "symcopy"; copy.mkdir(); (copy / "nfl.sqlite").symlink_to(data / "nfl.sqlite"); (copy / "nhl.sqlite").symlink_to(data / "nhl.sqlite")
for argv in (["nfl", "replay"], ["--sport", "nhl", "replay"], ["--sport", "nhl", "score"], ["nfl", "refresh"]):
    bt = tree(data); rc, out = run(argv, {"NBA_DATA_DIR": str(copy)}, NOREL)
    show(f"5g older-schema via symlink: {' '.join(argv)}", bt, tree(data), rc, out.replace(str(G), "$G")[:300])
print("5g schemas after:", schema(data / "nfl.sqlite"), schema(data / "nhl.sqlite"))

# ---- item 6: read-only open (mode=ro): WAL store with uncheckpointed frames; DELETE store gets no side files
d = S / "wal"; d.mkdir(); nfl_store(d)
w = sqlite3.connect(d / "nfl.sqlite", isolation_level=None); w.execute("PRAGMA journal_mode=WAL"); w.execute("PRAGMA wal_autocheckpoint=0")
w.execute("UPDATE raw_responses SET importer_version = importer_version WHERE 0=1"); w.execute("CREATE TABLE zz_probe(x)"); w.execute("INSERT INTO zz_probe VALUES (1)")
# delete one raw file so replay refuses after the preflight (no write by replay itself)
rp = ro(d / "nfl.sqlite", "SELECT path FROM raw_responses ORDER BY raw_id DESC LIMIT 1")[0][0]
b = (sha(d / "nfl.sqlite"), sha(d / "nfl.sqlite-wal"), ls(d))
w_keep = w   # keep the writer open so the WAL is not checkpointed by its close
(d / "raw" / rp).unlink()
rc, out = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, NOREL)
a = (sha(d / "nfl.sqlite"), sha(d / "nfl.sqlite-wal"), ls(d))
print(f"--- 6a WAL store, preflight refusal\nrc: {rc}\nstdout: {out.strip()[:300]}\nmain sha before/after: {b[0]} {a[0]}  wal sha before/after: {b[1]} {a[1]}\nfiles before/after: {b[2]} {a[2]}")
rc, out = run(["nfl", "release-check"], {"NBA_DATA_DIR": str(d)}, NOREL)
a2 = (sha(d / "nfl.sqlite"), sha(d / "nfl.sqlite-wal"), ls(d))
print(f"--- 6b WAL store, nfl release-check\nrc: {rc}\nraw_missing: {json.loads(out).get('raw_missing')}\nmain sha before/after: {b[0]} {a2[0]}  wal sha: {b[1]} {a2[1]}\nfiles after: {a2[2]}")
# the old connect_readonly for comparison, writer closed first? (writer still open: frames not checkpointed)
w_keep.close()
print("6 note: writer closed; main sha now:", sha(d / "nfl.sqlite"), "(writer close may checkpoint, expected)")
# DELETE journal store: no side files from the preflight or nfl release-check
d = S / "deletej"; d.mkdir(); nfl_store(d); rp = ro(d / "nfl.sqlite", "SELECT path FROM raw_responses ORDER BY raw_id DESC LIMIT 1")[0][0]; (d / "raw" / rp).unlink()
b = (sha(d / "nfl.sqlite"), ls(d))
rc1, out1 = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, NOREL)
rc2, out2 = run(["nfl", "release-check"], {"NBA_DATA_DIR": str(d)}, NOREL)
print(f"--- 6c DELETE-journal store\nreplay rc {rc1}: {out1.strip()[:200]}\nrelease-check rc {rc2} raw_missing: {json.loads(out2).get('raw_missing')}\nsha before/after: {b[0]} {sha(d/'nfl.sqlite')}  files before/after: {b[1]} {ls(d)}")

# ---- item 7: nfl release-check without raw tables
for label, setup in (("7a zero-byte", lambda p: p.write_bytes(b"")),
                     ("7b sqlite without raw tables", lambda p: (lambda c: (c.execute("CREATE TABLE t(x)"), c.commit(), c.close()))(sqlite3.connect(p))),
                     ("7c corrupt", lambda p: p.write_bytes(b"garbage" * 200)),
                     ("7d no store", lambda p: None)):
    d = S / ("rc_" + label.split()[0]); d.mkdir(); setup(d / "nfl.sqlite")
    b = (sha(d / "nfl.sqlite"), ls(d))
    rc, out = run(["nfl", "release-check"], {"NBA_DATA_DIR": str(d)}, NOREL)
    try: j = json.loads(out); summary = {"raw_missing": j.get("raw_missing"), "raw_missing_reason": j.get("raw_missing_reason")}
    except Exception: summary = out[:400]
    print(f"--- {label}\nrc: {rc}\n{summary}\nsha/files before/after: {b} {(sha(d/'nfl.sqlite'), ls(d))}")

# ---- item 8: <=20 ids
d = S / "many"; d.mkdir(); nfl_store(d, months=25)
for p in (d / "raw").rglob("*.gz"): p.unlink()
b = sha(d / "nfl.sqlite")
rc, out = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, NOREL)
j = json.loads(out.strip().splitlines()[-1])
print(f"--- 8 nfl replay with 25 missing\nrc: {rc}\nreason: {j['reason'][-160:]}\nmissing_raw: count={j['missing_raw']['count']} listed={len(j['missing_raw']['raw_ids'])} truncated={j['missing_raw']['truncated']}\nsha unchanged: {b == sha(d/'nfl.sqlite')}")
rc, out = run(["nfl", "release-check"], {"NBA_DATA_DIR": str(d)}, NOREL)
j = json.loads(out); print("8b nfl release-check raw_missing:", {k: (len(v) if isinstance(v, list) else v) for k, v in j["raw_missing"].items()})

# ---- item 9 by hand: NBA, older schema 31 target, earlier raw present, later missing
d = S / "hand9"; d.mkdir(); os.environ["NBA_DATA_DIR"] = str(d)
src = db.connect(d / "source.sqlite"); db.migrate(src)
ingest.ingest_month(src, "202401", fetch=lambda _m: payload(event(eid="1001")), retrieved_at="2024-01-20T12:00:00Z")
ingest.ingest_month(src, "202402", fetch=lambda _m: payload(event(eid="1002", date="2024-02-15T18:00Z")), retrieved_at="2024-02-20T12:00:00Z")
tgt = db.connect(d / "nba.sqlite"); db.migrate(tgt, upto=31)
for t in ("raw_responses", "raw_fetches"):
    have = {c[1] for c in tgt.execute(f"PRAGMA table_info({t})")}
    for r in src.execute(f"SELECT * FROM {t}"):
        cols = [c for c in r.keys() if c in have]
        tgt.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?'*len(cols))})", tuple(r[c] for c in cols))
tgt.execute("PRAGMA wal_checkpoint(TRUNCATE)"); tgt.execute("PRAGMA journal_mode=DELETE"); tgt.close()
later = src.execute("SELECT r.raw_id, r.path FROM raw_responses r JOIN raw_fetches f USING(raw_id) ORDER BY f.retrieved_at DESC LIMIT 1").fetchone()
earlier = src.execute("SELECT r.raw_id, r.path FROM raw_responses r JOIN raw_fetches f USING(raw_id) ORDER BY f.retrieved_at LIMIT 1").fetchone()
src.close(); os.environ.pop("NBA_DATA_DIR")
(d / "raw" / later["path"]).unlink()
print("9 earlier raw present:", (d / "raw" / earlier["path"]).is_file(), "later present:", (d / "raw" / later["path"]).is_file())
def dumpall(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try: return {t: sorted(map(repr, c.execute(f'SELECT * FROM "{t}"'))) for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally: c.close()
b = (sha(d / "nba.sqlite"), schema(d / "nba.sqlite"), dumpall(d / "nba.sqlite"), ls(d))
rc, out = run(["replay"], {"NBA_DATA_DIR": str(d)}, NOREL)
a = (sha(d / "nba.sqlite"), schema(d / "nba.sqlite"), dumpall(d / "nba.sqlite"), ls(d))
print(f"--- 9 hand repro NBA schema 31\nrc: {rc}\nstdout: {out.strip()[:500]}\nstore sha before/after: {b[0]} {a[0]}  schema before/after: {b[1]} {a[1]}  all tables equal: {b[2]==a[2]}  files: {b[3]} {a[3]}\nlater raw_id: {later['raw_id']}")
