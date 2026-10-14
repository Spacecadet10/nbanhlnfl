# Thresholds and exact subsets — NBA winner (pool)

Kind: historical out-of-sample (exploratory). Earlier 2021-10-19–2024-06-18 (3962 games); later 2024-10-22–2026-06-14 (2643 games). Later includes spent reserved-test seasons [2025, 2026].

Historical out-of-sample forecasts saved by the candidate pool (walk-forward, night-before cutoff). Every season here was already used to choose or screen models, including the spent reserved-test seasons: all results are exploratory, never an untouched test. No historical bookmaker odds: nothing here measures profit. Research only: nothing here changes the live forecasts or selects a model.

Win rate denominator: wins + losses (pushes and unsettled games excluded and shown separately).

Models compared: elo, logistic, logistic_v2, efficiency_margin, gbt_v1, logit_l2:nba_base, extra_trees:nba_base, random_forest:nba_base, mlp:nba_base, ridge:nba_base, elastic_net:nba_base, extra_trees:nba_base:normal, mlp:nba_base:normal, ngboost:nba_base, hetero_linear:nba_base.
Not stored: none.
Excluded: none.
Missing earlier forecasts (those games are excluded from any rule or average needing the model): none.
Missing later forecasts (those games are excluded from any rule or average needing the model): none.

## Cumulative thresholds (later period)

