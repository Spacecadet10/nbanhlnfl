# Card output integration — Nov 12

Owner authorized connecting the visible card recommendation to the ticket context preferences and adding soccer cards, including release after verification.

`context-card-v1` is a deterministic display comparison in the existing tickets module, not AI ticket judgment or a new probability model. It reuses quote checks, lineup and rest/movement facts. It excludes prohibited team totals and changed/uncheckable lines; compares warning-free candidates first, then a single support group, sportsbook probability (app probability when unavailable), exact-record support and app probability. Correlated support is not summed. The support group implements the current approved distance ranges, NBA sportsbook/opposing-spread/movement preferences, NHL +1.5 winner/67% support and back-to-back cautions. Warnings can survive only with explicit support and appear in the reason. No additional score floor. Future weekly changes to these definitions require a corresponding reviewed code update; editing the prose skill alone cannot change a running Python selector.

Live cards show one recommended pick with the app/book gap, supporting reason and known caution, or a concrete no-pick reason. The legacy formatter remains available for historical previews; scheduled sends explicitly use the new comparison. The frozen best_pick_record.py, v4 rule, recorder hash, stored forecasts and probabilities are untouched. Tests prove paper-recorder failure leaves context card text/image identical and paper scoring remains unchanged.

Soccer uses tickets.soccer_section and eligible, including its own 06:00 forecast cutoff and T-minus-two-hour delivery window. Refused competitions are omitted and reported; incomplete market reads fail visibly in job status. Actual Bet365 quotes remain distinct from derived prices, and absent exact-percent history is not a fabricated zero record. No invented team/injury data. No manual or test sends.

Deduplication preserves original keys and uncertain receipts. Switching the recommendation method does not resend an old card for the same sport/matchup/start. Existing new-side/new-start behavior otherwise remains covered.

Verification:
- `NBA_DATA_DIR=/tmp/card-output-fix/testdata PYTHONDONTWRITEBYTECODE=1 /Users/andry/nba-predictor-delivery/backend/.venv/bin/python -m pytest tests/test_telegram.py tests/test_best_pick_record.py tests/test_tickets.py tests/test_delivery_release.py tests/test_release_guard.py tests/test_soccer_live.py tests/test_soccer_markets_live.py tests/test_intl_live.py -q -p no:cacheprovider`: 338 passed, one existing Starlette warning, 42.42 s.
- Regression cases: Detroit 81.1/78.2; exactly +8; neutral NHL totals; warning vs clean alternative; changed line; soccer morning/future cutoff, after kickoff and postponed; missing history/odds; actual vs derived soccer quote; all soccer markets with long labels; repeated sends and refused competition; recorder independence.
- Offline PNGs rendered from archived pregame snapshots, no later outcome read. Detroit recommends DET win, gap +2.9; Senegal–Sudan recommends Senegal or draw with unavailable odds/history stated. Both visually inspected for wrapping and clipping. Full-market synthetic soccer rendering also passes.
- `git diff --check` passed. Structure checker fails on four existing preregistration/evidence filenames; no new failures. Full app readiness not claimed.
- Browser/API/frontend smoke unverified in this task: no API/frontend files changed; this is the scheduled Telegram sender and native PNG renderer.

Review: scoped diff checked for forecast writes, timestamp filters, duplicate delivery, missing-source handling, soccer quote provenance and accidental paper-rule changes. No new storage/schema/configuration paths. Skill-based AI ticket selection can still differ from this deterministic single-game display; do not describe them as identical algorithms.
