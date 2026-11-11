# Remediation plan — BATCH B (forecast and evaluation integrity) — 2026-11-07

Read-only reviewer. Code = `$SP/snap3` (line numbers are snap3 unless marked). Stores = `$SP/stores/{nba,nhl,nfl}.sqlite`
(live backups 15:29 ET, opened `?immutable=1`). Release checkouts compared read-only: `~/nba-predictor-live` (a55c874) and
`~/.codex/worktrees/nfl-live-release/…` (8539513). Nothing was written outside `$SP/work/plan-B/` and this file. No fitting,
recalibration, reserved-season outcome read, network, Telegram, launchd or live-store write.
`$SP` = `/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad`.

Evidence files (all in `$SP/work/plan-B/`):
- `b1_lag.py` + `b1_lag.out` (record lag, boundary crossings), `b1_newer.py` (canonical older than the latest primary row),
  `b1_telegram.py` (sends mapped to forecasts; it reads a **copy** `telegram.sqlite` of `REPO/.telegram-delivery.sqlite`, made with `cp`).
- `tests/test_planb_timestamps.py` (B1) and `tests/test_planb_eval.py` (B2/B4/M6). Both are proposed regression tests, and every test
  in them fails on snap3 (output below).

Command used for every test run (run from `$SP/snap3/backend`; scratch tmp/basetemp under `$SP/work/plan-B/pt`):
```
OMP_NUM_THREADS=2 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$SP/snap3/backend NBA_DATA_DIR=$SP/work/plan-B/data TMPDIR=$SP/work/plan-B/pt \
  REPO/backend/.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=$SP/work/plan-B/pt/<n> \
  --confcutdir=$SP/snap3/backend/tests $SP/work/plan-B/tests/<file>
```
Result: `test_planb_timestamps.py` 2 failed. `test_planb_eval.py` 3 failed. Each test failed at the intended assertion; the
failure messages are quoted under each item.

Cross-batch dependencies. **A3/M5** (BATCH-A): the NFL definition hash covers `coverage.py`, `markets.py`, `nfl_eval.py` and
`nfl_live.py`, so any edit to those files hides NFL forecasts until re-activation. **C1/H2** (BATCH-C): the Telegram job runs the
REPO working tree against live `data/`, so a REPO edit to `providers/real.py`, `threshold_overview.py` or `telegram.py` changes
live Telegram cards **without a release**. **C2**: schema release sequence (live stores 31/31/33, tree 35).

---

## B1 (M2) — Canonical timestamps: computation vs record vs publication vs eligibility

### Definitions (from code; the plan keeps these four separate)
| | Meaning | Where it is stored/derived today (snap3) |
|---|---|---|
| (a) **computation time** | when the probability was computed, with the inputs as of the job's `as_of` | **NBA/NHL winner**: `predictions.generated_at` = `near_tipoff_forecasts.generated_at`, stamped by `utc_now()` right after `model.predict` (`near_tipoff.py:125-126`). A retry with unchanged inputs returns the **earlier** stored row (ledger key excludes time), so (a) is the *first* time this probability was computed from these inputs. **NBA/NHL line markets/reg3**: `market_forecasts.generated_at` (`market_live.py:101`). In `game_forecasts` (a) is copied into `source_generated_at` (`coverage.py:190,197` → `:357`). **Direct rows** (`primary_direct`, `coverage._Direct.forecast` `coverage.py:238-262`) are computed inside the record loop; their `generated_at` is `None` (`:246,256`), so `source_generated_at` is NULL. **NFL**: `nfl_live.py:117-121` sets `source_generated_at = generated_at = now`. NFL's input cutoff is T−120 (`nfl_live.py:95,102,113`), which is separate from (a). |
| (b) **record time** | when the canonical row was written | `game_forecasts.generated_at` = `utc_now()` per row at write (`coverage.py:345`). `jobs.predict_now` calls `coverage.record` **last**, after research, pool, tracking and adaptive (`jobs.py:436`; the error path is `:400`). |
| (c) **publication time** | when a person could first see it | Live API reads the store directly. A canonical row is visible once its per-row transaction commits (`coverage._insert`, `with transaction`), so web/API ≈ (b). The *probability itself* was already visible at ≈(a) in the legacy "Within 2 hours" view, which reads the source rows. Dev preview republishes a copy every ≥60 s (`preview_publish.py`). Telegram: `deliveries.attempted_at` of the first `sent` row in `.telegram-delivery.sqlite` (table `deliveries`; key = sha256 of picks incl. `forecast_id` and `generated_at`, `telegram.py:218`). |
| (d) **eligibility ("Live", "within two hours")** | the rule that makes a forecast count | DB triggers: `game_forecasts_pregame` (b ∈ [tipoff−2h, tipoff), game scheduled, tipoff unchanged); `predictions_two_hour_window`/`markets_two_hour_window` (a ∈ [tipoff−2h, tipoff)); `predictions_live_pregame`/`market_forecasts_live_pregame`. Code: `coverage.record` refuses `generated >= tipoff` (`:346-348`); `_primary_recorded` takes only rows with `generated_at <= now AND < cutoff` for the **current** tipoff (`:180-198`); `_canonical_rows` filters on (b) (`:534`). Quote eligibility: **winner** uses (b): `real.py:750-760` `_winner_quote_prob(…, record.generated_at)` at `:821,841,893,974`. **Line markets** use the quote id the source used, with check `retrieved_at <= (b)` (`coverage.py:699`). Paper rules price at (b) (`paper_rules.py:58`, `paper_trading._quote`). Telegram eligibility: `start−2h ≤ (b) ≤ now` (`telegram.py:148`). |

