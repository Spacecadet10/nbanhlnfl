# Evaluation plan: home-rate and Elo baselines

Recorded 2026-09-30, **before** any model was run on real data. Changes to this plan must be dated and explained here.

## Data
- Source: ESPN scoreboard via the milestone 2 store (docs/DATA_SOURCES.md). A dataset version hash is written into every report.
- Games: `status = 'final'` and `season_type IN ('regular', 'play_in', 'postseason')`. Excluded: preseason, All-Star
  (`other`), and postponed/canceled placeholder rows.
- Neutral site: the ESPN flag, plus the 2020 Orlando bubble (tipoffs 2020-07-30 through 2020-10-12) treated as neutral.
  The bubble was announced before those games, so this is legitimate pregame knowledge.

## Point-in-time rule
- Prediction cutoff: 00:00 America/New_York on the game's Eastern calendar date (a conservative prior-day cutoff).
- A result can update a model only if `result_available_at <= cutoff`. Historical availability is the assumption
  tipoff + 4 h (`assumed_tipoff_plus_4h`), so the cutoff also excludes results from the previous night that were
  available after midnight Eastern. The assumption cannot give exact point-in-time integrity; this cutoff errs toward
  using less information.
- Predictors receive only pregame fields (ids, teams, neutral flag, season, season type, tipoff), never scores.
- Model state carries forward through all seasons. Scoring counts only games inside the evaluated period.

## Periods (by season end year)
| Period | Seasons | Use |
|---|---|---|
| Warm-up | 2019-20, 2020-21 | Model burn-in only; never scored |
| Validation | 2021-22, 2022-23, 2023-24 | Scoring and Elo hyperparameter selection |
| Test (reserved) | 2024-25, 2025-26 | Evaluated once with the frozen configuration. Every access is appended to `data/eval/test_access.jsonl` |

## Models
- Constant 0.5 (reference only).
- Home-rate: Laplace-smoothed home-win rate over all eligible non-neutral results available at the cutoff; 0.5 on neutral sites.
- Elo: initial rating 1500. `p = 1 / (1 + 10^(-(R_home + H - R_away) / 400))`, with H = 0 on neutral sites.
  Parameters are K, H, the between-season carry-over fraction toward 1500, and an optional FiveThirtyEight-style
  margin-of-victory multiplier.
- Elo tuning grid (validation only; fixed here): K ∈ {10, 15, 20, 25, 30}, H ∈ {50, 75, 100}, carry ∈ {0.5, 0.75, 1.0},
  MOV ∈ {off, on}: 90 configurations. Selection criterion: lowest validation Brier score. Ties go to the simplest
  configuration (MOV off, then carry 1.0, then smallest K).

## Metrics
Accuracy, Brier score, log loss, 5-bin calibration, sample counts, and per-season breakdown. The paired comparison is
Elo minus home-rate, using the per-game Brier difference with a seeded (20260930) block bootstrap by ISO week of cutoff
(2,000 resamples, 95% percentile interval). Market comparison: none possible, because there are no historical odds
(coverage 0). Report that; do not approximate it.

## Amendments
- **2026-09-30, before any test-period access:** the first validation run chose H = 50, the lowest grid value, and validation
  Brier improved monotonically as H fell (best per H: 100 → 0.2253, 75 → 0.2220, 50 → 0.2207). The H grid is extended
  to {25, 35, 50, 75, 100} (150 configurations). Only validation seasons are used; the test seasons remain unaccessed.
  First-run report: `data/reports/validation-2026-09-30T142023Z` (dataset 67ea40d5f04f241e).
- **2026-09-30, frozen configuration:** with the extended grid, H = 50 is an interior optimum (best per H: 25 → 0.2214,
  35 → 0.2209, 50 → 0.2207, 75 → 0.2220, 100 → 0.2253). Frozen Elo configuration for the test period:
  `{"k": 15, "home_advantage": 50, "carry": 0.75, "mov": true}` (validation Brier 0.22068; report
  `data/reports/validation-2026-09-30T142054Z`, dataset 67ea40d5f04f241e). The test period has not been accessed.
- **2026-09-30, holdout isolation (Codex re-review):** validation previously loaded every season and filtered only at
  scoring time. Reserved test rows were read, included in the dataset hash, and passed through model state, though
  never scored. `load_rows(conn, period)` now filters seasons in SQL: validation loads only warm-up + validation seasons,
  and `tune_elo` rejects any other season. The rerun (`data/reports/validation-2026-09-30T142603Z`) reproduced every metric,
  the bootstrap, and all 150 tuning trials exactly. Only the fingerprint changed: 67ea40d5f04f241e (all seasons) →
  08fe2552c1aa4579 (2019-20 to 2023-24). The test period is still unaccessed.

## Candidate models: logistic regression and calibration (recorded 2026-09-30, before running them)
Walk-forward on the same loadable seasons and cutoff rule. Model state is point-in-time: features are computed at predict
time from results available at the cutoff, and labels join the training set only when the result becomes available.

- **`logistic`**: L2-penalised logistic regression (λ = 1.0 on standardised features, intercept unpenalised; fixed, not
  tuned). It is refit once at the start of each season S on every labelled example from seasons < S. Before its first fit
  (2019-20) it falls back to the Elo probability; that season is warm-up and never scored. Features, all as home minus away
  where applicable:
  1. `elo_diff`: rating difference/100 from a separate walk-forward Elo using the frozen config (without home advantage).
  2. `mov10_diff`: mean scoring margin over each team's last 10 available results (0 with no history).
  3. `rest_diff`: days since each team's previous scheduled game, capped at 4 (4 for a team's first game).
     This is schedule information, known before tipoff.
  4. `b2b_home`, `b2b_away`: rest < 1.5 days. 5. `neutral`.
  Preprocessing: mean/std standardisation of `elo_diff`, `mov10_diff` and `rest_diff`, fit on the refit's training
  examples only. Binary features are left unscaled.
- **`elo_platt`, `logistic_platt`**: Platt scaling `σ(a·logit(p) + b)`, refit at each season start S on the base model's own
  out-of-sample predictions from earlier seasons (Elo: all seasons < S; logistic: 2020-21 to S−1, the seasons after its
  first fit). Before the first fit, predictions pass through unchanged. L2 λ = 1e-3.

