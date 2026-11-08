# Batch 1 fixes — full report (2026-11-07, Claude Opus 5.5 lead, multi-agent)

New file. No other .md file was changed for this batch. HANDOFF.md was deliberately not updated, because the user asked for
no edits to existing .md files. Everything that would normally go in a HANDOFF entry is in §8–§10.

## 0. Bottom line
- **Fixed, in a separate worktree, not applied to the main checkout:**
  - Plan items 0, 0b, 1, 2, 3, 3b and 6 of `remediation/PLAN.md`: the recommended first batch.
  - Audit findings covered: H1, H3 (the backup part), M14, M11 (the frontend part) and the replay part of C4/I9.
  - Size: 19 files, +1191/−53 lines, 49 new backend tests and 7 new frontend tests.
  - Every new test was shown failing on the old code before its fix (outputs saved).
- **Readiness gate passed on the patched tree:**
  - `check_project.py --ready` exited 0, with the structure guard, typecheck and build OK.
  - **1718 backend tests passed**, 1 warning. That is 1669 before plus 40 + 9 new.
  - The code-tree hash was the same before and after the run (`a50b992…`).
  - Frontend unit tests: 10/10 passed. These run outside the gate (see §8, open item O1).
  - Browser: 63 of 63 page loads at 390–1440 px had no sideways scroll. A control run without the wrapper reproduced the old
    710 px overflow.
- **Reviews:** four independent read-only reviewers across 3 patches. First-round verdicts were all "approve with changes".
  Every required change was made, re-tested and, for the guard, re-reviewed (final verdict: APPROVE). There are no
  unresolved review findings, only the recorded open items in §8.
- **Live system untouched:**
  - Nothing was applied to `/Users/andry/Desktop/nba-predictor-sprint1` (the main checkout).
  - No live store was opened for writing and no `data/` folder was used.
  - No launchd change, network fetch, Telegram message, training, commit or push.
  - Applying the patch ("landing") needs the user's go (§6).
- **What this does not show:** passing checks do not show model quality, a betting edge, or that the data sources may be
  used for this purpose. Those audit findings (H4, M2, M4–M6, M17, …) are untouched and still open (§7).

## 1. What was done, and by whom
| Step | Who | What |
|---|---|---|
| 0 Worktrees | lead | Four no-commit detached worktrees at HEAD 79ff6ec plus the main checkout's uncommitted diff and untracked files: `~/Desktop/nba-predictor-batch1` (base/integration), `…-batch1-w1`, `…-w2`, `…-w3` (one per writer). Each had code-tree hash `de65b46…`, equal to the main checkout at that moment. That tree differs from the gated `2275eab` only in docs (`docs/SMOKE_TEST.md` and audit evidence). In each, `app.__file__` resolves inside the worktree. The only change to the main repo is the `.git/worktrees/` entries. |
| 0b Evidence | lead | The written failing tests and search scripts from the plan stage were copied out of the session scratchpad to `remediation/preserved/`, with a SHA256SUMS file. This batch's outputs are in `batch1/logs/`. |
| Writer W1 | agent | Item 1 (live-store guard) and item 6 (replay raw-file check). Files: cli, config, db, ingest, raw_cache, release, soccer, conftest, plus the new test_live_store_guard.py. |
| Writer W2 | agent | Item 2 (backup support for every store). Files: backup.py, test_backup_restore.py. |
| Writer W3 | agent | Items 3 and 3b (History overflow, NFL wording). Files: 5 .tsx files, package.json, 2 new .mjs tests. |
| Reviewers | 4 agents | W3: code review. W2: code review. W1: security/bypass review (2 rounds) and a code review of the raw-file check. All read-only, on fake checkouts and copies. |
| Integration and verification | lead | Copied each writer's files into the base worktree; nothing overlapped and every base file was confirmed unchanged first. Ran the gate once; ran the browser check on copies; ran the patch-apply check against the main checkout (check only). |

Each writer owned a separate set of files, so there was only one writer per file.

## 2. The fixes

### 2.1 H1: development commands could write to or migrate the live stores (plan item 1)
**Before:** a dev command run from the main checkout without `NBA_DATA_DIR` used `data/` (the live folder). It opened the
live store and migrated it from 31/33 to 35, after which every scheduled job refused. This was reproduced on copies in the
audit.

