# Notes: categories F (league/media sites), G (open data), H (soccer-specific)

> **Main session review note (2026-11-11):** the G1 figures "36,415 games with 3-way" and "1998-05" were counted from the full download, which was not kept; they are not backed by a saved file (only the last date 2026-10-19 is, in samples/G-kaggle/archangel0x01-games-NHL-2026-27.csv). Pages deleted after review are listed in README.md Process notes.

Worker: research subagent, 2026-11-11 UTC (started 2026-11-11T04:5x UTC, i.e. late on 2026-11-10 ET).
All fetches used curl with UA `research-check (personal, non-commercial)`, at most one request per 1.2 s.
Each sample folder has a `meta.txt` (URL, UTC time, HTTP status, bytes).

## Verdicts

| # | Candidate | Missing markets covered | Verdict |
|---|---|---|---|
| G1 | Kaggle archangel0x01 "NBA, NHL and College Basketball Results and Odds" | **NHL regulation 3-way (average closing home/draw/away), 36,415 NHL games, 1998-05 to 2026-10-19** | **Usable with care**: history only, average across unnamed books, date only (no start time), not updated after Oct 2026, scraped from unnamed odds-comparison sites (provenance and rights unclear even though uploader says MIT) |
| G2 | Kaggle archangel0x01 "Football Odds Movements 2018-2026" | Soccer goals match total (main moving O/U line + prices, not just 2.5), open+close, Pinnacle/Bet365/SBOBET/Singbet, 2018-07 to 2026-06, incl. internationals, UCL, UEL | **Usable with care** for goals-total history. No corners, BTTS, or team goals. Same provenance caveat as G1 |
| G3 | oliviersportsdata (HF + Kaggle) timestamped football odds | **Corner totals (Bet365 open/close line + both prices)**, goals, AH, 1X2 | **Not usable**: full data is paid (Gumroad, single-commercial-user licence); free sample is only 20 matches (all 20 have corner close lines) |
| G4 | nflverse `games.csv` | none directly (spread, total, both moneylines, spread/total prices). Team totals can only be derived | Usable now for NFL main lines; matched WAS@NYG 2026-11-13T01:15Z. Not a team-total market |
| G5 | Kaggle cviaxmiwnptr NBA betting data 2007-08 to 2025-26 | none (spread, total, ML, 2nd-half spread/total) | Not usable for missing markets |
| G6 | HF minmaxxxer weekly odds (NBA/NHL/NFL/EPL 2026-27) | none (consensus spread/total/ML, no-vig) | Not usable for missing markets |
| G7 | HF abetco odds-history-archive | none (h2h/spreads/totals only; Odds-API style rows) | Not usable for missing markets |
| G8 | HF eatpizzanot soccer-dataset | none (1X2 closing only; corner COUNTS) | Not usable |
| G9 | Kaggle jonathanncoletti NHL, michaelmallari NHL | none (spread/OU/favourite ML) | Not usable |
| G10 | sportsbookreviewsonline archives | none (NHL ML/puck line/OU, ends 2022-23) | Not usable: terms forbid reproduction |
| G11 | Zenodo | nothing relevant | Skipped: robots `Disallow: /api` (seen after 2 searches; bodies deleted) |
| G12 | hockey-reference, sportsoddshistory | not checked for data | hockey-ref: robots only; sportsoddshistory robots HTTP 503 |
| H1 | football-data.co.uk | — | **Skipped**: robots.txt disallows ClaudeBot / Anthropic-AI / Claude-Web |
| H2 | OddsPortal | — | Not usable: terms forbid scraping and database extraction |
| H3 | BetExplorer, Flashscore | — | Not usable: Flashscore robots `Disallow: /` for most paths; BetExplorer terms page 404 (no terms read) |
| H4 | openfootball | none (results only, no odds fields) | Not usable for prices |
| H5 | FBref, Understat | none (stats) | FBref robots HTTP 403; Understat not pursued |
| F1 | NHL API api-web.nhle.com | — | **Skipped**: robots.txt disallows ClaudeBot / anthropic-ai / Claude-Web |
| F2 | NBA.com / cdn.nba.com | — | Skipped: www robots blocks ClaudeBot/anthropic-ai; cdn odds JSON gave HTTP 403 (Akamai) |
| F3 | Yahoo, BBC, The Athletic, TSN | — | Skipped: robots.txt names Claude/Anthropic agents |
| F4 | CBS Sports odds pages | none (open line, spread, total, ML by book) | Not usable for missing markets |
| F5 | Fox Sports API, SofaScore, FotMob | — | Not usable: api.foxsports.com `Disallow: /`; SofaScore 403 even for robots; FotMob `Disallow: /api/*` |
| F6 | NFL.com, NBC Sports, Sportsnet, Sky Sports | not investigated beyond robots.txt | No result |

