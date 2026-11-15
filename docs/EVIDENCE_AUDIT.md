# Evidence audit matrix: NBA / NHL, every market (read-only audit, 2026-10-02)

> **Update after the audit (2026-10-02 ~22:00 UTC):** the gaps listed in §3/§6 (candidates with members other than the
> strongest and with each other; the 7 `avail_v1` triples; the 3 NHL xG triples) were evaluated by `gapfill-v1`
> (MODEL_SELECTION §16; results in CHALLENGER_RESULTS "Gap-filling combination study"). Nothing qualified. Still not
> evaluated: NBA line-market triples beyond the 35-per-market budget (21 per market) and most 4+-member subsets (listed in
> docs/evidence/gapfill-v1/report-compact.json).

Code audited: `/Users/andry/Desktop/nba-predictor-poolsel` (branch `pool-selector`, HEAD e6b6abd, **not merged**). The live checkout
`/Users/andry/Desktop/nba-predictor-sprint1` is on branch `shadow-season` at 9d3e50e. It has no `candidates/selection.py`, no
`pool_rules.json`, no migration 0020, and its `rule_tracking.py` does not have the P3 `studied_members` fix.
Stores were opened with `file:...?mode=ro&immutable=1`. The plain `mode=ro` open failed with "unable to open database file", so the
row counts below are a snapshot of the files as they were last written: nba.sqlite 17:36 local, nhl.sqlite 17:27 local (21:27Z).

Abbreviations:
- **R** = `/Users/andry/Desktop/nba-predictor-sprint1/data/reports`
- **D** = `/Users/andry/Desktop/nba-predictor-sprint1/data`
- **E** = `docs/evidence` (in the repo)
- **MS** = docs/MODEL_SELECTION.md

Status labels:
- **impl** = implemented
- **hist** = historically evaluated, with a saved artifact
- **rec** = recording prospectively in the live store
- **elig** = selection eligible
- **sel** = actively selected (an active decision with a rule)

Season numbers are season end years (2024 = 2023-24).

Status summary: nothing anywhere is **sel**. All 60 `selector_decisions` (30 NBA, 30 NHL) are `insufficient_evidence` with 0 settled cohorts.
No settled prospective cohort exists. NBA has 0 two-hour rows of any kind (the season starts 2026-10-20).

---

## 0. Studies and their saved artifacts (all exploratory: every season used had been spent before)

