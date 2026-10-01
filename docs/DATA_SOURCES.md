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

## Accuracy plan step 1: source verification (2026-09-30, read-only; no schema, ingest or ledger change)
Probe: `scripts/probe_espn_summary.py` (reads the DB read-only). It samples 30 random final competitive games per season (seed 20260930), 1 request/s.

### ESPN game summary `site.api.espn.com/.../nba/summary?event=ID`
| Season | Sampled | HTTP 200 | Turnovers + OREB + DREB + FGA + FTA present | FGA equals stored scoreboard FGA | Median size (gzip) |
|---|---|---|---|---|---|
| 2019-20 | 30 | 30 | 30 | 60/60 | 40.0 kB |
| 2020-21 | 30 | 30 | 30 | 60/60 | 39.6 kB |
| 2021-22 | 30 | 30 | 30 | 60/60 | 40.1 kB |
| 2022-23 | 30 | 30 | 30 | 60/60 | 40.1 kB |
| 2023-24 | 30 | 30 | 30 | 60/60 | 38.9 kB |
| 2024-25 | 30 | 30 | 30 | 60/60 | 38.0 kB |
| 2025-26 | 30 | 30 | 30 | 60/60 | 37.9 kB |

- **Coverage gate (at least 95% per season): passed on the sample**, 210/210. 30/30 per season only bounds true coverage above about 88% (95% one-sided),
  so step 2 must measure exact coverage on the full set and report every gap.
- Fields: team `totalTurnovers`, `offensiveRebounds`, `defensiveRebounds`, `fieldGoalsMade-fieldGoalsAttempted`,
  `freeThrowsMade-freeThrowsAttempted` → possessions ≈ FGA − OREB + TOV + 0.44·FTA.
- **Timestamps:** none for when stats were published. `header.competitions[0].date` is the tipoff. Availability must follow the existing result
  rule (historical: assumed tipoff + 4 h; live: observed retrieval time).
- **Revisions:** a re-fetch of 5 games from different seasons returned identical team stats, but different bytes every time (the page carries
  news/odds). Raw-cache deduplication therefore won't collapse repeats: fetch each final game once (plus at most one
  re-check). Same-night revisions near the game can't be tested until the season starts. Check on the first preseason games (from 2026-10-03)
  at about 1 h and 24 h after the final.
- **Cost:** 1 request per game, about 0.2 s, about 370 kB raw / about 39 kB gzip. A full backfill (about 9,400 games) is about 2.6 h at 1 request/s and about 370 MB in the gzip cache.
  Lighter alternative: core API `sports.core.api.espn.com/v2/.../events/ID/competitions/ID/competitors/TEAM/statistics`
  (about 33 kB raw / 4 kB gzip, same fields), but 2 requests per game (about 5.2 h, about 80 MB). The choice is made in step 2.

### ESPN injuries `site.api.espn.com/.../nba/injuries`
- **Current snapshot only**: 66 active entries across 26 teams. `date` is each report's last update (2026-02-09 to 2026-09-30).
  Entries drop off when resolved, so **there is no history**, and it can't be backfilled or backtested. It can only be collected forward with our
  retrieval time. There is a `source` field (not yet assessed); statuses seen: Day-To-Day, Out. 756 kB raw / 41 kB gzip.
- Consistent with plan rule 8: any injury feature needs a season of our own timestamped snapshots first.

### Terms of use (important)
ESPN.com's footer links to the Disney Terms of Use (`disneytermsofuse.com/english/`, read 2026-09-30). Relevant clauses:
- 2.A: license "for your personal, noncommercial use only".
- 2.B.viii: no "commercial or business-related use or build a business utilizing the Disney Products".
- 2.B.x: no access "using a robot, spider, script, or automated means ... for purposes of ... data mining or web scraping".

**Consequences:**
- **Selling a product built on ESPN data is prohibited by these terms.**
- The automated collection this project already runs (scoreboard, odds, and now possibly summaries) is at best a grey area
  even for personal research.
- This is not legal advice. It is a blocker for any commercial plan, and a decision for the user about continued automated collection.

