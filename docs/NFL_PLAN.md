# NFL plan (data-only store first, then a winner model)

Recorded 2026-11-01 (session date), before any NFL model was run or any NFL rate, accuracy or Brier score was computed.
Authorisation: the user's request to add the NFL "without breaking the code". Existing rules still apply: AGENTS.md guards,
docs/EVALUATION_PLAN.md principles, docs/MULTISPORT_PLAN.md (one schema, one store per sport). No NBA or NHL parameter
value or result is transferred to the NFL.

## Safety design: registered, not served
- `sports.NFL` lives in `sports.DATA_ONLY`, not in `sports.SPORT_IDS`/`SPORTS`. `sports.get("nfl")` raises
  `UnsupportedSport`, so every served path (API `sport=` query, `--sport` CLI choices, `live.engine`, `RealProvider`,
  research and jobs) refuses NFL. About 75 places in the backend branch "if nba … else NHL"; none of them can be reached
  with an NFL store while NFL stays out of `SPORT_IDS`.
- `live.engine` now names its sports explicitly (NHL → hockey engine, NBA or unlabelled → NBA engine, anything else →
  `UnsupportedSport`) instead of treating every non-NHL store as NBA.
- Own store `data/nfl.sqlite`, created by the same migrations; `store_identity` binds it to `nfl` (migration 0032 adds
  `nfl` to the allowed values of that one table only; market/research tables still allow only `nba` and `nhl`).
- Own commands, independent of `--sport`: `python -m app.cli nfl {migrate,ingest,replay,coverage}` under the NFL job lock.
- NFL joins `SPORT_IDS` only after every branch on the sport handles it explicitly (phase C below), with tests.

## Source
ESPN public NFL scoreboard (`site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard`, free, no key; same
unofficial-endpoint caveat as the NBA adapter). One request per calendar month (`dates=YYYYMM&limit=1000`; a date range is
refused). Checked 2026-11-01: months back to 1999 return final results; 32 franchise ids (1–30, 33 BAL, 34 HOU); 31/32 are
the Pro Bowl AFC/NFC sides; international games and the Super Bowl carry `neutralSite: true`; ESPN keeps a franchise's id
through relocations (Rams, Chargers, Raiders), so ratings follow the franchise. Season = ESPN season year = the year the
season starts (2024 = 2024-25). Schedule, status and final score only; odds, injuries and box stats are later phases.

