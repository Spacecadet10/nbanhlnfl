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
(Data agent fills in: seasons imported, games per season, D-I/non-D-I counts, neutral, postseason, OT, box-score coverage,
odds coverage, rerun results.)

## Results
(Model agent fills in.)

## Evidence
Source probe 2026-11-17: 2026-11-17 68 D-I-group events (13 non-D-I team rows, 52 with DraftKings odds, 7 neutral);
2025-03-20 16 NCAA Tournament games (neutral, season type 3); 2024-01-13 133 games (9 with OT); 2012-03-15 16 games.
TheRundown sport 5: 39 events, 7 priced (moneyline/spread/total), 204 datapoints; market 94 empty.
