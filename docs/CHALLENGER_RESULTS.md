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

**Probability averages and stackers (log loss, combination − constituent, 95% week-block interval):**
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

## Selective-combination study `selective-v1` (§15; design 115885b; code cd36a40; run 17:32–17:33 UTC on saved tables)
Reports `data/reports/selective-{nba,nhl}-20261002/report.json`; saved point-in-time member tables
`data/research/tables/{nba,nba_evening,nhl}.json.gz` (fingerprinted; the final run reused them). All exploratory (reused seasons).
The user's earlier ~83% system: no artifact exists in this project (the only "83%" reference is a random-split paper), so it
could not be inspected or reproduced.

### What has been tested (audit matrix)
| Sport · market | Members (valid for that exact market) | §5 | §11 search | §14 expanded | §15 selective (new) |
|---|---|---|---|---|---|
| NBA winner | elo, logistic, logistic_v2, efficiency_margin, gbt_v1 | avg, stack, majority/unanimous, pairs w/ primary | 1,655 rules × 4 conditions | — | 895 rules incl. min-member confidence, ≤0.80 thresholds, disagreement condition |
| NBA winner, 17:45 ET | + avail_v1 (injury report; assumed publication times) | — | — | — | 1,170 rules (first test of the injury model in combinations) |
| NBA spread/total/team totals | points_normal, ridge, gbt, ridge@weekly, gbt@weekly | as above | 1,655 each | 27 each | 555 each |
| NHL winner | hockey_elo, goals_poisson, hockey_logistic, hockey_gbt, **xG** | (4 members) | 925 (no xG) | 36 (xG subsets) | 895 incl. xG everywhere |
| NHL reg3 / puck line / total / team totals | goals_poisson, goals_draw, goals_glm | yes | 345 / 405 | — | 255 / 195 each |
Thresholds, conditions and evaluation periods are in MODEL_SELECTION §5/§11/§14/§15. Every rule of every study is in its report.
`availability_two_hour` (live NBA injury member, two-hour cutoff) has no historical inputs at its own cutoff: the report
*content* history exists (2019-20 … 2023-24, used here at 17:45 ET), but its *timing* is assumed from published file times.

### Did any rule reach ~80–85%? (one canonical outcome per game; pushes counted separately, never as wins)
- **NBA winner — yes, but from confidence, not agreement.** 160 rules (≥100 picks) are at or above 80% on 2022–2024; e.g.
  "Elo and logistic_v2 both ≥ 75%": 565 picks (14% of games), 80.0% — the strongest single model's 565 most confident games
  are also 80.0%. Only 7 of the 160 beat confidence by ≥2 points, and the best of those is one model alone
  (efficiency_margin ≥ 75%: 358 picks, 83.5% vs 81.3%).
  **Chronological procedure** (highest-accuracy rule with ≥100 picks chosen on earlier seasons, then frozen):
  `logistic + gbt_v1 both ≥ 80%, models disagree` → 2023: 85.1% on selection → **79.4% (27–7) on 34 picks, 2.6% of games**;
  → 2024: 83.7% on selection → **82.9% (63–13) on 76 picks, 5.8% of games**, but the strongest single's most confident 76 games
  were 88.2%. So ~80–85% held up on later seasons at small coverage, and it is the accuracy of heavy favourites, not evidence of
  agreement value or of betting value (such favourites are priced accordingly).
- **NBA winner at 17:45 with the injury model:** frozen rule `stack(all six) ≥ 75%, disagree` → 2023: 77.8% (84–24) on 108
  picks vs 82.4% for confidence alone; `avg(avail_v1 + efficiency_margin + gbt_v1) ≥ 80%, disagree` → 2024: **89.6% (95–11) on
  106 picks, 8.7% of games** vs 85.8% for confidence alone (+3.8 points; one season, not significant after the family).
