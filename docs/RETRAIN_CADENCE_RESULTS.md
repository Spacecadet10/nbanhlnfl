# Retraining-cadence comparison — results (2026-10-02, exploratory)

Design and reading rule: docs/MODEL_SELECTION.md §9 (602aeab, before the run; amendment ae37db0, before the run).
Code at ae37db0. Command: `app.cli --sport {nba,nhl} retrain-cadence`, run on `.backup` copies of both stores.
Reports: `data/reports/retrain-cadence-{nba,nhl}-20261002/report.json` (NBA dataset 08fe2552c1aa4579, box 9ad11c5aa1590a46;
NHL dataset 5da8e01fc710d6a2). Run 13:02–13:12 UTC (NBA 13:02–13:09, NHL 13:09–13:12). These seasons were already used for model selection, so this is
**exploratory**, not evidence of prospective improvement. Nothing was changed live.

Checks: season cadence reproduced every unwrapped member's per-game losses exactly (all 9 members); 372 backend tests pass.

Differences are cadence − season, per game, on games every cadence forecast (negative = better). Intervals: ISO-week block
bootstrap, 20,000 resamples; "Bonf." = Bonferroni over the 18 primary comparisons.

| Member | Primary metric (games) | Monthly − season [Bonf.] | Weekly − season [Bonf.] | Rule met? |
|---|---|---|---|---|
| NBA `logistic` | winner Brier (3,962) | −0.00035 [−0.00083, +0.00009] | −0.00034 [−0.00083, +0.00014] | no / no |
| NBA `logistic_v2` | winner Brier (3,962) | −0.00024 [−0.00067, +0.00016] | −0.00026 [−0.00071, +0.00017] | no / no |
| NBA `gbt_v1` | winner Brier (3,962) | −0.00062 [−0.00276, +0.00152] | +0.00064 [−0.00162, +0.00289] | no / no |
| NBA `ridge_points` | margin log score (3,962) | −0.0017 [−0.0035, +0.0000] | −0.0017 [−0.0035, +0.0001] | no / no |
| NBA `gbt_points` | margin log score (3,962) | −0.0060 [−0.0113, −0.0006] | −0.0080 [−0.0146, −0.0017] | **yes / yes** |
| NHL `hockey_logistic` | winner Brier (4,877) | +0.00003 [−0.00048, +0.00053] | +0.00006 [−0.00049, +0.00065] | no / no |
| NHL `hockey_gbt` | winner Brier (4,877) | −0.00047 [−0.00179, +0.00077] | −0.00052 [−0.00192, +0.00082] | no / no |
| NHL `goals_glm` | reg. 3-way log loss (4,877) | +0.0004 [−0.0002, +0.0010] | +0.0005 [−0.0001, +0.0011] | no / no |
| NHL `goals_draw` | reg. 3-way log loss (4,877) | +0.0001 [−0.0001, +0.0004] | +0.0001 [−0.0001, +0.0003] | no / no |

Secondary (NBA total log score, weekly − season): `ridge_points` −0.0058 [Bonf. −0.0098, −0.0023], `gbt_points` −0.0101
[−0.0176, −0.0027]; better in every season for both. Per-season figures are in the reports.

## Reading
- **NHL:** no benefit from refitting more often; the goals models are slightly worse (within noise).
- **NBA winner models:** small, consistent-looking Brier gains in 2022–2023 (about −0.0003, roughly 0.15% of Brier) that vanish
  in 2024; not distinguishable from zero after the multiplicity correction.
- **NBA score models:** the clearest effect. `gbt_points` meets the pre-set rule for both cadences, and both score models'
  total forecasts improve. Sizes are small (≈0.006–0.010 nats per game). Even with weekly refits `gbt_points` (margin 4.0532)
  stays worse than `ridge_points` at season cadence (4.0468), so this does not create a new best member.
- Within-season refits also make the score members ready earlier in a season (paired comparisons exclude those extra games).
- Not tested: other cadences, other members, any effect on market picks or selector decisions.

## Next (not done; needs approval)
Per §9 the only permitted follow-up is prospective: record `gbt_points@weekly` and `ridge_points@weekly` (totals) as separate
shadow members judged on future NBA games from 2026-10-20, with live forecasts unchanged.
