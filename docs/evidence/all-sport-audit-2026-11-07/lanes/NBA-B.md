# NBA-B — NBA forecasting & evaluation (read-only audit, 2026-11-07)

Scope: NBA forecasting only. Code = SNAP (`$SP/snap`, paths below relative to repo root) unless marked LIVE
(`~/nba-predictor-live`, a55c874). Store = `$SP/stores/nba.sqlite` (immutable). Dev API = 127.0.0.1:8871 (lead's, on
`$SP/work/devapi` copy). Scripts used: `$SP/work/NBA-B/{recompute,mkt,bt,diffmkt,spread}.py` (read-only, `-I`, immutable URIs).
No model fit, no recalibration, no reserved-season rows read by me (backtest test-season *stored predictions* were read only
to reproduce the already-published Performance number), no activation change, nothing written outside `$SP/work/NBA-B` and this file.

## 0. Inventory: what exists vs what is active

Launchd (installed plists in ~/Library/LaunchAgents are byte-identical to REPO/scripts/launchd; `launchctl list` checked):

| Job (loaded?) | Command (LIVE code, data dir = REPO/data) | NBA forecasting effect |
|---|---|---|
| odds (loaded, every 60 s) | `app.cli tick` → refresh when due; odds cycle every 15 min → `jobs.predict_now` | primary + research + canonical writes (below) |
| nba-markets (loaded, 22:30) | `app.cli markets` → same `jobs.predict_now` (LIVE cli.py:564-567) | an extra cycle, same writers |
| daily (loaded, 22:00) | `app.cli daily` (maintenance + `live.score`) | `score` scores the discontinued night-before ledger: job_runs detail `"live_forecasts": 0, "scored": 0` (2026-11-05, 2026-11-07) |
| injuries (loaded, :02/:32) | `app.cli injuries` | inputs for research member only |
| adaptive-train (installed, **NOT loaded**: `launchctl print gui/501/com.nba-predictor.adaptive-train` → "Could not find service") | training-inputs + adaptive-train | none since 2026-11-02 (last adaptive_fits finished_at 2026-11-02T19:25:32Z) |
| api (loaded, :8100) | uvicorn LIVE app.main | serves the views |

`predict_now` order (LIVE jobs.py:411-473): refresh → odds → boxscores → injuries → **near_tipoff.predict_now** (frozen Elo;
`predictions` run_type live + `near_tipoff_forecasts`) → **market_forecasts** (sealed `points_normal`, nba_markets.sealed_model)
→ research_forecasts (research-v2 members elo/logistic/logistic_v2/efficiency_margin/gbt_v1/availability_two_hour, run_type
live, `research-` prefixed model_version) → research cohorts/selection → candidate pool → pool selection → tracking →
**adaptive_record** (adaptive_forecasts) → **coverage.record** (game_forecasts = canonical) .

Classification:
- **Production/live (canonical, shown as the record):** frozen Elo winner (backend/app/models/frozen_decision.json via
  backtest.load_frozen_decision, backtest.py:571-603; near_tipoff.py), sealed NBA market model (nba_markets.py:120-130,
  market_live.py), coverage.py canonical selection (ORDER_VERSION coverage-v2: adaptive never approved, coverage.py:109-134),
  ledger.py, live.py, markets.py (settlement), odds.py (proportional de-vig), metrics.py.
- **Research, recorded live but never canonical:** adaptive.py (+combine.py, selector.py via research_live/adaptive),
  availability.py/availability_live.py (injury member), candidate pool, agreement/tracking, selector_decisions (85 rows, all
  `insufficient_evidence`, last 2026-11-02T14:14:49Z).
- **Retrospective/backfill (separately labelled):** recovered_forecasts (65), adaptive_recovered_forecasts (305); API note
  "Not live predictions; never included in live win rates" (/api/recovered-forecasts).
- **Historical studies:** backtest.py validation/test, calibration_study.py, nba_markets.research, player_ratings (players-v1),
  combination/discovery/deep/tuning studies, threshold history (combination_thresholds).
