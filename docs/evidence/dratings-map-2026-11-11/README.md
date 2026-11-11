# DRatings (dratings.com) — what the app could use for free

Research only: nothing in the app changed, nothing released, nothing sent. All pages fetched 2026-11-11 05:08–05:25 UTC,
public pages only (no login; the Premium page itself was not fetched), one fetch at a time, a 2 s sleep before each
fetch, User-Agent `research-check (personal, non-commercial)`. `fetch-log.txt` lists 275 fetches (UTC time to the second,
HTTP status, bytes, URL, saved file); no logged gap is under 2 s. Not logged / extra: the redirect checks for 7 soccer
URLs (one extra request each, 05:11–05:12) and 4 redirect hops followed inside one fetch (Nations League, World Cup, NHL
ratings, international ratings), which were back-to-back. Only `fetch_details.py` (game pages) is saved as a script; the
other fetches were shell one-liners with the same sleep and User-Agent. Saved pages: `pages/`. Comparison: `compare.py` → `comparison-summary.json`,
`comparison-rows.jsonl`, `timing-check.json`. Parser: `parse.py`.

## 0. Access notes (read first)

- **robots.txt** (`pages/robots.txt`): `User-agent: * Disallow:` (everything allowed, except one plugin file); no rule
  for AI agents; points to `sitemap_index.xml`.
- **Terms / privacy**: none found. Not in the page sitemap (`page-urls.txt`, 112 pages; the post, attachment and category
  sitemaps were not checked) and `/terms-of-service/`,
  `/terms-of-use/`, `/terms/`, `/privacy-policy/`, `/privacy/` all return 404 (saved as `pages/try-*.html`).
- **About** (`pages/about.html`) and **Predictions Disclaimer** (`pages/predictions-disclaimer.html`): nothing about
  automated access or reuse. The footer says "© 2026 DRatings".
- Reading: nothing forbids automated reading, but the content is copyrighted and no reuse licence is given. Fine for
  private research and measurement; showing DRatings numbers inside the app or on tickets would need their permission.
- "Premium" appears only as a menu link; every field below was on a public page without login.

## 1. What the site shows, per sport

| Sport / competition | Predictions | Projected scores | Sportsbook odds shown | History (completed games) | Timing |
|---|---|---|---|---|---|
| NBA | win % per side | points per team + total (list and game page) | yes: moneyline, spread, over/under; 2 groups in the list ("offshore" / "vegas"); game page names 3et, Bovada, BetOnline, YouWager, SportsBetting and South Point, Circa, Wynn, Stations, Westgate, with Open Line, Best Line, No-Vig | yes, win %, best ML/spread, final score, sportsbook and DRatings log loss; back to at least 2020-08 (page 500 = Aug 24–Sep 2 2020; page 700 empty) | one page per calendar date: page 2 = Nov 12 (ET) games; page 2 links to "Games for Dec 25, 2026" (/upcoming/45, not fetched), so games are listed weeks ahead; game page "Updated" ≈1 min before the listed start |
| NHL | win % per side (2-way); goalies | goals per team + total | yes, same groups; puck line ±1½ | yes, back to at least 2021-02 (page 500 = Feb 19–21 2021; page 700 empty) | one page per date; at least 2 days ahead seen (to 2026-11-13T03:07Z; further pages not fetched); updated ≈1 min before listed start |
| NFL | win % per side; quarterbacks | points per team + total | yes, same groups | yes, back to at least 2020-12 (page 100 = Dec 27 2020–Jan 3 2021; page 150 empty) | at least to 2026-11-16 (upcoming/4; it links to /upcoming/5 "Games for Nov 16", not fetched) |
| Premier League | win / draw / win % | goals per team + total | list: two-sided best ML + best O/U; game page: 3-way ML incl. draw (offshore books), O/U | yes, win/draw %, best ML, final goals, log loss; back to at least 2022-08 (page 100 = Aug 7–20 2022; page 150 empty) | at least to 2026-11-22 (~11 days; upcoming/2 links to /upcoming/3, Nov 23, not fetched) |
| La Liga, Bundesliga, Serie A, Ligue 1 | same as Premier League | same | same | yes (completed page 1 seen; depth not probed) | next round (Nov 20–21) |
| Champions League, Europa League | same | same | same | yes (completed page 1 starts 2026-10-21 for Champions League, 2026-10-15 for Europa League) | next matchday (Nov 24 / Nov 26) |
| UEFA Nations League, World Cup, Int'l friendlies | **no current games**: Nations League and World Cup pages have no game table ("Updated 299 days ago" / "117 d"); the friendlies page has only a stale January 2020 table | — | — | — | — |
| International ratings | ratings table last updated 2022-12-31 ("1410 days ago") | — | — | — | stale |
| MLS | win / draw / win %, goals | yes | same as soccer | yes | next match Nov 18 |
| Domestic cups: Copa del Rey, Coppa Italia, DFB-Pokal, EFL Cup, Coupe de France | completed tables only today (no upcoming rows) | — | best ML in completed rows | yes (e.g. Copa del Rey from 2026-01-06; EFL Cup to 2026-10-29) | — |
| CAF Nations qualifiers, CONCACAF Nations League (in our store) | not fetched (no matching menu entry found for these competitions) | | | | |

