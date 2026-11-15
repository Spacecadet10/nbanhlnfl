# Plan: test whether additional systems improve NBA forecasts, without drift

## Context
The current models:
- **Elo**, the only model used for live forecasts, frozen in `backend/app/models/frozen_decision.json`.
- **Logistic regression**, which already exists (`backend/app/models/logistic.py`). It was a validation candidate and did not
  qualify, so it is reported descriptively only.

On the held-out 2024–26 test, Elo scored 67.2% / Brier 0.2105. That test **has already been used once**. It stays historical
evidence only and is never used to tune, select or justify a new model. That includes logistic's better test score.

The goal is **to test whether additional systems improve forecasts**, not to promise better accuracy. "No improvement
found" is a valid outcome. Candidates:
- efficiency/margin features and a margin model
- logistic regression (existing and extended)
- an ensemble, only if validation supports one

More sports: a roadmap and architecture boundaries only; nothing is built for other sports.

Fixed constraints:
- free, no-key sources only
- no future information in features
- frozen Elo stays the public forecast unless a candidate passes a rule written **before** its prospective results exist
- small steps, each with checks and a Codex review before the next
- manual approval for edits that touch data or evaluation

## Evaluation design (written into docs/EVALUATION_PLAN.md before any candidate is run)
- **Development (screening only):**
  - Walk-forward on warm-up 2019–21 and validation 2021–24, with the existing cutoff rule, `load_rows(conn, "validation")`
    season isolation and `LeakageError` guard.
  - Validation is optimistic: Elo was tuned there, and candidates will be too. It only screens which candidates go on to the
    prospective test; it does not prove improvement.
- **Prospective test (the evidence): 2026-27 live shadow season.** Candidates are frozen before opening night and record
  forecasts before each cutoff (`run_type='live'`, their own `model_version`). Nothing is changed mid-season.
- **Baselines, all scored on the same games:**
  - constant 0.5
  - home-rate
  - frozen Elo
  - DraftKings pregame moneyline with the bookmaker's cut removed: the last ESPN quote retrieved before the cutoff. Its coverage is reported, and nothing is filled in.
- **Metrics:** accuracy, Brier, log loss, calibration bins, sample size and date range.
- **Paired uncertainty:** per-game loss differences against Elo, via the existing `backtest.paired_bootstrap` (ISO-week blocks, seed 20260930).
- **Prewritten selection rule:**
  - At most **2** candidates enter shadow.
  - At season end (all 2026-27 regular season plus play-in games with forecasts), a candidate replaces Elo only if the
    upper bound of the Bonferroni-adjusted interval (97.5%) on Brier(candidate − Elo) is < 0 **and** its log loss is not worse.
  - If both pass, the lower Brier wins.
  - Interim looks are for operations only, never for decisions.
  - If fewer than 800 scored games exist, the result is "inconclusive" and Elo stays.
- **Screening rule** (which candidates go to shadow): a validation Brier point estimate below Elo's, and a paired 95%
  interval upper bound on Brier(candidate − Elo) below +0.001. It is deliberately lenient, because the shadow season is the
  real test. There's a cap of 2, taken by lowest validation Brier.

## Steps (each ends with `--ready` passing, a plain-words summary, a Codex review, then approval)
0. **Preregistration:** write the evaluation design above into `docs/EVALUATION_PLAN.md` and correct
   `HANDOFF.md`. Docs only.
1. **Source verification, read-only:** before depending on anything new, check ESPN's `/nba/summary?event=ID`.
   - Coverage: a sample of about 30 games per season 2019–26, reporting the share with turnovers and offensive/defensive rebounds.
   - Whether values change after the game (re-fetch a recent game over 2 days).
   - What timestamps exist.
   - Request cost.
   - ESPN's terms of use, with findings recorded. They are still unverified for commercial use, which blocks selling but not research.

   The same checks apply to `/nba/injuries`: what its `date` field means, and whether history is kept.
   Results go in `docs/DATA_SOURCES.md`.
   **Gate:** proceed to step 2 only if coverage is at least 95% per season. Otherwise stop and report.