| Source | Threshold | Record |
|---|---|---|
| elo | 50%+ | 2643 picks (100.0%) 1776–867–0P, 67.2% [65.4, 69.0], gap +0.9 pts |
| elo | 55%+ | 2206 picks (83.5%) 1536–670–0P, 69.6% [67.7, 71.5], gap +0.6 pts |
| elo | 60%+ | 1761 picks (66.6%) 1278–483–0P, 72.6% [70.4, 74.6], gap +0.7 pts |
| elo | 65%+ | 1325 picks (50.1%) 998–327–0P, 75.3% [72.9, 77.6], gap +0.3 pts |
| elo | 70%+ | 933 picks (35.3%) 732–201–0P, 78.5% [75.7, 81.0], gap +0.3 pts |
| elo | 75%+ | 604 picks (22.9%) 504–100–0P, 83.4% [80.3, 86.2], gap +2.1 pts |
| elo | 80%+ | 320 picks (12.1%) 273–47–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| elo | 85%+ | 128 picks (4.8%) 115–13–0P, 89.8% [83.4, 94.0], gap +1.5 pts |
| elo | 90%+ | 28 picks (1.1%) 25–3–0P, 89.3% [72.8, 96.3], gap -2.4 pts |
| logistic | 50%+ | 2643 picks (100.0%) 1780–863–0P, 67.3% [65.5, 69.1], gap +0.8 pts |
| logistic | 55%+ | 2187 picks (82.7%) 1538–649–0P, 70.3% [68.4, 72.2], gap +0.9 pts |
| logistic | 60%+ | 1748 picks (66.1%) 1269–479–0P, 72.6% [70.5, 74.6], gap +0.2 pts |
| logistic | 65%+ | 1347 picks (51.0%) 1028–319–0P, 76.3% [74.0, 78.5], gap +0.9 pts |
| logistic | 70%+ | 961 picks (36.4%) 766–195–0P, 79.7% [77.0, 82.1], gap +1.2 pts |
| logistic | 75%+ | 622 picks (23.5%) 515–107–0P, 82.8% [79.6, 85.6], gap +0.9 pts |
| logistic | 80%+ | 366 picks (13.8%) 313–53–0P, 85.5% [81.5, 88.8], gap +0.5 pts |
| logistic | 85%+ | 149 picks (5.6%) 132–17–0P, 88.6% [82.5, 92.8], gap -0.2 pts |
| logistic | 90%+ | 46 picks (1.7%) 44–2–0P, 95.7% [85.5, 98.8], gap +3.6 pts |
| logistic_v2 | 50%+ | 2643 picks (100.0%) 1779–864–0P, 67.3% [65.5, 69.1], gap +0.5 pts |
| logistic_v2 | 55%+ | 2180 picks (82.5%) 1534–646–0P, 70.4% [68.4, 72.2], gap +0.5 pts |
| logistic_v2 | 60%+ | 1778 picks (67.3%) 1294–484–0P, 72.8% [70.7, 74.8], gap +0.2 pts |
| logistic_v2 | 65%+ | 1360 picks (51.5%) 1037–323–0P, 76.2% [73.9, 78.4], gap +0.5 pts |
| logistic_v2 | 70%+ | 986 picks (37.3%) 792–194–0P, 80.3% [77.7, 82.7], gap +1.5 pts |
| logistic_v2 | 75%+ | 662 picks (25.0%) 538–124–0P, 81.3% [78.1, 84.1], gap -0.7 pts |
| logistic_v2 | 80%+ | 392 picks (14.8%) 336–56–0P, 85.7% [81.9, 88.8], gap +0.6 pts |
| logistic_v2 | 85%+ | 172 picks (6.5%) 151–21–0P, 87.8% [82.1, 91.9], gap -0.9 pts |
| logistic_v2 | 90%+ | 54 picks (2.0%) 50–4–0P, 92.6% [82.4, 97.1], gap +0.5 pts |
| efficiency_margin | 50%+ | 2643 picks (100.0%) 1769–874–0P, 66.9% [65.1, 68.7], gap +3.6 pts |
| efficiency_margin | 55%+ | 2053 picks (77.7%) 1466–587–0P, 71.4% [69.4, 73.3], gap +5.0 pts |
| efficiency_margin | 60%+ | 1498 picks (56.7%) 1129–369–0P, 75.4% [73.1, 77.5], gap +5.7 pts |
| efficiency_margin | 65%+ | 1007 picks (38.1%) 802–205–0P, 79.6% [77.0, 82.0], gap +6.5 pts |
| efficiency_margin | 70%+ | 634 picks (24.0%) 527–107–0P, 83.1% [80.0, 85.8], gap +6.6 pts |
| efficiency_margin | 75%+ | 339 picks (12.8%) 289–50–0P, 85.3% [81.1, 88.6], gap +5.1 pts |
| efficiency_margin | 80%+ | 148 picks (5.6%) 127–21–0P, 85.8% [79.3, 90.5], gap +2.0 pts |
| efficiency_margin | 85%+ | 50 picks (1.9%) 45–5–0P, 90.0% [78.6, 95.7], gap +2.9 pts |
| efficiency_margin | 90%+ | 5 picks (0.2%) 5–0–0P, 100.0% [56.6, 100.0], gap +8.7 pts |
| gbt_v1 | 50%+ | 2643 picks (100.0%) 1780–863–0P, 67.3% [65.5, 69.1], gap +0.5 pts |
| gbt_v1 | 55%+ | 2239 picks (84.7%) 1565–674–0P, 69.9% [68.0, 71.8], gap +0.5 pts |
| gbt_v1 | 60%+ | 1781 picks (67.4%) 1310–471–0P, 73.6% [71.5, 75.6], gap +1.1 pts |
| gbt_v1 | 65%+ | 1391 picks (52.6%) 1052–339–0P, 75.6% [73.3, 77.8], gap +0.3 pts |
| gbt_v1 | 70%+ | 969 picks (36.7%) 762–207–0P, 78.6% [75.9, 81.1], gap +0.1 pts |
| gbt_v1 | 75%+ | 612 picks (23.2%) 503–109–0P, 82.2% [79.0, 85.0], gap -0.0 pts |
| gbt_v1 | 80%+ | 384 picks (14.5%) 317–67–0P, 82.6% [78.4, 86.0], gap -2.5 pts |
| gbt_v1 | 85%+ | 179 picks (6.8%) 154–25–0P, 86.0% [80.2, 90.4], gap -2.0 pts |
| gbt_v1 | 90%+ | 28 picks (1.1%) 23–5–0P, 82.1% [64.4, 92.1], gap -9.0 pts |
| logit_l2:nba_base | 50%+ | 2643 picks (100.0%) 1786–857–0P, 67.6% [65.8, 69.3], gap +0.6 pts |
| logit_l2:nba_base | 55%+ | 2196 picks (83.1%) 1543–653–0P, 70.3% [68.3, 72.1], gap +0.4 pts |
| logit_l2:nba_base | 60%+ | 1772 picks (67.0%) 1302–470–0P, 73.5% [71.4, 75.5], gap +0.7 pts |
| logit_l2:nba_base | 65%+ | 1378 picks (52.1%) 1049–329–0P, 76.1% [73.8, 78.3], gap +0.4 pts |
| logit_l2:nba_base | 70%+ | 1012 picks (38.3%) 800–212–0P, 79.1% [76.4, 81.4], gap +0.3 pts |
| logit_l2:nba_base | 75%+ | 669 picks (25.3%) 547–122–0P, 81.8% [78.7, 84.5], gap -0.2 pts |
| logit_l2:nba_base | 80%+ | 388 picks (14.7%) 330–58–0P, 85.1% [81.2, 88.3], gap -0.2 pts |
| logit_l2:nba_base | 85%+ | 181 picks (6.8%) 157–24–0P, 86.7% [81.0, 90.9], gap -1.8 pts |
| logit_l2:nba_base | 90%+ | 54 picks (2.0%) 50–4–0P, 92.6% [82.4, 97.1], gap +0.5 pts |
| extra_trees:nba_base | 50%+ | 2643 picks (100.0%) 1782–861–0P, 67.4% [65.6, 69.2], gap +2.2 pts |
| extra_trees:nba_base | 55%+ | 2187 picks (82.7%) 1538–649–0P, 70.3% [68.4, 72.2], gap +2.4 pts |
| extra_trees:nba_base | 60%+ | 1693 picks (64.1%) 1254–439–0P, 74.1% [71.9, 76.1], gap +3.1 pts |
| extra_trees:nba_base | 65%+ | 1223 picks (46.3%) 954–269–0P, 78.0% [75.6, 80.2], gap +3.8 pts |
| extra_trees:nba_base | 70%+ | 863 picks (32.7%) 696–167–0P, 80.6% [77.9, 83.1], gap +3.6 pts |
| extra_trees:nba_base | 75%+ | 524 picks (19.8%) 438–86–0P, 83.6% [80.2, 86.5], gap +3.7 pts |
| extra_trees:nba_base | 80%+ | 248 picks (9.4%) 213–35–0P, 85.9% [81.0, 89.7], gap +3.1 pts |
| extra_trees:nba_base | 85%+ | 38 picks (1.4%) 34–4–0P, 89.5% [75.9, 95.8], gap +3.6 pts |
| random_forest:nba_base | 50%+ | 2643 picks (100.0%) 1773–870–0P, 67.1% [65.3, 68.8], gap +0.6 pts |
| random_forest:nba_base | 55%+ | 2144 picks (81.1%) 1518–626–0P, 70.8% [68.8, 72.7], gap +1.0 pts |
| random_forest:nba_base | 60%+ | 1732 picks (65.5%) 1283–449–0P, 74.1% [72.0, 76.1], gap +1.3 pts |
| random_forest:nba_base | 65%+ | 1380 picks (52.2%) 1040–340–0P, 75.4% [73.0, 77.6], gap +0.0 pts |
| random_forest:nba_base | 70%+ | 978 picks (37.0%) 770–208–0P, 78.7% [76.1, 81.2], gap +0.2 pts |
| random_forest:nba_base | 75%+ | 627 picks (23.7%) 513–114–0P, 81.8% [78.6, 84.6], gap -0.2 pts |
| random_forest:nba_base | 80%+ | 404 picks (15.3%) 337–67–0P, 83.4% [79.5, 86.7], gap -1.1 pts |
| random_forest:nba_base | 85%+ | 162 picks (6.1%) 142–20–0P, 87.7% [81.7, 91.9], gap -0.1 pts |
| random_forest:nba_base | 90%+ | 16 picks (0.6%) 15–1–0P, 93.8% [71.7, 98.9], gap +3.5 pts |
| mlp:nba_base | 50%+ | 2643 picks (100.0%) 1782–861–0P, 67.4% [65.6, 69.2], gap +1.1 pts |
| mlp:nba_base | 55%+ | 2203 picks (83.4%) 1545–658–0P, 70.1% [68.2, 72.0], gap +1.0 pts |
| mlp:nba_base | 60%+ | 1798 picks (68.0%) 1318–480–0P, 73.3% [71.2, 75.3], gap +1.6 pts |
| mlp:nba_base | 65%+ | 1362 picks (51.5%) 1041–321–0P, 76.4% [74.1, 78.6], gap +1.8 pts |
| mlp:nba_base | 70%+ | 963 picks (36.4%) 775–188–0P, 80.5% [77.9, 82.9], gap +2.8 pts |
| mlp:nba_base | 75%+ | 608 picks (23.0%) 500–108–0P, 82.2% [79.0, 85.1], gap +1.5 pts |
| mlp:nba_base | 80%+ | 316 picks (12.0%) 268–48–0P, 84.8% [80.4, 88.3], gap +0.9 pts |
| mlp:nba_base | 85%+ | 102 picks (3.9%) 92–10–0P, 90.2% [82.9, 94.6], gap +2.4 pts |
| mlp:nba_base | 90%+ | 15 picks (0.6%) 14–1–0P, 93.3% [70.2, 98.8], gap +2.2 pts |
| ridge:nba_base | 50%+ | 2643 picks (100.0%) 1783–860–0P, 67.5% [65.7, 69.2], gap +1.6 pts |
| ridge:nba_base | 55%+ | 2164 picks (81.9%) 1525–639–0P, 70.5% [68.5, 72.4], gap +1.7 pts |
| ridge:nba_base | 60%+ | 1717 picks (65.0%) 1272–445–0P, 74.1% [72.0, 76.1], gap +2.3 pts |
| ridge:nba_base | 65%+ | 1290 picks (48.8%) 995–295–0P, 77.1% [74.8, 79.3], gap +2.2 pts |
| ridge:nba_base | 70%+ | 883 picks (33.4%) 710–173–0P, 80.4% [77.7, 82.9], gap +2.0 pts |
| ridge:nba_base | 75%+ | 582 picks (22.0%) 484–98–0P, 83.2% [79.9, 86.0], gap +1.6 pts |
| ridge:nba_base | 80%+ | 314 picks (11.9%) 267–47–0P, 85.0% [80.7, 88.6], gap -0.1 pts |
| ridge:nba_base | 85%+ | 140 picks (5.3%) 123–17–0P, 87.9% [81.4, 92.3], gap -0.8 pts |
| ridge:nba_base | 90%+ | 47 picks (1.8%) 45–2–0P, 95.7% [85.8, 98.8], gap +3.8 pts |
| elastic_net:nba_base | 50%+ | 2643 picks (100.0%) 1779–864–0P, 67.3% [65.5, 69.1], gap +1.7 pts |
| elastic_net:nba_base | 55%+ | 2153 picks (81.5%) 1525–628–0P, 70.8% [68.9, 72.7], gap +2.2 pts |
| elastic_net:nba_base | 60%+ | 1704 picks (64.5%) 1266–438–0P, 74.3% [72.2, 76.3], gap +2.7 pts |
| elastic_net:nba_base | 65%+ | 1252 picks (47.4%) 967–285–0P, 77.2% [74.8, 79.5], gap +2.3 pts |
| elastic_net:nba_base | 70%+ | 872 picks (33.0%) 704–168–0P, 80.7% [78.0, 83.2], gap +2.6 pts |
| elastic_net:nba_base | 75%+ | 568 picks (21.5%) 471–97–0P, 82.9% [79.6, 85.8], gap +1.6 pts |
| elastic_net:nba_base | 80%+ | 305 picks (11.5%) 259–46–0P, 84.9% [80.5, 88.5], gap +0.0 pts |
| elastic_net:nba_base | 85%+ | 129 picks (4.9%) 113–16–0P, 87.6% [80.8, 92.2], gap -1.0 pts |
| elastic_net:nba_base | 90%+ | 42 picks (1.6%) 40–2–0P, 95.2% [84.2, 98.7], gap +3.4 pts |
| extra_trees:nba_base:normal | 50%+ | 2643 picks (100.0%) 1776–867–0P, 67.2% [65.4, 69.0], gap +2.4 pts |
| extra_trees:nba_base:normal | 55%+ | 2174 picks (82.3%) 1535–639–0P, 70.6% [68.7, 72.5], gap +3.2 pts |
| extra_trees:nba_base:normal | 60%+ | 1662 picks (62.9%) 1234–428–0P, 74.2% [72.1, 76.3], gap +3.7 pts |
| extra_trees:nba_base:normal | 65%+ | 1204 picks (45.6%) 936–268–0P, 77.7% [75.3, 80.0], gap +4.2 pts |
| extra_trees:nba_base:normal | 70%+ | 816 picks (30.9%) 659–157–0P, 80.8% [77.9, 83.3], gap +4.3 pts |
| extra_trees:nba_base:normal | 75%+ | 461 picks (17.4%) 387–74–0P, 83.9% [80.3, 87.0], gap +4.4 pts |
| extra_trees:nba_base:normal | 80%+ | 198 picks (7.5%) 171–27–0P, 86.4% [80.9, 90.5], gap +3.9 pts |
| extra_trees:nba_base:normal | 85%+ | 16 picks (0.6%) 15–1–0P, 93.8% [71.7, 98.9], gap +8.3 pts |
| mlp:nba_base:normal | 50%+ | 2643 picks (100.0%) 1786–857–0P, 67.6% [65.8, 69.3], gap +1.4 pts |
| mlp:nba_base:normal | 55%+ | 2187 picks (82.7%) 1525–662–0P, 69.7% [67.8, 71.6], gap +0.8 pts |
| mlp:nba_base:normal | 60%+ | 1742 picks (65.9%) 1284–458–0P, 73.7% [71.6, 75.7], gap +1.8 pts |
| mlp:nba_base:normal | 65%+ | 1309 picks (49.5%) 1000–309–0P, 76.4% [74.0, 78.6], gap +1.4 pts |
| mlp:nba_base:normal | 70%+ | 912 picks (34.5%) 732–180–0P, 80.3% [77.6, 82.7], gap +2.0 pts |
| mlp:nba_base:normal | 75%+ | 589 picks (22.3%) 488–101–0P, 82.9% [79.6, 85.7], gap +1.3 pts |
| mlp:nba_base:normal | 80%+ | 319 picks (12.1%) 275–44–0P, 86.2% [82.0, 89.6], gap +1.2 pts |
| mlp:nba_base:normal | 85%+ | 140 picks (5.3%) 122–18–0P, 87.1% [80.6, 91.7], gap -1.3 pts |
| mlp:nba_base:normal | 90%+ | 43 picks (1.6%) 41–2–0P, 95.3% [84.5, 98.7], gap +3.7 pts |
| ngboost:nba_base | 50%+ | 2643 picks (100.0%) 1780–863–0P, 67.3% [65.5, 69.1], gap +2.9 pts |
| ngboost:nba_base | 55%+ | 2132 picks (80.7%) 1506–626–0P, 70.6% [68.7, 72.5], gap +3.3 pts |
| ngboost:nba_base | 60%+ | 1627 picks (61.6%) 1210–417–0P, 74.4% [72.2, 76.4], gap +4.0 pts |
| ngboost:nba_base | 65%+ | 1218 picks (46.1%) 949–269–0P, 77.9% [75.5, 80.2], gap +4.8 pts |
| ngboost:nba_base | 70%+ | 721 picks (27.3%) 582–139–0P, 80.7% [77.7, 83.4], gap +3.6 pts |
| ngboost:nba_base | 75%+ | 441 picks (16.7%) 371–70–0P, 84.1% [80.4, 87.2], gap +4.0 pts |
| ngboost:nba_base | 80%+ | 187 picks (7.1%) 165–22–0P, 88.2% [82.8, 92.1], gap +4.7 pts |
| ngboost:nba_base | 85%+ | 52 picks (2.0%) 46–6–0P, 88.5% [77.0, 94.6], gap +1.6 pts |
| ngboost:nba_base | 90%+ | 5 picks (0.2%) 5–0–0P, 100.0% [56.6, 100.0], gap +8.1 pts |
| hetero_linear:nba_base | 50%+ | 2643 picks (100.0%) 1779–864–0P, 67.3% [65.5, 69.1], gap +1.7 pts |
| hetero_linear:nba_base | 55%+ | 2151 picks (81.4%) 1518–633–0P, 70.6% [68.6, 72.5], gap +2.0 pts |
| hetero_linear:nba_base | 60%+ | 1699 picks (64.3%) 1260–439–0P, 74.2% [72.0, 76.2], gap +2.7 pts |
| hetero_linear:nba_base | 65%+ | 1259 picks (47.6%) 975–284–0P, 77.4% [75.1, 79.7], gap +2.7 pts |
| hetero_linear:nba_base | 70%+ | 866 picks (32.8%) 699–167–0P, 80.7% [78.0, 83.2], gap +2.7 pts |
| hetero_linear:nba_base | 75%+ | 555 picks (21.0%) 463–92–0P, 83.4% [80.1, 86.3], gap +2.1 pts |
| hetero_linear:nba_base | 80%+ | 289 picks (10.9%) 246–43–0P, 85.1% [80.6, 88.8], gap +0.2 pts |
| hetero_linear:nba_base | 85%+ | 133 picks (5.0%) 118–15–0P, 88.7% [82.2, 93.0], gap +0.6 pts |
| hetero_linear:nba_base | 90%+ | 37 picks (1.4%) 35–2–0P, 94.6% [82.3, 98.5], gap +2.9 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 50%+ | 2643 picks (100.0%) 1787–856–0P, 67.6% [65.8, 69.4], gap +1.9 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 55%+ | 2178 picks (82.4%) 1537–641–0P, 70.6% [68.6, 72.4], gap +2.1 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 60%+ | 1712 picks (64.8%) 1267–445–0P, 74.0% [71.9, 76.0], gap +2.5 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 65%+ | 1277 picks (48.3%) 988–289–0P, 77.4% [75.0, 79.6], gap +2.8 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 70%+ | 891 picks (33.7%) 721–170–0P, 80.9% [78.2, 83.4], gap +3.3 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 75%+ | 556 picks (21.0%) 462–94–0P, 83.1% [79.8, 86.0], gap +2.2 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 80%+ | 298 picks (11.3%) 253–45–0P, 84.9% [80.4, 88.5], gap +0.9 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 85%+ | 105 picks (4.0%) 94–11–0P, 89.5% [82.2, 94.0], gap +2.3 pts |
| avg:elo+logistic+logistic_v2+efficiency_margin+gbt_v1+logit_l2:nba_base+extra_trees:nba_base+random_forest:nba_base+mlp:nba_base+ridge:nba_base+elastic_net:nba_base+extra_trees:nba_base:normal+mlp:nba_base:normal+ngboost:nba_base+hetero_linear:nba_base | 90%+ | 5 picks (0.2%) 5–0–0P, 100.0% [56.6, 100.0], gap +9.1 pts |