**Now** (`config.py`, `release.py`, `db.py`, `cli.py`):
- **Which folders are protected.** `config.protected_data_dir`: this checkout's `data/`, or any folder sitting directly in the
  root of a main git checkout (a `.git` directory). Linked worktrees, copies and temp folders are not protected.
  `config.protected_path` also follows links, so a symlink into the live folder counts.
- **What is refused.** Every command that would write, create or migrate a store there is refused, at any schema. The output
  is `{"status":"refused","reason":…,"problems":[…]}` with exit 1, before any lock, connection, folder or file is created.
  Commands covered:
  - the generic CLI path;
  - every `nfl` and `soccer` write action;
  - `tuning-study` and `deep-study`;
  - `adaptive-train` (unless `--dry-run`);
  - **`nfl activate`**: it could have replaced the live NFL activation file and silently stopped live NFL forecasts;
  - **`nfl test-run` and `nfl collect-lines --test`**: they append to the live one-shot test access log.
- **The one exception: the verified release environment.**
  - `release.migration_context()` = `release.check()` problems minus the schema-mismatch line, with `NBA_RELEASE_GUARD=1`
    required.
  - It also refuses when the code runs from the checkout that holds the data folder, so the dev checkout can never pass as a
    release just by setting environment variables.
  - Migration additionally needs the explicit `migrate` command.
  - `release.check()` itself is unchanged.
- **Hard links.** A store file with more than one hard link is treated as protected, because it may be the live store under
  another name. The live store files have 1 link each (checked with `stat`).
- **Second line of defence in `db.migrate`.** If migrations are pending on a protected file outside the explicit release
  migration, it raises `ProtectedStoreError` before writing anything. In-memory and temporary databases are exempt.
- **Tests.** A mandatory autouse fixture in `tests/conftest.py` gives every test a temporary `NBA_DATA_DIR`, so no test can
  touch the checkout's `data/`.
- **Unaffected (tested or reviewed):**
  - read-only commands, the API, and Telegram (dispatched before any guard; reads with `connect_readonly`; nothing new runs at
    import);
  - guarded release jobs: allowed in their release environment once the schema matches;
  - `scripts/dev.sh`: the dev copy is not protected;
  - preview-publish staging.

**Tests:** `backend/tests/test_live_store_guard.py`, 27 functions / 40 cases. The final file fails 15 cases on the round-1
code and 18 of 22 on the original code; all 40 pass now. Cases include:
- an implicit command never migrates;
- an explicit `NBA_DATA_DIR` naming the live folder is refused;
- `migrate` outside a release is refused;
- no store file is created;
- the same-schema write is refused for 10 commands;
- read-only commands still work;
- the release environment may migrate;
- a guard-less "release" is refused;
- copies still migrate;
- the `db.migrate` second line of defence;
- in-memory databases are exempt;
- symlink and hard-link bypasses;
- `nfl activate` and the NFL test-run are refused;
- the dev checkout is never a release environment.

**Review history:**
- Security reviewer, round 1, APPROVE WITH CHANGES:
  - H-1: `nfl activate` was unguarded.
  - M-1: a symlinked store got past the guard.
  - M-2: the dev checkout could pose as a release through environment variables.
  - M-3: the NFL test access log was unprotected.
- Round 2: APPROVE. All reproductions were re-run and refused, with the folder unchanged.
- One new low finding, a hard-linked store, was fixed in round 3.

### 2.2 Replay raw-file check (plan item 6; C4/I9)
**Before:** `replay` / `nfl replay` migrated the store first, then committed early fetches, and crashed on the first missing
raw file. The NFL store has 295 such files.

**Now** (`cli.py`, `ingest.py`, `soccer.py`, `raw_cache.py`):
- **When it runs.** A read-only check (`mode=ro` URI) runs before the lock, the CLI-level migrate and any replay transaction.
- **What it checks.** Exactly the files replay will read. The reviewer showed the set of checked files equals the set of
  loaded files for NBA, NHL and NFL stores with every source type.
- **If any file is missing:** exit 1 with `missing_raw` (the count and the first 20 ids). The message says
  `release-check` / `nfl release-check` lists them all.
- **Fails closed:** an unreadable store is refused, not let through.
- **Scope.** Soccer replay is covered too. The full scan runs only in `release-check` and the new read-only
  `nfl release-check`, never inside `release.check()`, which runs on every scheduled tick.

**Tests:**
- The replay-refusal test runs for nba, nfl and soccer, at schemas 31, 33, shipped−1 and shipped (12 cases). In each, an
  earlier file is present and a later one is missing, and the dump of every table must be unchanged.
