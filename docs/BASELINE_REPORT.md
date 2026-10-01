# Baseline report: validation period (home-rate vs Elo)

Generated 2026-09-30 by `python -m app.cli backtest --period validation`
(`data/reports/validation-2026-09-30T142603Z`, after the holdout-isolation fix; metrics are identical to the earlier runs).
Method: docs/EVALUATION_PLAN.md. Dataset version `08fe2552c1aa4579` (2019-20 to 2023-24 only). The ledger's
backtest rows were recorded earlier under `67ea40d5f04f241e`, a hash that also covered test-season rows (see the plan's amendments). Feature version `pregame-ids-v1+prior-day-et-cutoff`.
All predictions are **retrospective backtests** (run_type `backtest`), not live forecasts.
**The test seasons (2024-25, 2025-26) have not been evaluated.**

## Validation: 2021-22 to 2023-24 (3,962 final regular, play-in and postseason games; 2021-10-19 to 2024-06-18)
| Model | Accuracy | Brier ↓ | Log loss ↓ |
|---|---|---|---|
| Constant 0.5 | 55.9% (all home picks) | 0.2500 | 0.6931 |
| Home-rate (Laplace) | 55.9% | 0.2466 | 0.6864 |
| Elo (K 15, H 50, carry 0.75, MOV on) | **64.3%** | **0.2207** | **0.6316** |

Elo minus home-rate Brier: −0.0260; week-block bootstrap 95% CI [−0.0312, −0.0210]
(105 weeks, seed 20260930, 2,000 resamples). **Elo is better on validation, and the interval excludes 0.**
Caveat: Elo's hyperparameters were selected on these same seasons, so this validation score is optimistic. The
held-out test period is the honest estimate.

Per season (Elo accuracy / Brier; home-rate Brier): 2021-22 64.6% / 0.2214 (0.2478), 2022-23 63.2% / 0.2279 (0.2442),
2023-24 65.2% / 0.2127 (0.2479).

Elo calibration (predicted home-win probability → observed home-win rate, games):
0–20%: 0.167 → 0.182 (44) · 20–40%: 0.322 → 0.306 (628) · 40–60%: 0.513 → 0.522 (1,597) ·
60–80%: 0.687 → 0.672 (1,404) · 80–100%: 0.840 → 0.817 (289). Roughly calibrated; the top bins are slightly overconfident.

Tuning: 150 configurations (grid extended once, before any test access; see the plan's amendments). Brier range
0.2207–0.2298. MOV on beats MOV off (0.2207 vs 0.2219 best). Best by H: 25 → 0.2214, 35 → 0.2209, 50 → 0.2207,
75 → 0.2220, 100 → 0.2253.

Market comparison: **not possible.** 0 historical games have odds (coverage 0%).

## Candidates: logistic regression and Platt calibration (report `data/reports/validation-2026-09-30T143147Z`)
Same 3,962 validation games, dataset 08fe2552c1aa4579. Design and selection rule were recorded before running
(EVALUATION_PLAN "Candidate models").

| Model | Accuracy | Brier ↓ | Log loss ↓ | Brier − Elo (95% CI) | Beats Elo? |
|---|---|---|---|---|---|
| Elo (reference) | 64.3% | 0.22068 | 0.63158 | – | – |
| Elo + Platt | 64.3% | 0.22088 | 0.63212 | +0.00020 [−0.00023, +0.00066] | No |
| Logistic | 64.5% | 0.22033 | 0.63100 | −0.00035 [−0.00171, +0.00107] | No (CI spans 0) |
| Logistic + Platt | 64.5% | 0.22076 | 0.63183 | +0.00008 [−0.00127, +0.00136] | No |

**Decision: keep Elo.** Logistic regression is a hair better on point estimate, but the difference is well within
noise. Calibration did not improve validation performance for either model (Platt maps fit on earlier seasons slightly overcorrected).
Final logistic fit (trained on 2019-20 to 2022-23, 4,957 games, standardised features): intercept 0.228,
elo_diff 0.556, mov10_diff 0.132, rest_diff 0.041, b2b_home −0.135, b2b_away +0.250, neutral −0.175. Signs are as expected.
Per-season logistic Brier: 0.2224 / 0.2279 / 0.2106 (Elo 0.2214 / 0.2279 / 0.2127).
Candidate predictions are in the report's predictions.csv; they are not recorded in the ledger (see HANDOFF).

## Limitations
- Result availability is assumed (tipoff + 4 h). The prior-day Eastern cutoff is conservative, but not an observed
  point-in-time record.
- Elo uses only results and margins: no injuries, rest, travel, or roster changes. There are no possession stats (the source lacks turnovers).
- Playoff and regular-season games are pooled, and the NBA Cup final is included as a regular-season game.
- Three seasons is a modest sample; per-season Brier varies by ±0.008.

# Final test: 2024-25 and 2025-26 (evaluated once, 2026-09-30T14:40:35Z)
Command (after Codex approval): `app.cli backtest --period test --confirm-final-test --record`.
Report `data/reports/test-2026-09-30T144035Z`; one entry in `data/eval/test_access.jsonl`. **Primary model fixed on
validation: Elo** (k 15, H 50, carry 0.75, MOV on; sealed decision `backend/app/models/frozen_decision.json`). Nothing
was retuned or reselected. Dataset version `67ea40d5f04f241e`: the test load covers every season 2019-20 to 2025-26,
the same row set as the original pre-isolation validation hash. That is expected, not a leak. 15,858 backtest
predictions were appended to the ledger (2,643 games × 6 models).

## Primary model (Elo) vs baselines: 2,643 final regular, play-in and postseason games, 2024-10-22 to 2026-06-14
| Model | Accuracy | Brier ↓ | Log loss ↓ |
|---|---|---|---|
| Constant 0.5 | 55.0% (all home picks) | 0.2500 | 0.6931 |
| Home-rate | 55.0% | 0.2475 | 0.6882 |
| **Elo (primary)** | **67.2%** | **0.2105** | **0.6088** |

Elo minus home-rate Brier: **−0.0370, 95% CI [−0.0437, −0.0302]** (week-block bootstrap, 69 weeks, seed 20260930).
Per season: 2024-25 Elo 66.2% / Brier 0.2130 / log loss 0.6133 (home-rate 0.2480); 2025-26 68.2% / 0.2081 / 0.6042
(home-rate 0.2470). Elo scored better on test than on validation (0.2207). That is season-to-season variation, not
evidence of improvement.

Calibration (predicted → observed home-win rate, games): 0–20%: 0.161 → 0.054 (56) · 20–40%: 0.319 → 0.292 (511) ·
40–60%: 0.505 → 0.498 (882) · 60–80%: 0.695 → 0.691 (930) · 80–100%: 0.850 → 0.833 (264). The middle bins track
closely. At the extremes Elo was slightly too cautious about big road favourites (lowest bin; small sample) and slightly
overconfident about big home favourites.

## Other models: descriptive only, NOT a selection decision
Brier minus Elo (95% CI): elo_platt −0.00017 [−0.00052, +0.00019]; logistic −0.00230 [−0.00355, −0.00092];
logistic_platt −0.00202 [−0.00314, −0.00073]. Logistic regression did better than Elo on these test seasons. On
validation, where the decision was made, its advantage was within noise. Under the frozen protocol Elo stays primary.
Adopting logistic regression on the strength of this result would turn the test set into a selection set. Any future
switch needs fresh, prospective evidence (e.g. 2026-27 shadow forecasts) under a newly preregistered rule.

## Limitations
- **No market comparison:** 0 historical games have odds, so nothing here shows an edge over bookmakers.
- Result availability is assumed (tipoff + 4 h). The prior-day Eastern cutoff errs conservative but is not an observed point-in-time record.
- Source: ESPN's unofficial API; terms unverified. No turnovers or offensive rebounds, so no possession-based features.
- Elo ignores injuries, rosters, travel and rest. Regular season and playoffs are pooled (NBA Cup final included).
- Elo's settings were tuned on validation, and the validation comparisons are exploratory with no multiple-comparison
  correction. The test result is the one independent check, over two seasons.

# Shadow-candidate screening: validation 2021-22 to 2023-24 (run once, 2026-10-01T00:01:21Z)
Command: `app.cli screen-candidates`. Report `data/reports/screening-2026-10-01T000121Z`. Dataset `08fe2552c1aa4579` (warm-up +
validation seasons only), box fingerprint `9ad11c5aa1590a46` (6,270 box events), frozen Elo config (k 15, H 50, carry 0.75, MOV on),
feature version `pregame-ids-v1+prior-day-et-cutoff+possession-obs-v1`. Definitions and rule were recorded beforehand
(EVALUATION_PLAN "Prospective candidates" and "Step 4 candidate definitions"). **This is screening, not evidence of improvement:**
validation was used to tune Elo and is the only data these candidates have seen. The test seasons were not loaded.

| Model (3,962 games) | Accuracy | Brier ↓ | Log loss ↓ | Brier − Elo (95% CI) | Screen |
|---|---|---|---|---|---|
| Home-rate | 55.9% | 0.24664 | 0.68640 | – | – |
| Elo (frozen reference) | 64.3% | 0.22068 | 0.63158 | – | – |
| (a) efficiency_margin | 64.4% | 0.22205 | 0.63453 | +0.00138 [−0.00010, +0.00290] | **Fail** (worse than Elo) |
| (b) logistic | 64.5% | 0.22033 | 0.63100 | −0.00035 [−0.00171, +0.00107] | **Fail** (upper bound 0.00107 ≥ 0.001) |
| (c) logistic_v2 | 64.4% | 0.21977 | 0.62968 | −0.00091 [−0.00209, +0.00025] | **Pass** |

- **Shortlist for the 2026-27 shadow season: `logistic_v2` only.** Its interval against Elo includes 0, so it is not shown to be better;
  it passed a deliberately lenient screen. The point difference is about 0.4% of Elo's Brier.
- Per season (accuracy / Brier): Elo 64.6% / 0.2214, 63.2% / 0.2279, 65.2% / 0.2127; logistic_v2 65.4% / 0.2212, 62.5% / 0.2275,
  65.4% / 0.2106; efficiency_margin 64.7% / 0.2228, 63.2% / 0.2284, 65.2% / 0.2150.
- logistic_v2 calibration (predicted → observed, games): 0.164 → 0.193 (88) · 0.318 → 0.316 (716) · 0.510 → 0.542 (1,566) ·
  0.688 → 0.679 (1,278) · 0.844 → 0.812 (314).
- efficiency_margin: last fitted σ = 13.68 points. It is too cautious at both ends (0.337 → 0.274 over 438 games; 0.830 → 0.866 over 112).
  Its constants were fixed beforehand and are not adjusted after this result.
- Elo and logistic reproduce the earlier validation report exactly (0.22068; 0.22033 with the same interval).
- **Ensemble (step 5): not built.** The precondition needs at least 2 passing candidates; only one passed. Per-game Brier-loss correlations:
  efficiency_margin|logistic 0.941, efficiency_margin|logistic_v2 0.956, logistic|logistic_v2 0.990.
- Nothing was written to the ledger (27,744 rows before and after); `test_access.jsonl` still has 1 line; `frozen_decision.json` unchanged.
- No market comparison (no historical odds).

# Research round 2: gradient-boosted trees `gbt_v1` on validation 2021-22 to 2023-24 (run once, 2026-10-01T00:59:30Z)
Command: `app.cli research-gbt`. Report `data/reports/research-gbt_v1-2026-10-01T005930Z` (copy of `report.json` in
`docs/evidence/research-gbt_v1-2026-10-01/`). Definition recorded beforehand: EVALUATION_PLAN "Research round 2". Dataset
`08fe2552c1aa4579`, box fingerprint `9ad11c5aa1590a46`, frozen Elo config, scikit-learn 1.9.1. Chronological refits at each season
start: 1,143 games (2019-20) for 2020-21; 2,314 for 2021-22; 3,637 for 2022-23; 4,957 for 2023-24. Development research only; the test
seasons were not loaded, nothing was written to the ledger, and neither sealed decision file changed.

| Model (identical 3,962 games) | Accuracy | Brier ↓ | Log loss ↓ |
|---|---|---|---|
| Elo (frozen) | 64.34% | 0.22068 | 0.63158 |
| logistic_v2 (sealed challenger) | 64.41% | 0.21977 | 0.62968 |
| **gbt_v1** | **63.86%** | **0.22342** | **0.63819** |

| gbt_v1 compared with | Brier difference (95% CI) | Accuracy difference (games only one got right) (95% CI) |
|---|---|---|
| Elo | +0.00274 [+0.00037, +0.00497] | −0.48 points (247 vs 266) [−1.58, +0.60] |
| logistic_v2 | +0.00365 [+0.00167, +0.00555] | −0.56 points (193 vs 215) [−1.54, +0.41] |

- **Result: gbt_v1 did not improve on either model.** Its Brier score is worse than Elo's and worse than logistic_v2's, and both
  intervals exclude 0. Its accuracy is lower, with intervals that include 0. It would **not** pass the step 4 screen.
- Per season (accuracy / Brier): 2021-22 63.5% / 0.2280, 2022-23 61.8% / 0.2299, 2023-24 66.3% / 0.2123. Elo: 64.6% / 0.2214,
  63.2% / 0.2279, 65.2% / 0.2127. logistic_v2: 65.4% / 0.2212, 62.5% / 0.2275, 65.4% / 0.2106.
- Calibration (predicted → observed, games): 0.164 → 0.239 (92) · 0.319 → 0.323 (790) · 0.504 → 0.543 (1,386) · 0.690 → 0.677 (1,386) ·
  0.842 → 0.799 (308). It is overconfident at both ends, more than Elo or logistic_v2.
- What this does and does not show: this one conservative configuration, on these features and this much training data, was worse. It does
  not show that tree models cannot help with other settings, more seasons of training data or new inputs. The settings were fixed
  beforehand and are not adjusted after this result; a second configuration would be a new dated experiment.
- No market comparison (no historical odds).

# Research round 3: learned stacking on validation 2021-22 to 2023-24 (run once, 2026-10-01T01:25:57Z)
Command: `app.cli research-stacking`. Report `data/reports/research-stacking-2026-10-01T012557Z` (copy of `report.json` in
`docs/evidence/research-stacking-2026-10-01/`). Definition recorded beforehand: EVALUATION_PLAN "Research round 3". Dataset
`08fe2552c1aa4579`, box fingerprint `9ad11c5aa1590a46`, frozen Elo config. Stackers were fitted at each season start on base predictions
from 2020-21 up to the previous season only: 1,171 games for 2021-22, 2,494 for 2022-23, 3,814 for 2023-24. Development research only; the
test seasons were not loaded, nothing was written to the ledger, and neither sealed file changed.

| Forecast (identical 3,962 games) | Accuracy | Brier ↓ | Log loss ↓ |
|---|---|---|---|
| Elo (frozen) | 64.34% | 0.22068 | 0.63158 |
| logistic_v2 (sealed challenger) | 64.41% | 0.21977 | 0.62968 |
| Equal average of the five base models | 64.34% | 0.21988 | 0.62980 |
| **Stacker A `stack_logit`** | **64.44%** | **0.22057** | **0.63130** |
| **Stacker B `stack_conditional`** | **64.31%** | **0.22122** | **0.63293** |

| Stacker compared with | Brier difference (95% CI) | Accuracy difference (games only one got right) (95% CI) |
|---|---|---|
| A vs logistic_v2 | +0.00080 [−0.00014, +0.00172] | +0.03 pts (68 vs 67) [−0.58, +0.61] |
| A vs Elo | −0.00011 [−0.00116, +0.00100] | +0.10 pts (130 vs 126) [−0.71, +0.88] |
| A vs equal average | +0.00068 [−0.00031, +0.00169] | +0.10 pts (96 vs 92) [−0.64, +0.85] |
| B vs logistic_v2 | +0.00145 [+0.00046, +0.00244] | −0.10 pts (54 vs 58) [−0.64, +0.44] |
| B vs Elo | +0.00054 [−0.00051, +0.00163] | −0.03 pts (129 vs 130) [−0.85, +0.74] |
| B vs equal average | +0.00134 [+0.00036, +0.00234] | −0.03 pts (85 vs 86) [−0.70, +0.62] |

- **Result: neither learned stacker improved on logistic_v2.** Stacker A is level with Elo and slightly behind logistic_v2 and the equal
  average on Brier (intervals include 0). Stacker B, with the disagreement, rest and efficiency-gap interactions, is worse than
  logistic_v2 and the equal average on Brier (intervals exclude 0). Every paired accuracy interval includes 0.
- Step 4 screen against Elo: A passes narrowly (Brier 0.22057 below Elo's 0.22068; upper bound +0.000997, limit +0.001); B fails.
  A is still behind logistic_v2, so this does not make it a better candidate than the one already in shadow.
- Fitted weights of A (standardised inputs; Elo, efficiency_margin, logistic, logistic_v2, gbt_v1): 2021-22 0.12, −0.02, 0.39, 0.35, −0.21;
  2022-23 0.22, 0.03, 0.21, 0.38, −0.17; 2023-24 0.14, 0.06, 0.18, 0.38, −0.11. The weight on gbt_v1 is negative every season and the
  split between the correlated models moves from year to year.
- Per season (accuracy / Brier): A 65.1% / 0.2214, 62.8% / 0.2283, 65.4% / 0.2120; B 65.1% / 0.2228, 62.6% / 0.2281, 65.3% / 0.2128;
  logistic_v2 65.4% / 0.2212, 62.5% / 0.2275, 65.4% / 0.2106.

## Accuracy at fixed coverage (most confident games first; the ranking uses no outcome)
| Coverage | Games | Elo | logistic_v2 | Equal average | Stacker A | Stacker B |
|---|---|---|---|---|---|---|
| 50% | 1,981 | 72.2% | 72.8% | 72.6% | 72.1% | 72.8% |
| 70% | 2,773 | 68.7% | 68.9% | 69.0% | 68.5% | 68.3% |
| 85% | 3,368 | 66.9% | 66.7% | 67.0% | 66.6% | 66.4% |
| 100% | 3,962 | 64.3% | 64.4% | 64.3% | 64.4% | 64.3% |

- Wilson 95% intervals are about ±2.0 points at 50% coverage and ±1.5 at 100%; every difference within a row is inside them.
- On stacker B's own most confident half, the five base models score 72.5% to 72.8%: the selected games are the same easy games for all.
- All five models agree on 3,177 games (80.2%), and that pick was right 67.5% of the time. Each method's most confident 80.2% scores
  67.0% to 67.7%. **Filtering by agreement and filtering by one model's confidence give the same accuracy at the same coverage.**
- These rankings were made within the evaluated seasons; an operational rule would need a threshold fixed on earlier data.

## Reading
- A higher win rate is available by skipping games: about 72–73% on the most confident half and about 67% on the most confident 80%, from
  any of these forecasts, including the live Elo. That is selection, not a better model, and the skipped games are the close ones.
- What this run shows: these two stackers, with these inputs and at most three seasons of training rows, did not beat logistic_v2. It does
  not show that no learned combination could; settings were fixed beforehand and are not adjusted after this result.
- No market comparison (no historical odds), so nothing here says whether a confident pick is priced favourably.
