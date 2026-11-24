#!/bin/bash
# Scheduled autoresearch EVALUATION (free), all five sports: re-scores existing commits that lack a record. `prepare.py daily`
# never starts an agent and never spends money. Opt-in: refuses (exit 78) unless the switch file exists.
#
# Guarded, resumable chunks (app.learning_run: one heavy job per machine, nice, wall and memory limits), so a sport whose own
# games are near is skipped without stopping the others:
#   - own-sport window: nba/nhl/ncaab/nfl no start within 2.5 h before a tip-off or 4 h after one that is not final;
#     soccer only within 0.75 h before a kick-off (see docs/AUTORESEARCH.md "Five-sport schedule" for the Oct-Dec evidence);
#   - global live-load guard: no start within 0.5 h before ANY sport's tip-off, nor while ANY sport's game is in progress
#     (started within AUTORESEARCH_GLOBAL_LOOKBACK_HOURS, default 4, and not final) -- 2026-11-23: live jobs run during games;
#   - no item or trial count cap (AUTORESEARCH_TRIALS default 100000); a chunk is bounded by time: soft AUTORESEARCH_MINUTES (15)
#     passed as --max-minutes, guard hard limit = soft + AUTORESEARCH_HARD_MARGIN_MINUTES (30; NBA/NHL/NCAAB items are atomic, time is
#     only checked between items); per-trial --timeout; --max-rss-gb 6;
#   - the per-RUN window: AUTORESEARCH_TOTAL_MINUTES (170), shortened to AUTORESEARCH_STOP_AT (local HH:MM, default 08:55, empty = off)
#     when the run starts before it on the same local date (release the heavy lock before adaptive-train's 09:30 start). A chunk
#     starts only if >= AUTORESEARCH_MIN_START_SECONDS (360) remain.
# Resumable queue: prepare.py only scores what has no record (Soccer's walk stores a .partial.json), so work left by a chunk is picked
# up by the next one. Round-robin passes: pass 1 runs one chunk for every sport that is not done today (longest since last success
# first); further passes run one more chunk for each sport whose outcome is `pending`, until none is pending or the window ends.
# Every chunk goes through app.learning_run again (lock, game windows and live-forecast priority are re-checked each time).
# Every chunk prints `autoresearch-daily: sport=<s> outcome=<o> rc=<n> wall_s=<n> reason=<...>` with outcome one of
# evaluated | pending | no_candidates | refused | failed | skipped. prepare.py may state its own outcome with a line
# `sport-outcome: <sport> <combined> v3=.. [v2=..]`; without that line nothing counts as evaluated (rc 0 alone is `failed`).
# `pending` = work remains and progress is stored. Decided from the report file named on the evaluator's last stdout line
# (`sports.<s>.queue.remaining > 0`; nba/nhl `v2.stopped` = trial/time budget reached); without a readable report, from the reason
# (skipped: "budget reached", "candidates pending", "incumbent scoring incomplete"; evaluated: "; N queued"). A chunk that ends
# pending although nothing advanced since the previous chunk is `failed` (stuck), so nothing can loop. A later chunk refused by the
# guard leaves the sport `pending` (not retried again in this run).
# last_success is set ONLY by a complete `evaluated`; `no_candidates` is kept as last_outcome and is not counted as scored.
# State (read by the status page, columns 1-5 by position): $AUTORESEARCH_HOME/daily-state.tsv, one row per sport, rewritten after
# every chunk (atomic merge):
#   sport <TAB> last_attempt_utc <TAB> last_outcome <TAB> last_success_utc <TAB> last_reason <TAB> chunks_today (<local date>:<n>)
# Same-day retries: launchd starts this at 04:00, 05:00, 06:00 and 07:30 local. A sport whose last attempt was on today's LOCAL date is
# run again if that outcome was `refused`, `pending`, or `skipped` for the wall cap / a budget; evaluated (complete), no_candidates,
# failed and any other skip are final for the day and print `outcome=done_today` (row untouched; `failed` is never retried, so a
# broken sport cannot loop). Column 6 only counts guarded chunks started today; there is no attempt cap.
# Exit: 0, or 1 if any sport failed, or 75 if every sport that actually ran was refused (and at least one ran). `pending` alone is 0.
set -u
HOME_AR="${AUTORESEARCH_HOME:-$HOME/autoresearch-sports}"
SWITCH="$HOME_AR/SCHEDULE_DAILY_ENABLED"
if [ ! -f "$SWITCH" ]; then
  echo "refused: switch file $SWITCH is absent (opt-in schedule is off)"
  exit 78
