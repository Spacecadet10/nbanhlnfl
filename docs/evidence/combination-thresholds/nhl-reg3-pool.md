# Thresholds and exact subsets — NHL reg3 (pool)

Kind: historical out-of-sample (exploratory). Earlier 2017-10-04–2024-06-25 (9078 games); later 2024-10-04–2026-06-15 (2792 games). Later includes spent reserved-test seasons [2025, 2026].

Historical out-of-sample forecasts saved by the candidate pool (walk-forward, night-before cutoff). Every season here was already used to choose or screen models, including the spent reserved-test seasons: all results are exploratory, never an untouched test. No historical bookmaker odds: nothing here measures profit. Research only: nothing here changes the live forecasts or selects a model.

Win rate denominator: wins + losses (pushes and unsettled games excluded and shown separately).

Models compared: goals_poisson, goals_draw, goals_glm, multinomial_l2:nhl_base, poisson_draw:nhl_base.
Not stored: none.
Excluded: multinomial_l2:nhl_goalie (used the actual starting goalie, known only after the start (postgame input)); poisson_draw:nhl_goalie (used the actual starting goalie, known only after the start (postgame input)).
Missing earlier forecasts (those games are excluded from any rule or average needing the model): none.
Missing later forecasts (those games are excluded from any rule or average needing the model): none.

## Cumulative thresholds (later period)

