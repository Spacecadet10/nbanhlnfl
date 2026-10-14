# Thresholds and exact subsets — NHL winner (adaptive:8631cc578d16aa5c)

Kind: historical out-of-sample (exploratory). Earlier 2023-10-10–2025-06-18 (2798 games); later 2025-10-07–2026-10-09 (1462 games).

Out-of-sample forecasts of the adaptive candidates (weekly-refit walk, night-before cutoff), saved in the adaptive history store and exported without refitting. These seasons were already used for training and model choices: all results are exploratory, never an untouched test. No historical bookmaker odds: nothing here measures profit. Research only: nothing here changes the live forecasts or selects a model.

Win rate denominator: wins + losses (pushes and unsettled games excluded and shown separately).

Models compared: cand:extra_trees:nhl_base@adaptive, cand:keras_mlp:nhl_base@adaptive, cand:logit_l2:nhl_base@adaptive, cand:mlp:nhl_base@adaptive, cand:multinomial_l2:nhl_base@adaptive, cand:poisson_draw:nhl_base@adaptive, cand:torch_mlp:nhl_base@adaptive.
Not stored: goals_draw@adaptive, goals_glm@adaptive, goals_poisson, goals_poisson@market, hockey_elo, hockey_gbt@adaptive, hockey_logistic@adaptive, hockey_logistic_xg@adaptive.
Excluded: none.
Missing earlier forecasts (those games are excluded from any rule or average needing the model): none.
Missing later forecasts (those games are excluded from any rule or average needing the model): none.

## Cumulative thresholds (later period)

