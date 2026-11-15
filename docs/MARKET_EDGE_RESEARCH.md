# Winning books and bettors: what the evidence says, and what it means here

Research note, 2026-10-29. No code, model, or data was changed. "Winning books" is read two ways and both
are covered: (a) the sportsbooks — how they make money and why they win; (b) the playbooks of the small
minority of bettors who win. The local numbers below are computed read-only from the live stores and API
(`:8100`); the external sources are cited at the end. External claims are reported as found, not endorsed.

---

## 1. First, the local evidence: our model vs the market

Method: canonical winner forecasts (`game_forecasts`, `market='winner'`, `status='forecast'`, one forecast per
game, latest `generated_at`), joined to final regular-season/play-in/playoff games, each matched to the latest
`market_quotes` row for the same game retrieved at or before the forecast, de-vigged (`p_a_devig`). Paired
game-level Brier difference, 20,000 bootstrap resamples, seed 7.

| | NBA | NHL |
|---|---|---|
| Settled games | 63 | 62 |
| Model accuracy | 37–26 (58.7%) | 64.5% |
| Market-favourite accuracy (same games) | 43/63 (68.3%) | 61.3% |
| Model Brier | 0.2243 | 0.2278 |
| Market Brier | 0.1930 | 0.2313 |
| Paired Brier diff (model − market) | **+0.0313** | −0.0035 |
| 95% bootstrap CI | **+0.0077 to +0.0549** | −0.0149 to +0.0082 |
| Games where the model beat the market | 21 of 63 | 33 of 62 |

Reading: **the NBA model is significantly worse than the market**, not just noisy. The interval excludes
zero; the market is better on two thirds of the games. On the NHL sample the two are indistinguishable, not
"beating the market". Wilson 95% intervals: model 46.4–70.0%; 70%+ band 18–5 on 23 picks (58.1–90.3%);
NHL 54.3–69.6%.

The canonical model is also overconfident below 65%: in the 50–60% bucket the model's ~55% picks won 37.5%
(n=16); in the 60–70% bucket its ~66% picks won 50% (n=16). The old 70% filter was cutting on a number that
did not mean what it looked like. The 70%+ band (18–5) is too small to justify anything: it is compatible with
58% true accuracy.

Caveats: one book only (DraftKings via ESPN), the comparison quote is not a closing line, small samples, no
player props, and the market Brier comes from the quote the forecast used. These limits matter for any claim,
but the NBA result is not marginal.

## 2. Why the books win

- **They sell a margin, not a forecast.** At −110/−110 the implied probabilities sum to ~104.8%, so a bettor
  must win 52.38% just to break even. The book's edge is structural even if its model were no better than the
  market. Our model's 58.7% on 63 games (CI 46–70%) cannot demonstrate clearing that bar.
- **They are better at pricing than their customers.** Levitt's NFL study (NBER w9422 / Economic Journal 2004)
  finds bookmakers announce prices and take positions, are more skilled at predicting outcomes than bettors,
  and systematically exploit bettor biases; he finds **little evidence of bettors who systematically beat the
  bookmaker**, even with the distorted prices books set.
