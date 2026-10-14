#!/bin/sh
# One-command local development app (README "Run"). From the repo root:  sh scripts/dev.sh
#
# 1. Copies the live stores (read-only SQLite backup) and research files into a development data directory, then migrates
#    the copy to this checkout's schema. The live data/ folder is never migrated or written. Each start replaces the
#    copies; a store or folder missing from live has its earlier copy removed, so nothing stale is shown as current.
# 2. Starts the API (http://127.0.0.1:8000) and the web app (http://127.0.0.1:5173) and waits until both answer.
# 3. Stays in the foreground; Ctrl-C stops both. Restart to refresh the data copy.
#
# A previous development API/web server of THIS checkout on the same port is stopped first; anything else on the port
# (for example the release app) is never touched: the script refuses and names the process instead.
# Overrides: NBA_LIVE_DATA_DIR (default <repo>/data), NBA_DEV_DATA_DIR (default ~/nba-predictor-dev-data),
# API_PORT (8000), WEB_PORT (5173).
# `sh scripts/dev.sh --check-paths` only validates the two data directories and exits (nothing is stopped or written).
#
# Ownership: the development directory is only ever overwritten if this launcher created it. A path that does not exist
# yet is created and marked (.nba-dev-data); an existing directory without a valid marker -- even an empty one -- is
# refused, never adopted or marked. To start fresh, point NBA_DEV_DATA_DIR at a path that does not exist yet.
# Remaining limit: all checks run before use, not atomically with it. A directory, or one of its path components,
# replaced (for example by a symlink) by another process after the last check is not detected; the marker and the
# checks narrow this window but do not close it. Do not change these directories while the launcher starts.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
LIVE=${NBA_LIVE_DATA_DIR:-$ROOT/data}
DEV=${NBA_DEV_DATA_DIR:-$HOME/nba-predictor-dev-data}
API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-5173}
PY=$ROOT/backend/.venv/bin/python
DEV_MARK="nba-predictor development data copy; created by scripts/dev.sh, which may overwrite it"

fail() { echo "dev.sh: $*" >&2; exit 1; }
say() { echo "dev.sh: $*"; }

