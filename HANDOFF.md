# Current handoff

## Ledger: quote attached when the bookmaker later posts the model's line — Claude (Fable 5.1), 2026-10-03 21:25 UTC
- **Dev checkout only; NOT in the release and NOT in tonight's scheduled move (target stays `5534c34`).** It changes how the
  market-forecast ledger decides "same forecast", so it should get an independent review before a release move.
- `ledger.py` `record_market` (not identity-hashed): the idempotency key never included `line_source`, so when the first
  two-hour cycle had no bookmaker line and the bookmaker later quoted the same number, the retry returned the stored
  model-only row and the game stayed out of the bookmaker comparison. Now, only in that case (stored row `model`, new row
  `market`, same key), the quoted forecast is recorded once under `sha256(key + "\x1fline_source=market")`. Existing keys are
  unchanged, so no existing row is re-recorded; a retry returns the quoted row; the model-only row stays.
- Test `test_a_quote_posted_later_at_the_models_own_line_is_recorded_once_with_the_quote` fails on the previous
  `ledger.py` in a scratch copy. Full backend suite run directly (empty scratch data dir): **570 passed**; frontend
  typecheck and build ok. **`check_project.py --ready` itself exits 1 right now** at its structure guard, before any test:
  "Unexpected top-level directory: .claude" (`.claude/scheduled_tasks.lock`, created 21:09 UTC by a Claude session's
  scheduler, not by this change). The guard was not edited; the folder was left alone. Until it is removed or allowed,
  every `--ready` run fails for that reason.
- Not changed: the goal-model regularisation (`GLM_ALPHA`, `poisson_draw` alpha). That is a modelling choice in
  identity-hashed files; evaluate on earlier seasons first and batch it with any other hashed edit.

## Scheduled release move to `5534c34` after tonight's games — Claude (Fable 5.1), 2026-10-03 20:50 UTC
- The user plugged the Mac in and asked for the release to be moved after the games finish. A one-off script is running
  detached: `~/nba-predictor-backups/move-release.sh` (under `caffeinate -i -s`), log `~/nba-predictor-backups/release-move.log`.
- **It waits** (checks every 5 minutes) until all 13 NHL games of 2026-10-03 are final in the release API, no non-preseason
  game of either sport starts within 3 hours, and no `app.cli` job is running; it never moves after 13:00 UTC Oct 4.
- **Then:** holds both job locks, `git -C ~/nba-predictor-live merge --ff-only 5534c34`, `npm run build` (no `npm ci`:
  package files are unchanged), kickstarts `api` and `web`, runs `release-check` for both sports (must report definitions
  NHL `3e2504c4d655c41b`, NBA `1215f3641ad63e4f`) and checks the API serves the new `parts` field.
- **On any failure** it rolls back to the previous commit, rebuilds, restarts and logs `ROLLED BACK` (or `ROLLBACK NEEDS
  ATTENTION`). To cancel before it acts: `pkill -f move-release.sh`.
- Before scheduling: `check_project.py --ready` on `5534c34` in an empty scratch data dir: exit 0, **569 passed**. No
  identity-hashed file or migration differs between `948f1be` and `5534c34`; `requirements-release.txt` was added in
  `64b7b2c` and is the file the release venv was built from (no sync needed). The script's `ready` and `check` functions
  were dry-run at 20:48 UTC (not ready; check ok). **Next reader: read the log to see whether the move happened.**

## Tonight's safeguards — Claude (Fable 5.1), 2026-10-03 20:38 UTC
- **Power risk (needs the user):** the Mac was on battery (48%, ~5 h) with a 1-minute idle sleep, kept awake only by the
  agents' short `caffeinate` calls. Started `caffeinate -i -m -t 43200` (pid 15242, 12 h, no sudo) so it does not idle-sleep
  when the agents stop. It does NOT survive a closed lid on battery or an empty battery: plug the Mac in. A sleeping Mac
  records no two-hour forecasts and they cannot be backfilled.
- **Local backup (same disk, not off-site):** `~/nba-predictor-backups/2026-10-03/`: `nba-predictor-all-branches.bundle`
  (`git bundle --all`, verified) and `nba.sqlite` / `nhl.sqlite` taken with SQLite's backup API from query-only connections
  (integrity ok; row counts and schema 23 equal the live stores at 20:38 UTC). The repository still has no remote.
- **Verified Opus's `64b7b2c`:** release `~/nba-predictor-live` @ `948f1be`, clean; five jobs + `api` (:8100) + `web` (:4173)
  loaded; `release-check` exit 0 for both sports under the release venv; definitions unchanged; scikit-learn 1.9.1 in both
  venvs. Not re-checked: the 43-package pin, the 565-test run in the release venv.
- **Deliberately not done:** moving the release to `4a153e8` (presentation fixes) inside tonight's forecast window. Do it
  after the NHL games finish (~05:00 UTC), with the README "Pinned release" steps; no identity-hashed file is involved.

## Release isolation completed: own venv, always-on API + app, moved off Desktop — Claude (Opus 5.5), 2026-10-03 20:20–20:35 UTC
- **Release worktree moved** `~/Desktop/nba-predictor-live` -> **`~/nba-predictor-live`** (`git worktree move`; still `release` @
  `948f1be`, clean). Reason: the new `web` service's `node` hung in state `U` reading the Desktop checkout (macOS privacy
  permission never granted to that binary); after the move it started at once.
- **Own venv** `~/nba-predictor-live/backend/.venv` from `backend/requirements-release.txt` (43 packages, `uv pip freeze` of the
  dev venv, identical versions verified by diff). With it: `release-check` nba/nhl no problems, definitions unchanged (NBA
  `1215f3641ad63e4f`, NHL `3e2504c4d655c41b`), the release commit's full suite **565 passed** (isolated).
- **Always-on services** (`scripts/launchd/com.nba-predictor.{api,web}.plist`, KeepAlive + RunAtLoad): API :8100 and built app
  :4173 (proxies /api to :8100) from the release. Checked: health 200, app 200, deep link 200, proxied record 57/13/44; each
  killed once with -9 and restarted by launchd in ~2 s. The five jobs now use the release venv and `ProcessType` Standard
  (Background had deferred the NHL cycle ~7 min). First runs after the switch: NBA 422 / NHL 155 recorded release `948f1bec`
  (quota skips). Plist backups: `data/backups/launchd-before-release-venv-20261003/`. README "Pinned release" updated.
- **Items 3–4 were already done by Fable** (injury collector newest-first in `948f1be`, reviewed correct by me; stray worktree
  removed). The release lags `4a153e8` (presentation only) until the next deliberate move.
- **Open:** always-on host (user chose option A: a server; provider not chosen yet). The Mac must be awake until then.

## Open presentation items closed — Claude (Fable 5.1), 2026-10-03 20:05–20:30 UTC
- At the user's request ("fix the still open items"). API/frontend only: `providers/real.py`, `schemas.py`,
  `agreement_tracking.py` (output flags only), frontend. No identity-hashed file, job path, migration or stored row changed;
  the release stays at `948f1be` (nothing here needs a release move). Definition still NHL `3e2504c4d655c41b`.
- **Pre-game archive lead-time bands (registered in EVALUATION_PLAN) are now built:** `parts` on `/api/predictions`
  (live, pregame) and `/api/market-predictions` (pregame): 1 day / 2 days / 3–7 days by Eastern-date lead; rows carry
  `lead_days` and `horizon_hours`. Nothing dropped; parts add up. Live NHL winner: 13 / 13 / 31 = 57 (the registered counts).
- **NBA backtest scope:** backtest rows carry `period`; the record is also split into validation (seasons used to choose the
  model) and held-out test. Live: 6,605 = 3,962 validation (64.3%) + 2,643 test (67.2%); the test part equals Performance.
- **Bookmaker-implied rate:** market records add `quoted_decided`, `quoted_wins`, `mean_implied`. Live two-hour puck line:
  5–0, bookmaker-implied 63.7% on those five picks, shown under the record.
- **Reserved seasons:** folds of the chronological check and the joint later-season evaluation carry
  `reserved_test_season`; the UI marks them "reused test season"; the button reads "Explore joint rules on later seasons
  (not a validation)". The frozen primary model's one-time test is unchanged and still labelled as such.
- **Cards:** a finished game without a two-hour forecast says so (no "yet"); the market figure shows its quote time.
- **Stale preview stopped:** the :8032 API (pid 58379) and :5182 Vite (pid 57970) left from the morning are killed.
- Tests: 4 added to `tests/test_review3_fixes.py` (25). `NBA_DATA_DIR=<scratch> … check_project.py --ready`: exit 0,
  typecheck + build ok, **569 passed**. :8000 restarted 20:21 UTC; browser (desktop) shows the three archive bands, the
  NBA backtest split, the implied-rate sentence, the finished-game note (8 of 8 Oct 1 games) and quote times. Not seen in a
  browser: the "reused test season" flag (the API returns it for 2025 and 2026); phone width.
- **Still open:** the winner/line schedule mismatch (identity-hashed; frozen until ~05:00 UTC, and unreachable today).

## Injury collector newest-first, release moved to `948f1be` — Claude (Fable 5.1), 2026-10-03 19:30–20:00 UTC
- At the user's request ("commit, review, release move"). `injuries.py`: pending report slots are collected newest first
  (`_newest_first`; budgets unchanged), so missing older slots cannot delay the latest report the injury-aware forecast
  needs within 60 minutes. Not identity-hashed; NBA only. Commits `0e9c6d5` + `948f1be` (review follow-ups).
- **Review:** independent read-only review (general-purpose subagent, no code run): PASS. Follow-ups applied: exact order
  assertion restored in `test_injuries.py`, stale comment, `docs/INJURY_COLLECTOR_PLAN.md` now says newest first. Noted
  trade-off (reasoned, not executed): after a gap of ~21 h or more on a game day, a few of the oldest slots can age past the
  24 h lookback before they are requested.
- **Checks:** `NBA_DATA_DIR=<scratch> … scripts/check_project.py --ready` on the code of `0e9c6d5`: exit 0, **565 passed**,
  typecheck + build ok. After the follow-ups (tests/docs only): 53 targeted tests pass. The new starvation test fails
  against the previous `injuries.py` in a scratch copy.
- **Release move 19:57:28 UTC:** both job locks free, no CLI job running, first game 23:00 UTC. `git -C ../nba-predictor-live
  merge --ff-only 948f1be`; `release-check` exit 0 for both sports: no problems, schema 23 = 23, definitions unchanged
  (NHL `3e2504c4d655c41b`, NBA `1215f3641ad63e4f`), release checkout clean. No identity-hashed file differs from `9fc202c`.
  Roll back: `git -C ~/nba-predictor-live checkout -B release 9fc202c` (the release checkout moved to `~/nba-predictor-live`
  at 20:2x UTC; `../nba-predictor-live` in this entry is its earlier location).
- **Also:** the unused worktree `../nba-predictor-release` was mine (created 19:26 UTC to test a pinned checkout) and is
  removed. :8000 restarted 19:56 UTC on the deadlock fix: 60 concurrent requests all 200 in 1.35 s, health ok. The earlier
  clean pass on `8e4ff1d` was 555 passed. Tree-model outputs are bit-identical across runs and 1/4/default threads
  (scratch test), so the float ledger-conflict hypothesis is not supported.
- **Still open:** NBA backtest record scope (History 6,605 vs Performance 2,643, unlabelled); horizon-band breakdown
  registered in EVALUATION_PLAN but not built; "Test joint rules on later seasons" wording; stale preview pair on
  :8032/:5182; the winner/line schedule mismatch (identity-hashed, frozen tonight).

## Pinned release for the scheduled jobs — Claude (Opus 5.5), 2026-10-03 19:45–20:00 UTC
- **Live jobs now run `../nba-predictor-live` (branch `release` @ `9fc202c`), not this checkout.** All five installed plists
  (`daily`, `injuries`, `nhl-daily`, `nhl-odds`, `odds`) point there, with `NBA_DATA_DIR` set to this checkout's `data/`,
  `NBA_RELEASE_ROOT`, `NBA_RELEASE_GUARD=1` and the shared venv (absolute path; `nhl-daily`'s relative `.venv` fixed). Same
  labels, schedules, logs and existing env. Originals: `data/backups/launchd-before-release-20261003/`. Repo copies in
  `scripts/launchd/` updated (`nba-markets` too, still not installed). Update / rollback: README "Pinned release".
- **Guard (`app/release.py`, `cli.py`, `jobs.py`; not identity-hashed):** scheduled commands refuse unless the imported code
  is the clean release checkout and the store schema equals the release's (failed job run, before any request/lock/migration);
  each job run records `release`. `release-check` is read-only. Tests `tests/test_release_guard.py` (6); full suite 565 passed.
- **Verified:** dry run with launchd's minimal environment: app imported from the release, clean, schema 23 = 23, research
  definitions unchanged (NHL `3e2504c4d655c41b`, NBA `1215f3641ad63e4f`). Switched at 19:51:25 UTC with both store locks free
  and no CLI job running. First cycles: NHL odds run 152 success (release `9fc202c…`, forecasts skipped: no game within 2 h,
  selector success under `3e2504c4…`, props after); NBA odds run 417 skipped by the 10-minute quota (release recorded). No new
  definition rows; the release checkout stayed clean.
- **Not mine:** an undocumented, unused worktree `../nba-predictor-release` (detached at `8e4ff1d`, created 19:26 UTC by another
  agent) was left untouched. `injuries.py` / `test_injuries.py` edits by another writer remain uncommitted and are NOT in the
  release; they reach the jobs only via a reviewed commit and a release move.
- **Next:** one writer at a time; move the release only between cycles (README steps); no identity-hashed edit before
  tonight's NHL games finish (~05:00 UTC).

## API deadlock fix — Claude (Opus 5.5, took over at the user's request), 2026-10-03 19:25–20:05 UTC
- **Critical bug in `6e70a4e`:** `get_provider` took a process-wide `threading.Lock` inside FastAPI's worker pool. A burst
  larger than the pool (40 threads) parked every worker on the lock while the holder needed one for its endpoint: on a store
  copy, 60 concurrent `/api/meta` requests all timed out (40 s) and `/api/health` stopped answering until a restart. The live
  :8000 (restarted 19:18:53 UTC on that code) was exposed; two dev-mode Performance tabs (~28 requests each) could hang it.
- **Fix (`main.py`, not identity-hashed):** the same process-wide turn, but waited for on a thread of its own capacity
  limiter (never the shared pool); release guaranteed in `finally` even if the request is cancelled after acquiring; a
  request whose client already hung up while queued is not served (499), so abandoned heavy requests no longer block the
  queue (20 hung-up joint-evaluate requests had delayed the next request > 10 s; now 0.83 s).
- **Tests:** `tests/test_api_concurrency.py` (3): 8 concurrent requests with a 3-thread pool (hung, killed after 90 s, on
  `6e70a4e`; passes now), abandoned-while-queued -> 499 and the turn freed, store error inside an endpoint -> 503 and the turn
  freed. Fable's `test_store_backed_requests_are_served_one_at_a_time` still passes. Full isolated suite: **559 passed**,
  typecheck ok. Store-copy server: 60 and 120 concurrent requests all 200 (1.3 s / 2.3 s), health ok, page burst 0.9–1.0 s.
- **Fable's `6e70a4e` otherwise verified:** 16 of its 21 new tests fail on `9386f80` (the other 5 cover earlier fixes; the
  opening-price one can never fail: SQLite's index returns 'current' first). Fixes reviewed correct: read-only CLI, lock-busy
  trace, late/corrected result stamping, one-definition rule record, freshness as missed games, earlier-line-rule label,
  50% no-pick, Brier 0–2 labels, push-free quote basis, demo store lock, props after forecasts (except `jobs.scheduler`'s odds
  loop, still props-first; launchd does not use it). Candidate fingerprint fix is partial by design (existing artifacts kept).
- **Not mine, left uncommitted:** `injuries.py` / `test_injuries.py` (newest report first) — another writer was editing them
  at 19:30 UTC. Live :8000 needs a restart to load this fix.

## "Fix them all" batch — Claude (Fable 5.1), 2026-10-03 18:58–19:25 UTC. **Committed as `6e70a4e` (tree clean). RESEARCH DEFINITIONS CHANGED.**
- **FREEZE until tonight's NHL games finish (~05:00 UTC Oct 4):** no edits to identity-hashed files (`models/*.py`,
  `backtest.py`, `agreement.py`, `availability*.py`, `combine.py`, `research_live.py`, `markets.py`, `market_live.py`,
  `retrain_cadence.py`, `fit_register.py`), or tonight's games split across two research definitions. The scheduled jobs
  run this checkout, so any backend edit is live at the next cycle. The one test Opus saw fail mid-edit
  (`test_two_hour_cycle_records_tracking_after_the_pool`) passes alone and in the final full run (555 passed, no file
  changed afterwards except this handoff).
- **Research identity reset (user asked for the latent hashed-file fix):** `models/logistic.py`, `models/hockey.py`,
  `models/score_candidates.py` now compute a game's rest days once (`rest_seen`), so predicting a game twice no longer reads
  as a back-to-back. Applied in one step at 19:10:10 UTC. New definitions: **NHL `3e2504c4d655c41b`** (was `8d60d7aebdb6a68d`,
  which had 0 cohorts), **NBA `1215f3641ad63e4f`** (was `21025be327b4f408`). Single-prediction walks are unchanged: the full
  suite passed on a scratch copy before applying (548 + the 3 path-bound tests) and after.
- `ingest.py`: a result that arrives late for a game the store already tracked before its start is stamped with its retrieval
  time (never backdated to start + 4 h; first-met-final backfill unchanged); a corrected final score becomes available at
  the correction's retrieval time (`result_first_seen_at` keeps the first sighting). Existing rows are not rewritten.
- `candidates/live.py`: new fits are named by a fingerprint that includes training labels (`r2…`), so a corrected score
  gives a new fit; artifacts fitted under the ids-only name stay in force (no mass refit).
- `rule_tracking.py` + `/api/research/rules?definition=`: the combination-rule record covers ONE research definition (newest
  with cohorts by default), lists the others, and the card has a version selector. Live: `7f9821fa…` 3 games, `5040a9e7…` 3.
- `main.py`: store-backed requests are served one at a time (`_STORE_REQUESTS`). Root cause reproduced on a store copy
  (`scratchpad/perf_probe.py`): 13 concurrent requests 9.2 s vs 0.23 s serial; the time is in the reads (provider init +
  endpoint), NOT in open/journal pragma/close, and a shorter switch interval does not help. Live burst 7.3 s → ~1.0 s;
  Performance page settles in ~2 s (was 16–30 s). Demo `/api/research/retrospective` returns a stub (no real study in demo).
- `providers/real.py` sources: "Elo forecasts" is stale only when an eligible game started in the last 24 h without a
  two-hour forecast; no eligible game is fresh. `training_monitor.py`: odds is not stale after a healthy skip
  (`last_checked`), a lock-busy skip does not count. `tracking.py`: exactly 50% is no pick in the threshold table.
