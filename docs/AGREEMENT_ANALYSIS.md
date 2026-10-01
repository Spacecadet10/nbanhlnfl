# When all four models agree (descriptive analysis, 2026-10-01)

Script: `python3 scripts/agreement_analysis.py data/reports/screening-2026-10-01T000121Z/predictions.csv`
(output saved to `data/reports/agreement-2026-10-01/agreement.json`).

**Scope and limits.** This reads the saved development predictions only: 3,962 games from 2021-22 to 2023-24, the seasons used to
tune Elo and screen the candidates. It does not touch the 2024-26 test seasons, changes no model, and is **not part of model
promotion**. It describes the past; it is not a forecast of future accuracy and says nothing about betting profit (there are no
historical odds). Any "only bet when they agree" strategy would need its own definition fixed in advance and a forward record.

**Voting models:** Elo, efficiency_margin, logistic, logistic_v2. The constant and home-rate baselines do not vote. A model at exactly
50% would abstain; that never happened (0 games).

## Result
| Subset | Games | Share of all games | Correct picks | Win rate | 95% interval (Wilson) | 95% interval (week-block bootstrap) |
|---|---|---|---|---|---|---|
| All four pick the same team | 3,388 | 85.5% | 2,259 | **66.7%** | 65.1% – 68.2% | 64.9% – 68.5% |
| At least one model disagrees | 574 | 14.5% | see below | 50.5% – 51.9% by model | – | – |
| Any abstention | 0 | 0% | – | – | – | – |

- Per season, unanimous games: 2021-22 764/1,143 (66.8%), 2022-23 716/1,092 (65.6%), 2023-24 779/1,153 (67.6%).
- Unanimous picks were the home team 2,360 times and the away team 1,028 times.
- The models' own average probability for the unanimous pick was 65.7%, against 66.7% observed. Agreement adds no information beyond
  what the probabilities already say.

## Each model on identical subsets
| Model | All 3,962 games | Unanimous 3,388 (accuracy / Brier) | Disagreement 574 (wins, accuracy / Brier) |
|---|---|---|---|
| Elo | 2,549 (64.3%) | 66.7% / 0.2157 | 290, 50.5% / 0.2500 |
| efficiency_margin | 2,550 (64.4%) | 66.7% / 0.2172 | 291, 50.7% / 0.2507 |
| logistic | 2,557 (64.5%) | 66.7% / 0.2153 | 298, 51.9% / 0.2502 |
| logistic_v2 | 2,552 (64.4%) | 66.7% / 0.2148 | 293, 51.0% / 0.2490 |

On the unanimous subset every model has the same accuracy by definition (they make the same pick). The home team won 50.7% of the
disagreement games.

## Reading it
- The 66.7% comes from leaving out the 14.5% of games that are close to coin flips, where every model is about 50–52%. It is about
  2.3 points above each model's accuracy on all games (64.3–64.5%), not a new source of skill.
- The four models are not independent: three of them use the Elo rating, and their errors are 94–99% correlated
  (BASELINE_REPORT). Four agreeing votes are much closer to one opinion than to four.
- Limits: development seasons only (Elo was tuned on them); one sample of 3,388 games; no market comparison. Unanimous picks are
  mostly clear favourites, which is where bookmaker prices are shortest, so a higher win rate does not imply a profitable bet.

# Combinations: pairwise agreement, majority vote, averaged probabilities (descriptive, 2026-10-01)

Command: `backend/.venv/bin/python scripts/agreement_analysis.py data/reports/screening-2026-10-01T000121Z/predictions.csv --combinations`
(output saved to `data/reports/agreement-2026-10-01/combinations.json`). Same 3,962 development games, same limits as above.
The set was fixed in the script before anything was computed. No weights were fitted and nothing was selected. None of these
combinations is part of the sealed shadow experiment, and none changes the live Elo. Fourteen intervals are reported without
adjustment, so one or two looking notable is expected by chance.

