# Free sportsbook prices for unpriced markets — checked 2026-11-10 (ET) / 2026-11-11 (UTC)

Rules: free, no account, no sign-up, no new API key, no payment. Only publicly served data. Sites that block or forbid
automated access were skipped. Every row below comes from a response fetched today and saved in `raw/` (each file has
`_fetched_at`, `_url`, `_status`, selected response headers and the body; the TheRundown key is never saved — checked with
a grep of the key value: 0 files). robots.txt files are in `robots/`.

## Result per market

| Market | Sports | Result | Source / book | Freshness | Line + both prices | History |
|---|---|---|---|---|---|---|
| NHL 3-way regulation result (reg3) | NHL | **PASS — added** | TheRundown market 563 "moneyline_3_way_no_push" (key we already hold), **FanDuel only** (DraftKings/BetMGM not served) | `updated_at` per price, 5-min provider delay (`x-data-delay-seconds: 300`) | home, draw and away prices all present (e.g. TB @ BUF: BUF +160, TB +125, Draw +330) | current only; our own saved files build history |
| Home / away team total | NBA | FAIL | TheRundown market 94 requested (3 books and all books): response had no market 94 at all | — | — | — |
| Home / away team total | NFL | FAIL | TheRundown 94: none. ESPN core propBets "Team Total Points" (DraftKings): lines only, **no prices** | — | no price | — |
| Home / away team total | NHL | FAIL | TheRundown 94: none. ESPN core propBets "Team Total Goals (Excl OT)" (DraftKings): **excludes overtime** (our NHL team total counts OT and shootout) and the entries have **no over/under label** | updated 22:36Z | line + price, side not labelled | open + current |
| Soccer double chance / draw no bet | club | FAIL | TheRundown 331 / 330 requested for EPL Nov 21: none returned | — | — | — |
| Soccer corners | club | FAIL | TheRundown 899 requested: none returned. ESPN: no corners (already known) | — | — | — |
| Soccer goal totals | club (EPL seen) | Data present, **not added this session** | TheRundown market 3 at DraftKings, BetMGM, FanDuel (several lines) | updated_at per price | yes | current only |
| Soccer, internationals | FIFA | none listed | TheRundown FIFA (18) Nov 14: 0 events | — | — | — |

Soccer goal totals: the app already shows DraftKings goal prices from ESPN. Adding TheRundown for soccer needs a new
team-name match for seven competitions, a soccer path in the sealed soccer-markets code, and ~340 datapoints per
competition-day request (7 competitions x 2 days x 7 runs ≈ 33,000/day > the 20,000/day free allowance). Decision: not
added now; it would need fewer runs or fewer competitions. Recorded in HANDOFF.md.

## Other public sources (step 2)

| Source | Checked | Outcome |
|---|---|---|
| ESPN site scoreboard (NBA/NHL/NFL) | `raw/espn-*-scoreboard-*.json` | DraftKings moneyline, spread, total only |
| ESPN core odds + propBets | `raw/espn-core-*.json` | NHL: team totals excl. OT, no side label; NFL: team totals without prices; NBA: no propBets link (404) |
| Action Network API | robots.txt → 403 "Request blocked" | blocked: skipped |
| Kambi offering CDN (Unibet etc.) | robots.txt → 429 "No access" | blocked: skipped |
| DraftKings sportsbook data host (sportsbook-nash) | 403 | blocked: skipped; terms page also 403 |
| FanDuel sportsbook | terms page 403 | not readable automatically: skipped |
| Bovada | robots.txt allows all, but Terms of Service 6.4: "All actions on the Website must be executed by players via the supplied user interface"; 9.9 no commercial use of content | forbidden by terms: skipped, no odds fetched |
| Pinnacle guest API | needs an `x-api-key` header taken from their site | key required: skipped, not fetched |
| Oddschecker, OddsPortal, Covers, VegasInsider | robots.txt only | HTML pages, not tested for prices (not fetched) |

## Datapoints used today (TheRundown free plan, 20,000/day)
About 1,250 for this check (daily used 569 → 1,824). Adding 563 to the two NHL requests adds no request: the combined
1,2,3,563 request for 7 games cost 140 datapoints; the 3-way prices in it are 18 of its 126 odds datapoints.