- **Working tree only (not in LIVE release):** paper_rules.py + /api/paper-rules (LIVE main.py/providers have no reference),
  threshold/ranked additions; exact_rules shown as `"status": "demo"` in LIVE main.py:364.
- training_inputs.py: only reachable from the (unloaded) adaptive-train job.

## 1. Findings (most severe first)

### F1 HIGH — Reserved test seasons reused; the "used once / never to select" claim is false and the UI presents them as independent confirmation
- Files: docs/EVALUATION_PLAN.md:26, :102-104; backend/app/threshold_overview.py:15-25, :192-203; frontend/src/components/ThresholdOverview.tsx:36-42;
  backend/app/candidates/study.py:509-523; backend/app/player_ratings.py:40, :760-762. Sport: NBA (same pattern NHL — not checked).
- Evidence: `cat REPO/data/eval/test_access.jsonl` → **5 lines**: 2026-09-30T14:40:35Z (frozen test) + 4 × `"study": "players-v1",
  "seasons": [2025, 2026]` on 2026-10-10 (15:23, 17:37, 17:43, 17:54). `pool_assess_access_nba.jsonl`: 2026-10-02T17:57:26Z
  `"pool-v1 assess", "seasons": [2025, 2026]`; candidates/pool_rules.json routes: candidates become *selectable* if they beat
  the strongest member "on held-out seasons" (= that assess stage). EVALUATION_PLAN.md:102-104 says the test seasons are
  "never used to tune, screen, select or justify a candidate … `test_access.jsonl` must stay at exactly 1 line".
- UI/API: `/api/canonical/thresholds?sport=nba` winner verdict `{'best': 0.8, 'better_than_all': True, 'held_up': True}`;
  "later" = seasons [2025, 2026] and "earlier" = [2022, 2023, 2024]. Earlier = the exact seasons the Elo config was tuned
  on (150-config grid by validation Brier, backtest.py:610-616), later = spent test seasons. The JSON contains no
  "reserved"/"spent" flag (`'reserved' in s → False`); the UI says "held up in 2024-25–2025-26". (combination_thresholds.py:643/879
  does flag spent seasons in its own report; threshold_overview does not propagate it.)
- Why: "held up" is shown as out-of-sample confirmation; it is the 6th+ logged use of those seasons, and the choosing seasons are
  in-sample for the Elo hyperparameters. Ranked picks/tickets lean on this evidence.
- Fix: correct EVALUATION_PLAN.md:102-104 (list every logged access; state the seasons are spent); add
  `later_reserved_test_seasons` + "earlier = Elo tuning seasons" to threshold_overview output and the UI sentence; enforce
  the log (refuse a second `--period test`/reserved access unless an amendment id is passed). NEW (HANDOFF not checked for this exact item).

### F2 MEDIUM — Canonical record is written only after all research steps; later primary forecasts are dropped, timestamps drift, games go unrecorded
- Files: LIVE jobs.py:429-473 (coverage.record last, after research/pool/tracking/adaptive); backend/app/coverage.py:340-345
  (`generated >= tipoff` → nothing recorded); coverage.py:286 (row time = coverage write time).
- Evidence (store): near_tipoff job durations — failed 108 (avg 10.5 min, max 70.4), success 87 (avg 7.8, max 52.5),
  **3 stuck `running`**. `game_forecasts.generated_at − source_generated_at` for winner/primary_recorded: 1–15 min 107,
  15–30 min 29, >30 min 14 (max 4226 s). Example espn:401909285 (tip 02:00Z): prediction 28508 at 00:11:04 → canonical row 706
  at 00:43:39; a newer prediction 28514 at 01:55:53 was never canonical because run 4716 ended 02:24:52 and coverage reported
  `"games": 0`. `diffmkt.py`: **11 of 120** graded games' canonical forecast ≠ latest near-tipoff forecast. Nov 4 run started
  22:27:50Z still `running`; no odds/near_tipoff job until 01:04:47Z → 6 games (espn:401909924–29) missing. Total missing 12/132.
