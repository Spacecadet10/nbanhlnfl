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

## Integration fixes (Nov8, integration writer)

- An image file left by a crash before its `ticket_images` record no longer blocks the image forever: new ticket PNGs are named by content (`<day>-<key16>-<png sha256>.png`). Archives written by a concurrent identical run are accepted when byte-identical.
- If the ticket image cannot be rendered after the text was sent, a `failed` image receipt is recorded, so the Toronto-day ticket alert reports it. It is terminal and needs a person to look at it.
- A reservation committed without any delivery claim (a crash in between) raises an `unclaimed` ticket alert.
- Settlement notifications:
  - No notification problem aborts settlement, including a malformed Telegram reply. Grades are already committed.
  - A notice that ends `failed` or `retry` is listed in `notification_errors`.
  - A Telegram reply that is not a JSON object counts as unconfirmed (`uncertain`).
- `preflight --notify` still prints the committed settlement report when alerts cannot be sent (status `failed`, `alerts_error`).

**Withdrawn from the integrated build: early publication in `jobs.predict_now`, and the explanation-only rule in
`coverage._content_key`.**
- **Why.** `coverage.py` is one of the seven files whose bytes the approved NFL activation binds
  (`nfl_live.definition`, artifact `b6074877a7e7ccfe`, activated 2026-11-02). Editing it turned NFL off: no NFL
  forecasts, cards or ticket legs. This happened in the live sender/helper from 19:04Z on Nov 8 until sprint1's
  `coverage.py` was restored.
- **The conflict.** With the sealed `coverage.py`, early publication writes a second canonical row whenever only the
  fallback explanation changes between the early and final passes (`test_coverage.py:278`). Append-only rows cannot be
  removed afterwards.
- **What changed.** `coverage.py` and `jobs.py` are byte-identical to HEAD `79ff6ec`, which is the live behaviour.
  `tests/test_nfl_activation_compat.py` checks a byte copy of the production artifact and fails if any sealed file
  changes.
- **To bring it back** needs one of:
  - a new reviewed NFL activation for the new definition, which is a release decision;
  - a design that does not change `coverage.py` and creates no explanation-only versions.
- Still open (LOW): two identical plans sent concurrently cannot double-send or over-allocate, but the loser reports "Duplicate ticket combination" and exits 1.

## Guarded release for the sender and helper

**The problem.** The Telegram sender (`app.cli telegram run --once`, launchd every 60 s) and the ticket helper (`python -m app.tickets`, hourly Codex review) used to import the development working tree. Any uncommitted edit went live within a minute.

**What the guard does.** Under `NBA_RELEASE_GUARD=1` both refuse (exit 1, `"reason": "release guard"`) before any snapshot, ledger access or Telegram call, unless all of these hold (`app/release.py delivery_check`):
- the code is imported from `NBA_RELEASE_ROOT`;
- that checkout has no uncommitted changes;
- `NBA_DATA_DIR` is set;
- `NBA_TELEGRAM_LEDGER` and `NBA_TELEGRAM_CREDENTIALS` name existing regular files (not symlinks).

**Ledger safety.**
- Under the guard, `telegram.ledger()` never creates a missing ledger. An empty ledger would forget every receipt, duplicate guard, `/stop` state and $5 reservation.
- Without the guard, a data folder that sits inside another main checkout uses that checkout's ledger.

**Where it runs.** The sender and helper run from a dedicated delivery checkout, `~/nba-predictor-delivery`, and the forecast jobs keep their own `~/nba-predictor-live`. Moving `~/nba-predictor-live` to this tree would also promote the forecast jobs and require migrating the live stores from 31/33 to 37, which is a separate release decision. Read paths were checked: on raw, unmigrated copies of today's stores (NBA/NHL 31, NFL 33), this tree's `tickets snapshot` is identical to the current live code's.

**Configuration.**
- `scripts/launchd/com.nba-predictor.telegram.plist`: the sender template.
- `scripts/release_tickets.sh`: the helper wrapper.
- Both keep production state where it is now: `NBA_DATA_DIR`, `.telegram-delivery.sqlite` and `.env.telegram` in the main checkout.

