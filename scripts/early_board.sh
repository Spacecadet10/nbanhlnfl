#!/bin/sh
# Early board, PAPER ONLY (owner, Nov 10): grade earlier boards against the closing line, then write today's board of the
# app's sealed models against the first DraftKings lines. Reads copies of the live stores; writes only
# <data>/early-board/. No Telegram, no ticket, no change to the two-hour forecasts. Scheduled by
# scripts/launchd/com.nba-predictor.early-board.plist (11:00 and 23:30 Toronto).
set -u
LIVE=/Users/andry/nba-predictor-delivery
ROOT=/Users/andry/Desktop/nba-predictor-sprint1
PY="$LIVE/backend/.venv/bin/python"
cd "$LIVE" || exit 1
export PYTHONPATH="$LIVE/backend" PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=4
echo "== $(date -u +%Y-%m-%dT%H:%M:%SZ) early board at $(git rev-parse --short HEAD)"
"$PY" scripts/early_board.py --root "$ROOT" --grade; graded=$?
"$PY" scripts/early_board.py --root "$ROOT"; boarded=$?
[ "$graded" -eq 0 ] && [ "$boarded" -eq 0 ]
