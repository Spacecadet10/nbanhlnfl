# Travel and altitude experiment — proposed design, October 9, 2026

Status: plan only; no features computed, models trained, candidate registered or live definitions changed. Run after recovery review and safe model transitions. Treat effects as hypotheses, not established improvements.

## Question and scope

Do schedule-derived travel and elevation features improve pregame winner probabilities beyond the existing team-strength, recent-form, rest and back-to-back features? Run NBA and NHL separately. Primary endpoint: winner log loss; regulation, spreads and totals are out of scope for this first experiment. Do not add astrology or head-to-head features to this experiment.

## Data gate before any outcomes are analysed

Use read-only snapshots of the existing stores. Build a dated venue lookup (venue ID, team mapping, valid-from/to dates, coordinates, elevation in metres, IANA timezone, source, retrieval time and provenance). The old 30-arena NBA JSON is a seed to verify, not an authoritative source. Research official venue/team/geographic sources before populating this table. Account for relocations, renamed venues, neutral games and unusual home venues. Do not infer a neutral venue from the home team's usual arena.

Use the actual venue of the previous played game and the current game's venue. This is a **schedule travel proxy**, not proof of a team's flight or location between games. Previous games must have ended before the forecast cutoff; uncertain timing or missing venue means missing data, not zero travel. No inter-season travel inference. Use a two-hour pregame cutoff with known schedule revisions where available; historical corrected schedules without revision history must be labelled retrospective provenance limitations.

Publish coverage by sport/season/team and missingness before outcome evaluation. Primary historical analysis requires at least 95% valid venue-pair coverage in each evaluated season; otherwise stop and repair data. Compare every variant on the exact same covered games. Also show a full-slate sensitivity analysis with missing-value indicators fitted only on training data; never silently discard failed predictions.

## Features and ablations fixed in advance

Use the existing base feature vector and the same regularized logistic learner/training schedule for all four variants:

- B: existing sport-specific base features only.
- B+T: base plus travel proxy.
- B+A: base plus altitude features.
- B+T+A: both groups. NBA primary comparison is B+T+A versus B; NHL primary comparison is B+T versus B, with altitude secondary (review amendment below).

Travel group T: per-team great-circle distance in kilometres from previous venue (log1p transformed); signed timezone displacement from longitude-associated IANA zones using each zone's UTC offset at the same current-game timestamp (handles DST); elapsed hours since previous game start as a schedule recovery-time proxy; number of prior games in seven days; consecutive away-game count; distance × existing back-to-back indicator. Construct home and away features symmetrically. Existing rest/b2b fields are controls, not new discoveries.

Altitude group A: current venue elevation, each team's elevation change from previous venue, and visitor elevation change × visitor back-to-back. Do not encode simply “Denver/Utah=yes.” Without controls, altitude may proxy venue/team strength; run a prespecified sensitivity comparison adding home-venue categorical intercepts to BOTH baseline and augmented models (unseen venues use training-defined unknown category). Do not make a causal altitude claim even if forecast loss improves.

Use identical expanding historical training sets and weekly refit cutoffs for the four variants, with all outcome labels available before each cutoff. Learn standardization, missing-value treatment and regularization solely from earlier data. Choose regularization from [0.1, 1, 10, 100] using inner chronological validation, applying the same procedure to every variant. No random split, no changing features after viewing final results. Record all variants, including failures and negative results.

## Evaluation and what counts as evidence

First audit docs/MODEL_SELECTION.md and prior reports for every season's usage. Do not call an already inspected season an untouched holdout. Historical season-forward folds are exploratory: predict each season using only earlier available outcomes, with the above weekly updates. Preserve any existing reserved test restriction; pin the exact eligible development seasons in the run manifest before executing. Do not open an existing reserved test to choose this design.

Primary historical result: paired mean log-loss difference on identical games: NBA B+T+A minus B; NHL B+T minus B. NHL altitude comparisons are secondary and cannot replace a failed primary comparison. Report Brier score, calibration, coverage, season-by-season differences and high-travel/altitude subgroup sample sizes as secondary diagnostics. Win rate alone does not determine success. Use a seven-day moving-block bootstrap within seasons (2,000 replicates, fixed seed 20261009) to estimate uncertainty with approximate schedule dependence. This is exploratory uncertainty, not proof after repeated model selection. Ablations explain the combined comparison; they do not provide multiple chances to select a “winner.”

Proposed shadow-admission gate, fixed before the run: mean log-loss reduction of at least 0.002, a paired 95% block interval below zero, improvement in at least two-thirds of eligible season folds (minimum three folds), and no season degraded by more than 0.005 log loss. These are engineering decision thresholds, not universal scientific constants. Failure means retain the baseline; no silent threshold changes or second feature search disguised as the same study.

If a sport clears this gate, freeze one candidate for that sport and record it prospectively beside B after safe-transition review, without votes, selector promotion or primary replacement. Start a fresh evaluation period at actual registration, not retrospectively at season start. NBA and NHL may have different start dates. Predeclare its end date and comparison procedure before recording begins; do not stop early because results look good. A full subsequent regular-season period is the intended confirmation horizon; if power is inadequate, report inconclusive rather than extending selectively. Improved historical performance is not authority to sell a predictive edge.

## Implementation and safeguards