- Effects: (a) canonical `minutes_before_start` and the market/paper-rule quote window use the later coverage time, not the
  model's computation time (real.py:750-760 `retrieved_at <= c["record"]["generated_at"]`; paper_rules via paper_trading._quote)
  → latent look-ahead for the bookmaker benchmark (0 quotes differ today, `mkt.py`: "quotes differing 0"); (b) the two live
  views disagree: market Brier 0.190658 (Within 2 hours) vs 0.191025 (Canonical) on the same 120 games, model Brier identical.
- Fix: record coverage immediately after the primary winner/market step (research afterwards); use `source_generated_at` for
  quote eligibility and minutes-before-start; add a watchdog for `running` near_tipoff rows. Ops root cause → lane C. NEW.

### F3 MEDIUM — Canonical/forecast rows do not fully identify the data/code version used
- Files: migration for game_forecasts (schema above: no dataset_version/feature_version); coverage.py:247-262 (`_Direct` rows:
  `table None, id None, generated_at None`); backtest.py:520-526 / near_tipoff.py:35-36 (model_version = config string only).
- Evidence: `game_forecasts` primary_direct regular-season rows: 19 per market (winner/spread/total/tt_home/tt_away) with
  NULL source_table/source_id/source_generated_at; none currently canonical (by_source = primary_recorded 120). Stored
  model_version `elo-carry=0.75-home_advantage=50-k=15-mov=True-window=2h-policy=near_tipoff` carries no frozen-decision sha
  or code digest; release sha only in job_runs.detail (not FK-linked).
- Positive: predictions/market_forecasts carry model+dataset+feature version; adaptive_forecasts carry definition,
  combiner_fit_id, training_cutoff; append-only triggers exist on predictions, market_forecasts, game_forecasts,
  adaptive_forecasts, candidate_forecasts, near_tipoff_forecasts (+ pregame/two-hour-window triggers). game_forecasts key =
  content hash, + generated_at on revision (coverage.py:270-292) → A→B→A gets a new row. `predictions` key excludes time
  (ledger.py: game/run_type/model/dataset/feature/cutoff) → A→B→A dataset returns the old (earlier) row, so "latest" would be B.
- Fix: store dataset_version/feature_version (or results fingerprint) on primary_direct rows; add code/decision digest to
  model_version or a release column. NEW.

### F4 MEDIUM — "Best threshold" / "beats all picks" conflates win rate with skill
- Files: backend/app/threshold_overview.py:15-25, :192-203; ThresholdOverview.tsx:36-42.
- Evidence: rule = highest Wilson lower bound on win rate; for any monotone model higher thresholds win more often, so
  "better_than_all" is close to automatic (winner: 80%+ chosen, earlier 272-61). Live 80%+ is 13-0 (13 picks); the
  bookmaker comparison column exists but the verdict ignores price. Disclaimers present ("A higher win rate is not a better bet").
- Fix: rename to "most reliable win rate", drop "beats all picks" or compare against market-implied for the same picks. NEW.

### F5 MEDIUM — Adaptive research runs on stale fits; working-tree adaptive identity ≠ production
- Evidence: adaptive-train not loaded (see §0); adaptive_fits max finished_at 2026-11-02T19:25:32Z, training_cutoff
  2026-11-02T04:36:04Z, yet adaptive_forecasts for definitions 39b484bc (daily) / 4a4f1e0e (weekly) continue to 2026-11-07T02:50:45Z.
  `/api/research/adaptive?sport=nba` on SNAP shows definition `ef1272ea6260e88c`, "no combiner fit for this definition yet",
  empty prospective record. `adaptive.definition()` on the same store copy: SNAP `ef1272ea6260e88c`, LIVE `6de26c884738862e`
  — neither equals the stored 39b484bc/4a4f1e0e (definition depends on data-dir research files; I used the devapi copy, so the
  LIVE mismatch is **unverified**). Not canonical (coverage-v2), so no served-forecast impact.
- Fix: surface fit age vs schedule in adaptive status; decide whether adaptive-train should be loaded (needs a go); before
  release, confirm the definition change is intended (it orphans the prospective adaptive history). Partly in HANDOFF? not verified.

