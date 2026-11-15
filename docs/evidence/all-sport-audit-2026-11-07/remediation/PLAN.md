# Remediation plan — 2026-11-07 (revised after independent challenge)

Inputs (all in this folder): BATCH-A.md (engineering), BATCH-B.md (forecast/evaluation integrity), BATCH-C.md
(operations/recovery/sources), nfl_missing_raw.csv (295 rows), CHALLENGE.md (independent challenge, verdict REVISE; all 12
required changes adopted below). Audit: ../REPORT.md. Readiness: gate run 3 exit 0 on code tree 2275eab (HEAD 79ff6ec +
uncommitted); post-gate changes documentation only. Nothing in this plan has been implemented.

## Constraints (apply to every item)
K1 Working tree is live. `com.nba-predictor.telegram` imports REPO/backend every 60 s (39 app modules at import: config, db,
   cli, coverage, ingest, schemas, sources.espn, release, raw_cache, …; real.py/threshold_overview lazily). Any edit saved in
   REPO — by any session, including `ce` — changes live Telegram behaviour at once; a syntax error breaks it.
   LANDING RULE: no batch is applied to REPO's working tree until Telegram runs from a guarded release checkout (item 14), or
   the user gives an explicit go for a named landing window. "Live impact" below means impact at landing.
K2 NFL activation hash (b6074877) covers nfl_live.py, nfl_eval.py, coverage.py, markets.py, models/base.py, models/elo.py,
   models/points.py. Editing any of them where NFL is served deactivates NFL silently. Avoid them until item 9 (with a
   user-approved re-activation).
K3 The dev line lacks the live releases (7a83e27, a55c874, 8539513 are not ancestors of HEAD). Merge simulation: conflicts in
   cli.py (2), release.py (1), jobs.py (6), freshness.py (4); none in config.py, db.py, backup.py. Items touching jobs.py/
   freshness.py (5, 8) come after item 13; first-batch tests are re-run on the merged base X.
K4 Schemas: live 31/31/33, tree 35 (+0036 if item 11). release.check is exact-match → joint release (item 15).
K5 Standing: training paused (adaptive-train stays unloaded); no launchd change, live migration, network re-fetch, Telegram
   message, training, purchase, deployment or commit without a go; never backdate; never amend an evaluation rule
   retroactively; sources free AND suitable for the use — no item asks the user to declare restricted collection acceptable;
   a re-fetch is a new retrieval, never recovery unless sha256 matches; passing checks ≠ acceptance of models or sources;
   no soccer S2.

