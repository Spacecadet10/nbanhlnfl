# Soccer training report — openfootball history, frozen protocol, baselines, calibration, sealed candidate, preview

Date: 2026-11-08. Author: Claude (session 97, the only writer for this work). Worktree: `~/Desktop/nba-predictor-soccer-train`
(detached at 79ff6ec + the reviewed batch-1 tree `adacc372`, diff-vs-HEAD sha1 `2479a306…`; see §1). Nothing committed,
pushed, deployed or activated. No live store, launchd job, Telegram message or `data/` folder was touched. Existing Markdown
reports, HANDOFF.md and SMOKE_TEST.md were not edited: this file is the handoff (§11).

**Bottom line.** Soccer now has a reproducible CC0 results history (30,074 matches, 7 competitions), a protocol sealed before
any fit, three trained baselines, and an **uncalibrated draw-aware Elo** selected on validation as the sealed candidate
(`7dbc668c…`). On validation (2020-21 to 2022-23, 6,215 matches) Elo's log loss is 0.991 against 1.075 for outcome
frequencies; Poisson (0.991) was not clearly better, calibration improved log loss but worsened draw reliability past the
preregistered limit and was rejected. **This is model quality on validation only, not a test result and not a betting
edge.** The one-shot reserved test (2023-24 to 2025-26) is prepared and preflighted but **not run**. An isolated preview
shows the stored research forecasts, labelled Research backtest, at desktop and 390 px.

## 0. Sequence and ownership
1. Queued behind the backup/restore repair (session 2b). Waited for its independent review (APPROVE), final readiness run
   (`OMP_NUM_THREADS=4 backend/.venv/bin/python scripts/check_project.py --ready` in `~/Desktop/nba-predictor-batch1`,
   14:31:13–15:28:22Z, **exit 0, 1,737 passed**, tree hash `adacc372…` before = after; log read by me) and
   `remediation/RESTORE_FAILURE_ADDENDUM.md` (present, records APPROVE). Claims checked independently (patch sha1, tree hash).
2. Baseline: the reviewed batch-1 tree. The main checkout's code equals the batch-1 base except Codex's ticket work
   (`tickets.py`, `test_tickets.py`, `docs/TICKETS.md`, `jobs.py`, `telegram_card.py`) and docs, which are a separate owner
   and **not** in this worktree. The batch-1 patch is not applied to the main checkout either: landing this work later needs
   both merged (§11).
3. Exclusive write ownership agreed with session 2b in writing (it releases soccer/ingest/db/backup/migrations/soccer UI and
   will message before any later batch touching them). Reviewers were read-only subagents.

## 1. Current app capabilities (this worktree, isolated preview)
- **Data**: openfootball history for EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL and UEL main stages, through the raw
  cache at pinned commits (`soccer history-download|history-replay|history-coverage`). ESPN soccer ingestion code still
  exists from S0 but was **not used** (no ESPN collection in this task).
- **Models**: frequencies, draw-aware Elo, Poisson attack/defence (`app/soccer_eval.py`), all scoring the 90-minute 1X2.
- **Ledger**: append-only `soccer_forecasts` (migration 0037): regulation 1X2 only; p_home+p_draw+p_away = 1; cutoff,
  computation, generation and publication times kept apart; stable retry keys, genuine new versions; backtest rows can never
  be published or have a cutoff after their match date; prospective rows can never be generated after their cutoff.
- **API**: `/api/soccer/research/{meta,matches,matches/{id},performance,history}` — stored rows only, 503 with instructions
  when a store lacks the tables, never a computed fallback.
- **UI**: when research forecasts exist, soccer Game day / Performance / History / match pages show them with a Backtest
  badge, the cutoff, the computation time and "never published as live"; other markets, qualification and extra time are not
  forecast. Without research data the S0 ESPN screens are shown as before.
- **Not available**: live/prospective soccer forecasts, odds, totals/BTTS/qualification markets, any scheduled job.

## 2. Sources and licences (S2)
| Source | Use | Licence (primary evidence) |
|---|---|---|
| openfootball/football.json @ `e6744429ee39…` | en.1, es.1, de.1, it.1, fr.1 league JSON | CC0 1.0: `LICENSE.md` sha256 `36ffd9dc…` fetched at the pinned commit (= S1 record); README "dedicated to the public domain … no restrictions whatsoever" (`licence/`) |
| openfootball/champions-league @ `abfaeddc2ee3…` | `cl.txt` (UCL), `el.txt` (UEL) | same CC0 `LICENSE.md` (sha256 `36ffd9dc…`) |
No accounts, keys, paid sources, ESPN, football-data.co.uk or Wikipedia were used. CC0 §4(c) disclaims upstream rights
clearing (S1 note, unchanged). Second divisions exist in football.json (en.2 …; S1 said none existed) — **not used**.

