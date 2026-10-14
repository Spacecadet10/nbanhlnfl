# Pre-training launch (decision B1) — prepared 2026-10-11 ~13:05 UTC for ONE explicit approval. Not launched.

Purpose: publish adaptive-v1 fits for the definitions the next release will compute, **before** the release switch, so the
adaptive forecasts keep serving across the move (docs/ADAPTIVE_SYSTEM.md §12 step 2). This is the retry of the run that
stopped on 2026-10-11T03:07Z (activation checklist §3.3), with the launch mechanism changed so that no session can kill it.
It trains and writes fit records; it does not migrate, deploy, restart, register or change the serving release.

**Two separate purposes, kept separate (user clarification 2026-10-11 ~13:20 UTC).** This pre-training exists only to
preserve the existing adaptive forecasting system during the proposed bundled release. It does **not** retrain N1 or H2,
and it does not make them consume weekly adaptive fits: N1/H2 stay on their season-start fits (activation checklist, opening
section). Continually retraining agreement members remains a separate follow-up that this activation does not deliver.

**Success means verified published fits** for both expected definitions with every required market weighted and every
required member fit `success` — not a launched process, elapsed time or an exit code (§5). The serving release stays
unchanged until that is established and the later activation is explicitly approved. The launch cutoff in §4 is an
**estimate**, not a guarantee that publication lands before a game window: schedules and process/lock state are re-checked
at launch, the contention safeguards stay as they are, and a failure or delay is reported as such, without promising a
recovery time.

**Approval status:** the clarification above is not an authorization. The seven launch steps (§5), the release, the
migration, the registration and any destructive cleanup each still need their own explicit approval.

## 1. Fixed code revision and expected definitions (observed read-only, 2026-10-11 ~12:55 UTC)

