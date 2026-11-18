# Fixed rules: prospective NFL line-market evaluation and weekly NFL challengers (pre-registered 2026-11-18, DRAFT)

Status: **DRAFT, not binding.** Binding from the commit that first adds this file to `docs/preregistration/`. Paper only:
nothing here changes NFL serving, the frozen winner threshold, a card or a record. NFL serving code is hashed
(`nfl_live.definition()` = nfl_live.py, nfl_eval.py, coverage.py, markets.py, models/base.py, models/elo.py,
models/points.py); nothing in this plan may edit those files, because any edit deactivates NFL live forecasts.

Binding sha256: `PENDING-AT-COMMIT` (this line stays exactly as written; the sha256 of the committed bytes goes in the commit
message, HANDOFF.md and the evaluator's `PREREG_SHA256` constant).

## 0. Limits, stated first
- **Expected result this season: insufficient evidence.** About 120 NFL games remain from T0 through the Super Bowl
  (weeks 12–18 + 13 playoff games, fewer if quotes are missing). The minimum sample for any claim (section 6) is 600 decided
  bookmaker-line games per market, i.e. about two more full seasons. **Nothing is claimed in either direction below it**;
  reports below it carry the label "insufficient evidence (n of 600)" and show only counts and coverage (section 6).
- The retrospective closing-line test found no proven skill (`nfl_eval` verdict `proven=false`); line markets are shown as
  informational. A positive prospective result is not expected.
- Team totals: **no bookmaker team-total quote is stored anywhere** (ESPN scoreboard lines carry none), so every NFL
  team-total forecast uses the model's own line (`line_source='model'`). They cannot be compared with a book; they are
  evaluated against outcomes only (section 4.3) and no market claim is possible.
- Power (ROUGH, assumed spreads): per-game log-loss difference (app − book) SD ≈ 0.10. One-sided 5%/80%: n ≈ 620 detects
  ΔLL = −0.010, n ≈ 2,500 detects −0.005. Hit rate on the app's side against 50%: n ≈ 620 detects 55%, n ≈ 1,700 detects 53%.

## 1. Units, time, information
- **T0** = committer timestamp of the commit that first adds this file. Counted games: `games.season_type` in (regular,
  postseason), `tipoff_at > T0` + 120 min (so the cutoff is after T0).
- **Incumbent forecast** = the canonical `game_forecasts` row per (game, market) as `coverage.canonical(conn, games, market,
  as_of, began=<activation.activated_at>)` selects it (the cohort `nfl_live.Views.cohort` shows), `order_version='nfl-live-v1'`.
- **Decision time** = cutoff = kickoff − 120 min (`nfl_eval.cutoff_for`). `nfl_live.record` uses results available by the
  cutoff and the quote query `market_quotes WHERE sport='nfl' AND game_id=? AND market=? AND retrieved_at <= cutoff AND
  retrieved_at >= cutoff − 2 h ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1`. That quote is the **T-120 quote**; its
  `quote_id` is stored as `market_quote_id`, and `line_source='bookmaker'`. No quote in that window → `line_source='model'`
  (model line), excluded from book comparisons, counted.
- Note from the code: that query filters neither `price_label` nor provider. Within one fetch the ESPN parser writes the
  `open` row before the `current` row (`sources/espn.py` parse_lines), so `quote_id DESC` takes `current` when both exist. The
  evaluator reports the count of T-120 quotes whose `price_label='open'` and their providers; they stay in the sample
  (the forecast used them) and a sensitivity drops them.
- A forecast with `generated_at >= tipoff_at` is never canonical; generation after the cutoff is allowed (inputs still stop
  at the cutoff) and its lag is reported.

## 2. Probabilities compared (spread, total; winner secondary)
- App: p = p_a / (p_a + p_b) of the incumbent row (two-way, push share removed, as a two-way de-vig removes it).
- Book: q = `p_a_devig` of the stored `market_quote_id` (proportional de-vig, `odds.devig_two_way`, stored rounded 6 dp).
  Same game, market, line and side by construction.
- y = 1 if the realised side is a. Grade: `markets.settle(market, line, status, home, away, tipoff_at=<games.tipoff_at>,
  forecast_tipoff_at=<forecast tipoff_at>)`; `mid` (push), `void`, `unscorable` excluded and counted; a game still not final
  7 days after kickoff is excluded and counted. A `games` row counts as final only if `result_available_at` ≤ the evaluation
  time (as `Views` does).
- Winner (secondary): p = p_a; q = the moneyline row `odds.winner_quote(conn, 'nfl', game_id, cutoff)` returns, only if
  retrieved ≥ cutoff − 2 h.

## 3. Weekly challengers (owner decision 2026-11-18 Q3: challengers now)
- **Registration.** A challenger is a spec listed in a versioned policy file
  `reference/learning_policies/nfl-challengers-<id>.json` (spec, provenance, file sha256) committed **before** its first
  counted game. It counts only on games whose cutoff is after that commit. No backdating; adding a challenger never changes
  serving and never touches hashed files.
- **Registered set (lead resolution of Q5): exactly the six existing grid variants, no new families.** File
  `backend/app/reference/learning_policies/nfl-challengers-v1.json`, sha256
  `9503bca10a1120b3fd687f62105fd84467ea7ebf33269c79f3ebf5e15841cede` (computed 2026-11-18 on branch `learning-phase2`,
  commit `dc3550d`, committer time 2026-11-18T12:58:01Z; not yet on `main`). The evaluator refuses any other bytes.
  | name | head (markets scored) | change from sealed |
  |---|---|---|
  | nfl_elo_k10 | winner | Elo k = 10 (sealed 20) |
  | nfl_elo_k30 | winner | Elo k = 30 |
  | nfl_elo_ha25 | winner | Elo home advantage = 25 (sealed 50) |
  | nfl_elo_ha75 | winner | Elo home advantage = 75 |
  | nfl_points_eta02 | spread, total (team totals outcome-only, descriptive) | points eta = 0.02 (sealed 0.05) |
  | nfl_points_eta10 | spread, total (team totals outcome-only, descriptive) | points eta = 0.10 |
  A challenger is scored only on the markets of its own head. **Challenger counting start** = the later of T0 and the first
  `generated_at` at which the live recorder writes rows under this policy sha (it must be on the deployed branch);
  games whose cutoff precedes it are not counted, never backfilled.
- **Refit cadence.** Each challenger refits weekly at Tuesday 15:00Z from results with `result_available_at` ≤ the refit time
  (and is otherwise updated online exactly as its spec says); that fit is used for every game whose cutoff is after the refit
  and before the next one.
- **Recording.** At the incumbent's cutoff (same T-120 information rule), into a separate append-only research store (not
  `game_forecasts`, not the hashed registry), one row per (challenger, game, market) at **the incumbent forecast's line**
  (same `market_quote_id` or the same model line), with `generated_at`, inputs-through time and the challenger's fit id.
  A row with `generated_at` ≥ kickoff, or missing, is counted and never imputed.
