# BATCH C — operations, recovery and sources: remediation plan (read-only reviewer, 2026-11-07)

Reviewer: plan-C (read-only). Code read: `$SP/snap3` (tree, schema 35), `git -C REPO` objects for a55c874 / 8539513 / HEAD,
release trees extracted with `git archive` into `$SP/work/plan-C/rel-a55` and `rel-853` (`diff -rq` vs the real checkouts: identical).
Writes: only `$SP/work/plan-C/` and this file + `$SP/plan/nfl_missing_raw.csv`. Nothing written to REPO, REPO/data, the release
checkouts, launchd or ~/nba-predictor-dev-data. No network fetch of data; 6 licence/terms pages read (listed in C5).
`$SP` = `/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad`, `W` = `$SP/work/plan-C`.

Disclosure: proposed test 2 in C1 (run on snap3 as a failing-first check) opened `$SP/snap3/.telegram-delivery.sqlite`
(`telegram.ledger()` resolves REPO_ROOT to the snapshot); `CREATE TABLE IF NOT EXISTS` only, mtime unchanged (15:51). See N1.

## NEW findings from this batch (not in REPORT.md)
- **N1 (HIGH, ops):** the three code lines have diverged and the tree is NOT a superset of the live release. `release` branch
  commits 7a83e27 and a55c874 (and 6d55e00 by commit, partly by content) are not ancestors of HEAD (`git merge-base --is-ancestor` → no). Their
  content is missing from snap3: `MAX_TRANSIENT_BOX_ATTEMPTS`/`network_wait` (a55c874: 20 matches in jobs.py; snap3: 0),
  `REFUSAL_REPEAT` (a55c874 release.py; snap3 none), cycle clock `last_cycle_at`/`cycle_due` (a55c874 cli/jobs; snap3 none). Worse, snap3 still
  has the early forecast wake-ups (`jobs.py:789-806` `window_opened`/`new_data`) that 7a83e27 removed because "a timing change
  needs the user's own decision". Releasing the tree (with 0035) as is would silently undo three reviewed live fixes and change
  forecast timing. 8539513 (nfl-live) lacks 7a83e27/a55c874 too.
- **N2 (MEDIUM, ops/secrets):** the audit snapshots `$SP/snap`, `snap2`, `snap3` each contain the private `.env.telegram` (bot
  token, mode 600) and a copy of `.telegram-delivery.sqlite`. Code run from a snapshot resolves `config.REPO_ROOT` to the
  snapshot, so `telegram run|test` from a snapshot would send real messages with the real token. Recommend the lead delete these
  three files from the snapshots (scratch only) and that future snapshots exclude `/.env*` and `/.telegram-delivery*`.
- **N3 (MEDIUM, ops):** `telegram` is dispatched at `cli.py:333`, before the release guard (`cli.py:551`), so setting
  `NBA_RELEASE_GUARD=1` on the telegram plist would NOT guard it. Its credentials and ledger are found via `config.REPO_ROOT`
  (`config.py:10,40`; `telegram.py:70`), i.e. inside whichever code checkout runs it: moving the job to another checkout loses
  the dedup ledger (duplicate cards) unless the files move with it. a55c874 (the NBA/NHL release) has no `telegram.py` at all.

---

## C1 — Guarded release checkouts and venvs (H2; risk + confirmed config defect)

