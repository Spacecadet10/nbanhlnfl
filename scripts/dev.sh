#!/bin/sh
# One-command local development app (README "Run"). From the repo root:  sh scripts/dev.sh
#
# 1. Copies the live stores (read-only SQLite backup) and research files into a development data directory, then migrates
#    the copy to this checkout's schema. The live data/ folder is never migrated or written. Each start replaces the
#    copies; a store or folder missing from live has its earlier copy removed, so nothing stale is shown as current.
# 2. Starts the API (http://127.0.0.1:8000) and the web app (http://127.0.0.1:5173) and waits until both answer.
# 3. Republishes the copies while it runs (`app.cli preview-publish`, docs/REFRESH_PLAN.md): every PUBLISH_INTERVAL seconds
#    (default 60) it compares each live store with what it last published and, only when the data changed or the copy's
#    source time lags live by more than 10 minutes, publishes a new consistent backup in one transaction (the previous copy
#    stays served if that fails). NBA_DEV_PUBLISH=0 keeps the startup copy frozen (the app then says it will not update).
#    Research files (research/, raw/, eval/) are copied at startup only.
# 4. Stays in the foreground; Ctrl-C stops everything.
#
# A previous development API/web server of THIS checkout on the same port is stopped first; anything else on the port
# (for example the release app) is never touched: the script refuses and names the process instead.
# Overrides: NBA_LIVE_DATA_DIR (default <repo>/data), NBA_DEV_DATA_DIR (default ~/nba-predictor-dev-data),
# API_PORT (8000), WEB_PORT (5173), PUBLISH_INTERVAL (60 s), NBA_DEV_PUBLISH (1; 0 = frozen copy).
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
# after the directory is created, and again after the copy (before migrate and the servers). The check lives in
# backend/app/devpaths.py (standard library only, fed to the backend Python on stdin) so the preview publisher runs the
# very same proof before every publication.
check_paths() {
  "$PY" - check-paths "$LIVE" "$DEV" "$DEV_MARK" < "$ROOT/backend/app/devpaths.py" || fail "refusing to continue: the development data directory is not provably separate from the live one or not safe to overwrite."
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
for name in ("nba.sqlite", "nhl.sqlite", "nfl.sqlite"):
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
for sport in nba nhl nfl; do
  [ -f "$DEV/$sport.sqlite" ] || continue
  if [ "$sport" = nfl ]; then set -- nfl migrate; else set -- --sport "$sport" migrate; fi   # NFL: data-only command
  (cd "$ROOT/backend" && NBA_DATA_DIR="$DEV" "$PY" -m app.cli "$@" >/dev/null) \
    || fail "migrating the $sport copy failed (the live store is untouched)."
done
rm -f "$DEV/PUBLISH_STATUS.json"   # a previous run's publisher status must not describe this copy
date -u +%Y-%m-%dT%H:%M:%SZ > "$DEV/SNAPSHOT_AT"
say "data copy ready ($(cat "$DEV/SNAPSHOT_AT"))"

PIDS=""
cleanup() { [ -n "$PIDS" ] && kill $PIDS 2>/dev/null; wait 2>/dev/null; say "stopped"; }
trap cleanup EXIT
trap 'exit 130' INT TERM

if [ "${NBA_DEV_PUBLISH:-1}" != 0 ]; then
  (cd "$ROOT/backend" && NBA_DATA_DIR="$DEV" exec "$PY" -m app.cli preview-publish --live "$LIVE" --dev "$DEV" \
    --interval "${PUBLISH_INTERVAL:-60}") >"$DEV/publish.log" 2>&1 &
  PIDS="$PIDS $!"   # if it stops, the app keeps serving the last copy and reports it as frozen
fi

(cd "$ROOT/backend" && NBA_API_MODE=real NBA_DATA_DIR="$DEV" NBA_LIVE_DATA_DIR="$LIVE" \
  exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT") >"$DEV/api.log" 2>&1 &
API_PID=$!; PIDS="$PIDS $API_PID"
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
[ "${NBA_DEV_PUBLISH:-1}" != 0 ] && echo "  Copies are republished when the live data changes (checked every ${PUBLISH_INTERVAL:-60} s; $DEV/publish.log)."
echo "  Logs: $DEV/api.log, $DEV/web.log. Ctrl-C stops everything."
# If either server stops, stop the other too: a half-running app would only show errors.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do sleep 2; done
kill -0 "$API_PID" 2>/dev/null || echo "dev.sh: the API stopped; last lines of $DEV/api.log:" >&2
kill -0 "$WEB_PID" 2>/dev/null || echo "dev.sh: the web server stopped; last lines of $DEV/web.log:" >&2
tail -5 "$DEV/api.log" "$DEV/web.log" >&2
exit 1
