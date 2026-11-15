# Activation checklist — exact-rules-v1 with N1 (NBA winner) and H2 (NHL reg3) — prepared 2026-10-11 ~06:10 UTC, corrected ~13:00 UTC

**Status: prepared for ONE explicit approval. Nothing below has been executed.** No registration, commit, release move,
migration, restart, training or publication happened while preparing or correcting it. Every "observed" fact was read from
the live stores with `file:…?mode=ro` connections, from `git`, from the release checkout, from `pmset -g log`, or from the
launchd agents, all read-only. This checklist supersedes the "proposed activation plan" at the end of
`shortlist-2026-10-11.md` (that plan restarted api/web before the migration; see section 8 for the corrected order).

**Choices (user, 2026-10-11):** NBA winner **N1** `logistic@0.8,efficiency_margin@0.75,gbt_v1@0.8`; NHL reg3 **H2**
`goals_glm@0.55,multinomial_l2:nhl_base@0.65,poisson_draw:nhl_base@0.55`. Prospective research tracking only. No NHL winner rule.
N2/N3 deferred. Provenance = the frozen-list evidence file plus a note saying this is an exploratory choice.

## What activating N1/H2 delivers — and what it does not (read before approving)

**N1 and H2 are evaluated on season-start fits. They do not consume the weekly adaptive refits.** Observed in the live stores:

| Member | Fit schedule in force (`model_fits.schedule`) | Source of the live forecast |
|---|---|---|
| `goals_glm` (H2) | `season_start` (4 rows, latest definition `cb194620c0e6a3c6`, first used 2026-10-09) | core cohort recording, two-hour cycle |
| `multinomial_l2:nhl_base`, `poisson_draw:nhl_base` (H2) | `season_start` (1 row each, `cand:` prefix) | `candidates/live.ensure_fit` — fitted once per season, digest-verified artifact kept all season |
| `logistic`, `efficiency_margin`, `gbt_v1` (N1) | none yet (NBA has no cohort rows before 2026-10-20); they will register as `season_start` on first use, as the NHL core members did | core cohort recording |
| every `weekly` row (NHL 36, NBA 53) | `adaptive:…@adaptive` members only | adaptive-v1's own forecasts; **not an input of any exact rule** |

So activation gives **prospective tracking of two fixed member/threshold combinations under their existing training schedules**.
It does not deliver a continually retraining agreement system: nothing refits the members of a rule during the season, and a
rule cannot name an `@adaptive` member (the registry refuses it). If regularly retrained members are a requirement, that is a
separate design task before activation, not something this checklist can supply. **Product decision for the user: track
season-start rules as phase one, or require retrained members first.** The rest of this checklist assumes phase one.

What tracking does: every two-hour cycle it records whether the named members agree above their own thresholds on each
eligible game, and settles those picks later. It selects, recommends and promotes nothing, changes no forecast, and does not
discover or replace rules over time. The historical records in the shortlist are exploratory; the uncertainty of the
*improvement over the comparator* was not computed and is not needed for tracking.

---

**Recorded 2026-10-11 ~13:00 UTC (user, pasted brief):** product decision = proceed with N1/H2 as real prospective tracking
on their current season-start training policy (phase one); adaptive-member integration is out of scope. **Decision B = B1**:
complete the pre-training before the release switch — launch prepared separately in `pretrain-launch-2026-10-11.md`, not
launched. Release, live migration and registration stay unexecuted until their own explicit approval. Thresholds are never
changed to manufacture picks: an incomplete or disagreeing rule is a result.

**Scope, as clarified by the user 2026-10-11 ~13:20 UTC:** N1 and H2 are **exploratory starting candidates** for real
prospective agreement tracking, not proven best combinations. N1 is the simpler NBA starting point; H2 has weak historical
evidence and limited pick volume (68 later-period picks). Disagreements, missing members and no-pick cycles are recorded
honestly as results. B1 pre-training serves a different purpose — preserving the existing adaptive forecasting system
through the bundled release — and does not retrain N1/H2 or make them consume weekly adaptive fits; continually retraining
agreement members is a separate follow-up that this activation does not deliver. The clarification is not an authorization
of the launch, the release, the migration, the registration or any destructive cleanup.

## 0. Decisions that this checklist needs confirmed (one approval covers all; none is pre-chosen for you)