| Source | Threshold | Record |
|---|---|---|
| goals_poisson | 33.3%+ | 2792 picks (100.0%) 1239–1553–0P, 44.4% [42.5, 46.2], gap -4.4 pts |
| goals_poisson | 35%+ | 2792 picks (100.0%) 1239–1553–0P, 44.4% [42.5, 46.2], gap -4.4 pts |
| goals_poisson | 40%+ | 2792 picks (100.0%) 1239–1553–0P, 44.4% [42.5, 46.2], gap -4.4 pts |
| goals_poisson | 45%+ | 1947 picks (69.7%) 900–1047–0P, 46.2% [44.0, 48.4], gap -4.9 pts |
| goals_poisson | 50%+ | 947 picks (33.9%) 478–469–0P, 50.5% [47.3, 53.7], gap -4.7 pts |
| goals_poisson | 55%+ | 395 picks (14.1%) 219–176–0P, 55.4% [50.5, 60.3], gap -3.8 pts |
| goals_poisson | 60%+ | 131 picks (4.7%) 74–57–0P, 56.5% [47.9, 64.7], gap -7.0 pts |
| goals_poisson | 65%+ | 37 picks (1.3%) 21–16–0P, 56.8% [40.9, 71.3], gap -10.5 pts |
| goals_poisson | 70%+ | 5 picks (0.2%) 3–2–0P, 60.0% [23.1, 88.2], gap -12.1 pts |
| goals_draw | 33.3%+ | 2792 picks (100.0%) 1239–1553–0P, 44.4% [42.5, 46.2], gap -1.1 pts |
| goals_draw | 35%+ | 2792 picks (100.0%) 1239–1553–0P, 44.4% [42.5, 46.2], gap -1.1 pts |
| goals_draw | 40%+ | 2468 picks (88.4%) 1106–1362–0P, 44.8% [42.9, 46.8], gap -1.5 pts |
| goals_draw | 45%+ | 1245 picks (44.6%) 603–642–0P, 48.4% [45.7, 51.2], gap -1.6 pts |
| goals_draw | 50%+ | 506 picks (18.1%) 275–231–0P, 54.3% [50.0, 58.6], gap +0.1 pts |
| goals_draw | 55%+ | 167 picks (6.0%) 97–70–0P, 58.1% [50.5, 65.3], gap -0.2 pts |
| goals_draw | 60%+ | 42 picks (1.5%) 23–19–0P, 54.8% [39.9, 68.8], gap -7.6 pts |
| goals_draw | 65%+ | 5 picks (0.2%) 3–2–0P, 60.0% [23.1, 88.2], gap -7.1 pts |
| goals_glm | 33.3%+ | 2792 picks (100.0%) 1253–1539–0P, 44.9% [43.0, 46.7], gap -3.3 pts |
| goals_glm | 35%+ | 2792 picks (100.0%) 1253–1539–0P, 44.9% [43.0, 46.7], gap -3.3 pts |
| goals_glm | 40%+ | 2792 picks (100.0%) 1253–1539–0P, 44.9% [43.0, 46.7], gap -3.3 pts |
| goals_glm | 45%+ | 1892 picks (67.8%) 888–1004–0P, 46.9% [44.7, 49.2], gap -3.7 pts |
| goals_glm | 50%+ | 867 picks (31.1%) 461–406–0P, 53.2% [49.8, 56.5], gap -1.3 pts |
| goals_glm | 55%+ | 305 picks (10.9%) 177–128–0P, 58.0% [52.4, 63.4], gap -0.5 pts |
| goals_glm | 60%+ | 80 picks (2.9%) 47–33–0P, 58.8% [47.8, 68.9], gap -4.1 pts |
| goals_glm | 65%+ | 15 picks (0.5%) 8–7–0P, 53.3% [30.1, 75.2], gap -12.8 pts |
| goals_glm | 70%+ | 1 picks (0.0%) 0–1–0P, 0.0% [0.0, 79.3], gap -72.7 pts |
| multinomial_l2:nhl_base | 33.3%+ | 2792 picks (100.0%) 1252–1540–0P, 44.8% [43.0, 46.7], gap -3.3 pts |
| multinomial_l2:nhl_base | 35%+ | 2792 picks (100.0%) 1252–1540–0P, 44.8% [43.0, 46.7], gap -3.3 pts |
| multinomial_l2:nhl_base | 40%+ | 2580 picks (92.4%) 1167–1413–0P, 45.2% [43.3, 47.2], gap -3.7 pts |
| multinomial_l2:nhl_base | 45%+ | 1653 picks (59.2%) 796–857–0P, 48.2% [45.8, 50.6], gap -4.4 pts |
| multinomial_l2:nhl_base | 50%+ | 961 picks (34.4%) 502–459–0P, 52.2% [49.1, 55.4], gap -4.0 pts |
| multinomial_l2:nhl_base | 55%+ | 482 picks (17.3%) 263–219–0P, 54.6% [50.1, 59.0], gap -5.7 pts |
| multinomial_l2:nhl_base | 60%+ | 207 picks (7.4%) 121–86–0P, 58.5% [51.6, 65.0], gap -5.9 pts |
| multinomial_l2:nhl_base | 65%+ | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts |
| multinomial_l2:nhl_base | 70%+ | 20 picks (0.7%) 14–6–0P, 70.0% [48.1, 85.5], gap -1.8 pts |
| multinomial_l2:nhl_base | 75%+ | 1 picks (0.0%) 0–1–0P, 0.0% [0.0, 79.3], gap -77.1 pts |
| poisson_draw:nhl_base | 33.3%+ | 2792 picks (100.0%) 1262–1530–0P, 45.2% [43.4, 47.1], gap -0.9 pts |
| poisson_draw:nhl_base | 35%+ | 2792 picks (100.0%) 1262–1530–0P, 45.2% [43.4, 47.1], gap -0.9 pts |
| poisson_draw:nhl_base | 40%+ | 2486 picks (89.0%) 1137–1349–0P, 45.7% [43.8, 47.7], gap -1.2 pts |
| poisson_draw:nhl_base | 45%+ | 1379 picks (49.4%) 678–701–0P, 49.2% [46.5, 51.8], gap -1.4 pts |
| poisson_draw:nhl_base | 50%+ | 610 picks (21.8%) 340–270–0P, 55.7% [51.8, 59.6], gap +1.0 pts |
| poisson_draw:nhl_base | 55%+ | 232 picks (8.3%) 136–96–0P, 58.6% [52.2, 64.8], gap -0.1 pts |
| poisson_draw:nhl_base | 60%+ | 66 picks (2.4%) 42–24–0P, 63.6% [51.6, 74.2], gap +0.9 pts |
| poisson_draw:nhl_base | 65%+ | 8 picks (0.3%) 6–2–0P, 75.0% [40.9, 92.9], gap +7.7 pts |
| poisson_draw:nhl_base | 70%+ | 1 picks (0.0%) 0–1–0P, 0.0% [0.0, 79.3], gap -72.0 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 33.3%+ | 2792 picks (100.0%) 1252–1540–0P, 44.8% [43.0, 46.7], gap -2.3 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 35%+ | 2792 picks (100.0%) 1252–1540–0P, 44.8% [43.0, 46.7], gap -2.3 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 40%+ | 2748 picks (98.4%) 1233–1515–0P, 44.9% [43.0, 46.7], gap -2.4 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 45%+ | 1588 picks (56.9%) 772–816–0P, 48.6% [46.2, 51.1], gap -2.1 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 50%+ | 726 picks (26.0%) 391–335–0P, 53.9% [50.2, 57.5], gap -0.9 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 55%+ | 287 picks (10.3%) 169–118–0P, 58.9% [53.1, 64.4], gap +0.2 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 60%+ | 79 picks (2.8%) 49–30–0P, 62.0% [51.0, 71.9], gap -0.9 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 65%+ | 15 picks (0.5%) 10–5–0P, 66.7% [41.7, 84.8], gap +0.1 pts |
| avg:goals_poisson+goals_draw+goals_glm+multinomial_l2:nhl_base+poisson_draw:nhl_base | 70%+ | 1 picks (0.0%) 0–1–0P, 0.0% [0.0, 79.3], gap -72.5 pts |