- `jobs.py`/`cli.py`: prop lines are collected after the two-hour forecasts (`collect_props`, merged into the odds run's
  detail), never in front of them. `research_demo.py`: the demo store is built once under a lock and its connection serves
  one request at a time.
- Frontend: joint explorer keeps the model list mounted (focus stays) and disables/clears invalid upper bounds; market cards
  show the model's push-free pair next to a bookmaker pair when the line can push; `Research.tsx` version selector.
- Tests: `tests/test_review3_fixes.py` now 21 tests; the 15 for uncommitted work fail against HEAD backend code in a scratch
  copy. `test_api_research.py` updated for the demo stub. `NBA_DATA_DIR=<scratch> … check_project.py --ready`: exit 0,
  typecheck + build ok, **555 passed**. API restarted 19:19 UTC. NBA cycle 19:13 UTC ran clean on the new code.
- **Deliberately not done:** F (injury collector newest-first) — four existing tests encode oldest-first as designed and the
  starvation is unverified off-season; reverted, needs an in-season check or an explicit design decision. Schedule mismatch
  between winner and line research models — unreachable (the forecast refuses a team with an unfinished earlier game).
  Float ledger conflicts — no occurrence in any log; the ledger tolerance was not loosened. Pinned release runtime for the
  scheduled jobs — an operational launchd switch, not done inside tonight's forecast window.

## Read-only CLI commands, lock-busy trace, Brier scale labels — Claude (Fable 5.1), 2026-10-03 18:50–19:05 UTC
- At the user's request (Opus's findings A and B). No identity-hashed file, migration or stored forecast changed; live
  definition still `8d60d7aebdb6a68d`. **Uncommitted.** `cli.py`/`jobs.py`/`db.py` are what the scheduled jobs run.
- **A.** `cli.py` `READ_ONLY_COMMANDS` (score-markets, score-near-tipoff, thresholds, research-report, pool-report,
  pool-roster, tracking-report, coverage) now run on `db.connect_readonly` (`PRAGMA query_only`, no journal change), with
  no job lock and no migration, so a long report can no longer make a two-hour cycle wait or skip. `score` records a job
  run, so it still takes the lock (help text corrected). An `odds` cycle that gives up on a busy lock now writes a
  `job_runs` row (`odds`, `skipped`, `lock_busy: true`) via `jobs.record_lock_busy`; it never creates or migrates a store.
  Not changed: research/backtest commands that write still hold the lock for their whole run — run those on store copies.
- **B.** Research and tracking tables label their Brier column "Brier (0–2 scale)" with a tooltip (it is the multi-outcome
  sum: twice the 0–1 Brier on the Performance tiles for a two-way result); the market Performance tile hint says the same.
- Tests: 2 added to `tests/test_review3_fixes.py` (9 total); both fail against HEAD `cli.py`/`db.py`/`jobs.py` in a scratch
  copy. `NBA_DATA_DIR=<scratch> … scripts/check_project.py --ready`: exit 0, typecheck + build ok, **543 passed**.
- Live: `python -m app.cli --sport nhl score-near-tipoff` ran read-only against the live store (5 forecasts, 0–5).
- **Not fixed (low, need a decision):** C — a corrected past score does not refit a candidate artifact (fingerprint hashes
  game ids only); D — a result first seen more than 24 h late is backdated to start + 4 h (timing rule).

## Label-only fixes: earlier line rule, always-50% baseline — Claude (Fable 5.1), 2026-10-03 18:38–18:50 UTC
- At the user's request (Opus's two new findings). No identity-hashed file, stored row, line or score changed; live
  definition still `8d60d7aebdb6a68d`. **Uncommitted.**
- `providers/real.py` + `schemas.py`: a market forecast with a model-only line generated before `MODEL_LINE_RULE_SINCE`
  (2026-10-02T03:18:53Z, commit 2df4f75) carries `earlier_line_rule: true` and an extra limitation sentence; market
  Performance adds a limitation with the count. The always-50% baseline's `accuracy` is null (it never picks a side); its
  Brier score and log loss are unchanged.
- Frontend: `MarketView.tsx` shows "model-only · earlier line rule"; `PerformancePage.tsx` explains the dash; `types.ts` field.
- Tests: 2 added to `tests/test_review3_fixes.py` (7 total); both fail against HEAD `real.py` + `schemas.py` in a scratch copy.
  `NBA_DATA_DIR=<scratch> … scripts/check_project.py --ready`: exit 0, typecheck + build ok, **541 passed**.
- Live (:8000 restarted 18:42 UTC, no job running): NHL pre-game archive, latest row per game: 202 labelled (spread 44, total
  44, home goals 57, away goals 57), two-hour rows 0 labelled, every record unchanged. Browser: History home goals shows the
  label on all 57 rows; backtest Performance shows "Always 50% — 0.250 0.693" with the note.

## Review fixes (market price, 3-way filter, forecast note, card agreement, injury-member refusal) — Claude (Fable 5.1), 2026-10-03 18:03–18:20 UTC
- At the user's request, from Opus's read-only logic review. All changes are outside the identity-hashed files; the live
  definition is still `8d60d7aebdb6a68d`. Committed on their own (see `git log`); the first scheduled NBA cycle on the
  edited `jobs.py` (18:21 UTC) finished with every step success/skipped and no traceback.
- `providers/real.py`: (1) NBA Performance market comparison orders by `price_label = 'current'` at equal retrieval times
  (the old query returned the current price only by index order: latent, not wrong on stored data); (3) forecast notes no
  longer say the daily job will record a forecast; (4) `_agreement`: a game card's winner agreement uses one research
  definition (the newest) when the latest cohort would mix definitions. `agreement.py` itself is unchanged (hashed).
- `ConfidenceFilter.tsx`: (2) the 3-way regulation market offers All / 40 / 45 / 50 / 55 / 60%.
- Tests: new `tests/test_review3_fixes.py` (5). All 5 fail against HEAD `real.py` + `jobs.py` in a scratch copy (the price test stores the opening row under a provider
  name that sorts first, which makes the old query return the opening price).
  `NBA_DATA_DIR=<scratch> OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 backend/.venv/bin/python scripts/check_project.py --ready`:
  exit 0, typecheck + build ok, **539 passed** (final run, after the jobs.py change).
- Live: :8000 restarted 18:08 UTC (no job running). NBA Oct 20 cards show the new note; NHL 3-way archive: 40%+ 57 picks,
  45%+ 34, 50%+ 13, 55%+ 2, 60%+ 0 (browser matches API); spread levels unchanged.
- `jobs.py` (not hashed; **scheduled jobs run this file from the working tree**): `_refusal_is_not_failure` — when the
  agreement step failed ONLY because `availability_two_hour` raised its own `AvailabilityError` (no usable injury report,
  unmatched player, …), the step is 'partial' with the reason under `refused`, and the two-hour run is no longer 'failed'.
  The error stays in `errors`, so cohorts still record the member as missing and Training status still lists it. Any other
  member error, a crash of that member (another exception type) or a skipped core member still fails the run. NBA only in
  effect (NHL has no refusing member). Both new tests for it fail against HEAD `jobs.py` in a scratch copy.
- **Not done, needs the user's decision:** (5) a corrected final score keeps the original result time (`ingest.py:358-359`) —
  needs a migration and touches the timing rules. The three small inconsistencies (3-way draw picks, plurality on the card,
  exactly-50% picks) were left as they are; aligning them means editing hashed files.

## Record counts and pre-game archive labels — Claude (Opus 5.5, sole writer), 2026-10-03 16:20–16:50 UTC
- **Approved scope only:** explicit record counts and "Pre-game archive" wording. Also on this branch: `2bcab94` (B0, docs-only
  horizon-band registration in EVALUATION_PLAN.md). Nothing else from the audit plan was started.
- **API (additive, contract compatible):** winner `record` adds `pending`, `void`, `unscorable`
  (selected = settled + pending + void + unscorable; settled = wins + losses; the `games` CHECK makes winner unscorable 0).
  Market `record` adds `unscorable` and no longer folds it into `pending`
  (selected = W + L + push + voids + pending + unscorable). PredictionRecord gains `unscorable`. Policy id `pregame` unchanged;
  no stored row, population or filter changed. Titles/notes say "pre-game archive" (some forecasts made several days ahead).
- **UI:** record lines show pending · void · unscorable and a scope ("All dates", or the History date/team filter). The archive
  toggle, cards, game page, market blocks and agreement line say "Pre-game archive"; History/Today/Game explain lead times.
- **Identity:** no hashed file touched (changed: schemas.py, providers/real.py, frontend, tests). Live definition unchanged.
- **Checks:** new `tests/test_record_counts.py` 4 tests failed before the fix, pass after. Isolated readiness
  (`NBA_DATA_DIR=<empty scratch>` …`check_project.py --ready`): exit 0, typecheck/build ok, **534 passed**. Snapshot API/browser
  results: docs/SMOKE_TEST.md (latest section).
- **Self-review fix:** demo mode wrongly showed archive wording (Game page sentence, Today slate); fixed and re-verified in a
  temporary demo API; readiness rerun exit 0, 534 passed (SMOKE_TEST).
- **Independent review and follow-up — Claude (Fable 5.1), 2026-10-03 16:35–18:10 UTC:** PASS. Reviewer checks: 72 targeted
  tests pass in temporary stores; the 4 new tests fail against the pre-change `real.py`/`schemas.py` in a scratch copy. The
  user asked for the :8000 restart: done 16:49 UTC (no job running; same command, `NBA_API_MODE=real`; definition still
  `8d60d7aebdb6a68d`). Live browser: NHL History/Today read "57 picks · 4–9 (13 settled) · 44 pending · 0 void · 0 unscorable";
  two-hour puck line "5 picks · 5–0–0 · 0 pending"; no "night-before" text. Small fixes by the reviewer at the user's request:
  winner History row shows "Not scorable" for an unscorable row (matches the record); `main.py` policy description and three
  README lines now say pre-game archive. The batch was committed by Opus as `d66bebc` while this review was finishing; these reviewer fixes are the commit after it.
- **Remaining:** the earlier joint agreement/confidence work (agreement_tracking.py, the joint endpoint in main.py, Tracking.tsx,
  client.ts, PerformancePage.tsx, styles.css, test_joint_agreement.py, MODEL_SELECTION.md and its HANDOFF/SMOKE/README sections)
  is still uncommitted and awaits its own review. No void or unscorable case exists in real data (synthetic tests only); the
  frontend has no test runner. Freshness, backtest scope, concurrency, joint explorer, demo, release isolation: not started.

## Count clarification — Devin, 2026-10-03
- User asked why All shows ~57 picks but only ~12 settled. Verified the running NHL night-before (`pregame`) winner API:
  57 selected, now 13 settled (4 wins / 9 losses), 44 without recorded outcomes (Oct 3–8), 0 voids. All removes the
  confidence filter; it does not mean all games have settled. No app changes/check reruns needed. Next tasks unchanged below.

## Joint agreement/confidence completion — Devin, 2026-10-03
- Continued Codex's uncommitted feature on `838f879`; preserved his existing edits. Performance now has exact 2–8-model
  subsets, independent agreement/confidence ranges, historical vs recorded-live classifications and earlier-only rule
  selection/next-season comparison. Research only: no live recommendation, model, selector or forecast-ledger change.
- Fixed the joint search's top confidence band excluding exactly 100% (new failing-then-passing regression in
  `backend/tests/test_joint_agreement.py`). Fixed Research definition losing focus after one typed character, and added
  an error-state filter reset in `Tracking.tsx`.
- Codex's saved readiness log had 527 passed / 2 missing-Optuna failures. Installed existing pinned `optuna==5.0.0`
  with `uv pip install --python backend/.venv/bin/python 'optuna==5.0.0' --exclude-newer 2026-09-26`; documented the
  prerequisite in README. No dependency declarations changed and no training/ingestion/forecast job was run.
- Verification: structure passed; joint tests **13 passed**. Final
  `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 backend/.venv/bin/python scripts/check_project.py --ready`: **exit 0**,
  typecheck/build passed, **530 tests passed**, one existing Starlette/httpx warning. `git diff --check` clean.
- Running API: **11/11** market paths checked; NBA 6,605 historical games/3 later folds, NHL 11,870/7 per market.
  Live: 0 NBA, 5 NHL rows per market; NHL has two separate method/member-version groups. Both historical tracking tables
  serve current calculation results. No exhaustive subset search or prospective-performance claim.
- Browser: NBA/NHL joint feature at 1280×900 and 390×844 passed selections, chronological evaluation, research/live
  separation, loading/error/empty/retry/reset, typing and overflow checks; no JS page errors. Desktop three-way/line/sport
  switches and demo navigation/labels also checked. Exact commands and limitations: `docs/SMOKE_TEST.md` completion section.
- **Remaining:** extra demo navigation harness failed while awaiting existing `/api/research/slate`; direct curl also
  timed out after 15 s (exit 28). Root cause unconfirmed; that harness's phone navigation/market checks remain unverified.
  The joint feature's phone tests passed separately. No physical-phone test. Historical results remain exploratory.
- Real API restarted with current code on :8000; frontend remains http://127.0.0.1:5173 (Performance → Explore model
  agreement + confidence ranges). Temporary demo API stopped. Changes left uncommitted; nothing pushed/deployed.
- **Next:** Codex independent review of the uncommitted feature and follow-up fixes; separate investigation of demo
  research initialization timeout, then finish the additional navigation smoke run. Do not retrain or promote rules.

## Reconciliation of parallel work (option 1) — Claude (Opus 5.5, session andry-03), 2026-10-03 02:09–03:40 UTC
Single writer established: sessions -09 and -92 confirmed they made no commits; -c7 confirmed it made 803cee0..aa1e1b4 and
went read-only; all three stay read-only until reassigned (Codex process not reachable; no Codex commits seen). Branches
preserved: `backup/shadow-season-aa1e1b4`, `backup/pool-selector-20f27fe`. Live fast-forwarded aa1e1b4 → 914c827 at
03:23:24Z (no job running; backups `data/backups/{nba,nhl}-before-reconcile-20261003.sqlite`, integrity ok, schema 22).
- **Canonical:** tracking-v2 (`agreement_tracking`, sealed registry f07a54a0…, migrations 0021/0022) and the training
  status (`training_monitor`, `/api/research/training`). Removed as duplicates: `discovery_tracking.py`,
  `/api/research/discovery`, DiscoveryCard. **Never fast-forward live to `pool-selector`.**
- **Kept with original identities:** discovery-v1 (MODEL_SELECTION §20, registered as §18 in 2e55096), tuning-v1 (§21,
  registered as §19 in 942676c), gap-fill, daily-v1; outputs copied to `data/reports/{discovery-v1,discovery-v1-run1,
  tuning-v1,gapfill-v1,daily-v1-*}` and `data/research/tuning/`. Their results are not relabelled as tracking-v2 results.
- **Ported behaviour:** tracking-v2 reports the required descriptive grid (cumulative 50–90%, reg3 35–70%, 5-point bands
  incl. NHL [1/3,0.35) and [0.35,0.40)); `sealed` cells only in the leaderboard and checks; summaries rebuilt
  (`tracking-historical`, grid `report-grid-v1`, 03:23–03:26Z).
- **Fixes (each with a test that fails on aa1e1b4):** timing bug (live run 108: a game starting mid-cycle → skip, no
  `AttributeError`, trigger refusal = skip, no member forecasts for a started game; new core research definition, selectors
  have no decision in force until Monday 14:00Z); game card latest cohort per market; prop collection starvation;
  monitoring suffix constant. Tooling report corrected: LightGBM/XGBoost deferred/untested.
- **Verification:** `check_project.py --ready` on 914c827 exit 0 (490 passed). Rehearsals on fresh copies of the live
  stores (schema 22), final code: NHL 8 games → 48 cohorts, 168 candidate rows, 24 pool selections, 1,976 tracking rows, 0
  errors; NBA 1 game → 5 cohorts, 29 candidate rows, 583 tracking rows; second identical cycle 0 new rows in every table
  for both. Independent read-only review (Opus 5.5): 0 critical/high; 4 medium + 6 low → all resolved on re-review.
  Live after merge: NHL odds/refresh 03:26Z and NBA odds 03:33Z success; tracking report serves the new grid.
- **Remaining:** first live research cycle on the new code is the NHL window ~21:00Z today (unverified until then);
  the API preview on :8000 (started by -c7) still runs the old code until restarted; LOW: trigger refusal matched by
  message text; a plain-read window before member writes (guarded by the ledger); NGBoost not installed live.

## Agreement tracking tracking-v2, feature tranche, training status, prop collection — Claude (Opus 5.5), 2026-10-03 00:00–01:30 UTC
Single writer. Live checkout `shadow-season` (backend code changes developed in worktree `../nba-predictor-features`, branch
`features-v1`, fast-forwarded after tests). Commits 803cee0..(this handoff) on top of 31c19af.
- **Running now:** every two-hour cycle records `agreement_observations` (migrations 0021+0022, append-only, one row per
  registered group × research cohort × stored-input set; thresholds/bands derived when read). First live rows: run 108,
  nhl:2026020021, 247 observations. Odds jobs also store raw ESPN prop-line snapshots for games ≤ 2 h away (collection only).
- **Built:** sealed group registry `models/tracking_groups.json` (MODEL_SELECTION §18, registered before any result; ≤ 500
  configurations per sport/market: NBA winner 83 groups×6 thresholds, NBA lines 125×4, NHL winner 83×6, reg3 48×5, NHL lines 29×4;
  skipped groups listed). Rules: single, unanimous (min member prob), average, majority (votes, ties, abstentions; mean prob of
  the majority side over all members), cohort/expanded stacker, pool stacker (frozen weights fitted on saved OOF tables).
  Historical exploratory results from the candidate-pool OOF tables (no refit), always separate. API `/api/research/tracking`
  (+`/check`, `/game/{id}`), `/api/research/training`; Performance "Agreement tracking" (filters: sport, market, rule, model,
  threshold/band, exact line, version, ET dates, historical vs prospective; exploratory leaderboard vs active recommendation) and
  "Training and updates"; Game page tracking card. docs/MARKETS.md tracking audit + player-prop prerequisites.
- **Features (MODEL_SELECTION §19, registered ffe6a87 before code; 4 of 12 configurations per sport used):** NBA
  quality-weighted absences: no evidence beyond `avail_v1` (−0.0016 [−0.0037, +0.0005]); NHL shot volume / PP / SH goals: no
  standalone evidence; inside the average with the anchor, a small combination-only lead for reg3 and puck line (≈ −0.0005,
  intervals below 0; reused seasons). Nothing changes live. Results: docs/CHALLENGER_RESULTS.md "Feature tranche".
- **What each model consumes:** unchanged this session — table in docs/EVIDENCE_AUDIT.md §1 plus: NBA injury report only in
  `availability_two_hour` (minutes-weighted); NHL goalie inputs only in `*:nhl_goalie` candidates; PP/PK only as special-teams xG;
  shot volume, goalie confirmation time and player-quality absence weights are not used by any live model.
- **Schedules:** online members update after every result; learned members refit at season start (weekly shadows weekly);
  daily refits not adopted (daily-v1); no new challenger qualified, so no cadence registered for one. Weekly selection
  (Monday 14:00 UTC) unchanged and separate from training; tracking never selects.
