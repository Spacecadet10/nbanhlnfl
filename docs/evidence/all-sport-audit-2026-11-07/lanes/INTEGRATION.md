# INTEGRATION — cross-sport review (snap2, frozen 20:38Z)

Code audited: `$SP/snap2` (snap→snap2 changes: 0035 index, soccer reclassify branch, SOCCER_IMPORTER_VERSION 2, one test).
All reproductions ran snap2 code against fixture stores or store copies in `$SP/work/INTEGRATION/` (scripts in `scripts/`,
outputs `p1a.txt p1b.txt p1c.txt p1d.txt p3.txt p5.txt p9.txt`). Nothing was written to REPO, REPO/data, the live checkouts or
launchd. Python: REPO/backend/.venv, `PYTHONPATH=$SP/snap2/backend[:tests]`.

## 1. Findings (most severe first)

### I1 HIGH — A write command run from the dev checkout without NBA_DATA_DIR migrates the live stores to 35, which makes the release guard refuse every scheduled NBA, NHL and NFL job (confirms NBA-C HIGH-2, adds NFL and evidence)
- Code: `backend/app/config.py:43-44` (data_dir defaults to `REPO_ROOT/data`); `cli.py:580-582` (every non-read-only command:
  `db.connect` → `db.migrate` → `claim_store`); `cli.py:678-681` (`nfl` actions), `cli.py:734-736` (`soccer`, migrates before
  the identity check); `release.py:80-84` (exact schema equality). The release guard runs only when `NBA_RELEASE_GUARD=1`
  (`cli.py:551`), which no dev command sets. `devpaths` guards only dev.sh/preview_publish.
- Evidence (copies only):
  - `$W/tree` = copy of snap2/backend; `env -u NBA_DATA_DIR python -m app.cli --sport nhl migrate` → created
    `$W/tree/data/nhl.sqlite` with 0001…0035 applied. No guard ran. In REPO the same command targets `REPO/data`.
  - Live release code (`~/nba-predictor-live`, a55c874, ships 31) `release.check()` on store copies migrated to 35:
    `nba/nhl: "store schema 35 differs from this release's schema 31"`. NFL release worktree (`8539513`, ships 33):
    `nfl: "store schema 35 differs from this release's schema 33"`. launchd plists confirm `NBA_RELEASE_GUARD=1` on
    daily/injuries/markets/nhl-daily/nhl-odds/odds/nfl-live.
- Why: one accidental `refresh`, `replay`, `migrate` or `nfl tick` from REPO stops collection and forecasting for all three
  live sports, and the store cannot be migrated back.
- Fix (text): refuse write commands from a non-release checkout when `data_dir()` resolves to the live data directory (the one
  the plists name), unless an explicit override is given. Alternatively, drop the `REPO/data` default and require NBA_DATA_DIR.
  Also see I2 (no backup path for NFL/soccer). Status: NBA-C HIGH-2 (not reproduced there); reproduced here on copies.

### I2 HIGH (NEW) — The backup/restore tool covers only NBA and NHL. The NFL store cannot be rebuilt (295 raw files missing) and has no supported backup before the 0035 release migration
- Code: `backend/app/backup.py:23` `SPORTS = ("nba", "nhl")`. `backup.py:169` argparse `choices=SPORTS`. `backup.py:162`
  `sport_defs.get(sport)` raises for data-only sports. README.md:213-216 release procedure: "online SQLite backup of both stores".
- Evidence: `NBA_DATA_DIR=$W/mig python -m app.backup --dest $W/bk --sport nfl` →
  `error: argument --sport: invalid choice: 'nfl' (choose from nba, nhl)`. A default backup wrote only `nba.sqlite`,
  `nhl.sqlite`, `manifest.json`.
- Why: 0035 is a parent rebuild migration and runs in the NFL store as well. With NFL-A F1 (295/510 responses without bytes),
  a failed or bad NFL migration could not be recovered from source or from a tool backup. Soccer has no live store yet, so the
  same gap is latent there.
