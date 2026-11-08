# Integration report: ticket fixes verified, batch 1 + soccer + tickets integrated, guarded delivery release (2026-11-08)

Author: Claude Opus 5.5, session f7, the single implementation writer for this task. Two read-only verifier subagents
reproduced the ticket fixes. Nothing was committed, pushed, deployed or switched. No live job, launchd file, skill,
automation, Telegram message, live store, credential or reserved soccer test was touched.

## 0. Bottom line
- **Codex's six ticket fixes reproduce.** Two independent verifiers used the real Telegram error mapping through a mock
  transport, with sockets blocked and temporary ledgers.
  - Settlement was only partly fixed: a malformed Telegram reply could still abort it.
  - The verifiers found 11 further defects: 1 medium, 1 latent medium, the rest low. 9 are fixed test-first in the
    integration tree; 1 is partly fixed and is recorded as open along with 1 other (§3).
- **Integration tree:** `~/Desktop/nba-predictor-batch1`, uncommitted. It holds batch-1 final (`adacc372`), plus the
  soccer work (`8614c1e2`, sealed files byte-identical), plus Codex's ticket work, plus this task's fixes.
  - The only real overlap was `backend/app/config.py`, merged by hand.
- **Guarded release for the sender and the helper:** implemented, tested and rehearsed end to end in a throwaway
  repository. **It is not activated**; activation and rollback are in `docs/TICKETS.md` "Guarded release".
- **NFL activation blocker, raised by the user after the first draft and now resolved.**
  - The cause was the integrated `coverage.py`. The approved NFL activation (`b6074877a7e7ccfe`, 2026-11-02) binds
    `coverage.py` bytes, and the integrated tree gave `a926a90ed63fd631`, so `activation()` returned None.
  - `coverage.py` and `jobs.py` are now byte-identical to HEAD `79ff6ec`, so NFL resolves to `b6074877…` again.
  - The early publication in `jobs.py` and the explanation-only content-key rule are **withdrawn** from the integrated
    build (§3a).
  - Regression: `backend/tests/test_nfl_activation_compat.py` uses a byte copy of the production artifact
    (`tests/fixtures/nfl_activation_2026-11-02.json`, sha256 `18f55fd1…`, equal to `data/research/nfl_live/activation.json`).
    It failed first with `a926a90… != b6074877…` (`logs/nfl_activation_failing_first.txt`).
  - On raw copies of today's stores, the integrated code reports NFL active with 60 picks, identical to the live code.
- **Malformed `/games/%E0%A4%A`:** fixed in the shared layout, with a regression test and a recovery state for every
  league.
- **Final readiness gate on the final integrated tree `06dcc1d2`: PASSED,** 1,910 passed / 0 failed (§6). Ready for independent review; **not** released or activated. An earlier run was stopped and
  superseded when the NFL issue arrived (`gate-attempt1-STOPPED-superseded.log`).

## 1. Verification of the six ticket fixes (code as of sprint1 14:10 ET, now also in the integration tree)

Verifier scripts and logs are in `verifier-scripts/` (`v1-*`, `v2-*`).

| # | Claim | Result | Evidence |
|---|---|---|---|
| 1 | ConnectError and 429 retries send later at the current attempt time, with the original content and stakes | PASS | `v1-item1_2.log` (see below) |
| 2 | Uncertain text or image is never blindly resent | PASS | `v1-item1_2.log`: ReadTimeout or 500 gives `existing_uncertain`, exit 1, no resend. Image uncertain: nothing resent. Image ConnectError: later only the photo is resent, byte-identical |
| 3 | Grades persist even when Telegram identity, controls or notification calls fail | PARTIAL, now fixed | `v1-item3.log` (see below) |
| 4 | Ticket alerts exclude game cards and earlier Toronto days; a later failure cannot be masked | PASS | `v1-item4.log` (see below) |
| 5 | Early and final publication: no duplicate for an explanation-only change; counts correct | PARTIAL; then WITHDRAWN because it breaks the sealed NFL definition (§3a) | `v2-pytest_5a.log` (see below) |
| 6 | A development copy cannot write the production ledger | PASS for copies; latent risk for release checkouts, now fixed and guarded | `v2-item6_paths.log` (see below) |
| 7 | Text first, then the visual PNG, with separate dedup, exact PNG and caption archived, caption ≤ 1024 | PASS | `v1-item7.log`, `v1-card-3.png` (see below) |
| 8 | $5 cap under concurrency, legacy reservations and crashes | PASS | `v2-item8a.log`, `v2-test_p8_9.py` (see below) |
| 9 | Complete-slate reviews continue after the budget is used | PASS | `v2-item8_9.log` (see below) |

