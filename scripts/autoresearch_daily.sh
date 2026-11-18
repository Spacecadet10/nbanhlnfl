#!/bin/bash
# Scheduled autoresearch EVALUATION (free): re-scores existing commits that lack a record. `prepare.py daily` never starts
# an agent and never spends money. Opt-in: refuses (exit 78) unless the switch file exists. Runs through the learning-run
# guard (one heavy job per machine, nice, wall and memory limits, no start within 2.5 h of an NBA/NHL/NCAAB tip-off).
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
exec "${LEARNING_PYTHON:-python3}" -m app.learning_run --name autoresearch-daily --max-minutes 75 --max-rss-gb 6 -- \
  "${AUTORESEARCH_PYTHON:-python3}" "$PREPARE" daily --sport nba --sport nhl --max-trials 4 --max-minutes 60 --timeout 900
