# Injury reports as a pregame input: feature-feasibility result (2026-10-01)

Read-only study on a time-stamped sample. No model was fitted, no accuracy gain is estimated, and nothing in the database, the live
Elo, the sealed shadow experiment or the scheduler changed. Script: `scripts/injury_feature_feasibility.py` (needs `pypdf`, used from a
scratch environment; not a project dependency). Evidence: `docs/evidence/availability-feasibility-2026-10-01/feasibility.json`.

**Sample.** 4 random regular-season dates per season, 2019-20 to 2025-26 (seed 20261002; dates with at least 6 games, at least 30 days
into the season): 28 dates, 237 games, 474 team-games. For each date D, two reports: **A** = 11:30 PM Eastern on D−1 (before the existing
midnight cutoff) and **B** = 5:30 PM Eastern on D. 56 files requested, 1 s apart.

## Result in one table
| Question | Result on this sample | Verdict |
|---|---|---|
| Can the reports be read, including team and game? | 48 of 48 reports parsed; 6,176 player entries; every entry got a game date and a team; no unknown team name | **Feasible** |
| Is the publication time recoverable? | Header time read in 48 of 48 and equal to the expected slot; server `Last-Modified` 0.03 to 0.47 minutes later | **Feasible** (still an assumption for history) |
| Can names be matched to player IDs? | 3,708 of 3,735 entries for the sampled games resolved to one ID (99.3%); 26 unresolved, 1 ambiguous | **Feasible with an alias table**; unresolved are mostly absent players |
| Is there a report before the midnight cutoff? | 20 of 28 dates. None at 11:30 PM in 2019-20 and 2020-21 (the latest evening report then was 8:30 PM). Where it exists, 58 of 344 team-games (16.9%) were "NOT YET SUBMITTED" | **Partly** |
| Is there a report before tipoff on game day? | 28 of 28 dates; 448 of 474 team-games tipped off after 5:30 PM; none unsubmitted | **Yes for 94.5% of team-games at this time** |
| Can a pregame availability number be computed? | Yes: expected minutes of players listed Out or Doubtful (below) | **Computable; its predictive value is untested** |

## Parsing
- Two file producers: iTextSharp through 2022-23, GemBox from 2023-24. The second one's text comes out bottom-to-top within each page;
  reversing each page's lines restores the order. Both handled by one parser reading header time, game date, matchup, team, player, status.
- Reconciliation: in all 48 reports the number of parsed entries equals the number of lines that contain a name followed by a status
  (6,176 = 6,176). This is a check against one looser pattern, not against a human count.
- Team check against box scores: for players with a box-score row within 7 days, the parsed team matched in 2,225 of 2,295 cases (96.9%).
  The 14 mismatches inspected were all the box score lagging the report: players just traded (for example Patrick Beverley, Russell
  Westbrook and Reggie Jackson at the February 2023 deadline), assignment players, or an All-Star-game row. The other 56 were not inspected.

## Name matching
- Unresolved (15 distinct names, 26 entries, 24 of them listed Out or Doubtful): name variants ("Nicolas Claxton", "Nene Hilario",
  "Charles Brown Jr."), a suffix merged by the text extraction ("LivelyII", a rotation player), and players with no box-score row in
  2019-26 (for example Thomas Sorber, Justin Lewis, Gerald Green).
- As the audit warned, **the unresolved entries are almost all absent players.** A feature must count them as "unresolved absent", never
  as healthy, and the alias table must be reviewed by hand each season.

## Timing: what is known when
| | Night before (A, 11:30 PM D−1) | Game day (B, 5:30 PM D) |
|---|---|---|
| Dates with a report | 20 of 28 (none in 2019-20, 2020-21) | 28 of 28 |
| Team-games covered and before tipoff | 344 | 448 (26 tipped off earlier) |
| Team not yet submitted | 58 (16.9%) | 0 |
| Team-games with at least one entry | 251 | 441 |
| Players listed Out/Doubtful | 968 | 1,736 |
| … who then played (audit) | 11 (1.1%) | 3 (0.2%) |

On the 320 team-games with both reports usable, the minutes listed absent were identical in 169 (53%), higher on game day in 138 (43%)
and lower in 13 (4%); the average rose from 47.8 to 73.2 minutes. **A material part of the information arrives after the current
midnight cutoff**, and part of it is already there the night before.

## The candidate measure
*Expected minutes listed absent* for a team = the sum, over its players listed Out or Doubtful, of each player's average minutes in
his last 10 games played before the date (this season if at least 3 games, otherwise the end of last season; players with no history
count 0). It uses only box scores from earlier dates and the report itself.

| | Night before (A) | Game day (B) |
|---|---|---|
| Mean per team-game | 49.4 min | 69.0 min |
| Median | 38.4 | 61.5 |
| Team-games with 0 | 27.9% | 3.1% |
| Team-games with 20 or more | 62.2% | 85.3% |

Audit against who actually played (used for checking only, never as an input): of the minutes of rotation players who missed the game
(players averaging 10 or more minutes who had appeared for the team in the previous 14 days), the report had already listed 32.2% the night
before and 57.2% on game day. The rest were players listed Questionable or Probable, later scratches, and healthy players who simply
did not play, which this check cannot separate.

## What is not established
- **Whether this measure improves forecasts.** Not tested, and no gain is estimated.
- **How much of it is new information.** Most listed minutes belong to players who have been out for weeks, whose absence the team's
  recent results (and so Elo and net rating) already reflect. A usable feature must separate new absences from long-standing ones;
  that definition has not been written.
- **Exhaustive coverage.** 48 reports of roughly 28,000. The audit's four gates for historical backtesting still stand (full inventory,
  production parser with reconciliation, reviewed alias table, written availability rule).
- **The two earliest seasons at the midnight cutoff:** they need the 8:30 PM report, which this study did not read.
- **Early tipoffs** (5.5% of team-games) need an earlier game-day report.

## Feasibility conclusion
Building an injury-based input is **technically feasible** on this evidence: the reports can be read, timed and matched to players, and
a pregame measure can be computed for 94.5% of team-games on game day and for a smaller, less complete share the night before. The data
does not yet say whether the input helps. The next steps, if authorised, would be the full historical inventory and parser (gates 1 to 3)
and a written feature definition that isolates new absences, before any model sees it.
