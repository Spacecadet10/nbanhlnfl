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
  - `12-settle-release-vs-branch.txt` (final commit, copies taken after the last game ended): nothing new had settled;
    both codes appended and sent nothing in two runs, with 36 grade rows and 15 result notices already recorded. So
    switching to this branch sends nothing by itself.
  - `12b-…-nba-store-unreadable.txt` (first version of these fixes): the NBA store copy could not be opened, so six
    tickets had an ungradable leg. Stored rows were identical; the release sent "…/ 1154: unavailable / …", the branch
    did not.
- One more case was added after the reviewer's four: a notice Telegram rejected is not treated as announced, and a
  deferred one is retried once (`test_rejected_notice_is_not_what_was_announced_and_a_deferred_one_is_retried_once`).
- `16-full-backend-suite-after-review.txt`: the full suite on the tree with these fixes.
- (Superseded by the consolidated table below: HOU @ LAC now reads `blocker_observed: true`, `explained: false`.)

## Consolidated findings of the three reviews of 635a4ec, against the final commit

Reviewers: R1 = session 43 (Opus), R2 = session 5b7a (Opus, `REVIEW-635a4ec.md`), R3 = session 8a1e (Sonnet, relaying its
reviewer). All three: request changes. Status is for the final commit of this branch; "probe" means the reviewer's own
test, run unchanged on the final tree (`22-all-reviewer-probes.txt`: 27 of 28 pass; the 28th is explained under the table).

| # | Finding | Raised by | Status | Evidence |
|---|---|---|---|---|
| 1 | Result → pending → same result announced again as "Corrected" | R1 must, R2 #5, R3 high | Fixed | probes P1, `test_A_pending_A…`, `test_scn_win_pending_win…` |
| 2 | Score-only change re-announced | R1 | Fixed | probe P4 |
| 3 | An unreadable leg triggers a result notice (several for different error types) | R1, R2 #3, R3 high | Fixed: never announced | probes P2, `…different_errors`, `12b-…` |
| 4 | `explained: true` from a blocker that had cleared | R1 must, R2 #2, R3 medium | Fixed | probes B1, `test_blocker_that_cleared…`, `test_scn_explained_masks…` |
| 5 | Dark theme: forecast card unreadable | R1, R2 #1 | Fixed | `17-…` (1.08) → `05-…` (7.26) |
| 6 | New frontend crashes against the old API | R1 note, R2 #4, R3 medium | Fixed: optional fields with fallbacks | `21-…` (crash before) → `05-…` old-api cases |
| 7 | Soccer matches that start unreviewed are never reported | R3 medium | Fixed | `test_soccer_match_that_starts_unreviewed…` |
| 8 | "Awaiting forecast" after the cutoff has passed | R3 medium | Fixed: the API reports each match's cutoff | `test_the_api_reports_each_match_forecast_cutoff`, `05-…` cutoff case |
| 9 | A LOSS is announced again each time another leg settles | R3 open question | Changed by the owner's rule: initial loss, corrections, one final recap | `test_lost_ticket_is_announced_once…` |
| 10 | First correction unlabelled; label lost when the first attempt did not happen | R3 low, R2 #6 | Fixed | probes `…first_correction_is_labelled`, `test_corrected_label_survives…` |
| 11 | A locked forecast selected as a leg passes `check`, refused only at send | R3 low | Fixed: refused at `check` with "review again" | `test_locked_forecast_selected_as_a_leg…` |
| 12 | Lock reason says "sent" for a batch that was only reserved | R3 low | Fixed: "reserved or sent" | same test |
| 13 | Stale-data hint talks about refused forecasts for competitions never forecast | R3 low | Fixed | frontend unit test |
| 14 | "Latest stored result" ordered by rowid only | R3 low | Fixed: `as_of DESC, rowid DESC` | code |
| 15 | `.error-notice` has no style | R2 #7 | Fixed | code |
| 16 | Review window may start late if a forecast is re-versioned | R2 "not established" | Fixed: the window is the two hours before the start | `test_observation_made_before_the_review_window…` |
| 17 | `explained` stays true when the last observation was a blocker long before the start (HOU @ LAC, 57 min) | R1 note, R2 #2, owner | Fixed without a threshold: `blocker_observed` and `explained` (entire window) are separate; a blocker must be shown to last to the start | `04-…`, `test_blocker_seen_before_the_start_but_gone_at_the_next_check…` |
| 18 | A rejected ("failed") result notice is final and silent on later runs | R1 note | Deferred: same as the release; changing retry rules for rejected messages is a delivery-policy decision | — |
| 19 | A deferred ("retry") notice whose content was overtaken stays in `retry` for ever | R1 note | Deferred: harmless, nothing loops over deliveries; cleaning it would mean editing delivery rows | — |
| 20 | The observations table cannot be pruned; about one row per pending game per run | R2 note | Deferred: append-only by design; pruning is a storage decision | — |
| 21 | Basketball icon on the soccer page title | R3 low | Deferred: already in the release, cosmetic, no soccer icon exists in the icon set | — |

