# Thresholds and exact subsets — NBA spread (pool)

Kind: historical out-of-sample (exploratory). Earlier 2021-10-19–2024-06-18 (3962 games); later 2024-10-22–2026-06-14 (2643 games). Later includes spent reserved-test seasons [2025, 2026].

Historical out-of-sample forecasts saved by the candidate pool (walk-forward, night-before cutoff). Every season here was already used to choose or screen models, including the spent reserved-test seasons: all results are exploratory, never an untouched test. No historical bookmaker odds: nothing here measures profit. Research only: nothing here changes the live forecasts or selects a model. Line markets are settled at the primary model's own line (no historical bookmaker lines exist).

Win rate denominator: wins + losses (pushes and unsettled games excluded and shown separately).

Models compared: points_normal, ridge_points, gbt_points, ridge_points@weekly, gbt_points@weekly, ridge:nba_base, elastic_net:nba_base, extra_trees:nba_base:normal, mlp:nba_base:normal, ngboost:nba_base, hetero_linear:nba_base.
Not stored: none.
Excluded: none.
Missing earlier forecasts (those games are excluded from any rule or average needing the model): none.
Missing later forecasts (those games are excluded from any rule or average needing the model): none.

## Cumulative thresholds (later period)

| Source | Threshold | Record |
|---|---|---|
| points_normal | 50%+ | 2643 picks (100.0%) 1325–1318–0P, 50.1% [48.2, 52.0], gap -0.6 pts |
| ridge_points | 50%+ | 2643 picks (100.0%) 1425–1218–0P, 53.9% [52.0, 55.8], gap -1.1 pts |
| ridge_points | 55%+ | 1143 picks (43.2%) 643–500–0P, 56.3% [53.4, 59.1], gap -2.4 pts |
| ridge_points | 60%+ | 303 picks (11.5%) 183–120–0P, 60.4% [54.8, 65.7], gap -2.4 pts |
| ridge_points | 65%+ | 54 picks (2.0%) 35–19–0P, 64.8% [51.5, 76.2], gap -2.5 pts |
| ridge_points | 70%+ | 6 picks (0.2%) 5–1–0P, 83.3% [43.6, 97.0], gap +11.9 pts |
| gbt_points | 50%+ | 2643 picks (100.0%) 1404–1239–0P, 53.1% [51.2, 55.0], gap -3.1 pts |
| gbt_points | 55%+ | 1348 picks (51.0%) 745–603–0P, 55.3% [52.6, 57.9], gap -4.6 pts |
| gbt_points | 60%+ | 511 picks (19.3%) 285–226–0P, 55.8% [51.4, 60.0], gap -8.4 pts |
| gbt_points | 65%+ | 147 picks (5.6%) 88–59–0P, 59.9% [51.8, 67.4], gap -9.3 pts |
| gbt_points | 70%+ | 45 picks (1.7%) 22–23–0P, 48.9% [35.0, 63.0], gap -25.5 pts |
| gbt_points | 75%+ | 18 picks (0.7%) 11–7–0P, 61.1% [38.6, 79.7], gap -17.0 pts |
| gbt_points | 80%+ | 2 picks (0.1%) 1–1–0P, 50.0% [9.5, 90.5], gap -32.8 pts |
| gbt_points | 85%+ | 1 picks (0.0%) 0–1–0P, 0.0% [0.0, 79.3], gap -85.2 pts |
| ridge_points@weekly | 50%+ | 2643 picks (100.0%) 1427–1216–0P, 54.0% [52.1, 55.9], gap -0.9 pts |
| ridge_points@weekly | 55%+ | 1118 picks (42.3%) 631–487–0P, 56.4% [53.5, 59.3], gap -2.0 pts |
| ridge_points@weekly | 60%+ | 271 picks (10.3%) 161–110–0P, 59.4% [53.5, 65.1], gap -3.3 pts |
| ridge_points@weekly | 65%+ | 48 picks (1.8%) 30–18–0P, 62.5% [48.4, 74.8], gap -4.6 pts |
| ridge_points@weekly | 70%+ | 4 picks (0.2%) 3–1–0P, 75.0% [30.1, 95.4], gap +4.0 pts |
| gbt_points@weekly | 50%+ | 2643 picks (100.0%) 1404–1239–0P, 53.1% [51.2, 55.0], gap -2.8 pts |
| gbt_points@weekly | 55%+ | 1279 picks (48.4%) 693–586–0P, 54.2% [51.4, 56.9], gap -5.4 pts |
| gbt_points@weekly | 60%+ | 461 picks (17.4%) 259–202–0P, 56.2% [51.6, 60.6], gap -7.8 pts |
| gbt_points@weekly | 65%+ | 129 picks (4.9%) 78–51–0P, 60.5% [51.8, 68.5], gap -8.2 pts |
| gbt_points@weekly | 70%+ | 33 picks (1.2%) 19–14–0P, 57.6% [40.8, 72.8], gap -16.0 pts |
| gbt_points@weekly | 75%+ | 7 picks (0.3%) 3–4–0P, 42.9% [15.8, 75.0], gap -34.7 pts |
| gbt_points@weekly | 80%+ | 1 picks (0.0%) 0–1–0P, 0.0% [0.0, 79.3], gap -81.0 pts |
| ridge:nba_base | 50%+ | 2643 picks (100.0%) 1425–1218–0P, 53.9% [52.0, 55.8], gap -1.1 pts |
| ridge:nba_base | 55%+ | 1143 picks (43.2%) 643–500–0P, 56.3% [53.4, 59.1], gap -2.4 pts |
| ridge:nba_base | 60%+ | 303 picks (11.5%) 183–120–0P, 60.4% [54.8, 65.7], gap -2.4 pts |
| ridge:nba_base | 65%+ | 54 picks (2.0%) 35–19–0P, 64.8% [51.5, 76.2], gap -2.5 pts |
| ridge:nba_base | 70%+ | 6 picks (0.2%) 5–1–0P, 83.3% [43.6, 97.0], gap +11.9 pts |
| elastic_net:nba_base | 50%+ | 2643 picks (100.0%) 1427–1216–0P, 54.0% [52.1, 55.9], gap -0.9 pts |
| elastic_net:nba_base | 55%+ | 1115 picks (42.2%) 620–495–0P, 55.6% [52.7, 58.5], gap -2.8 pts |
| elastic_net:nba_base | 60%+ | 261 picks (9.9%) 158–103–0P, 60.5% [54.5, 66.3], gap -2.2 pts |
| elastic_net:nba_base | 65%+ | 49 picks (1.9%) 30–19–0P, 61.2% [47.2, 73.6], gap -5.9 pts |
| elastic_net:nba_base | 70%+ | 5 picks (0.2%) 4–1–0P, 80.0% [37.6, 96.4], gap +8.9 pts |
| extra_trees:nba_base:normal | 50%+ | 2643 picks (100.0%) 1421–1222–0P, 53.8% [51.9, 55.7], gap -1.5 pts |
| extra_trees:nba_base:normal | 55%+ | 1113 picks (42.1%) 622–491–0P, 55.9% [53.0, 58.8], gap -3.2 pts |
| extra_trees:nba_base:normal | 60%+ | 317 picks (12.0%) 179–138–0P, 56.5% [51.0, 61.8], gap -7.7 pts |
| extra_trees:nba_base:normal | 65%+ | 87 picks (3.3%) 52–35–0P, 59.8% [49.3, 69.4], gap -10.1 pts |
| extra_trees:nba_base:normal | 70%+ | 30 picks (1.1%) 16–14–0P, 53.3% [36.1, 69.8], gap -21.6 pts |
| extra_trees:nba_base:normal | 75%+ | 9 picks (0.3%) 5–4–0P, 55.6% [26.7, 81.1], gap -25.3 pts |
| extra_trees:nba_base:normal | 80%+ | 5 picks (0.2%) 2–3–0P, 40.0% [11.8, 76.9], gap -43.3 pts |
| mlp:nba_base:normal | 50%+ | 2643 picks (100.0%) 1418–1225–0P, 53.7% [51.7, 55.5], gap -2.3 pts |
| mlp:nba_base:normal | 55%+ | 1343 picks (50.8%) 742–601–0P, 55.2% [52.6, 57.9], gap -4.2 pts |
| mlp:nba_base:normal | 60%+ | 487 picks (18.4%) 276–211–0P, 56.7% [52.2, 61.0], gap -6.9 pts |
| mlp:nba_base:normal | 65%+ | 130 picks (4.9%) 75–55–0P, 57.7% [49.1, 65.8], gap -10.1 pts |
| mlp:nba_base:normal | 70%+ | 22 picks (0.8%) 14–8–0P, 63.6% [43.0, 80.3], gap -9.0 pts |
| mlp:nba_base:normal | 75%+ | 3 picks (0.1%) 3–0–0P, 100.0% [43.9, 100.0], gap +23.6 pts |
| ngboost:nba_base | 50%+ | 2643 picks (100.0%) 1389–1254–0P, 52.6% [50.6, 54.5], gap -3.1 pts |
| ngboost:nba_base | 55%+ | 1172 picks (44.3%) 638–534–0P, 54.4% [51.6, 57.3], gap -5.3 pts |
| ngboost:nba_base | 60%+ | 398 picks (15.1%) 222–176–0P, 55.8% [50.9, 60.6], gap -8.8 pts |
| ngboost:nba_base | 65%+ | 131 picks (5.0%) 75–56–0P, 57.3% [48.7, 65.4], gap -12.8 pts |
| ngboost:nba_base | 70%+ | 46 picks (1.7%) 30–16–0P, 65.2% [50.8, 77.3], gap -10.2 pts |
| ngboost:nba_base | 75%+ | 21 picks (0.8%) 12–9–0P, 57.1% [36.5, 75.5], gap -22.3 pts |
| ngboost:nba_base | 80%+ | 7 picks (0.3%) 3–4–0P, 42.9% [15.8, 75.0], gap -40.3 pts |
| ngboost:nba_base | 85%+ | 1 picks (0.0%) 0–1–0P, 0.0% [0.0, 79.3], gap -85.8 pts |
| hetero_linear:nba_base | 50%+ | 2643 picks (100.0%) 1429–1214–0P, 54.1% [52.2, 56.0], gap -0.8 pts |
| hetero_linear:nba_base | 55%+ | 1108 picks (41.9%) 619–489–0P, 55.9% [52.9, 58.8], gap -2.5 pts |
| hetero_linear:nba_base | 60%+ | 264 picks (10.0%) 163–101–0P, 61.7% [55.8, 67.4], gap -0.9 pts |
| hetero_linear:nba_base | 65%+ | 41 picks (1.6%) 23–18–0P, 56.1% [41.0, 70.1], gap -10.9 pts |
| hetero_linear:nba_base | 70%+ | 2 picks (0.1%) 2–0–0P, 100.0% [34.2, 100.0], gap +28.0 pts |
| avg:points_normal+ridge_points+gbt_points+ridge_points@weekly+gbt_points@weekly+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 50%+ | 2643 picks (100.0%) 1411–1232–0P, 53.4% [51.5, 55.3], gap -1.2 pts |
| avg:points_normal+ridge_points+gbt_points+ridge_points@weekly+gbt_points@weekly+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 55%+ | 998 picks (37.8%) 564–434–0P, 56.5% [53.4, 59.6], gap -1.7 pts |
| avg:points_normal+ridge_points+gbt_points+ridge_points@weekly+gbt_points@weekly+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 60%+ | 198 picks (7.5%) 116–82–0P, 58.6% [51.6, 65.2], gap -4.1 pts |
| avg:points_normal+ridge_points+gbt_points+ridge_points@weekly+gbt_points@weekly+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 65%+ | 33 picks (1.2%) 20–13–0P, 60.6% [43.7, 75.3], gap -6.3 pts |
| avg:points_normal+ridge_points+gbt_points+ridge_points@weekly+gbt_points@weekly+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 70%+ | 2 picks (0.1%) 2–0–0P, 100.0% [34.2, 100.0], gap +29.5 pts |

