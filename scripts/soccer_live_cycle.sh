#!/bin/sh
# Hourly live soccer cycle from the pinned delivery release (docs/SOCCER_LIVE_PLAN.md "Activation").
# 1. Refresh ESPN fixtures/results for the last 3 and next 7 days: 7 club competitions (Europa League as input only) and the
#    national-team competitions (app/intl_live.py).
# 2. Record due 90-minute result forecasts inside two hours of kickoff, preserving the models' frozen input cutoff.
# 3. Check both before and after the refresh, which can take several minutes.
# 4. The same for the goal / corner market families (step 4 below).
# Every step refuses from a dirty or development checkout (NBA_RELEASE_GUARD; app/release.py).
set -u
LIVE=/Users/andry/nba-predictor-delivery
MAIN=/Users/andry/Desktop/nbanhlnfl
cd "$LIVE/backend" || exit 1
py() { env NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT="$LIVE" NBA_DATA_DIR="$MAIN/data" PYTHONDONTWRITEBYTECODE=1 \
  OMP_NUM_THREADS=1 "$LIVE/backend/.venv/bin/python" -m app.cli soccer "$@"; }
status=0
py pregame-forecast || true
py live-refresh || status=1
py pregame-forecast || status=1
# 4. Goal / corner market forecasts (docs/SOCCER_MARKETS_PLAN.md): only families with a tested, pinned model; a refused
#    family records its check and never affects the 1X2 forecasts above.
if [ "$(date -u +%H)" -lt 6 ]; then py markets-forecast || status=1; fi
for i in 1 2 3 4 5 6 7; do
  py markets-forecast --day "$(date -u -v+${i}d +%Y-%m-%d)" --if-ready || status=1
done
# 5. Bet365 double chance quotes from ESPN (app/bet365_dc.py, owner request Nov 11): one request per club match kicking
#    off within 48 h, at most every 2 h, 24 a run; saved write-once under data/bet365-dc. Never changes the cycle status.
env NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT="$LIVE" NBA_DATA_DIR="$MAIN/data" PYTHONDONTWRITEBYTECODE=1 \
  "$LIVE/backend/.venv/bin/python" -m app.bet365_dc fetch || true
exit $status
