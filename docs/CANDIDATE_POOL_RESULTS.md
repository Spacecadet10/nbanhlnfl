# Candidate pool `pool-v1` — results (2026-10-02, exploratory)

Design: docs/CANDIDATE_POOL.md (committed 17:17 UTC as 6ec4978, rebased as dbe2797, before any scored run; dry runs used
warm-up seasons only). Repository research: docs/REPO_REVIEW.md. Compact evidence: docs/evidence/candidate-pool/
`{nba,nhl}-{screen,assess}.json`; full reports, out-of-fold tables and the experiment log stayed in the study data directory
(`.backup` copies of both stores taken 16:56 UTC; `reports/pool-*/`, `research/candidates/oof-*.json.gz`,
`research/candidates/experiments.jsonl`) and are copied to `data/` at integration. Stage-2 accesses logged in
`eval/pool_assess_access_{sport}.jsonl`.

**What kind of evidence this is.** Stage 1 used spent development seasons. Stage 2 used seasons that had never been used to
choose this shortlist (NBA 2025–26; NHL 2022–26), but they were used once before for the primaries' validation/test, so
this is still retrospective and exploratory. The only clean test is prospective recording, which is now built.

**Budget used:** 19 configurations, one setting each, no hyperparameter search. Wall clock (machine shared with other
sessions): NHL screen 5.4 min, NHL assess 11.6 min, NBA screen 18.6 min (17.6 min rebuilding the existing members), NBA
assess 15.9 min — within the declared 45 / 60 min.
Candidate fitting itself took ≤ 42 s per configuration.

## 1. Inspected, adopted, rejected
| Item | Verdict | Evidence |
|---|---|---|
| NGBoost (stanfordmlgroup/ngboost 0.5.11) | adopted as optional learner, **no value found** | seeded base tree + chronological early stopping (both defaults are unsafe: unseeded base, random validation split). On real NBA data its per-game spreads made forecasts worse (total +0.012 log loss in stage 2) |
| optuna 5.0.0 | not used | every setting declared, nothing searched; recorded as the tool for a future declared search |
| pbpstats, nba_data, nba-on-court, RAPM tutorial | reference only / deferred | lineups are postgame facts; stats.nba.com `playbyplayv2` now returns `{}`; nba-on-court breaks on numpy 2 |
| fastRhockey, sportsdataverse-data | reference / backfill only | their xG boosters were trained on 2010–24 shots (in-sample for backtests); goalie boxscores free but starters are postgame |
| penaltyblog (Dixon–Coles, bivariate Poisson) | rejected for our data | residual home/away goal correlation −0.05 (a shared component only adds positive correlation); excess draws at every tied score, covered by diagonal inflation |
| XGBoost / LightGBM | rejected | duplicate of sklearn HistGBT; need a system libomp on this Mac |
| Negative binomial | rejected | Pearson dispersion 1.02 / 0.96 given `goals_poisson` rates |
| kyleskom NBA betting repo | rejected (leakage example) | features joined to same-day season stats; an earlier version kept the best of 300 random splits |

## 2. Stage 1 — screening (log loss; candidate − strongest existing member, 95% week-block interval)
NHL 2018–21 (4,877 games). NBA 2022–24 (3,962 games). Strongest = best existing member on these seasons.
| Sport · market | Strongest | Best candidate | vs strongest | Shortlisted (rule) |
|---|---|---|---|---|
| NHL winner | `hockey_logistic_xg` 0.6716 | `logit_l2:nhl_base` (reference) 0.6722 | +0.0006 [−0.0004, +0.0017] | multinomial, mlp (within 0.002) |
| NHL reg3 | `goals_draw` 1.0523 | `poisson_draw:nhl_base` 1.0478 | **−0.0045 [−0.0067, −0.0023]** | multinomial (base, goalie), poisson_draw |
| NHL puck line | `goals_glm` 0.6827 | `poisson_draw:nhl_base` 0.6806 | **−0.0021 [−0.0036, −0.0006]** | poisson_draw (base, goalie) |
| NHL total / home team total | `goals_draw` / `goals_poisson` | poisson_draw | +0.0031 to +0.0041 (worse) | none |
| NHL away team total | `goals_draw` 0.6854 | poisson_draw 0.6842 | −0.0013 [−0.0034, +0.0008] | poisson_draw (pair gain −0.0015) |
| NBA winner | `logistic_v2` 0.6297 | `elastic_net` 0.6293 | −0.0004 [−0.0027, +0.0021] | elastic net, hetero_linear |
| NBA spread | `ridge_points@weekly` 0.6864 | `hetero_linear` 0.6864 | +0.0000 [−0.0008, +0.0008] | extra trees, hetero_linear, elastic net |
| NBA total / team totals | `points_normal` ≈ 0.693 | all candidates +0.005 to +0.021 worse | — | by small pair gains only |
- **Goalie inputs did not help**, even retrospectively with the actual starter (an optimistic assumption): `logit_l2:nhl_goalie`
  0.6735 vs `logit_l2:nhl_base` 0.6722; goalie variants never beat their base twins in any market.
