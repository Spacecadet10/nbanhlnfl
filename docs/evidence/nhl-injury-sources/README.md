# NHL historical injury sources — feasibility sample (2026-10-11)

PLAYER_RATINGS_STUDY §8 step (1). Question: is there a free historical NHL injury source, and can we prove that each record
was **public before the forecast cutoff** (tip-off − 2 h), not just dated after the fact? Sample only: no ingestion, no
store write, no model change. Raw downloads stay in the session scratchpad (third-party content, not committed).

## Summary

| Source | Coverage | Proof of publication time | Fetchable here | Verdict |
|---|---|---|---|---|
| **ESPN `/nhl/injuries` via Wayback Machine** | captures 2016-06 … 2026; 240–330 capture days/yr 2017–2024, 120 in 2025 | **yes**: archive capture timestamp (content cannot postdate it) | yes (CDX + `id_` raw pages, no key) | **viable point-in-time source**, stale rather than leaky |
| Pro Sports Transactions (injuries / IL filter) | ~2000 → present; study used 2016–2024 (5,397 time-loss events) | **no**: transaction date only (study found reporting date within ±3 days of injury; 90–94% date validity) | **no**: Cloudflare managed challenge, HTTP 403 to curl and WebFetch | retrospective; label/backfill use only, not a cutoff-safe feature |
| NHL Injury Viz (Tableau, LW3H) | 2000-01 … 2025-26, last published 2026-06 | **no**: manually compiled man-games-lost from puckpedia, CBS, capfriendly, TSN | **no**: CSV export behind AWS WAF captcha | retrospective; season-level outcomes only |
| NHL Rumors `/nhl-injuries/` | 324 archive pages × ~12 posts ≈ 3,900 posts, 2009-11 → 2026-10 | **yes**: `<time datetime>` to the second (ET), usually morning roundups | yes | free text, ~1 post/day, incomplete per team; supplementary only |
| Other Wayback pages | Rotowire 2–66 days/yr; Covers 7–102 days/yr; CBS and TSN CDX errored | yes (capture time) | partly | too sparse |

Retrospective records (PST, Injury Viz) say when an injury happened or a transaction was logged. They do **not** show the
information existed at forecast time, so they can't become pre-game features without a timing source. ESPN snapshots can.

## ESPN Wayback: parse, timing, accuracy

`parse_espn.py` handles both layouts: the 2016–2019 HTML table (a feed of recent updates: may list a player several times, and
on 2017-11-15 only 23 teams appear) and the 2020+ `__espnfitt__` JSON (one row per listed player, all 29–32 teams). In the new
layout `date` is ESPN's **estimated return date**, not the report date; the report date is in the comment ("Jan 13: …") when present.

| Snapshot (UTC) | Layout | Rows | Players | Teams | Statuses |
|---|---|---|---|---|---|
| 2017-11-15 10:53 | table | 72 | 50 | 23 | DTD 37, IR 28, Out 7 |
| 2019-01-10 06:07 | table | 144 | 71 | 30 | DTD 74, IR 59, Out 11 |
| 2021-03-02 01:45 | json | 73 | 73 | 29 | IR 43, DTD 15, Out 15 |
| 2022-12-01 14:30 | json | 112 | 112 | 31 | IR 85, DTD 12, Out 15 |
| 2024-01-15 08:37 | json | 117 | 117 | 32 | IR 74, DTD 26, Out 17 |
| 2025-11-12 05:29 | json (gzip) | 82 | 82 | 29 | IR 49, Out 21, DTD 12 |

**Accuracy against who actually dressed** (`validate.py`: newest row per player in a pre-game snapshot vs `rosterSpots` from
api-web play-by-play for that day's regular-season games; name matching is normalised exact match, so a missed match counts as
"sat out" and slightly flatters these rates):

| Day | IR: listed / dressed | Out: listed / dressed | DTD: listed / dressed |
|---|---|---|---|
| 2017-11-15 | 2 / 0 | 3 / 1 | 5 / 1 |
| 2019-01-10 | 27 / 5 | 5 / 0 | 21 / 6 |
| 2022-12-01 | 60 / 1 | 9 / 1 | 9 / 3 |
| 2024-01-15 | 45 / 3 | 10 / 0 | 17 / 4 |
| **Total** | **134 / 9 (93% sat out)** | **27 / 2 (93%)** | **52 / 14 (73%)** |

