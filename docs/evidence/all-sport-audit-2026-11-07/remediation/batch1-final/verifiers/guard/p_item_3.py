"""Item 3: code runs from the clean committed checkout that holds the data folder, NBA_RELEASE_GUARD=1, ROOT=itself."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
R = G / "release" / "selfrepo"
assert app.__file__.startswith(str(R) + "/backend/app/"), app.__file__
cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
S = fresh("item_3"); data = R / "data"
for p in data.glob("*"): p.unlink() if p.is_file() else shutil.rmtree(p)
build_store(S, data / "nhl.sqlite", "nhl"); build_store(S, data / "nfl.sqlite", "nfl", upto=33)
print("git status clean:", subprocess.run(["git", "-C", str(R), "status", "--porcelain"], capture_output=True, text=True).stdout == "")
def go(label, ddir, argv, root=str(R)):
    env = {"NBA_RELEASE_GUARD": "1", "NBA_RELEASE_ROOT": root, "NBA_DATA_DIR": ddir}
    saved = dict(os.environ); os.environ.update(env)
    try: chk = release.check(argv[0] == "nfl" and "nfl" or "nhl"); mc = release.migration_context(argv[0] == "nfl" and "nfl" or "nhl")
    finally: os.environ.clear(); os.environ.update(saved)
    b = tree(data); rc, out = run(argv, env)
    print(f"[{label}] release.check problems: {chk['problems']}\n  migration_context: {mc}")
    show(label, b, tree(data), rc, out.replace(str(G), "$G")[:500])
go("3a score (shipped schema)", str(data), ["--sport", "nhl", "score"])
go("3b explicit migrate nfl@33", str(data), ["nfl", "migrate"])
go("3c migrate nhl", str(data), ["--sport", "nhl", "migrate"])
# aliases of the SAME checkout path (the check compares resolved path strings)
up = str(R).replace("/selfrepo", "/SELFREPO")
go("3d case-variant NBA_DATA_DIR (ROOT exact)", up + "/data", ["--sport", "nhl", "score"])
go("3e case-variant NBA_DATA_DIR, explicit nfl migrate", up + "/data", ["nfl", "migrate"])
go("3f firmlink-alias NBA_DATA_DIR", "/System/Volumes/Data" + str(data), ["--sport", "nhl", "score"])
print("final schemas: nhl", schema(data / "nhl.sqlite"), "nfl", schema(data / "nfl.sqlite"), "shipped", SHIPPED)
