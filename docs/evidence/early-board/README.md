# Early board: evidence and limits (9 Nov 2026)

Owner's question: where could an edge come from, for free? Two read-only checks on the stores as of 2026-11-09T23:45Z.

**1. Two hours before the start there is nothing left to find.** Of 762 priced canonical picks (winner, spread, total; NBA, NHL, NFL), the DraftKings line was unchanged between the forecast and the last quote before the start in about 94% of spread and total picks, and it moved toward the pick as often as away (spread 3.5% toward / 2.8% away; total 3.5% / 1.6%; winner probability change 0.00 pts, interval −0.14 to +0.14).

**2. Measured from the first price the store saw (median 31 hours before the start), the market later moved toward the model's side.** `first_line_check.py` re-prices the stored two-hour score distribution at that first line (`PYTHONPATH=backend:scripts python docs/evidence/early-board/first_line_check.py <folder with nba.sqlite, nhl.sqlite copies> 2026-11-09T23:45:00Z`). Picks where the model's probability beat the first price's break-even:

| Market | Picks | Line toward / away | Mean move toward the pick | Paper return at the first price |
|---|---|---|---|---|
| NBA total | 106 | 62% / 26% | +1.36 pts [+0.80, +1.95] | +7.9% [−10.1, +24.3] |
| NBA spread | 87 | 44% / 30% | +0.46 pts [+0.02, +0.90] | −18.5% [−38.2, +1.8] |
| All winner | 125 | — | market probability +2.02 pts [+1.28, +2.76] | −13.2% [−28.1, +2.9] |
| All markets | 470 | — | — | −6.7% [−15.3, +1.8] |

Limits, stated plainly:
- **No profit is shown.** Every return interval includes zero or is negative. The only supported statement is that early lines moved toward the model's side.
- **Look-ahead:** the model numbers are the ones stored two hours before the start. Where a team played between the first quote and that forecast, the model a day earlier would have differed. Restricting to games with no such game in between gives the same direction (totals +0.70 pts [+0.34, +1.07], 131 picks).
- Any model that tracks the eventual consensus shows this pattern against a softer early line; it does not mean the model beats the closing line (it does not: docs/VALUE_AUDIT.md).
- One month, one sportsbook, no record of whether the first price could be bet or at what limits. NHL lines rarely move at all.
- Not tuned: the only rule is "model probability above break-even".

`scripts/early_board.py` is the forward test: it runs the same sealed models a day ahead, writes a board and a write-once paper log here, and `--grade` scores the logs against the closing line. Paper only; nothing is sent and the live app is untouched.