### Counts (store copies; script `b1_lag.py`, `b1_newer.py`, `b1_telegram.py`)
Lag = (b) − (a), primary_recorded rows (all are regular season; direct rows have no (a) for NBA/NHL):

| Store | rows | lag > 0 | > 5 min | > 30 min | avg / max min | selected canonical (latest per game·market, competitive) lag>0 / >5 / >30 |
|---|---|---|---|---|---|---|
| NBA | 901 | 901 | 626 | 70 | 11.8 / 70.4 | 600 / 474 / 65 |
| NHL | 841 | 841 | 611 | 24 | 9.6 / 53.9 | 744 / 582 / 24 |
| NFL | 10 (direct) | 0 | 0 | 0 | 0 / 0 | – |

Rows where (b) crosses a boundary that (a) did not:
| Boundary | NBA | NHL | Note |
|---|---|---|---|
| tipoff (a < tipoff ≤ b) | 0 | 0 | impossible: trigger + `coverage.py:346` |
| window start (a < tipoff−2h ≤ b) | 0 | 0 | source triggers enforce a ≥ tipoff−2h |
| result of either team becomes available in (a, b] | 0 | 0 | the "earlier unfinished game" guard makes this impossible |
| **winner moneyline retrieved in (a, b]** (used by the canonical market comparison) | 0 | **1** (row 762, nhl:2026020229) | details below |
| line-market quote retrieved after (a) | 0 / 447 quote-linked | 0 / 285 | comparison uses the source's own `market_quote_id` |
| paper-rule price (same-line current quote ≤ b) retrieved in (a, b] | 0 | 3 (762, 764, 765) | none is a paper bet: 762 confidence 0.517 < 0.65; 764/765 are NHL spread/total, which have no NHL rule |
| paper-rule freeze 2026-11-01T06:30Z (a < frozen ≤ b) | 0 | 0 | |
| run boundary: (a) from an earlier near_tipoff run than (b) | 0 | 6 (1 game) | the same row 762 game |
| **a later primary computation existed before tipoff but the canonical stayed on the older one** | 69 game·markets in 22 games; winner **11/120** graded | 2 (spread, total) | record ran after tipoff ("calculation finished at or after the start: nothing recorded") or the cycle never finished |

Row 762 (NHL): source prediction 10453 computed 19:31:33Z (moneyline 19:31:31Z, home 0.3775). Canonical rows 756–761
recorded 19:38:14Z. The 20:08 cycle recomputed Elo with the same dataset version, so the ledger returned the 19:31:33 row.
The fallback-reason text listed the two adaptive definitions in the opposite order, which made a *new* canonical revision
762–767 at 20:15:13Z. The canonical market comparison then used the 20:08:15Z quote (home 0.3908). Root causes: (i) record
time is used for the quote; (ii) `coverage._adaptive` joins reasons in newest-row order (`coverage.py:164-169`), so the
content key (`_content_key`, includes `fallback_reason`) changes with no change in the forecast. 6 such reason-only revisions
exist in NHL and 0 in NBA. Effect: 1 of 121 matched NHL winner games. The game was a home win (6-3). With the quote from (a),
the NHL canonical market Brier rises by ≈ +0.00013. The model Brier is unchanged and the direction (market better) does not change.

Publication (Telegram, ledger copy; 65 games with a sent card, NBA 32 / NHL 31 / NFL 2): first send was **never before** the
first canonical record and **never after** tipoff. Send − record avg 1.9 / max 3.1 min (NBA) and 1.8 / 3.2 (NHL). Record −
computation for those games avg 8.8 / max 15.7 min (NBA) and 8.0 / 13.3 (NHL). Cards do not show "minutes before start".
6 deliveries are `uncertain`.