| 22 | A refused review is filed under the wall-clock day, not the day of the snapshot it reviewed | Found by the author when the full suite ran past midnight | Fixed: filed under the snapshot's day | `24-…` (3 ticket tests failed on Nov 9), `23-…` (the unmodified release fails one of them too), `test_refused_review_is_filed_under_the_day_of_its_snapshot…` |

The one reviewer probe that fails on the final tree is R3's `test_scn_lock_filled_and_enforced`. It is about the forecast
lock, not about notifications: it expects `check()` to accept a ticket whose leg is a locked forecast and `send()` to
refuse it with "identical forecast". R3's own finding 11 asked for that refusal to move into `check()` with "review
again", which is what the final tree does, so the probe's `checked = t.check(plan)` line now raises. Every notification
probe of all three reviewers passes unchanged.

## Follow-up to the re-review of 3f85fc5 (owner's instruction, Nov 9)

The re-review of 3f85fc5 ended with three approvals, one medium finding (R3) and the deferred items above. The owner
asked for two of them to be fixed before activation, in a small follow-up commit on top of 3f85fc5, and for the rest to
stay outside it. "PENDING … Corrected result" (a corrected loss while other legs are open) is kept as it is, by the
owner's decision.

| # | Item | Before (3f85fc5) | Follow-up | Evidence |
|---|---|---|---|---|
| F1 | R3 medium: `explained` true because the blocker came back later the same day | "Lasted to the start" was any later sighting of the same blocker; a clean check left no record, so it could not be told apart from no check at all | Every trigger run is recorded (`ticket_trigger_runs`), clean ones and ones that could not evaluate the state included; observations belong to their run. `explained` uses the last run before the start and the FIRST run at or after it | `26-…` (fails before), `27-…`, `29-…` (real Nov 8 inputs) |
| F2 | Deferred #18: a rejected result notice is final and silent on later runs | After the run in which Telegram rejected it, nothing reported it; the user kept the earlier result (a WIN whose LOSS correction was rejected) | Still never sent again, receipt never changed. While it is the notice the user is owed, every settlement lists it (`undelivered_results`), and `preflight --notify` sends one alert naming the ticket, the rejected result and the last result on record | `26-…`, `27-…`, `28-…` (real ledger copy: none owed, nothing sent) |

Choices made in F2, for the reviewers and the owner:
- **Listed, alerted once, not resent.** The rejected notice keeps its `failed` receipt and is not retried: Telegram
  rejected that exact text, and resending is a delivery-policy change nobody asked for. The alert is a different
  message with its own key; it says the result, so the owner is not left with a wrong WIN.
- **`notification_errors` is unchanged**: it reports the rejection only in the run in which it happens. The review
  skill tells the 15-minute trigger not to stop while `notification_errors` is non-empty, so a notice that stays
  rejected would otherwise keep every run busy. The standing list is the separate `undelivered_results` field, which the
  skill does not read; the skill and the automation are not changed.
- **Unconfirmed notices are untouched**: an `uncertain` result notice still counts as announced, is never sent again
  and is not listed as rejected (`test_unconfirmed_result_notice_is_still_treated_as_announced…`).

New tests (13, all in `backend/tests/test_tickets.py`, section "Follow-up to the re-review of 3f85fc5"). Before the
change all 13 fail (`26-…`); 11 fail on behaviour or on the missing record, and two only on what is new in the output:
`test_blocker_seen_at_the_checks_on_both_sides…` (explanation wording) and `test_unconfirmed_result_notice…` (the new
`undelivered_results` key; its safeguard assertions already held). One existing assertion changed: the explanation
sentence in `test_blocker_seen_before_the_start_but_gone_at_the_next_check…` now names the first check after the start.