- **NHL and every line market — no.** Best NHL winner bands: 73.3% at 6.5% coverage (stack ≥ 70%), 71.1% at 10.6%; the
  frozen highest-accuracy rules gave 66–79% on 37–243 later picks. Line markets (at the primary's model line) peak at
  60–66% on 5–10% coverage.

### Accuracy–coverage frontier (best rule per coverage band, hindsight across all seasons; vs strongest single at the same count)
| Market | ~5–10% | ~10–20% | ~20–35% | ~35–60% | 60%+ |
|---|---|---|---|---|---|
| NBA winner | 87.1% (224) vs 85.3% | 81.2% (447) vs 80.8% | 79.3% (892) vs 78.6% | 75.5% (1,398) vs 75.9% | 70.7% (2,396) vs 70.3% |
| NBA winner 17:45 | 88.8% (187) vs 87.7% | 83.6% (403) vs 83.6% | 78.7% (761) vs 79.1% | 75.7% (1,347) vs 75.2% | 71.0% (2,213) vs 71.0% |
| NHL winner | 73.3% (318) vs 71.4% | 71.1% (519) vs 71.9% | 67.3% (988) vs 67.3% | 64.7% (1,906) = xG alone | 59.2% (3,005) vs 61.7% |
| NHL reg3 | 58.1% (289) vs 56.4% | 56.2% (589) vs 54.8% | 55.1% | 50.6% | 48.6% |
Line markets: NBA spread 65.5% / total 59.4% / team totals 58.8–61.8% at 5–10%; NHL puck line 62.8%, total 60.0%, team totals
61.8–61.9% at 5–10%. Their comparators are weak by construction (the primary is ~50/50 at its own line), so these numbers
measure disagreement with the primary's line, not value against a bookmaker line.

### Why nothing qualified (diagnosis, per the reports)
- **No improvement over confidence filtering** is the dominant cause: of rule×fold scores with ≥100 picks, 792/1,105 (NBA
  winner), 1,281/1,346 (NBA 17:45), 708/931 (NHL winner) did not beat the strongest single's most-confident games at the same
  count; the best Bonferroni lower bounds were −0.02 to −0.06.
- **Too few observations:** hundreds of rule×fold scores had positive lifts on <100 picks (e.g. 336 NHL winner, 144 NBA total);
  detecting a +3-point lift at 80% needs well over 1,000 picks per side.
- **Instability:** 1–4 frozen rules per market had a positive selection lift that was ≤ 0 on the next season.
- **Calibration:** member reliability gaps are small for winners (≤ 0.05) but large for the score members' line probabilities
  (ridge/gbt 0.14–0.24 at their own extremes), which makes their confidence thresholds unreliable; stacking fixes much of it (≤ 0.03).
- **Missing information:** no historical bookmaker lines (line markets cannot be judged against the market), no historical
  pregame goalie listings, assumed injury-report timing.
- **Implementation:** none found by the checks (tests/test_selective_study.py, test_rule_tracking.py: outcome-blind rules,
  earlier-only selection, pushes, three-way draws, missing members, retries, effective dates).

### Tracked prospectively (from the registry seal; research only)
All 5,365 registered rules across 11 markets are evaluated on the stored two-hour cohorts (expanded cohort for NHL winner and
NBA lines, core elsewhere), one canonical decision per game/market/rule; 19 are flagged `selected` by the last chronological fold
(all `unqualified research`: highest-accuracy and top-lift rules). NBA winner rules containing `availability_two_hour` are
prospective-only. Registry `backend/app/models/combination_rules.json` (sha256 in the file). Shown on Performance →
"Combination rules" (selected / all registered; historical frontier labelled backtest; prospective record with matched baselines).

## Gap-filling combination study `gapfill-v1` (2026-10-02 21:53–21:55 UTC; exploratory)
Design: MODEL_SELECTION §16 (a87d802, before the run; code e18eb47). Command: `python -m app.cli gapfill-study` (reads saved
out-of-sample tables only; no store opened, no refit). Inputs fingerprinted in the report: `oof-nhl-assess` (NHL 2022–26),
`oof-nba-assess` (NBA 2025–26), `nba_evening` (2022–24, `avail_v1`). Evidence: docs/evidence/gapfill-v1/ (compact report,
fold summaries, Bonferroni-corrected probability tables); full report `data/reports/gapfill-v1/report.json`.

**Search consumed (configurations per market, budget 500):** NHL winner 497 (7 members; all 21 pairs, all 35 triples, 11 of 63
larger subsets), reg3 366 (6 members; every subset), puck line 182 and away team total 182 (5 members; every subset); NBA winner
497 (7 members; all pairs and triples, 11 of 63 larger), NBA spread/total/home TT/away TT 499 each (8 members; all 28 pairs,
35 of 56 triples — the mixed candidate+existing triples first — 1 of 162 larger); NBA evening winner 366 (6 members incl.
`avail_v1`; every subset, so the 7 untested `avail_v1` triples are now covered). NHL total and home team total: 0 (no shortlisted
candidate; their 3 existing members were already fully covered). Omitted subsets are listed per market in the report.

**Results**
- **Pick rules: nothing qualified** in any market or fold (Bonferroni over this family plus every earlier rule for the market:
  822–4,257). The frozen highest-accuracy / top-lift rules did not beat the best prior single's own top picks at matched
  coverage on the later season in a consistent way (e.g. NBA winner 2025-26: `unanimous_min elo+logistic_v2 ≥0.80` 84.0% on 150
  picks vs 86.0% for logistic_v2's top 150; NHL winner: frozen candidate triples 63.6–77.8% vs 69.1–83.3% for the best single).
- **Probability level, vs the best single chosen on earlier seasons (Bonferroni over the subsets of each market):**
  - NHL reg3: three candidate-only averages (`multinomial_l2` base or goalie + `poisson_draw:nhl_base`) −0.0017 to −0.0023
    log loss. But `poisson_draw:nhl_base` **alone** is −0.0036 in 3 of 4 seasons against the same reference, so the
    combination adds nothing beyond its strongest member (already a standalone pool source).
  - NBA home team total: 45 of 65 averages beat the reference, because the earlier-season choice (`gbt_points`, best on
    2024-25) was weak in 2025-26; this reflects an unstable single-model choice, not a combination effect. One season only.
  - Every other market: no subset average beats the best prior single.
- **Route B (with vs without a candidate):** adding a candidate often lowers the log loss of a weaker partner (e.g. NHL winner
  40 of 56 comparisons, NBA evening `avail_v1` 29 of 31), but no candidate made any combination better than the best single
  model at the same time, except as above. **No new combination-only source qualifies**; the sealed pool registry is unchanged.
- **Answer:** across every compatible pair and triple tested (and the larger subsets within budget), no model subset, agreement
  condition or confidence threshold improved later-season picks beyond the best single model's own confidence at the same
  coverage. The supported probability gains remain the standalone ones already in the registry (NHL reg3 / puck line / away
  total `poisson_draw:nhl_base`, `multinomial_l2`; NBA home total `avg:points_normal+cand:hetero_linear`).
- Limits: all seasons spent before (exploratory); line markets at the model's own line (no historical bookmaker lines); NBA
  has a single held-out season; 2-hour live cutoffs differ from the historical night-before/17:45 cutoffs.

## Feature tranche `features-v1` (MODEL_SELECTION §19; run 2026-10-03 00:34–00:35 UTC on scratch store copies; exploratory)
Evidence: docs/evidence/features-v1/ (both reports and the experiment log: 4 configurations, all succeeded, none skipped; the NHL walk ran twice — the second run only added the anchor comparison).
**NBA — player-quality-weighted absences** (3,667 games 2022–24 at the 17:45 ET cutoff; report times assumed as `avail_v1`):
| Model | Log loss | Brier | Accuracy |
|---|---|---|---|
| elo | 0.6320 | 0.2209 | 64.3% |
| logistic_v2 | 0.6300 | 0.2199 | 64.5% |
| avail_v1 (minutes only) | 0.6251 | 0.2177 | 66.0% |
| avail_q_v1 (minutes + quality) | 0.6235 | 0.2170 | 65.7% |
| avail_q_only (quality, no minutes terms) | 0.6266 | 0.2185 | 64.2% |
- `avail_q_v1 − avail_v1` log loss −0.0016, Bonferroni interval [−0.0037, +0.0005] → **no evidence of information beyond
  `avail_v1`** (the registered reading rule). Both variants beat `logistic_v2` (−0.0065 [−0.0124, −0.0006]; −0.0033
  [−0.0061, −0.0006]): the injury report matters, the quality weighting adds no demonstrable increment.
- Incremental check, average with Elo: top-25% accuracy 77.5% (avail_v1) vs 78.4% (avail_q_v1) vs 78.8% (avail_q_only), 917
  games each — differences within noise. Nothing changes live; `avail_q` is not recorded prospectively.
**NHL — shot volume and special-teams finishing** (same learner, same walk; special − base log loss, Bonferroni over 4):
| Market · learner | 2018–21 (4,877) | 2022–26 (6,993) |
|---|---|---|
| winner · logit_l2 | +0.00001 [−0.0015, +0.0015] | −0.0001 [−0.0013, +0.0010] |
| winner · poisson_draw | −0.0005 [−0.0014, +0.0005] | −0.0005 [−0.0012, +0.0002] |
| reg3 · poisson_draw | −0.0002 [−0.0013, +0.0009] | −0.0005 [−0.0013, +0.0003] |
| puck line · poisson_draw | −0.0004 [−0.0015, +0.0006] | −0.0006 [−0.0013, +0.0001] |
- Every standalone interval spans 0 → **no evidence of added information** under the registered reading rule.
- **With/without in the average with the market's anchor** (the §19 metric added after the independent review found it
  missing; rerun 01:01 UTC, same walk; special − base inside `average(anchor, X)`), assess 2022–26:
  winner (anchor xG) −0.0002 [−0.0008, +0.0003] (logit) / −0.0002 [−0.0006, +0.0001] (goal rates);
  **reg3 (anchor goals_draw) −0.00055 [−0.00095, −0.00015]**; **puck line (anchor goals_glm) −0.0005 [−0.00086, −0.00013]**
  (intervals Bonferroni over 4; still below 0 over all 8 comparisons). A small **combination-only lead** on reused seasons:
  the inputs may help an average without qualifying alone. The registered rule gates on standalone value, so nothing is
  recorded live from this study; a prospective test would need its own registration (combination-only source, as the pool's
  combination route).