Reuse the existing feature recorder and learners in an isolated study on copies; add no second app/backend, no production candidate registry edits during exploration. Current definitions hash shared source files, so even a research helper edit can invalidate identities when deployed: run in an isolated checkout/output location until safe transitions exist. Record source commit, dependency lock, store snapshot hashes, venue lookup hash, cutoffs, exact season folds, exclusions and random seeds in a manifest.

Required tests: known coordinate distances and units; DST/timezone direction; venue changes and neutral sites; missing previous game and season boundary; home/away symmetry; future-game and own-outcome mutations cannot affect features or fits at the earlier cutoff; train-only preprocessing; identical comparison cohorts; repeated study does not mutate live stores or artifacts.

Deliverables: provenance/coverage table, reproducible manifest, four-variant paired results for each sport, ablations and venue-control sensitivity, and a pass/fail/inconclusive recommendation. No implementation or live promotion is authorized by statistical success alone; perform the normal independent review and safe release steps.

## Review and response — October 9, before implementation

Claude proposed a market-conditioned comparison, a cheaper venue feasibility audit, travel as the NHL primary hypothesis, and explicit eligible seasons. No results have been inspected for this study. Decisions below amend the design above.

1. **Market comparison: accepted as a separate conditional analysis.** Where archived pregame moneylines exist, compare market-only probabilities, B+market and the sport's primary augmented variant+market on the same games and at the same two-hour cutoff. Use proportional no-vig normalization of both sides from the same bookmaker/snapshot; freeze bookmaker priority in the manifest before inspecting outcomes, never select the best price retrospectively. Supply market probability as a clipped logit covariate, with clipping and preprocessing fixed across variants. Report quote age, missingness and coverage by season; no current-price substitutions or fabricated historical odds. Training and validation need adequate earlier quoted games, otherwise mark this analysis unavailable and defer to prospective collection. This check tests incremental information beyond prices; it does not invalidate outcome-model improvements when prices are absent. Availability of an odds feed does not mean our existing model consumes its probabilities. We have not established that bookmakers fully price these particular features, and log-loss gains do not establish profitability.

2. **Venue feasibility first: accepted with a provenance constraint.** Before building the full lookup, inventory known venue IDs in raw schedule payloads where available and audit neutral/alternate-home flags and team relocations by season. If using a reduced team-arena mapping, date its validity, verify exceptions and mark ambiguous games missing; a neutral flag of false is not proof of the usual venue. Quantify unidentified venues rather than assume exceptions are rare. Use an exception table rather than duplicating a venue per game. Proceed only if the 95% per-season coverage gate can be met with verified mappings. This is a data-only audit before model training.

3. **NHL primary test: travel, with altitude secondary.** Accepted before any outcome analysis: NBA remains B+T+A versus B; NHL becomes B+T versus B. NHL B+A and B+T+A are diagnostics and do not get independent promotion opportunities or veto a passing travel-only result. Their sample sizes will determine what can be said; no assumed geographic-effect magnitude is used as evidence. All four variants remain reported. The admission gate applies to the prespecified primary comparison for each sport.

4. **Eligible historical evaluation seasons: now explicit.** Follow existing MODEL_SELECTION development policies: NBA season IDs 2022, 2023, 2024 (2021–22 through 2023–24); NHL IDs 2018, 2019, 2020, 2021 (2017–18 through 2020–21). Use earlier available seasons for training/warm-up (NHL 2016–17 IDs as existing policy specifies); within a scored season, only newly available past outcomes may enter weekly refits. These are reused development seasons, not untouched tests. Do not score season IDs 2025/2026 or other reserved periods in this experiment. Venue coverage must be measured for these exact folds. If exclusions leave fewer than three eligible folds, historical admission is inconclusive; do not substitute seasons after seeing results. Confirmation must use new prospective forecasts.

5. **Interpretation of the 0.002 threshold.** Retained as a proposed engineering minimum, not a measured or sourced claim about the typical size of travel effects. A null or inconclusive result is acceptable. No benefit from altitude, travel or a market-conditioned model is assumed in advance.

## Season coverage and unusual schedules — fixed before results

- **NBA has no spare fold.** All three named seasons must meet the 95% venue-pair coverage requirement. If any fails, stop the historical admission decision as inconclusive until provenance/coverage is repaired. Do not lower the gate, substitute another season, or promote from two folds. Diagnostic results may still be reported with the limitation.
- **NHL counts all four named seasons, including 2020–21 (ID 2021).** This fixes the choice before results: the primary pooled loss, uncertainty and season-consistency gate include every coverage-qualified named fold. With four qualifying folds, at least three must improve; with three qualifying folds, at least two must improve, and the existing minimum-three-fold rule still applies. Coverage exclusions must be decided without examining prediction outcomes.
- **Mandatory NHL sensitivity reporting.** Flag ID 2021 as an unusual-schedule season in every results table. Report the primary paired comparison separately for ID 2021 and for the remaining folds; also report it excluding both IDs 2020 and 2021 so the pandemic-period sensitivity is not limited to 2020–21. These are diagnostic analyses, not alternate routes to passing or replacements for the prespecified primary decision. The two-fold non-pandemic comparison cannot independently satisfy the minimum-three-fold gate. If the gain is concentrated in unusual-schedule folds, explicitly limit the interpretation and require prospective confirmation under current schedules.
- Venue provenance must handle unusual/neutral venues explicitly. Do not infer ordinary home-arena travel for these games or claim the effect of unusual schedules has been quantitatively established before running the study.
