# Free price sources for the markets we cannot price — research, 2026-11-10/11

Research only: nothing in the app changed, nothing released, nothing sent. Fetched 2026-11-11 ~04:30–05:20 UTC.
Rules: free, no account, no sign-up, no key (only The Rundown key we already hold), public data only, robots.txt and terms
checked, one request per second at most, honest User-Agent. A "yes" below means a real response fetched today is saved
under `samples/`. Detailed notes per category: `README-part-A.md` (ESPN, The Rundown), `notes-B-C.md`, `notes-D-E.md`,
`notes-F-G-H.md`. Real upcoming games used for matching: `reference-games.txt` (from our stores).

## 1. Market × source table

yes = line and both prices, labelled, from a named book · partial = something real but with a stated defect · no = checked, nothing · — = not applicable / not offered.

| Missing market | ESPN DraftKings (scoreboard, core, propBets) | ESPN Bet365 (core odds) | The Rundown (our key) | Covers.com | Kaggle archives (history) | nflverse | Own data: implied / derived |
|---|---|---|---|---|---|---|---|
| NBA home/away team total | no | — | no | no | no | — | partial: implied line only |
| NHL home/away team total | partial: "Excl OT", sides not labelled | — | no | no | no | — | partial: implied line, rough (fixed ±1.5 puck line) |
| NFL home/away team total | partial: lines, **no prices** | — | no | no | — | no | not computed (possible) |
| NHL 60-min 3-way | no (per-period only) | — | **yes, FanDuel only** | no | partial: average closing H/D/A history, books unnamed, last game 2026-10-19 (start date and count not in a saved file; dataset metadata lists NHL 1998-01 → 2026-10) | — | — |
| Soccer goals match total | yes (already used) | yes (goal line incl. quarter lines) | yes (DK, BetMGM, FanDuel, alt lines) | no (no EPL fixtures served today) | partial: main O/U line history, Pinnacle/Bet365/SBOBET/Singbet, 2018-07 → 2026-06 | — | — |
| Soccer home goals | partial: sides not labelled, stale (Nov 4) | no | no | no | no | — | — |
| Soccer away goals | partial: same | no | no | no | no | — | — |
| Both teams to score | partial: yes/no not labelled, stale | no | no | no | no | — | — |
| Corners match total | no | no | no | no | no (paid product only) | — | — |
| Home corners | no | no | no | no | no | — | — |
| Away corners | no | no | no | no | no | — | — |
| Corner handicap | no | no | no | no | no | — | — |
| Double chance | partial: team ref, not "home/draw" labels, stale | **yes** (seen for EPL, Bundesliga, Serie A, Ligue 1, UCL; absent for La Liga, Nations League, CONCACAF — one match each; Europa League not checked) | no | no | no | — | **yes, derived from match result prices** (143 stored matches, all competitions) |
| Draw no bet | no | no | no | no | no | — | **yes, derived from match result prices** |

Skipped because terms/robots forbid or a key/account is needed (not counted above, details in notes): Polymarket (ToS
bans scraping; would otherwise have NFL/NBA team totals and soccer goals/BTTS/corners), Kalshi (Developer Agreement
forbids collecting/storing data; would otherwise have team totals incl. NHL OT/SO, corners, BTTS), DraftKings, FanDuel,
BetMGM, Caesars, bet365 site, Bovada, Pinnacle (key), Kambi/Unibet, Betfair, Smarkets, ScoresAndOdds, VegasInsider,
Action Network, OddsPortal, BetExplorer, Flashscore, FotMob, SofaScore, RotoWire, Left Wing Lock, The Odds API,
odds-api.io, OddsPapi (keys). Robots.txt names AI agents (ClaudeBot/anthropic-ai) → skipped: football-data.co.uk,
NHL API (api-web.nhle.com), NBA.com, Yahoo, BBC, The Athletic, TSN.

## 2. Ranked shortlist (best per market)

