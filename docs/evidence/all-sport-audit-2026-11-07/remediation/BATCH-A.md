# Remediation plan — BATCH A (safe local engineering fixes) — 2026-11-07

Reviewer: read-only plan agent (Claude Opus 5.5). Code read: `$SP/snap3` (tree 2275eab / git-diff 330feed). Release checkouts read
(read-only): `~/nba-predictor-live` a55c874 (ships schema 31, has **no** `nfl_live.py`), `~/.codex/worktrees/nfl-live-release/...`
8539513 (ships 33). Plists read read-only with `plutil -convert json -o -`. Store copies `$SP/stores/*.sqlite` opened `?immutable=1`.
All writes: `$SP/work/plan-A/` only. Nothing implemented. `SP=/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad`.

## 0. Facts every item depends on (observed)

**launchd environment (11 plists):**
| label | code imported from | venv | NBA_DATA_DIR | NBA_RELEASE_GUARD / ROOT | command(s) |
|---|---|---|---|---|---|
| odds, nhl-odds, daily, nhl-daily, injuries, nba-markets | `~/nba-predictor-live` (a55c874) | live venv | REPO/data | 1 / ~/nba-predictor-live | tick, `--sport nhl tick`, daily, `nhl odds; nhl daily`, injuries, markets |
| nfl-live | nfl-live-release worktree (8539513) | **REPO** venv | REPO/data | 1 / that worktree | `nfl tick` every 60 s |
| telegram | **REPO** (WorkingDirectory REPO/backend) | REPO venv | REPO/data | **none** | `telegram run --once` every 60 s |
| api | `~/nba-predictor-live` | live venv | REPO/data | none | uvicorn :8100 (no migrate; `db.connect` + `job_runs` check only, `main.py:87-104`) |
| adaptive-train (installed, **not loaded**; stays paused) | cwd `~/nba-predictor-live/backend` → a55c874 code | `~/nba-predictor-deep` venv | REPO/data | **none** | `training-inputs`, `adaptive-train` (both go through the migrating CLI path) |
| web | `~/nba-predictor-live/frontend` | node | — | — | vite preview :4173 |

All four checkouts share one git common dir: `REPO/.git` (REPO = main worktree; `~/nba-predictor-live`, nfl-live-release and
`~/nba-predictor-deep` are linked worktrees whose `.git` is a file).

**NFL definition hash today** (`nfl_live.py:18-20`, sha256 over the raw bytes of 7 files next to the running `nfl_live.py`):
snap3 = REPO working tree = nfl-live worktree = activation.json = `b6074877a7e7ccfe` (activated 2026-11-02T22:11:33Z).
Telegram computes it from **REPO** files, the nfl-live job from **8539513** files, the dev API from REPO files. a55c874 (live
API/web) has no NFL live code at all, so the always-on app never shows NFL forecasts either way.

**Consequence used below:** any edit saved in REPO to `nfl_live.py, nfl_eval.py, coverage.py, markets.py, models/base.py,
models/elo.py, models/points.py` changes REPO's `definition()` → within 60 s Telegram's `nfl_live.activation()` returns None →
`telegram.py:264-266` silently `continue`s past NFL (no NFL cards, no status) — a *live* effect of a *local* edit, before any
commit or release. A2, A3 must therefore be implemented outside REPO's working tree (separate worktree/branch) and land in REPO
only in the switch window (§A3 sequencing). A4 and A5 are designed so they touch **none** of the 7 hashed files.

Already fixed in snap3 (relevant to this batch): Canonical.tsx:242 Performance calibration table now wrapped (`.table-wrap`);
Canonical.tsx:85 grade-history line already shows `(OT)/(SO)` from `result_revisions.decision` (only the regrade history, not
the main Result cell). Nothing in snap3 addresses H1, M3, M4, M5, M10's main rows or M14 (diffed config/db/release/
freshness/nfl_live/schemas/Canonical vs HEAD).

---

## A1 (H1) — development commands can migrate/write the live stores

