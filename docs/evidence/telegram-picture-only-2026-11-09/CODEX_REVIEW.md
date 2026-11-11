# Codex review: picture-only Telegram patch

Verdict: changes required before activation. Delivery release remains clean at 1121d608c7834e0893a704bcaac5e100b93acb48. No production send, commit or deployment performed.

## Findings

1. High: incomplete ticket delivery for supported larger batches. `ticket_caption` slices captions at 1,024 characters; `render_ticket_card` still renders only CARD_TICKETS=3. A six-ticket, seven-leg probe produced a 1,024-character caption ending inside ticket 4, with ticket 6 leg 7 absent. The PNG renders only tickets 1–3 (1080×4980 in the probe). Tickets 5–6 have no delivered selections, and tickets beyond 3 lack IDs/start times even before truncation. Render every ticket completely or reject the batch before dispatch; never silently truncate selections.
2. Medium: picture failure silently restores the unwanted long-text path. `send` catches every exception from `ticket_picture` (including archived image hash failures), sets photo=None, then sends text. This violates picture-only delivery and can create unexpected messages after artifact-integrity failure. Fail with an explicit recorded operational outcome instead; retain the historical text-sent recovery path only for genuinely older batches.
3. Medium: game-card dedup still changes for a moved recommended line. The new identity contains `strong[0]['pick_label']`; line movement changes that label, generating another card for the same game/start/side. The pregame namespace also changes, so old receipts are not matched at cutover. Define intended correction behavior and migrate/match existing delivery identities without bypassing uncertain receipts.

## Verification

- Exported clean delivery HEAD into `/tmp/telegram-picture-review.b94mTD` and applied the patch only there.
- Release Python 3.12.12: `PYTHONDONTWRITEBYTECODE=1 NBA_DATA_DIR=/tmp/telegram-picture-review.b94mTD/test-data /Users/andry/nba-predictor-delivery/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tickets.py tests/test_telegram.py tests/test_delivery_release.py tests/test_nfl_live.py tests/test_soccer_live.py`: **227 passed, 1 warning, 13.00s**. Synthetic ledgers, mocked transport.
- Six-ticket/seven-leg caption/render boundary probe reproduced the truncation/omission above.
- Game-card identity probe: unchanged game/start with changed recommended line produces a different delivery key.
- `gh auth status`: existing Spacecadet10 token invalid. No push attempted; no token printed.
- No real Telegram test, full backend suite, frontend/browser readiness or model checks performed (review scope).

## Next bounded task

Author repairs these delivery findings and adds regressions for maximum-size batches, render/hash failure and dedup cutover, then Codex re-reviews the delta before activation. Keep existing production receipts immutable. Instructions already allow supported spreads/totals; seven-leg limit and any nightly long-shot preference require a versioned instruction update coordinated with the reviewed helper. Do not promise a ticket when fewer than seven distinct supported games or insufficient evidence exists. GitHub needs credential repair before pushing reviewed commits. Do not stage the main checkout wholesale: it contains unrelated active work.
