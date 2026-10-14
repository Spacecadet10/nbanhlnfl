# Thresholds and exact subsets — NBA winner (adaptive:2a0111fd73fc5cfa)

Kind: historical out-of-sample (exploratory). Earlier 2023-10-24–2024-06-18 (1319 games); later 2024-10-22–2026-06-14 (2643 games).

Out-of-sample forecasts of the adaptive candidates (weekly-refit walk, night-before cutoff), saved in the adaptive history store and exported without refitting. These seasons were already used for training and model choices: all results are exploratory, never an untouched test. No historical bookmaker odds: nothing here measures profit. Research only: nothing here changes the live forecasts or selects a model.

Win rate denominator: wins + losses (pushes and unsettled games excluded and shown separately).

Models compared: cand:extra_trees:nba_base@adaptive, cand:hetero_linear:nba_base@adaptive, cand:keras_mlp:nba_base@adaptive, cand:logit_l2:nba_base@adaptive, cand:mlp:nba_base@adaptive, cand:ngboost:nba_base@adaptive, cand:random_forest:nba_base@adaptive, cand:torch_mlp:nba_base@adaptive, cand:elastic_net:nba_base@adaptive, cand:extra_trees:nba_base:normal@adaptive, cand:mlp:nba_base:normal@adaptive, cand:ridge:nba_base@adaptive, cand:torch_mlp:nba_base:normal@adaptive.
Not stored: efficiency_margin, elo, gbt_points@adaptive, gbt_v1@adaptive, logistic@adaptive, logistic_v2@adaptive, points_normal, ridge_points@adaptive.
Excluded: none.
Missing earlier forecasts (those games are excluded from any rule or average needing the model): cand:elastic_net:nba_base@adaptive 244 of 1319 settled, cand:extra_trees:nba_base:normal@adaptive 244 of 1319 settled, cand:mlp:nba_base:normal@adaptive 244 of 1319 settled, cand:ridge:nba_base@adaptive 244 of 1319 settled, cand:torch_mlp:nba_base:normal@adaptive 244 of 1319 settled.
Missing later forecasts (those games are excluded from any rule or average needing the model): none.

## Cumulative thresholds (later period)

