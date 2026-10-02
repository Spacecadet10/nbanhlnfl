# Challenger and combination results — 2026-10-02 (exploratory)

All runs used `.backup` copies of the stores and only already-used development seasons (NBA dataset 08fe2552c1aa4579, box
9ad11c5aa1590a46; NHL 5da8e01fc710d6a2), so none of this is evidence of prospective improvement. Reports:
`data/reports/{catboost,search}-{nba,nhl}-20261002/report.json`. Designs were committed before each run (docs/MODEL_SELECTION.md).

## CatBoost challenger (§10; design c3f5b3e, code 9f6bd8a; run 13:33–13:36 UTC) — **rejected**
Fixed 8-configuration grid, chosen on tuning seasons, scored once on held-out seasons. Lower is better.
| Task (held-out games) | Chosen config | CatBoost | Replaced member | Strongest (chosen on tuning seasons) | CatBoost − strongest [95%] |
|---|---|---|---|---|---|
| NBA winner, log loss (2024, 1,319) | depth 4, lr 0.03, l2 10 | 0.6169 | `gbt_v1` 0.6131 | `logistic_v2` 0.6082 | +0.0087 [+0.0024, +0.0161] |
| NBA margin log score (2024, 1,319) | depth 4, lr 0.03, l2 10 | 4.128 | `gbt_points` 4.086 | `ridge_points` 4.073 | +0.055 [+0.030, +0.080] |
| NHL winner, log loss (2020–21, 2,164) | depth 4, lr 0.03, l2 10 | 0.6754 | `hockey_gbt` 0.6753 | `hockey_logistic` 0.6724 | +0.0030 [−0.0013, +0.0076] |
CatBoost's per-game variance (RMSEWithUncertainty) was poorly calibrated: tuning scores ranged 4.07–5.73 across the grid. With
these features, simple regularised linear models remain best. Not deployed; the code stays as a research command only.

## Combination search (§11; design 826983d; run 13:35–13:41 UTC) — **nothing qualified, nothing deployed**
1,655 predefined rules per NBA market and fold (925 NHL winner, 345 reg3, 405 other NHL markets), each also within four
pregame condition levels. Rolling origin: NBA select 2022 → assess 2023, select 2022–23 → assess 2024; NHL select earlier
seasons → assess 2019, 2020, 2021. Comparator: the strongest single member chosen on the selection seasons, most-confident
games at the same count.
- **No rule passed the Bonferroni lower bound on selection data in any sport, market or fold** (z ≈ 4.0 NBA). With ~100–300
  picks a rule needs a lift of roughly +0.15 to pass; the best observed selection lifts were +0.04 to +0.20 with lower bounds
  −0.06 to −0.20.
- **The best-by-point-estimate rules (frozen, assessed for transparency) did not hold up consistently:** NBA winner +0.011
  (2023) and 0.000 (2024); NBA spread 0.000 and −0.055; NHL winner −0.073, +0.053, no picks. Some line-market rules built on the
  weekly `ridge_points` member stayed positive (NBA total +0.122 on 41 picks; team total away +0.064 on 311 picks, both at
  the primary's own model line) — descriptive only; that member is now recorded prospectively, where this can be tested.
- **Probability averages (complementarity):** the best average of any subset barely beats the best single member:
  NBA winner `elo+logistic_v2` = `logistic_v2` (0.62968); NBA spread +0.00003 nats; NBA total `points_normal+gbt_points@weekly`
  −0.0018 vs `points_normal`; NHL winner `hockey_elo+hockey_logistic` −0.0001; NHL spread `goals_poisson+goals_glm` −0.0005.
  So weaker members add at most small probability information; agreement selection added none beyond confidence.
- **Limit:** line markets are scored at the primary's model-only line (no historical bookmaker lines), so "agreement against
  the bookmaker" can be measured only prospectively — which the live research cohorts now record.

## NHL shot-quality candidate `hockey_logistic_xg` (§12; design dc14004 before any fetch; code 8b29127; run 14:50 UTC) — **qualifies; prospective shadow**
Shots for all 7,515 eligible development-season games (2016–2021) imported from api-web.nhle.com into the live NHL store
(append-only `nhl_pbp_imports` / `nhl_shots`, raw responses cached; 0 errors, 0 incomplete; 641,168 unblocked attempts;
policy `historical_backfill`, i.e. assumed available with each result). Evaluated on a `.backup` copy; report
`data/reports/nhl-xg-20261002/report.json` (dataset 5da8e01fc710d6a2). 2016 has no earlier xG fit (1,321 games unvalued, warm-up).
| Development games 2018–2021 (4,877) | Brier | Log loss | Accuracy |
|---|---|---|---|
| `hockey_elo` (primary) | 0.24072 | 0.67427 | 57.7% |
| `hockey_logistic` | 0.24055 | 0.67391 | 57.7% |
| `hockey_logistic_xg` | **0.23945** | **0.67162** | 58.5% |
- Brier(candidate − `hockey_logistic`) −0.0011, 95% [−0.0019, −0.0002]; vs `hockey_elo` −0.0013 [−0.0023, −0.0002]
  (133 week blocks). Better in each season: 2018 0.2386 vs 0.2395, 2019 0.2415 vs 0.2427, 2020 0.2429 vs 0.2441, 2021
  0.2333 vs 0.2344. Accuracy +0.8 points, interval includes 0. Last-fit weights: `ev_xg_share_diff` +0.12,
  `st_xg_net_diff` +0.05 (standardised), while the goals-based `xg_diff` fell to −0.03.
- Caveats: development seasons were used before for other selection; the historical availability time is an assumption
  (same as the results); single pre-set candidate, no search. This is a reason to record it prospectively, not a promotion.
- **Live (from merge):** separately versioned winner member `research-v2:hockey_logistic_xg:<definition>`; recorded and
  scored as a research source, never a vote, average or stacker input; skipped with a reason when no shots have been valued
  or a team's last 3 current-season finals lack shots; shots for new finals are imported in the two-hour cycle and the nightly
  NHL job (≤ 60 games per cycle). Promotion only by the written rule after prospective evidence.