## 3. Coverage, exclusions, provenance (S2)
Counts regenerated from the store (`s2/coverage.json`):
| Competition | Seasons | Matches | Gradable | Notes |
|---|---|---|---|---|
| EPL | 2010-11 … 2026-27 | 6,394 | 6,064 | 66 rejected (2019-20, see below); 2026-27: 50 final, 50 result missing, 280 scheduled |
| La Liga | 2012-13 … 2026-27 | 5,700 | 5,379 | 2024-25 Matchday 38: 10 without score in the source; time missing 290 (2026-27) |
| Bundesliga | 2010-11 … 2026-27 | 5,202 | 4,931 | 1 awarded |
| Serie A | 2013-14 … 2026-27 | 5,320 | 4,978 | 2024-25 Matchday 38: 10 without score; 2 awarded |
| Ligue 1 | 2014-15 … 2026-27 | 4,644 | 4,279 | 2019-20: 101 cancelled (COVID); 1 cancelled 2025-26; 2 awarded |
| UCL main stage | 2011-12 … 2025-26 | 1,997 | 1,756 | 24 extra-time/pens noted, 217 deciding matches excluded; counts equal file headers (125/119/189) |
| UEL main stage | **2020-21 … 2024-25 only** | 817 | 692 | 22 extra-time/pens, 100 deciding excluded; 2 cancelled, 1 awarded; **no development data, no 2025-26 file** |
Totals: 30,074 matches, 274 clubs (109 aliased source names), 0 possible-duplicate or cross-country club flags, 0 extra-time
anomalies, 0 within-file duplicate identities, 0 same-day double matches. **Rejected**: 66 EPL 2019-20 matches played after
the June 2020 restart but dated July 2019 by the source (e.g. Man City 4–0 Liverpool listed 2019-07-02) — rejected, never
re-dated (left in, 2020 results would have looked known in 2019).
- **Grading rule**: only final matches that cannot have gone to extra time and carry no a.e.t./pen. note are graded on the
  90-minute result. Every UEFA deciding match (second legs, single-match knockouts, finals) is excluded because the source's
  a.e.t. notes are incomplete (Inter 4–3 Barcelona 2025; reviewer also recalls PSV–Juventus 2025) and no independent second
  source is in scope. Awarded and cancelled matches are excluded.
- **Times**: local, zone not stated, never converted; a txt line without a time continues the time of the line above in the
  same date block (football.txt layout; flagged `local_time_listed = 0`); missing times stay NULL.
- **Availability**: first result assumed known the next day 06:00 UTC (labelled assumption); a first result appearing in a
  later retrieval, a re-listing, a deciding-flag change or a drop counts only from that retrieval (`observed_retrieval`); a
  different result or date only from the retrieval that delivered it (`observed_correction`); schema CHECK: never before the
  day after the match.
- **Provenance**: every file cached with sha256, bytes, retrieval time, importer `openfootball-1`, commit and path
  (`soccer_source_files`); git blob sha1 verified on download (96/96); club map version `2026-11-08.1` recorded per row.
- **Reproducibility**: an independent CLI re-download at the same pins gave byte-identical files (96, 8,060,965 bytes) and
  identical match rows (sha256 over non-timestamp columns `1bb17be8…`); replay into the same store changes nothing; replay
  into a fresh store gives identical table digests.
- **Tests**: repeated ingestion, same-store and fresh-store replay, corrections (A→B→A), same-second retrievals (fetch-id
  order), late import of an older retrieval, dropped/restored matches, rename + new result, date corrections, club-map change
  refused, blob mismatch refused (`tests/test_soccer_history.py`, 35 tests).

Reproduce (from `backend/`, never against `data/`):
```
NBA_DATA_DIR=<new scratch dir> .venv/bin/python -m app.cli soccer migrate
NBA_DATA_DIR=<same> .venv/bin/python -m app.cli soccer history-download \
  --pin football.json=e6744429ee395bc86f247348c6184bb08d4eb361 --pin champions-league=abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef
NBA_DATA_DIR=<same> .venv/bin/python -m app.cli soccer history-coverage
```

