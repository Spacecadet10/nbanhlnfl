# Current handoff

Status: Shadow recording ENABLED 2026-10-01 (daily launchd job). Milestone 4 implemented and reviewed; odds snapshots and local launchd automation configured. Courtside frontend redesign complete. Git baseline d96db86. Free sources only (ESPN; no keyed odds API, user decision 2026-09-30). No deployment, no purchases.
Current owner: User (shadow season running: nightly Elo + sealed logistic_v2 shadow). Next agent task: descriptive combination analysis, then tree-model research as a separate plan. Final test was run once (2026-09-30T14:40:35Z); Elo is the frozen primary model.

## Shadow recording enabled (Claude, 2026-10-01, after Codex Step 6 acceptance)
- `scripts/launchd/com.nba-predictor.daily.plist` and the installed copy in `~/Library/LaunchAgents` now set
  `NBA_SHADOW_RECORDING=1`; `plutil -lint` OK; agent reloaded (`launchctl bootout` + `bootstrap gui/501`); `launchctl print` shows the variable.
- One actual scheduler run (`launchctl kickstart`): exit code 0, steps refresh → boxscores → predict → shadow → score.
  refresh success (202609, 202610); boxscores skipped (0 HTTP attempts); Elo predict 0 eligible; shadow success, 0 eligible, model
  `logistic_v2-shadow-32e5d9e9d514`; score 0. The shadow step took about 80 s under launchd's background priority (about 15 s in the foreground).
  Ledger 27,744 rows, 0 live rows, test_access 1 line: unchanged. First forecasts are expected about 2026-10-13 (7-day window before the 10-20 openers).
- To watch: `data/daily-launchd.log` each night; `app.cli shadow-report` for coverage. A stale-input skip makes the shadow job 'failed'
  and the nightly exit code 1 while Elo still runs.

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
