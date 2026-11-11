# Notes: categories B (sportsbooks' own pages/feeds) and C (odds comparison sites)

Run 2026-11-11 04:4x–05:1x UTC (2026-11-10 ET). Client: curl, User-Agent `research-check (personal, non-commercial)`, >=1.1 s between requests (helper script sleeps 1.1 s after each call). Every fetch is logged in `samples/<dir>/meta.txt` (URL, UTC time, HTTP status, content type, final URL).

## Verdict list

| Source | Missing markets offered | Verdict |
|---|---|---|
| Covers.com (also serves oddsshark.com and sportsoddshistory.com) | none. Main markets only: NBA/NFL spread/total/ML, NHL ML/total/puck line (2-way), EPL 3-way ML + total. No team totals, no NHL 3-way prices, no corners/BTTS/DC/DNB | Not usable for the missing markets. Robots and terms allow personal use; no scraping clause found |
| ScoresAndOdds | not checked (terms forbid) | Not usable: terms forbid robots/scraping |
| VegasInsider | not checked | Not usable: terms forbid scraping |
| Action Network | not checked | Not usable: terms forbid robots/page-scrape |
| OddsPortal / BetExplorer / Flashscore (Livesport group) | not checked | Not usable: terms forbid "aggregating, scraping" |
| FotMob | not checked | Not usable: footer forbids robots/crawlers |
| Rotowire | not checked | Not usable: terms forbid crawl/scrape |
| SofaScore | not checked | Not usable: robots.txt itself 403 |
| Oddschecker, Oddspedia, OddsJam, FootyStats | not checked | Not usable: terms page behind Cloudflare challenge, so terms can't be checked |
| SportsOddsHistory | not checked | Not usable: 503 (now redirects to covers.com/sportsoddshistory/) |
| Rotogrinders | not checked | Not usable: terms page not found (404 x2). Unverified, so skipped |
| The Odds API, odds-api.io, OddsPapi | n/a | Need a key (HTTP 401 "API key is missing") |
| Caesars Sportsbook | not checked | Not usable: terms forbid robots/scrapers |
| DraftKings, FanDuel | not checked | Not usable: terms pages 403 (earlier today), DK API robots 403 |
| Kambi feed (Unibet/BetRivers) | not checked (see slip below) | Not usable: kambicdn robots.txt returns 429 "No access"; operator terms not verifiable |
| bet365, BetMGM, Betfair, Paddy Power, Sports Interaction | not checked | Not usable: robots.txt request blocked (403 / Cloudflare / maintenance) |
| Pinnacle | n/a | Needs a key (x-api-key, earlier today). Not usable |
| Stake, theScore Bet | not checked | Not usable: terms behind Cloudflare challenge |
| 1xBet, BET99, Betway, William Hill, Fanatics, PROLINE | not checked | Not usable: terms not readable by script (SPA shell / 404 / 500 / no robots / connection failed). Unverified, so skipped |

**Bottom line for B+C:** no free, permitted source found for team totals (NBA/NHL/NFL), NHL 60-minute 3-way, or the soccer markets beyond 1X2 and match total. The one permitted site (Covers) carries main markets only.

**Process slip (disclosed):**
1. ScoresAndOdds: `/nba` and `/nba/more-lines` were fetched before the terms were read. The terms forbid automated access, so both responses were deleted. I only grep-counted words in them first ("1st half" appeared twice, no "team total" or "3-way"). Recorded in `samples/C-scoresandodds/meta.txt`.
2. Kambi: one odds request (`eu-offering-api.kambicdn.com/offering/v2018/ub/listView/basketball/nba.json`) went out before the operator terms were verified. It returned HTTP 200 JSON (24,719 bytes, 14 events). The body was deleted unused, and only the status, size and event count were recorded. This contradicts the earlier 429 result for this host, but the host's own robots.txt still returns 429 "No access" (re-checked). Recorded in `samples/B-kambi/meta.txt`.

## Searches run

WebSearch queries:
1. `sportsbook terms and conditions "automated" "scraping" odds prohibited Caesars Sportsbook terms`: led to the Caesars terms URL.
2. `BetRivers terms and conditions "robot" OR "automated means" OR "scraping"`: no primary BetRivers text found. A third-party clause DB quotes FanDuel's terms: "use any robot, spider, scraper, sniping software or other automated means to access the Service". This is a secondary source.
3. `Unibet OR Kambi terms "automated" "robots" sportsbook site prohibited data extraction`: no primary text found.
4. `unibet.com general terms and conditions "automated" OR "robots" OR "software" prohibited site use Kindred`: no primary text found.
5. `oddspedia terms and conditions scraping automated "oddspedia"`: no primary text found.
6. `oddschecker terms and conditions "scrape" OR "automated" OR "robot" use of website`: no primary English text found. Only scraper-vendor pages came up.