**Activation.** Not done: this needs review and the user's go. Run steps 2–4 between hourly reviews, not at :05–:20, and with no ticket send in flight.
1. Commit the reviewed integrated tree. Note its commit as `<C>`.
2. Create the delivery checkout and its environment:
   - `git worktree add ~/nba-predictor-delivery <C>`
   - `git -C ~/nba-predictor-delivery checkout -B delivery-release`
   - `uv venv ~/nba-predictor-delivery/backend/.venv --python 3.12`
   - `uv pip sync --python ~/nba-predictor-delivery/backend/.venv/bin/python backend/requirements-release.txt`
3. Read-only checks from `~/nba-predictor-delivery/backend`:
   - `sh ../scripts/release_tickets.sh snapshot --output /tmp/delivery-check.json` must print a snapshot path. This makes no network call.
   - With the plist's environment, `.venv/bin/python -m app.cli telegram status` must print the bot and receipt counts. This calls getMe/getWebhookInfo only.
4. Back up `~/Library/LaunchAgents/com.nba-predictor.telegram.plist` (for example to `data/backups/launchd-before-delivery-<date>/`). Then switch the sender:
   - `launchctl bootout gui/$(id -u)/com.nba-predictor.telegram`
   - `cp scripts/launchd/com.nba-predictor.telegram.plist ~/Library/LaunchAgents/`
   - `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.telegram.plist`
5. Switch the helper:
   - Point the installed skill wrapper (`~/.codex/skills/courtside-ticket-review/scripts/tickets.py`) at `/Users/andry/nba-predictor-delivery`.
   - Change the ACTIVE hourly automation prompt's helper command to `sh /Users/andry/nba-predictor-delivery/scripts/release_tickets.sh …`.
6. Confirm both are running from the release:
   - The next sender log line in `.telegram-delivery.log` is a normal result.
   - The next hourly preflight prints settlement JSON, not `release guard`.
   - The ledger's `deliveries` count is unchanged except for genuine new sends.

**Moving to a newer reviewed commit.** `git -C ~/nba-predictor-delivery merge --ff-only <new commit>`. Re-sync the venv only if `requirements-release.txt` changed. Then repeat step 3.