1. **Double chance (soccer)** — ESPN Bet365, already on a host we use, labelled; present in 5 of 6 club competitions sampled (one match each; Europa League not checked). Second: our own *derived from match result prices* for every match with stored DraftKings 1X2 (fair prices, never a quote).
2. **Draw no bet (soccer)** — only *derived from match result prices*. No free sportsbook quote found.
3. **NHL 60-min 3-way** — The Rundown market 563, FanDuel, key we hold; add 563 to the existing NHL request (built and reviewed as 7f10f71, NOT released). History for back-checks: Kaggle `archangel0x01/nba-nhl-and-college-basketball-results-and-odds` (with care, see below).
4. **Soccer goals match total, more lines/books** — The Rundown market 3 (three books) and ESPN Bet365 goal line. History: Kaggle `archangel0x01/football-odds-movements-2018-2026` (with care).
5. **Team totals NBA / NHL** — no free sportsbook line+price. Best available: *implied from spread and total* (line only) — see section 5.
6. **Team totals NFL** — ESPN DraftKings lines without prices; *implied from spread and total* possible from stored NFL quotes.

"With care" for Kaggle: downloaded without login today; Kaggle's terms page is JavaScript-only and could not be read by
script (a person must read it before use); books unnamed in the NHL file; data scraped by the uploader from unnamed odds
sites (MIT licence claimed by the uploader, upstream rights unclear); the NBA/NHL file has dates without start times (the
football-movements file has `kickoff_utc` and per-price timestamps).

## 3. No free source at all (stated plainly)

- **Corners: all four markets** (match total, home, away, handicap) — no free line or price, live or historical. The only corner-price history found is a paid product (oliviersportsdata, Bet365 corner open/close; its README says regular Bet365 corners from 2019/20; free sample of 20 matches only). So "no proven skill against the bookmaker" on corners cannot be tested with free data.
- **Soccer home/away team goals and BTTS** — only the ESPN DraftKings props whose sides are not labelled (and stale). Not usable without guessing the side.
- **Draw no bet** — no free sportsbook quote (derived fair prices only).
- **NBA team totals** — nothing beyond the implied line.

## 4. What switching on the top choices would involve (not built)