## Possession inputs: sportsdataverse team box release (accuracy plan step 2, imported 2026-09-30)
- Source: `github.com/sportsdataverse/sportsdataverse-data`, release `espn_nba_team_boxscores`, `team_box_<season>.csv`
  (repo MIT; content ESPN's, so personal research only). Command: `app.cli import-team-box --from-season 2020 --to-season 2026 --historical-backfill` (completed seasons only; omit the flag for the current season)
  (one GitHub API call plus one download per season; each file must match the SHA-256 GitHub publishes, otherwise it is rejected).
- Stored as append-only observations in `team_possession_observations` (0006 replaced the latest-only 0005 table; 0007 keys
  rows by retrieval event after the Codex re-review): one row per (game, team, download, i.e. `raw_fetches.fetch_id`) with points, FGA, FTA,
  OREB, DREB and TOV (= `total_turnovers`). A re-download of identical bytes is its own observation, so a correction that returns to
  an earlier value (12 → 13 → 12) is kept. Updates, deletes and conflicting inserts (including INSERT OR REPLACE) are rejected by triggers,
  as they are on `raw_fetches`.
  It is separate from `team_game_stats`, so scoreboard refreshes cannot touch it. Corrections are new rows; nothing is updated or deleted, and replay rebuilds it from the raw cache.
- Availability (the policy is stored in each cached file's request key, so replay applies the same one):
  - Default `observed_retrieval`: usable from our download time, however late the file arrives.
  - `--historical-backfill` (explicit): `historical_backfill_assumed`, available with the result (tipoff + 4 h), **only** for games
    whose result was itself imported retrospectively. Games whose results we observed live always use the download time.
  - The seven 2026-09-30 imports (2019-20 to 2025-26, all completed seasons) were historical backfills, so all 17,826 rows are `historical_backfill_assumed`.
- As-of values for features: `ingest.POSSESSION_AS_OF_SQL` / `possession_as_of(conn, cutoff)` give, per game and team, the most
  recently downloaded file among those available by the cutoff. A later correction is invisible before its own availability.
  Limit: a historical backfill cannot recover values published before a file was first downloaded; for backfilled seasons, the file we hold is treated as the postgame box.
- Coverage of final regular/play-in/postseason games with complete possession inputs for both teams:

| Season | Final games | With possession inputs | Share |
|---|---|---|---|
| 2019-20 | 1,143 | 1,143 | 100.00% |
| 2020-21 | 1,171 | 1,165 | 99.49% (6 play-in games absent from the file; they also lack scoreboard box stats) |
| 2021-22 | 1,323 | 1,323 | 100.00% |
| 2022-23 | 1,320 | 1,320 | 100.00% |
| 2023-24 | 1,319 | 1,319 | 100.00% |
| 2024-25 | 1,321 | 1,321 | 100.00% |
| 2025-26 | 1,322 | 1,321 | 99.92% (espn:401810169: one team's turnovers missing) |

- Cross-check against the scoreboard: points identical; FGA differs by 1 on 5 team rows in 2020-21 (the file's 2021-24 content was
  rewritten upstream on 2026-08-26). Reported, not overwritten. Non-franchise (All-Star) rows are skipped: 2–8 per season.
- **In-season refresh (step 6, 2026-10-01):** `app.cli boxscores` uses ESPN's game summary (`/nba/summary?event=ID`, raw-cache source
  `espn_summary`), one request per recent final lacking a complete box, plus one re-check 24 h after tipoff; at most 30 requests per run
  and 5 per game. The sportsdataverse file is not used in season (its update timing is unknown). Each retrieval is its own observation with
  `observed_at = available_at =` the retrieval time. Personal-research use of ESPN only (terms above).
  - Check against real data in a scratch copy of the database (3 requests, 2025-26 games): two games identical to the backfill on every
    field; for `espn:401810169` the summary supplies the turnovers the backfill file lacks (21), everything else identical.
  - The production store has no summary observation yet (no eligible final in the last 14 days; the job skipped with 0 requests).