### Verdict per class: does the "Live / within two hours" classification hold?
| Class | Rows | Live valid? | What is wrong |
|---|---|---|---|
| L1 same-run lag (record last in cycle) | NBA 901, NHL 835 | **Yes**: (a) and (b) are both in [tipoff−2h, tipoff), and no team result arrived in between | "made N min before the start" (`Canonical.tsx:74,95,150`) and the API `minutes_before_start` (`coverage.py:568`) understate the lead time by up to 70 min |
| L2 carried-over source + reason-only revision | NHL 6 (1 game) | **Yes** | winner market comparison uses a quote 37 min after (a). `Canonical.tsx:181` "retrieved no later than the forecast" is false for that game |
| L3 canonical superseded by a later unrecorded primary computation | NBA 69 (11/120 winner graded), NHL 2 | **Yes** (a genuine pregame forecast) | not the latest model forecast. "Canonical" and "Within 2 hours" views differ (NBA market Brier 0.191025 vs 0.190658, NBA-B) |
| L4 direct rows | NBA 177, NHL 126, NFL 10 | **Yes** ((a) ≈ (b), computed inside the record loop) | (a) is not stored for NBA/NHL (NULL), but is stored for NFL. The same column means different things by sport |
No row needs reclassification. The defects are in the displayed times and in the winner market-comparison quote. **No backdating**:
`generated_at` stays the record time.

### Reproduction
`python3 -I $SP/work/plan-B/b1_lag.py $SP/stores/{nba,nhl}.sqlite {nba,nhl}` → `b1_lag.out` (table above).
`python3 -I b1_newer.py ../../stores/nba.sqlite nba` → winner final 120, newer 11.
`sqlite3 'file:…/nhl.sqlite?immutable=1' "select forecast_id, fallback_reason from game_forecasts where forecast_id in (756,762)"` → same
two definitions, reversed order.