**Selection rule:** compare every candidate with `elo` (frozen config) on validation Brier using the paired week-block
bootstrap (same seed and settings). A candidate is eligible only if the upper end of its 95% interval for
(candidate − Elo) is below 0. Among eligible candidates, pick the lowest validation Brier. If none is eligible,
**keep Elo**. Log loss and calibration are reported but do not drive selection. The selected model and the rule are frozen
before any test access.
- **2026-09-30, candidate selection result (validation only):** report `data/reports/validation-2026-09-30T143147Z`, dataset 08fe2552c1aa4579.
  No candidate met the rule, so **Elo (k 15, H 50, carry 0.75, MOV on) remains the selected model.** Candidate − Elo
  Brier (95% CI): elo_platt +0.00020 [−0.00023, +0.00066]; logistic −0.00035 [−0.00171, +0.00107]; logistic_platt
  +0.00008 [−0.00127, +0.00136]. The test period has not been accessed. The final test should report all six models,
  with Elo as the pre-selected primary model.
- **2026-09-30, frozen decision (Codex M3 review P1):** the test path previously reran candidate selection on test
  outcomes. Selection now runs only on validation. The decision is sealed (SHA-256) in `backend/app/models/frozen_decision.json`,
  built with `app.cli freeze-decision` from `data/reports/validation-2026-09-30T143147Z` (primary `elo`,
  k 15 / H 50 / carry 0.75 / MOV on, validation dataset 08fe2552c1aa4579). Test evaluation must check this decision
  (digest, exact config fields, primary = elo, validation seasons, current validation fingerprint) before logging access
  and loading test rows. Elo is the primary model of the final report whatever the test scores. Other models appear only
  as descriptive comparisons. No retuning; historical predictions and ledger rows unchanged. No scored final-test
  evaluation has been reported.

## Prospective candidates: 2026-27 shadow evaluation (recorded 2026-09-30, before any candidate is built or run)
Goal: test whether additional systems improve on the frozen Elo. It is not a promise of better accuracy, and "no
improvement found" is a valid outcome. Plan: docs/ACCURACY_PLAN.md (steps 0–7).

**Frozen evidence, not reused.** The 2024-25 and 2025-26 test was accessed once (2026-09-30T14:40:35Z). It is historical
evidence only: never used to tune, screen, select or justify a candidate. That includes logistic's descriptive test result. No further
`--period test` runs. `data/eval/test_access.jsonl` must stay at exactly 1 line. Frozen Elo
(`backend/app/models/frozen_decision.json`) remains the public live forecast unless the rule below promotes a candidate.

**Candidates (fixed list).**
- (a) Efficiency margin model: time-decayed, opponent-adjusted offensive/defensive points per 100 possessions and pace.
  Predicted margin → P(home win) = Φ(margin / σ), with σ fitted on training data only.
- (b) Existing `logistic` (as specified above).
- (c) `logistic_v2`: elo_diff, net-rating diff, rest_diff, b2b_home, b2b_away, neutral. No feature search.
- (d) Ensemble: only if at least 2 of (a)–(c) pass screening **and** their per-game validation Brier losses have a correlation < 0.95.
  Weights are fitted walk-forward on earlier seasons only.

(a) and (c) depend on possession data, and are built only if the source check passes (ESPN summary coverage of at least 95% of final games in
every season 2019-20 to 2025-26; findings in DATA_SOURCES.md). Each model has leakage tests: same-day unfinished games,
current-game labels, future rows, and preprocessing fitted on training data only.

**Screening (development only; not evidence of improvement).** Walk-forward on warm-up 2019-21 and validation 2021-24
(`load_rows(conn, "validation")`), with the same cutoff rule and bootstrap (ISO-week blocks, seed 20260930, 2,000 resamples).
A candidate passes if its validation Brier point estimate is below Elo's **and** the upper end of its paired 95% interval on
Brier(candidate − Elo) is below +0.001. This is deliberately lenient: validation favours Elo, which was tuned on it, and it
can also flatter tuned candidates. At most 2 candidates go to shadow, chosen by lowest validation Brier. If none passes, no shadow
candidate is recorded.

**Prospective test (the evidence).**
- Shadow candidates are sealed (config + this rule, SHA-256) before their first live forecast, ideally before opening night. Nothing
  is recorded retroactively: if sealing is late, the shadow starts on the seal date.
- Each records `run_type='live'` forecasts before the same cutoff as Elo, under its own `model_version`. There are no changes
  mid-season; a deliberate change becomes a new version, reported separately.
- **Decision set:** 2026-27 regular-season and play-in games where Elo and the candidate both have a forecast recorded
  before the cutoff, and a final result. Games missing either forecast are excluded and counted.
- **Baselines, scored on the same games:** constant 0.5, home-rate, frozen Elo, and DraftKings via ESPN (the last
  `current` quote retrieved no later than the forecast's `generated_at`, with the bookmaker's cut removed proportionally; amended 2026-09-30
  after Codex review, because a later quote holds information the forecast lacked). The pre-tipoff closing snapshot is reported separately. Market coverage is reported; missing quotes are
  never filled in.
- **Metrics:** accuracy, Brier score, log loss, 5-bin calibration, n, and date range. Paired per-game differences against Elo use the
  same week-block bootstrap.
- **Promotion rule (applied once, after the last play-in game):**
  - A candidate replaces Elo only if the upper end of the Bonferroni-adjusted interval (97.5% with 2 candidates, 95% with 1)
    on Brier(candidate − Elo) is below 0 **and** its log loss is not higher than Elo's.
  - If both pass, the lower Brier wins.
  - With fewer than 800 games in the decision set, the result is "inconclusive" and Elo stays.
  - Interim reports are for operations only and are never used for decisions.
- Market comparison is reported alongside but does not affect promotion.

## Efficiency feature definition (recorded 2026-09-30, step 3, before any candidate is fitted or scored)
Fixed in `backend/app/models/efficiency.py`; these constants are **not tuned** in step 4 (changing them is a new, dated amendment):
- **Inputs:** `team_possession_observations`, delivered by `backtest.run(..., box_events)` only when `available_at <= cutoff`.
  A game's own box score available at or before its cutoff raises `LeakageError`. Per game, the latest delivered observation is used
  (the POSSESSION_AS_OF_SQL rule); incomplete lines are skipped and counted.
