---
license: other
license_name: single-commercial-user
license_link: https://sportsdataolivier.gumroad.com/l/hethzg?utm_source=huggingface&utm_medium=dataset&utm_campaign=intl_premium&utm_content=license_link
pretty_name: Timestamped Football Odds incl. Pinnacle — International Competitions (sample)
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
  - champions-league
  - world-cup
  - copa-libertadores
  - international
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
        path: Intl_Matches.csv
  - config_name: opening_closing
    data_files:
      - split: train
        path: Intl_Opening_Closing_Odds.csv
  - config_name: team_stats
    data_files:
      - split: train
        path: Intl_Team_Stats.csv
  - config_name: bookmaker_snapshots
    data_files:
      - split: train
        path: bookmaker_snapshots/*.csv.gz
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
dataset_info:
  - config_name: events
    features:
      - name: Match_ID
        dtype: int64
      - name: Event_Order
        dtype: int64
      - name: Minute
        dtype: int64
      - name: Added_Time
        dtype: int64
      - name: Event_Type
        dtype: string
      - name: Type_Ordinal
        dtype: int64
      - name: Team_Side
        dtype: string
      - name: Team
        dtype: string
      - name: Player
        dtype: string
      - name: Player_2
        dtype: string
      - name: Race_Target
        dtype: int64
      - name: Detail
        dtype: string
      - name: Extra_Time
        dtype: int64
  - config_name: minute_stats
    features:
      - name: Match_ID
        dtype: int64
      - name: Side
        dtype: string
      - name: Stat
        dtype: string
      - name: Minute
        dtype: int64
      - name: Value
        dtype: float64
      - name: Timestamp_UTC
        dtype: string
      - name: Epoch
        dtype: int64
---

# ⚽ Timestamped Football Odds incl. Pinnacle — International Competitions: Champions League to World Cup (free sample)

**Free sample: 20 matches out of 11,118.** Same files, same columns, same formats as the full
product. Only the number of matches differs.

Most football odds datasets give you one closing number per match. The full product gives you
**28,943,376 timestamped quotes** from 5 named bookmakers, pre-match and in-play, with the score
and the match clock on the in-play quotes, plus the opening and the closing of every book and
market with the exact time each was recorded, and 661,507 dated snapshots from 37 more bookmakers.

⚽ **[→ Get the full dataset on Gumroad](https://sportsdataolivier.gumroad.com/l/hethzg?utm_source=huggingface&utm_medium=dataset&utm_campaign=intl_premium&utm_content=card_top)** — 11,118 matches, 18 competitions, 2016–2026.

---

## What's in this sample

| Table | Sample | Full product |
|---|---:|---:|
| Odds_Timestamped | 62,829 | 28,943,376 |
| Minute_Stats | 9,485 | 3,037,116 |
| Bookmaker_Snapshots | 1,370 | 661,507 |
| Lineups | 902 | 401,511 |
| Events | 1,171 | 365,177 |
| Team_Stats | 40 | 22,236 |
| Opening_Closing | 20 | 11,118 |
| Matches | 20 | 11,118 |

The 20 matches were picked to show the edges, not the easy cases: the latest final of each of the
18 competitions, plus 2 matches whose kickoff time in the source is wrong. 11 went to extra time,
7 to a penalty shoot-out.

| Competition | Edition | Stage | Match | Result | Note |
|---|---|---|---|---|---|
| UEFA Champions League | 2024/25 | Round of 16, leg 2 | Atletico Madrid – Real Madrid | 1-0 · 1-0 aet · 2-4 pens · agg 2-2 | kickoff in the source: 2025-03-13 05:14 UTC; played 2025-03-12, pre-match cut at the real start |
| UEFA Champions League | 2025/26 | Final | PSG – Arsenal | 1-1 · 1-1 aet · 4-3 pens |  |
| UEFA Europa League | 2025/26 | Final | SC Freiburg – Aston Villa | 0-3 |  |
| UEFA Conference League | 2025/26 | League Phase, matchday 2 | HNK Rijeka – Sparta Prague | 1-0 | kickoff in the source: 2025-10-24 14:00 UTC; played 2025-10-23, pre-match cut at the real start |
| UEFA Conference League | 2025/26 | Final | Crystal Palace – Rayo Vallecano | 1-0 |  |
| UEFA Nations League | 2024/25 | Final | Portugal – Spain | 2-2 · 2-2 aet · 5-3 pens |  |
| UEFA European Championship | 2020 | Final | Italy – England | 1-1 · 1-1 aet · 3-2 pens |  |
| UEFA European Championship | 2024 | Final | Spain – England | 2-1 |  |
| FIFA World Cup | 2018 | Final | France – Croatia | 4-2 | no minute-by-minute stats in 2018; no Pinnacle, Betfair or Betsson for this tournament |
| FIFA World Cup | 2022 | Final | Argentina – France | 2-2 · 3-3 aet · 4-2 pens | the match shown quote by quote below |
| FIFA World Cup | 2026 | Final | Spain – Argentina | 0-0 · 1-0 aet |  |
| FIFA Club World Cup | 2025 | Final | Chelsea – PSG | 3-0 |  |
| Copa Libertadores | 2025 | Final | Palmeiras – Flamengo | 0-1 |  |
| Copa Sudamericana | 2025 | Final | Lanus – Atletico Mineiro | 0-0 · 0-0 aet · 5-4 pens |  |
| Copa America | 2024 | Final | Argentina – Colombia | 0-0 · 1-0 aet |  |
| CONCACAF Champions Cup | 2026 | Final | Toluca – Tigres UANL | 0-0 · 1-1 aet · 6-5 pens |  |
| AFC Champions League | 2025/26 | Final | Al Ahli Jeddah – Machida Zelvia | 0-0 · 1-0 aet |  |
| AFC Asian Cup | 2023 | Final | Jordan – Qatar | 1-3 |  |
| CAF Champions League | 2025/26 | Final, leg 2 | FAR Rabat – Mamelodi Sundowns | 1-1 · agg 1-2 | second leg, decided on aggregate; no lineups in the source for this competition |
| Africa Cup of Nations | 2025 | Final | Senegal – Morocco | 0-0 · 1-0 aet | kept as played: CAF later awarded the final 0-3 to Morocco |

UEFA Champions League, Europa League, Conference League and Nations League, the European Championship
(2020, 2024), the FIFA World Cup (2018, 2022, 2026) and Club World Cup, Copa Libertadores, Copa Sudamericana,
Copa America, CONCACAF Champions Cup, AFC Champions League, AFC Asian Cup, CAF Champions League and
Africa Cup of Nations: 9,644 club matches and 1,474 national-team matches in the full product.

---

## Book coverage (full product)

Coverage is not uniform. Here is what each book actually priced.

| Book | Quotes | Matches with a FT 1X2 close | Closing prices on |
|---|---:|---:|---|
| Bet365 | 20,948,125 | 10,786 (97.0 %) | all 8 markets |
| Pinnacle | 1,601,791 | 8,243 (74.1 %) | 1X2, Asian handicap, total goals |
| Unibet | 1,913,638 | 9,757 (87.8 %) | 1X2, total goals |
| Betfair (Sportsbook) | 3,719,750 | 2,491 (22.4 %) | 1X2 |
| Betsson | 760,072 | 4,218 (37.9 %) | 1X2, total goals |

Split: **21,777,262 in-play** (75.2 %) · **7,166,114 pre-match** (24.8 %).

Markets: full-time 1X2, Asian handicap, total goals and total corners, and the same four for the
first half. Bookmakers are always named, never aggregated. The 37 other bookmakers are in
`Bookmaker_Snapshots`: their first and last pre-match snapshot, each with its time.

---

## Said plainly

Stated up front rather than discovered after paying.

- **Not collected for three tournaments:** UEFA European Championship 2020 has no Betfair or Betsson,
  FIFA World Cup 2018 no Pinnacle, Betfair or Betsson, FIFA World Cup 2022 no Betfair.
- **A closing quote is the last pre-match price recorded.** Median gap to kickoff on FT 1X2:
  Pinnacle 3 min, Betfair 5 min, Betsson 7 min, Bet365 23 min, Unibet 48 min. That is why every
  book has its own closing time.
- **The other bookmakers' table is a dated snapshot, not a closing price:** the source keeps three
  snapshots per book (first price, around kickoff, last price). Check `Minutes_Before_Kickoff`.
- **Lineups are almost absent from the source for the CAF Champions League** (0.1 % of matches).
- **Data starts on 2016-08-31:** Copa Sudamericana 2016, CAF Champions League 2016, CONCACAF
  Champions Cup 2016/17 and AFC Champions League 2016 are partial editions, listed in the README.
- **8 source events were removed** (duplicates, a match of another competition, isolated qualifiers),
  each listed with its evidence in `Intl_README.md`. Results awarded after the match are kept as played.
- **Odds are UTC and come from one provider.** No cross-provider reconciliation is claimed.

---

## What in-play looks like, in this sample

Argentina 2-2 France, FIFA World Cup final, 18 December 2022. Bet365 full-time 1X2 (the 90-minute
market), in-play, around each goal. Every row is in `odds_timestamped/Intl_Odds_Timestamped_WC_2022.csv.gz`.

| Timestamp (UTC) | Min | Score | Argentina | Draw | France |
|---|---:|---|---:|---:|---:|
| 2022-12-18T15:05:38Z |  | 0-0 | 2.625 | 3.1 | 2.875 |
| 2022-12-18T15:22:32Z | 17 | 0-0 | suspended | | |
| 2022-12-18T15:23:15Z | 18 | 1-0 | 1.5 | 3.75 | 8 |
| 2022-12-18T15:35:33Z | 35 | 1-0 | suspended | | |
| 2022-12-18T15:35:57Z | 35 | 2-0 | 1.083 | 9.5 | 29 |
| 2022-12-18T16:42:10Z | 79 | 2-0 | suspended | | |
| 2022-12-18T16:42:22Z | 79 | 2-1 | 1.181 | 5 | 41 |
| 2022-12-18T16:43:50Z | 81 | 2-1 | suspended | | |
| 2022-12-18T16:43:58Z | 81 | 2-2 | 3.6 | 1.533 | 8 |

144 in-play Bet365 1X2 quotes on this match alone, and 349 Pinnacle quotes before kickoff. The
Matches row carries the rest of the story: 2-2 after 90 minutes, 3-3 after extra time, 4-2 on
penalties, Neutral_Venue = 1.

```python
import pandas as pd

o = pd.read_csv("odds_timestamped/Intl_Odds_Timestamped_WC_2022.csv.gz", dtype={"Match_ID": str})
live = o[(o.Match_ID == "6025486") & (o.Book == "Bet365") &
         (o.Market == "FT_1X2") & (o.Phase == "In-play")]
print(live[["Timestamp_UTC", "Minute", "Score_Home", "Score_Away",
            "Price_Home", "Price_Draw", "Price_Away", "Suspended"]].to_string())
```

---

## Eight tables, one key

Everything joins on `Match_ID`. No fuzzy name matching.

| File | One row per | Key content |
|---|---|---|
| `Intl_Matches.csv` / `.xlsx` | match | edition, stage, matchday, leg, kickoff, scores 90' / AET / penalties, aggregate, neutral venue, formations, managers, referee, venue |
| `Intl_Opening_Closing_Odds.csv` / `.xlsx` | match | open and close per book and market, each with its time |
| `odds_timestamped/` (18 × `.csv.gz`) | price update | book, market, phase, prices, line, score, clock, suspension, UTC, epoch |
| `bookmaker_snapshots/` (18 × `.csv.gz`) | snapshot | 37 more books: first and last pre-match snapshot, with minutes before kickoff |
| `Intl_Team_Stats.csv` / `.xlsx` | match × team | 29 metrics incl. xG |
| `lineups/` (18 × `.csv.gz`) | player × match | starters, subs, shirt, position |
| `events/` (18 × `.csv.gz`) | event | 13 event types with minute and player |
| `minute_stats/` (18 × `.csv.gz`) | stat × minute | running stats minute by minute |

The complete column dictionary, the editions and stages of every competition, the coverage tables and
the integrity notes are in `Intl_README.md`, the README shipped with the product.

**Nothing a spreadsheet can reinterpret.** Scores, minutes and shoot-outs are separate integer
columns. Timestamps are full ISO 8601 with the T and the Z, plus an epoch. Formations are digits
(4231 = 4-2-3-1), so 4-4-2 never becomes 4 April 2002. Editions read 2016/17 or 2019. Every cell
was audited against Excel auto-conversion patterns at write time: zero risky cells. A closing price
is never an in-play price.

---

## Licence

Sample: free to explore and evaluate.
Full product: single commercial user. Use the data as you wish, including the models, predictions and products you sell.
Redistribution and resale of the files, or of the raw data in another format, are not permitted.

A question before buying: contact@oliviersportsdata.com

---

👉 ⚽ **[→ Get the full dataset on Gumroad](https://sportsdataolivier.gumroad.com/l/hethzg?utm_source=huggingface&utm_medium=dataset&utm_campaign=intl_premium&utm_content=card_bottom)** 👈

---

*Olivier Sports Data — From Chicken Area 🐔*
Hand-built historical sports market data.
