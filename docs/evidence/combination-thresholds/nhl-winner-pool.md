# Thresholds and exact subsets — NHL winner (pool)

Kind: historical out-of-sample (exploratory). Earlier 2017-10-04–2024-06-25 (9078 games); later 2024-10-04–2026-06-15 (2792 games). Later includes spent reserved-test seasons [2025, 2026].

Historical out-of-sample forecasts saved by the candidate pool (walk-forward, night-before cutoff). Every season here was already used to choose or screen models, including the spent reserved-test seasons: all results are exploratory, never an untouched test. No historical bookmaker odds: nothing here measures profit. Research only: nothing here changes the live forecasts or selects a model.

Win rate denominator: wins + losses (pushes and unsettled games excluded and shown separately).

Models compared: hockey_elo, goals_poisson, hockey_logistic, hockey_gbt, hockey_logistic_xg, logit_l2:nhl_base, extra_trees:nhl_base, mlp:nhl_base, multinomial_l2:nhl_base, poisson_draw:nhl_base.
Not stored: none.
Excluded: logit_l2:nhl_goalie (used the actual starting goalie, known only after the start (postgame input)); extra_trees:nhl_goalie (used the actual starting goalie, known only after the start (postgame input)); multinomial_l2:nhl_goalie (used the actual starting goalie, known only after the start (postgame input)); poisson_draw:nhl_goalie (used the actual starting goalie, known only after the start (postgame input)).
Missing earlier forecasts (those games are excluded from any rule or average needing the model): none.
Missing later forecasts (those games are excluded from any rule or average needing the model): none.

## Cumulative thresholds (later period)

