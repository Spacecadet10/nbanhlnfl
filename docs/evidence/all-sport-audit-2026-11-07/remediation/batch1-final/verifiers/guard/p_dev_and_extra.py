"""Legit: unprotected dev copies create/migrate/write; WAL store with no side files passes the mode=ro preflight; real replay.
Extra: research/ symlinked into the protected folder + `nfl tick` (generic guard) -> live access log append."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
sys.path.insert(1, CODE + "/tests")
from test_nfl import nfl_event, nfl_payload
U = ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")
S = fresh("dev_and_extra")
print("no .git ancestor of scratch:", not any((a / ".git").exists() for a in [S, *S.parents]))
def ls(d): return sorted(p.name for p in Path(d).iterdir())
# D1 new store created + migrated by explicit migrate in an empty scratch dir
d = S / "d1"; rc, out = run(["--sport", "nhl", "migrate"], {"NBA_DATA_DIR": str(d)}, U); print("D1 nhl migrate on empty dir rc", rc, "schema", schema(d / "nhl.sqlite"), ls(d))
rc, out = run(["soccer", "migrate"], {"NBA_DATA_DIR": str(d)}, U); print("D1b soccer migrate rc", rc, "schema", schema(d / "soccer.sqlite"))
# D2 implicit migration of an older copy by a normal write command (runner stubbed)
d = S / "d2"; build_store(S, d / "nhl.sqlite", "nhl", upto=31)
real = cli._run; cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
rc, out = run(["--sport", "nhl", "score"], {"NBA_DATA_DIR": str(d)}, U); print("D2 implicit score on schema-31 copy rc", rc, out.strip(), "schema now", schema(d / "nhl.sqlite"))
cli._run = real
# D3 explicit nfl migrate 33 -> shipped on a copy
d = S / "d3"; build_store(S, d / "nfl.sqlite", "nfl", upto=33); rc, out = run(["nfl", "migrate"], {"NBA_DATA_DIR": str(d)}, U); print("D3 nfl migrate copy rc", rc, "schema", schema(d / "nfl.sqlite"))
# D4 real nfl replay on a WAL-mode copy whose connections are all closed (no -wal/-shm), every raw file present
d = S / "d4"; d.mkdir(); os.environ["NBA_DATA_DIR"] = str(d)
c = db.connect(d / "nfl.sqlite"); db.migrate(c)
ingest.ingest_nfl_month(c, "202410", fetch=lambda _m: nfl_payload(nfl_event()), retrieved_at="2024-10-07T12:00:00Z"); c.close(); os.environ.pop("NBA_DATA_DIR")
print("D4 journal mode:", ro(d / "nfl.sqlite", "PRAGMA journal_mode")[0][0], "files:", ls(d))
rc, out = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, U); print("D4 real nfl replay rc", rc, out.strip()[:200])
rc, out = run(["nfl", "release-check"], {"NBA_DATA_DIR": str(d)}, U); print("D4b nfl release-check raw_missing", json.loads(out)["raw_missing"], json.loads(out).get("raw_missing_reason"))
# D5 a fresh WAL store in an unprotected folder: 0-byte vs normal (finding: 0-byte is treated as nothing to check)
d = S / "d5"; d.mkdir(); (d / "nfl.sqlite").write_bytes(b"")
rc, out = run(["nfl", "replay"], {"NBA_DATA_DIR": str(d)}, U); print("D5 replay on 0-byte copy rc", rc, out.strip()[:200], "-> schema now", schema(d / "nfl.sqlite"))

# X1: copy with research/ symlinked into a protected folder; `nfl tick` passes the generic guard
fake = S / "fakerepo"; data = fake_main(fake); build_store(S, data / "nfl.sqlite", "nfl")
(data / "research/nfl_eval").mkdir(parents=True); log = data / "research/nfl_eval/nfl_test_access.jsonl"; log.write_text('{"live": "entry"}\n')
copy = S / "copy"; copy.mkdir(); shutil.copy(data / "nfl.sqlite", copy / "nfl.sqlite"); (copy / "research").symlink_to(data / "research")
from app import jobs, nfl_eval
print("X1 _write_refusal('nfl') for the copy:", (lambda: (os.environ.__setitem__("NBA_DATA_DIR", str(copy)), cli._write_refusal("nfl"))[1])())
print("X1 access_log_path resolves to:", nfl_eval.access_log_path().resolve().relative_to(S), "protected:", config.protected_path(nfl_eval.access_log_path()))
os.environ.pop("NBA_DATA_DIR")
def tick_stub(conn, **kw):
    # the access-log call nfl_live.record makes (nfl_live.py:90-91) when NFL live is active; network refresh not run
    sealed = nfl_eval.seal_choice({"probe": True})
    nfl_eval.load_rows(conn, through=nfl_eval.LAST_OPEN + 1, sealed=sealed, access_log=nfl_eval.access_log_path(), purpose="prospective NFL state initialization")
    return {"status": "ok", "stub": "jobs.tick -> nfl_eval.load_rows(access_log=access_log_path())"}
jobs.tick = tick_stub
b = tree(data); rc, out = run(["nfl", "tick"], {"NBA_DATA_DIR": str(copy)}, U)
show("X1 `nfl tick` on copy with research/ -> protected research", b, tree(data), rc, out)
print("X1 protected access log now:", log.read_text().strip().splitlines())
# same for `nfl activate` (explicit path list) for contrast
b = tree(data); rc, out = run(["nfl", "test-run", "--sealed", "/nonexistent", "--out", str(S / "o")], {"NBA_DATA_DIR": str(copy)}, U)
print("X1 contrast: `nfl test-run` same copy rc", rc, out.strip()[:120])
