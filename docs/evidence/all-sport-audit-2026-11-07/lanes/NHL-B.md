# NHL-B — NHL forecasting & evaluation (read-only audit, 2026-11-07)

Code audited: SNAP (`$SP/snap`, HEAD 79ff6ec + diff). Store: `$SP/stores/nhl.sqlite` (schema 31) and the dev-API copy
`$SP/work/devapi/nhl.sqlite` (same rows, migrated to 35; counts compared: market_forecasts 4075, predictions 10845,
game_forecasts 991, same max generated_at). Dev API: `127.0.0.1:8871` (SNAP code, NBA_DATA_DIR=`$SP/work/devapi`).
Live release `~/nba-predictor-live` (a55c874) compared file by file for the NHL forecasting path.
`$SP` = `/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad`.

No CRITICAL finding. Settlement, line binding, cutoff guards and the Performance numbers I recomputed all reproduce exactly.
The findings are about evidence quality and versioning.

## 0. Inventory: what exists vs what is ACTIVE

Loaded launchd jobs (`launchctl list | grep nba`): nhl-odds (`app.cli --sport nhl tick`, every 60 s, live checkout),
nhl-daily (`--sport nhl odds; --sport nhl daily`, 22:15), plus the API/web. **`com.nba-predictor.adaptive-train` is installed
in ~/Library/LaunchAgents but NOT loaded.** Its log `data/adaptive-train-launchd.log` was last written Nov 2 15:34. The newest
NHL `adaptive_fits` row (fit 590, the daily combiner) has training_cutoff 2026-11-02T04:00:50Z.

Path in the live release: `jobs.tick` → `odds_cycle` → `forecast_due` → `predict_now` (near_tipoff Elo) → `market_forecasts`
(market_live) → `research_forecasts`/`research_selection`/`candidate_pool`/`pool_selection`/`agreement_tracking`/`adaptive_record`
(all on, because `RESEARCH_SELECTION` defaults to "1") → `coverage.record` (canonical `game_forecasts`). Then `recover_missed`
and `recover_adaptive_missed`. Nightly `daily`: refresh, nhl_pbp, `live.score`, `near_tipoff.score`, `market_live.score`,
training_monitor, and adaptive due-state only.

