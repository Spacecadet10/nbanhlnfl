# Combination and selection study — results (2026-10-02)

Design: docs/MODEL_SELECTION.md (committed 5ea11f5 before any run). Two runs per sport, the budgeted maximum, both on scratch
copies of the stores: run 1 at 10:46 UTC; run 2 after the self-review found the selector *simulation* did not follow the live rules
(it kept the previous rule through abstention weeks, and broke cross-market ties by the last market name instead of the first).
Run 2 reproduced every model, combination and agreement figure exactly; the only change was one rule-change count (NBA home team
total 2 → 1). A third, supplementary pass (user-approved, 2026-10-02 ~13:00 UTC, to close a reporting gap; no new candidate,
rule or threshold) added one comparison and reliability tables: it reproduced every run-2 figure exactly. The evidence files are
that pass. Evidence: `docs/evidence/combination-study-2026-10-02/` (`nba.json`, `nhl.json`, `summary.txt`).

**Status: retrospective and exploratory.** NBA 2022-2024 and NHL 2018-2021 were already used to choose the primary models. Line
markets are scored at the primary's model-only line (no historical bookmaker lines exist), so nothing here measures performance
against a bookmaker or any betting value. Intervals: paired ISO-week block bootstrap (seed 20260930, 2,000 resamples); many
comparisons were examined, so an interval that excludes 0 can be chance.

## What was tested
Per sport and market: every member alone; equal average; learned stacker (season-start refits on earlier seasons' out-of-fold
member forecasts only); strict majority; unanimous; majority/unanimous + average ≥ t; each pair with the primary; confidence-only
filters of the primary and of the average at matched coverage; a weekly selector simulation (evidence starting empty).

## Results (log loss: lower is better; Δ = method − reference, 95% interval)