## Exact subsets (chosen on the earlier period, frozen, then scored on the later period)

### Search limits

- Source: pool (oof-nba-screen.json.gz 70699e2fa2f175be, oof-nba-assess.json.gz fdf536466fb40ca4).
- Models searched (15): elo, logistic, logistic_v2, efficiency_margin, gbt_v1, logit_l2:nba_base, extra_trees:nba_base, random_forest:nba_base, mlp:nba_base, ridge:nba_base, elastic_net:nba_base, extra_trees:nba_base:normal, mlp:nba_base:normal, ngboost:nba_base, hetero_linear:nba_base.
- Not searched: none.
- Subset sizes [2, 3]; per-member thresholds [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]; 105 subsets of size 2, 455 subsets of size 3; 161210 rules searched; 146975 had ≥100 decided earlier picks.
- Earlier (search) 2021-10-19–2024-06-18; later (frozen check) 2024-10-22–2026-06-14. Best single model chosen on the earlier period: elastic_net:nba_base.
- A rule's record is an observed record on these games, not proof it will hold. Many rules were searched; the best earlier records are partly luck, which is why only the later period (frozen rules, no retuning) is evidence — and it is exploratory too.

### What the counts mean

Each list holds 20 frozen rules. "X of 20" counts RULES whose later win rate was above the comparator (the best single model's same number of most confident later picks, possibly different games). It is not a count of games won, and it is about this search only (these models, sizes, grid and minimum).

Descriptive later-period overlap: frozen rules whose later picks are exactly the same games (game IDs) with the same picked sides share one pick set. Rules in one set made the same picks, so their later win–loss–push outcomes, pending picks and win rate are identical; their mean probability and calibration gap can differ, because those come from each rule's own members. Matching win–loss totals on different games are different sets. Rules sharing a set are one later result, not independent confirmations. Counted only after the rules were frozen on the earlier period; it never removes, replaces, reorders or chooses a rule.

### Highest earlier win rate (ranked by its 95% lower bound)

Later win rate above the comparator: 18 of 20 rules, 13 distinct later pick sets. The 20 rules make 15 distinct later pick sets; shared: set 9 = rules 9, 12, 14, 15, 16, 18 (278 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | elo ≥80% & gbt_v1 ≥80% & extra_trees:nba_base:normal ≥75% | 140 picks (3.5%) 125–15–0P, 89.3% [83.1, 93.4], gap +6.6 pts | 237 picks (9.0%) 202–35–0P, 85.2% [80.2, 89.2], gap +0.9 pts | 237 picks (9.0%) 202–35–0P, 85.2% [80.2, 89.2], gap +0.0 pts | 237 picks (9.0%) 199–38–0P, 84.0% [78.8, 88.1], gap -2.1 pts |
| 2 | 2 | logistic ≥80% & efficiency_margin ≥75% & gbt_v1 ≥80% | 186 picks (4.7%) 163–23–0P, 87.6% [82.1, 91.6], gap +4.4 pts | 243 picks (9.2%) 209–34–0P, 86.0% [81.1, 89.8], gap +1.5 pts | 243 picks (9.2%) 209–34–0P, 86.0% [81.1, 89.8], gap +0.6 pts | 243 picks (9.2%) 204–39–0P, 84.0% [78.8, 88.0], gap -2.0 pts |
| 3 | 3 | gbt_v1 ≥80% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 129 picks (3.3%) 114–15–0P, 88.4% [81.7, 92.8], gap +3.1 pts | 251 picks (9.5%) 215–36–0P, 85.7% [80.8, 89.5], gap +0.3 pts | 251 picks (9.5%) 215–36–0P, 85.7% [80.8, 89.5], gap +0.1 pts | 251 picks (9.5%) 211–40–0P, 84.1% [79.0, 88.1], gap -1.7 pts |
| 4 | 4 | efficiency_margin ≥75% & gbt_v1 ≥80% & extra_trees:nba_base:normal ≥75% | 161 picks (4.1%) 141–20–0P, 87.6% [81.6, 91.8], gap +6.7 pts | 263 picks (10.0%) 223–40–0P, 84.8% [80.0, 88.6], gap +2.0 pts | 263 picks (10.0%) 223–40–0P, 84.8% [80.0, 88.6], gap +0.1 pts | 263 picks (10.0%) 222–41–0P, 84.4% [79.5, 88.3], gap -1.2 pts |
| 5 | 5 | elo ≥75% & gbt_v1 ≥80% & extra_trees:nba_base:normal ≥75% | 180 picks (4.5%) 157–23–0P, 87.2% [81.6, 91.3], gap +5.2 pts | 308 picks (11.7%) 259–49–0P, 84.1% [79.6, 87.8], gap +0.7 pts | 308 picks (11.7%) 259–49–0P, 84.1% [79.6, 87.8], gap +0.3 pts | 308 picks (11.7%) 262–46–0P, 85.1% [80.7, 88.6], gap +0.2 pts |
| 6 | 6 | logistic ≥80% & efficiency_margin ≥75% & hetero_linear:nba_base ≥80% | 218 picks (5.5%) 189–29–0P, 86.7% [81.5, 90.6], gap +3.1 pts | 239 picks (9.0%) 203–36–0P, 84.9% [79.9, 88.9], gap +0.4 pts | 239 picks (9.0%) 203–36–0P, 84.9% [79.9, 88.9], gap -0.9 pts | 239 picks (9.0%) 200–39–0P, 83.7% [78.5, 87.8], gap -2.4 pts |
| 7 | 7 | elo ≥75% & efficiency_margin ≥75% & gbt_v1 ≥80% | 224 picks (5.7%) 194–30–0P, 86.6% [81.5, 90.5], gap +4.2 pts | 269 picks (10.2%) 228–41–0P, 84.8% [80.0, 88.6], gap +0.9 pts | 269 picks (10.2%) 228–41–0P, 84.8% [80.0, 88.6], gap +0.0 pts | 269 picks (10.2%) 228–41–0P, 84.8% [80.0, 88.6], gap -0.7 pts |
| 8 | 8 | random_forest:nba_base ≥75% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 179 picks (4.5%) 156–23–0P, 87.2% [81.5, 91.3], gap +3.1 pts | 276 picks (10.4%) 236–40–0P, 85.5% [80.9, 89.2], gap +0.7 pts | 276 picks (10.4%) 236–40–0P, 85.5% [80.9, 89.2], gap +0.2 pts | 276 picks (10.4%) 234–42–0P, 84.8% [80.1, 88.5], gap -0.6 pts |
| 9 | 9 | mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 185 picks (4.7%) 161–24–0P, 87.0% [81.4, 91.1], gap +2.0 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap +0.4 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap -0.0 pts | 278 picks (10.5%) 236–42–0P, 84.9% [80.2, 88.6], gap -0.4 pts |
| 10 | 10 | logistic ≥80% & efficiency_margin ≥75% & mlp:nba_base ≥80% | 191 picks (4.8%) 166–25–0P, 86.9% [81.4, 91.0], gap +3.0 pts | 254 picks (9.6%) 219–35–0P, 86.2% [81.4, 89.9], gap +2.2 pts | 254 picks (9.6%) 219–35–0P, 86.2% [81.4, 89.9], gap +0.8 pts | 254 picks (9.6%) 214–40–0P, 84.3% [79.3, 88.2], gap -1.5 pts |
| 11 | 11 | logistic ≥80% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 178 picks (4.5%) 155–23–0P, 87.1% [81.4, 91.2], gap +1.6 pts | 268 picks (10.1%) 230–38–0P, 85.8% [81.1, 89.5], gap +0.4 pts | 268 picks (10.1%) 230–38–0P, 85.8% [81.1, 89.5], gap +0.4 pts | 268 picks (10.1%) 227–41–0P, 84.7% [79.9, 88.5], gap -0.8 pts |
| 12 | 9 | gbt_v1 ≥70% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 184 picks (4.6%) 160–24–0P, 87.0% [81.3, 91.1], gap +2.7 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap +0.4 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap -0.0 pts | 278 picks (10.5%) 236–42–0P, 84.9% [80.2, 88.6], gap -0.4 pts |
| 13 | 12 | logistic_v2 ≥80% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 184 picks (4.6%) 160–24–0P, 87.0% [81.3, 91.1], gap +1.4 pts | 277 picks (10.5%) 236–41–0P, 85.2% [80.5, 88.9], gap -0.2 pts | 277 picks (10.5%) 236–41–0P, 85.2% [80.5, 88.9], gap -0.1 pts | 277 picks (10.5%) 235–42–0P, 84.8% [80.1, 88.6], gap -0.5 pts |
| 14 | 9 | mlp:nba_base ≥80% & elastic_net:nba_base ≥80% & extra_trees:nba_base:normal ≥70% | 184 picks (4.6%) 160–24–0P, 87.0% [81.3, 91.1], gap +4.5 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap +1.8 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap -0.0 pts | 278 picks (10.5%) 236–42–0P, 84.9% [80.2, 88.6], gap -0.4 pts |
| 15 | 9 | mlp:nba_base ≥80% & elastic_net:nba_base ≥80% & ngboost:nba_base ≥60% | 184 picks (4.6%) 160–24–0P, 87.0% [81.3, 91.1], gap +5.9 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap +2.0 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap -0.0 pts | 278 picks (10.5%) 236–42–0P, 84.9% [80.2, 88.6], gap -0.4 pts |
| 16 | 9 | mlp:nba_base ≥80% & ridge:nba_base ≥80% & elastic_net:nba_base ≥80% | 184 picks (4.6%) 160–24–0P, 87.0% [81.3, 91.1], gap +1.9 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap +0.2 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap -0.0 pts | 278 picks (10.5%) 236–42–0P, 84.9% [80.2, 88.6], gap -0.4 pts |
| 17 | 13 | efficiency_margin ≥70% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 183 picks (4.6%) 159–24–0P, 86.9% [81.2, 91.0], gap +3.5 pts | 274 picks (10.4%) 233–41–0P, 85.0% [80.3, 88.8], gap +1.6 pts | 274 picks (10.4%) 233–41–0P, 85.0% [80.3, 88.8], gap -0.3 pts | 274 picks (10.4%) 232–42–0P, 84.7% [79.9, 88.5], gap -0.7 pts |
| 18 | 9 | extra_trees:nba_base ≥70% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 183 picks (4.6%) 159–24–0P, 86.9% [81.2, 91.0], gap +4.3 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap +1.5 pts | 278 picks (10.5%) 237–41–0P, 85.3% [80.6, 88.9], gap -0.0 pts | 278 picks (10.5%) 236–42–0P, 84.9% [80.2, 88.6], gap -0.4 pts |
| 19 | 14 | logistic ≥80% & efficiency_margin ≥75% & ngboost:nba_base ≥65% | 251 picks (6.3%) 216–35–0P, 86.1% [81.2, 89.8], gap +6.5 pts | 275 picks (10.4%) 237–38–0P, 86.2% [81.6, 89.8], gap +3.9 pts | 275 picks (10.4%) 237–38–0P, 86.2% [81.6, 89.8], gap +1.1 pts | 275 picks (10.4%) 233–42–0P, 84.7% [80.0, 88.5], gap -0.7 pts |
| 20 | 15 | efficiency_margin ≥75% & gbt_v1 ≥80% & ridge:nba_base ≥80% | 151 picks (3.8%) 132–19–0P, 87.4% [81.2, 91.8], gap +4.0 pts | 241 picks (9.1%) 204–37–0P, 84.6% [79.6, 88.7], gap +0.2 pts | 241 picks (9.1%) 204–37–0P, 84.6% [79.6, 88.7], gap -0.9 pts | 241 picks (9.1%) 202–39–0P, 83.8% [78.6, 87.9], gap -2.2 pts |

### Strongest earlier improvement over the best single model at the same number of picks

Later win rate above the comparator: 3 of 20 rules, 3 distinct later pick sets. The 20 rules make 15 distinct later pick sets; shared: set 5 = rules 5, 10 (324 picks); set 12 = rules 13, 16, 17, 18, 20 (335 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | gbt_v1 ≥80% & mlp:nba_base ≥80% & elastic_net:nba_base ≥80% | 129 picks (3.3%) 114–15–0P, 88.4% [81.7, 92.8], gap +3.1 pts | 251 picks (9.5%) 215–36–0P, 85.7% [80.8, 89.5], gap +0.3 pts | 251 picks (9.5%) 215–36–0P, 85.7% [80.8, 89.5], gap +0.1 pts | 251 picks (9.5%) 211–40–0P, 84.1% [79.0, 88.1], gap -1.7 pts |
| 2 | 2 | elo ≥80% & gbt_v1 ≥80% & extra_trees:nba_base:normal ≥75% | 140 picks (3.5%) 125–15–0P, 89.3% [83.1, 93.4], gap +6.6 pts | 237 picks (9.0%) 202–35–0P, 85.2% [80.2, 89.2], gap +0.9 pts | 237 picks (9.0%) 202–35–0P, 85.2% [80.2, 89.2], gap +0.0 pts | 237 picks (9.0%) 199–38–0P, 84.0% [78.8, 88.1], gap -2.1 pts |
| 3 | 3 | mlp:nba_base ≥80% & elastic_net:nba_base ≥80% & ngboost:nba_base ≥70% | 121 picks (3.1%) 106–15–0P, 87.6% [80.6, 92.3], gap +5.6 pts | 265 picks (10.0%) 224–41–0P, 84.5% [79.7, 88.4], gap +1.1 pts | 265 picks (10.0%) 224–41–0P, 84.5% [79.7, 88.4], gap -0.8 pts | 265 picks (10.0%) 224–41–0P, 84.5% [79.7, 88.4], gap -1.0 pts |
| 4 | 4 | mlp:nba_base ≥80% & extra_trees:nba_base:normal ≥75% & ngboost:nba_base ≥70% | 121 picks (3.1%) 106–15–0P, 87.6% [80.6, 92.3], gap +7.7 pts | 290 picks (11.0%) 244–46–0P, 84.1% [79.5, 87.9], gap +2.2 pts | 290 picks (11.0%) 244–46–0P, 84.1% [79.5, 87.9], gap -0.5 pts | 290 picks (11.0%) 247–43–0P, 85.2% [80.6, 88.8], gap +0.0 pts |
| 5 | 5 | elo ≥75% & efficiency_margin ≥75% | 341 picks (8.6%) 287–54–0P, 84.2% [79.9, 87.7], gap +3.1 pts | 324 picks (12.3%) 275–49–0P, 84.9% [80.6, 88.4], gap +2.7 pts | 324 picks (12.3%) 275–49–0P, 84.9% [80.6, 88.4], gap +1.0 pts | 324 picks (12.3%) 276–48–0P, 85.2% [80.9, 88.6], gap +0.6 pts |
| 6 | 6 | elo ≥80% & mlp:nba_base ≥80% & ngboost:nba_base ≥70% | 124 picks (3.1%) 109–15–0P, 87.9% [81.0, 92.5], gap +5.8 pts | 245 picks (9.3%) 209–36–0P, 85.3% [80.3, 89.2], gap +1.6 pts | 245 picks (9.3%) 209–36–0P, 85.3% [80.3, 89.2], gap +0.0 pts | 245 picks (9.3%) 205–40–0P, 83.7% [78.5, 87.8], gap -2.2 pts |
| 7 | 7 | efficiency_margin ≥80% & gbt_v1 ≥75% & extra_trees:nba_base:normal ≥75% | 100 picks (2.5%) 88–12–0P, 88.0% [80.2, 93.0], gap +6.6 pts | 145 picks (5.5%) 124–21–0P, 85.5% [78.9, 90.3], gap +1.4 pts | 145 picks (5.5%) 124–21–0P, 85.5% [78.9, 90.3], gap -1.8 pts | 145 picks (5.5%) 128–17–0P, 88.3% [82.0, 92.5], gap +0.1 pts |
| 8 | 8 | efficiency_margin ≥80% & gbt_v1 ≥75% & elastic_net:nba_base ≥80% | 102 picks (2.6%) 89–13–0P, 87.3% [79.4, 92.4], gap +3.2 pts | 145 picks (5.5%) 124–21–0P, 85.5% [78.9, 90.3], gap -0.3 pts | 145 picks (5.5%) 124–21–0P, 85.5% [78.9, 90.3], gap -2.0 pts | 145 picks (5.5%) 128–17–0P, 88.3% [82.0, 92.5], gap +0.1 pts |
| 9 | 9 | elo ≥75% & efficiency_margin ≥75% & gbt_v1 ≥65% | 340 picks (8.6%) 286–54–0P, 84.1% [79.9, 87.6], gap +2.8 pts | 323 picks (12.2%) 274–49–0P, 84.8% [80.5, 88.3], gap +1.9 pts | 323 picks (12.2%) 274–49–0P, 84.8% [80.5, 88.3], gap +0.9 pts | 323 picks (12.2%) 275–48–0P, 85.1% [80.8, 88.6], gap +0.5 pts |
| 10 | 5 | elo ≥75% & efficiency_margin ≥75% & ngboost:nba_base ≥55% | 340 picks (8.6%) 286–54–0P, 84.1% [79.9, 87.6], gap +5.7 pts | 324 picks (12.3%) 275–49–0P, 84.9% [80.6, 88.4], gap +3.5 pts | 324 picks (12.3%) 275–49–0P, 84.9% [80.6, 88.4], gap +1.0 pts | 324 picks (12.3%) 276–48–0P, 85.2% [80.9, 88.6], gap +0.6 pts |
| 11 | 10 | elo ≥75% & logistic_v2 ≥75% & efficiency_margin ≥75% | 340 picks (8.6%) 286–54–0P, 84.1% [79.9, 87.6], gap +1.9 pts | 323 picks (12.2%) 275–48–0P, 85.1% [80.8, 88.6], gap +1.8 pts | 323 picks (12.2%) 275–48–0P, 85.1% [80.8, 88.6], gap +1.2 pts | 323 picks (12.2%) 275–48–0P, 85.1% [80.8, 88.6], gap +0.5 pts |
| 12 | 11 | efficiency_margin ≥75% & ngboost:nba_base ≥65% | 341 picks (8.6%) 286–55–0P, 83.9% [79.6, 87.4], gap +7.5 pts | 339 picks (12.8%) 289–50–0P, 85.3% [81.1, 88.6], gap +5.4 pts | 339 picks (12.8%) 289–50–0P, 85.3% [81.1, 88.6], gap +1.6 pts | 339 picks (12.8%) 290–49–0P, 85.5% [81.4, 88.9], gap +1.2 pts |
| 13 | 12 | efficiency_margin ≥75% & logit_l2:nba_base ≥75% & ngboost:nba_base ≥60% | 341 picks (8.6%) 286–55–0P, 83.9% [79.6, 87.4], gap +5.3 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +3.7 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +1.6 pts | 335 picks (12.7%) 287–48–0P, 85.7% [81.5, 89.0], gap +1.3 pts |
| 14 | 13 | efficiency_margin ≥80% & random_forest:nba_base ≥75% & extra_trees:nba_base:normal ≥75% | 105 picks (2.7%) 92–13–0P, 87.6% [80.0, 92.6], gap +6.3 pts | 144 picks (5.4%) 123–21–0P, 85.4% [78.7, 90.3], gap +1.3 pts | 144 picks (5.4%) 123–21–0P, 85.4% [78.7, 90.3], gap -2.0 pts | 144 picks (5.4%) 127–17–0P, 88.2% [81.9, 92.5], gap +0.0 pts |
| 15 | 14 | efficiency_margin ≥75% & gbt_v1 ≥70% & logit_l2:nba_base ≥75% | 344 picks (8.7%) 288–56–0P, 83.7% [79.5, 87.2], gap +2.3 pts | 333 picks (12.6%) 284–49–0P, 85.3% [81.1, 88.7], gap +2.1 pts | 333 picks (12.6%) 284–49–0P, 85.3% [81.1, 88.7], gap +1.5 pts | 333 picks (12.6%) 285–48–0P, 85.6% [81.4, 89.0], gap +1.1 pts |
| 16 | 12 | efficiency_margin ≥75% & logit_l2:nba_base ≥75% & random_forest:nba_base ≥65% | 344 picks (8.7%) 288–56–0P, 83.7% [79.5, 87.2], gap +2.6 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +2.1 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +1.6 pts | 335 picks (12.7%) 287–48–0P, 85.7% [81.5, 89.0], gap +1.3 pts |
| 17 | 12 | efficiency_margin ≥75% & logit_l2:nba_base ≥75% & extra_trees:nba_base ≥65% | 346 picks (8.7%) 290–56–0P, 83.8% [79.6, 87.3], gap +4.1 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +3.2 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +1.6 pts | 335 picks (12.7%) 287–48–0P, 85.7% [81.5, 89.0], gap +1.3 pts |
| 18 | 12 | efficiency_margin ≥75% & logit_l2:nba_base ≥75% & mlp:nba_base ≥70% | 346 picks (8.7%) 290–56–0P, 83.8% [79.6, 87.3], gap +2.3 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +2.5 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +1.6 pts | 335 picks (12.7%) 287–48–0P, 85.7% [81.5, 89.0], gap +1.3 pts |
| 19 | 15 | logistic_v2 ≥75% & efficiency_margin ≥75% & logit_l2:nba_base ≥75% | 346 picks (8.7%) 290–56–0P, 83.8% [79.6, 87.3], gap +1.6 pts | 334 picks (12.6%) 285–49–0P, 85.3% [81.1, 88.7], gap +1.7 pts | 334 picks (12.6%) 285–49–0P, 85.3% [81.1, 88.7], gap +1.6 pts | 334 picks (12.6%) 286–48–0P, 85.6% [81.5, 89.0], gap +1.2 pts |
| 20 | 12 | efficiency_margin ≥75% & logit_l2:nba_base ≥75% | 347 picks (8.8%) 291–56–0P, 83.9% [79.6, 87.4], gap +2.7 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +2.7 pts | 335 picks (12.7%) 286–49–0P, 85.4% [81.2, 88.8], gap +1.6 pts | 335 picks (12.7%) 287–48–0P, 85.7% [81.5, 89.0], gap +1.3 pts |