| # | Decision | Options and what the established procedure says |
|---|---|---|
| A | Release scope | Fast-forward the release to a new commit made from the current `model-audit` HEAD plus the pending work, committed in **grouped commits by reviewed increment** (section 1). A smaller "exact-rules only" release is **not** offered: the pending exact-rules hunks share `cli.py`, `main.py`, `types.ts` with the other increments and a hunk-split would be an untested combination. |
| B | Adaptive definitions change with the move (section 3.3) | The established switch procedure (docs/ADAPTIVE_SYSTEM.md §12 step 3.1) requires that "the pre-training runner has finished" before the switch. It has not (section 3.3). **B1 (what §12 expects): complete the pre-training first** — a separate authorization (it trains; ~2 h NHL + ~5–7 h NBA; must run detached from any session shell, see 3.3). **B2: switch without it** — adaptive forecasts are `unavailable` for both sports until a trainer run publishes the new fits; the next scheduled run (daily 09:30 local) is **not a guarantee**: it needs the Mac awake for the whole run and its predecessor did not complete. B2 must be an explicit acceptance, not a default. **B3: defer activation** until the adaptive move is handled on its own. |
| C | Timing | **Chosen at execution against the window criteria in section 4, not a fixed clock window.** The 2026-10-11 pre-13:00 UTC window in the first version of this checklist has passed and is withdrawn. |
| D | Registration order | Register in this checkout **before** the commit, so the registry file ships with the release (section 7). |
| E | Rollback boundaries | Code rollback uses the same pause/stop/drain sequence. **Backup restore, any data deletion and any destructive Git operation on the release branch (`checkout -B`, reset) each need their own explicit approval at that time** (section 10); none is authorized by this approval. |

---

## 1. Release scope: exact commits and included changes

**Release now:** `~/nba-predictor-live` on branch `release` at `f7ee505` (2026-10-09), worktree clean, schema 26, venv has
`ngboost 0.5.11`, `scikit-learn 1.9.1`, no torch. `f7ee505` is an ancestor of HEAD → a fast-forward is possible once HEAD is committed.

**Already committed since the release (8 commits, all reviewed per HANDOFF; Codex PASS on d52b248):**
`aa589fb`, `f300b1e` (handoff), `44a5460`, `c6b6140`, `97fc87f`, `45d7428` (players-v1 study: docs + research code, negative
result, no live behaviour), `deac94a` (adaptive recovery review fixes; **adds migration 0027**), `d52b248` (adaptive replay fix).
Consequence checked read-only: the adaptive definition digest under HEAD code is NHL `abfc617c4309ed33` / NBA `71d234fa8993bf9a`;
under the release code it is NHL `8631cc578d16aa5c` / NBA `2a0111fd73fc5cfa` (both computed in the release venv against the
live stores). See 3.3.

**Pending, uncommitted (24 modified + 14 new files before the test fix below; now 26 modified), grouped by the increment that reviewed it:**

| Commit (proposed) | Files | Review on record |
|---|---|---|
| 1. exact-rules-v1 | `backend/app/exact_rules.py` (new), `backend/app/migrations/0028_exact_rule_observations.sql` (new), `backend/tests/test_exact_rules.py` (new), `backend/tests/test_live.py` and `backend/tests/test_storage.py` (expected migration lists extended by 0028, section 2), `backend/app/agreement_tracking.py`, exact-rules hunks of `backend/app/cli.py` and `backend/app/main.py`, `frontend/src/api/types.ts` (ExactRule…), `frontend/src/components/Tracking.tsx`, `.gitignore`, `docs/MODEL_SELECTION.md` amendment, `docs/SMOKE_TEST.md` sections; plus `backend/app/models/exact_rules.json` once N1/H2 are registered (section 7) | independent review → correction → focused re-review PASS → game-view recheck PASS → synthetic end-to-end PASS |
| 2. thresholds + exact subsets + ledger (research UI) | `backend/app/combination_thresholds.py` (new), `backend/tests/test_combination_thresholds.py` (new), `frontend/src/components/ThresholdExplorer.tsx`, `RuleLedger.tsx` (new), hunks of `cli.py`, `main.py`, `types.ts`, `client.ts`, `PerformancePage.tsx`, `styles.css`, `docs/evidence/combination-thresholds/` (new) | Codex REQUEST CHANGES → fixes → Codex recheck PASS → Codex ledger review PASS |
| 3. combinations of stored forecasts + presentation cleanup | `backend/app/combination_research.py`, `backend/tests/test_combination_stored.py` (new), `frontend/src/components/Research.tsx` | leak fix review → edge states recheck PASS → presentation review PASS |
| 4. dev launcher, results overview, training status | `scripts/dev.sh` (new), `backend/tests/test_dev_script.py`, `test_training_run.py` (new), `backend/app/config.py`, `schemas.py`, `training_monitor.py`, `/api/training-run` + health hunks of `main.py`, `frontend/src/components/Layout.tsx`, `Overview.tsx` (new), `TodayPage.tsx`, `format.ts`, `README.md`, `backend/tests/test_api.py` | dev.sh path-safety reviews → final review → README recheck PASS |
| 5. process docs | `CLAUDE.md`, `docs/TASK_TEMPLATE.md` (new), `docs/ADAPTIVE_SYSTEM.md` §12, `HANDOFF.md` | documentation |