- Fix: base backup's sport list on `sports.store()` (all four sports, skipping missing files). Put the NFL backup in the 0035
  release checklist.

### I3 MEDIUM — 0035's header claim ("a team change on a final with an unchanged score is a recorded correction") is false for every final that already exists, in all four sports. That includes soccer, which SOCCER-A marked "Fixed"
- Code: `ingest.py:547-549` (no new revision when the latest revision has NULL teams); `soccer.py:207` (the same rule,
  `teams_same = last["home_team_id"] is None or …`); 0035 adds the columns without a backfill (`0035…sql:16-18`, `:113`).
- Evidence (`p1a.txt`): store at 0034, final A at t1, migrate to 0035, same score with a changed team at t2:
  - nba `row ('espn:20','espn:11', avail t2)` `revs [(1,124,115,None,None,t1)]`
  - nfl `row ('nfl:16','nfl:21', avail t2)` `revs [(1,23,17,None,None,t1)]`
  - nhl `row ('nhl:7','nhl:16', avail t2)` `revs [(1,4,1,None,None,t1)]`
  - soccer (0034 store built with HEAD code, then snap2): `('soccer:370','soccer:362', avail 21:00)`
    `revs [(1,1,1,None,None,20:00)]`
  - The same scenario on fresh 0035 stores records revision 2 in every sport.
  - In each case the games row and `result_available_at` change, but the append-only log has no entry for the change.
  - Affected now: NBA 9,611, NHL 16,003 and NFL 8,108 finals on the store copies (all their revisions will have NULL teams
    after migration). Soccer copy: 147 of 171 revisions have NULL teams.
- Why: a correction moves availability without leaving a revision, so grading or as-of reads cannot explain it.
  The existing test (`test_replay_same_store.py::test_a_team_change_on_a_final_is_a_recorded_correction`) covers only a fresh
  NBA store.
- Fix hazard (`p9.txt`): the NHL importer calls `_record_result_revision` for every final that is not stale, including
  "seen" (`ingest.py:520-523`). A naive fix that treats NULL teams as "different" would append a revision for every NHL final on
  the next season-list refresh. NBA/NFL call it only on inserted/updated.
- Fix: have `_upsert_game` / `_upsert_match` return an explicit `teams_changed` flag and append on that flag. Add one
  parametrized failing-first test (nba/nfl/nhl/soccer, `migrate(upto=34)` → final → migrate → team change), plus an NHL test
  that an identical newer retrieval of a pre-0035 final adds no revision. Fix ingest.py and soccer.py together.

### I4 MEDIUM — Editing a shared NBA/NHL file hides every NFL forecast in that checkout and stops NFL Telegram cards (confirms NFL-C F3 by reproduction)
- Code: `nfl_live.py:18-20` hashes `nfl_live.py, nfl_eval.py, coverage.py, markets.py, models/base.py, models/elo.py,
  models/points.py`. The shared files are coverage.py and markets.py (NBA/NHL canonical and markets), and models/base.py and
  models/elo.py (NBA Elo primary, NHL, backtest). Readers gated on `activation()`: `nfl.py:80` (capabilities),
  `freshness.py:153`, `telegram.py:265` (NFL silently skipped by `continue`), and `jobs.py:827` (producer).
- Evidence:
  - The hash is `b6074877a7e7ccfe` in snap2, REPO and the nfl-live-release worktree, and in `data/research/nfl_live/activation.json`.
  - Copy `$W/tree` with a one-line comment appended to `coverage.py`: `definition 098febf5e23d928c activation valid: False`,
    capabilities forecasts `[False]`, freshness `forecasts_supported: False` (before: True/True/True).
- Why: Telegram runs REPO `backend/` (plist WorkingDirectory). The first lane fix to coverage.py, for example NBA-B F2 or
  NHL-C F2, turns NFL dark in Telegram and in the dev UI. NflPages.tsx:106 then says "NFL is data only". The pinned NFL
  producer keeps recording, so the stored rows and the UI disagree.
