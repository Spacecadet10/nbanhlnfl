# Model agreement, combination and selection — design (recorded 2026-10-02, before any experiment in it is run)

Scope: NBA and NHL, every market the app supports (docs/MARKETS.md). Research and shadow only: nothing here replaces a primary
model, places a bet or moves money. Changes to this design are dated amendments at the end; results go in docs/COMBINATION_RESULTS.md.

## 1. Audit (state at ef56512, verified in code and in the real stores on 2026-10-02 10:35 UTC)

| Sport | Market | Models before this work | Inputs | Storage | Settlement | Gaps found |
|---|---|---|---|---|---|---|
| NBA | winner | primary `elo` (sealed); research `logistic`, `logistic_v2`, `efficiency_margin`, `gbt_v1`, `availability_two_hour` | results; box-score efficiency; injury report (availability only) | `predictions` (primary: `…-window=2h-policy=near_tipoff`; research: `research-v2:<model>:<digest>`, feature `agreement-research-v2:two-hour`) | final score incl. OT | agreement votes only; no stored combined probability; no stacker live; thresholds descriptive only |
| NBA | spread, total, team_total_home/away | `points_normal` (sealed) only | results | `market_forecasts` (`…-window=2h-policy=near_tipoff`) | exact line, push on integer lines | **one model per market**: no agreement possible |
| NHL | winner | primary `hockey_elo` (sealed); research `goals_poisson`, `hockey_logistic`, `hockey_gbt` | results (+decision) | as NBA | final incl. OT/SO | as NBA winner |
| NHL | reg3, spread (puck line), total, team totals | `goals_poisson` (sealed) only | results + decision | `market_forecasts` | reg3: OT/SO = draw; lines: final incl. SO goal | **one model per market**; draws under-predicted in the one-time test (16% vs 23%) |

Other findings:
- Two-hour rule: enforced by DB triggers (migration 0015) on `predictions` (feature `%two-hour%`) and `market_forecasts`
  (`%-window=2h-policy=near_tipoff`), plus calculation-completion re-checks in `agreement.predict` and `market_live.forecast`.
  The trigger re-reads the game's current `tipoff_at` at insert time (the "recheck before save").
- Rescheduling is treated differently by readers: winner tracking voids on **any** start change; market tracking uses the
  12-hour rule of `markets.settle`. New research records below use the stricter rule (any change → void).
- `models/gbt.py` says it is "never imported by the API or the jobs"; since ef56512 the agreement job imports it. Stale docstring
  (corrected with this work). scikit-learn (dev extra) is therefore already a live dependency of the research step.
- docs/MARKETS.md says the distribution model's winner probability "is stored as a separate model version"; `market_forecasts`
  does not allow `winner` and nothing stores it. The statement is corrected in MARKETS.md; this work does not add it.
- Real stores: NBA 27,744 backtest winner rows, 0 market rows; NHL 8,376 backtest + 292 live winner rows, 480 live (night-before
  archive) market rows. Zero two-hour rows so far. NHL 2026-27 regular season is under way (next game 2026-10-02 22:30Z);
  NBA regular season starts 2026-10-20.

## 2. Candidates (fixed; no search)

Winner (existing definitions, unchanged): NBA core = `elo`, `logistic`, `logistic_v2`, `efficiency_margin`, `gbt_v1`
(+ `availability_two_hour`, voting only); NHL core = `hockey_elo`, `goals_poisson`, `hockey_logistic`, `hockey_gbt`.

Line / 3-way markets — every candidate yields a full score distribution, so all its markets are coherent:

| Sport | Name | What it is | Different because |
|---|---|---|---|
| NBA | `points_normal` | existing sealed primary: online offence/defence point ratings, Normal margin & total | — |
| NBA | `ridge_points` | ridge regression (λ = 10 on standardised features) of margin and of total on point-in-time efficiency (ortg/drtg/pace of both teams, margin_est), Elo difference, last-10 margin difference, rest and back-to-backs; refit each season start on earlier seasons | possession-efficiency inputs + regression, not online gradient ratings |
| NBA | `gbt_points` | gradient-boosted regression trees (scikit-learn HistGradientBoostingRegressor, fixed settings below) on the same features, separately for margin and total | non-linear; one bounded configuration |
| NHL | `goals_poisson` | existing sealed primary: independent Poisson regulation goals, OT share | — |
| NHL | `goals_draw` | same rates as `goals_poisson`, regulation score distribution diagonally inflated (Karlis & Ntzoufras 2003): p′ = (1−w)·p + w·p·[h=a]/P(h=a); w fitted at each season start by maximum likelihood (grid 0…0.30 step 0.005) on earlier seasons' regulation scores and the rates recorded at prediction time | addresses the documented draw under-prediction; changes the joint/dependence structure |
| NHL | `goals_glm` | two L2 Poisson GLMs (scikit-learn PoissonRegressor, α = 1, standardised features) for home and away regulation goals on Elo difference, last-10 goals for/against of both teams, rest and back-to-backs; OT share as `goals_poisson`; refit each season start on earlier seasons | regression on team form, no online attack/defence ratings |

`gbt_points` settings: squared error, learning_rate 0.05, max_iter 200, max_depth 3, max_leaf_nodes 8, min_samples_leaf 50,
l2_regularization 1.0, early_stopping False, random_state 20260930.
Spread of the Normal for `ridge_points` / `gbt_points` = root-mean-square of **this model's own out-of-sample** residuals from
earlier seasons (predictions made before each game). A model is available only from the first season that has such residuals;
before that it is reported as `not_ready`, never replaced by another model.
A reference `points_league` / `league_poisson` (no team information) is reported in the retrospective study only.

Not added (reported as gaps): no team-total-specific model; no player-level input for any line market; no market-specific GBT for NHL
(low scoring counts; the GLM already covers regression). Availability (injuries) stays winner-only.

## 3. Feature availability
All inputs are those of the existing models: results delivered only when `result_available_at` ≤ the forecast's information time;
box-score efficiency only after its own and its game's result availability; rest from earlier scheduled tipoffs. Live research
forecasts use everything available at the actual calculation time inside [tipoff − 2 h, tipoff). Retrospective study rows use the
existing night-before cutoff (the only cutoff with verified historical availability); this is a documented difference from live.

## 4. Combinations and rules (per sport and market)
Members: the 3 line-market candidates, or the winner core set.
- **Agreement** (picks only): pick = side with the highest probability (reg3: home / draw / away — a draw is a pick); a member with
  equal top probabilities abstains. *Pairwise* (each pair of members), *strict majority* (> half of all expected members agree),
  *unanimous* (all expected members present and agree). Only complete cohorts count for majority/unanimous.
- **Equal average**: mean of member probability vectors (pushes kept). Requires all members.
- **Stacker** (`stack`): log-linear pooling p_c ∝ exp(b_c + Σ_i w_i log p_i,c), L2 penalty 1.0 on (w_i − 1/k) and on b, fitted by
  deterministic gradient descent. For line markets it pools the two non-push sides conditionally and keeps the average push
  probability. Trained only on **chronological out-of-fold** member forecasts: base predictions made before each game, from seasons
  strictly before the season being forecast, whose results were available before that season's first forecast; one row per game.
  Live: refit at each season start on the last two completed seasons' walk-forward member forecasts (lines: the primary's model
  line, as no historical bookmaker lines exist).
- **Agreement + confidence** (limited set): unanimous and average ≥ t, majority and average ≥ t, t ∈ {0.55, 0.60} (reg3: {0.45, 0.50}).

Comparisons for every rule: frozen primary; best single candidate; equal average; confidence-only filtering of primary and of the
average at **matched coverage** (same share of games). Conditional diagnostics: pairwise disagreement rates and who was right,
per-game loss correlation. The question "does agreement add anything beyond confidence?" is answered by agreement vs matched-coverage
confidence filters on identical seasons, with paired week-block bootstrap intervals.

## 5. Retrospective study (bounded; run once)
- Seasons by end year. NBA: warm-up 2020-2021, evaluated 2022-2024 (2021-22 … 2023-24, the existing "validation" seasons).
  NHL: warm-up 2016-2017, evaluated 2018-2021 ("development"). Line candidates become available one season after their first fit
  (§2), so line-market stackers are first evaluated a season later; the report states each method's evaluated seasons. Both were already used for model selection: results are **exploratory**, labelled
  retrospective, and are never shown as prospective performance. NBA 2025/2026 and NHL validation/test seasons are not evaluated.
- Line markets are evaluated at the primary's model-only line (live rule `markets.model_line`), the only line available historically.
- One run per sport; no setting is changed afterwards. Budget: ≤ 2 full runs per sport (a second only if a defect is found, recorded).
- The selector (§6) is also simulated week by week over the evaluated seasons with evidence starting empty, to test the machinery.

## 6. Adaptive selector (shadow / challenger)
Separately for each sport and market. Registered rule set = source × filter × threshold:
sources = primary, each member, average, stack; filters = none, majority, unanimous;
thresholds = {0.50, 0.55, 0.60, 0.65, 0.70, 0.75} (reg3: {0.40, 0.45, 0.50, 0.55, 0.60}). A game passes when the source's
probability of its pick ≥ threshold and the filter holds.

