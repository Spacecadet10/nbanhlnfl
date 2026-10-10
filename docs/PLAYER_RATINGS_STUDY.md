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

Gaps in **our cached data** (not proof that no source exists): no historical NHL injury reports in the cache, and no free
source checked yet; no time on ice cached (per-game boxscore TOI and bulk stats-API player-game reports have not been checked;
shift charts are only needed for event-level on-ice analysis); dressed lineups are known only after a game. NHL is out of scope
for `players-v1`; a separate NHL data-feasibility audit comes before any NHL decision (§8).

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

## 8. NHL: feasibility audit first (corrected after Codex review, 2026-10-10)

Before excluding NHL or committing to thousands of requests: (1) search for free historical NHL injury/availability sources and
record what each provides and when it was published; (2) check per-game player time on ice from the boxscore endpoint and the
bulk stats API (request count, coverage, timing); (3) measure what the existing goalie features and the prospective goalie
collection already add, rather than building an overlapping goalie candidate. Better NHL data is not made conditional on a crude
dressed-roster proxy succeeding: that proxy could fail simply because it measures player contributions poorly.

## 9. Interpretation limit (Codex review)

The rating update spreads each game's prediction error over the players by minutes. Teammates who share minutes move together, so
a rating is a **predictive team-strength input**, not an isolated estimate of a player's individual ability. Results are read only
as forecast quality, never as player valuations.

## 10. Results (final after Codex review; three runs, 2026-10-10)

> **Timing assumption.** Every historical result in the store (2019-20 … 2025-26) has
> `result_availability_basis = assumed_tipoff_plus_4h`; only 2026-27 has observed retrieval times. Report publication times are
> also assumed (header / Last-Modified). On this history, "delivered by the cutoff" is therefore the same as "dated before the
> game day", and the injury-report benefit below is historical evidence **under these assumptions**, not under observed times.

**Verdict (measured).** This player-rating method showed **no demonstrated added information** over the team model, nor over
the injury-report model, in any version of the analysis. The recent seasons agree (all intervals include 0). The injury report
(B+I) still improves log loss over B, with and without the corrections. Nothing to promote.

### Runs and what each change was
All on the copy `~/nba-predictor-research-data/players-v1/` (`reports/<run>/report.json`, per-game `games.json`). Game ids,
order, outcomes and cutoffs are identical in all three runs (3,667 development, 2,430 recent games), and every variant scores
every game.

| Run | Report | Change |
|---|---|---|
| 1 | `players-v1-2026-10-10T152102Z` | Original. **Superseded.** |
| 2 | `players-v1-2026-10-10T173450Z` | (a) rosters/expected minutes use only games whose result the walk delivered; (b) the adjusted interval is the actual 1.25 / 98.75 week-bootstrap percentile (run 1 used a normal approximation from the 95% interval). |
| 3 | `players-v1-2026-10-10T175100Z` | Adds the injury timing audit and the corrected variants B+I\*, B+R+I\* (injury absent minutes from delivered games only). Run-2 variants bit-identical. |

**Timing fixes — no measurable effect on this history.** Because of the tip-off + 4 h assumption, gating by delivery could not
differ from gating by date: a rebuild of every expected roster both ways is identical on every scored game, and the injury
timing audit finds **0 games in every season** (2022 … 2026) whose injury features used an undelivered game. The fixes matter
for live or genuinely delayed data; they are not measurable here.

**Data-population changes — the actual source of every run-1 → run-2 difference.** Run 1's rosters and minutes came from every
box-score appearance; runs 2–3 use only games in the walk's game list. The difference is entirely:
- **All-Star games** (store `season_type='other'`, coded regular season in the ESPN files; 6 in the development box, 13 in all):
  their minutes entered last-10 averages, and an All-Star was dropped from his team's expected roster until his next team game.
- **The six 2020-21 play-in games, whose box rows have blank minutes** (the remaining 10 development games, October 2021).

B and B+I are bit-identical between runs 1 and 2; `player_diff_full` changed on 473 development / 310 recent games and
`player_diff_report` on 430 / 283. K is still 0.02 (warm-up MSE 204.0 / 202.4 / 208.1 for K = 0.01 / 0.02 / 0.04). The same
population difference explains the corrected injury variants: B+I\* differs from B+I on 252 development / 237 recent games
(All-Star minutes; one game, `espn:401359833`, from the minute-less play-in date), never because of timing.

