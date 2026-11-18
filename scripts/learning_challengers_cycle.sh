#!/bin/sh
# Prospective challenger recording, every sport, from the pinned delivery release (docs/ADAPTIVE_SYSTEM.md, challengers).
# Research only: appends rows to <data>/research/challengers.sqlite; never touches serving, cards, betting or the sport stores.
# Each sport runs through `app.learning_run --light`: its own small lock (never the machine-wide heavy lock) and no game-window
# deferral, because the recorder only prices games inside the two hours before tip-off. Wall 120 s, memory 1.5 GB, nice 10,
# one thread. A failing sport never stops the next one; the exit code is 1 if any failed. One JSON line per sport on stdout.
# Extra arguments go to the recorder (e.g. --dry-run). LC_* variables exist for tests and rehearsals.
set -u
LIVE=${LC_LIVE:-/Users/andry/nba-predictor-delivery}
DATA=${LC_DATA:-/Users/andry/Desktop/nbanhlnfl/data}
PY=${LC_PY:-$LIVE/backend/.venv/bin/python}
SPORTS=${LC_SPORTS:-"nba nhl ncaab soccer nfl"}
cd "${LC_BACKEND:-$LIVE/backend}" || exit 1
export NBA_RELEASE_GUARD=${NBA_RELEASE_GUARD-1} NBA_RELEASE_ROOT=${NBA_RELEASE_ROOT-$LIVE} NBA_DATA_DIR="$DATA" \
  PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

# One backup of an existing challenger store before the first run that could migrate its schema (open_store migrates in place).
# The live store does not exist before the first non-dry run, which creates it.
STORE="$DATA/research/challengers.sqlite"
case " $* " in *" --dry-run "*) ;; *)
  if [ -f "$STORE" ] && ! ls "$DATA"/research/challengers.pre-cycle-*.sqlite >/dev/null 2>&1; then
    "$PY" -c 'import sqlite3,sys; s=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()' \
      "$STORE" "$DATA/research/challengers.pre-cycle-$(date -u +%Y%m%dT%H%M%SZ).sqlite" || { echo '{"status":"failed","reason":"store backup failed"}'; exit 1; }
  fi ;;
esac

TMP=$(mktemp -d "${TMPDIR:-/tmp}/lc-cycle.XXXXXX") || exit 1
trap 'rm -rf "$TMP"' EXIT
rc_all=0
for s in $SPORTS; do
  t0=$(date +%s)
  "$PY" -m app.learning_run --light --name "challengers-$s" --max-minutes 2 --max-rss-gb 1.5 --nice 10 --threads 1 -- \
    "$PY" -m app.learning_challengers record --sport "$s" "$@" >"$TMP/$s.out" 2>"$TMP/$s.err"
  rc=$?
  [ "$rc" -eq 0 ] || rc_all=1
  "$PY" -c '
import json, sys
sport, rc, wall, path = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
text = open(path).read()
try:
    out = json.loads(text)
except ValueError:
    out = {}
keys = ("status", "dry_run", "eligible_games", "created", "would_record", "already_recorded")
line = {"sport": sport, "rc": rc, "wall_s": wall, **{k: out[k] for k in keys if k in out}}
if rc and not out:
    line["output"] = text.strip()[-300:]
print(json.dumps(line, sort_keys=True))
' "$s" "$rc" "$(( $(date +%s) - t0 ))" "$TMP/$s.out"
  [ -s "$TMP/$s.err" ] && tail -n 5 "$TMP/$s.err" >&2
done
exit $rc_all
