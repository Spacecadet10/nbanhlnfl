# CHALLENGE — independent adversarial review of plan/PLAN.md (2026-11-07)

Reviewer: critic (read-only; did not write PLAN/BATCH-*). Writes: only `$SP/work/challenge/` and this file.
`$SP` = `/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad`, `R` = live repo (read only: `git show/diff/log/merge-base` only).

**VERDICT: REVISE.** The first batch is the right *kind* of work and is free of NFL-hash impact, but item 1's guard is narrower than
the audit's recommendation, the "no live effect" claim silently depends on the batch never landing in REPO's working tree, item 17
offers a go for a collection the plan itself rates as not permitted, and several REPORT MEDIUM items (incl. soccer S0/S1 acceptance
conditions) vanished from the table. Mode: escalated to ADVERSARIAL after the 3rd MAJOR.

## Checks actually run (all from `$SP/snap3/backend`, scratch under `$SP/work/challenge/`)
Common env: `NBA_DATA_DIR=$SP/work/challenge/empty TMPDIR=$SP/work/challenge/pt PYTHONPATH=$SP/snap3/backend PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2 R/backend/.venv/bin/python -m pytest -p no:cacheprovider -q --basetemp=$SP/work/challenge/pt/<n> <file>`
(`app.__file__` verified = snap3, not REPO, despite the REPO venv's editable finder).
1. `work/challenge/tests/test_backup_nfl_plan.py` (copy of plan-C's) → **3 failed**, each for the intended reason (`UnsupportedSport 'nfl'`, argparse `invalid choice: 'nfl'`, `No nba store`).
2. `work/challenge/tests/test_challenge_guard_preflight.py`:
   - item-1 implicit (`--sport nhl score`, fake main checkout with `.git/` dir, store at shipped−1) → **failed** `(0, 35) == (1, 34)`: exit 0 and migrated. Right reason.
   - item-1 explicit (`nfl migrate`, store at 33) → **failed** `35 == 33`. Right reason.
   - item-6 (NFL copy, one cached response whose file is gone) → raises `FileNotFoundError`, store unchanged (`(1,0,1)` before/after, mtime unchanged). I.e. a fixture with only a missing first file does NOT demonstrate "writes before failing" on snap3 — see finding m3.
3. Import set of Telegram's entry (`import app.cli, app.telegram` from snap3): 38 modules incl. **app.config, app.db, app.cli, app.release, app.ingest, app.raw_cache, app.schemas, app.sources.espn**, app.coverage, app.markets, app.models.elo; not app.backup. Lazy in telegram.py:161-318: threshold_overview, telegram_card, freshness, providers.real, nfl_live.
4. 3-way merge simulation (`git merge-file`, base = merge-base 18fa0bf, ours = snap3, theirs = a55c874; files extracted with `git -C R show`): conflicts config.py 0, db.py 0, backup.py 0, raw_cache.py 0, ingest.py 0, nfl.py 0, **cli.py 2, release.py 1, jobs.py 6, freshness.py 4**. Re-run with simulated item-1/item-6 insertions (guard calls before the three `store_lock`s, `migration_context()`, `raw_missing` key) → **still cli 2 / release 1: the batch adds no conflicts**. Ancestry: 6d55e00, 7a83e27, a55c874, 97e7efb, 8539513 all NOT ancestors of HEAD (`git merge-base --is-ancestor`).
5. Read: snap3 config.py:40-44, release.py (all), db.py:22-120, cli.py:333-339,402-497,551-585,588-685,711-741, backup.py (all), raw_cache.py:49-66, ingest.py:269-311, soccer.py:242-255, nfl_live.py:18-20, threshold_overview.py:48-53, EVALUATION_PLAN.md:98-106, MODEL_SELECTION.md:224-226, README:71-79,184-212, scripts/dev.sh, check_project.py:50. `ls -a` of snap/snap2/snap3: no `.env.telegram`/ledger left (N2 apparently cleaned).
Not run: full suite, browser, any REPO/live command.

## Verdict per question
| Q | Verdict | Evidence (short) |
|---|---|---|
| 1 Live/K2/K1 freedom of first batch | **Modify** | K2: no batch file is in `nfl_live.py:19` list — agree. K1: items 1 and 6 edit config/cli/db/release/ingest/raw_cache, all in Telegram's import set (check 3); "no live effect" holds only while code stays out of REPO's tree; PLAN never says how/when it lands (M2). Guard does not break dev.sh (DEV=`~/nba-predictor-dev-data`, parent has no `.git`, dev.sh:31,150), API (no migrate), guarded jobs (schema==shipped after release), Telegram (dispatch at cli.py:333, before any store open). It **does** refuse BATCH-C C2 step 6 as written (`NBA_DATA_DIR=REPO/data … migrate` without GUARD/ROOT) and any release migrate while `.venv-X/` is untracked (release.py:73-75) — fix the C2 text (m1). It never lets a release create a new store ("no migrated store" problem is not removed) — fine now (no S2), note it. |
| 2 Tests meaningful | **Modify** | Item 1 and 2 tests fail today for the right reason (checks 1–2). Gaps: item 1 has no test for same-schema dev writes (M1), none for `:memory:`/temp DBs under the `db.migrate` backstop (m4); item 2's "nfl_and_soccer" test never touches soccer and asserts only "no exception"; item 6's test is only named in PLAN/C4 and my spot-check shows the trivial fixture passes the "no writes" property on snap3 (m3). |
| 3 Ordering vs K3 / item 13 | **Modify** | Item 13 need not precede items 1/2/3/6: config/db/backup unchanged on the release line, and the batch's insertions add 0 conflicts (check 4). But item 5 (freshness.py, 4 conflict hunks; release line adds the activity/`plan` model and REFUSAL_REPEAT/`lock_busy` job rows its new flags must read) and item 8 (jobs.py, 6 hunks; record-order change) must come **after** 13. PLAN currently puts 4–5 before 13. After 13, re-run the batch's tests on X. |
| 4 Integrity rules | **Agree, with 3 corrections** | No item backdates (B1 keeps `generated_at`; B4 moves availability *later*); item 17 treats re-fetch as a new retrieval. Item 7's dated disclosure appended after EVALUATION_PLAN.md:104 (text at :102-104 untouched) is acceptable — but it should also say the 4 extra lines were recorded as user-requested amendments (PLAYER_RATINGS_STUDY §3), so it neither hides nor re-legitimises them. Item 8: BATCH-B classes L1–L4 all keep (a),(b) in [T−2h,T) with no intervening result → "Live" valid; agree. Corrections: (i) item 10's legacy `test_runs.json` entry (report sha computed 2026-11-07, seal committed after the test, original log lost) must be labelled "not verifiable at completion", never "digest verified" (m6); (ii) B4's "MODEL_SELECTION.md:225 amended going forward" must be an appended dated note, not an edit of the declared availability rule (:224-226) (m7); (iii) item 7's change to `threshold_overview.RULE` (:48-53) and `held_up` is a change to a declared product rule's output — PLAN must name it as such (it has a go anyway). Also missing: NFL backfill revisions are all labelled `observed` (REPORT LOW, NFL-A) — a backfill-as-observed label the plan should at least disclose. |
| 5 Missing items / severity | **Disagree** | Absent without reason: M7 (CONF; one malformed final aborts a whole response, all 4 sports; NFL 'other' can block forecasts 7 d), M9, M11, M12, M15, M16, H3's "replay never rebuilds NFL line quotes" (ingest.py:295-297, 0 vs 10,776), REPORT §5 S1 corrections (wiki_et.py hardcoded values, Betfair 9≠8, "no UEFA source probed", **S1 raw bytes only in another session's scratchpad**), NFL skipped-games reason, MARKETS.md SO-goal note, BATCH-C N2 status. REPORT made M15 a condition of S0 acceptance and M16 of S1 acceptance — dropping them silently changes an acceptance. Misordering: item 3 (cosmetic overflow) is in the first batch while M7 (data-collection robustness) is not in the table at all. |
| 6 Item 0 | **Modify** | A commit is not the minimum. See §"Item 0 options". |
| 7 Item 18 sources | **Disagree (wording)** | PLAN overstates vs BATCH-C C5 / REPORT §4: "NBA.com not suitable on explicit terms for automated collection" — C5 cites no automated-access clause for NBA.com (it records personal non-commercial download as *allowed*); "NBA.com reports not for staked tickets" is stated as fact, C5 says whether injury PDFs are "Statistics" is interpretation and which ticket inputs use them is unknown; ESPN/NHL "applicability to the undocumented API" caveats dropped; openfootball "clearly permitted" drops "upstream rights unknown (CC0 §4(c))". Understates: Wikipedia/Wikidata (licence permits commercial reuse; coverage unassessed) and Sports-Reference manual reuse are omitted. Item 18 itself does not ask for a declaration; **item 17 does, implicitly** (M3). |

## Findings
### MAJOR
**M1 — Item 1 guard only fires on a schema mismatch; after any release it no longer protects the live stores.** BATCH-A A1 step 3:
`If protected_data_dir(config.data_dir()) and release.store_schema(sport) != release.shipped_schema()` … and compat test
`test_store_at_shipped_schema_is_not_blocked` ("guarded/unguarded commands proceed"). REPORT H1/§5 asked for "dev-checkout write
commands refuse REPO/data unless an explicit release flag". Today (dev 35 vs live 31/31/33) everything is refused; after release X
(35 = 35) a dev `predict`, `odds --force`, `replay`, `refresh` run from REPO with `NBA_DATA_DIR` unset writes dev-code forecasts,
quotes and revisions into the live record with no refusal (cli.py:572-585 path). Same for a gate test that forgets `NBA_DATA_DIR`
(check_project.py:50 runs pytest in REPO/backend with inherited env). Confidence HIGH (design text). Realist: silent, integrity of the
live record; not CRITICAL only because nothing is released yet.
Fix: refuse **every** store-opening write command (generic, `_nfl`, `_soccer`, both `conn_for`) on a protected dir unless
`release.check(sport)` problems, minus the schema line, are empty (the documented release env) — independent of schema; `migrate`
additionally requires explicit command. Add `test_dev_checkout_write_on_protected_store_at_shipped_schema_is_refused`. Make the
`conftest.py` autouse `NBA_DATA_DIR=tmp_path` fixture mandatory, not "optional". adaptive-train (paused) then needs GUARD/ROOT in its
plist at the next release — list that in item 15/16 (launchd go).

**M2 — "No live effect" is unconditional in PLAN but true only until landing; landing is unspecified.** PLAN rationale: "changes no
forecast/Telegram text"; Live-impact column "none" (items 1,6) / "none until release" (items 3,4,5). K1 itself: Telegram imports REPO
code every 60 s. Check 3: items 1 and 6 modify config/db/cli/release/ingest/raw_cache, items 4/5 schemas/real/freshness — all loaded
by Telegram. PLAN says "the writer stops after the batch" but not whether the result is applied to REPO's working tree. Confidence HIGH.
Fix: state "the batch lives only in the worktree as a reviewed patch; it is applied to REPO's working tree only (a) after item 14 moves
Telegram, or (b) with a user go for a landing window, after `test_telegram` + an import smoke with REPO's venv, files replaced
atomically". Correct the Live-impact column for 1,4,5,6 to "Telegram at landing (REPO)". Same rule must cover session `ce`, which is
editing REPO's tree now (git status: `sources/espn.py`, `ingest.py`, `db.py`, `schemas.py` modified — all in Telegram's import set).