- **Checks:** full suite 469 passed (before review fixes); targeted after fixes; `backend/.venv/bin/python scripts/check_project.py --ready` on ba6ad92: exit 0 (structure, typecheck, build, 477 passed); the later side-label change: typecheck exit 0.
  Independent read-only review (Opus 5.5 subagent): 0 critical/high, 6 medium, 15 low → fixed in c71d387/ba6ad92 (props
  budget/timestamps/markers, per-cohort transactions, per-market historical files, ET dates, band labels, docs); remaining LOW:
  L7 (expanded groups take core-member vectors from the core cohort; same information time), L14 (leaderboard SE treats shared
  games as independent). Evidence: docs/SMOKE_TEST.md (latest section).
- **Remaining / limitations:** no settled prospective evidence yet (all "insufficient evidence"); pre-existing: a game starting
  during a 13-minute NHL cycle gets research skipped and `goals_draw`/`goals_glm` AttributeError (hashed `research_live` code;
  fixing restarts the research definition — needs your decision); historical tracking is night-before, model-only lines, reused
  seasons; NGBoost absent from live candidates; player props not modelled (prerequisites listed); no real phone test.
- **Next:** Codex review of 31c19af..HEAD; after games settle, read `/api/research/tracking` prospective; decide whether to
  register a prospective combination-only test of the NHL special inputs.

## Tooling audit + Optuna tuning `tuning-v1` — Claude (Opus 5.5), 2026-10-03 ~01:45–02:15 UTC
Branch `pool-selector` (worktree), commits 942676c..e22d1b3; **not merged into live** (same as the entry below).
- Audit (MODEL_SELECTION §21, from REPO_REVIEW/§10/FAMILY_AUDIT only): sklearn HGB/ExtraTrees/RF used with fixed settings;
  NGBoost used; CatBoost tested and rejected; Optuna missing → **adopted** (`optuna==5.0.0` in the `research` extra, worktree
  venv only; live venv unchanged); LightGBM/XGBoost **deferred/untested** (distinct implementations, not ruled out by the sklearn HGB result; need system libomp).
- `app/tuning_study.py` (no hashed pool file edited; live candidate identities unchanged): 8 learners (HGB binary/normal/
  reg3/goals, ExtraTrees binary/normal) tuned per season on earlier seasons only, 25 TPE trials; predictions, trials, fits
  saved; CLI `tuning-study` (store copies only), `tuning-evaluate`.
- Result (CHALLENGER_RESULTS "Tooling adoption and bounded tuning"): tuned learners are worse than the best existing member
  alone in every market and no better than their untuned counterparts; inside combinations no rule qualified and with/
  without differences are within ±2 points with mixed signs. Nothing proposed for live recording.
- Checks: `scripts/check_project.py --ready` on e22d1b3 exit 0 (structure, typecheck, build, 466 passed). Outputs are in the
  scratch copy; copy `research/tuning/` and `reports/tuning-v1/` into `data/` on merge.

## Historical training + combination discovery and tracking — Claude (Opus 5.5), 2026-10-03 ~00:00–02:10 UTC
Worktree `../nba-predictor-poolsel`, branch `pool-selector`, commits 2e55096..384868b on top of 31c19af (live). **Not merged
into live** (the previous merge-note commit was blocked as a production deploy; merge left to the user).
- **Timing check (user asked to "finish the timing bug"; none was identified):** every stored row in both live stores; rows
  more than 2 h early exist only before migration 0015 (2026-10-02 03:51Z: 49 night-before Elo + 196 archived research);
  since then 0 rows early or at/after the start in any table. First post-merge NHL cycle 23:31–23:46Z: 0020 applied, 14
  cohorts, 42 candidate rows (all cohort-linked), 6 pool selections, 9 legacy artifacts superseded (digest match 9/9).
- **discovery-v1 (MODEL_SELECTION §20, registered as §18 in 2e55096 before code):** chained chronological out-of-sample tables
  (NHL 2018–26, 11,870 games; NBA 2022–26, 6,605), 22 newly trained combiners (`stack_l2`, `stack_l1`, refitted each season),
  ≤ 6,000 configurations per market, 50–90% thresholds (reg3 35–70%) plus bands, rules chosen on earlier seasons and
  assessed on the next (68 market-folds). One rule qualified once and failed on the later season; no subset beat the best
  earlier single model; NBA line gains are matched by the all-member average (points model ~50/50 at its own line).
  Results: CHALLENGER_RESULTS "Historical training and combination discovery"; inventory docs/TRAINING_INVENTORY.md;
  evidence docs/evidence/discovery-v1/; outputs in the scratch copy (copy to `data/reports/discovery-v1/` on merge).
- **Tracking:** `app/discovery_tracking.py`, `/api/research/discovery` (filters: models, agreement method, threshold or band,
  registered only; historical vs prospective separately; exact lines prospectively, labelled cross-line pooling),
  Performance → "Combination discovery and tracking". 75 rules registered (final selection on all seasons) for prospective
  evaluation. No prediction rows are copied per threshold.
- **Checks:** SMOKE_TEST "Discovery tracking"; `scripts/check_project.py --ready` on 384868b exit 0 (structure, typecheck, build, 464 passed).
- ~~To go live: fast-forward `shadow-season` to this branch~~ **Superseded by the reconciliation entry at the top: never
  fast-forward live to `pool-selector`; its discovery tracking card/API was removed as a duplicate of tracking-v2.**

## Combination objective: fixes, audit, gap-fill study, daily-refit test, selection status — Claude (Opus 5.5), 2026-10-02 ~21:35–23:00 UTC
Single writer (user confirmed other sessions paused). Worktree `../nba-predictor-poolsel`, branch `pool-selector`, commits
a3a597c..d948a49 on top of e6b6abd. **Corrects the entry below:** the 12 h void rule is retired everywhere (`markets.settle`
voids on any start change; `near_tipoff.market_outcome` removed), so the core research definition changed (NHL
`5040a9e7…` → `7f9821fa…`; evidence restarts, old rows kept; no recorded settlement changed: 0 moved starts in either
store). Legacy candidate artifacts are never loaded (superseded by refits, provenance in `artifact_adoptions`).
- **Integrity (Phase 1):** review fixes for e6b6abd (8 tests fail on e6b6abd). Independent review of a3a597c (Opus 5.5,
  read-only): 1 HIGH (dry run destroyed legacy provenance), 3 MEDIUM (clock-dependent candidate idempotency, one game
  blocking a candidate, stale docs) → fixed in e0961c7/d948a49 (3 tests fail on a3a597c); re-review: all RESOLVED,
  remaining LOW items listed under Remaining.
- **Audit (Phase 2):** docs/EVIDENCE_AUDIT.md (per sport × market: implemented / evaluated / recording / eligible / selected).
- **gapfill-v1 (MODEL_SELECTION §16, registered a87d802 before the run):** 10 markets, ≤ 500 configurations each, from saved
  out-of-sample tables (no refit). No pick rule qualified; no new combination-only source; NHL reg3 candidate averages do
  not beat `poisson_draw:nhl_base` alone. CHALLENGER_RESULTS "Gap-filling combination study"; docs/evidence/gapfill-v1/.
- **daily-v1 (§17, registered 931e85a):** NHL — no member supports daily refits (all Bonferroni intervals span 0; `goals_glm`
  slightly worse than season-start). NBA — see RETRAIN_CADENCE_RESULTS "Daily refits". Nothing changes live.
- **App:** `/api/research/status` + Performance "Selection status by market" (rule = exact model/subset, agreement
  requirement, threshold, versions, decided/evidence/next-review dates, record, status, missing inputs). Daily job reports
  newly settled results per fit (`training_check`, never refits).
- **Checks:** see SMOKE_TEST "Review fixes, gap-fill study, selection status" (rehearsal on store copies, browser desktop +
  390 px emulation; no real phone). `scripts/check_project.py --ready` on d948a49: exit 0 (structure, typecheck, build, 452 passed).
- **Remaining:** LOW review items — goalie-listing changes without feature changes do not create a new candidate row;
  `live.score` (discontinued night-before winner records) has no start-change void; `daily-cadence` migrates the store it
  opens (run on copies only); training check counts all non-preseason finals. Untested: 21 NBA line triples per market and
  most 4+-member subsets (listed in the gapfill report); line markets never at historical bookmaker lines. No settled
  prospective evidence yet, so every market shows insufficient evidence (correct).

## Pool selection + audit fixes P1–P14 — Claude (-b6, single writer), 2026-10-02 ~20:15–21:30 UTC (branch pool-selector)
Worktree `../nba-predictor-poolsel`, branch `pool-selector` from 9d3e50e. **Not merged** into shadow-season (live): left for
Codex review. -66 and -6b were paused (messages 20:1xZ; -6b confirmed; -66 blocked on its own prompt with a stand-down queued).
- **Integrity first (each with a regression test that fails on the old code):** P11 a rescheduled game is void in every
  near-tipoff winner record (`near_tipoff.score`, API correct/history/performance); P12 a two-hour market forecast is void
  on any start change (`near_tipoff.market_outcome`, used by the API and tracking); P13 migration 0020 tightens the
  candidate-forecast trigger (scheduled game, well-formed time); P14 `jobs.predict` / `jobs.market_forecasts(policy=
  "pregame")` refuse before writing; P8 candidate artifacts verified by a sha256 sidecar before unpickling (mismatch →
  that candidate unavailable, no refit/substitute; legacy files older than 0020 adopted once, reported); P9 exact-line join.
- **P1 pool selection (`candidates/selection.py`, sealed `candidates/pool_rules.json` 99c3f7cf…, effective 20:22:45Z):**
  routes standalone or combination (with/without the candidate, held-out, CI upper < 0, average beats the strongest);
  combination-only candidates are sources only as their average (NBA `hetero_linear` → only
  `avg:points_normal+cand:hetero_linear:nba_base`). Weekly `pool_decisions` (unchanged selector arithmetic and
  thresholds, earlier-only settled core cohorts, frozen week) and `pool_selections` after `candidate_pool` in the cycle;
  separate tables (0020) so v1 is untouched. `/api/research/pool`, `pool-rules`, `pool-report`; roster eligibility =
  route + pool decision in force (P2); Performance → "Candidate-pool selector".
- **Also:** P3 rule tracking uses the studied members (NBA winner: 5, not the 6 listed); P4 recent probability = the rule
  statistic; P5 stack rules compared with constituents; P6 night-before vs two-hour disclosure; P7 experiment log lists
  skipped/failed configs; P10 `docs/evidence/repo-review/probe_nhl_goal_dependence.py/.out` reproduces 4,877 games,
  dispersion 1.015/0.961, residual corr −0.052, ties (1-1 is 3.6%, docs said 3.7%: corrected); REPO_REVIEW wording for
  unkept sdv files, optuna verdict, nhle goalie probe cited.
- **Acceptance (tests/test_pool_selection.py, synthetic stores through the real guards):** the selector picks an eligible
  individual candidate; picks an eligible combination whose candidate is not selectable alone (a forged single rule →
  "source unavailable"); < 100 picks → insufficient evidence; no better than reference → abstain; missing candidate →
  no-pick, no substitute; research rows keep accumulating while evidence is insufficient; frozen weekly; idempotent.
- **Verification:** see SMOKE_TEST "Pool selection, integrity fixes". New vs audited code on the same snapshot: every shared
  table identical row-for-row; core definitions NBA 28b64a0bac80ca86 / NHL 5040a9e7ad2ab51c and selector-v1:e00a275e092a
  unchanged. Upgrade of a copy of the current live store: 0020 applied, 9 legacy artifacts adopted, 63 candidate forecasts,
  0 errors. Full suite 437 passed / 0 skipped; `backend/.venv/bin/python scripts/check_project.py --ready` exit 0 (structure, typecheck, build, 438 passed, 0 skipped; 21:3xZ).
- **Unverified / remaining:** real settled prospective evidence (none exists; every pool decision is insufficient evidence,
  correctly); phone-width browser check of the new card; `market_live.score` (CLI and daily summary) still uses the 12 h
  void rule, because changing hashed `market_live.py`/`markets.py` would start a new core definition (user decision);
  library functions `live.predict` / `market_live.forecast(policy="pregame")` remain callable directly (no job/CLI path).
- **Before merging into live:** back up both stores (every CLI start runs `db.migrate`, so 0020 applies on the next job);
  merge between two-hour cycles; first cycle afterwards should report `artifacts_adopted` (9 NHL) and no pool errors.
- **Next:** Codex independent review of 9d3e50e..pool-selector.

## Candidate-pool integration — Claude, 2026-10-02 ~18:25–19:00 UTC
- Integrated session -6b's pool-v1 (docs/CANDIDATE_POOL.md, CANDIDATE_POOL_RESULTS.md): migration 0019 `candidate_forecasts`,
  two-hour recording after the research cohorts (`jobs.candidate_pool`, non-fatal), roster API and Performance card.
- Held-out (exploratory) value only for NHL `poisson_draw:nhl_base` (reg3/puck line/away total) and `multinomial_l2` (reg3);
  nothing for NBA; goalie inputs, MLP, extra trees, NGBoost no value. Candidates never vote; selectable after 100 settled picks.
- Gap: prospective combination rules involving candidates are not in the sealed registry (v1); a v2 registry is needed for that.

## Candidate pool `pool-v1` and repository research — Claude (-6b), 2026-10-02 16:55–~19:20 UTC (branch candidates-v3)
- **Repos inspected (code, commits, licences, probes):** docs/REPO_REVIEW.md + docs/evidence/repo-review/. NGBoost adopted as
  an optional learner (fixes: seeded base tree, chronological early stopping); optuna not needed (nothing searched);
  pbpstats/nba_data/fastRhockey reference only (lineups postgame; stats.nba.com pbp returns `{}`); XGBoost/LightGBM duplicate
  HistGBT and need libomp; NB, bivariate Poisson, Dixon–Coles rejected with our own data (dispersion 1.02/0.96, residual
  goal correlation −0.05, excess draws at every tie).
- **Registry** `backend/app/candidates/` (19 configs: logistic/multinomial, elastic net, RF/ET, MLP, NGBoost,
  heteroscedastic linear, Poisson+xG+draw joint score, goalie GSAx inputs) on one shared point-in-time walk; `ridge:nba_base`
  reproduces `ridge_points` exactly. Design committed before runs (docs/CANDIDATE_POOL.md, 6ec4978 → rebased dbe2797).
- **Results (exploratory; docs/CANDIDATE_POOL_RESULTS.md):** held-out gains only on NHL lines — `poisson_draw:nhl_base` reg3
  −0.0073 [−0.0091, −0.0053], puck line −0.0049 [−0.0065, −0.0032], better in all 5 held-out seasons; puck-line ≥ 0.65
  70.6% vs 62.9% for the strongest member's top games at the same count. Nothing helps NBA/NHL winners; goalie inputs,
  NGBoost, bagged trees and MLPs add nothing. 80–85%: NBA winner yes, by `logistic_v2`'s own confidence (84.9% on its top
  20%, 2025–26), not by combining; NHL winner only ~6% coverage; lines never.
- **Prospective (built, rehearsed on store copies, NOT wired into jobs):** `candidates.live.record` → append-only
  `candidate_forecasts` (migration 0019; two-hour trigger; `research-cand:` prefix; core cohort's exact line; artifacts +
  `model_fits`); `candidates.live.roster` (status, markets, fit, evaluation, prospective n, participation, eligibility).
- **Checks:** `scripts/check_project.py --ready` exit 0 (structure, typecheck, build, 414 passed; 0019 added to two migration-list tests).
  Study outputs copied to `data/reports/pool-*`, `data/research/candidates/`, `data/eval/pool_assess_access_*.jsonl`.
- **Next (integration owner -66):** call `candidates.live.record(conn, game_ids=ids, now=now)` after `research_live.record`
  in the two-hour cycle (never fails it); apply 0019 (backup first); roster API/UI; decide whether to install
  `ngboost==0.5.11` in the live venv (otherwise NGBoost is skipped with a reason). Unverified: real prospective rows.

## Selective-combination study and rule tracking — Claude, 2026-10-02 ~16:55–18:00 UTC (branch retrain-cadence)
- Design first (MODEL_SELECTION §15, 115885b). Audit matrix, 80–85% answer and diagnosis: docs/CHALLENGER_RESULTS.md
  "Selective-combination study". No artifact of the user's earlier ~83% system exists in the project.
- Saved point-in-time member tables `data/research/tables/{nba,nba_evening,nhl}.json.gz` (fingerprinted; reused on rerun).
- Result (exploratory): nothing qualified in any market. NBA winner reaches ~80–85% at 3–14% coverage, and a frozen rule held
  ~79–83% on later seasons, but confidence alone matches it (heavy favourites, not agreement). NHL max ~73%; lines ~60–66%.
- Prospective: sealed registry `backend/app/models/combination_rules.json` (5,365 rules, 19 selected as unqualified research),
  evaluated on stored cohorts (one canonical decision per game/market/rule), `/api/research/rules`, Performance → Combination rules.
- Coordination: user assigned the expanded candidate pool to session -6b (worktree ../nba-predictor-candidates, migration 0019,
  `pool-*` CLI); this session integrates afterwards (research_live, agreement, jobs, main, frontend, shared docs stay here).
- Unverified: real prospective rule records (first NHL cohorts tonight; NBA from 2026-10-20).

## Expanded-combination experiment and fixes — Claude, 2026-10-02 ~16:00–16:35 UTC (merged; live checkout 1d0709c + docs)
- Inventory (from reports): the §11 search included the weekly NBA members; it never tested `hockey_logistic_xg`;
  `availability_two_hour` is prospective-only. Separate experiment `expanded-v1` designed first (MODEL_SELECTION §14, 0f3b8d7).
- Retrospective (exploratory; CHALLENGER_RESULTS): 36 NHL-winner and 27-per-NBA-line-market registered rules; nothing
  qualified. NHL: no combination improves on xG alone. NBA totals: stacker(points_normal + weekly members) beats every
  constituent in log loss (−0.0027 [−0.0054, −0.00002] vs points_normal), a probability-level gain, not a pick rule.
- Live: each two-hour cycle also writes `expanded-v1` cohorts (NHL winner; NBA spread/total/team totals) next to the
  unchanged core; recording only (no selector/selections/recommendations); shown on Game and Performance.
- Fixed: primary market reads/scoring excluded research member rows only after 1d0709c (none existed in live stores before).
- Goalies: corrected assessment (prospective snapshots usable; 15/16 last listings and 14/14 confirmed matched the starter).
- Rehearsal + browser evidence in SMOKE_TEST. Unverified: tonight's first real NHL cohorts (from 20:30 UTC); NBA from 10-20.

