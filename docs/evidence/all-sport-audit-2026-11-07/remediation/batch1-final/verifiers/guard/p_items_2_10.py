"""Item 2 (symlink and path aliases) and item 10 (hard links). Command: `--sport nhl score` (a guarded write).
cli._run is replaced by a marker so we see whether the command got past the guard + store open + migrate."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/guard-verify")
from lib import *
cli._run = lambda *a, **k: (print(json.dumps({"status": "REACHED_RUN"})), 0)[1]
S = fresh("items_2_10")
fake = S / "fakerepo"; data = fake_main(fake)
build_store(S, data / "nhl.sqlite", "nhl")
(data / "raw").mkdir()
NOREL = ("NBA_RELEASE_GUARD", "NBA_RELEASE_ROOT")
def case(label, ddir, setup=None, cwd=None, extra_check=None):
    c = S / ("c_" + label.split()[0]); 
    if c.exists(): shutil.rmtree(c)
    if setup: setup(c)
    b = tree(data); jr = jobrows(data / "nhl.sqlite")
    old = os.getcwd()
    if cwd: os.chdir(cwd)
    try: rc, out = run(["--sport", "nhl", "score"], {"NBA_DATA_DIR": ddir(c) if callable(ddir) else ddir}, NOREL)
    finally: os.chdir(old)
    show(label, b, tree(data), rc, out)
    print("  protected job_runs before/after:", jr, jobrows(data / "nhl.sqlite"), "| side files in protected:", sorted(p.name for p in data.glob("nhl.sqlite-*")))
    if extra_check: extra_check(c)
# --- item 2 aliases
case("2a store-file-symlink", lambda c: str(c), lambda c: (c.mkdir(), (c / "nhl.sqlite").symlink_to(data / "nhl.sqlite")))
case("2b data-dir-symlink", lambda c: str(c), lambda c: c.symlink_to(data))
case("2c raw-dir-symlink", lambda c: str(c), lambda c: (c.mkdir(), shutil.copy(data / "nhl.sqlite", c / "nhl.sqlite"), (c / "raw").symlink_to(data / "raw")))
def lock_setup(c):
    c.mkdir(); shutil.copy(data / "nhl.sqlite", c / "nhl.sqlite"); (data / ".nhl-jobs.lock").write_text(""); (c / ".nhl-jobs.lock").symlink_to(data / ".nhl-jobs.lock")
case("2d lock-symlink", lambda c: str(c), lock_setup)
(data / ".nhl-jobs.lock").unlink()
def dangling_lock(c):
    c.mkdir(); shutil.copy(data / "nhl.sqlite", c / "nhl.sqlite"); (c / ".nhl-jobs.lock").symlink_to(data / ".nhl-jobs.lock")
case("2d2 dangling-lock-symlink(would-create-lock-in-protected)", lambda c: str(c), dangling_lock)
rel = os.path.relpath(str(data), "/Users/andry/Desktop/nba-predictor-batch1/backend")
case("2e relative-path-with-..", rel, cwd="/Users/andry/Desktop/nba-predictor-batch1/backend")
case("2e2 dotdot-in-absolute", str(fake / "data" / ".." / "data"))
case("2f trailing-slash", str(data) + "/")
case("2f2 double-slash", str(data).replace("/fakerepo/", "//fakerepo//"))
case("2g case-variant", str(data).replace("/fakerepo/data", "/FAKEREPO/DATA"))
case("2g2 firmlink-alias", "/System/Volumes/Data" + str(data))
case("2h empty-NBA_DATA_DIR-cwd-protected", "", cwd=str(data))
case("2h2 empty-NBA_DATA_DIR-cwd-checkout-root", "", cwd=str(fake), extra_check=lambda c: print("  created at checkout root:", sorted(p.name for p in fake.iterdir())))
for p in fake.glob("nhl.sqlite*"): p.unlink()
for p in fake.glob(".nhl-jobs.lock"): p.unlink()
case("2i tilde-path", "~/" + os.path.relpath(str(data), os.path.expanduser("~")) if str(data).startswith(os.path.expanduser("~")) else str(data))
case("2j parent-symlinked", lambda c: str(c / "data"), lambda c: c.symlink_to(fake))
# --- item 10 hard links
def hl(c): c.mkdir(); os.link(data / "nhl.sqlite", c / "nhl.sqlite")
case("10a hard-linked-store", lambda c: str(c), hl)
def hl_older(c):
    c.mkdir(); os.link(data / "nhl.sqlite", c / "nhl.sqlite")
case("10b hard-linked-store-migrate", lambda c: str(c), hl_older)
# explicit migrate too
c = S / "c_10b"; b = tree(data); rc, out = run(["--sport", "nhl", "migrate"], {"NBA_DATA_DIR": str(c)}, NOREL); show("10b' migrate on hard-linked store", b, tree(data), rc, out)
# 10c: whole data dir hard-link copy (cp -al equivalent)
def cpal(c):
    c.mkdir()
    for p in data.iterdir():
        if p.is_file(): os.link(p, c / p.name)
case("10c cp-al-style-copy", lambda c: str(c), cpal)
# 10d: store a regular copy, lock file hard-linked
def hl_lock(c):
    c.mkdir(); shutil.copy(data / "nhl.sqlite", c / "nhl.sqlite"); (data / ".nhl-jobs.lock").write_text(""); os.link(data / ".nhl-jobs.lock", c / ".nhl-jobs.lock")
case("10d hard-linked-lock(copy store regular)", lambda c: str(c), hl_lock)
(data / ".nhl-jobs.lock").unlink()
# 10e: store a regular copy, a raw file hard-linked (replay only reads raw; store_fetch writes tmp+rename)
def hl_raw(c):
    c.mkdir(); shutil.copy(data / "nhl.sqlite", c / "nhl.sqlite"); (data / "raw" / "x.json.gz").write_bytes(b"x"); (c / "raw").mkdir(); os.link(data / "raw" / "x.json.gz", c / "raw" / "x.json.gz")
case("10e hard-linked-raw-file", lambda c: str(c), hl_raw)
# 10f: false positives: regular copy, sqlite backup copy, APFS clone (cp -c) -> must NOT be refused
def plain(c): c.mkdir(); shutil.copy(data / "nhl.sqlite", c / "nhl.sqlite")
case("10f plain-copy(should-pass)", lambda c: str(c), plain)
def bk(c):
    c.mkdir(); s = sqlite3.connect(f"file:{data/'nhl.sqlite'}?mode=ro", uri=True); d = sqlite3.connect(c / "nhl.sqlite"); s.backup(d); d.close(); s.close()
case("10g sqlite-backup-copy(should-pass)", lambda c: str(c), bk)
def clone(c): c.mkdir(); subprocess.run(["cp", "-c", str(data / "nhl.sqlite"), str(c / "nhl.sqlite")], check=True)
case("10h apfs-clone-cp-c(should-pass)", lambda c: str(c), clone)
print("final protected listing:", sorted(p.name for p in data.iterdir()), "schema:", schema(data / "nhl.sqlite"), "shipped:", SHIPPED)
