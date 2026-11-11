# Notes: categories D (fantasy / pick'em / DFS research) and E (exchanges / prediction markets)

> **Main session, 2026-11-11:** Polymarket's ToS forbids scraping and Kalshi's Developer Agreement forbids collecting/storing its data, so under the task rules both are SKIPPED. Their data bodies were deleted unused; only terms, docs, robots and meta.txt remain. Prices quoted below for these two are not backed by a kept sample and are not counted anywhere in README.md.

Fetched 2026-11-11 04:50–05:05 UTC (evening of 2026-11-10 ET). Requests were made with `curl -A "research-check (personal, non-commercial)"`, at most one per second. No account, key or login was used anywhere. Samples are under `samples/D-*`, `samples/E-*` and `samples/_robots-D-E/`, and every folder has a `meta.txt` (URL | UTC time | HTTP status). Files over about 3 MB were gzip-compressed after download; the content is unchanged.

## Verdicts

| Source | Verdict | One line |
|---|---|---|
| E Polymarket (gamma-api + clob prices-history) | **Usable with care: terms conflict, owner decision needed** | Public API with no key. Lists NFL and NBA team totals (NBA only on game day), soccer goals O/U, home/away goals, BTTS, corners total and per team, and 1X2 legs (double chance = "No" on a leg). Free hourly price history. **ToS forbids "data mining tools, robots, crawlers … to scrape … data from the Site, any other Interface"**, and polymarket.com is not offered to US residents. No NHL team totals and no NHL regulation 3-way. |
| E Kalshi (external-api.kalshi.com) | **Not usable (terms)** | Technically the best fit: public reads with no key cover NBA, NHL and NFL team totals (NHL rules count OT and the shootout like ours), EPL and other league corners, team corners, BTTS and goals, plus hourly bid/ask candles and history. **However, Developer Agreement v1.1 §3 / §3.1 limits API use to "facilitating a members own trading" and prohibits "Collecting, caching, aggregating, or storing data"**. Samples were fetched before I found the agreement. |
| E Polymarket US (polymarket.us) | Not usable | robots.txt has `Disallow: /api/`. I did not test the docs API host. |
| E Smarkets | Not usable | The OpenAPI spec shows `/v3/markets/{ids}/quotes/` and `last_executed_prices` require `api_key` (a session). Event lists are open but carry no prices. |
| E Betfair Exchange | Not usable | Needs an app key and a logged-in account (search of the Betfair developer docs). www.betfair.com returned 403 here. |
| E Matchbook | Not usable | Search results say a login session is needed. I could not reach the official docs (404). |
| E Betdaq | Not usable | Account-based API (search only). I fetched robots.txt but no data. |
| E Novig | Not usable | Its free daily public CSVs (data.novig.com) are anonymized: they have marketId and family (e.g. `NHL-TEAM_TOTAL`) but **no line, no teams, no game**. Resolving them needs the keyed API. |
| E ProphetX | Not usable | Its docs say a "Market Data API" exists for partners, with keys by request. |
| E Sporttrade | Not usable | No public API found; the robots.txt request redirected to the marketing site. |
| E PredictIt | Not usable | Its public API works but lists only politics (151 markets, 0 sports). |
| E Manifold | Not usable | Public API works, but markets are play money (MANA), user-made and sparse. Not book prices. |
| D DailyFantasyFuel | Not usable (line only, derived) | Shows game O/U plus an integer "TM PTS" (implied team points). It names no book, gives no prices and keeps no history, and the page doesn't say how TM PTS is computed. |
| D RotoWire | Not usable | Terms Art. VI: "AUTOMATED ACCESS; NO SCRAPING". |
| D Left Wing Lock (NHL) | Not usable | ToS: "personal, non-commercial transitory viewing" and no scraping. |
| D RotoGrinders | Not usable / not checked further | Implied team totals appear inside dated articles. `/api/` is disallowed and the terms URL I guessed returned 404. |
| D FantasyPros, NumberFire (now FanDuel Research), Daily Faceoff, Basketball Monster, Establish The Run, LineStar, SaberSim, FantasyLabs | Not usable / no team-total table found | Details below. |
| D PrizePicks, Underdog, Sleeper, Pick6, ParlayPlay, Betr, Dabble, Chalkboard | Not usable | Pick'em apps. No public team-line API was found. api.prizepicks.com returned 403, and ParlayPlay's robots.txt has `Disallow: /` for `*`. |