| Study | Seasons: train / select / evaluate | Saved artifact | CLI (`python -m app.cli --sport X …`) |
|---|---|---|---|
| NBA Elo primary | warm-up 2020-21; tune and validate 2022-24; test 2025-26 run once | R/validation-2026-09-30T142603Z (and 5 other validation-* runs), R/test-2026-09-30T144035Z, D/eval/test_access.jsonl | `backtest` |
| NBA screening: logistic, logistic_v2, efficiency_margin | 2022-24 (validation, spent) | R/screening-2026-10-01T000121Z | `screen-candidates` |
| NBA research round 2: gbt_v1 | 2022-24 | R/research-gbt_v1-2026-10-01T005930Z | `research-gbt` |
| NBA research round 3: stacking | 2022-24 (stacker fitted on earlier seasons) | R/research-stacking-2026-10-01T012557Z | `research-stacking` |
| NBA 4-model agreement (descriptive) | 2022-24 | R/agreement-2026-10-01/{agreement,combinations,five_model_votes}.json | `scripts/agreement_analysis.py` (not a CLI command) |
| NBA markets: points_normal seal | grid tuned on 2022-23; holdout 2024 | R/nba-markets-2026-10-01T032904Z; `models/nba_market_decision.json` | `nba-market-research` |
| NBA avail_v1 (17:45 ET cutoff) | 2022-24 | R/nba-availability-2026-10-02T015402Z (the 014517Z run is superseded) | `nba-availability` |
| NHL primary: hockey_elo, goals_poisson | development 2018-21; validation 2022-24 run once; test 2025-26 run once | R/nhl-validation-2026-10-01T032305Z, R/nhl-test-2026-10-01T032343Z, D/eval/test_access_nhl.jsonl | `nhl-validate`, `nhl-test` |
| NHL learned members; agreement | 2018-21 | R/nhl-learned-…, R/nhl-agreement-…, R/nhl-agreement-explore-… | `nhl-learned`, `nhl-agreement`, `nhl-agreement-explore` |
| §5 combination/selection study | NBA warm-up 2020-21, evaluated 2022-24 (line stackers 2023-24 only); NHL warm-up 2016-17, evaluated 2018-21 | **Only the compact files** E/combination-study-2026-10-02/{nba,nhl}.json and summary.txt. There is no `R/combination-*` directory (run on scratch copies). | `combination-study` |
| §9 retrain cadence | as §5 | R/retrain-cadence-{nba,nhl}-20261002 | `retrain-cadence` |
| §10 CatBoost (rejected) | tune NBA 2022-23 / NHL 2018-19; held-out NBA 2024 / NHL 2020-21 | R/catboost-{nba,nhl}-20261002 | `catboost-challenger` |
| §11 combination search | rolling origin: NBA select 2022→assess 2023, 2022-23→2024; NHL 2018→2019, 2018-19→2020, 2018-20→2021 | R/search-{nba,nhl}-20261002 (`all_rules` is kept for every fold) | `combination-search` |
| §12 NHL xG candidate | warm-up 2016-17; scored 2018-21 | R/nhl-xg-20261002 | `nhl-xg-research` |
| §14 expanded-v1 | rolling origin as §11 | R/expanded-{nba,nhl}-20261002 | `expanded-search` |
| §15 selective-v1 | rolling origin as §11; saved tables D/research/tables/{nba,nba_evening,nhl}.json.gz | R/selective-{nba,nhl}-20261002 (`all_rules` per fold); sealed `models/combination_rules.json` | `selective-study`, `rules-registry` |
| pool-v1 stage 1 (screen) | NBA 2022-24; NHL 2018-21 | R/pool-{nba,nhl}/screen.json, shortlist.json; E/candidate-pool/*-screen.json | `pool-screen` |
| pool-v1 stage 2 (assess) | NBA 2025-26; NHL 2022-26. **These are the primaries' former validation/test seasons, reused** | R/pool-{nba,nhl}/assess.json; E/candidate-pool/*-assess.json; D/eval/pool_assess_access_*.jsonl; D/research/candidates/{experiments.jsonl,oof-*.json.gz} | `pool-assess` (logged; run once) |
| pool-v1 registry | built from the stage-2 evidence | `candidates/pool_rules.json` (effective 2026-10-02T20:22:45Z) | `pool-rules`, `pool-report`, `pool-roster` |

Notes on rerunning:
- These commands should be run on store copies. HANDOFF says every CLI start runs `db.migrate`.
- `nhl-test` refuses to rerun.
- `pool-assess` is logged and intended to run once.

---

## 1. Models per market (item 1) and retraining cadence (item 6)

Cadence sources: docs/TRAINING_AUDIT.md (table), MS §13, `registry.Spec.refit = "season"`, and live `model_fits` (NHL: 14 fits, all `season_start`, trained_season 2027, 14,508 training games).

**What refits, and how often**
- **Online models update after every result**, replayed each run: `elo`, `hockey_elo`, `points_normal`, `goals_poisson`, and the efficiency ratings.
  Their sealed hyperparameters are never re-tuned (`frozen_decision.json`, `nhl_frozen_decision.json`, `nba_market_decision.json`).
- **Learned research members refit at season start only**: `logistic*`, `gbt_v1`, `hockey_*`, `goals_draw`, `goals_glm`, `ridge_points`, `gbt_points`, and the σ of `efficiency_margin`.
  Current-season finals reach their features but not their weights until next season.
- **The exception: `ridge_points@weekly` and `gbt_points@weekly`** refit at season start and at the first forecast of each ISO week.
- **Stackers** refit every run on the last two completed seasons, so their weights are constant within a season.
- **Pool candidates** refit at season start only.
- **Never refreshed:** both sealed rule registries, the selector thresholds, the sealed primary configurations, and the CatBoost code (rejected; not live).

| Sport · market | Primary (impl) | Core research members (impl) | Weekly / shadow (impl) | expanded-v1 cohort members | Candidate pool specs producing this market (impl) |
|---|---|---|---|---|---|
| NBA winner | `elo` (online) | `logistic`, `logistic_v2`, `efficiency_margin`, `gbt_v1` (season start); `availability_two_hour` (season start, votes only; not in the average or stacker) | none | none (NBA winner has no expanded cohort) | 10: `logit_l2:nba_base` (ref), `extra_trees`, `random_forest`, `mlp`, `ridge:nba_base` (ref), `elastic_net`, `extra_trees:normal`, `mlp:normal`, `ngboost` (optional dependency), `hetero_linear` |
| NBA spread / total / team_total_home / team_total_away | `points_normal` (online) | `ridge_points`, `gbt_points` (season start) | `ridge_points@weekly`, `gbt_points@weekly` (weekly) | all 5 members | 6 normal heads: `ridge` (ref), `elastic_net`, `extra_trees:normal`, `mlp:normal`, `ngboost`, `hetero_linear` |
| NHL winner | `hockey_elo` (online) | `goals_poisson` (online), `hockey_logistic`, `hockey_gbt` (season start) | `hockey_logistic_xg` (season start; shadow: never votes, averages or stacks) | all 5 incl. xG | 9: `logit_l2` base (ref) / goalie, `extra_trees` base / goalie, `mlp` base, `multinomial_l2` base / goalie, `poisson_draw` base / goalie |
| NHL reg3 | `goals_poisson` | `goals_draw`, `goals_glm` (season start) | none | none | `multinomial_l2` base / goalie, `poisson_draw` base / goalie |
| NHL spread / total / team_total_home / team_total_away | `goals_poisson` | `goals_draw`, `goals_glm` | none | none | `poisson_draw` base / goalie |

Code locations:
- `agreement.NAMES` / `SHADOW` (agreement.py:17-20)
- `research_live.LINE_MEMBERS` / `SHADOW_LINE_MEMBERS` (research_live.py:23-26)
- `candidates/registry.py:43-86`: 19 specs
- `sports.py:28,35` lists the markets. No NHL team-total-specific model and no period or player markets exist.

---

## 2. Historical evaluation (item 2) and periods (item 4), per market

Every row here is exploratory: the seasons were already spent.

| Market | Evaluated historically (study → report) | Not evaluated historically |
|---|---|---|
| NBA winner | Elo test 2025-26; logistic, logistic_v2, efficiency_margin (screening); gbt_v1 (round 2); stackers (round 3); §5 (5 members + average + stack); §9 cadence; §10 CatBoost; §11; §15 (`nba` table, 5 members; `nba_evening` table + `avail_v1`); pool stage 1 (10 specs) and stage 2 (`elastic_net`, `hetero_linear`) | `availability_two_hour` at its own two-hour cutoff: no historical inputs. Only the 17:45 ET proxy `avail_v1` was evaluated, with assumed report times. |
| NBA spread / total / TTs | `points_normal` (nba-markets: tune 2022-23, holdout 2024); §5 (3 members); §9; §10 (margin only); §11 (5 members incl. weekly); §14 (27 rules); §15 (555 rules); pool stage 1 (6 specs); stage 2 shortlist (spread: et, hetero, elastic net; total and home TT: ngboost, et, hetero; away TT: hetero, et, elastic net) | `points_normal` was never scored on the reserved test seasons before pool stage 2. No bookmaker-line evaluation exists (every result is at the primary's model-only line). |
| NHL winner | `hockey_elo` dev / validation / test; nhl-learned; nhl-agreement; §5 (4 members); §9; §10; §11 (4 members); §12 xG; §14 (36 rules); §15 (5 members); pool stage 1 (9 specs); stage 2 (`multinomial_l2:nhl_base`, `mlp:nhl_base`) | — |
| NHL reg3 | `goals_poisson` validation / test (R/nhl-test); §5; §9 (`goals_draw`, `goals_glm`); §11 (345 rules); §15 (255 rules); pool stage 1 (4); stage 2 (`multinomial` base / goalie, `poisson_draw` base) | `poisson_draw:nhl_goalie` was not shortlisted for reg3 |
| NHL puck line, away TT | as reg3; §11 (405 rules); §15 (195); stage 2 (`poisson_draw` base / goalie) | — |
| NHL total, home TT | as reg3; §11; §15; pool **stage 1 only**: nothing shortlisted, so no stage 2 | candidates on held-out seasons |

---

## 3. Combinations and filters actually evaluated (item 3)

Rule counts are per market, per fold, or per family as stated. Each comes from the saved report.

### Rule families

| Family | Subsets | Methods | Thresholds / conditions | Rules |
|---|---|---|---|---|
| §5 (E/combination-study) | each member alone; **pairs with the primary only**; all members | average; OOF stacker; strict majority; unanimous; majority or unanimous + average ≥ t; confidence-only filters at matched coverage; weekly selector simulation | t 0.55/0.60 (reg3 0.45/0.50) | small, fixed set |
| §11 (R/search-*) | **every pair, every triple, and all members** for pick rules; every subset of every size for probability averages (`per_subset`, NBA sizes 1-5, NHL winner sizes 1-4) | `avg≥t`, `unanimous`, `unanimous&avg≥t`, `unanimous&similar(0.05)`, `majority(all)`, `single≥t`, `stack≥t` | t 0.50-0.75 (reg3 0.40-0.60); conditions all / close / clear / early / late | NBA 1,655 per market per fold; NHL winner 925; reg3 345; NHL lines 405 |
| §14 expanded (R/expanded-*) | NHL winner: the 4 xG pairs, 3 xG triples, all 5. NBA lines: pn+ridge@w, pn+gbt@w, ridge@w+gbt@w, that triple, all 5 | `unanimous`, `avg≥t`, `majority(all)`, stack (NHL: all 5; NBA: triple and all 5) | t 0.55/0.60/0.65; no conditions | NHL winner 36; NBA 27 per line market |
| §15 selective (R/selective-*) | every pair; 3 registered triples (NBA lines, NBA winner, NHL winner); full set | `single`, `unanimous_min≥t`, `avg≥t`, `unanimous&maxdis≤d` (0.03, 0.06), `majority`, `stack(all)≥t` | winner 0.60-0.80; reg3 0.45-0.60; lines 0.55-0.65; conditions all / close / clear / low / high disagreement | NBA winner 895; NBA evening 1,170; NBA lines 555 each; NHL winner 895; reg3 255; NHL lines 195 each |
| pool stage 2 (R/pool-*/assess.json) | candidate alone; average with the strongest; triple (strongest + 1 fixed partner + candidate) with vs without; stacker of existing members with vs without the shortlist | `cand≥t`, `pair avg≥t`, `agree(strongest, cand)&avg≥t`, `strongest≥t`; selector-v1 simulation | t 0.55-0.70 (reg3 0.45-0.55) | NBA 264 (winner 40, lines 56 each); NHL 162 (winner 40, reg3 42, puck line 40, away TT 40); NHL total and home TT: none |

