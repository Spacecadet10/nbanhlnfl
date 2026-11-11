"""Legitimate ops in a genuine release environment: separate clean clone of the integrated code, data in a fake main checkout."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
C = G / "release" / "relclone"; M = G / "release" / "fakemain"; data = M / "data"
assert app.__file__.startswith(str(C) + "/backend/app/"), app.__file__
print("data protected:", config.protected_data_dir(data), "| clone clean:", subprocess.run(["git", "-C", str(C), "status", "--porcelain"], capture_output=True, text=True).stdout == "")
S = fresh("release_env")
for p in data.glob("*"): p.unlink() if p.is_file() else shutil.rmtree(p)
build_store(S, data / "nhl.sqlite", "nhl", upto=31); build_store(S, data / "nfl.sqlite", "nfl", upto=33); build_store(S, data / "nba.sqlite", "nba", upto=31)
ENV = {"NBA_RELEASE_GUARD": "1", "NBA_RELEASE_ROOT": str(C), "NBA_DATA_DIR": str(data)}
def go(label, argv, env=ENV):
    b = tree(data); rc, out = run(argv, env)
    show(label, b, tree(data), rc, out.replace(str(G), "$G")[:600])
# implicit commands never migrate an older store (release env)
real_run = cli._run
cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
go("R1 implicit non-guarded `--sport nhl replay` on schema 31", ["--sport", "nhl", "replay"])
go("R2 implicit guarded `--sport nhl score` on schema 31", ["--sport", "nhl", "score"])
go("R3 implicit `nfl ingest` on schema 33", ["nfl", "ingest", "--from", "202410", "--to", "202410"])
go("R4 implicit `soccer ingest`-like `soccer replay` (no soccer store)", ["soccer", "replay"])
print("schemas after implicit:", schema(data / "nhl.sqlite"), schema(data / "nfl.sqlite"), schema(data / "nba.sqlite"))
# explicit migrate works
go("R5 explicit `--sport nhl migrate` 31->shipped", ["--sport", "nhl", "migrate"])
go("R6 explicit `nfl migrate` 33->shipped", ["nfl", "migrate"])
go("R7 explicit `--sport nba migrate` 31->shipped", ["--sport", "nba", "migrate"])
print("schemas after explicit:", schema(data / "nhl.sqlite"), schema(data / "nfl.sqlite"), schema(data / "nba.sqlite"), "shipped", SHIPPED)
# guarded write at shipped schema allowed (runner stubbed: no network)
go("R8 guarded `--sport nhl score` at shipped (runner stubbed)", ["--sport", "nhl", "score"])
# nfl refresh at shipped: guard passes; network-bearing job stubbed
from app import jobs
jobs.refresh = lambda conn, force=False: {"status": "ok", "stub": "jobs.refresh stubbed (no network)"}
go("R9 `nfl refresh` at shipped (jobs.refresh stubbed)", ["nfl", "refresh"])
# nfl activate / test-run: not refused for guard reasons
inp = S / "inputs"; inp.mkdir()
(inp / "sealed.json").write_text(json.dumps({"sha256": "x"})); (inp / "report.json").write_text("{}"); (inp / "access.jsonl").write_text("")
go("R10 `nfl activate` in release env", ["nfl", "activate", "--sealed", str(inp/"sealed.json"), "--report", str(inp/"report.json"), "--access-log", str(inp/"access.jsonl")])
go("R11 `nfl test-run` in release env", ["nfl", "test-run", "--sealed", str(inp/"sealed.json"), "--out", str(S/"tr-out")])
go("R12 `nfl collect-lines --test` in release env", ["nfl", "collect-lines", "--test", "--sealed", str(inp/"sealed.json")])
# read-only commands on the protected folder, guard OFF (dev checkout = this clone, but no release vars)
cli._run = real_run
noenv = {"NBA_DATA_DIR": str(data)}
saved = dict(os.environ); [os.environ.pop(k, None) for k in ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")]
for label, argv in (("RO1 `--sport nhl release-check`", ["--sport", "nhl", "release-check"]),
                    ("RO2 `nfl release-check`", ["nfl", "release-check"]),
                    ("RO3 `--sport nhl thresholds` (READ_ONLY_COMMANDS)", ["--sport", "nhl", "thresholds"]),
                    ("RO4 `nfl coverage`", ["nfl", "coverage"]),
                    ("RO5 `--sport nhl coverage`", ["--sport", "nhl", "coverage"])):
    b = tree(data); rc, out = run(argv, noenv, ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT"))
    show(label, b, tree(data), rc, out.replace(str(G), "$G")[:300])
os.environ.clear(); os.environ.update(saved)
# API read path (FastAPI TestClient), guard off, protected folder
os.environ["NBA_DATA_DIR"] = str(data); [os.environ.pop(k, None) for k in ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")]
try:
    from starlette.testclient import TestClient
    from app import main as api
    b = tree(data)
    with TestClient(api.app) as client:
        for url in ("/api/health", "/api/games?sport=nhl", "/api/status?sport=nhl"):
            r = client.get(url); print("API", url, r.status_code, r.text[:120].replace("\n", " "))
    print("API tree unchanged:", b == tree(data))
    if b != tree(data): print("  before", b, "\n  after", tree(data))
except Exception as e:
    import traceback; print("API probe error:", type(e).__name__, e)