| Source | Threshold | Record |
|---|---|---|
| hockey_elo | 50%+ | 2792 picks (100.0%) 1568–1224–0P, 56.2% [54.3, 58.0], gap -2.4 pts |
| hockey_elo | 55%+ | 1801 picks (64.5%) 1058–743–0P, 58.7% [56.5, 61.0], gap -3.1 pts |
| hockey_elo | 60%+ | 980 picks (35.1%) 631–349–0P, 64.4% [61.3, 67.3], gap -1.2 pts |
| hockey_elo | 65%+ | 457 picks (16.4%) 306–151–0P, 67.0% [62.5, 71.1], gap -2.3 pts |
| hockey_elo | 70%+ | 152 picks (5.4%) 106–46–0P, 69.7% [62.0, 76.5], gap -3.7 pts |
| hockey_elo | 75%+ | 46 picks (1.6%) 36–10–0P, 78.3% [64.4, 87.7], gap +1.4 pts |
| hockey_elo | 80%+ | 2 picks (0.1%) 2–0–0P, 100.0% [34.2, 100.0], gap +18.5 pts |
| goals_poisson | 50%+ | 2792 picks (100.0%) 1575–1217–0P, 56.4% [54.6, 58.2], gap -0.6 pts |
| goals_poisson | 55%+ | 1613 picks (57.8%) 961–652–0P, 59.6% [57.2, 61.9], gap -0.8 pts |
| goals_poisson | 60%+ | 695 picks (24.9%) 458–237–0P, 65.9% [62.3, 69.3], gap +1.3 pts |
| goals_poisson | 65%+ | 255 picks (9.1%) 178–77–0P, 69.8% [63.9, 75.1], gap +1.3 pts |
| goals_poisson | 70%+ | 67 picks (2.4%) 45–22–0P, 67.2% [55.3, 77.2], gap -5.6 pts |
| goals_poisson | 75%+ | 6 picks (0.2%) 4–2–0P, 66.7% [30.0, 90.3], gap -11.0 pts |
| hockey_logistic | 50%+ | 2792 picks (100.0%) 1556–1236–0P, 55.7% [53.9, 57.6], gap -3.6 pts |
| hockey_logistic | 55%+ | 1888 picks (67.6%) 1102–786–0P, 58.4% [56.1, 60.6], gap -4.3 pts |
| hockey_logistic | 60%+ | 1115 picks (39.9%) 707–408–0P, 63.4% [60.5, 66.2], gap -2.9 pts |
| hockey_logistic | 65%+ | 570 picks (20.4%) 381–189–0P, 66.8% [62.9, 70.6], gap -3.3 pts |
| hockey_logistic | 70%+ | 241 picks (8.6%) 167–74–0P, 69.3% [63.2, 74.8], gap -4.9 pts |
| hockey_logistic | 75%+ | 80 picks (2.9%) 61–19–0P, 76.2% [65.9, 84.2], gap -1.9 pts |
| hockey_logistic | 80%+ | 18 picks (0.6%) 16–2–0P, 88.9% [67.2, 96.9], gap +7.4 pts |
| hockey_logistic | 85%+ | 1 picks (0.0%) 1–0–0P, 100.0% [20.7, 100.0], gap +12.8 pts |
| hockey_gbt | 50%+ | 2792 picks (100.0%) 1569–1223–0P, 56.2% [54.3, 58.0], gap -3.6 pts |
| hockey_gbt | 55%+ | 1850 picks (66.3%) 1076–774–0P, 58.2% [55.9, 60.4], gap -5.3 pts |
| hockey_gbt | 60%+ | 1188 picks (42.6%) 739–449–0P, 62.2% [59.4, 64.9], gap -4.5 pts |
| hockey_gbt | 65%+ | 647 picks (23.2%) 422–225–0P, 65.2% [61.5, 68.8], gap -5.2 pts |
| hockey_gbt | 70%+ | 254 picks (9.1%) 177–77–0P, 69.7% [63.8, 75.0], gap -5.8 pts |
| hockey_gbt | 75%+ | 103 picks (3.7%) 75–28–0P, 72.8% [63.5, 80.5], gap -8.2 pts |
| hockey_gbt | 80%+ | 58 picks (2.1%) 45–13–0P, 77.6% [65.3, 86.4], gap -6.4 pts |
| hockey_gbt | 85%+ | 19 picks (0.7%) 15–4–0P, 78.9% [56.7, 91.5], gap -9.3 pts |
| hockey_gbt | 90%+ | 4 picks (0.1%) 3–1–0P, 75.0% [30.1, 95.4], gap -16.5 pts |
| hockey_logistic_xg | 50%+ | 2792 picks (100.0%) 1587–1205–0P, 56.8% [55.0, 58.7], gap -3.1 pts |
| hockey_logistic_xg | 55%+ | 1936 picks (69.3%) 1150–786–0P, 59.4% [57.2, 61.6], gap -3.9 pts |
| hockey_logistic_xg | 60%+ | 1214 picks (43.5%) 768–446–0P, 63.3% [60.5, 65.9], gap -3.5 pts |
| hockey_logistic_xg | 65%+ | 661 picks (23.7%) 451–210–0P, 68.2% [64.6, 71.7], gap -2.3 pts |
| hockey_logistic_xg | 70%+ | 300 picks (10.7%) 212–88–0P, 70.7% [65.3, 75.5], gap -3.9 pts |
| hockey_logistic_xg | 75%+ | 112 picks (4.0%) 89–23–0P, 79.5% [71.1, 85.9], gap +1.1 pts |
| hockey_logistic_xg | 80%+ | 27 picks (1.0%) 22–5–0P, 81.5% [63.3, 91.8], gap -0.5 pts |
| hockey_logistic_xg | 85%+ | 1 picks (0.0%) 1–0–0P, 100.0% [20.7, 100.0], gap +12.3 pts |
| logit_l2:nhl_base | 50%+ | 2792 picks (100.0%) 1585–1207–0P, 56.8% [54.9, 58.6], gap -3.1 pts |
| logit_l2:nhl_base | 55%+ | 1933 picks (69.2%) 1145–788–0P, 59.2% [57.0, 61.4], gap -4.0 pts |
| logit_l2:nhl_base | 60%+ | 1203 picks (43.1%) 765–438–0P, 63.6% [60.8, 66.3], gap -3.1 pts |
| logit_l2:nhl_base | 65%+ | 648 picks (23.2%) 440–208–0P, 67.9% [64.2, 71.4], gap -2.6 pts |
| logit_l2:nhl_base | 70%+ | 294 picks (10.5%) 212–82–0P, 72.1% [66.7, 76.9], gap -2.3 pts |
| logit_l2:nhl_base | 75%+ | 107 picks (3.8%) 87–20–0P, 81.3% [72.9, 87.6], gap +2.9 pts |
| logit_l2:nhl_base | 80%+ | 25 picks (0.9%) 20–5–0P, 80.0% [60.9, 91.1], gap -2.0 pts |
| logit_l2:nhl_base | 85%+ | 1 picks (0.0%) 1–0–0P, 100.0% [20.7, 100.0], gap +12.7 pts |
| extra_trees:nhl_base | 50%+ | 2792 picks (100.0%) 1579–1213–0P, 56.6% [54.7, 58.4], gap -2.2 pts |
| extra_trees:nhl_base | 55%+ | 1840 picks (65.9%) 1097–743–0P, 59.6% [57.4, 61.8], gap -2.4 pts |
| extra_trees:nhl_base | 60%+ | 1048 picks (37.5%) 670–378–0P, 63.9% [61.0, 66.8], gap -1.6 pts |
| extra_trees:nhl_base | 65%+ | 480 picks (17.2%) 331–149–0P, 69.0% [64.7, 72.9], gap -0.4 pts |
| extra_trees:nhl_base | 70%+ | 177 picks (6.3%) 126–51–0P, 71.2% [64.1, 77.4], gap -2.0 pts |
| extra_trees:nhl_base | 75%+ | 41 picks (1.5%) 33–8–0P, 80.5% [66.0, 89.8], gap +3.7 pts |
| mlp:nhl_base | 50%+ | 2792 picks (100.0%) 1586–1206–0P, 56.8% [55.0, 58.6], gap -2.7 pts |
| mlp:nhl_base | 55%+ | 1934 picks (69.3%) 1156–778–0P, 59.8% [57.6, 61.9], gap -2.9 pts |
| mlp:nhl_base | 60%+ | 1160 picks (41.5%) 738–422–0P, 63.6% [60.8, 66.3], gap -2.5 pts |
| mlp:nhl_base | 65%+ | 573 picks (20.5%) 389–184–0P, 67.9% [64.0, 71.6], gap -2.2 pts |
| mlp:nhl_base | 70%+ | 250 picks (9.0%) 184–66–0P, 73.6% [67.8, 78.7], gap -0.2 pts |
| mlp:nhl_base | 75%+ | 78 picks (2.8%) 60–18–0P, 76.9% [66.4, 84.9], gap -0.7 pts |
| mlp:nhl_base | 80%+ | 13 picks (0.5%) 11–2–0P, 84.6% [57.8, 95.7], gap +3.4 pts |
| multinomial_l2:nhl_base | 50%+ | 2792 picks (100.0%) 1576–1216–0P, 56.4% [54.6, 58.3], gap -2.7 pts |
| multinomial_l2:nhl_base | 55%+ | 1847 picks (66.2%) 1107–740–0P, 59.9% [57.7, 62.1], gap -2.5 pts |
| multinomial_l2:nhl_base | 60%+ | 1088 picks (39.0%) 700–388–0P, 64.3% [61.4, 67.1], gap -1.7 pts |
| multinomial_l2:nhl_base | 65%+ | 531 picks (19.0%) 360–171–0P, 67.8% [63.7, 71.6], gap -2.1 pts |
| multinomial_l2:nhl_base | 70%+ | 215 picks (7.7%) 157–58–0P, 73.0% [66.7, 78.5], gap -0.8 pts |
| multinomial_l2:nhl_base | 75%+ | 62 picks (2.2%) 54–8–0P, 87.1% [76.6, 93.3], gap +9.4 pts |
| multinomial_l2:nhl_base | 80%+ | 6 picks (0.2%) 5–1–0P, 83.3% [43.6, 97.0], gap +1.7 pts |
| poisson_draw:nhl_base | 50%+ | 2792 picks (100.0%) 1584–1208–0P, 56.7% [54.9, 58.6], gap -0.5 pts |
| poisson_draw:nhl_base | 55%+ | 1617 picks (57.9%) 991–626–0P, 61.3% [58.9, 63.6], gap +0.6 pts |
| poisson_draw:nhl_base | 60%+ | 753 picks (27.0%) 503–250–0P, 66.8% [63.4, 70.1], gap +2.3 pts |
| poisson_draw:nhl_base | 65%+ | 278 picks (10.0%) 207–71–0P, 74.5% [69.0, 79.2], gap +6.1 pts |
| poisson_draw:nhl_base | 70%+ | 71 picks (2.5%) 54–17–0P, 76.1% [65.0, 84.5], gap +3.6 pts |
| poisson_draw:nhl_base | 75%+ | 8 picks (0.3%) 7–1–0P, 87.5% [52.9, 97.8], gap +10.9 pts |
| poisson_draw:nhl_base | 80%+ | 1 picks (0.0%) 1–0–0P, 100.0% [20.7, 100.0], gap +19.4 pts |
| avg:hockey_elo+goals_poisson+hockey_logistic+hockey_gbt+hockey_logistic_xg+logit_l2:nhl_base+extra_trees:nhl_base+mlp:nhl_base+multinomial_l2:nhl_base+poisson_draw:nhl_base | 50%+ | 2792 picks (100.0%) 1570–1222–0P, 56.2% [54.4, 58.1], gap -2.5 pts |
| avg:hockey_elo+goals_poisson+hockey_logistic+hockey_gbt+hockey_logistic_xg+logit_l2:nhl_base+extra_trees:nhl_base+mlp:nhl_base+multinomial_l2:nhl_base+poisson_draw:nhl_base | 55%+ | 1817 picks (65.1%) 1088–729–0P, 59.9% [57.6, 62.1], gap -2.2 pts |
| avg:hockey_elo+goals_poisson+hockey_logistic+hockey_gbt+hockey_logistic_xg+logit_l2:nhl_base+extra_trees:nhl_base+mlp:nhl_base+multinomial_l2:nhl_base+poisson_draw:nhl_base | 60%+ | 1023 picks (36.6%) 662–361–0P, 64.7% [61.7, 67.6], gap -0.9 pts |
| avg:hockey_elo+goals_poisson+hockey_logistic+hockey_gbt+hockey_logistic_xg+logit_l2:nhl_base+extra_trees:nhl_base+mlp:nhl_base+multinomial_l2:nhl_base+poisson_draw:nhl_base | 65%+ | 470 picks (16.8%) 325–145–0P, 69.1% [64.8, 73.2], gap -0.4 pts |
| avg:hockey_elo+goals_poisson+hockey_logistic+hockey_gbt+hockey_logistic_xg+logit_l2:nhl_base+extra_trees:nhl_base+mlp:nhl_base+multinomial_l2:nhl_base+poisson_draw:nhl_base | 70%+ | 164 picks (5.9%) 120–44–0P, 73.2% [65.9, 79.4], gap -0.6 pts |
| avg:hockey_elo+goals_poisson+hockey_logistic+hockey_gbt+hockey_logistic_xg+logit_l2:nhl_base+extra_trees:nhl_base+mlp:nhl_base+multinomial_l2:nhl_base+poisson_draw:nhl_base | 75%+ | 49 picks (1.8%) 40–9–0P, 81.6% [68.6, 90.0], gap +4.0 pts |
| avg:hockey_elo+goals_poisson+hockey_logistic+hockey_gbt+hockey_logistic_xg+logit_l2:nhl_base+extra_trees:nhl_base+mlp:nhl_base+multinomial_l2:nhl_base+poisson_draw:nhl_base | 80%+ | 1 picks (0.0%) 1–0–0P, 100.0% [20.7, 100.0], gap +15.5 pts |