- Other cases:
  - an unlogged NHL file is not counted as missing (false-positive guard);
  - a missing team-box file is refused (2 cases);
  - a store that cannot be read is refused;
  - `nfl release-check` on a store without raw tables gives a reason, not a crash;
  - `release-check` lists the missing files, and `release.check()` does not scan.

**Review:** APPROVE WITH CHANGES. Required: fail closed, `mode=ro`, the crash fix, the message, more test cases. All were
done. The check still runs outside the store lock (open item O5).

### 2.3 H3: no supported backup for the NFL store (plan item 2)
**Before:** `app.backup` knew only nba/nhl, and `--sport nfl` was an invalid choice.

**Now** (`backup.py`):
- **Which stores.** nba, nhl, nfl and soccer, taken from `app.sports`. A default run backs up every store that exists.
- **Skipped stores.** Each absent store is named in the `skipped` field and in a `WARNING:` line on stderr. A named missing
  store is still an error and creates no folder.
- **Restore safety.** NFL/soccer restores require the store's own identity (a backup without one is refused). Duplicate
  `--sport` names are handled. The manifest format, the sha256/identity/lock checks, and not copying `raw/` are all
  unchanged.

**Tests:** 9 new in `test_backup_restore.py`:
- NFL backup → verify → restore round trip;
- the CLI with nfl+soccer;
- the default run skipping absent stores;
- nba+nhl+nfl, the live case;
- the stderr warning;
- a named missing store;
- an NFL backup without identity is refused;
- a held soccer lock;
- a sport named twice.

Fail-before: 4 of the 4 first-round tests failed on the original code, and 3 of the 5 later tests failed on the round-1 code.
13/13 pass now.

**Review:** APPROVE WITH CHANGES (silent skip, identity check, duplicate sports, test gaps). All done.

**Not done:** no backup of the live NFL store was made. That needs the user's go.

### 2.4 M14: History tables overflowed at 581–710 px (plan item 3)
**Change:** each of the 3 `table.history` tables (`Canonical.tsx:135`, `HistoryPage.tsx:176`, `MarketView.tsx:155`) is now
inside `<div className="table-wrap">`. There is no CSS change; the ≤580 px card layout still applies inside the wrapper.

**Tests:** `tables.test.mjs` checks:
- every history table is wrapped;
- the check is not empty (at least 3 tables found);
- `.table-wrap` keeps `overflow-x: auto`.

**Browser** (lead, `batch1/logs/browser_RESULTS.txt`):
- 9 pages × 7 widths (390/600/640/700/760/800/1440). Pages: NBA/NHL/NFL History, NBA/NHL/NFL Performance, NBA/NHL live
  history, NFL Game Day.
- 63/63 loads had page `scrollWidth == innerWidth`, nothing still loading, no API errors, and no unwrapped history tables.
- Control: removing the wrapper in the same load brought back 710 px at 600 and 700 px.

### 2.5 M11: the NFL "data only / no NFL model" text (frontend part, plan item 3b)
**Change:** the text now follows the NFL `forecasts` capability returned by the API.
- **Forecasts active:** "Live forecasts are recorded within two hours of kickoff for regular-season and playoff games: the
  winner from an NFL Elo model; spread, total and team totals from a points model, against a bookmaker line collected 2–4
  hours before kickoff when there is one, otherwise a model line. No injury or weather information."
  - The reviewer checked each claim against `nfl_live.py` and `nfl_eval.py`. The first draft overstated when a bookmaker line
    is used and was corrected.
- **Forecasts inactive:** "NFL live forecasts are not active for this data store…".
- **NFL Daily Learning:** now points to where the record is.
- No accuracy, edge or performance claim; a test checks this.

**Tests:** `nflWording.test.mjs`, order-sensitive, so a swapped condition fails (checked by deliberately breaking the code).
The browser check confirmed the new text appears and "data only" is gone on the NFL pages.

**Left out:** the backend `coverage.py:588` text (NFL-hashed; plan item 9) and `nfl.py:68` (open item O3).