### Development (decision), 2021-22 … 2023-24, 3,667 evening games (run 3)
| Variant | Accuracy | Log loss | Brier | ECE | ≥0.60 games / acc | ≥0.65 | ≥0.70 | ≥0.75 |
|---|---|---|---|---|---|---|---|---|
| B `logistic_v2` | 64.5% | 0.6300 | 0.2199 | 0.022 | 2,213 / 70.3% | 1,639 / 73.5% | 1,095 / 77.4% | 688 / 78.9% |
| B+I `avail_v1` | 66.0% | 0.6251 | 0.2177 | 0.023 | 2,271 / 70.8% | 1,708 / 73.7% | 1,186 / 76.8% | 732 / 79.6% |
| B+R | 64.7% | 0.6299 | 0.2199 | 0.024 | 2,216 / 70.6% | 1,628 / 73.2% | 1,087 / 77.5% | 684 / 79.1% |
| B+R+I | 65.6% | 0.6248 | 0.2176 | 0.025 | 2,271 / 70.8% | 1,712 / 73.3% | 1,177 / 77.7% | 747 / 79.8% |
| B+I\* | 65.9% | 0.6250 | 0.2177 | 0.024 | 2,271 / 70.8% | 1,710 / 73.7% | 1,187 / 76.8% | 734 / 79.6% |
| B+R+I\* | 65.6% | 0.6248 | 0.2176 | 0.025 | 2,273 / 70.8% | 1,715 / 73.3% | 1,180 / 77.6% | 749 / 79.7% |

| Comparison (paired; ISO-week block bootstrap, 2,000 samples, seed 20260930) | Log loss diff | 95% | Adjusted 1.25 / 98.75 | Accuracy [95%] |
|---|---|---|---|---|
| **Primary 1:** B+R − B | −0.00012 | [−0.00056, +0.00029] | **[−0.00063, +0.00034]** | +0.16 pts [−0.18, +0.52] |
| **Primary 2:** B+R+I − B+I | −0.00029 | [−0.00133, +0.00066] | **[−0.00148, +0.00077]** | −0.35 pts [−0.78, +0.03] |
| **Corrected primary 2:** B+R+I\* − B+I\* | −0.00028 | [−0.00131, +0.00067] | **[−0.00146, +0.00076]** | −0.33 pts [−0.76, +0.06] |
| Secondary: B+I − B | −0.00489 | [−0.00884, −0.00088] | [−0.00956, −0.00036] | +1.47 pts [+0.43, +2.58] |
| Secondary, corrected: B+I\* − B | −0.00493 | [−0.00889, −0.00089] | [−0.00960, −0.00037] | +1.45 pts [+0.40, +2.51] |
| Secondary: B+R+I − B | −0.00518 | [−0.00906, −0.00111] | [−0.00979, −0.00054] | +1.12 pts [+0.03, +2.27] |
| Correction effect: B+I\* − B+I | −0.00004 | [−0.00009, +0.00001] | [−0.00010, +0.00001] | −0.03 pts [−0.12, +0.06] |

Corrected primary 2 restates primary 2 with injury minutes from listed, delivered games only; it is a correction of a
pre-registered comparison, not an extra one, and uses the same two-comparison adjustment. The pre-registered adjustment covers
the primaries only; secondary adjusted intervals are shown for comparability. Run 1 (superseded) primaries: −0.00010 [−0.00057,
+0.00037] and −0.00024 [−0.00135, +0.00087] (normal approximation); same reading.

Per-season log loss (B / B+I / B+R / B+R+I / B+I\* / B+R+I\*): 2022 0.6372 / 0.6287 / 0.6372 / 0.6284 / 0.6286 / 0.6284;
2023 0.6429 / 0.6341 / 0.6428 / 0.6343 / 0.6341 / 0.6343; 2024 0.6098 / 0.6125 / 0.6096 / 0.6117 / 0.6125 / 0.6116 (in 2024 the
injury report did not help).