| Source | Threshold | Record |
|---|---|---|
| cand:extra_trees:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 802–660–0P, 54.9% [52.3, 57.4], gap -3.1 pts |
| cand:extra_trees:nhl_base@adaptive | 55%+ | 920 picks (62.9%) 532–388–0P, 57.8% [54.6, 61.0], gap -3.3 pts |
| cand:extra_trees:nhl_base@adaptive | 60%+ | 455 picks (31.1%) 277–178–0P, 60.9% [56.3, 65.3], gap -4.1 pts |
| cand:extra_trees:nhl_base@adaptive | 65%+ | 192 picks (13.1%) 125–67–0P, 65.1% [58.1, 71.5], gap -3.8 pts |
| cand:extra_trees:nhl_base@adaptive | 70%+ | 65 picks (4.4%) 43–22–0P, 66.2% [54.0, 76.5], gap -6.5 pts |
| cand:extra_trees:nhl_base@adaptive | 75%+ | 8 picks (0.5%) 7–1–0P, 87.5% [52.9, 97.8], gap +10.3 pts |
| cand:keras_mlp:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 809–653–0P, 55.3% [52.8, 57.9], gap -3.1 pts |
| cand:keras_mlp:nhl_base@adaptive | 55%+ | 912 picks (62.4%) 530–382–0P, 58.1% [54.9, 61.3], gap -3.8 pts |
| cand:keras_mlp:nhl_base@adaptive | 60%+ | 489 picks (33.4%) 306–183–0P, 62.6% [58.2, 66.8], gap -3.4 pts |
| cand:keras_mlp:nhl_base@adaptive | 65%+ | 232 picks (15.9%) 146–86–0P, 62.9% [56.6, 68.9], gap -7.1 pts |
| cand:keras_mlp:nhl_base@adaptive | 70%+ | 95 picks (6.5%) 61–34–0P, 64.2% [54.2, 73.1], gap -9.8 pts |
| cand:keras_mlp:nhl_base@adaptive | 75%+ | 32 picks (2.2%) 25–7–0P, 78.1% [61.2, 89.0], gap +0.5 pts |
| cand:keras_mlp:nhl_base@adaptive | 80%+ | 4 picks (0.3%) 3–1–0P, 75.0% [30.1, 95.4], gap -7.0 pts |
| cand:keras_mlp:nhl_base@adaptive | 85%+ | 1 picks (0.1%) 1–0–0P, 100.0% [20.7, 100.0], gap +14.6 pts |
| cand:logit_l2:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 796–666–0P, 54.4% [51.9, 57.0], gap -4.6 pts |
| cand:logit_l2:nhl_base@adaptive | 55%+ | 958 picks (65.5%) 551–407–0P, 57.5% [54.4, 60.6], gap -4.9 pts |
| cand:logit_l2:nhl_base@adaptive | 60%+ | 561 picks (38.4%) 347–214–0P, 61.9% [57.8, 65.8], gap -4.1 pts |
| cand:logit_l2:nhl_base@adaptive | 65%+ | 286 picks (19.6%) 184–102–0P, 64.3% [58.6, 69.7], gap -5.2 pts |
| cand:logit_l2:nhl_base@adaptive | 70%+ | 105 picks (7.2%) 72–33–0P, 68.6% [59.2, 76.7], gap -5.1 pts |
| cand:logit_l2:nhl_base@adaptive | 75%+ | 29 picks (2.0%) 24–5–0P, 82.8% [65.5, 92.4], gap +5.1 pts |
| cand:logit_l2:nhl_base@adaptive | 80%+ | 4 picks (0.3%) 2–2–0P, 50.0% [15.0, 85.0], gap -31.7 pts |
| cand:mlp:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 808–654–0P, 55.3% [52.7, 57.8], gap -3.4 pts |
| cand:mlp:nhl_base@adaptive | 55%+ | 974 picks (66.6%) 566–408–0P, 58.1% [55.0, 61.2], gap -3.6 pts |
| cand:mlp:nhl_base@adaptive | 60%+ | 525 picks (35.9%) 323–202–0P, 61.5% [57.3, 65.6], gap -4.0 pts |
| cand:mlp:nhl_base@adaptive | 65%+ | 248 picks (17.0%) 154–94–0P, 62.1% [55.9, 67.9], gap -7.1 pts |
| cand:mlp:nhl_base@adaptive | 70%+ | 90 picks (6.2%) 63–27–0P, 70.0% [59.9, 78.5], gap -3.3 pts |
| cand:mlp:nhl_base@adaptive | 75%+ | 21 picks (1.4%) 16–5–0P, 76.2% [54.9, 89.4], gap -0.9 pts |
| cand:mlp:nhl_base@adaptive | 80%+ | 2 picks (0.1%) 1–1–0P, 50.0% [9.5, 90.5], gap -30.7 pts |
| cand:multinomial_l2:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 796–666–0P, 54.4% [51.9, 57.0], gap -3.9 pts |
| cand:multinomial_l2:nhl_base@adaptive | 55%+ | 925 picks (63.3%) 540–385–0P, 58.4% [55.2, 61.5], gap -3.3 pts |
| cand:multinomial_l2:nhl_base@adaptive | 60%+ | 507 picks (34.7%) 312–195–0P, 61.5% [57.2, 65.7], gap -3.7 pts |
| cand:multinomial_l2:nhl_base@adaptive | 65%+ | 221 picks (15.1%) 144–77–0P, 65.2% [58.7, 71.1], gap -4.0 pts |
| cand:multinomial_l2:nhl_base@adaptive | 70%+ | 69 picks (4.7%) 49–20–0P, 71.0% [59.4, 80.4], gap -2.3 pts |
| cand:multinomial_l2:nhl_base@adaptive | 75%+ | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +10.4 pts |
| cand:multinomial_l2:nhl_base@adaptive | 80%+ | 1 picks (0.1%) 1–0–0P, 100.0% [20.7, 100.0], gap +19.5 pts |
| cand:poisson_draw:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 799–663–0P, 54.7% [52.1, 57.2], gap -1.8 pts |
| cand:poisson_draw:nhl_base@adaptive | 55%+ | 789 picks (54.0%) 465–324–0P, 58.9% [55.5, 62.3], gap -1.0 pts |
| cand:poisson_draw:nhl_base@adaptive | 60%+ | 332 picks (22.7%) 210–122–0P, 63.3% [57.9, 68.3], gap -0.4 pts |
| cand:poisson_draw:nhl_base@adaptive | 65%+ | 88 picks (6.0%) 65–23–0P, 73.9% [63.8, 81.9], gap +5.8 pts |
| cand:poisson_draw:nhl_base@adaptive | 70%+ | 20 picks (1.4%) 15–5–0P, 75.0% [53.1, 88.8], gap +3.2 pts |
| cand:poisson_draw:nhl_base@adaptive | 75%+ | 2 picks (0.1%) 1–1–0P, 50.0% [9.5, 90.5], gap -25.2 pts |
| cand:torch_mlp:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 813–649–0P, 55.6% [53.1, 58.1], gap -2.5 pts |
| cand:torch_mlp:nhl_base@adaptive | 55%+ | 946 picks (64.7%) 548–398–0P, 57.9% [54.8, 61.0], gap -3.3 pts |
| cand:torch_mlp:nhl_base@adaptive | 60%+ | 462 picks (31.6%) 281–181–0P, 60.8% [56.3, 65.2], gap -4.5 pts |
| cand:torch_mlp:nhl_base@adaptive | 65%+ | 198 picks (13.5%) 126–72–0P, 63.6% [56.7, 70.0], gap -5.8 pts |
| cand:torch_mlp:nhl_base@adaptive | 70%+ | 67 picks (4.6%) 48–19–0P, 71.6% [59.9, 81.0], gap -1.9 pts |
| cand:torch_mlp:nhl_base@adaptive | 75%+ | 22 picks (1.5%) 16–6–0P, 72.7% [51.8, 86.8], gap -4.2 pts |
| cand:torch_mlp:nhl_base@adaptive | 80%+ | 1 picks (0.1%) 1–0–0P, 100.0% [20.7, 100.0], gap +18.5 pts |
| avg:cand:extra_trees:nhl_base@adaptive+cand:keras_mlp:nhl_base@adaptive+cand:logit_l2:nhl_base@adaptive+cand:mlp:nhl_base@adaptive+cand:multinomial_l2:nhl_base@adaptive+cand:poisson_draw:nhl_base@adaptive+cand:torch_mlp:nhl_base@adaptive | 50%+ | 1462 picks (100.0%) 803–659–0P, 54.9% [52.4, 57.5], gap -3.1 pts |
| avg:cand:extra_trees:nhl_base@adaptive+cand:keras_mlp:nhl_base@adaptive+cand:logit_l2:nhl_base@adaptive+cand:mlp:nhl_base@adaptive+cand:multinomial_l2:nhl_base@adaptive+cand:poisson_draw:nhl_base@adaptive+cand:torch_mlp:nhl_base@adaptive | 55%+ | 923 picks (63.1%) 532–391–0P, 57.6% [54.4, 60.8], gap -3.7 pts |
| avg:cand:extra_trees:nhl_base@adaptive+cand:keras_mlp:nhl_base@adaptive+cand:logit_l2:nhl_base@adaptive+cand:mlp:nhl_base@adaptive+cand:multinomial_l2:nhl_base@adaptive+cand:poisson_draw:nhl_base@adaptive+cand:torch_mlp:nhl_base@adaptive | 60%+ | 475 picks (32.5%) 295–180–0P, 62.1% [57.7, 66.4], gap -3.0 pts |
| avg:cand:extra_trees:nhl_base@adaptive+cand:keras_mlp:nhl_base@adaptive+cand:logit_l2:nhl_base@adaptive+cand:mlp:nhl_base@adaptive+cand:multinomial_l2:nhl_base@adaptive+cand:poisson_draw:nhl_base@adaptive+cand:torch_mlp:nhl_base@adaptive | 65%+ | 192 picks (13.1%) 123–69–0P, 64.1% [57.1, 70.5], gap -5.3 pts |
| avg:cand:extra_trees:nhl_base@adaptive+cand:keras_mlp:nhl_base@adaptive+cand:logit_l2:nhl_base@adaptive+cand:mlp:nhl_base@adaptive+cand:multinomial_l2:nhl_base@adaptive+cand:poisson_draw:nhl_base@adaptive+cand:torch_mlp:nhl_base@adaptive | 70%+ | 67 picks (4.6%) 48–19–0P, 71.6% [59.9, 81.0], gap -1.5 pts |
| avg:cand:extra_trees:nhl_base@adaptive+cand:keras_mlp:nhl_base@adaptive+cand:logit_l2:nhl_base@adaptive+cand:mlp:nhl_base@adaptive+cand:multinomial_l2:nhl_base@adaptive+cand:poisson_draw:nhl_base@adaptive+cand:torch_mlp:nhl_base@adaptive | 75%+ | 15 picks (1.0%) 12–3–0P, 80.0% [54.8, 93.0], gap +3.3 pts |
| avg:cand:extra_trees:nhl_base@adaptive+cand:keras_mlp:nhl_base@adaptive+cand:logit_l2:nhl_base@adaptive+cand:mlp:nhl_base@adaptive+cand:multinomial_l2:nhl_base@adaptive+cand:poisson_draw:nhl_base@adaptive+cand:torch_mlp:nhl_base@adaptive | 80%+ | 1 picks (0.1%) 1–0–0P, 100.0% [20.7, 100.0], gap +19.4 pts |