Sites tried directly (robots.txt, then terms, then odds only if both allowed): see the per-candidate blocks below. All have files in `samples/`.

## Category C: odds comparison sites

### C1. Covers.com (also oddsshark.com, sportsoddshistory.com)
- URLs: `https://www.covers.com/sport/basketball/nba/odds`, `/sport/hockey/nhl/odds`, `/sport/football/nfl/odds`, `/sport/soccer/premier-league/odds`. Matchup pages: `/sport/<sport>/<league>/matchup/<id>/odds`. Internal fragments are referenced as `/odds/matchup/filling/nba/<id>/<marketId>/ca/qc`; these were not fetched.
- Found by: known site, listed in the task. `www.oddsshark.com/nba/odds` and `/nhl/odds` now redirect to Covers (final URL `covers.com/sport/...#oddsshark`). `oddsshark.com/terms-use` redirects to `covers.com/terms#oddsshark`. `sportsoddshistory.com` redirects to `covers.com/sportsoddshistory/` and returns 503.
- Access: robots.txt allows `/sport/.../odds`. It disallows `*/SportsbookRedirect`, `/*v2*`, `/sport/*/matchup/*/tab` and others. Terms (`samples/C-oddsshark/terms.html` = covers.com/terms) contain no scraping or robot clause. Key line: "The Website, the Services, and the Content are provided for your non-commercial entertainment and enjoyment ... you may download certain Content ... to a single personal computing device for your use ... you may not distribute, modify, republish, or publicly display any of the Content". No limits were hit.
- Markets checked:
  - NBA matchup page: Spread, Total, Moneyline, plus player props (Points, Rebounds, Assists, 3PM, Turnovers, P+R, P+A, R+A, Blocks, Steals, S+B). **No team totals.**
  - NFL matchup page: Spread/Total/ML plus player props. **No team totals.**
  - NHL matchup page: Moneyline (2-way), Total, Puck line plus player props. **No 3-way.** "3-way" appears only in an FAQ text.
  - EPL list page: bet-type options `moneyline`, `total` only. **No corners/BTTS/DC/DNB.** No EPL fixtures were listed (international break).
- Line + both prices: yes, per book. The served region was Canada/Quebec (`ca/qc`), with books bet365, Sports Interaction, Stake, Betinia, TonyBet, BET99, Rainbet, Betway, Coolbet.
- Freshness: live (page shows live scores and current lines). History: a "Line Movement" modal exists for main markets; not fetched. Early lines: yes, games 1–2 days ahead are listed (Nov 12 NHL games present on Nov 10).
- Worked example (main markets only, not a missing market):
  - CLE @ NY, 7:00PM ET Nov 11 = 2026-11-12T00:00Z (espn:401909967). Covers 388703: Spread CLE +3.5 -105 / NY -3.5 -111. Total o220.5 -115 / u221.5 -108. ML CLE +145 / NY -165. bet365 ML +145/-170.
  - TBL @ BUF, 7:00PM ET Nov 11 = 2026-11-12T00:00Z (nhl:2026020293). Covers 382570: ML TB -125 / BUF +110. Total 6.5 o-115/u+100. Puck line TB -1.5 +195. No 3-way.
  - WSH @ NYG, 8:15PM ET Nov 12 = 2026-11-13T01:15Z (nfl:401873052). Covers 379914: WAS +2.5 -108, total o41.0 -110, ML +118/-135. No team totals.
- Samples: `samples/C-covers/` (nba-odds.html, nhl-odds.html, nfl-odds.html, epl-odds.html, nba-matchup-388703-odds.html, nhl-matchup-382570-odds.html, nfl-matchup-379914-odds.html). `samples/C-covers/terms.html` is a 404 for `/terms-of-use`; the real terms are in `samples/C-oddsshark/terms.html`. Also `samples/C-oddsshark/nba-odds.html` and `nhl-odds.html` (redirected copies) and `samples/C-sportsoddshistory/` (503s).
- Verdict: **not usable for the missing markets.** Permitted for personal use, but it offers none of team totals, NHL 3-way, or the soccer side markets.

### C2. ScoresAndOdds: `https://www.scoresandodds.com/`
- Robots: `Disallow: /app` only. Terms (`/terms-of-service`, saved): "Use any robot, spider, or other automatic device, process, or means to access the Website for any purpose, including monitoring or copying any of the material". Also: "Users of the Service may not engage in unauthorized spidering, 'scraping,' data mining".
- Markets: not assessed. The odds pages were fetched before the terms were read, then deleted (see the slip above).
- Sample: `samples/C-scoresandodds/` (robots, terms, meta with note). No matching game is offered in the samples.
- Verdict: **not usable** (terms forbid).