Extra fields on the **soccer game page** (`pages/epl-game-detail-upcoming.html`, Fulham @ Man City, free): DRatings
probabilities for both teams to score (yes 46.9% / no 53.1%), match goals over/under at 0.5–7.5, each team's goals
over/under (Fulham 0.5–2.5, City 0.5–6.5), exact goals (match, per team, first half), winning margins, correct-score grid,
first-half totals. **No corners on the pages fetched** (one soccer game page, all list pages). NBA and NHL game pages have no props tabs (no team-total or 3-way
probabilities); NHL win % is 2-way only.

**Ratings pages** (`pages/{nba,nhl,nfl}-ratings.html`): per team Overall, Change, SOS, Standard, Inference, Vegas
ratings with ranks; "Updated" 14–16 h before the fetch (one snapshot; likely daily, not proven). International soccer ratings: last updated 2022-12-31.

**Delivery and URL patterns** (plain server-rendered HTML tables; no embedded JSON in the saved pages; the site's
JavaScript was not saved and no network trace was taken, so a background request cannot be ruled out):
- Sport page (upcoming, in progress, completed page 1): `https://www.dratings.com/predictor/<slug>/` with slugs
  `nba-basketball-predictions`, `nhl-hockey-predictions`, `nfl-football-predictions`, `english-premier-league-predictions`,
  `spain-la-liga-predictions`, `german-bundesliga-predictions`, `italy-serie-a-predictions`, `france-ligue-1-predictions`,
  `champions-league-predictions`, `europa-league-predictions`, `mls-soccer-predictions`, `copa-del-rey-predictions`,
  `coppa-italia-predictions`, `dfb-pokal-predictions`, `efl-cup-predictions`, `france-coupe-de-france-predictions`.
- More pages: `/predictor/<slug>/upcoming/<n>` (one calendar date per page, headed "Upcoming Games for <date>"; n counts
  dates, e.g. NBA page 45 = Dec 25) and `/predictor/<slug>/completed/<n>` (16 games per page, newest first). Exact
  start times come from `<time datetime="...Z">` in each row.