- **They manage who is allowed to win.** Reported Massachusetts Gaming Commission testimony: winning gamblers
  face betting limits while losing customers are cultivated as VIPs
  ([Boston Herald, 2025-09-30](https://www.bostonherald.com/2025/09/30/winning-gamblers-face-betting-limits-losers-made-vips-gaming-commission-learns/)).
  A profitable strategy that gets limited to trivial stakes has no capacity.
- **Consumer outcome evidence is bleak.** Survey reporting: more than half of Gen Z investors who bet on sports
  are "not coming close to breaking even"
  ([Yahoo Finance](https://ca.finance.yahoo.com/news/more-half-gen-z-investors-065800705.html)).

Consequence for a model: accuracy is not the product. **Beating the price after the margin** is the product.

## 3. What winning bettors actually do

Industry practice and the thin academic record converge on a few things; none of them is "predict the winner":

1. **Grade against the close (CLV).** Sharp-line trading documentation describes sharp books as the reference
   the market prices against, and measures a bet by the fill price versus the closing price ("grade yourself
   against the close"), not by the win/loss of one game
   ([OddsPapi guide](https://docs.oddspapi.io/guides/sharp-line-trading)). A model that cannot beat the close
   is not a betting model, whatever its hit rate.
2. **Shop prices and take size into account.** Two books at −105 vs −110, or the same side at different
   numbers, is free value; limits and delay determine whether the edge is executable. We currently have one
   book (ESPN/DraftKings) and no limit data, so we cannot shop and cannot model capacity.
3. **Play where the book is weakest, not where prediction is easiest.** Low-liquidity and alternate markets
   (props, periods, live) get less modelling attention and more stale prices — but they also carry lower
   limits, higher variance and faster line movement. Predictability is not the same as mispricing: the
   reliability study the prop playbook relied on says its own measure "is not profitability"
   ([StatPair](https://statpair.com/blog/most-predictable-football-stats-data-study)).
4. **Exploit information and timing**, not just statistics: confirmed lineups, late scratches, role changes,
   in-play latency. That is a data-pipeline and speed edge, not a better regression.
5. **Survive.** Bankroll sizing, record-keeping, and staying unbanned. Capacity (limits) is the binding
   constraint on every real edge.

What the evidence does **not** support: that a general "pick the winner" model with free public data beats a
major sportsbook on NBA sides/totals. Levitt found little systematic winning; our own paired test agrees for
this app's data.

## 4. What this means for Courtside

The current app is mathematically honest and clearly labelled, but it is positioned as a predictor. The market
benchmark says it is not one. There are three defensible paths, in order of cost:

**A. Reposition as a market-comparison and record tool (days).** Make the market the reference on the main
screens: show the model estimate next to the de-vigged market probability, and label it "model estimate (not a
prediction)". Keep the timestamped record, settlement, and paper-trading records — those are real assets and
are what a user can inspect. Remove or downgrade "confidence threshold" framing that implies edge. This resolves
the trust problem immediately and costs no modelling.

**B. Calibration / market-first experiment (weeks, bounded).** Recalibrate the probabilities out-of-sample
(isotonic or Platt on prior seasons), add the de-vigged market probability as a feature (model the residual,
not the stat), and evaluate on proper scores and CLV. Pre-register the gate: the model must beat the market's
Brier and show positive CLV on held-out games, with a CI. If it cannot, the experiment's answer is "no" and the
model is not promoted. Expect the NBA result to stay negative; the NHL tie is the only live question, and 62
games is not enough.

**C. Niche edge hunt (months, uncertain).** Player props, live markets, or a league where our data/timing is
better than the book's. This is the only path with a plausible *betting* edge, and it requires forward line
history (we have ~3 weeks of unparsed ESPN prop snapshots), multi-book prices, and the pre-registered gates in
`docs/PROPS_PLAYBOOK.md`. It is research, not a feature.

Recommended sequence: **A now; B as a bounded experiment with a kill rule; C only if B shows a signal or the
user wants to fund the data.** Do not tune thresholds on 63 games or add models without the market benchmark —
that is how a research app turns into a confident losing one.

## 5. The lesson in one paragraph

The book wins on margin, pricing skill and capacity limits; the winning bettor wins on price, niche, information
timing and survival — both are structural, not "accuracy". Our NBA winner model is significantly worse than the
market and the NHL one is tied, so the app's honest claim is "a record you can inspect, benchmarked against the
market", not "accurate picks". With one book and no closing-line feed we cannot even measure a real betting
edge yet; the prop work (forward line capture, market-first models, CLV gates) is the way to find out.

## 6. Sources

- Steven D. Levitt, *How Do Markets Function? An Empirical Analysis of Gambling on the National Football
  League*, NBER Working Paper 9422 (2003); published as *Why Are Gambling Markets Organised So Differently From
  Financial Markets?*, Economic Journal 114 (2004). https://www.nber.org/papers/w9422
- Boston Herald, *Winning gamblers face betting limits, losers made VIPs, gaming commission learns*,
  2025-09-30. https://www.bostonherald.com/2025/09/30/winning-gamblers-face-betting-limits-losers-made-vips-gaming-commission-learns/
- Yahoo Finance, *More than half of Gen Z investors have funneled cash into sports betting—but they're not
  coming close to breaking even*. https://ca.finance.yahoo.com/news/more-half-gen-z-investors-065800705.html
- OddsPapi, *Sharp-line trading & CLV: limits, delay and closing line value*.
  https://docs.oddspapi.io/guides/sharp-line-trading
- StatPair, *Which football stats are actually predictable?* (predictability ≠ profitability).
  https://statpair.com/blog/most-predictable-football-stats-data-study
- Local: `data/nba.sqlite`, `data/nhl.sqlite` (read-only), `GET /api/canonical/performance` on the dev API;
  method and numbers in §1. Related: [MARKETS.md](MARKETS.md), [PROPS_PLAYBOOK.md](PROPS_PLAYBOOK.md),
  [EVALUATION_PLAN.md](EVALUATION_PLAN.md).