## Exact subsets (chosen on the earlier period, frozen, then scored on the later period)

### Search limits

- Source: pool (oof-nhl-screen.json.gz bd4824b99f882a83, oof-nhl-assess.json.gz 69b587f71047f577).
- Models searched (5): goals_poisson, goals_draw, goals_glm, multinomial_l2:nhl_base, poisson_draw:nhl_base.
- Not searched: multinomial_l2:nhl_goalie, poisson_draw:nhl_goalie.
- Subset sizes [2, 3]; per-member thresholds [0.333, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65]; 10 subsets of size 2, 10 subsets of size 3; 3920 rules searched; 2686 had ≥100 decided earlier picks.
- Earlier (search) 2017-10-04–2024-06-25; later (frozen check) 2024-10-04–2026-06-15. Best single model chosen on the earlier period: multinomial_l2:nhl_base.
- A rule's record is an observed record on these games, not proof it will hold. Many rules were searched; the best earlier records are partly luck, which is why only the later period (frozen rules, no retuning) is evidence — and it is exploratory too.

### What the counts mean

Each list holds 20 frozen rules. "X of 20" counts RULES whose later win rate was above the comparator (the best single model's same number of most confident later picks, possibly different games). It is not a count of games won, and it is about this search only (these models, sizes, grid and minimum).

Descriptive later-period overlap: frozen rules whose later picks are exactly the same games (game IDs) with the same picked sides share one pick set. Rules in one set made the same picks, so their later win–loss–push outcomes, pending picks and win rate are identical; their mean probability and calibration gap can differ, because those come from each rule's own members. Matching win–loss totals on different games are different sets. Rules sharing a set are one later result, not independent confirmations. Counted only after the rules were frozen on the earlier period; it never removes, replaces, reorders or chooses a rule.

### Highest earlier win rate (ranked by its 95% lower bound)

Later win rate above the comparator: 5 of 20 rules, 3 distinct later pick sets. The 20 rules make 13 distinct later pick sets; shared: set 2 = rules 2, 5, 15 (48 picks); set 5 = rules 6, 9, 14 (76 picks); set 8 = rules 10, 13, 19 (72 picks); set 10 = rules 12, 16 (79 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | goals_draw ≥55% & goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 137 picks (1.5%) 98–39–0P, 71.5% [63.5, 78.4], gap +7.2 pts | 37 picks (1.3%) 26–11–0P, 70.3% [54.2, 82.5], gap +5.5 pts | 37 picks (1.3%) 26–11–0P, 70.3% [54.2, 82.5], gap +0.9 pts | 37 picks (1.3%) 26–11–0P, 70.3% [54.2, 82.5], gap -0.1 pts |
| 2 | 2 | goals_glm ≥60% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥55% | 173 picks (1.9%) 121–52–0P, 69.9% [62.7, 76.3], gap +4.9 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +7.8 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +3.9 pts | 48 picks (1.7%) 34–14–0P, 70.8% [56.8, 81.8], gap +1.2 pts |
| 3 | 3 | goals_draw ≥55% & goals_glm ≥60% & poisson_draw:nhl_base ≥60% | 131 picks (1.4%) 93–38–0P, 71.0% [62.7, 78.1], gap +8.6 pts | 44 picks (1.6%) 27–17–0P, 61.4% [46.6, 74.3], gap -1.4 pts | 44 picks (1.6%) 27–17–0P, 61.4% [46.6, 74.3], gap -6.5 pts | 44 picks (1.6%) 31–13–0P, 70.5% [55.8, 81.8], gap +0.6 pts |
| 4 | 4 | goals_glm ≥60% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥60% | 140 picks (1.5%) 99–41–0P, 70.7% [62.7, 77.6], gap +5.1 pts | 39 picks (1.4%) 28–11–0P, 71.8% [56.2, 83.5], gap +6.0 pts | 39 picks (1.4%) 28–11–0P, 71.8% [56.2, 83.5], gap +2.2 pts | 39 picks (1.4%) 27–12–0P, 69.2% [53.6, 81.4], gap -1.0 pts |
| 5 | 2 | goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 174 picks (1.9%) 121–53–0P, 69.5% [62.3, 75.9], gap +3.3 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +6.6 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +3.9 pts | 48 picks (1.7%) 34–14–0P, 70.8% [56.8, 81.8], gap +1.2 pts |
| 6 | 5 | goals_glm ≥60% & poisson_draw:nhl_base ≥55% | 209 picks (2.3%) 144–65–0P, 68.9% [62.3, 74.8], gap +6.7 pts | 76 picks (2.7%) 45–31–0P, 59.2% [48.0, 69.6], gap -3.0 pts | 76 picks (2.7%) 45–31–0P, 59.2% [48.0, 69.6], gap -7.4 pts | 76 picks (2.7%) 50–26–0P, 65.8% [54.6, 75.5], gap -2.3 pts |
| 7 | 6 | goals_draw ≥55% & goals_glm ≥60% & multinomial_l2:nhl_base ≥55% | 168 picks (1.9%) 117–51–0P, 69.6% [62.3, 76.1], gap +6.0 pts | 61 picks (2.2%) 35–26–0P, 57.4% [44.9, 69.0], gap -6.0 pts | 61 picks (2.2%) 35–26–0P, 57.4% [44.9, 69.0], gap -9.1 pts | 61 picks (2.2%) 43–18–0P, 70.5% [58.1, 80.4], gap +1.6 pts |
| 8 | 7 | goals_glm ≥60% & poisson_draw:nhl_base ≥60% | 147 picks (1.6%) 103–44–0P, 70.1% [62.2, 76.9], gap +6.8 pts | 49 picks (1.8%) 32–17–0P, 65.3% [51.3, 77.1], gap +1.9 pts | 49 picks (1.8%) 32–17–0P, 65.3% [51.3, 77.1], gap -2.7 pts | 49 picks (1.8%) 34–15–0P, 69.4% [55.5, 80.5], gap -0.1 pts |
| 9 | 5 | goals_draw ≥50% & goals_glm ≥60% & poisson_draw:nhl_base ≥55% | 205 picks (2.3%) 141–64–0P, 68.8% [62.1, 74.7], gap +7.7 pts | 76 picks (2.7%) 45–31–0P, 59.2% [48.0, 69.6], gap -1.9 pts | 76 picks (2.7%) 45–31–0P, 59.2% [48.0, 69.6], gap -7.4 pts | 76 picks (2.7%) 50–26–0P, 65.8% [54.6, 75.5], gap -2.3 pts |
| 10 | 8 | goals_glm ≥60% & multinomial_l2:nhl_base ≥60% | 210 picks (2.3%) 144–66–0P, 68.6% [62.0, 74.5], gap +3.1 pts | 72 picks (2.6%) 44–28–0P, 61.1% [49.6, 71.5], gap -3.9 pts | 72 picks (2.6%) 44–28–0P, 61.1% [49.6, 71.5], gap -6.0 pts | 72 picks (2.6%) 49–23–0P, 68.1% [56.6, 77.7], gap -0.3 pts |
| 11 | 9 | goals_draw ≥55% & goals_glm ≥60% | 169 picks (1.9%) 117–52–0P, 69.2% [61.9, 75.7], gap +7.9 pts | 62 picks (2.2%) 36–26–0P, 58.1% [45.7, 69.5], gap -3.7 pts | 62 picks (2.2%) 36–26–0P, 58.1% [45.7, 69.5], gap -8.2 pts | 62 picks (2.2%) 43–19–0P, 69.4% [57.0, 79.4], gap +0.5 pts |
| 12 | 10 | goals_glm ≥60% & multinomial_l2:nhl_base ≥55% | 212 picks (2.3%) 145–67–0P, 68.4% [61.9, 74.3], gap +2.9 pts | 79 picks (2.8%) 46–33–0P, 58.2% [47.2, 68.5], gap -6.4 pts | 79 picks (2.8%) 46–33–0P, 58.2% [47.2, 68.5], gap -8.1 pts | 79 picks (2.8%) 51–28–0P, 64.6% [53.6, 74.2], gap -3.4 pts |
| 13 | 8 | goals_draw ≥50% & goals_glm ≥60% & multinomial_l2:nhl_base ≥60% | 206 picks (2.3%) 141–65–0P, 68.4% [61.8, 74.4], gap +5.2 pts | 72 picks (2.6%) 44–28–0P, 61.1% [49.6, 71.5], gap -1.9 pts | 72 picks (2.6%) 44–28–0P, 61.1% [49.6, 71.5], gap -6.0 pts | 72 picks (2.6%) 49–23–0P, 68.1% [56.6, 77.7], gap -0.3 pts |
| 14 | 5 | goals_poisson ≥55% & goals_glm ≥60% & poisson_draw:nhl_base ≥55% | 203 picks (2.2%) 139–64–0P, 68.5% [61.8, 74.5], gap +6.0 pts | 76 picks (2.7%) 45–31–0P, 59.2% [48.0, 69.6], gap -3.4 pts | 76 picks (2.7%) 45–31–0P, 59.2% [48.0, 69.6], gap -7.4 pts | 76 picks (2.7%) 50–26–0P, 65.8% [54.6, 75.5], gap -2.3 pts |
| 15 | 2 | goals_draw ≥50% & goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 171 picks (1.9%) 118–53–0P, 69.0% [61.7, 75.5], gap +5.3 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +9.0 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +3.9 pts | 48 picks (1.7%) 34–14–0P, 70.8% [56.8, 81.8], gap +1.2 pts |
| 16 | 10 | goals_draw ≥50% & goals_glm ≥60% & multinomial_l2:nhl_base ≥55% | 208 picks (2.3%) 142–66–0P, 68.3% [61.7, 74.2], gap +5.1 pts | 79 picks (2.8%) 46–33–0P, 58.2% [47.2, 68.5], gap -4.5 pts | 79 picks (2.8%) 46–33–0P, 58.2% [47.2, 68.5], gap -8.1 pts | 79 picks (2.8%) 51–28–0P, 64.6% [53.6, 74.2], gap -3.4 pts |
| 17 | 11 | goals_poisson ≥60% & goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 121 picks (1.3%) 85–36–0P, 70.2% [61.6, 77.7], gap +4.2 pts | 35 picks (1.3%) 24–11–0P, 68.6% [52.0, 81.4], gap +2.2 pts | 35 picks (1.3%) 24–11–0P, 68.6% [52.0, 81.4], gap -0.8 pts | 35 picks (1.3%) 24–11–0P, 68.6% [52.0, 81.4], gap -1.9 pts |
| 18 | 12 | goals_draw ≥33.3% & goals_glm ≥60% | 213 picks (2.3%) 145–68–0P, 68.1% [61.5, 74.0], gap +7.6 pts | 80 picks (2.9%) 47–33–0P, 58.8% [47.8, 68.9], gap -2.1 pts | 80 picks (2.9%) 47–33–0P, 58.8% [47.8, 68.9], gap -7.4 pts | 80 picks (2.9%) 51–29–0P, 63.7% [52.8, 73.4], gap -4.2 pts |
| 19 | 8 | goals_poisson ≥55% & goals_glm ≥60% & multinomial_l2:nhl_base ≥60% | 204 picks (2.2%) 139–65–0P, 68.1% [61.5, 74.1], gap +3.5 pts | 72 picks (2.6%) 44–28–0P, 61.1% [49.6, 71.5], gap -3.3 pts | 72 picks (2.6%) 44–28–0P, 61.1% [49.6, 71.5], gap -6.0 pts | 72 picks (2.6%) 49–23–0P, 68.1% [56.6, 77.7], gap -0.3 pts |
| 20 | 13 | goals_glm ≥55% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥55% | 310 picks (3.4%) 207–103–0P, 66.8% [61.4, 71.8], gap +3.2 pts | 68 picks (2.4%) 48–20–0P, 70.6% [58.9, 80.1], gap +6.4 pts | 68 picks (2.4%) 48–20–0P, 70.6% [58.9, 80.1], gap +2.1 pts | 68 picks (2.4%) 47–21–0P, 69.1% [57.4, 78.8], gap +0.6 pts |

### Strongest earlier improvement over the best single model at the same number of picks

Later win rate above the comparator: 6 of 20 rules, 3 distinct later pick sets. The 20 rules make 12 distinct later pick sets; shared: set 7 = rules 7, 12, 13, 18 (48 picks); set 9 = rules 9, 10, 14, 16, 17 (70 picks); set 12 = rules 19, 20 (200 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | goals_draw ≥55% & goals_glm ≥60% & poisson_draw:nhl_base ≥60% | 131 picks (1.4%) 93–38–0P, 71.0% [62.7, 78.1], gap +8.6 pts | 44 picks (1.6%) 27–17–0P, 61.4% [46.6, 74.3], gap -1.4 pts | 44 picks (1.6%) 27–17–0P, 61.4% [46.6, 74.3], gap -6.5 pts | 44 picks (1.6%) 31–13–0P, 70.5% [55.8, 81.8], gap +0.6 pts |
| 2 | 2 | goals_draw ≥55% & goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 137 picks (1.5%) 98–39–0P, 71.5% [63.5, 78.4], gap +7.2 pts | 37 picks (1.3%) 26–11–0P, 70.3% [54.2, 82.5], gap +5.5 pts | 37 picks (1.3%) 26–11–0P, 70.3% [54.2, 82.5], gap +0.9 pts | 37 picks (1.3%) 26–11–0P, 70.3% [54.2, 82.5], gap -0.1 pts |
| 3 | 3 | goals_glm ≥60% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥60% | 140 picks (1.5%) 99–41–0P, 70.7% [62.7, 77.6], gap +5.1 pts | 39 picks (1.4%) 28–11–0P, 71.8% [56.2, 83.5], gap +6.0 pts | 39 picks (1.4%) 28–11–0P, 71.8% [56.2, 83.5], gap +2.2 pts | 39 picks (1.4%) 27–12–0P, 69.2% [53.6, 81.4], gap -1.0 pts |
| 4 | 4 | goals_poisson ≥60% & goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 121 picks (1.3%) 85–36–0P, 70.2% [61.6, 77.7], gap +4.2 pts | 35 picks (1.3%) 24–11–0P, 68.6% [52.0, 81.4], gap +2.2 pts | 35 picks (1.3%) 24–11–0P, 68.6% [52.0, 81.4], gap -0.8 pts | 35 picks (1.3%) 24–11–0P, 68.6% [52.0, 81.4], gap -1.9 pts |
| 5 | 5 | goals_glm ≥60% & poisson_draw:nhl_base ≥60% | 147 picks (1.6%) 103–44–0P, 70.1% [62.2, 76.9], gap +6.8 pts | 49 picks (1.8%) 32–17–0P, 65.3% [51.3, 77.1], gap +1.9 pts | 49 picks (1.8%) 32–17–0P, 65.3% [51.3, 77.1], gap -2.7 pts | 49 picks (1.8%) 34–15–0P, 69.4% [55.5, 80.5], gap -0.1 pts |
| 6 | 6 | goals_draw ≥55% & goals_glm ≥60% & multinomial_l2:nhl_base ≥55% | 168 picks (1.9%) 117–51–0P, 69.6% [62.3, 76.1], gap +6.0 pts | 61 picks (2.2%) 35–26–0P, 57.4% [44.9, 69.0], gap -6.0 pts | 61 picks (2.2%) 35–26–0P, 57.4% [44.9, 69.0], gap -9.1 pts | 61 picks (2.2%) 43–18–0P, 70.5% [58.1, 80.4], gap +1.6 pts |
| 7 | 7 | goals_glm ≥60% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥55% | 173 picks (1.9%) 121–52–0P, 69.9% [62.7, 76.3], gap +4.9 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +7.8 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +3.9 pts | 48 picks (1.7%) 34–14–0P, 70.8% [56.8, 81.8], gap +1.2 pts |
| 8 | 8 | goals_poisson ≥60% & goals_glm ≥60% & poisson_draw:nhl_base ≥60% | 119 picks (1.3%) 83–36–0P, 69.7% [61.0, 77.3], gap +5.6 pts | 42 picks (1.5%) 25–17–0P, 59.5% [44.5, 73.0], gap -4.9 pts | 42 picks (1.5%) 25–17–0P, 59.5% [44.5, 73.0], gap -8.2 pts | 42 picks (1.5%) 29–13–0P, 69.0% [54.0, 80.9], gap -0.9 pts |
| 9 | 9 | goals_draw ≥45% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥55% | 336 picks (3.7%) 223–113–0P, 66.4% [61.2, 71.2], gap +4.3 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +6.0 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts |
| 10 | 9 | multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥55% | 338 picks (3.7%) 224–114–0P, 66.3% [61.1, 71.1], gap +1.5 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +3.4 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts |
| 11 | 10 | goals_draw ≥55% & goals_glm ≥60% | 169 picks (1.9%) 117–52–0P, 69.2% [61.9, 75.7], gap +7.9 pts | 62 picks (2.2%) 36–26–0P, 58.1% [45.7, 69.5], gap -3.7 pts | 62 picks (2.2%) 36–26–0P, 58.1% [45.7, 69.5], gap -8.2 pts | 62 picks (2.2%) 43–19–0P, 69.4% [57.0, 79.4], gap +0.5 pts |
| 12 | 7 | goals_draw ≥50% & goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 171 picks (1.9%) 118–53–0P, 69.0% [61.7, 75.5], gap +5.3 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +9.0 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +3.9 pts | 48 picks (1.7%) 34–14–0P, 70.8% [56.8, 81.8], gap +1.2 pts |
| 13 | 7 | goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 174 picks (1.9%) 121–53–0P, 69.5% [62.3, 75.9], gap +3.3 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +6.6 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +3.9 pts | 48 picks (1.7%) 34–14–0P, 70.8% [56.8, 81.8], gap +1.2 pts |
| 14 | 9 | goals_draw ≥45% & multinomial_l2:nhl_base ≥65% | 341 picks (3.8%) 225–116–0P, 66.0% [60.8, 70.8], gap +3.5 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +5.6 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts |
| 15 | 11 | goals_glm ≥55% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥55% | 310 picks (3.4%) 207–103–0P, 66.8% [61.4, 71.8], gap +3.2 pts | 68 picks (2.4%) 48–20–0P, 70.6% [58.9, 80.1], gap +6.4 pts | 68 picks (2.4%) 48–20–0P, 70.6% [58.9, 80.1], gap +2.1 pts | 68 picks (2.4%) 47–21–0P, 69.1% [57.4, 78.8], gap +0.6 pts |
| 16 | 9 | goals_poisson ≥50% & multinomial_l2:nhl_base ≥65% & poisson_draw:nhl_base ≥55% | 334 picks (3.7%) 221–113–0P, 66.2% [60.9, 71.0], gap +2.7 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +4.6 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts |
| 17 | 9 | goals_poisson ≥50% & multinomial_l2:nhl_base ≥65% | 338 picks (3.7%) 222–116–0P, 65.7% [60.5, 70.5], gap +1.1 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +3.5 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts | 70 picks (2.5%) 48–22–0P, 68.6% [57.0, 78.2], gap +0.2 pts |
| 18 | 7 | goals_poisson ≥55% & goals_glm ≥60% & multinomial_l2:nhl_base ≥65% | 169 picks (1.9%) 116–53–0P, 68.6% [61.3, 75.2], gap +3.5 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +7.6 pts | 48 picks (1.7%) 35–13–0P, 72.9% [59.0, 83.4], gap +3.9 pts | 48 picks (1.7%) 34–14–0P, 70.8% [56.8, 81.8], gap +1.2 pts |
| 19 | 12 | goals_draw ≥45% & multinomial_l2:nhl_base ≥60% | 882 picks (9.7%) 562–320–0P, 63.7% [60.5, 66.8], gap +4.2 pts | 200 picks (7.2%) 117–83–0P, 58.5% [51.6, 65.1], gap -1.1 pts | 200 picks (7.2%) 117–83–0P, 58.5% [51.6, 65.1], gap -5.9 pts | 200 picks (7.2%) 118–82–0P, 59.0% [52.1, 65.6], gap -5.5 pts |
| 20 | 12 | goals_draw ≥45% & multinomial_l2:nhl_base ≥60% & poisson_draw:nhl_base ≥50% | 877 picks (9.7%) 558–319–0P, 63.6% [60.4, 66.7], gap +4.7 pts | 200 picks (7.2%) 117–83–0P, 58.5% [51.6, 65.1], gap -0.8 pts | 200 picks (7.2%) 117–83–0P, 58.5% [51.6, 65.1], gap -5.9 pts | 200 picks (7.2%) 118–82–0P, 59.0% [52.1, 65.6], gap -5.5 pts |