**M3 — Item 17 offers a "user go" for a bulk ESPN re-fetch that item 18 rates not suitable for automated collection.** Row 17:
`network | user go (and source suitability, 18)`; row 18: `ESPN … not suitable on explicit terms for automated collection`. Asking
the user to approve the re-fetch is asking them to accept a collection the plan's own analysis says the terms bar — the standing
rule forbids that framing. Confidence MEDIUM-HIGH (applicability to the undocumented API is interpretation; the plan states it as
explicit). Fix: row 17 = "record as not found (searched scope listed; ~/.Trash not searched — permission); no re-fetch proposed;
it becomes an option only if a source whose terms permit this use is identified (item 18)". Also replace "unrecoverable" with
"not found in the searched locations" (C4 lists ~/.Trash as unsearched).

**M4 — REPORT items dropped without reason, including acceptance conditions.** See Q5 row. Most consequential: M7 (CONF, data
collection), the S1 raw bytes living only in another session's `/private/tmp` scratchpad (REPORT §5 S1 condition), M15/M16 (S0/S1
acceptance conditions), NFL line quotes not rebuilt by replay. Confidence HIGH (table vs REPORT). Fix: add rows (or a "deferred,
because…" list) for M7, M9, M11, M12, M15, M16, S1 corrections, NFL-quotes-replay, NFL backfill `observed` labels, N2 status; and
state in PLAN that S0/S1 acceptance stays conditional until M15/M16 are done or recorded as known.

**M5 — Evidence durability: the whole plan rests on session-local `/private/tmp` scratch.** REPORT §0: "Scratchpad is session-local:
copy anything needed long-term." PLAN cites reproductions, written failing tests (`work/plan-B/tests`, `work/plan-C/tests`),
`nfl_missing_raw.csv`, `manual_backup_nfl.py`, and the only off-live copies of the 15:29 stores (`$SP/stores`) — all in /private/tmp;
the S1 raw bytes are in a third session's scratch. Confidence MEDIUM (loss timing unknown; /tmp does not survive a reboot). Fix: step 0b
of the first batch = copy plan/audit evidence and written tests into the worktree (`docs/evidence/audit-2026-11-07/`), and ask the
user where durable copies of the S1 raw bytes and store backups may go (no live write).

**M6 — Item 18 overstates certainty and omits candidates** (Q7 row). Confidence HIGH (PLAN text vs BATCH-C C5 table). Cheap to fix,
but the user decides on this text. Fix: replace the one-line verdict with C5's per-use table, keeping the explicit/interpretation column
and adding Wikipedia/Wikidata and Sports-Reference (manual) as unassessed candidates.

### MINOR
- m1 C2 step 6 (BATCH-C) migrate command lacks `NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT=…`; under item 1 it is refused (README:209 "same
  environment as step 4" is right). And C1 step 3's `.venv-X/` makes the release checkout dirty → release.check problem → migrate and all
  guarded jobs refused. Align item 15's text with item 1 before the window.
- m2 Item 6's `raw_missing` inside `release.check()` runs on every guarded tick (once a minute per sport; NHL 17,566 `is_file` calls) and
  lands in every refusal row's detail. Put the scan in the `release-check` command output only.
- m3 Item 6 test spec must: put a present response before the missing one in retrieval order; assert games/revisions/raw_fetches/identity
  unchanged and a structured refusal (rc 1 + list); and run the preflight via `connect_readonly` **before** `db.migrate` at cli.py:678-680
  (and :580-582 for `replay`), otherwise a schema-33 copy is still migrated to 35 before refusing (the H1 reproduction). Say whether
  `soccer replay` (soccer.py:242) is covered.
- m4 Item 1's `db.migrate` backstop via `PRAGMA database_list`: in-memory/temp DBs report file `''`; a naive `Path('').resolve()` is the cwd
  (`REPO/backend`) whose parent has `.git/` → every in-memory migrate in the gate would raise. Spec: empty file = not protected; add a test.
- m5 Item 5 should follow item 13 (Q3).
- m6 Item 10 legacy registry entry wording (Q4-i).
- m7 B4 doc change must be an appended dated note (Q4-ii).
- m8 Item 9 "re-activation keeps first activation time" merges forecasts made before and after the A2 quote-selection fix into one
  record; BATCH-A calls it a product choice — PLAN row 9 presents it as decided. Mark as user decision with the fresh-record alternative.
- m9 Item 2: the CLI test named "accepts nfl and soccer" never passes `soccer` and asserts nothing; add a default-backup test with
  nba+nhl+nfl all present (the real live case).
- m10 Worktree/venv trap: REPO venv has an editable finder → REPO/backend. Running `.venv/bin/pytest` (not `python -m pytest` from the
  worktree's backend) can silently test REPO's code. Item 0 must require printing `app.__file__` in the worktree run.

## Ambiguity risks
- PLAN row 6 "refuse before writing" → A: before replay's first transaction (migration already applied by cli) / B: before any write incl.
  migration. Wrong choice = copies keep being migrated by a refused replay.
- PLAN "writer stops after the batch" → A: patch kept in worktree for review / B: applied to REPO. B is a live Telegram change (M2).
- Row 2 "backup.py covers every store sport present" → A: default backs up every existing store (changes README "both stores" behaviour) /
  B: only adds `--sport nfl`. A changes what the documented pre-release backup produces; fine, but say so.

## Item 0 options (Q6)
| Option | Commit? | Writes to REPO | Pros | Cons |
|---|---|---|---|---|
| A. Commit gated tree on a new branch (PLAN) | yes — needs the user's request | index/ref of REPO's main worktree | exact provenance (sha); clean base for item 13 merge and release X | rule needs explicit request; commits unreviewed code; must be taken while `ce` is not mid-edit and verified = 2275eab |
| B. `git worktree add --detach <path> HEAD`; `git -C R diff --binary HEAD \| git -C <wt> apply`; copy `git ls-files -o --exclude-standard` files; `git -C <wt> add -A` (worktree's own index); verify code-tree hash = 2275eab with the gate recipe | **no** | only `.git/worktrees/<name>` + blobs; REPO index/HEAD/working tree untouched | minimum that respects "no commits"; writer's changes = unstaged `git diff` in the worktree; no secrets (ignored files are not copied) | base provenance is a hash recipe, not a sha; later commit still needs a request; item 13 merge needs a commit eventually |
| C. Copy-based scratch tree (rsync, exclude .git/data/.venv/.env*/.telegram*) | no (or a commit in a throwaway repo) | none | zero writes to REPO/.git | loses ancestry (item 13 impossible there); N2-style secret copy risk if an exclude is missed; editable-venv trap |
Recommendation: B by default; A only if the user asks for a commit. "Two copies of unreviewed work" (K1) applies equally to A.

## REQUIRED changes to PLAN.md
1. Item 1: guard on protected dir **regardless of schema** unless in the verified release env (M1); mandatory conftest fixture; in-memory exemption + test (m4); new same-schema test.
2. Add a landing rule and fix the Live-impact column for items 1,4,5,6 (M2); extend K1 to every session editing REPO (incl. `ce`).
3. Item 0: offer option B as the no-commit default; require `app.__file__` check and base hash = 2275eab (Q6, m10).
4. Add evidence-preservation step 0b (M5).
5. Item 6: preflight before cli-level migrate; precise test spec; scan only in `release-check` output; state soccer coverage (m2, m3).
6. Item 17: no re-fetch go; "not found in searched locations" (M3).
7. Item 18: per-use table with interpretation flags; add omitted candidates (M6).
8. Add rows or a "deferred because" list for M7, M9, M11, M12, M15, M16, S1 corrections + raw bytes, NFL quotes replay, NFL backfill
   `observed` labels, N2 status; restate S0/S1 acceptance as still conditional (M4).
9. Ordering: item 5 (and 8) after item 13; after 13, re-run the first batch's tests on X (Q3).
10. Item 15: migrate command with GUARD/ROOT; no untracked `.venv-X` in the release checkout; adaptive-train plist needs GUARD/ROOT if M1 fix adopted (m1).
11. Item 7 disclosure mentions the recorded user-requested amendments; name the RULE/held_up change as a product-rule change (Q4).
12. Item 10 legacy entry labelled "not verifiable at completion" (m6); item 9 continuity = user decision (m8); B4 doc change appended (m7).

## Recommended first batch (one writer, worktree, nothing applied to REPO)
0. Worktree by option B (or A if the user requests a commit); verify 2275eab; `app.__file__` check.
0b. Preserve evidence into the worktree (`docs/evidence/audit-2026-11-07/`: PLAN, BATCH-*, REPORT, CHALLENGE, written failing tests, CSV).
1. Item 1 as corrected (schema-independent; mandatory conftest; in-memory exemption).
2. Item 2 (+ m9 tests).
3. Item 6 as corrected (preflight before migrate; `release-check` output only).
4. Item 3 + M11's frontend-only part (Layout.tsx:124, NflPages.tsx:290,295; not coverage.py:588, which is NFL-hashed).
Next batch (before items 4–5): M7 per-event rejection rows (espn/nhl_stats parsers; Telegram-imported → same landing rule).
Landing: none until item 14 or an explicit user go for a landing window.
