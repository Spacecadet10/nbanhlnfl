# NCAA men's Division I basketball (NCAAB) — data contract and plan

Recorded 2026-11-17 by the lead (Claude) before any NCAAB data was imported or any NCAAB model was run. Authorisation: the
owner's NCAAB brief of 2026-11-17 (full fifth sport, NBA-like behaviour, $0 new data costs, deploy through the normal
release). Existing rules still apply: AGENTS.md guards, PLAN.md data/evaluation rules, docs/MULTISPORT_PLAN.md.

## Non-negotiables
- **$0 new data cost.** Free sources and existing free-tier quotas only. No signup, plan change or paid endpoint.
- **Other sports unchanged.** NBA, NHL, NFL and Soccer model ids, stored rows and behaviour stay identical.
  Do NOT edit these hashed/sealed files: `app/coverage.py`, `app/markets.py`, `app/models/base.py`, `app/models/elo.py`,
  `app/models/points.py`, `app/nfl_live.py`, `app/nfl_eval.py` (NFL activation `b6074877a7e7ccfe`);
  `app/backtest.py`, `app/agreement.py`, `app/availability*.py`, any model file an agreement member imports
  (NBA/NHL agreement identity); `app/best_pick_record.py`; `app/soccer_eval.py`, `app/soccer_history.py`,
  `app/soccer_clubs.py`, `app/soccer_markets_eval.py`, `app/metrics.py`; every `app/models/*_decision.json`.
  New NCAAB code goes in new modules. Import from sealed modules; never change their bytes.
- **`data/` is live.** Build and test against a scratch store: `NBA_DATA_DIR=$HOME/nba-ncaab-scratch/data`.
  Only the lead writes `data/ncaab.sqlite`, at release.
- **No Telegram sends** during development or verification: previews and dry runs only.

## Store and identity
- Own SQLite store `ncaab.sqlite`, created by the shared migrations, `store_identity.sport = 'ncaab'`.
- Ids: games `ncaab:<ESPN event id>`, teams `ncaab:<ESPN team id>` (ESPN ids verified stable between scoreboard and summary).
- Registry: `Sport(id="ncaab", label="NCAAB", db_file="ncaab.sqlite", id_prefix="ncaab:", eligible_season_types=("regular",
  "postseason"), markets=("winner","spread","total","team_total_home","team_total_away"))`. The data phase registers it as
  data-only. The app phase moves it into `SPORTS` once every `nba`/else-NHL branch handles it explicitly.
- Market specs: `markets.SPEC` cannot be edited (it is NFL-hashed). NCAAB specs are registered at import time by a new
  module (`app/ncaab_markets.py`: `markets.SPEC.update(...)`), imported from `app/sports.py`. A test proves
  `markets.py` bytes and the NFL definition are unchanged. Settlement reuses `markets.settle` unchanged.

## Sources (verified 2026-11-17 by the source probe; see the probe notes under "Evidence")
| Need | Source | Notes |
|---|---|---|
| Schedule, results, neutral site, tournament notes, OT linescores | ESPN `site.api.espn.com/.../basketball/mens-college-basketball/scoreboard?dates=YYYYMMDD&groups=50&limit=400` | `groups=50` required (without it: 11 of 68 games). One request per ET day. History reaches 2012 or earlier. Month queries are unverified; use day queries. |
| D-I team list (current season) | ESPN `.../mens-college-basketball/teams?groups=50&limit=1000` | 365 teams. Do NOT use `/groups` (lists 289). |
| Box score (FGA, FTA, OREB, TOV) for tempo/efficiency | scoreboard `competitors[].statistics` if it carries all four; otherwise ESPN `summary?event=ID` | 1 req/s, resumable, attempts recorded. |
| Forward sportsbook quotes | ESPN scoreboard odds block (DraftKings: spread, total, moneylines; open and current) at each retrieval; TheRundown sport_id 5 within the existing daily datapoint budget (DraftKings, BetMGM, FanDuel; ~200 datapoints per request) | No The Odds API use for NCAAB (shared 500/month free tier). Team totals only if a free feed actually returns them (TheRundown market 94 returned none on 2026-11-17). |
| Historical odds | ESPN summary `pickcenter`/odds if present, labelled `retrieved after the game; not a verified pregame quote` | Benchmark only; never shown as a same-time quote. If absent, report "no free historical odds". |
ESPN terms: personal, non-commercial use only (docs/DATA_SOURCES.md) — same as NBA.

## Game classification
- **Division I:** a game is D-I when both teams carry a conference id on that game's scoreboard (historical) — cross-checked
  against the current D-I list for the current season; disagreements are reported, not guessed.
- **Non-D-I opponents:** the game is stored with `season_type = 'other'`. It never updates ratings, is never forecast, graded,
  shown on the slate or counted in records. The opponent team is stored so reruns stay idempotent.