- **Per game:** possessions = mean over both teams of FGA − OREB + TOV + 0.44·FTA; ORtg = 100·pts/poss, DRtg = 100·opp pts/poss,
  pace = poss (overtime is not normalised).
- **Per team at the game's tipoff date:** current-season games with weight 0.5^(age_days/30). Opponent- and home-adjusted by 20 fixed-point
  rounds, then shrunk toward the team's previous-season rating (measured as of that season's last delivered game) with weight
  10 games, or toward the league average if there is no previous season. Home edge per team per 100 possessions = the weighted
  (home ORtg − away ORtg)/2 over non-neutral games, shrunk toward 1.25 with weight 100 home games.
- **Features (home minus away):** net_diff, pace, home_edge_per100, margin_est = (net_diff + home edge)·pace/100, and games counts.
- **Real-data check (validation load only, no outcomes examined):** 6,276 games / 6,270 box events (seasons 2019-20 to 2023-24; test
  seasons not loaded), box_version 9ad11c5aa1590a46; all features finite; about 10 s. Frozen Elo validation Brier is unchanged with box events
  passed (0.22068).
- **2026-09-30 amendment (Codex step 3 review, before any candidate is fitted or scored):**
  - *Delivery gate.* A box observation is delivered only when **both** its own `available_at` and its game's
    `result_available_at` are at or before the cutoff (effective availability = the later of the two). Events are ordered by
    effective availability. A box with no matching eligible result row, or one available at or before its own game's cutoff, raises `LeakageError`.
  - *Missing data.* Features use the most recently observed **complete** observation among those delivered (last-complete policy).
    An incomplete newer observation is counted, does not replace the active values and does not advance the freshness watermark
    (the `(observed_at, fetch_id)` of complete observations only). The result depends on the set of delivered events, not their order.
    This supersedes the "POSSESSION_AS_OF_SQL rule" wording above: that query and the coverage report use the latest observation, complete or not.
  - *Step 4 scope.* `logistic_v2` keeps the registered features only: elo_diff, net-rating diff, rest_diff, b2b_home, b2b_away, neutral.
    Travel, time-zone and altitude features are **not** included. They need verified venue and schedule data (actual venues, preceding
    locations, neutral games, DST) and a written definition, as a separately specified candidate; travel is never inferred from home arenas.
  - Real-data check after the change: the validation feature fingerprint is unchanged (6,276 games, box_version 9ad11c5aa1590a46),
    because every stored observation's availability equals its result's availability (17,826 of 17,826).

## Step 4 candidate definitions (recorded 2026-09-30, before the screening run on real data)
Implemented and tested on synthetic data only at the time of writing; no candidate has been run on real games.
- **Screening run:** `app.cli screen-candidates` → `backtest.evaluate_screening`. Loads warm-up + validation seasons only
  (`load_rows` / `load_box_events` with period "validation"), uses the **frozen Elo configuration** read from
  `frozen_decision.json` (no tuning, no grid), writes a report under `data/reports/screening-*`, records nothing in the ledger and
  performs no test access. Scored on validation 2021-22 to 2023-24. The global `FEATURE_VERSION` is unchanged (live Elo is unaffected);
  the screening report carries `pregame-ids-v1+prior-day-et-cutoff+possession-obs-v1` and the box fingerprint.
- **(a) `efficiency_margin`:** P(home win) = Φ(margin_est / σ), with margin_est from the fixed efficiency features. σ is refit at each
  season start as the root-mean-square of (actual home margin − margin_est) over all games of earlier seasons, using margin_est as
  captured at predict time. Before the first fit (fewer than 50 earlier-season games) σ = 12.0. No other parameter.
- **(b) `logistic`:** unchanged (the existing model and configuration).
- **(c) `logistic_v2`:** the same procedure as `logistic` (L2 λ = 1.0 on standardised features, intercept unpenalised, refit at each season
  start on all earlier seasons, Elo fallback before the first fit), with features elo_diff, net_diff (efficiency net-rating difference,
  home minus away), rest_diff, b2b_home, b2b_away, neutral. elo_diff, net_diff and rest_diff are standardised on the refit's training examples only.
- **Screening rule (as registered above):** passes if validation Brier is below Elo's and the paired week-block 95% upper bound on
  Brier(candidate − Elo) is below +0.001; at most 2 by lowest validation Brier. The report also gives per-game Brier-loss correlations
  for the ensemble precondition (at least 2 passed and correlation < 0.95). The ensemble itself is step 5 and is not built here.
- **One run.** The screening is run once. Constants are not adjusted afterwards; any change is a new dated amendment and is reported as such.

## Step 6: sealed challenger, in-season inputs and shadow recording (recorded 2026-10-01, before sealing; no shadow forecast exists)
- **Shortlist:** `logistic_v2` only (BASELINE_REPORT "Shadow-candidate screening"). One candidate, so the promotion interval is 95%.
  No ensemble (precondition not met).
- **Seal:** `backend/app/models/shadow_decision.json`, written once by `app.cli seal-shadow --report <screening report>` (it refuses to
  overwrite) and digest-sealed like the Elo decision. It holds the complete definition (logistic settings and features, frozen Elo config,
  every efficiency constant, the missing-data, delivery and stale-input rules, cutoff, feature version), the promotion rule, the screening
  evidence and the frozen Elo decision's digest. On every run `shadow.load_shadow_decision` rejects a broken seal, a different frozen Elo
  decision, or code whose constants no longer equal the sealed definition. The ledger `model_version` is
  `logistic_v2-shadow-<first 12 hex of the definition hash>`, so any changed definition is a new version, reported separately.
- **Shadow forecasts** (`app/shadow.py`): `run_type='live'`, the sealed `model_version`, feature version
  `pregame-ids-v1+prior-day-et-cutoff+possession-obs-v1`, generated before the same cutoff as Elo, append-only. The model state is rebuilt
  each run by the backtest walk (results and boxes only once available), then advanced to the generation time. Every eligible game without
  an available result is passed through in tipoff order so rest days match the backtest. The dataset version covers results, delivered
  boxes and that schedule. The Elo job, Elo scoring and the app filter on the Elo `model_version` and never read shadow rows.