| Sport · market | Games | Best single (log loss) | Notable paired result | Agreement beyond confidence? |
|---|---|---|---|---|
| NBA winner | 3,962 | logistic_v2 0.6297 (Elo 0.6316) | average − Elo −0.0018 [−0.0039, +0.0002]; stacker − Elo +0.0002 [−0.0025, +0.0030] | No: vs the average's own most-confident games, every rule is within ±0.004 |
| NBA spread | 3,962 (stacker 2,639) | ridge_points 0.6869 (primary 0.6931) | ridge − primary −0.0063 [−0.0102, −0.0024]; stacker − primary −0.0070 [−0.0101, −0.0038] | No vs the average; the primary is ~50/50 at its own line by construction |
| NBA total | 3,962 | points_normal 0.6930 | stacker − primary −0.0022 [−0.0049, +0.0003]; new members alone are worse (0.704, 0.707) | No |
| NBA home / away team total | 3,962 | points_normal 0.6927 / 0.6931 | away: stacker − primary −0.0018 [−0.0034, −0.0002]; home: n.s. | No |
| NHL winner | 4,877 | hockey_logistic 0.6739 (Elo 0.6743) | all combinations within ±0.0006 of Elo, intervals include 0 | No (unanimous −0.008 [−0.014, −0.001] vs Elo's confidence) |
| NHL regulation 3-way | 4,877 | goals_draw 1.0523 (primary 1.0629) | goals_draw − primary −0.0106 [−0.0147, −0.0068]; stacker − primary −0.0122 [−0.0170, −0.0074] | Picks barely change (draws are almost never the top outcome); the gain is in probabilities |
| NHL puck line | 4,877 | goals_glm 0.6827 | all within ±0.0006, intervals include 0 | No |
| NHL total | 4,877 | goals_poisson / goals_draw 0.6909 | average − primary +0.0014 [+0.0001, +0.0026] (worse); goals_glm worse alone (0.7018) | No |
| NHL home / away team total | 4,877 | goals_poisson 0.6805 / goals_draw 0.6854 | combinations within ±0.0016, intervals include 0 | No (some rules slightly worse than the primary's confidence) |

Simulated weekly selector (picks, accuracy vs the primary's most-confident games over the same span at the same count):
NBA winner 821 picks 77.3% vs 78.3% (−0.010 [−0.033, +0.016]); NBA spread 860 picks 60.1% vs 52.8% (the primary ranking is
meaningless at its own line); NHL winner 1,014 picks 65.2% vs 64.8% (+0.004, interval includes 0); NHL puck line 2,281 picks
60.2% vs 59.5%; NHL team totals: home 59.1% vs 59.8%, away 57.5% vs 59.0% (both intervals include 0). Rule changes were rare
(0-6 per market over 141/198 weeks), i.e. the hysteresis and minimum-evidence rules did not chase short streaks.

## Comparison with the strongest single model (added in the supplementary pass)
The strongest single member for each evaluated season is chosen by log loss on **earlier** out-of-sample seasons only (never on
the season it is compared in). Where no earlier season exists (NBA line markets in 2022, as the new members first become
available then) there is no choice and those games are left out of this comparison. Choices: NBA winner logistic (2022) then
logistic_v2; NBA spread ridge_points; NBA totals and team totals points_normal; NHL winner Elo; NHL 3-way, total goals_draw;
NHL puck line goals_glm; NHL team totals goals_glm / goals_poisson / goals_draw by season.
- Agreement vs that model's own most-confident games at the same count: mostly within ±0.01 with intervals including 0; a few are
  negative (NHL puck line and home team total, majority/unanimous + average ≥ 0.55: about −0.01, intervals just below 0).
- Intervals above 0 appear only where the chosen model is the primary at its own model-only line (NBA away team total: majority
  +0.027 [+0.009, +0.046], majority + average ≥ 0.55 +0.056 [+0.018, +0.097]). That model is ~50/50 there by construction, so its
  confidence ranking is close to random. Those rows show that rather than value added by agreement. NHL regulation 3-way:
  majority + average ≥ 0.45 +0.008 [+0.001, +0.014] against goals_draw; against the average's own confidence it is +0.000.
- Simulated selector vs the strongest earlier-chosen model at the same count: every interval includes 0 (e.g. NBA winner
  −0.022 [−0.044, +0.001], NHL winner +0.004 [−0.013, +0.021]).
- Many comparisons were examined; isolated intervals excluding 0 are expected by chance.

## Reliability tables
`docs/evidence/.../{nba,nhl}.json` → `methods.<model>.calibration` (5 bins of the picked side's probability vs observed win rate),
also shown in the app under Performance → Retrospective study → Reliability, and for prospective cohorts under Forward research →
Reliability. These are tables only; **no calibration was fitted**. A fitted calibration would need chronological training, a new
model version and its own evaluation.

## Answers to the questions asked
- **Does agreement add anything beyond selecting easier, higher-confidence games?** Not in this data. Against the equal
  average's own most-confident games at the same coverage, no agreement rule in any market had an interval excluding 0 in its
  favour; against the strongest earlier-chosen single model, the only favourable intervals come from a model whose ranking is
  degenerate at its own line (above). Against the primary's confidence it sometimes did (NBA winner majority + average ≥ 60%: +0.011 [+0.002, +0.019]), which
  means the *average* ranks games slightly better than Elo alone, not that votes add information.
- **Conditional disagreement:** NBA winner members disagree with Elo on 8-13% of games and, when they do, are right about as
  often as Elo (e.g. gbt_v1 247 vs Elo 266 of 513). Per-game loss correlation with Elo 0.90-0.97. NHL goals_glm disagrees with
  the primary on 36% of totals picks and is right less often (805 vs 935).
- **Where a different model or combination looked better:** NBA spread (ridge_points / stacker) and NHL regulation 3-way
  (draw-inflated distribution / stacker), both in probability quality. Both are exploratory: they become evidence only through
  the prospective record now being collected.
- **Where nothing helped:** NBA and NHL winner, NHL puck line, totals and team totals.

## What runs live from this
Nothing replaces a primary model. Every market's candidates and combinations are recorded prospectively as research cohorts, and the
weekly selector starts with **empty evidence** (it does not inherit these retrospective results). It needs ≥ 100 settled picks,
≥ 10% coverage and ≥ 3 weeks for a rule before it can recommend anything.