- **Season:** ending year (2027 = 2026-27). `regular` = ESPN season type 2 (including conference tournaments);
  `postseason` = type 3 (NCAA Tournament, NIT and other postseason events). Tournament name, conference-game flag and
  conference membership per team-season are stored in new NCAAB tables (one migration) so the app can filter by conference.
- **Neutral site:** ESPN's per-game `neutralSite`. Home advantage is zero on neutral sites.
- **Status:** scheduled / in_progress / final / postponed / canceled per the shared CHECK. Postponed and canceled settle `void`.
- **Result availability (history):** `assumed_tipoff_plus_4h`, as NBA/NHL. Live: `observed_retrieval`.

## Markets and settlement
Winner, spread, game total, home and away team totals. Overtime counts for every market (full-game score).
Half-point lines win/lose; whole-number lines can push (`mid`). Postponed/canceled → void. A start-time change after
the forecast → void (existing rule). Result corrections are stored as result revisions; the original forecast row is
never changed and the grade follows the corrected final. Model lines are labelled "model line"; a sportsbook comparison is
shown only when an exact matching quote (same game, market, side, line) was saved at or before the forecast's generation time;
otherwise "No sportsbook quote available".

## Evaluation plan (recorded before any NCAAB model output is viewed)
Separate from every NBA/NHL rule; no parameter value is transferred.
- **Point in time:** features use only results available at or before the cutoff (00:00 America/New_York on the game's ET
  date for backtests; the two-hour pregame policy live). Same-day unfinished games, current-game labels and future rows are
  excluded; leakage tests cover each.
- **Periods** (seasons by ending year; the data agent confirms coverage before modelling, and the lead adjusts only the
  warm-up start if a season is missing):

| Period | Seasons | Use |
|---|---|---|
| Warm-up | 2013, 2014 | burn-in only, never scored |
| Development | 2015 … 2021 | each candidate's own grid; criterion development log loss |
| Validation | 2022, 2023, 2024 | choose among tuned candidates, once each |
| Test (reserved) | 2025, 2026 | run once with the frozen choice; every access logged to `data/eval/test_access_ncaab.jsonl` |
| Prospective | 2027 | live two-hour record |
2020 has no NCAA Tournament (canceled games settle void and are excluded); 2021 had limited crowds — both stay in development.

- **Winner candidates:** constant 0.5; Laplace home rate (non-neutral games); `college_elo` (own class in a new file; grid
  K ∈ {20, 30, 40, 50}, H ∈ {0, 50, 75, 100} Elo points, carry ∈ {0.5, 0.65, 0.8, 1.0}, MOV multiplier off/on; ties → MOV off,
  carry 1.0, smallest K); `college_eff` (below; its winner probability).
- **Efficiency model `college_eff`:** per team adjusted offensive and defensive efficiency (points per 100 possessions) and
  tempo, possessions ≈ FGA − OREB + TOV + 0.475·FTA; opponent- and venue-adjusted by online updates with rate η; home
  advantage in points (zero on neutral sites); between seasons ratings shrink toward the D-I mean by `carry`. Early season:
  expected values blend the carried prior with current-season evidence by games played (prior weight w0 games). Margin and
  total are normal with σ fitted on development only; the joint score distribution gives winner, spread, total and team
  totals. Grid: η ∈ {0.02, 0.04, 0.08}, carry ∈ {0.5, 0.7, 0.9}, w0 ∈ {4, 8, 12}.
- **Market baselines:** spread from `college_elo` margin (Elo difference / 25 points per 1 point, σ fitted on development);
  total and team totals from each team's running points scored/allowed (opponent-unadjusted). `college_eff` ships for a
  market only if it beats that market's baseline on validation log loss; otherwise the baseline ships, or the market stays
  research if neither is calibrated (|mean predicted − observed| > 2 points in any validation decile).
- **Selection:** the best validation log loss among tuned winner candidates. Paired loss differences vs `college_elo` with a
  seeded weekly block bootstrap (2,000 resamples). If the simple baseline wins or the difference is not distinguishable,
  ship `college_elo`. No calibration layer unless a development-fit calibration improves validation log loss.
- **Reports:** sample sizes, Brier, log loss, reliability (10 bins), per-market results, comparison with historical
  sportsbook probabilities where a free source exists (labelled as after-game retrieval), dataset version, data cutoff,
  training command, model version. The chosen config is frozen in `app/models/ncaab_frozen_decision.json`
  (sha256 self-check like NHL). Backtests stay out of the live W/L record.
- **Picks warning:** any NCAAB pick/threshold rule stays research-only until the prospective NCAAB record supports it.