- **Bagged trees, MLPs, NGBoost:** no NBA or NHL market where they beat the strongest member; NGBoost was the worst NBA
  winner/total candidate (+0.011 / +0.013). Error correlations with the strongest member were 0.96–1.00 everywhere.

## 3. Stage 2 — assessment of the frozen shortlist (NHL 2022–26, 6,993 games; NBA 2025–26, 2,643 games)
### Probability quality
| Sport · market | Candidate | Candidate − strongest | Ablation (same combination with − without candidate) |
|---|---|---|---|
| NHL reg3 | `poisson_draw:nhl_base` | **−0.0073 [−0.0091, −0.0053]**, better in all 5 seasons | stacker(existing + shortlist) − stacker(existing) **−0.0044 [−0.0066, −0.0021]**; avg(goals_draw + poisson_draw) − goals_draw −0.0045 [−0.0054, −0.0035] |
| NHL reg3 | `multinomial_l2:nhl_base` | **−0.0066 [−0.0100, −0.0031]** | avg(goals_draw + multinomial) − goals_draw −0.0062 [−0.0079, −0.0044] |
| NHL puck line | `poisson_draw:nhl_base` | **−0.0049 [−0.0065, −0.0032]**, better in all 5 seasons | stacker with − without −0.0051 [−0.0070, −0.0031]; avg with goals_glm −0.0029 [−0.0038, −0.0021] |
| NHL away team total | `poisson_draw:nhl_base` | **−0.0030 [−0.0048, −0.0011]**, better in all 5 seasons | stacker with − without −0.0030 [−0.0042, −0.0018] |
| NHL winner | `multinomial_l2:nhl_base` | −0.0002 [−0.0010, +0.0006] | stacker −0.0000; pair −0.0002 [−0.0006, +0.0002] — no value |
| NBA winner | elastic net / hetero_linear | +0.0014 / +0.0012 (intervals span 0) | pair and stacker ablations ≈ 0 — no value |
| NBA spread | hetero_linear | −0.0001 [−0.0005, +0.0004] | triple avg(ridge@weekly, gbt_points, hetero) − avg(ridge@weekly, gbt_points) −0.0007 [−0.0013, −0.0001] (still not better than ridge@weekly alone) |
| NBA total / team totals | ngboost, extra trees, hetero_linear | +0.001 to +0.013 (worse) | home team total: avg(points_normal + hetero) − points_normal −0.0034 [−0.0062, −0.0003]; others span 0 |

### Picks: selection at matched coverage (same seasons; accuracy with Wilson 95%; "vs top" = the strongest member's most
confident games at the same count, difference with week-block interval)
| Sport · market | Rule | Picks / coverage | Accuracy | Strongest's top, same count | Difference |
|---|---|---|---|---|---|
| NHL puck line | `poisson_draw:nhl_base` ≥ 0.65 | 388 / 5.5% | 70.6% [65.9, 74.9] | 62.9% | **+7.7 [+3.3, +12.2]** |
| NHL puck line | `poisson_draw:nhl_base` ≥ 0.60 | 1,759 / 25% | 63.4% [61.1, 65.6] | 60.5% | **+2.9 [+1.2, +4.7]** |
| NHL reg3 | `multinomial_l2:nhl_base` ≥ 0.50 | 2,851 / 41% | 54.9% [53.0, 56.7] | 52.8% | **+2.1 [+0.7, +3.4]** |
| NHL reg3 | `poisson_draw:nhl_base` ≥ 0.55 | 841 / 12% | 62.7% [59.3, 65.9] | 58.1% | **+4.6 [+1.8, +7.2]** |
| NHL winner | agree(xG, multinomial) & avg ≥ 0.70 | 919 / 13% | 75.1% [72.2, 77.8] | 75.0% | +0.1 [−0.6, +0.9] |
| NBA winner | agree(logistic_v2, hetero) & avg ≥ 0.70 | 924 / 35% | 80.6% [78.0, 83.0] | 81.0% | −0.4 [−0.9, +0.3] |
| NBA winner | elastic net ≥ 0.70 | 872 / 33% | 80.7% [78.0, 83.2] | 81.0% | −0.2 [−1.4, +0.9] |
426 rule × threshold combinations were scored in stage 2 (NHL 162, NBA 264); the positive NHL line results are consistent with the probability gains above
(same candidate, every season), the rest are within noise. **Agreement filters added nothing over confidence in any
winner market.**

