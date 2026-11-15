"""Round 4 (integrated tree): N-B research symlinks, N-C 0-byte, dev-copy and read-only regressions, new-spelling probes."""
import sys, unicodedata; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify/round4")
from lib import *
assert app.__file__.startswith("/Users/andry/Desktop/nba-predictor-batch1/backend/app/")
sys.path.insert(1, CODE + "/tests")
from test_nfl import nfl_event, nfl_payload
from app import jobs, nfl_eval
U = ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")
S = fresh("main")
def ls(d): return sorted(p.name for p in Path(d).iterdir()) if Path(d).exists() else None
fake = S / "fakerepo"; data = fake_main(fake); build_store(S, data / "nfl.sqlite", "nfl"); build_store(S, data / "nhl.sqlite", "nhl")
(data / "research/nfl_eval").mkdir(parents=True); (data / "research/nfl_live").mkdir(parents=True)
log = data / "research/nfl_eval/nfl_test_access.jsonl"; log.write_text('{"live": "entry"}\n')
(data / "research/adaptive").mkdir()
calls = []
def appender(conn, **kw):
    calls.append(1)
    nfl_eval.load_rows(conn, through=nfl_eval.LAST_OPEN + 1, sealed=nfl_eval.seal_choice({"probe": True}),
                       access_log=nfl_eval.access_log_path(), purpose="prospective NFL state initialization")
    return {"status": "ok", "stub": "appended via access_log_path()"}
jobs.tick = appender; jobs.refresh = appender
def mkcopy(name, research=None):
    c = S / name; c.mkdir(); shutil.copy(data / "nfl.sqlite", c / "nfl.sqlite"); shutil.copy(data / "nhl.sqlite", c / "nhl.sqlite")
    if research: research(c)
    return c
def go(label, c, argv):
    n = len(calls); b = tree(data); rc, out = run(argv, {"NBA_DATA_DIR": str(c)}, U)
    show(label, b, tree(data), rc, out.replace(str(G), "$G4")[:230]); print("  stub reached:", len(calls) > n)
# --- N-B
c1 = mkcopy("x1", lambda c: (c / "research").symlink_to(data / "research"))
for a in ("tick", "refresh", "migrate", "ingest"):
    go(f"X1 research/ -> protected research: nfl {a}", c1, ["nfl", a] + (["--from", "202410", "--to", "202410"] if a == "ingest" else []))
c2 = mkcopy("x2", lambda c: ((c / "research").mkdir(), (c / "research/nfl_eval").symlink_to(data / "research/nfl_eval")))
go("X2 nested research/nfl_eval -> protected: nfl tick", c2, ["nfl", "tick"])
c3 = mkcopy("x3", lambda c: ((c / "research").mkdir(), (c / "research/nfl_live").symlink_to(data / "research/nfl_live")))
go("X3 nested research/nfl_live -> protected: nfl refresh", c3, ["nfl", "refresh"])
c4 = mkcopy("x4", lambda c: (c / "research").symlink_to(fake))      # research -> the checkout root (parent of data)
go("X4 research -> checkout root: nfl tick", c4, ["nfl", "tick"])
c5 = mkcopy("x5", lambda c: (c / "research").write_text("file"))
go("X5 research is a plain file (unprotected): nfl tick", c5, ["nfl", "tick"])
c6 = mkcopy("x6", lambda c: (c / "research").symlink_to(data / "nfl.sqlite"))
go("X6 research -> protected store FILE: nfl tick", c6, ["nfl", "tick"])
c7 = mkcopy("x7", lambda c: ((c / "research").mkdir(), (c / "research/adaptive").symlink_to(data / "research/adaptive")))
os.environ["NBA_DATA_DIR"] = str(c7)
print("X7 nested research/adaptive -> protected: _write_refusal('nhl') =", cli._write_refusal("nhl"), "(guard-level only)")
os.environ.pop("NBA_DATA_DIR")
print("protected access log lines after X1-X7:", len(log.read_text().splitlines()))
# --- N-C 0-byte
for sport, argv in (("nfl", ["nfl", "replay"]), ("nhl", ["--sport", "nhl", "replay"]), ("nba", ["replay"]), ("soccer", ["soccer", "replay"])):
    d = S / f"zero_{sport}"; d.mkdir(); (d / config.db_path(sport).name if False else d / {"nba": "nba.sqlite"}.get(sport, f"{sport}.sqlite")).write_bytes(b"")
    rc, out = run(argv, {"NBA_DATA_DIR": str(d)}, U)
    print(f"--- 0-byte {sport} replay: rc {rc} | {out.strip()[:160].replace(str(G), '$G4')} | files after {ls(d)} sizes {[p.stat().st_size for p in d.iterdir()]}")