### Pair and triple coverage, by market

| Market | Every pair covered? | Every triple covered? |
|---|---|---|
| NBA winner (5 core) | **yes** (§11, 10/10) | **yes** (§11, 10/10) |
| NBA winner incl. `avail_v1` (§15 evening) | 15/15 pairs | 3/20 triples (see §6) |
| NBA lines (5 incl. weekly) | **yes** (§11) | **yes** (§11) |
| NHL winner, 4 core | yes (§11) | yes (§11) |
| NHL winner incl. xG | 10/10 pairs (§15) | 7/10 triples (see §6) |
| NHL 3-member markets | all 3 pairs (§11, §15) | the full triple (§11, §15) |
| Pool candidates | only candidate + strongest | only one fixed triple partner |

For pool candidates, these were never evaluated: candidate + any other existing member, and candidate + candidate.

---

## 4. Prospective recording (item 7): code in jobs.py, counts from live stores (read-only)

`jobs.predict_now` (jobs.py:290-341) runs, inside the two-hour window, in this order:
1. primary winner
2. `market_forecasts`
3. `research_forecasts` (agreement members, including shadows)
4. `research_selection`: core and expanded-v1 cohorts, plus core selections
5. `candidate_pool`: all 19 specs; NGBoost is skipped if not installed
6. `pool_selection`: **this branch only**

