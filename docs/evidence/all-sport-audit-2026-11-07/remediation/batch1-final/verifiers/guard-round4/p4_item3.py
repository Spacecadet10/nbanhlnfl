"""N-A re-test: selfrepo (clean committed checkout holding its data/), release vars set to itself, path aliases."""
import sys, os; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify/round4")
from lib import *
R = G / "release" / "selfrepo"; Rs = str(R)
assert app.__file__.lower().startswith(Rs.lower() + "/backend/app/"), app.__file__
print("code_root():", release.code_root(), "| cwd:", os.getcwd())
cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
S = fresh("item3_" + os.environ.get("TAG", "x")); data = R / "data"
for p in list(data.glob("*")) + list(data.glob(".*")): p.unlink() if p.is_file() else shutil.rmtree(p)
build_store(S, data / "nhl.sqlite", "nhl"); build_store(S, data / "nfl.sqlite", "nfl", upto=33)
UP = Rs.replace("/selfrepo", "/SELFREPO"); FL = "/System/Volumes/Data" + Rs
def go(label, ddir, argv, root):
    env = {"NBA_RELEASE_GUARD": "1", "NBA_RELEASE_ROOT": root, "NBA_DATA_DIR": ddir}
    saved = dict(os.environ); os.environ.update(env)
    try: chk = release.check("nhl"); mc = release.migration_context("nhl")
    finally: os.environ.clear(); os.environ.update(saved)
    b = tree(data); rc, out = run(argv, env)
    print(f"[{label}] release.check problems: {chk['problems']}\n  migration_context: {[m[:110] for m in mc]}")
    show(label, b, tree(data), rc, out.replace(str(G), "$G4")[:260])
root = str(release.code_root())
for lbl, dd in (("exact", Rs + "/data"), ("case-variant", UP + "/data"), ("firmlink", FL + "/data"), ("dotdot", Rs + "/backend/../data"), ("trailing-dot", Rs + "/data/.")):
    go(f"{lbl} NBA_DATA_DIR: nhl score", dd, ["--sport", "nhl", "score"], root)
    go(f"{lbl} NBA_DATA_DIR: nfl migrate", dd, ["nfl", "migrate"], root)
print("final schemas: nhl", schema(data / "nhl.sqlite"), "nfl", schema(data / "nfl.sqlite"))
