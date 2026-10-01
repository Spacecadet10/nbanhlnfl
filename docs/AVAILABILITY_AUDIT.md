# Player-availability data: feasibility audit (2026-10-01)

Read-only audit. No model, schema, scheduler or dependency was changed; nothing was written to the database. Evidence files are in
`docs/evidence/availability-audit-2026-10-01/` (copied there on 2026-10-01 so reviewers can read them; `data/` is ignored by Git):
`injury_probe.json` (every HEAD result), `parse_check2.json` and `parse_check2.py` (report parsing, name mapping, sanity check),
`player_box_checks.json`. The raw PDFs and season CSVs are not kept in the repository. The injury-report probe is `scripts/probe_availability_sources.py`;
the PDF text checks used `pypdf` in a scratch environment only (it is not a project dependency).

Requests made: about 560 HEAD requests and 5 PDF downloads to `ak-static.cms.nba.com`, and 7 season files from GitHub.

## Conclusions
| Question | Verdict | Why |
|---|---|---|
| **Prospective collection** (save reports from now on, with our own retrieval times) | **Go for a raw-collection pilot only** | Reports are published every 15 minutes, were reachable with 0 network errors, and our own download time needs no assumption. |
| **Historical backtesting** (2019-20 to 2025-26) | **Not yet: conditional** | Files exist on every sampled date and their times look consistent, but coverage is sampled, publication time would be an assumption, and the parser and name mapping are audit-grade only. Four gates below. |

**What "go" means here.** It supports planning a small collector that saves each report unchanged, with the time the response finished
arriving, a content hash and the outcome of every fetch, within a fixed request budget and with failures visible. It does **not**
establish that collection will stay dependable over a season, that reports are parsed correctly, that names map to players, or that
any of this improves forecasts. No model or running experiment changes.

Use of NBA.com material is limited to private, non-commercial use by its terms (docs/DATA_SOURCES.md). Neither verdict covers a product that is sold.

## 1. Official injury reports (`ak-static.cms.nba.com/referee/injury/Injury-Report_<date>_<time>.pdf`)

### Coverage: sampled, not exhaustive
- **Sample:** 10 random game dates per season (seed 20261001) × three report times (1 PM, 5 PM, 8 PM Eastern) = 210 checks.
  That is a small fraction of roughly 28,000 hourly reports in seven seasons.

| Season | Dates with at least one report | Reports found / checked | Missing |
|---|---|---|---|
| 2019-20 | 10 / 10 | 20 / 30 | 1 PM and 8 PM on all five sampled bubble dates (2020-07-31 to 2020-09-25); 5 PM present |
| 2020-21 | 10 / 10 | 30 / 30 | – |
| 2021-22 | 10 / 10 | 30 / 30 | – |
| 2022-23 | 10 / 10 | 30 / 30 | – |
| 2023-24 | 10 / 10 | 30 / 30 | – |
| 2024-25 | 10 / 10 | 30 / 30 | – |
| 2025-26 | 10 / 10 | 30 / 30 | – |

- **Two full days checked exhaustively:** 2023-01-10 has a report for all 24 hours; 2026-03-10 has all 96 quarter-hour reports.
- **Nothing found before our data:** the 5 PM report on 2018-12-01, 2017-12-01 and 2016-12-01 returned 403 (one date each; not proof of absence).
- **Preseason and now:** no 5 PM report on 2025-10-10 or 2026-09-30 (a file does exist for 2026-07-15, in the off-season; its content
  was not read). When 2026-27 reports begin is unknown until they appear.
- The server answers 403 for a file that is not there, so "missing" and "blocked" cannot be told apart.

### File names
- Hourly names (`_05PM`) through 2025-12-21; quarter-hour names (`_05_00PM`) from 2025-12-22 (bisected on the 5 PM report).
- **An hourly file named `05PM` was published at 5:30 PM.** All 176 hourly files sampled were last modified 30.0 to 30.4 minutes after the
  hour in their name; all 24 quarter-hour files 0.1 to 0.2 minutes after. Times in names are Eastern.

### Timestamps and revisions: supporting evidence, with limits
- For three reports read in full, three times agree to within seconds: the time printed in the report, the PDF's creation time, and the
  server's `Last-Modified`. **These may all come from the same publishing process**, so they are consistent rather than independent.
- No sampled file (200 with a `Last-Modified`) was modified later than its publication slot. A file replaced afterwards would normally
  show a later time, and none did; this cannot rule out a replacement that preserved the time.
- **Can a URL be overwritten?** Unknown. Newer files carry a storage version id, which means the storage keeps versions, not that we can see
  them. Corrections appear to come as the next report, but that is an inference.
- **For history we have no retrieval time of our own.** Any backtest would treat "published at the file's time" as an assumption,
  labelled as such, like the existing tipoff + 4 h rule for results. Historical publication evidence and actual retrieval time stay separate.

### Reading the reports (4 reports: 2019-12-01, 2023-01-10, 2025-12-01, 2026-03-01)
- Columns: game date, game time, matchup, team, player ("Last, First"), status (Out, Doubtful, Questionable, Probable, Available), reason.
  The 2019 layout also has a previous-status column. Teams that have not filed show "NOT YET SUBMITTED".
- Player/status entries extracted: 132, 123, 145 and 134. For the three later reports that equals the number of status words on the
  page. For 2019 it is 132 of 157 status words; the extra column also contains status words, so the true miss count is unknown.
- **The team column was not parsed.** Two producers and layouts exist (2019: iTextSharp; 2025-26: GemBox, text comes out in reverse
  order). A production parser must handle both and reconcile every report (entries parsed = entries present).
- Only players with a designation are listed. A player not on the report is not thereby confirmed healthy if the team had not filed.

