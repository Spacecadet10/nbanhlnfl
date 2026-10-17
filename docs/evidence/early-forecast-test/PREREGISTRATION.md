# Early-forecast test (NHL winner): pre-registration

Status: **DRAFT, not frozen.** It is frozen at installation, before the first early forecast exists, by recording the
commit and the sha256 of this file and of the code below (section 9). After that, nothing in sections 1–8 changes; a
change means a new, separately registered test.

## 1. Question

Do NHL winner forecasts made **before the bookmaker line settles** gain enough closing-line value to beat the margin?
The two-hour forecasts cannot: the line has mostly settled by then (paper trading of `single:hockey_logistic_xg`, CLV
about +0.07 pt over 25 bets). This is a betting test, not a test of forecast accuracy, and it uses no past data.

## 2. Prior evidence (why the expectation is a fail)

- `hockey_logistic_xg` picks at an edge of 6+ pt gained about 1 pt of CLV from the opening line in each of the last three
  seasons: 2025–26 +1.08 pt [+0.48, +1.60] (275 bets), 2024–25 +1.32 pt, 2023–24 +1.17 pt (HANDOFF.md, holdout A).
  About 2.5 pt is needed to beat the margin. The 'open' arm below is therefore expected to fail; it is the control.
- No NHL model reacting to injury news has been tested against the line. The 'news' arm is the only one with upside.
- The 2025–26 bookmaker holdout is spent; only future games can test this.

## 3. Arms (separate; never pooled, never chosen between after the fact)

| Arm | Member | When | Once per |
|---|---|---|---|
| `open` | `hockey_logistic_xg`, computed exactly as the research cycle computes it (`agreement.predict`: walk over all results and shots available at the forecast time, season-start refits) | the first scheduled run (every 30 min) that finds a bookmaker moneyline for the game; lines are fetched up to 36 h before the start | game |
| `news` | `nhl_players_injuries@adaptive` (the only NHL member that reads injury reports), served from its weekly-cadence adaptive fit in force, as the adaptive cycle serves it (`adaptive.record`, read-only evaluation, and only when the fit it applied is the fit in force at that time) | a run (every 30 min; each run first takes its own capture) finds a live injury capture taken since the previous run (at least the last 45 min, never more than 2 h back) — by this job or by the two-hour cycle — that differs from the capture before it for a team (players, statuses or weights); for every quoted game of that team starting within 36 h | game and change (a change is never forecast twice) |

Source labels in paper trading: `early:open:hockey_logistic_xg`, `early:news:nhl_players_injuries@adaptive`.
A forecast the member cannot make is stored as `unavailable` with the reason (coverage is reported, section 6).
At installation, games whose line is already up are forecast at the first run (later than their first quote); every
row records its first quote time (`trigger_detail.first_quote_at`), and the delay is reported.

## 4. Information and price rules

- A forecast uses only results, shots and quotes retrieved, captures taken, and fits published at or before its
  `information_at` (the run's start); `generated_at` is when it finished. The store refuses rows made at or after the
  start or for a game not scheduled at that start (migration 0032).
- **Bet price: the first `current` moneyline quote retrieved after `generated_at` and before the start** (the bettor acts
  after the forecast; odds are polled every 30 min). Never a quote retrieved before the forecast.
- Close: the last quote of the bet's own bookmaker (names compared without spaces or case) retrieved before the start.
  When no quote follows the bet quote, the bet quote is the close (excess CLV = minus the margin); such bets count.
  Settlement: `markets.settle`; a void bet (postponed game or moved start: refunded) is left out of every metric.
- A run computes its forecasts without the store lock and appends them under it, waiting up to 20 min for the two-hour
  cycle; `generated_at` stays the time the forecast was made. A run that writes takes its times from the clock; a fixed
  `--now` is accepted only with `--dry-run`, which writes nothing.
- One bookmaker: DraftKings via ESPN, as recorded in `market_quotes`.

## 5. Bet rule

The first forecast of an arm per game whose probability exceeds the bet quote's de-vigged probability by at least
**6 pt** on a side bets that side, 1 unit, at the quoted American price (|American| < 100 is not a price: no bet, and
the arm's one bet for that game is used up).

## 6. Decision (one analysis, at the end)

- Games starting before **2027-05-01T00:00:00Z** (the 2026–27 regular season and any playoff games before that time),
  forecasts made from installation on (`INSTALLED_AT`, recorded at the freeze).
- **Primary metric: mean excess CLV** = de-vigged close probability of the bet side minus the implied probability of the
  price taken (> 0 means the close beats the price paid, margin included; expected profit per unit ≈ price × excess).
- **95% interval:** week-block bootstrap (ISO week of the start, in UTC), 10,000 resamples, seed 20261016, percentile.
- **Verdict per arm:** `pass` if at least **150** bets have an excess CLV and the interval's lower bound is above 0;
  `fail` if at least 150 and it is not; `inconclusive` with fewer than 150.
- Expected precision: the 2025–26 open bets had a per-bet CLV spread of about 4.7 pt, so 150 bets give roughly ±0.75 pt
  and 275 bets ±0.56 pt (before the week blocking widens it).
- Reported, never decisive: ROI and W-L (thousands of bets would be needed to decide on ROI), raw CLV, bets per arm,
  unavailable rows by reason, model versions in force, time from the trigger to the forecast, runs per day (`early_runs`;
  days with fewer than 40 of the 48 runs are listed), and both arms side by side (a descriptive comparison only).
- Command: `python -m app.cli --sport nhl early-forecasts-report` (read-only; `early_forecasts.verdict`, written before
  any row existed). Before the end date it shows rows, runs and coverage only; the verdict is sealed unless `--final` is
  given, and `paper-trades` leaves the early sources out.

## 7. What could make the result wrong (and what is done about it)

- **Price timing:** the quote's own time is unknown (retrieval time is used) and polling is every 30 min, so the bet
  price can be up to 30 min after the forecast. A real bettor could act sooner; the rule is conservative for the news arm.
- **One bookmaker:** a stale or off-market DraftKings line can fake an edge. Excess CLV is measured against the same book's
  close, which limits this; best-price shopping is not modelled.
- **Model identity changes:** a release can change the NHL adaptive definition mid-test. Each row records the definition
  (`model_version`) and fit (`provenance`); every row counts, and any change is listed in the report.
- **Outages** (job not running, capture failures, a store lock busy for 20 min) reduce bets but are not tied to
  outcomes; every run that reaches the store is in `early_runs`, and every day with fewer than 40 runs (none included)
  is listed. A run refused by the release guard or skipped for the lock is only in the job's log.
- **The injury member abstains often** (fit retired after new boxes, unresolved players): the news arm may end
  `inconclusive`. That is an answer, not a reason to change the rule.

## 8. Not allowed after freezing

Changing the members, triggers, threshold, price rule, metric, interval, minimum or end date; stopping early or
extending because of interim numbers; pooling the arms; scoring games before installation; dropping rows. Interim
`early-forecasts-report` runs are for operations only (rows created, errors).

## 9. Freeze record (filled at installation)

- Commit:
- sha256: this file; `backend/app/early_forecasts.py`; `backend/app/paper_trading.py`; `backend/app/cli.py`;
  `backend/app/migrations/0032_early_forecasts.sql`; `scripts/launchd/com.nba-predictor.nhl-early.plist`
- Frozen at (UTC), installed at (UTC) — the installed time is also set as `early_forecasts.INSTALLED_AT`:
- Release environment and NHL adaptive definition in force at installation:
