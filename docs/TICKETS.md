# Reviewed parlays

The application owns one workflow in `backend/app/tickets.py`. The installed `courtside-ticket-review/scripts/tickets.py` is a compatibility entry point. The AI agent performs judgment and comparison using the skill; the helper checks invariants and transmits the selected combination. There is no second predictive model or claim of an optimal/profitable parlay.

From the repository root (existing backend environment):

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.tickets preflight --notify
PYTHONPATH=backend backend/.venv/bin/python -m app.tickets snapshot --output /tmp/new-ticket-snapshot.json
PYTHONPATH=backend backend/.venv/bin/python -m app.tickets check --plan /tmp/reviewed-plan.json
PYTHONPATH=backend backend/.venv/bin/python -m app.tickets send --plan /tmp/reviewed-plan.json
PYTHONPATH=backend backend/.venv/bin/python -m app.tickets settle
```

Use a new snapshot filename per review; never overwrite prior evidence. Preflight settles prior tickets BEFORE any selection early exit, then checks source freshness and games overdue for an available forecast (ten minutes into the two-hour window). `--notify` enables deduplicated operational alerts and result notifications to the existing paired chat. Respect `/stop`. A failed/unconfirmed send keeps its reservation and never gets a bypass key. The existing hourly :10 automation invokes this workflow; it still needs the desktop host running and online. No new server has been purchased or deployed.

The review plan retains `snapshot_path`, `review`, `tickets`. Review includes `skill_sha256`, `helper_sha256` (hash of app/tickets.py), `snapshot_sha256`, `markets_reviewed`, `notes` and **`combination_comparison`** explaining the joint NBA/NHL/NFL candidate comparison, exclusions, timing and missing coverage. Each ticket has a label and two or three legs; no staking amount or daily allocation is required. Optional explicitly requested dollar amounts remain supported for compatibility. Legs have with sport, forecast_id and rationale. Shared identical core forecasts may recur across batches; the message shows joint dollar exposure and ticket IDs. Conflicting forecasts for a shared game and identical complete combinations are rejected. Original reservations and uncertain receipts remain immutable; there is no daily dollar cap or fixed staking template. No sport quotas; mixed-sport tickets are supported, not mandatory.

Soccer is explicitly reported as unavailable: its current adapter stores schedules/results but has no prediction model. Adding soccer predictions requires its separate model/data validation milestone. Source freshness does not establish forecast coverage or bookmaker availability.

## What Telegram receives

One readable message per selected batch, with numbered tickets, permanent IDs, exact combined legs, matchups, sports, Toronto dates/start times, dollar stakes and model product estimates labelled as an independence approximation. It says to combine all legs of a ticket in ONE parlay. Detailed analysis stays in the saved JSON. The visual PNG follows the readable ticket text; its caption is at most1,024 characters. Both deliveries have stable separate keys and receipts. Existing individual game-card notifications are a separate sender.

`ticket_batches` stores the exact outgoing text and original resolved forecasts in the existing private Telegram ledger (`.telegram-delivery.sqlite`). `ticket_items` enforces combination uniqueness in SQLite. The same exact text is also written to ignored `data/reports/tickets/`. Delivery receipts retain the Telegram message ID/status. API acceptance does not prove user read/visibility. No bot credentials are committed. Legacy v2 reservations/receipts and original archives are read to preserve earlier budget allocation; missing legacy evidence refuses new allocation.

## Settlement

The existing `coverage.grade` grades the **original immutable forecast ID** and line against recorded results, with result-availability checks. It does not replace an earlier forecast with a later canonical selection. Each ticket's complete leg list is retained, including pending/unavailable/loss/push/void. Any loss loses the ticket; all pushes/voids means void; otherwise unresolved legs keep it pending and a fully resolved remaining win wins. This is model settlement: exact sportsbook payout rules and promotions can differ.

`ticket_grades` appends only changed evidence snapshots; original batches and old grades cannot be updated/deleted. Result corrections append a new entry. Confirmed v3 deliveries can get one deduplicated result notification per evidence revision. Legacy tickets are graded without repeating their earlier result alerts. Suggested stakes do not establish an actual placed bet or profit.

## User-placed slips

Import explicitly supplied slips separately:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.tickets personal --plan /tmp/actual-slip.json
```

