# Candidate pool `pool-v1` — design (recorded 2026-10-02 before any screening run)

Goal: a broad, manageable pool of research learners from which combinations and a selector can later choose using only
earlier evidence. Nothing here changes the sealed primaries, the `research-v3` members/cohorts or their definitions.
Code: `backend/app/candidates/`. Every candidate is research only; promotion only by the written prospective rules.

## 1. Audit (what exists) and family decisions
Existing members (docs/TRAINING_AUDIT.md): NBA winner `elo` (primary), `logistic`, `logistic_v2`, `efficiency_margin`,
`gbt_v1` (HistGBT), `availability_two_hour`; NBA lines `points_normal` (primary), `ridge_points`, `gbt_points`, both also
`@weekly`; NHL winner `hockey_elo` (primary), `goals_poisson`, `hockey_logistic`, `hockey_gbt`, `hockey_logistic_xg`; NHL
lines/reg3 `goals_poisson`, `goals_draw` (diagonal draw inflation), `goals_glm` (Poisson GLMs). CatBoost tested and rejected.

Gaps: no bagged trees; no neural network; no per-game uncertainty for NBA margin/total (constant spreads); no direct NHL
regulation 3-way classifier; NHL count models lack shot quality (`goals_glm` uses goals only); no goalie input anywhere.

| Family | Decision | Reason / evidence |
|---|---|---|
| Regularised logistic | exists; one *reference* re-expression per sport | separates learner from feature effects; never shortlisted |
| Multinomial logistic | implemented (NHL reg3) | no direct 3-way classifier exists |
| Ridge / Elastic Net | ridge exists (reference); elastic net implemented | tests whether sparsity matters on 12 correlated features |
| Random Forest / Extra Trees | implemented | bagging absent; errors may differ from boosted trees |
| XGBoost / LightGBM | **deferred/untested** (amended 2026-10-03, MODEL_SELECTION §21) | related to but distinct from sklearn HistGBT; not ruled out by any HistGBT result; CatBoost rejected 2026-10-02 |
| NGBoost | implemented (optional `research` extra) | per-game spread is a real gap; heavy deps → a dependency-free heteroscedastic linear model registered too |
| Negative binomial | rejected (evidence) | NHL regulation goals given `goals_poisson` rates: Pearson dispersion 1.02 / 0.96 (2018–21, 4,877 games; `docs/evidence/repo-review/probe_nhl_goal_dependence.py`/`.out`) |
| Bivariate Poisson (shared λ3) | rejected (evidence) | residual home/away goal correlation −0.05 (same probe); a shared component only adds positive correlation |
| Dixon–Coles | rejected (covered) | excess draws at every tied score (1-1 3.6% vs 2.7%, 2-2 7.1% vs 5.4%, 3-3 7.0% vs 4.8%; same probe — 1-1 was first written as 3.7%), which diagonal inflation covers |
| Poisson GLM + xG + draw inflation | implemented | joint-score model carrying shot quality (and goalies) to NHL lines |
| Goalie quality | implemented as features | goals saved above our own xG per goalie; see §4 for the knowability caveat |
| Small MLP | implemented (16 units, α = 1, 5 seeds averaged) | ~5–11k games per sport; screened, not assumed better |
| NBA player / lineup impact | deferred | lineups are postgame facts; the pregame input (injury report) is already used by `availability_two_hour` |

## 2. Registry (19 configurations, one fixed setting each; no hyperparameter search)
`registry.SPECS`: name `family:feature_set[:head]`, sport, head (`binary` winner; `normal` NBA margin/total → spread, total,
team totals, winner; `reg3` NHL regulation 3-way + winner; `goals` NHL joint score → every NHL market), feature set, role
(`reference` or `candidate`), reason, refit schedule (season start), optional dependency. Settings: `learners.SETTINGS`
(seed 20261002). Identity: `registry.definition(spec)` = spec + settings + pool code + library versions.

