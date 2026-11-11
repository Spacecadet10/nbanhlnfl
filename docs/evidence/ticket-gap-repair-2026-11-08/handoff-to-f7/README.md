# Handoff to f7: line/price check + interrupted reviews + evidence-based selection text

The owner forwarded this request on Nov 8 at about 18:50 ET: remove the variety/size requirements, improve the evidence-based comparison, check changed lines/prices, and make interrupted reviews resume or record a clear failure before kickoff.

This change is NOT landed in sprint1 and `~/.codex` is untouched. f7 applies it in the release switch.

## Code

| | |
|---|---|
| Base | sprint1 `backend/app/tickets.py`, sha256 prefix `d93bf926a04eea7a` |
| Proposed | `tickets.py.proposed`, sha256 prefix `14f0a77d4427f1f8` |
| Diff | `tickets.py.diff` (applies cleanly to the base; the patched result matches `14f0a77d4427f1f8`) |
| Tests | `test_tickets.py.diff` / `test_tickets.py.proposed` |

What the code change does:

1. **`quote_check(conn, pick, stamp)`**
   - Added to every pick in `snapshot()`.
   - Compares the latest `market_quotes` 'current' row with `retrieved_at <= as_of` against the forecast's line. It prefers the provider of the forecast's own `market_quote_id`.
   - Fields: `provider`, `retrieved_at`, `line_source`, `line_at_forecast`, `line_now`, `price_now`, `price_at_forecast` (for the picked side), `line_moved`.
   - A lookup error is stored on the pick and never hides the forecast.
2. **Moved lines**
   - `review_state` excludes picks with `line_moved` from ticketable and lists them under `line_changed`. The key is added only when non-empty.
   - `check()` refuses such a leg with "Line changed since forecast (...); review again". The "review again" wording makes it retryable.
3. **Ticket text**
   - Each leg shows `/ DK -700 (was -650)`.
   - Footer: "Prices: latest stored DraftKings quotes, HH:MM; confirm before betting." When no leg has a quote, the old "Prices unverified" line stays.
   - Model-line legs get "...: the app's own line; no sportsbook line stored."
4. **Interrupted reviews**
   - New append-only table `ticket_review_starts`, created in `schema()`.
   - The `snapshot` action records a start and prints `review_start_recorded`.
   - `preflight` calls `record_interrupted(snap)`. A start older than `SNAPSHOT_TTL` (10 min, the same limit `check()` enforces) with no `ticket_reviews` row and no archive for that snapshot gets one retryable `operational_failure` row ("review interrupted: ..."). It is listed in `review.interrupted`.
   - With `--notify`, it is alerted once per snapshot (`interruption_alerts`, dedup suffix). Existing alert keys are unchanged.
5. **`record_review`** accepts `snapshot_sha256=` and `day=` for outcomes without a plan.

## Skill and automation text

- `SKILL.proposed.md` and `automation.proposed.toml`, with `SKILL.diff` and `automation.diff` against the current copies (`SKILL.current.md`, `automation.current.toml`).
- Removed: the Nov 8 "polyvalent / varied menu / safer pair, mixed markets, bigger 4-6 leg" wording, and the automation's "varied, intuitive menu of sizes and bet types".
- Added:
  - evidence-based comparison: app probability, recommendation, relevant record with sample size, and evidence quality;
  - not the highest percentage alone; sportsbook agreement is context, not proof;
  - ticket size follows the strength of the evidence; original tickets stay immutable;
  - the `quote_check`/`line_changed` rules;
  - "finish a started review through check/send before answering any other message in this thread", plus resuming `review.interrupted`.
- The automation TOML validates with tomllib; rrule `FREQ=MINUTELY;INTERVAL=15` and status ACTIVE are unchanged.

## Checks

These are copies of the files saved in this folder.

- **25-failing-first-quotes-interrupt.txt:** the 4 new tests fail on the base.
- **27-copy-tests-quotes.txt:** `pytest test_tickets test_telegram test_nfl_live test_freshness test_near_tipoff` on the copy with an isolated `NBA_DATA_DIR`: 148 passed / 1 failed. The failure is the pre-existing `test_fallback_explanation_only_does_not_create_new_forecast_identity` (coverage.py; NFL-hashed, not touched).
- **26-real-readonly-quotes.txt:** a snapshot of the live stores (read-only) with a copied ledger.
  - Prices are filled for winner, spread and total in NBA, NHL and NFL.
  - Team totals use model lines and have no stored quote; reg3 has none either.
  - No moved lines at 23:57Z; due=True with 38 forecasts across 7 games.
- **Mocked end-to-end run** (scratch e2e4, fresh store backups, httpx.MockTransport, fake token):
  - snapshot recorded a start;
  - a synthetic 2-leg plan was checked and sent;
  - the text showed "DK -700" and "DK -790" and the price footer;
  - ticket_sent was recorded.

Not run: the full suite, `--ready`, the browser. Not independently reviewed.

## Notes

- `schema()` creates `ticket_review_starts` in the live ledger on first use, the same way `ticket_reviews` was created.
- Not changed, waiting for the owner:
  - the 6-leg cap (`MAX_LEGS`);
  - the policy for whether a newer forecast version of an already-ticketed game may appear in another ticket. Today it is refused: only the identical forecast_id may recur.