**Issue — confirmed defect.** `config.py:43-44` `data_dir()` defaults to `REPO_ROOT/"data"` (= the live store when run from
REPO). Every non-read-only CLI command opens and migrates: `cli.py:579-582` (generic: lock → `db.connect` → `db.migrate` →
`claim_store`), `cli.py:676-680` (`nfl` actions), `cli.py:731-736` (`soccer`), `cli.py:409-412`, `492-496` (tuning-study,
deep-study `conn_for`). `db.migrate` (`db.py:99-120`) applies every pending file unconditionally; `db.connect` (`db.py:22-35`)
`mkdir`s and creates the file. The only guard, `release.check` (`release.py:59-84`), runs only when `NBA_RELEASE_GUARD=1`
and only for `GUARDED` commands. README:71-74 ("Run manually") + "Data store" (README:224-229, `.venv/bin/python -m app.cli
migrate` with no `NBA_DATA_DIR`) point a fresh developer straight at REPO/data. Affected: NBA, NHL, NFL live stores (31/31/33);
soccer (a `soccer ingest` from REPO would create `REPO/data/soccer.sqlite` at 35). Consequence: one mistyped dev command moves a
live store to 35 → every guarded job then refuses ("store schema 35 differs from this release's schema 31/33") → no odds, no
forecasts, no NFL ticks until a restore.