fi
PREPARE="${AUTORESEARCH_PREPARE:-$HOME_AR/workspace/prepare.py}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# The study runs through the pinned runner (code, interpreter, packages and snapshot of PIN.json, whatever this checkout is), always for
# ~/autoresearch-sports. Another home or python is for tests only: neither may reach the real home or its prepare.py.
REAL="$HOME/autoresearch-sports"
RUN=("${LEARNING_PYTHON:-python3}" -I "$HERE/research_runner.py")
if [ "$HOME_AR" -ef "$REAL" ] || [ "$PREPARE" -ef "$REAL/workspace/prepare.py" ]; then
  if [ -n "${AUTORESEARCH_PYTHON:-}" ] || ! [ "$HOME_AR" -ef "$REAL" ] || ! [ "$PREPARE" -ef "$REAL/workspace/prepare.py" ]; then
    echo "refused: AUTORESEARCH_PYTHON, AUTORESEARCH_HOME or AUTORESEARCH_PREPARE would bypass the pinned runner"
    exit 78
  fi
elif [ -n "${AUTORESEARCH_PYTHON:-}" ]; then
  RUN=("$AUTORESEARCH_PYTHON")
else
  echo "refused: $HOME_AR is not the research home the pinned runner serves"
  exit 78
fi
BACKEND="${LEARNING_BACKEND:-$(cd "$(dirname "$0")/.." && pwd)/backend}"
cd "$BACKEND" || exit 78
SPORTS="${AUTORESEARCH_SPORTS:-nba nhl ncaab nfl soccer}"
TRIALS="${AUTORESEARCH_TRIALS:-100000}"          # no item/trial cap (never 4: the pinned CLI maps exactly 4 to its default of 6)
PER_MIN="${AUTORESEARCH_MINUTES:-15}"
MARGIN_MIN="${AUTORESEARCH_HARD_MARGIN_MINUTES:-30}"
TIMEOUT="${AUTORESEARCH_TRIAL_TIMEOUT:-600}"
TOTAL_S="${AUTORESEARCH_TOTAL_SECONDS:-$(( ${AUTORESEARCH_TOTAL_MINUTES:-170} * 60 ))}"
STOP_AT="${AUTORESEARCH_STOP_AT-08:55}"
MIN_START_S="${AUTORESEARCH_MIN_START_SECONDS:-360}"
STATE="$HOME_AR/daily-state.tsv"
ALL="nba,nhl,ncaab,nfl,soccer"
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
today=$(date +%F)
# local calendar date of a stored UTC stamp (TZ env or the system zone); empty if unparsable
local_date() { python3 -c 'import sys; from datetime import datetime, timezone
print(datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d"))' "$1" 2>/dev/null; }
field() { awk -F'\t' -v s="$1" -v n="$2" '$1==s {print $n}' "$STATE" 2>/dev/null | head -1; }
# seconds from now until local HH:MM today; empty when it is not a valid HH:MM or has already passed
secs_until() { python3 -c 'import sys; from datetime import datetime
h, m = sys.argv[1].split(":"); n = datetime.now()
d = (n.replace(hour=int(h), minute=int(m), second=0, microsecond=0) - n).total_seconds()
print(int(d) if d > 0 else "")' "$1" 2>/dev/null; }
# per-sport values live in variables NAME_<sport> (bash 3.2: no associative arrays)
setv() { printf -v "$1_$2" '%s' "$3"; }
getv() { local n="$1_$2"; printf '%s' "${!n}"; }
# What the chunk's report file says (the evaluator's last stdout line is JSON naming it): `have|remain|adv|sig|next_item`.
# have=0 when no report is readable; remain=1 when v3 queue.remaining > 0 or the v2 queue stopped on a budget; adv=1 when v2 ran trials;
# sig = queue.done + size/mtime of the v3 items/*.partial.json files (a change means progress since the previous chunk).
probe() { python3 - "$1" "$2" <<'PY' 2>/dev/null || echo "0|0|0||"
import glob, hashlib, json, os, sys
tmp, s = sys.argv[1:3]
rf = None
for ln in reversed(open(tmp, errors="replace").read().splitlines()):
    if ln.strip().startswith("{"):
        try:
            d = json.loads(ln)
            rf = d["report_file"]
            rep = json.load(open(rf))
        except Exception:
            rf = None
        break
if rf is None or not isinstance(rep, dict):
    print("0|0|0||"); sys.exit()
sp = (rep.get("sports") or {}).get(s) or {}
q = sp.get("queue") or {}
v2 = rep.get("v2") or {}
tr = v2.get("trials")
adv = 1 if (len(tr) if isinstance(tr, list) else (tr or 0)) else 0
remain = 1 if ((q.get("remaining") or 0) > 0 or (s in ("nba", "nhl") and v2.get("stopped") in ("trial budget reached", "time budget reached"))) else 0
parts = []
pid = sp.get("protocol_id")
if pid:
    exp = os.path.dirname(os.path.dirname(rf))
    for f in sorted(glob.glob(os.path.join(exp, "v3", pid, s, "items", "*.partial.json"))):
        try:
            st = os.stat(f); parts.append((os.path.basename(f), st.st_size, st.st_mtime_ns))
        except OSError:
            pass
sig = hashlib.md5(repr((q.get("done"), parts)).encode()).hexdigest()
print(f"1|{remain}|{adv}|{sig}|{q.get('next_item') or ''}")
PY
}
# work remains per the reason text alone (no readable report)
remains_by_reason() {   # $1 outcome, $2 reason
  case "$1" in
    skipped) case "$2" in *"budget reached"*|*"candidates pending"*|*"incumbent scoring incomplete"*) return 0 ;; esac ;;
    evaluated) local n; n=$(printf '%s' "$2" | sed -n 's/.*; \([0-9][0-9]*\) queued.*/\1/p'); [ "${n:-0}" -gt 0 ] && return 0 ;;
  esac
  return 1
}