## Exact subsets (chosen on the earlier period, frozen, then scored on the later period)

### Search limits

- Source: pool (oof-nhl-screen.json.gz bd4824b99f882a83, oof-nhl-assess.json.gz 69b587f71047f577).
- Models searched (10): hockey_elo, goals_poisson, hockey_logistic, hockey_gbt, hockey_logistic_xg, logit_l2:nhl_base, extra_trees:nhl_base, mlp:nhl_base, multinomial_l2:nhl_base, poisson_draw:nhl_base.
- Not searched: logit_l2:nhl_goalie, extra_trees:nhl_goalie, multinomial_l2:nhl_goalie, poisson_draw:nhl_goalie.
- Subset sizes [2, 3]; per-member thresholds [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]; 45 subsets of size 2, 120 subsets of size 3; 43365 rules searched; 24547 had ≥100 decided earlier picks.
- Earlier (search) 2017-10-04–2024-06-25; later (frozen check) 2024-10-04–2026-06-15. Best single model chosen on the earlier period: hockey_logistic_xg.
- A rule's record is an observed record on these games, not proof it will hold. Many rules were searched; the best earlier records are partly luck, which is why only the later period (frozen rules, no retuning) is evidence — and it is exploratory too.

### What the counts mean

Each list holds 20 frozen rules. "X of 20" counts RULES whose later win rate was above the comparator (the best single model's same number of most confident later picks, possibly different games). It is not a count of games won, and it is about this search only (these models, sizes, grid and minimum).

Descriptive later-period overlap: frozen rules whose later picks are exactly the same games (game IDs) with the same picked sides share one pick set. Rules in one set made the same picks, so their later win–loss–push outcomes, pending picks and win rate are identical; their mean probability and calibration gap can differ, because those come from each rule's own members. Matching win–loss totals on different games are different sets. Rules sharing a set are one later result, not independent confirmations. Counted only after the rules were frozen on the earlier period; it never removes, replaces, reorders or chooses a rule.

### Highest earlier win rate (ranked by its 95% lower bound)

Later win rate above the comparator: 0 of 20 rules, 0 distinct later pick sets. The 20 rules make 17 distinct later pick sets; shared: set 7 = rules 7, 8, 12 (57 picks); set 11 = rules 13, 16 (80 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | hockey_logistic ≥75% & extra_trees:nhl_base ≥70% & multinomial_l2:nhl_base ≥75% | 127 picks (1.4%) 109–18–0P, 85.8% [78.7, 90.8], gap +9.1 pts | 44 picks (1.6%) 38–6–0P, 86.4% [73.3, 93.6], gap +8.5 pts | 44 picks (1.6%) 38–6–0P, 86.4% [73.3, 93.6], gap +5.8 pts | 44 picks (1.6%) 38–6–0P, 86.4% [73.3, 93.6], gap +5.4 pts |
| 2 | 2 | hockey_logistic ≥75% & logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 144 picks (1.6%) 122–22–0P, 84.7% [78.0, 89.7], gap +7.7 pts | 58 picks (2.1%) 48–10–0P, 82.8% [71.1, 90.4], gap +4.8 pts | 58 picks (2.1%) 48–10–0P, 82.8% [71.1, 90.4], gap +3.1 pts | 58 picks (2.1%) 49–9–0P, 84.5% [73.1, 91.6], gap +4.2 pts |
| 3 | 3 | hockey_logistic ≥75% & extra_trees:nhl_base ≥70% & mlp:nhl_base ≥75% | 136 picks (1.5%) 115–21–0P, 84.6% [77.5, 89.7], gap +7.9 pts | 56 picks (2.0%) 44–12–0P, 78.6% [66.2, 87.3], gap +0.9 pts | 56 picks (2.0%) 44–12–0P, 78.6% [66.2, 87.3], gap -1.0 pts | 56 picks (2.0%) 48–8–0P, 85.7% [74.3, 92.6], gap +5.3 pts |
| 4 | 4 | hockey_logistic ≥75% & hockey_logistic_xg ≥75% & extra_trees:nhl_base ≥70% | 151 picks (1.7%) 127–24–0P, 84.1% [77.4, 89.1], gap +7.0 pts | 59 picks (2.1%) 48–11–0P, 81.4% [69.6, 89.3], gap +3.3 pts | 59 picks (2.1%) 48–11–0P, 81.4% [69.6, 89.3], gap +1.8 pts | 59 picks (2.1%) 50–9–0P, 84.7% [73.5, 91.8], gap +4.5 pts |
| 5 | 5 | hockey_gbt ≥80% & extra_trees:nhl_base ≥70% & multinomial_l2:nhl_base ≥75% | 114 picks (1.3%) 97–17–0P, 85.1% [77.4, 90.5], gap +6.3 pts | 39 picks (1.4%) 33–6–0P, 84.6% [70.3, 92.8], gap +4.6 pts | 39 picks (1.4%) 33–6–0P, 84.6% [70.3, 92.8], gap +3.9 pts | 39 picks (1.4%) 34–5–0P, 87.2% [73.3, 94.4], gap +6.0 pts |
| 6 | 6 | hockey_logistic ≥75% & mlp:nhl_base ≥75% & multinomial_l2:nhl_base ≥75% | 150 picks (1.7%) 126–24–0P, 84.0% [77.3, 89.0], gap +5.7 pts | 43 picks (1.5%) 36–7–0P, 83.7% [70.0, 91.9], gap +4.9 pts | 43 picks (1.5%) 36–7–0P, 83.7% [70.0, 91.9], gap +3.1 pts | 43 picks (1.5%) 38–5–0P, 88.4% [75.5, 94.9], gap +7.4 pts |
| 7 | 7 | hockey_logistic ≥75% & extra_trees:nhl_base ≥65% & mlp:nhl_base ≥75% | 160 picks (1.8%) 134–26–0P, 83.8% [77.3, 88.7], gap +7.4 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -0.4 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.4 pts | 57 picks (2.0%) 49–8–0P, 86.0% [74.7, 92.7], gap +5.6 pts |
| 8 | 7 | hockey_logistic ≥75% & mlp:nhl_base ≥75% | 164 picks (1.8%) 137–27–0P, 83.5% [77.1, 88.4], gap +5.4 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -1.3 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.4 pts | 57 picks (2.0%) 49–8–0P, 86.0% [74.7, 92.7], gap +5.6 pts |
| 9 | 8 | hockey_elo ≥65% & logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 204 picks (2.2%) 169–35–0P, 82.8% [77.1, 87.4], gap +7.7 pts | 79 picks (2.8%) 65–14–0P, 82.3% [72.4, 89.1], gap +6.4 pts | 79 picks (2.8%) 65–14–0P, 82.3% [72.4, 89.1], gap +3.3 pts | 79 picks (2.8%) 68–11–0P, 86.1% [76.8, 92.0], gap +6.6 pts |
| 10 | 9 | hockey_logistic ≥75% & hockey_logistic_xg ≥75% & mlp:nhl_base ≥75% | 163 picks (1.8%) 136–27–0P, 83.4% [77.0, 88.4], gap +4.6 pts | 55 picks (2.0%) 43–12–0P, 78.2% [65.6, 87.1], gap -0.8 pts | 55 picks (2.0%) 43–12–0P, 78.2% [65.6, 87.1], gap -1.6 pts | 55 picks (2.0%) 47–8–0P, 85.5% [73.8, 92.4], gap +5.0 pts |
| 11 | 10 | hockey_gbt ≥80% & mlp:nhl_base ≥75% & multinomial_l2:nhl_base ≥75% | 122 picks (1.3%) 103–19–0P, 84.4% [77.0, 89.8], gap +4.1 pts | 40 picks (1.4%) 33–7–0P, 82.5% [68.1, 91.3], gap +1.8 pts | 40 picks (1.4%) 33–7–0P, 82.5% [68.1, 91.3], gap +1.8 pts | 40 picks (1.4%) 35–5–0P, 87.5% [73.9, 94.5], gap +6.3 pts |
| 12 | 7 | hockey_logistic ≥75% & hockey_gbt ≥70% & mlp:nhl_base ≥75% | 162 picks (1.8%) 135–27–0P, 83.3% [76.8, 88.3], gap +3.6 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.9 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.4 pts | 57 picks (2.0%) 49–8–0P, 86.0% [74.7, 92.7], gap +5.6 pts |
| 13 | 11 | logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 206 picks (2.3%) 170–36–0P, 82.5% [76.8, 87.1], gap +6.8 pts | 80 picks (2.9%) 65–15–0P, 81.2% [71.3, 88.3], gap +4.4 pts | 80 picks (2.9%) 65–15–0P, 81.2% [71.3, 88.3], gap +2.4 pts | 80 picks (2.9%) 69–11–0P, 86.2% [77.0, 92.1], gap +6.8 pts |
| 14 | 12 | hockey_gbt ≥80% & logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 126 picks (1.4%) 106–20–0P, 84.1% [76.8, 89.5], gap +5.0 pts | 48 picks (1.7%) 38–10–0P, 79.2% [65.7, 88.3], gap -1.0 pts | 48 picks (1.7%) 38–10–0P, 79.2% [65.7, 88.3], gap -0.7 pts | 48 picks (1.7%) 42–6–0P, 87.5% [75.3, 94.1], gap +6.7 pts |
| 15 | 13 | hockey_logistic ≥75% & extra_trees:nhl_base ≥70% & poisson_draw:nhl_base ≥70% | 126 picks (1.4%) 106–20–0P, 84.1% [76.8, 89.5], gap +9.1 pts | 52 picks (1.9%) 40–12–0P, 76.9% [63.9, 86.3], gap +1.0 pts | 52 picks (1.9%) 40–12–0P, 76.9% [63.9, 86.3], gap -2.8 pts | 52 picks (1.9%) 44–8–0P, 84.6% [72.5, 92.0], gap +4.0 pts |
| 16 | 11 | hockey_logistic_xg ≥75% & logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 205 picks (2.3%) 169–36–0P, 82.4% [76.6, 87.0], gap +5.5 pts | 80 picks (2.9%) 65–15–0P, 81.2% [71.3, 88.3], gap +3.8 pts | 80 picks (2.9%) 65–15–0P, 81.2% [71.3, 88.3], gap +2.4 pts | 80 picks (2.9%) 69–11–0P, 86.2% [77.0, 92.1], gap +6.8 pts |
| 17 | 14 | hockey_logistic ≥75% & logit_l2:nhl_base ≥75% & mlp:nhl_base ≥75% | 160 picks (1.8%) 133–27–0P, 83.1% [76.6, 88.1], gap +4.4 pts | 54 picks (1.9%) 43–11–0P, 79.6% [67.1, 88.2], gap +0.7 pts | 54 picks (1.9%) 43–11–0P, 79.6% [67.1, 88.2], gap -0.2 pts | 54 picks (1.9%) 46–8–0P, 85.2% [73.4, 92.3], gap +4.7 pts |
| 18 | 15 | hockey_elo ≥70% & hockey_logistic ≥75% & mlp:nhl_base ≥75% | 155 picks (1.7%) 129–26–0P, 83.2% [76.6, 88.3], gap +5.8 pts | 55 picks (2.0%) 43–12–0P, 78.2% [65.6, 87.1], gap +0.6 pts | 55 picks (2.0%) 43–12–0P, 78.2% [65.6, 87.1], gap -1.5 pts | 55 picks (2.0%) 47–8–0P, 85.5% [73.8, 92.4], gap +5.0 pts |
| 19 | 16 | goals_poisson ≥65% & hockey_logistic ≥75% & mlp:nhl_base ≥75% | 150 picks (1.7%) 125–25–0P, 83.3% [76.6, 88.4], gap +7.4 pts | 56 picks (2.0%) 43–13–0P, 76.8% [64.2, 85.9], gap +0.6 pts | 56 picks (2.0%) 43–13–0P, 76.8% [64.2, 85.9], gap -2.8 pts | 56 picks (2.0%) 48–8–0P, 85.7% [74.3, 92.6], gap +5.3 pts |
| 20 | 17 | hockey_gbt ≥70% & logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 204 picks (2.2%) 168–36–0P, 82.4% [76.5, 87.0], gap +4.8 pts | 76 picks (2.7%) 61–15–0P, 80.3% [70.0, 87.7], gap +2.1 pts | 76 picks (2.7%) 61–15–0P, 80.3% [70.0, 87.7], gap +1.2 pts | 76 picks (2.7%) 65–11–0P, 85.5% [75.9, 91.7], gap +6.0 pts |

### Strongest earlier improvement over the best single model at the same number of picks

Later win rate above the comparator: 0 of 20 rules, 0 distinct later pick sets. The 20 rules make 16 distinct later pick sets; shared: set 5 = rules 5, 7, 13 (57 picks); set 6 = rules 6, 18 (40 picks); set 13 = rules 15, 16 (39 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | hockey_logistic ≥75% & logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 144 picks (1.6%) 122–22–0P, 84.7% [78.0, 89.7], gap +7.7 pts | 58 picks (2.1%) 48–10–0P, 82.8% [71.1, 90.4], gap +4.8 pts | 58 picks (2.1%) 48–10–0P, 82.8% [71.1, 90.4], gap +3.1 pts | 58 picks (2.1%) 49–9–0P, 84.5% [73.1, 91.6], gap +4.2 pts |
| 2 | 2 | hockey_logistic ≥75% & extra_trees:nhl_base ≥70% & multinomial_l2:nhl_base ≥75% | 127 picks (1.4%) 109–18–0P, 85.8% [78.7, 90.8], gap +9.1 pts | 44 picks (1.6%) 38–6–0P, 86.4% [73.3, 93.6], gap +8.5 pts | 44 picks (1.6%) 38–6–0P, 86.4% [73.3, 93.6], gap +5.8 pts | 44 picks (1.6%) 38–6–0P, 86.4% [73.3, 93.6], gap +5.4 pts |
| 3 | 3 | hockey_logistic ≥75% & mlp:nhl_base ≥75% & multinomial_l2:nhl_base ≥75% | 150 picks (1.7%) 126–24–0P, 84.0% [77.3, 89.0], gap +5.7 pts | 43 picks (1.5%) 36–7–0P, 83.7% [70.0, 91.9], gap +4.9 pts | 43 picks (1.5%) 36–7–0P, 83.7% [70.0, 91.9], gap +3.1 pts | 43 picks (1.5%) 38–5–0P, 88.4% [75.5, 94.9], gap +7.4 pts |
| 4 | 4 | hockey_logistic ≥75% & hockey_logistic_xg ≥75% & extra_trees:nhl_base ≥70% | 151 picks (1.7%) 127–24–0P, 84.1% [77.4, 89.1], gap +7.0 pts | 59 picks (2.1%) 48–11–0P, 81.4% [69.6, 89.3], gap +3.3 pts | 59 picks (2.1%) 48–11–0P, 81.4% [69.6, 89.3], gap +1.8 pts | 59 picks (2.1%) 50–9–0P, 84.7% [73.5, 91.8], gap +4.5 pts |
| 5 | 5 | hockey_logistic ≥75% & extra_trees:nhl_base ≥65% & mlp:nhl_base ≥75% | 160 picks (1.8%) 134–26–0P, 83.8% [77.3, 88.7], gap +7.4 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -0.4 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.4 pts | 57 picks (2.0%) 49–8–0P, 86.0% [74.7, 92.7], gap +5.6 pts |
| 6 | 6 | hockey_gbt ≥80% & mlp:nhl_base ≥75% & multinomial_l2:nhl_base ≥75% | 122 picks (1.3%) 103–19–0P, 84.4% [77.0, 89.8], gap +4.1 pts | 40 picks (1.4%) 33–7–0P, 82.5% [68.1, 91.3], gap +1.8 pts | 40 picks (1.4%) 33–7–0P, 82.5% [68.1, 91.3], gap +1.8 pts | 40 picks (1.4%) 35–5–0P, 87.5% [73.9, 94.5], gap +6.3 pts |
| 7 | 5 | hockey_logistic ≥75% & mlp:nhl_base ≥75% | 164 picks (1.8%) 137–27–0P, 83.5% [77.1, 88.4], gap +5.4 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -1.3 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.4 pts | 57 picks (2.0%) 49–8–0P, 86.0% [74.7, 92.7], gap +5.6 pts |
| 8 | 7 | hockey_logistic ≥75% & extra_trees:nhl_base ≥70% & mlp:nhl_base ≥75% | 136 picks (1.5%) 115–21–0P, 84.6% [77.5, 89.7], gap +7.9 pts | 56 picks (2.0%) 44–12–0P, 78.6% [66.2, 87.3], gap +0.9 pts | 56 picks (2.0%) 44–12–0P, 78.6% [66.2, 87.3], gap -1.0 pts | 56 picks (2.0%) 48–8–0P, 85.7% [74.3, 92.6], gap +5.3 pts |
| 9 | 8 | hockey_gbt ≥80% & logit_l2:nhl_base ≥75% | 140 picks (1.5%) 116–24–0P, 82.9% [75.8, 88.2], gap +0.9 pts | 49 picks (1.8%) 38–11–0P, 77.6% [64.1, 87.0], gap -4.5 pts | 49 picks (1.8%) 38–11–0P, 77.6% [64.1, 87.0], gap -2.4 pts | 49 picks (1.8%) 42–7–0P, 85.7% [73.3, 92.9], gap +5.0 pts |
| 10 | 9 | goals_poisson ≥65% & hockey_logistic ≥75% & mlp:nhl_base ≥75% | 150 picks (1.7%) 125–25–0P, 83.3% [76.6, 88.4], gap +7.4 pts | 56 picks (2.0%) 43–13–0P, 76.8% [64.2, 85.9], gap +0.6 pts | 56 picks (2.0%) 43–13–0P, 76.8% [64.2, 85.9], gap -2.8 pts | 56 picks (2.0%) 48–8–0P, 85.7% [74.3, 92.6], gap +5.3 pts |
| 11 | 10 | hockey_elo ≥75% & hockey_gbt ≥60% & logit_l2:nhl_base ≥75% | 105 picks (1.2%) 88–17–0P, 83.8% [75.6, 89.6], gap +4.0 pts | 40 picks (1.4%) 34–6–0P, 85.0% [70.9, 92.9], gap +5.3 pts | 40 picks (1.4%) 34–6–0P, 85.0% [70.9, 92.9], gap +4.9 pts | 40 picks (1.4%) 35–5–0P, 87.5% [73.9, 94.5], gap +6.3 pts |
| 12 | 11 | hockey_logistic ≥75% & logit_l2:nhl_base ≥75% & mlp:nhl_base ≥75% | 160 picks (1.8%) 133–27–0P, 83.1% [76.6, 88.1], gap +4.4 pts | 54 picks (1.9%) 43–11–0P, 79.6% [67.1, 88.2], gap +0.7 pts | 54 picks (1.9%) 43–11–0P, 79.6% [67.1, 88.2], gap -0.2 pts | 54 picks (1.9%) 46–8–0P, 85.2% [73.4, 92.3], gap +4.7 pts |
| 13 | 5 | hockey_logistic ≥75% & hockey_gbt ≥70% & mlp:nhl_base ≥75% | 162 picks (1.8%) 135–27–0P, 83.3% [76.8, 88.3], gap +3.6 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.9 pts | 57 picks (2.0%) 44–13–0P, 77.2% [64.8, 86.2], gap -2.4 pts | 57 picks (2.0%) 49–8–0P, 86.0% [74.7, 92.7], gap +5.6 pts |
| 14 | 12 | hockey_logistic ≥75% & hockey_logistic_xg ≥75% & mlp:nhl_base ≥75% | 163 picks (1.8%) 136–27–0P, 83.4% [77.0, 88.4], gap +4.6 pts | 55 picks (2.0%) 43–12–0P, 78.2% [65.6, 87.1], gap -0.8 pts | 55 picks (2.0%) 43–12–0P, 78.2% [65.6, 87.1], gap -1.6 pts | 55 picks (2.0%) 47–8–0P, 85.5% [73.8, 92.4], gap +5.0 pts |
| 15 | 13 | hockey_gbt ≥80% & extra_trees:nhl_base ≥70% & multinomial_l2:nhl_base ≥75% | 114 picks (1.3%) 97–17–0P, 85.1% [77.4, 90.5], gap +6.3 pts | 39 picks (1.4%) 33–6–0P, 84.6% [70.3, 92.8], gap +4.6 pts | 39 picks (1.4%) 33–6–0P, 84.6% [70.3, 92.8], gap +3.9 pts | 39 picks (1.4%) 34–5–0P, 87.2% [73.3, 94.4], gap +6.0 pts |
| 16 | 13 | hockey_elo ≥70% & hockey_gbt ≥80% & multinomial_l2:nhl_base ≥75% | 123 picks (1.4%) 103–20–0P, 83.7% [76.2, 89.2], gap +4.3 pts | 39 picks (1.4%) 33–6–0P, 84.6% [70.3, 92.8], gap +4.7 pts | 39 picks (1.4%) 33–6–0P, 84.6% [70.3, 92.8], gap +3.9 pts | 39 picks (1.4%) 34–5–0P, 87.2% [73.3, 94.4], gap +6.0 pts |
| 17 | 14 | hockey_logistic ≥75% & hockey_gbt ≥80% & mlp:nhl_base ≥75% | 123 picks (1.4%) 103–20–0P, 83.7% [76.2, 89.2], gap +3.3 pts | 49 picks (1.8%) 38–11–0P, 77.6% [64.1, 87.0], gap -3.1 pts | 49 picks (1.8%) 38–11–0P, 77.6% [64.1, 87.0], gap -2.3 pts | 49 picks (1.8%) 42–7–0P, 85.7% [73.3, 92.9], gap +5.0 pts |
| 18 | 6 | hockey_gbt ≥80% & extra_trees:nhl_base ≥65% & multinomial_l2:nhl_base ≥75% | 124 picks (1.4%) 104–20–0P, 83.9% [76.4, 89.3], gap +5.3 pts | 40 picks (1.4%) 33–7–0P, 82.5% [68.1, 91.3], gap +2.6 pts | 40 picks (1.4%) 33–7–0P, 82.5% [68.1, 91.3], gap +1.8 pts | 40 picks (1.4%) 35–5–0P, 87.5% [73.9, 94.5], gap +6.3 pts |
| 19 | 15 | hockey_gbt ≥80% & logit_l2:nhl_base ≥75% & extra_trees:nhl_base ≥70% | 126 picks (1.4%) 106–20–0P, 84.1% [76.8, 89.5], gap +5.0 pts | 48 picks (1.7%) 38–10–0P, 79.2% [65.7, 88.3], gap -1.0 pts | 48 picks (1.7%) 38–10–0P, 79.2% [65.7, 88.3], gap -0.7 pts | 48 picks (1.7%) 42–6–0P, 87.5% [75.3, 94.1], gap +6.7 pts |
| 20 | 16 | hockey_logistic ≥75% & extra_trees:nhl_base ≥70% & poisson_draw:nhl_base ≥70% | 126 picks (1.4%) 106–20–0P, 84.1% [76.8, 89.5], gap +9.1 pts | 52 picks (1.9%) 40–12–0P, 76.9% [63.9, 86.3], gap +1.0 pts | 52 picks (1.9%) 40–12–0P, 76.9% [63.9, 86.3], gap -2.8 pts | 52 picks (1.9%) 44–8–0P, 84.6% [72.5, 92.0], gap +4.0 pts |

