#!/bin/bash
# Scheduled autoresearch EVALUATION (free), all five sports: re-scores existing commits that lack a record. `prepare.py daily`
# never starts an agent and never spends money. Opt-in: refuses (exit 78) unless the switch file exists.
#
# One guarded step per sport (app.learning_run: one heavy job per machine, nice, wall and memory limits), so a sport whose own
# games are near is skipped without stopping the others:
#   - own-sport window: nba/nhl/ncaab/nfl no start within 2.5 h before a tip-off or 4 h after one that is not final;
#     soccer only within 0.75 h before a kick-off (see docs/AUTORESEARCH.md "Five-sport schedule" for the Oct-Dec evidence);
#   - global forecasting-load guard: no start within 0.5 h before ANY sport's tip-off;
#   - budgets: AUTORESEARCH_TRIALS (3) trials and AUTORESEARCH_MINUTES (12) minutes per sport, AUTORESEARCH_TOTAL_MINUTES (100)
#     for the whole run (a sport starts only if >= 6 minutes remain; the run ends before adaptive-train's 09:30 start from 06:00).
# Resumable queue: prepare.py only scores commits without a record, so work left by a refused/timed-out sport is picked up the
# next day; sports run in order of their last success (never first), so a refused sport goes first next time.
# Every sport prints one line `autoresearch-daily: sport=<s> outcome=<o> rc=<n> wall_s=<n> reason=<...>` with outcome one of
# evaluated | no_candidates | refused | failed | skipped. prepare.py may state its own outcome with a line
# `sport-outcome: <word>`; otherwise rc 0 is `evaluated` (`no_candidates` when its summary reports "trials": 0).
# State (read by the status page): $AUTORESEARCH_HOME/daily-state.tsv, one row per sport:
#   sport <TAB> last_attempt_utc <TAB> last_outcome <TAB> last_success_utc <TAB> last_reason
# Exit: 0, or 1 if any sport failed, or 75 if every sport was refused.
set -u
HOME_AR="${AUTORESEARCH_HOME:-$HOME/autoresearch-sports}"
SWITCH="$HOME_AR/SCHEDULE_DAILY_ENABLED"
if [ ! -f "$SWITCH" ]; then
  echo "refused: switch file $SWITCH is absent (opt-in schedule is off)"
  exit 78
fi
PREPARE="${AUTORESEARCH_PREPARE:-$HOME_AR/workspace/prepare.py}"
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
field() { awk -F'\t' -v s="$1" -v n="$2" '$1==s {print $n}' "$STATE" 2>/dev/null | head -1; }

ORDER=$(i=0; for s in $SPORTS; do i=$((i + 1)); ls=$(field "$s" 4); echo "${ls:-0} $i $s"; done | sort | awk '{print $3}')
declare -a ROWS=()
T0=$(date +%s); NRUN=0; NREF=0; NFAIL=0; N=0
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT

for s in $ORDER; do
  N=$((N + 1))
  at=$(now); remaining=$(( TOTAL_S - ($(date +%s) - T0) )); rc=0; wall=0; reason=""
  if [ "$remaining" -lt "$MIN_START_S" ]; then
    outcome=skipped; reason="total wall cap reached (${remaining}s left)"
  else
    mins=$(( remaining / 60 - 4 )); [ "$mins" -gt "$PER_MIN" ] && mins=$PER_MIN; [ "$mins" -lt 1 ] && mins=1
    if [ "$s" = soccer ]; then win=(--defer-hours 0.75 --lookback-hours 0); else win=(--defer-hours 2.5); fi
    t1=$(date +%s)
    "${LEARNING_PYTHON:-python3}" -m app.learning_run --name "autoresearch-daily-$s" --max-minutes $((mins + 4)) --max-rss-gb 6 \
      --defer-sports "$s" "${win[@]}" --global-sports "$ALL" --global-hours 0.5 -- \
      "${AUTORESEARCH_PYTHON:-python3}" "$PREPARE" daily --sport "$s" --max-trials "$TRIALS" --max-minutes "$mins" \
      --timeout "$TIMEOUT" >"$tmp" 2>&1
    rc=$?; wall=$(( $(date +%s) - t1 )); cat "$tmp"
    stated=$(sed -n 's/^sport-outcome: *\([a-z_]*\).*/\1/p' "$tmp" | tail -1)
    case "$rc" in
      0) if [ -n "$stated" ]; then outcome=$stated
         elif grep -q '"trials": 0' "$tmp"; then outcome=no_candidates; else outcome=evaluated; fi ;;
      75) outcome=refused ;;
      78) outcome=skipped; reason="prepare.py refused to run this sport" ;;
      121|122|123) outcome=failed; reason="guard killed the run (rc $rc: wall-clock or memory limit)" ;;
      *) outcome=failed; reason="rc $rc" ;;
    esac
    [ "$outcome" = refused ] && reason="guard refused (lock held, game window or forecasting load; see learning-runs.jsonl)"
  fi
  case "$outcome" in evaluated|no_candidates) NRUN=$((NRUN + 1)); ok=$at ;; *) ok=$(field "$s" 4) ;; esac
  [ "$outcome" = refused ] && NREF=$((NREF + 1)); [ "$outcome" = failed ] && NFAIL=$((NFAIL + 1))
  echo "autoresearch-daily: sport=$s outcome=$outcome rc=$rc wall_s=$wall reason=${reason:--}"
  ROWS+=("$s"$'\t'"$at"$'\t'"$outcome"$'\t'"${ok:-}"$'\t'"${reason:--}")
done

# merge this run's rows into the state file (sports not in this run keep their rows); atomic replace
{ k=0; while [ "$k" -lt "${#ROWS[@]}" ]; do echo "${ROWS[$k]}"; k=$((k + 1)); done
  if [ -f "$STATE" ]; then awk -F'\t' -v keep=" $(echo $ORDER) " 'index(keep, " " $1 " ") == 0' "$STATE"; fi
} > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
echo "autoresearch-daily: summary sports=$N scored=$NRUN refused=$NREF failed=$NFAIL total_wall_s=$(( $(date +%s) - T0 ))"
[ "$NFAIL" -gt 0 ] && exit 1
[ "$NREF" -eq "$N" ] && exit 75
exit 0
