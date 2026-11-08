#!/bin/sh
# Hourly live soccer cycle from the pinned delivery release (docs/SOCCER_LIVE_PLAN.md "Activation").
# 1. Refresh ESPN fixtures/results for the last 3 and next 7 days: 7 club competitions (Europa League as input only) and the
#    national-team competitions (app/intl_live.py).
# 2. Before 06:00 UTC: forecast today's matches; a refusal is recorded and alerted by the ticket health check.
# 3. Look ahead: forecast tomorrow's matches as soon as every match before tomorrow's cutoff is settled (else wait quietly).
# Every step refuses from a dirty or development checkout (NBA_RELEASE_GUARD; app/release.py).
set -u
LIVE=/Users/andry/nba-predictor-delivery
MAIN=/Users/andry/Desktop/nba-predictor-sprint1
cd "$LIVE/backend" || exit 1
py() { env NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT="$LIVE" NBA_DATA_DIR="$MAIN/data" PYTHONDONTWRITEBYTECODE=1 \
  OMP_NUM_THREADS=1 "$LIVE/backend/.venv/bin/python" -m app.cli soccer "$@"; }
status=0
py live-refresh || status=1
if [ "$(date -u +%H)" -lt 6 ]; then py live-forecast || status=1; fi
py live-forecast --day "$(date -u -v+1d +%Y-%m-%d)" --if-ready || status=1
exit $status