Whoever commits must `git add` by this grouping (hunk-level `git add -p` for `cli.py`, `main.py`, `types.ts`, `client.ts`,
`styles.css`, `PerformancePage.tsx`, or accept one combined commit for the shared files). The **exact release commit** is the
HEAD after these commits; record its hash in HANDOFF before step 7 of section 8.

**Dependencies:** `backend/requirements-release.txt`, `backend/requirements.txt`, `frontend/package.json`,
`frontend/package-lock.json` are unchanged since `f7ee505` (`git diff f7ee505 --stat` empty) → no `uv pip sync`, no `npm ci`.

---

## 2. Readiness results (this checkout; first run 2026-10-11 ~05:50–06:15 UTC, corrected run ~12:45–13:05 UTC)

**Test fix made 2026-10-11 ~12:48 UTC (authorized by the user's correction brief; not committed):** the two hard-coded expected
migration lists in `backend/tests/test_live.py:231` and `backend/tests/test_storage.py:69` ended at
`0027_adaptive_recovery_attempts.sql` and were not extended when 0028 was added. `0028_exact_rule_observations.sql` is the
verified intended change (it is the migration reviewed and exercised in the exact-rules-v1 synthetic end-to-end PASS, and
`db.migrate` enumerates the migrations directory). Each list now ends `…, "0027_adaptive_recovery_attempts.sql",
"0028_exact_rule_observations.sql"]`; the assertions stay exact equalities over the full list (nothing weakened; `git diff` =
2 insertions, 2 deletions).

| Check | Command | Result |
|---|---|---|
| Structure guard | `python3 scripts/check_project.py` | passed |
| Whitespace | `git diff --check` | clean |
| Frontend types | `npm --prefix frontend run typecheck` | exit 0 |
| Frontend build | `npm --prefix frontend run build` | built, `index-3Zq84CRq.js` 403.46 kB |
| Full backend suite before the fix (deep venv, empty scratch `NBA_DATA_DIR`) | `cd backend && NBA_DATA_DIR=<empty scratch> PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python -m pytest -q -p no:cacheprovider` | 2 failed (`tests/test_live.py::test_odds_job_migration_keeps_existing_job_runs`, `tests/test_storage.py::test_migrations_are_idempotent`, both the migration-list expectation), 867 passed (9 min 18 s) |
| The two affected tests after the fix | `cd backend && NBA_DATA_DIR=<empty scratch> OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python -m pytest -q -p no:cacheprovider "tests/test_live.py::test_odds_job_migration_keeps_existing_job_runs" "tests/test_storage.py::test_migrations_are_idempotent"` | **2 passed** (0.34 s); scratch dir still empty |
| **Readiness checker after the fix** (deep venv = the environment with torch/keras, so the deep-study tests run; isolated empty scratch `NBA_DATA_DIR`) | `NBA_DATA_DIR=<empty scratch> OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready` (structure guard → typecheck → build → full pytest in `backend/`) | **passed, exit 0**: structure guard passed, typecheck exit 0, build `index-3Zq84CRq.js` 403.46 kB, pytest **869 passed, 0 failed** (9 min 14 s); scratch dir still empty |
| Rule validation | `exact_rules.validate` for N1 and H2 (no write) | both accepted, cohort `core` |
| Evidence provenance | `exact_rules.explorer_provenance` on the frozen `subsets.json` files | N1 = reliable list rank 2 (file sha256 ce50b1034e5f5082…); H2 = reliable list rank 20 (file sha256 0dab69122429af8b…) |
| Registry state | `ls backend/app/models/exact_rules.json*` | absent (nothing registered) |

Not run: `--ready` in this checkout's main venv (it lacks torch/keras, so 8 `test_deep_study.py` tests cannot pass there;
the deep venv imports this checkout's `app` when run from `backend/`, verified), browser, any service. After the commits,
re-run the readiness checker the same way and `git status --porcelain` (must be empty) before step 7 of section 8.

---

## 3. Member availability and training schedule in the target environment

### 3.1 N1 — NBA winner: `logistic` ≥80 %, `efficiency_margin` ≥75 %, `gbt_v1` ≥80 %

| Fact | Observed |
|---|---|
| Members in the release code | all three are NBA core cohort members (`agreement.NAMES["nba"]` in `~/nba-predictor-live`); no candidate forecast, no optional dependency |
| How they are produced live | by the two-hour cycle's research cohort recording (`research_live.record` → `research_cohorts`, member rows `research-v2:<member>:<definition>` in `predictions`) |
| Fits | season-start fits registered on first use in `model_fits` (`schedule = season_start`); NHL shows this pattern (28 `season_start` rows). **NBA has none yet**: `research_cohorts` 0 rows, `model_fits` has only the 53 `adaptive:…@adaptive` weekly rows, because no NBA game has entered a two-hour window this season |
| First eligible NBA game | 2026-10-20 19:00 UTC (regular season; preseason games are not recorded) |
| Refits vs the rule | the rule names members only; each observation stores every member's `model_version` and a `version_sig`; a refit never changes the rule; no weekly refit exists for these members |
| Risk | none specific to N1; if the NBA cohort recording fails to start on 2026-10-20 that is a cohort problem visible in `job_runs`, not a rule problem |

### 3.2 H2 — NHL reg3: `goals_glm` ≥55 %, `multinomial_l2:nhl_base` ≥65 %, `poisson_draw:nhl_base` ≥55 %

| Fact | Observed |
|---|---|
| `goals_glm` | NHL core cohort member for reg3 (release code); `model_fits` rows all `season_start` (definition `cb194620c0e6a3c6` first used 2026-10-09) |
| `multinomial_l2:nhl_base`, `poisson_draw:nhl_base` | in `candidates_for("nhl","reg3", live_ok=True)` of the **release checkout and venv**; no optional group (`candidate_requirements` → none); `model_fits` rows `cand:multinomial_l2:nhl_base` / `cand:poisson_draw:nhl_base` are `season_start`; artifacts with sha256 sidecars in `data/research/candidates/artifacts/nhl/{multinomial_l2__nhl_base,poisson_draw__nhl_base}/…-2027-cc9738f96956df94.pkl` (digest-verified, kept all season) |
| Live recording | `candidate_forecasts` reg3: 38 rows each in the last 7 days (as of ~06:00 UTC); 466 research cohorts, 15,579 agreement observations in the live store |
| Training schedule | candidate fits are season-start only (`candidates/live.ensure_fit`), cohort members season-start; the weekly `adaptive-train` fits are adaptive-v1's own (`adaptive:…@adaptive` members) and are not inputs of this rule |
| Refits vs the rule | as above |

### 3.3 Side effect of the release move: adaptive definitions change, and the pre-training did not complete

The 8 committed commits change the adaptive definition digests (section 1). After the fast-forward the release serves
definitions that have **no fits** in either store (`adaptive_fits` has rows only for `8631cc578d16aa5c` / `2a0111fd73fc5cfa`
and older, all `success`; none for `abfc617c4309ed33` / `71d234fa8993bf9a`), so `adaptive.record` writes `unavailable` rows
until a trainer run publishes, as it did on 2026-10-09 (11 `unavailable` rows).

**The pre-training meant to close this gap stopped at 2026-10-11T03:07Z, and the evidence points to the launch mechanism, not
the trainer:**
- `progress.log`: gate "checks passed; launching pretrain.py" and "nhl: attempt 1 started" at 02:45:55Z; no "exited" line.
  `nhl-1.err` ends after the 14 member walks (last write 02:56Z); `nhl-1.json` is empty (the runner never got the subprocess
  result). No traceback anywhere.
- The trainer was still progressing after that: `data/research/adaptive/nhl/history-abfc617c4309ed33.pkl` (+ `.sha256`) was
  written at **03:06Z**, and the snapshot `snapshots/2026-10-11T024555Z-72804.sqlite` (+ `-wal`/`-shm`) is left behind.
- `pmset -g log`: the gate's `caffeinate` (PID 81170, started with `sh gate.sh` at 19:39Z) shows `ClientDied` at
  **03:07:06Z**; the next sleep event is `Idle Sleep` at 06:10:38Z, i.e. the Mac did **not** sleep at the time of the stop.
- The gate was a **background job of a Claude session** (HANDOFF entry "Pre-training gated start ACTIVE", 2026-10-10 19:39Z),
  which recorded: "Dies if this session closes or the Mac sleeps." The whole tree (sh → caffeinate → python → trainer) died
  at once, with no sleep and no error: consistent with that session closing.
- Consequence: no fit for the new definitions was published; the orphan history file and snapshot are what §12 step 2
  describes ("an interrupted run leaves unreferenced artifacts and a snapshot that the next run of that sport clears or
  rewrites"). Nothing was written to `adaptive_fits` for the new digests (no `running`/`failed` row either).

If B1 is chosen, the pre-training must be launched **detached from any interactive session** (`nohup setsid caffeinate -i …`,
or a one-shot launchd job) so that closing a session cannot kill it, and it must be verified by the published rows, not by
elapsed time. Last scheduled training durations (NHL 02:36→04:23Z = 1 h 47 min, NBA 04:24→09:23Z = 5 h on 2026-10-10) are
reference values, not a guarantee. The daily 09:30 local `adaptive-train` agent trains only when a definition is due; while
the release stays at `f7ee505` it reports `due: false` and exits (observed in `data/adaptive-train-launchd.log`).

Adaptive is research only: this affects the adaptive card and adaptive rows, not the primary forecasts, the cohorts, the
sealed tracking groups or the exact rules.

---

## 4. Writers, locks, and the pause/stop/drain procedure (docs/ADAPTIVE_SYSTEM.md §12 step 3, reused as is)

**Writers on the live stores (all via the release checkout):** launchd `com.nba-predictor.odds` (every 15 min, NBA),
`nhl-odds` (every 30 min), `injuries` (:02 and :32), `daily` (22:00 local), `nhl-daily` (22:15 local), `adaptive-train`
(09:30 local daily, research venv, trains only when due). `api` and `web` are read-only but **KeepAlive**, and the API computes
adaptive definitions from the files on disk at call time, so they must be stopped before the release files change. Every CLI
writer, including `migrate`, takes the per-sport flock `data/.{sport}-jobs.lock` (`db.store_lock`; `odds` skips when busy,
others fail visibly), and `adaptive-train` also holds `adaptive.training_lock`. Locks stop only *new* lock-takers; a process
that imported the old code before the fast-forward is not stopped by them (§12 step 3), hence the sequence below.

**Window criteria (evaluate at execution time; §12 step 3.1):**
1. No eligible game in either sport starts within the next ~2.5 h (the near-tipoff cycle can hold the job lock for over an hour).
2. The nightly jobs (22:00/22:15 local) and the 09:30 local trainer are not due during the switch.
3. Decision B is settled: B1 → the pre-training has finished and its fits are published (`adaptive_fits` rows for the new
   digests with status `success`); B2 → explicitly accepted in the approval.
4. Every scheduled agent shows `state = not running` and no release/CLI/trainer process exists (next block).
Reference only, re-check at execution: on 2026-10-11 NHL games start 21:00, 22:00, 23:00 UTC (two-hour cycles from 19:00 UTC);
2026-10-12: 17:00 and 23:00 UTC; NBA has no eligible game before 2026-10-20. Do not reuse this schedule without re-reading it.

**Sequence (nothing is killed; active work finishes first):**
1. **Pause launches:** for each of `odds`, `nhl-odds`, `injuries`, `daily`, `nhl-daily`, `adaptive-train`:
   wait until `launchctl print gui/$(id -u)/com.nba-predictor.<label>` shows `state = not running`, **then**
   `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.<label>.plist`. Never bootout a running job
   (it would be killed mid-cycle); if one is running, wait for it.
2. **Stop the long-lived services:** `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.api.plist` and
   `…web.plist` (KeepAlive would otherwise restart them from the changed files).
3. **Drain and confirm quiet:** `pgrep -fl 'nba-predictor-live|app\.cli|adaptive-train|pretrain\.py'` prints nothing; all
   four locks can be taken with zero wait, from this checkout against the live data dir (the same check the pre-training
   gate used):
   ```sh
   cd /Users/andry/Desktop/nba-predictor-sprint1/backend && NBA_DATA_DIR=/Users/andry/Desktop/nba-predictor-sprint1/data .venv/bin/python - <<'EOF'
   from app import db, adaptive
   for s in ("nhl", "nba"):
       with db.store_lock(s, wait_seconds=0) as job_free:
           pass
       with adaptive.training_lock(s) as train_free:
           pass
       print(s, "job lock free:", job_free, "training lock free:", train_free)
   EOF
   ```
   → both `True` for both sports. If anything is running: wait or stop the switch; never kill a writer.
4. **Record boundaries (read-only) and the drain time:** per store `max(forecast_id)` of `adaptive_forecasts`,
   `max(recovery_id)` of `adaptive_recovered_forecasts`, `max(observation_id)` of `agreement_observations`,
   `max(run_id)` of `job_runs`, and the definitions in force before the move (release `release-check` output).
5. Backup (section 5) → fast-forward, build (section 8) → migrate (section 6) → `release-check` → restart api/web → verify →
   resume the six agents with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.<label>.plist`;
   `launchctl list | grep nba-predictor` shows all eight.

---

## 5. Verified backups (after the drain, while holding each sport's job lock — §12 step 5)

From this checkout's venv, with the agents paused and api/web stopped (section 4):
```sh
cd /Users/andry/Desktop/nba-predictor-sprint1/backend && NBA_DATA_DIR=/Users/andry/Desktop/nba-predictor-sprint1/data .venv/bin/python - <<'EOF'
import sqlite3
from app import db
D = "/Users/andry/Desktop/nba-predictor-sprint1/data"
for s in ("nba", "nhl"):
    with db.store_lock(s, wait_seconds=0) as held:
        assert held, f"{s} job lock busy: a writer is still active, stop the switch"
        src = sqlite3.connect(f"file:{D}/{s}.sqlite?mode=ro", uri=True)          # WAL store: mode=ro, never immutable=1
        dst = sqlite3.connect(f"{D}/backups/{s}-before-0027-0028-<date>.sqlite")
        src.backup(dst); dst.commit()
        print(s, dst.execute("pragma quick_check").fetchone()[0], "schema", dst.execute("select max(version) from schema_migrations").fetchone()[0],
              [(t, src.execute(f"select count(*) from {t}").fetchone()[0] == dst.execute(f"select count(*) from {t}").fetchone()[0])
               for t in ("games", "predictions", "market_forecasts", "research_cohorts", "agreement_observations", "candidate_forecasts", "adaptive_forecasts", "job_runs")])
        src.close(); dst.close()
EOF
```
Expected: `ok`, schema 26, every count `True`; the holder exits before the next step (the CLI takes the lock itself).
Reference counts observed 2026-10-11 ~05:55 UTC (they will be higher at execution): NHL games 17,203, predictions 9,205,
market_forecasts 1,313, research_cohorts 466, agreement_observations 15,579, candidate_forecasts 1,638, adaptive_forecasts 138;
NBA games 9,635, predictions 27,744, the rest 0. Live store sizes 158 MB / 27.9 MB, `quick_check` ok on both.
Earlier backups in `data/backups/` (`nhl-before-0026-20261010.sqlite`, `…-before-release-20261008.sqlite`) are kept.

---

## 6. Migrations 0027 and 0028 (applied by `migrate` from the release checkout, after the fast-forward, while quiescent)

| Migration | What it does | Data at risk | Verification after |
|---|---|---|---|
| `0027_adaptive_recovery_attempts.sql` (committed in `deac94a`) | `CREATE TABLE adaptive_recovery_attempts` — bounded retry state for adaptive reconstructions whose replay failed for a non-evidence reason (one row per game/definition, replaced on each attempt, removed once recorded). Operational state, never read by forecasts, training or quality reports. | none: new empty table | table exists, 0 rows |
| `0028_exact_rule_observations.sql` (pending, commit 1) | Rebuilds `agreement_observations` as 0022 did: creates `agreement_observations_new` with the `kind` CHECK widened by `'exact'` and the `status` CHECK by `'below_threshold'`, copies every row with the same `observation_id`, drops the old table, renames, recreates the index and the three triggers (`no_update`, `no_delete`, pregame guard). Backfills nothing. | the NHL rows (15,579 at ~06:00 UTC; NBA 0) are copied; a failure mid-way leaves the transaction rolled back (`db.migrate` runs inside `with transaction(conn)`) | `select count(*), min(observation_id), max(observation_id) from agreement_observations` equal to before; `select count(*) from sqlite_master where tbl_name='agreement_observations' and type='trigger'` = 3; `pragma quick_check` ok; `select max(version) from schema_migrations` = 28 |

Command (each sport, release checkout, guard env, after the fast-forward, with api/web stopped and the agents paused):
```sh
cd ~/nba-predictor-live/backend && NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT=$HOME/nba-predictor-live \
  NBA_DATA_DIR=/Users/andry/Desktop/nba-predictor-sprint1/data .venv/bin/python -m app.cli --sport nhl migrate   # then nba
```
Expected output: `"applied": ["0027_adaptive_recovery_attempts.sql", "0028_exact_rule_observations.sql"]` for each sport.
Until this step the new code reports `exact_rules.status = "schema_required"` and a cycle would skip exact rules with that reason.

---

## 7. Registration (in this checkout, before the commit; writes only `backend/app/models/exact_rules.json`)

Copy the two frozen-list files out of the old dev folder first (that folder is not to be modified):
```sh
S=/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/0e06ac22-eb45-4e3c-ac7f-9e84314ec7ac/scratchpad/evidence   # or any scratch path
cp ~/nba-predictor-dev-data/reports/combination-thresholds-nba-winner-pool/subsets.json $S/nba-winner-pool-subsets.json
cp ~/nba-predictor-dev-data/reports/combination-thresholds-nhl-reg3-pool/subsets.json  $S/nhl-reg3-pool-subsets.json
cd /Users/andry/Desktop/nba-predictor-sprint1/backend
# dry runs first (validate and print; write nothing — a dry run does create the gitignored exact_rules.json.lock)
.venv/bin/python -m app.cli --sport nba exact-rules-register --market winner --rule logistic@0.8,efficiency_margin@0.75,gbt_v1@0.8 \
  --evidence $S/nba-winner-pool-subsets.json --note "exploratory starting candidate for prospective tracking, not a proven best combination (shortlist 2026-10-11, N1, the simpler NBA starting point): later record identical to the best single on the same games; tests game selection, not a different pick; members on season-start fits, no weekly refit" --dry-run
.venv/bin/python -m app.cli --sport nhl exact-rules-register --market reg3 --rule goals_glm@0.55,multinomial_l2:nhl_base@0.65,poisson_draw:nhl_base@0.55 \
  --evidence $S/nhl-reg3-pool-subsets.json --note "exploratory starting candidate for prospective tracking, not a proven best combination (shortlist 2026-10-11, H2): weak historical evidence, limited pick volume, one-game edge over the comparator; members on season-start fits, no weekly refit" --dry-run
# then the same two commands without --dry-run; then
.venv/bin/python -m app.cli --sport nba exact-rules-list && .venv/bin/python -m app.cli --sport nhl exact-rules-list
```
Expected: `"status": "registered"`, versions 1 and 2, `effective_from` = the registration time (UTC). Only games that start
after `effective_from` are ever observed; NBA observes nothing before 2026-10-20. "Registered" is not "recording":
recording starts with the first cycle that runs the new code against a schema-28 store. The registry file is then committed in
commit 1 of section 1 and reaches the release with the fast-forward (registering inside the release checkout would make it unclean).

---

## 8. Service changes (in this order; steps 1–5 are section 4's sequence)

0. Before the window: registration (section 7), the five commits (section 1), readiness re-run and `git status --porcelain`
   empty in this checkout; note the release commit hash. Decision B settled (B1: published fits verified).
1. Window criteria met (section 4). Pause the six agents: wait for `state = not running`, then bootout each.
2. Stop `api` and `web` (bootout).
3. Drain check: no process, four locks free.
4. Record the row-ID boundaries and the drain time; backup both stores under the job lock (section 5), verify.
5. `git -C ~/nba-predictor-live merge --ff-only <release commit>`; `git -C ~/nba-predictor-live rev-parse HEAD` = the commit;
   `git -C ~/nba-predictor-live status --porcelain` empty.
6. `(cd ~/nba-predictor-live/frontend && npm run build)` (node via fnm v22.23.2; no `npm ci`, lockfile unchanged).
7. Migrate both sports (section 6); run the verification queries there.
8. `release-check` for nhl and nba from the release checkout → `problems: []`, schema 28/28, commit = the release commit.
9. Bootstrap `api` and `web`; `curl -s 127.0.0.1:8100/api/health` ok; web `200`.
   `curl -s '127.0.0.1:8100/api/research/tracking?sport=nhl'` → `exact_rules.status` is the no-observations state (not
   `schema_required`, not `failed`), one rule (H2) listed; same for nba with N1.
   `/api/research/adaptive` → the new definition; B1: `due: false` and the pre-trained fits; B2: no fit in force (expected).
10. Verify nothing was written while the code moved: no row above the step-4 boundaries in `adaptive_forecasts`,
    `adaptive_recovered_forecasts`, `agreement_observations` or `job_runs` with a timestamp between the drain time and now.
11. Resume the six agents (bootstrap). Confirm the next `nhl-odds`/`odds` runs in `job_runs` succeed with
    `detail.release` = the new commit.

---

## 9. First-cycle verification (first NHL two-hour cycle after activation; NBA from 2026-10-20)

- `job_runs` detail of the first two-hour cycle: `out["exact"]` present, H2's rule id listed, no `failed`/`schema_required`.
- `select kind, status, count(*) from agreement_observations where kind='exact' group by 1,2` → rows for that cycle's games,
  statuses among `pick`, `below_threshold`, `disagree`, `incomplete`; `registry` = H2's `rule_sha256`; `information_at < tipoff_at`.
- Performance → research → "Registered exact rules · Prospective only": H2 with eligible count and per-member reasons; NBA shows the rule with no eligible games yet.
- Game card of one of that day's games: the exact-rule row appears (note the known top-12 cut).
- After settlement (nhl-daily 02:15 UTC): W–L–P for H2 appears; sealed groups unchanged.
- Adaptive card: B1 → forecasts under the new definition; B2 → `unavailable` until a trainer run publishes; in both cases
  check `data/adaptive-train-launchd.log` and `adaptive_fits` (status `success` for the new digests), not elapsed time.
- NBA on 2026-10-20: first cohorts recorded, `model_fits` season-start rows appear for the NBA members, N1 observations begin.

---

## 10. Rollback and data-preservation boundaries

| Point | Rollback | Data impact | Approval |
|---|---|---|---|
| Before step 7 of section 8 (code moved, stores still 26) | move the release back to `f7ee505`, rebuild, bootstrap api/web, `release-check`, resume agents — same pause/stop/drain sequence | none; nothing recorded under the new code (agents paused, api/web stopped) | moving the `release` branch ref back is a destructive Git operation on the release checkout → **separate explicit approval** |
| After migration, before the agents resume | code rollback as above **plus** restore both section-5 backups (`cp` over `data/{sport}.sqlite` with everything still stopped, `-wal`/`-shm` removed first) | none: no cycle has written since the backup | **separate explicit approval** for the restore and for the Git operation |
| After agents run on schema 28 | `f7ee505` refuses a schema-28 store (release guard); there is no downgrade migration. (a) **forward-fix** on the new code; (b) restore the section-5 backups, which **discards every row recorded after the backup** (odds raw responses, predictions, market and near-tipoff forecasts, cohorts, sealed-group and exact observations, candidate and adaptive forecasts, job runs) | (b) loses data; (a) loses none | (b) and any deletion → **separate explicit approval at that time**; (a) is an ordinary release |

Rows written under 0028 (`kind='exact'`) are append-only (triggers) and cannot be removed by the app; a rule cannot be
unregistered, only left in place. The **commitment point is step 7 of section 8** (the migration while quiescent); up to
that point rollback is free of data loss. This approval authorizes no restore, no deletion and no destructive Git operation.

---

## 11. Known limitations carried into activation (recorded, not blocking)

`--evidence` with a missing/invalid file ends in a traceback (use existing files); `--dry-run` creates the gitignored lock file;
member availability is checked on the registering machine (irrelevant here: both rules verified in the release venv, section 3);
game-card top-12 cut; `schema_required` text missing a full stop; historical toggle to check by hand; excluded-games period label;
the uncertainty of each rule's improvement over its comparator is not computed anywhere; the orphan adaptive snapshot and
history file from the interrupted pre-training (3.3) are cleared by the next NHL training run, not by hand.
