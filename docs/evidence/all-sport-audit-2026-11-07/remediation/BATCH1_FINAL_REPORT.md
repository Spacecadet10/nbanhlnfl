# Batch 1: final reviewed report (2026-11-08, Claude Opus 5.5 lead)

This is a new file. No existing Markdown file was edited for this task: not HANDOFF.md, not docs/SMOKE_TEST.md, not
`../BATCH1-FIX-REPORT.md`, nor any other report. The user said this overrides the normal HANDOFF update.

This report supersedes `../BATCH1-FIX-REPORT.md` where the two differ. §11 lists the statements in that report that were
wrong or incomplete.

Evidence paths are relative to `docs/evidence/all-sport-audit-2026-11-07/remediation/batch1-final/` unless they start with
`/` or `~`.

## 0. Bottom line
- **The batch is finished and reviewed. It is not applied anywhere.**
  - Where it lives: the integration worktree `~/Desktop/nba-predictor-batch1` and the saved patch `batch1-final.patch`
    (19 files, +1338/−57).
  - Nothing was applied to the main checkout `~/Desktop/nba-predictor-sprint1`, to `~/nba-predictor-live`, to any live
    store, or to any live service.
- **The final independent reviews all approved:**
  - Live-store guard: APPROVE, after 4 rounds.
  - Backup: APPROVE.
  - Integration cross-check: consistent.
  - This round's verification found 4 more defects. One implementation writer fixed them test-first, and the verifiers
    that found them re-verified the fixes. Nothing reported is left unresolved. Remaining items are recorded in §9.
- **The final readiness gate FAILED.**
  - Run: once, on the stable final tree.
  - Result: exit 1, 1727 passed and 1 failed. The code-tree hash was unchanged during the run.
  - Failing test: `test_research_agent.py::test_the_deadline_kills_the_agent_tree_and_the_trial_in_flight`. Neither that
    test nor its code is in the patch.
  - Isolated runs show it fails intermittently on both trees: 2 of 5 runs on the patched tree, 1 of 5 on the untouched
    base. So this is an existing timing-dependent failure, not one caused by the patch (§5.3).
  - The gate was **not** re-run, because the brief asked for exactly one run. A clean gate result for this tree therefore
    does not exist yet; that is decision D1.
- **Passing checks say nothing about model quality, betting edge, or source permissions.** Those audit findings are
  untouched (§8).

## 1. Tree identity
| What | Identity |
|---|---|
| Commit both trees sit on | `79ff6ecb134b83e623c73ebe4a224bd38d7f7f67` (branch nfl-dev HEAD) |
| **Untouched base** | The **index** of `~/Desktop/nba-predictor-batch1`: HEAD plus the main checkout's uncommitted diff and untracked files, copied in on 2026-11-07 at about 18:20 ET. It also holds 3 empty intent-to-add entries for the new test files. The writer worktrees `-w1`, `-w2` and `-w3` have byte-identical indexes, minus those 3 entries (`logs/integration_check.txt`). |
| Base vs main checkout now | All 350 non-doc files in the base are byte-identical to the main checkout's working files (checked 2026-11-08 07:18Z). The only exceptions are the 3 new test files, which exist only in the worktree. No main-checkout code changed since the base was taken (`logs/base_vs_main_recheck.txt`). |
| **Final integration tree** | The **working tree** of `~/Desktop/nba-predictor-batch1` = base + the patch, as unstaged changes. Nothing is committed and nothing is staged by any agent. |
| Final code-tree hash | `3c2ec4a6023b2602e160bdae0d701d76aa19529b`, run in the worktree with `{ git diff -- . ':(exclude)HANDOFF.md'; git ls-files -o --exclude-standard \| sort \| xargs shasum; } \| shasum`. It was the same before the gate (06:23:00Z), after the gate (07:04:53Z) and at 07:18Z. There are 0 untracked files. |
| Pre-round-4 tree | Identical to `../batch1/batch1.patch`, ignoring `index` lines (checked). That was the tree gated on 2026-11-08 at 00:51Z. |

## 2. Integration verification
### 2.1 Every changed file and where it came from
Each file below is byte-equal to its writer's final file. The independent verifier also rebuilt the base from the index,
applied the patch, and got all 19 files back identical (`verifiers/integration/check3*.txt`).

