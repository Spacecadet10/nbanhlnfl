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
