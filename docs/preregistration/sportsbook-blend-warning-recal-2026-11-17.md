# Fixed rules for the future test (pre-registered 2026-11-17, before any game in the evaluation window)

These rules are written before any result in the evaluation window exists. Don't edit them after results come in. Any change goes in a dated addendum that applies only to games after the addendum. The sha256 of this file is recorded in the report.

## 1. Evaluation set
- Games with tipoff ≥ 2026-11-18T00:00Z. Live ledger only. Telegram and recovered data are never part of the evaluation.
- Unit: one canonical forecast per (game, market), the latest generated before the game's current tipoff, as `coverage._select` picks it. Other forecasts of the same game are ignored.
- Two-way markets: NBA, NHL and NFL winner, spread and total, scored separately per sport × market and pooled within a sport.
- **Quoted set:** picks with a DraftKings no-vig probability q on the same game, market, side and line, retrieved at or before generated_at (scorecard `book_devig`). Every comparison involving q uses only this set.
- Soccer 1X2 is a separate three-way analysis using the same rules, scored per match.
- Pushes, voids and postponements are excluded from scores and counted. Pending games are not evaluated.

## 2. Methods compared on identical picks
Notation: p is the app's raw probability for the shown side (`confidence`, not the displayed %); q is the book's no-vig probability for the same side; y = 1 if the shown side won.
| id | probability for the shown side |
|---|---|
| M0 app (baseline) | p |
| M1 book (baseline) | q |
| M2 fixed blend | p_b = 0.5·p + 0.5·q (Soccer: per outcome, then sum to 1) |
| M3 winner recalibration (winner markets only) | p_home' = σ(a + b·logit p_home), with frozen NBA (a, b) = (−0.2132, 1.1887) fitted on 183 recovered games, and NHL (a, b) = (−0.0686, 1.6864) on 313 games. Fit: `docs/preregistration/sportsbook-blend-warning-recal-2026-11-17-fit.py` (run: `python3 -I <that file> <dir holding snap/{nba,nhl}.sqlite>`), recovered winner rows with tipoff < 2026-11-15. Shown side = p_home' or 1 − p_home'. NFL: not applicable, because there is no recovered NFL data. |
| W one-sided warning (a filter, not a probability) | warned if 100·(p − q) ≥ 8.0, in scope NBA/NHL winner/spread/total except NHL total (tickets.py:563–565). The threshold is frozen at 8.0. |

Worked example (live forecast_id 1, PHI win vs IND, Nov 15): p = 0.776258, q = 0.705473.
- M2: 0.5·0.776258 + 0.5·0.705473 = 0.740865.
- M3: PHI is home, so logit p = ln(0.776258/0.223742) = 1.24399. Then −0.2132 + 1.1887·1.24399 = 1.26552, and σ = 0.7800.
- W: 100·(0.776258 − 0.705473) = 7.08, so it is not warned.
- If PHI wins, y = 1: Brier M0 = 0.0501, M1 = 0.0867, M2 = 0.0672, M3 = 0.0484.