Bottom line for D: no fantasy, pick'em or DFS research site publicly serves a real sportsbook team-total **line plus prices** that we may collect. Where "implied team totals" appear, they are lines only, and they are either rounded or derived (DFF) or behind no-scraping terms (RotoWire, LWL).

---

## Category E

### E1. Polymarket (international), Gamma API + CLOB prices-history
- **Endpoints:** `https://gamma-api.polymarket.com/sports`, `/sports/market-types`, `/events?series_id=<id>&closed=false&limit=100`, `/events/slug/<slug>`, `/markets?sports_market_types=<t>&closed=true&order=startDate&ascending=true`. History comes from `https://clob.polymarket.com/prices-history?market=<clobTokenId>&interval=max&fidelity=60`.
- **Found via:** polymarket.com/robots.txt points to `llms.txt`, which links docs `market-data/discover-markets.md`. That page says: "This data is public and does not require authentication."
- **Access rules:**
  - robots.txt (polymarket.com): `User-agent: * / Disallow:` (empty). gamma-api and clob have no robots.txt (404).
  - ToS (polymarket.com/tos, saved as `tos.html`) prohibits: "Use any data mining tools, robots, crawlers, or similar data gathering and extraction tools to scrape or otherwise remove data from the Site, any other Interface, or Features". It also bars commercial redistribution to "Capital Market Client" firms or market-data distributors.
  - The docs do advertise the public Gamma API. This conflicts with the ToS, and the owner must decide.
  - `llms.txt`: "polymarket.com is not available to US residents" (stated for trading; US users are sent to polymarket.us).
  - No rate limits were hit.
- **Coverage observed** (market types in `market-types.json` and actual markets present):
  - **NFL team totals: yes.** `team_totals`, about 22 per game, posted days ahead (all Week 11 games, and Week 12 already listed).
  - **NBA team totals: yes, but only near game day.** About 27–29 `team_totals` per game on Nov 6–10 games. The Nov 11 ET games (reference 2026-11-12T00:00Z) had only moneyline, spreads and totals at fetch time.
  - **NHL team totals: no.** NHL events only have moneyline, totals and spreads. **NHL regulation 3-way: no.** The type `hockey_team_regulation_winner` exists, but 0 open and 0 closed markets were found (`mt-*.json`, `closed-oldest-hockey_team_regulation_winner.json`).
  - **Soccer** (EPL, UNL and other leagues; separate "-more-markets" and "-total-corners" events):
    - goals O/U 0.5–8.5
    - home and away goals (`soccer_team_totals`)
    - BTTS
    - total corners O/U and per-team corners (`soccer_team_total_corners`)
    - 1X2 as three Yes/No markets
  - **Double chance:** no `double_chance` markets are listed (0 open). It can be read as the "No" side of a 1X2 leg (e.g. "No" on Croatia win = England or draw). **Draw no bet:** none. **Corner handicap:** none.
- **Line + prices:** Yes. Each line has Yes/No (Over/Under) prices plus bestBid/bestAsk/liquidity. These are exchange prices, not a sportsbook's. Many alt lines are thin: wide books (e.g. bid 0.03 / ask 0.97) show `outcomePrices` of 0.5/0.5, which is a placeholder and not a price. Filter on spread and liquidity.
- **Freshness:** live order book.
- **History:**
  - The `prices-history` endpoint is public (no key).
  - The NFL team-total line WSH O/U 20.5 has hourly points from 2026-10-27 to now (317 points). The leading points are a flat 0.5, which is probably the placeholder before trading started.
  - EPL Fulham–Newcastle O/U 9.5 corners (2026-11-07) has 290 hourly points from 2026-10-25 to kickoff.
  - Oldest closed markets: team totals from 2025-10-26 (NFL), BTTS from 2025-10-31, total corners from 2026-03-01.