- Fix: keep the hash check for the producer (`record`) only. Readers should show stored rows by their `model_version` and label
  a hash mismatch. Alternatively, hash only NFL-owned code plus explicit function-level fingerprints.

### I5 MEDIUM — NBA-B F2 and NHL-C F2 have one root cause: the canonical row is stamped when coverage.record runs, which is after all research steps
- Code: `jobs.py:396-438` runs primary, then markets, research, pool, pool_selection, tracking and adaptive, and only then
  `coverage.record` (last). `coverage.py:345` stamps `generated = now if fixed else utc_now()` at write time. Quote
  eligibility and lead time read that stamp: `providers/real.py:821,841,893,974` (`_winner_quote_prob(…, record.generated_at)`)
  and `Canonical.tsx:72` "made N min before the start". NFL writes `game_forecasts` from `nfl_live.record` and is not affected.
- Evidence (`game_forecasts`, status='forecast', `generated_at − source_generated_at`):

  | store | source | rows | avg min | max min | >10 min | source_generated_at NULL |
  |---|---|---|---|---|---|---|
  | NBA | primary_recorded | 901 | 11.8 | 70.4 | 373 | 0 |
  | NBA | primary_direct | 177 | – | – | – | 177 |
  | NHL | primary_recorded | 841 | 9.6 | 53.9 | 207 | 0 |
  | NHL | primary_direct | 126 | – | – | – | 126 |
  | NFL | primary_direct | 10 | 0.0 | 0.0 | 0 | 0 |

  The same column means different things by sport: NULL for NBA/NHL direct rows, equal to `generated_at` for NFL direct rows.
- Fix: one change in shared code. Record coverage straight after the primary step, and use `source_generated_at` for the quote
  window and the lead time. This touches coverage.py, so I4 must be resolved first or NFL goes dark.

### I6 MEDIUM — A malformed final aborts a whole response. There are two code sites, not three: NBA and NFL share one parser, NHL has its own copy of the same pattern, and soccer differs
- Code:
  - `sources/espn.py:153-154` `int(sides[..]["score"]) if final` in `_parse_scoreboard`, used by NBA (`parse_scoreboard`)
    and NFL (`parse_nfl_scoreboard`, `espn.py:113`). This is one root cause for NBA-A F2 and NFL-A F4.
  - `sources/nhl_stats.py:121-122` `int(g["homeScore"])`, a separate copy for NHL-A F3.
  - The shared design choice is one transaction per response (`ingest.py:54, 78, 487` + replay `:290`).
  - The parse runs before any row is written, so one bad event discards every event in that response.
- Evidence (`p1b.txt`): NBA final with `"score": ""` → `ValueError`, games 0, raw_fetches 0. NFL: same. NHL `homeScore: null` →
  `TypeError`, games 0, raw_fetches 0. Soccer empty score: imported, `matches_inserted 2, rejected 0`. The bad match is stored
  as `other` with no rejection row; soccer_rejections records only home==away (`soccer.py:99-104`). Soccer event without
  `date` → `KeyError`, matches 0: soccer also aborts for any other malformed field. In every case the gz file is already
  written (`raw_cache.py:29-34`) but no fetch is logged, so the bad response leaves no trace in the store.
- Fix: per-event try/except inside the parsers, plus one shared append-only `import_rejections` table (sport, event, reason,
  raw_id, fetch_id) used by all four importers. The rejected event keeps its previous row and is not set to 'other', which
  avoids NFL-A F4's 7-day forecast block.

### I7 MEDIUM — "replay rebuilds the same rows" (`ingest.py:1-3`) has three different behaviours. Same-store replay never re-derives NBA/NHL/NFL; soccer re-derives only its ending fields and logs that as a source revision; a fresh rebuild re-derives everything
- Code: `ingest.py:410-413` (a retrieval that is not newer is "stale", skipped as a whole: no game, decision or revision).
  `soccer.py:146-157` (same retrieval → re-derive `decided_in`/`decision_note`, return "reclassified") → `soccer.py:110`
  → `_record_revision`.