- **In-season possession inputs:** `app.cli boxscores` fetches the ESPN game summary for eligible finals of the last 14 days that have no
  retrieval with complete lines for both teams (at most 30 requests per run, 5 per game ever), and re-fetches once a box first seen sooner
  than 24 h after tipoff. Each retrieval is an observation stamped with its own retrieval time (`observed_retrieval`, never backdated),
  delivered only when the result is also available. A summary not marked completed records nothing. Failures, still-missing games and
  games given up on are listed in the job result.
- **Stale inputs (fixed before the season):** a game gets **no shadow forecast** from a run if either team lacks a complete delivered box
  for any of its 5 most recent finals of the current season (results available at generation time). The skip is reported (the shadow job
  is marked failed) and a later run before the cutoff may record the forecast. Older gaps are tolerated and counted. Games left without a
  shadow forecast fall outside the decision set and are counted, as already registered.
- **Enabling:** `daily` runs the possession refresh and the shadow forecast only when `NBA_SHADOW_RECORDING=1`. It is **off** until Codex
  reviews this step; the Elo steps are identical either way.
- **`shadow-report` (added 2026-10-01, before any shadow forecast exists):** interim mode is operational only (coverage, exclusions, job
  health, descriptive scores) and never computes the interval against Elo or a verdict. `--final` is refused until all regular-season and
  play-in games are finished, applies the sealed rule once, logs it to `data/eval/shadow_promotion.jsonl` and returns that record on later calls.
  It changes no decision or forecast; a promotion would be a separate reviewed step.
- **Refresh budget (amended 2026-10-01 after Codex review):** the 30-per-run and 5-per-game limits count every HTTP attempt, including retries,
  network failures and failed imports, recorded in `box_fetch_attempts`.
- **Amendments 2026-10-01 (Codex re-review, before any shadow forecast exists):**
  - *Retrieval time.* The refresh stamps an observation's `observed_at`/`available_at` from the clock only after the response has
    arrived; the attempt log keeps the request's start time. A response that arrives after a cutoff is never available before it.
  - *Season completion for `--final`.* "After the last play-in game" is verified from positive evidence, never from the absence of pending
    rows: all 30 teams have at least 82 final regular-season games; no regular-season or play-in game is scheduled or in progress; no play-in
    game is postponed or canceled; exactly 6 play-in games are final; and a postseason game is scheduled after the last play-in game. If any
    check fails, or the league's format differs from these numbers, the final report refuses to run and nothing is logged. The checks
    and what was found appear in every report.

## Research round 2: one gradient-boosted tree candidate (recorded 2026-10-01, before the model is built or run)
Authorised by the user on 2026-10-01 as an implementation round. It is research on development seasons only. It does **not** change the
live Elo, the sealed `logistic_v2` shadow experiment, its promotion rule, or the ledger, and the result cannot join the 2026-27 shadow
experiment. A good result would only justify proposing a separately sealed prospective test.

- **Candidate `gbt_v1`:** scikit-learn `HistGradientBoostingClassifier` (version recorded in the report; `scikit-learn>=1.5,<2` added as a
  dev dependency only: the API and the scheduled jobs never import it).
- **Settings, fixed here, no sweep:** log-loss objective, learning_rate 0.05, max_iter 200, max_depth 3, max_leaf_nodes 8, min_samples_leaf 50,
  l2_regularization 1.0, max_bins 255, early_stopping off, random_state 20260930. Monotonic increasing constraints on elo_diff, net_diff
  and mov10_diff; none on the other features. No calibration step, no feature selection.
- **Features (existing ones only, as captured at predict time):** elo_diff, mov10_diff, rest_diff, b2b_home, b2b_away, neutral (the
  `TeamFeatures` used by `logistic`) and net_diff, net_home, net_away, pace, games_home, games_away (the fixed efficiency features). No scaling.
- **Training:** chronological only. Refit at each season start on every labelled example from earlier seasons; before the first fit the
  frozen Elo probability is returned. No random split, no cross-validation, no test-season rows.
- **Evaluation, run once:** `backtest.run` walk-forward on warm-up + validation seasons with the same cutoff and box-delivery rules, frozen Elo
  configuration, scored on the 3,962 validation games (2021-22 to 2023-24).
- **Reported on identical games for Elo, logistic_v2 and gbt_v1:** accuracy, Brier, log loss, 5-bin calibration, per-season scores; paired
  Brier difference against Elo and against logistic_v2 (ISO-week block bootstrap, seed 20260930, 2,000 resamples); paired accuracy
  difference with the counts of games only one model got right and the same bootstrap interval.
- **Reading fixed in advance:** the report states whether gbt_v1 would pass the step 4 screen against Elo (Brier below Elo's and the 95%
  upper bound of Brier(gbt_v1 − Elo) below +0.001) and gives the same comparison against logistic_v2. Nothing is selected or promoted. These
  seasons were already used to tune Elo and to screen three candidates, so any apparent gain is optimistic. Settings are not adjusted
  after the result; any second configuration would be a new dated entry.

## Research round 3: learned stacking (recorded 2026-10-01, before the stackers are built or run)
Proposed in docs/STACKING_RESEARCH.md and authorised by the user on 2026-10-01. Development research only: it does not change the live
Elo, the sealed `logistic_v2` shadow experiment or the ledger, and no result here can join the 2026-27 shadow experiment. A good result
would only justify a separately sealed prospective test.

- **Base models (all five, none dropped):** Elo (frozen config), efficiency_margin, logistic, logistic_v2, gbt_v1, each run once with
  `backtest.run` on warm-up + validation seasons, exactly as already defined. Their predictions are walk-forward: every one was made before its game.
- **Genuine out-of-sample base rows start in 2020-21.** In 2019-20 the fitted models only return the Elo probability (and efficiency_margin
  its starting σ), so that season is dropped from stacker training. The run checks that each fitted base model's first fit is 2020-21.
- **Stacker training, chronological:** at the start of each validation season S (2021-22, 2022-23, 2023-24) the stacker is fitted on the base
  predictions and results of seasons 2020-21 … S−1 only, then predicts every game of season S. The run asserts that every training result
  was available before the first cutoff of season S. No random split, no cross-validation, no test seasons.