**Bottom line:** no free, permitted source of **corner lines or prices** was found. The only corner price history found is paid (G3).
The free finds are NHL regulation 3-way closing averages (G1) and soccer goal-total line history (G2), both from scraped Kaggle uploads with unclear rights.
No free source of team totals (NBA/NHL/NFL), BTTS, double chance, draw no bet, or soccer team goals was found in F/G/H.

## Process note (coordinator rule applied)

The coordinator later ruled that any site whose robots.txt names ClaudeBot, anthropic-ai or Claude-User must be skipped.
Before that ruling I had already fetched data bodies from api-web.nhle.com, football-data.co.uk and sports.yahoo.com.
I deleted those bodies. Only `robots.txt` and `meta.txt` remain, and nothing from them is reported here.
I also used Zenodo's `/api/records` search before reading Zenodo's robots.txt (`Disallow: /api`), so I deleted those results too.

---

## F. League and media sites

### F1 NHL API (api-web.nhle.com)
- Endpoint(s): `https://api-web.nhle.com/robots.txt`. Data endpoints were fetched before the ruling; bodies deleted, contents not used.
- Access: `api-web.nhle.com/robots.txt` lines 47-48 `User-agent: ClaudeBot` / `Disallow: /`; 62-63 `User-agent: anthropic-ai` / `Disallow: /`; 65-66 `User-agent: Claude-Web` / `Disallow: /`.
- Sample: `samples/F-nhl-api/robots.txt`, `www-robots.txt` (nhl.com), `meta.txt`.
- Verdict: **robots.txt disallows AI agents (ClaudeBot/Anthropic-AI); skipped.**

### F2 NBA.com
- `https://www.nba.com/robots.txt`: line 187 `User-agent: anthropic-ai` / `Disallow: /`; 315 `User-agent: ClaudeBot` / `Disallow: /`; 347 `User-agent: Claude-Web` / `Disallow: /`. Also `Disallow: /api/*`.
- `https://cdn.nba.com/robots.txt` gave HTTP 403. `https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json` gave HTTP 403 "Access Denied" (Akamai). Not retried, not circumvented.
- Sample: `samples/F-nba-cdn/` (robots files, the 403 body, meta).
- Verdict: skipped (robots names AI agents, and the CDN refuses).

### F3 Yahoo Sports
- robots lines 29-30 `User-agent: anthropic-ai` / `Disallow: /`; 56-57 `Claude-Web` / `Disallow: /`; 59-60 `ClaudeBot` / `Disallow: /`. Also `Disallow: /api/`.
- Odds pages had been fetched before the ruling; deleted. Sample: `samples/F-yahoo/robots.txt`, meta.
- Verdict: skipped.