| Source | Threshold | Record |
|---|---|---|
| cand:extra_trees:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1772–871–0P, 67.0% [65.2, 68.8], gap +1.6 pts |
| cand:extra_trees:nba_base@adaptive | 55%+ | 2186 picks (82.7%) 1537–649–0P, 70.3% [68.4, 72.2], gap +2.2 pts |
| cand:extra_trees:nba_base@adaptive | 60%+ | 1707 picks (64.6%) 1258–449–0P, 73.7% [71.6, 75.7], gap +2.6 pts |
| cand:extra_trees:nba_base@adaptive | 65%+ | 1258 picks (47.6%) 973–285–0P, 77.3% [75.0, 79.6], gap +3.2 pts |
| cand:extra_trees:nba_base@adaptive | 70%+ | 876 picks (33.1%) 710–166–0P, 81.1% [78.3, 83.5], gap +3.9 pts |
| cand:extra_trees:nba_base@adaptive | 75%+ | 538 picks (20.4%) 448–90–0P, 83.3% [79.9, 86.2], gap +3.2 pts |
| cand:extra_trees:nba_base@adaptive | 80%+ | 270 picks (10.2%) 229–41–0P, 84.8% [80.0, 88.6], gap +2.0 pts |
| cand:extra_trees:nba_base@adaptive | 85%+ | 50 picks (1.9%) 42–8–0P, 84.0% [71.5, 91.7], gap -1.8 pts |
| cand:hetero_linear:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1773–870–0P, 67.1% [65.3, 68.8], gap +1.5 pts |
| cand:hetero_linear:nba_base@adaptive | 55%+ | 2150 picks (81.3%) 1517–633–0P, 70.6% [68.6, 72.4], gap +2.0 pts |
| cand:hetero_linear:nba_base@adaptive | 60%+ | 1683 picks (63.7%) 1246–437–0P, 74.0% [71.9, 76.1], gap +2.4 pts |
| cand:hetero_linear:nba_base@adaptive | 65%+ | 1263 picks (47.8%) 981–282–0P, 77.7% [75.3, 79.9], gap +3.0 pts |
| cand:hetero_linear:nba_base@adaptive | 70%+ | 865 picks (32.7%) 698–167–0P, 80.7% [77.9, 83.2], gap +2.6 pts |
| cand:hetero_linear:nba_base@adaptive | 75%+ | 549 picks (20.8%) 456–93–0P, 83.1% [79.7, 86.0], gap +1.7 pts |
| cand:hetero_linear:nba_base@adaptive | 80%+ | 288 picks (10.9%) 246–42–0P, 85.4% [80.9, 89.0], gap +0.4 pts |
| cand:hetero_linear:nba_base@adaptive | 85%+ | 134 picks (5.1%) 120–14–0P, 89.6% [83.2, 93.7], gap +1.3 pts |
| cand:hetero_linear:nba_base@adaptive | 90%+ | 36 picks (1.4%) 34–2–0P, 94.4% [81.9, 98.5], gap +2.6 pts |
| cand:keras_mlp:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1778–865–0P, 67.3% [65.5, 69.0], gap +1.5 pts |
| cand:keras_mlp:nba_base@adaptive | 55%+ | 2183 picks (82.6%) 1535–648–0P, 70.3% [68.4, 72.2], gap +1.7 pts |
| cand:keras_mlp:nba_base@adaptive | 60%+ | 1685 picks (63.8%) 1242–443–0P, 73.7% [71.6, 75.8], gap +1.8 pts |
| cand:keras_mlp:nba_base@adaptive | 65%+ | 1260 picks (47.7%) 970–290–0P, 77.0% [74.6, 79.2], gap +2.0 pts |
| cand:keras_mlp:nba_base@adaptive | 70%+ | 884 picks (33.4%) 717–167–0P, 81.1% [78.4, 83.6], gap +2.9 pts |
| cand:keras_mlp:nba_base@adaptive | 75%+ | 541 picks (20.5%) 449–92–0P, 83.0% [79.6, 85.9], gap +1.1 pts |
| cand:keras_mlp:nba_base@adaptive | 80%+ | 311 picks (11.8%) 267–44–0P, 85.9% [81.5, 89.3], gap +0.8 pts |
| cand:keras_mlp:nba_base@adaptive | 85%+ | 139 picks (5.3%) 119–20–0P, 85.6% [78.8, 90.5], gap -2.9 pts |
| cand:keras_mlp:nba_base@adaptive | 90%+ | 38 picks (1.4%) 34–4–0P, 89.5% [75.9, 95.8], gap -2.5 pts |
| cand:logit_l2:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1781–862–0P, 67.4% [65.6, 69.1], gap +0.5 pts |
| cand:logit_l2:nba_base@adaptive | 55%+ | 2188 picks (82.8%) 1538–650–0P, 70.3% [68.3, 72.2], gap +0.4 pts |
| cand:logit_l2:nba_base@adaptive | 60%+ | 1774 picks (67.1%) 1305–469–0P, 73.6% [71.5, 75.6], gap +0.8 pts |
| cand:logit_l2:nba_base@adaptive | 65%+ | 1371 picks (51.9%) 1044–327–0P, 76.1% [73.8, 78.3], gap +0.4 pts |
| cand:logit_l2:nba_base@adaptive | 70%+ | 999 picks (37.8%) 794–205–0P, 79.5% [76.9, 81.9], gap +0.7 pts |
| cand:logit_l2:nba_base@adaptive | 75%+ | 669 picks (25.3%) 547–122–0P, 81.8% [78.7, 84.5], gap -0.2 pts |
| cand:logit_l2:nba_base@adaptive | 80%+ | 390 picks (14.8%) 333–57–0P, 85.4% [81.5, 88.5], gap +0.2 pts |
| cand:logit_l2:nba_base@adaptive | 85%+ | 176 picks (6.7%) 153–23–0P, 86.9% [81.2, 91.1], gap -1.7 pts |
| cand:logit_l2:nba_base@adaptive | 90%+ | 52 picks (2.0%) 48–4–0P, 92.3% [81.8, 97.0], gap +0.2 pts |
| cand:mlp:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1778–865–0P, 67.3% [65.5, 69.0], gap +0.9 pts |
| cand:mlp:nba_base@adaptive | 55%+ | 2210 picks (83.6%) 1552–658–0P, 70.2% [68.3, 72.1], gap +1.2 pts |
| cand:mlp:nba_base@adaptive | 60%+ | 1794 picks (67.9%) 1316–478–0P, 73.4% [71.3, 75.3], gap +1.6 pts |
| cand:mlp:nba_base@adaptive | 65%+ | 1368 picks (51.8%) 1044–324–0P, 76.3% [74.0, 78.5], gap +1.7 pts |
| cand:mlp:nba_base@adaptive | 70%+ | 967 picks (36.6%) 774–193–0P, 80.0% [77.4, 82.4], gap +2.5 pts |
| cand:mlp:nba_base@adaptive | 75%+ | 602 picks (22.8%) 493–109–0P, 81.9% [78.6, 84.8], gap +1.2 pts |
| cand:mlp:nba_base@adaptive | 80%+ | 308 picks (11.7%) 259–49–0P, 84.1% [79.6, 87.8], gap +0.2 pts |
| cand:mlp:nba_base@adaptive | 85%+ | 99 picks (3.7%) 88–11–0P, 88.9% [81.2, 93.7], gap +1.3 pts |
| cand:mlp:nba_base@adaptive | 90%+ | 15 picks (0.6%) 14–1–0P, 93.3% [70.2, 98.8], gap +2.3 pts |
| cand:ngboost:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1779–864–0P, 67.3% [65.5, 69.1], gap +2.2 pts |
| cand:ngboost:nba_base@adaptive | 55%+ | 2134 picks (80.7%) 1517–617–0P, 71.1% [69.1, 73.0], gap +3.0 pts |
| cand:ngboost:nba_base@adaptive | 60%+ | 1662 picks (62.9%) 1238–424–0P, 74.5% [72.3, 76.5], gap +3.4 pts |
| cand:ngboost:nba_base@adaptive | 65%+ | 1262 picks (47.7%) 970–292–0P, 76.9% [74.5, 79.1], gap +3.1 pts |
| cand:ngboost:nba_base@adaptive | 70%+ | 811 picks (30.7%) 654–157–0P, 80.6% [77.8, 83.2], gap +3.4 pts |
| cand:ngboost:nba_base@adaptive | 75%+ | 501 picks (19.0%) 419–82–0P, 83.6% [80.1, 86.6], gap +3.4 pts |
| cand:ngboost:nba_base@adaptive | 80%+ | 250 picks (9.5%) 213–37–0P, 85.2% [80.3, 89.1], gap +2.1 pts |
| cand:ngboost:nba_base@adaptive | 85%+ | 42 picks (1.6%) 37–5–0P, 88.1% [75.0, 94.8], gap +0.7 pts |
| cand:ngboost:nba_base@adaptive | 90%+ | 7 picks (0.3%) 6–1–0P, 85.7% [48.7, 97.4], gap -6.3 pts |
| cand:random_forest:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1780–863–0P, 67.3% [65.5, 69.1], gap +0.8 pts |
| cand:random_forest:nba_base@adaptive | 55%+ | 2139 picks (80.9%) 1510–629–0P, 70.6% [68.6, 72.5], gap +0.7 pts |
| cand:random_forest:nba_base@adaptive | 60%+ | 1764 picks (66.7%) 1290–474–0P, 73.1% [71.0, 75.1], gap +0.6 pts |
| cand:random_forest:nba_base@adaptive | 65%+ | 1377 picks (52.1%) 1039–338–0P, 75.5% [73.1, 77.7], gap +0.1 pts |
| cand:random_forest:nba_base@adaptive | 70%+ | 957 picks (36.2%) 752–205–0P, 78.6% [75.9, 81.1], gap -0.1 pts |
| cand:random_forest:nba_base@adaptive | 75%+ | 640 picks (24.2%) 525–115–0P, 82.0% [78.9, 84.8], gap +0.2 pts |
| cand:random_forest:nba_base@adaptive | 80%+ | 400 picks (15.1%) 334–66–0P, 83.5% [79.5, 86.8], gap -1.0 pts |
| cand:random_forest:nba_base@adaptive | 85%+ | 162 picks (6.1%) 141–21–0P, 87.0% [81.0, 91.4], gap -0.5 pts |
| cand:random_forest:nba_base@adaptive | 90%+ | 20 picks (0.8%) 17–3–0P, 85.0% [64.0, 94.8], gap -5.5 pts |
| cand:torch_mlp:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1776–867–0P, 67.2% [65.4, 69.0], gap +0.9 pts |
| cand:torch_mlp:nba_base@adaptive | 55%+ | 2192 picks (82.9%) 1543–649–0P, 70.4% [68.4, 72.3], gap +1.2 pts |
| cand:torch_mlp:nba_base@adaptive | 60%+ | 1767 picks (66.9%) 1294–473–0P, 73.2% [71.1, 75.2], gap +1.3 pts |
| cand:torch_mlp:nba_base@adaptive | 65%+ | 1334 picks (50.5%) 1023–311–0P, 76.7% [74.3, 78.9], gap +1.7 pts |
| cand:torch_mlp:nba_base@adaptive | 70%+ | 927 picks (35.1%) 744–183–0P, 80.3% [77.6, 82.7], gap +2.0 pts |
| cand:torch_mlp:nba_base@adaptive | 75%+ | 605 picks (22.9%) 494–111–0P, 81.7% [78.4, 84.5], gap +0.2 pts |
| cand:torch_mlp:nba_base@adaptive | 80%+ | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap -0.7 pts |
| cand:torch_mlp:nba_base@adaptive | 85%+ | 129 picks (4.9%) 112–17–0P, 86.8% [79.9, 91.6], gap -1.8 pts |
| cand:torch_mlp:nba_base@adaptive | 90%+ | 34 picks (1.3%) 31–3–0P, 91.2% [77.0, 97.0], gap -0.7 pts |
| cand:elastic_net:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1777–866–0P, 67.2% [65.4, 69.0], gap +1.8 pts |
| cand:elastic_net:nba_base@adaptive | 55%+ | 2143 picks (81.1%) 1517–626–0P, 70.8% [68.8, 72.7], gap +2.4 pts |
| cand:elastic_net:nba_base@adaptive | 60%+ | 1682 picks (63.6%) 1246–436–0P, 74.1% [71.9, 76.1], gap +2.6 pts |
| cand:elastic_net:nba_base@adaptive | 65%+ | 1234 picks (46.7%) 960–274–0P, 77.8% [75.4, 80.0], gap +3.1 pts |
| cand:elastic_net:nba_base@adaptive | 70%+ | 849 picks (32.1%) 686–163–0P, 80.8% [78.0, 83.3], gap +2.7 pts |
| cand:elastic_net:nba_base@adaptive | 75%+ | 548 picks (20.7%) 454–94–0P, 82.8% [79.5, 85.8], gap +1.6 pts |
| cand:elastic_net:nba_base@adaptive | 80%+ | 291 picks (11.0%) 248–43–0P, 85.2% [80.7, 88.8], gap +0.4 pts |
| cand:elastic_net:nba_base@adaptive | 85%+ | 122 picks (4.6%) 109–13–0P, 89.3% [82.6, 93.7], gap +0.9 pts |
| cand:elastic_net:nba_base@adaptive | 90%+ | 39 picks (1.5%) 38–1–0P, 97.4% [86.8, 99.5], gap +5.7 pts |
| cand:extra_trees:nba_base:normal@adaptive | 50%+ | 2643 picks (100.0%) 1768–875–0P, 66.9% [65.1, 68.7], gap +2.2 pts |
| cand:extra_trees:nba_base:normal@adaptive | 55%+ | 2150 picks (81.3%) 1524–626–0P, 70.9% [68.9, 72.8], gap +3.4 pts |
| cand:extra_trees:nba_base:normal@adaptive | 60%+ | 1657 picks (62.7%) 1233–424–0P, 74.4% [72.3, 76.5], gap +4.0 pts |
| cand:extra_trees:nba_base:normal@adaptive | 65%+ | 1204 picks (45.6%) 944–260–0P, 78.4% [76.0, 80.6], gap +4.9 pts |
| cand:extra_trees:nba_base:normal@adaptive | 70%+ | 797 picks (30.2%) 645–152–0P, 80.9% [78.1, 83.5], gap +4.4 pts |
| cand:extra_trees:nba_base:normal@adaptive | 75%+ | 457 picks (17.3%) 386–71–0P, 84.5% [80.9, 87.5], gap +4.9 pts |
| cand:extra_trees:nba_base:normal@adaptive | 80%+ | 195 picks (7.4%) 165–30–0P, 84.6% [78.9, 89.0], gap +2.1 pts |
| cand:extra_trees:nba_base:normal@adaptive | 85%+ | 14 picks (0.5%) 13–1–0P, 92.9% [68.5, 98.7], gap +7.4 pts |
| cand:mlp:nba_base:normal@adaptive | 50%+ | 2643 picks (100.0%) 1788–855–0P, 67.7% [65.8, 69.4], gap +1.8 pts |
| cand:mlp:nba_base:normal@adaptive | 55%+ | 2181 picks (82.5%) 1524–657–0P, 69.9% [67.9, 71.8], gap +1.2 pts |
| cand:mlp:nba_base:normal@adaptive | 60%+ | 1713 picks (64.8%) 1258–455–0P, 73.4% [71.3, 75.5], gap +1.7 pts |
| cand:mlp:nba_base:normal@adaptive | 65%+ | 1286 picks (48.7%) 988–298–0P, 76.8% [74.4, 79.1], gap +2.0 pts |
| cand:mlp:nba_base:normal@adaptive | 70%+ | 893 picks (33.8%) 720–173–0P, 80.6% [77.9, 83.1], gap +2.5 pts |
| cand:mlp:nba_base:normal@adaptive | 75%+ | 557 picks (21.1%) 463–94–0P, 83.1% [79.8, 86.0], gap +1.5 pts |
| cand:mlp:nba_base:normal@adaptive | 80%+ | 313 picks (11.8%) 271–42–0P, 86.6% [82.4, 89.9], gap +1.8 pts |
| cand:mlp:nba_base:normal@adaptive | 85%+ | 128 picks (4.8%) 113–15–0P, 88.3% [81.6, 92.8], gap -0.3 pts |
| cand:mlp:nba_base:normal@adaptive | 90%+ | 34 picks (1.3%) 33–1–0P, 97.1% [85.1, 99.5], gap +5.2 pts |
| cand:ridge:nba_base@adaptive | 50%+ | 2643 picks (100.0%) 1783–860–0P, 67.5% [65.7, 69.2], gap +1.8 pts |
| cand:ridge:nba_base@adaptive | 55%+ | 2149 picks (81.3%) 1518–631–0P, 70.6% [68.7, 72.5], gap +2.0 pts |
| cand:ridge:nba_base@adaptive | 60%+ | 1699 picks (64.3%) 1257–442–0P, 74.0% [71.8, 76.0], gap +2.3 pts |
| cand:ridge:nba_base@adaptive | 65%+ | 1266 picks (47.9%) 981–285–0P, 77.5% [75.1, 79.7], gap +2.7 pts |
| cand:ridge:nba_base@adaptive | 70%+ | 868 picks (32.8%) 702–166–0P, 80.9% [78.1, 83.4], gap +2.6 pts |
| cand:ridge:nba_base@adaptive | 75%+ | 572 picks (21.6%) 475–97–0P, 83.0% [79.7, 85.9], gap +1.7 pts |
| cand:ridge:nba_base@adaptive | 80%+ | 306 picks (11.6%) 261–45–0P, 85.3% [80.9, 88.8], gap +0.3 pts |
| cand:ridge:nba_base@adaptive | 85%+ | 132 picks (5.0%) 116–16–0P, 87.9% [81.2, 92.4], gap -0.7 pts |
| cand:ridge:nba_base@adaptive | 90%+ | 45 picks (1.7%) 43–2–0P, 95.6% [85.2, 98.8], gap +3.8 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 50%+ | 2643 picks (100.0%) 1780–863–0P, 67.3% [65.5, 69.1], gap +2.3 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 55%+ | 2160 picks (81.7%) 1526–634–0P, 70.6% [68.7, 72.5], gap +2.8 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 60%+ | 1678 picks (63.5%) 1241–437–0P, 74.0% [71.8, 76.0], gap +3.2 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 65%+ | 1202 picks (45.5%) 940–262–0P, 78.2% [75.8, 80.4], gap +4.1 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 70%+ | 817 picks (30.9%) 665–152–0P, 81.4% [78.6, 83.9], gap +4.1 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 75%+ | 481 picks (18.2%) 404–77–0P, 84.0% [80.4, 87.0], gap +3.3 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 80%+ | 229 picks (8.7%) 198–31–0P, 86.5% [81.4, 90.3], gap +2.2 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 85%+ | 79 picks (3.0%) 72–7–0P, 91.1% [82.8, 95.6], gap +3.0 pts |
| cand:torch_mlp:nba_base:normal@adaptive | 90%+ | 14 picks (0.5%) 14–0–0P, 100.0% [78.5, 100.0], gap +8.6 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 50%+ | 2643 picks (100.0%) 1780–863–0P, 67.3% [65.5, 69.1], gap +1.7 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 55%+ | 2168 picks (82.0%) 1535–633–0P, 70.8% [68.9, 72.7], gap +2.3 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 60%+ | 1712 picks (64.8%) 1270–442–0P, 74.2% [72.1, 76.2], gap +2.7 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 65%+ | 1275 picks (48.2%) 983–292–0P, 77.1% [74.7, 79.3], gap +2.5 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 70%+ | 877 picks (33.2%) 712–165–0P, 81.2% [78.5, 83.6], gap +3.4 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 75%+ | 554 picks (21.0%) 457–97–0P, 82.5% [79.1, 85.4], gap +1.6 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 80%+ | 294 picks (11.1%) 255–39–0P, 86.7% [82.4, 90.1], gap +2.7 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 85%+ | 107 picks (4.0%) 96–11–0P, 89.7% [82.5, 94.2], gap +2.4 pts |
| avg:cand:extra_trees:nba_base@adaptive+cand:hetero_linear:nba_base@adaptive+cand:keras_mlp:nba_base@adaptive+cand:logit_l2:nba_base@adaptive+cand:mlp:nba_base@adaptive+cand:ngboost:nba_base@adaptive+cand:random_forest:nba_base@adaptive+cand:torch_mlp:nba_base@adaptive+cand:elastic_net:nba_base@adaptive+cand:extra_trees:nba_base:normal@adaptive+cand:mlp:nba_base:normal@adaptive+cand:ridge:nba_base@adaptive+cand:torch_mlp:nba_base:normal@adaptive | 90%+ | 9 picks (0.3%) 8–1–0P, 88.9% [56.5, 98.0], gap -1.7 pts |