### Recent seasons, 2024-25 and 2025-26 — retrospective, previously reserved; not a final test (2,430 games, run 3)
| Variant | Accuracy | Log loss | Brier | ECE | ≥0.65 games / acc | ≥0.75 games / acc |
|---|---|---|---|---|---|---|
| B | 67.3% | 0.6013 | 0.2074 | 0.023 | 1,245 / 76.5% | 603 / 82.3% |
| B+I | 68.4% | 0.5904 | 0.2029 | 0.023 | 1,333 / 76.4% | 700 / 84.6% |
| B+R | 67.5% | 0.6005 | 0.2071 | 0.019 | 1,262 / 76.1% | 610 / 82.6% |
| B+R+I | 68.3% | 0.5894 | 0.2024 | 0.022 | 1,347 / 76.4% | 703 / 84.5% |
| B+I\* | 68.4% | 0.5904 | 0.2028 | 0.022 | 1,337 / 76.4% | 700 / 84.6% |
| B+R+I\* | 68.4% | 0.5894 | 0.2024 | 0.019 | 1,347 / 76.4% | 704 / 84.5% |

| Comparison | Log loss diff | Adjusted 1.25 / 98.75 | Accuracy [95%] |
|---|---|---|---|
| B+R − B | −0.00075 | [−0.00169, +0.00023] | +0.21 pts [−0.43, +0.81] |
| B+R+I − B+I | −0.00102 | [−0.00259, +0.00054] | −0.12 pts [−0.74, +0.55] |
| Corrected primary 2: B+R+I\* − B+I\* | −0.00101 | [−0.00257, +0.00054] | −0.08 pts [−0.70, +0.60] |
| B+I − B | −0.01083 | [−0.01595, −0.00614] | +1.15 pts [−0.05, +2.36] |
| B+I\* − B | −0.01086 | [−0.01597, −0.00616] | +1.15 pts [−0.05, +2.36] |

Per-season log loss: 2025 0.6037 / 0.5945 / 0.6030 / 0.5933 / 0.5945 / 0.5933; 2026 0.5989 / 0.5864 / 0.5980 / 0.5856 / 0.5863 /
0.5855. Rating point estimates lean slightly favourable; every interval includes 0, and these seasons cannot overturn the
development result (§3).

### Measured vs hypothesis
Measured (these seasons, these games, this method):
- Adding `player_diff_full` to `logistic_v2`, or `player_diff_report` to `avail_v1` (original or corrected), showed no
  demonstrated added information; the adjusted log-loss intervals include 0.
- The injury report improves log loss over B by about −0.0049 in development (adjusted interval excludes 0; not in 2024 alone)
  and −0.0108 in the recent seasons — under the timing assumptions stated at the top.
- No injury-feature timing violation on this history (structurally guaranteed by the assumed result times).

Hypotheses, not tested here:
- That the ratings add nothing *because* they duplicate team Elo / net rating (suggestive only: the update spreads team
  residuals by minutes, §9; last development fit weights `player_diff_full` 0.14 vs elo 0.25 and net 0.29; no ablation run).
- That a better-specified player model (regularized plus-minus with on/off splits, more tuning) would add information.
- That the recent-season lean means anything; that the injury benefit holds under observed publication and result times
  (the prospective collector since 2026-10-01 is the test of that).

### Coverage
- Reports: 1,002 development and 422 recent dates, all with a publication header and usable (0 download failures); every
  scored game had a usable report for both teams. Entries without a team: 18 (development), 77 (all).
- Unresolved report names: 2020 303, 2021 8, 2022 0, 2023 18, 2024 26, 2025 89, **2026 227** (those players stay on the roster).
- Rating updates: 6,269 games through 2023-24, 8,912 through 2025-26; the six 2020-21 play-in games gave no update (box rows
  exist, minutes blank).
- Access to 2025/2026 logged per run in the copy's `eval/test_access.jsonl` and mirrored to `data/eval/test_access.jsonl`
  (runs at 15:23, 17:37, 17:43 — the third-run attempt stopped by the audit's self-check — and 17:54 UTC).

### Limitations
- Name matching level `team_any_season` uses every loaded season (loading 2025–26 changed 2024 counts: `team_any_season`
  320 → 386, `league_unique` 459 → 393); identity only, as in `avail_v1`.
- Assumed report publication and result times (top of this section).
- One rating rule, one K grid, no other tuning. Ratings are a team-strength input (§9), not player valuations.