## Ownership (one writer per file; the lead merges)
| Phase | Owner | Owns |
|---|---|---|
| Data | data agent (branch `ncaab-data`) | `app/sources/espn_ncaab.py`, `app/ncaab.py` (ingest, backfill, classification, coverage), migration `0042_ncaab.sql`, data-only registration in `sports.py`, `ncaab` CLI subcommand, tests `test_ncaab_data*.py`, `docs/NCAAB_PLAN.md` "Coverage" section |
| Model | model agent (branch `ncaab-model`) | `app/models/college.py`, `app/ncaab_eval.py`, `app/models/ncaab_frozen_decision.json`, report under `data/reports/` (scratch) and `docs/NCAAB_PLAN.md` "Results", tests `test_ncaab_eval*.py` |
| App and ops | app agent (branch `ncaab-app`) | `SPORTS` registration and every sport branch site, `app/ncaab_markets.py`, live/near-tipoff/jobs/odds wiring, Telegram/tickets/scorecard, frontend, launchd plists, tests `test_ncaab_app*.py` |
| Review | independent reviewer | read-only findings to the lead |

## Interfaces between phases
- Data → model: `ncaab.load_rows(conn, *, cutoff)` style access through the store: `games` (eligible = both D-I, season_type
  regular/postseason, final), `team_game_stats` possessions fields, NCAAB context tables. `dataset_version` = hash of the
  eligible rows, as `backtest.dataset_version`.
- Model → app: `college.predict(conn, game_id, *, cutoff) -> {model_version, home_win_prob, markets: {market: {line, probs}}}`
  using the frozen decision; model ids prefixed `ncaab-`.

## Coverage
Filled by the data agent, 2026-11-17, from the scratch store `$HOME/nba-ncaab-scratch/data/ncaab.sqlite` (branch `ncaab-data`;
`python -m app.cli ncaab coverage`). Seasons 2013-2027 (2027 to 2026-11-17); every season is present, none is missing.
Counts per season (eligible = both teams Division I and season type regular or postseason; non-D-I = stored as `other`):

| Season | Games | D-I eligible | Non-D-I | Neutral | Postseason | OT | Final (eligible) | Postponed / canceled / other | Scoreboard FGA+FTA | Box complete (OREB, TOV) | Odds games |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2013 | 5838 | 5484 | 354 | 676 | 112 | 321 | 5471 | 9 / 4 / 0 | 4703 | 0 (0.0%) | 0 |
| 2014 | 5995 | 5545 | 450 | 655 | 146 | 365 | 5503 | 41 / 1 / 0 | 5503 | 0 (0.0%) | 0 |
| 2015 | 5949 | 5512 | 437 | 632 | 145 | 355 | 5495 | 16 / 1 / 0 | 5495 | 0 (0.0%) | 0 |
| 2016 | 5991 | 5553 | 438 | 654 | 147 | 343 | 5514 | 38 / 1 / 0 | 5508 | 0 (0.0%) | 0 |
| 2017 | 5982 | 5537 | 445 | 646 | 126 | 337 | 5520 | 15 / 0 / 2 | 5517 | 0 (0.0%) | 0 |
| 2018 | 6022 | 5550 | 472 | 668 | 133 | 394 | 5533 | 12 / 2 / 3 | 5532 | 0 (0.0%) | 0 |
| 2019 | 6066 | 5614 | 452 | 660 | 140 | 345 | 5600 | 10 / 4 / 0 | 5598 | 0 (0.0%) | 0 |
| 2020 | 5842 | 5405 | 437 | 518 | 0 | 300 | 5330 | 5 / 69 / 1 | 5330 | 0 (0.0%) | 0 |
| 2021 | 5282 | 4856 | 426 | 516 | 90 | 218 | 3948 | 267 / 636 / 5 | 3948 | 0 (0.0%) | 0 |
| 2022 | 6387 | 5863 | 524 | 697 | 132 | 319 | 5476 | 214 / 161 / 12 | 5476 | 0 (0.0%) | 0 |
| 2023 | 6261 | 5749 | 512 | 764 | 113 | 360 | 5715 | 11 / 17 / 6 | 5714 | 0 (0.0%) | 0 |
| 2024 | 6249 | 5731 | 518 | 741 | 120 | 343 | 5726 | 4 / 1 / 0 | 5724 | 0 (0.0%) | 0 |
| 2025 | 6299 | 5769 | 530 | 739 | 123 | 325 | 5764 | 5 / 0 / 0 | 5759 | 0 (0.0%) | 0 |
| 2026 | 6318 | 5767 | 551 | 700 | 105 | 325 | 5752 | 13 / 2 / 0 | 5752 | 2340 (40.68%) | 2339 |
| 2027 | 965 | 719 | 246 | 56 | 0 | 29 | 660 | 4 / 0 / 0 | 660 | 660 (100.0%) | 713 |
| all | 85446 | 78654 | 6792 | 9322 | 1632 | 4679 | 77007 | 664 / 899 / 29 | 76219 | 3000 | 3052 |

