# Soccer plan (data-only store first, then a validated three-outcome model)

Goal: trustworthy soccer predictions for the Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Champions League and
Europa League. Fixtures and results come first (done, phase S0). No soccer forecast is shown until a method has passed the
phases below. Each phase ends with checks, a HANDOFF.md entry and a review before the next one starts (one writer).

Standing constraints: the automatic-training pause stays in force; no scheduled training or refits; NBA/NHL/NFL models,
stores and jobs are not changed; no keyed or paid source, purchase or recurring job without the user's explicit go; work
on `NBA_DATA_DIR` copies, never `data/`. Model phases use the `preregistered-evaluation` workflow (preregister → guard
tests → development/validation on copies → seal → review → one logged test run).

## What is being forecast
The target is the **90-minute result** (home win / draw / away win), the outcome 1X2 markets settle on. Three probabilities
per match, summing to 1. Extra time, penalties and two-legged qualification are not the target; qualification markets are
out of scope. Consequence found in S0: ESPN publishes no 90-minute score for a match that went to extra time, so those
knockout matches cannot be graded from ESPN alone. Each phase reports how many matches are ungradable for this reason,
and S1 looks for a source of 90-minute scores; without one they are excluded from grading. They are never assumed to be
draws: a second leg can go to extra time after a non-level 90 minutes. Only single-match ties (e.g. finals) imply a level
90 minutes, and using that would need an explicit S3 rule backed by a verified competition format.

## S0 — Fixtures and results (done 2026-11-07; awaiting review)
Data-only `soccer.sqlite`, migration 0034, `/api/soccer/*`, soccer screens. See HANDOFF.md and docs/SMOKE_TEST.md.

## S1 — Source assessment (read-only; no ingestion into stores)
Find and assess sources for historical results, match statistics and odds. For each, record **actual** findings only:
competitions and seasons covered (counted, not quoted from a web page), fields (90-minute score, extra time, shoot-out,
kickoff time, team ids/names), cost and licence/terms, rate limits, and timestamp semantics (when a row became available;
for odds: opening, a pre-kickoff snapshot with a time, or closing; whether quote times are published).
Candidates to check (none verified yet): ESPN day scoreboards backwards in time (depth of history; 90-minute scores
absent after extra time), football-data.co.uk season files (domestic results and 1X2 odds snapshots; UEFA competitions
likely missing), openfootball / other open results datasets, open event-data releases, and any odds source. Keyed or paid
sources are assessed from public documentation only and proposed with costs; none is used without a go.
Output: a coverage table per competition-season and source, team-identity mapping difficulty, gaps, and a recommendation.
Acceptance: every coverage number reproducible from a cached probe response; nothing claimed from documentation alone is
reported as verified.

## S2 — Historical download and cache
Download the chosen sources through the raw cache (exact bytes, source, request key, retrieval time, content hash,
importer version) with idempotent import and replay, as in S0. Requirements:
- Database uniqueness per source event; repeated ingestion and replay tests; result corrections appended, never overwritten.
- An explicit, versioned team-identity map between sources (ESPN id ↔ other names/ids), with an unmatched-teams report;
  no fuzzy matching applied silently.
- Historical result availability is an assumption (kickoff + fixed delay), labelled as such, like the NBA/NHL/NFL stores.
- Odds kept with bookmaker, market, label (opening / snapshot / closing) and quote time when published. A closing price
  is a benchmark only, never a model input for a forecast made before it existed.
Acceptance: coverage report regenerated from the store matches S1; duplicate/conflict tests pass; replay is byte-identical.

## S3 — Preregistration (before any soccer model is fit)
Written to this file and sealed (digest) before development results exist:
- Chronological periods chosen from actual S2 coverage: development seasons, validation seasons, and a reserved test
  period with an access log. The reserved period is read once, by the one-shot test run.
- Forecast cutoff (e.g. kickoff minus a fixed lead) and the rule that features use only results available by the cutoff.
- Leakage guards as tests: same-day unfinished matches, the current match's own result, future rows, preprocessing and
  calibration fitted outside development data.
- Promoted teams: their starting strength comes from a rule fitted on development data only (for example the average
  entry level of earlier promoted teams), never from their later results.
- Cross-league European matches: one rating scale across leagues needs league-strength offsets; offsets are estimated only
  from European matches before the cutoff. UEFA competitions are also evaluated separately from domestic leagues.
- Metrics, baselines, the decision rule for choosing a method, and the margins required to keep calibration.

## S4 — Baselines and models (development and validation only)
In order, each tested separately:
1. Outcome frequencies: home / draw / away rates per competition from training data only.
2. Draw-aware Elo: Elo updates with a draw as half a win, mapped to three probabilities by a draw model fitted on
   development data (rating difference → home/draw/away), with home advantage and the promoted-team rule.
3. Poisson goals model: attack/defence strengths with home advantage and time decay; a low-score correction only if its
   rationale is stated in advance.
Further models (e.g. combining Elo and goals, market-informed variants) only with a written rationale and the same
periods; NBA/NHL models are not ported by default.
Evaluation: three-outcome log loss (primary), multiclass Brier score, per-outcome calibration (reliability tables),
sample counts, paired comparisons with a seeded matchweek-block bootstrap. Market comparison only on matches with
de-vigged three-way odds available at forecast time, with coverage reported; closing odds as a labelled benchmark.

## S5 — Calibration
Fitted on development data only. Kept only if validation improves log loss by the preregistered margin without worsening
calibration; otherwise the uncalibrated method is used.

## S6 — Freeze, test once, then prospective forecasts
Seal the chosen method (code version, parameters, data version, digest), run the reserved test once and report it as is.
Prospective forecasts need: an append-only soccer forecast ledger (new migration: p_home + p_draw + p_away = 1, generated
before cutoff and kickoff, idempotency key, model/data/feature versions, run type), grading against the 90-minute result
with result revisions traceable, and screens that keep backtest and live records apart. Activation, scheduling and any
recurring job need the user's explicit go; the training pause applies (no scheduled refits).
