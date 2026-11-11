---
name: courtside-ticket-review
description: Review Courtside forecasts, build distinct parlay tickets with AI selection reasoning, send them to the paired Telegram chat, and review the exact sent results.
---

# Courtside ticket review

Use app forecasts plus your judgment to select and combine legs. Your selection is not a second predictive model. The user authorized this skill to send reviewed tickets to the existing paired private Telegram chat. Scheduling is separate from invocation.

## Review the skill every run

Read this entire file and the project's HANDOFF.md. Assess whether these instructions fit the current backend contracts, source freshness, user preferences and known failures. Record this assessment in review.notes. Correct demonstrated workflow defects and verify them before delivery. Do not silently change selection rules based on winners observed afterward. Version substantive methodology changes and get the user's agreement. Reviewing the workflow does not establish future prediction accuracy.

The project is /Users/andry/Desktop/nba-predictor-sprint1. Run the bundled helper with backend/.venv/bin/python from that directory. It imports the existing backend and Telegram sender, reads forecast stores without changing them and uses the existing private configuration and delivery receipts.

Run settlement and operational checks BEFORE selection each run:

    backend/.venv/bin/python -m app.tickets preflight --notify

Run from backend/ or set PYTHONPATH=backend from the repository root. This appends exact-forecast outcome changes, sends a deduplicated settlement for confirmed v3 deliveries, and alerts on stale/unavailable sources and overdue missing forecasts. No source refresh or model training is performed. Review leg-level unavailable errors as failures, never as wins. A Telegram acknowledgement confirms API acceptance, not the user's read/visibility.

Take a snapshot with a new output filename each run. The helper reports soccer explicitly unavailable while there is no soccer forecast model. Never substitute results-only matches as predictions.

    backend/.venv/bin/python ~/.codex/skills/courtside-ticket-review/scripts/tickets.py snapshot --output /tmp/ticket-snapshot.json

The output prints the snapshot's SHA-256. Missing sports are explicit gaps. Use NFL's own Views contract. Never silently substitute another sport, demo fixtures or yesterday's slate.

## Apply and challenge the selection

- Review winners, spreads, totals, regulation and team totals where available. Use only genuinely recorded pending forecasts within T−120 and before scheduled start.
- Compare the app ranking with your selection. Explain differences using actual forecast IDs. Preserve probabilities and picked sides. Do not manufacture independent AI percentages or reverse losing picks.
- Apply the November 6 all-market comparison process consistently. The app strong flag, 30-settled threshold, proven-market-skill label and verified positive price edge are not mandatory ticket gates. Review their evidence and disclose limitations, but do not reject an otherwise supported combination solely for lacking these labels or unverified prices. Compare all available markets and explain the strongest distinct-game selection. Exact-percentage records inform the review with their actual sample sizes. They do not by themselves establish a reliable probability. The November 6 archive does not demonstrate an automatic reversal strategy.
- Keep historical bands, current live records and exact-percentage records separate. Include sample sizes. Yesterday's successful tickets are examples of the process, not a validated selection rule.
- Optimize for the strongest supported selection, not a ticket count or a low/medium/high template. Prefer two legs. Send one ticket when that is the best creation. Send additional variations only when each has its own evidence-based reason. Allow zero to three tickets per review, each with two or three legs. Keep different games within each ticket. Justified variations in the same batch may share the identical forecast for a core game. Explain the overlap and combined exposure. Never repeat an identical ticket. Later batches may share an identical forecast for a core game within the same Toronto day, with combined exposure disclosed; conflicting forecasts for that game are rejected. Build no ticket when support is insufficient. Risk labels are relative and optional categories, not quotas.
- Verify offered prices for the exact line when available. Never label model-generated team totals as bookmaker offers. Disclose missing prices. If executable slips are requested, skip lines whose availability cannot be confirmed.
- Assess correlation and common model errors. Multiplying probabilities for distinct games is only an independence approximation. Do not multiply same-game legs.
- Challenge unnecessary overlap, thin evidence, ambiguous Minnesota names, side/line transcription, source age and games already past start. Record why each leg survived review and why tempting alternatives were omitted.

Send clearly numbered combined parlays as readable Telegram text: every leg, matchup, sport, exact side/line, Toronto start time, permanent ticket ID. Every ticket explicitly says to combine all listed legs in ONE parlay. Send the visual combined-parlay ticket PNG after its readable text. The image has numbered tickets/legs, IDs, Toronto start times, with its own receipt and archived PNG/caption. A generic image caption must not replace the readable text. Keep detailed reasoning in the saved review. Show the main ticket first, optional distinct variations afterward, exact team/market/line, and a short estimated-risk note. Keep detailed market assessments and selection reasoning in the saved review. Do not force extra tickets.