## Pairwise agreement (both models pick the same team)
| Pair | Games agreeing | Share | Win rate | 95% (Wilson) | 95% (week-block) | Games disagreeing (wins by each model) |
|---|---|---|---|---|---|---|
| Elo + efficiency_margin | 3,621 | 91.4% | 65.7% (2,379) | 64.1–67.2% | 64.1–67.4% | 341 (170 / 171) |
| Elo + logistic | 3,658 | 92.3% | 65.6% (2,401) | 64.1–67.2% | 63.9–67.4% | 304 (148 / 156) |
| Elo + logistic_v2 | 3,655 | 92.3% | 65.6% (2,397) | 64.0–67.1% | 63.9–67.4% | 307 (152 / 155) |
| efficiency_margin + logistic | 3,553 | 89.7% | 66.1% (2,349) | 64.5–67.7% | 64.5–67.8% | 409 (201 / 208) |
| efficiency_margin + logistic_v2 | 3,602 | 90.9% | 65.8% (2,371) | 64.3–67.4% | 64.3–67.4% | 360 (179 / 181) |
| logistic + logistic_v2 | 3,801 | 95.9% | 65.1% (2,474) | 63.6–66.6% | 63.4–66.8% | 161 (83 / 78) |

Every pair lands between 65.1% and 66.1%, below the four-way 66.7%, with overlapping intervals. When two models disagree, each is right
about half the time.

## Majority vote (at least 3 of 4)
| Vote | Games | Share | Majority pick correct | 95% (Wilson) | 95% (week-block) |
|---|---|---|---|---|---|
| Any majority (4-0 or 3-1) | 3,802 | 96.0% | 65.2% (2,478) | 63.6–66.7% | 63.6–66.9% |
| 4-0 | 3,388 | 85.5% | 66.7% (2,259) | 65.1–68.2% | 64.9–68.5% |
| 3-1 | 414 | 10.4% | 52.9% (219) | 48.1–57.7% | 47.7–58.2% |
| 2-2 (no majority) | 160 | 4.0% | – (home team won 51.3%) | – | – |

In 3-1 games the majority was right 52.9% of the time, and the interval includes 50%. The lone dissenter was efficiency_margin 188
times (right 92), Elo 109 (53), logistic 70 (31) and logistic_v2 47 (19).

## Averaged probabilities (equal weights), scored on all 3,962 games
| Forecast | Accuracy | Brier ↓ | Log loss ↓ | Brier − Elo (95% CI) | Brier − logistic_v2 (95% CI) |
|---|---|---|---|---|---|
| Elo alone | 64.3% | 0.22068 | 0.63158 | – | – |
| efficiency_margin alone | 64.4% | 0.22205 | 0.63453 | – | – |
| logistic alone | 64.5% | 0.22033 | 0.63100 | – | – |
| logistic_v2 alone (the sealed challenger) | 64.4% | 0.21977 | 0.62968 | – | – |
| Mean of all four | 64.6% | 0.21988 | 0.62979 | −0.00080 [−0.00154, −0.00005] | +0.00011 [−0.00052, +0.00077] |
| Mean of Elo + efficiency_margin | 64.4% | 0.22068 | 0.63149 | +0.00000 [−0.00074, +0.00077] | +0.00091 [−0.00037, +0.00220] |
| Mean of Elo + logistic | 64.7% | 0.22009 | 0.63036 | −0.00059 [−0.00126, +0.00012] | +0.00032 [−0.00033, +0.00101] |
| Mean of Elo + logistic_v2 | 64.8% | 0.21980 | 0.62968 | −0.00088 [−0.00149, −0.00031] | +0.00002 [−0.00056, +0.00063] |
| Mean of efficiency_margin + logistic | 64.6% | 0.22031 | 0.63074 | −0.00037 [−0.00138, +0.00069] | +0.00054 [−0.00033, +0.00148] |
| Mean of efficiency_margin + logistic_v2 | 64.5% | 0.22014 | 0.63033 | −0.00053 [−0.00153, +0.00048] | +0.00037 [−0.00046, +0.00126] |
| Mean of logistic + logistic_v2 | 64.5% | 0.21992 | 0.63004 | −0.00076 [−0.00199, +0.00051] | +0.00015 [−0.00020, +0.00051] |

## Paired accuracy on the same games (added 2026-10-01 after Codex review)
Difference in winner accuracy against a reference model, counting only the games where the two disagree on the pick, with a week-block
bootstrap interval (seed 20260930, 2,000 resamples). "83 vs 67" means the average alone was right in 83 games and the reference alone in 67.

