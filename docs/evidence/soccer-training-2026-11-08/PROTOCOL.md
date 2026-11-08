# Soccer 1X2 evaluation protocol (S3) — frozen before any model fit

Machine-readable source of truth: `backend/app/soccer_protocol.json` **version 2**, sha256
`f37946397ce971a28dd2d1603fad570273e9b7eb50a67583fbe9830ac32f70e9`, pinned in `backend/app/soccer_eval.py`
(`PROTOCOL_SHA256`; the module refuses to load a changed file). This page restates it; where they differ, the JSON rules.
Version 1 (`043b5ede…`) was sealed 2026-11-08 16:27Z; amendment 1 (below) was made after the independent S3 review
(REQUEST CHANGES) and the S2 data-quality review, **before any soccer model was fitted, any development or validation
metric existed, or any outcome rate was computed** (see `s3/protocol_seal.json`).

## Amendment 1 (2026-11-08, before any fit)
- Model choice and calibration acceptance additionally require a lower mean log loss in **at least 2 of the 3 validation
  seasons** (so the crowd-free 2020-21 season cannot decide alone).
- Fewer than 1,000 calibration matches → calibration not attempted (uncalibrated), instead of failing.
- Poisson prior centres: the season used is the forecast match's (other clubs: their latest input row's); centres come
  from the previous cutoff's fit; "other clubs" = the league-season's other members in that fit, promoted ones included;
  newly covered league or no such member → 0.
- Availability: a first result appearing in a later retrieval of a match already listed without one, or a change of
  whether a result may be used (a second leg appears, a match is listed again), counts only from that retrieval
  (`observed_retrieval`); a date change is a correction; never before the day after the match (schema CHECK on matches
  and revisions).
- Disclosed: **66 EPL 2019-20 matches** played after the June 2020 restart are dated July 2019 by the source and were
  rejected at import — absent from frequencies, inputs and the calibration set (EPL 2019-20: 314 of 380 matches).
- Reserved access hardened: development runs on a **copy that holds only seasons ≤ 2022**; seasons after 2025 always
  refused; one test run per access log whatever the seal; the candidate must be the **only** seal the mandatory
  experiments log recorded (a start entry precedes every development load); dataset pins (commits, importer, club map)
  checked; code digest also covers `backtest.py` and `metrics.py`; numpy/scipy/python versions recorded.
- Reporting: exclusion counts, reliability tables for every model, validation with and without UEL 2020-21, Elo
  development figure labelled in-sample for its draw model.
- Engine: models never receive the label of the match they forecast.

## Target and data
- 90-minute result (home / draw / away), three probabilities summing to 1. Qualification, extra time and shoot-outs are
  not targets.
- openfootball (CC0) history at pinned commits (football.json `e6744429…`, champions-league `abfaeddc…`), importer
  `openfootball-1`, club map `2026-11-08.1`. EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL main stage, UEL main stage.

## Periods (season = start year)
| Role | Seasons | Notes |
|---|---|---|
| Warm-up | 2010–2012 | ratings burn in; never scored |
| Development — tune | 2013–2017 | hyperparameters, Elo draw model |
| Development — calibration | 2018–2019 | calibrator fit only |
| Validation | 2020–2022 | model choice, calibration acceptance |
| Reserved test | 2023–2025 | locked; one logged run after review (not in this task) |
| Prospective | 2026 | locked |
Each league's first covered season is never scored (EPL/Bundesliga 2010, La Liga 2012, Serie A 2013, Ligue 1 2014).
UEFA competitions are scored from their first season. **UEL has no development data** (covered 2020–2024 only): it is
evaluated in validation (2020–2022) and test (2023–2024) with UCL's competition parameters.

## Point in time
- Cutoff: 06:00 UTC on the match's listed local date (earliest listed kickoff 12:00 local). All matches of a date share
  one state: same-day results never inform each other.