Evidence files of the follow-up:
- `26-followup-tests-before-fix.txt`: the 13 new tests against the helper of 3f85fc5.
- `27-followup-tests-after-fix.txt`: the 13 new tests, the ticket file (148), nine focused files (318) and the
  structure guard on the follow-up tree.
- `28-followup-real-copy-check.txt`: `scripts/followup_check.py` on copies of the live ledger and stores with Telegram
  mocked: settlement twice (nothing appended, nothing sent, no undelivered result), the trigger twice (two clean runs
  recorded) and `status` (records nothing).
- `29-followup-replay-nov8.txt`: the real Nov 8 reviewer snapshots replayed through the follow-up code: HOU @ LAC stays
  `explained: false` and now names the clean first check after the start (21:06:30Z).
- `30-full-backend-suite-followup.txt`: the full backend suite on the follow-up tree.
- `31-followup.patch`: the code and documentation change against 3f85fc5.

Still open after the follow-up (not part of this patch, by the owner's instruction):
- R3 low: for the time between serving the new page and restarting the API on 8101, matches that have a forecast show
  "Awaiting forecast".
- R3 low, owner's decision: an announced WIN whose leg later becomes permanently `unscorable` is not retracted.
- R2 note, still true: `explained` is decided from recorded runs, and a run records itself after it has computed the
  state. At the first check after a start `explained` is therefore false ("no check at or after the start is recorded
  yet") and can become true at the next run.
- Deferred #19 (an overtaken `retry` notice is never sent), #20 (the trigger tables cannot be pruned; now one more row
  per run, about 96 a day) and #21 (basketball icon on the soccer title).
- In a run where a leg cannot be read nothing is compared, so `undelivered_results` is empty for that run
  (`settlement_unavailable` is reported instead).
- A check after Toronto midnight is not looked at for a game that started just before it (runs are read by day), so
  such a game stays `explained: false`.

## Delta review of acd984a (three reviewers) and the second follow-up commit

Verdicts on `acd984a`: session 43 APPROVE (4 low), session a9 APPROVE (2 low), session cb REQUEST CHANGES (1 medium,
4 low). Every finding is addressed in the second follow-up commit; tests first (`32-…`: 6 of 7 fail on acd984a, the
seventh guards against over-blocking and passes on both).

| # | Finding | Raised by | Status | Evidence |
|---|---|---|---|---|
| D1 | The rejected text is sent again under a new key when only the payload changes (a score, a result time), with a new alert each time | cb medium, 43 low 3, a9 low 1 | Fixed: an owed notice with the identical text keeps the rejected receipt's key while that receipt is newer than the last notice that reached the user; a result that returns to the rejected one is not attempted again either (stricter than cb's variant, which allowed a resend after a return to the delivered result) | `test_rejected_notice_is_not_sent_again_when_only_the_payload_changes`, `test_a_rejected_text_sent_before_the_last_delivered_notice…` |
| D2 | A check timed exactly at the start counts on both sides of it | 43 low 1, cb low 2 | Fixed: `before` and the game's observations use `< start` | `test_a_check_exactly_at_the_start_belongs_to_the_checks_after_it` |
| D3 | The `undelivered_results` step is unguarded; a read problem ends the run after grades are committed | 43 low 2, cb low 4 | Fixed: guarded, reported as `undelivered_error` | `test_listing_undelivered_results_can_fail_without_costing_the_run` |
| D4 | "Recorded neither a blocker nor a due review" for a check that predates the game's first forecast | 43 low 4 | Fixed: says the forecast was made after the last check | `test_forecast_that_arrived_after_the_last_check_is_said_so` |
| D5 | "Was not seen at the first check after" reads as cleared when no game of the sport was pending | cb low 1 | Fixed: wording says the check recorded nothing for the sport | `test_last_pending_game_of_its_sport…` |
| D6 | A ledger with the run_id-less observations table of an unreleased build would silently stop recording | cb low 3 | Fixed: refused with a message in `observations_error` every run (live ledger has no such table) | `test_an_observations_table_from_an_unreleased_build_is_refused_loudly` |
| D7 | A preflight whose snapshot fails leaves no run row, so the next run stands in for it | a9 low 2 | Fixed for snapshot failures: a best-effort `due` NULL run with the clock's time. A run whose own recording fails still leaves no row (`observations_error` only) | `test_preflight_whose_snapshot_fails_still_leaves_a_run_record` |

