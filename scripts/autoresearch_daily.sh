#!/bin/bash
# Scheduled autoresearch EVALUATION (free), all five sports: re-scores existing commits that lack a record. `prepare.py daily`
# never starts an agent and never spends money. Opt-in: refuses (exit 78) unless the switch file exists.
#
# One guarded step per sport (app.learning_run: one heavy job per machine, nice, wall and memory limits), so a sport whose own
# games are near is skipped without stopping the others:
#   - own-sport window: nba/nhl/ncaab/nfl no start within 2.5 h before a tip-off or 4 h after one that is not final;
#     soccer only within 0.75 h before a kick-off (see docs/AUTORESEARCH.md "Five-sport schedule" for the Oct-Dec evidence);
#   - global live-load guard: no start within 0.5 h before ANY sport's tip-off, nor while ANY sport's game is in progress
#     (started within AUTORESEARCH_GLOBAL_LOOKBACK_HOURS, default 4, and not final) -- 2026-11-23: live jobs run during games;
#   - budgets: AUTORESEARCH_TRIALS (3) trials and AUTORESEARCH_MINUTES (12) minutes per sport, AUTORESEARCH_TOTAL_MINUTES (100)
#     for the whole run (a sport starts only if >= 6 minutes remain; the run ends before adaptive-train's 09:30 start from 06:00).
# Resumable queue: prepare.py only scores commits without a record, so work left by a refused/timed-out sport is picked up the
# next day; sports run in order of their last success (never first), so a refused sport goes first next time.
# Every sport prints one line `autoresearch-daily: sport=<s> outcome=<o> rc=<n> wall_s=<n> reason=<...>` with outcome one of
# evaluated | no_candidates | refused | failed | skipped. prepare.py may state its own outcome with a line
# `sport-outcome: <sport> <combined> v3=.. [v2=..]`; without that line nothing counts as evaluated (rc 0 alone is `failed`).
# last_success is set ONLY by `evaluated`; `no_candidates` is kept as last_outcome and is not counted as scored.
# State (read by the status page, columns 1-5 by position): $AUTORESEARCH_HOME/daily-state.tsv, one row per sport:
#   sport <TAB> last_attempt_utc <TAB> last_outcome <TAB> last_success_utc <TAB> last_reason <TAB> attempts_today (<local date>:<n>)
# Same-day retries: launchd starts this at 06:00, 11:10 and 13:10 local. A sport whose last attempt was on today's LOCAL date is run
# again only if that outcome was `refused` (game window / forecasting guard, or stated by the evaluator) or `skipped` for the total wall cap; evaluated,
# no_candidates, failed and any other skip are final for the day and print `outcome=done_today` (row untouched; `failed` is never
# retried, so a broken sport cannot loop). At most 3 attempts per sport per local day (a wall-cap skip is not an attempt).
# Exit: 0, or 1 if any sport failed, or 75 if every sport that actually ran was refused (and at least one ran).
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
TRIALS="${AUTORESEARCH_TRIALS:-3}"
PER_MIN="${AUTORESEARCH_MINUTES:-12}"
TIMEOUT="${AUTORESEARCH_TRIAL_TIMEOUT:-600}"
TOTAL_S="${AUTORESEARCH_TOTAL_SECONDS:-$(( ${AUTORESEARCH_TOTAL_MINUTES:-100} * 60 ))}"
MIN_START_S="${AUTORESEARCH_MIN_START_SECONDS:-360}"
STATE="$HOME_AR/daily-state.tsv"
ALL="nba,nhl,ncaab,nfl,soccer"
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
today=$(date +%F)
# local calendar date of a stored UTC stamp (TZ env or the system zone); empty if unparsable
local_date() { python3 -c 'import sys; from datetime import datetime, timezone
print(datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d"))' "$1" 2>/dev/null; }
field() { awk -F'\t' -v s="$1" -v n="$2" '$1==s {print $n}' "$STATE" 2>/dev/null | head -1; }

ORDER=$(i=0; for s in $SPORTS; do i=$((i + 1)); ls=$(field "$s" 4); echo "${ls:-0} $i $s"; done | sort | awk '{print $3}')
declare -a ROWS=()
RAN=""
T0=$(date +%s); NRUN=0; NNC=0; NREF=0; NFAIL=0; N=0; NDONE=0
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT

for s in $ORDER; do
  # same-day retry gate: attempts today, from column 6 (`<date>:<n>`), else 1 for a legacy row whose last attempt was today
  last_at=$(field "$s" 2); last_out=$(field "$s" 3); last_reason=$(field "$s" 5); cnt=$(field "$s" 6)
  prev=0
  if [ "${cnt%%:*}" = "$today" ]; then prev=${cnt##*:}
  elif [ -n "$last_at" ] && [ "$(local_date "$last_at")" = "$today" ]; then prev=1; fi
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  done_why=""
  if [ "$prev" -ge 3 ]; then done_why="attempt cap (3) reached today"
  elif [ "$prev" -gt 0 ] && [ -n "$last_at" ] && [ "$(local_date "$last_at")" = "$today" ]; then
    case "$last_out" in
      refused) ;;
      skipped) case "$last_reason" in "total wall cap reached"*) ;; *) done_why="$last_out" ;; esac ;;
      *) done_why="$last_out" ;;
    esac
  fi
  if [ -n "$done_why" ]; then
    NDONE=$((NDONE + 1))
    echo "autoresearch-daily: sport=$s outcome=done_today reason=$done_why at $last_at"
    continue
  fi
  N=$((N + 1)); RAN="$RAN $s"
  at=$(now); remaining=$(( TOTAL_S - ($(date +%s) - T0) )); rc=0; wall=0; reason=""
  if [ "$remaining" -lt "$MIN_START_S" ]; then
    outcome=skipped; reason="total wall cap reached (${remaining}s left)"; att=$prev      # never started: not an attempt
  else
    mins=$(( remaining / 60 - 4 )); [ "$mins" -gt "$PER_MIN" ] && mins=$PER_MIN; [ "$mins" -lt 1 ] && mins=1
    if [ "$s" = soccer ]; then win=(--defer-hours 0.75 --lookback-hours 0); else win=(--defer-hours 2.5); fi
    att=$((prev + 1)); t1=$(date +%s)
    "${LEARNING_PYTHON:-python3}" -m app.learning_run --name "autoresearch-daily-$s" --max-minutes $((mins + 4)) --max-rss-gb 6 \
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
  fi
  # last_success only for `evaluated`; `no_candidates` is recorded as last_outcome and never counts as a success
  case "$outcome" in evaluated) NRUN=$((NRUN + 1)); ok=$at ;; no_candidates) NNC=$((NNC + 1)); ok=$(field "$s" 4) ;; *) ok=$(field "$s" 4) ;; esac
  [ "$outcome" = refused ] && NREF=$((NREF + 1)); [ "$outcome" = failed ] && NFAIL=$((NFAIL + 1))
  echo "autoresearch-daily: sport=$s outcome=$outcome rc=$rc wall_s=$wall reason=${reason:--}"
  ROWS+=("$s"$'\t'"$at"$'\t'"$outcome"$'\t'"${ok:-}"$'\t'"${reason:--}"$'\t'"$today:$att")
done

# merge this run's rows into the state file (sports not in this run keep their rows); atomic replace
{ k=0; while [ "$k" -lt "${#ROWS[@]}" ]; do echo "${ROWS[$k]}"; k=$((k + 1)); done
  if [ -f "$STATE" ]; then awk -F'\t' -v keep="$RAN " 'index(keep, " " $1 " ") == 0' "$STATE"; fi
} > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
echo "autoresearch-daily: summary sports=$N scored=$NRUN no_candidates=$NNC refused=$NREF failed=$NFAIL done_today=$NDONE total_wall_s=$(( $(date +%s) - T0 ))"
[ "$NFAIL" -gt 0 ] && exit 1
[ "$N" -gt 0 ] && [ "$NREF" -eq "$N" ] && exit 75
exit 0
