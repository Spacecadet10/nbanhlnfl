# NBA / NHL / NFL freshness milestone

**Status (2026-11-01):** core refresh implementation and P1–P4 fixes passed the focused independent re-review recorded in HANDOFF.md. Preview publisher is running. NBA/NHL launchd templates now call `tick` every 60 seconds (configuration prepared, NOT INSTALLED; freshness/release-guard tests 35 passed). The pinned live release lacks `tick`; a scoped reviewed release must precede installation. Live collection therefore still uses the old jobs. Carolina's delayed final was recovered with the guarded released importer; this one-off recovery does not complete automatic-ingestion rollout.

Proposed 2026-11-01 from the current code and read-only operational checks. Opus remains the implementation owner; this is a design/acceptance plan, not a deployed change or a model-policy change. Read AGENTS.md and HANDOFF.md first.

## Problem and scope
- `jobs._forecast_due` ties results refresh to forecast eligibility/recent starts. Launcher intervals are 15 minutes for NBA and 30 for NHL; `jobs.refresh` also enforces a 30-minute quota.
- NBA refresh currently fetches calendar months, NHL a season list. Repeated full-history/season work is inappropriate for minute-level status updates.
- Preview :5194 uses a frozen development copy; frontend `useApi` has no automatic refresh. Upstream ingestion, published preview data and visible UI can therefore have different ages.
- NFL now has data-only API/UI changes in the working tree, but remains outside SPORT_IDS. Preserve that separation. A shared data-refresh policy does not authorize NFL forecasting, generic NBA/NHL forecast endpoints, training or live jobs through an unsafe sport branch.

## Proposed operating policy
Intervals below are initial targets to measure against source response times, documented terms/limits and errors; they are not promises of upstream live coverage.

| Activity of a sport | Schedule/status/final-result fetch target |
| --- | --- |
| No games near their start or active | Every 60 minutes, plus daily wider schedule reconciliation |
| Any game starts within two hours | Every 2 minutes |
| Any game active, or past scheduled start awaiting confirmation | Every 60–120 seconds |
| Final result first received | Confirm on a subsequent fetch; reconcile recent results later for corrections |

- Run a lightweight coordinator tick every minute; due-time decisions and provider request budgets determine actual work. Reuse existing jobs, adapters, storage and store locks. Separate result/status refresh eligibility from forecast eligibility and odds collection.
- Replace the fixed refresh quota with the explicit state-aware policy; shortening launcher intervals alone is insufficient. Keep per-provider caps, bounded retries/timeouts, jitter, backoff on errors/429, no overlapping fetches and recovery after downtime. Refresh failures never count as fresh data.
- Validate smaller date-scoped endpoints before depending on them; reuse existing parsing/ingestion. Keep broad month/season backfills and historical reconciliation on their slower cadence. NFL must dispatch explicitly to its NFL adapter, never a non-NHL-equals-NBA branch.
- All three sports receive data updates. Only approved NBA/NHL models receive forecast work. Wake at forecast-window boundaries and retry blocked due forecasts promptly when fresh prerequisites arrive. Preserve current forecast selection, versioning, eligibility and freshness rules; do not retrain on page refresh or create new forecast versions just because a browser polls.
- Due data refresh and pregame work take priority over historical research/recovery. Preserve store isolation, short write transactions, database uniqueness and idempotent retries. Keep original retrieval/result availability times and append-only forecasts; never backdate a missed prediction.
- Never mark a game in progress merely because the scheduled time passed: show 'start time passed; awaiting source update' when appropriate. In-progress score support, if wanted, needs separate timestamped observations; do not repurpose final-result fields/availability to display live scores.

## Publish fresh data and update the UI
- Make an explicit distinction between a live/read-only dashboard and a frozen review snapshot. A frozen review snapshot must say it will not update automatically.
- For the current development preview, safely publish refreshed read-only data rather than leaving one startup copy indefinitely. Use consistent SQLite backups, schema/store-identity validation and coherent required artifacts; do not copy a live database file without its WAL state, overwrite open files, migrate live stores or weaken dev.sh path protections. Publish a complete revision atomically; keep the previous revision and show staleness if publication fails.
- Reuse the single API client/hook. One inexpensive freshness check per selected sport checks a revision approximately every 30 seconds while visible; refetch only changed visible game/forecast data. Do not poll every research/training endpoint or make provider requests per user/page.
- Keep existing data visible during background refresh, with a quiet updating indicator. Pause polling while hidden/offline and refresh on visibility return/reconnection; abort obsolete requests, avoid overlapping requests and ensure an old response cannot overwrite a newer selection. No new data-fetching dependency is required by this plan.
- Display separate source-fetch time, published snapshot time and forecast-generation time, plus stale/error state and last failed attempt. A recently copied snapshot is not proof that its source data is recent. Distinguish 'not due yet', 'source stale', 'model unavailable', 'job failed', and 'NFL forecasts not supported'.

## Acceptance and rollout
- Meaningful tests: simulate window opening without a page reload; recent/active games without any eligible forecasts; delayed, postponed, canceled and rescheduled starts; DST/date boundaries; provider failure/backoff; restart/catch-up and overlapping ticks; repeated ingestion/forecast retry; final corrections; snapshot publication failure; hidden-tab/resume; stale-response races.
- Verify all three sports render new stored status/final results without manual reload. Verify new eligible NBA/NHL forecasts appear with unchanged timestamp/selection rules; NFL forecast/job guards still reject NFL and unavailable metrics remain unavailable. Measure actual end-to-end update lag and request counts; report source delays separately from app delays.
- Check NBA/NHL regressions and database invariants. Run the required structure/readiness checks, then desktop and phone browser smoke with loading/error/empty/stale states. Document exact evidence in SMOKE_TEST.md and HANDOFF.md.
- Implement/test in owned scratch/development stores first. No live scheduler edits, production migrations, training, release, purchases or push are authorized by this proposal. Preserve the current uncommitted redesign and NFL work; independent review precedes release.

Reference for visibility-aware polling: https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API
