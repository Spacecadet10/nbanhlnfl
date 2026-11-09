# Fixes prepared from the live-state audit of Nov 8 (NOT deployed)

Worktree `~/Desktop/nba-predictor-audit-fixes`, branch `audit-fixes-20261108`, based on the live release commit `7f629ea`
(`~/nba-predictor-delivery`). Nothing was deployed, no store or ledger was migrated or written, no launchd job, skill or
automation was changed, and no Telegram message was sent. All checks ran on scratch copies with Telegram mocked.
The audit as received is `00-audit-as-received.txt`.

## What was found and what changed

| # | Finding | Cause established | Change |
|---|---|---|---|
| 1 | Soccer page says "no predictions / no validated model / not refreshed automatically" beside live forecasts | Page text was fixed strings; the API only said `available` when a competition already had forecasts, and called everything else "No soccer model exists yet" | API reports `forecast_state` (recorded / awaiting / out_of_scope / no_live_store), the forecasts of the listed matches, and the age of the source data; the page text is derived from those (`frontend/src/components/soccerStatus.ts`) |
| 2 | HANDOFF did not match what runs | Two deployments at 21:08–21:09 ET landed while entries were being written | New "Current live state" entry in `HANDOFF.md` (sprint1 working tree), verified against git, shasum, launchctl, lsof and the stores |
| 3 | NBA review entries repeat an NFL sentence | The reviewer's one-off plan script gave one catch-all sentence to every remaining forecast (19 of 31 in review 7; 18 were lock-blocked) | The helper records the lock for lock-blocked forecasts itself, so they need no reviewer text; every lock is archived under `locks`. No text comparison of any kind was added |
| 4 | CIN @ ATL and HOU @ LAC listed as never reviewed | CIN @ ATL was reviewed twice (13:10Z, 14:10Z) as a lone-game window, in hand-written files the later accounting does not read. HOU @ LAC: NFL was refused by the helper 19:04–20:31Z (definition hash drift from an uncommitted edit in the tree the helper then ran from) and the next hourly run took its snapshot at 21:06:30Z, 90 s after the 21:05:00Z kickoff | Both operational causes were already removed (helper runs from the pinned clean release since 00:04Z Nov 9; reviews are triggered every 15 minutes). Added: the trigger records why a pending game is not being reviewed, and a game that starts unreviewed carries those records (`explained` true/false) |
| 5 | 8 uncertain game-card receipts | Telegram's reply was not confirmed for each | Inventory only (`08-uncertain-receipts-inventory.txt`); all 8 rows preserved |
| 6 | Push/void grading and append-only corrections unconfirmed; WPG ticket not caught up | Push/void grading is correct. One gap: a result corrected back to an earlier result was neither stored nor announced. WPG ticket `cd2a6a716c85` was settled at 02:11:47Z (loss; DET lost 116–121, WPG +1.5 won 4–3; receipt 291) | A correction back to an earlier result is appended and announced once; a leg that cannot be graded for a moment never repeats a notice |

## Evidence files

- `01-…` to `03-…`: the new tests run before the fixes (6 + 3 failing, then the frontend module missing).
- `04-replay-missed-games.txt`: the real Nov 8 reviewer snapshots replayed through the fixed helper on copies. The
  `due` column there is not historical (the copy holds all of the day's later review archives).
- `05-browser-check.txt` + `screens/`: soccer screens × 1280 px and 390 px real viewports in headless Chrome
  (DevTools device metrics, not a resized window), light and dark for the forecast card: 30/30 ok.
  `scripts/browser_check.mjs` is the check.
- `06-full-backend-suite.txt`: full backend suite on the final tree: 1,982 passed, 8 failed. All 8 are
  `tests/test_deep_study.py` (torch/keras not installed in the venv used); `13-…` shows the same 8 failing on an
  unmodified export of `7f629ea`.
- `07-focused-suites.txt`: focused backend suites on the final tree (287 passed). `11-frontend-checks.txt`.
- `12-settle-release-vs-branch.txt`: settlement with a mocked Telegram on copies of the real ledger and stores,
  under the release code and under this branch: identical new rows, hashes and notices; a second run adds and sends
  nothing (`scripts/settle_compare.py`).
- `08-uncertain-receipts-inventory.txt`, `09-proposed-skill-text.md`.
- `scripts/`: `dup_reasons.py` (which review entries share text), `replay_missed.py`, `snap_db.py` (read-only store copy).

## After the independent review of 635a4ec (session 43: request changes, 2 must-fix, 2 should-fix)

| Finding | Fix | Evidence |
|---|---|---|
| A result that went pending (or changed only a score) and came back was announced again as "Corrected" | Notices are decided by what was last announced (ticket grade + leg grades), not by what was last stored | `14-…` (failing first), `15-…` (the reviewer's probes pass) |
| `explained: true` from a blocker that had already cleared | The trigger also records that a review was due; `explained` follows the last observation before the start | `14-…`, `15-…` (B1 now false), `04-…` |
| A passing read error on a lost ticket still sent "… unavailable" | A result with an ungradable leg is stored but never announced | `12b-…`: on real copies with the NBA store unreadable, the release sends that notice and the branch does not; stored rows identical |
| Dark theme: forecast card unreadable | Card uses the theme tokens | `17-dark-card-before-fix.txt` (contrast 1.08) → `05-browser-check.txt` (7.26 dark, 5.87 light; 30/30 screens) |

- Settlement on copies of the real ledger and stores, Telegram mocked, release code against this branch:
  - `12a-…-two-new-results.txt` (first version of these fixes): two tickets had newly settled; both codes appended the
    same 2 rows and sent the same 2 notices; a second run added and sent nothing.
  - `12-settle-release-vs-branch.txt` (final code, later copies): nothing new had settled; both codes appended and sent
    nothing, with 12 result notices already recorded.
  - `12b-…-nba-store-unreadable.txt` (first version of these fixes): the NBA store copy could not be opened, so six
    tickets had an ungradable leg. Stored rows were identical; the release sent "…/ 1154: unavailable / …", the branch
    did not.
- One more case was added after the reviewer's four: a notice Telegram rejected is not treated as announced, and a
  deferred one is retried once (`test_rejected_notice_is_not_what_was_announced_and_a_deferred_one_is_retried_once`).
- `16-full-backend-suite-after-review.txt`: the full suite on the tree with these fixes.
- The HOU @ LAC replay still reads `explained: true` because the last thing the trigger saw (20:07Z) was the refused
  sport; `last_observation` now shows that this was 57 minutes before the start. The run that would have seen the
  recovery did not exist under the hourly schedule of that afternoon.

## Not claimed

- The fixes have not run against live data, the live ledger or real Telegram.
- No ticket was produced to test delivery; revision 4's payout lines in a real ticket remain unobserved.
- The stale-source screen was produced from a scratch copy whose fetch times were moved back three days; the failed
  live-read screen by blocking that request in the browser. Neither was observed on the live app.
- Not independently reviewed: this session wrote the changes.