## Improvement pipeline A–F — Claude, 2026-10-02 12:57–~16:00 UTC (merged; live checkout at 7a674f1 + handoff commits)
Worktree `../nba-predictor-cadence`, branch `retrain-cadence`, fast-forwarded into `shadow-season`. Each design was committed
in docs/MODEL_SELECTION.md (§9–§13) before its run. Every study is exploratory (spent development seasons). Write-ups:
docs/RETRAIN_CADENCE_RESULTS.md, docs/CHALLENGER_RESULTS.md, docs/TRAINING_AUDIT.md, DATA_SOURCES "Feasibility probes".
- **Correction:** I wrongly said CatBoost would be the first ML dependency; scikit-learn was already declared (TRAINING_AUDIT).
- **A. Weekly shadows (live):** `ridge_points@weekly`, `gbt_points@weekly`, separately versioned NBA line members with a
  per-forecast fit identity. They never vote, average or stack, and are scored as research sources. Real-data check before
  the first NBA game is OK (SMOKE_TEST). The voting bug they briefly introduced is fixed.
- **B. Audit:** docs/TRAINING_AUDIT.md (what learns, when, schedule, gaps).
- **C. CatBoost (rejected):** worse than or equal to existing members on held-out seasons for NBA winner, NBA margin/total
  and NHL winner. Installed only in the worktree venv (optional extra `research`); the live venv is unchanged.
- **D. Sources:** read-only probes in DATA_SOURCES. Built the NHL shot-quality candidate `hockey_logistic_xg`:
  - Imported all ~14.3k eligible NHL games' shots into the live NHL store (migration 0017, append-only, raw cached).
  - **Qualified** on its pre-set rule: Brier −0.0011 [−0.0019, −0.0002] vs `hockey_logistic`, better in all 4 seasons.
  - Now a prospective shadow winner member. A bounded shot import runs in the two-hour cycle and the nightly NHL job.
- **E. Combination search (nothing qualified):** 1,655 rules per NBA market (345–925 per NHL market) × pregame conditions,
  rolling-origin selection. No rule cleared its Bonferroni bound, and best-by-point rules did not hold up. Nothing deployed.
- **F. Fit register (migration 0018 `model_fits`):** params digest, results fingerprint, results-available-through,
  libraries and first use for every learned research member, plus the declared schedule (§13). Writes are non-fatal.
- **Live changes:**
  - Backups taken: `data/backups/{nba-before-0017-0018,nhl-before-0017,nhl-before-0018}-20261002.sqlite`.
  - Migrations 0017/0018 applied to both stores.
  - No launchd change; no primary or sealed config changed.
  - The research definition digest changed, so prospective research evidence starts under the new definition.
- **Checks:**
  - `check_project.py --ready` exit 0 in the live checkout (389 passed).
  - Browser: 3 pages, phone-width frames, no console errors.
  - A tests-wide guard blocks real NHL play-by-play requests (`tests/conftest.py`).
- **Unverified / next:**
  - First real NHL cohorts with the xG member: tonight's window (from 20:30 UTC).
  - NBA weekly members: from 2026-10-20.
  - New research labels in the browser once cohorts exist.
  - Codex review of d69e466..7a674f1 and the shot-import availability assumption.

## Gap closing 1–4 and 7 — Claude, 2026-10-02 ~13:30 UTC (review requested)
- User asked to close five reported gaps, keep abstention, add reliability tables only, choose the best single model on earlier
  data, keep old rows' missing provenance unknown, and show research per selected market. No new candidate, rule, threshold
  or experiment was added.
- **1 Best single at matched coverage:** study picks it per season from earlier out-of-sample seasons only (`best_prior`); the live
  report uses the study's choice (earlier seasons). Supplementary study pass reproduced every earlier figure; conclusion
  unchanged (docs/COMBINATION_RESULTS.md "Comparison with the strongest single model").
- **2 Reliability tables** for every source (live report and study), labelled; no calibration fitted.
- **3 Tests:** 8 new (A → B → A, late quotes, slow download past start, definition groups, unknown settlement version, slate per
  market, NBA two-hour path, frontend/API contract). **361 passed.**
- **4 Storage:** winner cohorts record quote table + id (NBA `odds_quotes`, NHL `market_quotes`); decisions record
  `settlement_version` in evidence; reports expose it. Rows written before this change stay unknown; no migration, no backfill.
- **7 Today:** research line for the selected market in every market view (line, source, status/reason, missing inputs, minutes).
- **Merged** into `shadow-season` as 7bc48c1 after committing Codex's uncommitted advisory unchanged (41fd211, user's choice);
  live-checkout `scripts/check_project.py --ready` exit 0 (structure, typecheck, build, 361 passed).
- **Review checklist:** `git diff 30b689e..7bc48c1`; SMOKE_TEST "Gap closing 1–4 and 7"; MODEL_SELECTION amendment of the same date.
- **Still pending:** first live research cohort → settlement → weekly decision (NHL window tonight; decision Mon 2026-10-05 14:00 UTC).

## Training-repository advisory — Codex, 2026-10-02
- Read-only code inspection confirms logistic/GBT and NBA score candidates fit earlier-season examples at season boundaries; weekly rule selection is a different process from model refitting. No training-capacity ceiling established.
- Reviewed upstream READMEs/docs: github.com/catboost/catboost (candidate learner), stanfordmlgroup/ngboost (probabilistic learner), optuna/optuna (bounded tuning), dblackrun/pbpstats (NBA possessions/lineups), sportsdataverse/fastRhockey (NHL play-by-play/goalie data access). Recommendations only, not installed, benchmarked or source-access verified locally.
- Next proposal: finish current gap fixes, then register a bounded CatBoost challenger and retraining-cadence comparison; audit richer NBA/NHL inputs. No application, model, database or scheduler changes; no tests rerun for this advisory.

## Model agreement, combination and shadow selection — Claude, 2026-10-02
- **Design first:** docs/MODEL_SELECTION.md (5ea11f5, before any run): audit inventory, candidates, stacking protocol, threshold
  grids, weekly selector (Mon 14:00 UTC), lift-over-reference metric with Bonferroni bound, minimum evidence 100 picks / 10% / 3
  weeks, abstention, one market per game, manual promotion criteria, versioning, off switch. Amendments dated in the same file.
- **Built:** `models/score_candidates.py` (NBA ridge_points, gbt_points; NHL goals_draw, goals_glm), `combine.py` (agreement,
  average, out-of-fold log-linear stacker), `selector.py`, `research_live.py` (cohorts at the primary's exact line/quote, weekly
  decisions, selections, reports; cached line-member state), migration 0016 (`research_cohorts`, `selector_decisions`,
  `research_selections`; append-only, two-hour and start-time triggers), jobs integration (`RESEARCH_SELECTION=0` disables),
  CLI `selector-decide`, `research-report`, `combination-study`, API `/api/research/{game,slate,performance,history,retrospective}`,
  UI on Today/Game/History/Performance. Demo mode uses an in-memory synthetic store run through the real code.
- **Results (exploratory, development seasons, docs/COMBINATION_RESULTS.md):** agreement added nothing beyond the average's own
  confidence in any market; probability gains only for NBA spread (ridge/stacker) and NHL regulation 3-way (draw-inflated/stacker).
  Nothing promoted; the live selector starts with empty prospective evidence.
- **Self-review** (separate agent in this session, not independent): 1 high, 4 medium, 6 low findings; fixed in 4a3fce4 (details in
  MODEL_SELECTION amendments); study rerun once (run 2, identical figures except one rule-change count). Bug reinjection: 6 checks,
  one initially uncaught (draw scored as push) → test added.
- **Integrated:** `shadow-season` fast-forwarded to 4a3fce4 at ~11:42 UTC; migration 0016 applied to both real stores after
  backups `data/backups/{nba,nhl}-before-0016-20261002.sqlite`; all 18 pre-existing tables identical except schema_migrations.
  Existing launchd jobs now record research cohorts; no new job installed. First real run on the new code: NBA odds
  11:50 UTC success, forecasts "skipped: no eligible game within two hours", 5 decisions written (insufficient evidence, effective
  to 2026-10-05 14:00 UTC). `scripts/check_project.py --ready` exit 0 (structure, typecheck, build, 353 passed) after removing
  `.claude/scheduled_tasks.lock`, a Claude Code scheduler lock from this session that tripped the structure guard.
- **Remaining:** first real research cohort → settlement → weekly decision lifecycle (first NHL two-hour window tonight, 2026-10-02
  ~20:30 UTC; first decision Monday 2026-10-05 14:00 UTC; NBA from 2026-10-20). Rehearsed on scratch copies only. Runtime: NBA
  cycle ~164 s first, ~76 s cached (remainder is the existing agreement walk). No bookmaker history, so no betting-value claim.

---
## Two-hour policy + forward tracking — Codex, 2026-10-02 (current; supersedes timing notes below)
- User authorized takeover (Claude paused) and requires **all new predictions within two hours before game time**, NBA/NHL, all supported markets. Implemented CLI/periodic-job routing, model namespaces, migration 0015 guards and actual calculation-completion timestamps. Existing frozen settings/files and historical rows retained.
- Existing NBA/NHL odds agents now run refresh → primary winner → supported markets → research agreement inside the window. Daily is maintenance/scoring only. Previous night-before shadow recording is discontinued, not relabelled; CLI `shadow-predict --record` skips. No extra job installed.
- `app/agreement.py`: five existing NBA models plus separate `availability_two_hour`; four NHL models. Point-in-time training, stale-input skips, no learner fallback counted as a vote, exact-50% abstentions, same-cohort votes, majority/unanimous records separated by definition. Agreement only concerns winner markets; score-distribution models still supply other markets.
- `app/availability_live.py`: prepared checksum-sealed historical facts without new scoring; 2026 free player-minute seed digest verified, 591 players. Current-season player minutes parsed from cached final ESPN summaries; joint observed availability, missing-history/report/unsubmitted/unknown-player failures visible. Live injury report must have arrived AND been published within 60 minutes. This separately versioned two-hour variant does not inherit the old 17:45 accuracy claim.
- `app/tracking.py`, `/api/thresholds`, Performance panel: fixed 50–90% threshold tables, W/L, sample/coverage, pending/push/void, descriptive intervals, split by sport/market/model/line source. All versions retained; report latest pregame version per game within each model definition. No automatic tuning/promotion.
- UI defaults Within 2 hours, night-before archive explicit. Game detail shows current policy first and per-model winner agreement; Today/History/Performance use selected policy. Phone/desktop, dark/light checked; temporary phone viewport reset.
- Verification: **321 tests**, typecheck/build pass; final copy-only build passes; exact commands/evidence in SMOKE_TEST. Early readiness failures were stale expectations for newly added daily scoring output, corrected in both test branches; no tests disabled.
- Real stores: every pre-change row unchanged vs SQLite backups; NBA 27,744 winner/0 market; NHL 8,668 winner/480 market (includes 196 earlier research forecasts now archived). Zero new two-hour rows because none eligible during verification. No test-period evaluation rerun.
- Five existing launchd agents loaded, last exit 0; NBA/NHL odds interval 15/30 min. App/API preview running on localhost. NBA fresh injury report absent now, candidate correctly refuses. No paid sources or public deployment.
- Remaining verification: first real two-hour forecast → final lifecycle, injury-aware candidate with a fresh real report, populated threshold panels. No claim of live accuracy or bookmaker edge. See README and dated EVALUATION_PLAN amendment.

---
Older handoff entries below are historical; their nightly prediction instructions are superseded.


## NHL goalie snapshots + NBA availability experiment — Claude, 2026-10-02 02:00 UTC
- **NHL goalies (live):** migration 0014 `goalie_snapshots`; the installed NHL odds job now records ESPN's probable goalie per team per retrieval
  of not-yet-started games, status as published (expected / confirmed), `none_listed` when absent, available at our download time. Backfilled
  716 rows from 57 cached retrievals (rerun adds 0). Shown on NHL game pages; used by no model yet.
- **NBA availability (`avail_v1`), preregistered 145361c:** 1,002 official 5:30 PM injury reports for 2019-24 (0 failures; research cache
  `data/research/availability/`, test seasons never requested), two-layout parser with independent reconciliation (1,002/1,002), reviewed
  alias table, expected minutes from earlier box scores. Corrected result (independent leakage review; first run superseded, amendment in
  EVALUATION_PLAN): avail_v1 66.0% / Brier 0.2177 vs logistic_v2 64.5% / 0.2199 and Elo 64.3% / 0.2209 on 3,667 games at 17:45 ET;
  avail_v1 − logistic_v2 Brier −0.0022 [−0.0040, −0.0004]. Agreement adds nothing beyond avail_v1's own confidence. Exploratory
  (spent seasons). docs/AVAILABILITY_RESULTS.md.
- **Registered as a separate forward candidate** (EVALUATION_PLAN). Not recording: needs current-season player minutes (parse ESPN summaries
  from the box-score job) and an evening job (new persistent job → user approval). Sealed Elo and logistic_v2 shadow unchanged.
- `pypdf` added to the dev extra (installed in backend/.venv with uv). `--ready` exit 0 (301 passed).
- **Next:** build the live evening pipeline for avail_v1 (minutes from summaries, report parse at 17:45, three forecasts) and ask for the job install.

## NHL jobs verified + NHL agreement research — Claude, 2026-10-01 11:30 UTC
- **Running (checked directly):** `com.nba-predictor.nhl-odds` loaded, every 1,800 s; first run 11:20:54Z success (job_runs row 6, 2 requests,
  dates 20261001-02; NHL store now 114 quotes). `com.nba-predictor.nhl-daily` loaded, 22:15 local, odds then daily; not run yet. Installed
  files are identical to `scripts/launchd/` (committed here). NBA daily/odds/injuries unchanged. `nba-markets` NOT installed (my install attempt
  was blocked by the Claude Code permission classifier; the user/Devin installed the NHL ones), so NBA market forecasts are still manual (`app.cli markets`).
- **NHL agreement research** (preregistered in 8b61375 before computing; 1381d14): development seasons only, Elo vs goals model, unanimous,
  averaging, agreement + thresholds vs matched-coverage confidence filters. Nothing met the rule; nothing frozen; live model unchanged.
  Details and tables: docs/NHL_BASELINE_REPORT.md. Paired test check: Elo − home rate accuracy +1.9 points [+0.3, +3.6].
- **NHL learned candidates** (preregistered 330475b; result 2026-10-01): logistic and gradient-boosted trees, season-boundary refits on earlier
  seasons, development 2017-21. Logistic ≈ Elo (Brier −0.0002 [−0.0008, +0.0004]); trees and goals model worse; four-model average and unanimity
  no better. Validation not accessed; nothing frozen; live Elo unchanged. docs/NHL_BASELINE_REPORT.md.
- **NHL agreement exploration** (user request; descriptive; development only): vote splits, lone dissenters, 6 pairs, agreement × confidence,
  per season. Agreement never beat Elo's own top-confidence games at the same coverage. docs/NHL_BASELINE_REPORT.md.
- `--ready` exit 0 (284 passed).
- **Next options:** install `nba-markets` (user action; NBA market forecasts are otherwise manual); prospective ESPN goalie-confirmation
  snapshots, the one new NHL input with plausible signal, as a separately preregistered candidate.

## NHL scheduled jobs installed — Devin, 2026-10-01 ~07:21 EDT
- User explicitly requested installation of NHL daily and odds only. Initially neither plist existed in ~/Library/LaunchAgents and neither service was loaded. Installed unchanged copies of the currently prepared files; both source and installed files passed plutil -lint.
- Both `launchctl bootstrap gui/501 /Users/andry/Library/LaunchAgents/com.nba-predictor.nhl-{daily,odds}.plist` calls exited 0 (run separately). `launchctl print` confirms daily at 22:15 local (Mac currently EDT), runs=0; odds RunAtLoad and 1800-second interval, runs=1, last exit code=0.
- Original NBA daily, odds and injuries services remain loaded: all five expected services verified individually. NBA markets was NOT installed; no manual daily kickstart, model/code edits or commits.
- Log read was denied by ignore rules for data/nhl-odds-launchd.log; fetch counts and database job rows were not verified. Scheduler exit 0 is process evidence only. No application tests rerun for this installation; git diff --check passed.
- Next: inspect the first NHL odds log/job row and tonight's first scheduled daily run. Daily runs odds before daily using `;`, so daily still proceeds if odds fails and the shell exit status is the daily command's status. Existing source-plist edits and earlier handoff notes remain uncommitted.

## Devin advisory after 907ebdf
- Confirmed this checkout is at 907ebdf and was clean; read the current handoff and three prepared plists. No independent acceptance of the seven fixes or the reported 280-test run in this advisory.
- Recommended next bounded task: re-review 907ebdf, browser-check the NHL History link, and review scheduler timing/order/quotas before installation. The calendar jobs specify 22:15 and 22:30 local time; verify their relationship to midnight Eastern and sleep/wake behavior. Separate jobs do not themselves guarantee odds-before-forecast ordering.
- No jobs installed or invoked, no data/model changes. Only this note added; git diff --check passed. Stop before goalie inputs or additional model work pending explicit assignment.

## Multi-sport (NBA + NHL) and markets — Claude, 2026-09-30/10-01 (branch shadow-season; dev worktree multisport-dev)
Plan/status: docs/MULTISPORT_PLAN.md. Markets: docs/MARKETS.md. NHL results: docs/NHL_BASELINE_REPORT.md. Sources: DATA_SOURCES "NHL and market sources".
- **Works:** NHL store (`data/nhl.sqlite`, same schema; `store_identity` binds each store to one sport); NHL stats ingestion 2015-16 … 2026-27;
  NHL Elo (sealed, `models/nhl_frozen_decision.json`) and Poisson goals model; live NHL night-before winner + market forecasts recorded
  2026-10-01T03:30Z (47 games, 235 market rows; 8 games at DraftKings lines); market quotes per retrieval for both sports; NBA spread/total/
  team-total model sealed (`models/nba_market_decision.json`, development research only); API v0.3 with `sport`/`market`; frontend sport
  toggle and market selector on all screens; NHL backtest rows (8,376) recorded from the saved test report.
- **Measured:** NHL validation (once) Elo Brier 0.2334 vs goals 0.2358; NHL final test (once, logged `data/eval/test_access_nhl.jsonl`) Elo
  56.2%, Brier 0.2432 vs home rate 0.2482; puck line and regulation 3-way beat the league reference, totals do not; regulation draws
  under-predicted (16% vs 23%). NBA points model 2023-24 holdout: margin MAE 11.2, total MAE 14.5, intervals calibrated. No market
  comparison is possible historically (no free historical odds).
- **Fixed after independent review** of 4d28c3f: 6 findings, each with a regression test proven to fail on 4d28c3f. Also fixed: A → B → A
  revisions were ignored by the importer (now applied at each retrieval's time; NBA replay rehearsal on a store copy reproduces games, teams,
  stats, possession and odds exactly); NHL meta 19.8 s → 0.2 s.
- **Second independent review** (66c0138..596d452): 7 findings, all fixed in the next commit; 5 backend regressions fail on 596d452 and
  pass now: decision cache now covers decision values; a regulation draw is a loss for a 3-way pick (was a push, inflating the record); a
  line that moves away and back is re-forecast so the current line is the latest; bookmaker comparison excludes pushes; a broken sealed
  decision is a 503. Also: History winner links keep `sport`; dead duplicate CLI branches removed.
