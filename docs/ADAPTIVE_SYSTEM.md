# Adaptive prediction system `adaptive-v1` (NBA and NHL)

Design written before implementation (2026-10-05). Results and limitations are appended at the end once the replay has
run. The adaptive system is a separately versioned research layer: it never changes a primary forecast, a research
cohort, a candidate forecast, a selector decision or any existing identity.

## 1. What it extends

| Need | Existing code reused (not edited: every file below is hashed into a live identity) | New code |
|---|---|---|
| Point-in-time walk of a learned member with within-season refits | `retrain_cadence.Cadenced` (weekly cadence; `member._refit(season+1)` trains on every delivered result), `backtest.run(..., deliver_until=now)` | `adaptive.Adaptive(Cadenced)`: weekly period by `retrain_cadence.period_key`, refit only when new eligible results were delivered since the member's last fit (the `cadence_daily.Daily` rule), `frozen` flag so the forecast path never refits |
| Registry candidates incl. the neural networks (`mlp`, `torch_mlp`, `keras_mlp`) | `candidates.features.walk` (shared feature walk), `candidates.engine.fit/outputs/vector/ready`, `candidates.deep.fit`, `registry.SPECS`, `registry.MIN_TRAIN` | `adaptive.scheduled_walk`: weekly refit on every labelled row whose result was available at the fit cutoff (all seasons, current one included); residual spread from the candidate's own out-of-sample residuals so far |
| Combination weights | `combine.fit_stack` / `combine.stack` (log-linear pool, unconstrained weights), `combination_research.STACK_MIN_TRAIN`, `research_live.STACK_SEASONS` | `adaptive.fit_combiner`: training rows = out-of-sample predictions of the last two completed seasons plus the current season to date; settled prospective `adaptive_forecasts` rows replace the reconstruction of the same game; greedy member admission by common out-of-sample coverage; L2 chosen on the newest quarter of the training rows (time ordered) from a registered grid; leave-one-out fallback stacks for one missing member |
| Lines, quotes, settlement | `market_live` primary two-hour forecast (exact line, line source, quote id), `research_live._winner_quote`, `markets.settle`, `markets.model_line` | — |
| Pick / abstain | `selector.decide` / `selector.apply` with the unchanged thresholds, MIN_PICKS, coverage and week gates | decisions computed at combiner fit time from settled prospective adaptive forecasts only; the source is `adaptive` |
| Artifacts | `candidates.live._write` (data then digest, atomic renames), `_load_verified`, `_locked`, `_results_fingerprint`; `fit_register.record` (`model_fits`) | `adaptive_fits` run log, `adaptive_forecasts` ledger (migration 0024) |
| Jobs | `jobs.daily` (nightly, off the forecast path) and `jobs.predict_now` (two-hour cycle, after the pool steps) | `jobs.adaptive_train`, `jobs.adaptive_record` (never raise into the cycle) |
| Replay / comparison | `candidates.study.existing_tables` (existing members at identical lines), `combination_research._stack_walk` (existing season-start stacker), `_summary` (calibration), `backtest.block_bootstrap` | `adaptive_replay.run` |

## 2. Members evaluated as contributors (per sport)

- Online (state replayed, no stored fit): NBA `elo`, `points_normal`, `efficiency_margin`; NHL `hockey_elo`, `goals_poisson`.
- Learned core members, each wrapped as `<name>@adaptive`: NBA `logistic`, `logistic_v2`, `gbt_v1`, `ridge_points`, `gbt_points`;
  NHL `hockey_logistic`, `hockey_gbt`, `hockey_logistic_xg`, `goals_draw`, `goals_glm`.
- Registry candidates as `cand:<spec>@adaptive`: every `registry.SPECS` entry of the sport, including the scikit-learn
  `mlp`, `torch_mlp` and `keras_mlp` networks.
- Recorded exclusions: `availability_two_hour` (forward-only injury inputs, no out-of-sample history), the season-start
  version and the existing `@weekly` shadow of a member that has an adaptive version (same model), `ngboost` where the
  dependency is missing, deep networks where neither library nor artifact is available, `nhl_goalie` feature sets
  (historical rows use the actual starter, known only after the start).
