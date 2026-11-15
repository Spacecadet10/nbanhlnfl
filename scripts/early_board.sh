#!/bin/sh
# Early board, PAPER ONLY (owner, Nov 10): grade earlier boards against the closing line, then write today's board of the
# app's sealed models against the first DraftKings lines. Reads copies of the live stores; writes only
# <data>/early-board/. No Telegram, no ticket, no change to the two-hour forecasts. Scheduled by
# scripts/launchd/com.nba-predictor.early-board.plist at 11:00, 18:00, 20:30 and 23:30 Toronto: first lines appear mostly
# 17:00-23:00 the evening before, and grading keeps each game's EARLIEST board. Grading runs once a day (the 11:00 run, or its late catch-up before 18:00).
set -u
LIVE=/Users/andry/nba-predictor-delivery
ROOT=/Users/andry/Desktop/nba-predictor-sprint1
PY="$LIVE/backend/.venv/bin/python"
cd "$LIVE" || exit 1
export PYTHONPATH="$LIVE/backend" PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=4
echo "== $(date -u +%Y-%m-%dT%H:%M:%SZ) early board at $(git rev-parse --short HEAD)"
graded=0
hour=$(date +%H)   # 11:00 run, or a run launchd delivers late after sleep before 18:00
if [ "$hour" -ge 11 ] && [ "$hour" -lt 18 ]; then "$PY" scripts/early_board.py --root "$ROOT" --grade; graded=$?; fi
"$PY" scripts/early_board.py --root "$ROOT"; boarded=$?
[ "$graded" -eq 0 ] && [ "$boarded" -eq 0 ]