- **Early lines:** NFL, soccer goals and soccer corners appear days or weeks early. NBA team totals appear only on game day.
- **Worked examples** (reference games):
  - NFL `nfl:401873052` WSH @ NYG, 2026-11-13T01:15Z, Polymarket `nfl-was-nyg-2026-11-13` (startTime 2026-11-13T01:15:00Z):
    - "Commanders Team Total: O/U 20.5": Over 0.46 / Under 0.54, bid 0.44 ask 0.48, liquidity about $15.7k.
    - "Giants Team Total: O/U 20.5": Over 0.545, bid 0.53 ask 0.56.
    - Saved in `samples/E-polymarket/ev-nfl-was-nyg-2026-11-13.json`.
  - Soccer `soccer:uefa.nations:401861150` Croatia @ England, 2026-11-12T19:45Z, Polymarket `unl-eng-hrv-2026-11-12*` (startTime 2026-11-12T19:45:00Z):
    - 1X2 legs: England win 0.785, draw 0.135, Croatia win 0.075, so double chance England-or-draw ≈ 1 − 0.075.
    - O/U 2.5 goals: Over 0.665 (bid 0.66 / ask 0.67).
    - BTTS: Yes 0.485 (0.48 / 0.49).
    - England goals O/U 1.5: Over 0.765. Croatia goals O/U 0.5: Over 0.51.
    - Total corners O/U 9.5: Over 0.485 (0.47 / 0.50, liquidity about $4.3k).
    - Croatia corners O/U 2.5: Over 0.53 (0.51 / 0.55).
    - Saved in `ev-unl-eng-hrv-2026-11-12*.json`.
  - EPL `soccer:eng.1:401879233` Fulham @ Man City, 2026-11-21T12:30Z: O/U 2.5 Over 0.64 (0.63 / 0.65), BTTS Yes 0.565 (0.54 / 0.59). The corners lines exist but are empty books (0.03 / 0.97).
  - NHL `nhl:2026020293` TBL @ BUF, 2026-11-12T00:00Z: only moneyline and game totals (e.g. O/U 6.5 Over 0.525). No team totals and no regulation market.
  - NBA `espn:401909967` CLE @ NY, 2026-11-12T00:00Z: no team totals yet. A game-day example on a non-reference game, OKC @ POR 2026-11-11T04:00Z: "Thunder Team Total O/U 114.5" bid 0.51 / ask 0.58.
- **Verdict: usable with care.** The data fits very well, and it is the only E source not blocked by an explicit API agreement. Still, the site ToS anti-scraping clause and the US-residency restriction need an explicit owner decision before any collection.

### E2. Kalshi, public market data API
- **Endpoints:** `https://external-api.kalshi.com/trade-api/v2/series?category=Sports`, `/markets?series_ticker=<S>&status=open|settled&limit=1000`, `/historical/cutoff`, `/historical/markets?series_ticker=<S>`, `/series/<S>/markets/<T>/candlesticks?start_ts&end_ts&period_interval=60`, `/historical/markets/<T>/candlesticks`.
- **Found via:** docs.kalshi.com/llms.txt, which links "Quick Start: Market Data" ("access real-time market data without authentication").
- **Access rules:**
  - kalshi.com/robots.txt was **not readable**: the request returned 429 and then a "Vercel Security Checkpoint" page, which I did not try to get past. external-api has no robots.txt (404).
  - **Developer Agreement v1.1** (kalshi-public-docs.s3.amazonaws.com, saved as PDF and txt). §1: "Accessing or using Kalshi's API shall be considered a binding acceptance of this Agreement." §3: "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the Exchange; all other usages are disallowed". §3.1 prohibits "Collecting, caching, aggregating, or storing data or content accessed via the API except for purposes of facilitating your own trading on Kalshi."
  - **Process note:** I fetched the samples on the strength of the docs ("no authentication") *before* I found this agreement. That broke the brief's "terms before data" order for this source. I stopped fetching once I read it. The samples stay on disk only as evidence and should not be used as data. The owner can delete them.
- **Coverage observed:**
  - **Team totals:** `KXNBATEAMTOTAL` (19 lines per team), `KXNHLTEAMTOTAL` (1.5–5.5 per team), `KXNFLTEAMTOTAL`. The NHL rules say: "Team totals include goals scored in overtime. If the game goes to a shootout, the shootout winner is credited with one additional goal". That matches the app's NHL definition.
  - **NHL regulation 3-way: no.** `KXNHLGAME` is a 2-way "wins" market.
  - **Soccer:** league series for game, total, BTTS, team total, corners and team corners (EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL, international friendlies).
    - The EPL extra markets had 0 open at fetch time, 10+ days before Nov 21.
    - `KXUEFANLGAME` has only 1X2. No UNL totals or corners series were found.
    - 1X2 is three Yes/No markets, so double chance = "No" on a leg. No DNB or corner handicap series was seen.