- Evidence (`p1c.txt`):
  - NHL preseason period-5 game imported under the old rule → `('OT',5)`. Same-store replay →
    `games_updated 0, ('OT',5)` (unchanged). Fresh rebuild from the same log → `('SO',5)`, revision 1 'SO'.
    This confirms NHL-A F2's mechanism.
  - Soccer, same fixture as snap2's test: replay `{revisions:1, reclassified:1}`, revisions
    `[(1,'regulation',T1,raw 1,fetch 1),(2,'unknown',T1,raw 1,fetch 1)]`, i.e. the same raw, fetch and `observed_at`.
    `updated_at` is unchanged. This confirms SOCCER-C MEDIUM-1 and SOCCER-A F2.
- Resolution of the contradiction: the docstring holds only when the importer has not changed since the rows were written and
  every byte is present. The two pipelines have chosen opposite policies for an importer fix. NBA/NHL/NFL: never applied to
  stored rows. Soccer: applied, but recorded as if the source had sent a new version. Neither marks the provenance.
- Fix: one shared policy. Add a `basis`/`reason` column to both revision tables (result_revisions already has `basis`:
  'observed'|'backfill'; add 'rederived'). Add an explicit, logged `rederive` command, separate from `replay`, for importer
  version changes. Make `replay` purely idempotent. Changing NHL decisions changes `nhl_eval._decisions_digest` (NHL-B
  fingerprints), so it needs a go.

### I8 MEDIUM — Store identity is enforced in the DB only for market_quotes, market_forecasts and soccer_* tables. games, teams, game_forecasts and raw_responses accept any sport in any store, soccer included (extends NFL-A F3)
- Evidence (`p3.txt`, fresh stores claimed nba/nhl/nfl/soccer; plain SQL):
  - ALLOWED in every store: teams `soccer:999`, `espn:999`, `nfl:998`; games `nfl:1` (in the NBA store and in the soccer
    store); `game_forecasts` with `sport='nhl'` in the NBA and soccer stores; raw_responses/raw_fetches with source
    `espn_soccer`.
  - REFUSED in non-soccer stores: soccer_matches ("soccer matches belong only in a soccer store"), via the 0034/0035 triggers.
- Cross-sport side effect: an `espn_soccer` fetch row in an NBA/NHL/NFL store changes that store's `freshness.revision`
  (`65b60c14…` → `cd9e4790…`). This is only reachable through a writer that does not claim the store; every current writer
  claims first (soccer.py:77-78 claims before `store_fetch`).
- Fix: BEFORE INSERT triggers on games, teams and game_forecasts that check the prefix or sport against `store_identity`,
  active only when an identity exists (NBA legacy stores may be unlabelled). Also a source-to-sport check on raw_responses.

### I9 LOW — Missing raw bytes: replay fails loudly but leaves a fresh rebuild half-built. No release, backup or publish path checks for bytes (refines NFL-A F1)
- Code: `raw_cache.missing_files` is called only by `cli.py:432` (adaptive-train). `release.check` (`release.py:61-87`)
  checks git, env and schema. `backup.py:5` deliberately does not copy `raw/`. `preview_publish` copies only stores. dev.sh
  copies `raw/` once at startup.
- Evidence (`p1d.txt`): 2 NFL fetches with the second file deleted. `missing_files` lists it, and `release.check` problems
  contain nothing about raw. Fresh replay raises `FileNotFoundError` after committing fetch 1 (each fetch is its own
  transaction, `ingest.py:290`), leaving `games ['nfl:401671001']` with no marker. A same-store replay raises
  `FileNotFoundError` too, although it would change nothing.
  `coverage.source_schedule` (`coverage.py:755-761`) catches the error and reports `available: False`. That is the only
  silent-but-labelled path. NBA and NHL have 0 missing (lead), so only NFL is affected today.