- **Stacker A (`stack_logit`):** L2-penalised logistic regression (existing `fit_logistic`, λ = 10, intercept unpenalised) on the five base
  probabilities as logits, each probability clipped to [0.02, 0.98] first, each input standardised on the training rows only.
- **Stacker B (`stack_conditional`):** A's five inputs plus three fixed interactions, with m = mean of the five clipped logits:
  m × d (d = highest minus lowest base probability, the disagreement), m × |rest_diff| and m × |net_diff| (rest and efficiency gap as captured
  pregame by logistic_v2). Same λ = 10 and standardisation. No other inputs, no feature search, no penalty search.
- **Comparators on identical games (3,962 validation games):** Elo, logistic_v2 and the equal-weight average of the five base probabilities.
- **Reported:** accuracy, Brier, log loss, 5-bin calibration, per-season scores; paired Brier and paired accuracy differences (ISO-week block
  bootstrap, seed 20260930, 2,000 resamples) of each stacker against logistic_v2, Elo and the equal average; fitted weights per season.
- **Selective prediction, fixed levels 50%, 70%, 85%, 100%:** for each method the most confident games (largest |p − 0.5|; ranking uses no
  outcome) at each coverage, with accuracy and a Wilson 95% interval. Also the all-five-agree filter at its own coverage, against each
  method's most confident games at that same coverage, and each base model's accuracy on stacker B's selected games. These rankings are made
  within the evaluated seasons, so they describe the past; an operational threshold would have to be fixed on earlier data.
- **Reading fixed in advance:** each stacker is reported against the step 4 screen versus Elo and against logistic_v2 directly. Nothing is
  selected or promoted. These seasons have now been used to tune Elo and to examine five candidate models and several combinations, so any
  apparent gain is optimistic. Run once; settings are not adjusted afterwards.

## Near-tipoff forecast policy (recorded 2026-10-01, before the first such forecast exists)
A separate forecast for games starting soon, requested by the user. It does **not** change the night-before Elo forecast, the sealed
`logistic_v2` shadow experiment, their promotion rule or their records.

- **Model:** the frozen Elo configuration, unchanged. Its state includes every result available at the moment of generation, instead of
  stopping at midnight Eastern. It knows nothing about injuries or line-ups; the app says so next to every near-tipoff forecast.
- **Separation:** stored as `run_type='live'` under its own `model_version` (`<Elo version>-policy=near_tipoff`) and feature version
  `pregame-ids-v1+tipoff-cutoff`, with `cutoff_at` = the game's scheduled tipoff. The night-before queries, the shadow report and the app's
  default views filter on the plain Elo version and never read these rows. Earlier forecasts are kept; nothing is overwritten.
- **When:** only for scheduled regular-season, play-in or postseason games tipping off within 24 hours and strictly after the generation
  time. The ledger additionally rejects any forecast generated after its cutoff or with a cutoff after tipoff.
- **Refused, with the reason reported, never guessed:** after tipoff; when the last successful schedule and results refresh is more than
  60 minutes old (`stale_results`); when either team has an earlier game that is not yet final in the store (`missing_result`).
- **Freshness recorded per forecast** (`near_tipoff_forecasts`, append-only): generation time, tipoff, time of the results refresh used, newest
  result included, number of results used, and the market quote available at that moment.
- **Market comparison:** the latest `current` quote retrieved at or before the forecast's generation time, de-vigged proportionally. Coverage is
  reported; a missing quote is "no market comparison".
- **Scoring:** reported separately from the night-before record, on the latest near-tipoff forecast per game. Where both exist for the same
  game they are compared with the paired tools already used; no decision or promotion follows from it. A near-tipoff forecast has more
  information than a night-before one, so the two records are not interchangeable.

## NHL evaluation plan (recorded 2026-09-30, before any NHL data is imported or any NHL model is run)
Separate from every NBA rule above; no NBA parameter value or result is transferred. Store: `data/nhl.sqlite` (docs/MULTISPORT_PLAN.md).
Before this was written, one NHL schedule week (2025-04-10) and the 2024-25 stats-REST game list were opened **for schema checks
only** (field names, state codes, shootout scoring); no model, rate or accuracy was computed from them.

- **Data:** NHL-owned stats REST season game lists (one request per season) for 2015-16 … 2025-26, all in the 3-on-3 overtime era.
  Eligible: final regular-season and playoff games (`gameType` 2 and 3). Excluded: preseason, All-Star and other exhibitions
  (types 19/20 and similar), and unplayed "if necessary" playoff games. Season = ending year (2016 = 2015-16).
- **Decision type:** regular season `period` 3 = REG, 4 = OT, 5 = SO; playoffs: 3 = REG, > 3 = OT (no shootouts).
- **Point in time:** as for the NBA: night-before cutoff 00:00 America/New_York on the game's Eastern date; historical results
  available at the assumption tipoff + 4 h; a result updates a model only when available at or before the cutoff.
  2020 playoff hub games (2020-08-01 … 2020-09-29, Toronto/Edmonton) are treated as neutral site.
- **Franchise continuity:** Arizona → Utah (relocated player assets) carries the rating; expansion teams (Vegas 2017-18,
  Seattle 2021-22) start at the initial rating. The exact team-id map is recorded in `app/sports.py` once verified from the source.

| Period | Seasons | Use |
|---|---|---|
| Warm-up | 2015-16, 2016-17 | burn-in only, never scored |
| Development | 2017-18 … 2020-21 | each candidate's own hyperparameter grid (fixed below) |
| Validation | 2021-22, 2022-23, 2023-24 | choosing among the tuned candidates, once each |
| Test (reserved) | 2024-25, 2025-26 | run once with the frozen choice; every access logged to `data/eval/test_access_nhl.jsonl` |
| Prospective | 2026-27 | live night-before and near-tipoff record |

- **Winner candidates:** constant 0.5; Laplace home rate; `hockey_elo` (own class; K ∈ {4, 6, 8, 10, 12}, H ∈ {0, 15, 30, 45, 60},
  carry ∈ {0.5, 0.75, 1.0}, goal-margin multiplier off/on with `mult = ln(|margin| + 1) / ln 2`; criterion: development Brier;
  ties → MOV off, carry 1.0, smallest K); `goals_poisson` (below; its winner probability).
