# NCAAB / NFL / Soccer candidates and the route from research to published predictions — PROPOSAL (2026-11-26)

Status: proposal for the owner. Nothing here is implemented or approved. Builds on the 2026-11-26 learning audit
(~/nba-predictor-research-data/learning-audit-2026-11-26/ PLAN.md, PROMOTION_CRITERIA.md) and the skill `learning-evidence`.
No step here promises better accuracy: each step only makes a fair comparison possible, and most comparisons will end "no difference shown".

## What exists today (verified 2026-11-26)
- autoresearch-daily re-scores a fixed queue of research trials on the frozen 2026-11-22 snapshot (app code pinned at 23df530). It creates no new
  candidate and uses no new results. It is scheduled re-scoring, not learning.
- Forward store (research/forward-candidates.sqlite): NCAAB, NFL and Soccer record only a baseline. NCAAB and NFL baselines are re-implementations
  computed at record time, not the published forecast; Soccer 1X2 is the published forecast, goals/BTTS/corners are recomputed. Status: "no candidate".
- Prospective challengers are BUILT but NOT SCHEDULED: app/learning_challengers.py with policies in reference/learning_policies/
  (NCAAB C1, C2, C4 calibration/sigma refits of the frozen NCAAB model, C3 the total priced by the existing college_eff model; NFL one-parameter Elo/points variants from nfl_eval grids; Soccer Dixon-Coles,
  half-life and dispersion variants of the served goals joint). Template scripts/launchd/com.nba-predictor.learning-challengers.plist says
  "Install (owner decision)". data/research/challengers.sqlite does not exist, so no challenger row has ever been recorded.
  Update 2026-11-26 (branch challenger-install): the owner decided to install the job for NCAAB, NFL and Soccer only (research only; no promotion; NBA/NHL keep the
  candidate pool). The evaluator (app/learning_eval.py `pair`) now pairs each challenger row with the PUBLISHED run (game_forecasts for NCAAB/NFL; soccer_live_forecasts and the served goals
  joint for Soccer) under FORWARD_PAIRING on INFORMATION times: challenger cutoff_at at or before the published run's information time and at most 10 minutes before it, same exact line, published
  row made by start - 60 min. Published information time: NCAAB the run's source_generated_at; NFL the frozen kickoff - 120 min cutoff (the NFL recorder now stores that as its own cutoff_at/generated_at);
  Soccer goals the real cutoff_at (06:00Z); Soccer 1X2 the real 06:00Z cutoff (a pregame row stores the kickoff as cutoff_at: labelling risk). Unpaired rows keep a reason (no_published_row, line, as_of_late,
  published_after_as_of, candidate_later, gap), with per-sport counts printed by `python -m app.learning_eval`. Known limit: NCAAB challengers price at the recorder's own clock, so a row is paired only when the
  recorder runs at or just before a published near-tipoff run (rehearsal: 14 of 27 paired, 13 candidate_later); the fix (price as of the published run's time) is not built. forward_status shows
  v3["challengers"]: waiting until rows exist, then recording, never passed. FORWARD_PAIRING['published_bar_from'] stays None.
- No code path promotes anything; coverage.py never takes adaptive forecast rows as canonical; promotion criteria are a proposal (PROMOTION_CRITERIA.md).

## Next implementation, in order (each step reviewed, tested, released separately)
1. Bar = the published row for all three sports (forward store). NCAAB: record the served near-tipoff forecast read-only (as NBA/NHL `published`)
   at its own information time; NFL: the stored served nfl_live forecast at kickoff-120 min; Soccer goals/BTTS: the stored soccer_markets_live rows
   (not a recomputation). Re-implemented baselines stay as labelled context rows. Test: published rows equal the canonical served rows on a backup copy.
2. Register challengers in the forward store instead of a second store: each learning_challengers policy writes forward rows with
   inputs.information_at = the served cutoff, under the existing FORWARD_PAIRING rule (same game, market, exact line and side; information time
   within 10 min before and never after the bar). forward_status then reports them for all five sports. These files are outside the
   served-identity list, but autoresearch.app_identity() hashes every app .py/.json: any edit starts a new model_version series for the
   NBA/NHL context incumbents and a new study-cache key. Plan that break deliberately (record before/after) and confirm with git diff --name-only.
3. Owner decision made 2026-11-26: install the challenger job for ncaab nfl soccer (light mode, 15 min, own lock; plist prepared on challenger-install, installed by the lead after the release). Status stays "waiting" until rows exist.
4. Verify on live games: rows carry information time, pairs counted valid, the job stays under its 120 s wall. Only then a dated commit sets
   a FUTURE start (never backdated). FORWARD_PAIRING["published_bar_from"] is one global value today: a per-sport start needs a small code change.
5. One named contender per sport and market chosen BEFORE the start (preregistration in docs/preregistration/, hashed), the others exploratory.

## Validated route from research to published predictions (what is missing)
research trial (development data) -> registered candidate -> forward rows at the published row's information time -> valid pairs counted
from the prospective start -> preregistered evaluator decision (promotion-v1, not built) -> owner approval record (not built) -> served
by a read-side switch with acceptance checks and rollback (not built) -> demotion monitor (not built).
Missing, all needing owner decisions first: promotion constants (PROMOTION_CRITERIA.md: metric log loss, minimum gain, looks, Holm, bookmaker
rule), the approval record format, the serving switch, and demotion rules. The colour rule (bucket-record-v1) and win rate are never evidence.

## Realistic horizon (from PROMOTION_CRITERIA.md sample sizes, not a forecast of success)
- NCAAB: enough games this season (~1,500 per market by late Jan) to detect gains of ~0.003-0.005 log loss, if a contender is registered by early Dec.
- NFL: ~17 games a week; this season cannot decide anything. Record for an across-seasons analysis only.
- Soccer: 1X2 has no challenger (goals joint variants price 1X2 too, so a Dixon-Coles 1X2 contender is possible); ~600 matches per market needed.

## Not in this plan
No change to served models, colours (bucket-record-v1), prediction percentages or Telegram keys. No paid agent work (budget expires 2026-12-31;
renewal is an owner decision). The paid agent covers NBA/NHL only.