## 3. Verification (exact commands, observed results)
| Check | Command / method | Result |
|---|---|---|
| Readiness gate (patched tree) | in `~/Desktop/nba-predictor-batch1`: `OMP_NUM_THREADS=4 /Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python scripts/check_project.py --ready` (`app.__file__` checked first; it resolves in the worktree) | **passed**: exit 0, END 2026-11-08T00:51:18Z, 1718 passed, 1 warning (Starlette/httpx deprecation), 24 min; log `batch1/logs/gate.log` |
| Code-tree hash during the gate | gate recipe in the worktree, before and after | `a50b99265a3fdd58cd14c1216e4fbef7c70aa924` both times (unchanged) |
| Frontend unit tests | `npm run test:unit` in the worktree's frontend | **passed**: 10/10 (3 competition, 4 NFL wording, 3 tables) |
| Per-writer full suites | W1: full backend suite on WT1; W2: full backend suite on WT2 | **passed**: 1691 (W1, round 1), 1673 (W2, round 1). The final merged tree is the gate run above. |
| Fail-before evidence | each writer ran its new tests on the unmodified code | **failed as expected**; outputs in `batch1/logs/w1|w2|w3/` (`before*.txt`, `r2_before.txt`, `r3_before.txt`, `review_before.txt`) |
| Browser | vite (worktree) → API :8871 (worktree code), `NBA_DATA_DIR` = copies of the 15:29 live backups, iframe of exact width | **passed** (§2.4/§2.5); servers stopped by PID |
| Patch applies to the main checkout | `git apply --check --verbose batch1/batch1.patch` (no change made) | **passed** (all 19 files) |
| Main checkout code unchanged since the base | `cmp` of every changed/untracked non-docs file against the worktree base | no differences; only files under `docs/evidence/` were added |
| Live-store hard-link count | `stat -f %l data/*.sqlite` (metadata only) | 1, 1, 1 |
| Not run | real `scripts/dev.sh` launch; restart behaviour on a release checkout; anything on live stores; `npm run test:unit` from within the gate | — |

## 4. Files
- **Patch:** `batch1/batch1.patch`, the full binary diff against the base, including the 3 new test files. File list in
  `batch1/batch1.diffstat.txt`.
- **Worktrees (left in place for review):**
  - `~/Desktop/nba-predictor-batch1`: the integrated tree, i.e. the patch;
  - `~/Desktop/nba-predictor-batch1-w1`, `-w2`, `-w3`: the writers' copies;
  - remove them with `git worktree remove` once the patch has landed or been dropped.
- **Logs:** `batch1/logs/`: gate.log, gate_hash_before.txt, browser_RESULTS.txt, and the per-writer before/after/full-suite
  outputs.
- **Preserved plan-stage evidence:** `remediation/preserved/` (plan-B/plan-C written tests and scripts, plus SHA256SUMS.txt).

## 5. Proposed README wording (not applied: no .md edits)
- **"Run manually" (README:71-74):** "Development commands never write to the live stores. A command whose data folder is this
  checkout's `data/` (or the `data/` of any main git checkout), and that would open a store for writing or create or migrate
  one, is refused (`{"status":"refused"}`, exit 1) before any lock or file, whatever the schema. Use `scripts/dev.sh` or
  `NBA_DATA_DIR=<a copy>`. Only the release environment may write there (`NBA_RELEASE_GUARD=1`, `NBA_RELEASE_ROOT` = a
  separate clean release checkout, `NBA_DATA_DIR` set), and only the explicit `migrate` command migrates."
- **"Data store" (README:224-229):** prefix the examples with `NBA_DATA_DIR=<copy>`. Add: "`replay` (also `nfl replay` and
  `soccer replay`) first checks, read-only, that every cached file it will read exists, and otherwise refuses with the missing
  raw ids, without migrating or writing; `release-check` and `nfl release-check` report `raw_missing`."
- **Backup (README:213-216):** "online SQLite backup of every store present (NBA, NHL, NFL, and soccer once it exists);
  skipped stores are named in the result and in a WARNING line on stderr; `--sport` requires specific stores", and the restore
  example gains `--sport nfl`.

## 6. Landing: what applying this patch would change, and what needs approval
The patch is **not** applied. Landing rule K1 from the plan is still in force:
- The live Telegram job imports the main checkout's `backend/` every 60 s.
- Saving these files into the main checkout changes live Telegram code at once, including cli, config, db, ingest, release
  and soccer.
- So landing needs one of two things:
  - (a) Telegram is first moved to a guarded release checkout (plan item 14, needs a go); or
  - (b) the user names a landing window. In that window: `test_telegram` plus an import smoke test with the main checkout's
    venv, then the files are replaced atomically.