## 3. Metrics (all reported, whatever the result)
- Brier = mean (P − y)²; log loss with P clipped to [1e-6, 1 − 1e-6]. Soccer uses the three-outcome versions: Σ_k (P_k − y_k)² and −ln P_true.
- Hit rate with Wilson 95% CI. For M2/M3 the pick is the side with probability > 0.5, so it can flip.
- Calibration: intercept and slope from logistic regression of y on logit P; ten-bin reliability table with n per bin.
- Flat-stake return: 1 unit at the stored DraftKings price for the side each method picks (scorecard book_price, or the other side's price from market_quotes when the pick flips). Picks with no stored price are excluded and counted. Report it separately from calibration.
- Filter W: report n kept vs removed, in picks and games, with every metric for both groups.

## 4. Dependence and inference
- For each game g, d_g = the mean over its picks of (score_method − score_M0). Inference resamples games: bootstrap with B = 10,000 and seed 20261118, giving a 95% percentile CI. The reported n is games.
- Three primary hypotheses, Holm-adjusted at α = 0.05 overall:
  - **H1 blend:** Brier(M2) − Brier(M0) < 0 on the quoted set (all sports pooled; per sport secondary).
  - **H2 warning:** on warned picks, Brier(M0) is higher than on unwarned picks by ≥ 0.03, AND on warned picks ΔBrier(M0 − M1) > 0. Both must hold. Test statistic: the difference of game-clustered means, with CI.
  - **H3 recalibration:** logloss(M3) − logloss(M0) < 0 on winner games (NBA + NHL).
- Secondary, descriptive only, no success claim: per sport and per market; Soccer; M2 vs M1; the logistic c in logit P(y) = a + b·logit q + c·(logit p − logit q).

## 5. When to look
- Single looks only, each when its sample size is reached. H1: ≥ 150 quoted games. H2: ≥ 40 warned picks over ≥ 30 games. H3: ≥ 150 winner games.
- Stop at the earlier of twice those sizes or 2027-01-31. Results are reported at the stop point even if the minimum isn't reached, labelled "underpowered".
- No retuning: the 0.5 weight, 8.0 threshold and (a, b) stay as written. A new idea starts its own pre-registration on later games.

## 6. Success criteria (fixed)
- **H1 succeeds** if the Holm-adjusted test rejects AND M2 is not worse than M1 by more than 0.002 Brier. Then the blend may be proposed as a model change, as a separate decision for the owner.
- **H2 succeeds** if both parts hold. Then keep the warning, and consider showing q instead of p on warned picks. **H2 fails** if the warned-minus-unwarned CI lies entirely below +0.03. Then the warning is reviewed.
- **H3 succeeds** if the Holm-adjusted test rejects AND the calibration slope of M3 is closer to 1 than M0's.
- Otherwise: "not shown". This never counts as "shown false" unless the CI excludes the success region.

## 7. Additional method (Claude): market-anchored residual
P* = σ(logit q + c·(logit p − logit q)). c = 0 is the book, c = 1 is the app, and c in between is a logit-space blend. c is estimated online, using only games before each forecast. Justification to test: c must be > 0 with a game-clustered CI excluding 0 on ≥ 150 quoted games (today c = −1.72 [−4.39, +0.39] explore, −0.27 [−2.56, +2.37] live). Until then, M2 is the fixed stand-in.

## Addendum A (2026-11-17, before the evaluation window opens; based on the number/quote audit, not on any result)
- q is the app's stored PROPORTIONAL no-vig (odds.devig_two_way; Soccer: per-side latest offered rows, soccer_book.one_x_two). Power and Shin versions of q are reported as sensitivity only, never as the primary result.
- "DraftKings" includes the provider spelling "Draft Kings" (it appears from 2026-11-16T20:41Z).
- Soccer q is taken from the per-side rule, not the scorecard `book_devig`. That field uses the latest all-sides retrieval and is stale on 10 of 53 rows, by up to 1.8 points.
- Lineup and injury factors are evaluated only on forecasts made after the NBA player-minutes file was restored (generated_at ≥ 2026-11-17T03:00Z). Earlier NBA rows are "no_player_minutes" by data loss.

## Addendum B (2026-11-17, before any game in the window; data-quality fixes, no result seen)
- Soccer scorecard rows written from now on use soccer_book.latest_1x2, the card's rule, so Addendum A's per-side q and the scorecard agree. Historical rows are unchanged (append-only). For research, `scripts/soccer_scorecard_corrected.py` exports corrected values beside the originals.
- Effect on the sample: on the Nov 17 snapshot, 53 of 103 Soccer rows are quoted under both rules (no forecast enters or leaves the sample). 10 rows change q, by −0.58 to +1.81 points.
- 'Draft Kings' and 'DraftKings' are one book in every lookup (odds.provider_key). Effect on the two-way quoted set: none, because winner, spread and total q already ignored the provider name or used the forecast's own quote. Opening-price and move columns, and implied team totals, now also find quotes saved under the new spelling.
- Lineup guard: a player file rewritten with unchanged bytes no longer marks a forecast "unknown". No saved row was affected (0 rows with player_file_after_forecast).