- **Line + prices:** yes (yes/no bid and ask). Exchange prices.
- **History:**
  - Hourly candlesticks (bid/ask OHLC) are public.
  - Live set: after the cutoff of 2026-09-11.
  - Archive: `/historical/markets`. EPL corners go back to Aug 2026 (first match 26AUG21 ARS–COV), and NBA team totals to at least Apr 2026 (there are more pages).
  - NHL team-total markets start around 2026-10-28.
- **Worked examples** (fetched, but under the agreement above):
  - NBA CLE @ NY 2026-11-12T00:00Z (`KXNBATEAMTOTAL-26NOV11CLENYK`): "New York over 112.5" bid 0.48 / ask 0.51; "Cleveland over 109.5" 0.47 / 0.49.
  - NHL TBL @ CBJ 2026-11-13T00:00Z: "Tampa Bay over 2.5" 0.60 / 0.74.
  - NFL WSH @ NYG: "Washington over 20.5" 0.48 / 0.49.
  - UNL ENG–CRO: tie 0.14 / 0.15.
- **Verdict: not usable** (the API agreement limits use to members' own trading and forbids storing the data).

### E3. Polymarket US (polymarket.us)
- **What I fetched:** robots.txt, docs llms.txt and the Markets overview doc.
- **robots.txt:** "Blocked below: the JSON/telemetry API … `Disallow: /api/`".
- **Docs:** list Markets, BBO, Book and Price History endpoints. I did not test whether they need auth: the robots block on the site API plus the separate authentication docs were enough to stop.
- **Verdict: not usable** as checked.

### E4. Smarkets
- **What I fetched:** robots.txt (allows `/`), docs.smarkets.com (Swagger) and `https://api.smarkets.com/v0/control/openapi/?tags=trading_api`.
- **Auth:** the OpenAPI spec marks `/v3/events/`, `/markets/` and `/contracts/` as open, but `/v3/markets/{ids}/quotes/`, `/last_executed_prices/` and `/volumes/` as `security: api_key`.
- **Not done:** the terms page was a large JS bundle, and I extracted no scraping clause. I fetched no event or market data.
- **Verdict: not usable** (prices need a session key).

### E5. Betfair Exchange
- **What I tried:**
  - www.betfair.com/robots.txt returned 403 with an HTML block page.
  - The developer docs page returned 403 to WebFetch.
- **Search finding:** Betfair developer docs (via search) say every request needs an Application Key tied to a Betfair account, the Delayed key is for development, and "App Key generation is for personal betting purposes only."
- **Verdict: not usable** (needs a key and an account).

### E6. Matchbook
- **What I fetched:** robots.txt (`Disallow: /reports/`). The developers.matchbook.com page returned 404.
- **Search finding:** wrapper libraries and vendor pages say a login session token is required. Not verified first-hand.
- **Verdict: not usable** (needs an account).

### E7. Betdaq
- **What I fetched:** robots.txt only (Cloudflare content signals, `Allow: /`).
- **Not checked:** the API, which is account-based according to general knowledge and search; I did not verify this.
- **Verdict: not usable / not checked further.**

### E8. Novig
- **What I fetched:**
  - docs.novig.com/llms.txt: trading and catalog endpoints need a key ("Key: any").
  - docs `public-data/overview.md` and `markets.md`.
  - `https://data.novig.com/reporting/trade-data/index.json`: trades from 2026-08-03 and markets from 2026-08-04 through 2026-11-09.
  - One `markets.csv` for 2026-11-09 (11 MB, gzipped in samples).
- **Coverage:** reportTicker families include `NBA-TEAM_TOTAL`, `NHL-TEAM_TOTAL`, `NFL-TEAM_TOTAL`, `NHL-WINNER`, `NHL-SPREAD` and `NHL-TOTAL`. Soccer is almost absent (`EPL-SAVES`, `EPL-SHOTS_ON_TARGET`). There is no NHL regulation family.
- **Columns:** `date, marketId, reportTicker, openInterest, dailyVolume, open, high, low, close, status`. **There is no line, team or event**, so a row cannot be matched to a reference game without the keyed catalog API.
- **robots.txt:** data.novig.com/robots.txt returned 403 (an S3 error page); docs robots.txt allows everything except `/_next/`.
- **Verdict: not usable.** The public files cannot be mapped to games or lines.

### E9. ProphetX
- **What I fetched:** robots.txt (`Disallow: /board`) and docs.prophetx.co. The docs list a "Market Data API — Read-only access … Built for partners" plus a "Getting API Keys" guide.
- **Verdict: not usable** (partner key).

### E10. Sporttrade
- **What I fetched:** robots.txt, which redirected to the getsporttrade.com home page.
- **Search finding:** search found no public API, only paid resellers.
- **Verdict: not usable.**

### E11. PredictIt
- **What I fetched:** robots.txt (`Allow: /`) and `https://www.predictit.org/api/marketdata/all/` (200, 151 markets).
- **Coverage:** no NBA, NHL, NFL or soccer game markets.
- **Verdict: not usable.**

### E12. Manifold
- **What I fetched:** robots.txt (`Allow: /`) and `https://api.manifold.markets/v0/search-markets?term=Knicks%20Cavaliers` (200).
- **Coverage:** only resolved, user-created play-money (MANA) markets from earlier in 2026. No team totals.
- **Verdict: not usable.**

### Searches run, category E
- WebSearch:
  - "Kalshi terms of service scraping automated access market data API public"
  - "\"Kalshi\" \"Developer Agreement\" OR \"member agreement\" API data redistribution"
  - "Smarkets API v3 public endpoints events markets quotes without login terms"
  - "Matchbook API session token required \"login\" get events prices developers.matchbook.com"
  - "Betfair Exchange API application key required delayed key account market data"
  - "Novig OR ProphetX OR Sporttrade public API market data without account"
- Sites tried directly: robots.txt on all candidates; polymarket.com/llms.txt; docs.polymarket.com; docs.polymarket.us; docs.kalshi.com; docs.smarkets.com; docs.novig.com; docs.prophetx.co; predictit API; manifold API.

---

## Category D

### D1. DailyFantasyFuel
- **URL:** `https://www.dailyfantasyfuel.com/nba/projections/` (200), saved as `samples/D-dailyfantasyfuel/nba-projections.html`.
- **robots.txt:** `Disallow: /lineup/*`, `Allow: /`.
- **Terms** (`/terms-of-service`, saved): a grep for scrap, robot, crawl, harvest, reproduce, redistribute and "data mining" found nothing. The terms are mostly about subscriptions.
- **Content:** per-player rows carry "VEGAS O/U" and "TM PTS". The page does not say whether TM PTS is computed from spread+total, and it names no sportsbook and gives no prices.
- **Worked example:** NBA `espn:401909971` LAL @ MIL, 2026-11-12T01:00Z. Rows for LAL players show O/U 232.5 and TM PTS 122. The page gives no start time; I matched by slate date and teams. Integer rounding suggests a derived number.
- **History:** none seen.
- **Verdict: not usable** for pricing: a line only, derived and rounded, with no price and no history.

### D2. RotoWire
- **robots.txt:** `User-agent: * Disallow:` (empty), plus `/account/` and login.
- **Terms** (`termsandconditions.php`): "use manual or automated software, devices, or other processes to 'crawl,' 'scrape,' or 'spider' any of the Roto Sports Network website pages" is prohibited, and Art. VI is titled "AUTOMATED ACCESS; NO SCRAPING". The nba-lineups page was fetched once before I read the terms; I extracted no data from it.
- **Verdict: not usable.**

### D3. Left Wing Lock (NHL)
- **robots.txt:** `Allow: /`.
- **ToS** (`tos.php`): "for personal, non-commercial transitory viewing … We reserve to right to immediately ban … any account that employs scraping technology". The home page was fetched before I read the terms; no team-total table was seen there.
- **Verdict: not usable.**

### D4. Daily Faceoff (NHL)
- **robots.txt:** `Disallow: /api/`.
- **Home page:** fetched. It shows betting-picks articles, not a team-total table. The terms URLs I guessed returned 404.
- **Verdict: not usable / no team-total feed found.**

### D5. RotoGrinders
- **robots.txt:** `Disallow: /api/`, `/app`.
- **Search finding:** search shows "NBA DFS First Look … Sortable Odds … implied team totals" articles (dated, embedded in article bodies). The terms URL I guessed returned 404. I did not fetch an article.
- **Verdict: not usable / not checked further** (lines only, article format).

### D6. FantasyPros
- **robots.txt:** `Disallow: /api/ /json/ /ajax/`, `Crawl-delay: 5`.
- **Terms:** the page was fetched, but its text sits in a JS bundle and grep found no clause. I found no team-total page.
- **Verdict: not usable / not checked further.**

### D7. NumberFire
- **Status:** www.numberfire.com/robots.txt returns 301 to fanduel.com/research. FanDuel robots: `Disallow: /api`, `/research/api`.
- **Verdict: not checked further.**

### D8. Basketball Monster
- **robots.txt:** allows all.
- **What happened:** both the terms URL and `nbagames.aspx` returned a "Basketball Monster Error" page to our client.
- **Verdict: not usable.**

### D9. FantasyLabs, Establish The Run, LineStar, SaberSim
- **FantasyLabs:** robots.txt is an empty `User-agent: *`. ETR: `Disallow: /feed/`.
- **LineStar:** linestar.app gave no response (both passes failed).
- **SaberSim:** robots.txt has only a sitemap line.
- **Not done:** I did not fetch their pages. As far as I know, all four are paid tools, but I did not verify this today.
- **Verdict: not usable / not checked.**

### D10. Pick'em apps: PrizePicks, Underdog, Sleeper, DraftKings Pick6, ParlayPlay, Betr, Dabble, Chalkboard
- **robots.txt results:**
  - api.prizepicks.com/robots.txt: 403.
  - www.prizepicks.com: `Disallow:` (empty).
  - Underdog: redirects to underdogsports.com, robots.txt has a sitemap only.
  - Sleeper: `Allow: /`.
  - Pick6: `Disallow: /embed/`.
  - **ParlayPlay: `User-agent: * Disallow: /`.**
  - Betr: `Disallow: /api/`.
  - Dabble: `Disallow:` (empty).
  - Chalkboard: `Allow: /`.
- **Not done:** I fetched no projection or line data from any of them. I did not verify whether any lists team-level lines today.
- **Verdict: not usable / not checked further.** These are player-prop pick'em products, their API hosts are blocked or disallowed, and none is a sportsbook team-total source.

### Searches run, category D
- WebSearch:
  - "NBA implied team totals tonight page dailyfantasyfuel OR rotogrinders OR rotowire OR fantasypros"
  - "NHL implied team totals goals tonight leftwinglock OR dailyfaceoff OR rotowire"
  - "NFL implied team totals week 11 2026 fantasypros OR rotogrinders OR establishtherun OR linestar"
- Sites tried directly: robots.txt for all 22 D candidates. Pages: dailyfantasyfuel.com/nba/projections/, basketballmonster.com/nbagames.aspx, leftwinglock.com, rotowire.com/basketball/nba-lineups.php, dailyfaceoff.com. Terms pages: DFF, LWL, RotoWire, FantasyPros, RotoGrinders (404) and Daily Faceoff (404).
- Leads from search that I did not fetch and that are outside my categories: eatdrinkandsleepfootball.com "Implied Totals" pages (they say FanDuel lines are used), sharpfootballanalysis.com implied team totals tool, and firstdown.studio.

## What could make these findings wrong
- **Polymarket and Kalshi coverage is a snapshot at about 05:00 UTC.** NBA team totals on Polymarket and EPL extras on Kalshi seemed to be posted later. Absence today ≠ absence at game time.
- **Polymarket `outcomePrices` on thin books are placeholders** (0.5/0.5). Only bid and ask with real liquidity are prices.
- **Polymarket history before the first trade is a flat 0.5.** Treat early points as missing, not as prices.
- **Whether polymarket.com's ToS "Interface/Features" clause covers its own documented public API is a legal reading I can't settle.** The same goes for the US-residency restriction applying to read-only data access.
