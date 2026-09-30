# Data sources and coverage (milestone 2)

Assessed 2026-09-30 from this machine. Re-check before relying on any source for public deployment.

## Source assessment
| Source | Result here | Use |
|---|---|---|
| `stats.nba.com` (what `nba_api` wraps) | Timed out after 25 s and 60 s, even with browser-style headers | Not used. `nba_api` not added. |
| `cdn.nba.com` static schedule / live scoreboard | HTTP 403 | Not used |
| ESPN `site.api.espn.com/.../nba/scoreboard?dates=YYYYMM&limit=1000` | HTTP 200, ≈3 MB/month, about 1 s | **Adopted** for schedule, results, basic team box stats and current moneylines |
| basketball-reference.com | HTTP 200 (HTML) | Not used (scraping, strict rate limits) |

ESPN caveats: the API is unofficial and undocumented, with no key, SLA or published terms. Treat it as local research
use until terms are reviewed. It rejected a custom User-Agent with 403; the client sends the default
`python-httpx` UA (no browser impersonation). Day ranges (`YYYYMMDD-YYYYMMDD`) return 400; month queries work.

Box stats available per team-game: points, FGM/FGA, 3PM/3PA, FTM/FTA, rebounds, assists.
**Not available** from the scoreboard: turnovers and offensive rebounds, so possessions and offensive/defensive
ratings cannot be computed from this source. ESPN's per-game `summary` endpoint may have them; it is not assessed (1 request per game).

## Odds
- **Historical:** ESPN strips odds from finished games (0 of 9,412 finished games carry odds). The Odds API offers
  historical NBA odds **only on paid plans**: coverage starts 2020-06-06, snapshots every 10 minutes (every 5 minutes from
  Sept 2022), at 10 credits per region per market ([docs](https://the-odds-api.com/liveapi/guides/v4/), read 2026-09-30).
  Nothing was purchased and no key is configured. **Historical market comparison is blocked on a paid source.**
- **Current:** ESPN carries DraftKings moneylines (open and current) for some upcoming games: 26 of 155 scheduled
  2026-27 games at retrieval. They are stored append-only in `odds_quotes` as `open` / `current` with a proportional de-vig.
  `quote_time` is NULL because ESPN publishes none. The retrieval time is recorded, and lines on finished games are never
  stored as a "close".
- **Forward collection (decided 2026-09-30: free sources only, no historical odds):** `app.cli odds` re-fetches the ESPN
  day scoreboard (`dates=YYYYMMDD`, ≈10–40 KB, checked 2026-09-30) for days with a game tipping off within 36 h, at most
  every 10 min. The closing snapshot is the last `current` quote whose fetch (`raw_fetches`) is at or before tipoff;
  `coverage` reports how many games have one and the median gap. Limits: one bookmaker (DraftKings via ESPN), not a sharp
  book; no quote time; coverage depends on the collector running (a sleeping laptop leaves gaps, which are reported, not filled).

## Timestamp rules
- Event time: `games.tipoff_at` (UTC).
- Retrieval: `raw_responses.first_retrieved_at`, every fetch in `raw_fetches`, and `games.first_seen_at` / `result_first_seen_at`.
- Availability: `games.result_available_at` + `result_availability_basis`:
  - `observed_retrieval`: the result was first seen within 24 h of tipoff, and availability is the retrieval time.
  - `assumed_tipoff_plus_4h`: the result was imported retrospectively. This is an **assumption**, not an observed publication time.
    All historical results currently use it.
- Schedule publication time is unknown and not stored.

## Coverage (actual, after ingesting 2019-10 … 2026-10)
Totals: 9,635 games, 59 teams (30 franchises, plus exhibition and All-Star teams), 18,824 team-game stat rows, 52 odds quotes at that time (110 on 2026-09-30 20:17Z; run `coverage` for the current count),
88 cached responses (≈10 MB gzip), SQLite ≈5 MB.

| Season | Regular rows (final) | Play-in | Post-season | Preseason | Other (All-Star) | Postponed/canceled rows | Finals w/o box stats | Dates |
|---|---|---|---|---|---|---|---|---|
| 2019-20 | 1,062 (1,059) | 1 | 92 | 78 | 2 | 12 | 0 | 2019-10-04 – 2020-10-11 |
| 2020-21 | 1,112 (1,080) | 6 | 85 | 49 | 1 | 32 | 6 (play-in) | 2020-12-12 – 2021-07-21 |
| 2021-22 | 1,241 (1,230) | 6 | 87 | 66 | 1 | 11 | 7 (preseason) | 2021-10-03 – 2022-06-17 |
| 2022-23 | 1,231 (1,230) | 6 | 84 | 70 | 1 | 1 | 0 | 2022-09-30 – 2023-06-13 |
| 2023-24 | 1,233 (1,231) | 6 | 82 | 73 | 1 | 2 | 0 | 2023-10-05 – 2024-06-18 |
| 2024-25 | 1,236 (1,231) | 6 | 84 | 74 | 3 | 6 | 5 (preseason) | 2024-10-04 – 2025-06-23 |
| 2025-26 | 1,235 (1,231) | 6 | 85 | 71 | 4 | 4 | 0 | 2025-10-02 – 2026-06-14 |
| 2026-27 | 88 scheduled | – | – | 67 scheduled | – | – | – | 2026-10-03 – 2026-11-01 (fetched through Oct) |

Notes:
- Final regular-season counts match the known season lengths: 1,059 (COVID-shortened), 1,080 (72 games), and 1,230.
  From 2023-24 onward there is one extra game, because ESPN labels the NBA Cup final as regular season even though it does not count in standings.
- Postponed/canceled rows are ESPN placeholder events. 66 of them have a separately identified final for the same matchup
  later in the season. Models must use `status = 'final'` only.
- All-Star-weekend events are labelled regular season by ESPN; the importer reclassifies them as `other`,
  based on ESPN franchise IDs 1–30.
- Seven completed seasons plus the current schedule meet the plan's target of at least five seasons, for results and basic box stats only.