- Game page: `/predictor/<slug>/<uuid>` (uuid in each row's link); holds projected scores, final score, all books'
  open/current lines, soccer props, and an "Updated" `<time datetime>`.
- Ratings: `/sports/nba-basketball-ratings/`, `/sports/nhl-hockey-ratings/`, `/sports/nfl-football-ratings/`.

**Start times**: DRatings shows NBA games at the store's scheduled start + 10 min and NHL + 7 min, a fixed shift for
every matched game (two games each did not fit; see §3). The Premier League example matched exactly
(2026-11-21T12:30Z = store match soccer:eng.1:401879233).

## 2. Which missing markets it helps with

All are DRatings **projections** (their model), never sportsbook prices.

| Missing market | DRatings | What exactly |
|---|---|---|
| NBA team totals | partial | projected points per team (no probability, no line) |
| NHL team totals | partial | projected goals per team (no probability) |
| NFL team totals | partial | projected points per team (list page) |
| NHL 3-way regulation | no | 2-way win % only |
| Soccer goals match total | yes (probability) | over/under % at 0.5–7.5 |
| Soccer home goals / away goals | yes (probability) | each team's over/under % and exact-goal % |
| Both teams to score | yes (probability) | yes/no % |
| Double chance | partial | derivable from win/draw/win % (not shown directly) |
| Draw no bet | partial | derivable from win/draw/win % (not shown directly) |
| Corners total / home / away / handicap | no | nothing |

Sportsbook odds on the site: moneyline, spread, over/under (and soccer 3-way ML incl. draw) from offshore and Las Vegas
books; no team totals, no NHL 3-way, no corners, no double chance, no draw no bet.

## 3. Comparison with the app (settled games, start in 2026-10-28T05:00Z … 2026-11-11T05:00Z)

Matching: DRatings time = store start + offset (NBA 10 min, NHL 7 min) and both team names equal after normalising accents
and dots plus an alias list in `compare.py` ("Los Angeles Clippers" = store "LA Clippers"; "Utah Hockey Club" = "Utah
Mammoth"; a no-op Montreal entry). Run: `python3 compare.py ~/Desktop/nba-predictor-sprint1/data` at about 05:26Z on
2026-11-11, read-only (immutable). Re-running later gives different counts (the NBA store had one more final game in the
window afterwards). App = the canonical `game_forecasts` winner row
(latest before the start). DraftKings = latest stored current winner quote before the start (margin removed). DRatings
win % = the completed list; projected scores = the game page.

| | NBA | NHL |
|---|---|---|
| store settled games / matched | 102 / 100 | 93 / 91 |
| matched with an app winner forecast | 93 | 88 |
| app and DRatings pick the same side | 83 (89%) | 72 (82%) |
| app record when they agree | 63-20 (75.9%) | 51-21 (70.8%) |
| when they disagree: app / DRatings | 4-6 / 6-4 (n=10) | 6-10 / 10-6 (n=16) |
| straight-up record: app / DRatings / DraftKings | 67-26 / 69-24 / 69-24 | 57-31 / 61-27 / 55-31 (DK n=86) |
| Brier (lower is better): app / DRatings / DraftKings | 0.193 / 0.183 / 0.176 | 0.218 / 0.211 / 0.213 |
| log loss: app / DRatings | 0.568 / 0.544 | 0.627 / 0.610 |
| DRatings projected team score vs implied (spread+total), mean abs diff | 1.35 pts (200 team-games) | not meaningful* |
| DRatings projected team score vs app team-total line, mean abs diff | 1.94 pts (186) | 0.29 goals (176) |
| mean abs error vs actual team score: DRatings / implied / app line (app line on 186 / 176 team-games, others on 200 / 182) | 9.66 / 9.35 / 9.83 | 1.25 / not meaningful* / 1.34 |

*NHL: every stored DraftKings spread is ±1.5 (puck line), so the "implied" team goals are just total/2 ± 0.75 and say
nothing about the expected margin. The NHL numbers (0.43 and 1.27 in `comparison-summary.json`) are left out.

**Too small to conclude**: the disagreement groups (10 and 16 games) and every difference in this table. Two weeks is
one short window.

**Unequal timing (favours DRatings)**: DRatings' shown numbers are its last update, median 1.0 min (NBA) / 0.7 min (NHL)
before its listed start. The app's forecasts were made a median 88 min (NBA) / 95 min (NHL) before the scheduled start.
DRatings could react to late news the app had not seen. One NHL game (VAN-WPG) was updated 8.4 min after its listed start
(`timing-check.json`). The same applies to DraftKings: its quotes used were a median 6 min (NBA) / 22 min (NHL) before
the start, so the app-vs-DraftKings Brier comparison is also unequal. A fair test would need DRatings numbers saved at the same time as the app's forecast.

Games not matched (DRatings time ≠ store start + offset):
- NBA OKC @ DAL 2026-10-29T01:30Z (DRatings 01:47Z); DAL @ SA 2026-11-05T02:30Z (02:47Z).
- NHL DET @ COL 2026-11-08T02:00Z (02:11Z); MIN @ MTL 2026-11-11T00:00Z (00:22Z).
Also 7 matched NBA and 3 matched NHL games had no app winner forecast before the start (left out of the agree/disagree rows).

Soccer: no comparison. The app's soccer store has 86 forecasts, all for scheduled international matches; none settled.

## 4. What using it would involve (not built)

- A daily fetch (plus one ~2 h before each slate) of the sport pages and each game page, at ≥2 s per request; for NBA+NHL
  about 20–30 pages a day. Save every page whole with its fetch time, never overwrite, and parse into a separate table
  (e.g. `dratings_projections`), keyed to our games by teams + start (offset rule above).
- Shown only as a separate line, labelled "DRatings projection" (win %, projected score, soccer goal props), next to the
  app's own numbers; never as a sportsbook price, never mixed into the app's percentages or tickets.
- A one-off back-fill of the completed pages (16 games per page) would give DRatings' win %
  history to measure against outcomes (NBA: page 500 = 2020 was the deepest non-empty page checked, so 500+ pages);
projected scores need one game page per game.
- First ask DRatings for permission before showing their numbers anywhere (no licence on the site).

## Process notes

- The first attempt at the soccer and two ratings URLs got 301 redirects (0 bytes saved); each new location was then
  fetched once (the soccer redirect check used one extra request per page).
- `pages/detail-nba/` (102 files) and `pages/detail-nhl/` (93 files) are the game pages of the comparison window.

- Not verified: whether anything used sits behind Premium (the Premium page was not fetched); whether the canonical
  `game_forecasts` row is exactly what the app displayed for each game.
- A fresh-context reviewer checked this README against the saved pages; its 17 findings are applied above.