# the run window: TOTAL, or until STOP_AT when the run starts before it on this local date
WIN_S=$TOTAL_S
if [ -n "$STOP_AT" ]; then
  u=$(secs_until "$STOP_AT"); case "$u" in ''|*[!0-9]*) ;; *) [ "$u" -lt "$WIN_S" ] && WIN_S=$u ;; esac
fi
T0=$(date +%s)
window_left() { echo $(( WIN_S - ($(date +%s) - T0) )); }
echo "autoresearch-daily: window_s=$WIN_S total_s=$TOTAL_S stop_at=${STOP_AT:--}"

ORDER=$(i=0; for s in $SPORTS; do i=$((i + 1)); ls=$(field "$s" 4); echo "${ls:-0} $i $s"; done | sort | awk '{print $3}')
RAN=""; N=0; NDONE=0
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT

# merge this run's rows into the state file (sports not in this run keep their rows); atomic replace; called after every chunk
flush_state() {
  { for s in $RAN; do getv ROW "$s"; echo; done
    if [ -f "$STATE" ]; then awk -F'\t' -v keep="$RAN " 'index(keep, " " $1 " ") == 0' "$STATE"; fi
  } > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}
set_row() {   # $1 sport; uses at, OUT_, LS_, reason, CNT_
  local s=$1 out ok
  out=$(getv OUT "$s"); ok=$(getv LS "$s"); [ "$out" = evaluated ] && ok=$at
  setv LS "$s" "$ok"
  setv ROW "$s" "$s"$'\t'"$at"$'\t'"$out"$'\t'"${ok:-}"$'\t'"${reason:--}"$'\t'"$today:$(getv CNT "$s")"
  case " $RAN " in *" $s "*) ;; *) RAN="$RAN $s" ;; esac
  flush_state
}