| File | Writer | Round-4 change |
|---|---|---|
| backend/app/cli.py | W1, then the round-4 writer | yes (N-B, N-C) |
| backend/app/config.py | W1, then round 4 | yes (N-A) |
| backend/app/release.py | W1, then round 4 | yes (N-A) |
| backend/app/db.py, ingest.py, raw_cache.py, soccer.py | W1 | no |
| backend/tests/conftest.py | W1 | no |
| backend/tests/test_live_store_guard.py (new) | W1, then round 4 | yes (+9 cases) |
| backend/app/backup.py | W2, then round 4 | yes (F1) |
| backend/tests/test_backup_restore.py | W2, then round 4 | yes (+1 test) |
| frontend/package.json; components Canonical.tsx, Layout.tsx, MarketView.tsx; pages HistoryPage.tsx, NflPages.tsx | W3 | no |
| frontend/src/components/tables.test.mjs, nflWording.test.mjs (new) | W3 | no |

Approved patches present:
- W1: 9 of 9 files. W2: 2 of 2. W3: 8 of 8.
- Round 4: exactly the 6 allowed files. A per-file diff against the pre-round tree showed no other file changed.

### 2.2 What "their files match the original base" meant
That status line referred to a no-clobber check that ran **before** copying. For each file, the integration copy was
compared with the base index (`cmp -s <(git show :$f) $B/$f || echo "base changed"`), and only then was the writer's file
copied over it. The check proved that nothing else had already changed those files. It did **not** say the patches matched
the base.

The patches were not dropped:
- All 19 integration files differ from the base and equal the writers' final files.
- Rebuilding from the base plus the patch reproduces the tree.