## Exact subsets (chosen on the earlier period, frozen, then scored on the later period)

### Search limits

- Source: adaptive:2a0111fd73fc5cfa (oof-2a0111fd73fc5cfa.json.gz a501b69609dabac1).
- Models searched (13): cand:extra_trees:nba_base@adaptive, cand:hetero_linear:nba_base@adaptive, cand:keras_mlp:nba_base@adaptive, cand:logit_l2:nba_base@adaptive, cand:mlp:nba_base@adaptive, cand:ngboost:nba_base@adaptive, cand:random_forest:nba_base@adaptive, cand:torch_mlp:nba_base@adaptive, cand:elastic_net:nba_base@adaptive, cand:extra_trees:nba_base:normal@adaptive, cand:mlp:nba_base:normal@adaptive, cand:ridge:nba_base@adaptive, cand:torch_mlp:nba_base:normal@adaptive.
- Not searched: efficiency_margin, elo, gbt_points@adaptive, gbt_v1@adaptive, logistic@adaptive, logistic_v2@adaptive, points_normal, ridge_points@adaptive.
- Subset sizes [2, 3]; per-member thresholds [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]; 78 subsets of size 2, 286 subsets of size 3; 101920 rules searched; 87042 had ≥100 decided earlier picks.
- Earlier (search) 2023-10-24–2024-06-18; later (frozen check) 2024-10-22–2026-06-14. Best single model chosen on the earlier period: cand:ridge:nba_base@adaptive.
- A rule's record is an observed record on these games, not proof it will hold. Many rules were searched; the best earlier records are partly luck, which is why only the later period (frozen rules, no retuning) is evidence — and it is exploratory too.