Details:
1. **Retries.** First send: `retry`, exit 1. A second send before `next_attempt_at`: `retry_pending`, no sendMessage.
   A later send repeats the archived text exactly, with `attempted_at` set to the current attempt. Still 1 batch and the
   same `ticket_items`; the photo follows.
3. **Settlement.** Grades are committed in every case. But a malformed getUpdates or getMe reply (KeyError or
   AttributeError) escaped `settle`. An offline `preflight --notify` lost the printed report.
4. **Alerts.** Only the ticket `retry` and `ticket-image` `failed` rows alert. A later failure and a status change each
   produce a new alert. The DST day boundaries are exact.
5. **Early and final publication.** `test_coverage.py` 26 passed, including the former `:278` failure. An
   explanation-only change creates 0 rows; probability, line, model, source and missing changes each create a version.
   Wrong counts appeared when the final pass versioned an early row, and an early exception escaped (§3).
6. **Ledger isolation.** A release checkout with `NBA_DATA_DIR=sprint1/data` would have used a new
   `sprint1/data/.telegram-delivery.sqlite`, losing all dedup, budget and `/stop` state.
7. **Visual ticket.** For 1, 2 and 3 tickets the PNG sent equals the archived bytes and the stored sha. Captions are
   175, 217 and 259 characters. The text and the image have different keys. The text carries every leg, matchup, side,
   Toronto time, ID and stake.
8. **Budget cap.** 4 OS processes started together, 11 rounds: never over $5, at most one $3 and two $1, no duplicate.
   Legacy reservations count toward the cap, and an archive-less legacy row blocks allocation. A crash after the
   reservation keeps it, and the rerun sends once.
9. **Complete-slate review.** With $5 reserved, a `tickets=[]` review still needs every forecast plus the comparison,
   and is archived with coverage 15/15. A ticket plan passes `check`; `send` exits 1 with the daily limit.

Focused suite on the sprint1 code (verifier 2):
`tests/test_tickets.py tests/test_telegram.py tests/test_near_tipoff.py tests/test_freshness.py` gave **103 passed**.
This matches Codex's claim. `test_coverage.py` alone gave 26 passed (verifier 2: 28 with two ticket tests).

## 2. Integration
- **Base:** the batch1 worktree index. It is HEAD `79ff6ec` plus sprint1's uncommitted work as of Nov 7 18:20.
- **Batch 1:** the batch1 working tree, verified at `adacc372` before reuse. It equals the soccer-train index and
  `remediation/batch1-final/batch1-final-r5.patch`.
- **Soccer:** soccer-train working minus index, 89 files, copied byte-for-byte (0 hash mismatches).
- **Tickets:** sprint1 working minus base, 153 files (code and docs/evidence), copied byte-for-byte.
- **Overlaps:**
  - Only `config.py` was changed by both batch 1 (`protected_data_dir`, `protected_path`, `lock_path`) and tickets
    (`telegram_ledger_path`). Both were kept.
  - Soccer and tickets do not overlap.
  - The three intent-to-add test files that sprint1 lacks are batch-1 additions, not deletions.
- **Seal:** the six sealed-candidate files are byte-identical to `soccer-training-2026-11-08/s4/frozen_code_sha256.txt`.
  No `soccer eval-*` command was run, and the soccer experiment logs were not touched.