- **Process notes:** the installed launchd odds job runs this checkout every 15 min and migrates at start: it applied 0012 (03:06Z) and 0013
  (03:21Z) to `data/nba.sqlite` minutes after the files were written (additive schema only; nothing else written). Further work was done in
  the `../nba-predictor-dev` worktree and fast-forwarded here only at verified commits. Commit b151f4d was made with one failing test (exit
  code hidden by a pipe); fixed in 098dd46.
- **Checks:** `backend/.venv/bin/python scripts/check_project.py --ready` exit 0 (280 passed, typecheck and build passed); browser/API evidence in SMOKE_TEST "NBA + NHL and markets".
- **Scheduler review needed (not installed):** `scripts/launchd/com.nba-predictor.nhl-daily.plist` (22:15 local: `--sport nhl daily`),
  `…nhl-odds.plist` (every 15 min, ≤ 2 ESPN requests when a game is within 36 h; ~190 requests/day in season — consider 30 min),
  `…nba-markets.plist` (22:30: NBA market forecasts). The installed NBA daily job is unchanged and does not record NBA market forecasts.
  Until NHL jobs are installed, NHL forecasts happen only when `--sport nhl odds` + `--sport nhl daily` are run by hand before midnight ET.
- **Blocked / not done:** period/quarter markets and player props (DATA_SOURCES blockers); team-total lines (forward ESPN propBets only, not
  collected); no goalie/injury inputs (no timestamped pregame source); NHL draw-dependence candidate not started; `coverage` counts only NBA
  moneylines in `games_with_odds`; keyboard-only and real-phone checks not done; no market forecast settled yet.
- **Next:** review/install the three plists; run `--sport nhl odds` then `--sport nhl daily` each evening meanwhile; Codex review of
  the review fixes; then an ESPN `probables` parser for prospective goalie snapshots.

## Color theme update
- Updated `frontend/src/styles.css` to the second user-provided palette (Oiler Navy, Brilliant Blue, Ocean Water, Sunset Orange and Mango), with coordinated light and dark token sets.
- Added a persistent light/dark toggle to the workspace header. Layout and app behavior are unchanged. `cd frontend && npm run build` passed.
- Not committed. Other existing HANDOFF/SMOKE_TEST edits predate this visual task.

## Claude phone/demo recheck — 2026-10-01
- Committed Codex's approval note as 04bd067 (docs only).
- Phone-width (390-px frames) and demo-mode recheck of the near-tipoff fixes passed: correct counts and picks per policy, no horizontal
  overflow, no console errors. Details: docs/SMOKE_TEST.md "Phone width and demo mode after the near-tipoff fixes". Temporary servers
  (:8001, :8002, :5174, :5175) stopped; preview servers (:8000, :5173) left running.
- Docs only: no code, model, data or scheduler change. These two doc edits are uncommitted.
- Still unverified: keyboard-only use, a real phone, and a real near-tipoff forecast on a real game (first possible 2026-10-19/20).

## Codex near-tipoff fixes accepted
- Reviewed cfd1c4f: retained forecast metadata is returned on retries; Today selection, picks and record use explicit policy with no fallback. Both previous findings resolved in this scoped review.
- Independently ran near-tipoff/API tests: 28 passed (existing dependency warning); git diff --check passed. Commit and initially clean tree confirmed. Full readiness and browser checks not repeated; phone/demo recheck and real upcoming-game lifecycle remain unverified.
- No models, production data or scheduler changes. This review note is the only new uncommitted change. Near-tipoff remains manually invoked via predict-now.

## Codex near-tipoff review — fixes requested
- Scoped code review and 12 near-tipoff tests passed independently (existing dependency warning); no real ingestion, scheduler changes or forecast writes. Full readiness/browser checks not repeated.
- P2 near_tipoff.predict_now constructs its returned forecast metadata before ledger deduplication. Reproduced retry at 22:45 returning minutes_before_tipoff=45 while the retained forecast was generated at 22:30 for 23:30 tipoff (60 minutes). Odds can likewise be newly fetched in the command response while the app/scorer retain the original snapshot. On retries return the stored forecast identity, generated_at and associated metadata, or explicitly separate current calculation from retained prediction. Add odds-change-with-unchanged-results coverage.
- P2 TodayPage filters using (g.prediction ?? g.near_tipoff), always preferring night-before when both exist, while displaying both picks. Thus night-before .65 / near-tipoff .80 disappears under default .70, while .80 / .65 displays a below-range near-tipoff pick. Add an explicit forecast-policy selection for the slate and apply the confidence filter/record to that policy consistently; preserve separate midnight records.
- Resolve these bounded issues before accepting the near-tipoff flow. Preserve existing uncommitted work; this review did not commit it.
### Claude fixes — 2026-10-01
- Retry reporting: `near_tipoff.predict_now` now builds each returned forecast from the stored ledger row and its freshness record after
  writing (status `created` or `already_recorded`, prediction_id, the retained generated_at, minutes before tipoff from that time, and the
  market snapshot stored then). A dry run is labelled `dry_run` and is the only case that reports a fresh calculation. Regression:
  `test_codex_retry_reports_the_retained_forecast_not_the_recomputed_one` (odds change with unchanged results at 22:40; retry at 22:45 still
  reports 22:30 / 60 minutes / the original quote; the dry run reports 45 minutes and the new quote).
- Slate policy: `/api/games` takes `policy` and `min_confidence`; `schemas.apply_slate_policy` sets each game's `slate_pick` from the chosen
  policy only (pregame → `prediction`, near_tipoff → `near_tipoff`, no fallback) and returns `selection` counts. Today has an explicit
  policy toggle (real mode); cards, the confidence filter, the count and the record lines all use that policy; the other forecast is shown
  only as "not used by this view"; the Backtest record appears only under the night-before policy. The client-side filter rule was removed.
  Regressions: `test_codex_slate_filter_uses_only_the_selected_policy` (65/80, 80/65, near-only, night-only, away pick at exactly 70%, 422s)
  and `test_demo_slate_selection_counts_match_the_picks`.
- `--ready` exit 0: 219 passed, typecheck and build passed; guard and diff check passed. Browser evidence: SMOKE_TEST "Near-tipoff review fixes".
- No model, sealed-rule or scheduler change. Still unverified: a real upcoming-game forecast (first possible 2026-10-19/20).

## Codex confidence-filter review
- Reviewed commit 1666de4: winner confidence uses max(p, 1-p), inclusive threshold, default 70%, settled-only record before pagination, separate run types. No blocking findings in this scoped code review.
- Independently ran API/live tests: 36 passed (existing dependency warning). Confirmed the four reported commits and initially clean tree; git diff --check passed. Did not repeat full readiness or desktop/mobile browser checks; those remain Claude's recorded evidence.
- Backtest range record spans stored development and test seasons; it is not an independent-test-only statistic. No models, production data or schedules changed. This review note is the only new uncommitted change.

## Codex collector fix re-review and research status
- Overwrite-check findings resolved in the reviewed code: HTTP failures are failed checks, replacement failures affect status, absent ETags trigger bounded hash comparison. Independently ran injury tests: 25 passed, existing dependency warning. No full readiness or scheduler verification in this scoped review; no production or schedule changes.
- Read saved research-round-3 report: learned stacking HAS run. stack_logit 64.44% accuracy/Brier .22057; conditional 64.31%/.22122; logistic_v2 64.41%/.21977. Saved results are not an independent rerun or full stacking implementation review. Do not request a duplicate experiment based on older pasted messages.
- Confidence-band findings describe selection, not new predictive information; calibration on reused development data does not establish that live probabilities can always be trusted. Further thresholds chosen using spent test outcomes would invalidate their use as independent confirmation.

## Codex stacking research
- User explicitly requested research into learned combinations. Sources and a bounded proposed experiment are in docs/STACKING_RESEARCH.md. Findings support testing learned/conditional stacking; earlier averaging failure does not settle it. NBA 83% paper uses random splits and is not a comparable benchmark.
- Recommended next research: two fixed regularized stackers using chronological base forecasts, with matched-coverage confidence-filter comparisons. Existing development predictions are exploratory due to prior model-selection reuse. Preserve live/sealed experiments and spent test seasons. No model run, dependency installation, production writes or scheduling performed in this research task.

Status: Shadow recording ENABLED 2026-10-01 (daily launchd job). Injury-report collector agent INSTALLED 2026-10-01. Milestone 4 implemented and reviewed; odds snapshots and local launchd automation configured. Courtside frontend redesign complete. Git baseline d96db86. Free sources only (ESPN; no keyed odds API, user decision 2026-09-30). No deployment, no purchases.
Current owner: Codex re-review of the two near-tipoff fixes (committed, see below). Shadow season and injury collector running unchanged. Final test was run once (2026-09-30T14:40:35Z); Elo is the frozen primary model.

## Advisory review (Devin, 2026-09-30 session date)
- Read the handoff, plan, evaluation rules and agreement analysis; Git working tree was clean. No code, data, scheduler or model changes; tests were not rerun for this advisory review.
- Combination results show no demonstrated Brier improvement over the challenger, not proof that combinations cannot help accuracy. No verified accuracy ceiling or injury-feature uplift was established.
- Recommended next task: a bounded audit of timestamped injury/roster/player-history availability before authorizing a new candidate. Preserve both sealed experiments; compare any later-cutoff candidate against a baseline generated at that same cutoff and report coverage and paired uncertainty.
- Blocker to credible historical injury evaluation: source coverage and pregame availability have not been verified in this review. No new implementation authorized.

## Shadow recording enabled (Claude, 2026-10-01, after Codex Step 6 acceptance)
- `scripts/launchd/com.nba-predictor.daily.plist` and the installed copy in `~/Library/LaunchAgents` now set
  `NBA_SHADOW_RECORDING=1`; `plutil -lint` OK; agent reloaded (`launchctl bootout` + `bootstrap gui/501`); `launchctl print` shows the variable.
- One actual scheduler run (`launchctl kickstart`): exit code 0, steps refresh → boxscores → predict → shadow → score.
  refresh success (202609, 202610); boxscores skipped (0 HTTP attempts); Elo predict 0 eligible; shadow success, 0 eligible, model
  `logistic_v2-shadow-32e5d9e9d514`; score 0. The shadow step took about 80 s under launchd's background priority (about 15 s in the foreground).
  Ledger 27,744 rows, 0 live rows, test_access 1 line: unchanged. First forecasts are expected about 2026-10-13 (7-day window before the 10-20 openers).
- To watch: `data/daily-launchd.log` each night; `app.cli shadow-report` for coverage. A stale-input skip makes the shadow job 'failed'
  and the nightly exit code 1 while Elo still runs.

## Availability audit review (Devin; report/code review only)
- Read the report, HEAD probe and paired-accuracy amendment. The split verdict is reasonable provisionally: prospective raw-file collection pilot is feasible; historical modeling remains gated. No implementation or scheduler change authorised.
- Clarify AVAILABILITY_AUDIT gate 4: "file time" must use the report's publication timestamp with timezone and the legacy +30-minute naming offset, not the literal filename hour. Later revisions/missing metadata need an explicit conservative policy; historical availability remains assumed even after the gates pass.
- Evidence verification BLOCKED: reads of all four files in data/reports/availability-audit-2026-10-01 were denied by ignore rules, including after request_scope granted read access. Counts, checksums, timestamp distributions and reported network results were not independently reproduced. User must make the audit evidence readable or supply it before numerical acceptance.
- Reviewed paired-accuracy code: preserves all games in the denominator and resamples paired weekly blocks; saved numerical intervals were not rerun. No network requests, database access or tests in this review. Only HANDOFF.md edited; git diff --check passed.
- Next: clarify the timestamp rule and unblock evidence review; then seek approval for a bounded raw-report collector plan, separate from parsers, player ratings and models.

## Research round 2: gradient-boosted trees (Claude, 2026-10-01; user-authorised implementation round)
- Definition recorded before building: EVALUATION_PLAN "Research round 2" (fixed settings, 12 existing features, monotonic constraints,
  chronological season-start refits, one run, reading rule). No sweep, no random split, no test seasons.
- Code: `backend/app/models/gbt.py` (`GradientBoostedTrees`, standalone: `logistic.py`, `efficiency.py`, `shadow.py` and `live.py` untouched);
  `backtest.paired_accuracy_bootstrap`, `backtest.evaluate_research_gbt` (lazy scikit-learn import; frozen Elo config; validation rows/boxes
  only; no ledger; no test access); CLI `research-gbt`. Dependency: `scikit-learn>=1.5,<2` in the **dev** extra only (installed 1.9.1 with
  `uv pip install -e '.[dev]'`); a test asserts that importing app.main/jobs/shadow/shadow_report/cli does not import sklearn.
- Tests `tests/test_gbt.py` (9): registered settings; future rows/boxes and own label/box leave predictions unchanged; fits use earlier seasons
  only; warm-up = Elo; determinism; paired accuracy bootstrap; research run reads only "validation" and leaves ledger/decision/access log alone.
- Result (BASELINE_REPORT "Research round 2"; evidence `docs/evidence/research-gbt_v1-2026-10-01/report.json`): gbt_v1 63.86% / Brier 0.22342
  / log loss 0.63819 vs Elo 64.34% / 0.22068 / 0.63158 and logistic_v2 64.41% / 0.21977 / 0.62968 on the same 3,962 games. Brier difference
  +0.00274 [+0.00037, +0.00497] vs Elo and +0.00365 [+0.00167, +0.00555] vs logistic_v2; accuracy −0.48 pts [−1.58, +0.60] and −0.56 pts
  [−1.54, +0.41]. Fails the step 4 screen. Nothing tuned afterwards.
- Invariants around the real run: ledger 27,744; test_access 1 line; frozen_decision and shadow_decision file hashes unchanged.
- Injury/player data investigation requested in the same message: already delivered as docs/AVAILABILITY_AUDIT.md (+ docs/evidence/availability-audit-2026-10-01/);
  not repeated. No accuracy gain is estimated there.
- Uncommitted: this round's code, tests and docs, plus the earlier audit, collector plan and analysis amendment.

## Near-tipoff forecast path (Claude, 2026-10-01; user "yes build it")
- Policy recorded first: EVALUATION_PLAN "Near-tipoff forecast policy". Same frozen Elo; state uses every result available at generation;
  stored as run_type live under `<elo version>-policy=near_tipoff`, feature version `pregame-ids-v1+tipoff-cutoff`, cutoff_at = tipoff.
  The night-before Elo rows, LATEST_LIVE_SQL consumers, shadow report and default app views filter on the plain Elo version: untouched.
- Code: `app/near_tipoff.py` (`predict_now`, refusals after_tipoff / stale_results (> 60 min since last successful refresh) / missing_result
  (an earlier game of either team in the last 7 days not final), market quote at generation time, `latest_rows`, `score`);
  migration 0011 (`near_tipoff_forecasts` append-only freshness record + job name; 42 job rows kept); `jobs.predict_now` (refresh → odds →
  boxscores → forecast stamped after the refresh; 'failed' on stale/missing; dry run stores nothing); CLI `predict-now [--dry-run]`,
  `score-near-tipoff`; API: `near_tipoff` object on game payloads, `policy=pregame|near_tipoff` on /predictions and /performance (422 with
  backtest), `policy` in both responses; UI: Near-tipoff block on game cards and a card on the game page, policy toggle on History and
  Performance (live only), badge. `live.py`, `shadow.py`, sealed files and scheduled jobs unchanged.
- Tests `tests/test_near_tipoff.py` (12): freshness + market record; uses results the night-before forecast lacked; refused at/after tipoff and
  by the ledger trigger; stale (61 min) vs fresh (60 min); missing result then resolved; 24-h window; rerun idempotent, new data = new
  version, earlier forecast kept, append-only incl. INSERT OR REPLACE; dry run; night-before rows/scoring/default views byte-identical;
  full chain to displayed pick and scored result; job wrapper statuses; demo API. `--ready` exit 0: 216 passed, typecheck and build passed; guard and diff check passed.
- Real store: migration 0011 applied; `predict-now` dry and real runs → skipped (no game within 24 h; refresh skipped by quota because the 22:00
  daily job had just refreshed); 0 forecasts; ledger 27,744; test_access 1 line. NOT yet exercised: a real refresh followed by a real forecast
  on a live game. Scratch-copy rehearsal and browser evidence: docs/SMOKE_TEST.md "Near-tipoff forecasts".
- Not built: a button or endpoint to trigger it from the app (CLI only), any schedule for it, injury inputs, a near-tipoff version of logistic_v2.
- Also observed: the 22:00 daily launchd run on 2026-10-01T02:00Z succeeded with shadow enabled (refresh, boxscores, predict, shadow, score).
- API preview server restarted to load this code (detached uvicorn :8000).

## Confidence filters in the app (Claude, 2026-10-01; user instruction via Codex)
- Commits before this change (branch `shadow-season`): 7dc43b8 research rounds 2–3, audit and analyses; 1abd18d injury collector;
  ebde8a0 handoff. Commit 7dc43b8 was checked alone in a temporary worktree (175 tests passed). Tree was clean before the UI work.
- API: `/api/predictions` takes `min_confidence` (0.5–1; inclusive, tolerance 1e-9 so 1 − 0.3 counts as 70%); rows gain `pick` and `confidence`
  = max(p_home, 1 − p_home); response gains `record` {min_confidence, selected, settled, wins, losses, win_rate} computed over all filtered
  rows (not the row limit); unsettled games never count. Helpers in `schemas.py`; both providers. No forecast, model or ledger change.
- Frontend: `components/ConfidenceFilter.tsx` (All / 60 / 65 / 70 / 75 / 80%+, URL `?conf=`, default 70%+; `RecordLine`, `RangeRecords`);
  History filters server-side and shows the record for the selected run type; Today filters the date's games client-side with the same
  rule, shows "N of M selected", each card's pick and model confidence, and separate Live and Backtest record lines. Empty states offer "Show all".
  Model confidence and observed win rate are labelled separately; no historical percentage is shown as a promise.
- Tests: test_live.py (3 new: boundaries for home and away picks at every level incl. 422s; record counts only settled games and ignores the
  limit; live and backtest records separate and forecasts unchanged) and test_api.py (demo filter). `--ready` exit 0: 204 passed, typecheck and build passed; structure guard and `git diff --check` passed.
- Browser evidence: docs/SMOKE_TEST.md "Confidence filter" (desktop real mode; 390 px via same-origin frames). Not browser-verified: demo mode, keyboard-only use.
- The API preview server was restarted to load this code (detached uvicorn on 127.0.0.1:8000, log data/api-preview.log).
- Note: the Backtest record in the app covers every season in the ledger for Elo (2021-22 to 2025-26, 6,605 games), labelled Backtest.