## Exact subsets (chosen on the earlier period, frozen, then scored on the later period)

### Search limits

- Source: pool (oof-nba-screen.json.gz 70699e2fa2f175be, oof-nba-assess.json.gz fdf536466fb40ca4).
- Models searched (11): points_normal, ridge_points, gbt_points, ridge_points@weekly, gbt_points@weekly, ridge:nba_base, elastic_net:nba_base, extra_trees:nba_base:normal, mlp:nba_base:normal, ngboost:nba_base, hetero_linear:nba_base.
- Not searched: none.
- Subset sizes [2, 3]; per-member thresholds [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]; 55 subsets of size 2, 165 subsets of size 3; 59290 rules searched; 5119 had ≥100 decided earlier picks.
- Earlier (search) 2021-10-19–2024-06-18; later (frozen check) 2024-10-22–2026-06-14. Best single model chosen on the earlier period: ridge_points@weekly.
- A rule's record is an observed record on these games, not proof it will hold. Many rules were searched; the best earlier records are partly luck, which is why only the later period (frozen rules, no retuning) is evidence — and it is exploratory too.

### What the counts mean

Each list holds 20 frozen rules. "X of 20" counts RULES whose later win rate was above the comparator (the best single model's same number of most confident later picks, possibly different games). It is not a count of games won, and it is about this search only (these models, sizes, grid and minimum).

Descriptive later-period overlap: frozen rules whose later picks are exactly the same games (game IDs) with the same picked sides share one pick set. Rules in one set made the same picks, so their later win–loss–push outcomes, pending picks and win rate are identical; their mean probability and calibration gap can differ, because those come from each rule's own members. Matching win–loss totals on different games are different sets. Rules sharing a set are one later result, not independent confirmations. Counted only after the rules were frozen on the earlier period; it never removes, replaces, reorders or chooses a rule.

### Highest earlier win rate (ranked by its 95% lower bound)

Later win rate above the comparator: 1 of 20 rules, 1 distinct later pick set. The 20 rules make 17 distinct later pick sets; shared: set 11 = rules 11, 14, 15, 20 (98 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | ridge:nba_base ≥55% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 101 picks (2.5%) 75–26–0P, 74.3% [65.0, 81.8], gap +8.2 pts | 91 picks (3.4%) 50–41–0P, 54.9% [44.7, 64.8], gap -11.6 pts | 91 picks (3.4%) 50–41–0P, 54.9% [44.7, 64.8], gap -6.5 pts | 91 picks (3.4%) 53–38–0P, 58.2% [48.0, 67.8], gap -7.3 pts |
| 2 | 2 | elastic_net:nba_base ≥55% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 109 picks (2.8%) 79–30–0P, 72.5% [63.4, 80.0], gap +6.5 pts | 98 picks (3.7%) 55–43–0P, 56.1% [46.3, 65.5], gap -10.4 pts | 98 picks (3.7%) 55–43–0P, 56.1% [46.3, 65.5], gap -4.8 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 3 | 3 | extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% & hetero_linear:nba_base ≥55% | 111 picks (2.8%) 80–31–0P, 72.1% [63.1, 79.6], gap +6.1 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -11.7 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -6.4 pts | 95 picks (3.6%) 55–40–0P, 57.9% [47.8, 67.3], gap -7.5 pts |
| 4 | 4 | gbt_points@weekly ≥55% & ridge:nba_base ≥55% & ngboost:nba_base ≥65% | 113 picks (2.9%) 81–32–0P, 71.7% [62.8, 79.2], gap +6.3 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -11.2 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -6.7 pts | 95 picks (3.6%) 55–40–0P, 57.9% [47.8, 67.3], gap -7.5 pts |
| 5 | 5 | gbt_points@weekly ≥55% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 136 picks (3.4%) 96–40–0P, 70.6% [62.4, 77.6], gap +2.9 pts | 118 picks (4.5%) 70–48–0P, 59.3% [50.3, 67.8], gap -8.4 pts | 118 picks (4.5%) 66–52–0P, 55.9% [46.9, 64.6], gap -3.5 pts | 118 picks (4.5%) 69–49–0P, 58.5% [49.5, 67.0], gap -6.4 pts |
| 6 | 6 | ridge_points ≥60% & ridge_points@weekly ≥60% & gbt_points@weekly ≥60% | 147 picks (3.7%) 103–44–0P, 70.1% [62.2, 76.9], gap +6.2 pts | 162 picks (6.1%) 97–65–0P, 59.9% [52.2, 67.1], gap -4.3 pts | 162 picks (6.1%) 97–65–0P, 59.9% [52.2, 67.1], gap -3.5 pts | 162 picks (6.1%) 101–61–0P, 62.3% [54.7, 69.4], gap -1.7 pts |
| 7 | 7 | ridge_points@weekly ≥55% & gbt_points@weekly ≥55% & ngboost:nba_base ≥65% | 123 picks (3.1%) 87–36–0P, 70.7% [62.2, 78.0], gap +5.5 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -10.1 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -5.6 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 8 | 8 | ridge_points ≥55% & ridge_points@weekly ≥55% & ngboost:nba_base ≥65% | 120 picks (3.0%) 85–35–0P, 70.8% [62.2, 78.2], gap +7.0 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -8.7 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -5.7 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 9 | 9 | ridge_points@weekly ≥55% & elastic_net:nba_base ≥55% & ngboost:nba_base ≥65% | 125 picks (3.2%) 88–37–0P, 70.4% [61.9, 77.7], gap +6.6 pts | 100 picks (3.8%) 56–44–0P, 56.0% [46.2, 65.3], gap -8.3 pts | 100 picks (3.8%) 56–44–0P, 56.0% [46.2, 65.3], gap -5.2 pts | 100 picks (3.8%) 59–41–0P, 59.0% [49.2, 68.1], gap -6.3 pts |
| 10 | 10 | ridge_points@weekly ≥55% & ngboost:nba_base ≥65% & hetero_linear:nba_base ≥55% | 125 picks (3.2%) 88–37–0P, 70.4% [61.9, 77.7], gap +6.6 pts | 99 picks (3.7%) 55–44–0P, 55.6% [45.7, 65.0], gap -8.6 pts | 99 picks (3.7%) 55–44–0P, 55.6% [45.7, 65.0], gap -5.7 pts | 99 picks (3.7%) 58–41–0P, 58.6% [48.7, 67.8], gap -6.7 pts |
| 11 | 11 | ridge:nba_base ≥55% & ngboost:nba_base ≥65% & hetero_linear:nba_base ≥55% | 122 picks (3.1%) 86–36–0P, 70.5% [61.9, 77.9], gap +6.7 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -9.3 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -6.2 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 12 | 12 | ridge_points ≥60% & ridge_points@weekly ≥60% & extra_trees:nba_base:normal ≥60% | 133 picks (3.4%) 93–40–0P, 69.9% [61.7, 77.1], gap +6.3 pts | 150 picks (5.7%) 86–64–0P, 57.3% [49.3, 65.0], gap -6.7 pts | 150 picks (5.7%) 86–64–0P, 57.3% [49.3, 65.0], gap -6.4 pts | 150 picks (5.7%) 92–58–0P, 61.3% [53.3, 68.8], gap -3.0 pts |
| 13 | 13 | gbt_points ≥50% & ridge:nba_base ≥55% & extra_trees:nba_base:normal ≥60% | 223 picks (5.6%) 151–72–0P, 67.7% [61.3, 73.5], gap +4.5 pts | 251 picks (9.5%) 141–110–0P, 56.2% [50.0, 62.2], gap -6.9 pts | 251 picks (9.5%) 141–110–0P, 56.2% [50.0, 62.2], gap -5.1 pts | 251 picks (9.5%) 148–103–0P, 59.0% [52.8, 64.9], gap -3.9 pts |
| 14 | 11 | gbt_points ≥50% & ridge:nba_base ≥55% & ngboost:nba_base ≥65% | 123 picks (3.1%) 86–37–0P, 69.9% [61.3, 77.3], gap +4.1 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -11.1 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -6.2 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 15 | 11 | ridge:nba_base ≥55% & elastic_net:nba_base ≥55% & ngboost:nba_base ≥65% | 123 picks (3.1%) 86–37–0P, 69.9% [61.3, 77.3], gap +6.2 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -9.5 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -6.2 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 16 | 14 | gbt_points ≥55% & ridge:nba_base ≥55% & ngboost:nba_base ≥65% | 120 picks (3.0%) 84–36–0P, 70.0% [61.3, 77.5], gap +4.0 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -10.7 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -5.7 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 17 | 15 | gbt_points@weekly ≥55% & elastic_net:nba_base ≥55% & ngboost:nba_base ≥65% | 120 picks (3.0%) 84–36–0P, 70.0% [61.3, 77.5], gap +4.8 pts | 102 picks (3.9%) 57–45–0P, 55.9% [46.2, 65.1], gap -10.0 pts | 102 picks (3.9%) 57–45–0P, 55.9% [46.2, 65.1], gap -5.1 pts | 102 picks (3.9%) 61–41–0P, 59.8% [50.1, 68.8], gap -5.5 pts |
| 18 | 16 | gbt_points ≥50% & ridge_points@weekly ≥60% & gbt_points@weekly ≥60% | 177 picks (4.5%) 121–56–0P, 68.4% [61.2, 74.8], gap +3.7 pts | 166 picks (6.3%) 99–67–0P, 59.6% [52.0, 66.8], gap -4.9 pts | 166 picks (6.3%) 99–67–0P, 59.6% [52.0, 66.8], gap -3.7 pts | 166 picks (6.3%) 103–63–0P, 62.0% [54.5, 69.1], gap -2.0 pts |
| 19 | 17 | gbt_points@weekly ≥60% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 108 picks (2.7%) 76–32–0P, 70.4% [61.2, 78.2], gap +1.8 pts | 97 picks (3.7%) 56–41–0P, 57.7% [47.8, 67.1], gap -11.0 pts | 97 picks (3.7%) 53–44–0P, 54.6% [44.7, 64.2], gap -5.5 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 20 | 11 | ridge:nba_base ≥55% & ngboost:nba_base ≥65% | 125 picks (3.2%) 87–38–0P, 69.6% [61.1, 77.0], gap +4.3 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -10.8 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -6.2 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |

### Strongest earlier improvement over the best single model at the same number of picks

Later win rate above the comparator: 2 of 20 rules, 2 distinct later pick sets. The 20 rules make 18 distinct later pick sets; shared: set 8 = rules 8, 14, 15 (98 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | elastic_net:nba_base ≥55% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 109 picks (2.8%) 79–30–0P, 72.5% [63.4, 80.0], gap +6.5 pts | 98 picks (3.7%) 55–43–0P, 56.1% [46.3, 65.5], gap -10.4 pts | 98 picks (3.7%) 55–43–0P, 56.1% [46.3, 65.5], gap -4.8 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 2 | 2 | ridge:nba_base ≥55% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 101 picks (2.5%) 75–26–0P, 74.3% [65.0, 81.8], gap +8.2 pts | 91 picks (3.4%) 50–41–0P, 54.9% [44.7, 64.8], gap -11.6 pts | 91 picks (3.4%) 50–41–0P, 54.9% [44.7, 64.8], gap -6.5 pts | 91 picks (3.4%) 53–38–0P, 58.2% [48.0, 67.8], gap -7.3 pts |
| 3 | 3 | extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% & hetero_linear:nba_base ≥55% | 111 picks (2.8%) 80–31–0P, 72.1% [63.1, 79.6], gap +6.1 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -11.7 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -6.4 pts | 95 picks (3.6%) 55–40–0P, 57.9% [47.8, 67.3], gap -7.5 pts |
| 4 | 4 | ridge_points ≥55% & ridge_points@weekly ≥55% & ngboost:nba_base ≥65% | 120 picks (3.0%) 85–35–0P, 70.8% [62.2, 78.2], gap +7.0 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -8.7 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -5.7 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 5 | 5 | gbt_points@weekly ≥55% & ridge:nba_base ≥55% & ngboost:nba_base ≥65% | 113 picks (2.9%) 81–32–0P, 71.7% [62.8, 79.2], gap +6.3 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -11.2 pts | 95 picks (3.6%) 52–43–0P, 54.7% [44.7, 64.4], gap -6.7 pts | 95 picks (3.6%) 55–40–0P, 57.9% [47.8, 67.3], gap -7.5 pts |
| 6 | 6 | gbt_points ≥55% & ridge:nba_base ≥55% & ngboost:nba_base ≥65% | 120 picks (3.0%) 84–36–0P, 70.0% [61.3, 77.5], gap +4.0 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -10.7 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -5.7 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 7 | 7 | gbt_points@weekly ≥55% & elastic_net:nba_base ≥55% & ngboost:nba_base ≥65% | 120 picks (3.0%) 84–36–0P, 70.0% [61.3, 77.5], gap +4.8 pts | 102 picks (3.9%) 57–45–0P, 55.9% [46.2, 65.1], gap -10.0 pts | 102 picks (3.9%) 57–45–0P, 55.9% [46.2, 65.1], gap -5.1 pts | 102 picks (3.9%) 61–41–0P, 59.8% [50.1, 68.8], gap -5.5 pts |
| 8 | 8 | ridge:nba_base ≥55% & ngboost:nba_base ≥65% & hetero_linear:nba_base ≥55% | 122 picks (3.1%) 86–36–0P, 70.5% [61.9, 77.9], gap +6.7 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -9.3 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -6.2 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 9 | 9 | ridge_points@weekly ≥55% & gbt_points@weekly ≥55% & ngboost:nba_base ≥65% | 123 picks (3.1%) 87–36–0P, 70.7% [62.2, 78.0], gap +5.5 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -10.1 pts | 97 picks (3.7%) 54–43–0P, 55.7% [45.8, 65.2], gap -5.6 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 10 | 10 | gbt_points@weekly ≥60% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 108 picks (2.7%) 76–32–0P, 70.4% [61.2, 78.2], gap +1.8 pts | 97 picks (3.7%) 56–41–0P, 57.7% [47.8, 67.1], gap -11.0 pts | 97 picks (3.7%) 53–44–0P, 54.6% [44.7, 64.2], gap -5.5 pts | 97 picks (3.7%) 57–40–0P, 58.8% [48.8, 68.0], gap -6.6 pts |
| 11 | 11 | gbt_points@weekly ≥60% & mlp:nba_base:normal ≥55% & ngboost:nba_base ≥65% | 109 picks (2.8%) 76–33–0P, 69.7% [60.5, 77.6], gap +2.8 pts | 89 picks (3.4%) 50–39–0P, 56.2% [45.8, 66.0], gap -11.1 pts | 89 picks (3.4%) 51–38–0P, 57.3% [46.9, 67.1], gap -3.8 pts | 89 picks (3.4%) 51–38–0P, 57.3% [46.9, 67.1], gap -8.3 pts |
| 12 | 12 | gbt_points@weekly ≥55% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 136 picks (3.4%) 96–40–0P, 70.6% [62.4, 77.6], gap +2.9 pts | 118 picks (4.5%) 70–48–0P, 59.3% [50.3, 67.8], gap -8.4 pts | 118 picks (4.5%) 66–52–0P, 55.9% [46.9, 64.6], gap -3.5 pts | 118 picks (4.5%) 69–49–0P, 58.5% [49.5, 67.0], gap -6.4 pts |
| 13 | 13 | gbt_points@weekly ≥55% & ngboost:nba_base ≥65% & hetero_linear:nba_base ≥55% | 122 picks (3.1%) 85–37–0P, 69.7% [61.0, 77.1], gap +4.4 pts | 99 picks (3.7%) 54–45–0P, 54.5% [44.8, 64.0], gap -11.3 pts | 99 picks (3.7%) 54–45–0P, 54.5% [44.8, 64.0], gap -6.6 pts | 99 picks (3.7%) 58–41–0P, 58.6% [48.7, 67.8], gap -6.7 pts |
| 14 | 8 | gbt_points ≥50% & ridge:nba_base ≥55% & ngboost:nba_base ≥65% | 123 picks (3.1%) 86–37–0P, 69.9% [61.3, 77.3], gap +4.1 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -11.1 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -6.2 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 15 | 8 | ridge:nba_base ≥55% & elastic_net:nba_base ≥55% & ngboost:nba_base ≥65% | 123 picks (3.1%) 86–37–0P, 69.9% [61.3, 77.3], gap +6.2 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -9.5 pts | 98 picks (3.7%) 54–44–0P, 55.1% [45.2, 64.6], gap -6.2 pts | 98 picks (3.7%) 57–41–0P, 58.2% [48.3, 67.4], gap -7.2 pts |
| 16 | 14 | ridge_points@weekly ≥55% & elastic_net:nba_base ≥55% & ngboost:nba_base ≥65% | 125 picks (3.2%) 88–37–0P, 70.4% [61.9, 77.7], gap +6.6 pts | 100 picks (3.8%) 56–44–0P, 56.0% [46.2, 65.3], gap -8.3 pts | 100 picks (3.8%) 56–44–0P, 56.0% [46.2, 65.3], gap -5.2 pts | 100 picks (3.8%) 59–41–0P, 59.0% [49.2, 68.1], gap -6.3 pts |
| 17 | 15 | ridge_points@weekly ≥55% & ngboost:nba_base ≥65% & hetero_linear:nba_base ≥55% | 125 picks (3.2%) 88–37–0P, 70.4% [61.9, 77.7], gap +6.6 pts | 99 picks (3.7%) 55–44–0P, 55.6% [45.7, 65.0], gap -8.6 pts | 99 picks (3.7%) 55–44–0P, 55.6% [45.7, 65.0], gap -5.7 pts | 99 picks (3.7%) 58–41–0P, 58.6% [48.7, 67.8], gap -6.7 pts |
| 18 | 16 | ridge:nba_base ≥60% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥60% | 106 picks (2.7%) 74–32–0P, 69.8% [60.5, 77.7], gap +5.0 pts | 123 picks (4.7%) 70–53–0P, 56.9% [48.1, 65.3], gap -8.1 pts | 123 picks (4.7%) 70–53–0P, 56.9% [48.1, 65.3], gap -6.9 pts | 123 picks (4.7%) 73–50–0P, 59.3% [50.5, 67.6], gap -5.4 pts |
| 19 | 17 | gbt_points@weekly ≥65% & mlp:nba_base:normal ≥55% & ngboost:nba_base ≥60% | 109 picks (2.8%) 75–34–0P, 68.8% [59.6, 76.7], gap +1.8 pts | 91 picks (3.4%) 58–33–0P, 63.7% [53.5, 72.9], gap -3.7 pts | 91 picks (3.4%) 59–32–0P, 64.8% [54.6, 73.9], gap +2.9 pts | 91 picks (3.4%) 53–38–0P, 58.2% [48.0, 67.8], gap -7.3 pts |
| 20 | 18 | ridge_points@weekly ≥55% & extra_trees:nba_base:normal ≥60% & ngboost:nba_base ≥65% | 112 picks (2.8%) 78–34–0P, 69.6% [60.6, 77.4], gap +3.7 pts | 93 picks (3.5%) 52–41–0P, 55.9% [45.8, 65.6], gap -10.4 pts | 93 picks (3.5%) 52–41–0P, 55.9% [45.8, 65.6], gap -5.4 pts | 93 picks (3.5%) 54–39–0P, 58.1% [47.9, 67.6], gap -7.4 pts |