- **Preserved:** append-only triggers, forecast timestamps (no backdating; content key unchanged except for the
  explanation rule), raw provenance, research and live separation.
  - Tickets still report soccer as `forecasts_unavailable` (`tickets.py:53`).
  - No ticket or Telegram code imports the soccer research modules.
- **Structure check:** it rejected `batch1-final.patch` and `batch1-final.diffstat.txt`. They are renamed
  byte-identically to `batch1-round4-3c2ec4a.*`, with a `RENAMED.md` mapping. Nothing was deleted and the checker is
  unchanged.
- **Environment:** `backend/.venv` in the worktree is a git-ignored symlink to sprint1's venv, used by `dev.sh` only.
  The gate uses the real interpreter path.

## 3. Defects found during verification and their status (integration tree; failing-first logs in `logs/`)

| Sev | Defect (location before fix) | Status |
|---|---|---|
| MED | Orphan PNG from a crash before its `ticket_images` row gives a permanent hash mismatch and no image (`tickets.py:302-314`) | FIXED: content-named PNG plus `write_once` (`tickets.py:163`, `deliver_visual`) |
| MED (latent) | A release checkout elsewhere would create an empty ledger in the live data folder (`config.py` `telegram_ledger_path`) | FIXED: main-checkout data folder resolves to its own ledger (`config.py:100`); under the guard, explicit paths are required and are never created (`telegram.py:76`, `release.py:152`) |
| LOW-MED | Render or caption failure after the text was sent leaves no receipt and no alert | FIXED: `failed` image receipt (`tickets.py:321`), then alerted |
| LOW | A malformed Telegram reply aborts settlement (KeyError/AttributeError) | FIXED: notification block catches everything after the grades commit (`tickets.py:503`); a non-object body counts as uncertain (`telegram.py:52`) |
| LOW | Offline `preflight --notify` drops the settlement report | FIXED: report printed with `alerts_error`, exit 1 (`tickets.py:641`) |
| LOW | A failed or retry settlement notice was not reported | FIXED: listed in `notification_errors` (`tickets.py:502`) |
| LOW | A reservation without any delivery claim (crash) was never alerted | FIXED: `unclaimed` alert (`tickets.py:555`) |
| LOW | `predict_now` under-counted `already_recorded` when the final pass versioned an early row | MOOT: early publication withdrawn (§3a) |
| LOW | An early-publication exception skipped research and the final pass, and the run stayed `running` | MOOT: early publication withdrawn (§3a) |
| LOW | A concurrent identical review archive raised FileExistsError | FIXED: `write_once` |
| LOW | A concurrent identical plan: the loser gets "Duplicate ticket combination", exit 1 (no double send, no over-allocation) | OPEN, recorded |
| LOW | The stored canonical `fallback_reason` keeps the first explanation | MOOT with `coverage.py` restored: every explanation change is versioned again, as in live |

### 3a. Withdrawn: early publication (Codex, `jobs.py`) and the explanation-only content key (`coverage.py:269`)
- **Why.** `coverage.py` is sealed in the NFL activation definition (`nfl_live.py:18-20`, seven files). The edit
  silently disabled NFL in the live sender/helper from 19:04Z on Nov 8 until sprint1's `coverage.py` was restored; see
  the 15:15 market-coverage audit in HANDOFF.
- **The conflict.** With the sealed `coverage.py`, the early pass plus the final pass write a second canonical row
  whenever only the fallback explanation changes (`test_coverage.py:278`, `assert 2 == 2 and 6 == 12`). Append-only rows
  cannot be removed, so the early pass cannot stay.
- **Result.** Both files equal HEAD, which is also the live forecast jobs' behaviour. Removed with the feature, because
  they tested only withdrawn code:
  - `test_primary_coverage_published_before_slow_research`
  - `test_fallback_explanation_only_does_not_create_new_forecast_identity`
  - my two cycle-count and early-failure tests
- **To bring it back** needs one of:
  - a new reviewed NFL activation for the new definition, which is a release decision;
  - a design outside the sealed files that creates no explanation-only versions.
- **Sprint1 itself:** its `coverage.py` is restored, but its `jobs.py` still contains the early publication. That is a
  dev-only duplicate-row risk; the forecast jobs run from `~/nba-predictor-live`.