# one guarded chunk for sport $1
run_chunk() {
  local s=$1 mins remaining win rc wall t1 stated outcome pr have remain adv sig next line prevn
  at=$(now); remaining=$(window_left); reason=""
  mins=$(( remaining / 60 - 4 )); [ "$mins" -gt "$PER_MIN" ] && mins=$PER_MIN; [ "$mins" -lt 1 ] && mins=1
  if [ "$s" = soccer ]; then win=(--defer-hours 0.75 --lookback-hours 0); else win=(--defer-hours 2.5); fi
  setv CNT "$s" $(( $(getv CNT "$s") + 1 )); prevn=$(getv NCH "$s"); setv NCH "$s" $((prevn + 1)); t1=$(date +%s)
  "${LEARNING_PYTHON:-python3}" -m app.learning_run --name "autoresearch-daily-$s" --max-minutes $((mins + MARGIN_MIN)) --max-rss-gb 6 \
    --defer-sports "$s" "${win[@]}" --global-sports "$ALL" --global-hours 0.5 --global-lookback-hours "${AUTORESEARCH_GLOBAL_LOOKBACK_HOURS:-4}" -- \
    "${RUN[@]}" "$PREPARE" daily --sport "$s" --max-trials "$TRIALS" --max-minutes "$mins" \
    --timeout "$TIMEOUT" >"$tmp" 2>&1
  rc=$?; wall=$(( $(date +%s) - t1 )); cat "$tmp"
  # app.autoresearch prints `sport-outcome: <sport> <combined> v3=<o> [v2=<o>] [(reason)]`. The outcome is v3's own; v2 speaks only
  # when v3 had no candidate (the pinned evaluator's <combined> let a v2 success hide a v3 refusal on Nov 23).
  stated=$(awk -v s="$s" '$1 == "sport-outcome:" && $2 == s {
    a = ""; b = ""
    for (i = 4; i <= 5 && i <= NF; i++) { if ($i ~ /^v3=/) a = substr($i, 4); if ($i ~ /^v2=/) b = substr($i, 4) }
    print (a == "" ? $3 : (a == "no_candidates" && b != "" ? b : a)) }' "$tmp" | tail -1)
  line=$(grep "^sport-outcome: $s " "$tmp" | tail -1)
  case "$rc" in
    75|121|122|123) outcome=""; ;;                                  # the guard's own codes win over anything the child printed
    *) outcome=$stated ;;
  esac
  case "$outcome" in evaluated|no_candidates|refused|failed|skipped) ;; *) outcome="" ;; esac
  if [ -z "$outcome" ]; then
    case "$rc" in
      0) outcome=failed; reason="no sport-outcome line (rc 0 alone is not a result)" ;;
      75) outcome=refused ;;
      78) outcome=skipped; reason="prepare.py refused to run this sport" ;;
      121|122|123) outcome=failed; reason="guard killed the run (rc $rc: wall-clock or memory limit)" ;;
      *) outcome=failed; reason="rc $rc" ;;
    esac
  else
    reason=$(sed -n "s/^sport-outcome: $s [a-z_]* [^(]*(\(.*\))\$/\1/p" "$tmp" | tail -1)
  fi
  [ "$rc" = 75 ] && reason="guard refused (lock held, game window or forecasting load; see learning-runs.jsonl)"
  have=0; remain=0; adv=0; sig=""; next=""
  if [ "$outcome" = evaluated ] || [ "$outcome" = skipped ]; then
    IFS='|' read -r have remain adv sig next <<EOP
$(probe "$tmp" "$s")
EOP
    if [ "$have" = 1 ]; then [ "$remain" = 1 ] && outcome=pending
    elif remains_by_reason "$outcome" "$reason"; then outcome=pending; fi
    if [ "$outcome" = pending ]; then
      # no progress in a full chunk: the report's queue/partial signature (or v2 trials) did not move, or (no report) the same line twice
      if [ "$prevn" -gt 0 ] && { { [ "$have" = 1 ] && [ "$adv" = 0 ] && [ "$sig" = "$(getv SIG "$s")" ]; } || { [ "$have" = 0 ] && [ -n "$line" ] && [ "$line" = "$(getv LINE "$s")" ]; }; }; then
        outcome=failed; reason="no progress in a full chunk at ${next:-the next item} (stuck, or one item needs longer than a chunk)"
      fi
    fi
  elif [ "$rc" = 75 ] && [ "$prevn" -gt 0 ]; then
    outcome=pending; reason="guard refused a later chunk (lock held, game window or forecasting load) and work remains"; setv BLK "$s" 1
  fi
  setv SIG "$s" "$sig"; setv LINE "$s" "$line"; setv OUT "$s" "$outcome"
  echo "autoresearch-daily: sport=$s outcome=$outcome rc=$rc wall_s=$wall reason=${reason:--}"
  set_row "$s"
}