## Exact subsets (chosen on the earlier period, frozen, then scored on the later period)

### Search limits

- Source: adaptive:8631cc578d16aa5c (oof-8631cc578d16aa5c.json.gz 2be43a745a971160).
- Models searched (7): cand:extra_trees:nhl_base@adaptive, cand:keras_mlp:nhl_base@adaptive, cand:logit_l2:nhl_base@adaptive, cand:mlp:nhl_base@adaptive, cand:multinomial_l2:nhl_base@adaptive, cand:poisson_draw:nhl_base@adaptive, cand:torch_mlp:nhl_base@adaptive.
- Not searched: goals_draw@adaptive, goals_glm@adaptive, goals_poisson, goals_poisson@market, hockey_elo, hockey_gbt@adaptive, hockey_logistic@adaptive, hockey_logistic_xg@adaptive.
- Subset sizes [2, 3]; per-member thresholds [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]; 21 subsets of size 2, 35 subsets of size 3; 13034 rules searched; 7108 had ≥100 decided earlier picks.
- Earlier (search) 2023-10-10–2025-06-18; later (frozen check) 2025-10-07–2026-10-09. Best single model chosen on the earlier period: cand:keras_mlp:nhl_base@adaptive.
- A rule's record is an observed record on these games, not proof it will hold. Many rules were searched; the best earlier records are partly luck, which is why only the later period (frozen rules, no retuning) is evidence — and it is exploratory too.

