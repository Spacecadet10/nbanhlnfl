# NBA availability candidate `avail_v1`: development result (2026-10-02)

Rule: docs/EVALUATION_PLAN.md "NBA availability candidate `avail_v1`" (recorded before any data; commit 145361c) and its 2026-10-02 amendment.
Report: `data/reports/nba-availability-2026-10-02T015402Z/report.json` (the earlier run 2026-10-02T014517Z is superseded; see the amendment).
Data gates: docs/AVAILABILITY_AUDIT.md "Gates 1-3 completed". **Exploratory development evidence**: 2021-22 … 2023-24 already selected Elo and
screened `logistic_v2`. The spent 2024-26 test seasons were not downloaded or read.

**Setup.** Forecast at 17:45 ET on game day from the 5:30 PM ET official injury report; all three models use that same cutoff and the same
results and box scores. 3,667 games from 18:00 ET (295 earlier games excluded); every scored game had a usable report.

| Model | W–L | Accuracy | Brier | Log loss |
|---|---|---|---|---|
| Elo (frozen) | 2,359–1,308 | 64.3% | 0.2209 | 0.6320 |
| logistic_v2 (sealed shadow) | 2,365–1,302 | 64.5% | 0.2199 | 0.6300 |
| **avail_v1** (logistic_v2 + injury report) | 2,419–1,248 | **66.0%** | **0.2177** | **0.6251** |

Paired, same games (week-block bootstrap, 95%):
| | Brier | Log loss | Accuracy (points) |
|---|---|---|---|
| avail_v1 − logistic_v2 | −0.0022 [−0.0040, −0.0004] | −0.0049 [−0.0088, −0.0009] | +1.5 [+0.4, +2.6] (197 vs 143 games only one right) |
| avail_v1 − Elo | −0.0032 [−0.0055, −0.0009] | −0.0069 [−0.0120, −0.0018] | +1.6 [+0.5, +2.9] |

By season (Brier, avail_v1 / logistic_v2 / Elo): 2021-22 0.2191 / 0.2225 / 0.2225; 2022-23 0.2217 / 0.2259 / 0.2263; 2023-24 0.2124 / 0.2114 / 0.2139 —
better in two seasons, slightly worse than logistic_v2 in the third.

**What carried the signal.** Last fit: the absence of all listed players weighted by expected minutes (0.22 per scaled unit) matters more
than new absences alone (0.05). The model uses the report's content, not just who was already missing.

**Combinations (preregistered set).**
- Average of Elo and avail_v1: Brier 0.2181, better than Elo (−0.0028 [−0.0040, −0.0016]) but no better than avail_v1 alone (+0.0004 [−0.0007, +0.0015]).
- Elo and avail_v1 agree on 87.6% of games: 2,162–1,051, 67.3%. That beats Elo's own most confident games at the same coverage (66.4%;
  +0.9 [+0.1, +1.7]) but not avail_v1's (67.2%; +0.1 [−0.6, +0.8]). **The gain comes from the new input, not from agreement as such.**
- avail_v1's most confident 50% / 25% of games: 72.5% / 77.9%, Elo's: 72.2% / 76.8% (differences +0.3 [−1.0, +1.5] and +1.1 [−0.7, +3.1]: not established).
- When the two disagree (454 games), avail_v1 was right 257 times and Elo 197.

**Diagnostics.** Per-game Brier-loss correlation with Elo 0.915, with logistic_v2 0.948. Highly correlated, yet measurably better: correlation
alone would have been the wrong gate.

**Checks.** An independent leakage review reproduced the first run exactly, re-matched every player using only earlier appearances (0 scored
features changed), confirmed every report's Last-Modified within 5 minutes of its 5:30 PM header, and found one training-side leak (early
games using a later report), fixed before this run.

**What this does and does not show.** Injury-report information at 5:45 PM improved forecasts on these development seasons by about 1.5
points of accuracy, with intervals that exclude zero. It is one look at reused seasons with a new feature definition; the effect size is
uncertain (lower bounds near zero), and nothing here compares with bookmakers, whose lines already move on injury news. Independent
evidence can only come from forward forecasts (registered separately in EVALUATION_PLAN; not recording yet).
