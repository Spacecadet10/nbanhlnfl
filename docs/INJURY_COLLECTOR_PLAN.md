# Plan: raw injury-report collector (pilot)

Status (updated 2026-10-01): **steps 1 to 4 done at the user's instruction (terms check, storage and adapter, job, supervised trial). Step 5 (the scheduled agent) was installed on 2026-10-01 at the user's instruction. Step 6 (pilot report after four weeks of games) is open.** Original status: plan only. Written 2026-10-01 after the availability audit
(docs/AVAILABILITY_AUDIT.md), which gave "go for a raw-collection pilot only".

## Purpose
Save the NBA's official injury reports as they are published during 2026-27, unchanged, with our own retrieval times, so that
later work has an archive whose availability is **observed** instead of assumed. The pilot answers one question: can this machine
collect the reports dependably and within a fixed request budget?

## Out of scope (each needs its own plan and review)
- Reading the PDFs: no parsing, no player or team extraction, no PDF library, no new dependency.
- Name-to-player mapping, player ratings, features, models, backtests, near-tipoff forecasts.
- Any historical download (the inventory of past seasons is gate 1 of the audit, not part of this pilot).
- Any change to the live Elo forecast, the sealed shadow experiment, their jobs or the app.

## What is collected
- **Source:** `https://ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<HH_MM><AM|PM>.pdf`, Eastern time in the name.
- **Slots:** the reports named for :00 and :30 of every hour (48 per day). Reports are full snapshots (each lists the current
  status for today's and tomorrow's games), so skipping the :15 and :45 reports loses only changes that last under 30 minutes.
- **When:** only on days inside a game window: a regular-season, play-in or postseason game in our schedule tips off within the
  next 36 hours or tipped off within the last 6 hours (the rule the odds job already uses, extended backwards). No requests in the off-season or preseason.
- **Expected volume:** at most 48 files a day, about 85 kB each: roughly 4 MB a day and 0.6 GB over a season.

## What is stored for every attempt
One append-only row per HTTP attempt, successful or not, in a new table `injury_report_attempts`:
- the slot (report date and time, Eastern) and file name requested;
- `started_at`: clock read before the request;
- `completed_at`: clock read **after the whole response body has arrived**. This is the retrieval time; nothing is ever stamped earlier;
- outcome: `stored`, `not_available` (the server answered 403 or 404; "not published yet" and "missing" cannot be told apart), or `failed` (network error, wrong content type, body not starting with `%PDF-`, or length different from `Content-Length`);
- HTTP status, `Last-Modified`, `ETag`, `Content-Length` as sent by the server;
- for `stored`: SHA-256 and size of the body and the link to the raw file; whether it is `late` (completed more than 30 minutes after the slot time);
- for `failed`: the error text.

The PDF itself goes into the existing raw cache (`raw_cache.store_fetch`, content-addressed, never overwritten) under a new source
name `nba_injury_report`, with request key `file=<file name>`. The raw cache gets one small extension: a file suffix argument, so the file
is saved as `.pdf.gz` instead of `.json.gz`; existing rows and paths are untouched.

## Timing rules
- A stored report's availability for any later use is our `completed_at` (`observed_retrieval`). The time in the file name and the
  server's `Last-Modified` are recorded as evidence only.
- A report fetched late (machine asleep, earlier failure) keeps its true, late retrieval time. It is never backdated to its slot.
- The time printed inside the PDF is not read in this pilot.

## Request budget (counts every attempt, including retries, 403s and failures)
- At most **8 attempts per run**, **4 attempts per slot ever**, **120 attempts per day**. All three are counted from `injury_report_attempts`.
- Each run takes the unfinished slots of the last 24 hours, oldest first, and stops the moment a budget is used up. Slots not reached are reported as deferred.
- A slot still `not_available` or `failed` after 4 attempts is marked given up and listed; it is not retried.
- At most 2 attempts per slot per run, with the existing 2 s / 8 s backoff. One slot failing does not stop the others.

## Overwrite check (answers an open audit question)
Once a day, 2 already-stored slots from 24 to 48 hours earlier get a HEAD request. If `ETag`, `Last-Modified` or `Content-Length`
differ from what was stored, the slot is flagged `changed_upstream` and re-downloaded once as a new raw file; the first file is kept.
These 2 requests count against the daily budget.

## Schedule and switch
- A separate launchd agent, `com.nba-predictor.injuries`, running `app.cli injuries` at minutes :02 and :32 of every hour, so a report is normally retrieved about 2 minutes after publication.
- Off by default. It collects only when `NBA_INJURY_COLLECTION=1` is set in that agent, and the agent is installed only after the code is reviewed.
- The existing odds and daily agents are not touched.

## Visibility
- Every run writes a `job_runs` row (`injuries`) with: slots due, stored, not available, failed, deferred, given up, attempts used.
- `app.cli injuries --dry-run` lists the slots it would request and makes no request.
- `app.cli coverage` gains an injury-report section per day: slots expected, stored, stored late, not available, given up, median minutes from slot to retrieval.

## Storage rules
- Migration 0010: the attempts table with update, delete and conflicting-insert triggers (as on the ledger), and `injuries` added to the allowed job names (existing job rows kept).
- `replay` ignores this source: there is nothing to parse.

## Tests required before any real request
1. Slot and file-name generation, including the two daylight-saving changes and the midnight slot.
2. Retrieval time is taken after the response arrives (a slow response crossing a half-hour boundary).
3. Budgets under a full outage, with retries, across repeated runs and when a budget runs out mid-retry.
4. Not available, then available on a later run; given up after 4 attempts.
5. A body that is not a PDF, or shorter than `Content-Length`, is not stored and is logged as failed.
6. Rerunning stores nothing twice; a late catch-up is flagged late with its true time.
7. Overwrite check: unchanged slot, changed slot (second raw file, first kept).
8. No request outside a game window; nothing collected when the switch is off.
9. Append-only protection, including `INSERT OR REPLACE` from a plain connection.
10. The migration keeps existing job rows; the Elo and shadow tests are unchanged and still pass.

## Steps (each reviewed before the next)
1. **Terms check (read-only).** Read NBA.com's terms for automated retrieval of these files and record the wording. If they forbid it, stop and ask the user, as was done for ESPN.
2. **Storage and adapter:** migration 0010, raw-cache suffix, URL builder, single-fetch function with the attempt log, tests 1, 2, 5, 9, 10.
3. **Job:** slot planning, budgets, overwrite check, CLI, coverage section, tests 3, 4, 6, 7, 8.
4. **Supervised trial:** dry run on the real store, then one real run in a scratch copy of the database; results in HANDOFF.md.
5. **Enable:** install the agent with the switch on; verify the first runs.
6. **Pilot report** after four weeks of regular-season games: share of expected slots stored on days the Mac was awake, retrieval lag, failures by cause, given-up slots, overwrite-check results, disk used. Then a separate decision on parser work.

## Pilot success measures (fixed now)
- At least 95% of expected slots stored on days with the Mac awake for the whole game window.
- Median time from slot to retrieval of 5 minutes or less.
- No failure without a recorded cause; no budget ever exceeded.

## Open questions for review
- Half-hour slots (proposed) or all four quarter-hour slots (about 1.2 GB a season and twice the requests)?
- When the 2026-27 reports start is unknown; the collector simply finds nothing until they appear (counted against the per-slot budget).
- The collector runs only while this Mac is awake and logged in. Gaps are recorded and filled late, with late timestamps.