`selector_decisions` runs from the odds job every Monday at 14:00 UTC, with pool decisions **on this branch only** (jobs.py:425-441).
Rule tracking (`rule_tracking.report`) **writes nothing**. It evaluates stored cohorts when the API is read (`/api/research/rules`, main.py:218).

| Store | research_cohorts | candidate_forecasts | selector_decisions | research_selections | model_fits | pool_decisions / pool_selections |
|---|---|---|---|---|---|---|
| NBA | 0 | 0 | 30 (6 definitions × 5 markets; all insufficient_evidence) | 0 | 0 | tables absent (no 0020) |
| NHL | 16 for 2 games (all `scheduled`): core 2 per market; winner core 3 + expanded-v1 3; definition 5040a9e7ad2ab51c | 63 (9 model versions winner, 4 reg3, 2 per line market) | 30 (5 definitions × 6 markets; all insufficient_evidence) | 18, all `no_pick` (insufficient evidence) | 14 (5 research members + 9 candidates) | absent |

Status per market:
- NHL, every market: **rec** for core cohorts, **rec** for the candidates.
- NHL winner: also **rec** for expanded-v1.
- NBA: rec in code only; no rows until 2026-10-20.
- Pool selection: not running anywhere live.

---

## 5. What the selector can choose (item 8)

