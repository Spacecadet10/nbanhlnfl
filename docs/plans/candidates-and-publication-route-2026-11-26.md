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
  (NCAAB C1-C4 calibration/sigma refits of the frozen NCAAB model; NFL one-parameter Elo/points variants from nfl_eval grids; Soccer Dixon-Coles,
  half-life and dispersion variants of the served goals joint). Template scripts/launchd/com.nba-predictor.learning-challengers.plist says
  "Install (owner decision)". data/research/challengers.sqlite does not exist, so no challenger row has ever been recorded.
  Their evaluator (app/learning_eval.py) pairs against research incumbents (NCAAB: the served near-tipoff forecast at the same line), not
  uniformly against the published row, and its results are not in forward_status.
- No code path promotes anything; coverage.py rejects adaptive fits; promotion criteria are a proposal (PROMOTION_CRITERIA.md).

## Next implementation, in order (each step reviewed, tested, released separately)
1. Bar = the published row for all three sports (forward store). NCAAB: record the served near-tipoff forecast read-only (as NBA/NHL `published`)
   at its own information time; NFL: the stored served nfl_live forecast at kickoff-120 min; Soccer goals/BTTS: the stored soccer_markets_live rows
   (not a recomputation). Re-implemented baselines stay as labelled context rows. Test: published rows equal the canonical served rows on a backup copy.
2. Register challengers in the forward store instead of a second store: each learning_challengers policy writes forward rows with
   inputs.information_at = the served cutoff, same game/market/line as the published row, under the existing FORWARD_PAIRING rule
   (same line kind, information time within 10 min before and never after the bar). forward_status then reports them for all five sports.
   No hashed file changes (learning_challengers, autoresearch, forward_status are unhashed; confirm with git diff --name-only before commit).
3. Owner decision: install the challenger job (light mode, 15 min, own lock; template exists). Until installed, status stays "no candidate".
4. Verify on live games: rows carry information time, pairs counted valid, the job stays under its 120 s wall. Only then a dated commit sets
   FORWARD_PAIRING["published_bar_from"] to a FUTURE start for these sports (never backdated).
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