| Item | Value |
|---|---|
| Training checkout | `~/nba-predictor-pretrain`, detached HEAD at `d52b248a7f712475d5a5c5609a5024c42456d651`, `git status --porcelain` empty |
| Runner's pinned commit | `pretrain.py` `COMMIT = d52b248…`; `checkout_ok()` refuses to train if the checkout differs or is dirty (checked before every attempt) |
| Definitions the runner expects | NHL `abfc617c4309ed33`, NBA `71d234fa8993bf9a` (`EXPECTED` in `pretrain.py`) |
| Same definitions recomputed now via `adaptive.definition(db.connect_readonly(sport))` | pretrain worktree + deep venv: NHL `abfc617c4309ed33` / NBA `71d234fa8993bf9a`; **this working tree with all pending uncommitted changes**, deep venv and release venv: identical; release checkout + release venv: NHL `8631cc578d16aa5c` / NBA `2a0111fd73fc5cfa` (what is served now) |
| Why the release commit will compute the same digests | `adaptive.definition` hashes `adaptive.py`, the research identity (`combine.py`, `research_live.py`, `markets.py`, `market_live.py`, `retrain_cadence.py`, `fit_register.py`, the line members' model files), the agreement digest and the candidate registry; none of these is touched by the pending work (verified by recomputing from the working tree). The five activation commits must not edit them; the activation checklist re-checks the digest after the commits |
| Definitions the release serves now | NHL `8631cc578d16aa5c` / NBA `2a0111fd73fc5cfa` (`/api/research/adaptive?sport=nhl` at 13:00:33Z reports `8631cc578d16aa5c`, no error); fits for both exist with status `success` |
| Fits for the new definitions | none in `adaptive_fits` (no `success`, `failed` or `running` row) |

## 2. The existing runner, reused unchanged

- `~/nba-predictor-pretrain-logs/pretrain.py` (reviewed by Codex on 2026-10-10 per HANDOFF): NHL first, then NBA; each sport
  runs `app.cli --sport <s> adaptive-train` from the pretrain checkout with the **deep venv**
  (`~/nba-predictor-deep/backend/.venv/bin/python`, the same interpreter the scheduled `adaptive-train` agent uses) and
  `NBA_DATA_DIR=/Users/andry/Desktop/nba-predictor-sprint1/data`, `OMP_NUM_THREADS=4`; stdout to `<sport>-<attempt>.json`,
  stderr to `<sport>-<attempt>.err`, progress lines to `progress.log`.
- `~/nba-predictor-pretrain-logs/gate.sh` (reviewed with it): before launching, checks every 10 min that `daily`,
  `nhl-daily` and `adaptive-train` are `state = not running`, that no `adaptive-train|pretrain.py|app.cli` process exists,
  and that both sports' job locks and training locks can be taken with zero wait; its `NOT_BEFORE` (2026-10-11T02:45Z) is in
  the past, so the checks start at once. Neither file is modified for this launch.
- What `adaptive-train` does (`adaptive.run_scheduled`): takes the sport's **training lock** for the whole run; takes the
  **job lock briefly** (120 s wait) for the due check and a consistent snapshot of the store, releases it, trains from the
  snapshot (forecast cycles keep running), then takes the job lock briefly again to verify and **publish** the fit records.
  Leftover snapshots of an interrupted run are deleted under the training lock before the new snapshot; the history cache
  `history-<definition>.pkl` is sha256-verified on load and rewritten on mismatch; artifacts are content-addressed with
  sha256 sidecars. If the definition computed at publication differs from the one trained, nothing is published.

## 3. Session-independent execution mechanism (new; the only new piece)

A **one-shot launchd user agent**, modelled on `com.nba-predictor.adaptive-train.plist` (same `caffeinate -i`, same
`ProcessType Background`), with `RunAtLoad` so it starts when bootstrapped and `KeepAlive` false so it runs once:
`com.nba-predictor.pretrain-once.plist` (drafted in the session scratchpad, `plutil -lint` OK, **not installed**):
```xml
Label               com.nba-predictor.pretrain-once
ProgramArguments    /usr/bin/caffeinate -i /bin/sh /Users/andry/nba-predictor-pretrain-logs/gate.sh
RunAtLoad true · KeepAlive false · ProcessType Background
WorkingDirectory    /Users/andry/nba-predictor-pretrain-logs
StandardOutPath / StandardErrorPath   /Users/andry/nba-predictor-pretrain-logs/launchd.log
EnvironmentVariables PATH=/usr/bin:/bin:/usr/sbin:/sbin   (git, pgrep, caffeinate, launchctl are all there)
```
launchd owns the process tree, so closing any terminal or Claude session cannot end it (the 03:07Z stop was the session
mechanism, not the trainer). `caffeinate -i` prevents idle sleep; a closed lid or a manual sleep still pauses it, as it does
the scheduled trainer. The agent is booted out and its plist removed after the run (step 6), so it never re-runs at login;
if it did, the runner would find the fits already published (`due: false`), verify them and exit 0 without training.

## 4. Lock and contention safeguards

| Contender | Safeguard |
|---|---|
| Scheduled `adaptive-train` (daily 09:30 local = 13:30 UTC) | gate refuses to launch while it is running; if it fires during the run, whichever trainer is second skips on the training lock with exit 0 (§12); the runner retries a lock skip every 10 min for up to 12 h per sport. Under the current release it reports `due: false` and exits in seconds (observed in `data/adaptive-train-launchd.log`) |
| Two-hour NHL cycles (`nhl-odds`, every 30 min; near-tipoff cycles can hold the NHL job lock for over an hour) | publication waits only 120 s for the job lock, then returns `failed` ("store busy at publication … the next run republishes") and the runner stops. **Launch window criterion: NHL publication (≈1 h 50 min after start) must land outside NHL two-hour windows.** Reference, re-check at launch: today's NHL games start 21:00/22:00/23:00 UTC, so the cycles run from 19:00 UTC → launch **by ~16:30 UTC today**, or after tonight's cycles and the 02:15 UTC `nhl-daily` run (from ~03:00 UTC on 2026-10-12, with 2026-10-12 games at 17:00 and 23:00 UTC in mind) |
| NBA store | no NBA game before 2026-10-20; `odds`/`injuries` hold the NBA job lock for seconds; the 02:00 UTC `daily` run must not coincide with NBA publication (NBA trains ≈5 h after NHL; a 16:30 UTC launch publishes NBA by ~23:30 UTC) |
| Nightly `daily`/`nhl-daily` | gate refuses to launch while they run |
| Checkout drift | `checkout_ok()` before every attempt; `publish()` refuses if the definition changed during training |
| The activation release switch | **not started until this run has verified** (checklist §4 window criterion 3) |

## 5. Launch steps (after approval; in this order; each output recorded in HANDOFF)

1. Re-check now, read-only: `git -C ~/nba-predictor-pretrain rev-parse HEAD && git -C ~/nba-predictor-pretrain status --porcelain`
   (d52b248, empty); `git -C ~/nba-predictor-live rev-parse HEAD` (f7ee505); `launchctl list | grep nba-predictor` (eight
   agents); `date -u` against the window criterion in §4; `pgrep -fl 'adaptive-train|pretrain\.py|app\.cli'` empty.
2. Keep the interrupted attempt's files: `mkdir ~/nba-predictor-pretrain-logs/interrupted-20261011 && mv
   ~/nba-predictor-pretrain-logs/nhl-1.err ~/nba-predictor-pretrain-logs/nhl-1.json ~/nba-predictor-pretrain-logs/interrupted-20261011/`
   (the runner would overwrite `nhl-1.*`; `progress.log` is append-only and stays).
3. Install and start the one-shot agent:
   `cp <scratchpad>/com.nba-predictor.pretrain-once.plist ~/Library/LaunchAgents/ && plutil -lint ~/Library/LaunchAgents/com.nba-predictor.pretrain-once.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.pretrain-once.plist`
   then `launchctl print gui/$(id -u)/com.nba-predictor.pretrain-once | grep -E 'state|pid'` → running, and within a minute
   `progress.log` shows `gate: checks passed; launching pretrain.py` and `nhl: attempt 1 started` (or `not yet: …` with the
   reason, rechecked every 10 min).
4. Monitor (read-only): `tail -f ~/nba-predictor-pretrain-logs/progress.log`; expected lines in order: `nhl: attempt 1
   status=success …`, `nhl: verification {… "ok": true}`, `nba: attempt 1 started`, `nba: attempt 1 status=success …`,
   `nba: verification {… "ok": true}`, `RESULT: nhl=verified, nba=verified`, `gate: pretrain.py exited 0`. Reference durations
   (2026-10-10, same agent priority): NHL 1 h 47 min, NBA 5 h — not a guarantee. The Mac must stay awake (lid open or on
   power with sleep prevented); `pmset -g assertions | grep caffeinate` shows the assertion while it runs.
5. Success checks, read-only, after `RESULT`:
   - `launchctl print gui/$(id -u)/com.nba-predictor.pretrain-once | grep 'last exit code'` → 0; `launchd.log` has no traceback.
   - Published fits (the criterion, not elapsed time), per sport with `file:data/<s>.sqlite?mode=ro`:
     `select kind, status, count(*), max(finished_at) from adaptive_fits where definition=? group by 1,2` → one `combiner`
     `success` row and `member` `success` rows for every member the live definition has (NHL 15, NBA 21; the runner's
     `verify()` already compares the sets and the market coverage); `select max(version) from schema_migrations` still 26.
   - Serving release unchanged: `git -C ~/nba-predictor-live rev-parse HEAD` = f7ee505, `status --porcelain` empty;
     `release-check` for both sports → `problems: []`; `/api/research/adaptive?sport=<s>` still reports the old definition
     with its fits (the new rows are invisible to the old code until the switch).
   - Leftovers: `data/research/adaptive/<s>/snapshots/` empty; the 02:45Z snapshot gone.
6. Remove the one-shot agent: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.pretrain-once.plist
   && rm ~/Library/LaunchAgents/com.nba-predictor.pretrain-once.plist`; `launchctl list | grep nba-predictor` → eight agents.
7. Record the outcome in HANDOFF; then the activation commands (checklist §7–§8) are returned for their own approval.

## 6. If it fails

The runner stops at the first non-success (`STOP: …` in `progress.log`) and exits 1; the agent then just ends. Nothing is
rolled back because nothing in the serving path changed: `failed`/`partial` fit rows for the new definition stay in the
ledger as history, artifacts stay on disk, the release keeps serving the old definition. Diagnose from `<sport>-<n>.err`,
`launchd.log` and `adaptive_fits.detail`; do not re-launch without reading them. Removing fit rows or artifacts is a
separate approval (never done by the runner or the app).

## 7. What this approval does not cover

The release switch, migrations 0027/0028, registration of N1/H2, any restart, any rollback, and any deletion. Those stay
with the activation checklist and its own approval.
