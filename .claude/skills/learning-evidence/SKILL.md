---
name: learning-evidence
description: Use before touching forecasts, candidates, forward tests, pick colours, grading, promotion or the files whose bytes are hashed into model identities. Also when asked whether a candidate or adaptive model is better or ready to be published.
---
# Learning-evidence rules

Sources (cited per rule): ~/nba-predictor-research-data/learning-audit-2026-11-26/ {BUILD_RULES.md = B, FINDINGS.md = F, PLAN.md = P,
PROMOTION_CRITERIA.md = PC}; docs/AUTORESEARCH.md; docs/PICK_COLOURS.md; HANDOFF.md "Forward bar repair" entry (FB). PC and P are proposals, not adopted.

## DO NOT EDIT without an owner-approved model change (B)
Bytes are hashed into model/activation identities: an edit deactivates NFL (the Telegram run then records NFL `inactive` with the reason,
`GET /api/nfl/activation`) or retires NBA/NHL fits (F: activation hash covers nfl_live, nfl_eval, coverage, markets, models/base, elo, points).
Files: nfl_live.py, nfl_eval.py, coverage.py, markets.py, models/*.py, adaptive.py, player_ratings.py, nhl_player_ratings.py, sources/nhl_injuries.py,
combine.py, research_live.py, market_live.py, retrain_cadence.py, fit_register.py, candidates/{features,learners,engine,goalies,registry,deep}.py,
deep_study.py, daily_agreement.py, combination_thresholds.py, backtest.py, agreement.py, availability*.py, reference/identity_aliases.json,
best_pick_record.py, every sealed JSON (models/*.json, soccer_*sealed*.json, intl_sealed_candidate.json, soccer_markets_reading_*.json,
soccer_*protocol*.json). Library versions are hashed too (scikit-learn in the research/agreement digests, `libraries()` in registry.definition):
a venv package upgrade changes identities. `identity_aliases.json` can map changed code to an old identity: treat an alias as a reviewed decision.
NFL pin: docs/nfl_activation_pin.json; re-pin (`python -m app.nfl_status pin`) only after a reviewed, deliberate `nfl activate`.
Unhashed code that still changes served predictions with no identity change (review it as a model change): ncaab.py, ncaab_app.py, near_tipoff.py,
live.py, soccer_live.py, soccer_markets_live.py, nfl.py.
Check `git diff --name-only <base>` before committing; if a fix needs one, STOP and report. Also unchanged unless the brief says so: card text and
Telegram delivery keys (the weekly recorder parses them), prediction percentages, colour rules (threshold_overview.bucket_colour, COLOUR_RULE), promotion.
Historical rows are never rewritten or deleted; label them. Live data folder: never write; use a backup copy as NBA_DATA_DIR.

## Forecast-time inputs
Every input must be available at `inputs.information_at`; quotes retrieved at or before it. Calendar date is not a cutoff (CLAUDE.md). Known
labelling risks (F): NCAAB sealed validation used 00:00 ET cutoff vs the served two-hour window; soccer 1X2 `cutoff_at` stores kickoff while the real cutoff is 06:00Z.

## Valid pairing (FB, docs/AUTORESEARCH.md)
The bar is the PUBLISHED forecast as of start - 60 min (not necessarily the last card). A pair needs the same game, market and line; against the
published bar also the same line kind (bookmaker vs model line); the candidate's information time between 10 min before and the published row's
time, never after. The quote itself is not compared. Rows without information time (all pre-repair rows) are labelled
"invalid: no information time (pre-repair)", never rewritten, never counted. Do not cite the API's `raw_joined_graded_items` (a naive join);
only `paired_graded_items` (valid pairs).

## Statuses (forward_status.py, FB)
no candidate / waiting / invalid comparison / failed / recording valid evidence. Say which; "recording valid evidence" is not "passed".

## Prospective start
`FORWARD_PAIRING['published_bar_from']`: None = nothing confirmatory. Set only by a separate dated commit to a FUTURE start, after live verification;
never backdated; nothing before it counts.

## Recovered history vs live colour snapshots (docs/PICK_COLOURS.md)
`pick_snapshots.sqlite` is append-only (trigger), in the nightly backup. Live colours are saved before the start; recovered history is separate
and has its own colour field. Backfill (`python -m app.pick_snapshots backfill [--apply]`) only from saved card text (printed %, printed W-L, rule legend);
no reconstructed colours are ever used; unknown colour stays unknown (`before_colour_rule`, `not_recorded`, ...).

## Grading
Saved side and line are graded on read by the app's own settlement. Win rate = wins / (wins + losses); pushes, voids, unsettled counted apart. A stored
forecast that no longer equals the saved pick is unscorable. Units only with a stored price at the same side and line, at most 20 min old.

## Colours
Rule bucket-record-v1 is unchanged (green/red/neutral by the exact printed % bucket's W-L record). Always show W-L and n; n < 30 is a small
sample label (threshold_overview.MIN_LIVE_GRADED = 30; it also picks the evidence band behind `rank`/`strong`). Owner 2026-11-26: labels yes,
a 30-result minimum GATE is NOT approved. Colour is display only and never decides recommendation eligibility: the card's Recommended pick is
tickets.card_choice (context-card-v1, owner-authorised 2026-11-12; docs/TELEGRAM.md note). The SAME exact-% record still feeds its rank and
its "losing record" warning with only RANK_MIN_SETTLED = 3 results. Colours measure; they do not show green picks are profitable.

## Known train/serve gaps (F)
Live NHL line markets force `empty_net: True` (market_live.py) unlike their seal; adaptive trains at model lines but serves at bookmaker lines.

## Never evidence for promotion (F, P, PC)
Retraining, coverage and win rate. Adaptive forecasts are not in the forward store and `coverage.py` rejects adaptive unconditionally. Promotion
proposal (log-loss, one named contender, Holm, intention-to-treat) is NOT adopted. Nothing promotes without an owner decision; the evaluator may only recommend.