Write a JSON plan with snapshot_path, review and tickets. Before sending the first batch, compare the strongest available NBA/NHL/NFL combinations together, including mixed-sport and shared-core variations; do not split by sport by default. Select tickets only after this joint comparison. Review missing sports and timing constraints explicitly. Soccer schedules/results do not qualify as forecasts. The review object requires combination_comparison (substantive comparison of candidate combinations, exclusions, missing sports and why this batch is preferable), skill_sha256 and helper_sha256 copied from the snapshot, snapshot_sha256 printed by the helper, markets_reviewed mapping each sport to every available market and a substantive assessment, and notes containing the skill review, app comparison, evidence limitations, price availability and correlation assessment. Each ticket has label and legs. The user removed all fixed staking and allocation rules on November 8: no main/variation dollar amounts, daily dollar cap, or budget-based stopping. Omit stake_usd and dollar recommendations unless the user explicitly asks for an amount. Read earlier archives before proposing more tickets. Each leg has sport, forecast_id and rationale. Use actual numeric forecast IDs. Use tickets=[] for a no-ticket report.

Validate the proposed tickets and read the output before sending.

    backend/.venv/bin/python ~/.codex/skills/courtside-ticket-review/scripts/tickets.py check --plan /tmp/ticket-plan.json
    backend/.venv/bin/python ~/.codex/skills/courtside-ticket-review/scripts/tickets.py send --plan /tmp/ticket-plan.json

The helper checks snapshot age, skill/code hashes, source freshness and current forecast eligibility again. It archives the reviewed plan and exact message in ignored data/reports/tickets before delivery. It sends the exact archived ticket text through the paired bot, honoring /stop and the existing receipt mechanism. app/tickets.py is the sole implementation; this skill script is a compatibility wrapper. Do not duplicate ticket validation, storage or grading in a second script. Later reviews may send additional batches as new forecasts arrive. Read earlier daily ticket archives first. Each batch is deduplicated and identical combinations are blocked across batches. Shared core forecasts are allowed with overlap across earlier tickets disclosed. Prior receipts remain immutable; earlier ticket amounts never block new justified combinations. This skill does not install a recurring schedule.

Report sent only on a sent result. An uncertain acknowledgement requires receipt inspection. Never change the chat, credentials or delivery key to bypass a duplicate, pause or uncertain result. A repeated batch is reported with its existing receipt status. Do not send a weaker later batch merely because it is technically eligible.

## Review sent results

Use app.tickets settle (or preflight) before selection each run. Read the archived plan and delivery receipt. It grades immutable forecast IDs at their original lines, including pushes and voids, records every leg and appends corrections without overwriting old grades. Pending/unavailable legs stay visible. Suggested tickets have no assumed profit. Legacy v2 batches are graded but their already-reviewed results are not re-notified automatically. Preserve losing legs. Separate distinct legs, tickets, overlapping proposals and bets the user actually placed. Calculate profit only with known stakes, prices and settlement rules. Record demonstrated skill issues and narrow proposed improvements. Do not optimize rules on yesterday's outcomes.

End each invocation by updating HANDOFF.md with actual verification, delivery status, limitations and next task.

## User-placed combinations

Import an explicitly supplied personal slip with `PYTHONPATH=backend backend/.venv/bin/python -m app.tickets personal --plan /path/to/slip.json`. Required fields: slip_id, source, stake_usd, legs (exact selections); optional return_usd. This records user-provided settlement separately from AI suggestions. Profit is calculated only from supplied actual stake/return; prices and outcomes are never inferred. Repeated identical imports are idempotent; changed content requires an explicit correction ID. See docs/TICKETS.md for operations and known hosting/source limitations.

## Complete-slate review

Continue hourly reviews through the remaining slate. Take a fresh snapshot and review every newly available pregame forecast across all supported sports and markets. Compare against earlier selections and send only new justified combinations. Use tickets=[] and helper check to archive a review when no ticket is justified. No fixed staking template or daily allocation cap applies. Never describe the first batch as the whole day's best slate. Track unopened forecast windows as pending coverage and reassess when they open.

Every review requires review.forecasts_reviewed: a mapping of "sport:forecast_id" to {"decision":"candidate" or "excluded", "reason":"actual evidence-based assessment"}. Cover EVERY eligible pregame forecast in the snapshot and any newly published eligible forecasts detected at validation. The helper refuses incomplete coverage and selected legs marked excluded. A newer publication requires re-review. review.combination_comparison is required even for tickets=[]; save useful later alternatives in that reasoning without assigning new stakes. Show coverage counts and missing sports in the review summary. Presence checks cannot establish the quality of the AI judgment; reasons must reflect actual markets, sample sizes, prices and uncertainties.

## Delivery and audit remediation

Text and visual PNG are separate deduplicated deliveries. Retryable connection/429 failures use current attempt time while preserving original content and delivery records; uncertain acknowledgements are never blindly re-sent. A retry or partial visual failure returns nonzero from the command; inspect both receipts. A past format test uses `PYTHONPATH=backend backend/.venv/bin/python -m app.tickets preview --plan /path/to/past-archive.json` (requires settled past original forecast IDs, never creates a live ticket). Settlement commits all grades before attempting Telegram notifications, and reports notification failures separately. Delivery alerts only include ticket text/image attempts in the Toronto day; unrelated game-card failures are excluded. `NBA_DATA_DIR` isolates the receipt ledger as well as forecast stores; production defaults retain the existing root ledger. It does not isolate credentials: tests must use mocked Telegram/configuration. The sender/helper still run the active working tree until a guarded-release change is separately reviewed.