### What the counts mean

Each list holds 20 frozen rules. "X of 20" counts RULES whose later win rate was above the comparator (the best single model's same number of most confident later picks, possibly different games). It is not a count of games won, and it is about this search only (these models, sizes, grid and minimum).

Descriptive later-period overlap: frozen rules whose later picks are exactly the same games (game IDs) with the same picked sides share one pick set. Rules in one set made the same picks, so their later win–loss–push outcomes, pending picks and win rate are identical; their mean probability and calibration gap can differ, because those come from each rule's own members. Matching win–loss totals on different games are different sets. Rules sharing a set are one later result, not independent confirmations. Counted only after the rules were frozen on the earlier period; it never removes, replaces, reorders or chooses a rule.

### Highest earlier win rate (ranked by its 95% lower bound)

Later win rate above the comparator: 18 of 20 rules, 9 distinct later pick sets. The 20 rules make 10 distinct later pick sets; shared: set 1 = rules 1, 3 (15 picks); set 2 = rules 2, 4, 5, 6 (16 picks); set 4 = rules 8, 9, 10 (14 picks); set 5 = rules 11, 19 (15 picks); set 6 = rules 12, 14 (22 picks); set 8 = rules 15, 18 (16 picks); set 10 = rules 17, 20 (27 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | cand:mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 109 picks (3.9%) 94–15–0P, 86.2% [78.5, 91.5], gap +8.5 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +9.7 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +8.0 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 2 | 2 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 113 picks (4.0%) 97–16–0P, 85.8% [78.2, 91.1], gap +9.8 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +11.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 3 | 1 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 107 picks (3.8%) 92–15–0P, 86.0% [78.2, 91.3], gap +9.2 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +10.5 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +8.0 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 4 | 2 | cand:extra_trees:nhl_base@adaptive ≥50% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 118 picks (4.2%) 101–17–0P, 85.6% [78.1, 90.8], gap +9.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +11.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 5 | 2 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:keras_mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 112 picks (4.0%) 96–16–0P, 85.7% [78.0, 91.0], gap +8.8 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +10.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 6 | 2 | cand:keras_mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 115 picks (4.1%) 98–17–0P, 85.2% [77.6, 90.6], gap +7.1 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.7 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 7 | 3 | cand:keras_mlp:nhl_base@adaptive ≥75% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 102 picks (3.6%) 87–15–0P, 85.3% [77.1, 90.9], gap +6.7 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +8.7 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +7.8 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 8 | 4 | cand:multinomial_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 102 picks (3.6%) 87–15–0P, 85.3% [77.1, 90.9], gap +9.7 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +11.0 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +7.1 pts | 14 picks (1.0%) 11–3–0P, 78.6% [52.4, 92.4], gap -0.8 pts |
| 9 | 4 | cand:keras_mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 101 picks (3.6%) 86–15–0P, 85.1% [76.9, 90.8], gap +8.4 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +9.7 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +7.1 pts | 14 picks (1.0%) 11–3–0P, 78.6% [52.4, 92.4], gap -0.8 pts |
| 10 | 4 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:multinomial_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 100 picks (3.6%) 85–15–0P, 85.0% [76.7, 90.7], gap +9.7 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +11.0 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +7.1 pts | 14 picks (1.0%) 11–3–0P, 78.6% [52.4, 92.4], gap -0.8 pts |
| 11 | 5 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 108 picks (3.9%) 91–17–0P, 84.3% [76.2, 89.9], gap +8.4 pts | 15 picks (1.0%) 12–3–0P, 80.0% [54.8, 93.0], gap +4.5 pts | 15 picks (1.0%) 12–3–0P, 80.0% [54.8, 93.0], gap +1.0 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 12 | 6 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥75% | 136 picks (4.9%) 113–23–0P, 83.1% [75.9, 88.5], gap +6.3 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap +1.0 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -0.7 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -1.2 pts |
| 13 | 7 | cand:logit_l2:nhl_base@adaptive ≥75% & cand:mlp:nhl_base@adaptive ≥70% & cand:poisson_draw:nhl_base@adaptive ≥70% | 116 picks (4.1%) 97–19–0P, 83.6% [75.8, 89.3], gap +7.0 pts | 16 picks (1.1%) 13–3–0P, 81.2% [57.0, 93.4], gap +5.3 pts | 16 picks (1.1%) 13–3–0P, 81.2% [57.0, 93.4], gap +2.6 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 14 | 6 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥65% | 135 picks (4.8%) 112–23–0P, 83.0% [75.7, 88.4], gap +7.8 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap +2.8 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -0.7 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -1.2 pts |
| 15 | 8 | cand:keras_mlp:nhl_base@adaptive ≥75% & cand:logit_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 110 picks (3.9%) 92–18–0P, 83.6% [75.6, 89.4], gap +6.4 pts | 16 picks (1.1%) 13–3–0P, 81.2% [57.0, 93.4], gap +4.7 pts | 16 picks (1.1%) 13–3–0P, 81.2% [57.0, 93.4], gap +2.4 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 16 | 9 | cand:logit_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 119 picks (4.3%) 99–20–0P, 83.2% [75.5, 88.8], gap +7.1 pts | 17 picks (1.2%) 14–3–0P, 82.4% [59.0, 93.8], gap +7.0 pts | 17 picks (1.2%) 14–3–0P, 82.4% [59.0, 93.8], gap +3.9 pts | 17 picks (1.2%) 13–4–0P, 76.5% [52.7, 90.4], gap -2.5 pts |
| 17 | 10 | cand:logit_l2:nhl_base@adaptive ≥75% & cand:mlp:nhl_base@adaptive ≥70% | 157 picks (5.6%) 129–28–0P, 82.2% [75.4, 87.4], gap +4.4 pts | 27 picks (1.8%) 22–5–0P, 81.5% [63.3, 91.8], gap +4.7 pts | 27 picks (1.8%) 22–5–0P, 81.5% [63.3, 91.8], gap +4.2 pts | 27 picks (1.8%) 21–6–0P, 77.8% [59.2, 89.4], gap -0.2 pts |
| 18 | 8 | cand:keras_mlp:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 114 picks (4.1%) 95–19–0P, 83.3% [75.4, 89.1], gap +7.4 pts | 16 picks (1.1%) 13–3–0P, 81.2% [57.0, 93.4], gap +5.8 pts | 16 picks (1.1%) 13–3–0P, 81.2% [57.0, 93.4], gap +2.4 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 19 | 5 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:keras_mlp:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 109 picks (3.9%) 91–18–0P, 83.5% [75.4, 89.3], gap +7.8 pts | 15 picks (1.0%) 12–3–0P, 80.0% [54.8, 93.0], gap +4.4 pts | 15 picks (1.0%) 12–3–0P, 80.0% [54.8, 93.0], gap +1.0 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 20 | 10 | cand:keras_mlp:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥75% & cand:mlp:nhl_base@adaptive ≥70% | 156 picks (5.6%) 128–28–0P, 82.1% [75.3, 87.3], gap +4.3 pts | 27 picks (1.8%) 22–5–0P, 81.5% [63.3, 91.8], gap +4.5 pts | 27 picks (1.8%) 22–5–0P, 81.5% [63.3, 91.8], gap +4.2 pts | 27 picks (1.8%) 21–6–0P, 77.8% [59.2, 89.4], gap -0.2 pts |

### Strongest earlier improvement over the best single model at the same number of picks

Later win rate above the comparator: 14 of 20 rules, 8 distinct later pick sets. The 20 rules make 12 distinct later pick sets; shared: set 1 = rules 1, 2 (15 picks); set 2 = rules 3, 4, 5, 10 (16 picks); set 5 = rules 8, 19, 20 (14 picks); set 10 = rules 14, 15 (22 picks); set 11 = rules 16, 17 (61 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 107 picks (3.8%) 92–15–0P, 86.0% [78.2, 91.3], gap +9.2 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +10.5 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +8.0 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 2 | 1 | cand:mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 109 picks (3.9%) 94–15–0P, 86.2% [78.5, 91.5], gap +8.5 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +9.7 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +8.0 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 3 | 2 | cand:extra_trees:nhl_base@adaptive ≥50% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 118 picks (4.2%) 101–17–0P, 85.6% [78.1, 90.8], gap +9.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +11.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 4 | 2 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:keras_mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 112 picks (4.0%) 96–16–0P, 85.7% [78.0, 91.0], gap +8.8 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +10.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 5 | 2 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 113 picks (4.0%) 97–16–0P, 85.8% [78.2, 91.1], gap +9.8 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +11.9 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 6 | 3 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥70% | 222 picks (7.9%) 176–46–0P, 79.3% [73.5, 84.1], gap +4.2 pts | 59 picks (4.0%) 39–20–0P, 66.1% [53.4, 76.9], gap -7.7 pts | 59 picks (4.0%) 39–20–0P, 66.1% [53.4, 76.9], gap -8.9 pts | 59 picks (4.0%) 43–16–0P, 72.9% [60.4, 82.6], gap -2.8 pts |
| 7 | 4 | cand:keras_mlp:nhl_base@adaptive ≥75% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 102 picks (3.6%) 87–15–0P, 85.3% [77.1, 90.9], gap +6.7 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +8.7 pts | 15 picks (1.0%) 13–2–0P, 86.7% [62.1, 96.3], gap +7.8 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 8 | 5 | cand:multinomial_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 102 picks (3.6%) 87–15–0P, 85.3% [77.1, 90.9], gap +9.7 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +11.0 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +7.1 pts | 14 picks (1.0%) 11–3–0P, 78.6% [52.4, 92.4], gap -0.8 pts |
| 9 | 6 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 108 picks (3.9%) 91–17–0P, 84.3% [76.2, 89.9], gap +8.4 pts | 15 picks (1.0%) 12–3–0P, 80.0% [54.8, 93.0], gap +4.5 pts | 15 picks (1.0%) 12–3–0P, 80.0% [54.8, 93.0], gap +1.0 pts | 15 picks (1.0%) 11–4–0P, 73.3% [48.0, 89.1], gap -5.9 pts |
| 10 | 2 | cand:keras_mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% | 115 picks (4.1%) 98–17–0P, 85.2% [77.6, 90.6], gap +7.1 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.7 pts | 16 picks (1.1%) 14–2–0P, 87.5% [64.0, 96.5], gap +9.0 pts | 16 picks (1.1%) 12–4–0P, 75.0% [50.5, 89.8], gap -4.1 pts |
| 11 | 7 | cand:keras_mlp:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥70% & cand:mlp:nhl_base@adaptive ≥70% | 280 picks (10.0%) 219–61–0P, 78.2% [73.0, 82.6], gap +2.7 pts | 77 picks (5.3%) 54–23–0P, 70.1% [59.2, 79.2], gap -4.1 pts | 77 picks (5.3%) 54–23–0P, 70.1% [59.2, 79.2], gap -4.5 pts | 77 picks (5.3%) 53–24–0P, 68.8% [57.8, 78.1], gap -6.0 pts |
| 12 | 8 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥70% & cand:poisson_draw:nhl_base@adaptive ≥65% | 217 picks (7.8%) 171–46–0P, 78.8% [72.9, 83.7], gap +5.2 pts | 57 picks (3.9%) 38–19–0P, 66.7% [53.7, 77.5], gap -5.5 pts | 57 picks (3.9%) 38–19–0P, 66.7% [53.7, 77.5], gap -8.6 pts | 57 picks (3.9%) 41–16–0P, 71.9% [59.2, 81.9], gap -3.9 pts |
| 13 | 9 | cand:extra_trees:nhl_base@adaptive ≥50% & cand:logit_l2:nhl_base@adaptive ≥75% | 178 picks (6.4%) 144–34–0P, 80.9% [74.5, 86.0], gap +5.3 pts | 29 picks (2.0%) 24–5–0P, 82.8% [65.5, 92.4], gap +7.3 pts | 29 picks (2.0%) 24–5–0P, 82.8% [65.5, 92.4], gap +5.7 pts | 29 picks (2.0%) 23–6–0P, 79.3% [61.6, 90.2], gap +1.5 pts |
| 14 | 10 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥65% | 135 picks (4.8%) 112–23–0P, 83.0% [75.7, 88.4], gap +7.8 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap +2.8 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -0.7 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -1.2 pts |
| 15 | 10 | cand:extra_trees:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥75% | 136 picks (4.9%) 113–23–0P, 83.1% [75.9, 88.5], gap +6.3 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap +1.0 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -0.7 pts | 22 picks (1.5%) 17–5–0P, 77.3% [56.6, 89.9], gap -1.2 pts |
| 16 | 11 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥70% | 275 picks (9.8%) 214–61–0P, 77.8% [72.5, 82.3], gap +3.8 pts | 61 picks (4.2%) 44–17–0P, 72.1% [59.8, 81.8], gap -1.3 pts | 61 picks (4.2%) 44–17–0P, 72.1% [59.8, 81.8], gap -3.1 pts | 61 picks (4.2%) 44–17–0P, 72.1% [59.8, 81.8], gap -3.5 pts |
| 17 | 11 | cand:mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥70% | 278 picks (9.9%) 216–62–0P, 77.7% [72.4, 82.2], gap +3.0 pts | 61 picks (4.2%) 44–17–0P, 72.1% [59.8, 81.8], gap -1.8 pts | 61 picks (4.2%) 44–17–0P, 72.1% [59.8, 81.8], gap -3.1 pts | 61 picks (4.2%) 44–17–0P, 72.1% [59.8, 81.8], gap -3.5 pts |
| 18 | 12 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:keras_mlp:nhl_base@adaptive ≥70% & cand:logit_l2:nhl_base@adaptive ≥70% | 296 picks (10.6%) 232–64–0P, 78.4% [73.3, 82.7], gap +3.9 pts | 81 picks (5.5%) 56–25–0P, 69.1% [58.4, 78.1], gap -4.4 pts | 81 picks (5.5%) 56–25–0P, 69.1% [58.4, 78.1], gap -5.4 pts | 81 picks (5.5%) 55–26–0P, 67.9% [57.1, 77.1], gap -6.7 pts |
| 19 | 5 | cand:extra_trees:nhl_base@adaptive ≥65% & cand:multinomial_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 100 picks (3.6%) 85–15–0P, 85.0% [76.7, 90.7], gap +9.7 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +11.0 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +7.1 pts | 14 picks (1.0%) 11–3–0P, 78.6% [52.4, 92.4], gap -0.8 pts |
| 20 | 5 | cand:keras_mlp:nhl_base@adaptive ≥70% & cand:multinomial_l2:nhl_base@adaptive ≥75% & cand:poisson_draw:nhl_base@adaptive ≥70% | 101 picks (3.6%) 86–15–0P, 85.1% [76.9, 90.8], gap +8.4 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +9.7 pts | 14 picks (1.0%) 12–2–0P, 85.7% [60.1, 96.0], gap +7.1 pts | 14 picks (1.0%) 11–3–0P, 78.6% [52.4, 92.4], gap -0.8 pts |