| Market | Core selector-v1 sources (`research_live.sources`; elig, none sel) | Pool registry sources (`pool_rules.json`; elig only after merge; none sel) | Sealed `combination_rules.json` (tracking only, **never a selector source**) |
|---|---|---|---|
| NBA winner | elo, logistic, logistic_v2, efficiency_margin, gbt_v1, availability_two_hour, average, stack: 8 sources × 3 filters × 6 thresholds = 144 rules | none | 1,170 rules; 365 prospective-only; selected: `unanimous_min\|logistic+gbt_v1\|0.8\|clear`, `…\|high` |
| NBA spread | points_normal, ridge_points, gbt_points, ridge@weekly, gbt@weekly, average, stack (126) | none | 555 rules; selected: `avg\|ridge_points+gbt_points@weekly\|0.6\|close`, `unanimous_min\|ridge@w+gbt@w\|0.6\|high` |
| NBA total | same 7 sources | none | selected: `unanimous_min\|ridge_points+gbt_points\|0.6\|close`, `unanimous_min\|ridge_points+gbt_points@weekly\|0.65\|all` |
| NBA team_total_home | same 7 sources | `avg:points_normal+cand:hetero_linear:nba_base` (combination route) | selected: `avg\|ridge_points+gbt_points\|0.65\|clear`, `unanimous_min\|ridge_points+ridge_points@weekly\|0.6\|close` |
| NBA team_total_away | same 7 sources | none | selected: `single\|gbt_points@weekly\|0.65\|close` |
| NHL winner | hockey_elo, goals_poisson, hockey_logistic, hockey_gbt, hockey_logistic_xg, average, stack (126) | none | 895; selected: `unanimous_min\|xg+hockey_logistic+hockey_gbt\|0.7\|clear` |
| NHL reg3 | goals_poisson, goals_draw, goals_glm, average, stack (5×3×5 = 75) | `cand:multinomial_l2` base / goalie, `cand:poisson_draw:nhl_base` (standalone), and their averages with `goals_draw` and with `goals_draw+goals_poisson` (9 sources) | 255; selected: `single\|goals_glm\|0.5\|close`, `stack\|all\|0.55\|low` |
| NHL spread | 5 sources (90) | `cand:poisson_draw` base / goalie, plus averages with `goals_glm` and with `goals_glm+goals_poisson` (6) | 195; selected: `avg\|goals_poisson+goals_draw\|0.6\|high`, `stack\|all\|0.65\|low` |
| NHL total | 5 sources | none | selected: `avg\|goals_poisson+goals_glm\|0.6\|high` |
| NHL team_total_home | 5 sources | none | selected: `single\|goals_draw\|0.6\|low`, `avg\|all\|0.6\|high` |
| NHL team_total_away | 5 sources | `cand:poisson_draw` base / goalie, plus averages with `goals_draw` and with `goals_draw+goals_poisson` (6) | selected: `avg\|all\|0.6\|close`, `stack\|all\|0.6\|low` |

