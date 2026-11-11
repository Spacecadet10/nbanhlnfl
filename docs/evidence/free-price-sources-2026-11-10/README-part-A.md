## A. What we already have (fetched 2026-11-11 UTC)

### ESPN (no key; the app already uses these hosts)
| Endpoint | Providers seen | Missing markets covered | Sample |
|---|---|---|---|
| site.api.espn.com …/{nba,nhl,nfl}/scoreboard | DraftKings only | none (moneyline, spread, total only) | samples/A-espn/espn-{nba,nhl}-scoreboard-20261111.json, espn-nfl-scoreboard-*.json |
| …/summary (pickcenter, odds) NBA, NHL | DraftKings only | none | espn-nba-summary-401909966.json, espn-nhl-summary-401892690.json |
| sports.core.api.espn.com …/odds (NBA) | DraftKings only; no propBets link (propBets URL → 404) | none | espn-core-nba-odds-401909966.json, espn-core-nba-propbets-401909966-all.json (404) |
| core …/odds/100/propBets (NHL, TB @ BUF 2026-11-12T00:00Z) | DraftKings | team totals: **"Team Total Goals (Excl OT)"** — excludes overtime (our NHL team total counts OT+SO), and over/under is **not labelled** (12 entries per team, line + price only). "Will There Be Overtime?" present; **no** full-game 60-min 3-way (only per-period 3-way). | espn-core-nhl-propbets-401892690-all.json |
| core …/odds/100/propBets (NFL, 401873052) | DraftKings | "Team Total Points": **lines only, no prices at all** | espn-core-nfl-propbets-401873052-all.json |
| core …/odds (soccer) | **DraftKings + Bet 365** (eng.1, ger.1 [Bet365 only], ita.1, fra.1, uefa.champions); DraftKings only for esp.1, uefa.nations, concacaf.nations.league | Bet365 `bettingOdds.teamOdds`: 1X2, **double chance (HomeOrDraw / DrawOrAway / HomeOrAway, labelled)**, goal line over/under with its line (e.g. 3.25), fractional prices, no timestamp | espn-core-eng.1-odds-401879233.json (+ esp.1, ger.1, ita.1, fra.1, uefa.champions, uefa.nations, concacaf files) |
| core …/odds/100/propBets (soccer, Fulham @ Man City 2026-11-21T12:30Z) | DraftKings | team total goals, BTTS, double chance present, **but every price sits under the key "over"** (BTTS yes and no both "over"; team goals over and under at 1.5 both "over"); DC entries carry a team ref (= team or draw) / no team (= either team). lastUpdated 2026-11-04 (days old; note ESPN `lastUpdated` is not reliable UTC — NHL/NFL values are later than the fetch time). | espn-core-eng1-propbets-401879233-all.json |

Worked example (Bet365 via ESPN, Fulham @ Manchester City, 2026-11-21T12:30Z = store match soccer:eng.1:401879233): 1X2 21/50, 15/4, 11/2; DC City-or-draw 1/9, Fulham-or-draw 7/4, either team 2/11; goal line 3.25 over 41/40 / under 33/40.

### The Rundown (key already in .env.odds; key never printed or saved — grep of the key over every sample: 0 files)
- Market list (`/api/v2/markets`, 1,062 markets): team_totals 94, 60-min 3-way 563 (NHL), draw_no_bet 330, double_chance 331, both_teams_to_score 954, total corners 899, team total corners 1418, corner handicap 508 (UEFA EURO only), plus core 1/2/3. samples/A-therundown/rundown-markets.json
- What the free plan actually returns (requests for those ids, with the three books and with all books):
  - NHL 563 (60-min 3-way): **yes, FanDuel only**, home/draw/away all priced, `updated_at` per price, 5-minute provider delay. TB @ BUF (2026-11-12T00:00Z): BUF +160, TB +125, Draw +330. rundown-nhl-2026-11-12-m94-563*.json, rundown-nhl-2026-11-12-m1-2-3-563.json
  - Team totals 94: **no** for NBA (2026-11-12, 3 books and all books), NHL (2026-11-12, requested with 563: only 563 came back), NFL (2026-11-13 three books, 2026-11-16 all books). For NBA and NFL the response silently falls back to markets 1/2/3.
  - Soccer 330/331/508/899/954/1418/94: **no** (EPL 2026-11-21 and 2026-11-22, UCL 2026-11-25: only 1/2/3 returned). Soccer goal **match total** (market 3) yes at DraftKings, BetMGM, FanDuel with alternate lines. FIFA (internationals) 2026-11-14: 0 events.
  - History: current prices only (no history endpoint used); our own saved files are the history.
  - Datapoints used today ≈ 3,100 of 20,000 (headers saved in each sample).