### What the counts mean

Each list holds 20 frozen rules. "X of 20" counts RULES whose later win rate was above the comparator (the best single model's same number of most confident later picks, possibly different games). It is not a count of games won, and it is about this search only (these models, sizes, grid and minimum).

Descriptive later-period overlap: frozen rules whose later picks are exactly the same games (game IDs) with the same picked sides share one pick set. Rules in one set made the same picks, so their later win–loss–push outcomes, pending picks and win rate are identical; their mean probability and calibration gap can differ, because those come from each rule's own members. Matching win–loss totals on different games are different sets. Rules sharing a set are one later result, not independent confirmations. Counted only after the rules were frozen on the earlier period; it never removes, replaces, reorders or chooses a rule.

### Highest earlier win rate (ranked by its 95% lower bound)

Later win rate above the comparator: 0 of 20 rules, 0 distinct later pick sets. The 20 rules make 4 distinct later pick sets; shared: set 1 = rules 1, 2, 3, 4, 5, 7, 12 (313 picks); set 2 = rules 6, 8, 10, 11, 14, 16, 17, 20 (343 picks); set 3 = rules 9, 15, 18, 19 (340 picks).

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | cand:logit_l2:nba_base@adaptive ≥80% & cand:torch_mlp:nba_base@adaptive ≥80% | 104 picks (7.9%) 94–10–0P, 90.4% [83.2, 94.7], gap +5.1 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap -0.1 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| 2 | 1 | cand:extra_trees:nba_base@adaptive ≥70% & cand:logit_l2:nba_base@adaptive ≥80% & cand:torch_mlp:nba_base@adaptive ≥80% | 103 picks (7.8%) 93–10–0P, 90.3% [83.0, 94.6], gap +6.6 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +1.1 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| 3 | 1 | cand:logit_l2:nba_base@adaptive ≥80% & cand:ngboost:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥80% | 103 picks (7.8%) 93–10–0P, 90.3% [83.0, 94.6], gap +7.3 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +1.4 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| 4 | 1 | cand:logit_l2:nba_base@adaptive ≥80% & cand:random_forest:nba_base@adaptive ≥70% & cand:torch_mlp:nba_base@adaptive ≥80% | 103 picks (7.8%) 93–10–0P, 90.3% [83.0, 94.6], gap +5.3 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.2 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| 5 | 1 | cand:logit_l2:nba_base@adaptive ≥80% & cand:torch_mlp:nba_base@adaptive ≥80% & cand:elastic_net:nba_base@adaptive ≥50% | 103 picks (9.6%) 93–10–0P, 90.3% [83.0, 94.6], gap +5.1 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.3 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| 6 | 2 | cand:extra_trees:nba_base@adaptive ≥50% & cand:torch_mlp:nba_base@adaptive ≥80% | 110 picks (8.3%) 99–11–0P, 90.0% [83.0, 94.3], gap +7.8 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.9 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |
| 7 | 1 | cand:logit_l2:nba_base@adaptive ≥80% & cand:torch_mlp:nba_base@adaptive ≥80% & cand:extra_trees:nba_base:normal@adaptive ≥70% | 102 picks (9.5%) 92–10–0P, 90.2% [82.9, 94.6], gap +6.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +1.5 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| 8 | 2 | cand:extra_trees:nba_base@adaptive ≥70% & cand:torch_mlp:nba_base@adaptive ≥80% | 109 picks (8.3%) 98–11–0P, 89.9% [82.8, 94.3], gap +7.6 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.9 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |
| 9 | 3 | cand:logit_l2:nba_base@adaptive ≥75% & cand:torch_mlp:nba_base@adaptive ≥80% | 109 picks (8.3%) 98–11–0P, 89.9% [82.8, 94.3], gap +4.8 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap -0.9 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap +0.1 pts | 340 picks (12.9%) 288–52–0P, 84.7% [80.5, 88.1], gap +0.3 pts |
| 10 | 2 | cand:ngboost:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥80% | 109 picks (8.3%) 98–11–0P, 89.9% [82.8, 94.3], gap +8.5 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +1.3 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |
| 11 | 2 | cand:random_forest:nba_base@adaptive ≥70% & cand:torch_mlp:nba_base@adaptive ≥80% | 109 picks (8.3%) 98–11–0P, 89.9% [82.8, 94.3], gap +5.7 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap -0.4 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |
| 12 | 1 | cand:logit_l2:nba_base@adaptive ≥80% & cand:ngboost:nba_base@adaptive ≥65% & cand:torch_mlp:nba_base@adaptive ≥80% | 101 picks (7.7%) 91–10–0P, 90.1% [82.7, 94.5], gap +7.0 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +1.4 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.7 pts | 313 picks (11.8%) 267–46–0P, 85.3% [81.0, 88.8], gap +0.5 pts |
| 13 | 4 | cand:hetero_linear:nba_base@adaptive ≥75% & cand:torch_mlp:nba_base@adaptive ≥80% | 108 picks (8.2%) 97–11–0P, 89.8% [82.7, 94.2], gap +5.3 pts | 335 picks (12.7%) 284–51–0P, 84.8% [80.5, 88.2], gap +0.5 pts | 335 picks (12.7%) 284–51–0P, 84.8% [80.5, 88.2], gap +0.6 pts | 335 picks (12.7%) 285–50–0P, 85.1% [80.9, 88.5], gap +0.6 pts |
| 14 | 2 | cand:torch_mlp:nba_base@adaptive ≥80% & cand:elastic_net:nba_base@adaptive ≥50% | 108 picks (10.0%) 97–11–0P, 89.8% [82.7, 94.2], gap +5.4 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap -0.1 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |
| 15 | 3 | cand:extra_trees:nba_base@adaptive ≥70% & cand:logit_l2:nba_base@adaptive ≥75% & cand:torch_mlp:nba_base@adaptive ≥80% | 108 picks (8.2%) 97–11–0P, 89.8% [82.7, 94.2], gap +6.3 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap +0.3 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap +0.1 pts | 340 picks (12.9%) 288–52–0P, 84.7% [80.5, 88.1], gap +0.3 pts |
| 16 | 2 | cand:extra_trees:nba_base@adaptive ≥70% & cand:ngboost:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥80% | 108 picks (8.2%) 97–11–0P, 89.8% [82.7, 94.2], gap +8.8 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +1.7 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |
| 17 | 2 | cand:extra_trees:nba_base@adaptive ≥70% & cand:random_forest:nba_base@adaptive ≥70% & cand:torch_mlp:nba_base@adaptive ≥80% | 108 picks (8.2%) 97–11–0P, 89.8% [82.7, 94.2], gap +6.8 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.6 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |
| 18 | 3 | cand:logit_l2:nba_base@adaptive ≥75% & cand:ngboost:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥80% | 108 picks (8.2%) 97–11–0P, 89.8% [82.7, 94.2], gap +6.9 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap +0.5 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap +0.1 pts | 340 picks (12.9%) 288–52–0P, 84.7% [80.5, 88.1], gap +0.3 pts |
| 19 | 3 | cand:logit_l2:nba_base@adaptive ≥75% & cand:random_forest:nba_base@adaptive ≥70% & cand:torch_mlp:nba_base@adaptive ≥80% | 108 picks (8.2%) 97–11–0P, 89.8% [82.7, 94.2], gap +5.0 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap -0.6 pts | 340 picks (12.9%) 286–54–0P, 84.1% [79.9, 87.6], gap +0.1 pts | 340 picks (12.9%) 288–52–0P, 84.7% [80.5, 88.1], gap +0.3 pts |
| 20 | 2 | cand:ngboost:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥70% & cand:torch_mlp:nba_base@adaptive ≥80% | 108 picks (8.2%) 97–11–0P, 89.8% [82.7, 94.2], gap +7.5 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.8 pts | 343 picks (13.0%) 288–55–0P, 84.0% [79.7, 87.5], gap +0.1 pts | 343 picks (13.0%) 290–53–0P, 84.5% [80.3, 88.0], gap +0.2 pts |

### Strongest earlier improvement over the best single model at the same number of picks

Later win rate above the comparator: 4 of 20 rules, 4 distinct later pick sets. The 20 rules make 20 distinct later pick sets.

| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |
|---|---|---|---|---|---|---|
| 1 | 1 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥60% | 733 picks (55.6%) 561–172–0P, 76.5% [73.3, 79.5], gap +3.9 pts | 1554 picks (58.8%) 1161–393–0P, 74.7% [72.5, 76.8], gap +1.5 pts | 1554 picks (58.8%) 1161–393–0P, 74.7% [72.5, 76.8], gap +2.3 pts | 1554 picks (58.8%) 1170–384–0P, 75.3% [73.1, 77.4], gap +2.6 pts |
| 2 | 2 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:ngboost:nba_base@adaptive ≥55% & cand:random_forest:nba_base@adaptive ≥60% | 727 picks (55.1%) 557–170–0P, 76.6% [73.4, 79.5], gap +5.0 pts | 1542 picks (58.3%) 1156–386–0P, 75.0% [72.7, 77.1], gap +2.3 pts | 1542 picks (58.3%) 1156–386–0P, 75.0% [72.7, 77.1], gap +2.5 pts | 1542 picks (58.3%) 1163–379–0P, 75.4% [73.2, 77.5], gap +2.7 pts |
| 3 | 3 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥55% & cand:torch_mlp:nba_base@adaptive ≥60% | 759 picks (57.5%) 574–185–0P, 75.6% [72.4, 78.5], gap +3.5 pts | 1601 picks (60.6%) 1191–410–0P, 74.4% [72.2, 76.5], gap +1.6 pts | 1601 picks (60.6%) 1191–410–0P, 74.4% [72.2, 76.5], gap +2.3 pts | 1601 picks (60.6%) 1196–405–0P, 74.7% [72.5, 76.8], gap +2.4 pts |
| 4 | 4 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥60% | 716 picks (54.3%) 549–167–0P, 76.7% [73.4, 79.6], gap +3.9 pts | 1528 picks (57.8%) 1145–383–0P, 74.9% [72.7, 77.0], gap +1.6 pts | 1528 picks (57.8%) 1145–383–0P, 74.9% [72.7, 77.0], gap +2.3 pts | 1528 picks (57.8%) 1155–373–0P, 75.6% [73.4, 77.7], gap +2.7 pts |
| 5 | 5 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥60% | 714 picks (54.1%) 546–168–0P, 76.5% [73.2, 79.4], gap +3.7 pts | 1536 picks (58.1%) 1154–382–0P, 75.1% [72.9, 77.2], gap +1.9 pts | 1536 picks (58.1%) 1154–382–0P, 75.1% [72.9, 77.2], gap +2.6 pts | 1536 picks (58.1%) 1160–376–0P, 75.5% [73.3, 77.6], gap +2.7 pts |
| 6 | 6 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:ngboost:nba_base@adaptive ≥55% & cand:random_forest:nba_base@adaptive ≥55% | 769 picks (58.3%) 581–188–0P, 75.6% [72.4, 78.5], gap +4.6 pts | 1616 picks (61.1%) 1202–414–0P, 74.4% [72.2, 76.4], gap +2.3 pts | 1616 picks (61.1%) 1202–414–0P, 74.4% [72.2, 76.4], gap +2.4 pts | 1616 picks (61.1%) 1206–410–0P, 74.6% [72.5, 76.7], gap +2.4 pts |
| 7 | 7 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:logit_l2:nba_base@adaptive ≥60% & cand:ngboost:nba_base@adaptive ≥55% | 736 picks (55.8%) 559–177–0P, 76.0% [72.7, 78.9], gap +4.2 pts | 1585 picks (60.0%) 1190–395–0P, 75.1% [72.9, 77.1], gap +2.6 pts | 1585 picks (60.0%) 1190–395–0P, 75.1% [72.9, 77.1], gap +2.8 pts | 1585 picks (60.0%) 1185–400–0P, 74.8% [72.6, 76.8], gap +2.3 pts |
| 8 | 8 | cand:logit_l2:nba_base@adaptive ≥65% & cand:mlp:nba_base@adaptive ≥60% | 646 picks (49.0%) 506–140–0P, 78.3% [75.0, 81.3], gap +3.8 pts | 1369 picks (51.8%) 1043–326–0P, 76.2% [73.9, 78.4], gap +1.1 pts | 1369 picks (51.8%) 1043–326–0P, 76.2% [73.9, 78.4], gap +2.2 pts | 1369 picks (51.8%) 1047–322–0P, 76.5% [74.2, 78.6], gap +2.4 pts |
| 9 | 9 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:logit_l2:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥55% | 742 picks (56.3%) 563–179–0P, 75.9% [72.7, 78.8], gap +3.0 pts | 1587 picks (60.0%) 1191–396–0P, 75.0% [72.9, 77.1], gap +1.8 pts | 1587 picks (60.0%) 1191–396–0P, 75.0% [72.9, 77.1], gap +2.8 pts | 1587 picks (60.0%) 1186–401–0P, 74.7% [72.5, 76.8], gap +2.3 pts |
| 10 | 10 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:logit_l2:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥60% | 743 picks (56.3%) 563–180–0P, 75.8% [72.6, 78.7], gap +3.0 pts | 1579 picks (59.7%) 1185–394–0P, 75.0% [72.9, 77.1], gap +1.9 pts | 1579 picks (59.7%) 1185–394–0P, 75.0% [72.9, 77.1], gap +2.7 pts | 1579 picks (59.7%) 1183–396–0P, 74.9% [72.7, 77.0], gap +2.4 pts |
| 11 | 11 | cand:hetero_linear:nba_base@adaptive ≥60% & cand:keras_mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥55% | 721 picks (54.7%) 549–172–0P, 76.1% [72.9, 79.1], gap +3.5 pts | 1553 picks (58.8%) 1166–387–0P, 75.1% [72.9, 77.2], gap +2.2 pts | 1553 picks (58.8%) 1166–387–0P, 75.1% [72.9, 77.2], gap +2.5 pts | 1553 picks (58.8%) 1169–384–0P, 75.3% [73.1, 77.4], gap +2.6 pts |
| 12 | 12 | cand:hetero_linear:nba_base@adaptive ≥60% & cand:keras_mlp:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥60% | 723 picks (54.8%) 550–173–0P, 76.1% [72.8, 79.0], gap +3.5 pts | 1550 picks (58.6%) 1164–386–0P, 75.1% [72.9, 77.2], gap +2.3 pts | 1550 picks (58.6%) 1164–386–0P, 75.1% [72.9, 77.2], gap +2.5 pts | 1550 picks (58.6%) 1167–383–0P, 75.3% [73.1, 77.4], gap +2.6 pts |
| 13 | 13 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:logit_l2:nba_base@adaptive ≥60% & cand:ngboost:nba_base@adaptive ≥50% | 749 picks (56.8%) 567–182–0P, 75.7% [72.5, 78.6], gap +4.1 pts | 1597 picks (60.4%) 1199–398–0P, 75.1% [72.9, 77.1], gap +2.7 pts | 1597 picks (60.4%) 1199–398–0P, 75.1% [72.9, 77.1], gap +2.9 pts | 1597 picks (60.4%) 1194–403–0P, 74.8% [72.6, 76.8], gap +2.4 pts |
| 14 | 14 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:logit_l2:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥60% | 705 picks (53.4%) 540–165–0P, 76.6% [73.3, 79.6], gap +3.2 pts | 1522 picks (57.6%) 1146–376–0P, 75.3% [73.1, 77.4], gap +1.6 pts | 1522 picks (57.6%) 1146–376–0P, 75.3% [73.1, 77.4], gap +2.6 pts | 1522 picks (57.6%) 1150–372–0P, 75.6% [73.3, 77.7], gap +2.7 pts |
| 15 | 15 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥55% | 754 picks (57.2%) 569–185–0P, 75.5% [72.3, 78.4], gap +3.3 pts | 1612 picks (61.0%) 1202–410–0P, 74.6% [72.4, 76.6], gap +1.8 pts | 1612 picks (61.0%) 1202–410–0P, 74.6% [72.4, 76.6], gap +2.5 pts | 1612 picks (61.0%) 1203–409–0P, 74.6% [72.4, 76.7], gap +2.4 pts |
| 16 | 16 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:ngboost:nba_base@adaptive ≥55% & cand:torch_mlp:nba_base@adaptive ≥60% | 755 picks (57.2%) 569–186–0P, 75.4% [72.2, 78.3], gap +4.3 pts | 1593 picks (60.3%) 1190–403–0P, 74.7% [72.5, 76.8], gap +2.5 pts | 1593 picks (60.3%) 1190–403–0P, 74.7% [72.5, 76.8], gap +2.5 pts | 1593 picks (60.3%) 1191–402–0P, 74.8% [72.6, 76.8], gap +2.4 pts |
| 17 | 17 | cand:extra_trees:nba_base@adaptive ≥55% & cand:keras_mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥55% | 783 picks (59.4%) 589–194–0P, 75.2% [72.1, 78.1], gap +4.0 pts | 1642 picks (62.1%) 1214–428–0P, 73.9% [71.8, 76.0], gap +1.8 pts | 1642 picks (62.1%) 1214–428–0P, 73.9% [71.8, 76.0], gap +2.1 pts | 1642 picks (62.1%) 1222–420–0P, 74.4% [72.3, 76.5], gap +2.4 pts |
| 18 | 18 | cand:keras_mlp:nba_base@adaptive ≥60% & cand:ngboost:nba_base@adaptive ≥50% & cand:random_forest:nba_base@adaptive ≥55% | 784 picks (59.4%) 589–195–0P, 75.1% [72.0, 78.0], gap +4.4 pts | 1640 picks (62.1%) 1213–427–0P, 74.0% [71.8, 76.0], gap +2.1 pts | 1640 picks (62.1%) 1213–427–0P, 74.0% [71.8, 76.0], gap +2.2 pts | 1640 picks (62.1%) 1220–420–0P, 74.4% [72.2, 76.4], gap +2.3 pts |
| 19 | 19 | cand:mlp:nba_base@adaptive ≥60% & cand:random_forest:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥60% | 735 picks (55.7%) 557–178–0P, 75.8% [72.6, 78.7], gap +3.2 pts | 1596 picks (60.4%) 1191–405–0P, 74.6% [72.4, 76.7], gap +1.5 pts | 1596 picks (60.4%) 1191–405–0P, 74.6% [72.4, 76.7], gap +2.5 pts | 1596 picks (60.4%) 1193–403–0P, 74.7% [72.6, 76.8], gap +2.4 pts |
| 20 | 20 | cand:random_forest:nba_base@adaptive ≥60% & cand:torch_mlp:nba_base@adaptive ≥60% | 760 picks (57.6%) 571–189–0P, 75.1% [71.9, 78.1], gap +2.8 pts | 1620 picks (61.3%) 1201–419–0P, 74.1% [71.9, 76.2], gap +1.1 pts | 1620 picks (61.3%) 1201–419–0P, 74.1% [71.9, 76.2], gap +2.2 pts | 1620 picks (61.3%) 1208–412–0P, 74.6% [72.4, 76.6], gap +2.4 pts |

