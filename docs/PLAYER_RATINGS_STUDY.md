# Player ratings and injuries: research study `players-v1` (design recorded 2026-10-10, before any code or result)

Status: design only at the time of writing. Exploratory research on data copies. Nothing here changes a live model, a live
definition, the release or a scheduled job. Codex reviews the implementation and evidence before any further step.

## 1. Audit (2026-10-10, read-only)

### NBA: what exists
| Input | Coverage | Where |
|---|---|---|
| Player box scores (sportsdataverse ESPN, SHA-256 verified) | seasons 2019-20 … 2023-24 (IDs 2020–2024), 28.8k–35.0k rows each | `data/research/availability/player_box/` |
| Official 5:30 PM ET injury report, one per evening game date | 2019-20 … 2023-24: 1,002 PDFs with HTTP metadata | `data/research/availability/reports/` |
| Report parser (both PDF producers), name→player matching, aliases | built and independently reviewed for `avail_v1` | `app/availability.py`, `app/reference/nba_player_aliases.csv` |
| Expected minutes (last-10 mean this season, else previous season) | as above | `availability.MinutesHistory` |
| Current-season player minutes from cached ESPN summaries; 2026 minutes seed (591 players) | 2026-27 live | `app/availability_live.py` |
| Prospective injury-report collector (raw PDFs, our own retrieval times) | since 2026-10-01; first game window 2026-10-19 | `app/injuries.py`, launchd `injuries` |

Earlier results (all exploratory, development seasons 2021-22 … 2023-24, 17:45 ET cutoff, 3,667 games):
- `avail_v1` (logistic_v2 + minutes-weighted injury report) beat `logistic_v2`: log loss −0.0049 [−0.0088, −0.0009], accuracy
  +1.5 points (docs/AVAILABILITY_RESULTS.md).
- `avail_q_v1` (absences also weighted by game score per minute) added no demonstrable information beyond `avail_v1`
  (−0.0016, Bonferroni [−0.0037, +0.0005]; docs/CHALLENGER_RESULTS.md, features-v1).

What does **not** exist: any player rating that is updated game by game from results; any use of player ratings to estimate
team strength from the expected roster; the 2024-25 and 2025-26 player boxes and injury reports (deliberately never downloaded:
reserved test seasons).

### NHL: what exists, and the gaps
| Input | Coverage | Notes |
|---|---|---|
| Play-by-play (api-web), raw cache | 2015-16 … 2026-27, about 92% of final games (e.g. 2024-25: 1,400 of 1,515) | contains `rosterSpots` (dressed players) and player ids on shots/goals/penalties |
| Goalie snapshots (ESPN probable/confirmed starters, our retrieval times) | prospective only, since 2026-10-01 | 6,310 rows |
| Goalie quality features (goals saved above our xG) | in the candidate pool | `docs/CANDIDATE_POOL.md` |

Gaps, not solvable inside this study: **no historical NHL injury reports** from any free source; **no time on ice** (shift
charts or boxscore endpoints are not cached; one request per game, about 17k requests); dressed lineups are known only after a
game. An honest NHL player-and-injury backtest is therefore not possible with current data. NHL is out of scope for `players-v1`
beyond this audit; options are listed in §8.

## 2. Question

NBA only. At the same 17:45 ET cutoff as `avail_v1`, does team strength estimated from **game-by-game player ratings and the
expected roster** add information beyond the existing team model, and beyond the existing injury-report candidate?

## 3. Data and seasons (amendment to the reserved-season rule, at the user's request)

- **Development (primary decision):** scored seasons 2021-22 … 2023-24 (IDs 2022–2024), exactly the `avail_v1` games (evening
  tip-offs at or after 18:00 ET). Warm-up from 2019-20 (ratings and logistic fits only).
- **Recent seasons (retrospective, secondary):** 2024-25 and 2025-26 (IDs 2025, 2026) are the NBA test seasons reserved in
  docs/EVALUATION_PLAN.md. They are no longer untouched (docs/MODEL_SELECTION.md: "already reused by research"; the live models
  train on them). **Amendment:** the user asked for training and evaluation through recent seasons. This study downloads their
  player boxes (same verified release) and 5:30 PM reports (same downloader, 1.2 s spacing), appends every access to
  `data/eval/test_access.jsonl`, and reports them **separately**, flagged "retrospective, previously reserved; not a final test".
  They cannot rescue a failed development result and are not a second final test.