**Reproduction (observed, scratch copy):** fake main checkout `$SP/work/plan-A/h1/fakerepo/{.git/,data/nfl.sqlite}` (copy of
live NFL backup, schema 33), `env -u NBA_DATA_DIR`, `config.REPO_ROOT = fakerepo`, `cli.main(["nfl","replay"])` from snap3 →
command **failed** (`FileNotFoundError`, H3's missing raw bytes) **but the store was already migrated 33 → 35** (`before 33
shipped 35 … after 35`). INTEGRATION I1 reproduced the same for NBA/NHL.

**Proposed change (minimal):**
1. `config.py`: `protected_data_dir(path) -> bool`: true when `path.resolve() == (REPO_ROOT/"data").resolve()` **or**
   `(path.resolve().parent / ".git").is_dir()` (the data folder at the root of a *main* git checkout — exactly
   `nba-predictor-sprint1/data`; linked worktrees have a `.git` *file*, dev copies/tests/scratch have none). No subprocess, no
   import-time work (Telegram imports this module from REPO every minute).
2. `release.py`: `migration_context(sport) -> list[str]` = `release.check(sport)["problems"]` minus the single "store schema …
   differs" line, plus "NBA_RELEASE_GUARD is not 1" when unset. Empty list = the documented release-migration environment
   (README:203-212 step 4 + "command `migrate`"). No new flag: the existing release procedure already uses exactly that env.
3. `cli.py`: one helper `_store_write_allowed(sport, explicit_migrate: bool) -> Optional[str]` called **before** `store_lock`
   / `db.connect` in the generic path (after the release-guard block, `cli.py:551-558`), in `_nfl` before line 676, in `_soccer`
   before line 731, and in the two `conn_for` closures. If `protected_data_dir(config.data_dir())` and
   `release.store_schema(sport) != release.shipped_schema()` (includes "no store yet"): allowed only when `explicit_migrate`
   (the `migrate` command / `nfl migrate` / `soccer migrate`) **and** `migration_context(sport) == []`; otherwise print
   `{"status":"refused","reason":"<store> is a protected live store at schema X; this checkout ships Y; …use scripts/dev.sh or
   NBA_DATA_DIR=<copy>, or the release migration procedure"}` and exit 1. No job row (these are manual commands; the scheduled
   ones are already caught by `release.check`).
4. `db.migrate(conn, upto=None, *, explicit=False)`: defence in depth — if there are pending migrations, the main db file
   (`PRAGMA database_list`) is in a protected dir, and not (`explicit` and `release.migration_context` empty) → raise
   `StoreMismatch`-like `ProtectedStoreError` before the first transaction. Catches future call sites and `preview_publish`
   misuse. (`preview_publish` migrates `dev/.publish-staging/…` — not protected; `research_demo` uses `:memory:`.)
5. README:71-74 and :224-229: prefix with `NBA_DATA_DIR=<copy>` / "use scripts/dev.sh"; state the refusal.
6. Optional (not needed for H1): `tests/conftest.py` autouse fixture that sets `NBA_DATA_DIR` to `tmp_path` when a test did
   not, so no test can ever touch REPO/data.

Must NOT change: `release.check` semantics/messages (existing tests assert them); `connect_readonly`; behaviour on unprotected
dirs (dev.sh copies — it already runs an explicit `migrate` on the copy, `scripts/dev.sh:149-150` — preview staging, tests,
scratch); Telegram dispatch (`cli.py:333`, before any store open; uses `connect_readonly`).

**Regression tests** — new `backend/tests/test_live_store_guard.py` (temp dirs only; a fake main checkout = `tmp/repo/.git/`
dir + `tmp/repo/data`, `monkeypatch.setattr(config, "REPO_ROOT", tmp/repo)`, `delenv NBA_DATA_DIR`):
- `test_implicit_command_never_migrates_the_checkout_store`: NHL store migrated `upto=shipped-1`; patch `cli._run` to return 0;
  `cli.main(["--sport","nhl","score"])` → exit 1, `MAX(version)` still shipped-1. **Fails on snap3**: exit 0, schema = shipped
  (same mechanism as the reproduction above).
- `test_explicit_migrate_outside_a_release_checkout_is_refused`: `cli.main(["--sport","nhl","migrate"])` → refused, schema
  unchanged. **Fails on snap3** (migrates).
- `test_no_store_file_is_created_in_a_protected_dir`: `cli.main(["soccer","ingest",…])` with no soccer store → refused and
  `not (data/"soccer.sqlite").exists()`. **Fails on snap3** (file created + migrated before the network call).
- `test_release_migration_context_may_migrate` (compat): `git_repo()` release root (reuse helper from test_release_guard.py),
  `NBA_RELEASE_GUARD=1`, `NBA_RELEASE_ROOT`, monkeypatch `release.code_root`, explicit `migrate` → applied.
- `test_copies_still_migrate_implicitly` (compat): `NBA_DATA_DIR=tmp/copy` → implicit upgrade unchanged.
- `test_store_at_shipped_schema_is_not_blocked` (compat): protected dir, schema == shipped → guarded/unguarded commands proceed
  (what every scheduled job sees after a release).
- `test_db_migrate_refuses_protected_pending_without_context` (unit on `db.migrate`).

**Compatibility:** guarded jobs (a55c874, 8539513) never reach a pending migration (release.check refuses any mismatch
earlier), and they don't contain this code until a release anyway; after a release they run with schema == shipped → no-op.
Unguarded release-checkout processes (api, adaptive-train) don't migrate when schema == shipped; adaptive-train (paused) would
get a visible refusal instead of a silent upgrade if a release ever shipped an unapplied migration — the intended behaviour.
Telegram (REPO code, live): only `config`/`cli` import paths change; the new helpers are lazy, never called on its path —
but a syntax/import error in `config.py`/`cli.py`/`db.py` in REPO would break Telegram within 60 s: run the focused tests
before saving into REPO. NBA/NHL/NFL/soccer all share the path. Schema: none. Existing tests: test_release_guard
`test_the_cli_refuses_before_any_request_lock_or_migration…` patches `db.migrate` — unaffected; tests calling
`db.migrate(c)` positionally unaffected (new kw-only arg).

**Live impact:** none (code + tests in the dev tree; release checkouts unchanged). **Approval:** none for code/tests; README
wording none. **Size:** S–M. **Dependencies:** none; land first (protects every later batch).

---

## A2 (M4) — NFL line forecasts ignore `price_label`

**Issue — confirmed latent defect.** `nfl_live.py:112-114` selects `market_quotes` by `sport, game_id, market, retrieved_at
window ORDER BY retrieved_at DESC, quote_id DESC` with no `price_label='current'`. Every other quote reader filters or prefers
current (`market_live.py:48`, `near_tipoff.py:59/63`, `research_live.py:289/294`, `paper_trading.py:49`, `real.py:144/148/755-758`
prefer current at equal time). Correct today only because `espn.parse_lines` (`sources/espn.py:333`) emits `open` before
`close→current` per provider, so the current row gets the larger quote_id. It also sets `market_quote_id` for the **winner**
row (line 117/122), so the winner's linked quote has the same flaw. Live today: NFL store has only DraftKings, 5,388 open +
5,388 current; all 10 recorded NFL forecasts link `current` quotes (or none for team totals) — no affected live row yet.
Breaks if a provider/fetch has an `open` block without `close`, a second provider, or a parser re-order.

**Reproduction (observed):** `$SP/work/NFL-B/scripts/repro.py` run against snap3 into `$SP/work/plan-A/nflb`:
`quote rows: [(1,'current',-3.0),(2,'open',-7.0)]` → `spread forecast line used: (-7.0, 'bookmaker', 2)`.

**Proposed change:** add `AND price_label='current'` to the query at `nfl_live.py:112` (one predicate; all NFL markets incl.
winner). No other NFL quote reader exists (`grep market_quotes`: coverage.py:694 reads by stored `market_quote_id`; nfl.py,
nfl_eval live paths don't read `market_quotes`). Must NOT change: the 2-hour window, the fall-back to `markets.model_line`
when no current quote, `line_source` values.

**Interim zero-impact guard (can land now, no hashed file):** `tests/test_sources.py::test_line_quotes_emit_open_before_current_per_provider`
pinning the parser order that today's NFL selection relies on — passes on snap3 (documents the dependency; not a regression
test for the fix).

**Regression test:** `tests/test_nfl_live.py::test_line_forecast_uses_current_quote_when_open_is_inserted_later(live_store)`:
after the fixture, insert (same `fetch_id/raw_id` as the last `raw_fetches` row, `retrieved_at='2026-11-02T22:00:00Z'`) a
`current` spread −3.0 then an `open` spread −7.0; `nfl_live.record(c, now=NOW)`; assert spread row `line == -3.0` and its
`market_quote_id` is the current row; same for winner's `market_quote_id`. **Fails on snap3** (observed −7.0 / quote 2).

**Compatibility — activation hash (the critical part):** `nfl_live.py` is one of the 7 hashed files → this one-line edit
changes `definition()` from `b6074877…`. Effects, precisely:
- `activation()` (`nfl_live.py:27-37`) compares `activation.json["definition"]` to the *running checkout's* `definition()`.
- The file read is `config.data_dir()/research/nfl_live/activation.json` = `REPO/data/research/nfl_live/activation.json` for
  the job, Telegram and the live dev API alike; dev.sh copies `research/` into the dev data dir.
- Where the edit lands → what the user sees:
  - in **REPO** (even uncommitted): Telegram NFL cards stop at the next minute (silent `continue`, `telegram.py:264-266`);
    dev app NFL pages lose all forecasts/history (`Views.attach` skips, `report()` `active: False`), freshness note says "NFL
    forecasts are not supported: schedule and results only" (`freshness.py:155-163`).
  - in the **nfl-live worktree**: `jobs.tick` NFL branch (`jobs.py:825-828`) returns `{"status":"unsupported", …}` →
    exit 0, **no job row** → no new NFL forecasts, nothing visible (M3).
- Recovery requires `nfl activate --sealed --report --access-log` (`cli.py:600-613` → `nfl_live.activate`, writes the live
  `activation.json`). With today's `activate()`, a definition change creates a **new** `activated_at`; `Views.cohort/report`
  use `began=activated_at`, so the 10 existing NFL records (Nov 2–7) fall out of the record/report. → do A2 only together with
  A3 (one re-activation, history preserved) — see A3 sequencing.

Other sports: untouched. Schema: none. Release: nfl-live worktree needs a new commit = 8539513 + this patch (schema 33; the dev
tree's 0035 must NOT ride along — `release.check` is exact-match, "35 vs 33" would stop every NFL tick).
**Live impact:** needs NFL release + re-activation. **Approval:** user go for the worktree move and for `nfl activate` (live
write). **Size:** S (code) / M (sequencing). **Dependencies:** bundle with A3.

---

## A3 (M5) — NFL forecasts disappear after unrelated shared-file edits

**Issue — confirmed defect + newly found under-coverage.** `nfl_live.py:18-20` hashes whole raw files including shared
`coverage.py` (NBA/NHL canonical logic, 900+ lines), `markets.py`, `models/base.py`, `models/elo.py`: a comment-only edit
deactivates NFL (INTEGRATION I4 reproduced; `test_stale_source_inactive_and_definition_changes_refuse` only monkeypatches
`definition`, so it can't see this). **Also too narrow** (not in the audit report): the NFL serving path reads code that is
*not* hashed — `sports.py` (`MARKETS = sports.NFL.markets`, nfl_live.py:15: which markets are recorded), `threshold_overview.py`
(`to.percentage_index/percentage_record` in `Views`), `nfl.py` (`_require_nfl`), `backtest.py`/`metrics.py` (via nfl_eval),
`freshness.py` (the `record()` gate). A change there alters NFL forecasts/labels without deactivation.

**Reproduction:** INTEGRATION I4 (comment edit in coverage.py → `activation()` None). Mechanism verified by reading; hashes of
the 7 files per checkout listed in §0.

**Proposed design (recommended): serving definition v2 = digest of the NFL call closure, normalized.**
- `nfl_definition.py` (new module, so the rule is reviewable on its own): starting from entry points
  `nfl_live.record`, `nfl_live.Views` (all methods), `nfl_live._game`, and the `nfl_eval` functions they call, walk `ast` of
  `app/**.py` and collect every module-level function/class/constant reached through `Name`/`module.attr`/`from-import`
  references (transitively, across modules: coverage, markets, models.*, sports, threshold_overview, nfl, freshness,
  backtest, metrics, db.utc_now …). Unresolvable references (getattr, star imports) → include the whole module (safe
  direction).
- Per reached symbol: digest of its **token stream** (`tokenize`, dropping COMMENT/NL tokens and the docstring STRING) — not
  `ast.dump` (its format differs across Python versions; Telegram/nfl-live use the REPO venv, a future release venv may differ).
- `definition()` returns `"v2:" + sha256(sorted(symbol→digest))[:16]`; `definition_detail()` returns the map.
  `activate()` stores both; on mismatch `activation_status()` returns `{"active": False, "reason": "serving code changed
  since activation", "changed": [symbols]}`.
- Make deactivation visible (files not hashed): `telegram.py:264-266` records `result["sports"]["nfl"] = {"status":
  "inactive", "reason": …}` instead of a silent `continue`; `freshness.summary` `forecasts_note` for NFL uses the reason
  ("NFL forecasts paused: serving code changed since activation (coverage._insert)") instead of "not supported"; `jobs.tick`
  NFL branch returns `status: "inactive"` with the reason (still no per-minute row — see A5 for the after-the-fact count).
- Re-activation keeps continuity: `activate()` on a definition change appends `{definition, detail, activated_at}` to
  `activation.json["history"]` and keeps the first `activated_at` as `recording_began_at` for `Views`; new rows already carry
  the new definition in `model_version` (`nfl_live.py:119`). (Product choice → user decides; alternative = fresh record,
  earlier 10 rows only in history.)
- Fallback if the closure walker is judged too much for this batch: per-file token-normalized digests of the same 7 files
  **plus** sports.py/threshold_overview.py/nfl.py (fixes the comment-only case and the under-coverage; still deactivates on
  functional NBA-only edits to coverage.py/markets.py). Not "silent weakening" either way.

Must NOT change: strictness of `record()` (any reached-symbol change → no new forecasts); seal/test checks in `activation()`;
`model_version` format; reserved-season access (re-activation calls `_check_frozen` on open data only).

**Regression tests** (`tests/test_nfl_definition.py`; copy `app/` into `tmp_path`, edit the copy, compute `definition(root=copy)`):
- `test_comment_and_docstring_edits_keep_the_definition`: add a comment + edit a docstring inside a *reached* coverage function
  → unchanged. **Fails on snap3** (raw-byte hash changes).
- `test_functional_edit_to_an_nba_only_function_keeps_it`: change a coverage.py function the closure does not reach (the test
  first asserts it is not in `definition_detail()`) → unchanged. **Fails on snap3.**
- `test_edit_to_nfl_settlement_changes_it`: change `markets.settle` (push/mid rule) → changed. Passes on both (safety kept).
- `test_edit_to_the_nfl_market_list_changes_it`: change `sports.NFL` markets tuple → changed. **Fails on snap3** (sports.py
  not hashed — demonstrates the under-coverage).
- `test_edit_to_nfl_quote_selection_changes_it`: change the SQL string in `nfl_live.record` → changed.
- `test_reactivation_keeps_recording_began_and_history` (if continuity approved): activate, change definition, re-activate →
  `Views.report()["recording_began_at"]` = first activation; earlier rows still listed. **Fails on snap3** (new activated_at).
- `test_telegram_reports_inactive_nfl_reason`: telegram `run` with mismatched definition → `sports.nfl.status == "inactive"`.
  **Fails on snap3** (key absent).
- Existing `test_stale_source_inactive_and_definition_changes_refuse` keeps passing.

**Sequencing A2 + A3 (needs the user's go; describe to them exactly this):**
0. Implement A2+A3 in a separate worktree/branch based on **8539513** (schema 33) — never in REPO's working tree, because
   Telegram runs REPO code and would drop NFL immediately. Run NFL focused tests there (test_nfl_live, test_nfl, test_nfl_api,
   test_nfl_eval, test_telegram, new test_nfl_definition) and the same on the dev tree with the patch applied in a scratch copy.
1. Pick a window with no NFL game within T−120 (NFL: Sun/Mon/Thu; e.g. a Tuesday/Wednesday daytime).
2. User go → (a) copy `REPO/data/research/nfl_live/activation.json` to a dated backup; (b) fast-forward the nfl-live worktree
   to the new commit (clean checkout → `release.check` passes, schema 33 = 33); (c) apply the identical NFL-path code to REPO
   (so Telegram's and the job's v2 definitions agree — any divergence in a reached symbol deactivates one side, by design);
   (d) from the nfl-live worktree: `NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT=<worktree> NBA_DATA_DIR=REPO/data python -m app.cli nfl
   activate --sealed … --report … --access-log …` (same files as on 2026-11-02).
3. Verify: from both checkouts `python -c "from app import nfl_live; print(nfl_live.activation_status())"` → active; next
   `nfl tick` output has `forecasts.status` in (`not_due`,`ok`); dev app NFL history still shows the Nov 2–7 records; Telegram
   result JSON shows `nfl` status.
   Between 2(b) and 2(d) (minutes): NFL forecasts paused, Telegram NFL silent (but now with a reason in its result).
**Live impact:** NFL release + re-activation (live write of activation.json). **Approval:** user go. **Size:** M.
**Dependencies:** carries A2. Also: H2 (moving Telegram to a release worktree, Batch C) would remove step 2(c)'s coupling.

---

## A4 (M10 + M14) — NHL OT/SO on finals; History tables overflow at 581–~710 px

### M10 — confirmed defect (display)
`schemas.py:227-230` `Outcome{home_score, away_score, home_won}`; built at `providers/real.py:161-162` (`_summary`: Today and
Game pages), `:604-605` (market history), `:721-722` (legacy prediction history), `:790-791` (canonical history). NHL
`game_decisions` exists (copy: REG 12,406 / OT 2,525 / SO 1,048; 2026-27 regular: 200/38/19); NBA and NFL stores have **0** rows
(table exists in all — shared 0012). Frontend shows score only: `GamePage.tsx:159-160,176`, `TodayPage.tsx:218` ("FINAL"),
`Canonical.tsx:148`, `HistoryPage.tsx:187`, `MarketView.tsx:77,167`. So a correct reg3 "regulation draw" pick on an OT game reads
"… UTA 4 – 5 MTL | Wrong"-style rows with no OT shown.

**Change:** `Outcome.decision: Optional[Literal["REG","OT","SO"]] = None` (+ description); `RealProvider._outcome(g, decisions)`
helper used at the four sites, with decisions fetched in one batch per response via the existing read-only
`coverage._decisions(conn, ids)` (`coverage.py:515-523`) — **call it, do not edit coverage.py** (coverage.py is in the NFL
hash today). `types.ts:48` add `decision?: 'REG'|'OT'|'SO'|null`. New `frontend/src/components/outcome.ts` (pure, types-only
import, like `competition.ts`): `finalScore(o, away, home)` → `"UTA 4 – 5 MTL (OT)"`, `finalStatus(o)` → `FINAL`, `FINAL/OT`,
`FINAL/SO`; used at the 7 render sites. Must NOT change: `home_won` (SO winner keeps its stored score semantics), grading,
NBA/NFL output (decision null → identical strings).

**Tests:**
- Backend `tests/test_api_markets.py` (or test_canonical.py) `test_nhl_outcome_carries_regulation_decision`: NHL store, final
  4–5 with `game_decisions` OT and one SO, one REG; GET `/api/games/{id}?sport=nhl`, `/api/games?sport=nhl&date=…`,
  canonical history → `outcome.decision` = OT/SO/REG; NBA final → `decision is None`. **Fails on snap3** (field absent).
- Frontend `src/components/outcome.test.mjs` (node:test, same pattern as competition.test.mjs): `finalScore` adds `(OT)`/`(SO)`
  and nothing for REG/null; `finalStatus` → `FINAL/OT`. **Fails on snap3** (module absent; the real assertion is the label).
  `package.json` `test:unit` → `node --test --experimental-strip-types src/components/*.test.mjs` (Node 22.23.2 per web plist).

### M14 — confirmed defect (layout)
`Canonical.tsx:135` `<table className="table history">` is not inside `.table-wrap` (`styles.css:161`); cards only at
`max-width: 580px` (`styles.css:283-295`). Measured by lead (BROWSER-lead.md): page scrollWidth NBA History 694 @600; NHL
History 710 @600 and 710 @700. The same unwrapped pattern exists at `HistoryPage.tsx:176` (legacy history) and
`MarketView.tsx:155` (market history) — not measured, same risk.

**Change:** wrap all three in `<div className="table-wrap">…</div>` (the pattern Canonical.tsx:232/242 already uses). No CSS
change; at ≤580 px the card layout still applies inside the wrapper.

**Tests:** `src/components/tables.test.mjs` (node:test, reads the `.tsx` sources as text): every `<table className="table
history">` is directly preceded by `<div className="table-wrap">`. **Fails on snap3** (3 unwrapped). Plus the browser
protocol (manual, recorded): dev instance on a **copy**; same-origin iframe of exact width 600, 640, 700, 760, 800 (and 390,
1440 regression) for NBA/NHL `/history` (each market tab incl. reg3), `/history?run=live`, NHL `/performance`, NFL `/history`;
pass = `document.documentElement.scrollWidth === innerWidth` and each history table's scroll container `scrollWidth >
clientWidth` only inside `.table-wrap`. Re-use `window.measure2` harness from BROWSER-lead.md.

**Compatibility (A4):** no hashed NFL file touched (schemas.py, providers/real.py, frontend only) → NFL activation unaffected;
Telegram imports `real.py`/`schemas.py` from REPO live (`RealProvider.ranked_picks`): the change must not alter
`ranked_picks` output (add the decision only to `Outcome`; Telegram message formatting reads its own fields — verify with
`tests/test_telegram.py`). NFL pages use `nfl_live.Views`/NFL schemas (not `Outcome`) → unchanged; soccer has its own
`decided_in` (precedent, unchanged). Schema/migration: none (game_decisions since 0012, present in live 31/33). Release
checkouts unchanged until a release; live web (a55c874) keeps showing score-only until then.
**Live impact:** none now; visible on the live app only after a release (needs go). **Approval:** none for code/tests.
**Size:** S (M10) + S (M14). **Dependencies:** none; can land independently of A1–A3.

---

## A5 (M3) — missed forecast windows, stuck runs and non-refresh failures are invisible

**Issue — confirmed defect (observability).** `freshness.py:72-80` `_runs` and `summary` (`:136-150`) read only
`job='refresh'`; `failing` = refresh failures since last refresh success. Not surfaced anywhere in the freshness bar:
(a) odds / near_tipoff / markets / injuries failures, including release-guard refusals (`release.record_refusal` writes job
`odds`, `release.py:91-103`; NFL refusals also as `odds`); (b) rows stuck `running` (NBA copy: run 6126 near_tipoff started
2026-11-04T22:27:50Z, 6465 2026-11-06T00:40:12Z — both at the start of the sleep windows; 6933 20:10:15Z was in flight at
backup time); (c) whole-host gaps (no rows at all); (d) games whose 2-hour window passed with **no** canonical row — computed
on the copies (competitive, after recording began, not postponed/canceled, no `game_forecasts` row for that game+tipoff):
NBA 12 since recording began (7 in the last 7 days: 6 on 2026-11-05, 1 on 11-06), NHL 3 in the last 7 days (2026020247/248/261)
plus Oct 20–21 and Oct 27–28 batches; NFL 0. Per-game "missing" rows exist in History and `canonical_summary` counts them,
but nothing says "the system missed windows" on the pages people watch. NFL `blocked`/`inactive` ticks exit 0 with no row
(`jobs.py:825-828`). Observed on the NHL copy: last odds run `failed` 2026-11-07T07:50:35Z while `summary()` reports
`failing: False`, `last_failed_at: 2026-10-27T19:41:12Z` (a refresh) and nothing about odds.

**Proposed change (smallest honest surfacing; read-only queries; `failing`/`source_state` semantics unchanged):**
`freshness.summary` gains, each via the `_one`-style guarded helper (an old/new schema or missing table → `None`, never an
exception):
- `job_failures`: for each job other than `refresh`, if its **latest** row is `failed`, `{job, finished_at, reason}` (reason
  from `detail.reason`/`error`; release-guard refusals show "release guard refused …").
- `unfinished_runs`: count + oldest `started_at` of `status='running'` rows older than 3 h. (Declared display rule: the longest
  completed run in either store is 122 min — boxscores/training_inputs; near_tipoff max 73 min. Label: "started > 3 h ago,
  never finished — the process stopped (sleep, crash or kill)".)
- `missed_forecasts`: count + latest game over the last 7 days, using the SQL above with `began = coverage.recording_began`
  (NBA/NHL) or the NFL activation file's `activated_at` (read raw, independent of the definition check).
- `last_job_run_at`: `MAX(started_at)` over all job_runs (lets the bar say "no scheduled job has run since …" during an outage).
`schemas.py` `DataFreshness` + `types.ts` + `Freshness.tsx`: show flags "Odds job failed 07:50", "2 job runs never finished",
"7 games in the last 7 days had no forecast (no forecast cycle ran in the 2-hour window)" — the existing `is-warn` styling;
`failing` and its "Last refresh failed" flag unchanged.
Out of scope here (mention only): keep-awake/`caffeinate`/pmset and launchd changes → Batch C (ops); a per-tick NFL row would
need a new `job_runs.job` value (CHECK constraint → migration) → not Batch A.

Must NOT change: `failing`, `source_state`, `plan()` backoff — **Telegram (`telegram.py:273-275`, REPO code, live) and
`nfl_live.record` (`nfl_live.py:72-74`) stop picks/forecasts when `failing` is true**; widening `failing` would silently stop
live NBA/NHL Telegram cards and (after an NFL release) NFL recording. Must not raise on schema-31/33 stores (an exception in
`summary` → Telegram marks that sport `failed` and sends nothing).

**Regression tests** (`tests/test_freshness.py`):
- `test_summary_counts_games_whose_window_passed_without_a_forecast`: one game with a `game_forecasts` row (sets began), one
  later competitive final with none → `missed_forecasts.count == 1`; a postponed one not counted. **Fails on snap3** (key).
- `test_unfinished_runs_are_reported_without_changing_failing`: `running` row 4 h old → `unfinished_runs.count == 1`,
  `failing is False`. **Fails on snap3.**
- `test_odds_and_release_guard_failures_show_but_do_not_change_failing`: `release.record_refusal("nfl","tick",info)` on a
  temp NFL store → `job_failures[0].job == "odds"` with the refusal reason; `failing is False`; `telegram`'s gate input
  unchanged. **Fails on snap3.**
- `test_summary_on_old_schemas_never_raises` (compat): stores migrated `upto=31` and `upto=33` → summary returns.
- Existing `test_telegram.py` / `test_nfl_live.py` freshness monkeypatches keep passing.

**Compatibility:** all sports (soccer store has job_runs too; soccer page uses its own import-time text — verify
`/api/freshness?sport=soccer` still validates). Telegram picks up the new freshness.py within 60 s of saving into REPO —
safe only because `failing`/`source_state` are untouched and the new queries cannot raise; still run test_telegram before
saving. freshness.py is not in the NFL hash (today); under A3-v2 it would be (it's on the `record()` path) — so land A5
before A3's activation, or accept that A5 edits after A3 deactivate NFL only if they touch reached symbols (`summary` is
reached → yes). **Recommend: land A5 before the A2/A3 switch.** Performance: 4 small indexed queries per poll.
**Live impact:** none (dev app only; live app after a release). **Approval:** none for code/tests. **Size:** M.
**Dependencies:** before A3 activation (see above).

---

## Prioritized table
| # | Issue | Proposed change | Failing-first test | Live impact | Approval |
|---|---|---|---|---|---|
| 1 | A1 / H1 dev commands migrate live stores | protected-dir rule (`config`), pre-open check in 3 CLI paths + `conn_for`, `db.migrate` defence, migration only via documented release env; README | test_live_store_guard.py (implicit, explicit, no-file, + 3 compat) | none | none |
| 2 | A4 / M14 History overflow 600–710 px | `.table-wrap` around 3 `.table.history` | tables.test.mjs + browser protocol 600/640/700/760/800 | none until release | none |
| 3 | A4 / M10 NHL OT/SO hidden | `Outcome.decision` via `coverage._decisions` (no coverage.py edit); `outcome.ts` labels at 7 sites | API test (NHL OT/SO/REG, NBA null) + outcome.test.mjs | none until release | none |
| 4 | A5 / M3 silent gaps | freshness: job_failures, unfinished_runs (>3 h), missed_forecasts (7 d), last_job_run_at; bar flags; `failing` unchanged | 3 tests in test_freshness.py + old-schema compat | none until release | none |
| 5 | A3 / M5 NFL hash over-broad **and** incomplete | v2 definition = token-normalized digest of the NFL call closure; visible inactive reason; re-activation keeps history | test_nfl_definition.py (comment, NBA-only edit, sports.py edit, settlement edit, re-activation, telegram reason) | NFL release + re-activation | **user go** |
| 6 | A2 / M4 NFL quote ignores price_label | `AND price_label='current'` at nfl_live.py:112 (bundled with A3) | test_line_forecast_uses_current_quote_when_open_is_inserted_later (repro'd −7.0) | same switch as A3 | **user go** |

Order: A1 → A4 → A5 (all in REPO, none touches the 7 hashed files) → A2+A3 built off 8539513 in a separate worktree, then the
one approved switch (backup activation.json → move nfl-live worktree → identical NFL-path code into REPO → `nfl activate` →
verify). Nothing here changes launchd, migrates a live store, trains, or contacts a network.

Non-claims: no test in this plan was run except the two reproductions (H1 migration on a scratch copy; NFL-B quote repro on
snap3); "fails on snap3" for the others is by reading the code. Closure-walker feasibility (A3) is a design judgement, not
prototyped.
