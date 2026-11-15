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
  Nothing was purchased and no key is configured.
- **Historical (free, found 2026-10-16):** ESPN's core odds endpoint
  `sports.core.api.espn.com/v2/sports/<basketball|hockey>/leagues/<nba|nhl>/events/<id>/competitions/<id>/odds` keeps
  odds for finished games (no key). `app/sources/espn_odds_history.py` + `app.cli odds-history` store the raw bodies
  (research files only, never the store's tables); NHL events are matched from ESPN scoreboards (start ±3 h).
  Coverage seen: 10–12 books with open and close for NBA 2022–24 and NHL 2022–24; "current" only (used as the close) for
  older seasons; one book (DraftKings) for NHL 2024–25 and 2025–26 and NBA 2025–26 (as checked). Cleaning: overround 1.00–1.12, sport line bounds, mirrored
  handicaps, in-play feeds and projection sites excluded, books > 0.15 from the other books' median dropped. About 6.7%
  of NBA best-price pairs imply an arbitrage (books at different moments): best-price returns are an upper bound.
  Used for the 2026-10-16 market comparison (HANDOFF.md): no model or weighting beat the closing line.
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

## NBA injury reports: raw collection pilot (built 2026-10-01; not scheduled yet)
- **Terms check (plan step 1, 2026-10-01).** NBA.com Terms of Use (page dated July 13, 2026), as extracted by an automated summary of
  the page: section 1 allows downloading displayed material "to any single computer only for your personal, noncommercial use" and forbids
  use "for public or commercial purposes"; section 9 limits NBA Statistics to "legitimate news reporting or private, non-commercial
  purposes" and excludes gambling and fantasy uses. No clause about automated access, scripts or scraping was found. The host's
  `robots.txt` (`ak-static.cms.nba.com/robots.txt`, fetched directly) does not disallow `/referee/injury/`, sets `Crawl-Delay: 1` and
  disallows URLs with a query string. This is not legal advice. Consequence: personal research only, at least 1 s between requests, never a
  query string; nothing collected here may be published or sold.
- **Collector:** `app.cli injuries` (`app/injuries.py`, `app/sources/nba_injury.py`, migration 0010). It saves the :00 and :30 reports for
  half-hour slots inside a game window as unchanged PDFs in the raw cache (`data/raw/nba_injury_report/…​.pdf.gz`) and logs every HTTP
  attempt in `injury_report_attempts` (request start, response-completion time, outcome, status, Last-Modified, ETag, size, hash).
  The PDFs are not read. Budgets: 8 attempts per run, 4 per report, 120 per rolling 24 hours, counting retries, 403s and failures.
  A daily HEAD check of 2 stored reports looks for upstream changes. Off unless `NBA_INJURY_COLLECTION=1`.
- **Supervised trial (scratch copy of the database, 3 GET + 1 HEAD):** two published reports from 2026-03-10 stored (102,487 and 102,763
  bytes, hashes verified, valid PDF headers, about 95 kB each on disk); a date with no report logged as `not_available` (HTTP 403); the
  HEAD re-check matched the stored ETag, Last-Modified and length. Reports in March 2026 are about 100 kB, so a season at 48 a day is
  nearer 0.8 GB than the plan's 0.6 GB estimate.
- Installed 2026-10-01 as launchd agent `com.nba-predictor.injuries` (runs at :02 and :32). First run: skipped, 0 requests (no game window until 2026-10-19T07:00Z).

## NHL and market sources (2026-09-30; probes from this machine, about 70 requests in total)
Raw probe excerpts were kept in the session scratchpad, not the repository. One probe exceeded the intended size limit: a
ranged request to MoneyPuck's `all_teams.csv` returned the whole 126.5 MB file. It was inspected and deleted.

| Source | Access / cost | Coverage verified | Use |
|---|---|---|---|
| NHL stats REST `api.nhle.com/stats/rest/en/game?cayenneExp=season=YYYYYYYY` | NHL-owned, public, no key; undocumented | every game of a season in one request (2009-10 … 2026-27): type, Eastern start, final score incl. shootout goal, last period (REG/OT/SO); no neutral flag, no period scores | **Adopted**: schedule, results, decisions (`app/sources/nhl_stats.py`), 1 request per refresh |
| NHL stats REST `/team`, `team/summary`, `team/realtime`, `team/powerplay`, `goalie/summary` (`isGame=true`) | same | team list with ids/franchise ids; per team-game shots, PP/PK; per goalie-game `gamesStarted` (2 per game in 2023-24) | team list adopted; the others are candidates for later features (actual starters are **post-game** facts) |
| NHL `api-web.nhle.com/v1/...` (schedule, score, gamecenter right-rail/boxscore/landing/play-by-play) | same; `cache-control: max-age=1` | per-period goals and shots per game back to 2009 (one request per game); no probable-starter field | not used (per-game volume); period results available if period markets are built |
| ESPN `site.api.espn.com/.../{nba,nhl}/scoreboard` | unofficial, no key | DraftKings moneyline, spread/puck line and total with open/current prices for scheduled games; NHL `probables` (expected/confirmed goalie) for upcoming games only, overwritten after the game; month payloads include quarter/period `linescores` | **Adopted** for line quotes (`market_quotes`, every retrieval kept); NBA lines were back-filled from already-cached day scoreboards (642 quotes, 26 games) |
| ESPN core `.../events/{id}/competitions/{id}/odds/100/propBets` | unofficial, no key | upcoming NHL games only: team totals, period lines, player props, each with `lastUpdated`; 404 for past games | **not adopted yet**; the only free team-total/period line source found (forward snapshots only) |
| MoneyPuck data (moneypuck.com/data.htm) | free for non-commercial use with credit; "non-approved scraping will be blocked" | 2008-2026 team game-by-game xG (126.5 MB file rebuilt daily, no version stamp) | optional later feature (lagged xG); would need dated snapshots |
| sportsdataverse-data NHL releases | MIT repo; data from the NHL API | 2010-2026 schedules, team/goalie boxes; the `nhl_linescore` file has game totals only (release notes say per period) | not needed: the NHL API covers it |
| hockeyR / hockeyR-data, fastRhockey | stale or unlicensed; R packages | – | rejected |
| DailyFaceoff starting goalies | Cloudflare 403 on robots.txt | – | rejected (not bypassed) |
| The Odds API | needs a key (free tier exists) | – | not used; would need your approval |

Terms: NHL.com's terms limit use to "non-commercial, informational, personal use" and forbid unauthorized automated
harvesting; the stats API has no separate terms. Request volume is kept to one season request per refresh (≥ 30 min apart) and
≤ 2 ESPN day requests per odds snapshot. ESPN terms are as noted above (personal research decision, 2026-09-30).

### Blockers by market (exact)
- **Period/quarter markets:** results exist (ESPN month `linescores`, NHL right-rail), but no historical period prices exist for
  free, and no period model or settlement rules have been built. Not implemented.
- **Team totals:** forecasts are recorded at model-only lines; bookmaker team-total lines exist only as forward ESPN propBets
  snapshots (not collected yet), so there is no bookmaker comparison.
- **Starting goalies:** actual starters are known only after the game; ESPN `probables` are overwritten after the game and carry
  no timestamp. Usable only if snapshotted prospectively (the ESPN NHL day scoreboards now cached by the odds job contain them,
  but they are not parsed). No historical pregame goalie feature is possible.
- **Player props:** no player identity mapping, participation model, DNP policy or historical prop lines. Not started.

## Feasibility probes — 2026-10-02 (read-only; scratch venv, ~30 requests, 2 files downloaded; nothing ingested)
| Source | Probe (actual) | Adds beyond current inputs | Coverage verified | Timing | Licence / terms | Cost |
|---|---|---|---|---|---|---|
| pbpstats (MIT, last release 2024-04) | stats.nba.com pbp v2/v3: timeout 25 s; cdn.nba.com: 403; data.nba.com 2025-26: empty stub; NBA S3 `liveData` pbp 2019-20/2023-24/2025-26 games: 200; pbpstats `live` provider parsed 0022300061 (191 possessions, lineups) | real possessions, 5-man lineups, shot zones, on/off | single games only | postgame; `Last-Modified` is last rewrite, not first publication | NBA.com terms; package unmaintained; S3 path undocumented | M |
| SportsDataverse releases (MIT repo; ESPN/NBA content) | `nba_stats_possessions/nba_possessions_2026.parquet` 200 (3.8 MB): 278,341 possessions, 1,400 games incl. all 1,230 regular 2025-26, 0 missing lineups; `nba_lineups_2026.parquet` 200 | as above, historical backfill without hitting stats.nba.com | 2025-26 only (2019-20…2024-25 claimed, not downloaded) | no publication time; possessions release not refreshed in season (last write 2026-08-13) | MIT repo; data owner terms | S backfill, M in-season |
| NHL api-web pbp (used by fastRhockey) | `/v1/gamecenter/2024020001/play-by-play` 200; `2015020001` 200 | shot x/y, shot type, zone, goalie in net, strength state (`situationCode`), penalties | one game in 2015-16 and 2024-25 | postgame; no availability header | NHL.com terms | M (own xG model, ~1,312 requests/season) |
| fastRhockey (R, MIT + file, v1.0.0) | read DESCRIPTION/source; not run (R) | wraps the sources above; sdv NHL release files incl. pbp, shifts, xG boosters | not downloaded | — | MIT + file (earlier "unlicensed" note outdated) | reuse sources only |
| NGBoost 0.5.11 (Apache-2.0) | fit Normal/Bernoulli on synthetic data with sklearn 1.9.1, numpy 2.5.3 | predictive distributions | — | — | Apache-2.0; pulls lifelines, pandas, matplotlib | S, heavy deps |
| CatBoost 1.2.10 (Apache-2.0) | arm64/cp312 wheel installs with numpy 2.5.3; Logloss and RMSEWithUncertainty fit | learner only | — | — | Apache-2.0; ~88 MB + pandas, matplotlib, plotly | S |
| Optuna 5.0.0 (MIT) | 20-trial study ran | tuning only | — | — | MIT; sqlalchemy, alembic | S (not needed for a small fixed grid) |

Ranking (Claude): NHL lagged team shot-quality (xG) features from api-web play-by-play are the highest-value feasible addition
— shots carry far more signal than goals in hockey, the source is free and open now, and in-season refresh is one request per
finished game. NBA real possessions largely duplicate our possession estimate; lineup-weighted ratings are higher value but
need an undocumented in-season feed (L). Goalie quality cannot be used pregame (starters known only after the game).