- **Schedule:** a decision every Monday 14:00 UTC (before any game). Evidence = settled research cohorts of the current research
  definition whose result was available at or before that scheduled time; one canonical (latest pre-start) cohort per
  game/market. The decision is effective from its actual write time until the next scheduled time; forecasts made before it was
  written use the previous decision. Never applied to its own evidence period. A late or missed run writes nothing retroactively.
- **Primary selection metric:** for each rule, lift = (wins − Σ reference probability of the picked side) / n over its settled
  picks (pushes and voids excluded). Reference = de-vigged bookmaker probability at that exact line when the cohort used a bookmaker
  line; otherwise the evidence window's frequency of the picked outcome class (home/draw/away, over/under, home/away cover) across
  all its settled cohorts. This removes class balance (home teams, draws, over/under at a line) instead of ranking raw win %.
- **Uncertainty / multiple comparisons:** one-sided lower bound lift − z·se, se from the Bernoulli residuals, z Bonferroni-adjusted
  for the number of registered rules in that market (e.g. NBA winner 8 sources × 3 × 6 = 144 rules → z ≈ 3.19). Game dependence
  is not modelled (stated in the UI).
- **Minimum evidence:** ≥ 100 settled picks, coverage ≥ 10% of settled cohorts, evidence spanning ≥ 3 distinct weeks. At n = 100 a
  rule needs a lift of roughly +0.15 to pass at z ≈ 3; at n = 400, roughly +0.08. Small samples cannot promote a rule.
- **Choice:** highest lower bound > 0; ties → higher coverage → registry order (primary first). Hysteresis: the incumbent rule is
  kept while its own bound is > 0 unless a challenger's bound is higher by ≥ 0.01. No rule passes → status `abstain` (market gives
  no research pick). No evidence → `insufficient_evidence`.
- **Market selection for a game:** among markets whose decision is active and whose rule passes for that game, recommend the one with
  the highest decision lower bound; at most one recommendation per game; otherwise **no pick**. Every market's reason (not active,
  below threshold, filter failed, missing member, outranked) is stored. Payouts are not used: without contemporaneous odds for every
  market there is no betting-value claim; with odds, the reference already uses the de-vigged price at that line.
- **Two objectives kept apart:** prediction quality (log loss, Brier, calibration, accuracy vs coverage) is what this selects.
  Betting value would need contemporaneous actionable odds for all compared markets and a separate protocol — not done here.
- **Promotion (preregistered, manual, never automatic):** the selector's choice may be proposed to replace a primary only after
  ≥ 800 (NBA) / 1,000 (NHL) complete settled cohorts in that market from the forward record, with the paired log-loss difference
  (chosen source − primary) week-block 95% upper bound < 0 and matched-coverage pick accuracy not lower. A dated human decision follows.
- **Versioning / rollback:** selector version = hash of selector code and registry; research definition = hash of member code,
  settings and scikit-learn version. A change starts a new version with empty evidence; old decisions, cohorts and selections stay.
  Rollback = run the old code version; its rows remain distinguishable. Off switch: `RESEARCH_SELECTION=0` disables research
  cohorts, decisions and selections in the jobs (primary forecasts unaffected).

## 7. Storage (migration 0016; append-only, idempotent)
- Member forecasts: `predictions` (winner, existing) and `market_forecasts` (lines/reg3: model version
  `research-v3:<name>:<digest>-window=2h-policy=near_tipoff`, same line, line source and quote id as the primary in that run).
- `research_cohorts`: game, market, exact line, line source, quote id, information time, completion time, scheduled start, member
  forecast ids and probability vectors, missing members with reasons, average and stack vectors, stack weights digest, agreement
  counts, research definition. Key = game, market, line, line source, quote, definition, member data digest (a retry is the same row; a
  new line/input/model snapshot is a new row).
- `selector_decisions`: sport, market, selector version, scheduled time, write time, effective interval, evidence window and stats,
  eligible rules, chosen rule and threshold, status and reason.
- `research_selections`: per cohort and decision: status (pick / no_pick), side, probability, threshold, reasons, and whether this
  market is the game's recommendation.
- All three: UPDATE/DELETE rejected; inserts must be inside [tipoff − 2 h, tipoff) for the game's current start. Performance uses one
  canonical row (latest pre-start) per game/market/definition (cohorts) or per game/market/decision (selections).

## 8. Sources inspected
- Karlis & Ntzoufras (2003), *Analysis of sports data by using bivariate Poisson models*, JRSS D 52:381-393 — abstract only
  (https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/1467-9884.00366): diagonal inflation improves draw prediction. Used: the
  diagonal-inflation idea only, simplified to independent margins.
- scikit-learn 1.9.1 `PoissonRegressor` docs (L2 α, lbfgs, log link); `HistGradientBoostingRegressor` already used by `gbt_v1`.
- scikit-learn StackingClassifier warning and Yao et al. stacking (docs/STACKING_RESEARCH.md): stacker trained only on held-out
  chronological predictions. No prediction repository copied.

## 9. Retraining-cadence comparison (recorded 2026-10-02 12:57 UTC in 602aeab, before any run)
Question: do the existing learned members forecast better when refit more often than at season start? Same definitions,
features, hyperparameters and data as the study in §5; only the refit schedule changes. Code: `app/retrain_cadence.py`.
- **Members (unchanged):** NBA `logistic`, `logistic_v2`, `gbt_v1`, `ridge_points`, `gbt_points`; NHL `hockey_logistic`,
  `hockey_gbt`, `goals_glm`, `goals_draw`. Online-rating models (Elo, `goals_poisson`, efficiency) already update every result
  and are not part of this comparison.
- **Cadences:** `season` (existing: first forecast of each season); `monthly` (also at the first forecast whose Eastern tipoff
  date is in a new calendar month); `weekly` (also at the first forecast in a new ISO week). Every refit trains on every result
  the walk-forward had delivered by that forecast's cutoff (backtest.run rule), current season included; nothing else changes
  (residual spreads and the draw weight are refit with the weights). Games sharing a cutoff share one fit.
- **Data:** NBA warm-up 2020–2021, evaluated 2022–2024; NHL warm-up 2016–2017, evaluated 2018–2021. These seasons were already
  used for selection, so results are **exploratory**. No test/validation-reserved season is read.
- **Metrics (per game, paired on games every cadence forecast):** winner Brier (all members); NBA score members also the
  Gaussian log score of the observed margin and of the observed total; NHL score members also regulation 3-way log loss.
- **Comparison:** monthly − season and weekly − season, mean per-game difference pooled over the evaluated seasons, with the
  ISO-week block bootstrap (seed 20260930, 20,000 resamples): 95% interval and a Bonferroni interval over the 18
  member × cadence comparisons of each member's primary metric (winner Brier for winner members; margin log score for NBA score
  members; regulation 3-way log loss for NHL score members). Per-season point estimates reported.
- **Reading rule (fixed now):** a cadence "supports a prospective trial" for a member only if the Bonferroni upper bound of its
  primary-metric difference is below 0 **and** the per-season difference is ≤ 0 in at least 2 of 3 NBA / 3 of 4 NHL seasons.
  Otherwise "no evidence of improvement". Either way nothing is promoted or changed live; a supported cadence would only be
  proposed as a separately recorded shadow member, judged on future games.
- **Checks:** a wrapped member's training set at every refit contains only results available by that cutoff (test); `season`
  cadence reproduces the unwrapped member's forecasts exactly (test and real run).

## 10. CatBoost challenger (recorded 2026-10-02, before any run)
Bounded experiment; exploratory (development seasons already used). Code: `app/catboost_challenger.py`. Dependency: CatBoost
1.2.10 (Apache-2.0; arm64/cp312 wheel; works with the pinned numpy 2.5.3 / scikit-learn 1.9.1), installed only in the research
worktree environment as the optional extra `research`; not imported by any live path unless a candidate qualifies.
- **Targets and features (unchanged inputs):** NBA winner on `gbt_v1` features (Logloss); NBA margin and total on
  `ridge_points`/`gbt_points` features (RMSEWithUncertainty → per-game mean and variance); NHL winner on `hockey_gbt` features.
  Pregame feature vectors are the ones those members record at forecast time in the walk-forward (identical inputs).
- **Training:** season-start fits on all earlier seasons only, as the members it challenges. No random splits.
- **Fixed search (8 configurations, no other tuning):** depth {4, 6} × learning_rate {0.03, 0.08} × l2_leaf_reg {3, 10};
  500 iterations, no early stopping, random_seed 20260930, thread_count 4.
- **Chronological folds:** configuration chosen by mean primary loss on tuning seasons (NBA 2022–2023; NHL 2018–2019), each
  fitted on earlier seasons only; then frozen and scored once on held-out seasons (NBA 2024; NHL 2020–2021). No test season.