Other open limitations:
- **Reasoning checks are presence-only.** `"."` passes `combination_comparison`. AI judgment quality is not verified by
  code.
- **Credentials are shared.** A development copy reads the production `.env.telegram` unless the environment or
  `NBA_TELEGRAM_CREDENTIALS` overrides it. Ledger isolation without credential isolation could resend real messages, so
  tests must mock Telegram.
- **Retries are not automatic.** Each one needs a fresh snapshot and a re-review within 10 minutes, before the first
  leg starts. After tipoff the stake stays reserved and is alerted.
- **Controls with a malformed update.** `telegram.controls` can apply `/stop` before storing the offset. Settlement now
  survives this, but `send` and the sender's `run_once` still fail visibly with exit 1.
- **Alert timing.** A failure attempted late on day D after that day's last preflight is not alerted on day D+1.

## 4. New code in this task (integration tree only)
- **Guarded release:**
  - `app/release.py`: `_checkout` (factored out of `check`, same messages) and `delivery_check`.
  - `app/config.py`: `NBA_TELEGRAM_LEDGER` and `NBA_TELEGRAM_CREDENTIALS`, `telegram_credentials_path`.
  - `app/telegram.py`: guarded ledger is never created.
  - `app/cli.py:443`: the `telegram` command is guarded.
  - `app/tickets.py` `main`: guarded.
  - Templates: `scripts/launchd/com.nba-predictor.telegram.plist` (release form) and `scripts/release_tickets.sh`.
  - Tests: `tests/test_delivery_release.py`, 11 tests including one that pins the template and wrapper.
- **Ticket fixes in §3:** 8 new tests in `tests/test_tickets.py`, plus sprint1's later `unavailable_sports` /
  `complete_supported_sports` coverage fields and their test, merged.
- **NFL compatibility regression:** `tests/test_nfl_activation_compat.py` plus its fixture.
- **Router:**
  - `frontend/src/components/gameRoute.ts` and `gameRoute.test.mjs`.
  - `Layout.tsx`: a malformed-link state for every league, the last-game link never stores a malformed path, and a
    `PageErrorBoundary` keeps one broken page from blanking the app (`States.tsx`).
  - `NflPages.tsx` and `SoccerPages.tsx` decode through `gameRoute`.
- **Docs:** `docs/TICKETS.md`, sections "Integration fixes" and "Guarded release" (activation and rollback).

## 5. Checks run (exact commands; all from the integration tree unless noted)
| Check | Command | Result |
|---|---|---|
| Verifier focused suite (sprint1 code) | `cd backend && env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID NBA_DATA_DIR=<tmp> OMP_NUM_THREADS=4 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tickets.py tests/test_telegram.py tests/test_near_tipoff.py tests/test_freshness.py` | 103 passed |
| Guard tests before code | same runner, `tests/test_delivery_release.py` | 9 failed (`logs/release_guard_failing_first.txt`) |
| Delivery defect tests before fix | `tests/test_tickets.py` | 6 failed / 36 passed (`logs/delivery_defects_failing_first.txt`) |
| Job/alert/archive tests before fix | `tests/test_tickets.py` | 4 failed / 42 passed (`logs/jobs_alert_defects_failing_first.txt`) |
| Ledger default test before fix | `tests/test_delivery_release.py` | 1 failed / 10 passed |
| NFL activation regression before fix | `tests/test_nfl_activation_compat.py` | 1 failed: `a926a90ed63fd631` != `b6074877a7e7ccfe` |
| Wide focused after the NFL fix | runner above with `tests/test_nfl_activation_compat.py tests/test_nfl_live.py` plus the nine files below | **225 passed**, 1 warning, 129 s (`logs/focused_wide2.log`) |
| NFL on raw store copies | `tickets.snapshot()`, sprint1 vs integrated code, raw copies plus `research/nfl_live` | both NFL active, 60 picks, identical |
| Wide focused, superseded (before the NFL fix) | runner above with `tests/test_coverage.py tests/test_release_guard.py tests/test_live_store_guard.py tests/test_canonical.py tests/test_tickets.py tests/test_telegram.py tests/test_delivery_release.py tests/test_near_tipoff.py tests/test_freshness.py` | **217 passed**, 1 warning, 126 s (`logs/focused_wide.log`) |
| Route regression before code | `node --test --experimental-strip-types src/components/gameRoute.test.mjs` | 1 failed (module missing) |
| Frontend | `npx tsc --noEmit -p .`; `npm run test:unit`; `npm run build` | passed; 14/14; built |
| Structure | `python3 scripts/check_project.py` | passed (after the evidence renames) |
| Read compatibility | `tickets.snapshot()` from the sprint1 and integration code on raw, unmigrated backups of today's stores (NBA/NHL 31, NFL 33), sockets blocked | identical outputs (`logs/snap_*.json`); stores still 31/31/33 afterwards |
| Guard rehearsal | throwaway git repo of this tree, fake ledger and credentials, network blocked, 9 scenarios | all as designed (`logs/rehearsal.log`, see below) |
| Browser, NBA/NHL/NFL | `dev.sh` preview of this tree on a frozen copy (ports 8862/5262); 8 routes × 3 leagues × 1280 and 390 px via iframes | **48/48**: no horizontal overflow, no page errors (see below) |
| Browser, soccer research | `dev.sh` preview from a copy of session 97's `preview2-src` (8863/5263) | see below |