- **Comparison.** Paired, same games, same lines: Δ_c = mean over games of [LL(challenger) − LL(incumbent)] per market, and
  the same against the book where the line is a bookmaker line.

## 4. Metrics (all reported, whatever the result)
### 4.1 Spread and total, bookmaker-line set
- Log loss (p clipped to [1e-6, 1 − 1e-6]) and Brier of app and book; per-game ΔLL = LL(app) − LL(book).
- Hit rate of the app's side (p ≥ 0.5 → a), Wilson 95%; mean q of that side; excess = y_side − q_side.
- Calibration intercept/slope (logistic of y on logit p); reliability table, 5 bins, n per bin.
- **CLV (secondary):** close = latest row of the same provider (`odds.same_provider`), same market, `price_label='current'`,
  `retrieved_at < tipoff_at`; only when its line equals the forecast line: CLV = q_close(side) − q(side) for the app's side;
  line moved: points CLV (spread side a: L_f − L_c; side b: L_c − L_f; over: L_c − L_f; under: L_f − L_c). No close or a
  close older than tipoff − 60 min: excluded and counted. No probability conversion across lines for NFL (no frozen
  conversion constant is preregistered here).
### 4.2 Winner (secondary): the same metrics against the T-120 moneyline.
### 4.3 Team totals (outcome-only): Brier/log loss of the model's two-way p at its own line; hit rate of the model's side
  (≈ 50% by construction of a fair model line); calibration. No book metric.
### 4.4 Challengers: Δ_c per market, with the same paired figures vs the book on the bookmaker-line set.