## 4. Frozen evaluation protocol and access logs (S3)
`backend/app/soccer_protocol.json` **v2**, sha256 `f3794639…`, pinned in `soccer_eval.py`; readable copy `PROTOCOL.md`;
seal record `s3/protocol_seal.json` (v1 `043b5ede…` 16:27Z; v2 amendment 1 16:47Z; **0 model fits and 0 metrics before
either seal**). Periods: warm-up 2010–12; development tune 2013–17; development calibration 2018–19; validation 2020–22;
reserved test 2023–25 (locked); 2026 prospective (always refused). Cutoff 06:00 UTC on the match date; same-day results
never inputs. Primary metric multiclass log loss; ISO-week block bootstrap (seed 20260930, 2,000). Choice: frequency → Elo
→ Poisson, a more complex model only if pooled log loss is lower with the 95 % interval below 0 **and** it is better in at
least 2 of 3 validation seasons. Calibration (selected model only): multinomial log-probability recalibration fitted on
development-calibration, kept only under the same test plus no outcome's ECE worse by > 0.005.
Guards (`tests/test_soccer_eval.py`, 39 tests, the first 18 written before the module; failing-first logs and mutation logs in `s3/`):
current-match label never passed to a model; own/same-day/future results and later corrections never fed; unfinished,
cancelled, awarded, extra-time and deciding matches never inputs; forecasts invariant to anything after their cutoff and to
truncation; frequencies, draw model and calibrator refuse other seasons; reserved seasons refused without a seal; prospective
seasons always refused; one test run per (shared) access log; the candidate must be the only seal and the only development
start in the mandatory experiments log; changed code, protocol, data or dataset pins refused; development runs on a copy
holding only seasons ≤ 2022 (rows **and** raw bytes).
**Access log**: `~/nba-predictor-research-data/soccer-eval-logs/soccer_test_access.jsonl` **does not exist** — no reserved
season was read. Experiments log (same folder; copy `s4/soccer_experiments.copy-at-seal.jsonl`): 1 start, 648 Elo + 12
Poisson configurations, 1 validation choice, 1 calibration, 1 seal.
Inspection disclosure: before sealing I inspected source integrity only (counts, statuses, a few match lines incl. 2023-24
and 2025-26 samples, club names); the S2 reviewer spot-checked ~85 real results across seasons from recollection. No outcome
rate or metric was computed before the seal.

## 5. Models trained, configurations, results (S4)
One development run (`soccer eval-develop`, 16:57–17:03Z, on the ≤ 2022 copy `dev-final`; report
`s4/development_validation_report.json`, sha256 in `s4/development_run_sha256.txt`). Scored matches: development tune
8,538; development calibration 3,699; validation 6,215 (each reconciled exactly against the schedules: e.g. validation =
5,477 league + 330 UCL + 408 UEL).
| Model | Selected on development tune | Dev tune LL | Dev calibration LL | **Validation LL [95 % CI]** | Validation Brier |
|---|---|---|---|---|---|
| Frequencies | per competition, 2010–19 (in-sample on dev) | 1.0611* | 1.0693 | 1.0748 [1.068, 1.083] | 0.6509 |
| Draw-aware Elo | K 15, H 50, C 1.0, P 100, F 100, goal multiplier on | 0.9659** | 0.9821 | **0.9913 [0.979, 1.003]** | 0.5903 |
| Poisson | half-life 365 d, τ 3, D 0.25 | 0.9681 | 0.9810 | 0.9912 [0.981, 1.001] | 0.5904 |
\* in-sample; \*\* in-sample for Elo's draw model (13 parameters), comparable across configurations. Grid-edge selections
(reported only, grids never extended): Elo K, H, C; Poisson τ, D. Elo's nearest alternatives differ by ≤ 2e-5 (H 75/100).
**Choice (preregistered)**: Elo vs frequencies −0.0835 [−0.0934, −0.0737], better in 3/3 seasons → Elo. Poisson vs Elo
−0.0001 [−0.0042, +0.0041], better in 2020-21 and 2021-22 but worse in 2022-23 → **not accepted; simpler Elo kept**.
Validation by competition (log loss; frequency / Elo / Poisson; n): EPL 1.071 / 0.991 / 0.987 (1,140); La Liga
1.075 / 1.000 / 1.006 (1,140); Bundesliga 1.066 / 1.002 / 1.004 (918); Serie A 1.085 / 0.974 / 0.980 (1,139); Ligue 1
1.088 / 1.015 / 1.010 (1,140); UCL 1.053 / 0.919 / 0.898 (330); UEL 1.061 / 0.986 / 0.988 (408). By season (Elo): 2020-21
1.000, 2021-22 0.992, 2022-23 0.982. Without UEL 2020-21: Elo 0.9918, Poisson 0.9917 (n 6,042). Intervals per competition
overlap between Elo and Poisson everywhere; competition differences are descriptive only.
Plausibility: frequency LL ≈ entropy of the observed shares (home 43–46 %, draw ~25 %, away ~30 %); rating-model LLs near
0.97–1.00 are typical for European 1X2. Elo over-predicts home wins on validation (46.2 % vs 43.1 % observed; ECE 0.034):
2020-21 was played largely without crowds.

