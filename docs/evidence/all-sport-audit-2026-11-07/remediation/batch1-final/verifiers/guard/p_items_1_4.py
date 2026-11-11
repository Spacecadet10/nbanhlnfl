"""Items 1 and 4: nfl activate / test-run / collect-lines --test on a protected folder outside the release env."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
S = fresh("items_1_4")
data = fake_main(S / "fakerepo")
build_store(S, data / "nfl.sqlite", "nfl")
inp = S / "inputs"; inp.mkdir()
for n in ("sealed.json", "report.json"): (inp / n).write_text("{}")
(inp / "access.jsonl").write_text("")
NOREL = ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")
env = {"NBA_DATA_DIR": str(data)}
# 1a: no activation file yet
b = tree(data); rc, out = run(["nfl", "activate", "--sealed", str(inp/"sealed.json"), "--report", str(inp/"report.json"), "--access-log", str(inp/"access.jsonl")], env, NOREL)
show("1a activate, protected, no activation file", b, tree(data), rc, out)
# 1b: an existing activation file must not be replaced
(data / "research/nfl_live").mkdir(parents=True); (data / "research/nfl_live/activation.json").write_text('{"live": true}')
b = tree(data); rc, out = run(["nfl", "activate", "--sealed", str(inp/"sealed.json"), "--report", str(inp/"report.json"), "--access-log", str(inp/"access.jsonl")], env, NOREL)
show("1b activate, protected, existing activation file", b, tree(data), rc, out)
# 1c: guard ON but not a real release (NBA_RELEASE_ROOT wrong) still refused
b = tree(data); rc, out = run(["nfl", "activate", "--sealed", str(inp/"sealed.json"), "--report", str(inp/"report.json"), "--access-log", str(inp/"access.jsonl")], {**env, "NBA_RELEASE_GUARD": "1", "NBA_RELEASE_ROOT": str(S)})
show("1c activate, protected, guard=1 but wrong root", b, tree(data), rc, out)
# 1d: unprotected copy whose research/ is a symlink into the protected folder
copy = S / "copy"; copy.mkdir(); shutil.copy(data / "nfl.sqlite", copy / "nfl.sqlite"); (copy / "research").symlink_to(data / "research")
b = tree(data); rc, out = run(["nfl", "activate", "--sealed", str(inp/"sealed.json"), "--report", str(inp/"report.json"), "--access-log", str(inp/"access.jsonl")], {"NBA_DATA_DIR": str(copy)}, NOREL)
show("1d activate, copy with research/ symlinked into protected", b, tree(data), rc, out)
# 4a/4b: test-run and collect-lines --test, access log present with known content
(data / "research/nfl_eval").mkdir(parents=True); (data / "research/nfl_eval/nfl_test_access.jsonl").write_text('{"x":1}\n')
b = tree(data); rc, out = run(["nfl", "test-run", "--sealed", str(inp/"sealed.json"), "--out", str(S/"out-tr")], env, NOREL)
show("4a test-run, protected", b, tree(data), rc, out); print("out dir created:", (S/"out-tr").exists())
b = tree(data); rc, out = run(["nfl", "collect-lines", "--test", "--sealed", str(inp/"sealed.json")], env, NOREL)
show("4b collect-lines --test, protected", b, tree(data), rc, out)
# 4c: access log absent: not created
(data / "research/nfl_eval/nfl_test_access.jsonl").unlink()
b = tree(data); rc, out = run(["nfl", "test-run", "--sealed", str(inp/"sealed.json"), "--out", str(S/"out-tr2")], env, NOREL)
show("4c test-run, protected, no access log yet", b, tree(data), rc, out)
# 4d: copy with research/ symlinked into protected
b = tree(data); rc, out = run(["nfl", "test-run", "--sealed", str(inp/"sealed.json"), "--out", str(S/"out-tr3")], {"NBA_DATA_DIR": str(copy)}, NOREL)
show("4d test-run, copy with research/ symlinked", b, tree(data), rc, out)
print("lock files anywhere in protected:", [p.name for p in data.glob(".*lock")])