### F6 LOW — "holds" calibration label rendered as "wins as often as stated"
- Files: threshold_overview.py:116-125; ThresholdOverview.tsx:14.
- Evidence: live winner 50%+: stated 0.6618, win rate 0.625, Wilson [0.536, 0.706], n=120 → "holds". Canonical calibration bins
  (API): 0.5–0.6 pred 0.547 obs 0.444 (27); 0.6–0.7 pred 0.651 obs 0.552 (29); 0.7–0.8 pred 0.757 obs 0.588 (17). "Holds" means
  "interval not excluding stated", not confirmation. Fix: "not distinguishable from stated (n=…)". NEW.

### F7 LOW — Nightly `score` job scores nothing
- LIVE live.py score() scores the night-before model version; job_runs 2026-11-05T03:15:37Z / 2026-11-07T03:00:28Z
  `"live_forecasts": 0, "scored": 0`. Near-tipoff/canonical are graded on read only. Fix: score near_tipoff + canonical or drop the step. NEW.

### F8 LOW — Model vs market reported without uncertainty
- Canonical winner: model Brier 0.2182 vs market 0.1910 (n=120); spread: 0.2620 vs 0.2485, spread pick accuracy 42.5% (51-69).
  No CI on the paired difference in the API. Honest direction (market better) is shown. Fix: add week-block bootstrap CI as
  backtest.block_bootstrap does for paper rules. NEW.

## 2. Leakage / cutoff trace (item 2)
- Winner inference: near_tipoff.py:85-92 `live.available_rows(conn, now)` → live.py:82-85 `result_available_at <= as_of`;
  backtest.load_rows (backtest.py:66-79) reads only `status='final'`. Refusals: after tipoff, stale refresh >60 min,
  earlier unfinished game of either team within 7 days (near_tipoff.py:68-75, 108-122); post-computation tipoff re-check
  (near_tipoff.py:125-129); DB trigger `predictions_live_pregame` (generated ≤ cutoff ≤ tipoff), `near_tipoff_forecasts` CHECK
  generated_at < tipoff_at. Store: 0 live predictions with generated_at > cutoff_at.
- Walk-forward: backtest.run raises LeakageError if a result/box is available at/before its own cutoff (backtest.py:147-159);
  boxes delivered at max(box available_at, result available_at); per-fetch append-only observations → later box corrections
  arrive as later events.
- Result corrections: ingest.py:428-429 moves result_available_at to the correction retrieval (store: 1 score correction,
  espn:401901822 preseason 117-98 → 116-98, availability 2026-10-17T02:35:25Z → 2026-10-18T16:13:17Z) → conservative, no
  look-ahead; but replays cannot reproduce the pre-correction state (dataset_version differs).
- Historical availability = tipoff+4h assumed (9412 finals, seasons 2020–2026); observed retrieval for 2027 (199).
- Injury member: report header publication time ≤ cutoff (historical: tipoff−2h; live: now, report ≤60 min old, reconciled)
  availability_live.py:156-221. Research only.
- Quotes: eligibility by `retrieved_at` (not bookmaker quote_time) ≤ forecast time (near_tipoff.py:53-66; market_live.latest_quote);
  caveat F2 for canonical.
- Frozen-decision guard: live Elo refuses if validation-season data changed since freezing (backtest.py:599-602).