- Fix: run `missing_files` up front in `replay`/`nfl replay` and `soccer replay`, and add it to `release.check(sport)` as a
  warning.

### I10 LOW — Fallbacks to NBA or NHL: an enumeration. None mixes sports' data today; reachable inputs are listed
| Branch | Falls into | Reachable today by |
|---|---|---|
| `main.py:26` `SPORT = Query("nba", …)` (served endpoints) | NBA store | any served-API call without `?sport` (e.g. `/api/games/nfl:…`): NBA store lookup → 404, never another sport's data (BROWSER-lead) |
| `main.py:118` `LEAGUE` default nba; pattern excludes soccer | NBA | `/api/freshness` without sport; soccer is not polled (`freshness.ts:29`) |
| frontend `competition.ts:26-29` `leagueFrom` → 'nba' | NBA screens | link without/unknown/uppercase `?sport` (NHL-C F7, NFL-C F6). Only soccer ids derive the league from the path; `nhl:`/`nfl:` ids do not |
| `Selection.tsx:44-47` `useSport` → 'nba' for nfl/soccer | NBA forecast pages | not reachable: Layout.tsx:147 renders NflScreens/SoccerScreens instead of `<Outlet/>` |
| `db.py:26` `connect()` → nba.sqlite | NBA store | any caller without path/sport; none found in app (`grep "db.connect()"` → 0) |
| `jobs.py:66,90,818`, `coverage.py:466`, `live.py:69` `or "nba"` / `(None)` | NBA dispatch | only an unlabelled store (all 4 store copies are labelled; cli claims after migrate); API `_provider` accepts unlabelled only for nba (`main.py:97`) |
| `jobs.py:818-820` tick | refuses non nba/nhl/nfl | soccer store → ValueError (correct) |
| `ingest._match_game` `ingest.py:605-615` else → NHL name matching | NHL logic | only with literal "nhl"; soccer never calls it |
| `coverage.source_schedule` `coverage.py:741-757` else → ESPN NBA source | NBA source | guarded: `live.engine` raises for nfl/soccer before the branch |
| `ingest.SOURCE_SPORTS` lacks `espn_soccer`; `ingest.replay` on a soccer store | no-op | `cli --sport …` cannot open soccer; returns zeros silently if called |
| research modules `(nba … else nhl)` (adaptive_replay, cadence_daily, combination_*, retrain_cadence, diagnostics…) | NHL | `--sport` choices = `SPORT_IDS` (nba, nhl) at `cli.py:61`, so not reachable with nfl/soccer |
| `backup.py:23` | nba/nhl only | see I2 |

### I11 LOW — Shared UI assumptions: which come from one shared component
- `Canonical.tsx` (CanonicalBlock/GradeText/GRADE_LABEL, used by Today/Game/History/Performance/Overview and NflPages):
  - `:38` void label "Void (tie, start changed…)": NFL tie wording, shown for every sport.
  - `:72` "made N min before the start": I5.
  - `:85` decision suffix appears only inside the regraded-revision note.
  - Cause of NHL-C F1: shared `schemas.Outcome` (`schemas.py:227-230`) has no decision, so no shared view can show OT/SO.
    NflPages (`FINAL · TIE`) and SoccerPages (PENS) label their own endings.
- Draws: soccer pages are separate (SoccerPages.tsx does not import Canonical). NHL reg3's draw is the `mid` side, labelled by
  the backend `pick_label` / `result_label`.
- "Data-only NFL" wording: `Layout.tsx:124` (unconditional for NFL) and `NflPages.tsx:290` (Daily Learning). `NflPages.tsx:106`
  is conditional on capabilities, so with I4 it flips to "NFL is data only".
- NBA wording on NFL: `coverage.py:588` "legacy views (Within 2 hours, Pre-game archive)", shared reason text (NFL-C F2).