- A result revision becomes an input at its `result_available_at`: the first result is **assumed** known the next day
  06:00 UTC (the source has no publication time); a different later result (a correction) only from the retrieval that
  delivered it. Inputs must be usable for grading at that revision: final, no a.e.t./pen. note, not a UEFA deciding match.
- Never inputs: scheduled, result missing, cancelled, awarded, extra-time/shoot-out noted, UEFA deciding matches, rejected
  rows. Kickoff time is not used.
- Elo consumes each match once, when first available (corrections do not rewrite earlier updates); Poisson refits from the
  as-of snapshot at each cutoff.

## Scoring
- Scored: latest revision gradable, scored season, not a league first season. Label = sign of the 90-minute score.
- Primary metric: multiclass log loss. Reported: multiclass Brier, per-outcome 10-bin reliability and ECE, mean predicted
  vs observed rates, accuracy of the most likely outcome, n — overall, per competition, per season.
- Uncertainty: `backtest.block_bootstrap` (ISO-week blocks of the cutoff, seed 20260930, 2,000 resamples, 95 %).

## Candidates (each tuned on development — tune only)
1. **Frequency**: home/draw/away shares per competition from all gradable matches of 2010–2019; UEL uses UCL; fixed.
2. **Draw-aware Elo**: one scale across competitions; K, home advantage H, season carry C, promoted offset P, UEFA-only
   club offset F, optional goal multiplier (grid 4×3×3×3×3×2 = 648). League-season start: newly covered league → 1500;
   otherwise returning clubs regress to the previous season's league mean M by C, promoted clubs start at M − P. Clubs
   outside covered leagues start at 1500 − F and regress to their pool mean each season. Ordered-logit draw model (one
   slope, cut points per competition, UEL = UCL) fitted on development — tune. Tie-break: smaller K, H, larger C, smaller
   P, F, mov off.
3. **Poisson attack/defence**: competition goal level and home advantage (UEL = UCL), club attack/defence on one scale,
   730-day window, exponential time decay (half-life 180/365/730 days), ridge τ ∈ {3, 10} toward a prior centre (0;
   promoted clubs: league mean − D, D ∈ {0.1, 0.25}); refit at every cutoff; no low-score correction. Tie-break: larger
   half-life, larger τ, smaller D.

## Choice on validation (pooled over all competitions)
Start with frequency; Elo then Poisson replaces the current choice only if its validation log loss is lower, the 95 %
interval of the paired per-match difference is entirely below 0, **and** it is better in at least 2 of the 3 validation
seasons. Otherwise the simpler model is kept. A more complex model is compared with the current choice only.

## Calibration (selected model only)
Multinomial logistic recalibration of log-probabilities (scale s, draw and away offsets; L2 1e-3), fitted on the
selected model's development — calibration forecasts (≥ 1,000 matches, else not attempted). Kept only if, on
validation, log loss is lower with the paired 95 % interval below 0, lower in at least 2 of the 3 validation seasons,
**and** no outcome's ECE worsens by more than 0.005. Otherwise uncalibrated.

## Frozen for test and reserved access
Nothing is refitted for the test; the walk-forward continues using only results available at each cutoff. The loader
refuses seasons after 2022 unless given a valid sealed candidate and an access log (written before any read); the
one-shot runner refuses an invalid seal, an existing output, a second run of the same seal, and changed code, protocol or
open data. **This task stops before running it.**

## Disclosure of pre-protocol inspection
Before sealing, the lead inspected source integrity only: file and match counts per competition-season, status/grading
counts, rejected lines, a handful of individual match lines and scores (including 2023-24 and 2025-26 samples) while
checking parser formats, and club names. No outcome rate, home/draw/away share, goal average or metric was computed for
any period. The S2 data-quality reviewer was asked to spot-check real results across seasons (source integrity).

## Not claimed
No betting edge; no comparison with bookmaker odds (none available from a permitted free source); no evaluation of
totals, both-teams-to-score or qualification markets.
