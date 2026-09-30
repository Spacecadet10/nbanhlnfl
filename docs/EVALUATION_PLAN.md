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