## 6. Calibration decision (S5)
Fitted on Elo's development-calibration forecasts (3,699): scale 0.923, draw +0.028, away +0.045. On validation: log loss
−0.0025 [−0.0038, −0.0013], better in 3/3 seasons, home ECE 0.034 → 0.018, away 0.031 → 0.020, **draw ECE 0.0057 →
0.0125 (worse by 0.0068 > 0.005)** → **rejected by the preregistered rule; the candidate is uncalibrated**. Failed and
kept experiments are all in the experiments log.

## 7. Per-competition limitations
- **UEL**: 5 seasons only (2020-21 … 2024-25); no development data, so UEL uses UCL's draw cut points and goal level; scored
  in validation (408) and would be in test 2023-24/2024-25 only; no 2025-26 file.
- **UEFA knockouts**: all deciding matches ungraded and never inputs (~15 per season); first legs graded.
- **Serie A / Ligue 1**: first covered seasons 2013-14 / 2014-15 (unscored warm-up); Serie A and La Liga 2024-25 lack 10
  final-day results in the source; Ligue 1 2019-20 truncated (101 cancelled).
- **EPL 2019-20**: 314 of 380 matches (66 misdated by the source, rejected).
- **Promoted clubs**: no second-division data used (exists in football.json; not in scope).
- **2026-27**: the source lags (results missing for already-played matches); not evaluated, locked.
- Kickoff zones unknown; no odds; no statistics; no market comparison possible.

## 8. Preview startup and tested flows (S6)
Isolated copy only (seasons ≤ 2022 + ledger; an empty migrated NBA scaffold store because `dev.sh` requires one):
```
R=~/nba-predictor-research-data/soccer-train-20261108
# build the preview source once (copy without reserved seasons, then the ledger):
NBA_DATA_DIR=$R/preview-src .venv/bin/python -I ../docs/evidence/soccer-training-2026-11-08/s6/build_open_season_store.py $R/data $R/preview-src
NBA_DATA_DIR=$R/preview-src .venv/bin/python -m app.cli soccer eval-record --out $R/development-run
NBA_DATA_DIR=$R/preview-src .venv/bin/python -m app.cli --sport nba migrate
# start (from the worktree root):
NBA_LIVE_DATA_DIR=$R/preview-src NBA_DEV_DATA_DIR=$R/preview-app API_PORT=8851 WEB_PORT=5251 NBA_DEV_PUBLISH=0 sh scripts/dev.sh
```
App http://127.0.0.1:5251/?sport=soccer, API http://127.0.0.1:8851. Ledger: 55,356 rows (3 models × 18,452 matches), 9
runs; recording again stored 0 rows (all retries); ledger metrics equal the development report exactly (9/9 runs).
Browser (details `browser/BROWSER_RESULTS.md`): 11 flows at 1440 and 390 px (iframes), no horizontal overflow, Backtest
badge on every forecast, no Live label; interactive date step, competition switch, card → match page, history paging,
deep link with a wrong competition (corrected), error state for an unknown id, UCL final shown ungraded without forecast.