Effects at landing or release, stated now so nothing surprises:
1. **Dev commands against `data/` in the main checkout** are refused. This is intended, and it affects every session working
   there (including `ce`). They must use `scripts/dev.sh` or an `NBA_DATA_DIR` copy.
2. **Telegram, the API and guarded jobs:** no behaviour change. Tested in this tree, not yet on a release checkout.
3. **adaptive-train plist (paused):** its `training-inputs` and `adaptive-train` would be refused, because the plist lacks
   `NBA_RELEASE_GUARD`/`NBA_RELEASE_ROOT`. It must get them at the next release (launchd go). It stays unloaded.
4. **NFL activation procedure:** activating against the live folder now needs the release environment. Note this for plan
   item 9.
5. **A release must run from its own clean checkout.** A release from the main checkout with the release variables set is
   refused. Untracked files (e.g. a `.venv-X` inside a release checkout) make it dirty and refused, as before.
6. **Backup default:** now covers NFL (and soccer). A run with a missing NBA/NHL store now succeeds with a warning instead of
   failing.
7. **Frontend:** visible only after a web release (the live web is a55c874).

Approvals needed (none were assumed):
- landing in the main checkout (a or b above);
- a backup of the live NFL store;
- the adaptive-train plist change (launchd);
- commit/merge (plan items 0-A and 13).

## 7. Status of every audit finding after this batch
"Patch" means fixed in the worktree patch and not landed.

| Finding | Status | Next step | Approval |
|---|---|---|---|
| H1 dev commands write/migrate live stores | **Patch** | land (§6) | landing |
| H2 Telegram runs unguarded dev code | open | item 14: move token/ledger out, guard, move the job | user go (release + launchd) |
| H3 NFL not reproducible / no backup | **backup: patch**; 295 raw files: open, recorded as not found in the searched locations, no re-fetch proposed | live NFL backup | user go |
| H4 reused seasons shown as "held up"/"strong" | open | item 7b: disclosure after EVALUATION_PLAN:104, rule text unchanged; held_up no longer shown as confirmation | user go (product rule, Telegram text) |
| H5 dev line lacks the live releases | open | item 13: merge release lines | commit/merge request + timing decision |
| M1 team-change corrections (0035) | addressed by `ce` before the gate (verified on snap3) | — | — |
| M2 forecast time = record time | open | item 8 (after 13); never backdate | user go |
| M3 missed windows silent | open | item 5 (after 13) | none for code |
| M4 NFL quote ignores price_label | open | item 9 (NFL-hashed; re-activation) | user go |
| M5 NFL definition hash too broad | open | item 9 | user go |
| M6 one-shot guards per seal/dir | open | item 10 | user go |
| M7 one bad final aborts an import | open | next batch (item 7) | landing rule |
| M8 replay three behaviours; 62 NHL SO stored as OT | partly: replay now refuses on missing files; OT/SO re-derivation design still open | after 13 | — |
| M9 DB sport triggers | deferred to item 15 (schema) | — | user go |
| M10 NHL OT/SO hidden in UI | open | item 4 (after 13) | none |
| M11 NFL "data only" text | **frontend: patch**; `coverage.py:588` and `nfl.py:68` open | item 9 / backend wording batch | user go (NFL-hashed) |
| M12 API describes jobs from its own env | open | item 12 | none |
| M13 no dataset/code identity on rows | open | item 11 (migration 0036) | user go |
| M14 History overflow | **Patch** (browser-verified) | web release | release go |
| M15/M16 soccer stale labels, S1 independence | deferred (next soccer batch); S0/S1 acceptance stays conditional | — | — |
| M17 source terms | open, policy | item 18: corrected per-use table; product scope is the user's decision. No source has been declared acceptable. | user decision |
| C4/I9 replay before checking raw files | **Patch** | land | landing |
| C6 host sleep gaps | open | item 19 | user go |

## 8. Open items from this batch (recorded, not dropped)
- **O1.** `scripts/check_project.py --ready` does not run `npm run test:unit`, so no frontend unit test is part of the gate.
  Fix: one more entry in its check list. Not done (outside the batch scope).
- **O2.** Unguarded commands that write research files (not stores) under the data folder: `nhl-player-inputs`,
  `odds-history`, `historical-start`, `pool-fit-deep`, `nfl collect-lines` (without `--test`), `nfl evaluate`, and the
  report-writing studies. Decide later which of them may target `data/`.
