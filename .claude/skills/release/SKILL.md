---
name: release
description: Use when moving the live NBA/NHL checkout (~/nba-predictor-live), the delivery checkout (~/nba-predictor-delivery), the backup runner (~/nba-predictor-backup-runner) or any launchd plist to a new commit, or when rolling one back. Not for ordinary feature work in a worktree.
---
# Release a commit to a running checkout

Sources: docs/RELEASE_CHECKOUTS.md (which checkout runs what, plist order), HANDOFF.md entries "NHL player-box repair ... RELEASED Nov 25" and
"Green/red/neutral pick tracking ... RELEASED" (steps and rollback as actually done), backend/app/release.py (`pin`), the 2026-11-26 audit
BUILD_RULES.md (zsh push note, delivery-before-live ordering), docs/ADAPTIVE_SYSTEM.md section 14 (exit codes).

## Which checkout runs what
- `~/nba-predictor-live` (branch `live-release`): NBA and NHL collection, forecasts, daily, injuries, markets, `training-inputs`, `adaptive-train`; API 8100.
- `~/nba-predictor-delivery` (branch `delivery-release`): NCAAB, NFL, Soccer, Telegram, scorecard, weekly report, learning guard, autoresearch; API 8101, web 4173.
- `~/nba-predictor-backup-runner` (a `delivery-release` commit): nightly backup and forecast journal.
- NBA/NHL fits in force carry the LIVE code's definitions (adaptive.definition, research_live.definition). Running those jobs from delivery
  changes them: that is its own reviewed release, not a configuration fix.
- Data folder `~/Desktop/nbanhlnfl/data` is shared by all; a release never migrates or edits it unless the task says so.

## Steps (in this order)
1. Release commit is reviewed, on a branch, tests run on that exact commit (report commands and counts; known unrelated failures: 8 in test_deep_study).
2. Do not move live while `adaptive-train` runs (it can run up to 300 min; 09:30 and 15:30 ET). Never reload or bootout a job while it runs (bootout kills it).
   Check `launchctl list | grep nba-predictor` and the process list.
3. Plists first, if jobs or their exit-code handling change (`adaptive-train`, `nhl-daily`, `ncaab-daily`: 0/1/3). Copy the template from scripts/launchd/
   to ~/Library/LaunchAgents, keep the old ones (e.g. `plists-before/`), bootout + bootstrap. The new shell text accepts the old codes, so plists go BEFORE code.
4. Pre-release backup, from `~/nba-predictor-backup-runner`: `NBA_DATA_DIR=~/Desktop/nbanhlnfl/data PYTHONPATH=~/nba-predictor-backup-runner/backend
   ~/nba-predictor-delivery/backend/.venv/bin/python scripts/backup_nightly.py --root ~/nba-predictor-backups` (exit 2 = no mirror drive, expected);
   check `journal_verify_ok` and every `integrity: ok`; note the folder (~/nba-predictor-backups/stores/<UTC stamp>).
5. Push the branch first with explicit 40-hex hashes. In zsh `$X:refs` is parsed as a modifier: write `"${X}:refs/heads/<branch>"`, never `$X:refs/...`.
6. In the checkout: `git checkout --detach <commit>` (never commit inside a release checkout). Frontend changed: `cd frontend && npm run build`.
7. `python -m app.release pin` in that checkout, ONLY for a commit a pushed branch holds (it refuses a commit no branch holds).
   THE STEP PEOPLE FORGET: the pin. A forgotten pin shows later as a one-line "pin differs from checkout" in logs (it is never a refusal, owner Nov 11).
8. Restart what changed: `launchctl kickstart -k gui/$(id -u)/com.nba-predictor.api` (live) or `.api-delivery` (delivery), plus `.web` after a frontend build.
9. `release-check`: `python -m app.cli --sport nba release-check` and `--sport nhl` in live; `nfl release-check` in delivery. Expect problems `[]`,
   pin_note `null`, research/adaptive definitions unchanged unless the change is meant to move them (record before/after hashes).
10. A delivery commit needs its own entry in `data/reports/weekly_report_review.json` (copy the format of the LAST entry; make a `.bak` first),
    or the recorder review is missing (HANDOFF entries mark this NOT done when skipped).
11. Delivery comparison/evaluation code ships with or before live-pool changes that feed it (live pool freeze must not outrun delivery).
12. Secrets/data scan before any push (the repo is PUBLIC): `git diff <base>..<commit>` and `git ls-files` for .env*, tokens, chat ids, sqlite, data/; none may be committed.
13. Observe: next scheduled ticks, error logs, `/api` health, first Telegram run; record exact times in HANDOFF.md with what was NOT observed.

## Rollback (reverse order)
Runner and delivery: `git checkout --detach <previous>`, frontend build if needed, pin, kickstart api-delivery (+web); then live: detach previous, pin
(its branch must hold it), kickstart api; then plists from the saved `plists-before`, bootout + bootstrap. Data additions (boxes, snapshots) can stay.
