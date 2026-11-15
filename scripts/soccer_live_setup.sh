#!/bin/sh
# One-time creation of the live soccer store from the pinned delivery release (docs/SOCCER_LIVE_PLAN.md "Activation").
# Creates <MAIN>/data/soccer.sqlite (new file; no existing store is touched), imports the openfootball history at the
# pins the sealed model was evaluated on, backfills the ESPN 2026-27 season, then runs one live cycle.
set -eu
LIVE=/Users/andry/nba-predictor-delivery
MAIN=/Users/andry/Desktop/nba-predictor-sprint1
test ! -e "$MAIN/data/soccer.sqlite" || { echo "refused: $MAIN/data/soccer.sqlite already exists"; exit 1; }
cd "$LIVE/backend"
py() { env NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT="$LIVE" NBA_DATA_DIR="$MAIN/data" PYTHONDONTWRITEBYTECODE=1 \
  OMP_NUM_THREADS=1 "$LIVE/backend/.venv/bin/python" -m app.cli soccer "$@"; }
COMPS="--competition eng.1 --competition esp.1 --competition ger.1 --competition ita.1 --competition fra.1 --competition uefa.champions --competition uefa.europa"
py migrate
py history-download --pin football.json=e6744429ee395bc86f247348c6184bb08d4eb361 --pin champions-league=abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef
py ingest $COMPS --from 20260801 --to 20260930
py ingest $COMPS --from 20261001 --to "$(date -u -v+7d +%Y%m%d)"
# National teams: the pinned CC0 history the international model was sealed on, then ESPN since that history ends.
env NBA_DATA_DIR="$MAIN/data" PYTHONDONTWRITEBYTECODE=1 "$LIVE/backend/.venv/bin/python" -m app.intl_eval download \
  --commit b0948cb558c927c357db4067eac5b37b96d0e4bb
INTL="--competition uefa.nations --competition concacaf.nations.league --competition fifa.friendly --competition caf.nations_qual --competition uefa.euroq --competition afc.cupq --competition fifa.worldq.uefa --competition fifa.worldq.conmebol --competition fifa.worldq.concacaf --competition fifa.worldq.afc --competition fifa.worldq.caf --competition fifa.worldq.ofc --competition uefa.euro --competition conmebol.america --competition caf.nations --competition afc.asian.cup --competition concacaf.gold --competition fifa.world"
py ingest $INTL --from 20261007 --to "$(date -u -v+7d +%Y%m%d)"
sh "$LIVE/scripts/soccer_live_cycle.sh"