## Injury collector installed + confidence ranges (Claude, 2026-10-01; user instruction)
- Installed `~/Library/LaunchAgents/com.nba-predictor.injuries.plist` (copy of scripts/launchd/…; `plutil -lint` OK; `launchctl bootstrap gui/501`);
  `launchctl print` shows NBA_INJURY_COLLECTION=1 and minutes 2 and 32. One real scheduler run (`kickstart`): exit 0, job_runs row
  `injuries|skipped|0 attempts` (0 slots due: no game window until 2026-10-19T07:00Z). 0 attempt rows, ledger 27,744, test_access 1 line.
  Installed on the user's explicit instruction; Codex's review of the overwrite-check fix was not yet recorded. To remove:
  `launchctl bootout gui/501/com.nba-predictor.injuries` and delete the plist. It writes one 'skipped' job row per run while nothing is due.
- docs/CONFIDENCE_RANGES.md (+ docs/evidence/confidence-ranges-2026-10-01/ranges.json; `scripts/agreement_analysis.py --ranges`): win rate by
  stated-probability band, threshold, pick side and season phase on saved development predictions. Elo: ≥65% → 73.8% on 42.9% of games; ≥70% →
  76.6% on 27.6%; ≥75% → 79.9% on 16.6%; ≥80% → 81.7% on 8.4%. Home/away pick and season phase show no difference beyond noise. Test-season
  figures quoted there are recomputed from the calibration bins already published in BASELINE_REPORT (no new test access). Descriptive only.