**Rollback.**
- To a previous delivery commit: `git -C ~/nba-predictor-delivery checkout -B delivery-release <previous commit>`, then step 3.
- Back to the pre-guard setup:
  1. `launchctl bootout gui/$(id -u)/com.nba-predictor.telegram`
  2. Restore the backed-up plist.
  3. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.telegram.plist`
  4. Restore the skill wrapper and automation command to the sprint1 paths.

  The ledger and credentials never move, so rollback loses no receipts.

**Limits.**
- A refused run is visible only in the sender log and the helper's JSON output; there is no Telegram alert, because the guard runs before Telegram. The hourly review must report a `release guard` refusal.
- The Mac must be awake.
- Development copies still read the production credentials file unless `NBA_TELEGRAM_CREDENTIALS` or the environment overrides it, so tests must mock Telegram.

## Live-state audit fixes (Nov 8; prepared on `audit-fixes-20261108`, not deployed)

Evidence and causes: `docs/evidence/audit-fixes-2026-11-08/README.md`.

- **Locks are recorded by the helper.** A forecast whose game is already in a reserved or sent ticket with a different
  forecast cannot be a leg. `check` records that itself for every such forecast the reviewer left out
  (`forecasts_reviewed[key] = {decision: excluded, reason: "Locked: …", recorded_by: helper}`) and archives every lock
  under `locks` (ticket IDs, the ticket's forecast and pick). An entry the reviewer wrote is kept as written. A locked
  forecast selected as a leg is refused by `check` with a "review again" reason (before, only `send` refused it, without
  a retry). Forecasts that are not locked still need the reviewer's own decision and reason. Reason text is never
  compared or deduplicated: one explanation may be attached to several forecasts.
- **Why a game started unreviewed.** Every `preflight` appends one row to `ticket_trigger_runs` (time, day, whether a
  review was due, the trigger's reason, how many observations), also when it saw nothing to record; a run whose review
  state could not be evaluated, or whose snapshot failed (best effort, with the clock's time), is recorded with `due`
  NULL. A run whose own recording fails (ledger busy) leaves no row and reports `observations_error`; a ledger holding
  the older-shaped observations table of an unreleased build (no `run_id`) is refused with a message, every run. Under that run it appends to `ticket_trigger_observations`
  what it saw for each pending game: what kept it out of review (a refused sport when it excludes forecasts, an
  unusable source, a moved line, a single-game window; also shown as `review.not_reviewed_now`), or that a review of it
  was due (`review_due`). Both are new append-only tables in the Telegram ledger, created on first use; a run and its
  observations are written in one transaction. Each `missed_before_start` entry carries, from its review window (the
  two hours before its start, `review_window_from`), `recorded_blockers`, `review_due_seen`, `failed_reviews`,
  `last_observation` (what, when, and how many minutes before the start) and `trigger_runs` (checks in the window, the
  last check before the start, the first check at or after it). Soccer matches are covered too (their forecasts are
  made at the cutoff, days before, so they have their own "was reviewable" rule). `status` records nothing.
  - Two different facts are reported. **`blocker_observed`**: a blocker was recorded at some check in the window.
    **`explained`**: the entire window is accounted for. That needs either a review that failed after the trigger last
    said one was due, or a blocker recorded at the last check before the start that is shown to have lasted to the
    start: the FIRST check at or after the start recorded the same blocker for that sport, or, for a single-game
    window, no other game became eligible between that check and the start. A check timed exactly at the start belongs
    to the checks after it.
  - Because clean runs are recorded, "the last check before" and "the first check after" are the actual runs. A
    blocker seen at the last check and gone at the first check after the start leaves the minutes in between
    unaccounted for: `explained` is false and `explanation` says how many and names that check (HOU @ LAC on Nov 8: 57
    minutes, first check after the start 21:06:30Z). A blocker that comes back later the same day does not change
    that. A blocker that had cleared at the last check before the start (that check recorded nothing for the game, or a
    due review that never ran) also leaves `explained` false, as does a check that could not evaluate the review state.
    While no check at or after the start is recorded yet, `explained` is false and says so; it can become true at the
    next run. When no game of the sport is pending any more, a check records nothing for that sport, so the explanation
    says "recorded nothing for {sport}" rather than that the blocker was gone. When nothing at all was recorded for the
    game in its window and its current forecast (after the start, the latest version made before it, often a re-issue
    minutes before) is younger than the last check, the explanation says that check could not have seen it. No time
    threshold is used. Known limit: the single-game rule's "other windows" also use each game's current forecast time.
- **What is stored and what is announced are decided separately.**
  - *Stored (the archive):* `ticket_grades` gets a row whenever the result differs from the ticket's latest stored
    result, including every leg update and a return to an earlier result (that row carries `supersedes`, the hash it
    replaces). Hashes of first and unchanged results are as before. A result with a leg that could not be graded is
    stored as before and skipped when comparing.
  - *Announced (owner's rule, Nov 8):* the user is told (1) the first settled result of a ticket: a loss as soon as one
    leg loses, a win or void once every leg is in; (2) a genuine correction: a leg that was announced as settled now has
    another settled grade, sent with "Corrected result: it replaces the result sent before.", also when it reopens the
    ticket (`PENDING`); (3) one final recap when every leg is settled and the last notice still showed an open leg.
    Nothing else is sent: an unchanged losing ticket is not announced again when another leg settles; a leg that reads
    as open for a while, or a corrected score with the same grades, changes nothing the user was told.
  - The comparison is with the last notice that reached the user for that ticket (sent or unconfirmed). A notice
    Telegram rejected or deferred does not count; a deferred notice with the owed text is retried under its own key.
  - A result with a leg that could not be graded is never announced (the `settlement_unavailable` error and alert remain).
- **A refused review is filed under the day of the snapshot it reviewed**, not the wall-clock day (they differ around
  Toronto midnight). The trigger looks refusals up by the snapshot's day, so a "review again" refusal just after midnight
  is found again. The release files it under the wall-clock day; one of its own tests fails on any date but Nov 8.
- **A rejected result notice stays visible.** A result notice Telegram rejected stays `failed` and its receipt is never
  changed. Its text is not attempted again either: while that receipt is newer than the last notice that reached the
  user, an owed notice with the identical text keeps the rejected receipt's key (as a deferred one keeps its `retry`
  key), so a reading whose payload changed without changing the text (a score, a result time) does not send it under a
  new key, and a result that returns to the rejected one is not attempted again. A different text is a new notice.
  New: while that notice is still the one the user is owed, every
  settlement lists it under `undelivered_results` (ticket, grade, when it was rejected, its delivery key, whether it
  was a correction, and the last result notice on record), with notices on or off; brief preflight shows the list when
  it is not empty. `preflight --notify` sends one alert per rejected notice ("RESULT NOTICE NOT DELIVERED", naming the
  ticket, the rejected result and the last result on record; its key has no date, so it is sent once, and not again if
  the notice drops off the list and is owed again later; the alert says so). The entry
  disappears when a notice that reaches the user says the current result, or the result returns to what they have.
  Listing the undelivered notices is guarded like the notification step: a read problem there is reported as
  `undelivered_error` and never ends the run.
  `notification_errors` reports the rejection only in the run in which it happens, as before, so the 15-minute trigger
  is not kept busy by a notice that stays rejected. An unconfirmed (`uncertain`) notice still counts as announced and
  is never sent again. In a run where a leg cannot be read, nothing is compared, so the list is empty for that run.
- **Known delivery limit, unchanged from the release:** a deferred notice whose content was overtaken by a newer result
  stays `retry` and is never sent; it is visible in `deliveries` only.
- **Unchanged:** selection method, forecast-version lock, 2–6 legs and at most 6 tickets, legacy reservation read,
  existing rows.

## Owner changes (Nov 9 evening)

Requested by the owner in his own words ("I just want to see the picture", "it just clutters the Telegram", seven-leg parlays). Second version, after the Codex review of the first (docs/evidence/telegram-picture-only-2026-11-09/).

- **Pictures only.** `send` delivers a batch as pictures with short captions; no long text message. Every ticket is drawn in full on exactly one picture (`ticket_pages`: at most three tickets and about nine legs per picture, so a seven-leg ticket stays readable). Each caption (`ticket_captions`) lists its picture's tickets: legs, stored prices, payout. A caption never cuts a selection: it drops labels, then prices, and otherwise `send` refuses before any reservation. A menu whose stored text exceeds one Telegram message is still refused whole by `check`, as before.
- **Receipts.** The first picture is sent under the batch key, and the `ticket-image:` receipt is recorded as that same Telegram message, so settlement, result notices, delivery alerts and `ticket_deliveries` read what they read before. Further pictures have receipts `ticket-image:<hash>:p<n>` (kind `ticket_image_page`), are sent in order and are never resent when uncertain. Pictures are archived once in `ticket_images` under `<batch key>#p<n>` and reused unchanged; an archived picture that fails its hash raises and nothing is sent. The exact full text stays in `deliveries.text`, `ticket_batches` and the archive.
- **Renderer unavailable.** The ticket still arrives, as the short caption lines in one message (never the long text); the image receipt is recorded `failed`, the result is `partial_failed` and the delivery alert fires as for any failed image. A batch whose text the earlier release already sent keeps that release's text-then-image path.
- **Quiet health states.** `source_unavailable_or_stale` and `missing_due_forecasts` (`QUIET_HEALTH`) are no longer sent to the chat; they remain in the preflight report, the snapshot and the trigger observations. Every other alert is unchanged.
- **Up to seven legs** (`MAX_LEGS = 7`).
- **One game card per game** (`telegram.deliver_card`). The delivery key is unchanged, so receipts of the earlier release are matched at cutover. Before a card with a new key is sent, the ledger is searched (two days) for a card with the same sport, matchup, start and recommended pick, ignoring the line number, that reached or may have reached the chat (sent, uncertain, sending); if one exists the new card is not sent (`already_sent_for_game`). A different recommended side or start sends again; a rejected (`failed`) card does not block.
- **Game cards show the sportsbook next to the model** (`telegram.format_batch`, owner request Nov 9 after CLE @ CHA: 'CLE win' was the recommended pick at 55% while the price implied 74%). Each priced market row reads `Sportsbook X% (model ±Y)`; the headline reads `Model A% · Sportsbook B%` and adds `no value at this price` when the sportsbook's margin-free probability is above the model's. The recommendation itself, the delivery key and the one-card-per-game guard are unchanged; markets without a stored price are unchanged. This is information, not a value claim: over the first month the model's disagreements with DraftKings two hours before the start lost on paper (docs/VALUE_AUDIT.md).

Not changed: the review instructions (`SKILL.md`), the automation schedule, selection rules, settlement, stores. Tested in a Linux copy of this checkout (Python 3.10, own venv): `tests/test_tickets.py tests/test_telegram.py tests/test_delivery_release.py tests/test_nfl_live.py tests/test_soccer_live.py`, 237 passed. Not run by the author: the full backend suite, the release venv, a real Telegram send.