### C3. VegasInsider: `https://www.vegasinsider.com/`
- Robots allow paths without `?`. Terms (`/terms-of-use/`, saved): "Users of the Service may not engage in unauthorized spidering, 'scraping,' data mining or harvesting of Content, or use any other unauthorized automated means to gather data".
- No odds were fetched. Verdict: **not usable.**

### C4. Action Network: `https://www.actionnetwork.com/`
- Robots (today, 200) disallow `/*?*` and others. Earlier today a previous run got a CloudFront 403 for robots on www and api. Terms (`/terms`, saved): "You may not use any 'deep-link', 'page-scrape', 'robot', 'spider' or other automatic device ... to access, acquire, copy or monitor any portion of the Site".
- No odds were fetched. Verdict: **not usable.**

### C5. OddsPortal: `https://www.oddsportal.com/`
- Robots disallow `*/feed/*`, `*/match-event/*`, `*/ajax-*`, and all `*-20xx*` archive paths. Terms (`/terms/`, saved) 2.11: "you are not permitted to use our content available on the Website by embedding, aggregating, scraping or recreating it without our express consent".
- Historical soccer odds (incl. some O/U, AH, BTTS, DC, DNB) are known to exist there, but nothing was fetched. Verdict: **not usable** (terms forbid; archive paths also robots-disallowed).

### C6. BetExplorer: `https://www.betexplorer.com/`
- Robots allow most pages. Terms (`/terms-of-service.php`, saved) use the same clause: "not permitted to use our content ... by embedding, aggregating, scraping or recreating it without our express consent". The homepage was fetched only to find the terms link.
- Verdict: **not usable.**

### C7. Flashscore / Livesport: `https://www.flashscore.com/`
- Terms (`/terms-of-use/`, saved): "you are not permitted to use the content of the website by embedding, aggregating, scraping or recreating it without our express consent".
- Verdict: **not usable.**

### C8. FotMob: `https://www.fotmob.com/`
- Robots are long (saved). `/terms` redirected to the homepage, whose footer says: "The use of automatic services (robots, crawler, indexing etc.) as well as other methods for systematic or regular use is not permitted."
- Verdict: **not usable.**

### C9. SofaScore: `https://www.sofascore.com/`, `https://api.sofascore.com/`
- robots.txt on both hosts returns 403 JSON `{"error": {"code": 403, "reason": "Forbidden" }}`.
- Verdict: **not usable** (blocked; not circumvented).

### C10. Oddschecker: `https://www.oddschecker.com/`
- Robots (saved) disallow `*/view-all-markets/*`, `/ajax-page/`, `/*/bet-history/*`. The terms page `/terms-and-conditions` returned a Cloudflare "Attention Required!" page (403), so the terms can't be checked. Web search found no primary English clause.
- Verdict: **not usable** (terms unverifiable behind a challenge; not circumvented).

### C11. Oddspedia: `https://oddspedia.com/`
- Robots (saved) notably contain `Allow: /api/v1/*`. The terms page returned a Cloudflare "Just a moment..." challenge (403), so the terms can't be checked. Web search found no primary terms text.
- No odds were fetched. Verdict: **not usable as is.** It is the only site with an explicit robots allowance for an API path. It needs a human to read the terms in a browser before anyone touches `/api/v1/`.

### C12. OddsJam: `https://oddsjam.com/`
- Robots disallow `/api/backend/*`. Terms: Cloudflare challenge (403). It is also a paid product.
- Verdict: **not usable.**

### C13. Rotowire: `https://www.rotowire.com/`
- Terms (`/termsandconditions.php`, saved): "use manual or automated software, devices, or other processes to 'crawl,' 'scrape,' or 'spider' any of the Roto Sports Network website pages ... Article VI (Automated Access; No Scraping; Rate Limits and Access Controls)".
- Verdict: **not usable.**

### C14. Rotogrinders: `https://rotogrinders.com/`
- Robots disallow `/api/`. `/terms` and `/pages/terms-of-use` both returned 404, so the terms were not found.
- Verdict: **not usable** (terms unverified; skipped).

### C15. SportsOddsHistory: `https://www.sportsoddshistory.com/`
- Redirects to `https://www.covers.com/sportsoddshistory/`, which returned 503 on 3 tries.
- Verdict: **not usable today** (down). Its historical content was main lines and futures in any case. Not verified today.

### C16. The Odds API / odds-api.io / OddsPapi
- `https://api.the-odds-api.com/v4/sports` returned 401 `{"message":"API key is missing","error_code":"MISSING_KEY"...}`.
- `https://api.odds-api.io/v3/events?sport=football` returned 401 `{"error":"You need to provide a valid apiKey"}`.
- `https://api.oddspapi.io/v4/sports` returned 401 `{"error":{"message":"Missing API key"...}}`.
- Verdict: **needs key/account**, so excluded. Samples are in `samples/C-theoddsapi/`, `C-oddsapi-io/`, `C-oddspapi/`.