## Five-model agreement cut (Claude, 2026-10-01; user "try model agreement"; descriptive only)
- `scripts/agreement_analysis.py --five` (reads the stacking run's saved predictions; new `vote_splits`); docs/AGREEMENT_ANALYSIS.md
  "Five-model agreement"; evidence docs/evidence/research-stacking-2026-10-01/five_model_votes.json.
- 5-0: 3,177 games (80.2%), 67.5% [65.9, 69.1]; 4-1: 496 (12.5%), 52.8%; 3-2: 289 (7.3%), 52.9%. At 50% coverage, confidence alone 72.8%,
  unanimous-then-confidence 72.7%, 1,956 of 1,981 games shared. No model, rule or schedule changed.

## Research round 3: learned stacking (Claude, 2026-10-01; user "go")
- Definition recorded before building: EVALUATION_PLAN "Research round 3" (five base models, OOS rows from 2020-21, season-start fits on
  earlier seasons, stacker A = L2 logistic on clipped logits, stacker B = A + three fixed interactions, λ 10, coverage levels 50/70/85/100%).
- Code: `backend/app/models/stacking.py` (pure functions), `backtest.evaluate_research_stacking` (frozen Elo config; validation rows/boxes
  only; checks base first-fit season and that training results precede the season; no ledger, no test access), CLI `research-stacking`.
  Live, shadow, logistic and efficiency code untouched; scikit-learn still dev-only and lazily imported.
- Tests `tests/test_stacking.py` (8): registered definition; features; season-by-season training sets; later and own-season results do not
  change predictions; availability guard; weights favour the informative base; confidence ranking ignores outcomes; research run reads only
  "validation" and leaves ledger/decision/access log alone. `--ready` exit 0: 200 passed, typecheck/build passed.
- Result (BASELINE_REPORT "Research round 3"; evidence docs/evidence/research-stacking-2026-10-01/report.json): stack_logit 64.44% / 0.22057,
  stack_conditional 64.31% / 0.22122, logistic_v2 64.41% / 0.21977, equal average 64.34% / 0.21988, Elo 64.34% / 0.22068. Neither stacker beats
  logistic_v2 (A +0.00080 [−0.00014, +0.00172]; B +0.00145 [+0.00046, +0.00244]). A passes the lenient step 4 screen vs Elo by a hair
  (upper bound +0.000997); B fails. At 50/70/85% coverage all methods are within about 1 point of each other (72–73%, 68–69%, 66–67%).
  All-five agreement (80.2% coverage) 67.5% vs 67.0–67.7% for confidence filters at the same coverage. Nothing tuned afterwards.
- Invariants: ledger 27,744; test_access 1 line; frozen_decision and shadow_decision hashes unchanged.

## Injury feature feasibility (Claude, 2026-10-01; read-only sample study)
- Report: docs/AVAILABILITY_FEASIBILITY.md; evidence docs/evidence/availability-feasibility-2026-10-01/feasibility.json; script
  scripts/injury_feature_feasibility.py (pypdf from a scratch venv; no project dependency; reads the DB read-only/immutable).
- Sample: 28 dates (4 per season, seed 20261002), 474 team-games, reports at 11:30 PM D−1 (A) and 5:30 PM D (B). 62 requests to the NBA host
  in total (56 GET, 6 HEAD), 1 s apart.
- Parsing: 48/48 reports, 6,176 entries, strict = loose count in every report, team and date on every entry; two producers (iTextSharp to
  2022-23, GemBox from 2023-24, reversed page text). Header time = expected slot in 48/48; Last-Modified 0.03–0.47 min later.
- Mapping: 3,708/3,735 resolved (99.3%), 26 unresolved (24 listed absent), 1 ambiguous. Team check vs box 2,225/2,295; 14 mismatches
  inspected = trades/assignments/All-Star rows, 56 not inspected.
- Timing: A missing in 2019-20 and 2020-21 (8:30 PM is the last evening report then); 16.9% of team-games not submitted at A; B covers 448/474
  team-games before tipoff. Minutes listed absent rise from A to B in 43% of team-games.
- Not established: predictive value (untested, no estimate), separation of new from long-standing absences, exhaustive coverage.
- No model, schema, scheduler or dependency change.

## Injury collector built (Claude, 2026-10-01; user instruction "build the injury collector"; plan steps 1–4)
### Codex review
- Independently ran readiness: 186 tests, typecheck/build passed; dependency deprecation and physical-core detection warnings only. git diff --check passed. Read saved GBT report and fitting code: gbt_v1 accuracy 63.86%, Brier .22342 versus logistic_v2 64.41%, .21977; no real research rerun.
- P2 `injuries.run` overwrite branch: non-200 HEAD responses are recorded as changed_upstream and leave the job successful. Reproduced in temporary DB: two HTTP 500 responses yield status=success, changed=2, failed=0. Also the changed-file GET outcome is discarded, so failed replacement downloads do not affect job status. Distinguish failed/unknown checks from confirmed changes and propagate failed replacement downloads; test HTTP errors and changed-HEAD/failed-GET.
- Missing HEAD validators must not establish unchanged content merely because tuples of missing metadata (or only equal length) match. Require a meaningful validator or bounded GET/hash comparison; preserve request limits and originals.
- Review changed no production data or schedules. Fix these bounded overwrite-check cases before collector installation. Then proceed toward injury feature feasibility (publication times/player matching), not another repeat of the already completed tree experiment. Collector stores raw files only and does not yet improve predictions.
### Claude fix for the overwrite-check findings — 2026-10-01
- `injuries.run` overwrite branch rewritten. A HEAD that is not HTTP 200 (500, 403, 404, …) is recorded as `failed` ("state is unknown"),
  never as a change, and makes the job `failed`. A change is concluded only from a meaningful validator: both ETags present and different.
  The replacement download's outcome is now propagated: if it is not `stored`, the file is listed in `overwrite_check.redownload_failed` and
  the job is `failed`; the original raw file is always kept.
- Missing validator: if either side has no ETag, equal dates or lengths prove nothing. One budgeted GET is made and SHA-256 is compared with
  the stored hash (same → `unchanged`; different → `changed_upstream`, new content already saved as a second raw file). With no budget left,
  or if that GET fails, the check is `failed`. All requests still count against the run, slot and day budgets.
- Tests (test_injuries.py, now 25): `test_codex_head_error_is_a_failed_check_not_a_change` (500/403/404),
  `test_codex_changed_file_whose_download_fails_makes_the_job_fail`,
  `test_missing_etag_is_settled_by_a_hash_comparison_not_by_matching_metadata`,
  `test_missing_etag_without_budget_or_with_a_failed_download_is_a_failed_check`. `--ready` exit 0: 192 passed, typecheck/build passed.
- Collector still not installed; waiting for the installation review.

- Step 1 terms: no clause on automated access found in NBA.com ToU (automated summary of the page; personal non-commercial download
  allowed; statistics not for gambling); host robots.txt read directly: path not disallowed, Crawl-Delay 1, no query strings. Recorded in DATA_SOURCES.md.
- Code: `sources/nba_injury.py` (names, GET/HEAD, PDF validation; no content parsing), `injuries.py` (slots incl. DST, game-window rule,
  plan, single logged download, budgets, daily overwrite check, summary), `jobs.injury_reports` (job 'injuries'; returns 'disabled' and
  writes nothing unless NBA_INJURY_COLLECTION=1 or a dry run), CLI `injuries [--dry-run]`, `coverage` → `injury_reports`,
  `raw_cache.store_fetch(suffix=)`, migration 0010 (`injury_report_attempts` append-only + job name), `config.injury_collection_enabled`.
  Elo, shadow, jobs.daily, the daily/odds agents and their plists are untouched. No new dependency.
- Retrieval time = clock read after the response body is in hand; request start kept separately. Deviation from the plan: none in rules;
  report size is about 100 kB (season estimate about 0.8 GB, plan said 0.6 GB).
- Tests `tests/test_injuries.py` (19): names and DST days; game window; slow response; outage vs run/slot/day budgets; budget exhausted
  mid-retry; crawl delay; 403 then given up; bad bodies; rerun and late catch-up; overwrite check (unchanged, changed with first file kept,
  never starving collection, HEAD failure); switch off; append-only incl. INSERT OR REPLACE; migration; coverage; replay.
  `--ready` exit 0: 186 passed, typecheck/build passed; guard and diff check passed.
- Real store: migration 0010 applied (31 job rows kept); `injuries --dry-run` → 0 due; `injuries` → disabled, no request; 0 attempt rows;
  ledger 27,744; test_access 1 line. Dry runs with a fixed future clock: window opens 2026-10-19T07:00Z (36 h before the first tipoff).
- Supervised trial in a scratch copy (3 GET + 1 HEAD to the NBA host): 2 reports stored and verified, 1 not_available (403), HEAD unchanged.
- NOT done: step 5 (install `scripts/launchd/com.nba-predictor.injuries.plist`; file exists, `plutil -lint` OK, not installed) and step 6.
- Review focus: slot/window rules, budget accounting, overwrite check order (after collection, only with spare budget), 403 handling
  before the season's reports begin (up to 120 attempts a day until given up per slot).

## Injury collector plan (Claude, 2026-10-01; plan only)
- docs/INJURY_COLLECTOR_PLAN.md: raw-collection pilot for 2026-27 injury-report PDFs. Stores unchanged files in the raw cache plus an
  append-only attempt log (request start, response-completion time, outcome, status, Last-Modified, ETag, hash). No parsing, mapping,
  model, history download or dependency. Budgets count every attempt (8 per run, 4 per slot, 120 per day). Separate launchd agent, off by default.
- Review focus: half-hour vs quarter-hour slots; game-window rule; budgets; the daily overwrite check; step 1 (NBA terms for automated
  retrieval) not yet done; pilot success measures.
- Nothing implemented. Uncommitted docs: this plan, AVAILABILITY_AUDIT.md, docs/evidence/, AGREEMENT_ANALYSIS.md amendment.

## Availability data audit (Claude, 2026-10-01; read-only, bounded)
- Follow-up to the audit review (2026-10-01): evidence copied to the non-ignored `docs/evidence/availability-audit-2026-10-01/` (4 files,
  97 kB; no raw PDFs/CSVs) so it can be read; gate 4 rewritten as an explicit publication-time rule (header time in America/New_York, never
  the file-name hour, later-of with Last-Modified, exclusion on missing metadata, still labelled assumed); "go" narrowed to a raw-collection
  pilot. No collector plan written or authorised yet.
- Report: docs/AVAILABILITY_AUDIT.md. Evidence: docs/evidence/availability-audit-2026-10-01/ (probe JSON, parse checks, player box checks).
  Probe script: scripts/probe_availability_sources.py (stdlib, HEAD only). PDF text checks used pypdf in a scratch venv; no dependency added.
- Verdicts: prospective collection = go; historical backtesting = not yet (conditional on four gates: exhaustive inventory, production
  parser with reconciliation, cross-season alias table with an explicit unresolved category, written availability rule).
- Injury reports: sampled 210 checks (10 dates × 3 times × 7 seasons): every date has a report; 200/210 found (misses: 1 PM/8 PM on five
  2020 bubble dates). Two full days exhaustive (24/24, 96/96). Hourly names publish at :30 (176/176 files +30.0–30.4 min); format changed
  2025-12-22. No sampled file modified after its slot. 0 network errors in about 560 requests. Timestamps are consistent but possibly from one process.
- Name mapping on 534 entries from 4 reports: 510 same season, 17 other season (season-long absentees), 7 unresolved, 1 ambiguous.
- Player box scores: all 7 seasons, digest-verified, exhaustive checks vs our final games (table in the report).
- Also from the previous turn, uncommitted: paired accuracy table in docs/AGREEMENT_ANALYSIS.md and `scripts/agreement_analysis.py`.
- No model, schema, scheduler or dependency change. Nothing committed in this step.

## Combination analysis (Claude, 2026-10-01; descriptive, separate from promotion)
- `scripts/agreement_analysis.py --combinations` (extends the existing script; reuses `backtest.paired_bootstrap` and `app.metrics`);
  write-up in docs/AGREEMENT_ANALYSIS.md "Combinations". Saved validation predictions only (3,962 games); the fixed set was written in the
  script before running; no weights fitted; nothing added to the shadow experiment; live Elo and both sealed files untouched.
- Pairwise agreement: 89.7–95.9% of games, 65.1–66.1% correct. Majority (≥3 of 4): 96.0% of games, 65.2%; 3-1 splits 52.9% (414 games,
  interval includes 50%); 160 games 2-2. Equal-weight averages: best is Elo + logistic_v2 at Brier 0.21980 vs logistic_v2 alone 0.21977;
  no average beats logistic_v2 (all intervals include 0); two averages beat Elo by < 0.001 Brier (unadjusted, 14 intervals).
- Git: reviewed work committed on branch `shadow-season` (ef1579f); this analysis in the following commit. Data, .venv and .env are ignored.

## Codex Step 6 acceptance
- Reviewed response-arrival timestamps and positive season-completion checks with their regression tests; both reported findings are resolved. No remaining blocking findings in this scoped re-review. Completion guard assumes the documented current season format.
- Independently ran readiness: 158 tests, typecheck and production build passed; existing dependency warning only. git diff --check passed. No production writes, screening/final-test reruns or scheduler changes.
- Next: Claude enables NBA_SHADOW_RECORDING=1 on the daily job, reloads and verifies an actual run (zero eligible forecasts is valid before the forecast window). Preserve live Elo and sealed definitions. Save the reviewed work in Git after checking staged scope and excluding data/secrets.
- Then perform the agreed descriptive pairwise agreement/majority vote/averaged-probability analysis on saved development predictions. Do not add those combinations to the sealed shadow experiment. Tree-model research follows separately.

## Step 6: seal, refresh, gated shadow (Claude, 2026-10-01)
### Codex re-review — two bounded fixes remain
- Request budgets now count retries and ordinary network/import failures correctly. Independently ran readiness: 151 tests, typecheck/build passed; existing dependency warning only; git diff --check passed.
- P1 `jobs.boxscores`: `at` is captured BEFORE fetch and then passed as `retrieved_at` to ingestion. Synthetic clock reproduction: request starts 03:59:59Z, response arrives 04:00:05Z, stored availability is 03:59:59Z. Keep request-start time for attempt accounting, but stamp successful response retrieval AFTER fetch returns. Add a delayed-response/cutoff-crossing regression. This regression was introduced by the budget fix.
- P1 `shadow_report.report`: finalization requires only one final play-in plus no *known* scheduled/in-progress games. An incomplete schedule can therefore finalize after the first play-in and permanently cache an early verdict. The existing final-once test actually accepts four regular games plus one play-in. Require explicit, verified season/schedule completion (including the complete play-in slate and unresolved postponed games), fail closed if coverage is unknown, and test a partial schedule whose known games are all final. Do not infer season completion from absence of pending rows.
- Interim descriptive scores may remain visible; no automatic selection or promotion from them. No production writes, real screening or final evaluation were performed in this review. Keep recording off until the timestamp fix is reviewed; finish finalization safeguards before accepting Step 6 as complete.

### Claude fixes for the re-review — 2026-10-01
- P1 retrieval time: `jobs.boxscores` reads the clock twice per attempt. `started` (before the request) goes to `box_fetch_attempts`;
  `received` (after `fetch` returns) is the `retrieved_at` passed to ingestion, so observed_at/available_at = arrival time. New optional
  `clock` argument for tests. Regression `test_codex_slow_response_is_stamped_when_it_arrives_not_when_requested` (03:59:59Z request,
  04:00:05Z response: observation stamped 04:00:05Z, attempt 03:59:59Z, invisible to the as-of lookup at the 04:00:00Z cutoff).
  The other ingest paths already take the time after the fetch (`ingest_box_summary`, `ingest_team_box`, `_ingest`).
- P1 finalization: `shadow_report.season_completion` requires positive evidence: all EXPECTED_TEAMS (30) with ≥ 82 final regular-season
  games, nothing pending, no postponed/canceled play-in game, exactly 6 play-in finals, and a postseason game scheduled after the last
  play-in. `--final` raises with the failed checks unless all pass; nothing is logged. The checks appear in every report.
  Regressions: `test_codex_partial_schedule_with_every_known_game_final_cannot_finalize` (4 regular + 1 play-in, nothing pending: refused),
  four parametrized missing-piece cases, a pending-game case, the final-once test on a verified complete (reduced-format) season, and a
  check that the real constants are 30/82/6. Format constants are monkeypatched small in tests only.
- Real-data check of the completion logic (game counts only, read-only): 2024-25 and 2025-26 verify complete (30 teams, 6 play-in finals,
  playoffs present); 2020-21 (72-game season) fails only the full-regular-season check; 2026-27 fails as expected.
- Interim descriptive scores left visible, as agreed. Rules recorded in EVALUATION_PLAN (2026-10-01 amendments).
- `--ready` exit 0: 158 passed, typecheck/build passed; guard and diff check passed. Real store: `shadow-report --final` refused
  (all_teams…, pending, play-in slate, playoffs checks failed); no promotion log; ledger 27,744; test_access 1 line; recording off.

### Codex review — request-budget fix required before enabling
- Independently ran readiness: 143 tests, typecheck/build passed; existing dependency warning only; git diff --check passed. No real screening/final-test rerun or production writes.
- P2 `jobs.boxscores`: MAX_BOX_REQUESTS limits queued games, but `_fetch_each` retries each up to 3 times. Synthetic outage reproduction with 30 queued games made 90 fetch attempts, exceeding the stated 30-request cap. The lifetime per-game counter also counts only persisted raw_fetches, so network failures (and rolled-back parse/import failures) never consume the stated 5-attempt allowance. Enforce both limits on actual attempts, including failures, persist per-game attempt accounting and test repeated failing runs and budget exhaustion mid-retry.
- Keeping stale skips visible as failed is acceptable for now; ensure reporting distinguishes partial shadow coverage from Elo job failure.
- Complete the remaining shadow-report with the sealed promotion rule before declaring this workflow finished; interim reports must not trigger selection. Then re-review and enable the scheduled shadow workflow.
- User-requested follow-up AFTER current work: Claude should measure unanimous winner agreement across Elo, efficiency_margin, logistic and logistic_v2 using the existing saved development screening predictions. Report wins/games, win rate with uncertainty, coverage, disagreement subset and each model on the identical subset; treat exactly 50% as abstention. Do not include constant/home-rate baselines as voting models, change promotion rules, tune on spent test seasons or claim improved accuracy/profit in advance. This is descriptive analysis; any prospective consensus strategy needs a separately fixed definition and forward record.

### Claude fixes — 2026-10-01
- Request budget (P2): `jobs.boxscores` no longer uses `_fetch_each`. Every HTTP attempt (retry, network failure, failed import) decrements
  the per-run budget (MAX_BOX_REQUESTS 30) and the per-game lifetime allowance (MAX_BOX_ATTEMPTS 5), and is written to the new append-only
  `box_fetch_attempts` table (migration 0009) outside the import transaction. `box_refresh_plan` counts that table. Retries stop the moment
  either budget is exhausted; unreached games are `deferred`. Result adds `http_attempts`, `attempt_budget` and per-failure `attempts`.
- Tests (test_shadow.py, 22 in total): `test_codex_outage_never_exceeds_the_per_run_attempt_budget` (35 games, outage → exactly 30 calls),
  `test_failed_attempts_use_the_lifetime_allowance_across_runs` (3 + 2 + 0 calls, then gave_up),
  `test_run_budget_exhausted_mid_retry_stops_immediately`, `test_retry_then_success_and_import_failure_are_both_counted`.
- `shadow-report` (`app/shadow_report.py`, CLI `shadow-report [--final]`): decision set and exclusion counts (missing candidate only /
  Elo only / both), descriptive scores (constant, walk-forward home-rate, Elo, candidate), market on matched games (quote retrieved no later
  than the earlier of the two forecasts), job health with the Elo job and the shadow job reported separately. Interim mode never computes the
  interval against Elo or a verdict. `--final` is refused until every regular-season/play-in game is finished and a play-in game is final;
  it applies the sealed rule (`apply_promotion_rule`), appends one line to `data/eval/shadow_promotion.jsonl`, and later calls return that
  line. It changes no decision file or forecast. Tests: interim, refusal, final-once, rule logic (4).
- Review note: interim mode shows descriptive accuracy/Brier/log loss for both models (no paired interval, no verdict). Say if these should be hidden too.
- Real store: migration 0009 applied; `boxscores` skipped (0 attempts); `shadow-report` interim shows 0 games; `--final` refused (exit 1);
  ledger 27,744, test_access 1 line, no promotion log. `--ready` exit 0: 151 passed, typecheck/build passed; guard and diff check passed.
- Agreement analysis (user request; separate from promotion): `scripts/agreement_analysis.py`, `docs/AGREEMENT_ANALYSIS.md`. Saved validation
  predictions only: all four agree in 3,388 of 3,962 games (85.5%), 2,259 correct (66.7%, Wilson 65.1–68.2%, week-block 64.9–68.5%); the
  574 disagreement games are 50.5–51.9% by model; 0 abstentions. Descriptive only.
- Still not done: enabling (`NBA_SHADOW_RECORDING=1` on the daily job).

- Rules recorded before sealing: EVALUATION_PLAN "Step 6" (seal contents, shadow rows, refresh, stale-input rule, enabling).
- Seal: `backend/app/models/shadow_decision.json` (CLI `seal-shadow`, refuses to overwrite). model_version
  `logistic_v2-shadow-32e5d9e9d514`, sha256 a0ae1e8f…cff4, linked to the frozen Elo digest. `shadow.load_shadow_decision` rejects a broken
  seal, a different frozen Elo decision, or code constants that differ from the sealed definition.
- Refresh: `espn.py` summary adapter (source `espn_summary`); `ingest.ingest_box_summary` / `import_box_summary_fetch` (shared
  `_record_observation`; observed_at = available_at = retrieval time); `rebuild_team_box`/`replay` cover both sources; `jobs.boxscores`
  + `box_refresh_plan` (14-day window, 30 per run, 5 per game, one 24 h re-check; failures/missing/gave-up listed); migration 0008 adds job
  names 'boxscores' and 'shadow' (20 job rows kept).
- Shadow: `app/shadow.py` `predict(conn, now, write=False)`: replays history with `backtest.run(..., deliver_until=now)` (new optional
  argument; default behaviour unchanged), passes every not-yet-available eligible game through in tipoff order (rest days), skips games with
  stale inputs (any of a team's 5 most recent current-season finals lacking a complete delivered box), writes only with write=True.
  `jobs.shadow_forecasts` logs job 'shadow' ('failed' if any stale skip). `jobs.daily` adds boxscores + shadow only if
  `config.shadow_enabled()` (NBA_SHADOW_RECORDING=1); neither launchd plist sets it. CLI: `boxscores`, `seal-shadow`,
  `shadow-predict [--record]`. `live.py`, the real provider and FEATURE_VERSION are untouched.
- Tests `tests/test_shadow.py` (14): seal contents / shortlist / tamper / code drift; dry run writes nothing; shadow rows separate while Elo rows,
  scoring and API output are identical before and after; idempotent rerun and new version on new data; live forecast equals the backtest walk
  incl. a scheduled back-to-back; stale skip until the box is available; job logging; daily gating; summary parser; refresh timestamps,
  re-check, incomplete/unfinished/failed/gave-up; replay. `--ready` exit 0: 143 passed, typecheck and build passed; guard and diff check passed.
- Real data: sealed on the production store (ledger 27,744, test_access 1 line, frozen_decision sha unchanged). `boxscores` skipped (0 recent
  finals, 0 requests); `shadow-predict` dry run: 0 eligible, nothing written; live rows 0. In scratch copies only: 3 real summaries matched the
  backfill (one fills a missing TOV); a dry run with clock 2026-10-19T15:00Z gave 43 forecasts in about 15 s, no stale skips, 0 rows written;
  the live walk reproduces the screening predictions on all 3,962 validation games (max difference 5e-7, the CSV's rounding). No metric was
  computed on 2024-26.
- Review focus: stale rule (5 recent games; job marked failed); `_schedule` pass-through for rest parity; dataset_version composition;
  refresh quotas/re-check; whether 'failed' on stale skip is the right job status.
- Found while verifying: `test_tampered_seal_or_drifted_code_is_rejected` used `monkeypatch.undo()`, which dropped the fixture's paths and made
  the test depend on the real seal file being absent (it failed once the real seal existed). Rewritten to restore the constant explicitly and to
  pass a missing `decision_path`; the real seal was checked intact afterwards (digest matches).
- To enable after review: add `NBA_SHADOW_RECORDING=1` to the daily plist's EnvironmentVariables and reload it. Not done.
- Remaining: `shadow-report` (season-end promotion rule and interim operational report) is not built.

## Codex Step 4 acceptance
- Reviewed candidate fitting, training-only scaling/residuals, season isolation and screening implementation against the registered definitions. No blocking findings in this scoped review.
- Read the saved screening report: only logistic_v2 passes; ensemble precondition is false. This is a shadow shortlist, not established superiority over Elo.
- Independently ran readiness: 129 tests, typecheck and build passed (existing dependency warning); git diff --check passed. Did not rerun real screening or the final test, or modify production data.
- Next: Step 6. Seal the complete candidate definition (including efficiency constants, missing-data policy, refit/scaling rules and promotion rule, beyond the inherited logistic config). Implement and verify free in-season possession refresh with actual retrieval timestamps, explicit stale/missing-input handling, and separate shadow forecasts. Keep live Elo and its displayed results unchanged; stop for review before enabling shadow recording.

## Codex Step 3 acceptance — 2026-09-30
- Timing gate and effective-availability ordering are correct; orphan events are rejected. Last-complete missing-data policy is explicit and tested. No remaining blocking findings in this scoped re-review.
- Independently verified readiness: 118 tests, typecheck and build passed; existing dependency warning only. git diff --check passed.
- Reintroduced the old timing behavior in memory only: the new delayed-result regression failed as expected. No source changes, production data access or final-test evaluation for this check.
- Next: Step 4 with registered features and development/validation seasons only. Preserve frozen live Elo; defer travel/time-zone/altitude features as agreed.

## Codex Step 2 acceptance — 2026-09-30
- Reviewed retrieval-event identity, as-of selection, replay and migration 0007 protections. Both remaining findings are resolved; no further blocking findings in this review.
- Independently ran full readiness: 106 tests, TypeScript and production build passed; existing Starlette/httpx deprecation warning only. `git diff --check` passed.
- Read-only real DB check: migration 0007 applied; 17,826 observations, 27,744 predictions, 98 fetches, 1 final-test access entry. All observation raw IDs and observed timestamps match their referenced fetch records (0 mismatches). This verifies current state, not an independently captured pre-migration checksum.
- Next: Step 3 efficiency features and leakage tests. Use possession_as_of with the forecast cutoff and require eligible completed results available by that cutoff; define missing-data behavior. Historical backfill timestamps remain explicit assumptions. Current-season possession refresh remains an operational item before shadow deployment.
- No production ingestion or evaluation rerun, model changes, UI changes or commits in this review.

## Step 4: candidates on validation (Claude, 2026-10-01)
- Code: `models/efficiency.py` `EfficiencyMargin` (Φ(margin_est/σ); σ = RMS residual of earlier seasons, 12.0 before the first fit);
  `models/logistic.py` `LogisticV2` (registered features; Standardizer/LogisticElo take a feature list, existing `logistic` unchanged);
  `backtest.py` `screening_models`, `screen_candidates`, `evaluate_screening` (frozen Elo config via load_frozen_decision; validation
  rows and boxes only; no ledger, no test access); CLI `screen-candidates`. `candidate_models`, `select_candidate`, the test path,
  `FEATURE_VERSION` and live code are untouched.
- Definitions recorded before the real run: EVALUATION_PLAN "Step 4 candidate definitions". Run once: `data/reports/screening-2026-10-01T000121Z`.
- Result (3,962 validation games; BASELINE_REPORT "Shadow-candidate screening"): Elo 0.22068; efficiency_margin 0.22205 (+0.00138
  [−0.00010, +0.00290], fail); logistic 0.22033 (−0.00035 [−0.00171, +0.00107], fail: upper bound ≥ 0.001); logistic_v2 0.21977
  (−0.00091 [−0.00209, +0.00025], pass). Shortlist: logistic_v2 only. Ensemble precondition not met (1 passed), so step 5 is skipped.
- Tests `tests/test_candidates.py` (11): future rows/boxes and own label/box don't change predictions (both models); σ and the v2 scaler/fit
  use earlier seasons only; warm-up σ; screening rule incl. cap and upper-bound failure; screening run loads only "validation", leaves ledger,
  decision file and access log alone, and reproduces Elo/logistic. `--ready` exit 0: 129 passed, typecheck and build passed; structure guard and `git diff --check` passed.
- Invariants around the real run: ledger 27,744 before/after; test_access 1 line; frozen_decision.json sha unchanged.
- Review focus: σ definition (uncentred RMS); whether "logistic" narrowly failing on the fixed 0.001 bound is applied as intended; v2 reuses
  LogisticElo state handling. Nothing was tuned after seeing results.
- Next (needs approval): step 6: seal logistic_v2 + the promotion rule and add shadow recording to `live.predict` before about 2026-10-20.
  Blocking open item for a live shadow: in-season refresh of possession inputs (DATA_SOURCES.md).

## Step 3: efficiency features (Claude, 2026-09-30)
### Codex review — fixes required
- Independently ran full readiness: 115 tests, typecheck and build passed (existing dependency deprecation warning); git diff --check passed. Synthetic checks only; no production ingestion or final-test evaluation.
- P1 `backtest.py:150–153`: box delivery checks only box availability, not the corresponding eligible result's availability. Reproduction: Nov 1 game, box available Nov 2 12:00Z, result available Nov 4 12:00Z; Nov 3 forecast cutoff 04:00Z already receives that game's efficiency stats (games_home=1, net_home=3.0344). This violates the agreed completed-result gate. Deliver only after BOTH times, with events ordered by effective availability so deferred events cannot block eligible ones; reject/exclude events without a matching eligible result. Add regression coverage for delayed results and interleaved events.
- Missing-data clarification: a newer incomplete box is skipped while the old complete box remains active (confirmed). This is a last-complete policy, not exactly POSSESSION_AS_OF_SQL's latest-observation policy. Explicitly document/test the intended fallback and freshness watermark, or invalidate the game until a complete correction arrives.
- Step 4 scope recommendation: retain already registered rest/back-to-back features. Add travel/time-zone/altitude only in a separately specified candidate after verifying actual venues, preceding locations, neutral games and DST; do not infer travel from home arenas. No candidate tuning on spent test seasons.
- Next: Claude fixes the bounded delivery guard and clarifies missing-data semantics, then Codex re-reviews before Step 4.
### Claude fix — 2026-09-30
- P1: `backtest.run` delivers a box only when max(box available_at, result_available_at) <= cutoff; boxes are sorted by that effective
  time; a box with no matching row in `rows` raises LeakageError (as does one available at or before its own cutoff).
- Missing data: chose and documented the last-complete policy (`EfficiencyRatings.update_box` docstring; EVALUATION_PLAN amendment):
  the latest complete observation among those delivered is used; an incomplete one is counted, never replaces values and never advances the watermark;
  the result is independent of delivery order. Noted that POSSESSION_AS_OF_SQL and coverage use latest-observation instead.
- Tests (efficiency: 12; suite 118): `test_codex_p1_box_waits_for_its_result_and_does_not_block_other_events` (Codex's Nov 1/2/4 case, with an
  interleaved eligible event), `test_box_without_a_matching_eligible_result_is_rejected`,
  `test_incomplete_correction_keeps_the_latest_complete_observation_in_any_order`. The P1 test was not run against the old code; it asserts
  games_home == 0 where Codex's reproduction showed 1.
- Real data (read-only): validation feature fingerprint 74cb31d8f854961b identical before and after (6,276 games); all 17,826 observations have
  available_at equal to the result's. Ledger 27,744; test_access 1 line.
- Step 4 scope recorded (EVALUATION_PLAN amendment; RESEARCH §5): logistic_v2 = registered features only; travel/time-zone/altitude deferred.

- `app/models/efficiency.py`: BoxLine/BoxEvent, `EfficiencyRatings` (update_box keeps the latest observed per game; ratings per
  (season, reference date) with a 30-day half-life, opponent/home adjustment in 20 rounds, a 10-game prior-season shrink (prior measured at the
  previous season's end), home edge shrunk toward 1.25/100 with weight 100). `features(g)` gives net_diff, pace, home edge, margin_est and counts.
- `backtest.py`: `load_box_events(conn, period)` (same season isolation as load_rows; pairs both teams per fetch),
  `box_version()`, and `run(model, rows, box_events=None)` delivers boxes when available_at <= cutoff and raises LeakageError if a game's own
  box is available at or before its cutoff. With no box_events, behaviour is unchanged.
- Definition preregistered: EVALUATION_PLAN "Efficiency feature definition" (constants fixed, not tuned in step 4).
- Tests `tests/test_efficiency.py` (9): own box before cutoff rejected; same-day games independent; prefix invariance (future
  rows/boxes don't change earlier features); a correction is used only after its availability, and a late older copy is ignored; opponent adjustment;
  prior carry at the new season; incomplete lines skipped; DB season isolation plus box_version. Full suite 115 passed.
- Real-data check (read-only; no outcomes examined): validation load 6,276 games / 6,270 events, all finite, about 10 s; Elo validation Brier
  0.22068 unchanged. Found and fixed during step 3: the home edge absorbed team strength in small samples (now shrunk).
- Not in step 3: preprocessing and scaling (they belong to the step 4 model fits, which must fit on training seasons only), the live/shadow wiring
  (step 6), the in-season possession refresh (open item).

## Step 2 round-2 fixes (Claude, 2026-09-30)
- P1 (reversion lost): observations are keyed by retrieval event (`raw_fetches.fetch_id`), with observed_at = that fetch's time.
  `raw_cache.store_fetch` returns (raw_id, fetch_id); `ingest.import_team_box_fetch` replaces import_team_box_raw; rebuild and replay
  iterate fetches in time order. No compaction, so no intervening reversion can be lost.
- P2 (INSERT OR REPLACE): migration 0007 recreates the table with a BEFORE INSERT conflict trigger (as in 0002) and adds
  update/delete/conflicting-insert triggers to `raw_fetches`, which now carries observation timing.
- Tests (team box: 12): `test_rereview_p1_correction_back_to_an_earlier_value_is_observed` (12→13→12; as-of 13 then 12; replay no-op) and
  `test_rereview_p2_insert_or_replace_is_rejected_from_a_default_connection` (plain sqlite3 connection, recursive_triggers=0; also raw_fetches).
- Real DB: 0007 applied; rebuild 7 fetches → 17,826 observations, second rebuild 0; coverage unchanged; ledger 27,744, test_access 1 line,
  team_game_stats checksum unchanged; raw_fetches 98 unchanged. The odds launchd job continues (skips: no game within 36 h).

## Codex Step 2 re-review — 2026-09-30
- Original late-import and A/B/C ordering reproductions now pass. Independently ran full readiness: 104 tests, typecheck and build passed (existing dependency warning).
- P1: `ingest.py` identifies observations by raw_id and uses raw_responses.first_retrieved_at; identical bytes returning after a correction lose the new observation. Reproduced A(day17 TOV12), B(day18 TOV13), A(day19 TOV12): day19 records zero rows and as-of lookup incorrectly returns13. Separate content identity from retrieval-event identity, preserving raw_fetches timing in ingestion and replay. Consecutive unchanged fetches may be compacted only without losing intervening reversions.
- P2: migration 0006 has UPDATE/DELETE protection but no conflicting-insert trigger. With SQLite recursive_triggers OFF, INSERT OR REPLACE overwrites an observation (reproduced TOV13 →99). Add a forward migration with the same BEFORE INSERT conflict protection as migration 0002 and test an external/default SQLite connection.
- Reproductions used temporary databases only. No production ingestion, ledger edits or final-test evaluation. Fix these two bounded cases before Step 3; no model/UI work requested.

## Codex Step 2 review — 2026-09-30
- P1 `ingest.py:122–125`: a live game's stats first retrieved >24 h after tipoff are backdated to result availability. Synthetic reproduction: retrieval Jan 18 12:00 becomes availability Jan 16 02:00. Require an explicit historical-import policy; prospective stats/revisions must retain their actual observation time, including delayed feeds.
- P2 `ingest.py:127–140`: unchanged values retain an old raw_id, also used as the freshness watermark. Import files A(day1: home TOV12/away14), B(day3:12/15), C(day2:13/14): older C incorrectly changes home to13; replay changes it back to12. Track latest observation separately from first provenance and test this full sequence, including replay after stale input.
- Verified in temporary databases only; production data, predictions and final-test log untouched. Focused storage/live/team-box suite: 51 passed (existing dependency warning). `git diff --check` passed. No new full readiness or browser run for this review.
- Next: fix both with regression tests, then review Step 2 again. Confirm how Step 3 reconstructs pre-correction values from raw revisions; the latest-only table is insufficient by itself for historical as-of features.

## Step 2 fixes for Codex P1/P2 (Claude, 2026-09-30)
- Design change: the latest-only `team_possession_stats` is replaced by append-only `team_possession_observations` (migration 0006:
  drop, create, rebuild from raw). One row per (game, team, file); no updates or deletes (triggers); corrections are new rows.
- P1: availability defaults to the download time (`observed_retrieval`). The tipoff + 4 h assumption needs an explicit
  `--historical-backfill` import AND a game whose result basis is `assumed_tipoff_plus_4h`. Live-observed games are never backdated.
  The policy is in the cached request key (`&policy=`); the 7 legacy keys without it are the completed-season backfills (`sdv_team_box.policy_of`).
- P2: there is no overwrite path left. The "latest" is ordered by observed_at (file retrieval), not by import order, so A, B, C in any order give the same rows.
- Step 3 as-of: `ingest.possession_as_of(conn, cutoff)` / `POSSESSION_AS_OF_SQL` = the most recently observed file among those with
  available_at <= cutoff, per game and team, so pre-correction values are recovered for any cutoff between two files. Limit: backfills
  cannot recover values published before our first download.
- Tests: `tests/test_team_box.py` (10) incl. `test_p1_late_stats_for_a_live_game_are_never_backdated` and
  `test_p2_older_file_never_overrides_newer_and_replay_is_order_independent` (as-of checks, replay no-op, reversed-order store equality).
- Real DB: migrate applied 0006; rebuild 7 files → 17,826 observations (all historical_backfill_assumed); a second rebuild added 0;
  coverage per season unchanged (99.49–100%). Ledger 27,744, test_access 1 line, team_game_stats checksum unchanged.

## Established (M1–M3, reviewed)
- backend/app: SQLite migrations, raw cache, ESPN adapter, idempotent ingest, append-only ledger (REPLACE-proof), odds, CLI.
- M3: walk-forward backtester (00:00 ET cutoff, availability guard), Elo primary (k15/H50/carry .75/MOV) in the sealed
  models/frozen_decision.json. Final test on 2,643 games: Brier 0.2105 vs home-rate 0.2475. Logistic is descriptive only.
- Ledger: 27,744 backtest rows under dataset 67ea40d5f04f241e (the all-seasons hash). Do not relabel or duplicate them.
- Limits: ESPN is unofficial (terms unverified); availability assumed at tipoff + 4 h; no turnovers; no historical odds.

## M4 changes (Claude)
- Migration 0003 `job_runs`. `app/live.py`: live Elo forecasts for eligible (regular/play-in/postseason) scheduled games
  whose cutoff is within 7 days. The model state uses results with result_available_at <= now; forecasts go to the ledger as run_type=live,
  cutoff = 00:00 ET on the game date (the trigger requires generation before it). The idempotency key includes the dataset
  fingerprint: an unchanged rerun returns the stored row, and a new result creates a new version. Shown and scored = latest version
  before the cutoff. `score` is read-only.
- `app/jobs.py` + CLI `refresh | predict | score | daily | scheduler`: refresh covers ESPN months (yesterday → +8 days; up to 3
  months), with up to 3 attempts per month and backoff; ≥ 30 min between successful refreshes (quota), each run logged. `daily` still predicts if
  the refresh fails (the status stays visible). The scheduler is a bounded loop (max_runs, interval ≥ 30 min). No system cron installed.
- API contract v2 (`schemas.py`): `mode` real|demo; prediction optional with `forecast_note`; prediction `cutoff_at`;
  market provenance (price_label, retrieved_at, nullable quote_time); team stats = record/last-10/points/rest, point-in-time
  at the cutoff (ORtg/DRtg dropped: no possessions); `run_type` filter on history/performance; performance `baselines`.
- `app/providers/{real,demo}.py`; main.py picks by `NBA_API_MODE` (default real; 503 with guidance if there's no store).
  Real: prediction = latest live, else the primary model's backtest (labelled), else none plus a note (preseason, warm-up,
  not yet, missed). Backtest performance = test seasons only, from the ledger. API DB connections are per request
  (`check_same_thread=False`: FastAPI may run the dependency and the endpoint on different threads).
- Frontend: mode-driven header/banner (real vs demo); Live/Backtest badges with explanations; Courtside charcoal/amber redesign, responsive sidebar/top-nav, daily-slate court hero and refreshed matchup cards (Codex). Live/Backtest toggle on
  Performance and History (never mixed); game cards and detail handle missing forecasts; new stats rows; dates with years.
- Tests: tests/test_live.py (13): full lifecycle (forecast → idempotent reruns → new version → cutoff → result → score),
  only-available results, post-cutoff rejection, refresh retry/fail/quota, month window, bounded scheduler, CLI
  failure exit, real-API labels/notes/market, point-in-time stats, separate run types, meta. Demo API tests updated to v2.

## Odds collection (Claude, 2026-09-30; user: free sources only, no historical odds, collect from now on)
- ESPN day scoreboard (`espn.fetch_day`, `ingest.ingest_day`); `jobs.odds_snapshot` + CLI `odds` (days with a game within
  36 h, max 2 requests, ≥10 min apart, retries shared with refresh via `_fetch_each`); `scheduler --job odds` (≥10 min).
- Migration 0004 rebuilds `job_runs` to allow job 'odds' (rows kept; tested). `coverage.closing_summary` / `LAST_PREGAME_SQL`:
  last `current` quote confirmed via raw_fetches at or before tipoff; gap reported; a scheduled-status quote after tipoff is excluded.
- Not changed: live performance still compares against the last quote before the forecast cutoff (forecast-time market).
  A closing-snapshot comparison is not in the API yet.
- Checks: `--ready` exit 0, 94 passed, typecheck/build passed. Real: migrate applied 0004; `odds` skipped (no game within 36 h);
  one real `ingest_day 20261020` stored 6 DraftKings quotes.

## Local scheduling (Codex, 2026-09-30)
- Installed user LaunchAgents: odds at load + every 15 min; daily refresh/predict/score at 22:00 local. Source plists:
  `scripts/launchd/`; logs: `data/odds-launchd.log`, `data/daily-launchd.log`.
- Runs only while the Mac is awake and the checkout remains at its configured absolute path. To remove, `launchctl bootout`
  the two labels from `gui/501` and delete their plist files in `~/Library/LaunchAgents`.
- Verified: both agents loaded; odds agent has a 900-second interval and daily has a 22:00 calendar trigger. Initial odds run
  exited 0 and skipped correctly because no game was within 36 hours. Daily first fires at 22:00.

## Accuracy plan (approved 2026-09-30): docs/ACCURACY_PLAN.md
- Goal: test whether extra systems (efficiency/margin model, logistic, logistic v2, conditional ensemble) beat frozen Elo.
  This is not a promise of accuracy. Elo is the only live model; logistic exists but is descriptive only.
- The 2024-26 test is spent (1 access): it is never used to tune or select. Screening is on validation; the evidence is the
  2026-27 shadow season under the promotion rule preregistered in docs/EVALUATION_PLAN.md ("Prospective candidates").
- Step 0 done (docs only): EVALUATION_PLAN amendment, ACCURACY_PLAN copy, this handoff. No code, data or ledger touched.
- Review focus: is the screening/promotion rule sound (Bonferroni, 800-game floor, log-loss condition, decision set)?
- Step 1 done (read-only; DATA_SOURCES.md "Accuracy plan step 1"; probe `scripts/probe_espn_summary.py`): summary
  possession stats 210/210 sampled (30 per season, 2019-26), FGA matches the scoreboard 420/420; no stat publication time; stats
  stable on re-fetch but bytes are not; about 39 kB gzip per game. Injuries: current snapshot only, no history.
- **Blocker:** ESPN's linked Disney Terms forbid commercial use and automated data mining/scraping. The user must decide
  whether automated collection continues for personal research before step 2's backfill (about 9,400 requests).
- User chose personal research (option 1). Research done: docs/RESEARCH.md (methods, sources/terms, repos). Proposal: take possession
  stats from sportsdataverse team box files (about 8 downloads, hash-pinned) instead of about 9,400 ESPN summary requests.
- Market-comparison fix (Codex finding): live market uses the latest quote retrieved at or before the forecast's generated_at, not the cutoff
  (`providers/real.py`; new test `test_market_comparison_ignores_quotes_retrieved_after_the_forecast`; EVALUATION_PLAN amended). `--ready` passes (95 tests).
- Superseded: step 2 (possession ingestion); choose between summary (1 request per game) and core stats (2 requests per game).
  Shadow should be sealed before about 2026-10-20.
- Multi-sport: roadmap only (ACCURACY_PLAN); nothing is built.

## Accuracy step 2: possession inputs (Claude, 2026-09-30)
- Superseded the blocked `gh` approach: the release is public (sportsdataverse/sportsdataverse-data, tag espn_nba_team_boxscores);
  no auth needed. CSV via stdlib (no new dependency); each download verified against GitHub's published SHA-256.
- Files: `app/sources/sdv_team_box.py`, migration 0005 `team_possession_stats` (separate from team_game_stats), `ingest.ingest_team_box`
  / `import_team_box_raw` (matched by game id plus home/away check; skips counted; scoreboard FGA/points cross-check reported), `replay`,
  `coverage` (possession coverage per season), CLI `import-team-box`, `tests/test_team_box.py` (7).
- Real run: seasons 2020–2026 imported, coverage 99.49–100% (DATA_SOURCES.md table). Ledger 27,744 rows, test_access 1 line, and the
  team_game_stats checksum were unchanged before and after.
- Also: docs/RESEARCH.md wording corrected per Codex (no parity claim, target marked as an unvalidated estimate, leakage wording, rights).
- Next (step 3, needs approval): efficiency features with leakage tests. Open: in-season refresh of possession inputs (see DATA_SOURCES.md).

## Verification (actual)
- `--ready` passed: 88 tests, typecheck, build.
- Real `daily` run: refresh success (2 requests, 52 new odds quotes), predict 0 eligible (the season starts 10-20; preseason is excluded),
  score 0. API backtest performance reproduces the final test exactly (2,643; 0.21052; home-rate 0.24752).
- Review recheck: both API health URLs returned real mode; live performance returned 0 games; Oct 20 endpoint returned 3 openers with market lines and no forecast.
  Current SQLite ledger has 0 live rows. The timestamp-tie defect was reproduced with an in-memory SQLite case.
- Browser: 8 routes at 1280 px and 390 px (no horizontal scroll), demo mode checked; details in docs/SMOKE_TEST.md. This review opened a preview but did not independently inspect its rendered UI.
- Fixed during verification: a 4 s history query (now 0.1 s); dates missing years; an inaccurate toggle label.

## Open items / review focus
- Fixed and reviewed (same-second live versions): `LATEST_LIVE_SQL` in app/live.py selects one row per game,
  ordered by generated_at DESC, prediction_id DESC. The regression checks game detail, history, performance and scoring.
  Independent `backend/.venv/bin/python scripts/check_project.py --ready`: exit 0, 89 passed, typecheck/build passed (one Starlette/httpx deprecation warning).
- No live forecast exists yet (the first is expected about 2026-10-13, when openers enter the 7-day window). Not faked.
- Live cutoff = midnight ET before game day, so a forecast needs `daily` run the evening before; later results that
  night are not used (same as the backtest).
- Rest days use fractional tipoff gaps; postponement reschedules are not modelled. Odds have no quote time.
- The real provider does per-game queries (fine locally); revisit if hosted. The team/date caches key on db file + game
  count + latest game updated_at, so they self-refresh on ingest from another process; only a team rename without a game
  change shows a stale label until a game updates (labels only, not forecasts).
- Operations: launchd runs odds (15 min) and daily (22:00). From about 10-13, verify the first real forecast lifecycle
  (forecast before cutoff → result → score) in the browser. Live sample_size is 0 so far; nothing is faked.

Frontend design verification: docs/SMOKE_TEST.md (desktop + 390px browser checks; no horizontal overflow). Local servers are running for preview.

References: PLAN.md; README.md (operation); docs/{EVALUATION_PLAN,BASELINE_REPORT,DATA_SOURCES,SMOKE_TEST,MILESTONE2_REVIEW,MILESTONE3_REVIEW}.md.

## Research-claims review (Devin, 2026-09-30)
- Report delivered in chat; only this handoff changed. No implementation, ingestion, test-set rerun or commits.
- Reviewed BASELINE_REPORT, RESEARCH, EVALUATION_PLAN and existing market-fix diff. Recorded Elo test: 2,643 games, 67.2% accuracy, Brier 0.2105, log loss 0.6088; no demonstrated market edge or live track record.
- Research's 67–69%/0.204–0.208 target is unvalidated; cross-study scores do not establish parity with FiveThirtyEight or the market. Claims that >72% almost always leaks or no project beats ours are not established. Fewer downloads do not resolve data rights.
- Verification: document/diff inspection only; generated report.json access denied by workspace ignore rules. Prior 95-test result not independently rerun; no new browser/API or readiness checks (documentation-only review).
- Next: correct unsupported research claims and resolve source licensing/coverage before approving the proposed ingestion and candidate amendments. Existing test seasons remain spent; improvements require prospective evidence.

## Possession ingestion started (Devin, 2026-09-30; blocked)
- Inspected ingest/raw cache, ESPN adapter, schema/migrations, CLI, coverage, configuration, dependencies and storage tests; no implementation/data/model changes yet. Existing uncommitted work preserved.
- `gh api repos/sportsdataverse/hoopR-data/releases/tags/espn_nba_team_boxscores` failed (exit 4): CLI requires `gh auth login` or GH_TOKEN. No release schema, hash, license or coverage independently verified. Asked user to authenticate locally or provide a downloaded season file; no credentials requested in chat.
- `python3 scripts/check_project.py`: exit 0, structure guard passed. No readiness, browser/API, ingestion or evaluation run.
- Next: inspect a real release, then extend existing ingestion/replay and team_game_stats with source-specific provenance/availability, uniqueness and replay tests. Do not let scoreboard refreshes overwrite possession provenance. No UI, paid API, model training or spent-test evaluation.

## Agreement-objective advisory — Codex, 2026-10-02
Read the delivery attachment and inspected agreement.py/research_live.py plus CHALLENGER_RESULTS/DATA_SOURCES. New NHL xG and weekly NBA members are explicitly excluded from core votes/averages/stacks. Preserving the old protocol is correct, but a separately registered expanded-combination experiment is still needed to address the user’s main objective. The failed search does not establish that all combinations fail. The report’s broad goalie claim conflates unavailable historical starting-goalie knowledge with existing prospective ESPN probable/confirmed snapshots. No implementation or independent validation of the reported 389 tests performed; no jobs/data changed. Next: inspect exact search universe and add a bounded, separately versioned combination study using the new inputs, without altering the existing core record.

## Independent focused review — Codex, 2026-10-03
Reviewed audit worktree at 05f085b (suspension timestamp also has an uncommitted change); live remains ab0f190. No implementation, live store, scheduler or model changes; this note only.
- Confirmed nine goalie sources, including dependent registered combinations, are excluded by selection.entries; zero goalie entries remain selectable.
- P2 reproduced: providers/real.py:302 filters every market-history/game-detail query to the current sealed version; a game with only an older forecast returns no row. Market APIs provide no version/archive parameter. Preserve version-specific history while avoiding double counting.
- P2 reproduced: agreement_tracking._stored changes 0.699999 serialization from 0.70000 to 0.69999, but historical tables/summaries keep the same registry/grid validation. _precomputed accepts a pre-fix summary. Version the calculation artifacts and rebuild/validate both tables and summaries before rollout; old chronological checks also require invalidation.
- Verification: audit worktree backend `.venv/bin/python -m pytest tests/test_audit_pool_tracking.py tests/test_audit_fixes.py -q`: 19 passed in 78.49s; physical-core discovery and sandbox pytest-cache warnings. Two standalone mocked reproductions confirmed the above reporting behaviors; no real-data incidence claim. No full readiness or browser checks in this focused review.
- Next: current Claude writer fixes these reporting gaps, finishes its review/rehearsals, and verifies final live cycle. Keep one writer; no new model search needed for these fixes.

## Scheduled-loop verification — Claude, 2026-10-03 (read-only; no code, store or scheduler change)
Checked launchd, `jobs.py`, `research_live.decide_due` and both stores (query_only).
- Wired and running: 5 LaunchAgents loaded, last exit 0 (odds 15 min: 209 runs; nhl-odds 30 min: 73; daily 22:00: 4; nhl-daily 22:15: 2; injuries: 103). `odds` → `forecast_due` → weekly decisions, two-hour forecasts, markets, cohorts, selections, pool, tracking. Settlement is derived at read time from refreshed results (no separate job).
- Seen on real NHL data (launchd log holds runs 108 and 113): 5 games forecast, 580 market forecasts, 60 selections all `no_pick`, decisions all `insufficient_evidence` (0 settled cohorts); run 108 failed, run 113 succeeded 43 min later.
- Not demonstrated: an active rule applied to a game; a decision using settled results; any NBA cycle (preseason only until 10-20; 0 forecasts/cohorts); a live NBA weekly refit; the 2026-10-05 Monday decision.
- Gap: `definition()` hashes source files and `evidence()` filters by definition, so each edit to those files restarts selector evidence. 8 NBA / 7 NHL definitions since 10-02; the 5 NHL games' cohorts sit under two older definitions, current definition has 0 cohorts.
- Gap: tracking-v2 observations exist for 1 of the 5 games (recording began 01:12Z). No failure alert beyond job_runs/logs. Runs only while the Mac is awake; a missed Monday decision is not written retroactively (previous carried one week).
- Next: freeze the hashed files (or decide how evidence carries across definitions) before evidence accrues; re-verify after 10-05 14:00Z and after the first NBA regular-season cycle.

## Old-project source lessons — Codex, 2026-10-03
User explicitly authorized reading unified_sports_platform; inspected temporary clone at 37d9a0d (no execution, model deserialization or changes to that repository). Focused source review only; no current-app implementation changes.
- smart_consensus.py:384 tests agreeing pairs, triple majority and triple unanimity; ranks the supplied outcomes without an independent later-period evaluation inside that method.
- adaptive_consensus.py:307 searches agreement fractions 50–95% per sport/market, separate from probability; maximizes accuracy*sqrt(coverage) on the same supplied outcomes. Useful behavior concept, not validated performance.
- threshold_optimizer.py:218 learns separate agreement/probability ranges on earlier records, but test block at :317 evaluates edge caps rather than the complete learned agreement/probability rule. Do not port its validation claim.
- Key requirement to reconcile after current audit: exact subset × sport/market/line × vote-agreement fraction/range × probability-confidence range. These are distinct axes; keep majority ties and missing members explicit. Evaluate the complete frozen conjunction on later games; keep historical starting policy separate from prospective evidence.
- No tests or accuracy reproduction for old project; no old performance claim endorsed. Next: current writer maps these concepts to existing tracking/selector before implementing anything additional. Preserve one writer.
