#!/bin/sh
# The ticket helper (`python -m app.tickets`) from the pinned release, never from the development checkout
# (docs/TICKETS.md "Guarded release"). Arguments pass through, e.g. `sh scripts/release_tickets.sh preflight --notify`.
# The helper refuses (exit 1, "release guard") unless the release checkout is clean and the production receipt ledger and
# credentials below exist. Keep these values identical to scripts/launchd/com.nba-predictor.telegram.plist.
set -eu
LIVE=/Users/andry/nba-predictor-delivery
MAIN=/Users/andry/Desktop/nba-predictor-sprint1
cd "$LIVE/backend"
exec env NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT="$LIVE" NBA_DATA_DIR="$MAIN/data" \
  NBA_TELEGRAM_LEDGER="$MAIN/.telegram-delivery.sqlite" NBA_TELEGRAM_CREDENTIALS="$MAIN/.env.telegram" \
  PYTHONDONTWRITEBYTECODE=1 "$LIVE/backend/.venv/bin/python" -m app.tickets "$@"
