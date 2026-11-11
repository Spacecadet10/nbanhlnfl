"""Contrived alias: a regular copy of the store, but its -wal/-shm are symlinks to the protected store's -wal/-shm."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
S = fresh("wal_symlink"); data = fake_main(S / "fakerepo")
build_store(S, data / "nhl.sqlite", "nhl", wal=True)
copy = S / "copy"; copy.mkdir(); shutil.copy(data / "nhl.sqlite", copy / "nhl.sqlite")
live = sqlite3.connect(data / "nhl.sqlite", isolation_level=None); live.execute("PRAGMA journal_mode=WAL"); live.execute("PRAGMA wal_autocheckpoint=0")
live.execute("CREATE TABLE live_only(x)"); live.execute("INSERT INTO live_only VALUES (42)")      # frames only in the live WAL
for s in ("-wal", "-shm"): Path(str(copy / "nhl.sqlite") + s).symlink_to(Path(str(data / "nhl.sqlite") + s))
os.environ["NBA_DATA_DIR"] = str(copy); print("_write_refusal for the copy:", cli._write_refusal("nhl")); os.environ.pop("NBA_DATA_DIR")
wal = Path(str(data / "nhl.sqlite") + "-wal"); b = hashlib.sha256(wal.read_bytes()).hexdigest()[:16]
cli._run = lambda *a, k=None, **kw: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
rc, out = run(["--sport", "nhl", "score"], {"NBA_DATA_DIR": str(copy)}, ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT"))
print("rc", rc, out.strip()); print("protected -wal changed by the dev command:", b != hashlib.sha256(wal.read_bytes()).hexdigest()[:16], "size", wal.stat().st_size)
print("copy sees live-only table:", ro(copy / "nhl.sqlite", "SELECT name FROM sqlite_master WHERE name='live_only'"))
live.close()