# Anchor both data directories to the directory the script was started from, before anything uses them: later steps run
# from backend/ and frontend/, where a relative path would name a different (possibly the live) directory.
START=$(pwd -P) || fail "cannot determine the current directory."
case "$LIVE" in /*) ;; ?*) LIVE=$START/$LIVE ;; esac
case "$DEV" in /*) ;; ?*) DEV=$START/$DEV ;; esac

[ -x "$PY" ] || fail "backend environment missing ($PY). See README 'Setup (once)'."
[ -d "$ROOT/frontend/node_modules" ] || fail "frontend dependencies missing. Run: cd frontend && npm ci"
[ -f "$LIVE/nba.sqlite" ] || fail "no live NBA store at $LIVE/nba.sqlite (set NBA_LIVE_DATA_DIR)."

# The development directory is replaced and partly deleted below, so it must be provably separate from the live one:
# relative paths, '..' and symlinks are resolved, then identity and nesting in either direction are refused, both by
# resolved path (case-insensitively) and by device/inode of every ancestor (catches firmlinks and bind mounts). Anything
# that cannot be resolved with certainty is refused. An existing development directory must also carry this launcher's
# marker, must not itself be a symlink (nor be named through one with a trailing '.' or '..'), and must contain no
# symlink anywhere (logs, SNAPSHOT_AT, stores and their -wal/-shm/-journal/.tmp/lock files, research/raw/eval/reports and
# anything the app may add); the files written in place must not be hard links either. The live trees that are copied
# must contain no symlink (cp -R copies links as links). Runs before any server is stopped or any file is touched, again
# after the directory is created, and again after the copy (before migrate and the servers).
check_paths() {
  "$PY" - check-paths "$LIVE" "$DEV" "$DEV_MARK" <<'EOF' || fail "refusing to continue: the development data directory is not provably separate from the live one or not safe to overwrite."
import os, stat, sys
live, dev, mark = sys.argv[2], sys.argv[3], sys.argv[4] + "\n"
MARKER = ".nba-dev-data"
FILES = ["SNAPSHOT_AT", "api.log", "web.log"] + [n for sport in ("nba", "nhl") for n in (
    f"{sport}.sqlite", f"{sport}.sqlite-wal", f"{sport}.sqlite-shm", f"{sport}.sqlite-journal", f".{sport}.sqlite.tmp",
    f".{sport}-jobs.lock")]
DIRS = ["research", "raw", "eval", "reports"]
COPIED = ["research", "raw", "eval"]
def refuse(msg):
    print(f"dev.sh: {msg} (live: {live!r}, development: {dev!r})", file=sys.stderr)
    sys.exit(1)
def ancestors(p):
    while True:
        yield p
        parent = os.path.dirname(p)
        if parent == p:
            return
        p = parent
def ident(p):
    s = os.stat(p)
    return s.st_dev, s.st_ino
def raise_error(exc):
    raise exc
def no_links(top, what):
    for base, dirs, files in os.walk(top, onerror=raise_error):   # never follows links; unreadable dirs are errors
        for n in dirs + files:
            if os.path.islink(os.path.join(base, n)):
                refuse(f"{what} contains a symlink: {os.path.join(base, n)}")
try:
    if not live or not dev:
        refuse("empty data directory path")
    if os.path.basename(dev.rstrip("/")) in (".", ".."):     # islink("<link>/.") is false: would hide a symlink
        refuse("development path ends in '.' or '..'; give the directory's own path")
    L = os.path.realpath(live, strict=True)
    if not os.path.isdir(L):
        refuse("live data path is not a directory")
    # Longest existing prefix of the development path, resolved by the OS; the rest must be plain new names.
    raw = dev if os.path.isabs(dev) else os.path.join(os.getcwd(), dev)
    head, tail = raw, []
    while not os.path.lexists(head):
        head, name = os.path.split(head)
        if name:
            tail.insert(0, name)
        elif not head or head == os.path.dirname(head):
            refuse("development path has no existing ancestor")
    if any(n in (".", "..") for n in tail):
        refuse("development path uses '.' or '..' below a directory that does not exist yet")
    E = os.path.realpath(head, strict=True)          # raises for a dangling or looping symlink
    if not os.path.isdir(E):
        refuse("development path (or its existing part) is not a directory")
    D = os.path.join(E, *tail)
    Lc, Dc = os.path.normcase(L).casefold(), os.path.normcase(D).casefold()
    if os.path.commonpath([Lc, Dc]) in (Lc, Dc):
        refuse("development and live data directories are the same or nested")
    live_id = ident(L)
    if any(ident(a) == live_id for a in ancestors(E)):
        refuse("development data directory is the live directory or inside it")
    if not tail:
        dev_id = ident(E)
        if any(ident(a) == dev_id for a in ancestors(L)):
            refuse("live data directory is inside the development directory")
    for sub in COPIED:                                 # copied into the development directory as they are
        p = os.path.join(L, sub)
        if os.path.islink(p):
            refuse(f"live {sub} is a symlink")
        if os.path.isdir(p):
            no_links(p, f"live {sub}")
    if not tail:                                       # existing: reused only if this launcher created it
        if os.path.islink(raw.rstrip("/") or "/"):
            refuse("development data directory is itself a symlink; give its real path")
        m = os.path.join(E, MARKER)
        try:
            st = os.lstat(m)
        except FileNotFoundError:
            refuse(f"existing development data directory {E} was not created by this launcher (no launcher marker "
                   f"{MARKER}); it is never adopted. Set NBA_DEV_DATA_DIR to a path that does not exist yet")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            refuse(f"launcher marker {m} is a symlink or not a regular file")
        fd = os.open(m, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)   # never waits on a FIFO swapped in meanwhile
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            refuse(f"launcher marker {m} is a symlink or not a regular file")
        with os.fdopen(fd, "rb") as f:
            if f.read(len(mark.encode()) + 1) != mark.encode():
                refuse(f"launcher marker {m} has unexpected contents")
        for n in FILES + DIRS:
            p = os.path.join(E, n)
            if not os.path.lexists(p):
                continue
            st = os.lstat(p)
            mode = st.st_mode
            if stat.S_ISLNK(mode):
                refuse(f"write target is a symlink: {p}")
            if n in FILES and not stat.S_ISREG(mode):
                refuse(f"write target is not a regular file: {p}")
            if n in FILES and st.st_nlink != 1:          # written in place: another link would receive the write
                refuse(f"write target is a hard link shared with another file: {p}")
            if n in DIRS and not stat.S_ISDIR(mode):
                refuse(f"write target is not a directory: {p}")
        no_links(E, "development data directory")
except SystemExit:
    raise
except Exception as exc:
    refuse(f"could not validate the data directories ({type(exc).__name__}: {exc})")
print(f"dev.sh: data directories are separate: live {L}, development {D}"
      + (" (launcher-owned)" if not tail else " (will be created)"))
EOF
}
# Only a directory made here, at a path where nothing existed, is marked. os.mkdir fails if anything has appeared there
# since the check, so an unrelated directory is never adopted; the marker is created exclusively and never follows links.
create_dev() {
  "$PY" - create-dev "$DEV" "$DEV_MARK" <<'EOF' || fail "could not create a new development data directory at $DEV (nothing existing is adopted)."
import os, sys
dev, mark = sys.argv[2].rstrip("/"), sys.argv[3] + "\n"
os.makedirs(os.path.dirname(dev), exist_ok=True)
os.mkdir(dev)
fd = os.open(os.path.join(dev, ".nba-dev-data"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
with os.fdopen(fd, "w") as f:
    f.write(mark)
EOF
}
check_paths
[ "${1:-}" = "--check-paths" ] && exit 0

# Free a port held by an earlier development server of this checkout; refuse for anything else.
free_port() {
  for pid in $(lsof -t -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null); do
    cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    case "$cwd" in
      "$ROOT"/backend|"$ROOT"/frontend)
        case "$cmd" in *uvicorn*app.main:app*|*vite*)
          say "stopping earlier development server on port $1 (pid $pid: $cmd)"
          kill "$pid"; sleep 1; continue ;;
        esac ;;
    esac
    fail "port $1 is in use by pid $pid ($cmd, cwd $cwd). Stop it or set ${2}."
  done
}
free_port "$API_PORT" API_PORT
free_port "$WEB_PORT" WEB_PORT

say "copying live stores from $LIVE to $DEV (read-only backup)"
[ -e "$DEV" ] || [ -L "$DEV" ] || create_dev
check_paths >/dev/null   # again now that the directory exists (inode checks in both directions, marker, links)
"$PY" - "$LIVE" "$DEV" <<'EOF'
import os, sqlite3, sys
from pathlib import Path
live, dev = Path(sys.argv[1]), Path(sys.argv[2])
for name in ("nba.sqlite", "nhl.sqlite"):
    src = live / name
    tmp = dev / f".{name}.tmp"
    old = [dev / n for n in (name, f"{name}-wal", f"{name}-shm", f"{name}-journal", tmp.name)]
    if not src.is_file():                # no stale copy may be served under a fresh SNAPSHOT_AT
        if any(p.exists() for p in old):
            print(f"dev.sh: {name} not found in {live}; removing the earlier development copy")
        for p in old:
            p.unlink(missing_ok=True)
        continue
    tmp.unlink(missing_ok=True)
    s = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)   # read-only: sees committed WAL data, writes nothing
    d = sqlite3.connect(tmp)
    s.backup(d)
    d.close(); s.close()
    for suffix in ("-wal", "-shm", "-journal"):
        (dev / f"{name}{suffix}").unlink(missing_ok=True)
    os.replace(tmp, dev / name)
EOF
# Research artifacts and caches the API reads (copy-on-write clones on APFS; a plain copy elsewhere). Only these three
# launcher-managed names inside the validated, marked development directory are ever removed. A failed clone may leave a
# partial copy, which is removed before the plain copy (cp -R into an existing directory would nest it).
for sub in research raw eval; do
  if [ ! -d "$LIVE/$sub" ]; then
    [ -e "$DEV/$sub" ] && say "$sub not found in $LIVE; removing the earlier development copy"
    rm -rf "$DEV/$sub"
    continue
  fi
  rm -rf "$DEV/$sub"
  cp -cR "$LIVE/$sub" "$DEV/$sub" 2>/dev/null \
    || { rm -rf "$DEV/$sub" && cp -R "$LIVE/$sub" "$DEV/$sub"; } \
    || fail "copying $LIVE/$sub failed."
done
check_paths >/dev/null   # again after the copy: nothing written below may lead outside the development directory
for sport in nba nhl; do
  [ -f "$DEV/$sport.sqlite" ] || continue
  (cd "$ROOT/backend" && NBA_DATA_DIR="$DEV" "$PY" -m app.cli --sport "$sport" migrate >/dev/null) \
    || fail "migrating the $sport copy failed (the live store is untouched)."
done
date -u +%Y-%m-%dT%H:%M:%SZ > "$DEV/SNAPSHOT_AT"
say "data copy ready ($(cat "$DEV/SNAPSHOT_AT"))"

PIDS=""
cleanup() { [ -n "$PIDS" ] && kill $PIDS 2>/dev/null; wait 2>/dev/null; say "stopped"; }
trap cleanup EXIT
trap 'exit 130' INT TERM

(cd "$ROOT/backend" && NBA_API_MODE=real NBA_DATA_DIR="$DEV" NBA_LIVE_DATA_DIR="$LIVE" \
  exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT") >"$DEV/api.log" 2>&1 &
API_PID=$!; PIDS="$API_PID"
(cd "$ROOT/frontend" && API_PROXY_TARGET="http://127.0.0.1:$API_PORT" \
  exec npm run dev -- --host 127.0.0.1 --port "$WEB_PORT" --strictPort) >"$DEV/web.log" 2>&1 &
WEB_PID=$!; PIDS="$PIDS $WEB_PID"

wait_for() {   # url, name, log
  i=0
  until curl -fsS -m 5 "$1" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
      echo "dev.sh: $2 did not answer at $1 within 60 s. Last log lines ($3):" >&2
      tail -20 "$3" >&2
      exit 1
    fi
    sleep 1
  done
}
wait_for "http://127.0.0.1:$API_PORT/api/health" API "$DEV/api.log"
wait_for "http://127.0.0.1:$WEB_PORT/" "web app" "$DEV/web.log"
wait_for "http://127.0.0.1:$WEB_PORT/api/health" "web app → API proxy" "$DEV/web.log"

say "ready"
echo "  App:  http://127.0.0.1:$WEB_PORT/"
echo "  API:  http://127.0.0.1:$API_PORT/api/health"
echo "  Data: copy of $LIVE taken $(cat "$DEV/SNAPSHOT_AT"); training status reads $LIVE directly (read-only)."
echo "  Logs: $DEV/api.log, $DEV/web.log. Ctrl-C stops both."
# If either server stops, stop the other too: a half-running app would only show errors.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do sleep 2; done
kill -0 "$API_PID" 2>/dev/null || echo "dev.sh: the API stopped; last lines of $DEV/api.log:" >&2
kill -0 "$WEB_PID" 2>/dev/null || echo "dev.sh: the web server stopped; last lines of $DEV/web.log:" >&2
tail -5 "$DEV/api.log" "$DEV/web.log" >&2
exit 1
