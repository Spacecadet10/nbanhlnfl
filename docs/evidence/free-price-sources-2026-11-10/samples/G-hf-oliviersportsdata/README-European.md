---
license: other
license_name: single-commercial-user
license_link: https://sportsdataolivier.gumroad.com/l/uooqdc?utm_source=huggingface&utm_medium=dataset&utm_campaign=big6_premium&utm_content=license_link
pretty_name: Timestamped Football Odds incl. Pinnacle — 12 European Leagues (sample)
language:
  - en
tags:
  - sports
  - football
  - soccer
  - betting
  - odds
  - timestamped
  - in-running
  - pinnacle
  - premier-league
  - la-liga
  - serie-a
  - bundesliga
  - ligue-1
size_categories:
  - 10K<n<100K
task_categories:
  - tabular-regression
  - tabular-classification
  - time-series-forecasting
configs:
  - config_name: odds_timestamped
    default: true
    data_files:
      - split: train
        path: odds_timestamped/*.csv.gz
  - config_name: matches
    data_files:
      - split: train
        path: Big6_Matches.csv
  - config_name: opening_closing
    data_files:
      - split: train
        path: Big6_Opening_Closing_Odds.csv
  - config_name: team_stats
    data_files:
      - split: train
        path: Big6_Team_Stats.csv
  - config_name: lineups
    data_files:
      - split: train
        path: lineups/*.csv.gz
  - config_name: events
    data_files:
      - split: train
        path: events/*.csv.gz
  - config_name: minute_stats
    data_files:
      - split: train
        path: minute_stats/*.csv.gz
---

# ⚽ Timestamped Football Odds incl. Pinnacle — Premier League, La Liga, Serie A, Bundesliga, Ligue 1 + 7 More (free sample)

**Free sample: 20 matches out of 45,554.** Same files, same columns, same formats as the full
product. Only the number of matches differs.

Most football odds datasets give you one closing number per match. The full product gives you
**87,379,474 timestamped quotes** from 5 named bookmakers, pre-match and in-play, with the score
and the match clock on the in-play quotes, plus the opening and the closing of every book and
market with the exact time each was recorded.

⚽ **[→ Get the full dataset on Gumroad](https://sportsdataolivier.gumroad.com/l/uooqdc?utm_source=huggingface&utm_medium=dataset&utm_campaign=big6_premium&utm_content=card_top)** — 45,554 matches, 12 competitions, 2016–2026.

---

## What's in this sample

| Table | Sample | Full product |
|---|---:|---:|
| Odds_Timestamped | 49,549 | 87,379,474 |
| Minute_Stats | 12,127 | 11,533,683 |
| Lineups | 834 | 1,775,335 |
| Events | 1,232 | 1,547,381 |
| Team_Stats | 40 | 91,108 |
| Opening_Closing | 20 | 45,554 |
| Matches | 20 | 45,554 |

The 20 matches were picked to show the edges, not the easy cases:

- **12 last-matchday games of 2025/26**, one per competition
- **3 play-offs**: two settled in extra time (Southampton – Middlesbrough, Paderborn – Wolfsburg),
  one straight to penalties after 90 minutes (St Etienne – Rodez, 7-6)
- **5 games from 15 September 2026**, the season where Pinnacle in-play quotes are dense

England, Spain, Italy, Germany, France and Portugal, top two divisions each:
Premier League, Championship, La Liga, Segunda Division, Serie A, Serie B, Bundesliga,
2. Bundesliga, Ligue 1, Ligue 2, Primeira Liga, Liga Portugal 2.

---

## Book coverage (full product)

Coverage is not uniform. Here is what each book actually priced.

| Book | Quotes | Matches with a FT 1X2 close | From | Closing prices on |
|---|---:|---:|---|---|
| Bet365 | 59,584,092 | 45,497 (99.9 %) | 2016/17 | all 8 markets |
| Pinnacle | 5,284,952 | 34,004 (74.6 %) | 2018/19 | 1X2, Asian handicap, total goals |
| Unibet | 6,031,334 | 41,556 (91.2 %) | 2017/18 | 1X2, total goals |
| Betfair (Sportsbook) | 14,334,133 | 8,699 (19.1 %) | 2023/24 | 1X2 |
| Betsson | 2,144,963 | 17,226 (37.8 %) | 2022/23 | 1X2, total goals |

Split: **59,981,836 in-play** (68.6 %) · **27,397,638 pre-match** (31.4 %).

Markets: full-time 1X2, Asian handicap, total goals and total corners, and the same four for the
first half. Bookmakers are always named, never aggregated.

---

## Said plainly

Stated up front rather than discovered after paying.

- **Pinnacle starts in 2018/19, Betsson in 2022/23, Betfair** (Sportsbook, 1X2 only) **in 2023/24.**
  Coverage dips for Pinnacle in 2023/24 (69 %) and for Betfair in 2024/25 (24 %). The
  season-by-season table is in `Big6_README.md`.
- **A closing quote is the last pre-match price recorded.** Median gap to kickoff on FT 1X2:
  Pinnacle 3 min, Betfair 6 min, Betsson 8 min, Bet365 24 min, Unibet 53 min. That is why every
  book has its own closing time.
- **Bet365 prices all 8 markets**, but its full-time corner market is regular only from 2019/20
  and its first-half handicap from 2020/21. Pinnacle in-play quotes are numerous only in 2026/27.
- **The first matchdays of 2016/17 are missing** (data starts 28 Aug 2016, 9 Sep for the
  Championship, Ligue 2 and 2. Bundesliga). Formations start in 2019/20, minute-by-minute stats
  are complete from 2020/21.
- **17 source events were removed** (postponed, abandoned, duplicated or reversed fixtures), each
  listed with its evidence in the README.
- **Odds are UTC and come from one provider.** No cross-provider reconciliation is claimed.

---

## What in-play looks like, in this sample

Elche 2-3 Real Madrid, La Liga, 15 September 2026. Bet365 full-time 1X2, in-play, around each
goal. Every row is in `odds_timestamped/Big6_Odds_Timestamped_ESP_LL.csv.gz`.

| Timestamp (UTC) | Min | Score | Elche | Draw | Real Madrid |
|---|---:|---|---:|---:|---:|
| 2026-09-15T19:35:23Z | 3 | 0-0 | 12 | 7 | 1.181 |
| 2026-09-15T19:57:09Z | 25 | 0-1 | 29 | 12 | 1.055 |
| 2026-09-15T20:04:30Z | 32 | 0-1 | suspended | | |
| 2026-09-15T20:04:43Z | 32 | 0-2 | 51 | 23 | 1.015 |
| 2026-09-15T20:13:01Z | 41 | 0-3 | suspended | | |
| 2026-09-15T20:13:34Z | 41 | 0-2 | 51 | 23 | 1.015 |
| 2026-09-15T21:02:29Z | 70 | 1-2 | 41 | 9 | 1.071 |
| 2026-09-15T21:14:20Z | 82 | 2-2 | 12 | 1.909 | 2.2 |
| 2026-09-15T21:22:12Z | 90 | 2-2 | 17 | 1.25 | 4.75 |
| 2026-09-15T21:22:24Z | 90 | 2-3 | 251 | 13 | 1.035 |

At 41' the score reads 0-3 while the market is suspended, then goes back to 0-2 and the prices
reopen unchanged. Real Madrid's third goal only came at 90'. 216 in-play Bet365 1X2 quotes on
this match, and 1,914 Pinnacle quotes across its markets.

```python
import pandas as pd

o = pd.read_csv("odds_timestamped/Big6_Odds_Timestamped_ESP_LL.csv.gz", dtype={"Match_ID": str})
live = o[(o.Match_ID == "13086922") & (o.Book == "Bet365") &
         (o.Market == "FT_1X2") & (o.Phase == "In-play")]
print(live[["Timestamp_UTC", "Minute", "Score_Home", "Score_Away",
            "Price_Home", "Price_Draw", "Price_Away", "Suspended"]].to_string())
```

---

## Seven tables, one key

Everything joins on `Match_ID`. No fuzzy name matching.

| File | One row per | Key content |
|---|---|---|
| `Big6_Matches.csv` / `.xlsx` | match | kickoff, season, stage, scores 90' / AET / penalties, formations, managers, referee, venue |
| `Big6_Opening_Closing_Odds.csv` / `.xlsx` | match | open and close per book and market, each with its time |
| `odds_timestamped/` (12 × `.csv.gz`) | price update | book, market, phase, prices, line, score, clock, suspension, UTC, epoch |
| `Big6_Team_Stats.csv` / `.xlsx` | match × team | 29 metrics incl. xG |
| `lineups/` (12 × `.csv.gz`) | player × match | starters, subs, shirt, position |
| `events/` (12 × `.csv.gz`) | event | 13 event types with minute and player |
| `minute_stats/` (12 × `.csv.gz`) | stat × minute | running stats minute by minute |

The complete column dictionary, the coverage tables and the integrity notes are in
`Big6_README.md`, the README shipped with the product.

**Nothing a spreadsheet can reinterpret.** Scores, minutes and shoot-outs are separate integer
columns. Timestamps are full ISO 8601 with the T and the Z, plus an epoch. Formations are digits
(4231 = 4-2-3-1), so 4-4-2 never becomes 4 April 2002. Every cell was audited against Excel
auto-conversion patterns at write time, and every table was opened in LibreOffice in English and
French locales: zero conversions. A closing price is never an in-play price.

---

## Licence

Sample: free to explore and evaluate.
Full product: single commercial user. Use the data as you wish, including the models, predictions and products you sell.
Redistribution and resale of the files, or of the raw data in another format, are not permitted.

A question before buying: contact@oliviersportsdata.com

---

👉 ⚽ **[→ Get the full dataset on Gumroad](https://sportsdataolivier.gumroad.com/l/uooqdc?utm_source=huggingface&utm_medium=dataset&utm_campaign=big6_premium&utm_content=card_bottom)** 👈

### Only need one country?

Each country is also sold on its own: both divisions, same tables, same columns, same 5 bookmakers incl. Pinnacle.

| Country | Competitions | Matches | Timestamped quotes | Full dataset |
|---|---|---:|---:|---|
| England | Premier League + Championship | 9,427 | 18,385,493 | [Gumroad →](https://sportsdataolivier.gumroad.com/l/nlmoz?utm_source=huggingface&utm_medium=dataset&utm_campaign=england_premium&utm_content=card_country) |
| Spain | La Liga + Segunda Division | 8,581 | 17,711,160 | [Gumroad →](https://sportsdataolivier.gumroad.com/l/gtcdde?utm_source=huggingface&utm_medium=dataset&utm_campaign=spain_premium&utm_content=card_country) |
| Italy | Serie A + Serie B | 7,899 | 15,415,836 | [Gumroad →](https://sportsdataolivier.gumroad.com/l/cbpiiw?utm_source=huggingface&utm_medium=dataset&utm_campaign=italy_premium&utm_content=card_country) |
| Germany | Bundesliga + 2. Bundesliga | 6,215 | 11,830,048 | [Gumroad →](https://sportsdataolivier.gumroad.com/l/fexff?utm_source=huggingface&utm_medium=dataset&utm_campaign=germany_premium&utm_content=card_country) |
| France | Ligue 1 + Ligue 2 | 7,097 | 12,545,106 | [Gumroad →](https://sportsdataolivier.gumroad.com/l/hsimne?utm_source=huggingface&utm_medium=dataset&utm_campaign=france_premium&utm_content=card_country) |
| Portugal | Primeira Liga + Liga Portugal 2 | 6,335 | 11,491,831 | [Gumroad →](https://sportsdataolivier.gumroad.com/l/bzuul?utm_source=huggingface&utm_medium=dataset&utm_campaign=portugal_premium&utm_content=card_country) |
| **All 6: Big 6 Europe Premium** | **12 competitions** | **45,554** | **87,379,474** | **[Gumroad →](https://sportsdataolivier.gumroad.com/l/uooqdc?utm_source=huggingface&utm_medium=dataset&utm_campaign=big6_premium&utm_content=card_pack)** |

*Olivier Sports Data — From Chicken Area 🐔*
Hand-built historical sports market data.
