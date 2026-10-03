# Training inventory (2026-10-03)

What was trained, when, on what, and which predictions each study reused. Seasons are end years (2026 = 2025-26). Every
learned model is refitted at a season start on all earlier seasons' results available before that season; online models
update after every result. Cutoffs of the historical tables: night before the game (~midnight ET); live forecasts are made
within two hours of the start. Schedules: docs/TRAINING_AUDIT.md, MODEL_SELECTION §13.

## Existing members (reused; not refitted by the discovery study)
| Sport · markets | Model | Type / features | Training |
|---|---|---|---|
| NBA winner | `elo` (primary) | online rating, sealed config | after every result |
| NBA winner | `logistic`, `logistic_v2`, `efficiency_margin`, `gbt_v1` | Elo + rest/b2b/efficiency features (BASELINE_REPORT) | season start (weekly variants studied, §9) |
| NBA lines | `points_normal` (primary) | online margin/total ratings, normal head | after every result |
| NBA lines | `ridge_points`, `gbt_points`; `…@weekly` shadows | `NBA_FEATURES` (elo_diff, mov10_diff, rest_diff, b2b, neutral, …) | season start; weekly shadows refit each ISO week |
| NHL winner | `hockey_elo` (primary), `goals_poisson` | online rating / Poisson goal rates | after every result |
| NHL winner | `hockey_logistic`, `hockey_gbt`, `hockey_logistic_xg` | ratings, rest, goal and shot-quality (xG) rates | season start |
| NHL reg3, lines | `goals_poisson` (primary), `goals_draw`, `goals_glm` | goal rates; draw inflation; GLM | online / season start |

## Candidate pool (pool-v1; reused chronological out-of-sample predictions)
Fitted 2026-10-02 (pool study, `data/reports/pool-{nhl,nba}/{screen,assess}.json`, `data/research/candidates/experiments.jsonl`):
- **NHL** (`nhl_base`: elo_diff, xg_diff, gd10_diff, rest_diff, b2b, neutral, …; `nhl_goalie` adds goalie GSAx/experience):
  `logit_l2`, `extra_trees`, `mlp`, `multinomial_l2`, `poisson_draw` (base and goalie). 10 season-start fits each (seasons
  2017–2026); last fit 13,114 games with results through 2025-06-18.
- **NBA** (`nba_base` = `NBA_FEATURES`): `logit_l2`, `extra_trees`, `random_forest`, `mlp` (winner); `ridge`, `elastic_net`,
  `extra_trees:normal`, `mlp:normal`, `ngboost`, `hetero_linear` (margin/total). 6 fits each (2021–2026); last fit 7,597
  games through 2025-06-23.
- Chained out-of-sample tables used by discovery-v1: NHL 2018–2026 (11,870 games), NBA 2022–2026 (6,605 games); input
  sha256 prefixes `oof-nhl-screen` bd4824b9…, `oof-nhl-assess` 69b587f7…, `oof-nba-screen` 70699e2f…, `oof-nba-assess` fdf53646….
- Goalie variants are excluded from discovery: their historical inputs used the actual starter (not known pregame).

## Newly trained in discovery-v1 (2026-10-03, MODEL_SELECTION §18)
Two learned combiners per market, refitted at every season start on earlier seasons' out-of-sample member predictions:
`stack_l2` (L2 logistic/multinomial on clipped log-probabilities, C 0.1) and `stack_l1` (L1, C 0.05). 22 configurations.
- nhl:winner: 8 season fits each (2019–2026); last fit 10476 games, seasons 2018–2025; L1 keeps ['hockey_logistic_xg', 'logit_l2:nhl_base']
- nhl:reg3: 8 season fits each (2019–2026); last fit 10476 games, seasons 2018–2025; L1 keeps ['multinomial_l2:nhl_base']
- nhl:spread: 8 season fits each (2019–2026); last fit 10476 games, seasons 2018–2025; L1 keeps ['poisson_draw:nhl_base']
- nhl:total: 8 season fits each (2019–2026); last fit 10476 games, seasons 2018–2025; L1 keeps ['goals_poisson', 'poisson_draw:nhl_base']
- nhl:team_total_home: 8 season fits each (2019–2026); last fit 10476 games, seasons 2018–2025; L1 keeps ['poisson_draw:nhl_base']
- nhl:team_total_away: 8 season fits each (2019–2026); last fit 10476 games, seasons 2018–2025; L1 keeps ['poisson_draw:nhl_base']
- nba:winner: 4 season fits each (2023–2026); last fit 5283 games, seasons 2022–2025; L1 keeps ['logistic_v2', 'gbt_v1', 'elastic_net:nba_base', 'mlp:nba_base:normal']
- nba:spread: 4 season fits each (2023–2026); last fit 5283 games, seasons 2022–2025; L1 keeps ['ridge_points@weekly', 'gbt_points@weekly', 'extra_trees:nba_base:normal', 'mlp:nba_base:normal']
- nba:total: 4 season fits each (2023–2026); last fit 5283 games, seasons 2022–2025; L1 keeps ['extra_trees:nba_base:normal', 'ngboost:nba_base']
- nba:team_total_home: 4 season fits each (2023–2026); last fit 5283 games, seasons 2022–2025; L1 keeps ['ngboost:nba_base']
- nba:team_total_away: 4 season fits each (2023–2026); last fit 5283 games, seasons 2022–2025; L1 keeps ['extra_trees:nba_base:normal']

## Not trained (ideas only)
Bookmaker-line models (no historical lines), injury-aware NBA line models (availability only studied for the winner at
17:45 ET), goalie-aware models with pregame (not actual) starters for history, daily refits (tested in daily-v1: no gain).
