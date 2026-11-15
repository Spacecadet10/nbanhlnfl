# International soccer (national teams): preregistered protocol v1

Written 2026-11-08, about 21:45Z, before any model code, fit or metric. Author: the integration writer (session f7).

Asked by the user (2026-11-08):
> Build it all. Nations League, UFA Champions, UFA International, like the whole thing.

Club competitions are already covered by the club model; this protocol covers national teams. It mirrors the club
protocol (`docs/evidence/soccer-training-2026-11-08/PROTOCOL.md`) and adds the two pieces the club one lacked:
- a paired comparison with the baseline is computed by the test runner;
- the test-reading rules are fixed here, before the model exists.

## Data
- **Source:** martj42/international_results (CC0-1.0, GitHub, no account). Files: `results.csv`, `goalscorers.csv`,
  `shootouts.csv`.
- **Pinning:** the commit is pinned when imported (current HEAD `2026-10-31T10:12:59Z`). Bytes are cached with their
  sha256.
- **Scope:** senior men's international matches as listed. The model uses only the listed home/away teams, the
  `neutral` flag, the tournament name and the date. City and country are not used.

**Pre-protocol inspection (disclosure).**
- Inspected: file headers, minute conventions (stoppage time usually recorded as 90, sometimes 91–99; extra time 91–120),
  and counts since 2010 of shootouts (286), matches with goals at 106+ (35), goals only at 91–105 (25), and matches with
  incomplete goal minutes (8,798 of 16,339).
- No home/draw/away share, goal average or metric was computed.
- Disclosure: the author's general knowledge includes outcomes of major recent tournaments. Every choice below is fixed
  before any model run.

## 90-minute result (label)
- **A shootout entry:** the match went to extra time, so it was level after 90 minutes. The label is a draw.
- **A goal at minute ≥ 106:** extra time was played, so the label is a draw.
- **A goal only at 91–105, with neither of the above:** ambiguous (stoppage or extra time), so the match is **excluded**
  as input and as scored match.
- **Final-tournament matches with incomplete goal minutes are excluded.** These are tournaments whose name contains none
  of "qualification", "Friendly" or "Nations League", and whose goal-minute rows do not add up to the score. Extra time
  cannot be ruled out for them.
- **Otherwise:** the label is the sign of the listed score. Rows without a score are not results.

## Scored competition groups (all other tournaments are inputs only)
| Group | Tournaments |
|---|---|
| world_cup | "FIFA World Cup" |
| continental_finals | "UEFA Euro", "Copa América", "African Cup of Nations", "AFC Asian Cup", "Gold Cup", "OFC Nations Cup" |
| wc_qualification | "FIFA World Cup qualification" |
| continental_qualification | "UEFA Euro qualification", "African Cup of Nations qualification", "AFC Asian Cup qualification", "Gold Cup qualification", "OFC Nations Cup qualification" |
| nations_league | "UEFA Nations League", "CONCACAF Nations League" |
| friendly | "Friendly" |

## Periods (match date)
| Period | Years |
|---|---|
| warm-up | 1872–2009 (inputs only) |
| development tune | 2010–2015 |
| development calibration | 2016–2017 (reported only; no calibration is fitted) |
| validation | 2018–2021 |
| reserved test | 2022–2025 (locked) |
| prospective | 2026 onwards (never evaluated here) |

## Point in time
A match dated D is forecast at D 06:00 UTC, from results of matches dated before D. A result is assumed available the
day after its date, at 06:00 UTC. Same-day results are never inputs.

## Candidates
1. **Frequency:** home/draw/away shares from scored-group matches of 1990–2009, separately for neutral and non-neutral
   venues. Fixed.
2. **International Elo:**
   - every team starts at 1500 when it first appears;
   - update `K_eff = K × w(tournament) × g(goal difference)`, with `w` = 60 world_cup, 50 continental_finals, 40 both
     qualification groups and nations_league, 20 friendly, 30 any other tournament;
   - `g` = 1, 1.5 for a 2-goal margin, (11 + margin) / 8 for 3+ when the goal multiplier is on, else 1;
   - expected score from `dr = R_home − R_away + H × (1 − neutral)`;
   - draw model: ordered logit on dr/400 (one slope, two cut points), fitted on development-tune forecasts of each
     configuration;
   - grid: K ∈ {10, 15, 20} × H ∈ {50, 75, 100} × goal multiplier {on, off} = 18 configurations.
   - **Selection:** lowest development-tune log loss. Tie-break: smaller K, then smaller H, then multiplier off.

## Choice on validation
Elo replaces the frequency baseline only if all three hold:
- its pooled validation log loss is lower;
- the 95 % interval of the paired per-match difference (ISO-week block bootstrap, seed 20260930, 2,000 resamples) lies
  entirely below 0;
- it is lower in at least 3 of the 4 validation years.

Otherwise the baseline is kept and nothing is launched. No calibration step.

## Reserved test (one logged run) and its reading, fixed now
- **Runner:** refuses an invalid seal, an existing output, a second run, and changed code or data. It writes overall, per
  competition-group and per-year metrics, **and** the paired per-match difference against the frequency baseline with
  its 95 % interval (same bootstrap).
- **R1:** paired mean difference < 0 **and** the upper end of its 95 % interval < 0, pooled over all scored groups.
- **R2:** each outcome's ECE ≤ 0.05, pooled.
- **R3, launch scope per competition group:** R1 and R2 pass pooled, **and** for the group: paired mean difference < 0,
  interval upper end < 0, and n ≥ 300. Groups that fail are not served.
- **R4 (descriptive):** per-year metrics, reliability, predicted vs observed shares.
- **Fixed in advance regardless of outcome:** no tuning, recalibration, re-seal or second run afterwards. Passing does
  not establish a betting edge. Unrounded floats; a tie fails `<`.

## Serving (after the test, if in scope)
- Same cutoff semantics as the club live path. History = the pinned CC0 file, read with the seasons ≤ 2025 logic.
- The current matches come from ESPN international scoreboards, matched to dataset team names through an explicit
  reviewed map.
- Every input match before the cutoff must be settled, or the run is refused.
- Market: 90-minute home/draw/away only.

## Amendment 1, before any result (2026-11-08, about 21:48Z)
The first development start stopped while parsing the data, before any fit or metric, because the real file lists some
fixtures more than once with the same date and teams (first: Tahiti–New Caledonia, 1974-02-17).
- Goal minutes and shootouts are keyed by date and teams, so they cannot be attributed to one of those listings.
- **Rule:** every listing of a duplicated (date, home, away) key is excluded as input and as scored match. The exclusion
  reason is "duplicate listing: goal minutes/shootout cannot be attributed", and the count is reported.
- No other change.
