# Batch 1 addendum: a restore that fails partway is now rolled back

Date: 2026-11-08. This addendum adds to `BATCH1_FINAL_REPORT.md` in the same folder and does not change it. Where the
two disagree on the final tree, test counts or gate result, this file is the newer record.

## 0. Bottom line

- **The defect.** A restore that failed during the file swap could leave a store missing. Example: NBA was already
  replaced, the old NFL files were moved aside, and the new NFL file was left under a temporary name, so the app had no
  NFL store. This was already the case before the batch.
- **The fix.** Any error during the swap now undoes every rename already done, for every sport, and leaves the original
  files in place. If the undo itself fails, nothing is deleted and the error lists every file plus the renames to do by
  hand. A crash or power loss mid-swap cannot be undone automatically; the code documentation says what can be left
  behind and how to recover by hand.
- **Review.** The same single writer as round 4 wrote the fix, test first. The independent backup verifier re-ran
  everything itself and gave **APPROVE**, with three low or informational notes recorded in §5 and not fixed.
- **Final gate on the final tree: passed.** Exit 0, 1,737 passed, 0 failed. The code-tree hash was `adacc372…`
  before and after.
- **The previous gate failed.** It ran on the older tree `3c2ec4a…`: 1,727 passed, 1 failed (the flaky
  `test_research_agent` deadline test). It remains evidence for that older tree only.
  - Correction to the brief: it called the previous gate a "pass"; it was a fail.
  - The flaky test passed this time. That does not make it stable: see §6.
- **Nothing applied or published.** The patch is not applied to the main checkout. No live store, launchd, Telegram,
  deployment, commit or push was touched.

## 1. Tree identity

