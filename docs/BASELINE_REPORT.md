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
