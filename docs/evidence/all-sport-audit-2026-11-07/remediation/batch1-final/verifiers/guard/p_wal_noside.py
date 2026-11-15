"""mode=ro preflight on a WAL-mode store with no -wal/-shm present (all connections closed): no false refusal, no side files."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
sys.path.insert(1, CODE + "/tests")
from test_nfl import nfl_event, nfl_payload
S = fresh("wal_noside"); d = S / "d"; d.mkdir(); os.environ["NBA_DATA_DIR"] = str(d)
c = db.connect(d / "nfl.sqlite"); db.migrate(c)
ingest.ingest_nfl_month(c, "202410", fetch=lambda _m: nfl_payload(nfl_event()), retrieved_at="2024-10-07T12:00:00Z")
c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); c.close(); os.environ.pop("NBA_DATA_DIR")
for s in ("-wal", "-shm"):
    p = Path(str(d / "nfl.sqlite") + s)
    if p.exists(): print("removing leftover", p.name, p.stat().st_size, "bytes"); p.unlink()
print("header journal: WAL" if (d / "nfl.sqlite").read_bytes()[18:20] == b"\x02\x02" else "header journal: not WAL")
rp = ro(d / "nfl.sqlite", "SELECT path FROM raw_responses LIMIT 1")[0][0]
for s in ("-wal", "-shm"):
    p = Path(str(d / "nfl.sqlite") + s); p.unlink(missing_ok=True)
before = sorted(x.name for x in d.iterdir()); h = hashlib.sha256((d / "nfl.sqlite").read_bytes()).hexdigest()[:16]
U = ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")
rc, out = run(["nfl", "release-check"], {"NBA_DATA_DIR": str(d)}, U)
print("release-check raw_missing:", json.loads(out)["raw_missing"], json.loads(out).get("raw_missing_reason"), "files:", sorted(x.name for x in d.iterdir()), "files before:", before)
os.environ["NBA_DATA_DIR"] = str(d)
print("preflight (_replay_refused) result:", cli._replay_refused("nfl", ingest.replay_missing), "files after:", sorted(x.name for x in d.iterdir()), "main unchanged:", h == hashlib.sha256((d / "nfl.sqlite").read_bytes()).hexdigest()[:16])
(d / "raw" / rp).unlink()
print("preflight with the raw file missing:", cli._replay_refused("nfl", ingest.replay_missing))