| Tree | Where | Code-tree hash | Gate |
|---|---|---|---|
| Untouched base | index of `~/Desktop/nba-predictor-batch1` (HEAD `79ff6ec` + the main checkout's uncommitted work as of the batch start) | — | not run on it |
| Pre-round-5 batch tree | the batch1 working tree before round 5 | `3c2ec4a6023b2602e160bdae0d701d76aa19529b` | **FAILED**, 1,727 passed / 1 failed (07:04Z) |
| **Final batch tree (reviewed baseline)** | the batch1 working tree now | **`adacc372fa2ba7bda2a8728bfcbd601cbd2233a5`** | **PASSED**, 1,737 passed / 0 failed (15:28Z) |

- **Hash recipe.** Run in the batch1 worktree:
  `{ git diff -- . ':(exclude)HANDOFF.md'; git ls-files -o --exclude-standard | sort | xargs shasum; } | shasum`.
  The 3 new test files are now intent-to-add entries, so nothing is untracked. The hash therefore equals the SHA-1 of
  `batch1-final/batch1-final-r5.patch`, which `shasum` confirmed: `adacc372…`.
- **The round-5 hash was taken at 14:31:13Z,** just before the gate, and matches `gate_hash_before.txt`. The pre-round-5
  hash `3c2ec4a…` was re-checked when round 5 started (`scratchpad/final/round5/hash_pre_round5.txt`).

## 2. The defect

- **Reproduction** (verifier, round 5): `Path.rename` patched to raise `OSError(5)` only for the
  `nfl.sqlite.restoring → nfl.sqlite` install, then `restore(B, d, sports=['nba','nfl'])` on scratch stores.
- **Result:**
  - the call raised `OSError`;
  - `nba.sqlite` was already the restored copy;
  - `nfl.sqlite`, `-wal` and `-shm` had been moved to `.replaced-<T>`;
  - `nfl.sqlite.restoring` was left in place, and `nfl.sqlite` did not exist.
  - Log: `batch1-final/verifiers/backup-round5/15-partial-restore-repro.log`.
- **Cause:** step 3 of `restore()` renamed files one by one with no undo. The comment "a two-sport restore never
  half-completes" was true only for steps 1–2 (checks, locks, staged copies).
- **Already in the base:** the base `backup.py` has the same per-sport swap with no undo. Round 4 added one more raise
  inside the swap (the safety-copy name clash), which can only fire if another process creates the name in between.

## 3. The fix (round 5)

Files: `backend/app/backup.py` and `backend/tests/test_backup_restore.py` only, +180/−24
(`batch1-final/writer-round5/round5_only.diff`).

- **Step 3 now runs inside `try … except BaseException`.**
  - Every rename (each part moved aside, then the `.restoring` install) is recorded across all sports.
  - On any exception, including `KeyboardInterrupt` and the safety-copy clash `BackupError`, the new `_undo_swap` undoes
    the renames newest first.
  - It never overwrites: if the original name already exists, that step counts as failed.
- **Undo complete:**
  - the staged `.restoring` copies are removed and the folder is fsynced;
  - the **original exception is re-raised**;
  - if only that fsync fails, a `BackupError` chained to the original says the originals are back.
- **Undo incomplete:**
  - **nothing is deleted** and the folder is still fsynced;
  - a `BackupError` chained to the original names, per sport, the active path, every safety copy and the staged copy;
  - it lists the remaining renames as numbered steps (`N. move A to B`, never overwriting) and says the `.restoring`
    files are only copies of the backup.
  - Sample: `writer-round5/r5_sample_message.txt`.
- **Unchanged:**
  - validation, locking and staging (steps 1–2);
  - the round-4 unique safety-copy suffix and the refusal to overwrite a safety copy.
- **Docstring.** It now separates three cases:
  - steps 1–2 (a failure touches no store);
  - an **exception** in step 3 (rolled back automatically in the same process);
  - a **crash or power loss** in step 3 (not recoverable by the code).
  For a crash it lists what can be left per sport: `<name>.replaced-<suffix>` and its `-wal`/`-shm`, a `.restoring`
  file, and either no `<name>` or an already restored `<name>` whose sha256 equals the manifest's. It also gives the
  manual recovery both ways, put the original back or finish the restore, and warns that each sport in the same restore
  may be in either state.
- **Recovery is manual for a crash.** Nothing detects a half-finished swap on the next start. The restore command
  leaves the job locks to the caller: stop the jobs and the API first, as before.

## 4. Regression tests

New in `tests/test_backup_restore.py`. Failures are injected by patching `Path.rename` to raise `OSError(5)`. Fixture:
nba and nfl stores whose live main, `-wal` and `-shm` files have distinct bytes.

| Test | Stage | Old code | New code |
|---|---|---|---|
| `test_a_failed_swap_is_rolled_back_for_every_sport[nba-main/-wal/-shm/-install]` | first sport: main aside, `-wal` aside, `-shm` aside, install | 4 failed | 4 passed |
| `test_a_failed_swap_is_rolled_back_for_every_sport[nfl-main/-wal/-shm/-install]` | later sport, after nba was fully swapped (asserted from the recorded renames) | 4 failed | 4 passed |
| `test_a_failed_rollback_deletes_nothing_and_names_the_manual_recovery` | nfl install fails, then nba's rollback rename fails | 1 failed (raw `OSError`, no recovery list) | passed; it also performs the listed manual steps and checks all originals are back |

- **What the 8 parametrized cases assert:**
  - the original `OSError` propagates;
  - the folder (lock files aside) is exactly the original six files with their original bytes, with no `.replaced-*`
    or `.restoring` left;
  - the folder was fsynced after the rollback.
- **Before/after evidence:**
  - Writer: `writer-round5/before_R5.txt` (9 failed on a pre-round-5 copy), `before_R5_filestate.txt` (same, without
    the fsync assertion, failing on file state), `after_R5.txt` (9 passed).
  - Verifier, re-run independently: old tree 9 failed / 14 passed; final tree **23 passed**
    (`verifiers/backup-round5/16-old-vs-new-tests.log`).
- **Not covered by a test:**
  - the fsync failing after a successful rollback (handled in code only);
  - a real crash or power loss.

## 5. Independent review (verifier `a6656725`, read-only): APPROVE

Every probe asserted `app.__file__` under the batch1 tree. Logs are in `batch1-final/verifiers/backup-round5/`.

| # | Check | Result |
|---|---|---|
| 1 | New tests fail on old code, pass on new | passed (re-run by the verifier, log 16) |
| 2 | Own probes: each of the 8 renames of a clean nba+nfl restore failed in turn, once with `OSError` and once with `KeyboardInterrupt`; plus a forced safety-copy clash and a failure in `_describe` after the nfl install | passed: 16 of 16, plus both extra cases, ended with all six original files byte-identical and nothing left over (logs 17, 18) |
| 3 | Rollback failure: forward failure at renames 1–8 combined with a rollback failure at positions 1–5 (25 combinations reached the rollback) | passed: 25 of 25 lost nothing, named every `.replaced-*` path, and following the listed steps literally restored every original (log 18, sample in log 20) |
| 4 | `.restoring` removed only after a full rollback | passed |
| 5 | Unchanged behaviour: four-sport round trip (`check_db.py` ALL OK, byte-identical), second restore keeps safety copies, tamper refused, held lock refused with the other store untouched, 5 identity refusals plus the legacy nba case accepted, missing-store warnings | passed (log 19) |
| 6 | Docstring accuracy | passed, with one nit (R5-3) |

Findings, open and not fixed:

- **R5-1 (low).** In `backup.py` each aside rename and its `done.append(...)` record are two statements. An
  asynchronous signal landing between them would leave one rename unrecorded, so it would not be undone. The verifier
  could not inject into that window. Possible fix: record before renaming and have the undo skip entries whose source
  does not exist.
- **R5-2 (info).** If the final directory fsync after a fully successful swap raises, the caller sees an exception
  although the restore completed and was not rolled back.
- **R5-3 (nit).** The manual-recovery message prints paths unquoted, so a data folder whose path contains spaces needs
  quoting when typed into a shell.
- **Still open from the first backup review:** F2–F6 (O14–O16, O19 in `BATCH1_FINAL_REPORT.md` §9).

## 6. Focused tests and the final readiness gate

**Import check:** `cd ~/Desktop/nba-predictor-batch1/backend && OMP_NUM_THREADS=4
/Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python -c "import app; print(app.__file__)"` printed
`/Users/andry/Desktop/nba-predictor-batch1/backend/app/__init__.py` (`logs-round5/import_probe.txt`).

**Focused backend tests: passed.** 340 passed, 1 warning (Starlette deprecation), exit 0, 14:30–14:31Z.
`logs-round5/focused_backend.txt`.

- cwd: `/Users/andry/Desktop/nba-predictor-batch1/backend`
- `OMP_NUM_THREADS=4 /Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_store_guard.py tests/test_release_guard.py tests/test_backup_restore.py tests/test_replay_same_store.py tests/test_storage.py tests/test_nfl.py tests/test_nfl_live.py tests/test_soccer.py tests/test_telegram.py tests/test_dev_script.py tests/test_live.py`

**Focused frontend tests: passed.** `npm run test:unit` in `~/Desktop/nba-predictor-batch1/frontend`: 10 of 10, exit 0
(`logs-round5/focused_frontend.txt`).

**The one new readiness gate: PASSED.**

| | |
|---|---|
| Working directory | `/Users/andry/Desktop/nba-predictor-batch1` |
| Command | `OMP_NUM_THREADS=4 /Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python scripts/check_project.py --ready` (the worktree has no `backend/.venv`; `check_project.py` runs pytest with cwd `ROOT/backend`, so the batch1 code is imported) |
| Start / end | 2026-11-08T14:31:13Z / 15:28:22Z |
| Code-tree hash before / after | `adacc372fa2ba7bda2a8728bfcbd601cbd2233a5` / `adacc372fa2ba7bda2a8728bfcbd601cbd2233a5` |
| Result | structure guard passed, typecheck and build OK, **1,737 passed, 0 failed**, 1 warning; "Automated readiness checks passed"; **EXIT=0** |
| Logs | `batch1-final/logs-round5/gate.log`, `gate_hash_before.txt`, `gate_hash_after.txt` |

- **Test count:** 1,737 = the previous gate's 1,728 collected (1,727 passed + 1 failed) + the 9 new restore tests.
- **The flaky test.** `test_research_agent.py::test_the_deadline_kills_the_agent_tree_and_the_trial_in_flight` passed in
  this run but is not fixed. Run alone earlier, it failed 2 of 5 times on the patched tree and 1 of 5 on the untouched
  base (`batch1-final/logs/flaky_test_isolated_runs.txt`). A later run of this gate can still fail on it. It is outside
  the patch.
- **Scope of the pass.** This is an engineering check of the code. It says nothing about model performance or source
  usage.

## 7. Integration

- **Final patch.** `batch1-final/batch1-final-r5.patch` (`git diff --binary` of the batch1 tree): 19 files, +1,506/−69.
  Diffstat: `batch1-final-r5.diffstat.txt`. Round 5 changed only 2 of the 19 files, and the list of files is the same as
  before.
- **Rebuild check.** A fresh export of the base index, with the patch applied, rebuilds the final tree byte for byte:
  `APPLY_OK`, `mismatches=0` (`logs-round5/patch_rebuild_check.txt`).
  - The 3 intent-to-add placeholders, which export as 0-byte files, were removed from the export first.
  - The first attempt aborted on them; the same log notes this.
- **Main-checkout check.** `git apply --check` against the main checkout succeeds. It was a dry run; nothing was applied.
- **Staged and unstaged state in batch1:**
  - staged = the untouched base, plus 3 intent-to-add entries;
  - unstaged = the 19 batch files;
  - no untracked files outside `docs/evidence`;
  - the round-5 writer did not stage anything.
- **Old evidence kept.** The earlier `batch1-final.patch` (`3c2ec4a…` tree) is kept unchanged for the record.

## 8. Main checkout and other sessions (observed, not caused by this task)

Compared at 15:28Z with the untouched base:

- **Code:** all files equal, except `backend/app/jobs.py` (modified 09:26 ET) and `backend/app/telegram_card.py`
  (09:24 ET), which carry ticket and coverage work by another session.
- **New untracked files:** `backend/app/tickets.py`, `backend/tests/test_tickets.py` and `docs/TICKETS.md`, from the
  Codex ticket workflow.
- **Markdown:** `HANDOFF.md` and `docs/SMOKE_TEST.md` gained Codex entries: hourly ticket preflight, and "Ticket workflow
  upgrade — Nov 8". `docs/SOCCER_PLAN.md` is unchanged, as is `BATCH1_FINAL_REPORT.md` (last written 02:20 ET).
- **No overlap with the patch:** none of these files is among the 19 patch files.
- **Telegram:** because Telegram imports the main checkout's `backend` every 60 s, those other sessions' edits are what
  it runs. This task did not touch them.

## 9. Disclosures

- **The writer's script slip.** The writer's one-off message script (`r5_msg.py`) first imported `app` from the main
  checkout, because the script folder came first on `sys.path`. It failed inside `backup.run` on a temporary folder
  under the scratchpad before writing anything, and was re-run with the batch1 path first and an assert. The main
  checkout's code still equals the base apart from the two files in §8, which the writer never imported or edited.
- **Briefs and reviewers.** I wrote the briefs for the writer and the verifier but authored none of the round-5 code;
  review came from the separate verifier.
- **Evidence the verifier did not use.** It did not rely on the writer's logs and re-ran everything itself.
- **Browser evidence not repeated.** Round 5 changed no frontend file, so the browser evidence in
  `BATCH1_FINAL_REPORT.md` §7 still applies to the same frontend. Its limits are unchanged: 9 pages × 7 widths, and the
  API server started before the guard backend was merged.
- **Secrets.** A scan of the new evidence folders (`writer-round5`, `verifiers/backup-round5`, `logs-round5`) for
  API-key, token, secret, password and bot-token patterns found nothing.

## 10. Not run

- a real crash, power loss or process kill during the swap;
- the signal window in R5-1;
- the fsync failing after a rollback;
- paths containing spaces;
- any restore or backup of live stores;
- a browser re-run;
- the full suite apart from the one gate;
- any live, launchd, Telegram, network, deployment, commit or push step.

## 11. Locations

All paths are relative to `docs/evidence/all-sport-audit-2026-11-07/remediation/`.

- `RESTORE_FAILURE_ADDENDUM.md` (this file)
- `batch1-final/batch1-final-r5.patch`, `batch1-final/batch1-final-r5.diffstat.txt`
- `batch1-final/writer-round5/`
- `batch1-final/verifiers/backup-round5/`
- `batch1-final/logs-round5/`

The final tree is the working tree of `~/Desktop/nba-predictor-batch1`.

## 12. Ready for review vs applied

- **Ready for review and landing:** the final tree `adacc372…` / `batch1-final-r5.patch`, reviewed with the gate passed.
- **Applied:** nowhere. The main checkout, live stores, launchd and Telegram are unchanged by this task.
- **Still your decisions:**
  - **Landing:** move Telegram onto a protected release setup first, or name a window to apply the patch.
  - **Live NFL backup:** go or no go.
  - **adaptive-train:** it needs `NBA_RELEASE_GUARD`/`NBA_RELEASE_ROOT` before it is re-enabled.
  - **The flaky deadline test:** whether to stabilise it, separately from this batch.
  - **Earlier D1** (what to do about the failed gate) is superseded by this gate run.