Eligible finals total 77,007 (D-I games stored 78,654). Odds games = eligible games with at least one stored quote: 2027 has
713 (54 scheduled games with forward DraftKings quotes, 659 finals with after-game lines); 2026 has 2,339 games with after-game DraftKings lines only.

- **Requests used:** 2,569 day scoreboards (2,554 backfill + 15 rerun; about 2 MB each), 1 D-I team list, 3,000 game summaries,
  0 failed. One backfill hiccup, fixed: March 2020 lists canceled tournament games against placeholder 'TBD' teams (ids -1/-2);
  they are skipped (15 days were re-fetched).
- **Rerun proof (last two weeks, `ncaab ingest --from 20261103 --to 20261117 --no-resume`):** games 85,446 before and after, 0
  inserted, 0 updated, 862 finals identical (same count and score checksum), result_revisions unchanged (83,640; no game has a
  second revision), teams/context/team_seasons/stats unchanged; raw_fetches +15 (each retrieval logged), raw_responses +15
  (the live day pages changed), market_quotes +308 (one set per retrieval, by design). Test `test_rerun_adds_no_games_changes_no_finals_and_records_the_fetch`.
- **Box scores:** the scoreboard carries FGA and FTA (2013: 86% of eligible finals, from 2014 99.9%+) but NOT offensive rebounds or
  turnovers, so possessions need the game summary (1 request each, 1.2 s observed). Fetched so far: all of 2027 (660/660) and the
  newest 2,340 of 5,752 eligible 2026 finals. **Still pending: 74,007 summaries (about 25 hours at 1 request/s)**; seasons
  2013-2025 have NO possession inputs yet. This is the gap that blocks `college_eff` on any season before 2026. Resume with
  `NBA_DATA_DIR=$HOME/nba-ncaab-scratch/data python -m app.cli ncaab boxes --sleep 1` (newest first; resumable; at most 3 attempts per game;
  `--season N`, `--limit N`, `--oldest-first`). The lead decides whether to run it fully, run it for the development/validation seasons first, or
  use a bulk source instead (not verified here).
- **Historical odds (summary `pickcenter`, a sample of 6 games per season, fetched after the game):** 2013-2023 list only `consensus`
  (spread), `numberfire` and `teamrankings` (spread and total; these are model projections, not sportsbook prices); 2024 and 2025: none;
  2026: DraftKings spread and total (2,998 of the stored games so far). So **there is no free historical sportsbook quote for
  2013-2025** (2024-25 have nothing). Stored in `ncaab_after_game_lines`, labelled `retrieved_after_game`; benchmark only, never a pregame quote.
  Forward DraftKings quotes (open and current, spread/total/moneyline) exist from now on in `market_quotes` (scheduled games only).
- **Division I rule:** 6,792 stored games have a non-D-I team (8%, `season_type='other'`). The current D-I list (365 teams, retrieved
  2026-11-17) and the scoreboard rule agree for 2027: 0 listed teams without a conference id, 0 D-I teams not listed. Earlier seasons
  have no list to cross-check.
- **Statuses seen:** FINAL, CANCELED, POSTPONED, SCHEDULED, plus FORFEIT (16), SUSPENDED (7), UNCONTESTED (6), all stored as `other` and
  never final. 2020 has no postseason (canceled; 69 canceled eligible games), 2021 has 267 postponed and 636 canceled eligible games.
  Postponed games that were later played appear under their new date (same id).
- **Result availability:** every historical final is `assumed_tipoff_plus_4h` (76,985); 22 finals are `observed_retrieval`.
- **Known gaps:** conference names are not stored (ids only); a game ESPN dropped from the day list is not detected; a result corrected by
  ESPN after our retrieval is only seen when its day is fetched again.
- **Interface:** `ncaab.load_rows(conn, cutoff=...)` returns eligible rows (final, both D-I, regular/postseason, `result_available_at <= cutoff`)
  with `ot_periods`, `conference_competition`, `tournament_note`, conference ids and `home_/away_` `fga fta oreb dreb tov` as known at the
  cutoff (None when no box); `ncaab.dataset_version(rows)` is the 16-hex hash of those rows (current full-store value `b75c61f7137ef67b`).

## Results
(Model agent fills in.)

## Evidence
Source probe 2026-11-17: 2026-11-17 68 D-I-group events (13 non-D-I team rows, 52 with DraftKings odds, 7 neutral);
2025-03-20 16 NCAA Tournament games (neutral, season type 3); 2024-01-13 133 games (9 with OT); 2012-03-15 16 games.
TheRundown sport 5: 39 events, 7 priced (moneyline/spread/total), 204 datapoints; market 94 empty.
