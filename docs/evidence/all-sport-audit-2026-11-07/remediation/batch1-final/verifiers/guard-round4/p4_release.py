"""Round 4 release env: separate clean clone of the NEW app; data in a fake main checkout."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify/round4")
from lib import *
C = G / "release" / "relclone"; M = G / "release" / "fakemain"; data = M / "data"
assert app.__file__.startswith(str(C) + "/backend/app/"), app.__file__
from app import jobs
S = fresh("release")
for p in list(data.glob("*")) + list(data.glob(".*")): p.unlink() if p.is_file() else shutil.rmtree(p)
build_store(S, data / "nhl.sqlite", "nhl", upto=31); build_store(S, data / "nfl.sqlite", "nfl", upto=33); build_store(S, data / "nba.sqlite", "nba", upto=31)
ENV = {"NBA_RELEASE_GUARD": "1", "NBA_RELEASE_ROOT": str(C), "NBA_DATA_DIR": str(data)}
def mc(env, sport):
    saved = dict(os.environ); os.environ.update(env)
    try: return release.check(sport)["problems"], release.migration_context(sport)
    finally: os.environ.clear(); os.environ.update(saved)
print("research/ present in live folder:", (data / "research").exists())
print("migration_context nhl (store at 31):", mc(ENV, "nhl"))
def go(label, argv, env=ENV):
    b = tree(data); rc, out = run(argv, env); show(label, b, tree(data), rc, out.replace(str(G), "$G4")[:200])
cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
go("implicit nhl replay on 31 (must refuse)", ["--sport", "nhl", "replay"])
go("implicit nfl tick on 33 (must refuse)", ["nfl", "tick"])
for argv in (["--sport", "nhl", "migrate"], ["nfl", "migrate"], ["--sport", "nba", "migrate"]): go("explicit " + " ".join(argv), argv)
print("schemas:", schema(data / "nhl.sqlite"), schema(data / "nfl.sqlite"), schema(data / "nba.sqlite"), "shipped", SHIPPED)
jobs.tick = lambda conn, **k: {"status": "ok", "stub": "jobs.tick (no network)"}; jobs.refresh = lambda conn, **k: {"status": "ok", "stub": "jobs.refresh (no network)"}
go("guarded nhl score at shipped", ["--sport", "nhl", "score"])
go("nfl tick at shipped", ["nfl", "tick"])
go("nfl refresh at shipped", ["nfl", "refresh"])
(data / "research/nfl_eval").mkdir(parents=True); (data / "research/nfl_eval/nfl_test_access.jsonl").write_text("")
go("nfl tick at shipped, research/ present", ["nfl", "tick"])
# release with a data dir that does not exist yet (only the identity part may not add a problem)
env2 = {**ENV, "NBA_DATA_DIR": str(M / "data-not-yet")}
p, m = mc(env2, "nfl"); print("nonexistent data dir: check problems", p, "| migration_context", m, "| identity added nothing:", [x for x in m if x not in p] == [])
env3 = {**ENV, "NBA_DATA_DIR": str(G / "release" / "elsewhere" / "deep" / "data")}
p, m = mc(env3, "nfl"); print("nonexistent nested data dir:", [x for x in m if x not in p] == [], m)
