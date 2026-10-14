# Input and threshold review — 2026-10-12

No network collection, live-store mutation, real-data training, or performance evaluation was performed.

## Confirmed defects repaired
- NHL normalized player rows could disagree with their hash-verified source. Both individual and bulk caches now reconstruct player rows from their raw JSON and compare them to the stored rows. Individual caches also verify source game ID and team names; filenames must match game IDs. Source JSON is parsed only after checking its hash. Bulk payloads are shared and parsed once per load.
- Threshold tables accepted invalid saved vectors. They now reject nonfinite, negative, over-one and non-unit-sum probabilities, across winner/spread/total/team totals/regulation three-way markets.
- Daily-rule application zipped mismatched member/threshold lists, silently omitting a member. It now requires corresponding finite thresholds and unique members, and rejects invalid probability vectors with an explicit no-pick reason.

## Actual verification
- `backend/.venv/bin/python -m pytest -q backend/tests/test_nhl_player_ratings.py backend/tests/test_combination_thresholds.py backend/tests/test_daily_agreement.py`: 100 passed in 15.39s after fixes (one Starlette/httpx deprecation warning).
- Additional daily application assertions across all six market types: `backend/.venv/bin/python -m pytest -q backend/tests/test_daily_agreement.py -k apply_rejects`: 2 passed, 17 deselected in 0.15s.
- Raw/normalized mismatch regressions failed in both cache formats before repair. Invalid saved vectors failed in 24 market/vector combinations; truncated frozen rule reproduced a wrong pick before repair.
- Structure guard and diff whitespace checks passed. No full readiness/browser verification; Claude owns that gate.

## Read-only real NBA sample
Store: `/Users/andry/nba-predictor-dev-data/nba.sqlite`, opened with SQLite URI `mode=ro`.
Cache: `/Users/andry/nba-predictor-dev-data/research/availability`.
Loaded with `player_ratings.AdaptiveInputs(cache, player_ratings.team_names(conn))`.
Box seasons: 2020–2024; 6,276 games; box digest `83e2667566cc7f31`.
Reports: 1,002, dated 2019-10-22 through 2024-06-17.
Sample: sort report dates, select `days[::max(1,len(days)//30)][:30]`, call `get(day, day+'T21:00:00Z')`.
Result: 28 usable reports, 2 excluded as published after the cutoff. This is report-level availability at a fixed sampled UTC cutoff, NOT per-game tip-minus-two-hour coverage.
Eight unmatched entries remained unresolved: Gerald Green and Nene Hilario on 2019-11-24 and 2020-01-31; Robert Franks on 2019-12-29; Ray Spalding on 2020-03-11; Omari Spellman on 2020-12-31; Justin Lewis on 2023-11-22. No guessed aliases added. Their cause is not established from this sample.

## Remaining evidence gaps
- The earlier isolated NHL cache is unavailable, and no NHL player cache exists in the checked project/deep/dev/live research locations. Synthetic NHL integrity checks do not establish real coverage or name-match rates.
- Older bulk caches encode home/away side by the collector's ordered team mapping. Reconstruction checks player data but does not independently prove those stored team IDs/names against the schedule. Claude should reconcile team identity and source completeness on actual caches before fitting.
- NBA cache is historical and ends in 2024; no current-season readiness claim. Full per-game cutoff/coverage audits, remaining identity ambiguities, and all live/browser paths remain independent review work.
