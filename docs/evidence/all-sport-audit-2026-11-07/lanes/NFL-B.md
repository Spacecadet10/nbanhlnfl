# NFL-B — NFL forecasting & evaluation (read-only audit, 2026-11-07)

Code audited: SNAP `backend/app/{nfl_eval,nfl_live,nfl,markets,coverage,odds,exact_rules}.py`, `models/elo.py`, tests `test_nfl_eval.py`, `test_nfl_live.py`.
Rows: `$SP/stores/nfl.sqlite` (immutable). Artifacts read: `REPO/data/research/nfl_live/activation.json`, `REPO/data/research/nfl_eval/nfl_test_access.jsonl`,
`REPO/docs/evidence/nfl-{winner-validation,markets-validation}-2026-11-01/`, `nfl-winner-test-2026-11-02/` (seal and access-log files only; I took the test numbers from the activation artifact's aggregate report. No 2022–2025 game rows were queried.)

## 0. Inventory: what serves NFL forecasts

| Item | Status | Evidence |
|---|---|---|
| `nfl_live.record` via launchd `com.nba-predictor.nfl-live` (`app.cli nfl tick` every 60 s) | **production/live** | `~/Library/LaunchAgents/com.nba-predictor.nfl-live.plist` is identical to `REPO/scripts/launchd/…` (diff → SAME). WorkingDirectory = `~/.codex/worktrees/nfl-live-release/nba-predictor-sprint1/backend` (branch codex/nfl-live-20261102, HEAD 8539513, `git status --short` empty). Interpreter = **REPO/backend/.venv/bin/python** (dirty dev checkout's venv). NBA_DATA_DIR = REPO/data (live). `launchctl list` shows it loaded, last exit 0. The log (`.nfl-live.log`) at 20:33Z shows `forecasts: not_due`. |
| `~/nba-predictor-live` (a55c874) | **does not serve NFL** | `ls ~/nba-predictor-live/backend/app | grep nfl` → nothing. |
| Worktree vs SNAP code | identical for NFL logic | `diff -q` is empty for nfl_eval.py, nfl_live.py, nfl.py, markets.py, exact_rules.py, odds.py. cli.py/db.py/main.py differ (soccer additions); 0033 migration differs by one trailing blank line. SNAP `nfl_live.definition()` = `b6074877a7e7ccfe` = activation `definition`. |
| Winner model: `NflElo` K20 H50 carry 0.5 mov=True, raw probabilities | **sealed choice, served** | seal `d0e51f91…` (digest recomputes correctly); `model_version nfl-elo:d0e51f91c3ac:b6074877a7e7ccfe` on rows 1 and 6 |
| Line markets: `NflPoints` eta 0.05 carry 0.75 (priors 22/13.5/13.5) | **sealed, served as informational** (`best_pick_allowed:false`) | rows 2–5, 7–10 |
| `nfl evaluate`, `nfl test-run`, `nfl collect-lines`, `nfl activate` | research / one-off | cli.py:588–668 |
| Platt calibrator | unused (rejected on validation; `calibration.accepted:false`) | activation.sealed |
| Home-rate and constant baselines | evaluation only | nfl_eval.py:601 |
| Earlier winner-only seal `bb0d871a…` (2026-11-01 20:58Z) | superseded before the test, same winner config | evidence/nfl-winner-validation-2026-11-01 |
| `exact_rules.py` | not used by NFL (no NFL references) | grep |

Live ledger: 10 rows (2 games × 5 markets), forecast_id 1–10, `order_version nfl-live-v1`. These are all NFL regular/postseason games since activation 2026-11-02T22:11:33Z: nfl:401873036 (MNF CHI@SEA) and nfl:401873037 (TNF JAX@BAL). Coverage is 2/2.

## 1. Findings (most severe first)

### F1 MEDIUM — Live line forecasts choose the bookmaker quote without `price_label='current'`, so the line depends on insert order (NEW)
- File: `backend/app/nfl_live.py:112-114`. The query `… WHERE sport='nfl' AND game_id=? AND market=? AND retrieved_at<=? AND retrieved_at>=cutoff-2h ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1` has no price_label filter. Every other consumer filters `price_label='current'`: market_live.py:48, near_tipoff.py:59, research_live.py:289, paper_trading.py:49.
- Each NFL fetch stores an `open` and a `current` row with the same `retrieved_at`. In the store copy, 3,093 of the 5,388 open/current pairs have different lines. Today the right row wins only because `espn.parse_lines` emits open before close (sources/espn.py:333), which gives `current` the higher quote_id. All 5,388 latest-per-(game, market, retrieved_at) rows are `current`, and the two live games used current quotes 101/102 and 6797/6798.
- Reproduction (synthetic temp store, SNAP code): `$SP/work/NFL-B/scripts/repro.py` inserts `current −3.0` and then `open −7.0` at the same retrieved_at. Output: `spread forecast line used: (-7.0, 'bookmaker', 2)`. The opening line is recorded and graded as the forecast's "bookmaker" line.
- Why it matters: if the parser order, a replay or a backfill path ever changes, forecasts silently use stale opening lines and grade against them. No test covers this.
- Fix: add `AND price_label='current'` (the winner moneyline lookup too). Add a regression test that inserts open after current.

### F2 MEDIUM — The one-shot test and the activation proof are enforced per seal and per data directory, and they trust self-asserted files (NEW at code level; HANDOFF has only the procedural "never rerun")
- `nfl_eval.py:660-670` `_already_run` refuses only when the log has a `test run…` entry for **the same sha256**. A different seal (re-chosen model or threshold) passes. `access_log_path()` (575-577) sits under `NBA_DATA_DIR`, so a fresh scratch copy has no log and passes too. The real run was done on `/tmp/courtside-nfl-eval`, and that directory no longer exists (`ls` → No such file). The live log is a copy, so the original is gone. Its first two lines are identical to `docs/evidence/nfl-winner-test-2026-11-02/nfl_test_access.jsonl` (start and complete both at 2026-11-02T21:42:37Z).
- `nfl_live.py:40-48` `activate` accepts any JSONL containing `{"choice_sha256": <seal>, "purpose": "test run: complete"}` and any report whose `choice_sha256` and threshold match. It does not check the report's integrity, `dataset_version_all`, or whether `confirmed`/`holds` were computed. The test fixture does exactly this: `tests/test_nfl_live.py:24-32` writes the log line and the report by hand, and activation succeeds.
- Why it matters: "the single test was run once and the result is honest" depends on process, not code. A second seal could be tested and the better result activated.
- Fix: keep a sport-wide marker (any `test run: start` blocks every later seal unless the user records a decision). Write the report's sha256 into the completion entry and verify it at activation. Commit the seal before the run (see F3).

### F3 LOW — "Sealed before any test access" is supported by timestamps that are internally consistent but not tamper-evident
- Seal `d0e51f91…`: `chosen_at` inside the digest = 2026-11-01T21:40:06Z, and the file mtime is 2026-11-01T16:40:26-05:00 (21:40:26Z). The test started 2026-11-02T21:42:37Z, about 24 h later. The seal used for the test (`nfl_sealed_choice_used.json`) equals the validation seal and the activation's `sealed` (Python compare → True).
- No git commit contained `d0e51f91` before the test. HEAD-line commits: d1572ca (2026-11-01 16:29Z) has no seal; the worktree commit 97e7efb at 2026-11-02 22:09:57Z is 27 min **after** the test. All the evidence is mtimes and self-reported fields. The Codex review "NFL line-market preregistration/code spot review — findings before line evaluation" (HANDOFF:350) is the only independent record that preregistration came first.
- No other evidence of test-season access: the live log has only the 2 test entries plus 2 later "prospective NFL state initialization" reads (11-02 23:16:03Z and 11-05 23:15:32Z, matching the generated_at of the 2 live batches).
- Fix for future sports: commit the seal (or post its hash somewhere outside the machine) before the test run.

### F4 LOW — Live team-total forecasts use model-chosen 50/50 lines, unlike the evaluated market definition (NEW)
- `nfl_live.py:115`: with no NFL team-total quotes, `line = markets.model_line(dist, market)`, the half-point line closest to 50/50. Rows 4, 5, 9 and 10 have confidence 0.5048–0.51. Evaluation graded team totals at the *implied* book line (T∓S)/2 (`nfl_eval.py:409-411, 454`; NFL_PLAN "Team totals").
- These near-coin picks are graded W/L in `/api/nfl/record` (team_total_home 1–1, team_total_away 1–1). The live record therefore measures something other than what was validated. It is labelled `line_source: model`, so the source is honest, but the metric means little.
- Fix: derive implied team lines from the same spread/total quotes recorded with the forecast (labelled implied), or keep model-line team totals out of the performance summary.

### F5 LOW — The winner "probability" is an expected score, and tie mass is set to 0
- `nfl_live.py:116` stores `(p, 0., 1-p)`. Elo is trained with a tie as 0.5 (`nfl_eval.py:121`), so `p` = P(home win) + ½·P(tie). The UI shows "JAX 59.3% / BAL 40.7%" adding to 100%. Grading voids ties (`markets.py:72`), which matches NFL_PLAN and NFL_LIVE:26 ("Winner ties void"). The effect is about 0.2–0.4 pp per game. It is a labelling issue, not a grading one.

### F6 LOW — A game skipped by the missing-results guard leaves no record
- `nfl_live.py:97-101` `continue`s without writing a `missing` row or returning the game in the output. Later the game shows the generic "not recorded: no forecast cycle ran within two hours" (coverage.py:595), not the real reason. NFL_LIVE:25 says unresolved results "block forecasts", which is true, but the block is not explained.
- Fix: insert a `status='missing'` row with the reason (the schema supports it), or return the skipped ids.

### F7 LOW — Serving drift checks are partial
- `record()` never re-checks the 2002–2021 `dataset_version`; only `activate` does (`nfl_live.py:42`). A later correction to historical rows would change live Elo state without disabling serving. The `definition()` hash (nfl_live.py:19) leaves out `backtest.py`, `models/baselines.py`, `threshold_overview.py`, `freshness.py` and `sources/espn.py`. Today the open dataset still matches: recomputed `f59df479f5769084` (5,360 rows) = sealed.
- `nfl_live.py:217,219` hard-code `threshold=.75` and `min_settled 200` in ranked picks instead of reading `test_report.winner.frozen_threshold.threshold`. The values are equal today.

### F8 LOW — Stale or misleading text
- `jobs.py:813-815` docstring says "NFL never reaches any forecast code", but jobs.py:825-828 calls `nfl_live.record`.
- `markets.py:29-32`: the NFL spread/total/team-total settlement note says "winner ties void" when the actual rule is an integer-line push.
- `docs/NFL_PLAN.md` line-market section: "never a model input" for the closing line. Codex already flagged that the line is a decision input (HANDOFF:350), and the plan amendment covers it. Not new.

### F9 INFO — "Reserved seasons not read" means "not read by evaluation code"
- The guard exists only in `nfl_eval.load_rows`/`line_events`. The data-only NFL API/UI (`nfl.games_on`, `game_detail`, live since 2026-11-01 ~18:15Z, before sealing) serves 2022–2025 scores. NFL_PLAN:52-53 discloses that 2024/2025 scoreboard months were opened for schema checks. I found no evidence of any model being scored on 2022–2025 before the seal.

## 2. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "2022–2025 are reserved: `load_rows` refuses them unless a sealed choice is supplied, and logs every access" (nfl_eval.py:1-3, NFL_PLAN:107) | verified (code) | nfl_eval.py:74-79. Tests `test_validation_never_reads_reserved_seasons` and `test_test_run_needs_a_sealed_choice_and_logs_every_access` pass. Limits in F2/F9. |
| "One-shot … refuses if the access log already has a test-run entry for this seal" (NFL_PLAN:202) | verified as written; weaker than "no second run" | F2 |
| "One-shot held-out run completed ONCE … seal d0e51f91… / open dataset matched" (HANDOFF:227) | verified for this seal and log; "once" not provable (original log deleted) | log has exactly one start/complete pair; `dataset_version_open` f59df479 = seal; recomputed on the store copy = f59df479 |
| "Frozen 75%+: 126–31/157 = 80.3%, Wilson 73.3–85.7" (HANDOFF:228, NFL_LIVE:32) | arithmetic verified (from report counts, not from test rows) | `wilson(126,157)` = [0.7334, 0.8573]; activation report frozen_threshold matches |
| "Winner confirmed" | verified from report | `chosen_vs_home_rate.ci95` = [−0.0321, −0.0186], beats True, 1,136 games, 88 weeks. Rule = Elo upper bound vs home rate < 0 (nfl_eval.py:621) |
| Validation "75%+ 204 picks, 80.9%, Wilson 74.9–85.7" (NFL_PLAN:189) | verified | validation report rows: 0.75 → 204/165, [0.7494, 0.8569]. The rule picked the highest band with ≥ 200 picks |
| "Line markets: all four worse than a coin at the close" (NFL_PLAN:184-187) | verified (report) | spread +0.0067 [0.001, 0.013], total +0.0123, team totals +0.0137/+0.0092. counts scored+push+no_line = 1,355 |
| "Reserved line evaluation not_run" (HANDOFF:229, NFL_LIVE:33) | verified | activation.test_report.line_markets.status = not_run |
| "Inputs stop at T−120 even when a cycle runs late; actual generation time" (NFL_LIVE:21-22) | verified | nfl_live.py:95, 102 (results ≤ cutoff), 113 (quotes ≤ cutoff), generated_at = now. Row 6: quote 22:52:07Z ≤ cutoff 23:15Z, generated 23:15:32Z. Positive and negative cutoff controls in §3 |
| "Book quotes must additionally be within 2h of that cutoff" | verified | nfl_live.py:113 |
| "No historical forecast backfill" | verified | trigger `game_forecasts_pregame` (generated_at within [tipoff−2h, tipoff) and game still scheduled). Append-only update/delete triggers present |
| "A complete five-market batch is recorded once per game/start; an interrupted batch resumes without duplicating" | verified | nfl_live.py:80-82 (`due` counts distinct markets per tipoff); `_insert` content key. Tests pass |
| "Winner ties void; line pushes are excluded from W/L" | verified | markets.py:72, 83; coverage.py:437-438, 603-615 |
| Line grading uses "the line available when each forecast was made" | verified | coverage `_canonical_one` grades `sel["line"]`. Quote selection caveat: F1 |
| "Changes to those serving/model files disable forecasts" (NFL_LIVE:39) | partly verified | true for the 7 hashed files; others not covered (F7) |
| "These test games never enter live accuracy or exact-percentage records" (NFL_LIVE:34) | verified | `Views.report` filters tipoff ≥ activated_at; game_forecasts holds only the 10 live rows |
| "NFL percentages verified … JAX 59.3% / BAL 40.7%, BAL +10 at 68.0%, zero settled exact matches" (HANDOFF:160-162) | verified as **display = stored row** only | row 6 p_a 0.407 (BAL home) → JAX 59.3%; row 7 confidence 0.6803 at line +10.0 (quote 6797). Dev API `/api/nfl/games/nfl:401873037` returns the same, settled 0. It checked display fidelity, not model quality |
| "Validation choice … Elo chosen (Brier 0.2226 …)" (NFL_PLAN:124-127) | unverified by recomputation (no model rerun allowed) | numbers consistent with the evidence report |
| jobs.tick docstring "NFL never reaches any forecast code" | contradicted | jobs.py:825-828 |

## 3. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `cd $SP/snap/backend; OMP_NUM_THREADS=2 PYTHONPATH=$SP/snap/backend NBA_DATA_DIR=$SP/work/NFL-B/data .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_nfl_eval.py tests/test_nfl_live.py -k "reserved or seal or cutoff or tie or neutral or push or one_shot or fits_nothing or retry or resumes or recommended or definition or price_ingestion"` | pass | `25 passed, 16 deselected in 1.47s` | pass |
| `repro.py` positive control: prior MIN–NYJ result available 19:00Z < cutoff 22:15Z | p changes | p_home 0.5715 → 0.4497 | pass (the existing test checks only the exclusion side) |
| `repro.py` quote order: current −3.0 then open −7.0, same retrieved_at | uses −3.0 | uses −7.0 (quote 2, 'open') | **fail → F1** |
| SQL on the store copy: latest quote per (game, market, retrieved_at) by quote_id | all current | `current|5388` | pass today |
| Seal digests recomputed (validation, markets, used, activation) | match | bb0d871a ✓, d0e51f91 ✓ ×2, activation.sealed == used == markets seal | pass |
| `load_rows(through=2021)` dataset_version on the store copy | f59df479f5769084 | f59df479f5769084 (5,360 rows) | pass |
| `nfl_live.definition()` on SNAP | b6074877a7e7ccfe | b6074877a7e7ccfe | pass |
| Recompute `/api/nfl/record` (dev API 8871) from rows 1–10 | match | winner 1–1, Brier (0.659² + 0.407²)/2 = 0.2999 ✓; spread 0–2, Brier (0.6087² + 0.6803²)/2 = 0.4167 ✓; total 1–1, Brier ((1−0.4595)² + (1−0.7103)²)/2 = 0.1880 ✓. n = 2 per market; Wilson 1/2 = [9.5%, 90.5%], 0/2 = [0%, 65.8%]. The summary returns no CI | pass (values); CI absent |
| Recompute Wilson for test 126/157 and validation 165/204 | match docs | [0.7334, 0.8573], [0.7494, 0.8569] | pass |

Not run: the full test suite (lead gate); model refits or evaluation reruns (forbidden); any 2022–2025 row query (forbidden); the live job (read-only).

## 4. Open questions / cross-lane

- (A/C) nfl:401873037's `result_available_at` is 2026-11-06T23:39:17Z, 22.4 h after kickoff, versus about 3 h for nfl:401873036. The scoreboard refresh lagged; possibly the Mac was asleep or the idle policy is 3,600 s. Not chased.
- (C) The live NFL job runs the dirty REPO venv's interpreter with clean worktree code. A pip change in REPO/backend/.venv changes production NFL dependencies without the release guard noticing.
- (C) `/api/nfl/record` shows accuracy 50% / 0% at n = 2 with no interval. Check the UI's small-sample labelling.
- (shared) The F1 pattern (no price_label filter) is NFL-only. The NBA/NHL paths filter `current`.
- (shared) The trust gaps in coverage `_insert` and activation (F2) apply to any future sport that reuses the `activate` pattern.
