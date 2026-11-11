#!/bin/sh
# Switches on the three-sportsbook price comparison for tickets (book-prices.patch) on the delivery release.
# Built and tested in a separate temporary checkout; the live checkout only moves by one clean fast-forward at the end,
# so the Telegram job never sees a half-applied change. Stops without changing anything live if any step fails.
# Rollback: git -C ~/nba-predictor-delivery reset --hard d444fe4
#           launchctl bootout gui/$(id -u)/com.nba-predictor.rundown-odds; rm ~/Library/LaunchAgents/com.nba-predictor.rundown-odds.plist
set -eu
LIVE="$HOME/nba-predictor-delivery"
MAIN="$HOME/Desktop/nba-predictor-sprint1"
HERE="$MAIN/docs/evidence/book-prices-2026-11-10"
BASE=d444fe4
BRANCH=book-prices-20261110
WORK="$(mktemp -d)/w"

[ -z "$(git -C "$LIVE" status --porcelain)" ] || { echo "STOP: the delivery checkout has uncommitted changes."; exit 1; }
case "$(git -C "$LIVE" rev-parse HEAD)" in $BASE*) ;; *) echo "STOP: the delivery checkout is not at $BASE."; exit 1;; esac
[ -f "$MAIN/.env.odds" ] || { echo "STOP: $MAIN/.env.odds (the price key) is missing."; exit 1; }
[ "$(shasum -a 256 "$HERE/book-prices.patch" | cut -d' ' -f1)" = "$(cat "$HERE/book-prices.patch.sha256")" ] || { echo "STOP: the patch file changed."; exit 1; }

git -C "$LIVE" branch -D "$BRANCH" >/dev/null 2>&1 || true   # left by an earlier failed attempt
git -C "$LIVE" worktree add -q -b "$BRANCH" "$WORK" "$BASE"
cleanup() { git -C "$LIVE" worktree remove --force "$WORK" 2>/dev/null || true; }
trap cleanup EXIT
git -C "$WORK" apply "$HERE/book-prices.patch"
echo "Testing (about a minute)..."
( cd "$WORK/backend" && NBA_DATA_DIR="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 "$LIVE/backend/.venv/bin/python" -m pytest \
    tests/test_rundown_odds.py tests/test_tickets.py tests/test_telegram.py tests/test_delivery_release.py tests/test_release_guard.py tests/test_soccer_markets_live.py -q -p no:cacheprovider ) \
  || { echo "STOP: tests failed. Nothing live was changed."; git -C "$LIVE" branch -D "$BRANCH" >/dev/null 2>&1 || true; exit 1; }
git -C "$WORK" add backend/app/rundown_odds.py backend/app/tickets.py backend/app/telegram_card.py \
    backend/tests/test_rundown_odds.py backend/tests/test_tickets.py scripts/launchd/com.nba-predictor.rundown-odds.plist
git -C "$WORK" commit -q -m "Compare ticket prices at three sportsbooks (TheRundown)

Stored prices of the same pick on the same line at DraftKings, FanDuel and BetMGM are read into the ticket review
snapshot and shown on the ticket picture; the app's own stored quote and every existing check are unchanged.
Written by Claude after Codex ran out of usage mid-task; not yet independently reviewed.

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQetjyUeoU1jmU1wZxNwsk"
cleanup
git -C "$LIVE" merge -q --ff-only "$BRANCH"
echo "Live code updated: $(git -C "$LIVE" rev-parse --short HEAD)"

PLIST="$HOME/Library/LaunchAgents/com.nba-predictor.rundown-odds.plist"
cp "$LIVE/scripts/launchd/com.nba-predictor.rundown-odds.plist" "$PLIST"
launchctl bootout "gui/$(id -u)/com.nba-predictor.rundown-odds" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Price check scheduled: 1:00, 4:00, 6:00, 7:30, 9:00 and 10:30 PM every day."

echo "First price check now..."
( cd "$LIVE/backend" && env NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT="$LIVE" NBA_DATA_DIR="$MAIN/data" PYTHONDONTWRITEBYTECODE=1 \
    "$LIVE/backend/.venv/bin/python" -m app.rundown_odds fetch ) || echo "NOTE: the first price check did not complete (see the line above)."
( cd "$LIVE/backend" && env NBA_DATA_DIR="$MAIN/data" PYTHONDONTWRITEBYTECODE=1 "$LIVE/backend/.venv/bin/python" -m app.rundown_odds status )
git -C "$LIVE" push -q origin delivery-release && echo "Pushed to GitHub." || echo "NOTE: not pushed to GitHub."
echo "DONE."