- Markets: a member contributes to every market its output supports at the exact line of the primary forecast.
  A member that cannot predict a game/market is `missing` with a reason; the combination then uses the registered
  leave-one-out fallback or abstains. Probabilities are never substituted.

## 3. Schedules (registered constants, `adaptive.SCHEDULE`)

- Period: ISO week of the game's Eastern date (`retrain_cadence.period_key("weekly", ...)`).
- Member refit: at the first forecast of a new period, only if eligible results were delivered since that member's last
  fit, and only with at least the member's minimum training size. Deep networks additionally wait until the current
  season has delivered `MIN_DEEP_VALIDATION = 100` results (their epoch count is chosen on the latest season in the
  training set). The season-start refit of each member is unchanged.
- Combiner refit: same period rule, trained on rows whose outcome was available at the cutoff.
- Live trigger: `jobs.daily` runs `adaptive.train` when the next eligible scheduled game's period differs from the
  period of the combiner fit in force (or none exists) and new eligible results exist; otherwise it is skipped with the
  reason. The two-hour cycle (`adaptive.record`) only loads artifacts, delivers results that arrived after each
  artifact's cutoff to the frozen state, and predicts. No fit happens in the forecast path.

## 4. Identities and artifacts

- `adaptive.definition(conn)`: `adaptive-v1` + research definition + agreement digest + every candidate definition +
  this module's code. Model versions: `adaptive-v1:<member>:<definition>`; combiner: `adaptive-v1:combiner:<definition>`.
- `data/research/adaptive/<sport>/members/<name>/<definition>-<period>-<fingerprint>.pkl` (+ `.sha256`),
  `.../history-<definition>.pkl` (out-of-sample history store, checkpointed per member),
  `.../combiner/<definition>-<period>-<fingerprint>.pkl`. A file is loaded only after its digest matches.
- `model_fits` gets one row per adaptive member fit (`adaptive:<name>`, schedule `weekly`), so the existing training
  monitor lists them. `adaptive_fits` logs every attempt (success / failed / skipped) with the error; the forecast path
  loads the latest successful artifact, so a failed refit leaves the previous valid one in force and visible.

## 5. How adaptive forecasts reach the application