## 3. Shared point-in-time feature walk
`features.Recorder` runs through `backtest.run` once per sport; features are captured at each game's cutoff from results
and box/shot events available then; labels join a fit only when `result_available_at` ≤ the fit's cutoff. NBA features =
`NBA_FEATURES` (as `ridge_points`); NHL = `hockey_logistic` features + goal rates + xG share/special-teams xG (+ goalie set).
Normal-head spreads without a per-game model use the candidate's own out-of-sample residuals from earlier seasons (≥ 200).
Check: the `ridge:nba_base` reference reproduces `ridge_points` (margin log score 4.0468 on 2022–24, as RETRAIN_CADENCE_RESULTS).

## 4. Goalie inputs (NHL)
Ratings: (xGA − GA) / (xGA + 25) per goalie from delivered shots valued by the season's xG fit; earlier seasons halved at
each season (constants declared, not tuned); goalie ids from cached play-by-play (no new requests). **Who starts:** the
retrospective study uses the actual starter (first goalie in net), i.e. it assumes the pregame listing was right (observed
15/16 prospectively, 14/14 for confirmed; docs/CHALLENGER_RESULTS.md, goalie listing check). This is optimistic; goalie results are exploratory upper bounds. Live forecasts
use only the latest ESPN listing retrieved before the forecast; an unmatched or missing listing → rating 0 and a recorded
provenance flag, never a blocked forecast.

## 5. Two-stage study (exploratory; budget declared)
- **Stage 1 `screen`:** NBA scored 2022–2024 (loader `validation`), NHL 2018–2021 (`development`) — spent seasons. Per
  sport·market every candidate and reference: log loss, Brier, accuracy, calibration, per season; paired week-block
  bootstrap vs the strongest existing member (by log loss on these seasons); error correlation with each existing member;
  disagreement rate and each side's accuracy when they disagree; pair average with the strongest member; OOF stacker of
  existing members with vs without the candidate (ablation).
- **Shortlist rule (frozen to `shortlist.json` before stage 2):** a candidate qualifies if its log loss is within 0.002 of
  the strongest member or its pair average beats the strongest member (point estimate); at most 3 per sport·market, ranked
  by pair gain. Each shortlisted candidate gets a triple partner: the existing member (≠ strongest) with the lowest error
  correlation to it.
- **Stage 2 `assess` (once; access logged to `data/eval/pool_assess_access_{sport}.jsonl`):** NBA 2025–2026, NHL 2022–2026,
  never used to choose the shortlist (they were used once before, for the primaries' validation/test, so this is still not
  prospective evidence). For each shortlisted candidate and market: standalone metrics per season; ablations — pair average
  vs the strongest alone, triple vs the same triple without the candidate, stacker with vs without the shortlist; registered
  rules at thresholds 0.55/0.60/0.65/0.70 (reg3 0.45/0.50/0.55): candidate alone, pair average, agreement(strongest,
  candidate) & average ≥ t, the strongest alone; each against the strongest member's (and the average's) most confident
  games at the same count; accuracy-by-coverage curves and the largest coverage reaching 80% / 85%; a weekly selector
  simulation (selector-v1 arithmetic) over the strongest member, the shortlist, their pair averages and the pool stacker,
  evidence only from games settled before each decision, counted on stage-2 games.
- **Budget:** 19 configurations; stage 1 ≤ 45 min per sport, stage 2 ≤ 60 min per sport (single machine). Every fit and
  result is appended to `data/research/candidates/experiments.jsonl`; reports `data/reports/pool-{sport}/`.
- **Reading:** "selected easier games" vs "agreement chose better games": a rule is credited only by its lift over the
  strongest member's own most-confident games at the same count, never by raw accuracy.

## 6. Prospective recording and selection (after the study)
Candidates that pass the shortlist rule (and the references needed for ablations) are recorded prospectively in the
two-hour window, at the core cohort's exact line/quote, under their own definition, append-only (`candidate_forecasts`).
Fitted artifacts are persisted with training cutoff, input and model fingerprints, refit at season start (declared). A
missing or stale input skips that candidate for that game with a reason; it never blocks the primary, the core cohort or
other candidates. The pool selector is specified in §7.