### 2.3 Staging
The earlier claim that "W3 staged its own edits" was wrong:
- All writer indexes equal the base index.
- `MM` meant two things at once: the index differs from HEAD (it holds the main checkout's uncommitted diff as the base),
  and the working tree differs from the index (the writer's edit on top).
- No agent ran `git add`, and no writer edit is hidden in an index.

The 3 intent-to-add entries were made by the lead, so that `git diff` includes the new files in the patch.

### 2.4 Which code the imports and tests load
- **The trap.** The shared venv (`~/Desktop/nba-predictor-sprint1/backend/.venv`) has an editable install of the **main
  checkout**. `python -c 'import app'` from the worktree root, or a script run by path, loads the main checkout's code
  (shown by the verifier).
- **The correct way.** From `~/Desktop/nba-predictor-batch1/backend`, `python -m pytest` loads the worktree.
  - A probe test run that way printed the worktree path for `app`, `app.cli`, `app.config`, `app.backup` and `app.db`
    (`logs/import_probe.txt`).
  - `scripts/check_project.py` runs `[sys.executable, '-m', 'pytest', '-q']` with `cwd = <its own checkout>/backend`, so
    the gate tests the worktree's code.
  - Writers and verifiers asserted `app.__file__` before every run.
- **Frontend.** `frontend/node_modules` is a symlink to the main checkout's `node_modules`, so only dependencies come from
  there. `tsc` (include `src`, `vite.config.ts`), `vite build` and `test:unit` compile the worktree's sources. The build
  output is the worktree's `frontend/dist` (ignored).

### 2.5 Two patch files and an earlier hash
- **Gated vs final patch.** The scratch `batch1-gated.patch` (16 files) was a plain `git diff` taken before the
  intent-to-add entries existed. `../batch1/batch1.patch` (19 files) adds only the 3 new test files; the other 16 files'
  hunks are identical.
- **The hash `a50b992…`** recorded by the first gate does not reproduce from the current index. A copy of the index
  without the 3 intent-to-add entries does reproduce it (`logs/hash_explained.txt`). So the difference comes from how the
  3 new files were represented (untracked vs intent-to-add), not from different content.

## 3. Changes and regression tests
Every new test was first shown failing on the code before its fix. Outputs are in `../batch1/logs/w1|w2|w3/` for rounds
1–3 and in `writer-round4/before_*.txt` / `after_*.txt` for round 4.

### 3.1 H1: development commands could write to or migrate live stores
Files: `config.py`, `release.py`, `db.py`, `cli.py`, `conftest.py`.

**Which folders are protected:**
- this checkout's `data/`, or any folder under a main git checkout (a `.git` *directory*);
- a path whose real location is there, including symlinks;
- any spelling of such a path: case variants and the `/System/Volumes/Data` form, matched by file identity since round 4;
- a store file with more than one hard link.

**What is refused, and when:**
- Every store-writing, store-creating or migrating command whose data dir, store, `raw/`, lock or `research/` is
  protected gets `{"status":"refused"}`, exit 1.
- The refusal comes before any lock, connection or file.
- It covers:
  - the generic path, `nfl` writes (including `tick`/`refresh`, through the activation and access-log paths), `soccer`;
  - `tuning-study`, `deep-study`, and `adaptive-train` (except `--dry-run`);
  - `nfl activate`, `nfl test-run` and `nfl collect-lines --test`.

**The only exception: the release environment.**
- It requires `NBA_RELEASE_GUARD=1`, a clean checkout at `NBA_RELEASE_ROOT`, and `NBA_DATA_DIR` set.
- That checkout must not be the one holding the data folder, compared by `(st_dev, st_ino)` since round 4.
- Migration also needs the explicit `migrate` command.
- `release.check()` is unchanged.

**Second line of defence:** `db.migrate` raises `ProtectedStoreError` on a protected file with pending migrations outside
an explicit release migration. The CLI turns it into a structured refusal.

**Tests:**
- An autouse fixture gives every test a temporary `NBA_DATA_DIR`.
- `test_live_store_guard.py` has 30 functions / 49 cases, covering:
  - implicit commands never migrating;
  - an explicit live `NBA_DATA_DIR`;
  - `migrate` outside a release;
  - no file creation;
  - same-schema writes (10 commands);
  - read-only commands still working;
  - release migration allowed;
  - a guard-less "release" refused;
  - copies still migrating;
  - the backstop, and in-memory databases exempt;
  - links: symlinked store and `raw/`, the backstop through a symlink, hard links;
  - NFL: `activate`, `test-run` and `collect-lines --test`;
  - the "checkout holds the data folder" check under 4 spellings, and the checkout's own data folder under another
    spelling;
  - a linked `research/` for `nfl tick` and `nfl refresh`.

### 3.2 Replay checks raw files before writing (C4/I9)
Files: `cli.py`, `ingest.py`, `soccer.py`, `raw_cache.py`.

**What it does:**
- Before the lock, the migration and any replay transaction, a read-only (`mode=ro`) preflight checks exactly the raw files
  replay will read.
- If any are missing: exit 1, `missing_raw` (the count and up to 20 ids), with a pointer to `release-check` /
  `nfl release-check`.
- Any database read error is a refusal ("could not read the store"), never a pass.
- An existing 0-byte store is refused (round 4).
- A store file that does not exist still produces a fresh empty store, as before.
- `release-check` and the new read-only `nfl release-check` report `raw_missing`. `release.check()` does not scan.

**Tests:**
- Replay refusal for nba/nfl/soccer × schemas {31, 33, shipped−1, shipped}, with every table dump unchanged;
- every raw file present still runs;
- an unlogged NHL file is not missing;
- a missing team-box file (sportsdataverse, espn_summary) is refused;
- an unreadable store fails closed;
- `nfl release-check` without a cache log;
- release-check lists the missing files;
- a 0-byte store is refused (replay, nfl, soccer).

### 3.3 H3: backup and restore covered only NBA and NHL
File: `backup.py`.

**What changed:**
- Stores covered: nba, nhl, nfl and soccer (from `app.sports`). A default run backs up every store present.
- Absent stores: listed in `skipped`, with one stderr `WARNING:` each. A named missing store is an error and creates no
  folder.
- Restore checks:
  - NFL and soccer restores require the store's identity;
  - duplicate `--sport` names are deduplicated;
  - COUNTED gained 4 tables.
- Safety copies (round 4): a restore's safety copy can no longer be overwritten. The name carries microseconds plus a
  counter, and an existing name raises instead of being replaced.

**Tests:** `test_backup_restore.py` has 14 tests, 10 of them new.

### 3.4 M14: History tables overflowed at 581–710 px
Each `table.history` (in Canonical.tsx, HistoryPage.tsx and MarketView.tsx) is wrapped in `div.table-wrap`. There is no CSS
change.

Tests: `tables.test.mjs` (3 tests).

### 3.5 M11, frontend part: the NFL "data only" text
- The NFL text now follows the API's `forecasts` capability.
- With forecasts active, it describes the live forecast procedure and makes no accuracy, edge or performance claim.
- Tests: `nflWording.test.mjs` (4 tests, order-sensitive).

## 4. Independent review findings and resolution
All reviewers were separate read-only agents, and none of them wrote code. The writers were W1, W2 and W3 in rounds 1–3,
and one round-4 writer.

| Round | Reviewer | Finding | Severity | Resolution | Re-verified by |
|---|---|---|---|---|---|
| 1 | guard (security) | H-1 `nfl activate` unguarded; could replace the live activation file and silently stop NFL forecasts | High | refused outside release env | guard r2; final verifier item 1 |
| 1 | guard | M-1 store symlinked into the live folder bypassed the guard | Med | resolve real paths (`protected_path`) | r2; final item 2 (13 alias spellings) |
| 1 | guard | M-2 dev checkout could pose as a release through environment variables | Med | `migration_context` refuses the checkout that holds the data folder | r2; final item 3 |
| 1 | guard | M-3 `nfl test-run` / `collect-lines --test` appended to the live one-shot access log | Med | refused | r2; final item 4 |
| 1 | preflight (code) | fail-open on sqlite errors | Med | fail closed with a structured refusal | final item 5 |
| 1 | preflight | `mode=ro` connections | Low | done | final item 6 |
| 1 | preflight | `nfl release-check` crash without raw tables | Low | `raw_missing: null` plus reason | final item 7 |
| 1 | preflight | refusal message should point to the full list | Low | done | final item 8 |
| 1 | preflight | more test cases (schemas, NHL false positive, team-box) | Low | added | final item 9 |
| 1 | backup (code) | silent skip, identity on restore, duplicate sports, test gaps | Med/Low | done | final backup verification |
| 1 | frontend (code) | NFL text overstated when a bookmaker line is used; test gaps | Med/Low | corrected; mutation-tested | — (not re-reviewed this round; unchanged since) |
| 2 | guard | **APPROVE**; new N-1: hard-linked store bypass | Low/Med | `st_nlink > 1` → protected (round 3) | final item 10 (first independent check of this fix) |
| 4 | final guard verifier | **N-A** "checkout holds the data folder" beaten by a case variant or `/System/Volumes/Data` spelling; a real 33→35 migration was reproduced | Med | file-identity comparison in `release.py` and `config.py` | guard round 4: 20 spellings × 2 cwd forms refused; release env still migrates |
| 4 | final guard verifier | **N-B** `research/` symlinked into the live folder let `nfl tick` append to the live access log | Low/Med | `research/` added to the guard targets; nfl write guard passes the activation and access-log paths | round 4: refused for tick, refresh, migrate, ingest and 4 link variants |
| 4 | final guard verifier | **N-C** a 0-byte store passed the replay preflight and was migrated | Low | refused | round 4: nba, nhl, nfl and soccer |
| 4 | backup verifier | **F1** two restores in the same second overwrote the first safety copy | Med | unique suffix (microseconds plus counter); refuse if the name exists | round 4: real clock, frozen clock, and with `-wal`/`-shm` parts |

**Final verdicts:**
- Guard: **APPROVE** (`verifiers/guard-round4/`). It reproduced all 10 fixes, then N-A, N-B and N-C.
- Backup: **APPROVE** (`verifiers/backup-round4/`).
- Integration: consistent, with the hash and staging explanations in §2.

**Legitimate operations the guard verifier showed still work:**
- in a separate clean release clone:
  - explicit `migrate`: nhl 31→35, nfl 33→35, nba 31→35;
  - guarded `score`, `nfl tick` and `nfl refresh` allowed at the shipped schema (network stubbed);
  - implicit commands still never migrate;
- dev copies, with and without `research/`, still write and migrate;
- read-only commands on a protected folder (`nfl release-check`, `coverage`, `thresholds`) and the API's `/api/health`;
- the new fail-closed branches cause no false refusal when the data folder does not exist yet.

**Not shown in a release environment:** `nfl activate`, `test-run` and `collect-lines --test` are not refused by the guard
there. They then failed for reasons unrelated to it (dummy seal: `TestSeasonsLocked`, digest mismatch).

## 5. Integrated tests and the final readiness gate
### 5.1 Focused integrated tests (final tree, before the gate)
| Run | Command (cwd) | Result |
|---|---|---|
| Lead, pre-round-4 tree | `OMP_NUM_THREADS=4 /Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_store_guard.py tests/test_release_guard.py tests/test_backup_restore.py tests/test_replay_same_store.py tests/test_storage.py tests/test_nfl.py tests/test_nfl_live.py tests/test_soccer.py tests/test_telegram.py tests/test_dev_script.py tests/test_live.py` (`~/Desktop/nba-predictor-batch1/backend`) | passed: 321, 1 warning (`logs/focused_backend.txt`) |
| Round-4 writer, final tree | the same command | passed: 331, 1 warning (`writer-round4/focused_after.txt`) |
| Guard verifier, final tree | guard, release, telegram, dev_script, replay, storage and nfl_live tests | passed: 239 (`verifiers/guard-round4/pytest.txt`) |
| Backup verifier, final tree | `tests/test_backup_restore.py` | passed: 14 |
| Frontend unit, final tree (frontend unchanged since) | `npm run test:unit` (`~/Desktop/nba-predictor-batch1/frontend`) | passed: 10/10 (`logs/focused_frontend.txt`) |

### 5.2 The one final gate
| Field | Value |
|---|---|
| Working directory | `/Users/andry/Desktop/nba-predictor-batch1` |
| Exact command | `OMP_NUM_THREADS=4 /Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python scripts/check_project.py --ready` |
| Note on the command | The brief's `backend/.venv/bin/python` does not exist in the worktree. The same venv was used by absolute path; `scripts/check_project.py` is the worktree's own copy. `OMP_NUM_THREADS=4` is the standing local setting. |
| Start / end | 2026-11-08T06:23:05Z / 07:04:46Z (24 min 34 s of pytest) |
| Code-tree hash before / after | `3c2ec4a6023b2602e160bdae0d701d76aa19529b` / `3c2ec4a6023b2602e160bdae0d701d76aa19529b` |
| Log | `logs/gate.log`; hashes in `logs/gate_hash_before.txt` and `logs/gate_hash_after.txt` |
| Structure guard | passed |
| Frontend typecheck and build | passed (the 550 kB chunk-size warning noted before is printed again) |
| Backend tests | **1 failed, 1727 passed**, 1 warning (Starlette/httpx deprecation) |
| Exit code | **1 (FAILED)** |

The total of 1728 = 1669 (the audit gate) + 59 new tests (49 guard cases + 10 backup tests).

The gate does not run the frontend unit tests (open item O1). They were run separately: 10/10.

### 5.3 The failing test
- **Which test:** `tests/test_research_agent.py::test_the_deadline_kills_the_agent_tree_and_the_trial_in_flight`.
  - It asserts that a trial killed by a 15-second run deadline is recorded as `timeout`.
  - In the gate it was recorded as `interrupted`, which is a race between the run deadline and the trial's own limit.
- **Not part of the patch:** neither `app/research_agent.py` nor that test file is changed by it. The earlier gates (1669
  and 1718) passed this test.
- **Isolated runs** (`logs/flaky_test_isolated_runs.txt`), each from its own `backend/` with the import location printed:

  | Tree | That test alone, 5 runs | Whole file |
  |---|---|---|
  | final patched tree | failed 2 of 5 (runs 2 and 5) | 46/46 |
  | untouched base (exported from the index to scratch) | failed 1 of 5 (run 3) | 46/46 |

- **Conclusion:** this is an existing timing-dependent test. It also fails on the base, and nothing here indicates the
  patch caused it.
- **Still, the gate did not pass.** The brief allowed one gate, so it was not re-run, and no clean gate result exists for
  this tree. Options are in D1.
- **Not done:** no attempt to fix or stabilise the test. It is outside this batch.

## 6. Backup validation on isolated fixtures
Done by the backup verifier, read-only, with all stores and outputs under the session scratchpad and imports asserted
(`verifiers/backup/`, `verifiers/backup-round4/`).

| Check | Result |
|---|---|
| Coverage | **Held.** Fixture stores for nba, nhl, nfl and soccer were fully migrated to 35 with FK-linked rows. A default backup gave `skipped=[]`, with all 4 stores in the output and the manifest. |
| Missing-store warnings | **Held.** With nhl and soccer removed: exit 0, `skipped=['nhl','soccer']`, one `WARNING:` per sport. A named missing store gives exit 1 and no folder. An empty or nonexistent data dir gives an error and no folder. |
| Database integrity | **Held** for all 4 stores: `integrity_check` ok; `foreign_key_check` empty (as in the source); schema SQL, user_version and identity row equal to the source; manifest counts equal to the actual counts; a full table dump equal to the source. |
| Manifest and hash | **Held.** Recomputed sha256 and sizes match, and the manifest lists exactly the files present. A flipped byte in a store, or an edited manifest sha256, makes restore refuse. A multi-sport restore with one bad file restores nothing. |
| Restore (existing path: `python -m app.backup --restore <dir> --sport …`) | **Held.** A four-sport round trip into a fresh dir is byte-identical. Refused: identity mismatches (nba as nfl, nfl as nba/soccer), nfl or soccer without identity, a held lock (separate process; the other store unchanged). A legacy nba store without identity is accepted, as intended. The replaced store is kept as `.replaced-<stamp>`, and since round 4 never overwritten. |
| Source untouched | **Held** for DELETE-journal sources (sha256 equal before and after). WAL sources: main-file bytes unchanged, but see O15. |
| Tests | 14/14 passed. |

What was not done:
- no live store was backed up, opened or touched;
- no backup of the live NFL store was made (that needs the user's go);
- no crash was simulated during the restore swap.

## 7. Browser evidence (retained; not re-run)
- **Evidence:** `../batch1/logs/browser_RESULTS.txt`, run on 2026-11-07 from 19:07 ET.
- **Frontend:** vite served `~/Desktop/nba-predictor-batch1/frontend`. All 8 changed frontend files (6 sources and 2 tests)
  had been written at 19:00:55 ET and are byte-identical now. So the browser exercised **the integrated, final frontend**.
- **Backend (disclosure):** the API (uvicorn from `~/Desktop/nba-predictor-batch1/backend`) ran **before** W1's guard
  files were merged (19:26:54 ET) and before round 4.
  - Round 4 changed only `cli.py`, `config.py`, `release.py` and `backup.py`.
  - W1 also changed `db.py`, `ingest.py`, `raw_cache.py` and `soccer.py`.
  - None of these changes alter the API's read path. The API never migrates, and `db.py`'s backstop and `store_lock` are
    not used by the API.
  - That is a code reading, not a re-run. The stores were copies in the session scratchpad.
- **Pages tested at every width.** Each page was loaded at each of 390, 600, 640, 700, 760, 800 and 1440 px, giving 63
  loads:
  - `/history` (NBA), `/history?sport=nhl`, `/history?sport=nfl`;
  - `/performance` (NBA), `?sport=nhl`, `?sport=nfl`;
  - `/history?run=live` (NBA), `/history?sport=nhl&run=live`;
  - `/?sport=nfl` (NFL Game Day).
- **What was measured:**
  - Method: a same-origin iframe of exact width, waiting until nothing showed "Loading".
  - Measured: `scrollWidth` against `innerWidth`, API errors, and the parent of every `table.history`.
  - Result: 63/63 with no sideways scroll, 0 still loading, 0 API errors, 0 unwrapped history tables.
- **Control** (wrapper removed in the same load): `/history` NBA and NHL at 600 and 700 px went back to the old 710 px
  overflow. NFL history stayed 600/700 either way.
- **Wording check, 1440 px only:** on `/?sport=nfl` and `/daily-learning?sport=nfl`, "data only" is absent and the
  active-forecast text is present. `/history?sport=nhl` was a contrast page.
- **Not tested in the browser, and not claimed:**
  - every other page and flow (market pages that render the `MarketView.tsx` table, Daily Learning layout for NBA/NHL,
    soccer pages, game detail, selection);
  - widths other than the seven above;
  - interaction (sorting, filters, paging);
  - a production build served by the live web.
  - The `MarketView.tsx` and `Canonical.tsx` wrapping is covered by the unit test, not by a browser load, except where
    those components render inside the 9 pages above.

## 8. Audit findings outside this batch (still open)
| Finding | Status | Next step / approval |
|---|---|---|
| H2 Telegram runs unguarded dev code | open | plan item 14 (move token/ledger out, guard, move job): release and launchd go |
| H3 295 NFL raw files missing | open; not found in the searched locations; no re-fetch proposed (a re-fetch cannot recreate the original bytes) | live NFL backup: user go |
| H4 reused seasons shown as "held up"/"strong" | open | item 7b disclosure: user go (product rule and Telegram text) |
| H5 dev line lacks the live releases | open | item 13 merge: commit/merge request |
| M2 forecast time = record time | open; never backdate | item 8 (after 13): user go |
| M3 missed windows are silent | open | item 5 (after 13) |
| M4 NFL quote ignores price_label; M5 NFL definition hash too broad | open | item 9 (NFL-hashed; re-activation): user go |
| M6 one-shot guards per seal/dir | open | item 10: user go |
| M7 one bad final aborts an import | open | next batch (item 7) |
| M8 replay behaviours; 62 NHL shootouts stored as overtime | partly done (replay refuses on missing or empty input); OT/SO design open | after 13 |
| M9 database sport triggers | deferred | item 15: user go |
| M10 NHL OT/SO hidden in the UI | open | item 4 (after 13) |
| M11 backend text (`coverage.py:588`, `nfl.py:68`) | open (frontend part done) | item 9 / wording batch |
| M12 API describes jobs from its own environment | open | item 12 |
| M13 no dataset/code identity on rows | open | item 11 (migration 0036): user go |
| M15/M16 soccer stale labels and S1 independence | deferred; S0/S1 acceptance stays conditional | — |
| M17 source terms | open, policy. No source has been declared acceptable. | item 18: user decision |
| C6 host sleep gaps | open | item 19: user go |

## 9. Open items from this batch (recorded, not dropped)
O1–O12 are carried over from `../BATCH1-FIX-REPORT.md` §8, updated where noted. O13 onwards are new this round.

- **O1.** The gate does not run `npm run test:unit`.
- **O2.** Commands that write research files (not stores) into a protected `data/` remain unguarded: `nhl-player-inputs`,
  `odds-history`, `historical-start`, `pool-fit-deep`, non-test `nfl collect-lines`, `nfl evaluate`, and report-writing
  studies.
  - Related, from round 4 (LOW): a symlink nested *inside* `research/`, such as `research/adaptive → live`, is not caught
    for non-NFL commands. NFL is covered through its explicit paths.
  - Decide which of these may target `data/`.
- **O3.** NFL wording is still wrong in the backend (`nfl.py:68`, `coverage.py:588`), and stale "data-only" comments
  remain.
- **O4.** The "NFL Elo model" text matches the current seal only.
- **O5.** The replay preflight runs outside the store lock.
- **O6.** The preflight checks that files exist; it does not decompress or hash them.
- **O7.** By spec: a separate clean clone with the three release variables counts as a release environment. The guard
  stops accidents, not deliberate misuse.
- **O8.** Existing behaviour:
  - `NBA_RELEASE_GUARD=1` outside a release can create `-wal`/`-shm` beside a WAL store (`release.store_schema`);
  - guarded refusals write one failed `job_runs` row.
- **O9.** Fail-safe breadth: anything under a main checkout's root is protected.
- **O10.** NBA/NHL restores still accept a store without identity (legacy).
- **O11.** Not tested on the real release checkouts. Merge conflicts with the release lines remain (cli.py).
- **O12.** Vite 550 kB chunk warning.
- **O13 (new).** The gate's flaky `test_research_agent` deadline test (§5.3).
- **O14 (new, backup F2, low).** A restore does not check the identity of the store it replaces. The old file is kept as
  `.replaced-*`.
- **O15 (new, backup F3, low).** Backing up a WAL-mode source leaves `-shm` and an empty `-wal` beside it (main bytes
  unchanged). The backup copy keeps `journal_mode=wal`. The docstring says "reads only".
- **O16 (new, backup F4/F5, low).** CLI errors print a traceback (exit 1). The manifest `rows` counts are informational
  and not checked on restore.
- **O17 (new, low).** In a dev copy where `research` is a plain file, `nfl tick` crashes with `NotADirectoryError`. Nothing
  is written to the live folder.
- **O18 (new, info).** A soccer replay refusal lists 20 ids, but soccer has no `release-check` that gives the full list.
- **O19 (new, info).** Safety-copy names now carry microseconds and an optional `-N`. Anything matching the old
  `…Z` pattern literally must be updated.
- **O20 (new, info).** In the release environment, `nfl activate` and `collect-lines` raise an uncaught `TestSeasonsLocked`
  on a bad seal. This existed before and is unrelated to the guard.

## 10. Locations
- **Final integration worktree:** `~/Desktop/nba-predictor-batch1`. Final tree = its working tree; base = its index.
  Code-tree hash `3c2ec4a6…`.
- **Writer worktrees:** `~/Desktop/nba-predictor-batch1-w1`, `-w2` and `-w3`. They are left in place and hold the
  round-1–3 writer copies (round 4 was done in the integration worktree). Remove them with `git worktree remove` after
  landing or dropping.
- **Final patch:** `batch1-final/batch1-final.patch`, a `git diff --binary` of the final tree against the base, 19 files.
  - File list: `batch1-final/batch1-final.diffstat.txt`.
  - `git apply --check` against the main checkout passes (check only; nothing applied).
- **Round-4-only diff:** `batch1-final/writer-round4/round4_only.diff` (6 files, +159/−16).
  `writer-round4/pre_round4.diff` = the earlier `../batch1/batch1.patch`.
- **Logs:**
  - `batch1-final/logs/`: gate, hashes, focused runs, import probe, integration check, hash explanation, flaky-test runs,
    base recheck, and the round-1/2 guard verdict texts;
  - `batch1-final/verifiers/`: guard and guard-round4, backup and backup-round4, integration (scripts and outputs);
  - earlier logs are in `../batch1/logs/`.
- **Secrets scan:** the evidence folder holds no credentials or delivery ledgers. A pattern scan for keys and tokens found
  only the word "telegram" in a CLI usage line.

## 11. Ready for review vs applied
| Item | State |
|---|---|
| Batch 1 code (19 files) | **Ready for review**, in the worktree and the patch. **Not applied** to the main checkout, `~/nba-predictor-live`, or any release checkout. |
| Final readiness gate | **Failed** (1 flaky failure; §5.3). Not re-run. |
| Live stores and services | **Untouched.** No live store was opened for writing, backed up or migrated. Hard-link counts of `data/*.sqlite`: 1, 1, 1 (metadata only). No launchd change, Telegram message, network fetch, training, deployment, commit or push. |
| Frontend | Visible only after a web release (the live web is a55c874). |
| Landing | Needs a go. Telegram imports the main checkout's `backend/` every 60 s, so applying the patch there changes live Telegram code at once (plan rule K1). See D2. |

**Corrections to `../BATCH1-FIX-REPORT.md`:**
1. It said the guard's final verdict was APPROVE. That was round 2. The round-3 hard-link fix had not been reviewed
   independently until this round. This round also found N-A, N-B and N-C, now fixed.
2. "W3 staged its own edits": wrong (§2.3).
3. "1718 passed" and `a50b992…` describe the pre-round-4 tree. The final tree is `3c2ec4a…`, and its gate result is in
   §5.2.
4. The browser check ran before W1's backend files were merged (§7).

## 12. Process disclosures
- **HANDOFF.md** in the main checkout changed during this task (2026-11-08, 00:07–02:06 ET). The changes are hourly
  "Ticket preflight" entries from a separate Codex job, not from this session. SMOKE_TEST.md and SOCCER_PLAN.md are
  unchanged since the base.
- **A shell-variable mistake** (`path` in zsh overwrote `PATH`) broke one of my scratch export commands. It wrote nothing
  outside the scratchpad and was redone in bash.
- **Verifiers' pytest runs** may have refreshed ignored `__pycache__` folders in the worktree. `git status` shows no new
  untracked files, and the code-tree hash is unchanged.
- **Vite's dependency cache** in the main checkout's `node_modules/.vite`, from the 2026-11-07 browser run, is still there
  (ignored build cache).
- **Agents used:** 3 read-only verifiers (guard, backup, integration), each re-used for its own re-verification, and 1
  implementation writer for round 4. No other writer was started.

## 13. Decisions for the user
- **D1. The failed gate.** Choose one:
  - (a) authorise one more gate run on the same tree `3c2ec4a…`;
  - (b) accept the result as is, with the flaky test documented (O13);
  - (c) first stabilise that test as its own small task, then gate.
  - The batch should not be called gate-passed until one of these is done.
- **D2. Landing.** Choose one: move Telegram to a guarded release checkout first (plan item 14), or name a landing window
  for this patch.
- **D3. Live NFL backup.** Run it with the new `app.backup --sport nfl` after landing, or with the stdlib script in
  `preserved/plan-C-scripts/` now.
- **D4. adaptive-train plist.** Before it is re-enabled after landing, it needs `NBA_RELEASE_GUARD`/`NBA_RELEASE_ROOT`.
  That is a launchd change, and it stays paused.
- **D5. Next batch.** Not started, as instructed.