- `adaptive_forecasts` row per game, market and line: adaptive probability vector (or `unavailable` with the reason),
  contributing members with their probabilities and fit ids, missing members with reasons, weights, combiner fit id,
  training cutoff, weight-update time, bookmaker quote at the same line or `odds_status` "unavailable", selection
  (pick / no pick with the selector's reason).
- API: `GET /api/research/adaptive` (status, fits in force, weights, exclusions, schedule, prospective record, replay
  summary) and `GET /api/research/adaptive/game/{game_id}`. UI: an "Adaptive combination (adaptive-v1)" card on the
  Game page and an adaptive section in the Performance page's training card, both labelled research and distinct from
  the primary forecast.

## 6. Validation plan

`adaptive-replay --sport nba|nhl`: NBA warm-up + validation seasons (2020–2024, evaluated 2022–2024); NHL warm-up +
development seasons (2016–2021, evaluated 2018–2021). Reserved test seasons are never loaded (`backtest.LOADABLE`,
`nhl_eval.LOADABLE`). The full schedule is replayed chronologically; the existing system (primary, core average, core
season-start stacker) is scored on identical games and lines. Reported per market: n, coverage, log loss, Brier,
calibration, accuracy, paired differences with ISO-week block bootstrap. Leakage assertions: no training row's
outcome is available after the fit cutoff; no fit's results reach the cutoff of a game it predicts.

## 7. Operation (implemented 2026-10-05)

- Tables: migration `0024_adaptive.sql` (`adaptive_fits`, `adaptive_forecasts`; append-only, two-hour and current-start
  guards). The pinned release refuses a store migrated past the schema it ships, so the live stores are migrated only by
  the release move that carries this code, never before.
- Commands (`backend/`): `.venv/bin/python -m app.cli [--sport nhl] adaptive-train [--dry-run|--force]`,
  `adaptive-record [--dry-run]`, `adaptive-status`, `adaptive-replay [--out DIR] [--skip-deep]`. `daily` only reports the
  due state; `predict-now` runs `adaptive-record` after the pool steps. Off switch: `RESEARCH_SELECTION=0`.
- Locking (`adaptive.run_scheduled`): the per-sport training lock (`data/research/adaptive/<sport>/train.lock`) is
  taken first and held through the whole run — snapshot, training, publication, cleanup — so two trainers can never share
  or delete each other's snapshot or republish each other's records; the store's job lock is held only briefly inside it,
  twice — for the due check plus a consistent SQLite online-backup snapshot, and for verifying and publishing the fit
  records — so the two-hour odds/forecast cycles are never blocked by a refit. Training writes no ledger row; `publish` re-verifies every artifact
  digest and refuses if the live definition changed meanwhile. Scheduled by
  `scripts/launchd/com.nba-predictor.adaptive-train.plist` (research-environment Python; not installed).
- Cost: a training run re-walks every learned member from the first stored season with its weekly refits (the walk is
  what makes the history out of sample), so the first run of a definition takes tens of minutes per sport (NHL
  `hockey_gbt` dominates); later runs reuse the candidate history store and only add new periods. Training is weekly and
  nightly, never in the two-hour cycle. Set `OMP_NUM_THREADS` when running several sports at once.
- Deep networks (`torch_mlp`, `keras_mlp`) are fitted only in the research environment (`~/nba-predictor-deep`); in the
  release environment their fit is recorded as `skipped` with the reason and the member is `missing` in every forecast.
- Forecast path: artifacts are loaded only when their digest matches; results that arrived after an artifact's cutoff
  are delivered to the frozen state (no refit; a season the member has no fit for makes it `missing`); one missing
  member uses the registered leave-one-out stack, more abstain with the reason; probabilities are never substituted.

## 8. Replay results (run 2026-10-06 UTC on store copies, research environment with torch/tensorflow)

Chronological replay (`adaptive-replay`), full schedule, identical games and lines; log loss (lower is better), paired
difference adaptive − source with a seeded ISO-week block bootstrap 95% interval; negative favours the adaptive
combination. Evidence: `docs/evidence/adaptive/{nhl,nba}-replay.json`. Exploratory development data only; the reserved
test seasons were not read. Pushes are excluded from log loss (as in the combiner's objective).

**NHL** — development seasons 2018–2021 (loaded 2016–2021), 15 members (5 online/learned core incl. the xG member, 10
candidates incl. `mlp`, `torch_mlp`, `keras_mlp`; goalie feature sets excluded), 4,877 winner games, 4,846 line games,
133 weekly combiner refits per market, 34 min.

| Market | Primary | Core average | Core season stacker | Adaptive | Adaptive − primary | Adaptive − stacker |
|---|---|---|---|---|---|---|
| winner | 0.6743 | 0.6749 | 0.6744 | 0.6731 | −0.0011 [−0.0031, +0.0008] | −0.0013 [−0.0033, +0.0007] |
| reg3 | 1.0633 | 1.0552 | 1.0509 | 1.0487 | **−0.0147 [−0.0191, −0.0104]** | **−0.0022 [−0.0043, −0.0000]** |
| spread | 0.6830 | 0.6813 | 0.6803 | 0.6801 | **−0.0029 [−0.0045, −0.0011]** | −0.0002 [−0.0017, +0.0013] |
| total | 0.6908 | 0.6920 | 0.6913 | 0.6911 | +0.0003 [−0.0008, +0.0013] | −0.0002 [−0.0011, +0.0006] |
| team_total_home | 0.6805 | 0.6806 | 0.6818 | 0.6805 | +0.0001 [−0.0013, +0.0015] | −0.0012 [−0.0026, +0.0001] |
| team_total_away | 0.6852 | 0.6844 | 0.6844 | 0.6840 | −0.0013 [−0.0028, +0.0002] | −0.0004 [−0.0015, +0.0006] |

Reading: a clear gain on the regulation 3-way market (every season negative, interval below zero against every existing
source) and a small one on the puck line against the primary (not against the existing stacker). On the winner market the
best single member (`hockey_logistic_xg@adaptive`, 0.6719) beats the adaptive combination (0.6731): the combination does
not beat its best member there. Totals: indistinguishable. "Adaptive weights on the core members" (the same weekly
combiner over the existing season-start members only) sits between: most of the reg3 gain comes from the adaptive
members (`poisson_draw`, `multinomial_l2` candidates with within-season refits), not from the weights alone.

**NBA** — validation seasons 2022–2024 (loaded 2020–2024), 20 members (6 online/learned core, 14 candidates incl. the
three deep networks; `ngboost` missing), 3,962 winner games; line markets 2,639 common games (2023–2024 only: the
existing season-start line stacker needs two earlier out-of-sample seasons and so has no 2022 forecasts — adaptive
coverage is 100% but the comparison is restricted to games every source forecast), 105 weekly combiner refits, 63 min.

| Market | Primary | Core average | Core season stacker | Adaptive | Adaptive − primary | Adaptive − stacker |
|---|---|---|---|---|---|---|
| winner | 0.6316 | 0.6298 | 0.6318 | 0.6303 | −0.0013 [−0.0042, +0.0017] | −0.0015 [−0.0049, +0.0018] |
| spread | 0.6928 | 0.6863 | 0.6858 | 0.6852 | **−0.0076 [−0.0119, −0.0030]** | −0.0006 [−0.0025, +0.0013] |
| total | 0.6930 | 0.6939 | 0.6907 | 0.6915 | −0.0014 [−0.0048, +0.0019] | +0.0008 [−0.0016, +0.0030] |
| team_total_home | 0.6925 | 0.6937 | 0.6918 | 0.6918 | −0.0007 [−0.0040, +0.0029] | −0.0000 [−0.0017, +0.0018] |
| team_total_away | 0.6935 | 0.6924 | 0.6917 | 0.6907 | −0.0028 [−0.0060, +0.0001] | −0.0011 [−0.0032, +0.0010] |

Reading: on the spread the adaptive combination beats the primary points model clearly but is level with the existing
season-start stacker and the core average; elsewhere nothing is distinguishable from the existing combinations with
three seasons. On the winner market the L2 choice settled at 1000 (near-equal weights over 18 members): the data did not
support learning distinct weights, and the equal core average (0.6298) is as good. The best single members are
`logistic_v2@adaptive` (winner, 0.6290) and `hetero_linear@adaptive` (spread, 0.6862).

**What the replay does not show:** any live or prospective result (no adaptive forecast has settled yet), any
bookmaker comparison (development seasons carry no quotes; the replay never fabricates them), or a reason to replace the
primary system. Weekly refits plus the learned combination help where the members themselves improve within the season
(NHL regulation 3-way, puck line); on winners and totals they are a wash.

## 9. Live state after this task

- Artifacts and fits exist only on the session's store copies (NHL: 15 member fits + combiner for period
  `2027:2026-W41`, trained on 2,831 window rows, 0 prospective; NBA: 20 member fits + combiner for period `2027:2026-W43`, 2,643 window rows; definition `1e34742836491cfa`). The live
  stores are not migrated and hold no adaptive rows. Nothing is deployed; the pinned release is unchanged.
- First real adaptive forecasts will be recorded by the first two-hour cycle after a release move that (1) migrates the
  stores, (2) runs `adaptive-train` once (deep fits need the research environment's Python, otherwise the deep members
  are skipped with the reason), and (3) keeps the nightly `daily` job. The selector will report "insufficient evidence"
  (no picks) until 100+ settled adaptive picks with quotes exist, exactly as for the research selector.

## 10. Review fixes (Codex review 2026-10-06; fixed the same day)

1. **Immutable, content-addressed artifacts.** `_save` names every artifact by the digest of its bytes
   (`…-<fingerprint>-<sha12>.pkl`); an identical fit maps to the same file, a different one to a new file, so a retry or a
   forced refit of the same period never overwrites an earlier artifact. Every loader (`load_fit`) verifies the bytes
   against the digest recorded on the fit row it is loading (and the sidecar) before unpickling; a mismatch is
   `ArtifactMismatch` and the member is `missing`, never substituted. The candidate history checkpoint is the one mutable
   file, and no fit row refers to it.
2. **History checkpoint vs live-prepared fit.** `live_fit` prepares the next period's fit on a copy of the history store;
   the checkpoint keeps each period's own fit, so a walk resumed after the live preparation reproduces the uninterrupted
   one (Codex's reproduction is now `test_resume_after_the_live_preparation_reproduces_the_uninterrupted_walk`).
3. **Partial runs are retried.** A combiner row is `success` only when every market with two or more available members
   got a combination and no member refit failed; otherwise it is `partial`. The forecast path applies the usable markets
   of the latest usable fit and keeps the most recent earlier combination for the others (`combiners_in_force`,
   per-market `combiner_fit_id` on every forecast row); `due` is true again while the period's latest run is partial.
4. **Missing deep libraries no longer drop the networks.** A member whose fitting library is missing keeps its stored
   fit (`kept_for_dependency`), is applied from the NumPy weights, and stays in the combination; only a member with no
   fit at all is skipped. `due` becomes true in an environment that can refit the kept members, so the research
   environment's scheduled run (`scripts/launchd/com.nba-predictor.adaptive-train.plist`, not installed) refits what the
   release environment could only apply. Both runs are idempotent.

The migration (never applied to a live store) now allows status `partial`. The replay numbers in §8 are unaffected
(these paths are not used by the replay); the real-copy NHL training was rerun with the final code (SMOKE_TEST).
5. **Training outside the store's job lock** (second review, same day). `adaptive-train` no longer runs inside
   `cli.main`'s job lock nor inside `daily`: `run_scheduled` snapshots under the lock, trains from the snapshot under a
   separate training lock, and publishes under the lock again (artifacts verified against each record's digest; a
   record whose artifact does not verify is published as `failed`). `jobs.daily` reports the due state only. Tests:
   the store lock is free and a two-hour forecast cycle runs while a training run is blocked mid-fit; an ordinary
   next-period run (new week + a settled prospective forecast) trains and publishes through the scheduled command on
   both the synthetic NHL and the synthetic NBA store.
6. **Snapshot ownership and retryable publication** (third review). The training lock is now acquired before the
   snapshot and held through publication and cleanup (snapshot names also carry the pid); a concurrent trainer is refused
   before it touches anything. `publish` verifies the whole batch first; when a usable member's artifact fails, the
   combiner of the same run is published as `partial` with the reason, never `success`, so `due` retries the week.
   Tests: a second `run_scheduled` during a blocked first one is refused and the first snapshot survives; a tampered
   member record → member `failed`, combiner `partial`, `due` true; a clean republication → `success`, not due.
7. **Member record write failures** (fourth review). `publish` writes member records first and the combiner last; a
   usable member whose record could not be written (not only one whose artifact failed to verify) downgrades the
   combiner to `partial` with the reason, so the week stays due and the ordinary scheduled retry completes it. Test:
   forced write failure → member row absent, combiner `partial`, `due` true → unforced `run_scheduled` → success, not due.

## 11. Retrospective recovery of missed adaptive forecasts (implemented 2026-10-09, not deployed)
- Ledger `adaptive_recovered_forecasts` (migration 0026), append-only, never read by live forecasts, the selector,
  `prospective_rows` or combiner training. Rows are `reconstructed` (probabilities) or `unreconstructable` (reason).
- `adaptive.record(..., replay=<game>, now=<tipoff - 1 s>, write=False)` is the read-only replay: the same evaluator, with
  fits chosen by training cutoff (not publication time) and accepted only if both the fit's `training_cutoff` and the
  artifact's information time (`cutoff`) are at or before the simulated cutoff. Lines come from a primary two-hour
  forecast made before the cutoff, else from the primary reconstruction; quotes only if retrieved before the cutoff.
- `recovery.recover_adaptive` runs in `jobs.forecast_due` after live work and primary recovery, one replayed game per
  cycle (about 40 s on the real NHL store). It defers when no current-definition combiner exists yet (training pending)
  or a primary line is missing; it records `unreconstructable` only when every current-definition combiner was trained
  after the cutoff, or the replay itself abstains. A replay error records nothing and is retried.
- Era: games from the first combiner in force (any definition) or the first adaptive attempt. Reported under
  `adaptive` in `/api/recovered-forecasts`; not shown in the UI.
- Any edit to `adaptive.py` changes the adaptive definition (the file's bytes are hashed). Deploying this code therefore
  starts a new definition: both sports retrain, and games before the new fits' snapshot become unreconstructable.