| Forecast | vs Elo | vs logistic_v2 |
|---|---|---|
| Mean of all four | +0.25 pts (103 vs 93) [−0.39, +0.88] | +0.18 pts (74 vs 67) [−0.33, +0.70] |
| Mean of Elo + efficiency_margin | +0.03 pts (75 vs 74) [−0.56, +0.63] | −0.05 pts (146 vs 148) [−0.92, +0.81] |
| Mean of Elo + logistic | +0.33 pts (89 vs 76) [−0.30, +0.95] | +0.25 pts (94 vs 84) [−0.27, +0.82] |
| Mean of Elo + logistic_v2 | +0.48 pts (88 vs 69) [−0.05, +1.01] | +0.40 pts (83 vs 67) [−0.15, +0.99] |
| Mean of efficiency_margin + logistic | +0.23 pts (133 vs 124) [−0.50, +0.99] | +0.15 pts (84 vs 78) [−0.40, +0.70] |
| Mean of efficiency_margin + logistic_v2 | +0.20 pts (139 vs 131) [−0.58, +0.97] | +0.13 pts (79 vs 74) [−0.47, +0.73] |
| Mean of logistic + logistic_v2 | +0.20 pts (157 vs 149) [−0.57, +0.97] | +0.13 pts (43 vs 38) [−0.36, +0.60] |
| logistic_v2 alone | +0.08 pts (155 vs 152) [−0.67, +0.83] | – |

Every paired accuracy interval includes 0. The largest observed difference is Elo + logistic_v2: 16 more correct picks than logistic_v2
alone out of 3,962 games.

## Reading it
- **By Brier score, no average beats the sealed challenger:** each is equal to or slightly worse than logistic_v2 alone, and every interval
  against it includes 0. The best average (Elo + logistic_v2, 0.21980) is level with logistic_v2 (0.21977).
- **By accuracy, Elo + logistic_v2 had the highest observed hit rate (64.8% against 64.4%), but that is not an established improvement:**
  the paired difference is +0.40 points with an interval of −0.15 to +0.99.
- Two averages have intervals against Elo that exclude 0 (all four; Elo + logistic_v2), by under 0.001 Brier. That is the same small
  edge logistic_v2 already showed, on the seasons used to tune and screen, and without adjustment for 14 comparisons.
- Voting adds nothing over the probabilities: agreement mostly marks the games that were already clear, and 3-1 or 2-2 games are close
  to coin flips whichever side is taken.
- The models' errors are highly correlated, which makes further averaging of these four less promising. It does not test whether a
  different model class (for example a tree model with interactions) or new information (for example player availability) would help;
  both remain open hypotheses.

# Five-model agreement (descriptive, 2026-10-01)

Command: `python3 scripts/agreement_analysis.py data/reports/research-stacking-2026-10-01T012557Z/predictions.csv --five`
(output copied to `docs/evidence/research-stacking-2026-10-01/five_model_votes.json`). Voting models: Elo, efficiency_margin, logistic,
logistic_v2, gbt_v1. Same 3,962 development games and the same limits as above: it describes the past on the seasons used for
tuning, changes no model and is not part of promotion. The cuts were fixed in the script before computing.

| Vote | Games | Share | Majority pick correct | 95% (Wilson) |
|---|---|---|---|---|
| 5-0 | 3,177 | 80.2% | 67.5% (2,145) | 65.9–69.1% |
| 4-1 | 496 | 12.5% | 52.8% (262) | 48.4–57.2% |
| 3-2 | 289 | 7.3% | 52.9% (153) | 47.2–58.6% |

- By season, 5-0 games: 2021-22 67.7% (1,034), 2022-23 65.9% (1,040), 2023-24 68.9% (1,103). Week-block interval for all 5-0 games: 65.7–69.3%.
- On the 785 games without full agreement, each model alone is right 49.0% to 52.5% of the time.
- **Agreement combined with confidence, at 50% coverage (1,981 games):** logistic_v2's most confident half is right 72.8% (1,442);
  taking only unanimous games and then logistic_v2's most confident gives 72.7% (1,441). The two selections share 1,956 of 1,981 games.

Reading: with five models, full agreement covers 80% of games at 67.5%. When even one model dissents, the majority is right about 53% of
the time, and the interval includes 50%. Requiring agreement on top of confidence selects almost the same games as confidence alone and
gives the same accuracy.