## Prioritized table
Type: D = confirmed defect, R = risk/latent defect, P = policy/decision. Live impact = at landing (K1).
| P | Issue | Proposed change | Regression test (fails on snap3) | Live impact | Approval |
|---|---|---|---|---|---|
| 0 | Safe place to work (process) | Default (no commit): `git worktree add --detach <wt> HEAD`; apply `git diff --binary HEAD` + copy untracked non-ignored files; `git -C <wt> add -A` in the worktree's own index; verify code-tree hash = 2275eab with the gate recipe; check `app.__file__` resolves into <wt>. Only REPO change: `.git/worktrees/<name>`. Option: a commit on a branch if the user requests one. | hash check + `app.__file__` check recorded | none | none (option B); user request (commit) |
| 0b | Evidence durability (R) | Evidence already copied to docs/evidence/all-sport-audit-2026-11-07/ (report, lanes, plan, CSV); writer also copies the batch's written failing tests there. 15:29 store copies and S1 raw bytes remain session-local (live stores are the source; S1 bytes: see deferred list). | — | none | none |
| 1 | H1 dev commands can write/migrate live stores (R, reproduced on copies: `score` 34→35 exit 0; `nfl migrate` 33→35; unguarded `nfl replay` migrated before crashing) | Protected-dir rule: REPO/data (and any data dir at a main checkout root) refuses store-opening WRITES at ANY schema unless in the verified release env (NBA_RELEASE_GUARD + clean release root); `db.migrate` backstop; in-memory/explicit scratch exempt; mandatory conftest fixture so tests never touch REPO/data; README L71-74 | test_live_store_guard.py: implicit default dir, explicit NBA_DATA_DIR=REPO/data, no-file, SAME-schema write refused, in-memory allowed, compat (dev.sh copy, guarded job env, Telegram read-only, API read) | at landing: dev commands against data/ refuse (intended); guarded jobs/Telegram/API unaffected (verified by tests) | none for code; landing per K1 |
| 2 | H3 NFL has no supported backup (D) | backup.py backs up every store sport present (changes the documented "both stores" output — stated in README) | 3 tests test_backup_restore.py (observed fail) + restore round-trip | none | none for code; a manual backup of live NFL = user go |
| 3 | M14 History overflow 581–710 px (D, measured 694/600, 710/700) | `.table-wrap` around the 3 `.table.history` tables (Canonical.tsx:135, HistoryPage.tsx:176, MarketView.tsx:155) | tables.test.mjs + browser 600/640/700/760/800 px | frontend only, at release | none |
| 3b | M11 NFL "data only" text (D, frontend part) | Layout.tsx:124, NflPages.tsx:290,295 wording; NOT coverage.py:588 (NFL-hashed, K2 → item 9) | text test in a node test | frontend only | none |
| 6 | C4/I9 replay/migrate do not preflight raw bytes (D; trivial fixture does NOT show it — snap3 raises before writing when the FIRST file is missing; defect is migration-before-check and half-built rebuilds) | preflight runs before the CLI-level migrate and before any replay transaction; full missing-bytes scan reported only by `release-check`; states soccer coverage | fixture: a later raw file missing → store schema and rows unchanged, clear error; release-check lists missing ids | none (read-only checks) | none |
| 7 | M7 one malformed final aborts a whole response (R, fixtures) | per-event rejection rows in espn/nhl_stats parsers (as soccer does) | per-sport fixture with one empty-score final | parsers are Telegram-imported → landing rule | none for code |
| 4 | M10 NHL OT/SO hidden (D) | `Outcome.decision` via existing `coverage._decisions` (no coverage.py edit); outcome.ts labels at 7 sites | API test NHL REG/OT/SO, NBA/NFL null; outcome.test.mjs | API/frontend at release (schemas.py is Telegram-imported → K1) | none |
| 13 | K3 dev line missing live fixes (D, release risk) | merge release lines into dev; single release commit X; keep 7a83e27 timing (reversal = user decision) | 11 release-branch tests; re-run batch 1 tests on X | none until release | commit/merge = user request; timing = user decision |
| 5 | M3 missed windows silent (D) | freshness: non-refresh job failures incl. guard refusals, runs `running` > 3 h, games past window with no forecast (7 d), last job run; `failing` unchanged (Telegram/NFL depend on it) — AFTER 13 (freshness.py conflicts) | 3 tests test_freshness.py + old-schema compat | at release | none |
| 12 | M12 API describes job settings from its own env (D) | derive "collection on/off" from stored job runs/captures, not the API process env | API test with env unset but job rows present | at release | none |
| 14 | H2 Telegram unguarded on dev code; state tied to checkout (D) | NBA_TELEGRAM_DIR for token/ledger; guard check before dispatch (cli.py:333 precedes guard at :551); move job to X | 2 tests (observed fail) | release + launchd | user go |
| 15 | C2 joint schema release 31/31/33→35(+36) + rollback (rehearsed on copies; a55c874 reads 35 identically; old jobs refuse 35) | sequence per BATCH-C with corrections: migrate command run WITH guard/root env; no untracked `.venv-X` inside a release checkout (dirty → guard refuses); adaptive-train plist gets guard/root if item 1 is adopted (stays unloaded) | raw-missing warning test | migration + launchd | user go |
| 16 | I12 nfl-live on dev venv (D) | live venv at X | interpreter pin test | launchd | user go |
| 7b | H4 reused seasons shown as "held up"/"strong" (D evidence + P) | season_use.json from preserved logs; dated disclosure appended after EVALUATION_PLAN:104 naming the recorded user-requested amendments (PLAYER_RATINGS_STUDY §3) — rule text unchanged; held_up no longer shown as confirmation (a product-rule change, named as such); "strong" labelled as based on seasons used to build/test | test_b2_held_up… (observed fail); season_use vs logs | Telegram/UI wording at release | user go (product rule + Telegram text) |
| 8 | M2 lead time/quote use record time (D; Live label valid for every row class — no row crosses tipoff or window start) | expose computed_at; minutes and quote from it; record canonical right after primary; sorted reasons; past-row disclosure; never backdate — AFTER 13 | 2 observed fails (25.0≠60.0; quote 0.238) + 2 specified | release; coverage.py → K2 (bundle with 9) | user go |
| 9 | M4 NFL quote ignores price_label + M5 NFL hash over-broad/incomplete (D) | bundle: `price_label='current'`; v2 NFL definition = normalized digest of the NFL call closure; visible inactive reason; whether re-activation keeps the Nov 2 start (continuity) = user decision | repro −7.0; test_nfl_definition.py (6 cases) | NFL release + re-activation | user go |
| 10 | M6 one-shot guards per seal/dir, none for NBA (D) | committed sport-wide test_runs.json; report sha at completion and activation; legacy NFL entry labelled "not verifiable at completion" | test_m6_second_seal… (observed fail) | with 9 | user go |
| 11 | M13 no dataset/code identity on canonical rows (D) | migration 0036 (4 nullable cols), new rows only; past rows "unknown"; readers tolerate missing cols | test_new_canonical_rows_record_identity | migration (item 15) | user go |
| 12b | M8/B4 NHL xG availability back-dated (D, research only; recovered rows 49/523/577 possibly exposed) | availability = retrieval for live-season pbp, corrected at read time; mark the 3 rows; any MODEL_SELECTION note appended and dated | test_b4_late_pbp… (observed fail) | release; research only | none for code |
| 17 | C4 295 NFL raw files (D) | record as NOT FOUND IN THE SEARCHED LOCATIONS (2.07M files by name, 208 archives, 664 same-size files by sha256; ~/.Trash unreadable, not searched; no Time Machine). No re-fetch proposed: ESPN collection is not suitable on its terms (item 18), and a new fetch would be a new retrieval, never recovery unless sha256 matches. Store keeps its rows; replay is documented as impossible for raw_id 1–295. | — | none | none |
| 18 | M17 source suitability (P) — see table below | docs corrections (espn.py:3, DATA_SOURCES.md:13: terms exist); per-use verdicts; product decisions follow from them | — | determines what may be collected/used | user decides product scope; nothing to "accept" |
| 19 | C6 host sleep gaps (D: 4 simultaneous gaps since Nov 4, 2.6–23 h; sleep 1 min) | options: AC-only keep-awake agent, pmset, scheduled wake; code side = item 5 | gap/orphan test | power/launchd | user go |