Notes on the sealed registry:
- All 19 "selected" rules are **unqualified research** (highest-accuracy or top-lift picks).
- **No rule qualified** in §11, §14, §15 or pool stage 2 picks under the Bonferroni bar.

---

## 6. Result per market (item a) and pair/triple subsets never evaluated (item b)

| Market | Best supported finding (all exploratory) | Never evaluated |
|---|---|---|
| NBA winner | **No improvement from combining.** Details below this table. | Triples containing `avail_v1`/`availability_two_hour` (7 of 10, listed below the table). All candidate pairs except with `logistic_v2`; all candidate triples except `logistic_v2+gbt_v1+cand`; anything with `availability_two_hour` at its own cutoff. |
| NBA spread | ridge_points / stacker − points_normal −0.0063 / −0.0070 (§5), but at the primary's own model line, where the primary is ~50/50 by construction. No gain over `ridge@weekly` (§14, pool). Nothing qualified. | none among the 5 core and weekly members (§11 covered every pair and triple). Pool: et, hetero and elastic net only with `ridge@weekly` and the `ridge@weekly+gbt_points` triple. |
| NBA total | expanded stacker(pn+ridge@w+gbt@w) − pn **−0.0027 [−0.0054, −0.00002]** (R/expanded-nba…/total `stack:triple`), the only gain and borderline; candidates worse | as spread. Pool: only with `points_normal` (and the `+ridge_points` or `+gbt_points` triple). |
| NBA team_total_home | avg(points_normal + hetero_linear) − pn −0.0034 [−0.0062, −0.0003] (pool stage 2; a single result among many) → the only pool source | as total |
| NBA team_total_away | §5 stacker − primary −0.0018 [−0.0034, −0.0002]; expanded stacker −0.0020 [−0.0039, −0.0002] vs pn, but spans 0 vs `ridge@weekly`; no pool candidate qualified | as total |
| NHL winner | `hockey_logistic_xg` Brier −0.0011 [−0.0019, −0.0002] vs hockey_logistic (R/nhl-xg); **no combination improves on xG** (§14, §15, pool) | xG triples: `xg+goals_poisson+hockey_gbt`, `xg+goals_poisson+hockey_logistic`, `xg+hockey_elo+hockey_gbt`; every 4-member subset containing xG. Pool: multinomial and mlp only with xG and the `xg+goals_poisson` triple. |
| NHL reg3 | **Strongest finding:** `poisson_draw:nhl_base` − goals_draw −0.0073 [−0.0091, −0.0053] held-out 2022-26, every season; multinomial −0.0066; goals_draw − primary −0.0106 (§5) | none for the core members; pool candidates only with goals_draw and goals_draw+goals_poisson, never with goals_glm or each other |
| NHL puck line | `poisson_draw:nhl_base` − goals_glm −0.0049 [−0.0065, −0.0032] held-out; ≥0.65 picks +7.7 points vs the strongest's top games at the same count (assess.json) | pool candidates never with goals_draw, nor base with goalie |
| NHL total | **no improvement**: average worse than primary (+0.0014); candidates +0.003-0.004 worse | candidates beyond stage 1 |
| NHL team_total_home | **no improvement** | candidates beyond stage 1 |
| NHL team_total_away | `poisson_draw:nhl_base` − goals_draw −0.0030 [−0.0048, −0.0011] held-out | as puck line, with goals_glm |

