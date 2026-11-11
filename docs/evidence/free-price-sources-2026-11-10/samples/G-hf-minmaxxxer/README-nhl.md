---
license: cc-by-4.0
pretty_name: NHL 2026-27 Odds and Results (Updated Weekly)
language:
- en
tags:
- sports
- nhl
- ice-hockey
- odds
- closing-line
- ats
- sports-analytics
- backtesting
task_categories:
- tabular-classification
- tabular-regression
size_categories:
- n<1K
configs:
- config_name: default
  data_files: nhl_2026_27_odds.csv
---

# NHL 2026-27 odds and results, updated weekly

Every game of the **2026-27 NHL season played so far — 282 regular-season games** (updated every Monday; last update 9 November 2026) — with the betting market's **consensus opening and closing lines** (spread, total, moneyline win probability) and
**every bet already graded**: on the puck line (`ats_*` columns), over/under and moneyline. One row per game, ready for pandas, R, SQL
or a spreadsheet.

Built for backtesting, closing line value (CLV) research and sports-analytics teaching.

## Quick look

| | |
|---|---|
| Games | 282 |
| Home teams covering the closing puck line | 45.4% (pushes: 0) |
| Overs at the closing total | 52.3% (pushes: 1) |
| Closing favourites winning outright | 61.9% |
| Closing home-win probability (average) vs home wins | 53.3% vs 56.7% |
| Bookmakers per game (median) | 24 |

```python
import pandas as pd
df = pd.read_csv("hf://datasets/minmaxxxer/nhl-odds-2026-27/nhl_2026_27_odds.csv")
df.groupby("home_team").ats_result.value_counts().unstack()     # ATS record of every team at home
```

## Columns

| Column | Meaning |
|---|---|
| `game_id` | Stable game ID (Flashscore) |
| `date_et`, `start_time_utc` | Game day in US Eastern time; start in UTC |
| `season_type`, `stage`, `round` | `regular` / `postseason`; `Regular season`, `Play-In` or `Playoffs`; play-off round (`First Round` … `Stanley Cup Final`) |
| `home_team`, `away_team`, `home_team_id`, `away_team_id` | Teams and their stable IDs |
| `home_score`, `away_score`, `overtime` | Final score, overtime and shootout included; `overtime` is true for games decided after regulation |
| `bookmakers` | Bookmaker brands in the moneyline consensus |
| `open_home_win_prob`, `close_home_win_prob` | No-vig home win probability at the opening and at the close (overtime included) |
| `close_fair_home_odds`, `close_fair_away_odds` | Fair (no-vig) decimal moneyline at the close |
| `open_spread_home`, `close_spread_home` | The market's main spread, home handicap (the puck line, -1.5 = home gives 1.5 goals) |
| `close_home_cover_prob` | No-vig probability that the home team covers the closing spread |
| `open_total`, `close_total`, `close_over_prob` | Main total at the opening and the close; no-vig over probability at the close |
| `moneyline_winner`, `favorite`, `favorite_won` | Winner (`home` / `away`; `push` = a tie), closing favourite and whether it won |
| `ats_result`, `ats_margin`, `ats_result_open` | Side that covered the closing spread (`home`/`away`/`push`), home margin + spread, same on the opening spread |
| `total_result`, `total_margin`, `total_result_open` | `over`/`under`/`push` on the closing total, points minus total, same on the opening total |

## How it was made

Opening and latest (= closing, once the game started) odds of every bookmaker that priced the game were collected; each
bookmaker's margin was removed proportionally and the **median across bookmaker brands** taken (a brand listed under several
licences counts once). The main spread and total are the lines most bookmakers quoted closest to 50/50. Bets are graded the way
US books settle them: moneyline, puck line and total on the final score, overtime and shootout included.

## Want more?

This file is a free sample. The **[NBA, NFL, MLB & NHL Betting Odds Actor](https://minmaxxxer.dev/hf-nhl2627)** produces the same data — plus the
**opening and closing price of each of the 24 bookmakers** that priced this season (DraftKings, FanDuel, BetMGM, bet365, Betway …), every alternate
line, team statistics and scores by period — for **any season back to 2009**, any date range, today's games, and the
NBA, NFL, MLB, KHL, AHL and European leagues. Python and API access, CSV / Excel / JSON export, pay per game.

Prefer a plain REST API? The **[Sports Betting Odds & Arbitrage API](https://minmaxxxer.dev/hf-api)** on RapidAPI serves the same games with
every bookmaker's opening and closing odds and graded bets — NBA, NFL, MLB and NHL since 2009, Europe's top soccer
leagues since 2005 — plus this season's games refreshed every hour and arbitrage and value bets on the coming games.
Free plan: 500 requests a month.

Data compiled from publicly available game and odds information; not affiliated with the NHL, Flashscore or any
bookmaker. For research and analysis.