Evidence files: `32-delta-review-tests-before-fix.txt`, `33-delta-review-tests-after-fix.txt` (8 new tests, ticket file
156, nine focused files 326, structure guard), `34-second-followup-real-copy-check.txt` (live-ledger copies: nothing
appended or sent, two clean runs recorded, `status` records none), `35-full-backend-suite-second-followup.txt`,
`36-second-followup.patch` (the delta against acd984a).

Notes from the reviewers, recorded, no change: `explained` looks only at the end of the window (a review due early in
the window that never ran, then a blocker through the start, reads explained; same on 3f85fc5); result-notice pick
labels are not HTML-escaped (pre-existing, outside this patch; the live notices contain no `& < >`).

## Recheck of 98f8f96 (three reviewers) and the third follow-up commit

Verdicts on `98f8f96`: session 43 APPROVE (2 low nits), session cb APPROVE (2 low), session a9 REQUEST CHANGES (1 medium,
new in 98f8f96). Addressed in the third follow-up commit; tests first (`37-…` before, `38-…` after).

| # | Finding | Raised by | Status | Evidence |
|---|---|---|---|---|
| E1 | The D4 sentence ("no trigger run saw this game: its first forecast was made after the last check") fired for games whose forecast was re-issued shortly before the start, because `first_forecast_at` is the CURRENT forecast's time; `explained` went true → false and "a review was due and none ran" was hidden. Common in the live stores (28 of 134 NBA games re-issued within 15 min of tipoff) | a9 medium | Fixed: the branch is taken only when nothing was recorded for the game in its window; it says "current forecast" | `test_a_forecast_reissued_before_the_start_does_not_hide…`, `…keeps_a_blocker_seen_on_both_sides_explained` |
| E2 | `undelivered_error` dropped from the brief preflight output | 43 low | Fixed | `test_listing_undelivered_results_can_fail_without_costing_the_run` |
| E3 | "(no game of it was pending)" can be false (reviewed or locked games are pending too) | 43 low | Fixed: parenthesis removed | the three wording tests |
| E4 | A rejected notice that drops off the list and is owed again is not alerted again | cb low | Wording: the alert says it is not repeated; the alert key is unchanged (one alert per rejected receipt) | `test_rejected_notice_alert_says_it_is_not_repeated` |
| E5 | A run recorded on a snapshot failure is filed under the clock's Toronto day, not the slate's | cb low | Recorded, not changed: without a snapshot there is no slate day; matters only at the midnight edge already listed | — |
| E6 | `_review_window` (the single-game rule's other windows) also uses the current forecast version's time | a9 low, pre-existing | Recorded, not changed; documented in TICKETS.md | — |

Evidence files: `37-recheck-tests-before-fix.txt`, `38-recheck-tests-after-fix.txt`, `39-third-followup-real-copy-check.txt`,
`40-full-backend-suite-third-followup.txt`, `41-third-followup.patch` (the delta against 98f8f96).

## Wording follow-up on 1121d60 (owner: "Apply only the two wording nits")

Verdicts on `1121d60`: three APPROVE (43, a9, cb); it was activated on Nov 9 at 17:24 ET. Two wording nits remained and
are applied here, behaviour unchanged: (1) 43: when the last check before the start could not evaluate the review state,
the explanation says so instead of the timing sentence, also when the forecast was re-issued after that check;
(2) a9: the rejected-notice alert says "this alert is not repeated unless another result reaches you first". Tests:
`42-wording-tests-before-fix.txt` (2 fail on 1121d60), `43-wording-tests-after-fix.txt` (ticket file 160, nine focused
files 330, structure guard). Full suite not rerun for this delta (two strings and one condition); `44-wording.patch`.

## Not claimed

- The fixes have not run against live data, the live ledger or real Telegram.
- No ticket was produced to test delivery; revision 4's payout lines in a real ticket remain unobserved.
- The stale-source screen was produced from a scratch copy whose fetch times were moved back three days; the failed
  live-read screen by blocking that request in the browser. Neither was observed on the live app.
- Not independently reviewed: this session wrote the changes.
