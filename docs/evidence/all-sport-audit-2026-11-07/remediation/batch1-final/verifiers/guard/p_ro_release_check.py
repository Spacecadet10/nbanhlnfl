"""Is the `--sport nhl release-check` exception guard-related? Compare protected folder vs an unprotected copy, and nba."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
S = fresh("ro_release_check")
data = fake_main(S / "fakerepo"); build_store(S, data / "nhl.sqlite", "nhl"); build_store(S, data / "nba.sqlite", "nba")
copy = S / "copy"; copy.mkdir(); shutil.copy(data / "nhl.sqlite", copy / "nhl.sqlite"); shutil.copy(data / "nba.sqlite", copy / "nba.sqlite")
U = ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")
for label, d, argv in (("protected nhl", data, ["--sport", "nhl", "release-check"]), ("copy nhl", copy, ["--sport", "nhl", "release-check"]),
                       ("protected nba", data, ["--sport", "nba", "release-check"])):
    b = {k: v for k, v in tree(d).items() if not k.endswith(("-wal", "-shm"))}
    rc, out = run(argv, {"NBA_DATA_DIR": str(d)}, U)
    a = {k: v for k, v in tree(d).items() if not k.endswith(("-wal", "-shm"))}
    try: j = json.loads(out); s = {k: j.get(k) for k in ("problems", "raw_missing", "guard_enabled")}
    except Exception: s = out[:200]
    print(f"--- {label}: rc={rc}\n  {s}\n  main files unchanged (ignoring -wal/-shm): {a == b}")
