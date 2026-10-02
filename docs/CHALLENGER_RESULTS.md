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

## Expanded-combination experiment `expanded-v1` (§14; design 0f3b8d7, code a27b521, run 16:02–16:06 UTC)
**Inventory of the earlier §11 search (verified from its reports):** NBA winner `elo, logistic, logistic_v2, efficiency_margin,
gbt_v1`; NBA lines `points_normal, ridge_points, gbt_points, ridge_points@weekly, gbt_points@weekly` — the weekly members
*were* in every §11 line family and stacker; NHL winner `hockey_elo, goals_poisson, hockey_logistic, hockey_gbt`; NHL
reg3/lines `goals_poisson, goals_draw, goals_glm`. Never tested before today: `hockey_logistic_xg`; `availability_two_hour`
cannot be tested historically (no historical inputs) and is prospective only. The live *core* cohorts exclude every shadow.

**Registered family:** NHL winner 36 rules (every subset contains xG); NBA spread/total/team totals 27 rules each (every
subset contains a weekly member). Reports `data/reports/expanded-{nba,nhl}-20261002/report.json`.

**Pick rules — nothing qualified** (same criteria as §11: ≥100 picks, ≥5% coverage, Bonferroni lower bound > 0 vs the
strongest single, positive against every constituent). Best selection lower bounds: NHL −0.038 to −0.058 (z 2.99); NBA
−0.010 to −0.072 (z 2.90). On the NHL the strongest single on earlier data was xG itself in every fold, and agreement of xG
with other models matched xG's own most-confident games (lift −0.005 to 0.000), so the others added no selection value.

**Probability averages and stackers (log loss, cadence − constituent, 95% week-block interval):**
| Combination | vs its best constituent | Reading |
|---|---|---|
| NHL avg(xG + `hockey_elo`) | +0.0005 [−0.0007, +0.0016] vs xG | no gain over xG alone |
| NHL avg(xG + `hockey_logistic`) | +0.0006 [−0.0003, +0.0015] vs xG | no gain |
| NHL stacker (all five) | +0.0008 [−0.0005, +0.0022] vs xG | no gain; it improves on every other member |
| NBA total: stacker(`points_normal + ridge@weekly + gbt@weekly`) | −0.0027 [−0.0054, −0.00002] vs `points_normal` | **better than every constituent** (2023–24, 2,639 games) |
| NBA away team total: stacker (triple / all five) | −0.0020 [−0.0040, −0.0002] vs `points_normal` | better than the primary; vs `ridge@weekly` interval spans 0 |
| NBA spread: averages/stackers | ≥ −0.0006 vs `ridge@weekly` | no gain over `ridge@weekly` |
| NBA home team total | ≈ 0 vs `points_normal` | no gain |
So: xG's information is not improved by mixing in the existing NHL winner models; NBA totals show small genuine complementary
value at the probability level (the stacker of the primary with the weekly members), not a selection rule. All exploratory.

**Prospective recording (from merge):** every two-hour cycle writes an `expanded-v1` cohort next to the unchanged core cohort —
NHL winner (5 members incl. xG) and NBA spread/total/team totals (5 members incl. weekly) — with member forecast references,
all-member average, all-member out-of-fold stacker and all-member agreement. The app shows it on the Game page and as its own
"Expanded experiment · Recording only" group on Performance, with the registered subset averages as sources. No selector
decision, selection or recommendation reads it.

## Goalies — corrected assessment (2026-10-02)
My earlier note that goalie quality "cannot be used pregame" was too broad. Historical *starters* are postgame information, but
the app has recorded prospective ESPN probable/confirmed goalie snapshots with our retrieval time since 2026-10-01 03:30 UTC:
1,252 snapshots, 26 regular-season games, every team-game listed before the start (expected: median 36 h before;
confirmed for 18 of 52 team-games, median 7.6 h before). Checked against the actual starter in the play-by-play (first goalie in
net) for the 8 finished games: the last pre-start listing was right for 15 of 16 teams; every `confirmed` listing (14/14) was
right; the one miss was an `expected` listing. A goalie-quality input is therefore feasible prospectively: goalie ratings from
our xG model on historical shots (goalie ids are in the cached raw play-by-play, so no new requests), joined to the latest
listing before the forecast by name (exact-name match worked 16/16 here). It can only be judged on future games, because
historical pregame listings do not exist. Not built today.