### I12 LOW — Environment: the production nfl-live job uses the dev venv, which has the deep extra and lacks the research extra. Numerics are identical
- Jobs by venv (plists):
  - REPO/backend/.venv: telegram (code = REPO working tree, no guard) and nfl-live (code = nfl-live-release worktree, guard on;
    `import app` resolves to the worktree because cwd comes first, `app.__file__` checked. The venv's editable `.pth` points at
    REPO/backend, so any other cwd would run REPO code).
  - `~/nba-predictor-live/.venv`: api, daily, injuries, markets, nhl-*, odds.
  - `~/nba-predictor-deep/.venv`: adaptive-train (live code, no guard).
- importlib.metadata: numpy 2.5.3, scipy 1.18.1, scikit-learn 1.9.1, fastapi, pydantic, httpx and uvicorn are identical in
  repo, live and deep. The repo venv has torch 2.14.1 and tensorflow 2.21.0 (+keras); live does not. Live has pandas 2.3.3,
  ngboost, lifelines and matplotlib; repo does not.
- Importing `app.telegram, app.nfl_live, app.jobs, app.providers.real, app.main, app.cli` loads none of torch, tensorflow,
  pandas, ngboost, catboost, optuna or sklearn (`sys.modules` check). So the deep extra changes no import used by these jobs.
- pyproject.toml:23 says deep is "research venv only, never the release venv". The repo venv is the nfl-live production venv,
  so the claim is contradicted in practice. Candidate availability (`candidates/registry.py:177`, ngboost) differs between the
  repo and live venvs. That matters only for research views served from the repo venv. Matches NBA-C LOW-12.

## 2. Item 5 (fingerprints): verified unchanged
`freshness.revision` HEAD vs snap2 (`p5.txt`):

| store | state | HEAD | snap2 |
|---|---|---|---|
| nba | 31 | 9d3358c16d17eaf8 | 9d3358c16d17eaf8 |
| nhl | 31 | 6b4c54973c7e8cb7 | 6b4c54973c7e8cb7 |
| nfl | 33 | 0f8561a290a61821 | 0f8561a290a61821 |
| nba/nhl/nfl | after migrate to 35 (empty soccer tables) | same values as above | same values as above |
| soccer | 35 | 65b60c14… | 610851e3… (intended) |

0035 migrated the copies in 0.1-0.6 s with no FK violations. `preview_publish.SPORTS` adds soccer. `_state` returns None when
the live soccer store does not exist, so it is skipped. Staged copies are migrated, never the live store
(`preview_publish.py:107-110`). Caveat: the revision is unchanged only while no `espn_soccer` fetch rows are in a non-soccer
store (I8).