### Inventory (read-only: `plutil -convert json`, `launchctl print gui/501/<label>`, `launchctl list`, 2026-11-07 ~16:35 ET)
| label | loaded / state (runs, last exit) | interpreter (venv) | WorkingDirectory = code | env | schedule |
|---|---|---|---|---|---|
| api | loaded, running pid 942 | ~/nba-predictor-live/backend/.venv | ~/nba-predictor-live/backend (a55c874) | NBA_API_MODE=real, NBA_DATA_DIR=REPO/data, CORS | KeepAlive, :8100 |
| web | loaded, running pid 953 | node 22.23.2 (fnm) vite preview | ~/nba-predictor-live/frontend | API_PROXY_TARGET :8100 | KeepAlive, :4173 |
| odds (`tick`) | loaded (797 runs, exit 0) | live venv | live (a55c874) | GUARD=1, ROOT=live, DATA=REPO/data, NBA_DAILY_AGREEMENT=1 | StartInterval 60 |
| nhl-odds (`--sport nhl tick`) | loaded, running (825, 0) | live venv | live | GUARD, ROOT, DATA, NBA_DAILY_AGREEMENT=1, NHL_INJURY_CAPTURE=1 | 60 s |
| injuries | loaded (44, 0) | live venv | live | GUARD, ROOT, DATA, NBA_INJURY_COLLECTION=1 | :02, :32 |
| daily | loaded (1, 0) | live venv | live | GUARD, ROOT, DATA, NBA_PLAYER_BOX_REFRESH=1, NBA_SHADOW_RECORDING=1 | 22:00 |
| nhl-daily (`/bin/sh -c` odds; daily) | loaded (1, 0) | live venv | live | GUARD, ROOT, DATA, NBA_DAILY_AGREEMENT=1, NHL_INJURY_CAPTURE=1 | 22:15 |
| nba-markets | loaded (1, 0) | live venv | live | GUARD, ROOT, DATA | 22:30 |
| nfl-live (`nfl tick`) | loaded (902, 0) | **REPO/backend/.venv** (dev venv: torch/tensorflow, editable finder → REPO/backend) | ~/.codex/worktrees/nfl-live-release/…/backend (8539513, clean) | GUARD, ROOT=nfl worktree, DATA, OMP=1, PYTHONDONTWRITEBYTECODE | 60 s |
| telegram (`telegram run --once`) | loaded, running (678, 0) | **REPO/backend/.venv** | **REPO/backend (dirty dev tree)** | DATA=REPO/data, OMP=1; **no GUARD/ROOT** | 60 s |
| adaptive-train | **installed, NOT loaded** ("Could not find service") — unloaded by user decision (training paused): keep it so | ~/nba-predictor-deep/backend/.venv via `caffeinate -i sh -c` | ~/nba-predictor-live/backend | DATA, OMP=4, *_PLAYER_BOX_REFRESH=1; no guard | 09:30, 15:30 |
Logs: all under REPO/data/*.log except nfl-live and telegram (REPO root `.nfl-live*.log`, `.telegram-delivery*.log`).

Venvs (`$W/freeze.py` via importlib.metadata, `-I`): all CPython 3.12.12 built with uv 0.12.5. Live venv (65 dists) == a55c874
`requirements-release.txt` exactly (`comm` empty both ways). REPO venv (81): adds torch 2.14.1, tensorflow 2.21.0, keras and deps,
hypothesis, sortedcontainers, pillow 13.0.0 (live 12.3.0), editable `nba-predictor-backend`; lacks pandas, ngboost, lifelines,
matplotlib (+deps). Deep venv 96. Shared core identical (numpy 2.5.3, scipy 1.18.1, scikit-learn 1.9.1, fastapi 0.142.2, pypdf 5.9.0).
Tree/8539513 `requirements-release.txt` = a55c874's + `hypothesis==6.168.3`, `sortedcontainers==2.4.0`; pyproject adds extra
`notifications = Pillow` (telegram cards import PIL: `telegram_card.py`). No venv exists in the nfl-live worktree.
Freeze outputs: `$W/freeze_{Desktop,nba-predictor-live,nba-predictor-deep}.txt`.

### Issue
Confirmed config: telegram runs uncommitted dev code with live credentials against live stores, unguarded (H2; plist above; N3);
nfl-live runs reviewed code on an unpinned dev venv (I12). Consequence today: content of real-money ticket cards follows code
another session edits; a venv change in REPO (e.g. Nov 7 13:23 torch/tensorflow install) reaches the NFL producer. Store access
by telegram is `connect_readonly` (`telegram.py:267`), so no migration risk from it.

### Reproduction
`plutil -convert json -o - ~/Library/LaunchAgents/com.nba-predictor.telegram.plist`; `launchctl print gui/501/com.nba-predictor.telegram`
→ program REPO venv, no guard env. Failing-first test of N3: `$W/tests/test_telegram_guard_plan.py` run on snap3:
`cd $SP/snap3/backend && NBA_DATA_DIR=$W/tests/nodata PYTHONPATH=$SP/snap3/backend PYTHONDONTWRITEBYTECODE=1 REPO/backend/.venv/bin/python -m pytest -p no:cacheprovider -q --rootdir=$W/tests $W/tests/test_telegram_guard_plan.py --basetemp=$W/tests/tmp2`
→ **2 failed** (observed): guard ignored (`telegram.command` called with GUARD=1 and a wrong ROOT); `NBA_TELEGRAM_DIR` ignored.

### Target layout (proposal)
One release checkout, one venv, one commit for every scheduled job:
- `~/nba-predictor-live` (branch `release`) moved to a single reviewed commit **X** = tree (35) + merged `release` branch fixes (N1) +
  NFL live (already in tree; definition hash b6074877a7e7ccfe identical in snap3, 8539513 and live activation.json — verified) + telegram.
- `~/nba-predictor-live/backend/.venv` rebuilt from X's `requirements-release.txt` (no editable install, no deep/research-only extras
  beyond what the file pins). Used by api, odds, nhl-odds, injuries, daily, nhl-daily, nba-markets, **nfl-live, telegram**. web stays node.
- Telegram private files outside any code checkout: `NBA_TELEGRAM_DIR` (proposed env) → the directory holding today's
  `.env.telegram` + `.telegram-delivery.sqlite` (REPO root today, or a new `~/nba-predictor-telegram/`, mode 700). Keeps the ledger (no duplicate cards).
- adaptive-train: unchanged, stays unloaded (training paused by user decision). Not part of this change.
- The nfl-live worktree is retired only after one clean day on X (HANDOFF: "DO NOT archive: launchd uses it").
- Interim (optional, before X exists): give nfl-live its own venv inside its worktree (`backend/.venv/` is git-ignored in 8539513, so
  the guard's clean check is unaffected). Telegram has no safe interim checkout: a55c874 lacks telegram; 8539513's
  `telegram_card.py` differs from the tree by ~269 lines (cards would change). Leave telegram as is until X, recorded as an accepted risk, or pause it (user choice).

### Proposed code change (local only)
1. `config.py`: `telegram_dir()` = `Path(os.environ["NBA_TELEGRAM_DIR"])` if set else `REPO_ROOT`; use it in `telegram_settings`,
   `save_telegram_setting` (`config.py:10,31,35`) and `telegram.ledger` (`telegram.py:70`). Must NOT change file formats, modes (600), token checks.
2. `cli.py`: before the telegram dispatch (`cli.py:333`), when `release.enabled()` and action is `run`: `release.check(s)` for every
   store telegram reads (nba, nhl, nfl); any problem → print the refusal JSON, exit 1, send nothing. No job_runs row (job names are a
   fixed set; telegram has a read-only connection). `status|preview|connect|test` stay unguarded (manual).
3. `release.py`: optional interpreter pin: if `NBA_RELEASE_PYTHON` is set, problem when `Path(sys.prefix).resolve()` differs.
4. `.gitignore`: already ignores `/.telegram-delivery.sqlite*` in the tree (also covers `-journal`); a55c874's `*.sqlite` would not cover the
   transient `-journal`, which is why telegram state must not live in a guarded checkout without this entry.

### Regression tests (fail on snap3 — observed for 1 and 2)
- `tests/test_telegram.py::test_telegram_run_is_refused_by_the_release_guard_outside_the_release_checkout` — GUARD=1, ROOT elsewhere,
  `telegram.command` monkeypatched; asserts not called, rc≠0. Fails today: called (N3).
- `tests/test_telegram.py::test_telegram_state_lives_in_the_configured_directory_not_the_code_checkout` — env set; ledger file created
  there. Fails today: ledger opened at REPO_ROOT.
- `tests/test_release_guard.py::test_a_release_job_on_another_interpreter_is_refused` (if item 3 adopted) — fails today: no such check.

### Compatibility
NBA/NHL/NFL jobs: no behaviour change unless env is set. Telegram content unchanged. Activation hash: none of the touched files
(`config.py`, `cli.py`, `telegram.py`, `release.py`) is in the NFL definition (`nfl_live.py:18-20`) → no re-activation. Schema: none.
Existing tests: none assert the absence of a telegram guard (grep `telegram` in tests/test_release*.py: none).

### Change procedure for later approval (no changes now)
1. Prereqs: X committed, readiness gate passed on X, Codex review, C2 rehearsal passed.
2. Back up plists: `mkdir ~/nba-predictor-backups/<UTC>/launchagents && cp ~/Library/LaunchAgents/com.nba-predictor.*.plist` there.
3. Build venv beside the old one: `uv venv --python ~/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/bin/python3.12 ~/nba-predictor-live/backend/.venv-X`;
   `uv pip install --python …/.venv-X/bin/python --no-deps -r ~/nba-predictor-live/backend/requirements-release.txt` (after checkout of X, step C2.5);
   `uv pip check`; freeze == requirements file (`comm` empty); `import app` from WD resolves to the checkout.
   (`.venv-X/` is not matched by `.venv/` in .gitignore — either name it `.venv` after moving the old one aside, or add the pattern in X.)
4. Stop telegram + nfl-live (`launchctl bootout gui/501 ~/Library/LaunchAgents/com.nba-predictor.{telegram,nfl-live}.plist`); C2 window.
5. Edit plists: nfl-live → ProgramArguments[0] live venv, WD/ROOT `~/nba-predictor-live`; telegram → live venv, WD `~/nba-predictor-live/backend`,
   add `NBA_RELEASE_GUARD=1`, `NBA_RELEASE_ROOT`, `NBA_TELEGRAM_DIR`; all others → same venv path (unchanged if `.venv` reused).
6. `plutil -lint`; `launchctl bootstrap gui/501 <plist>` each; verify `launchctl print` program/env; first nfl-live tick exit 0 with
   `release` = X in job_runs.detail; `telegram status` from the release (no send) reads the existing ledger (count of `sent` rows unchanged).
7. Rollback: bootout, restore plists from step 2, bootstrap; old venv kept until one clean day.

Live impact: needs release + launchd. Approval: user go (launchd, release). Size M. Depends on C2 (X), H1 guard (other batch).

---

## C2 — Schema compatibility and a joint release sequence (H1/H3 consequence; risk + N1)

### Facts (observed)
- Migrations shipped: a55c874 0001–0031; 8539513 0001–0033; tree/snap3 0001–0035. Live stores (15:29 copies): NBA 31, NHL 31, NFL 33.
- `release.check` (`snap3 release.py:80-84`, identical in 8539513; a55c874 adds only the refusal rate limit) requires `store == shipped` exactly.
  Telegram reads all three stores, so a guarded telegram (C1) needs all three on X's schema at once → one joint release.
- Migration of copies with tree code (`$W/mig35.py`): NFL 33→35 0.1 s; NHL 31→35 1.4 s; NBA 31→35 1.4 s. Row counts of games,
  game_forecasts, market_quotes, result_revisions, raw_responses, job_runs, store_identity identical 31 vs 35 for all three; `quick_check ok`,
  `foreign_key_check` empty (NBA/NFL/NHL).
- **Can a55c874 read a schema-35 store?** Yes for the API surface tested. `$W/api_compat.py` with a55c874 code (`PYTHONPATH=$W/rel-a55/backend`,
  live release venv) against `$W/s31` vs `$W/s35` copies: 17 NBA + 17 NHL endpoints (health, meta, sources, games ×2 dates, game detail,
  canonical forecasts/performance, predictions, performance, thresholds, coverage, research status/adaptive, agreement-performance)
  → identical status codes and identical response digests; only `/api/coverage` differed, and only in the copy's path string inside a
  raw-cache FileNotFoundError (copies have no raw/). Outputs `$W/api_a55_s31.txt`, `$W/api_a55_s35.txt`, `$W/cov_nba_s3{1,5}.json`.
  8539513 NFL endpoints (meta, games ×2, game detail, record, freshness) on NFL 33 vs 35: identical (`$W/nfl_compat.py`).
  Guarded writers do NOT run: a55c874 `release.check` on the 35 copies → "store schema 35 differs from this release's schema 31";
  8539513 → "35 differs from 33" (observed). Writes by old code into a 35 store were not tested (the guard prevents them).
- Consequence: the API/web can stay on old code briefly after a migration (read-compatible), but collection/forecast jobs cannot.

### Proposed release sequence (one window, all three stores, user go required)
0. Code (local, no live): (a) merge branch `release` (6d55e00, 7a83e27, a55c874) into the dev line and resolve so that the cycle clock,
   refusal rate limit, transient box retries are kept and the early forecast wake-ups are removed (7a83e27 P3-4) unless the user decides
   otherwise; bring their 27 tests (6d55e00 16, 7a83e27 6, a55c874 5) — the 7a83e27/a55c874 ones are expected to fail on snap3 (their symbols are absent; NOT run);
   (b) H1 guard; (c) C3 backup for nfl/soccer; (d) C1 telegram changes; (e) missing-bytes preflight (I9) reported by `release-check` as a
   warning, never a blocker for NFL (295 files cannot be recovered, C4); (f) readiness gate on X; Codex review. Commit X.
1. Rehearsal on copies (no network): fresh `mode=ro` backups of nba/nhl/nfl + raw/ + research/ into a scratch dir; worktree at X; venv from X's
   requirements; `migrate` each store; `release-check` with GUARD/ROOT set → `problems == []` per sport; API digest comparison old-vs-X as in
   `$W/api_compat.py`; `telegram preview` (no send) per sport; NFL activation still valid (`nfl_live.activation()` not None).
2. Pick the window: no NBA/NHL/NFL game starting within ~3 h (outside every T−2 h forecast window), Mac on AC and awake (C6).
3. Stop writers: bootout odds, nhl-odds, injuries, nfl-live, telegram (daily/nhl-daily/nba-markets only if within their hour). Confirm
   no process holds `.{nba,nhl,nfl}-jobs.lock` (`lsof` on the lock files). API/web may keep running (read-compatible, verified above).
4. Preflight: backup with X's `app.backup --dest ~/nba-predictor-backups --sport nba --sport nhl --sport nfl` (or the C3 interim script
   for NFL); manifests: integrity ok, identity, schema 31/31/33, sha256; `raw_cache.missing_files` per store recorded (expected 0/0/295);
   copy plists (C1 step 2).
5. Move code: `git -C ~/nba-predictor-live checkout release && git merge --ff-only X` (or `reset --keep` per the existing move-release.sh);
   rebuild venv (C1 step 3).
6. Migrate as X with live env: `NBA_DATA_DIR=REPO/data …/.venv/bin/python -m app.cli --sport nba migrate`, `--sport nhl migrate`,
   `nfl migrate`; then `release-check` (GUARD=1, ROOT=live) per sport → `problems == []`.
7. Update/bootstrap plists (C1 steps 5–6); restart api + web (kickstart -k) so they serve X. Observe: one tick per sport, job_runs.detail.release = X,
   freshness fresh, telegram ledger unchanged count. Record in HANDOFF.
8. Soccer: no live store exists; nothing to migrate.

### Rollback (decision criteria + procedure)
Trigger: any `release-check` problem after step 6, a failed first tick, or wrong API output. Procedure: bootout all writers + api/web;
`app.backup --restore <dir> --sport nba --sport nhl --sport nfl` (X's version; restores under the job lock, moves the 35 stores aside as
`<store>.replaced-<UTC>`, never deletes); `git -C ~/nba-predictor-live checkout a55c874`-state (`release` branch reset to a55c874), old venv,
plists from the backup; nfl-live worktree was never touched (stays 8539513); bootstrap; `release-check` → schema 31/31/33 problems []. Tested on a copy:
`$W/rb` — NFL backup → migrate 33→35 → one job_runs row written → files moved aside → backup copied back (sha matched
55759f7c…) → 8539513 `release.check` reports no schema problem; the post-migration job_runs row (339th) is absent from the restored
store and present only in `nfl.sqlite.replaced-<UTC>` (observed).
Rows written between migrate and rollback: lost from the served store. Raw bytes stay in `raw/` (append-only) but their
`raw_responses`/`raw_fetches` rows (retrieval times) are only in the `.replaced` file; forecasts made in that window cannot be remade
(never backdate) and those games will read "missing". Keep the window short (jobs stopped from step 3 to 7, ~minutes), keep the
`.replaced` files as evidence, record the window in HANDOFF; any merge of rows back is a separate reviewed tool, not part of rollback.

### Regression tests
- `tests/test_release_guard.py::test_release_check_reports_missing_raw_files_as_a_warning` — store referencing an absent file; asserts
  `info["raw_missing"] == 1` and `problems` unchanged. Fails on snap3 (key absent).
- The 11 release-branch tests named above (7a83e27 6, a55c874 5) — expected to fail on snap3, not run (cycle marker / transient retry absent); and
  `test_freshness.py::test_forecast_window_opening_wakes_the_forecast_step_without_waiting` (snap3:244) is replaced by 7a83e27's
  `test_a_forecast_window_opening_between_cycles_waits_for_the_cycle`.
Compatibility: NBA/NHL timing and box-retry behaviour return to the reviewed live behaviour; NFL definition files untouched by those
commits (they change cli, freshness, ingest, jobs, release, nhl_stats only) → no NFL re-activation. Telegram: unaffected.
Live impact: release + migration + launchd. Approval: user go. Size L. Depends on C1, C3, H1, gate.

---

## C3 — NFL backup and rollback (H3/I2; confirmed defect)
Issue: `backup.py:23` `SPORTS = ("nba","nhl")`; `backup.py:161-162` `_db_file` → `sports.get()` raises for data-only sports (`sports.py:70-75`);
`backup.py:169` argparse `choices=SPORTS`; default run requires both nba and nhl (`backup.py:63-66`). Affects NFL (live) and soccer (no live
store yet). Consequence: no supported backup/restore before 0035 rebuilds tables in the NFL store, whose history cannot be re-derived (C4).

Reproduction (observed, snap3): `$W/tests/test_backup_nfl_plan.py` → 3 failed: `UnsupportedSport: Unknown sport 'nfl'`;
`argparse … invalid choice: 'nfl' (choose from nba, nhl)`; default backup with only an NFL store → `BackupError: No nba store`.

Proposed change (smallest): `SPORTS = (*sports.SPORT_IDS, *sports.DATA_ONLY)` (or explicit `("nba","nhl","nfl","soccer")`); `_db_file` uses
`sports.store(sport).db_file` (`sports.py:78`, accepts data-only); default `run()` = every sport whose store file exists, error if none; an
explicitly named missing sport still errors (keeps `test_a_backup_refuses_an_existing_destination_and_a_missing_store`). Add `soccer_matches`,
`market_quotes`, `raw_responses`, `raw_fetches` to `COUNTED` (absent tables are skipped already). Restore identity check already generic.
Must NOT copy `raw/`, change the manifest format, or relax the sha/identity/lock checks.

Regression tests (in `tests/test_backup_restore.py`): `test_an_nfl_store_is_backed_up_verified_and_restorable`,
`test_the_cli_accepts_nfl_and_soccer`, `test_a_default_backup_includes_every_existing_store_and_skips_absent_ones` — all 3 fail on snap3 (observed).
Compatibility: NBA/NHL backups unchanged (same files, manifest keys); README:213-216 "both stores" → "every store". No schema, hash or Telegram effect.
Live impact none (code); using it on live needs a go. Size S.

Interim manual procedure (for the user to approve; stdlib only, `mode=ro` source, never writes the source):
script `$W/manual_backup_nfl.py` — `python3 -I manual_backup_nfl.py REPO/data/nfl.sqlite ~/nba-predictor-backups/<UTC>-nfl`
(refuses an existing destination; online backup API; `integrity_check`, `foreign_key_check`, schema, identity, row counts, sha256;
manifest in app.backup's format so the new `--restore` can use it later). Demonstrated on a copy (`$W/s31/nfl.sqlite` → `$W/manualbk/demo-1`:
integrity ok, fk 0, schema 33, identity nfl, games 8230, sha 55759f7c…). Note: the read-only `$SP/stores/nfl.sqlite` (WAL header, file mode 444)
could not be opened `mode=ro` without a writable `-shm`; on the live store the jobs' `-shm` exists — that is how the lead's 15:29 backups were made.
Interim restore (until C3 code lands): bootout nfl-live and telegram; confirm `.nfl-jobs.lock` free; move `nfl.sqlite`, `-wal`, `-shm` to
`*.replaced-<UTC>`; copy the backup to `nfl.sqlite.restoring`, verify sha256 = manifest, rename; `release-check` for nfl; bootstrap. (Rehearsed on `$W/rb`.)

---

## C4 — Missing NFL raw artifacts (H3; confirmed defect, unrecoverable from local copies)
Inventory: `$SP/plan/nfl_missing_raw.csv` (295 rows + header: raw_id, source, endpoint, request_key, content_hash, byte_size, path,
first_retrieved_at, importer_version, fetch_count, games_with_last_raw_id). All `espn_nfl` scoreboard months `dates=200208…202610&limit=1000`,
raw_id 1–295, first_retrieved_at 2026-11-01T16:17:23Z–18:24:19Z, importer `espn-nfl-scoreboard-1`, 69,865,402 bytes uncompressed, 1 fetch each.
Built by `$W/missing_csv.py` (existence check of each `path` under REPO/data/raw, read-only).

Search (read-only; result per hash = **NOT FOUND for all 295**; no hash found anywhere):
- By filename (any basename containing a 64-hex sha in the set) — `$W/hashsearch.py`, logs `$W/hashsearch.log`, `hashsearch2.log`:
  REPO/data (incl. raw/ and backups/ — NBA/NHL sqlite backups, jsonl, launchd folders; no NFL store, no archives), ~/.codex/worktrees (incl. nfl-live worktree),
  ~/nba-predictor-live, all of /private/tmp (incl. the three session scratchpads 11c8ef97…, 9adf6978…, eaeb30dc…, `/tmp/courtside-active-preview-20261101`
  whose raw/espn_nfl has 214 files = copies of present ones), ~/nba-predictor-{research-data,backups,deep-data,pretrain,pretrain-logs,deep,launchd-backup-20261101-refresh},
  ~/autoresearch-sports, all ~/Desktop/nba-predictor-* worktrees (11 incl. REPO), ~/Desktop/backup, ~/projects, ~/Downloads, ~/Documents,
  ~/hearth-backups, ~/.claude: 2,066,268 files, 208 tar/zip archives listed (members checked), 0 matches. ~/.Trash: "Operation not permitted" (not searched).
- By content (uncompressed copies under another name): every file whose size equals one of the 295 byte_sizes (664 candidates, excluding .venv/node_modules)
  hashed with sha256 over /private/tmp, ~/Desktop, ~/nba-predictor-{live,research-data,backups,deep-data,pretrain}, ~/.codex, ~/projects, ~/Downloads, ~/Documents → 0 hits (`$W/sizesearch.py`).
- ~/nba-predictor-dev-data: only `find … -type f -name '*.json.gz' -path '*espn_nfl*'` → 0 files; nothing read or copied.
- /tmp/courtside-nfl-eval and /tmp/courtside-preview-20261101 (HANDOFF:398, the scratch the store came from): absent.
- Time Machine: `tmutil listlocalsnapshots /` → none; `tmutil destinationinfo` → "No destinations configured".

Recovery position (state plainly): the original bytes are gone from this machine. A new fetch of the same request cannot recreate them
unless its sha256 equals the recorded `content_hash`, which is unlikely for live ESPN payloads (they embed volatile fields). A future
user-approved re-fetch must be recorded as a NEW retrieval: new `raw_responses` row (new hash) + `raw_fetches.retrieved_at = now`, never
written under the old path/hash, never reusing raw_id 1–295, and never changing `games.result_available_at` or revisions of already-stored
facts to an earlier time (a re-import at a later time must not backdate anything). If (rarely) a re-fetched body's sha256 equals the old
`content_hash`, the file may be placed at the old path (that is then a byte-exact recovery, verifiable by `raw_cache.load`) and a new
`raw_fetches` row still records the new retrieval. Record in HANDOFF which rows remain byte-less. Network re-fetch needs the user's go and is
subject to C5 (ESPN terms bar automated collection).
Proposed change: `replay`/`nfl replay` refuse up front with the missing list (I9), `release-check` reports `raw_missing`; test
`test_nfl_replay_refuses_before_writing_when_bytes_are_missing` (fails on snap3: replay commits fetch 1 then raises FileNotFoundError, I9 p1d).
Live impact none (code); re-fetch needs go. Size S.

---

## C5 — Sources: suitability per use (M17; policy) — no proposal to accept prohibited collection
Evidence used: lane pages read 2026-11-07 (NBA-A F3: disneytermsofuse.com/english/ "Last updated May 24, 2024"; nba.com/termsofuse "July 13, 2026";
NHL-A F7: nhl.com/info/terms-of-service "October 29, 2025"; SOCCER-B: openfootball LICENSE.md re-fetched, football-data.co.uk, StatsBomb, football-data.org cached)
plus pages I read today (2026-11-07, WebFetch; quotes via the fetch summariser — a human should re-read the exact text):
1. https://github.com/nflverse/nflverse-data — repo labelled "CC-BY-4.0 license"; upstream not named.
2. https://raw.githubusercontent.com/nflverse/nflverse-data/master/LICENSE.md — "Attribution 4.0 International" (CC BY 4.0 legal code; covers "database").
3. https://nflreadr.nflverse.com/ — "NFL data accessed by this package belong to their respective owners"; "are governed by their terms of use".
4. https://github.com/nflverse/nfldata — no licence stated.
5. https://moneypuck.com/data.htm — "free to use for non-commercial purposes"; "Please clearly credit MoneyPuck.com"; unapproved scraping blocked; betting not mentioned.
6. https://www.sports-reference.com/termsofuse.html (May 19, 2023; covers basketball/hockey/pro-football-reference) — bars "use any automated means to access or use the Site" (without written permission) and databases "that compete"; reuse "welcomed, whether for commercial or non-commercial purposes" with credit.
7. https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use (effective June 7, 2023) — CC BY-SA 4.0; "these licenses do allow commercial uses"; bars automated uses "that are abusive or disruptive"; API/User-Agent policies apply.

| Source (sport) | (a) personal research display | (b) real-money ticket decisions | basis |
|---|---|---|---|
| ESPN site APIs (NBA results/odds/box; NHL odds/goalies; NFL; soccer) | Not supported for automated collection: 2.B(x) bars automated access for data mining/web scraping; 2.A excludes AI/ML training/testing/validation | Not supported (same clauses; 2.A "personal, noncommercial use only"; whether a personal bankroll is "commercial" = interpretation) | explicit text; applicability to the undocumented API = interpretation |
| NBA.com injury reports (NBA) | Download for "personal, noncommercial use" allowed (explicit) | §9: NBA Statistics "may not be used in connection with any gambling activity (including legal…)" — whether injury PDFs are "Statistics" = interpretation; which ticket inputs use them = unknown | explicit + interpretation |
| NHL.com / api.nhle.com / api-web (NHL) | Not supported for automated compiling/database: §2, §7 | Not supported (same); gambling not mentioned | explicit; whether the undocumented APIs are "Services" = interpretation; no recorded user decision |
| sdv team box (MIT code, ESPN content; NBA) | as ESPN | as ESPN | interpretation (content inherits ESPN terms) |
| openfootball (soccer results) | Suitable: CC0 "for any purpose whatsoever, including … commercial" | Suitable on licence text; provenance/upstream rights unknown (CC0 §4(c)) | explicit |
| nflverse-data (NFL schedules/results) | CC BY 4.0 label explicit, but nflreadr says the NFL data belong to their owners and their terms govern | Unknown (effective grant over upstream data not established) | explicit text both ways → unknown |
| Sports-Reference family (NBA/NHL/NFL) | Not for automated collection without written permission; manual download is not a workaround for automated use | same | explicit |
| MoneyPuck (NHL stats, game-by-game team data) | Non-commercial with credit; scraping blocked → manual download only | Unknown (non-commercial vs personal staking = interpretation; betting not mentioned) | explicit + interpretation |
| Wikipedia/Wikidata (all; results in season articles) | Licence permits reuse with attribution + share-alike; bot/API policies apply | Licence permits commercial reuse; data completeness/timeliness for schedules unknown (not assessed) | explicit; coverage unknown |
| football-data.co.uk, StatsBomb | Not suitable (explicit restrictions; robots disallow ClaudeBot) | Not suitable | explicit (SOCCER-B) |

Unsupported coverage (honest): within what I searched, no free source with clearly permissive terms covers **live/timely schedules and results for
NBA, NHL or NFL with automated collection**, nor **bookmaker odds for any sport**. Soccer results are covered only by openfootball (no odds, no
stats; volunteer-maintained, timeliness unknown; UEL only 2020-21..2024-25 per SOCCER-B). Not searched: Kaggle datasets, balldontlie / other keyed
APIs (keyed = needs asking per free-sources rule), The Odds API, sportsbookreviewsonline, UEFA, Understat, ClubElo terms, Retrosheet-style
projects for these sports. This is not a claim that none exists.
Proposed change (docs only): correct `espn.py:3` / `DATA_SOURCES.md:13` ("no published terms"); add the Disney 2.A ML clause, NBA §9, NHL §2/§7
to DATA_SOURCES.md; record per source the two-use verdict above. Decision for the user (not a request to accept prohibited use): whether to stop
using ticket outputs that depend on ESPN/NHL/NBA.com inputs, and whether to evaluate a permissive replacement per sport. Live impact none (docs).

---

## C6 — Host sleep and unattended gaps (M3 ops side; risk)
Evidence (read-only): `pmset -g` AC and battery `sleep 1` (minute), `powernap 1`, `standby 1`, laptop (battery 80 %, AC attached);
`pmset -g sched` only Apple wakes; `pmset -g log` shows Sleep→DarkWake cycles every ~1.5 min this morning (11:42–11:51 ET) without job runs.
Job gaps since NFL activation (store copies, `job_runs` started_at gaps > 30 min): all three stores stop together —
11-04 02:2xZ→13:51Z (~11.4 h), 11-04 22:0x–22:27Z→11-05 01:04Z (2.6–2.9 h), 11-05 11:xxZ→22:51Z (~11 h), 11-06 00:4xZ→23:38Z (~23 h);
3 NBA job rows stuck `running`. Consequence: 12 NBA + 3 NHL games with no canonical forecast, NFL result 22 h late (lanes), no alert.

Options for the user (decisions; no changes made):
1. AC keep-awake via power settings: `sudo pmset -c sleep 0` (display may still sleep: `displaysleep` unchanged). Simple; needs admin; lid closed
   without external display still sleeps; battery profile unchanged.
2. Keep-awake agent without sudo: a launchd KeepAlive job running `/usr/bin/caffeinate -i -s` (‑s holds system awake only on AC). Reversible by
   bootout; same lid caveat. Variant: wrap only the tick jobs in `caffeinate -i` (as adaptive-train does) — prevents sleep during a run but
   does not wake the Mac, so it does not close the gaps above.
3. Scheduled wake: `sudo pmset repeat wakeorpoweron MTWRFSU 11:00:00` (one repeat slot) + option 2 to stay awake through game windows; covers
   overnight sleep only if combined with 1/2.
4. Heartbeat + visible gap (code, M3 in another batch): on each tick, if the previous job row is > N min old, mark orphaned `running` rows
   `abandoned` and expose "no cycle ran from X to Y (host asleep or stopped)" in /api/freshness and coverage reasons; optionally one Telegram
   status line per gap (Telegram message = user go). Test: `test_a_tick_after_a_gap_closes_orphaned_runs_and_reports_the_gap` (fails today:
   orphans stay `running`, freshness reads only `job='refresh'`).
5. Accept and document: "Mac must be awake" (NFL_LIVE.md:15) extended to NBA/NHL; every-game coverage rule then has known holes.
Recommendation: 2 (AC-only, no sudo, reversible) + 4; 1 if the user prefers a system setting. Live impact: launchd/power settings → user go.
Size S (ops) / M (heartbeat code).

---

## Prioritized table
| # | Issue | Proposed change | Test (fails on snap3) | Live impact | Approval |
|---|---|---|---|---|---|
| 1 | N1 code lines diverged; tree would undo 3 live fixes + change forecast timing | merge `release` into dev line, keep 7a83e27 timing; X = single release commit | 11 release-branch tests (7a83e27, a55c874) | none until release | none (code); timing reversal = user decision |
| 2 | C3 no NFL backup | backup covers all store sports; default = existing stores | 3 tests in test_backup_restore.py (observed fail) | none | none; interim manual NFL backup on live = user go |
| 3 | C2 joint 31/31/33→35 release | sequence 0–8 + rollback (rehearsed on copies) | raw_missing warning test | release + migration + launchd | user go |
| 4 | H2/N3 telegram unguarded on dev code; state tied to checkout | `NBA_TELEGRAM_DIR`, guard before dispatch, move to X | 2 tests (observed fail) | release + launchd | user go |
| 5 | I12 nfl-live on dev venv | own venv now (interim) or live venv at X | interpreter-pin test (optional) | launchd | user go |
| 6 | N2 bot token + ledger copied into audit snapshots | lead deletes the 3×2 files from $SP/snap*; exclude in future snapshots | — | none | lead (scratch only) |
| 7 | C4 295 NFL bytes unrecoverable | replay/release-check preflight; HANDOFF note; any re-fetch = new retrieval | replay-refuses-before-writing test | none; re-fetch = network | user go for re-fetch (and C5) |
| 8 | C6 host sleep gaps | caffeinate AC agent and/or pmset; heartbeat/gap reporting | gap/orphan test | launchd/power | user go |
| 9 | M17 sources | docs corrections + per-use verdicts; user decides use of restricted inputs | — | none | user decision |