### Mapping report names to player IDs: 534 entries
| Outcome | Entries | Share |
|---|---|---|
| Matched to a player in the same season's box scores | 510 | 95.5% |
| Matched only in another season's box scores | 17 | 3.2% |
| **Unresolved** | **7** | **1.3%** |
| Name matching more than one player ID | 1 | – |

- Unresolved: Nicolas Claxton, Ivan Rabb, Charles Brown Jr. (2019); Eli John Ndiaye, Nikola Djurisic (2025); Hansen Yang, Thomas Sorber
  (2026). Causes: name variants ("Nicolas" vs "Nic"), a merged first name, and players with no box-score row in any of the seven seasons.
- The 17 other-season matches are players out for the whole season (for example Kevin Durant, Klay Thompson and John Wall in 2019-20,
  Tyrese Haliburton in 2025-26). **These are exactly the players an availability feature most needs**, so matching within a season is not enough.
- The report has names only, no IDs. A stable mapping needs an explicit alias table. **An unmatched name must stay an explicit
  "unresolved" record: never treated as healthy and never dropped.**

### Sanity check of meaning (not predictive validation)
Restricted to players whose team had a game on the report date that tipped off **after** the report was published. Eventual participation
is used here for auditing only and must never be used to rebuild pregame inputs.

| Listed as | Players | Played |
|---|---|---|
| Out | 195 | 1 |
| Doubtful | 3 | 0 |
| Questionable | 23 | 12 |
| Probable | 14 | 14 |
| Available | 31 | 24 |

Excluded: 118 entries whose game had already started when the report came out, and 126 whose team did not play that day. The team was
inferred from the player's nearest box-score row, because the team column was not parsed. An "Out" player who played can be a
legitimate later status change.

### Collection reliability
0 network errors in about 560 requests from this machine; typical file 43 to 87 kB. A change of file-name format happened once
mid-season (2025-12-22), so a collector must keep raw files and tolerate format changes.

## 2. Player box scores (sportsdataverse `espn_nba_player_boxscores`, one CSV per season)
All seven season files (2019-20 to 2025-26) were downloaded and matched their published SHA-256. Checked exhaustively against our
final regular-season, play-in and postseason games:

| Season | Our final games | Both teams present | Both teams with ≥ 5 players with minutes | Team-games whose minutes add up (240, or overtime) | Rows of players who played but lack minutes | Rows without a player ID |
|---|---|---|---|---|---|---|
| 2019-20 | 1,143 | 1,143 | 1,143 | 2,284 / 2,286 | 0 / 24,111 | 0 |
| 2020-21 | 1,171 | 1,171 | 1,165 | 2,327 / 2,342 | 137 / 25,054 | 0 |
| 2021-22 | 1,323 | 1,323 | 1,323 | 2,644 / 2,646 | 0 / 28,051 | 0 |
| 2022-23 | 1,320 | 1,320 | 1,320 | 2,640 / 2,640 | 0 / 27,743 | 0 |
| 2023-24 | 1,319 | 1,319 | 1,319 | 2,635 / 2,638 | 0 / 28,366 | 0 |
| 2024-25 | 1,321 | 1,321 | 1,321 | 2,642 / 2,642 | 83 / 28,348 | 0 |
| 2025-26 | 1,322 | 1,322 | 1,322 | 2,629 / 2,644 | 46 / 28,738 | 33 |

- What this does **not** show: that every player who appeared has a row, or that individual minute values are correct. Only the
  listed checks were run.
- The six 2020-21 games without minutes are the same play-in games that lack team box stats.
- 333 player IDs appear in both 2022-23 and 2025-26, always with the same name (stability checked for that pair of seasons only).
- Format differs between files (2019-20 uses `TRUE/FALSE`, later files `true/false`). Files are rewritten upstream (2021-25 on 2026-08-26).
- The `reason` column on did-not-play rows (for example "RIGHT ANKLE SPRAIN") is recorded after the game. **It is not pregame knowledge.**
- The data is ESPN's (personal research only), and for history its availability would be assumed, as for team box scores.

## 3. Gates before historical backtesting can be trusted
1. **Inventory:** list, for every game day in seven seasons, which report files exist around the forecast time, instead of a sample.
2. **Parser:** read both layouts including the team and game columns, and reconcile each report; report every file that fails.
3. **Mapping:** an alias table across all seasons, with an explicit unresolved category and its size per season.
4. **Availability rule**, written down before any model is run (clarified 2026-10-01 after Codex review):
   - **Never the literal hour in the file name.** A legacy file named `05PM` was published at about 5:30 PM.
   - **Historical publication time** = the time printed in the report's own header ("Injury Report: MM/DD/YY HH:MM AM/PM"), read as
     America/New_York local time (daylight saving applied) and converted to UTC.
   - **Cross-check and revisions:** compare it with the server's `Last-Modified`. If they differ by more than 2 minutes, use the later
     of the two and flag the report. A later `Last-Modified` is treated as a rewrite: the content is only usable from that later time.
   - **Missing metadata:** a report whose header time cannot be read, or which has no `Last-Modified`, is excluded from history and
     counted. No time is guessed or defaulted.
   - **A report is usable for a forecast only if its publication time is at or before the forecast's generation time.**
   - **Status of the result:** even with all four gates passed, historical availability is **assumed** (`assumed_published_time`), never
     observed. Reports collected from now on use only our own response-completion time (`observed_retrieval`), even when the printed time is earlier.

## 4. Note for the later experiment design (not acted on here)
A 5:30 PM report already lists next-day games: on 2023-01-10 it covered four games of 2023-01-11, with 5 of those 8 teams still "NOT YET
SUBMITTED". So some injury information exists before the current midnight cutoff, and its value should not be assumed to need a
near-tipoff forecast. This is one report; it was not analysed further.
