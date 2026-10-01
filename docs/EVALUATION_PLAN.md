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
