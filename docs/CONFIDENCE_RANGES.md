# Win rate by range (descriptive, 2026-10-01)

Command: `python3 scripts/agreement_analysis.py data/reports/screening-2026-10-01T000121Z/predictions.csv --ranges`
(output: `docs/evidence/confidence-ranges-2026-10-01/ranges.json`). Saved development predictions only: 3,962 games, 2021-22 to 2023-24.
The ranges were fixed in the script before computing (round-number probability bands, home or away pick, part of the season). No model,
rule or threshold is changed or selected by this; these are the seasons Elo was tuned on, so it describes the past. There are no odds
in this data, so it says nothing about whether a pick is a good bet.

"Stated" is the probability the model gave its own pick. "Won" is how often that pick was right.

## The one range that matters: how confident the model is
Live Elo model:

| Pick's stated probability | Games | Share of games | Won | 95% (Wilson) | Stated (mean) |
|---|---|---|---|---|---|
| 50–55% | 819 | 20.7% | 52.6% | 49.2–56.0% | 52.5% |
| 55–60% | 778 | 19.6% | 59.9% | 56.4–63.3% | 57.5% |
| 60–65% | 664 | 16.8% | 59.8% | 56.0–63.5% | 62.3% |
| 65–70% | 607 | 15.3% | 68.7% | 64.9–72.3% | 67.4% |
| 70–75% | 438 | 11.1% | 71.7% | 67.3–75.7% | 72.4% |
| 75–80% | 323 | 8.2% | 78.0% | 73.2–82.2% | 77.3% |
| 80% or more | 333 | 8.4% | 81.7% | 77.2–85.5% | 83.9% |

| Only picks at or above | Games | Share of games | Won (Elo) | 95% (Wilson) | Won (logistic_v2, its own picks) | Its share |
|---|---|---|---|---|---|---|
| 50% (all games) | 3,962 | 100% | 64.3% | 62.8–65.8% | 64.4% | 100% |
| 55% | 3,143 | 79.3% | 67.4% | 65.7–69.0% | 67.4% | 80.0% |
| 60% | 2,365 | 59.7% | 69.9% | 68.0–71.7% | 70.3% | 60.5% |
| 65% | 1,701 | 42.9% | 73.8% | 71.6–75.8% | 73.8% | 44.6% |
| 70% | 1,094 | 27.6% | 76.6% | 74.0–79.0% | 77.4% | 29.7% |
| 75% | 656 | 16.6% | 79.9% | 76.6–82.8% | 79.3% | 18.5% |
| 80% | 333 | 8.4% | 81.7% | 77.2–85.5% | 81.1% | 10.1% |

- The win rate rises steadily with the stated probability, and the two numbers stay close: a pick the model calls 75% wins about 75–80%
  of the time. The top band is slightly overconfident (stated 83.9%, won 81.7%).
- logistic_v2 gives the same win rates at each threshold, on slightly more games at the higher thresholds.

**Already-published check on the held-out 2024-26 test (no new access; from the calibration bins in BASELINE_REPORT):** Elo's picks
stated at 80% or more won 273 of 320 (85.3%, 12.1% of games); picks stated 60–80% won 1,005 of 1,441 (69.7%, 54.5% of games); the
remaining 40–60% band (882 games) was close to even. That is consistent with the development seasons.

## Ranges that do not help
| Range (Elo) | Games | Won | 95% (Wilson) |
|---|---|---|---|
| Home team picked | 2,667 | 65.0% | 63.2–66.8% |
| Away team picked | 1,295 | 62.9% | 60.3–65.5% |
| October–November | 915 | 63.8% | 60.7–66.9% |
| December | 639 | 64.2% | 60.4–67.8% |
| January | 684 | 64.2% | 60.5–67.7% |
| February | 500 | 64.8% | 60.5–68.9% |
| March–April | 953 | 64.6% | 61.5–67.6% |
| Play-in and playoffs | 271 | 64.9% | 59.1–70.4% |

- Home and away picks, and every part of the season, sit within each other's intervals. **The early season is not measurably weaker**
  (63.8% against 64.2–64.9%); an earlier remark that October to December is the weakest stretch is not supported by this data.
- Playoff picks won 64.9% against a stated 59.8% on 271 games; the interval (59.1–70.4%) still includes the stated value.

## Reading
- A higher win rate comes from taking fewer, more confident picks. On these seasons: about 74% at a 65% threshold (43% of games), 77% at
  70% (28% of games), 80% at 75% (17% of games).
- This is a property of calibrated probabilities, not a new edge, and it holds for the live Elo as it is. Confident picks are mostly
  clear favourites, where bookmaker prices are shortest.
- A threshold for real use should be written down before the season and then checked on this season's live forecasts.