## 3. Item 9: fixes in shared files, other sports affected, and the regression test that would catch it
| Proposed fix (lane) | Shared file | Other sports hit | Existing test that would catch a regression |
|---|---|---|---|
| team-change revision with pre-0035 rows (NBA-A F1, I3) | ingest.py (+soccer.py) | NHL, NFL, soccer; NHL "seen" flood hazard | `test_replay_same_store.py::test_rows_stored_before_migration_0035_replay_without_duplicates` (replay only); **no test** for a pre-0035 team change or an NHL "seen" final → add |
| per-event rejection (NBA-A F2, NFL-A F4, NHL-A F3) | espn.py `_parse_scoreboard`, nhl_stats.py, ingest.py | NBA+NFL share the parser | `test_storage.py`/`test_nfl.py` parser tests; **no malformed-final test** in any sport |
| re-derive NHL decisions (NHL-A F2) | ingest.py `_upsert_game`/import_nhl_fetch | NBA/NFL replay idempotency; NHL eval digest | `test_replay_same_store.py` (all 4 cases), `test_multisport.py::test_nhl_reimport_is_idempotent_and_replay_rebuilds_the_same_store`, `test_result_revisions.py::test_a_changed_regulation_decision_alone_is_a_revision` |
| NFL replay imports lines (NFL-A F2) | ingest.py replay | NBA/NHL replay order | `test_review_fixes.py::test_replay_matches_nhl_quotes_as_live_ingestion_did`; `test_nfl.py::test_replay_rebuilds_identical_nfl_games` compares games only → extend to market_quotes |
| DB identity triggers (NFL-A F3, I8) | migrations (all stores) | all; unlabelled NBA store | `test_multisport.py::test_quotes_and_forecasts_of_the_other_sport_are_rejected_by_the_store`; `test_storage.py::test_migrations_are_idempotent` (asserts the migration list, needs updating) |
| coverage first / source_generated_at (NBA-B F2, NHL-C F2) | jobs.py, coverage.py, real.py, Canonical.tsx | NBA+NHL; **NFL goes dark via I4** | `test_canonical_markets.py::test_a_bookmaker_comparison_needs_the_same_market_line_and_a_quote_before_the_forecast`, `test_live.py::test_market_comparison_ignores_quotes_retrieved_after_the_forecast`; `test_nfl_live.py::test_stale_source_inactive_and_definition_changes_refuse` asserts the refusal, so it *passes* while NFL goes dark: **no test** catches the operational effect |
| freshness failing/verdict fixes (NBA-C M-5, NFL-C F1) | freshness.py | all 4 (revision feeds the publisher) | `test_freshness.py::test_summary_states_and_revision`, `::test_odds_only_change_moves_the_revision`, `::test_publisher_republishes_only_changed_data_and_readers_see_whole_revisions` |
| OT/SO in Outcome (NHL-C F1) | schemas.py, Canonical.tsx, GamePage/TodayPage | NBA (decision NULL), NFL views share CanonicalBlock | no frontend component tests; only `frontend/src/components/competition.test.mjs` exists |
| league from game-id prefix (NHL-C F7, NFL-C F6) | competition.ts | all | `competition.test.mjs` (soccer cases only) → add nhl:/nfl: cases |
| NFL wording by capabilities (NFL-C F2) | Layout.tsx, coverage.py:588 reason | coverage.py edit → I4 | none |
| soccer revision provenance (SOCCER-C M-1) | soccer.py only (but align with I7) | none directly | `test_soccer.py::test_replaying_the_last_retrieval_reclassifies…` asserts the current (same-observed_at) behaviour and would need changing |

Rule for every coverage.py, markets.py, models/base.py or models/elo.py edit: re-activate NFL or decouple the hash first (I4).

## 4. Lane findings: CONFIRMED / CONTRADICTED / NOT REPRODUCED
- CONFIRMED (reproduced here): NBA-A F1 (extended to NFL, NHL and soccer); NBA-A F2 = NFL-A F4 (malformed part; same parser);
  NHL-A F3 (separate copy); NHL-A F2 (mechanism, with a synthetic old-rule fixture; the 62-row count is not recounted);
  SOCCER-C MEDIUM-1 and SOCCER-A F2 (same raw, fetch and observed_at; updated_at unchanged); NFL-A F1 effect on replay
  (loud failure, partial fresh rebuild; the 295 count comes from the lead and is not recounted); NFL-A F3 (extended to the NBA
  and soccer stores, teams and raw); NBA-C HIGH-2 (now reproduced on copies, plus NFL); NFL-C F3 (by reproduction);
  NBA-B F2 = NHL-C F2 (one root cause; numbers match: NBA max 70.4 min, NHL avg 9.6 / max 53.9); NFL-C F2 (wording lines);
  NHL-C F1 root cause (shared `Outcome`); NBA-C LOW-12 (versions).
- CONTRADICTED: SOCCER-A verdict "MEDIUM: team change on a final with same score leaves no revision — Fixed". It is fixed
  only for revisions written after 0035; a migrated soccer store still drops it (p1a.txt; 147/171 revisions in the soccer
  copy have NULL teams). The NBA-A F1 sport list "NBA, NFL, NHL" is incomplete: soccer.py has the same rule. NFL-A F4 /
  NBA-A F2 / NHL-A F3 as "three issues": two parser sites, one shared transaction design.