## Phases
### Live activation amendment — 2026-11-02
User explicitly requested active NFL predictions throughout the app. Implement A2 through the existing dedicated
NFL namespace; keep `SPORT_IDS`, shared NBA/NHL model dispatch and research guards unchanged. Reuse the sealed NFL
learners, existing canonical ledger, market quotes, percentage records, API client and Telegram renderer. Complete the
single frozen test on a scratch copy first; never tune from it. Winner recommendations require its frozen confirmation
and threshold tests. All four score markets remain informational. Inputs stop at kickoff minus 120 minutes even when a
cycle runs late; record actual generation time, never backdate. Live records start empty and remain separate from tests.
Activation requires a pinned, reviewable NFL release; never point scheduled NFL writes at this dirty checkout.
Schema amendment during integration: `market_quotes.sport` also has a two-sport CHECK. Migration 0033 rebuilds
that existing table with NFL allowed, preserving row IDs, indexes, uniqueness and every append-only/store trigger.
The migrator must temporarily turn foreign keys off outside this one rebuild transaction, run a full foreign-key
check before commit, then restore enforcement in `finally` (SQLite's documented parent-table rebuild procedure).
Only the NFL live store is migrated/activated here; NBA/NHL scheduled releases and stores remain untouched.

- [x] A0 Guards + registry + migration 0032 + ESPN NFL parser + ingest/replay + coverage (tests: `tests/test_nfl.py`)
- [x] A1 (frozen one-shot test completed 2026-11-02; see docs/evidence/nfl-winner-test-2026-11-02/) Evaluation plan below recorded (done in this file) → walk-forward of the winner candidates on development and
      validation seasons → seal the choice → one logged test run
- [x] A2 Dedicated `nfl_live` adapter behind its own commands (shared engine remains guarded): two-hour-window forecasts (as NBA/NHL) in the NFL ledger and grading
- [ ] C  Make every "nba … else NHL" branch explicit, then add `nfl` to `SPORT_IDS` (API, UI toggle, jobs) — needs the
      user's go, like any live change
- [ ] B  Spreads/totals (football score model: key numbers 3/7), injuries (official report), weather — each preregistered first

## NFL evaluation plan (recorded 2026-11-01, before any NFL model is run)
Before this was written, NFL scoreboard months 2024-09/10, 2025-01/02 and single months of 1999/2002/2006/2010 were opened
**for schema and coverage checks only** (fields, ids, neutral flags, event counts); no model, rate or accuracy was computed.

- **Eligible:** final regular-season and postseason games between two franchises. Excluded: preseason, Pro Bowl (`other`),
  unplayed games. A tie is a final result: it updates ratings as half a win, and it is **void for winner scoring**
  (no winner; the bookmaker moneyline is usually refunded), counted and reported separately.
- **Point in time (amended 2026-11-01, before any NFL model result; user decision):** the cutoff is the **two-hour window,
  the same in history and in serving: kickoff − 120 min** (stored kickoff time). A historical result is available at its
  kickoff + 4 h (ESPN history has no first-seen time; conservative) and is used by **every** candidate only when available
  at or before the cutoff. This matters for the running home-win rate, which uses all teams' results: e.g. Sunday 1 p.m.
  games (available 5 p.m.) count for an 8:20 p.m. game (cutoff 6:20 p.m.) but not for a 4:25 p.m. game. Team-only Elo is
  unaffected (a team plays at most once a week) but follows the same rule. Replaces the earlier night-before cutoff.
- **Later information:** NFL inactive lists appear about 90 min before kickoff, after a T−120 forecast. Any later
  injury/inactive-based update needs its own preregistered forecast-version policy (phase B) before it exists; until then
  there is one forecast per game under the NBA/NHL canonical rules.
- **Neutral site (as the parser, `sources/espn._nfl_adjust`):** neutral = ESPN's `neutralSite` flag, **or** a venue
  country other than USA (London, Germany, Mexico, Brazil, the Bills' Toronto series), **or** a postseason game whose event
  note starts "Super Bowl" (before 2014 ESPN leaves the flag false for international games and Super Bowls). Neutral games
  get no home advantage (H = 0 for that game; the home-rate baseline predicts 0.5). Games dated outside August of the season
  year … March of the next are `other` and excluded. The 2020 season (few or no fans) is not special-cased.

| Period | Seasons (ESPN year) | Use |
|---|---|---|
| Warm-up | 2002, 2003, 2004 | burn-in only, never scored |
| Development | 2005 … 2016 | each candidate's own grid (below) |
| Validation | 2017 … 2021 | choosing among the tuned candidates, once each |
| Test (reserved) | 2022, 2023, 2024, 2025 | run once with the frozen choice; every access logged |
| Prospective | rest of 2026, then 2027 | live two-hour-window record |

- **Winner candidates:** constant 0.5; Laplace-smoothed running home win rate; `nfl_elo` (K ∈ {10, 15, 20, 25, 30},
  H ∈ {0, 25, 50, 75}, carry ∈ {0.5, 0.67, 0.75, 1.0}, margin multiplier off/on with
  `mult = ln(|margin| + 1) × 2.2 / (0.001 × winner_diff + 2.2)`). Criterion: development Brier on non-tie games.
  **Tie-break (complete, fixed order):** Briers equal after rounding to 6 decimals are tied; then prefer multiplier off,
  then carry 1.0 > 0.75 > 0.67 > 0.5, then the smallest K, then the smallest H.
- **Bootstrap (fixed):** `backtest.paired_bootstrap` — per-game Brier difference, blocks = ISO weeks (UTC) of the cutoff,
  seed 20260930, 2,000 resamples, 95 % percentile interval.
- **Validation choice:** Elo is chosen only if its validation Brier is the lowest of the three AND the upper bound of the
  95 % interval of (Elo − home rate) is below 0. Otherwise the home rate is chosen if it beats the constant 0.5 by the same
  rule; otherwise the constant 0.5. Each tuned candidate is scored on validation once.
- **Ties and the margin multiplier in the candidates (pinned 2026-11-01, before results):** a tie counts as half a home
  win (actual = 0.5) in Elo, and as 0.5 home win in the home-rate count (non-neutral games only, as before). Margin
  multiplier on: `mult = ln(max(|margin|, 1) + 1) × 2.2 / (0.001 × winner_diff + 2.2)`, where `winner_diff` is the rating
  difference (incl. home advantage) from the winner's side, and for a tie `margin` counts as 1 and `winner_diff` as 0
  (so a tie still moves ratings, by `ln 2`).
- **Calibration (tested, never applied automatically):** one calibrator only — Platt scaling, pinned:
  - map `p_cal = σ(a · logit(p) + b)`; `logit` of `p` clipped to [1e−6, 1 − 1e−6]; home-site and neutral games together.
  - fit by Newton's method (`models.logistic.fit_logistic`, ≤ 50 iterations, step tolerance 1e−10), L2 penalty 1e−3 on the
    slope `a` only (intercept unpenalised).
  - training pairs = the chosen raw model's walk-forward predictions (out-of-sample by construction) of non-tie games in
    seasons 2005 … s−1 for scored season s (warm-up 2002–2004 never used); refitted once at the start of each season.
  - fallback: fewer than 50 pairs, a fitting exception, or non-finite / non-positive slope → the raw probability is used
    for that season and the fallback is recorded in the report (no older coefficients are reused).
  - acceptance on validation 2017–2021 (calibrated vs raw, same games): calibrated Brier lower AND calibrated log loss lower
    AND the bootstrap 95 % upper bound of (calibrated − raw) per-game **Brier** below 0. The bootstrap interval is computed
    for Brier only; log loss is a point comparison here. Otherwise raw probabilities are sealed. Not applied to 0.5.
- **Final calibrator (if accepted):** fitted once on all 2005–2021 non-tie walk-forward predictions of the chosen model, with
  the same settings; its coefficients (a, b) are written into the sealed choice before 2022–2025 are opened and are not
  refitted during the test run (no refit at 2023, 2024, 2025).
- **Role of log loss:** for choosing the raw model (Elo grid, the three-way validation choice) it is reported only, never a
  condition; for accepting calibration it is a required point condition, as above.
- **Reported, not used for choosing the raw model:** log loss, accuracy (ties excluded, counted separately), and a reliability table
  (10 equal-width probability bins: count, mean forecast, observed home-win rate) for each candidate on validation.
- **Test access:** seasons 2022–2025 are not read by any evaluation run until the choice (model, parameters, calibration
  decision) is written to a sealed file; the evaluation code refuses those seasons without it, and every access is logged.
- **Expectation, stated before running:** NFL seasons have ~272 games (vs ~1,230 NBA), so validation has ~1,350 games and
  the test ~1,100; intervals will be wide. Bookmaker NFL lines are very efficient; no betting edge is expected or claimed.

## A1 result — development + validation only (2026-11-01, Claude; 2022–2025 not read)
Code `backend/app/nfl_eval.py` (`python -m app.cli nfl evaluate --out DIR`), guard tests `tests/test_nfl_eval.py` (14, synthetic).
Run on a scratch copy of the NFL store (`/tmp/courtside-nfl-eval/nfl.sqlite`), dataset version `f59df479f5769084`. Evidence:
`docs/evidence/nfl-winner-validation-2026-11-01/` (report + sealed choice, sha256 `bb0d871a…`).
- Development (3,198 scored games, 6 ties): Elo K 20, H 50, carry 0.5, multiplier on; Brier 0.2177 (next best 0.2178). carry 0.5
  is the lowest value of the preregistered grid — the optimum may lie lower; the grid is not extended after seeing results.
- Validation 2017–2021 (1,350 scored, 5 ties): Brier constant 0.2500, home rate 0.2491, Elo 0.2226; log loss 0.6931 / 0.6914 /
  0.6362; accuracy 54.1 % / 54.1 % / 64.1 % (reported only). Elo − home rate per-game Brier −0.0265, 95 % [−0.0338, −0.0195]
  (106 weeks) → **Elo chosen**. (Home rate − constant −0.0009 [−0.0049, +0.0030]: would not have beaten 0.5.)
- Calibration: Platt rejected — calibrated Brier 0.22268 vs raw 0.22262, log loss 0.63674 vs 0.63624, interval
  [−0.0006, +0.0007]; raw probabilities sealed, no final calibrator.
- Sealed choice: nfl_elo {k 20, home_advantage 50, carry 0.5, mov true}, cutoff kickoff − 120 min, calibration none.
  **Next: independent review of the sealed choice, then the single logged test run on 2022–2025** (not implemented as a command yet).

## NFL line markets and thresholds — preregistered 2026-11-01, before any NFL line was collected or scored
User: "add the line markets so the app is alike for all sports". Same four line markets as NBA/NHL (spread, total, home team
total, away team total) plus confidence thresholds for every market. Feasibility (schema/count probes only, 24 events): the
free ESPN core odds endpoint (`sources/espn_odds_history`, as used for NBA/NHL) has NFL bookmaker spread/total/moneyline from
**2014** (6–10 books per game; 2011–2013 none, only projection sites); **no team-total lines**; lines carry **no time** —
"close"/"current" is the book's last pre-start line as ESPN keeps it.
- **Line used for grading:** per game the consensus closing line (`espn_odds_history.consensus`, phase "close": the most
  common line among valid, non-outlier books). It is **never a model input** (it is not known at kickoff − 120 min and has
  no time); grading at the close is a stricter benchmark than the line at forecast time, stated as such. Integer-line
  pushes are void for scoring and counted. A game with no valid consensus line is not scored for that market (counted).
- **Team totals:** no quoted NFL team-total lines exist in the source. Graded against the **implied** team line from the
  consensus spread S (home) and total T: home = (T − S) / 2, away = (T + S) / 2, rounded to the nearest 0.5 (a value exactly between, x.25 / x.75, goes to the x.5 line, which cannot push; pinned before any line was collected) — labelled
  "implied line (no quoted NFL team totals)" everywhere it is shown. Not a bookmaker market; never a best pick.
- **Score model (one candidate, reused):** `models.points.PointsNormal` (online offence/defence point ratings, running
  residual variances, `league_rate` 0.01) with NFL priors fixed now: mean points 22.0 per team, sd margin 13.5, sd total
  13.5; NFL margin distribution keeps zero (ties are possible) — margin and total discretised Normals, team totals
  H = (T + M)/2, A = (T − M)/2 with sd √(sd_T² + sd_M²)/2. Grid: eta ∈ {0.01, 0.02, 0.05, 0.1}, carry ∈ {1.0, 0.75, 0.5}.
  Known limitation stated before results: Normals ignore the 3/7 key numbers (a key-number model would be a separate,
  later preregistration).
- **Development (2005–2016, no lines needed):** criterion = mean squared error of the predicted margin + mean squared error
  of the predicted total (both per game, non-tie and tie games alike, regular + postseason). Tie-break (rounded to 4
  decimals): carry 1.0 > 0.75 > 0.5, then smaller eta. Seasons after 2016 refused.
- **Validation (2017–2021, lines collected for these seasons only):** per market, the model's probability of side a (home
  covers / over) at the consensus line, pushes removed, vs the honest baseline 0.5; paired week-block bootstrap of per-game
  Brier (as above). A market has **proven skill** only if model Brier < 0.5's AND the 95 % upper bound < 0. Otherwise it is
  labelled "no proven skill" (as NBA/NHL) and never offered as a best pick; its estimate is still shown. Log loss and a
  10-bin reliability table are reported only. Lines for 2022–2025 are not collected before the sealed test run.
- **Thresholds (every market, incl. winner from the sealed Elo):** picked side = the side with probability ≥ 0.5; bands
  50 %+, 55 %+ … 90 %+ cumulative, on validation picks; per band: settled picks, win rate, Wilson 95 % interval,
  calibration state (`threshold_overview.calibration_state`); break-even 52.4 % (−110) shown for line markets. Best
  threshold rule = `threshold_overview.choose` unchanged (highest Wilson lower bound among bands with ≥ 200 picks; ties →
  lower band); "strong" = Wilson lower bound > 0.5 (`STRONG_LOWER`). Line markets without proven skill never produce a best
  pick, whatever their threshold table shows.
- **Sealing and test:** the score-model config, the per-market skill verdicts and the threshold choices are sealed with the
  winner choice before 2022–2025 lines or results are read; then one logged test run.
- **Amendment 2026-11-01 after the Codex review, before any line was scored:**
  - The line-market evaluation is a **retrospective closing-line test**: probabilities, sides and thresholds are computed
    at the consensus close, which is not known at kickoff − 120 min. It shows probability skill (Brier vs a coin flip) at
    the close, judged afterwards — **not** that those picks were available two hours before kickoff, and **not** a
    profitable edge (no prices, vig or line movement are tested). Reports and the sealed choice say so (`scope`).
  - Consequently **no NFL line market can produce a best pick from this evaluation** (`best_pick_allowed: false` in the
    seal, whatever the verdict): a best pick needs a prospective record graded at the line available when each forecast
    was made. Spread/total keep "no proven skill" in the app until then.
  - Implied team totals are hard-restricted in code: never a best pick and never "strong", whatever the data (regression
    test).
  - Winner thresholds use the probability version being sealed (calibrated if the calibrator was accepted, else raw).

## Line markets + thresholds result — development + validation only (2026-11-01, Claude; 2022–2025 not read)
Lines: 1,355 validation games collected (`nfl collect-lines`, 0 failed), consensus close for 1,352. Evidence
`docs/evidence/nfl-markets-validation-2026-11-01/` (report + sealed choice incl. winner, unchanged: Elo, raw).
- Score model (development): `nfl_points_normal` eta 0.05, carry 0.75 (interior of the grid).
- Retrospective closing-line test, validation 2017–2021 (model − coin, per-game Brier, 95 %): spread +0.0067 [+0.0010, +0.0130]
  (pick rate 51.5 %, 1,325 scored, 27 pushes); total +0.0123 [+0.0077, +0.0170] (48.6 %); implied home team line +0.0137
  [+0.0083, +0.0193] (48.9 %); implied away team line +0.0092 [+0.0043, +0.0139] (49.6 %). **All four are worse than a coin flip
  at the close** → "no proven skill", no best pick (and best picks are blocked for line markets anyway).
- Winner thresholds (sealed Elo, raw probabilities, actual results — not a price): best threshold **75 %+** (204 picks, 80.9 %,
  Wilson [74.9 %, 85.7 %]) → strong; all picks 64.1 % [61.6 %, 66.7 %].

## Reserved test run protocol — preregistered 2026-11-01, before 2022–2025 are read (Claude; runner built, NOT RUN)
After Codex's focused verification ("freeze candidate 75 %; finish scoped independent evaluation review and a guarded
one-shot test runner before requesting/running the reserved test"). Written before any 2022–2025 line, result or score is read.
- **Frozen input:** the line-study sealed choice `docs/evidence/nfl-markets-validation-2026-11-01/nfl_winner_sealed_choice.json`
  (sha256 `d0e51f91…`): Elo K 20, H 50, carry 0.5, multiplier on, raw probabilities (no calibrator), score model eta 0.05
  carry 0.75, winner threshold **75 %+**, all four line markets `best_pick_allowed: false`. The runner refuses a seal whose
  digest does not match, that has no frozen winner threshold, or whose `dataset_version` differs from the 2002–2021 rows of
  the store it is given (the store changed since sealing).
- **One shot:** the runner refuses if the access log already has a test-run entry for this seal, or if the output directory
  exists. The start of the run is logged before any reserved row is read; completion is logged after the report is written.
  A crashed run is not retried silently: it needs the user's explicit decision, recorded here.
- **Reserved lines:** 2022–2025 event ids are listed for line collection only with the valid seal, and that listing is logged
  (`collect-lines --test --sealed FILE`). Collection reads odds only, never results.
- **Winner (no refit):** the frozen models walk 2002–2025 in the shared walk-forward (results feed Elo/home rate only when
  available at each cutoff, as in validation; no parameter, grid or calibrator is fitted). Scored: non-tie games of
  2022–2025 (ties counted). Reported: Brier, log loss, accuracy, 10-bin reliability for Elo, home rate and 0.5; per season
  Brier; paired week-block bootstrap (seed 20260930, 2,000) of Elo − home rate and Elo − 0.5.
  **Confirmed** = Elo's 95 % upper bound vs the home rate below 0 (the validation choice rule). Otherwise "not confirmed".
- **Winner threshold (frozen 75 %+):** test picks, wins, win rate, Wilson 95 %. **Holds** = Wilson lower bound > 50 %
  (the app's "strong"). The 200-pick minimum is a selection rule and does not apply to this check (expected ≈ 150–200 picks).
  The full 50–90 % table is reported only; **no band is promoted, re-chosen or re-labelled from the test.**
- **Line markets:** the frozen score model, graded at the 2022–2025 consensus close exactly as validation (retrospective
  closing-line test). Verdicts are reported per market with per-market intervals, no family-wise correction; they are
  exploratory and cannot unlock a best pick (`best_pick_allowed` stays false). Missing lines → "not run" for line markets;
  the winner part still runs once.
- **Validation-result note (labelling only, written after the validation line results; changes no verdict):** the four
  validation line verdicts were per-market tests without a family-wise rule; all four failed, so none is affected.
- Whatever the test shows is the result: no re-tuning, no second run. Serving NFL needs a separate go.