The rehearsal's 9 scenarios:
- A clean release runs the snapshot and settle.
- `telegram status` passes the guard, then fails as expected because the network is blocked.
- A dirty release is refused, for both the helper and the sender.
- The development checkout with release variables is refused, for both.
- A missing ledger is refused and is not created.
- Once the ledger is restored, runs pass again.

Browser detail, NBA/NHL/NFL:
- `/games/%E0%A4%A` and `/games/%25` (client navigation) show "This game link is damaged or incomplete…" or the API
  404 card.
- Unknown ids show the 404 card. NFL inactive pages show "Not available".

Browser detail, soccer research:
- All research screens are labelled Research backtest, with no "Live" wording. Game day and match pages say "never
  published as live".
- The malformed link shows the recovery state (screenshot `logs/malformed-link-soccer-1440.jpg`). At 390 px there is
  no overflow.
- **At 1280 px the soccer header overflows by 69–151 px.** The soccer-only build on 5251 shows the identical 69 and
  125 px, so this is pre-existing and not caused by integration (`Layout.tsx:117` `header-tools has-competition`;
  `styles.css:225/276/932`). It is recorded, not fixed. The soccer report tested 1440 and 390 only.
- Not separately captured: the loading spinner state.

## 6. Final readiness gate (one run)
Every run used `OMP_NUM_THREADS=4 /Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python scripts/check_project.py --ready`
from the integration root, with the code-tree hash (`tree_hash.sh`, everything except docs/) taken before and after.

| Attempt | Tree | Result |
|---|---|---|
| 1 | before the NFL fix | stopped (superseded) when the NFL activation issue arrived: `gate-attempt1-STOPPED-superseded.log` |
| 2 | `dc80a869` | stopped at the user's "Stop everything": `gate-attempt2-STOPPED-by-user.log` |
| 3 | `0134cbd7` (integration + NFL fix + live club soccer) | **PASSED, exit 0, 1,898 passed / 0 failed**, hash equal before and after (21:19:58Z → 21:45:12Z) |
| 4 | `91406bb2` (adds international model and live, migration 0039, header fix) | **FAILED, exit 1: 1 failed / 1,909 passed** (22:10:58Z → 23:10:41Z, hash equal). The failure was `test_soccer_live.py::test_the_api_serves_national_team_competitions` (my new test), which inherited a demo-mode override left by `tests/test_api.py`. Reproduced in that order, fixed with the existing fixture's isolation; app code unchanged. The live NBA/NHL tick jobs used about 8 cores during this run (34.5 min). |
| 5 | **`06dcc1d2`, the final tree** | **PASSED, exit 0: 1,910 passed / 0 failed** (25.5 min), hash `06dcc1d2a83ef4b11f30d93760016b6202b239c4` equal before (23:13:15Z) and after (23:39:01Z). Log: `gate.log`. |