d = S / "nostore"; rc, out = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, U)
print(f"--- nonexistent nfl store replay: rc {rc} | {out.strip()[:120]} | created: {ls(d)} schema {schema(d / 'nfl.sqlite')}")
# --- unprotected dev copies still write (with a real research/ dir, and without one)
for name, mk in (("dev_research", lambda c: (c / "research/nfl_eval").mkdir(parents=True)), ("dev_noresearch", None)):
    c = mkcopy(name, mk)
    for argv in (["nfl", "tick"], ["nfl", "refresh"], ["nfl", "migrate"]):
        n = len(calls); rc, out = run(argv, {"NBA_DATA_DIR": str(c)}, U); print(f"--- {name}: {' '.join(argv)} rc {rc} stub reached {len(calls) > n} | {out.strip()[:90]}")
    real = cli._run; cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
    rc, out = run(["--sport", "nhl", "score"], {"NBA_DATA_DIR": str(c)}, U); print(f"--- {name}: nhl score rc {rc} {out.strip()}"); cli._run = real
    print(f"    {name} own access log lines:", len((c / "research/nfl_eval/nfl_test_access.jsonl").read_text().splitlines()) if (c / "research/nfl_eval/nfl_test_access.jsonl").exists() else 0)
d = S / "dev_new"; rc, out = run(["--sport", "nhl", "migrate"], {"NBA_DATA_DIR": str(d)}, U); print("--- new dev dir nhl migrate rc", rc, "schema", schema(d / "nhl.sqlite"))
# --- read-only on the protected folder (guard off)
for argv in (["nfl", "release-check"], ["nfl", "coverage"], ["--sport", "nhl", "coverage"], ["--sport", "nhl", "thresholds"]):
    b = {k: v for k, v in tree(data).items() if not k.endswith(("-wal", "-shm"))}
    rc, out = run(argv, {"NBA_DATA_DIR": str(data)}, U)
    a = {k: v for k, v in tree(data).items() if not k.endswith(("-wal", "-shm"))}
    print(f"--- RO {' '.join(argv)}: rc {rc} refused={'refused' in out} content unchanged={a == b}")
# --- spellings: unicode NFC/NFD folder names, trailing dot, case
uf = S / unicodedata.normalize("NFC", "fakérepo"); ud = fake_main(uf); build_store(S, ud / "nhl.sqlite", "nhl")
nfd = str(S / unicodedata.normalize("NFD", "fakérepo") / "data")
print("NFD spelling differs as a string:", nfd != str(ud), "exists:", Path(nfd).exists())
for label, dd in (("NFD of NFC-named checkout", nfd), ("trailing /.", str(data) + "/."), ("case", str(data).replace("fakerepo", "FakeRepo"))):
    b = tree(ud if "NFD" in label else data)
    real = cli._run; cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
    rc, out = run(["--sport", "nhl", "score"], {"NBA_DATA_DIR": dd}, U); cli._run = real
    print(f"--- spelling {label}: rc {rc} refused={'refused' in out} tree unchanged={b == tree(ud if 'NFD' in label else data)}")
print("protected final:", ls(data), "| log lines:", len(log.read_text().splitlines()))