## 7. Pool selection (sealed registry `pool-v1`, 2026-10-02 20:22:45Z, from historical evidence only)
Implemented in `backend/app/candidates/selection.py`; registry `backend/app/candidates/pool_rules.json` (sealed sha256,
`python -m app.cli pool-rules --effective-from <time>`; frozen from the committed stage-2 evidence, never from live results).
- **Two qualifying routes**, judged once on the held-out (stage-2) seasons with `held_out_gains`:
  1. *standalone* — the candidate alone beats the strongest existing member (log-loss difference, 95% upper bound < 0);
  2. *combination* — an equal average with the candidate beats the same average without it (week-block bootstrap,
     95% upper bound < 0, chronological held-out seasons) **and** beats the strongest member alone.
  A candidate need not beat the strongest member individually to enter a combination. A candidate qualifying only by
  route 2 is a selector source **only as that average** (`avg:...`), never alone. Candidates with neither route stay
  research-only: still recorded and shown, never sources. Each source records its route and exact evidence (file, key,
  comparison, mean, CI, games, weeks).
- **Sealed sources (pool-v1):** NHL reg3 (`multinomial_l2` base/goalie and `poisson_draw:nhl_base`, alone and in averages
  with `goals_draw`, `goals_poisson`), NHL puck line and away team total (`poisson_draw` base/goalie, alone and in
  averages), NBA home team total **only** `avg:points_normal+cand:hetero_linear:nba_base` (combination route; `hetero_linear`
  is not a source alone). No candidate qualified for any winner market or NBA spread/total/away total.
- **Weekly pool decisions** (`pool_decisions`, migration 0020): the unchanged selector-v1 arithmetic (`selector.decide`:
  ≥ 100 settled picks, ≥ 10% coverage, ≥ 3 weeks, Bonferroni lower bound of lift > 0 over every rule of core + pool
  sources, hysteresis), same Monday schedule and evidence cutoff as the core decisions, frozen for the week. Items are the
  canonical core cohorts settled before the cutoff **and made after the registry took effect** (the registry is frozen
  before the evidence it is judged on); each pool source's vector is joined only from a candidate forecast made for that
  exact cohort (same game, market and start; same line — none for winner/reg3 — and `line_ref = research_cohorts:<id>`
  for every market; the registry's pinned candidate identity; made at or after the cohort and before the start). Anything else leaves the source absent
  ("source unavailable", no fallback). Registry membership only makes a source selectable; it is never selected without the
  selector's own evidence.
- **Live pool selections** (`pool_selections`): written in the two-hour cycle after the candidate forecasts, under the pool
  decision in force, with the candidate forecast ids each source used. Core (v1) decisions, selections, recommendations,
  cohort definitions and the selector version are unchanged (separate tables; no hashed file edited).
- **Live selections skip** a rescheduled game's old cohort (void) and games no longer scheduled; one game's failure never
  withholds the others'.
- **Artifacts:** each candidate fit is stored with a sha256 digest, verified before it is unpickled (a mismatch makes that
  candidate unavailable; never a substitute); writes are locked per artifact and atomic. A file saved before digests
  existed is **never loaded**: it is kept as `.legacy` and superseded by a deterministic refit from the same earlier-season
  data, and `artifact_adoptions` records whether the refit reproduced the content digest the earlier code registered in
  `model_fits` (all 9 live NHL fits did, checked on a store copy), with the limitation that this does not establish the
  legacy file's bytes afterwards (they are no longer used).
- **Reports:** `/api/research/pool` and `pool-report` score every pool source prospectively (one cohort per game across
  core definitions; win/loss/push on settled
  canonical cohorts; void and pending excluded) beside the decision history; the roster shows each candidate's route per
  market and the pool decision in force.

## Amendments
- **2026-10-02, before any scored run:** NGBoost uses a seeded base tree and chronological early stopping on the latest
  training season (repository review: the default base tree is unseeded and the built-in validation split is random); dry
  runs on warm-up seasons only (NBA 2021, NHL 2017) exercised the code; NHL shots go only to `hockey_logistic_xg` among the
  existing members (as expanded_search). Live line markets default to the latest core cohort's exact line.
- **2026-10-02, after stage 2 (results: docs/CANDIDATE_POOL_RESULTS.md):** selection eligibility narrowed to held-out
  confirmed gains (`candidates.live.held_out_gains`); the shortlist itself is unchanged and every candidate keeps recording.
  The pool selector simulation started with empty evidence at stage 2 (the pool stacker exists only there).