### Weekly selector over the pool (selector-v1 arithmetic; evidence starts empty at stage 2, only settled earlier games)
| Sport · market | Picks / coverage | Accuracy | Strongest's top, same count | Difference |
|---|---|---|---|---|
| NBA winner | 662 / 25% | 81.4% [78.3, 84.2] | 80.7% | +0.8 [−1.0, +2.6] |
| NHL winner | 1,645 / 24% | 70.9% [68.6, 73.0] | 70.5% | +0.4 [−1.5, +2.6] |
| NHL puck line | 2,479 / 35% | 62.8% [60.9, 64.7] | 60.5% | **+2.3 [+0.4, +4.1]** |
| NHL reg3 | 1,407 / 20% | 58.0% [55.4, 60.5] | 56.4% | +1.6 [−0.9, +4.4] |
| NBA home team total | 568 / 21% | 57.0% [52.9, 61.1] | 46.0% | +11.1 [+5.2, +17.1] (the strongest's confident team-total picks lose; one of 11 selector runs; descriptive) |
| NBA spread / total | 790 / 144 picks | 54.2% / 50.0% | 55.2% / 57.6% | −1.0 / −7.6 |

## 4. Can any supported combination reach 80–85%?
- **NBA winner, yes — by selecting easy games, not by combining.** The existing `logistic_v2` alone: 84.9% on its top 20%
  of games (529 games, 2025–26), 86.7% on the top 10%, 81.2% on the top 30%; it first holds ≥ 80% at 38% coverage (1,010
  games, 80.0% [77.4, 82.4]) and ≥ 85% at 19.8% (522 games, 85.1% [81.7, 87.9]). No candidate, agreement rule, average,
  stacker or selector did better than that member's own confidence at the same count. ~500 settled picks give a ±3-point
  interval; telling 85% from 82% needs roughly 1,000+ picks (about one NBA season at 20% coverage).
- **NHL winner:** 80% only on the top ~6% (425 games, 80.0% [75.9, 83.5]); 85% only on 34 games (interval 70–94%). Not
  attainable at useful coverage.
- **Line markets (spread, totals, team totals, reg3):** nothing approaches 80%; the best held-out rules are ~63–71% at
  5–25% coverage on the NHL puck line, at the primary's model-only line (no historical bookmaker lines, so no claim
  against the bookmaker).

## 5. What changes
- **Prospective recording (built, rehearsed on store copies):** every registry candidate (19; weaker ones too) records
  two-hour forecasts into append-only `candidate_forecasts` (migration 0019) at the core cohort's exact line, model_version
  `research-cand:<name>:<definition>`, with fitted artifacts, fit register rows (training cutoff, fingerprints), input
  provenance (goalie listing used, feature values) and stale-input skips. Rehearsal: NHL tonight's 2 games 26 rows (44 s,
  retry adds 0); NBA 2026-10-20 opener 10 winner rows (129 s first run training all artifacts, 15 s retry).
- **Selection eligibility (rule written after stage 2; it can only narrow the frozen shortlist):** a shortlisted
  candidate is selectable in a market only where stage 2 showed a gain with a 95% upper bound below zero, alone against the
  strongest member or as the same average with versus without it, and only when that combination also beats the strongest
  member alone (`candidates.live.held_out_gains`). Result: NHL reg3 `poisson_draw:nhl_base`, `multinomial_l2:nhl_base`,
  `multinomial_l2:nhl_goalie`; NHL puck line and away team total `poisson_draw:nhl_base`, `poisson_draw:nhl_goalie`; NBA
  home team total `hetero_linear:nba_base` (pair with `points_normal`, −0.0034 [−0.0062, −0.0003]; a single result among
  many, weakest of the six). Each becomes selectable only after ≥ 100 settled prospective picks (selector rule). Everything
  else, including NGBoost, extra trees and the MLPs, is recorded for complementarity evidence only.
- **Primaries unchanged.** No promotion; `goals_draw` / `goals_glm` remain the research members; the sealed NHL primary is
  untouched.
- **Not done here (owned by the integration session -66 per the user's split):** calling `candidates.live.record` from the
  two-hour cycle, the pool selector's live weekly decisions, the roster API/UI (`candidates.live.roster` returns every field).