- **Bet365 double chance via ESPN**: extend the soccer odds parser (`app/sources/espn.py`, `parse_soccer_odds`) to read `bettingOdds.teamOdds.preMatchDoubleChance*` from the core odds endpoint (one extra request per match, or per competition-day via the scoreboard if it carries it — not checked); store as provider "Bet365" in `soccer_odds_quotes` (needs a migration: market `double_chance` not allowed today); fractional → decimal; no timestamp in the feed, so store `retrieved_at` only.
- **Derived DC/DNB**: a read view computing fair prices from the stored DraftKings 1X2 of one retrieval; label "derived from match result prices"; never written as a quote; never a ticket price unless you decide so.
- **NHL 3-way (The Rundown 563)**: release 7f10f71 after fixing its open review MEDIUM (a refused 563 request must fall back to the core NHL request so NHL/NFL prices are never lost). No extra requests; in the one combined request saved, the 3-way prices were 18 of 126 odds datapoints.
- **Implied team totals**: a read view pairing the stored DraftKings spread and total of the same response (`fetch_id`) — line only, labelled "implied from spread and total", for NBA (and NFL); NHL only with a warning (puck line is fixed ±1.5).
- **Kaggle histories** (after a person reads Kaggle's terms): one-off download into a research copy, never into `data/`; NBA/NHL file: match by date + teams only (no start times); football file: match by kickoff time + teams.

## 5. Implied team totals vs the app (label: "implied from spread and total"; a line, never a price)

Method (`implied/implied-team-totals-summary.json`, rows in `implied/implied-team-totals-rows.jsonl`): settled games with
start in the 30 days before 2026-11-11T04:53Z; the app's primary (non-research) live near-tipoff team-total forecast, the
last one before the start; DraftKings spread + total from the same stored response retrieved at or before the forecast's
`generated_at` (what was known then). home = total/2 − home spread/2, away = total/2 + home spread/2. Pick = the side the
app gave more probability. No price is involved.

| | NBA | NHL |
|---|---|---|
| team-total forecasts compared / games compared (settled games in window) | 292 / 146 (189) | 366 / 183 (205) |
| app line − implied: mean / mean absolute | −1.64 / 2.66 pts | −0.10 / 0.50 goals |
| within 1 point/goal of implied | 34.9% | 94.3% |
| mean absolute error vs actual score: app line / implied at forecast / implied at close | 10.03 / 9.55 / 9.53 | 1.36 / 1.32 / 1.33 |
| app picks vs its own line (W-L) | 142-150 (48.6%, p=0.68) | 188-178 (51.4%, p=0.64) |
| when lines differ, actual landed on the implied side | 166-107 (60.8%, p=0.0004) | 153-100 (60.5%, p=0.001) |
| app pick agreed with implied side / disagreed | 81-53 / 54-85 | 74-40 / 60-79 |

Reading: the implied line was closer to the final score than the app's own team-total line, in both sports. When they
disagree, the market's side won about 61%. NHL caveat: the stored NHL spread is the fixed ±1.5 puck line (spreads seen:
only ±1.5), so the NHL implied line assumes a 1.5-goal margin for every favourite; it still beat the app's line here, but
it is a rough benchmark. What could make this wrong: one 30-day window, two sports tested (p-values not corrected for
that), picks are near coin flips (the app's line is its own fair line), and implied lines are not bettable.

Real vs implied team total (the only real team-total lines found and kept): DraftKings via ESPN, TB @ BUF
2026-11-12T00:00Z (store game nhl:2026020293), "Team Total Goals (Excl OT)". Stored DraftKings spread BUF +1.5, total 6.5
→ implied BUF 2.5, TB 4.0. The DraftKings ladders put BUF between 2.5 (over −190) and 3.5 (over +120), and TB near 3.5
(+105 / −145; TB 2.5 at −225) — roughly half a goal or more from the implied numbers (no calculation saved). Sides were inferred from the price order (not labelled),
regulation only, one game: an illustration, not a measurement. No real NBA or NFL team-total line with prices was found
in a permitted source, so "how far apart they usually are" cannot be measured yet.

## 6. Derived double chance / draw no bet (label: "derived from match result prices"; never a sportsbook quote)

`derived/derived-dc-dnb.csv`: 143 matches with stored DraftKings 1X2 (all three sides from one retrieval), margin removed
proportionally. Competitions: eng.1 20, esp.1 10, ger.1 7, ita.1 10, fra.1 9, uefa.champions 18, uefa.europa 15,
uefa.nations 25, caf.nations_qual 23, concacaf.nations.league 3, fifa.friendly 3. Check against real quotes, Fulham @
Man City (2026-11-21T12:30Z): derived fair DC 1.183 / 2.904 / 1.234 (City-or-draw / Fulham-or-draw / either team);
Bet365 1.111 / 2.75 / 1.182; DraftKings props 1.10 / 2.60 / 1.154 — the real prices sit below fair by the books' margin,
as expected. Derived DNB: City 1.236, Fulham 5.235.

## Process notes (honest record)

- Fetched before terms were read, then deleted unused (only meta.txt kept): ScoresAndOdds (2 pages), Kambi NBA feed (1),
  Kalshi and Polymarket API data, football-data.co.uk, NHL API and Yahoo data (fetched before the AI-agent robots rule was
  applied), two Zenodo API searches (robots disallow /api). After review also deleted: RotoWire, Left Wing Lock, BetExplorer
  and SBR pages (terms forbid scraping/reproduction), Novig files (robots 403, unverified), abetco HF odds (no licence).
- ESPN propBets `lastUpdated` is not reliable UTC (NHL/NFL values are later than the fetch time); "stale (Nov 4)" is
  approximate.
- The Rundown: ≈3,100 of 20,000 daily datapoints used; key never printed or saved (grep of the key over all samples: 0).
- Covers served a Canada/Quebec view; a US view was not tried (would mean faking location).