## 3. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "test … accessed once … must stay at exactly 1 line" (docs/EVALUATION_PLAN.md:102-104) | contradicted | test_access.jsonl 5 lines; pool assess on [2025,2026] |
| "never used to tune, screen, select or justify a candidate" (EVALUATION_PLAN.md:102-103) | contradicted | pool-v1 assess → pool_rules.json selectability routes "held-out seasons" |
| Backtest note "evaluated once on held-out seasons" (/api/performance run_type=backtest) | verified for the Elo test run | Elo backtest rows all generated 2026-09-30T14:40:35Z; one decision-run log line |
| Canonical winner 120 graded, 75-45, acc 0.625, Brier 0.218236, LL 0.623320, 12 missing (/api/canonical/performance) | verified | recompute.py identical to 1e-15 |
| Canonical spread 51-69, acc 0.425, Brier 0.262001 | verified | spread.py |
| Backtest 2643 games acc 0.67196, Brier 0.210516, LL 0.608776 | verified | bt.py |
| Near-tipoff market Brier 0.190658 / canonical market Brier 0.191025 | both reproduced; differ by rule/timing | diffmkt.py: 11 games use different forecasts/quotes |
| "Market comparison uses the latest quote retrieved no later than the forecast" (API notes) | partly: canonical uses coverage write time, not model time | F2 |
| "no adaptive ensemble is approved for this record" (canonical note) | verified | coverage.py:123-130; by_source primary_recorded 120 |
| "Development research only (2023-24 holdout); no independent test yet" (meta spread/total) | consistent with code | nba_markets.research refuses test seasons (nba_markets.py:69-71) |
| "No injury or line-up information is used" (meta, canonical) | verified for Elo/points_normal | near_tipoff limitation; models take PregameGame only |
| "Estimates assume independent games … distinct games" (tickets/2026-11-06-final-three-test-v1.json) | stated; includes an NBA total leg the app labels "no proven skill" | ticket text |
| "Preseason … reported apart" | verified | 12 preseason games, 12 experimental rows, excluded from metrics |

## 4. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `plutil -p` 11 plists; `diff -q` vs ~/Library/LaunchAgents; `launchctl list \| grep nba-predictor` | inventory | identical; adaptive-train not loaded | done |
| `curl :8871/api/canonical/performance?sport=nba` (+`market=spread`), `/api/performance?policy=near_tipoff`, `run_type=backtest`, `/api/canonical/thresholds`, `/api/research/adaptive`, `/api/recovered-forecasts` | headline numbers | saved under $SP/work/NBA-B/*.json | done |
| `python -I recompute.py stores/nba.sqlite` and on devapi copy | = API | identical | pass |
| `python -I spread.py`, `bt.py`, `mkt.py`, `diffmkt.py` | = API | identical; 11 differing forecasts | pass / finding F2 |
| `OMP_NUM_THREADS=2 NBA_DATA_DIR=$SP/work/NBA-B/td PYTHONPATH=$SP/snap/backend .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_backtest.py -k "future_rows or reserved or before_its_own_cutoff"` | pass | 8 passed (with test_near_tipoff collected under -k) | pass |
| same, `tests/test_near_tipoff.py` | pass | 15 passed | pass |
| same, `tests/test_canonical.py -k "market or quote or perform"` | pass | 2 passed, 10 deselected | pass |
| `adaptive.definition()` with SNAP vs LIVE code on store copy (PYTHONDONTWRITEBYTECODE=1) | equal to stored defs | ef1272…/6de26c… vs stored 39b484…/4a4f1e… | finding F5 (LIVE part unverified) |

Not run: full suite (lead's gate); NHL counterparts; any reserved-season evaluation; adaptive training-row cutoff
inside adaptive.train (not traced line-by-line — unverified); calibration_study outputs; MODEL_SELECTION/EVIDENCE_AUDIT
numeric claims (historical docs, not re-derived); closing-line replay claims behind NO_PROVEN_SKILL (threshold_overview.py:36-40) not reproduced.

## 5. Open questions / cross-lane
- Lane C: near_tipoff cycles last up to 70 min, 3 stuck `running` rows, 108/238 near_tipoff runs "failed" (agreement step:
  `availability_two_hour: AvailabilityError: Invalid player minutes`, run 4716) — they block later cycles and cause the 12 coverage gaps.
- Lead: is loading adaptive-train intended to be off? (needs the user's go either way).
- NHL-B: same test-access/threshold "held up" pattern likely (nhl_eval PERIODS test 2025-26; test_access_nhl.jsonl).
- The SNAP adaptive.py change alters the adaptive definition digest; releasing it starts a new adaptive identity with no fits.