### Proposed change (minimal; no migration needed for B1)
1. **Expose (a) and use it** (`providers/real.py`, `coverage.py`, `schemas.py`, `Canonical.tsx`, `api/types.ts`):
   - `coverage._canonical_one` (`:566-569`): add `computed_at = sel["source_generated_at"] or sel["generated_at"]` and
     `computed_at_basis = "source" | "record_time_only"`, and compute `minutes_before_start` from `computed_at`. Keep
     `generated_at` (= record time) unchanged and also return `recorded_minutes_before_start`.
   - `schemas.CanonicalRecord` (`:119-140`): add optional `computed_at: Optional[datetime]`, `computed_at_basis`.
   - `real.py`: every `_winner_quote_prob(…, c["record"]["generated_at"])` (`:821,841,893`) and `ranked_picks` market_prob
     uses `computed_at`. `coverage.forecast_quote` (`:699`) checks `q.retrieved_at <= rec["computed_at"]` (stricter; 0 rows change).
   - `_Direct.forecast` (`:246,256`): set `"generated_at": utc_now()` (or the cycle's fixed `now`) for direct rows going forward,
     so `source_generated_at` is filled as NFL already does. Docstring of the column in `COVERAGE.md:29`: "time the probability
     was computed".
   - `Canonical.tsx:74,95,150,181`: "computed N min before the start · recorded HH:MM". The 'Made' fact becomes two facts
     (Computed / Recorded). The market sentence becomes "retrieved no later than the model computed the forecast".
   - Do **not** change `ranked_picks[*].generated_at` or the Telegram delivery key (`telegram.py:189,218`). Changing them would re-send
     cards for games inside their window at deploy time. Add `computed_at` beside it.
2. **Record canonical right after the primary steps** (`jobs.py:396-438`): call `coverage.record` once after `market_forecasts`
   (and in the error path), before research/pool/tracking/adaptive, and remove the final call. Selection does not change:
   coverage-v2 never approves adaptive. Its `fallback_reason` then describes the adaptive rows that exist at record time
   (previous cycle), which is true as of (b). This closes class L3 and most of L1.
3. **Deterministic reasons** (`coverage.py:169`): join `latest` sorted by definition id. Stops reason-only revisions (class L2).
4. **Disclosure for past rows** (read-time, no row edits): API note + `docs/COVERAGE.md`: "Rows recorded before <release date>:
   recorded up to 70 min (NBA) / 54 min (NHL) after the probability was computed. Lead time and winner market comparison now use
   the computation time stored with the source row. 177 NBA / 126 NHL direct rows have no stored computation time (shown
   'record time only'). One NHL game (nhl:2026020229) changes its market comparison quote; 11 NBA winner forecasts (22 NBA games
   in some market) are canonical although a later two-hour forecast existed."
5. Must NOT change: `game_forecasts.generated_at` semantics or values, the 2-hour triggers, selection order, paper-rule pricing
   (`paper_rules.py:58` follows the frozen file's "retrieved at or before the forecast"; frozen rules are never edited. 0 bets
   differ today. Record in the paper-rule report notes that "forecast" = canonical record time).

### Regression tests (written; fail on snap3)
`$SP/work/plan-B/tests/test_planb_timestamps.py`. Proposed home: `backend/tests/test_canonical.py`.
- `test_canonical_record_carries_computation_time_and_lead_time_from_it`: near_tipoff at 22:30, a new -400 moneyline at 22:50,
  `coverage.record(now=23:05)`. Asserts generated_at 23:05 (not backdated), `minutes_before_start == 60.0`, and
  `computed_at` 22:30. **snap3: `AssertionError: 25.0 == 60.0`** (and `computed_at` is absent).
- `test_winner_market_comparison_uses_the_quote_retrieved_by_the_computation`: same, game final, `/api/canonical/ranked`
  market_prob ∈ {0.5798, 0.4202} (the quote retrieved before 22:30). **snap3: 0.238** (the 22:50 quote).
- Not written, specified: `test_canonical_is_recorded_before_research_steps` (`test_coverage.py`). Monkeypatch
  refresh/odds/boxscores to skipped, and make `research_selection` advance the coverage clock past tipoff. Assert a canonical row exists
  with generated_at < tipoff. Fails on snap3 because the record runs last and refuses ("calculation finished at or after the
  start"). `test_adaptive_reason_order_is_deterministic`: two definitions written in alternating order give one canonical row.
  Fails on snap3 (two rows).

### Compatibility
- NFL: `coverage.py` is in the NFL definition hash (`nfl_live.py:18-20`). Editing it hides NFL forecasts until A3/M5 lands or
  NFL is re-activated (user go). `jobs.py`, `real.py`, `schemas.py` and the frontend are not hashed. NFL rows already have
  (a) = (b).
- Release a55c874 (live web): has the same winner-quote rule (`~/nba-predictor-live/backend/app/providers/real.py:808`). The fix
  reaches the live site only with a release. Telegram (REPO code, H2): `computed_at` is additive, and the delivery key is unchanged.
  Ranked market_prob changes only for rows with lag and a newer quote (0 NBA, 1 NHL so far).
- Schema: none for B1 (uses existing `source_generated_at`).
- Existing tests: `test_canonical.py:46` (`minutes_before_start == 60.0`, same now → still 60). `test_coverage.py`, `test_adaptive.py`,
  `test_research_live.py`, `test_agreement_tracking.py`, `test_review3_fixes.py` and `test_near_tipoff.py` call `jobs.predict_now`.
  Check any assertion that coverage sees same-cycle adaptive rows (fallback_reason naming a same-cycle definition).
- Live impact: needs release (NBA/NHL release checkout). Telegram picks up real.py immediately (H2) → user go or C1 first.
- Size M. Depends on A3 (NFL hash) for the coverage.py parts; on C1 for a clean Telegram rollout.

---

## B2 (H4 + M6) — Reused reserved seasons; one-shot guards

### Issue (confirmed: logs; policy decision on wording)
- `docs/EVALUATION_PLAN.md:102-104` says the NBA test seasons were accessed once, "never used to tune, screen, select or justify", and
  that `test_access.jsonl` "must stay at exactly 1 line". The log has **5 lines**. The rule text is **not** amended (per brief). The fix
  is disclosure.
- `threshold_overview.choose` (`:187-203`) reports `held_up` on the "later" seasons, and `ThresholdOverview.tsx:41-43` shows
  "held up in 2024-25–2025-26". `rank` (`:299-308`) marks `strong` from `evidence_ladder` (`:258-271`), which **sums earlier
  (tuning) and later (reused test) seasons**. `strong` drives the GamePage best pick (`GamePage.tsx:189`) and the Telegram
  "Recommended pick" (`telegram.py:166-172`). The release a55c874 has no threshold_overview/telegram. The live user sees these
  labels through Telegram (REPO code) and the dev API.
- One-shot guards differ by sport. NBA `backtest._evaluate_test` (`backtest.py:629-640`) logs and **never refuses** a second run.
  NHL `nhl_eval.evaluate_test` (`:285-290`) refuses when the log is non-empty, but only within one data dir. NFL `_already_run`
  (`nfl_eval.py:660-670`) refuses only the **same seal**, also per data dir (`access_log_path` `:573-577`). `nfl_live.activate`
  (`:39-48`) accepts any hand-written completion line plus any report whose threshold matches. The fixture does exactly this:
  `test_nfl_live.py:24-32`.

### Season use, per season (docs + logs; logs read-only, copies under the research data dirs have identical hashes except players-v1's own pre-mirror copy)
**NBA** (season id = end year):
| Season | Role | Uses (evidence) |
|---|---|---|
| 2020 (2019-20) | warm-up | Elo burn-in; logistic final fit trained 2019-20…2022-23 (`BASELINE_REPORT.md:48`) |
| 2021 (2020-21) | warm-up | stacker out-of-sample base rows start here (`EVALUATION_PLAN.md:266-269`); candidate-pool code runs on "warm-up seasons only (NBA 2021)" (`CANDIDATE_POOL.md:127`) |
| 2022–2024 | validation | Elo grid tuned by validation Brier (`EVALUATION_PLAN.md:22-35`); candidate screening; nba_markets dev research (2023-24 holdout, `nba_markets.py:3`); bookmaker-line replays 2021-24 behind NO_PROVEN_SKILL (`threshold_overview.py:36-40`); threshold "earlier" seasons that **choose** the best threshold |
| 2025–2026 | reserved test | `test_access.jsonl`: 2026-09-30T14:40:35Z frozen Elo test; 2026-10-10T15:23:49Z, 17:37:31Z, 17:43:57Z, 17:54:07Z players-v1 "retrospective recent-season report (amendment)", run on `~/nba-predictor-research-data/players-v1` (`PLAYER_RATINGS_STUDY.md:45-53,192`). `pool_assess_access_nba.jsonl`: 2026-10-02T17:57:26Z pool-v1 assess → candidate selectability routes "held-out seasons" (`candidates/pool_rules.json:11-12`). Derived displays: threshold "later"/held_up; ranked-pick ladder (strong) |
| 2027 (2026-27) | live | canonical record since recording began; paper rules frozen 2026-11-01T06:30Z (`reference/paper_rules.json`, committed 48cb93f 2026-11-01T16:04:32Z, 25 min before the first counted canonical forecast at 16:29:37Z) |
**NHL**:
| Season | Role | Uses |
|---|---|---|
| 2016 | warm-up | first stored season; burn-in only. No earlier season exists, so it cannot be scored walk-forward |
| 2017 | warm-up | features-v1 walk-forward fits scored from 2017 (`docs/evidence/features-v1/experiments.jsonl`, 2026-10-03); pool code runs (`CANDIDATE_POOL.md:127`) |
| 2018–2021 | development | hockey_elo grid tuning; pool stage 1 (`research/candidates/experiments.jsonl` 2026-10-02T17:21:32Z, seasons 2018-2021); threshold "earlier" |
| 2022–2024 | validation | model selection (`nhl_frozen_decision.json`); pool screening (17:32:21Z); holdout A validation evidence (`frozen-rule-nhl-xg.json` evidence_so_far); threshold "earlier" |
| 2025–2026 | test | `test_access_nhl.jsonl` 2026-10-01T03:23:43Z one-shot test; `pool_assess_access_nhl.jsonl` 2026-10-02T17:32:28Z (2022-2026); features-v1 `nhl_eval.load_rows('test')` ×2, 2026-10-03T00:34:26Z and 01:01:00Z (scratch copies); holdout A hockey_logistic_xg, scored 2026-10-16T12:41:42Z (FAIL, `~/nba-predictor-research-data/release-rehearsal-20261015/audit/holdoutA/`); out-of-repo autoresearch holdout 2026-10-17T15:48:27Z (`~/autoresearch-sports/…/nhl/holdout_access.jsonl`, seasons [2025, 2026]); threshold later/held_up; ranked ladder |
| 2027 | live | canonical record; paper rule nhl-winner-65 |
**NFL**: 2002–2021 open; 2022–2025 reserved. `REPO/data/research/nfl_eval/nfl_test_access.jsonl`: test start+complete
2026-11-02T21:42:37Z (seal d0e51f91…); 2 "prospective NFL state initialization" reads (11-02 23:16:03Z, 11-05 23:15:32Z). The
original `/tmp/courtside-nfl-eval` log is gone. The evidence report equals `activation.test_report` (dict compare True; sha256
`0cb39cefd6080d5535dd02739261790e2e8549f79a5bd3581d09502c95b1c905`), but it was first committed in 79ff6ec (2026-11-07), after the run.

**Untouched historical season: none for NBA or NHL.** Every season was used for burn-in/training, tuning, selection or
(re)testing. NHL 2016 is untouched for scoring but cannot be scored (no prior data) and is 10 years old.

### Proposed change
(i) **Contamination disclosure** (one committed source, rendered in three places):
- New file `backend/app/reference/season_use.json`: per sport, per season id: role, and every logged access `{at, study, log
  file, what was decided with it}` from the tables above. It is append-only, and a test pins it against the access logs. The code reads it;
  it does not edit any log.
- Docs: a dated section **appended after** `EVALUATION_PLAN.md:104` (rule text untouched): "Disclosure 2026-11-07: this rule was not
  kept. test_access.jsonl has 5 entries (list); pool-v1 assess used 2025/2026 to decide candidate selectability. 2024-25 and
  2025-26 are spent: no result on them is independent evidence." The same in `NHL_BASELINE_REPORT.md` (accesses above; the
  baseline report's "one entry" stays true for that log only) and `MARKET_EDGE_RESEARCH.md` (holdout A FAIL, already NHL-B F6).
- API (`threshold_overview.history`, `:206-228`): `earlier.status = "tuning_seasons"` (NBA: Elo tuned on 2022-24; NHL:
  tuned 2018-21, selected 2022-24) and `later.status = "reused_reserved_test"` with `later.accesses` from `season_use.json`. Use
  `combination_research._reserved(sport)` (already used by `combination_thresholds.py:636-643` for `later_reserved_test_seasons`).
- UI `ThresholdOverview.tsx:36-44`: "**80%+** — most reliable win rate in 2021-22–2023-24 (the seasons Elo was tuned on). Also
  above the all-picks rate in 2024-25–2025-26, seasons already used for testing and candidate screening, so this is not an
  independent check." Column headers `:83-84`: "chooses (tuning seasons)" / "reused test seasons".
(ii) **Labels stop presenting reused/in-sample seasons as confirmation** (`threshold_overview.py`):
- `choose()` returns `held_up = None` when any later season is reserved/used, plus `later_check = {"above": bool, "seasons": […],
  "status": "reused_reserved_test"}`. `RULE` (`:48-53`) adds this sentence.
- `evidence_ladder`/`pick_evidence`: add `basis = "in_sample_history"` (history) or `"live_season"`. `rank()` keeps the
  2026-11-01 rule unchanged (no product-rule reversal), but adds `strong_basis`. GamePage/Telegram text "Recommended pick" →
  "Top-ranked pick" plus one line "ranked on past seasons the model was built and tested on (not an independent check)". Changing
  the `strong` rule itself (e.g. live-only evidence) is a **product decision**: offer it, do not do it.
(iii) **Untouched evaluation route = prospective, preregistered** (follow the `preregistered-evaluation` skill; note that skill's
`/tmp` scratch path conflicts with this repo's "never write /tmp" incident; use a scratch dir under the session scratchpad):
- File `backend/app/reference/threshold_confirmation.json`, sealed like `paper_rules.json` (FROZEN_SHA256 pinned by a test). It
  fixes, before its `start_at`: markets (NBA winner, NHL winner, NHL reg3) and the thresholds **as the existing declared rule
  chose them** (read from the API at seal time; no re-choosing); the hypothesis (primary: at the threshold, Wilson 95% lower
  bound > the same games' all-picks win rate, i.e. the existing rule; secondary, reported only: win rate − bookmaker de-vigged
  probability for the same picks, ISO-week block bootstrap, seed 20260930, 2,000 resamples); eligibility (regular season,
  canonical rows with **computed_at and generated_at ≥ start_at**, which needs B1); stop rule (one verdict when the threshold has 100
  settled picks (`MIN_LATER_PICKS`), or after the last 2026-27 regular-season game; fewer than 100 → INCONCLUSIVE; no interim
  verdicts).
- `start_at` ≥ 24 h after the commit. The seal sha256 is recorded outside the machine before `start_at` (the user's choice of
  channel. The agent must not send it by Telegram). An independent review of the file happens before `start_at`. An n=100 target
  may not be reached in one season (the live NBA 80%+ count is small per NBA-B). INCONCLUSIVE is a valid outcome.
- Existing paper rules stay as they are (frozen, never edited).
(iv) **One-shot guards (M6) — sport-wide, no re-run of anything**:
- `backend/app/reference/test_runs.json` (committed, append-only): one entry per sport test run with seal sha, report sha256,
  date, and an amendment field that only the user's recorded words can fill. Seed it with NBA 2026-09-30, NHL 2026-10-01, and NFL
  2026-11-02 (report sha above, `"seal_committed_after_test": true`, `"original_log_lost": true`).
- `nfl_eval.run_test` (`:673-686`): refuse if the data-dir log has **any** `test run` entry (any seal) **or** the registry has an
  NFL entry without an amendment. Write `report_sha256` into the `_RUN_DONE` entry. Apply the same guard to
  `backtest._evaluate_test` (NBA, which today never refuses) and `nhl_eval.evaluate_test`.
- `nfl_live.activate` (`:39-48`): take the report **path**, require `sha256(bytes) == completion.report_sha256`, or (legacy)
  `== registry.report_sha256`, and require `report.dataset_version_open == sealed.dataset_version`. It never re-runs the test.
  The current `activation.json` stays valid only through the legacy registry entry.

### Regression tests (fail on snap3; output observed)
- `test_b2_held_up_on_reused_reserved_seasons_is_not_reported_as_confirmation` (`test_threshold_overview.py`): monkeypatched
  history earlier [2022-24]/later [2025, 2026]. **snap3: `assert True is None`** (held_up True).
- `test_m6_a_second_seal_cannot_run_the_reserved_test` (`test_nfl_eval.py`, synthetic store): run seal A, then seal B with K=25.
  **snap3: `DID NOT RAISE TestRunAlreadyDone`**.
- Specified, not run: `test_rank_marks_history_evidence_in_sample` (fails: no `basis` key);
  `test_activation_requires_the_report_digest_recorded_at_completion` (fails: snap3 activates with the hand-written line, as the
  fixture `test_nfl_live.py:24-32` shows); `test_nba_test_period_refuses_a_second_access` (fails: `backtest.py:629-640` has no
  refusal); `test_a_fresh_data_dir_cannot_bypass_the_registry` (fails: no registry); `test_season_use_matches_access_logs`
  (fails: file absent).

### Compatibility
- NFL: editing `nfl_eval.py`/`nfl_live.py` changes the NFL definition → inactive until re-activation (A3). The legacy registry
  entry lets re-activation pass without re-running the test. Existing tests that change: `test_nfl_live.py` fixture
  (`:24-32`, must write report_sha256), `test_nfl_eval.py:342-355` (still passes).
- Telegram (H2): label/text changes in `threshold_overview.py`/`telegram.py` reach live cards immediately from REPO → user go or C1
  first. The delivery key includes `strong` in `versions` (`telegram.py:189`). If `strong` keeps its value, no re-send happens.
- a55c874 has no threshold overview, so there is no live web change until a release. Schema: none.
- Size M (disclosure + labels S; guards M; prospective file S + review). Depends on B1 for `computed_at` eligibility.

---

## B3 (M13) — Version identity on new canonical rows

### Issue (confirmed, schema read)
`game_forecasts` (migration 0029) has `model_version` only. There is no dataset/feature version, decision digest or code
version. Direct rows also have no source link: NBA 177 rows (82 preseason + 95 regular) and NHL 126. Model versions are config
strings (`near_tipoff.py:35`, `market_live.py:38`). The release commit is only in `job_runs.detail` (`jobs.py:47-48`, env
`NBA_RELEASE_COMMIT`). Recorded rows can be traced through `source_table/source_id` to `predictions/market_forecasts`, which do
carry dataset/feature version.

### Proposed change (smallest)
- Migration **0036** (bundle into the C2 joint release; live 31/31/33): `ALTER TABLE game_forecasts ADD COLUMN dataset_version TEXT;
  ADD COLUMN feature_version TEXT; ADD COLUMN decision_sha256 TEXT; ADD COLUMN code_version TEXT;`. Plus a BEFORE INSERT
  trigger: `source_generated_at IS NULL OR (source_generated_at <= generated_at AND source_generated_at >=
  strftime(…tipoff_at,'-2 hours'))`. 0 historical rows violate it.
- `coverage.record` (`:340-362`): for recorded rows, copy `dataset_version`/`feature_version` from the source row. For direct rows, use
  `eng.dataset_version(rows)` + `near_tipoff.feature_version`/`market_live.feature_version`. `decision_sha256` comes from
  `live.primary_model` (winner) / the sealed market decision (`nba_markets.py:127`, NHL decision sha). `code_version` =
  `NBA_RELEASE_COMMIT` or NULL ("not a release run"). Keep all four **out of `_content_key`** so they cause no revision churn.
  They describe the first computation of that decision.
- NFL (`nfl_live.py:117-121`): fill the same columns (sealed sha, dataset_version_open, definition).
- API/UI: CanonicalRecord optional fields. Past rows show "data/code identity not recorded (before <release>)". **No past-row
  rewrite.**
- Reads must tolerate the columns being absent (Telegram runs REPO code against live schema 31 until the release, H2).

### Regression test
`test_new_canonical_rows_record_dataset_and_code_identity` (`test_canonical.py`): `record_now`; with
`monkeypatch.setenv("NBA_RELEASE_COMMIT","abc123")`, assert the primary_recorded winner row's dataset_version equals the
prediction's, decision_sha256 equals the frozen decision sha, and code_version is "abc123"; a direct (preseason) row has
dataset_version ≠ NULL. **Fails on snap3**: the columns do not exist (verified from the schema), so the row lookup raises KeyError.
Plus `test_reads_tolerate_a_store_without_0036` (open a 0035 store, `/api/games` 200).

### Compatibility / impact
coverage.py → NFL hash (A3). Schema bump → all three release checkouts + release.check exact-match (C2). Migration needs the user's go.
Size M. Depends on B1 (same coverage.py edit window), A3, C2.

---

## B4 (M8 part) — NHL play-by-play availability back-dating

### Issue (confirmed defect for the live season; historical use is a declared assumption)
`nhl_shots.py:27-29` classifies a retrieval > 24 h after the start as `historical_backfill`. `:47-48` then sets `available_at =
max(result_available_at, tipoff)`, which is earlier than the retrieval. This is documented for history (`MODEL_SELECTION.md:225-226`) but
also applied to live-season games. Store: season 2027, 28 complete imports back-dated. 22 games with observed results average
18.6 h (max 36.7 h); 6 with assumed results average 55.5 h (max 62.9 h). 236 were observed on time.

### Is it used now?
- Live forecasting paths (`agreement.py:131-135` research-v2 xG member, `candidates/live.py:236-241`, adaptive via
  `adaptive._events` `:1071-1072`) filter `max(available_at, result_available_at) <= now`. A live run can only see rows
  already inserted, so there is **no live leak**. No canonical or primary forecast uses shots.
- Paths that evaluate a **past** cutoff after the late import landed do see data the live job did not have:
  `adaptive.record(replay=…)` (recovered forecasts, `:1755-1771`), adaptive training walks (`train` `:1084-1113`;
  train/serve skew, not label leakage), and fit-invalidation digests `_events_digest(events, known, cut)` (`:826-829`, may
  falsely flag "events changed"). Store: **3 NHL `adaptive_recovered_forecasts` winner rows** (recovery_id 49, 523, 577;
  games nhl:2026020064/0145/0190) were reconstructed after a back-dated import of a same-team game landed inside their gap. Their
  members include the xG member, so they are **possibly exposed** (not recomputed). They are research rows ("never included in live
  win rates").
- Historical research (xG challenger, holdout A on 2025-26, features-v1) used `historical_backfill` on seasons whose results
  are themselves assumed (tipoff+4h). That is the declared assumption, not affected by this defect.

### Proposed change
- `nhl_shots.import_game` (`:47-48`): `available = retrieved_at` unless the game belongs to a season completed before live
  collection began (season < first live NHL season, e.g. a `LIVE_SINCE_SEASON = 2027` constant), in which case keep the assumed time.
  The label `policy` is unchanged.
- `nhl_shots.load_events` (`:83-97`): effective availability = `retrieved_at` for live-season games, so the 28 stored rows are
  corrected at read time. Stored rows are not edited (the import ledger stays as recorded).
- Disclosure: `MODEL_SELECTION.md:225` amended **going forward** (implementation change, not an evaluation-plan change). Mark the 3
  recovered rows "possibly used play-by-play retrieved after the cutoff" in `/api/recovered-forecasts` (or list them in docs).
  Adaptive fits trained after 2026-10-02 that include 2027 games: note the skew. No other research result needs a disclosure.

### Regression test (written; fails on snap3)
`test_b4_late_pbp_of_an_observed_final_is_available_only_from_its_retrieval` (`test_nhl_xg.py`): synthetic NHL store,
observed final, import retrieved 2027-01-01. **snap3: `'2025-10-10T23:59:00Z' == '2027-01-01T00:00:00Z'` fails**. Add:
`test_replay_at_a_past_cutoff_does_not_see_pbp_retrieved_later` (load_events + filter at a cutoff between result and
retrieval → 0 events; fails on snap3).

### Compatibility / impact
Not in the NFL hash. NBA unaffected (box events have their own policy; NBA-A covers box timing). Research-only today; it changes
adaptive `events_digest` → some NHL adaptive fits may be reported "changed" after the release (expected, disclosed). Release only;
no migration. Size S.

---

## Prioritized table
| # | Issue | Proposed change | Test (fails on snap3) | Live impact | Approval |
|---|---|---|---|---|---|
| 1 | B2(i,ii) H4: reused/tuning seasons shown as "held up"/"strong" (Telegram "Recommended pick") | season_use.json; held_up=None + later_check; evidence basis; wording in UI/Telegram/docs (append disclosure after EVALUATION_PLAN:104, rule untouched) | test_b2_held_up… (**observed fail**); rank basis; season_use vs logs | Telegram text changes immediately from REPO (H2); web needs release | user go for the Telegram wording (or C1 first); docs/code none |
| 2 | B1 M2: lead time and winner quote use record time; record runs last | computed_at exposed; quote + minutes from it; record right after primary; sorted reasons; past-row disclosure | 2 tests (**observed fail**: 25.0≠60.0; 0.238) + 2 specified | release (NBA/NHL); coverage.py → NFL re-activation unless A3 | user go for release/re-activation |
| 3 | B2(iv) M6: one-shot guards per seal/per dir; NBA none; activation trusts hand-written files | committed test_runs.json registry; sport-wide refusal; report_sha256 at completion and activation; legacy entry for NFL | test_m6_second_seal… (**observed fail**); activation digest; NBA second access | NFL definition change → re-activation | user go (re-activation) |
| 4 | B4 M8: live-season pbp back-dated | availability = retrieval for live-season games; read-time correction; mark 3 recovered rows | test_b4_late_pbp… (**observed fail**); replay cutoff | release; research only | none for code; release go |
| 5 | B3 M13: no dataset/code identity on canonical rows | migration 0036 (4 nullable cols + computed-time trigger), filled for new rows, out of content key; past rows "unknown" | test_new_canonical_rows_record_identity (fails: no columns) | migration on all 3 stores (C2) | user go (migration/release) |
| 6 | B2(iii): no untouched season | preregistered prospective threshold_confirmation.json, start ≥24 h after commit, external hash, n=100 / season-end stop | sealed-file sha test (written with the file) | none until sealed; read-only report | user decides markets/start and records hash |

Not done / not shown: no test was run in REPO; the specified-but-unwritten tests were reasoned from code, not executed; no
recomputation of the 3 recovered NHL rows; Telegram mapping matched games by team abbreviations + start time (65 games;
old-season false matches discarded by hand); the market-Brier delta for row 762 is arithmetic on one game, not an API rerun.