- **Goals model `goals_poisson`:** regulation goals are independent Poisson with `log λ_home = μ + h + att_home − def_away`,
  `log λ_away = μ + att_away − def_home`; regulation score = final score minus the winner's deciding goal when the decision is OT/SO.
  After each available result, one gradient step on the Poisson log-likelihood with rate η updates att/def; μ and h are running
  league means (log scale). Between seasons att/def shrink by `carry`. Tied regulation → home wins OT/SO with a Laplace-smoothed
  running share. The final-score distribution (truncated at 15 goals each) gives winner, `reg3`, puck line, totals and team totals.
  Grid: η ∈ {0.005, 0.01, 0.02, 0.04}, carry ∈ {0.5, 0.75, 1.0}; criterion: development mean negative log-likelihood of the
  regulation score; ties → smaller η, then carry 1.0. Reference: `league_poisson` = the same model with η = 0 (no team strength).
- **Selection (validation, once):** the primary NHL winner model is the lower validation Brier of tuned `hockey_elo` and
  `goals_poisson`, with Elo kept unless the goals model's paired 95% upper bound of (goals − Elo) Brier is below 0. Puck line,
  totals and `reg3` use `goals_poisson` (the only distributional candidate); they are reported against `league_poisson` on
  log loss / Brier of each market at standard lines (puck line ±1.5, totals 5.5 and 6.5).
- **Metrics:** accuracy, Brier, log loss, 5-bin calibration, per season, paired week-block bootstrap (seed 20260930, 2,000
  samples), coverage and exclusions. No historical odds exist: no market comparison is possible or claimed.
- **Confidence filters:** NHL thresholds and records are computed from NHL forecasts only; NBA figures are never shown for NHL.

## NBA spread and total research (recorded 2026-09-30, before the model is built or run)
Development research only. It must not touch the sealed Elo, the `logistic_v2` shadow, or the spent 2024-25/2025-26 seasons.
- **Model `points_normal`:** online offence/defence point ratings (`pts_home = μ + h/2 + off_home − def_away`, away likewise,
  one gradient step of rate η per available result, `carry` between seasons). Margin and total are Normal with standard
  deviations equal to running root-mean-square residuals of earlier available results; integer probabilities use a ±0.5
  continuity correction; a tied margin goes to overtime and is removed from the winner probability. Team totals follow
  from the same margin/total pair.
- **Data:** warm-up 2019-20 and 2020-21 (burn-in); tuning on 2021-22 and 2022-23 (η ∈ {0.02, 0.05, 0.1}, carry ∈ {0.5, 0.75, 1.0},
  criterion: mean Gaussian negative log-likelihood of margin plus total); 2023-24 reported once as a development holdout.
  Reference: the same model with η = 0. Bound/sum/monotonicity checks are tested.
- **Use:** model-only lines are labelled; prospective 2026-27 forecasts at the stored market line are the only independent
  evidence. The rule for judging them is the plain report of win/loss/push counts, Brier and log loss against the de-vigged
  market at the same line, with no promotion decision attached.
- **2026-09-30 amendment (development seasons only; validation and test not yet accessed):** the first development tuning on
  real data (7,515 loadable games, 4,877 scored development games, 0 without a decision) chose grid-edge values for both
  candidates: Elo K = 4 (smallest K; best Brier 0.24072, H = 30, carry 0.75, MOV on) and goals η = 0.005 (smallest η; NLL 3.8064).
  The grids are extended downwards, as for the NBA home-advantage grid: Elo K ∈ {2, 3, 4, 6, 8, 10, 12} and goals
  η ∈ {0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.04}. Criteria and tie rules are unchanged. Home-rate development Brier 0.24841.