The 2019 IR misses (5/27) are from the old feed layout, where the newest row can be days old.

**Freshness at the cutoff.** For every regular-season NHL game in the store (read-only, `file:data/nhl.sqlite?mode=ro`):
age of the latest ESPN capture taken **before** tip-off − 2 h. Season = end year.

| Season | Games | ≤ 12 h | ≤ 24 h | ≤ 48 h |
|---|---|---|---|---|
| 2015-16 | 1,230 | 0% | 0% | 0% |
| 2016-17 | 1,230 | 40% | 77% | 84% |
| 2017-18 | 1,271 | 46% | 85% | 97% |
| 2018-19 | 1,271 | 47% | 78% | 92% |
| 2019-20 | 1,082 | 43% | 71% | 90% |
| 2020-21 | 868 | 52% | 79% | 91% |
| 2021-22 | 1,312 | 48% | 81% | 93% |
| 2022-23 | 1,312 | 43% | 76% | 92% |
| 2023-24 | 1,312 | 68% | 95% | 98% |
| 2024-25 | 1,312 | 41% | 72% | 89% |
| 2025-26 | 1,312 | 22% | 26% | 33% |

## What could make this wrong

- **Staleness, not leakage.** Using only captures before the cutoff rules out look-ahead (the archive can't hold content newer
  than its capture time). But a snapshot can be up to a day or more old, so late scratches and game-time decisions are missed.
  Backtests would understate what a live T−2 h feed gives, and train/serve inputs would differ: live has fresh reports.
  A feature must carry snapshot age, and training should be restricted to ages the live feed matches, or report results by age.
- **CDN / page caching.** ESPN may serve a cached page, which makes content older than the capture time. That is safe in
  direction (staler), but it isn't measured.
- **Layout drift.** Two layouts were seen; other variants may exist between samples. Run a full parse with per-capture
  row/team counts and flag captures with < 20 teams.
- **Coverage holes.** 2015-16 has none; 2025-26 is thin (26% ≤ 24 h). The current season needs our own forward collection
  (ESPN injuries are already collected live: `espn_nhl_injuries`, 2026 and 2027 only).
- **Sample size.** The accuracy check covers 4 days and 213 player-listings. It is evidence of usefulness, not a measured
  error rate.
- **Goalies.** Starter identity is a separate, larger effect. It is covered (prospectively) by the goalie snapshots, not by this.

## Recommendation

NHL injuries are **not** forward-only. ESPN-via-Wayback gives a cutoff-safe historical series for 2016-17 … 2024-25 at about
70–95% ≤ 24 h freshness. Next step, if approved: a full CDX pull and parse (~2,600 day-captures, rate-limited, raw cache under
`NBA_DATA_DIR`), per-capture QA, then an NHL availability study with the same pre-registration discipline as `avail_v1`.
PST and Injury Viz only cross-check absences after the fact; they cannot be features.

## Commands (all observed)

- `curl …/hockey/Search/SearchResults.php?…` (PST) → 403, Cloudflare "Just a moment…"; WebFetch → 403.
- `curl "http://web.archive.org/cdx/search/cdx?url=espn.com/nhl/injuries&from=2015&to=2026&output=json&fl=timestamp&filter=statuscode:200"` → 4,527 captures.
- `curl "http://web.archive.org/web/<ts>id_/http://www.espn.com/nhl/injuries"` × 6 → 200.
- `curl https://public.tableau.com/profile/api/single_workbook/NHLinjurydatabase` → metadata (above); `.../NHLInjuryDatabase.csv` → 404 WAF page.
- `curl https://nhlrumors.com/nhl-injuries/page/{50,150,324}/` → 200; oldest post 2009-11-03.
- `curl https://api-web.nhle.com/v1/score/<day>` and `/v1/gamecenter/<id>/play-by-play` for 4 days (39 games).
- `python3 -I parse_espn.py raw/espn_*.html`; `python3 -I validate.py`; freshness query (inline, read-only SQLite).