- **O3.** NFL wording still wrong in the backend:
  - `nfl.py:68`: "No NFL research models, combinations or thresholds exist", shown on NFL Daily Learning while a frozen 75%
    winner rule is active;
  - `coverage.py:588`: NBA "legacy views" wording on NFL games (NFL-hashed → item 9);
  - stale "data-only" code comments: `NflPages.tsx:15`, `Layout.tsx:39`, `Selection.tsx:21`, `api/types.ts:10,902`,
    `nfl.py:1-5`, `sports.py:45`.
- **O4.** The "NFL Elo model" text matches the current seal only; the frontend cannot see a re-seal.
- **O5.** The replay check runs outside the store lock. The window is narrow, because files are written before rows and the
  raw tables are append-only.
- **O6.** The replay check confirms files exist; it does not decompress them or verify their hashes. A corrupt file can still
  stop a replay part-way.
- **O7.** Accepted, by spec: a separate clean clone with the three release variables set counts as the release environment.
  The guard prevents accidents; it does not stop deliberate misuse.
- **O8.** Already existing: a refusal with `NBA_RELEASE_GUARD=1` outside a release can create `-wal`/`-shm` next to a WAL store
  (`release.store_schema`), and a guarded command run from a dev process with the guard on writes one failed `job_runs` row
  (`release.record_refusal`, by design).
- **O9.** Over-broad by design: anything under a main checkout's root counts as protected (fail-safe). A dev data folder placed
  inside a main checkout would now be refused.
- **O10.** Backup: NBA/NHL restores still accept a store without identity (pre-0012 stores). The read-only `-shm` limitation in
  `run()` is unchanged.
- **O11.** Untested here: the guard on the actual release checkouts (a55c874/8539513 do not contain this code), and merge
  conflicts with the release lines (item 13: cli.py has known conflicts; re-run these tests on the merged base).
- **O12.** Vite build warning: a 550 kB chunk. It is in the build log of the merged tree; whether the unpatched tree prints it
  too was not checked.

## 9. Process notes and disclosures
- **Writers working in their own worktrees** read the main checkout (specs, `git show`) and the launchd plists (read-only,
  secrets filtered). They wrote nothing to the main checkout or any `data/` folder.
- **One reviewer's first probe imported the main checkout's `app` package** through the editable install. The probe stopped
  with an AttributeError after creating folders only in the scratchpad, with `NBA_DATA_DIR` pointing at the scratchpad.
  Nothing was written elsewhere. Every later run checked the import location first.
- **My browser dev server wrote vite's dependency cache** (`frontend/node_modules/.vite/deps`, created 19:07) into the main
  checkout's `node_modules`, because the worktrees use a symlink to it. It is an ignored build cache, not code or data; the
  live web runs from `~/nba-predictor-live`. It was left in place.
- **The browser check used** copies of the live stores taken at 15:29 (migrated copies in the session scratchpad) and read-only
  copies as `NBA_LIVE_DATA_DIR`. Ports 8871/5271; never the live :8100/:4173. Both processes (PIDs 42834, 42835) were stopped
  by PID.
- **Files added to the main checkout** are under `docs/evidence/all-sport-audit-2026-11-07/` only: this report, `batch1/` and
  `remediation/preserved/`. No existing .md was edited. HANDOFF.md was not updated (user instruction); this report replaces
  that entry for now.
- **Worktree index state:**
  - W3's worktree has some of its own edits staged ("MM"). The patch was therefore built from the base worktree, whose index
    holds the untouched base.
  - The base worktree's index has intent-to-add entries for the 3 new test files, so they appear in `git diff`.

## 10. Decisions for the user
1. **Landing:** approve moving Telegram to a guarded release checkout first (item 14), or name a landing window for this patch
   (§6).
2. **Live NFL backup:** with the new `app.backup --sport nfl`, after landing, or with the stdlib script in
   `remediation/preserved/plan-C-scripts/manual_backup_nfl.py` now.
3. **Next batch:**
   - Recommended: item 7 (one bad final no longer aborts an import), plus O1 (frontend tests in the gate) and O3's `nfl.py:68`
     text. Same rules: worktree, one writer per file set, independent review, landing rule.
   - Items 4, 5, 8 and 12 wait for item 13 (the release merge, which needs a commit/merge request).
4. **Unchanged policy items:** H4 disclosure, item 9 (NFL bundle and re-activation), item 18 (source scope). These are the
   user's decisions. This batch does not touch them.