| Component (SNAP file) | Class | Evidence |
|---|---|---|
| `hockey_elo` winner (models/hockey.py, nhl_frozen_decision.json) | **production/live** | 239 `predictions` rows `hockey_elo-…-window=2h-policy=near_tipoff`, feature `nhl-pregame-ids-v1+tipoff-cutoff+two-hour-v1` |
| `goals_poisson` + empty-net market model (market_live.py, markets.py) | **production/live** | 1044 rows `goals_poisson-carry=0.75-empty_net=l1=0.29-l2=0.42-s=0.97-…-window=2h-policy=near_tipoff`, 181 games, 2026-10-09 → 2026-11-07 |
| Canonical record (coverage.py, `game_forecasts`, coverage-v2) | **production/live** | 991 rows. Sources: primary_recorded 841, primary_direct 126, none 24, adaptive **0** ("no adaptive definition is approved") |
| Paper rules (paper_rules.py, reference/paper_rules.json) | live, read-only report; **not in the live release** (no file in a55c874) | `nhl-winner-65`, frozen 2026-11-01T06:30Z |
| Research cohorts, agreement v2, candidate pool, selectors (research_live, agreement*, candidates/*) | **research candidate, recording live** | predictions `research-v2:*` (4 definitions), candidate_forecasts 6544, research_selections 2334 |
| adaptive-v1 (adaptive.py, combine.py) | **research candidate, recording live**; fits stale since Nov 2 (trainer unloaded) | adaptive_forecasts 2592; members incl. `cand:*@adaptive`, `hockey_logistic_xg@adaptive`; `nhl_players_injuries@adaptive` missing ("stale inputs: missing recent NHL player boxes") |
| nhl_player_ratings.py (injury/player member) | **present but effectively unused** (excluded on every recent row) | `missing` JSON on the latest adaptive row |
| nhl_shots.py (pbp import) | live input to research (`nhl_pbp` when research is on) | nhl_shots 1.27M rows |
| nhl_eval.py validation/test/agreement/learned/xG | **historical study** (sealed once) | test access log has 1 entry 2026-10-01T03:23:43Z |
| exact_rules.py | **unused**: no registry file `backend/app/models/exact_rules.json` | `FileNotFoundError` when opening it |
| odds.py | live (proportional de-vig) | quote 66023: −238/+195 → 0.675031/0.324969 = proportional |

SNAP vs live: `nhl_eval.py, market_live.py, combine.py, exact_rules.py, odds.py, near_tipoff.py, recovery.py, models/hockey.py,
nhl_frozen_decision.json` are byte-identical. `markets.py` differs only by the NFL specs and a winner tie → void (no effect on
NHL). `coverage.py`/`live.py` differ in refactors and the sport guard. `adaptive.py`, `jobs.py` and `nhl_player_ratings.py`
differ (not chased; this is research code).

## 1. Findings (most severe first)

### F1 MEDIUM — "History" threshold evidence for NHL winner/reg3 is labelled out-of-sample but its "earlier" seasons are the seasons the primary was tuned and selected on. NEW
- `backend/app/threshold_overview.py:10,207` ("saved walk-forward out-of-sample forecasts"), `choose()` :187, `evidence_ladder()` :258.
- Evidence: `GET /api/canonical/thresholds?sport=nhl` returns winner `history.earlier.seasons` = [2018…2024] and
  `later` = [2025, 2026]. `nhl_eval.PERIODS` (nhl_eval.py:22): development 2018-2021 (the Elo grid of K/H/carry/MOV was tuned
  on these by Brier), validation 2022-2024 (the winner model was selected on these). The verdict is
  `{'best': 0.7, 'better_than_all': True, 'held_up': True}`. reg3 gives best 0.6, held_up True.
- Why it matters: the walk-forward is out-of-sample for the *fitted state*, but not for the hyper-parameters or the model
  choice. "Best threshold" is chosen on in-sample-tuned seasons. The "later" seasons (2025-26) are the one-time test seasons,
  which were reused afterwards by pool stage 2 (`data/eval/pool_assess_access_nhl.jsonl`: 2022-2026 on 2026-10-02) and by the
  market holdout A (HANDOFF:900). They are no longer a clean confirmation. The ranked-pick "strong" label (`rank()`
  :299, `evidence_ladder` sums earlier+later) inherits this optimism. With 4 Elo parameters on about 5k games the bias is
  probably small, but the label overstates what the evidence is.
- Fix (text): label history as "walk-forward, hyper-parameters tuned on 2018-21 and selected on 2022-24; 2025-26 already
  reused". Or restrict "earlier" to seasons not used for tuning or selection (none remain for NHL). Then state that "held up"
  is a reused-test check, not a fresh holdout.

### F2 MEDIUM — Canonical forecast rows (`game_forecasts`) do not store dataset/feature version; 126 `primary_direct` rows cannot be traced to a data version. NEW
- Schema `game_forecasts` (migration in SNAP; columns listed in this report's evidence): model_version only, with no dataset_version
  or feature_version. `coverage._Direct.forecast` (coverage.py:236-262) computes the probability in-process and stores
  `table=None, id=None`.
- Evidence: `select source, source_table, count(*) from game_forecasts group by 1,2` → primary_direct NULL 126,
  primary_recorded market_forecasts 702, predictions 139. The recorded ones are traceable: 702/702 have the same line, p_a
  and quote id as their source `market_forecasts` row.
- Why it matters: these are the forecasts the app grades on every page (canonical). For the 126 direct rows (21 games × 6
  markets) the result set that produced them is not recorded, so a replay cannot confirm them. This matters for preseason
  and "no recorded forecast" games.
- Fix: add `dataset_version`/`feature_version` (and `results_used`/latest `result_available_at`) to `game_forecasts`, filled from
  `_Direct` (rows, version) and copied from the source row for recorded ones.

### F3 MEDIUM (known) — Live market model is not the sealed model: `empty_net=True` is injected outside the seal, and its constants have no committed fit/check script. Already in HANDOFF (2671-2673, 2882)
- `backend/app/market_live.py:32` `GoalsConfig(**{**d["goals_config"], "empty_net": True})`. `markets.py:151-155`
  `EMPTY_NET = {0.29, 0.42, 0.97}` with a comment claiming a dev fit and validation check ("Test seasons were not used").
  `nhl_frozen_decision.json` has `goals_config {'eta': 0.003, 'carry': 0.75}` only.
- The change was motivated by the test result ("Two-goal wins were under-predicted in testing", markets.py:297). The
  validation seasons had already been used for selection. So its only clean evidence is prospective. Live canonical puck-line
  record: model Brier 0.2283 vs bookmaker 0.2254 on 121 matched games (`/api/canonical/performance?market=spread`).
- Additional NEW detail: the limitation text `markets.py:297-298` still says "it has no live record yet". There are 181 games
  of live rows for the empty-net version (it is served with every puck-line forecast; see the API payload in §3).
- Fix: commit the fitting script with the data version; refresh the limitation text; state in MARKETS.md that the live market
  model = sealed goals_poisson + post-test empty-net adjustment.

### F4 MEDIUM (cross-lane NHL-A) — Assumed result availability earlier than first observation for current-season games affects replays. NEW
- Rows: `nhl:2026020001…0005, 0007` (season 2027, regular) have `result_availability_basis='assumed_tipoff_plus_4h'` with
  `result_available_at < result_first_seen_at`. Example: `nhl:2026020007` available_at 2026-10-01T06:00:00Z, first seen final
  2026-10-02T02:15:03Z.
- `live.available_rows` (live.py) and `recovery.py:62` treat a result as known from `result_available_at`. A replay as of a
  time in that gap uses a result the live job did not have, so its dataset_version ≠ the recorded one. No recovered forecast
  falls in the gap (recovered cutoffs start 2026-10-06T22:59:59Z), so no stored row is affected today.
- Fix (lane A): for games first observed final by a live retrieval, use `observed_retrieval`, or max(assumed, first_seen).

### F5 LOW — reg3 draw pick graded differently by two pages (latent). NEW
- `coverage.pick_side` (coverage.py:415) lets the draw be the reg3 pick (product decision 2026-10-14), and a draw then grades
  `win`. `market_live.pick` (market_live.py:137) never picks the draw, and `result_for_pick` grades any reg3 `mid` as loss.
  Reproduced: `coverage.grade('reg3',None,(0.3,0.4,0.3),SO game)` → `win`; `market_live.result_for_pick('reg3','a','mid')`
  → `loss`.
- No stored row has the draw as the maximum (market_forecasts reg3: 807 rows, 0; game_forecasts reg3: 160, 0), so nothing
  differs today.
- Fix: use `coverage.pick_side` in market_live (the legacy "Within 2 hours" view), or document the difference.

### F6 LOW — Docs omit or blur NHL negative results. NEW
- `docs/MARKET_EDGE_RESEARCH.md` (dated 2026-10-29) calls NHL "tied … the only live question" (lines 29, 102, 118). It does
  not mention that the pre-registered NHL holdout A (hockey_logistic_xg, 2025-26) already FAILED on 2026-10-16 (close ROI
  −6.4% [−15.8, +2.6], HANDOFF.md:900-901). That failure is recorded only in HANDOFF. The doc's 62-game NHL numbers (model
  0.2278 vs market 0.2313) are now reversed: canonical winner on 121 games, model 0.22519 vs market 0.22385. Spread is
  0.2283 vs 0.2254 and total 0.2471 vs 0.2458: the bookmaker is ahead in every quoted market (point estimates; no CI computed).
- `docs/MARKETS.md:44` team totals say "OT and SO count" without "(SO goal included)". The code counts the shootout goal for
  the winner's team total (verified, §2).
- `docs/NHL_BASELINE_REPORT.md:56` ("No goalie, injury … inputs are used") is true for the sealed primaries. Live research
  members do use goalie listings and injury reports (not canonical).

### F7 LOW — Test `test_walk_forward_uses_only_results_available_before_each_cutoff` asserts less than its name. NEW
- `backend/tests/test_nhl_eval.py:110-123` only asserts that updated results have `tipoff < game.tipoff`. It would pass if a
  result delivered after the cutoff (e.g. a previous-evening game at tipoff+4h past the 00:00 ET cutoff) were used. The
  engine itself is correct: `backtest.py:158-162` compares `result_available_at <= row.cutoff_at` and raises LeakageError
  when a game's own result precedes its cutoff.
- Fix: assert `result_available_at <= cutoff_at` in the spy.

### F8 LOW — Data oddities seen while grading (cross-lane NHL-A)
- `nhl:2015010045` (2016 preseason) has a final of 4-1 with decision OT, periods 4. reg3 would grade it as a draw. It is outside
  training and canonical (preseason).
- Provider written two ways in `market_quotes`: "Draft Kings" (55 rows per market/label) and "DraftKings" (11,521).
  `latest_quote` ignores the provider, so there is no grading effect.
- `near_tipoff_forecasts.market_quote_id` references `odds_quotes`, which is empty for NHL. All 239 NHL rows store NULL, and
  the winner bookmaker comparison is re-derived from `market_quotes` at read time (near_tipoff.py:54-62, documented).

## 2. Settlement reproduction (task items 2-3)

Rule (MARKETS.md:12-14, 40-44, 60-64; code `markets.settle` markets.py:60-83):
- winner: final including OT and the SO goal.
- reg3: decision REG gives home/away; OT or SO gives a draw.
- puck line, total and team totals: final score including the SO winner's +1 goal.
- An exact hit on a line is a push.
- void when the game is postponed or canceled, or its start moved after the forecast.

Store check (my computation from store rows): all 1048 SO finals and 2524/2525 OT finals have a 1-goal margin, so the stored
finals include the SO goal. The exception is F8.

By-hand settlement of the latest current-version market forecasts vs the dev API `/api/market-predictions?sport=nhl&policy=near_tipoff`:

| Game (final, decision) | reg3 | puck line | total | home TT | away TT |
|---|---|---|---|---|---|
| nhl:2026020258 CBJ 2-1 DAL (SO) | draw; pick b → loss | +1.5: 2.5>0 a; pick a → win | 5.5: 3 under; pick over → loss | 2.5: 2 under; pick over → loss | 3.5: 1 under; pick under → win |
| nhl:2026020232 BOS 3-2 VGK (SO) | draw; pick a → loss | +1.5 → a; pick a → win | 6.5: 5 under; pick under → win | 3.5: under; pick under → win | 3.5: under; pick under → win |
| nhl:2026020254 NJD 5-4 STL (OT) | draw; pick a → loss | −1.5: −0.5 → b; pick b → win | 5.5: 9 over; pick over → win | 3.5: 5 over; pick under → loss | 2.5: 4 over; pick over → win |

The API returned the same 15 results. Stored lines match their quotes exactly: forecast 3866 line 1.5, quote 66023 line 1.5,
retrieved 22:51:53Z, forecast generated 22:52:00Z. DB triggers `market_forecasts_quote_matches` and `markets_two_hour_window`
enforce quote line = forecast line, quote retrieved ≤ generated and ≤ cutoff, and generation within 2 h before a scheduled
start. Canonical `game_forecasts` carry the recorded line: 327/327 quote-linked rows have line = quote line and retrieved ≤
generated. Grading uses the stored `line`, never a later one (`coverage.grade` with `sel["line"]`; `market_live.outcome` with
`row["line"]`).

Integer lines: none exist in the NHL store (`market_quotes` and `market_forecasts`/`game_forecasts` with an integer line: 0
rows). Code check on SNAP: `settle('total',5.0, 3-2 SO)` → mid; `coverage.grade` → push; `result_for_pick` → push; spread −1
on 3-2 → mid. The poisson grid gives the push mass (total 6.0 → 0.4301/0.1089/0.4610). A paper-rule push has profit 0
(paper_rules.py:64).

## 3. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "For the NHL this includes the one goal credited to a shootout winner" (MARKETS.md:12-14) | verified | all 1048 SO finals have margin 1; 15/15 hand settlements = API |
| "regulation only: any OT/SO game is a draw" (MARKETS.md:41) | verified | 3 OT/SO games graded draw; `settle` markets.py:73-78 |
| "Each forecast stores its exact line … latest bookmaker line retrieved at or before generation" (market_live.py:3-7) | verified | trigger `market_forecasts_quote_matches`; forecast 3866/66023 |
| "Test access log … one entry, 2026-10-01T03:23:43Z" (NHL_BASELINE_REPORT.md:9-10) | verified | `data/eval/test_access_nhl.jsonl` has exactly that line |
| 2025-26 used "once" as a holdout | contradicted in effect, disclosed elsewhere | test 10-01, pool stage 2 (2022-2026) 10-02, holdout A 10-16. CANDIDATE_POOL.md:63 and HANDOFF:814 disclose this |
| Threshold history = "out-of-sample" (threshold_overview.py:10) | partly contradicted | F1 |
| "it has no live record yet" (empty-net, markets.py:297-298) | contradicted | 181 games of live rows for the empty-net version |
| "EMPTY_NET … fit on dev 2018-21, checked on validation; test seasons not used" (markets.py:151-154) | unverified | no fitting script in repo (HANDOFF open item) |
| "NHL … tied … the only live question" (MARKET_EDGE_RESEARCH.md:102,118) | stale/incomplete | F6 |
| "no adaptive ensemble is approved for this record" (canonical note) | verified | 0 `adaptive` sources in game_forecasts; fallback_reason "not approved for canonical use" ×835 |
| Canonical winner 78-43, 64.5%, Brier 0.22519, LL 0.64211 (API) | verified (recomputed) | `$SP/work/NHL-B/scripts/recompute.py`, exact to 1e-15 |
| Wilson 95% [0.5561, 0.7243] for 78/121; [0.6144, 0.8151] for 53/73 (API) | verified | independent Wilson computation |
| Near-tipoff winner sample 210, accuracy 0.62857 (API /api/performance) | verified | SQL: 213 games, 210 final, 132 correct |
| De-vig = proportional (odds.py:11-14) | verified | quote 66023 arithmetic |
| Paper rule price = latest current quote at/before the forecast (paper_rules.py:3, paper_trading._quote) | verified (code) | `retrieved_at <= at`, `line IS ?`, `price_label='current'` |
| "closing" comparison | n/a: no closing feed; `coverage.closing_summary` labels "Last pregame snapshot … not a bookmaker-published closing line" | coverage.py:69-85 |

## 4. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `launchctl list \| grep nba` | which NHL jobs are loaded | nhl-odds, nhl-daily loaded; adaptive-train absent | info |
| `diff` of ~/Library/LaunchAgents vs repo plists (nhl-daily, nhl-odds, adaptive-train) | same | same | pass |
| `cmp` SNAP vs live for the 17 scope files | — | listed in §0 | info |
| `curl …/api/market-predictions?sport=nhl&market={reg3,spread,total,team_total_home,team_total_away}&policy=near_tipoff&date_from=2026-11-02&date_to=2026-11-05` | 15 grades = by hand | 15/15 equal | pass |
| `python3 -I $SP/work/NHL-B/scripts/recompute.py $SP/work/devapi/nhl.sqlite 2026-11-07T20:38:37Z winner reg3 spread total` vs `/api/canonical/performance?sport=nhl&market=…` | equal | W-L, pending, accuracy, Brier, LL equal for all 4 markets (winner 78-43, reg3 65-56, spread 74-47, total 71-50) | pass |
| SQL near-tipoff latest-live (LATEST_LIVE_SQL logic) vs `/api/performance?sport=nhl&policy=near_tipoff` | 210 / 0.62857 | 210 final, 132 correct | pass |
| Wilson recompute 78/121, 53/73 | = API ci95 | equal | pass |
| `pytest -q tests/test_nhl_eval.py -k "walk_forward or delivered or earlier_seasons or settlement or validation_runs_once or refuses_changed"` (SNAP, NBA_DATA_DIR=$SP/work/NHL-B/data, OMP_NUM_THREADS=2) | pass | 6 passed, 8 deselected | pass (see F7 on the assertion) |
| `pytest -q tests/test_nhl_player_ratings.py -k "cutoff or future or live_capture or delivered"` | pass | 6 passed, 49 deselected | pass |
| In-process `markets.settle`/`coverage.grade`/`result_for_pick` edge cases (integer total, reg3 SO, missing decision, moved start) | per the rule | push/push/push; draw; unscorable; void | pass (F5 noted) |
| SQL: OT/SO margins; integer lines; reg3 draw-as-max; canonical line = source line | — | §2, F5 | info |

Not run:
- Full suite. That is the lead's gate.
- `nhl-validate`, `nhl-test`, pool assess, holdout scripts. These would read or log reserved seasons and are forbidden.
- Any model fit or recalibration.
- Reproduction of the EMPTY_NET fit. There is no script.
- Bootstrap CI for model vs market Brier on 121 games. That needs a paired resample script; I report point estimates only.
- The NHL player and injury member on live data. It is excluded on every recent row.

## 5. Open questions / cross-sport concerns
- adaptive-train is unloaded, so the "daily" adaptive definition (92443ff7…) has served a fit from 2026-11-02 for 5 days. Is
  this intended (retrain waits for go)? If so, the definition's "daily schedule" label is misleading (lane C).
- `game_forecasts` versioning gap (F2) is shared code. NBA canonical rows have the same schema.
- The threshold history (F1) uses the same construction for NBA `elo` (tuned/validated seasons as "earlier"). That needs
  checking by NBA-B.
- `market_quotes` provider spelling and assumed availability (F4, F8) belong to the NHL-A data lane.
- Overlap: ranked picks (`threshold_overview.rank`) list winner, reg3 and puck line of the same game separately with
  separate Wilson CIs. No aggregate across markets treats them as independent. Paper rules are one market each with a
  week-block bootstrap. But a user summing "strong" picks across markets of one game would double count.
