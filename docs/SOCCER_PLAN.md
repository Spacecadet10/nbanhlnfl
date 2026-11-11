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
out of scope. ESPN's status name does not establish how a match ended (S1: 2 extra-time matches labelled FULL_TIME,
10 false AET labels), so S0 classifies the ending from status, period, clock, event minutes, shoot-out evidence and the tie
structure together; any disagreement is stored as `unknown` with the reason, and only a consistent `regulation` ending
shows a 90-minute result. A 90-minute score after extra time is derivable from goal minutes, but derived values stay
disabled until S3 preregisters the grading rule: grade only when the 90-minute score from the results source agrees with an
independent second source; otherwise the match is ungradable. A draw is never assumed: a second leg can go to extra time
after a non-level 90 minutes.

## S0 — Fixtures and results (corrected after the 2026-11-07 audit; acceptance pending review of the corrections)
Data-only `soccer.sqlite` (migrations 0034, 0035), `/api/soccer/*`, soccer screens, dev launcher/publisher support. The
audit found that ESPN's FULL_TIME label hid extra time and that replay, tie facts and team changes needed stricter rules;
the corrections (extra-time classification, per-retrieval replay identity for soccer and NBA/NHL/NFL, tie-fact
availability, team-change revisions, rejection log, fetch-only republication) are recorded in HANDOFF.md and
docs/SMOKE_TEST.md. Data is updated only by manual `app.cli soccer ingest`; no recurring job. Soccer data now comes from
ESPN, which S1 found not clearly permitted for this use: it stays a development fixture until the user decides.

## S1 — Source assessment (read-only; no ingestion into stores)
Find and assess sources for historical results, match statistics and odds. For each, record **actual** findings only:
competitions and seasons covered (counted, not quoted from a web page), fields (90-minute score, extra time, shoot-out,
kickoff time, team ids/names), cost and licence/terms, rate limits, and timestamp semantics (when a row became available;
for odds: opening, a pre-kickoff snapshot with a time, or closing; whether quote times are published).
Candidates checked: ESPN scoreboards backwards in time (the pre-S1 hypothesis "90-minute scores absent after extra time"
was disproved: derivable once extra time is detected), football-data.co.uk season files (domestic results and 1X2 odds snapshots; UEFA competitions
likely missing), openfootball / other open results datasets, open event-data releases, and any odds source. Keyed or paid
sources are assessed from public documentation only and proposed with costs; none is used without a go.
Output: a coverage table per competition-season and source, team-identity mapping difficulty, gaps, and a recommendation.
Acceptance: every coverage number reproducible from a cached probe response; nothing claimed from documentation alone is
reported as verified.

### S1 result (2026-11-07; report rewritten after the audit): docs/evidence/soccer-s1-sources-2026-11-07/REPORT.md
Rule: every source must be **free** (no paid plans/trials/purchases) **and clearly permitted** for this use; free API keys
only after the user agrees to create the account; odds optional; no manual-download workarounds. Findings: among the sources
checked, only openfootball (CC0, licence read in all 7 repos) is both free and clearly licensed. Season files exist for EPL
(1992-93+), Bundesliga (2010-11+), La Liga (2012-13+), Serie A (2013-14+), Ligue 1 (2014-15+), UCL main stage 2011-12..2025-26
and UEL 2020-21..2024-25 only; match contents were counted only for EPL 2022-23..2024-25, UCL and UEL. 90-minute/HT scores and
incomplete "a.e.t."/pens notes; no statistics, no odds. EPL 2023-24 matches ESPN 380/380. Wikipedia goal minutes agree with
the ESPN-derived 90-minute score on the 8 sampled ESPN-labelled AET/PEN matches (the 2 FULL_TIME-labelled extra-time matches
were not compared). ESPN, football-data.co.uk, FBref, Transfermarkt, OddsPortal and StatsBomb carry explicit restrictions
(quoted in the report). No free, permitted historical odds were verified among the 11 sources checked; Betfair and
API-Football are unassessed. Compliance: the S1 probes made 9 football-data.co.uk requests (robots.txt, then 8 pages despite its disallow; the recheck
re-read robots.txt once, 10 in total; the source is excluded),
and 5 Wikimedia API calls lacked the required descriptive User-Agent.
**S2 proposal (not started):** results-only cache from openfootball for the supported competition-seasons; 90-minute
grading after extra time only with independent second-source agreement; UEL limited to 5 seasons (or left out); statistics
and odds labelled unavailable. **For the user (account decisions only):** a free football-data.org account (current UCL + 5
leagues, not UEL; conditions: one application per key (2.3), credentials not in open-source repositories (6.1),
attribution (7.1), no referencing of the data on one's own site or service after cancellation (9.1)); optionally a free The Odds
API account (forward-only odds); Wikipedia as a validation source (descriptive User-Agent required); UEL scope. The existing
ESPN collection (soccer S0, NBA/NFL) is flagged against the "clearly permitted" criterion; no schedule was changed.

## S2 — Historical download and cache
Download the chosen sources through the raw cache (exact bytes, source, request key, retrieval time, content hash,
importer version) with idempotent import and replay, as in S0. Requirements:
- Database uniqueness per source event; repeated ingestion and replay tests; result corrections appended, never overwritten.
- An explicit, versioned team-identity map (openfootball names ↔ the store's team ids; the anchor is chosen once the user
  decides on ESPN), with an unmatched-teams report; no fuzzy matching applied silently.
- How a match ended is detected from period/clock/event minutes or the source's explicit a.e.t./pens notes plus an
  independent check, never from a single status label (S1: ESPN is wrong both ways).
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