```json
{"slip_id":"user-slip-unique-id","source":"user-provided screenshot","stake_usd":5,"return_usd":18.07,"legs":[{"sport":"nba","pick_label":"ATL win"},{"sport":"nba","pick_label":"OKC win"}]}
```

These are user-provided facts, not independently verified sportsbook records. `return_usd` is optional; profit remains unknown without it. Exact repeated imports are idempotent; changed content requires an explicit correction ID. Stored in `personal_tickets`, separate from AI suggestions/budget. No automatic sportsbook login, screenshot OCR pipeline or app UI for personal slips is implemented.

## Publication and remaining hosting limits

`jobs.predict_now` records primary coverage after winner/market generation and before slow research, then records final coverage again afterward. Existing append-only/timestamp rules remain in force; later adaptive versions have their actual availability time. Test in the development checkout before promoting to the immutable live release. This change does not independently refresh sources while a long process holds the existing scheduler lock; health alerts expose stale sources, but eliminating that lock coupling requires a separately reviewed worker split.

The app's live release checkout and desktop automation are separate deployment concerns. This task does not deploy the changed prediction job to the immutable release, configure an always-on host, invent soccer forecasts, or claim a successful scheduled delivery of the new message format. Those require real operational verification after review. Current readiness/verification results are in SMOKE_TEST.md and HANDOFF.md.

## Coverage guard (Nov8 follow-up)

Hourly analysis continues through later forecast windows. Every plan, including `tickets=[]`, must include `review.forecasts_reviewed`, keyed `sport:forecast_id`, with `decision` (`candidate`/`excluded`) and a substantive `reason` for every eligible pending forecast in both saved and current snapshots. Newly published forecasts cause validation to refuse until reviewed. Selected legs must be candidates. `combination_comparison` is required for no-stake plans too. A successful check archives the full review under `data/reports/tickets/reviews/` and reports required/accounted counts, without sending messages. Check cannot verify the quality of reasoning, so the agent still owns evidence-based judgment. Future unopened windows remain pending coverage; absent forecasts cannot be invented.

## Audit remediation batch (Nov8)

Retry attempts preserve the original payload and reservations but use the current validated attempt time, not the archive creation time. Retry/paused/uncertain/partial-image statuses exit nonzero. A successful text message is never resent merely because its image failed; the image has a separate stable delivery key, original PNG hash and caption stored in ticket_images. An uncertain image is never automatically resent.

Settlement persists all outcome evidence before calling Telegram; notification failures are returned in notification_errors and cannot suppress grading. Ticket failure alerts include retry/sending/uncertain/failed text or image receipts in the Toronto calendar day for the paired destination. Each affected delivery/status has its own dedup key, so an unrelated earlier failure cannot mask a later ticket failure. Individual game-card failures are excluded from these ticket alerts.

The default production ledger remains .telegram-delivery.sqlite at repository root for compatibility. With a different NBA_DATA_DIR the ledger resides inside that directory; explicit ledger(path) still works. Data isolation does not isolate credentials; tests require mocked Telegram/config.

Early and final publication do not create a new forecast for an explanation-only change in fallback_reason; original rows remain untouched. Actual probability, model, source, line and missing-decision changes retain versioning. The cycle reports created rows across both publication passes.

Historical visual tests use `PYTHONPATH=backend backend/.venv/bin/python -m app.tickets preview --plan /path/to/past-archive.json`. The JSON contains date and resolved original tickets. The command checks past original forecasts, labels the PNG TEST/PAST GAMES, uses a test ID, example stakes, a separate receipt and no betting-budget reservation.

Still open: guarded-release operation of the sender/helper, cross-window stake policy, same-game/new-market policy, personal-slip UI/correction workflow and token rotation. A reported soccer model in another checkout is not automatically an approved forecast provider here.