### C17. FootyStats (`footystats.org`)
- Robots (saved) block ClaudeBot and others. Terms: Cloudflare challenge (403). Its API needs a key.
- Verdict: **not usable.**

### (aside) football-data.co.uk
- Only robots.txt was fetched: `User-agent: *` / `Disallow:` (allowed). It blocks GPTBot, Anthropic-AI, ClaudeBot and others. Not my category, and nothing else was fetched. This is a lead for whoever owns historical CSVs: it is known to carry corners counts and main-market odds, but not corner lines. **Not verified today.**

## Category B: sportsbooks' own pages and feeds

All B candidates stopped at robots or terms. **No odds were fetched from any sportsbook** except the single Kambi slip disclosed above.

| Book | robots.txt (today) | Terms | Verdict |
|---|---|---|---|
| DraftKings | `sportsbook.draftkings.com` 200, minimal disallows. `sportsbook-nash.draftkings.com` (API) 403 Access Denied (cited from earlier run) | 403 to automated read (earlier today, cited) | not usable |
| FanDuel | 200, disallows `/basketball/nba$/`, `/ice-hockey/nhl$/`, `/football/nfl$/` | 403 earlier today. A secondary source (conductatlas, via WebSearch) quotes: "use any robot, spider, scraper, sniping software or other automated means to access the Service" | not usable |
| Caesars | 200 `Allow: /` | NV terms (saved): "Use any robot, spider, scraper or other automated means to access the Company Materials for any purpose without our express written permission" | not usable (terms forbid) |
| BetMGM | 403 (`sports.betmgm.com`) | not reached | not usable (blocked) |
| BetRivers (Kambi) | `www.betrivers.com` 200 `Disallow:` (allow all) | `/terms-and-conditions` 404. Terms are JS-only and not found by search | not usable (terms unverified) |
| Unibet (Kambi) | 200, only `/kwp-*` disallowed | `/general-terms-and-conditions` 404. Not found by search | not usable (terms unverified) |
| Kambi offering API | `eu-offering-api.kambicdn.com/robots.txt` 429 "No access" (re-checked today) | n/a | not usable (see slip) |
| bet365 | 403 Cloudflare "Attention Required!" | not reached | not usable |
| Pinnacle | 200, disallows `*/account/*`, `*/search/*` | terms URL redirected to an SPA shell. Guest API needs `x-api-key` (earlier today) | needs key |
| Bovada | `disallow:` (empty), cited | Terms 6.4 forbid bots (earlier today, cited) | not usable |
| Fanatics | 404 (no robots) | not checked | not usable (unverified) |
| theScore Bet | 200, disallows account paths | `/terms-of-use` Cloudflare "Just a moment..." | not usable |
| William Hill | 200, disallows `/*results/`, `/fragments/eventEntity/` | `williamhill.com/terms` HTTP 500 | not usable (unverified) |
| Betfair | 403 | not reached | not usable |
| Paddy Power | 403 | not reached | not usable |
| 1xBet | 200, many disallows | `/en/information/rules` 200, but the rules text is not in the HTML (no clause found by grep). Unverified | not usable (unverified; offshore) |
| Stake | 200 (15 KB) | `/policies/terms` Cloudflare challenge | not usable |
| BET99 (Ontario/Canada) | 200 `Allow: /` with account disallows | terms page is a 5.7 KB SPA shell with no text | not usable (unverified) |
| Sports Interaction (Canada) | 403 "Maintenance" | not reached | not usable |
| Betway | 200, disallows `/*/sports/event/`, `/*/evt/`, `/*/dyn/` (event pages disallowed) | `/en/terms-and-conditions` 404 | not usable (event pages robots-disallowed) |
| PROLINE (OLG, Ontario) | curl failed (status 000, no response) | not reached | not usable |

Samples: `samples/B-<name>/robots-*.txt`, `terms.html` where fetched, and `meta.txt` for each.

Worked examples: none. No sportsbook source was permitted, so no reference-game odds were taken.

## What could make this wrong
- Terms marked "unverified" (BetRivers, Unibet, William Hill, BET99, 1xBet, Fanatics, Oddschecker, Oddspedia) may in fact allow access. I could not read them without a browser or a challenge bypass, so they stay excluded. A human reading them in a browser could change the verdict. Oddspedia is the most promising because of its `Allow: /api/v1/*` robots line.
- Covers' served region was Canada/Quebec. US-region matchup pages might list different props, possibly team totals. I could not test a US view without geo-spoofing, which is not allowed.