# pass 1: one chunk for every sport that is not done today
for s in $ORDER; do
  last_at=$(field "$s" 2); last_out=$(field "$s" 3); last_reason=$(field "$s" 5); cnt=$(field "$s" 6)
  prev=0
  if [ "${cnt%%:*}" = "$today" ]; then prev=${cnt##*:}
  elif [ -n "$last_at" ] && [ "$(local_date "$last_at")" = "$today" ]; then prev=1; fi
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  done_why=""
  if [ "$prev" -gt 0 ] && [ -n "$last_at" ] && [ "$(local_date "$last_at")" = "$today" ]; then
    case "$last_out" in
      refused|pending) ;;
      skipped) case "$last_reason" in "total wall cap reached"*|*"budget reached"*|*"candidates pending"*|*"incumbent scoring incomplete"*) ;; *) done_why="$last_out" ;; esac ;;
      evaluated) remains_by_reason evaluated "$last_reason" || done_why="$last_out" ;;
      *) done_why="$last_out" ;;
    esac
  fi
  if [ -n "$done_why" ]; then
    NDONE=$((NDONE + 1))
    echo "autoresearch-daily: sport=$s outcome=done_today reason=$done_why at $last_at"
    continue
  fi
  N=$((N + 1))
  setv CNT "$s" "$prev"; setv NCH "$s" 0; setv SIG "$s" ""; setv LINE "$s" ""; setv BLK "$s" ""; setv LS "$s" "$(field "$s" 4)"
  left=$(window_left)
  if [ "$left" -lt "$MIN_START_S" ]; then
    at=$(now); reason="total wall cap reached (${left}s left)"; setv OUT "$s" skipped      # never started: not a chunk
    echo "autoresearch-daily: sport=$s outcome=skipped rc=0 wall_s=0 reason=$reason"
    set_row "$s"
  else
    run_chunk "$s"
  fi
done

# further passes: one more chunk for each pending sport, until none is pending or the window ends
window_over=0
while [ "$window_over" = 0 ]; do
  anyp=0
  for s in $RAN; do
    [ "$(getv OUT "$s")" = pending ] && [ -z "$(getv BLK "$s")" ] || continue
    if [ "$(window_left)" -lt "$MIN_START_S" ]; then window_over=1; break; fi
    anyp=1; run_chunk "$s"
  done
  [ "$anyp" = 0 ] && break
done
if [ "$window_over" = 1 ]; then
  for s in $RAN; do
    if [ "$(getv OUT "$s")" = pending ] && [ -z "$(getv BLK "$s")" ]; then
      at=$(now); reason="run window ended ($(( $(date +%s) - T0 ))s of ${WIN_S}s) with work remaining: $(getv ROW "$s" | cut -f5)"
      set_row "$s"; echo "autoresearch-daily: sport=$s outcome=pending reason=$reason"
    fi
  done
fi

NRUN=0; NNC=0; NREF=0; NFAIL=0; NPEND=0
for s in $RAN; do
  case "$(getv OUT "$s")" in evaluated) NRUN=$((NRUN + 1)) ;; no_candidates) NNC=$((NNC + 1)) ;; refused) NREF=$((NREF + 1)) ;;
    failed) NFAIL=$((NFAIL + 1)) ;; pending) NPEND=$((NPEND + 1)) ;; esac
done
echo "autoresearch-daily: summary sports=$N scored=$NRUN no_candidates=$NNC refused=$NREF failed=$NFAIL done_today=$NDONE pending=$NPEND total_wall_s=$(( $(date +%s) - T0 ))"
[ "$NFAIL" -gt 0 ] && exit 1
[ "$N" -gt 0 ] && [ "$NREF" -eq "$N" ] && exit 75
exit 0