2. **Possession data ingestion.**
   - `espn.py`: `summary_request` / `fetch_summary` / parser, following the `day_request` / `fetch_day` / `_get` pattern.
   - Migration `0005`: add `tov`, `oreb`, `dreb` to `team_game_stats`.
   - `ingest.ingest_summary`, reusing `_ingest`, `raw_cache.store` and the `_upsert_stats` ordering. Included in `replay`.
   - Availability follows the result rule: historical rows use the assumed availability, live rows the observed retrieval time.
   - Resumable CLI `backfill-summaries`: about 9,400 requests at 1 per second, run in the background after a one-month trial.
   - `daily` fetches summaries for newly finished games.
   - Tests: no duplicates on rerun, replay gives the same result, missing data is reported.
   - Coverage is reported per season.
3. **Efficiency features:** in a feature module used by the models, time-decayed offensive and defensive points per 100
   possessions and pace, opponent-adjusted, using only results available at the cutoff. Leakage tests:
   - same-day unfinished games
   - the current game's own result
   - future rows
   - preprocessing fitted on training data only
4. **Candidates on validation:**
   - (a) efficiency margin model: predicted margin turned into a probability with a normal curve whose spread is fitted on training data
   - (b) existing logistic
   - (c) logistic v2: Elo difference, net-rating difference, rest, back-to-backs, neutral site, with no feature search

   Registered in `backtest.CANDIDATES` / `candidate_models`, with `FEATURE_VERSION` bumped. The report goes to
   `docs/BASELINE_REPORT.md` and applies the screening rule.
5. **Ensemble, conditional:** only if at least 2 candidates pass screening *and* their validation errors are not almost the
   same (per-game Brier correlation < 0.95). Weights are fitted walk-forward on earlier seasons only. The ensemble counts
   toward the cap of 2. Otherwise this step is skipped and the skip recorded.
6. **Shadow recording:**
   - Seal the chosen candidates and the selection rule, reusing `backtest.seal()`.
   - `live.predict` also records the shadow forecasts. The UI keeps showing Elo only.
   - A `shadow-report` CLI shows the metrics above against the baselines and the market.
   - Target: before opening night, about 2026-10-20. If it's late, shadow starts on its seal date. Nothing is backfilled or
     made retroactive, and the smaller sample is reported.
7. **Injury snapshots, optional, after step 6:** if step 1 passes for `/nba/injuries`, store timestamped snapshots
   append-only. They are **not used by any model this season**.

## Multi-sport roadmap (plan only; nothing is built for other sports)
- **Order:** finish the NBA shadow decision first. Then choose the next sport by free data coverage checked the same way as
  step 1. ESPN's scoreboard covers NFL, NHL and MLB in the same format.
- **Parts that are reusable now:**
  - the raw cache (`raw_cache.py`)
  - the append-only ledger (`ledger.py`)
  - job logging (`jobs.py`)
  - metrics and bootstrap (`metrics.py`, `backtest.paired_bootstrap`)
  - the model protocol (`models/base.py`)
  - odds de-vigging (`odds.py`)
- **Parts tied to the NBA today, to generalize only when a second sport starts:**
  - ESPN URL and season types (`sources/espn.py`)
  - team IDs 1–30
  - cutoff rule and periods (`backtest.py`)
  - Elo settings
  - the absence of a `sport` column in `games` and `predictions`
- **When sport 2 starts:** add a `sport` column through a migration, one source adapter per sport, and evaluation periods and
  a frozen decision per sport. The same preregistration and shadow process applies. Draws (soccer) or low scoring (NHL) need
  their own model types, not the NBA's.

## Verification
- Every step: `backend/.venv/bin/python scripts/check_project.py --ready` and `python3 scripts/check_project.py`, with exact results in HANDOFF.md.
- Step 1: a coverage table in DATA_SOURCES.md from real requests.
- Step 2:
  - a one-month real backfill
  - full coverage per season
  - `replay` gives identical row counts
  - the ledger and the test-access log are unchanged, with the same row count and test-access line count
- Steps 3–5: a validation report with every candidate, the intervals against Elo and the screening outcome. `data/eval/test_access.jsonl`
  still has exactly 1 line.
- Step 6: a fixed-clock test shows Elo and shadow rows written before the cutoff. After the first game night, the real `shadow-report` lists scored games for each.
