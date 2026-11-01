# NFL plan (data-only store first, then a winner model)

Recorded 2026-11-01 (session date), before any NFL model was run or any NFL rate, accuracy or Brier score was computed.
Authorisation: the user's request to add the NFL "without breaking the code". Existing rules still apply: AGENTS.md guards,
docs/EVALUATION_PLAN.md principles, docs/MULTISPORT_PLAN.md (one schema, one store per sport). No NBA or NHL parameter
value or result is transferred to the NFL.

## Safety design: registered, not served
- `sports.NFL` lives in `sports.DATA_ONLY`, not in `sports.SPORT_IDS`/`SPORTS`. `sports.get("nfl")` raises
  `UnsupportedSport`, so every served path (API `sport=` query, `--sport` CLI choices, `live.engine`, `RealProvider`,
  research and jobs) refuses NFL. About 75 places in the backend branch "if nba … else NHL"; none of them can be reached
  with an NFL store while NFL stays out of `SPORT_IDS`.
- `live.engine` now names its sports explicitly (NHL → hockey engine, NBA or unlabelled → NBA engine, anything else →
  `UnsupportedSport`) instead of treating every non-NHL store as NBA.
- Own store `data/nfl.sqlite`, created by the same migrations; `store_identity` binds it to `nfl` (migration 0032 adds
  `nfl` to the allowed values of that one table only; market/research tables still allow only `nba` and `nhl`).
- Own commands, independent of `--sport`: `python -m app.cli nfl {migrate,ingest,replay,coverage}` under the NFL job lock.
- NFL joins `SPORT_IDS` only after every branch on the sport handles it explicitly (phase C below), with tests.

## Source
ESPN public NFL scoreboard (`site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard`, free, no key; same
unofficial-endpoint caveat as the NBA adapter). One request per calendar month (`dates=YYYYMM&limit=1000`; a date range is
refused). Checked 2026-11-01: months back to 1999 return final results; 32 franchise ids (1–30, 33 BAL, 34 HOU); 31/32 are
the Pro Bowl AFC/NFC sides; international games and the Super Bowl carry `neutralSite: true`; ESPN keeps a franchise's id
through relocations (Rams, Chargers, Raiders), so ratings follow the franchise. Season = ESPN season year = the year the
season starts (2024 = 2024-25). Schedule, status and final score only; odds, injuries and box stats are later phases.

## Phases
- [x] A0 Guards + registry + migration 0032 + ESPN NFL parser + ingest/replay + coverage (tests: `tests/test_nfl.py`)
- [ ] A1 Evaluation plan below recorded (done in this file) → walk-forward of the winner candidates on development and
      validation seasons → seal the choice → one logged test run
- [ ] A2 NFL serving engine (`live.Engine`) behind its own commands: night-before forecasts in the NFL ledger and grading
- [ ] C  Make every "nba … else NHL" branch explicit, then add `nfl` to `SPORT_IDS` (API, UI toggle, jobs) — needs the
      user's go, like any live change
- [ ] B  Spreads/totals (football score model: key numbers 3/7), injuries (official report), weather — each preregistered first

## NFL evaluation plan (recorded 2026-11-01, before any NFL model is run)
Before this was written, NFL scoreboard months 2024-09/10, 2025-01/02 and single months of 1999/2002/2006/2010 were opened
**for schema and coverage checks only** (fields, ids, neutral flags, event counts); no model, rate or accuracy was computed.

- **Eligible:** final regular-season and postseason games between two franchises. Excluded: preseason, Pro Bowl (`other`),
  unplayed games. A tie is a final result: it updates ratings as half a win, and it is **void for winner scoring**
  (no winner; the bookmaker moneyline is usually refunded), counted and reported separately.
- **Point in time:** as for the NBA and NHL: night-before cutoff 00:00 America/New_York on the game's Eastern date;
  historical results available at kickoff + 4 h; a result updates a model only when available at or before the cutoff.
  Neutral site = ESPN's per-game flag (no home advantage). The 2020 season (few or no fans) is not special-cased.

| Period | Seasons (ESPN year) | Use |
|---|---|---|
| Warm-up | 2002, 2003, 2004 | burn-in only, never scored |
| Development | 2005 … 2016 | each candidate's own grid (below) |
| Validation | 2017 … 2021 | choosing among the tuned candidates, once each |
| Test (reserved) | 2022, 2023, 2024, 2025 | run once with the frozen choice; every access logged |
| Prospective | rest of 2026, then 2027 | live night-before record |

- **Winner candidates:** constant 0.5; Laplace-smoothed running home win rate; `nfl_elo` (K ∈ {10, 15, 20, 25, 30},
  H ∈ {0, 25, 50, 75}, carry ∈ {0.5, 0.67, 0.75, 1.0}, margin multiplier off/on with
  `mult = ln(|margin| + 1) × 2.2 / (0.001 × winner_diff + 2.2)`). Criterion: development Brier on non-tie games;
  ties in the criterion → multiplier off, carry 1.0, smallest K.
- **Validation choice:** lowest validation Brier among the three tuned candidates; Elo must also beat the home rate on
  week-block paired bootstrap (as the NHL plan) to be chosen, otherwise the home rate is the sealed winner model.
- **Expectation, stated before running:** NFL seasons have ~272 games (vs ~1,230 NBA), so validation has ~1,350 games and
  the test ~1,100; intervals will be wide. Bookmaker NFL lines are very efficient; no betting edge is expected or claimed.
