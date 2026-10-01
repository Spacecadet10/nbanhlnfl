# NHL baseline report

Plan: docs/EVALUATION_PLAN.md "NHL evaluation plan" (recorded before any NHL data was imported) and its 2026-09-30 grid
amendment (development seasons only). Data: NHL stats REST season game lists, 2015-16 … 2025-26, final regular-season and
playoff games, night-before cutoff (00:00 ET), historical result availability assumed at tipoff + 4 h. No odds: no market
comparison is possible for any NHL figure below.

Reports (local, not committed): `data/reports/nhl-validation-2026-10-01T032305Z`, `data/reports/nhl-test-2026-10-01T032343Z`.
Sealed decision: `backend/app/models/nhl_frozen_decision.json` (sha256 d6f178ca…). Test access log: `data/eval/test_access_nhl.jsonl`
(one entry, 2026-10-01T03:23:43Z).

## Development tuning (2017-18 … 2020-21, 4,877 scored games)
- `hockey_elo`: K 4, H 30, carry 0.75, goal-margin multiplier on (Brier 0.24072; home rate 0.24841).
- `goals_poisson`: η 0.003, carry 0.75 (regulation-score NLL 3.8057; η = 0 reference not tuned).
- Both first runs hit the edge of their grids; the grids were extended downwards before validation (amendment).

## Validation (2021-22 … 2023-24, 4,201 games), run once
| Model | Accuracy | Brier | Log loss |
|---|---|---|---|
| constant 0.5 | 53.3% | 0.2500 | 0.6931 |
| home rate | 53.3% | 0.2490 | 0.6912 |
| **hockey_elo** (selected) | 60.4% | 0.2334 | 0.6589 |
| goals_poisson | 60.5% | 0.2358 | 0.6643 |

Selection rule (goals only if the paired upper bound of goals − Elo Brier < 0): goals − Elo = +0.0025, so **Elo** is the winner
model; `goals_poisson` is the market model (the only distributional candidate).

## Final test (2024-25, 2025-26; 2,792 games), run once with the sealed choice
| Model | Accuracy | Brier | Log loss | Brier 2024-25 / 2025-26 |
|---|---|---|---|---|
| home rate | 54.2% | 0.2482 | 0.6896 | 0.2464 / 0.2501 |
| **hockey_elo** | 56.2% | 0.2432 | 0.6792 | 0.2375 / 0.2489 |
| goals_poisson | 56.4% | 0.2430 | 0.6790 | 0.2373 / 0.2488 |

- Elo − home rate Brier −0.0051, 95% CI [−0.0086, −0.0014] (week-block bootstrap). Goals − Elo −0.0001 [−0.0015, 0.0012]:
  no difference.
- 2025-26 was much harder than 2024-25: both models were close to the home-rate baseline. Expect NHL winner probabilities
  to be modest (most picks 50-66%).

### Markets (goals_poisson vs the no-strength `league_poisson`, log loss; negative = better)
| Market | Test log loss | vs league [95% CI] | Observed a / mid / b | Predicted a / mid / b |
|---|---|---|---|---|
| Regulation 3-way | 1.0712 | [−0.018, −0.005] | 42.3 / 22.9 / 34.8 % | 45.4 / 16.4 / 38.2 % |
| Puck line home −1.5 | 0.6269 | [−0.016, −0.004] | 32.7 / – / 67.3 % | 29.9 / – / 70.2 % |
| Puck line home +1.5 | 0.5765 | [−0.014, −0.003] | 73.3 / – / 26.7 % | 76.3 / – / 23.8 % |
| Total 5.5 | 0.6879 | [−0.003, +0.004] | 55.7 / – / 44.3 % | 54.1 / – / 45.9 % |
| Total 6.5 | 0.6887 | [−0.002, +0.003] | 44.6 / – / 55.4 % | 43.1 / – / 56.9 % |

