#!/bin/sh
# Hourly live soccer cycle from the pinned delivery release (docs/SOCCER_LIVE_PLAN.md "Activation").
# 1. Refresh ESPN fixtures/results for the last 3 and next 7 days: 7 club competitions (Europa League as input only) and the
#    national-team competitions (app/intl_live.py).
# 2. Before 06:00 UTC: forecast today's matches; a refusal is recorded and alerted by the ticket health check.
# 3. Look ahead 7 days: each day's matches are forecast once their inputs are settled (else that day waits quietly).
# 4. The same for the goal / corner market families (step 4 below).
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
for i in 1 2 3 4 5 6 7; do              # national teams publish days ahead once their teams' earlier games are settled
  py live-forecast --day "$(date -u -v+${i}d +%Y-%m-%d)" --if-ready || status=1
done
# 4. Goal / corner market forecasts (docs/SOCCER_MARKETS_PLAN.md): only families with a tested, pinned model; a refused
#    family records its check and never affects the 1X2 forecasts above.
if [ "$(date -u +%H)" -lt 6 ]; then py markets-forecast || status=1; fi
for i in 1 2 3 4 5 6 7; do
  py markets-forecast --day "$(date -u -v+${i}d +%Y-%m-%d)" --if-ready || status=1
done
exit $status