- Report publication times are an assumption, as in `avail_v1` (header and Last-Modified); hourly names before 2025-12-22,
  quarter-hour names after.
- Everything runs on copies: a research data folder `~/nba-predictor-research-data/players-v1/` holding store snapshots and a copy of
  the availability cache. The live `data/` folder is not written.

## 4. Player rating model (fixed in advance)

- **State:** one rating per ESPN athlete id, in points of team margin per 48 minutes on the floor, league average 0. A player
  keeps his rating across trades (the id carries it). A player with no rating starts at the newcomer prior −1.0.
- **Update, only after the game's result is delivered by the walk-forward runner** (`backtest.run` delivers a result only
  when it was available before the current cutoff): predicted margin = Σ_home (min/48)·r − Σ_away (min/48)·r + 2.5 (0 at a neutral
  site), using that game's actual minutes (post-game information, used only after the game). Residual e = actual margin −
  predicted. Each player who played: r ← r + K·(min/48)·e (home) or − K·(min/48)·e (away).
- **Season carry:** at a player's first game of a new season, r ← 0.75·r (shrink toward 0).
- **K:** chosen once from {0.01, 0.02, 0.04} by mean squared margin error on 2020-21 (warm-up) games only, then frozen. No
  other tuning.

## 5. Pregame features at the cutoff

- **Expected roster:** players whose most recent appearance before the game date was for this team and who played in at least
  one of the team's last 5 games before the date. Expected minutes = `MinutesHistory.expected` (last-10 mean). Minutes are scaled
  so the roster sums to 240.
- **`player_diff_full`:** expected-roster strength Σ (min/48)·r, home minus away. Uses no injury report.
- **`player_diff_report`:** the same after removing reported absences (Out/Doubtful weight 1, Questionable 0.5, as `avail_v1`),
  rescaling the remaining minutes to 240. If the report is missing or a team did not submit, it equals `player_diff_full`
  and `report_missing` = 1.
- Never used pregame: that game's lineup, minutes, box score or result. Ratings and minutes use only games before the date.

## 6. Variants and evaluation (fixed in advance)

Same learner, scaling rule and season-start refit as `logistic_v2`/`avail_v1`; same games for all four:
- **B** `logistic_v2` (team baseline)
- **B+I** `avail_v1` (unchanged)
- **B+R** `logistic_v2` + `player_diff_full`
- **B+R+I** `logistic_v2` + `avail_v1` features + `player_diff_report`

**Primary comparisons** (development seasons, paired, same games): (1) B+R − B; (2) B+R+I − B+I. Log loss with the existing
week-block bootstrap, Bonferroni over the two primaries. Reading rule: "adds information" only if the upper bound is below 0.
Secondary: B+R+I − B; Brier; accuracy; calibration (10 equal-count bins); accuracy and coverage at model confidence ≥ 0.60,
0.65, 0.70, 0.75; per-season results; coverage (report usable, unresolved names, players with no rating). All variants
reported, including failures and negative results. The recent seasons are reported in their own tables with the same metrics.

No promotion follows from this study. A variant that adds information can only become a separately versioned forward
research candidate after Codex review and the safe-transition work.

## 7. Implementation guards

- New module `app/player_ratings.py` and tests only. `app/availability.py`, `availability_live.py`, `backtest.py` and every
  other file hashed into live definitions are **not edited** (editing them would restart live research records or the adaptive
  definition); the new module imports them.
- Required tests: own-outcome independence (changing a game's result or box changes none of its own features); future
  mutation independence; ratings use only results delivered before the cutoff; trades keep the rating; newcomers get the
  prior; missing/unsubmitted report falls back to `player_diff_full` with the flag; minutes rescaling; season carry.

## 8. NHL options (not part of this study)

(a) Prospective only: record goalie starters (already collected) and dressed-roster absences from the previous game, and judge
on future games. (b) Backfill shift charts (about 17k requests) to get time on ice, then rate skaters from on-ice goal and xG
differential. (c) Rate only goalies (already a candidate feature) with the probable/confirmed starter. Each needs its own design.
