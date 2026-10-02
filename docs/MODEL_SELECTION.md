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