## 9. Sealed candidate and the one-shot test (prepared, NOT run)
`s4/sealed_candidate.json`: model `elo`, config above, draw model (slope 2.198, cut points per competition), calibration
`null`, protocol `f3794639…`, open-data digest `c9bced9c…` (equal on the full store and the ≤ 2022 copy), code digests
(`soccer_eval.py e21624e5…`, `soccer_history.py b6d97ec4…`, `soccer_clubs.py 8a9c6cca…`, `backtest.py 2dfbf703…`,
`metrics.py cbdde1c8…`), source pins, environment (Python 3.12.12, numpy 2.5.3, scipy 1.18.1), sha256 **`7dbc668c…`**.
Read-only preflight on the full store (`s6/one_shot_preflight.json`): all 8 checks pass (valid seal, output absent, no earlier
test start, exactly one development start, sole seal, code/protocol/data unchanged, dataset pins, reserved seasons present).
Run it only after review and the user's go — once:
```
NBA_DATA_DIR=~/nba-predictor-research-data/soccer-train-20261108/data .venv/bin/python -m app.cli soccer eval-test \
  --sealed <…>/development-run/sealed_candidate.json --out ~/nba-predictor-research-data/soccer-train-20261108/reserved-test-run
```
Any change to the six digested files invalidates the candidate (by design).

## 10. Independent reviews (read-only subagents; findings resolved or recorded below)
| Phase | Reviewer | First verdict | After fixes |
|---|---|---|---|
| S2 history | free-source / data quality | APPROVE WITH NOTES (3 MEDIUM: date correction kept old availability; first result after a missing listing backdated; club-map change silently not applied) | re-check: 3 FIXED, 2 PARTLY → rename gap and Monaco note then fixed (`s2/recheck_m2_*.txt`); **APPROVE WITH NOTES** |
| S3 protocol | leakage / evaluation / statistics | **REQUEST CHANGES** before any fit (H1 label reachable by models; H2 seal/one-shot bypassable; M1–M4) | protocol v2 (amendment 1, before any fit) + code; re-review **APPROVE WITH NOTES**, fits may start once on the ≤ 2022 copy; notes N1 (shared log folder) and N2 (single start) fixed **before** the run |
| S4–S6 candidate | leakage / evaluation / statistics | **APPROVE WITH NOTES**: every number reproduced exactly; no leakage; sealed candidate reproduces its forecasts bit-for-bit; preflight passes | M1 (test output aggregate-only, no reading rule) → user decision (§11); L7 full-store pin and L8 code archive done (`s6/full_store_pin.json`, `s4/frozen_code.tar.gz`) |
| S6 API / UI | integration | **REQUEST CHANGES** (M1 test isolation; M2 non-backtest rows mislabelled / 500; M3 labels/timestamps only enforced for backtest; M4 two runs blended; M5 reserved seasons capped only by store contents) | all fixed test-first (`s6/review_fixes_*.txt`, `s6/mutation_s6_review.txt`); re-review **APPROVE WITH NOTES** (all MEDIUM fixed; its own full backend run 1,825 passed); new LOW N1 (shown run chosen by writer-supplied time) and N2 (research reads imported the evaluation module) fixed test-first (`s6/rereview_lows_*.txt`); N3 and L4 rest recorded open (§11) |
Failing-first evidence and mutation checks for every guard are in `s2/`, `s3/` and `s6/`.

## 11. Handoff — remaining steps, decisions, open items
**Decisions for the user (none taken here):**
1. **One-shot reserved test** (2023-24 … 2025-26; expected 5,959 scored matches). Prepared and preflighted, not run. Before the go,
   decide how its result will be read (candidate review M1): the runner reports aggregate metrics by competition and season
   for the frozen Elo only, no per-match rows and no paired comparison with frequencies (a point comparison with the fixed
   frequencies is computable from the report; a confidence interval is not). Options: (a) accept as is and write a dated
   reading plan before the run; (b) extend the runner (then `soccer_eval.py`'s digest changes, the candidate must be
   re-sealed by a new development run, and the experiments log will show two starts — disclosed, and `run_test` will refuse
   until the user decides). Before running: re-verify the full-store pin (`s6/full_store_pin.json`), check `NBA_DATA_DIR`
   points at the full store and the output parent exists (a crash after the logged start uses up the single run).
2. **Live/prospective activation**: not prepared. Needs the user's go, a forward-only openfootball refresh (the source lags
   days to weeks; 2026-27 already shows missing results), prospective rows (`run_type 'prospective'`, generated now), and
   grading once results arrive. Automatic retraining remains paused; no job exists.