- **Primary metrics:** winner log loss (Brier also reported); NBA margin Gaussian log score (total also reported).
- **Comparators on identical held-out games:** the boosted-tree member it replaces (`gbt_v1`, `gbt_points`, `hockey_gbt`) and the
  strongest eligible existing member, chosen by the same primary loss on the tuning seasons (earlier data).
- **Qualifying rule:** held-out primary loss below both comparators **and** paired week-block 95% upper bound of
  (CatBoost − strongest) below 0. A qualifier would only be proposed as a prospective shadow member; otherwise it is rejected
  and its report kept.

## 11. Combination search (recorded 2026-10-02, before any run)
Goal: find agreement or combination rules that add predictive value beyond confidence alone. Exploratory (development seasons
already used); anything that survives is only a prospective research candidate. Code: `app/combination_search.py`.
- **Inputs:** the §5 study tables (identical outcome, market line and night-before information cutoff for every member; line
  markets at the primary's model-only line, no historical bookmaker lines). Members: NBA winner `elo, logistic, logistic_v2,
  efficiency_margin, gbt_v1`; NBA lines `points_normal, ridge_points, gbt_points, ridge_points@weekly, gbt_points@weekly`;
  NHL winner `hockey_elo, goals_poisson, hockey_logistic, hockey_gbt`; NHL lines/reg3 `goals_poisson, goals_draw, goals_glm`.
  Winner agreement is never used for another market.
- **Rule registry (fixed; every rule is reported):** for every member subset S of size 2, 3 and all members:
  `avg(S) ≥ t`; `unanimous(S)`; `unanimous(S) & avg(S) ≥ t`; `unanimous(S) & similar(S)` (chosen-side probabilities within
  0.05 of each other); `majority(all)`; plus `single(m) ≥ t` per member and `stack ≥ t` (§5 out-of-fold stacker).
  t ∈ selector thresholds (2-way 0.50–0.75; reg3 0.40–0.60).
- **Pregame conditions (fixed, 2 × 2 levels, each rule also evaluated within each level):** favourite strength from the
  primary winner probability (`close` < 0.60 ≤ `clear`); season phase (`early` = before 1 December of the season's start
  year, else `late`). No other subsets.
- **Selection then frozen assessment (rolling origin):** for each assessment season s (NBA 2023, 2024; NHL 2019, 2020, 2021)
  rules are scored only on earlier evaluated seasons. Score = lift = rule win rate − win rate of the strongest single member's
  most-confident games at the same count (strongest single = lowest log loss on the same earlier seasons). A rule qualifies on
  selection data with ≥ 100 decided picks, coverage ≥ 5% and a Bonferroni (all rules in that sport·market·fold) lower bound of
  lift > 0 (normal approximation). Qualifiers — and, for transparency, the single best rule by point lift — are frozen and
  scored once on season s against the same matched-coverage comparator, plus the primary's and the average's.
- **Probability quality:** log loss, Brier and reliability of `avg(S)` for every S and of the stacker, per season, against the
  strongest single; a weaker member counts as complementary if adding it to a subset lowers the subset average's log loss.
- **Deployment rule:** only a rule that qualifies on selection data **and** keeps a positive lift in every later assessment
  season is deployed as a separately versioned research candidate (member forecasts and selection reasons recorded); otherwise
  rejected, report retained.

## 12. NHL shot-quality candidate `hockey_logistic_xg` (recorded 2026-10-02, before any data was fetched)
Highest-value feasible feature addition from the 2026-10-02 probes (docs/DATA_SOURCES.md). Separately versioned candidate;
exploratory on development seasons; never replaces anything without the prospective promotion rule.
- **Source:** api-web.nhle.com `/v1/gamecenter/{id}/play-by-play` (free, no key; NHL.com terms, personal non-commercial),
  stored in the raw cache (source `nhl_api_web`) with every retrieval logged. Parsed unblocked shot attempts (shot-on-goal,
  missed-shot, goal) of regular-season and playoff games, shootouts excluded: side (home/away), period, seconds, x, y, zone,
  shot type, situation code, goal, empty net (no `goalieInNetId`).
- **Availability:** as the NBA box backfill. Retrievals more than 24 h after the start are `historical_backfill`, assumed
  available with the game's result (`result_available_at`); otherwise `observed` (retrieval time). A game's shots reach a model
  only once both its shots and its result are available at the cutoff (backtest.run box-event rule).
- **xG model:** L2 logistic regression (scikit-learn, C = 1) of goal on distance and angle to the attacked net (net at ±89 ft,
  side from the zone code), their product, shot-type indicators, rebound (unblocked attempt by the same team ≤ 3 s earlier,
  same period) and strength (even / own power play / short-handed). Empty-net attempts excluded. Fitted at each season start on
  all earlier seasons' attempts; a season's attempts are valued only by the fit made before it.
- **Team inputs (lagged, point-in-time):** for each team the last 20 games with shots (carried across seasons): even-strength
  (5v5, both goalies in) xG share = xGF / (xGF + xGA), and special-teams net xG per game = power-play xGF − short-handed xGA.
- **Candidate:** `hockey_logistic` (same features, l2, scaling and season-start refit) plus `ev_xg_share_diff` and
  `st_xg_net_diff` (home − away), both standardised. No feature or hyperparameter search.
- **Evaluation:** development seasons (warm-up 2016–2017; scored 2018–2021; 2016 has no earlier xG fit, so xG features start in
  2017) against `hockey_logistic` and the primary `hockey_elo`, identical games, winner Brier and log loss, paired week-block
  bootstrap. **Qualifies** for prospective shadow recording only if its Brier is below both and the paired 95% upper bound of
  Brier(candidate − `hockey_logistic`) is below 0. Otherwise rejected with its report kept; the shot backfill stays available.

## 13. Controlled ongoing learning — declared schedule (recorded 2026-10-02)
The learning *procedure* is fixed and versioned; model *parameters* learn from new games on this schedule. New outcomes never
change an issued forecast (append-only ledger and cohort tables) and never rewrite an earlier decision or fit record.
| Component | Learns from new finals | Schedule | Identity recorded |
|---|---|---|---|
| Primary `elo` / `hockey_elo`, `points_normal` / `goals_poisson` | ratings, online | every result, replayed each run | sealed config + `dataset_version`; near-tipoff rows `latest_result_available_at` |
| Research winner members (`logistic*`, `gbt_v1`, `hockey_*`, `efficiency_margin` σ) | weights | first forecast of each season, on all earlier seasons | `model_fits` row (params digest, results fingerprint, results available through, libraries, first used) |
| Research line members (`ridge_points`, `gbt_points`, `goals_draw`, `goals_glm`) | weights | season start | `model_fits` |
| Shadow line members `ridge_points@weekly`, `gbt_points@weekly` | weights | season start + first forecast of each ISO week (Eastern) | `model_fits` + per-forecast `params.fit` |
| Stackers | pooling weights | each run, on the last two completed seasons (identical within a season) | weights in each cohort's `stack` JSON |
| Selector | which rule to follow (no parameters) | Monday 14:00 UTC, settled live cohorts | `selector_decisions` (append-only) |
| Thresholds | none chosen automatically | — | — |
| Studies (cadence §9, CatBoost §10, combinations §11, xG §12) | — | re-run at most once per season, after the season ends, each design-first; no in-season search | reports under `data/reports/` |
- **Forecast timing (unchanged):** training/refits may run any time before the window; time-sensitive inputs are refreshed and
  forecasts issued only within two hours before the start and strictly before it (code checks + triggers, migrations 0015/0016).
- **Primary stability:** the primaries change only through the written prospective promotion rules (§6 "Promotion",
  EVALUATION_PLAN promotion rules), applied manually; research recommendations adapt only through the frozen, versioned selector.
- **Candidate lifecycle:** design recorded → exploratory study on spent seasons → if it meets its pre-set rule, prospective
  shadow recording under its own version → promotion only by the written rule. Rejected candidates keep their report and code
  path out of the live run (no runtime cost).
- **Known limit:** the research `definition` digest includes source files, so any code change starts a new research identity
  and prospective evidence restarts for that definition. Changes should therefore be batched and infrequent.

## 14. Expanded-combination experiment `expanded-v1` (recorded 2026-10-02 ~16:05 UTC, before any run)
Question: do the members with genuinely new information add value in combination? The core experiment (§4–§7, §11) is
unchanged; this is a separate, versioned experiment.
- **Inventory of the completed §11 search (from its reports):** NBA winner `elo, logistic, logistic_v2, efficiency_margin,
  gbt_v1`; NBA lines `points_normal, ridge_points, gbt_points, ridge_points@weekly, gbt_points@weekly` (the weekly members
  WERE included, in every subset family and the stacker); NHL winner `hockey_elo, goals_poisson, hockey_logistic, hockey_gbt`;
  NHL reg3/lines `goals_poisson, goals_draw, goals_glm`. **Not tested:** `hockey_logistic_xg` (built after the search) and
  `availability_two_hour` (no historical inputs; prospective only). The live core cohorts exclude all shadow members.
- **Registered rules (small and fixed; no condition levels; t ∈ {0.55, 0.60, 0.65}):**
  - *NHL winner* (members `hockey_elo, goals_poisson, hockey_logistic, hockey_gbt, hockey_logistic_xg`): subsets containing xG —
    the 4 pairs with each existing member, 3 triples (`xg+hockey_elo+hockey_logistic`: ratings + form; `xg+hockey_elo+goals_poisson`:
    ratings + scoring rates; `xg+hockey_logistic+hockey_gbt`: learned models), and all five. Per subset `unanimous(S)` and
    `avg(S) ≥ t`; plus `majority(all five)` and an out-of-fold stacker over all five `≥ t`. 36 rules.
  - *NBA line markets* (spread, total, team totals; members as §11): subsets containing a weekly member — `points_normal +
    ridge@weekly`, `points_normal + gbt@weekly`, `ridge@weekly + gbt@weekly`, the triple of those three, and all five. Per subset
    `unanimous(S)` and `avg(S) ≥ t`; plus `majority(all five)`, and stackers over the triple and over all five `≥ t`. 27 rules
    per market. **Caveat:** these NBA rules were already inside the 1,655-rule §11 family whose results I have seen, so the NBA
    part is not blind; the NHL xG part is (xG's individual result is known; no combination involving it was run).
- **Evaluation:** §5 tables (identical outcome, line, settlement and night-before cutoff; shots delivered only to the xG member,
  §12 rule). Rolling origin as §11 (NBA assess 2023, 2024; NHL 2019, 2020, 2021), selection on earlier evaluated seasons only.
  Per rule: picks, coverage, W/L/P, accuracy with Wilson interval; matched-coverage comparison against the strongest single
  (chosen on selection seasons) AND each constituent. Probability quality per subset average and stacker: log loss, Brier,
  reliability, per season, with paired week-block bootstrap against each constituent.
- **Qualifying rule (not weakened):** same as §11 — ≥ 100 decided picks, coverage ≥ 5%, Bonferroni (over this family's rules
  in that sport·market·fold, α = 0.05) lower bound of lift vs the strongest single > 0 — plus a positive point lift against
  every constituent. Deployable as a recommendation candidate only if it qualifies and stays positive in every later
  assessment; even then it only enters prospective evaluation (no promotion).
- **Prospective recording (regardless of the retrospective result):** each two-hour cycle also writes an `expanded-v1` cohort
  (definition `<core definition>:expanded-v1`) for NHL winner and NBA line markets: all members incl. shadows with their stored
  forecast references, the all-member average, the expanded stacker (out-of-fold, last two completed seasons) and all-member
  agreement. Recording only: no selector decision, selection or recommendation uses it; the core record, its definition and
  its selector are unchanged. Reported in the app as its own group, labelled as recording-only research.
- **Goalies (assessment only, §15 of this note in docs/CHALLENGER_RESULTS.md):** prospective probable/confirmed goalie
  snapshots (migration 0014) carry our retrieval time and are usable going forward; assess coverage, lead time and status.

## 15. Market-specific selective-combination study `selective-v1` (recorded 2026-10-02 ~17:00 UTC, before any run)
Purpose: where, if anywhere, does model agreement add value per sport and market — including selective, high-accuracy subsets —
measured honestly against confidence alone. The user reported an earlier ~83% system; no artifact of it exists in this project
(the only "83%" reference is a published paper with random splits, HANDOFF 2026-10-01), so it is not reproduced here.
- **Audit (what was already done; reused, not rerun as new):** §5 study (members, average, stacker, majority/unanimous,
  pair-with-primary agreement); §11 search (1,655/925/405/345 rules incl. pairs/triples, avg ≥ t, unanimous+avg, unanimous within
  0.05, conditions close/clear/early/late); §14 expanded (xG and weekly subsets: unanimous, avg ≥ t, stackers). See
  docs/CHALLENGER_RESULTS.md for the matrix.
- **Genuinely untested before this study:** (1) agreement with a *minimum member confidence* (every member ≥ t on the agreed
  side); (2) a *model-disagreement* condition; (3) thresholds up to 0.80 for winners (the high-accuracy region); (4) xG inside
  the full family with conditions; (5) the injury-aware member at its own cutoff in combinations; (6) the chronological
  "highest-accuracy" procedure and an explicit accuracy–coverage frontier.
- **Point-in-time tables (saved for reuse):** `data/research/tables/{nba,nba_evening,nhl}.json.gz` with dataset fingerprints.
  `nba`: night-before cutoff, winner members `elo, logistic, logistic_v2, efficiency_margin, gbt_v1`, line members
  `points_normal, ridge_points, gbt_points, ridge_points@weekly, gbt_points@weekly`. `nba_evening`: 17:45 ET cutoff (the
  injury report's), winner members as `nba` plus `avail_v1`, same rows/results/boxes for all (historical report times are
  *assumed* from the published file time; content history exists for 2019-20 … 2023-24). `nhl`: winner `hockey_elo,
  goals_poisson, hockey_logistic, hockey_gbt, hockey_logistic_xg`; reg3/puck line/total/team totals `goals_poisson,
  goals_draw, goals_glm` (xG gives winner probabilities only). Every member is compared at the same game, line (the primary's
  model line for line markets), settlement and cutoff.
- **Registered family per market** (thresholds: winner 0.60/0.65/0.70/0.75/0.80; reg3 0.45/0.50/0.55/0.60; lines 0.55/0.60/0.65):
  `single(m) ≥ t`; every pair: `unanimous_min(pair) ≥ t`, `avg(pair) ≥ t`; registered triples: `unanimous_min ≥ t`,
  `avg ≥ t`, `unanimous & max disagreement ≤ d` (d 0.03, 0.06); all members (n ≥ 3): `majority`, `unanimous_min ≥ t`,
  `avg ≥ t`, `unanimous & max disagreement ≤ d`; `stack(all) ≥ t` (out-of-fold). Triples: NBA winner `elo+logistic_v2+gbt_v1`,
  `elo+efficiency_margin+logistic`, `logistic_v2+efficiency_margin+gbt_v1`; NBA evening `elo+logistic_v2+avail_v1`,
  `avail_v1+efficiency_margin+gbt_v1`, `elo+logistic+avail_v1`; NBA lines `points_normal+ridge_points+gbt_points`,
  `points_normal+ridge@weekly+gbt@weekly`, `points_normal+ridge@weekly+gbt_points`; NHL winner `xg+hockey_elo+hockey_logistic`,
  `xg+hockey_elo+goals_poisson`, `xg+hockey_logistic+hockey_gbt`; NHL 3-member markets: the full set only.
- **Conditions (2, fixed):** favourite strength from the primary winner probability (`close` < 0.60 ≤ `clear`); model
  disagreement = max − min of all members' probabilities for the primary's picked side (`low` ≤ 0.05 < `high`). Every rule is
  scored under all / close / clear / low / high. Every rule and condition is logged.
- **Chronological procedure (rolling origin as §11):** select on earlier evaluated seasons only, freeze, assess once on the next.
  Frozen per fold: (a) qualifiers (unchanged §14 bar: ≥ 100 decided picks, ≥ 5% coverage, Bonferroni lower bound of lift vs
  the strongest single's matched-coverage confidence > 0, positive vs each constituent); (b) the highest-accuracy rule with
  ≥ 100 picks and ≥ 5% coverage (answers the 80–85% question by procedure, not hindsight); (c) the top point-lift rule.
- **Reported:** per market an accuracy–coverage frontier (exploratory, all evaluated seasons; with the strongest single and each
  constituent at matched coverage), per-fold assessments, the 80–85% answer, and a failure diagnosis (too few picks / no gain
  over confidence / unstable / calibration / missing information / implementation).
- **Prospective tracking:** the registered family is frozen in `backend/app/models/combination_rules.json` (sha256, effective
  from its merge) and evaluated on the stored two-hour cohorts (expanded cohort where one exists, else core), one canonical
  decision per game/market/rule (latest cohort before the start). Rules frozen by the last fold are flagged `selected`
  (`qualified` or `unqualified research`). Live NBA winner uses `availability_two_hour` (a different cutoff) in place of
  `avail_v1`; such rules are prospective-only. Nothing is promoted; the core experiment and selector are unchanged.

## 16. Gap-filling combination study `gapfill-v1` (recorded 2026-10-02 ~22:20 UTC, before any run)
Purpose: test the compatible subsets the audit (docs/EVIDENCE_AUDIT.md) found never evaluated, in particular candidates combined
with members other than the strongest and with each other, the NBA winner triples with `avail_v1`, and the NHL winner xG
triples. Exploratory: every season used was already spent. Nothing here grants eligibility by itself.
- **Data (saved, chronological out-of-sample; no refit, 0 of the 24 retraining configurations used):**
  `data/research/candidates/oof-{nhl,nba}-assess.json.gz` (NHL 2022–26, NBA 2025–26; every member and candidate fitted on
  earlier seasons only) and `data/research/tables/nba_evening.json.gz` (2022–24, 17:45 ET cutoff with `avail_v1`). Fingerprints
  recorded in the report. Lines are the primary's model line (no historical bookmaker lines): line-market lifts are
  measured there only.
- **Members per market:** existing members + the stage-1 shortlist (`data/reports/pool-*/shortlist.json`, frozen from the
  earlier screen seasons). NHL total and home team total have no shortlisted candidate and every subset of their 3 members was
  already covered (§11, §15): 0 new configurations. NBA evening winner: the 6 members of `nba_evening`.
- **Family per market (deterministic budget ≤ 500 configurations; condition level `all`):** thresholds T: winner 0.60/0.70/0.80,
  reg3 0.45/0.50/0.55, lines 0.55/0.60/0.65. In this order until the budget is reached:
  1. full member set: `avg≥t` (3), `unanimous_min≥t` (3), `majority` (1; exact ties and no strict majority abstain),
     `unanimous_maxdis≤d` d 0.03/0.06 (2), regularised stacker `stack≥t` (3; out-of-fold, refitted at each season start on
     earlier seasons only) = 12;
  2. every pair: `avg≥t`, `unanimous_min≥t` = 6 each;
  3. triples, those mixing ≥ 1 candidate and ≥ 1 existing member first, each group in a seeded order (seed 20261002):
     `avg≥t`, `unanimous_min≥t`, `majority`, `unanimous_maxdis≤d` = 9 each;
  4. 4+-member subsets by size, seeded order: `avg≥t`, `majority` = 4 each.
  Every evaluated and omitted subset is listed in the report; a truncated family is never called exhaustive.
- **Procedure (as §15):** rolling origin; select on all earlier seasons of the table, assess the next season once (NHL 4
  folds, NBA 1 fold, NBA evening 2 folds). The best single member is chosen by log loss on the selection seasons only.
- **Primary metric (pick rules):** accuracy lift over the best single's own most-confident picks at matched coverage.
  Qualifies: ≥ 100 picks, ≥ 5% coverage, Bonferroni lower bound > 0 with α = 0.05 / (this family + every rule previously
  evaluated for that market in §11/§14/§15/pool stage 2), and better than every constituent at matched coverage.
  Frozen per fold: qualified + highest-accuracy + top-lift rules; assessed once on the next season against the best prior
  single, the primary, the strongest constituent, the all-member average and confidence-only filtering, all at matched coverage.
- **Probability level (secondary):** for every evaluated subset's equal average, log loss minus the best prior single on the
  assess seasons, 95% week-block bootstrap (1,000, seed 20261002). **Route B (combination-only value):** for every candidate
  c and evaluated subset S ∋ c with |S| ≥ 2, log loss of avg(S) − avg(S \ {c}) pooled over assess seasons, with the same
  bootstrap; reported per season too. A combination supported here may be proposed for a new sealed pool registry
  (prospective evidence only); it never makes c eligible alone or in other combinations.
- **Compute:** one process on scratch copies, ≤ 2 h wall-clock; stop and report if exceeded.

## 17. Daily-refit comparison `daily-v1` (recorded 2026-10-02 ~22:45 UTC, before any run)
- **Question:** does refitting the trainable members daily beat the existing season-start and weekly cadences? Not assumed.
- **Members (9 retraining configurations: NBA 5, NHL 4 of the 24 allowed per sport):** the §9 members, unchanged
  configurations: NBA `logistic`, `logistic_v2`, `gbt_v1`, `ridge_points`, `gbt_points`; NHL `hockey_logistic`, `hockey_gbt`,
  `goals_glm`, `goals_draw`. Online models (Elo, `points_normal`, `goals_poisson`) already update after every result.
- **Daily cadence:** refit at the first forecast of each Eastern calendar date, only if newly settled results were delivered
  since the previous fit (otherwise skipped and counted); never in a walk's first season (as §9). Code
  `backend/app/cadence_daily.py` (separate from the hashed `retrain_cadence.py`), CLI `--sport X daily-cadence`.
- **Data, metrics, statistics:** exactly §9 (NBA validation seasons 2022–24 with warm-up; NHL development 2018–21; primary
  metric per member as §9; paired on games every cadence forecast; ISO-week block bootstrap 20,000, Bonferroni over 18 =
  9 members × {daily − season, daily − weekly}).
- **Reading rule (as §9):** "supports a trial" only if the Bonferroni interval is below 0 and the member is not worse in at
  least 2 of the 3 (NBA) / 3 of the 4 (NHL) seasons. Even then the only permitted follow-up is a separately versioned
  prospective shadow; sealed members and live schedules are not changed by this study.
- Runs on scratch store copies only.

## 18. Persistent agreement tracking `tracking-v2` (recorded 2026-10-03 ~00:10 UTC, before any result was computed)
- **Purpose:** keep a permanent, versioned record of how every registered model subset and agreement rule classifies each
  game, by sport × market × exact line × subset/member versions × rule × confidence threshold/band, including research-only
  combinations that can never be recommended. Tracking only: it selects, recommends and promotes nothing. The weekly
  selectors (core v1, pool) remain the only source of active recommendations.
- **Groups (sealed `backend/app/models/tracking_groups.json`, built by `agreement_tracking.build_registry`):** per market, in
  priority order until the allocation cap: every single member and candidate; the core set (average, unanimous, majority,
  cohort stacker; NBA winner also the six registered voters); the expanded set incl. shadows (average, unanimous, majority,
  expanded stacker); a pool stacker over every member and candidate present in the saved out-of-sample tables (weights
  fitted once on those tables, all seasons earlier than any live game, L2 = 1, frozen in the registry); every pair of
  cohort members (unanimous, average); triples (least-evaluated first: those containing a shadow or a member without
  historical inputs); candidate + the market's anchor (the sealed comparator of `combination_rules.json`); candidate + other
  members; candidate pairs (average); triple majorities; 4-member subsets. NGBoost is excluded while it is not installed
  (it is never recorded live).
- **Allocation (registered now):** ≤ 500 rule configurations per sport/market, configuration = group × cumulative threshold.
  Thresholds: winner 0.55–0.80 (6), NHL regulation 3-way 0.40–0.60 (5), lines 0.55–0.70 (4). Bands are the non-overlapping
  intervals between them (lowest band from the floor: 0.50, or 1/3 for 3-way), derived from the same statistic, not new
  configurations. Groups beyond the cap are listed in the registry as skipped (allocation cap). No new model/training
  configuration is used (0 of 12 per sport).
- **Confidence semantics:** single = member probability of its pick; unanimous = minimum member probability for the agreed
  outcome; average = average probability of its most probable outcome; stack/pool stack = stacker probability; majority =
  strict majority of all members (an exact member tie is an abstention, equal leading counts a tie), confidence = mean
  probability of the majority outcome over all members, vote counts recorded.
- **Recording:** migration 0021 `agreement_observations`, append-only, one row per group and research cohort, written in the
  two-hour cycle after the candidate pool, before the start (trigger); candidates only from forecasts made against that
  cohort's exact line; a missing member makes the observation `incomplete` (never computed on a subset). Thresholds are
  never stored per row. Canonical evidence = the latest pregame cohort per game.
- **Reporting:** per group and threshold/band: eligible, selected, pending, settled, coverage, wins/losses/pushes/voids, win
  rate with Wilson 95% interval, Brier/log loss for rules that output probabilities, mean confidence (calibration by band),
  matched-coverage comparison with the anchor and each constituent; "insufficient evidence" below 100 decided picks (shown,
  never hidden). Historical (exploratory, night-before out-of-sample tables of the candidate pool, model-only lines,
  goalie candidates with the actual starter) and prospective records are always separate.
- **Exploratory leaderboard:** configurations with ≥ 100 decided picks and ≥ 5% coverage ranked by the Bonferroni lower bound
  (over the market's configurations) of the win-rate difference against the anchor at matched coverage. Historical check:
  rank on earlier seasons only, then report the first-ranked configuration on the next season.

## 19. Feature tranche `features-v1` (recorded 2026-10-03 ~00:45 UTC, before any code or result)
Allocation for this tranche (brief §8): at most 12 new model/training configurations per sport; used here: **NBA 2, NHL 2**.
Every run is logged (data/research/features-v1/experiments.jsonl: success, failure, skip). Reused seasons ⇒ exploratory.
- **NBA `avail_q` — player-quality-weighted absences.** *Hypothesis:* `avail_v1` weights an absent player by expected minutes
  only; weighting by the player's recent production per minute (Hollinger game score per minute, last 20 played games before
  the report date, centred on the previous season's league mean per minute) carries more of the strength change and so
  diversifies errors from the team-rating members. *Configurations:* (1) `avail_q_v1` = `avail_v1` features +
  quality-weighted new/all absence differences; (2) `avail_q_only` = `logistic_v2` features + quality-weighted differences
  + `report_missing` (no minutes-only terms). *Data:* the same 2022–24 scored games, 17:45 ET cutoff and official reports as
  `avail_v1` (publication times assumed from the PDF header/Last-Modified — the same limitation); player box scores strictly
  before the report date. *Metrics:* paired log loss and Brier vs `avail_v1` and `logistic_v2` (week-block bootstrap, 20,000;
  Bonferroni over 4 comparisons); incremental value: average(elo, X) vs average(elo, avail_v1) and accuracy of the top 25% by
  confidence at matched coverage. *Reading rule:* "adds information" only if the Bonferroni interval of log loss vs
  `avail_v1` is below 0; then the only permitted follow-up is prospective recording (live inputs exist: official report,
  ESPN summary box scores); nothing live changes from this study.
- **NHL `nhl_special` — shot volume and special-teams finishing.** *Hypothesis:* the shot-quality inputs (xG share at even
  strength, special-teams xG net) miss shot volume (all-situation shot-attempt share) and finishing on the power play / penalty
  kill (power-play goals for and short-handed goals against per game), which may diversify errors. Computed from our own
  play-by-play shots of games delivered before the cutoff (20-game window, as xG). *Configurations:* (3)
  `logit_l2:nhl_special`, (4) `poisson_draw:nhl_special` (the candidate learners with `nhl_base` + the three inputs).
  *Data/metrics:* the candidate-pool walk (screen 2018–21, assess 2022–26: reused seasons), paired log loss vs the same learner
  on `nhl_base` (winner and reg3 / puck line, Bonferroni over the markets tested), and with/without in the tracking groups'
  average with the anchor. *Reading rule:* "adds information" only if the Bonferroni interval vs `nhl_base` is below 0 in
  the assess seasons; then prospective recording as a candidate; otherwise recorded as a negative result.
- **Not done in this tranche (inputs not established before the cutoff):** goalie confirmation *time* as a feature (ESPN
  publishes no announcement time; our snapshots give a status at retrieval only, collected prospectively since 2026-10-01 —
  shown as an input with its retrieval time, not backfilled); NBA expected minutes beyond last-10 means (no free projected
  minutes source); PP/PK opportunity counts (penalties are not in the stored shot data; NHL REST `team/powerplay` is a
  post-game per team-game source, assessed but not integrated).

## 20. Historical training and combination discovery `discovery-v1` (recorded 2026-10-03 01:29 UTC in commit 2e55096, before any run; registered as "§18" on branch pool-selector, renumbered at reconciliation)
Pipeline: historical data → models trained at each season start on earlier seasons only → chronological out-of-sample
predictions → search of subsets × agreement rules × thresholds per market → rules chosen on earlier seasons, evaluated once
on the next season, repeated → results kept for tracking (historical, exploratory) and the frozen rules recorded for
prospective evaluation. Every season used was spent before: **exploratory, not an independent test.**
- **Reused trained models (no refit):** the chained pool-v1 out-of-sample tables `oof-{sport}-screen` + `oof-{sport}-assess`
  (identical members, no overlapping games, same night-before cutoffs; every member refitted at each season start on
  earlier seasons): **NHL 2018–2026 (9 seasons, 11,870 games), NBA 2022–2026 (5 seasons, 6,605 games).** Inventory of
  models, features and training windows: docs/TRAINING_INVENTORY.md.
- **Members per market:** existing members + candidates, excluding the two pool *reference* re-expressions (`logit_l2:nba_base`,
  `ridge:nba_base`: duplicates of existing members) and every `*_goalie` candidate (historical inputs used the actual starter,
  an optimistic input not available pregame). NHL winner 10, reg3 5, NHL lines 4; NBA winner 13, NBA lines 10.
- **Newly trained challengers (22 configurations: NHL 12, NBA 10, within 24 per sport):** two learned combiners per market,
  refitted at every season start on the earlier seasons' out-of-sample member predictions only: `stack_l2` (L2 logistic /
  multinomial on clipped log-probabilities of all members, C = 0.1) and `stack_l1` (L1, C = 0.05, sparse: selects members).
  No fit in a table's first season. Code `backend/app/discovery_study.py`.
- **Lines:** historical lines are the primary model's own pregame line stored with each game (points model / goals model),
  labelled **"pregame research line"**; never derived from final scores; not bookmaker lines; the primary is ~50/50 there by
  construction. Prospective tracking uses the exact bookmaker or model line recorded with each cohort. No profit claim.
- **Confidence definitions (never mixed):** `single` = the member's probability of its pick; `avg` = equal-weight average
  probability of its pick; `unanimous_min` = every member picks the same outcome, confidence = the minimum member probability
  of that outcome; `majority` = more than half the members pick the same outcome (ties and no strict majority abstain),
  confidence = mean probability of the supporting members, votes k/n recorded; `stack` = stacker probability of its pick.
  A pick is the most probable allowed outcome (draw allowed only in reg3).
- **Thresholds:** cumulative 50–90% in 5-point steps (two-way markets, 9 levels); reg3 35–70% (8 levels). **Bands**
  (non-overlapping, descriptive, never used for selection): two-way [50,55) … [85,90), [90,100]; reg3 [33.3,35), [35,40) …
  [65,70), [70,100].
- **Search family (cumulative thresholds only), order and budget:** singles (members + stackers); every pair (`avg`,
  `unanimous_min`); triples (`avg`, `unanimous_min`, `majority`; mixed candidate + existing first, seeded 20261003); larger
  subsets (`avg`, `majority`; by size, seeded) until **6,000 configurations per market** (raised from 500 because the 50–90%
  grid multiplies thresholds by 9; recorded here before the run). Omitted subsets are listed.
- **Chronological procedure:** for each assess season after the first, select on all earlier seasons, assess once on that
  season (NHL 8 folds, 2019–26; NBA 4 folds, 2023–26). Best single = lowest log loss on the selection seasons. Frozen per
  fold: qualified rules; top point lift; highest accuracy (≥ 100 picks, ≥ 5% coverage); and for each cumulative threshold the
  rule with the highest lift at that threshold (≥ 100 picks). Assessed against the best prior single, the primary, the
  strongest constituent (prior), the all-member average and confidence-only filtering, at matched coverage.
- **Qualification:** ≥ 100 picks, ≥ 5% coverage, Bonferroni lower bound of lift > 0 over this family plus every rule
  previously evaluated for the market, better than every constituent at matched coverage.
- **Reporting per group** (every evaluated subset × method × threshold or band, per season and pooled): eligible games,
  selected games, coverage, wins / losses / pushes / voids / pending, win rate with Wilson 95% interval, mean confidence
  (calibration gap), Brier and log loss of the group's probability on its picks where it has one, and the best prior single's
  win rate on the same number of its most confident picks. Groups with < 50 decided picks are labelled "insufficient
  evidence". Saved compactly for the Performance tracking view; underlying predictions stay in the saved tables (no
  per-threshold rows).
- **Compute:** ≤ 2 h wall-clock, scratch copies only, one process per market in parallel.

## 21. Tooling audit and bounded tuning `tuning-v1` (recorded 2026-10-03 01:52 UTC in commit 942676c, before any install or run; registered as "§19" on branch pool-selector, renumbered at reconciliation)
**Tooling audit** (from docs/REPO_REVIEW.md, CHALLENGER_RESULTS §10, `candidates/registry.FAMILY_AUDIT` and this repository's
environments only; no other repository accessed):
| Tool | Status before this step | Evidence | Decision |
|---|---|---|---|
| scikit-learn 1.9.1 HistGradientBoosting | installed, **used** with fixed settings (`gbt_v1`, `gbt_points`, `hockey_gbt`) | TRAINING_AUDIT | reuse; tune (below) as a pool learner |
| scikit-learn ExtraTrees / RandomForest | installed, **used** (pool-v1 candidates, fixed settings) | CANDIDATE_POOL | reuse; tune ExtraTrees (below) |
| NGBoost 0.5.11 | installed in research venvs, **used** (pool-v1, NBA lines) | CANDIDATE_POOL_RESULTS | keep; no change |
| CatBoost 1.2.10 | research extra, **tested and rejected** (§10: worse or equal in every task) | CHALLENGER_RESULTS §10 | not retested (no new hypothesis) |
| Optuna 5.0.0 | **missing** (resolved in the review venv; never used: every setting was declared) | REPO_REVIEW #3 | **adopt** for bounded chronological tuning |
| LightGBM 4.7.0 / XGBoost 3.4.1 | **missing** in the project venvs (REPO_REVIEW's throwaway venv resolved them; its note says a libomp is needed) | REPO_REVIEW "+" row | **deferred/untested**: related to but distinct from sklearn HistGradientBoosting (different split finding, regularisation, categorical handling and defaults); the sklearn result below does not rule them out; adoption needs a new system library |
| statsmodels (negative binomial) | missing | REPO_REVIEW; dispersion 1.02/0.96 | rejected (no overdispersion) |
The concrete gap addressed here is that **no learner was ever tuned**: every setting was declared. This experiment tunes
sklearn HistGradientBoosting (already a dependency) and ExtraTrees; it says nothing about LightGBM or XGBoost, which stay
untested (correction recorded at reconciliation, 2026-10-03; the original text called LightGBM's algorithm available through
sklearn, which overstated the equivalence).

**tuning-v1 design**
- **Dependency:** `optuna==5.0.0` in the optional `research` extra, installed in the worktree venv only; the live venv and every
  live candidate identity are unchanged (new code in `backend/app/tuning_study.py`; the hashed pool files are not edited).
- **Configurations (8 retraining configurations, ≤ 24 per sport):** NBA `hgb_tuned` binary (winner) and normal (margin/total
  → spread, total, team totals); `et_tuned` binary and normal. NHL `hgb_tuned` binary, reg3 (multiclass) and goals (Poisson
  loss, home/away, fitted draw inflation → every NHL market); `et_tuned` binary. Features: `nba_base` / `nhl_base` (no goalie
  inputs). Same point-in-time feature walk as pool-v1.
- **Chronological tuning:** for each season s of the evaluated tables (NHL 2018–2026, NBA 2022–2026): Optuna TPE
  (seed 20261003, 25 trials, fixed search space below) trains on seasons < s−1 and scores season s−1 (binary/reg3: log loss;
  normal: margin + total MSE scaled by their variances; goals: Poisson deviance); the best setting is refitted on every season
  < s and predicts season s only. Season s outcomes are never seen by its own tuning. All trials are saved.
  Search spaces: HGB learning_rate 0.02–0.2 (log), max_iter 100–600, max_leaf_nodes 8–64, min_samples_leaf 20–300,
  l2_regularization 0.001–10 (log); ExtraTrees (300 trees) min_samples_leaf 10–300, max_features 0.2–1.0, max_depth 4–30.
- **Predictions saved:** per-game vectors at each table row's line, `data/research/tuning/oof-{sport}.json.gz`, with the
  per-season best settings and fit records.
- **Evaluation:** (a) **alone:** per market, log loss minus the strongest existing member and minus the untuned pool
  counterpart (`extra_trees:*`), per season and pooled with a 95% week-block bootstrap; (b) **inside combinations:** the
  discovery-v1 procedure (§20, unchanged code and budget) rerun with the tuned members added, rules chosen on earlier seasons,
  assessed on the next, reported by market and threshold, plus with/without-the-tuned-member comparisons.
- Exploratory (spent seasons). Compute ≤ 2 h on scratch copies. Nothing changes live; a tuned learner could only enter live
  recording later as a separately versioned candidate.

## 22. Deep-learning study `deep-v1` (registered 2026-10-03 ~22:00 UTC, before any deep model was fitted on project data)
**Why.** The user asked for neural networks built with PyTorch and TensorFlow/Keras. The pool already has one small
scikit-learn network (`mlp`); this study measures whether deeper networks trained with those libraries add anything, under
the same point-in-time rules as every other study. Exploratory: it selects nothing for live use.

**Inputs.** The pool-v1 point-in-time feature walk (`candidates/features.py`, base feature sets only; no goalie inputs, which
are optimistic historically). Stores are read from copies. No new data source.

**Models (fixed before fitting; at most 4 per sport).** Standardised inputs; every network is an average of 5 seeds.
- `torch_mlp:<sport>_base` — PyTorch, binary winner: 64-32 hidden units, ReLU, dropout 0.2, AdamW (lr 1e-3, weight decay 1e-3),
  batch 128.
- `keras_mlp:<sport>_base` — TensorFlow/Keras, binary winner: the same architecture and optimiser settings.
- NBA `torch_mlp:nba_base:normal` — PyTorch regression of margin and total (two networks, MSE).
- NHL `torch_goals:nhl_base` — PyTorch Poisson regression of home and away regulation goals (log link, Poisson loss), with the
  pool's draw weight and overtime share.

**Training rule (no tuning on the evaluated season).** For season s: training rows are those whose results were available
before the season's first cutoff (`engine.training_rows`). The latest training season is held out to choose the number of
epochs (early stopping, patience 15, at most 200 epochs); the network is then refitted on all training rows for that many
epochs and predicts season s only. A season without an earlier validation season is skipped. Fixed seeds; single-threaded.

**Evaluation.** Per market, on games where every compared forecast exists: log loss, multi-outcome Brier and pick accuracy,
per season and pooled; difference in log loss against (a) the best existing member chosen on earlier seasons only and (b) the
scikit-learn counterpart (`mlp:<sport>_base`, `gbt_points` / `poisson_draw:nhl_base` for the score heads), with a
week-block bootstrap 95% interval. Seasons 2025 and 2026 are the reserved test seasons, already reused by research: their rows
are reported and flagged, and nothing here is a second final test.

**Decision rule.** A deep model is proposed for prospective recording as a research candidate only if its pooled log-loss
difference against the best prior existing member has an interval entirely below 0. Otherwise it stays a study result. No
model, selector, registry or live forecast changes as part of this study.

## Amendments
- **2026-10-02, before the study run:** `ridge_points` / `gbt_points` spreads have a floor of 1.0 point (`MIN_SD`), found with
  synthetic test data where every total was identical. Real residual spreads are about 12 (margin) and 18 (total) points, so the
  floor never binds on real data. No other setting changed.
- **2026-10-02, after the study (no effect on it):** NBA winner agreement keeps the registered expected set of six models
  (`availability_two_hour` included), so an NBA winner cohort is "complete"/"unanimous" only when the injury-aware model ran; the
  equal average and the stacker use the five core models. Exact 50% member forecasts are ties: they abstain and are counted as such.
- **2026-10-02, operational detail:** stackers for line markets are trained at the primary's model-only line and applied at
  whatever line the live cohort uses (often a bookmaker line). That shift is a known limitation; the prospective record measures
  it rather than assuming it away.
- **2026-10-02, after the self-review (code review by a separate agent in this session; not independent):** (1) research member
  forecasts' identity now includes the research run's own input digest (results, box observations, training seasons, definition),
  so a refetched box score can no longer collide with an earlier member under one key; (2) the fitted line-member walk-forward
  state is cached on disk per input digest (it cost about 216 s per NBA cycle); (3) the previous weekly decision stays in force
  until the new one is written (at most one week past its interval), as §6 intended; (4) a selection "context" now includes the
  decisions, and the game page reads the game's latest context, so one game never shows two recommendations; (5) a research
  failure is reported but no longer marks the primary forecast run as failed; (6) reports count unscorable games separately from
  void and honour `as_of`; (7) the retrospective simulation follows the live incumbent and tie-break rules (study run 2).
  Known limitations kept: `game_decisions` has no availability time of its own (the decision arrives with the final result);
  the winner cohort's bookmaker quote is looked up at the research step's start, which can be a few minutes after the primary's.
- **2026-10-02, gap closing (user-directed; no new candidate, rule, threshold or experiment):** (1) the strongest single member
  used in comparisons is chosen on earlier data only: per evaluated season from earlier out-of-sample seasons in the study; for the
  live report, from the retrospective study (all earlier than any live game); (2) reliability tables only, no fitted calibration;
  (3) a winner cohort's bookmaker record now names its quote table and id; every decision records the settlement-rules version.
  Rows written before this change keep these as unknown (not backfilled); (4) the slate API and Today show each selected market's
  research status with its exact line, missing-input reasons and timing; (5) abstention when evidence is insufficient is kept as
  designed (the previous rule is not carried forward).
- **2026-10-02, §9 before the cadence run:** no within-season refits in the walk's first season (NBA 2020, NHL 2016): no
  earlier-season data exists there and the logistic fit on a few games is singular (found with synthetic tests). That season is
  warm-up, never scored; every cadence is identical through it.
- **2026-10-02, after the §9 run (correction only):** the §9 heading first said "~13:10 UTC"; the design commit 602aeab is
  12:57 UTC and the real run started 13:02 UTC. Results: docs/RETRAIN_CADENCE_RESULTS.md.
- **2026-10-02, §12 result and integration (after the run):** the candidate met its rule (docs/CHALLENGER_RESULTS.md) and is
  recorded prospectively as a shadow winner member (`agreement.SHADOW`). Shadow members (NBA weekly line members, NHL xG) never
  vote, never enter averages or stackers, and a shadow skip or error is reported (`partial`) without failing the run. Fix found
  while integrating: line-market agreement had counted the weekly shadow members' votes since 9fac164 (never deployed with
  cohorts: no NBA cohort exists before 2026-10-20); agreement now uses the registered members only (test added).
- **2026-10-02 ~22:00 UTC, start-change voiding unified (new core definition):** `markets.settle` now voids a forecast on any
  change of the start (was: more than 12 hours), so scoring, CLI, API, tracking and research cohorts apply one rule.
  `markets.py` is part of the research identity, so the core research definition changes (NHL `5040a9e7ad2ab51c` →
  `7f9821facdf5b8f9` on a store copy; NBA likewise). Earlier cohorts, selections and decisions are kept under their own
  definition; the selector's prospective evidence restarts under the new one (it was 0 settled cohorts in every market, so
  no evidence is lost). No stored live forecast had a moved start (NBA 0 / NHL 0 of 510 market forecasts), so no recorded
  settlement changes.
- **2026-10-03 ~01:40 UTC, discovery-v1 amendment (after run 1):** added the final selection step the §20 pipeline names
  ("record them prospectively"): the same selection procedure on every historical season, no assessment, saved as
  `prospective_registry` per market. It changes no evaluated figure (same seed, same folds); run 2 reproduces run 1's folds.
- **2026-10-03 ~02:40 UTC, timing fix (reconciliation; user's "finish the timing bug"):** a game accepted at the start of a
  two-hour cycle that starts, is rescheduled or leaves the schedule before its cohort is written is now skipped with a
  reason (and a line member without state is recorded as missing) instead of raising (live run 108: `goals_draw`/`goals_glm`
  `AttributeError`; the cohort trigger also refused the insert and aborted the step). `research_live.py` is part of the
  research identity, so the core research definition changes again; earlier cohorts, observations, selections and decisions
  keep their definition and nothing is relabelled. Regression test: `test_a_game_that_starts_during_the_cycle_is_a_member_skip_not_an_error`.
  Until the next weekly decision (Monday 14:00 UTC) is written under the new definition, the core and pool selectors have
  no decision in force for it, so every selection is "no decision" (research recording continues). The same check now
  runs inside the cohort insert transaction, a trigger refusal is a skip, and line-member forecasts are not written for a
  game that is no longer scheduled at its forecast start (review L5/L6).
- **2026-10-03 ~02:50 UTC, §18 tracking-v2 reporting grid (reconciliation):** summaries now also report the user's required
  descriptive grid — cumulative 50–90% (reg3 35–70%) and 5-point non-overlapping bands, including the NHL three-way bands
  [1/3, 0.35) and [0.35, 0.40) — derived from each stored statistic when read (`REPORT_THRESHOLDS`, `report_bands`, grid id
  `report-grid-v1`). Every cell carries `sealed`; only the sealed registry's thresholds enter the exploratory leaderboard,
  its Bonferroni count and the chronological check, so the sealed registry (f07a54a0…) and recorded observations are
  unchanged. Correction to the §18 text: the lowest band starts at 0.0 ("below …"), not at the floor, because a majority's
  mean over all members can fall below it. Precomputed historical summaries carry the grid id and are rebuilt
  (`tracking-historical`); stale files are ignored.

## Audit corrections — 2026-10-03 (§18, §21)
- **§18 recording:** since migration 0022 a cohort can have more than one observation row per group (one per distinct set of
  stored inputs). The canonical row for a game is the latest observation of the game's latest core cohort; a group with no
  observation at that cohort has none for the game (it is never taken from an older cohort or line).
- **§18 bands:** the lowest descriptive band starts at 0 ("below 0.50", or "below 0.33" for the three-way market), not at the
  floor; the registry's `bands` field is not read by any report.
- **§18 statistics:** in two-way markets the majority statistic (mean probability of the majority side over all members) equals
  the average statistic whenever both pick the same side; in line markets the statistic is the unconditional probability of the
  side (push mass not removed), so it can be below 0.50. A value within 1e-9 below a threshold counts as reaching it.
- **§18 tie rule:** the leaderboard orders by adjusted lower bound, then decided picks, then group id; equal thresholds of one
  group resolve to the lowest threshold.
- **§18 uncertainty:** intervals treat a group and its anchor, and the same game in several groups or markets, as independent.
  They are descriptive and exploratory; nothing is selected from them.
- **§21 comparator:** "strongest existing member" is the best earlier member of the saved table, pool candidates included.

## Joint agreement and confidence exploration — Codex, 2026-10-03
Authorized successor to the one-axis view. Research calculation `joint-1` uses stored member probabilities, not a new forecast ledger or a change to sealed selectors. Exact chosen subsets (2–8 members) are evaluated using a unique strict majority; denominator includes all members, including abstentions. Missing members reject the entire subset. Agreement is supporting votes / subset size. Confidence is either the supporters' mean probability for that outcome, the all-member mean, or the supporters' minimum; these are separate definitions.

Historical evaluation reuses chronological OOF tables (exploratory, previously reused seasons). Goalie-source subsets are refused for historical evaluation because their pregame identity was not established. Live evaluation reads original cohort-linked vectors and never synthesizes forecasts. New query combinations are retrospective classifications of recorded forecasts, not pre-registered live recommendations. Exact lines, dates and definitions remain filterable. No live prediction/model/selector definition is changed by this research view.

For a requested subset and confidence definition, search all contiguous ranges of feasible strict-majority vote fractions (k/n, exclusive upper bound or no upper limit) plus cumulative probability thresholds and disjoint 5-point bands. Rank on earlier seasons by win-rate lift over an earlier-chosen best constituent at matched coverage; minimum 100 decided and 5% coverage. Freeze the rule and comparator for the next season. All later results, including losses, are shown. This bounded, per-subset exploratory check makes no significance or profitability claim and cannot automatically promote a rule. Historical line results remain model-line results, not bookmaker backtests.

## Historical starting policy `historical-start-v2` and the selector's reference bases — Claude, 2026-10-04
Recorded with the code (`backend/app/historical_start.py`, `selector.py`), before any policy was frozen from the live data
directory. Research only: it never replaces a primary forecast, a core or pool selector decision, or their records.

- **Two reference bases in `selector`, never mixed in one evaluation.** `bookmaker` (live, unchanged rule): a quoted market
  is judged against the de-vigged bookmaker probability; an unquoted two-way game is not judged and no longer counts in
  coverage (neither its picks nor its pushes). `historical` (no historical odds exist): every settled item is judged
  against the outcome base rate of the picked side among the evaluated items at the same line; a line with fewer than
  50 such items is not judged and not counted in coverage (lines are never pooled; winner/3-way are one stratum) — never
  a flat 50%. (v1, superseded the same morning after Codex's review, pooled sparse lines and fitted the stacker on all
  earlier seasons; its scratch files are kept, never relabelled.) Lift on this basis is "beats the base rate"; it implies no
  betting value and, for the winner market, mostly reflects picking favourites (reported with the picks' mean model
  probability).
- **Sources:** the live research members' own classes and configurations (`agreement.models`, `research_live.line_models`)
  replayed walk-forward on already-used seasons (NBA validation 2022–24, NHL development 2018–21; never the reserved test
  seasons). Each member alone; the equal average and the stacker only where they can be formed and only when the live
  combination has the same members. The stacker is refit as the live cohorts fit it: at each season start, on the last
  `research_live.STACK_SEASONS` (2) completed seasons, at least `STACK_MIN_TRAIN` (300) games. Agreement filters only when the historical voters equal the live voters (NBA winner:
  `availability_two_hour` has no history, so filters are not offered). A missing combination never removes a game for the
  single members.
- **Procedure:** a weekly chronological check (each week decided on earlier games only, Bonferroni bound over every rule,
  minimum picks/coverage/weeks unchanged), then one decision on all historical games, frozen once per research
  definition and selector version (`data/research/historical-start/<sport>-<definition>-<selector>-<policy>.json`; never
  overwritten). "No rule qualified" is a valid outcome.
- **Live use:** applied at read time to two-hour research cohorts of the same definition made after freezing. Its record
  is reported apart from the live selector's, and the bookmaker's implied probability of the same picks apart from the
  win/loss record. A definition change (model code change) leaves the frozen policy describing the earlier definition;
  a new one must be built. The historical seasons were used to choose the models: exploratory, not held-out evidence.

## Calibration study `calibration-v1` — registered by Claude, 2026-10-04, before any code or result
Question (from the model-contribution audit, docs/MODEL_AUDIT.md): do the research members gain from recalibration, and
is the NBA totals combination gain explained by calibration? Research only: nothing here changes a forecast, a member, a
selector, an identity or a stored row; any live use needs its own registration and review.
- **Data:** walk-forward forecasts of the live members' own code and settings (`historical_start._history`), NBA
  validation 2022–24, NHL development 2018–21 (already used; exploratory). Line markets at the primary's model-only line.
- **Variants per member, every market:** `raw`; `platt` (two-way: logistic map of logit P(side a) on decided games;
  3-way: one temperature on the log probabilities); `shrink` (two-way: 0.5 + λ(p − 0.5); 3-way: toward the uniform
  1/3 each; λ on a 0.00–1.50 grid). Each fitted at every season start on that member's out-of-sample forecasts from
  earlier seasons only (≥ 300 decided games, else that season is not evaluated for the variant).
- **Combinations:** the existing equal average and live-parity stacker of raw members, and the equal average of
  `platt` members.
- **Comparison set:** identical later games: evaluated seasons with every variant of every member and the stacker present,
  same line per game. Paired log loss differences with the week-block bootstrap (95%), plus ECE (10 bins) and accuracy.
- **Pre-stated readings:** a variant "improves the member" if `variant − raw` has its 95% interval below 0. The
  calibration explanation of a combination gain is "supported" if the earlier-chosen best member's `platt` or `shrink`
  variant is not worse than the average (interval of `variant − average` not entirely above 0) while the raw best member
  is worse; "not supported" otherwise. All markets and members are reported, including no-gain and losses.
