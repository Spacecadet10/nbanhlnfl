# NFL live operation — 2026-11-02

The user requested active NFL predictions throughout the app. The dedicated NFL adapter leaves shared NBA/NHL model,
research and CLI dispatch guarded. It reuses the existing frozen NFL learners, canonical ledger, market quotes,
percentage records, API client, frontend components and Telegram card renderer. No new app or backend.

## Running configuration

- Pinned NFL-only release: `/Users/andry/.codex/worktrees/nfl-live-release/nba-predictor-sprint1`, branch
  `codex/nfl-live-20261102`, commit `8539513` (parent `97e7efb`). Keep this attached worktree: the NFL job uses it.
- Job: `~/Library/LaunchAgents/com.nba-predictor.nfl-live.plist`; source template in `scripts/launchd/`.
  Runs `app.cli nfl tick` every 60 seconds, with the clean-release and exact-schema guard enabled.
- Shared data directory: `/Users/andry/Desktop/nba-predictor-sprint1/data`; NFL store schema33. NBA/NHL live stores stay31,
  their release and jobs unchanged. The API serves migrated copies, never migrates those live stores.
- Logs: ignored `.nfl-live.log` / `.nfl-live-error.log`. Mac must be awake, online and logged in.
- Local app: `http://127.0.0.1:5198/?sport=nfl`; API `:8841`, copy `/tmp/courtside-active-preview-20261101`.
  Publisher checks each60s; restarted with the new migration code. Standard restart remains `scripts/dev.sh`.

## Forecast and evidence policy

Actual generation within T−120 to kickoff; eligible regular/playoff games only. Results and quotes used by the model
must be available by the fixed T−120 cutoff, including when the job runs late. Book quotes must additionally be within
2h of that cutoff. Later quotes cannot rewrite the model's original line. No historical forecast backfill. A complete
five-market batch is recorded once per game/start; an interrupted batch resumes without duplicating its earlier rows.
Stale/failed refreshes and unresolved recent results block forecasts. Changed starts void old records, allowing a new
forecast at the new start. Source/model versions and real timestamps remain append-only. Winner ties void; line pushes
are excluded from W/L. The exact displayed tenth-percent record excludes the target game and separates markets,
directions, model versions and line provenance. The card may separately show its own settled result.

The one-shot 2022–25 test is complete, not to be rerun. Evidence: `docs/evidence/nfl-winner-test-2026-11-02/`;
original access log `/tmp/courtside-nfl-eval/research/nfl_eval/nfl_test_access.jsonl`, copied to the live data directory.
Seal `d0e51f91…`, open dataset matched. Frozen winner75%+:126–31 /157,80.3%, Wilson73.3–85.7%; winner confirmation and
frozen threshold held. No threshold reselected. Reserved line-price evaluation not run (no held-out prices collected).
These test games never enter live accuracy or exact-percentage records. Line-market validation showed no proven edge;
spread,total,home/away totals stay informational and cannot become Recommended pick. Missing real lines are explicitly
model lines. No NFL injury/weather inputs, daily model search or retraining are enabled.

Activation artifact `data/research/nfl_live/activation.json` binds the completed test, seal and serving definition.
Changes to those serving/model files disable forecasts until explicitly reviewed and activated again. The clean-release
guard refuses dirty code or a mismatched schema before any scheduled request/migration. Telegram's existing paired bot
reads the NFL store query-only, uses the same exact records and frozen winner rule, and applies its existing pregame,
freshness, pause and deduplication rules. No live NFL message has yet been sent: no game is in-window at verification.
Tonight CHI@SEA starts20:15 EST Nov2; first forecast window18:15 EST (23:15Z). Initial live ledger is empty by design.

## Local controls and rollback

Read status: `launchctl print gui/$(id -u)/com.nba-predictor.nfl-live`; inspect the two ignored logs.
Stop only NFL: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.nfl-live.plist`.
Resume: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.nfl-live.plist`.
To disable the UI capability as well, rename `research/nfl_live/activation.json` to `activation.disabled.json` in the live
and active preview directories (recoverable), then restart the local API. Never remove ledger rows or reset a database.
NBA/NHL jobs and schemas require no rollback for this activation.

Before another release, check `app.release.check('nfl')` from the pinned checkout with the plist environment; expected
schema33/33 and problems[]. Use the existing `nfl activate --sealed FILE --report FILE --access-log FILE` after review,
not `nfl evaluate` or `nfl test-run`. Do not rerun the held-out test to activate a release.