## Source suitability (item 18; explicit text vs interpretation; scope = pages read in BATCH-C and lane reports)
| Source | Explicit text | Interpretation / unknown | Personal-research display | Real-money ticket decisions |
|---|---|---|---|---|
| ESPN (Disney ToU, upd. 2024-05-24) | 2.A personal, noncommercial; excludes AI/ML training/testing; 2.B(x) bars automated access for data mining/scraping | that the ToU governs ESPN's unauthenticated JSON endpoints (interpretation, likely) | not established as permitted for automated collection | not suitable on the explicit text |
| NHL.com / api.nhle.com (ToS 2025-10-29) | bars "unauthorized automated means to compile information"; non-commercial personal use | applicability to the undocumented API (interpretation) | not established | not suitable on the explicit text |
| NBA.com (ToU 2026-07-13) §9 | statistics "may not be used in connection with any gambling activity" | whether injury reports are "statistics" and whether ticket reviews are "gambling activity" (interpretation, plausible); no automated-access clause cited in our evidence | unknown | not suitable if §9 applies (interpretation) |
| openfootball | CC0 | data quality errors (known) | yes (soccer results) | results only; no odds |
| nflverse | data labelled CC-BY 4.0 | its docs say NFL data belongs to its owners → effective permission unknown | unknown | unknown |
| Wikipedia / Wikidata | CC BY-SA / CC0 | per-article accuracy; automated access policies (user-agent etiquette) | candidate for results checks, not assessed | not assessed |
| Sports-Reference | bars automated access | manual download is not a workaround for use restrictions | no | no |
| MoneyPuck | non-commercial only | — | no (automated) | no |
| football-data.co.uk, StatsBomb, Betfair, UEFA | as in REPORT §4 | — | — | — |
Unsupported: no free, clearly permitted source found for live NBA/NHL/NFL results or any odds, within the pages actually read.

## Deferred (with reason)
- M9 DB sport triggers for games/teams/game_forecasts/raw rows: schema change → joins item 15.
- M15 soccer stale-state labels, M16 S1 relabeling + wiki_et.py hardcode + S1 raw bytes into durable evidence: soccer docs/UI;
  next soccer batch. Soccer S0/S1 acceptance stays CONDITIONAL on these.
- NFL quotes not rebuilt by replay; NFL backfill revisions all `observed`: after item 17's documentation; replay of 1–295 impossible.
- NHL 62 stored OT-for-SO decisions: corrected by the next genuine retrieval (lead verified 9 via the cached list); a recorded
  re-derivation needs a design (no silent edits) — after item 13.
- N2 (token copied into audit snapshots): closed — files deleted by the lead; future snapshots exclude `.env*`/`.telegram*`.
- All LOW findings: see REPORT §1 LOW.

## Recommended first implementation batch (one writer, worktree, nothing landed in REPO)
0 → 0b → 1 → 2 → 6 → 3 + 3b. Each protects data or fixes a measured defect, avoids NFL-hashed files, changes no forecast or
Telegram text, needs no migration/launchd/network, and has failing-first tests. Output = a reviewed patch in the worktree.
Next batch: 7 (M7). Items 4, 5, 8, 12 after item 13. Landing only after item 14 or a named user-approved landing window.
Proposed writer: ONE session (`ce` has the shared-ingest context) — assigned by the user. This auditor and Codex review.