- NOT REPRODUCED / not run: NFL-A F4 7-day forecast block for status 'other' (code read only); NHL-A F2's exact 62 count on
  the store; the operational effect of I1 on the real launchd jobs (shown with `release.check` on copies only); NBA-C HIGH-1
  beyond reading the plist.

## 5. Checks run
| Command | Expected | Actual | Result |
|---|---|---|---|
| `python scripts/p1a_teams.py $W/p1a` (+ HEAD-built soccer 0034 via `p1a_soccer_head.py`, `p1a_soccer_snap2.py`) | revision 2 after a team change | pre-0035: none in 4 sports; fresh 0035: present | defect confirmed |
| `python scripts/p1b_malformed.py $W/p1b` | other events still imported | NBA/NFL/NHL abort, 0 rows; soccer empty score imported as other; soccer missing date aborts | defect confirmed |
| `python scripts/p1c_rederive.py $W/p1c` | — | NHL same-store keeps OT, fresh gives SO; soccer 2 revisions with the same observed_at | confirmed |
| `python scripts/p1d_missing.py $W/p1d` | up-front refusal | mid-run FileNotFoundError, partial fresh store; release.check silent on raw | confirmed |
| `python scripts/p3_identity.py $W/p3` + sqlite3 inserts | cross-sport rows refused | refused only for soccer_matches in non-soccer stores | confirmed |
| `python scripts/p5_rev.py` (HEAD vs snap2 revision; migrate copies to 35) | unchanged for nba/nhl/nfl | unchanged at 31/33 and at 35 | pass |
| `python scripts/p9_seen.py $W/p9` | — | NHL calls `_record_result_revision` on a seen final, NBA does not | confirmed |
| `env -u NBA_DATA_DIR python -m app.cli --sport nhl migrate` in `$W/tree` copy | — | migrated to 35 into `<tree>/data`, no guard | confirmed |
| live `release.check` (`~/nba-predictor-live`, nfl-live-release worktree) with NBA_DATA_DIR=$W/mig (35) | — | schema mismatch problems for nba, nhl, nfl | confirmed |
| `python -m app.backup --dest … --sport nfl` (NBA_DATA_DIR=$W/mig) | — | invalid choice 'nfl' | confirmed |
| `nfl_live.definition()` after comment edit in `$W/tree` copy | — | activation False, capabilities False, forecasts_supported False | confirmed |
| `pytest -q -p no:cacheprovider tests/test_replay_same_store.py tests/test_multisport.py test_nfl_live.py::test_stale_source_inactive_and_definition_changes_refuse test_freshness.py::test_summary_states_and_revision test_soccer.py::test_replaying_the_last_retrieval_reclassifies…` (snap2) | pass | 51 passed | pass (they do not cover I3/I6 scenarios) |
| sqlite3 lag queries on `$SP/stores/{nba,nhl,nfl}.sqlite` (immutable) | — | table in I5 | done |
| importlib.metadata listing of 3 venvs; `sys.modules` after importing job entry modules | — | I12 | done |

Not run: the full test suite (the gate is running elsewhere); anything against REPO/data or launchd; NBA/NFL "rule changed,
rows not re-derived" fresh-vs-store diffs (only NHL and NFL lanes ran these); frontend build/tests.

## 6. Open questions for the lead
- Should importer fixes be applied to stored rows at all (I7)? This needs one product rule for all sports before any fix to
  NHL-A F2 or SOCCER-C M-1.
- Release plan for 0035: back up the NFL store by hand (I2) and decide when to move all three release checkouts together
  (31/33 → 35), since release.check requires exact equality.
- Decoupling the NFL definition hash (I4) should come before any coverage.py or markets.py fix.