## 7. Live versus staged (2026-11-08 ~16:00 ET)
- **Live:**
  - The Telegram sender (launchd, 60 s) and the hourly Codex ticket helper import the **sprint1 working tree**, with
    Codex's six fixes but **none of this task's fixes**. The automation runs `python -m app.tickets` in sprint1
    directly, not through the skill wrapper.
  - Forecast jobs, API and web: `~/nba-predictor-live` at `a55c874`. NFL tick: `~/.codex/worktrees/nfl-live-release` at
    `8539513`. `jobs.py` early publication is not live.
- **Staged (uncommitted):** `~/Desktop/nba-predictor-batch1`. Not copied into sprint1, because sprint1 edits go live at
  once.

## 8. Decisions for the user
1. Independent review of the integrated tree, then a commit (not done here).
2. Activate the delivery release (`docs/TICKETS.md` "Guarded release"): a new worktree `~/nba-predictor-delivery`, a
   plist swap, then the skill wrapper and automation command.
3. Separately: moving `~/nba-predictor-live` to this tree means migrating the live stores 31/33 → 37 and promoting
   batch 1, soccer tables and early publication into the forecast jobs.
4. Ticket policies, unchanged here: same-game/new-market locking, cross-window stakes, a personal-slip screen and
   correction lifecycle.
5. Bot token rotation: the token is in plaintext in Codex history.
6. Soccer: the reserved one-shot test and its reading plan, and live activation. Neither is prepared or enabled here.
7. Soccer header overflow at 1280 px: fix scope.


## 9. Soccer live and national teams (added after the first report)
- **The user's instructions and the source decision** are in `docs/SOCCER_LIVE_PLAN.md` §4–6 (ESPN; "forget the
  terms"; real data only).
- **Club model.** Reserved test run once (21:00:57Z) and read by the pre-run plan
  (`docs/evidence/soccer-test-reading-plan-2026-11-08/`):
  - log loss 0.980 vs 1.071;
  - launch scope EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL;
  - UEL failed R3 (n 284), so it is an input only.
- **International model.** Protocol written first; developed, sealed and tested once (21:47Z):
  - paired difference −0.173 (−0.194 to −0.152);
  - in scope: friendlies, Nations Leagues, World Cup and continental qualifying, continental finals;
  - not in scope: World Cup finals (n 64).
- **First real forecasts, in the working copy, not live:**
  - 85 national-team matches, Nov 11–14;
  - 3 club matches on Nov 20;
  - further days publish as earlier results arrive.
- **Browser** (integration preview on the real working store, 1024/1280/1440/1920/390 px):
  - the panel shows exact 3-way probabilities, the 90-minute rule and the label;
  - World Cup finals show the scope note;
  - 0 page errors, 0 horizontal overflow;
  - the pre-existing soccer-header overflow is fixed: the picker is capped and the header wraps.
- **Screenshots:** `docs/evidence/international-2026-11-08/app-*.jpg`.
- **Test-time fixes found by the browser:**
  - the API competition pattern and the response Literal were club-only; regression test
    `test_the_api_serves_national_team_competitions` failed first;
  - "results through —" on national-team cards.
- **Not live:** creating `data/soccer.sqlite` and the hourly cycle are activation steps (SOCCER_LIVE_PLAN §5) after the
  delivery release.

## 10. Gap found at the end
Sprint1 changed live at 18:20–18:35 ET, by another
  session: stale-source delay and re-trigger, 4-leg tickets. Files: `app/tickets.py`, `app/telegram.py`,
  `app/telegram_card.py`, `tests/test_tickets.py`, `tests/test_telegram.py`, `docs/TICKETS.md`. The integration tree
  (gated `06dcc1d2`) has the earlier versions of these files. Re-merge them, rerun the focused suites and one gate before
  review or release.