## 5. Inference
- Unit = game. Per-game paired differences; bootstrap over NFL weeks (cluster = season + week), B = 10,000,
  `default_rng(20261118)`, 95% percentile CI, one-sided p = (1 + #{θ*_b ≥ 0}) / (B + 1) for "app/challenger better" (Δ < 0).
- **Primary family (α = 0.05 over 2, Bonferroni):** H1 spread ΔLL(app − book) < 0; H2 total ΔLL(app − book) < 0. Each is
  tested only when its market reaches n = 600 decided bookmaker-line games (section 6). The two markets reach 600 at
  different looks, so Holm (which needs both p-values at once) is replaced by its first step: each is rejected only if
  p ≤ 0.025.
- **Challenger family (α = 0.05 over 8, Bonferroni, p ≤ 0.00625 each):** the 8 hypotheses of `nfl-challengers-v1` (4 Elo
  variants × winner; 2 points variants × {spread, total}): H_c: Δ_c(challenger − incumbent) < 0, each at its own single look
  at 600 paired decided games. Every registered hypothesis is counted, including those dropped or failing. A later policy
  file forms its own family, declared by addendum before its first counted game.
- Everything else descriptive.

## 6. Sample size, horizon, stopping
- **Minimum n for any claim: 600 decided bookmaker-line games per market** (≈ 620 for the ΔLL MDE of 0.010; also ≈ the n at
  which a 55% hit rate is detectable). Single look per market at the first moment the market reaches 600, after every
  earlier game is final or past 7 days. Challengers: single look each at 600 paired games.
- Deadline: if a market has not reached 600 by 2029-02-28, it is reported once as "insufficient evidence" and closed.
- **Below n = 600 per market (lead resolution of Q4): only counts and coverage may be published**: n per market (forecasts,
  decided bookmaker-line games, model-line forecasts), push / void / unscorable / past-7-day counts, T-120 quote coverage,
  open-label T-120 counts, close coverage, challenger row counts and missing-row counts. **No skill metric value** of any
  kind for that market (no log loss, Brier, ΔLL, hit rate, excess, calibration, CLV or points CLV, for the app, the book or
  any challenger) until that market reaches 600; challengers likewise until 600 paired games. The app's existing pages
  may keep showing their own grades as today; nobody computes or publishes a section 4 quantity on counted games early.
- **No extension, no early stop**: n = 600 and 2029-02-28 are fixed.

## 7. Exclusions (counted)
Model-line forecasts (from book comparisons only), pushes, voids, unscorable, past 7 days, start changed after the forecast,
open-label T-120 quotes (sensitivity only), preseason, games with no canonical forecast.

## 8. What would and would not be claimed
- Below n = 600 per market: **nothing**, in either direction, and no metric value (section 6).
- At the look: "app beats the T-120 book on market X" only if rejected at p ≤ 0.025 (section 5); "book beats app" only if the 95% CI of ΔLL lies
  entirely above 0; else "not shown". A challenger "beats the incumbent" on a market only if rejected in its family (p ≤ 0.00625); that permits a
  promotion proposal, a separate owner decision under the existing promotion rules, never automatic.
- Never claimed: profit; team-total market skill; anything about the frozen winner threshold (it has its own record).

## 9. Integrity and deviations
- Evaluator and challenger recorder: synthetic-data tests, then a **fresh, independent Opus review (read-only)** of the code
  against this frozen document, recorded in HANDOFF.md with reviewer, model, UTC time, reviewed commit and file sha256 and
  `PREREG_SHA256`, before their first counted row (recorder) or first outcome read (evaluator); any later code change needs
  a new review entry first; read-only on copies (`NBA_DATA_DIR`); recorder version hashed into every row; rows by a
  later-found defective version excluded whole, decided before reading their results.
- No change to markets, metrics, n, alpha, seeds or deadline after T0. Deviations: dated addendum, never after consulting any
  counted outcome, applying to later games only. A change of `nfl_live.definition()` (new serving release) does not stop the
  evaluation; forecasts are stratified by definition descriptively.

## 10. Previously inspected data (disclosed before T0)
- A weekly report viewed before this document was frozen showed NFL outcome counts: 60 decided picks through 2026-11-18.
  The retrospective closing-line test (`nfl_eval`, `proven=false`) and the grid search that chose the sealed Elo/points
  parameters read pre-T0 outcomes; the six challenger values come from those existing grids (disclosed, not re-chosen).
- None of these enters the analysis: a game counts only if its cutoff (kickoff − 120 min) is after T0 (section 1), and a
  challenger game only after the challenger counting start (section 3). No pre-T0 game is pooled, used as a baseline or
  used to set a threshold, and no rule here may be justified by those figures.

## 11. Specification checklist (where each item is fixed)
T0 rule: section 1. Eligible forecasts: section 1 (canonical incumbent; regular + postseason; cutoff after T0) and section 3
(challengers). Comparisons: sections 2–3. Metrics: section 4. Sample and stopping: section 6. Missing data (no T-120 quote →
model line, excluded from book comparisons; no canonical forecast; missing challenger row; no close; not final after 7
days): sections 1, 3, 4.1, 7, all counted, none imputed. Pushes (`mid`), voids and unscorable: section 2, excluded and
counted.