3. **Landing**: this worktree = reviewed batch-1 tree + soccer work, uncommitted. Landing in the main checkout needs the
   batch-1 patch (not yet applied there) and Codex's ticket work merged; migrations 0036/0037 then create empty soccer
   tables in every store at the next explicit release migrate (same pattern as 0034).
4. **Scope choices to confirm**: UEL kept on 5 seasons; deciding UEFA matches excluded until an independent second source is
   chosen; second divisions (available, CC0) not used for promoted clubs; ESPN soccer screens remain for stores without
   research data (a store with research data shows research on Game day / Performance / History — product choice).

**Open items (recorded, not fixed):**
- S6 re-review N3 (LOW): the ledger's cutoff ≤ match-date rule is checked at insert only; a later history replay that moves a
  match earlier could leave a stored cutoff after the new date (edge case; the row stays visible).
- S6 L4 rest (LOW): `SoccerPages.tsx` (ESPN branch) and `NflPages.tsx` still decode match ids without a guard — part of the
  pre-existing malformed-URL blank page below. S6 L10: the match page's back link drops the date you came from (cosmetic).
- Pre-existing (not soccer code): a malformed percent-encoded URL (`/games/%E0%A4%A`) blanks the whole app for every league.
- S3 re-review N3/N4 (LOW): invariance test covers one league; logs are editable files (procedural protection only).
- Candidate review LOWs: K/C/H optima on grid edges (small expected loss, grid not extended by rule); in-sample draw model on
  development tune (labelled); ISO-week CI anti-conservative; 2024-25 UCL/UEL league-phase format unseen in development;
  `run_test` records but does not check the environment (irrelevant for pure-Python Elo); failed run consumes the one shot.
- S2 notes: awarded rows carry doubtful source scores (excluded); some deciding matches' `ft` columns hold after-extra-time
  scores in the source (excluded); Monaco under a new unaliased spelling would need an explicit alias.
- `dev` copy (16:49Z) was the first ≤ 2022 copy used only to compare open-data digests; superseded by `dev-final` (built
  with the final code) on which the single development run ran; no fit or metric ever ran on `dev`.
- Environment: `backend/.venv` in this worktree is a symlink to the main checkout's venv (git-excluded).

**Not done in this task (by instruction):** reserved-test execution, live activation, jobs, Telegram, deployment, commit,
push, NCAA. NBA/NHL/NFL models and jobs unchanged (their code paths untouched apart from the shared migration lists in four
tests).

## 12. Final readiness gate (stable final code)
| Attempt | Command (from the worktree root) | Result |
|---|---|---|
| 1 (18:24Z) | `OMP_NUM_THREADS=4 backend/.venv/bin/python scripts/check_project.py --ready` | **FAILED**, exit 1 in 0 s: structure guard rejected the evidence script name `make_dev_copy.py` (renamed `build_open_season_store.py`); `gate/gate-attempt1-FAILED.log` |
| 2 (18:26–19:19Z) | same | **FAILED**, exit 1: 16 failed / 1,809 passed; all 16 in `tests/test_research_agent.py` ("agent failed to start"); tree hash `7c57431f…` before = after. Cause (reproduced): this worktree's `backend/.venv` is a symlink to the main checkout's venv; the research agent's OS sandbox will not start an interpreter reached through it — the same test fails via the symlink and passes via the real interpreter path. Not soccer code. `gate/gate-attempt2-FAILED-venv-symlink.log` |
| 3 (19:21:03–19:45:05Z) | `OMP_NUM_THREADS=4 /Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python scripts/check_project.py --ready` (real path, as the batch-1 gate) | **PASSED, exit 0**: structure guard, typecheck, build, **1,829 passed, 0 failed**; code-tree hash (everything except `docs/`, recipe `gate/tree_hash.sh`) `8614c1e2…` before = after; `gate/gate.log` |
The tested tree is the final code: worktree `~/Desktop/nba-predictor-soccer-train`, HEAD 79ff6ec + uncommitted changes, hash
`8614c1e2b283fd1a2d6468b805665fc0c4f92cd6`. The six sealed-candidate files are byte-identical to the seal
(`s4/frozen_code_sha256.txt`). The script's pointer to docs/SMOKE_TEST.md: SMOKE_TEST.md was deliberately not edited; the
browser/API evidence for this work is `browser/BROWSER_RESULTS.md`. **A passing engineering gate does not establish model
quality**; model quality is §5 (validation only) and the untested reserved period.