- **2026-10-01 result (development research, run once):** selected η 0.05, carry 0.5 (tuning NLL 8.3853 on 2,643 games; carry
  is at the grid's lower edge; the grid was **not** extended because the 2023-24 holdout had already been viewed). Holdout
  2023-24 (1,319 games): margin MAE 11.16 / RMSE 14.34, total MAE 14.50 / RMSE 18.12 (reference 12.52 / 15.72 and 15.95 / 19.94);
  central 50%/80% interval coverage margin 0.52/0.80, total 0.48/0.81; NLL − reference −0.189 [−0.222, −0.155]. Dataset
  08fe2552c1aa4579 (= the NBA validation dataset: no test-season rows). Sealed: `backend/app/models/nba_market_decision.json`.

## NHL agreement research (recorded 2026-10-01, before any agreement figure was computed)
Requested by the user. Development research; it does not change the live NHL model (`hockey_elo`, sealed).
- **Data:** development seasons 2017-18 … 2020-21 only (warm-up 2015-16, 2016-17 for burn-in), walk-forward with the sealed configurations
  (`hockey_elo` K 4, H 30, carry 0.75, MOV on; `goals_poisson` η 0.003, carry 0.75). Validation (2021-24) and the spent test seasons (2024-26)
  are not read. Caveat: both configurations were tuned on these seasons, so every method here is optimistic in absolute terms; the
  comparisons between methods use the same predictions.
- **Methods (fixed):**
  1. Elo alone; goals model alone (each picks its favoured side; confidence = max(p, 1 − p)).
  2. **Average:** p_avg = (p_elo + p_goals) / 2. This is how agreement methods produce a probability: a selected game's probability is
     p_avg; agreement itself does not create a probability.
  3. **Unanimous:** games where both models pick the same side (p = 0.5 counts as no pick; none expected).
  4. **Unanimous + threshold:** unanimous games with average confidence ≥ t, t ∈ {0.55, 0.60, 0.65}.
  5. **Matched-coverage single-model filters:** for each subset in 3-4 with coverage c, the top-c share of development games ranked by
     Elo confidence, by goals confidence and by average confidence (ties broken by game id).
- **Reported:** games, coverage, wins, losses, accuracy with Wilson 95% interval; Brier and log loss for probability methods (Elo, goals,
  average) on all games and on each subset; each base model's accuracy and Brier on each exact subset; both models on the disagreement
  games. Uncertainty: paired week-block bootstrap (seed 20260930, 2,000) for identical-game comparisons; for subsets of different games,
  a week-block bootstrap of the accuracy difference (both subsets recomputed from the same resampled weeks).
- **Decision rule:** a combination is called promising only if (a) the average's paired Brier upper bound vs the better single model is
  below 0 on all games, or (b) an agreement subset's accuracy minus Elo's matched-coverage filter has a 95% lower bound above 0. One
  primary comparison per subset; with 4 subsets, chance findings are possible and are labelled as such. A promising combination is
  frozen as a separate forward (shadow) experiment with its own model version; the live model is not changed.
- **Result (2026-10-01):** no method met the rule (average − Elo Brier +0.0006 [+0.0001, +0.0010]; every agreement subset within ±0.6
  points of Elo's matched-coverage filter, all intervals including 0). Nothing frozen. Details: docs/NHL_BASELINE_REPORT.md.

## NHL learned candidates: logistic and gradient-boosted trees (recorded 2026-10-01, before either is built or run)
Requested by the user ("we need multiple models to test and learn from"). Development research; the live model stays `hockey_elo`.
- **Features (pregame, point in time; no search):** `elo_diff` (sealed hockey_elo ratings / 100), `xg_diff` (sealed goals model expected
  regulation goals home − away), `gd10_diff` (difference of the teams' mean goal differential over their last 10 available results),
  `rest_diff` (days since previous game, capped at 4), `b2b_home`, `b2b_away` (previous game < 1.5 days earlier), `neutral`.
- **`hockey_logistic`:** L2 logistic regression (λ = 1, the NBA setting, not tuned), scaled `elo_diff`, `xg_diff`, `gd10_diff`, `rest_diff`.
- **`hockey_gbt`:** scikit-learn HistGradientBoosting with the NBA `gbt_v1` settings unchanged (depth 3, 8 leaves, 200 iterations, rate 0.05,
  min leaf 50, L2 1.0, seed 20260930), monotonic increasing in `elo_diff`, `xg_diff`, `gd10_diff`.
- **Fitting:** both refit at the start of each season on all earlier seasons' examples, features captured when the game was predicted and
  labels added only when the result was available (the NBA walk-forward discipline). Before the first fit they return the Elo probability.
- **Data:** warm-up 2015-16 and 2016-17, development 2017-18 … 2020-21 scored. Every development season is out of sample for the learners
  (no hyperparameter tuning). Elo and goals configurations were tuned on these seasons, so the inputs carry that optimism.
- **Compared with Elo on identical games (paired week-block bootstrap):** each learner; the equal average of Elo, goals, logistic and GBT;
  four-model unanimous picks (probability = that average) against Elo's own confidence filter at the same coverage; each model's accuracy
  and Brier on the exact unanimous subset.
- **Decision rule:** a candidate passes screening if its paired Brier upper bound vs Elo is below 0 on development. A passing candidate is
  then checked once on validation 2021-24 (labelled as reused: those seasons chose Elo over the goals model) and, if still better, frozen as
  a separate NHL shadow experiment with its own version. The spent test seasons 2024-26 are never read. No candidate replaces Elo
  without a prospective record.
- **Result (2026-10-01):** nothing passed (logistic − Elo Brier −0.0002 [−0.0008, +0.0004]; GBT +0.0027 [+0.0012, +0.0042]; four-model
  average +0.0003 [−0.0003, +0.0008]; four-model unanimity 0.8 points below Elo's matched-coverage filter). Validation not accessed; nothing
  frozen. Details: docs/NHL_BASELINE_REPORT.md.

## NBA availability candidate `avail_v1` (recorded 2026-10-01, before any report is downloaded for it or any feature is computed)
Requested by the user. A new, separately dated experiment: the frozen Elo, the sealed `logistic_v2` shadow and their rules are unchanged.
Sources and checks: docs/AVAILABILITY_AUDIT.md (gates 1-4 apply), docs/AVAILABILITY_FEASIBILITY.md.

**Forecast time (`evening` cutoff).** 17:45 America/New_York on the game's date. Only games tipping off at or after 18:00 ET are scored;
earlier games are excluded and counted. Results are usable if available by the cutoff (historical assumption: tipoff + 4 h). The baselines
are recomputed at this same cutoff, so every comparison has the same information time.

**Report.** The official NBA injury report of 5:30 PM ET on the game date (file name per the published naming rule; never the literal
hour as a time). Publication time = the header time read as ET; if `Last-Modified` differs by more than 2 minutes, the later of the two,
flagged; a report without a readable header time or `Last-Modified` is excluded (counted). Usable only if published at or before 17:45 ET.
Historical availability is therefore `assumed_published_time`, never observed. One request per game date (≤ 2 retries, ≥ 1.2 s apart),
seasons 2019-20 … 2023-24 only. **The spent test seasons 2024-25 and 2025-26 are neither downloaded nor read.**

**Parser and reconciliation.** One parser for both producers (iTextSharp; GemBox, whose page text is reversed), reading game date,
matchup, team, player and status, and the "NOT YET SUBMITTED" marker. Every report is reconciled: entries parsed = name-plus-status lines
found by a looser pattern; a report failing reconciliation is excluded and listed.

**Player matching.** Report names ("Last, First") are normalised (accents, punctuation, suffixes Jr/Sr/II-V removed) and matched, in order, to
sportsdataverse ESPN player-box athletes (SHA-verified files, 2019-20 … 2023-24): (1) same team, same season; (2) same team, any loaded season;
(3) a unique league-wide match in the same or previous season; (4) a reviewed alias table committed with the code. Anything else, including
ambiguous names, is **unresolved**: kept, counted per season, never treated as healthy.

**Expected minutes.** Mean minutes over the player's last 10 games played (minutes > 0) before the game date in the same season if he has at
least 3, otherwise his last 10 of the previous season, otherwise 0 (counted as `no_history`). Unresolved players contribute 0 minutes and are
counted. Only box scores of earlier dates are used; who eventually played is never an input.

**Status weights.** Out 1, Doubtful 1, Questionable 0.5, Probable 0, Available 0. A player not on the report is not assumed healthy if his
team has "NOT YET SUBMITTED"; that team is marked unsubmitted.

**Features (per game; positive favours home):** `new_absent_diff` = (away − home) of Σ weight × expected minutes over listed players who
played in their team's most recent game before the date, / 48; `all_absent_diff` = the same over all listed players, / 48;
`report_missing` = 1 if no usable report exists for the date or either team is unsubmitted (then both absence features are 0).

**Candidate.** `avail_v1` = the sealed `logistic_v2` feature set plus the three features above: L2 logistic (λ = 1, the existing setting),
continuous features scaled on the training window, refit at each season start on earlier seasons only, Elo before the first fit. No
search over features, weights or thresholds.

**Data periods.** Feature burn-in 2019-20; first fit at the start of 2020-21 (warm-up, not scored); scored 2021-22 … 2023-24. These seasons
already selected Elo and screened `logistic_v2`, so every result is **exploratory development evidence**; independent evidence can only
come from a forward record.

**Comparisons (all on identical games, paired week-block bootstrap, seed 20260930, 2,000 resamples):**
1. `avail_v1` vs `logistic_v2` (incremental value of availability) and vs Elo: Brier, log loss, accuracy, 5-bin calibration, per season.
2. Combinations (fixed set): equal average of Elo and `avail_v1`; Elo and `avail_v1` both picking the same side (probability = their
   average) against Elo's and `avail_v1`'s own confidence filters at the same coverage; `avail_v1`'s top 50% and top 25% most confident games
   against Elo's at the same coverage. Each base model is reported on each exact subset.
3. Diagnostics only (no gate): per-game Brier-loss correlation of `avail_v1` with Elo and with `logistic_v2`; how often they pick
   different sides and who is right then.
4. Coverage: dates with a usable report, unsubmitted teams, unresolved entries, `no_history` players, excluded early games.

**Decision rule.** Availability is reported as adding measurable value only if the paired 95% upper bound of Brier(`avail_v1` −
`logistic_v2`) is below 0. A combination is reported as adding value only if its comparison interval excludes 0 in its favour. Either result
may be registered as a **separate forward candidate** with its own dated rule and model version, recorded from its registration date only;
nothing is promoted from this development evidence. A null or negative result is reported as such.
- **2026-10-02 amendment (after the first run, before the corrected rerun):** the first run
  (`data/reports/nba-availability-2026-10-02T014517Z`, avail_v1 − logistic_v2 Brier −0.0023 [−0.0041, −0.0005]) is **superseded**. An
  independent leakage review found that games tipping off before 18:00 ET (unscored, kept at the night-before cutoff) still received that
  date's 5:30 PM report as a training feature: about 7% of training rows used a report published after their tipoff. Fixed: such games
  always get `report_missing = 1` and zero absence features. Also fixed: the season of a report date is the stored season of its games, not
  the calendar (the 2020 bubble ran August-October 2020 in season 2019-20). No definition, feature, weight, threshold or comparison changed;
  the evaluation is rerun once with the fixes and that run is the recorded result.
- **Result (2026-10-02, corrected run):** passes the rule: avail_v1 − logistic_v2 Brier −0.0022 [−0.0040, −0.0004]; details
  docs/AVAILABILITY_RESULTS.md.

## Forward candidate `avail_v1` (registered 2026-10-02; separate from the sealed logistic_v2 shadow, which is unchanged)
- **Definition:** exactly the code at commit 46272ee (`availability.avail_v1`, sealed logistic_v2 settings + the three features), refit at
  each season start on earlier seasons; model version `avail_v1-evening` plus the definition hash recorded with the first forecast.
- **When:** the `evening` policy, 17:45 ET on game day, using the 5:30 PM report as retrieved live (availability = our download-completion
  time, never the printed time); games from 18:00 ET. Frozen Elo and logistic_v2 are recorded at the same cutoff as baselines.
- **Recording starts only when** the live pipeline exists and is reviewed: (1) current-season player minutes from game summaries collected by
  the existing box-score job (not yet parsed), (2) an evening job that refreshes, parses the report and records the three forecasts
  (a new persistent job: needs the user's installation approval). Nothing is backfilled.
- **Report:** regular season and play-in games with all three forecasts and a final result; paired Brier and log loss vs logistic_v2 and Elo
  with week-block 95% intervals; coverage and missing-report counts. Fewer than 800 games is inconclusive. No promotion follows
  automatically; any switch of the live model is a separate decision.

## User-directed two-hour forward policy — 2026-10-02

New production forecasts for NBA and NHL, for every supported market, are restricted to [tipoff minus 2 hours, tipoff). Existing midnight forecasts, shadow rows and reports remain immutable archives; that earlier shadow protocol is discontinued, not silently relabelled. The periodic odds jobs now trigger forecasts; daily jobs only maintain data and scores.

Winner agreement is new descriptive forward research: NBA Elo, logistic, efficiency logistic, efficiency margin, boosted trees, and a separate availability_two_hour candidate; NHL Elo, Poisson goals, logistic, boosted trees. No candidate automatically replaces a primary model. Code/configuration changes create separate model identities. Missing models are explicit; only a complete same-input cohort counts toward the unanimous/strict-majority record. Agreement is not a calibrated probability. Non-winner markets retain their own score-distribution model: winner votes are not market votes.

Availability_two_hour reuses the registered availability features, status weights, prior-minute estimator and logistic settings without tuning. Its chronological training cutoff is two hours before each historical game; an existing historical report is usable only if its publication assumption precedes that cutoff. Missing historical reports retain the registered missing flag. The prior 17:45 experiment is not an accuracy claim for this new definition. Live inputs require a reconciled report both published and actually received within 60 minutes, before calculation; unsubmitted teams, unresolved weighted players, missing prior-season minutes or current-season minute coverage skip this candidate. Minute seeds are bulk ESPN player files verified against GitHub's published digest, available only from actual download time; subsequent finished-game summaries use their own retrieval times. Historical training facts are checksum-sealed separately. No fresh historical performance screening is authorized by this amendment.

Track fixed confidence thresholds 50–90% in five-point steps, per sport, market, model version and line provenance. Show wins/losses, pending, pushes/voids, sample size, coverage and descriptive Wilson intervals (not corrected for repeated selection or game dependence). Reports select the latest valid pregame forecast per game/market/model; all earlier versions stay in the ledger. Threshold exploration is descriptive: selecting a threshold later requires a dated forward rule and new future evidence. No retrospective rebranding of a winning subset.
