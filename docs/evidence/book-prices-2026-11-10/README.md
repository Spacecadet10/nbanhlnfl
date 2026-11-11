# Three-sportsbook prices on tickets (2026-11-10)

Status: **built and tested, not live, not independently reviewed.** Codex began this task and ran out of usage
(until Nov 15) with its edits uncommitted in its own worktree, which this session cannot read. Claude rebuilt it from
the module and tests visible in Codex's log, with the design change below.

## What it does
- `backend/app/rundown_odds.py`: `fetch` saves TheRundown responses (DraftKings, FanDuel, BetMGM; NBA, NHL, NFL; today
  and tomorrow) under `data/odds-books/`. Reading never fetches. Key from `.env.odds` (chmod 600), never logged.
- `tickets.snapshot()`: each winner/spread/total pick gets `book_comparison` (prices on the forecast's exact line only);
  `assessment` gets `book_prices`, `best_book`, `best_book_price`, `best_book_gap`; ticket assessment gets
  `book_payouts` (only books pricing every leg) and `best_book`. Top-level `book_prices` reports the cache state.
- Ticket picture: one grey line per leg with each book's price; "Pays most at <book>: about ×N" per ticket.
- `scripts/launchd/com.nba-predictor.rundown-odds.plist`: six fetches a day (36 requests; cap 40 a UTC day).

## Difference from Codex's draft
Codex's `attach` replaced `quote_check.provider/price_now` with the best book. Here `quote_check` is never changed:
the stored DraftKings quote stays the basis of `price`, payout, the moved-line refusal and grading. A parlay is placed at
one book, so the ticket names the single book that pays most for all legs instead of mixing books.

## Checks actually run (Linux VM copies of delivery-release b8b5f44 and, after the soccer release, 223f174; Python 3.10)
- `pytest tests/test_rundown_odds.py tests/test_tickets.py tests/test_telegram.py tests/test_delivery_release.py tests/test_release_guard.py` → 232 passed.
- `scripts/check_project.py` → structure guard passed.
- Real saved sample (`../early-board/therundown-sample.json`): NO +11.5 → DraftKings -113 (FanDuel's -12.5 line kept out);
  Under 228.5 → BetMGM -105, DraftKings -107; PHX win → DraftKings +312, FanDuel +310; started game → no comparison.
  Picture: `ticket-preview-real-sample.png`.
- `tickets.snapshot()` on a copy of the real NBA/NHL stores with a cache file present: ran, no error (no picks that day).

## Not verified
- No live request was made from this session (therundown.io is unreachable from it). The quota header name
  (`X-Datapoints-Remaining`) is unconfirmed; the request caps hold without it.
- Not run on the Mac's Python/venv, launchd not loaded, no ticket sent with it.
- The review skill (`~/.codex/skills/courtside-ticket-review/SKILL.md`) is not updated: the reviewer sees the new
  fields in the snapshot but has no instruction about them yet.
- TheRundown free data is delayed about five minutes and prices can be up to three hours old; every use shows the time.

## Activate / roll back
`sh ~/Desktop/nba-predictor-sprint1/docs/evidence/book-prices-2026-11-10/activate.sh` (tests in a temporary checkout,
then one fast-forward of `delivery-release`, installs the schedule, first fetch). Rollback lines are at the top of it.