NBA winner details:
- logistic_v2 log loss 0.6297 vs Elo 0.6316 (n.s.).
- `avail_v1` (17:45 ET) − logistic_v2: −0.0049 log loss [−0.0088, −0.0009].
- 80-85% accuracy appears only at 3-20% coverage and is matched by confidence alone.

NBA winner triples never evaluated (`avail` = `avail_v1`, or `availability_two_hour` live):
- `avail+eff+elo`
- `avail+eff+logistic`
- `avail+eff+logistic_v2`
- `avail+elo+gbt_v1`
- `avail+gbt_v1+logistic`
- `avail+gbt_v1+logistic_v2`
- `avail+logistic+logistic_v2`

---

## 7. Missing or invalid evidence, defects, superseded claims (item 9)

### No saved artifact

| # | Item |
|---|---|
| 1 | The user's earlier ~83% system has no artifact (CHALLENGER_RESULTS §15; HANDOFF). |
| 2 | The §5 study is kept only as compact evidence (E/combination-study-2026-10-02). The full report, and runs 1 and 2 described in COMBINATION_RESULTS, are not in R. |

### Validity limits and potential leakage

| # | Item |
|---|---|
| 3 | **Every** historical result is exploratory: the seasons were spent. Pool stage 2 reused the NBA 2025-26 and NHL 2022-26 validation/test seasons, so the test seasons are no longer clean anywhere. |
| 4 | Line markets were judged only at the primary's model-only line, with no historical bookmaker lines. The primary is ~50/50 there, so "lift vs primary confidence" is degenerate (COMBINATION_RESULTS). Line stackers are trained at the model line but applied live at bookmaker lines (MS amendment). |
| 5 | The retrospective studies used the night-before cutoff; live uses two-hour (MS §3). Assumed availability times: `avail_v1` injury report times; NHL shots `historical_backfill`; pool goalie inputs use the **actual** starter (an optimistic upper bound). `avail_v1` had one training-side leak, fixed before the cited run (AVAILABILITY_RESULTS). |
| 6 | The expanded-v1 NBA part was not blind: the rules sat inside the §11 family, whose results had already been seen (MS §14). |

### Defects in code and registries

| # | Item |
|---|---|
| 7 | The NBA winner sealed registry was built with 6 members (rule_tracking.py:52-68). The studied 5-member all-set rules and the 5-member `stack(all)` are **absent**; they were replaced by 6-member versions marked prospective-only (365 prospective-only rules). The `nba_evening` family was skipped, so its `avail_v1` triples are not tracked. The live checkout also lacks P3 (`studied_members`). |
| 8 | Pool selection, the P1-P14 fixes and migration 0020 existed only on unmerged `pool-selector` at audit time. Since 2026-10-02 ~22:00 UTC every settlement path voids on any start change (MODEL_SELECTION amendment; new core definition); the 12 h rule is retired. Merge status: HANDOFF. |
| 9 | TRAINING_AUDIT gaps remain: the NBA market seal has no data-fingerprint check; numpy/scipy are undeclared; the research `definition` hash changes on any code edit. 6 NBA and 5 NHL definitions already exist, and each change restarted the selector's evidence. |

### Superseded or corrected claims

| # | Item |
|---|---|
| 10 | CatBoost "first ML dependency" (wrong; corrected). MARKETS "winner prob stored" (corrected). avail run 014517Z (superseded). Weekly shadows briefly counted in line agreement (fixed before any cohort). Dixon-Coles 1-1 share 3.7% → 3.6%. The `gbt.py` "never imported" docstring was stale. |