### F4 CBS Sports (`https://www.cbssports.com/nba/odds/`, `/nhl/odds/`)
- Found by: direct check (league/media sites list).
- robots: `User-agent: *`, disallows `/data/*`, `/partners` and others. The odds pages are not disallowed, and Claude/Anthropic agents are not named.
- Content: `OddsBlock` table with columns opening line, spread, total, moneyline, plus a book selector (FanDuel, DraftKings, BetMGM, Caesars, Consensus). The NHL page has 6 games (today's slate), with totals such as o6.5/u6.5.
- Not found: team totals, NHL 3-way, or any soccer market. The "Regulation" string is a tooltip/nav label, not a market.
- Freshness: live/today only. Terms not read.
- Samples: `samples/F-cbs/nba-odds.html`, `nhl-odds.html`.
- Reference match: none attempted (no missing market present).
- Verdict: **not usable** (no missing market).

### F5 Fox Sports, SofaScore, FotMob, Flashscore
- Fox: `https://api.foxsports.com/robots.txt` = `User-agent: *` / `Disallow: /`. Skipped.
- SofaScore: `www.sofascore.com/robots.txt` and `api.sofascore.com/robots.txt` both returned HTTP 403 `{"error": {"code": 403, "reason": "Forbidden" }}` to our UA. Skipped, no retry.
- FotMob: robots `User-agent: *` ... `Disallow: /api/*` (only Googlebot, Bingbot and similar are allowed /api). Skipped.
- Flashscore: robots `User-agent: *` with `Disallow: /` repeated for many sections. Operator is Livesport, the same company as OddsPortal, whose terms forbid scraping (see H2). Skipped.
- Samples: `samples/F-foxsports/`, `F-sofascore/`, `F-fotmob/`, `F-flashscore/` (robots only).

### F6 BBC Sport, The Athletic, TSN
- BBC robots 111-118: `ClaudeBot`, `Claude-Web`, `anthropic-ai` all `Disallow: /`.
- nytimes.com (The Athletic) robots 148-189: `anthropic-ai`, `ClaudeBot`, `Claude-User`, `Claude-Web` all `Disallow: /`.
- TSN robots 443-444: `Anthropic-ai` / `Disallow: /`.
- Verdict: skipped.

### F7 NFL.com, NBC Sports, Sportsnet, Sky Sports
- robots.txt fetched (no Claude/Anthropic lines). No odds feed or page was investigated, so there is no claim about their content.

### Searches (F)
- Direct robots checks on all sites listed in the task (15 robots files). Direct probes: CBS and Yahoo odds pages, the NBA CDN odds JSON, and NHL partner/schedule endpoints (the NHL bodies were later deleted).
- No search engine query was needed for F. The candidates came from the task list.

---

## G. Open data

### G1 Kaggle: archangel0x01 / nba-nhl-and-college-basketball-results-and-odds — NHL REGULATION 3-WAY HISTORY
- URL: `https://www.kaggle.com/api/v1/datasets/download/archangel0x01/nba-nhl-and-college-basketball-results-and-odds/<file>`. Metadata: `/api/v1/datasets/metadata/archangel0x01/nba-nhl-and-college-basketball-results-and-odds`.
- Found by: Kaggle public API search `datasets/list?search=nhl odds`.
- Access: www.kaggle.com/robots.txt returns 404 (no rules). Downloads worked **without login or key** (HTTP 200), contrary to the brief's expectation. Kaggle site terms were not read. Licence stated by the uploader: MIT.
- Markets: `games.avg_home_odds / avg_draw_odds / avg_away_odds`. The dictionary says "Average closing decimal price for a draw in regulation time (three-way markets only)". The description says "Hockey averages are three-way prices for the result after 60 minutes".
- Whose numbers: an average "across the bookmakers listed on the results page". Books are not named for the NHL (`closing_odds` has 0 NHL rows).
- Freshness/history: static upload collected Oct 2026. NHL 37,295 games, **36,415 with 3-way averages, 1998-05-05 to 2026-10-19**. Not live, no early lines. Closing only.
- Provenance: "collected ... by web scraping publicly accessible odds comparison, results and statistics websites". Sources are not named, so redistribution rights are unclear.
- Gaps: date only (no start time), and the 2026-27 rows lack period scores.
- Settled example (from `archangel0x01-games-NHL-2026-27.csv`): `2026-10-19, Calgary Flames (home) 3 - 2 Carolina Hurricanes, avg_home 3.67, avg_draw 4.45, avg_away 1.76`. The reference-games list has no games before 2026-11-12, so there is no reference match.
- Other markets: the NBA rows hold only 2-way moneyline (1,010 rows), not team totals.
- Samples: `samples/G-kaggle/archangel0x01-nba-nhl-data_dictionary.csv`, `archangel0x01-games-first200.csv`, `archangel0x01-games-NHL-2026-27.csv`, `archangel0x01-closing_odds-first200.csv`, `archangel0x01-metadata.json`. The full games.csv (15,697,620 B) and closing_odds.csv (8,569,907 B) were trimmed.
- Verdict: **usable with care**. It is the only free NHL regulation 3-way price history found and works for calibration and backtests. Before relying on it, check it against games we already grade, and note the unclear upstream rights.

### G2 Kaggle: archangel0x01 / football-odds-movements-2018-2026 — SOCCER GOALS TOTAL HISTORY
- Found by: Kaggle API search `team totals odds`. Licence MIT (uploader). Downloads work without login.
- Markets: 1X2, Asian handicap, and over/under total goals. The O/U data covers the main line as it moved (example close line 2.75), not just 2.5. Every update is timestamped, with open and close per book, and AH/OU settlement labels.
- Books: Pinnacle, Bet365, SBOBET, Singbet.
- History: 63,597 matches, 2018-07 to 2026-06. Covers the Premier League, La Liga, Serie A, Ligue 1, Bundesliga (listing cut off but present per metadata), UEFA Europa League, other UEFA club competitions, World Cup qualifiers and international friendlies. Only finished matches. Not live.
- Not covered: corners, BTTS, team goals, double chance, draw no bet. DC and DNB could be derived from 1X2 and AH, but that is not a market price.
- Settled example (`archangel0x01-football-match_closing-first20000bytes.csv`): match 1502095 Bet365, close 1X2 2.37/3.75/3.00, close AH -0.25 (2.05/1.85), **close O/U 2.75 over 1.98 / under 1.92 at 2018-09-06T18:42Z**, result `Lose/Win`. matches.csv first row: 2018-07-05 International Friendly Malaysia 1-0 Fiji, OU close 3.0.
- Provenance: scraped, same caveat as G1. Files are big (odds_ou.csv 723,767,714 B). Only range-fetched heads were saved.
- Samples: `samples/G-kaggle/archangel0x01-football-movements-metadata.json`, `archangel0x01-football-match_closing-first20000bytes.csv`, `archangel0x01-football-matches-first6000bytes.csv`, `files-archangel0x01_football-odds-movements-2018-2026.json`.
- Verdict: **usable with care** for goals-total history. Not corners.

### G3 oliviersportsdata (Hugging Face + Kaggle) — CORNER PRICES EXIST BUT PAID
- URLs: `https://huggingface.co/datasets/oliviersportsdata/European-Football-Timestamped-Odds-Pinnacle-Match-Stats`, `.../International-Football-Timestamped-Odds-Pinnacle-Match-Stats`, plus NFL/NBA samples and the Kaggle mirror `oliviersportsdata/timestamped-football-odds-incl-pinnacle-sample`.
- Found by: HF API `api/datasets?search=odds`. HF robots: `User-agent: *` / `Allow: /`.
- Content: Bet365 FT and HT total corners, open and close, line plus over/under prices with timestamps. README: "Bet365 prices all 8 markets, but its full-time corner market is regular only from 2019/20". Full product covers 45,554 matches, 12 competitions, 2016-2026. The international set lists UCL, UEL, Nations League and more.
- Sample row: 2026-05-02T11:30Z Championship, Preston 1-3 Southampton, Bet365 FT corners close line 10, over 1.85 / under 1.95 at 2026-05-02T11:18:20Z. All 20 of the 20 sample rows have it.
- Licence: `license_name: single-commercial-user`. "Free sample: 20 matches out of 45,554", "Get the full dataset on Gumroad".
- Verdict: **not usable** (payment needed). It does show that corner price history is sold, not given away.

### G4 nflverse nfldata `games.csv`
- URL: `https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv` (raw.githubusercontent robots.txt returns 404).
- Found by: task hint. The GitHub repo API shows no licence field; pushed 2026-11-11T04:45Z, so it is updated continuously.
- Fields: away_moneyline, home_moneyline, spread_line, away_spread_odds, home_spread_odds, total_line, under_odds, over_odds. The book is not documented (DATASETS.md defines the columns but not their source).
- History: back to 1999 for spread/total (first row 1999 MIN@ATL spread -4, total 49). Moneyline is blank in early rows.
- Early lines: yes, next week's games already have lines (e.g. 2026_11 JAX@NYG).
- Reference match: **WAS @ NYG, ESPN 401873052, 2026-11-12 20:15 ET = 2026-11-13T01:15Z**. Row: `2026_10_WAS_NYG ... away_moneyline 114, home_moneyline -135, spread_line 2.5 (home favoured), -110/-110, total_line 41.5, under -115 / over -105`.
- Missing markets: none. Team totals can only be derived ((41.5±2.5)/2 → NYG 22.0 / WAS 19.5), and that derived number is not a market line.
- Samples: `samples/G-nflverse/games-first200.csv`, `games-2026-season-rows.csv`, `DATASETS.md`, `repo-api.json` (the full file was 2,189,969 B).
- Verdict: usable now for NFL spread/total/ML, but **not for any missing market**.

### G5 Kaggle cviaxmiwnptr / nba-betting-data (CC0)
- Columns: whos_favored, spread, total, moneyline_away/home, h2_spread, h2_total. Seasons 2007-08 to 2025-26 (last row 2026-06-13 playoffs). No team totals.
- Samples: `samples/G-kaggle/cviaxmiwnptr-nba-first200.csv` (full file 2,493,308 B).
- Verdict: not usable for missing markets.

### G6 HF minmaxxxer (nba/nhl/nfl/premier-league odds 2026-27, CC-BY-4.0)
- README fetched (`samples/G-hf-minmaxxxer/README-nhl.md`). Consensus no-vig ML, spread, total, open/close, built from Flashscore (game_id is a Flashscore ID). Updated weekly. A paid "Actor" sells per-book data.
- Verdict: not usable (no missing market; Flashscore provenance).

### G7 HF abetco/odds-history-archive (no licence)
- Daily JSONL from 2026-10-17. Only `h2h`, `spreads` and `totals` market keys. Books include draftkings, fanduel, pinnacle, kalshi, plus `_average`/`_fair`. NHL file 2026-11-10 includes TBL@BUF 2026-11-12T00:10Z (the commence time is +10 min against our 00:00Z).
- Samples: `samples/G-hf-abetco/odds_nba_20261105.jsonl.gz`, `odds_nhl_20261110.jsonl.gz`, `archive_manifest.json`.
- Verdict: not usable (no missing market; looks like a redistributed paid-API feed).

### G8-G9 other datasets
- HF eatpizzanot/soccer-dataset (CC-BY-4.0): `odds` table has home_win, draw and away_win only (96%+ Pinnacle closing). Corners and BTTS appear as outcome labels, not prices. Sample: `samples/G-hf-eatpizzanot/data_dictionary.md`.
- Kaggle jonathanncoletti/nhl-historical-game-data: columns spread, over_under, favorite_moneyline (from 2004). Sample: `samples/G-kaggle/jonathanncoletti-nhl_data_plus-first6000bytes.csv`.
- Kaggle michaelmallari/sportsbook-odds-on-nhl-games: moneyline, fav, over_under, seasons 2021-23. Sample: `samples/G-kaggle/michaelmallari-sportsbook-nhl-2022-2023.csv`.
- Kaggle hugomathien/soccer (European Soccer Database): downloaded without login (34,297,253 B zip containing database.sqlite), then deleted. It holds 1X2 odds 2008-2016 and was not inspected further.
- Verdict: none cover missing markets.

### G10 sportsbookreviewsonline (SBRO)
- URL: `https://www.sportsbookreviewsonline.com/scoresoddsarchives/nhl-odds-2022-23` (HTML tables). Index: `/scoresoddsarchives/nhl/nhloddsarchives.htm`.
- robots: `User-agent: *` `Allow: /` (plus `Disallow: /go/`).
- Columns: Date, Rot, VH, Team, period scores, Final, Open, Close (ML), PuckLine, OpenOU, CloseOU. Seasons 2007-08 to 2022-23. No team totals, no 3-way.
- Terms (`/terms/`): "You may not use or reproduce any content or materials on our website without our express written consent."
- Samples: `samples/G-sbro/`.
- Verdict: not usable (terms, and no missing market).

### G11 Zenodo
- `zenodo.org/robots.txt`: `User-agent: *` / `Disallow: /search` / `Disallow: /api`. I had run 2 `/api/records` searches before reading this. The results were deleted. Remembered titles had nothing relevant: the corner search returned ESPnet models, the hockey search returned unrelated papers.
- Verdict: skipped.

### G12 hockey-reference, sportsoddshistory, pro-football-reference, Kaggle (general)
- hockey-reference robots fetched (`Disallow: /hockey/`, `/play-index/*cgi`, etc.). No game-level odds pages were looked for. A web search shows only preseason (futures) odds pages.
- sportsoddshistory.com robots returned HTTP 503. Per search results it covers futures/series prices, not game team totals. Not fetched further.
- pro-football-reference: not fetched (nflverse G4 already covers NFL lines).

### Searches (G)
- WebSearch: "NHL historical odds archive free download team totals regulation three-way sportsbookreviewsonline"; "NBA historical odds dataset github team totals csv"; "free historical "team total" odds dataset NFL NBA download csv archive"; "nflverse schedules spread_line total_line source of betting lines documentation".
- HF API: `api/datasets?search=odds&limit=100` (57 results; sample saved at `samples/G-huggingface/api-datasets-search-odds.json`).
- Kaggle API: `datasets/list?search=corner odds`, `?search=nhl odds`, `?search=team totals odds` (saved in `samples/G-kaggle/api-list-search-*.json`).
- Zenodo API: 2 searches (deleted, see G11).
- No free dataset with **NBA/NHL/NFL team-total lines** was found.

---

## H. Soccer-specific

### H1 football-data.co.uk
- robots.txt lines 18-25: `User-agent: Anthropic-AI` / `Disallow: /`; `User-agent: Claude-Web` / `Disallow: /`; `User-agent: ClaudeBot` / `Disallow: /`.
- Data files and notes.txt had been fetched before the coordinator ruling. I saw the block lines in the same output as the notes fetch, i.e. not before reading. Per the rule, those bodies are deleted and no column claims are reported here.
- Sample kept: `samples/H-football-data/robots.txt`, `meta.txt`.
- Verdict: **robots.txt disallows AI agents (ClaudeBot/Anthropic-AI); skipped.**

### H2 OddsPortal
- robots `User-agent: *` disallows `/ajax-*`, `*/feed/*`, `*/match-event/*` and others.
- Terms (`https://www.oddsportal.com/terms/`, operator Livesport s.r.o.):
  - "you are not permitted to use our content available on the Website by embedding, aggregating, scraping or recreating it without our express consent"
  - "no extraction (copying) ... of the Database Content ... is permitted without our express consent"
  - "You must not burden our server ... with automated requests"
- Samples: `samples/H-oddsportal/robots.txt`, `terms.html`.
- Verdict: not usable. Scrapers on GitHub (OddsHarvester, scrapeOP) can pull BTTS and other markets from OddsPortal, but that is forbidden by these terms.

### H3 BetExplorer
- robots `User-agent: *` disallows `/bookmaker/`, `/*?year=` and others. `https://www.betexplorer.com/terms-of-use/` returned HTTP 404, so no terms were read. The operator was not verified today.
- Verdict: not used.

### H4 openfootball
- `https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json`: round, date, time, team1, team2, score.ft/ht. No match for "odds" or "price".
- Sample: `samples/H-openfootball/en1-2025-26.json`.
- Verdict: results only, not a price source.

### H5 FBref / Understat
- FBref robots returned HTTP 403 (blocked; not retried). Understat robots fetched (26 KB, no Claude lines). Not pursued: it is a stats source, not prices.

### Searches (H)
- WebSearch: "historical football corners betting odds dataset csv free download corner line over under"; "github dataset soccer odds BTTS "corners" closing odds csv"; "github csv "corners" odds bet365 asian corners historical premier league dataset "corner line"".
- Results: football-data (skipped, H1), BeatTheBookie (1X2 closing 2000-2015 per search result, not fetched), eatpizzanot (G8), OddsHarvester/scrapeOP (OddsPortal scrapers, H2), and paid APIs or products (Apify, thestatsapi, oddspapi, bigdataball, sharpapi). None offered free corner prices.
- HF and Kaggle searches (G) found corner prices only in the oliviersportsdata paid product (G3).
- **Corner history verdict: no free, permitted source of past corner lines or prices was found.**