## What this means
- Winner: a modest, real improvement over home rate on unseen seasons; nothing about beating bookmakers is established.
- Puck line and regulation 3-way carry information beyond league averages; **totals do not** (no demonstrated skill).
- **Known miscalibration:** independent Poisson regulation goals under-predict regulation draws (≈16% predicted vs ≈23%
  observed) and two-goal home wins. The app labels `reg3` and puck-line probabilities with this limitation. A dependence or
  draw-inflation candidate is a new research round (preregistered; the validation seasons are now spent for it, so its only
  clean evidence would be prospective).
- No goalie, injury, travel or shot-quality inputs are used (no timestamped pregame source; docs/DATA_SOURCES.md).

## Paired uncertainty of the test result (computed 2026-10-01 from the saved test predictions; reporting only)
Elo − home rate on the same 2,792 test games: accuracy +1.9 points, 95% week-block interval [+0.3, +3.6] (Elo alone right 478 times, home
rate alone right 424); Brier −0.0051 [−0.0086, −0.0014]. The improvement is supported by this one test but small and uncertain; it is not
evidence of beating bookmakers.

## Agreement research (development seasons 2017-18 … 2020-21; plan recorded in EVALUATION_PLAN before computing)
Report: `data/reports/nhl-agreement-<timestamp>/report.json`; dataset 5da8e01fc710d6a2; 4,877 games, 0 abstentions. Both models use
their sealed configurations, which were tuned on these seasons, so absolute figures are optimistic; the comparisons share that bias.
Agreement selects games; a selected game's probability is defined as the average of the two models.

| Method (all games) | W–L | Accuracy | Brier | Log loss |
|---|---|---|---|---|
| Elo | 2,814–2,063 | 57.7% | 0.2407 | 0.6743 |
| Goals model | 2,777–2,100 | 56.9% | 0.2425 | 0.6781 |
| Average of both | 2,808–2,069 | 57.6% | 0.2413 | 0.6754 |

Average − Elo Brier +0.0006 [+0.0001, +0.0010] (worse); average − goals −0.0013 [−0.0018, −0.0007]; average − Elo accuracy −0.1 points
[−0.7, +0.5].

The two models disagree on 603 games (12.4%). There Elo's pick won 53.1% and the goals model's 46.9% (home team won 45.9%).

| Subset (probability = average) | Games (coverage) | W–L | Accuracy [Wilson 95%] | Brier | Elo's own filter at the same coverage | Subset − Elo filter, accuracy [95%] |
|---|---|---|---|---|---|---|
| Both agree | 4,274 (87.6%) | 2,494–1,780 | 58.4% [56.9, 59.8] | 0.2402 | 58.7%, Brier 0.2394 | −0.3 [−0.8, +0.3] |
| Agree, average ≥ 55% | 2,905 (59.6%) | 1,786–1,119 | 61.5% [59.7, 63.2] | 0.2356 | 61.6%, 0.2349 | −0.1 [−0.8, +0.7] |
| Agree, average ≥ 60% | 1,247 (25.6%) | 803–444 | 64.4% [61.7, 67.0] | 0.2281 | 65.0%, 0.2258 | −0.6 [−1.9, +0.6] |
| Agree, average ≥ 65% | 403 (8.3%) | 282–121 | 70.0% [65.3, 74.2] | 0.2103 | 70.2%, 0.2084 | −0.2 [−3.2, +2.5] |

On every subset Elo alone has a lower Brier than the goals model (e.g. 0.2397 vs 0.2413 when both agree). Against the goals model's own
confidence filter some agreement subsets look better (≥ 60%: +1.8 [+0.2, +3.4]), but that only reflects the goals model being the weaker
of the two.

**Conclusion under the preregistered rule: nothing is promising.** Agreement picks games with high confidence, and Elo's own confidence filter
already selects games at least as well at every coverage tested. Averaging adds the weaker model's noise (Brier worse than Elo). Where the
models disagree, Elo is right more often. No forward experiment was frozen; the live NHL model is unchanged. This matches the earlier NBA
finding (docs/AGREEMENT_ANALYSIS.md). It does not rule out a different second model (for example one with goalie or shot-quality inputs)
adding information; that would be a new, separately preregistered candidate.
