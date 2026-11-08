# Soccer live forecasts: plan and gates (2026-11-08, integration writer)

**Goal (user, 2026-11-08):** real upcoming soccer predictions in Courtside, then eligible soccer picks in Telegram and
mixed-sport parlays. Nothing here is live until the gates in §1 pass and the user gives a go.

## 1. Gates, in order
1. **Reserved test.**
   - The frozen Elo (`7dbc668c…`) is run once and read only by the pre-run reading plan
     (`docs/evidence/soccer-test-reading-plan-2026-11-08/READING_PLAN.md`).
   - Launch scope = the competitions passing R3.
   - If R1 or R2 fails, nothing below is served.
2. **A current, free, clearly permitted result source.** The model's evaluated point in time uses every result
   available by 06:00 UTC on the match date. A live forecast built on fewer results is not the evaluated model.
   - **openfootball (CC0),** the only source that passed the S1 permission check, measured 2026-11-08
     (`docs/evidence/soccer-live-2026-11-08/openfootball_lag.txt`):
     - last `football.json` commit 2026-09-22; results end 2026-09-20;
     - past matches without a result: EPL 50, La Liga 41, Bundesliga 42, Serie A 55, Ligue 1 39;
     - `champions-league` last commit 2026-07-02, with no 2026-27 file.
   - **ESPN** is not clearly permitted for soccer (Disney terms: no automated collection, no ML use; S1 §C).
   - **football-data.org free tier** has no restricting clause found and covers UCL plus the 5 leagues. It needs an
     account and a key, which is the user's decision (S1 §F.4). Nothing was created.
3. **Kickoff times in UTC.** Tickets and "before kickoff" checks need them.
   - openfootball times are local without a zone. Domestic leagues convert with the league country's zone
     (Europe/London, Madrid, Berlin, Rome, Paris). Unit-tested; S1 found EPL consistent with London time in all 380
     sampled 2023-24 matches.
   - A match without a time is never forecast for tickets.
   - UCL/UEL have a venue zone that is unknown from openfootball, so they are excluded until a source gives UTC.

## 2. Pipeline (source-agnostic; openfootball adapter first)
- **refresh:**
  - resolve the source's current revision (openfootball: the HEAD commit through the public GitHub API, no key);
  - download the current season files at that revision through the existing raw cache;
  - ingest with `soccer_history`'s append-only revisions;
  - a result first seen in this retrieval is available only from this retrieval (`observed_retrieval`, never backdated).
- **completeness, per competition, at forecast time:** refuse, visibly and alerted, when either holds:
  - the last successful retrieval is older than 26 h;
  - any match of the current season dated before the forecast date lacks a result (cancelled or awarded excepted).

  A refused competition records no forecast.
- **forecast:**
  - once per match date, at or before that date's 06:00 UTC cutoff (ledger rule: a prospective row is never generated
    after its cutoff);
  - the frozen Elo walks every usable revision available by the cutoff and predicts the date's scheduled matches;
  - rows go to the append-only `soccer_forecasts` with run_type `prospective`, model identity = the sealed candidate's
    sha256, and the source revision plus retrieval time in the inputs digest;
  - only competitions in the R3 launch scope.
- **grade:** 90-minute result (home/draw/away) once the match is final in a retrieval. Extra time, penalties, awarded
  and cancelled matches are ungraded or void, never guessed.
- **schedule (templates, not installed):** refresh hourly; forecast 04:00–05:50 UTC daily; grading after each refresh.

## 3. App, Telegram and tickets
- **App:** soccer Game day shows upcoming forecasts (label "Upcoming forecast · 90-minute result") apart from Research
  backtest screens. The settlement rule is shown on every card: home/draw/away after 90 minutes plus stoppage time;
  extra time and penalties do not count. No other soccer market is shown as forecast.
- **Tickets:**
  - soccer joins the joint review as market `1x2_regulation`;
  - pick = the outcome the reviewer selects, with that outcome's exact probability;
  - a leg is eligible from its forecast's generation until kickoff, the soccer analogue of the near-tipoff window: the
    forecast exists only at the cutoff;
  - legs settle on the 90-minute result;
  - budget, retry, uncertainty and settlement protections are shared with the other sports.

## 4. Status (2026-11-08, evening)

**Source decision (user, 2026-11-08):**
> forget the terms. Implement the goddamn thing.

So live fixtures and results come from ESPN, which needs no account; S1 had flagged ESPN as not clearly permitted. The
openfootball feed is not used for live inputs because it is stale. football-data.org is not used.

**Reserved test.**
- Run once at 21:00:57Z.
- Read by the pre-run plan, sha256 `9d382ed8…`: `docs/evidence/soccer-test-reading-plan-2026-11-08/result/`.
- Elo test log loss 0.980, 95 % interval 0.967–0.992, against the fixed baseline's 1.071. ECE ≤ 0.028.
- Launch scope: EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL.
- UEL fails R3 (n 284 < 300). It is used as input only.

**Fidelity on real open seasons** (`docs/evidence/soccer-live-2026-11-08/fidelity.txt`):
- the walk without earlier-season forecasts equals the full walk (2,054 matches, difference 0);
- the live loop equals the frozen walk (147 forecasts, difference 0).

**Code:**
- `app/soccer_live.py`;
- migration `0038_soccer_live.sql`, a separate append-only ledger plus checks;
- `app/soccer_live_clubs.json`, 135 reviewed ESPN → history clubs;
- `app/soccer_sealed_candidate.json`, a byte copy of the seal;
- CLI `soccer live-refresh | live-forecast [--day D] [--if-ready] | live-status`;
- `GET /api/soccer/live`;
- the soccer Game day panel;
- tickets: soccer picks, eligibility, 90-minute settlement and alerts;
- `scripts/soccer_live_cycle.sh`, `scripts/soccer_live_setup.sh`, `scripts/launchd/com.nba-predictor.soccer-live.plist`.

## 5. Activation (needs the go; nothing here is live yet)
1. Do the delivery release first: `docs/TICKETS.md` "Guarded release", steps 1–3 (`~/nba-predictor-delivery` at the
   reviewed commit).
2. `sh ~/nba-predictor-delivery/scripts/soccer_live_setup.sh`. It creates `data/soccer.sqlite` (a new file), imports
   the pinned openfootball history, backfills ESPN 2026-27 and runs one cycle. It takes about 30–40 min, because ESPN
   answers about one request per 3 s.
3. Install the hourly cycle: `cp scripts/launchd/com.nba-predictor.soccer-live.plist ~/Library/LaunchAgents/` then
   `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nba-predictor.soccer-live.plist`.
4. Ticket review:
   - update the installed skill text: soccer is now reviewed with NBA/NHL/NFL. Market `1x2_regulation`; the pick is the
     model's most likely 90-minute outcome; legs settle on the 90-minute result;
   - switch the helper to the release wrapper (`docs/TICKETS.md` step 5).
5. Check: `.../app.cli soccer live-status` shows today's checks and forecasts; the app's soccer Game day shows
   "Upcoming forecasts".

**Rollback:**
- `launchctl bootout gui/$(id -u)/com.nba-predictor.soccer-live`;
- the tickets then report the competitions as refused (no run today), and nothing else changes;
- the store `data/soccer.sqlite` can stay; it is append-only and read only by soccer screens and tickets;
- to remove soccer from tickets entirely, move `data/soccer.sqlite` aside, and tickets report soccer unavailable.

## 6. National teams (added 2026-11-08 evening, user: "Build it all. Nations League, … like the whole thing.")

**Model.**
- Preregistered protocol: `docs/INTERNATIONAL_PROTOCOL.md` v1 (21:39Z) plus amendment 1 (duplicate listings excluded,
  before any result).
- Data: martj42/international_results, CC0, commit `b0948cb5` (49,940 real matches).

**Development (once, 21:47Z).**
- Elo K 20 (at the grid edge; the grid is not extended), H 75, goal multiplier on.
- Validation 2018–21: paired log-loss difference −0.181, 95 % interval −0.204 to −0.156, better in 4 of 4 years.
- Sealed as `10547b47…`.

**Reserved test (once, 21:47:40Z), read by the fixed rules R1–R3.**
- 3,758 matches. Log loss 0.878; paired difference vs baseline −0.173 (−0.194 to −0.152); ECE ≤ 0.019.
- Launch scope: continental finals, continental qualifying, friendlies, Nations Leagues, World Cup qualifying.
- World Cup finals are excluded (n 64 < 300).
- Evidence: `docs/evidence/international-2026-11-08/`.

**Fidelity on real data (`tests/test_intl_live.py`).**
- The live core equals the sealed evaluation walk on real 2025 matches (difference < 1e-12).
- Waiting only for affected teams gives exactly the settled forecast on real data. Mutation-checked.

**Code:**
- `app/intl_eval.py` (sealed module), `app/intl_live.py`;
- migration `0039_soccer_international.sql`, a parent rebuild that adds 18 ESPN national-team competitions;
- `app/intl_teams.json`: 175 ESPN teams, 165 exact names plus 10 reviewed aliases;
- `app/intl_sealed_candidate.json`;
- tests: `tests/test_intl_eval.py` and `tests/test_intl_live.py`, on a byte-for-byte real excerpt in
  `tests/fixtures/international_real`. No invented rows.

**Live inputs and timing.**
- Inputs: the pinned history through 2026-10-06, plus ESPN results since.
- A target is forecast before its 06:00 UTC cutoff as soon as none of its teams is affected by an unsettled earlier
  match. This is exact for this pairwise Elo, so most of a window publishes days ahead.
- Not inputs: results of national teams in competitions ESPN does not list here (disclosed).

**First real run, in the working copy only (not live):**

| Day | Forecasts | Waiting on earlier results |
|---|---|---|
| Nov 11 | 1 | 0 |
| Nov 12 | 27 | 0 |
| Nov 13 | 38 | 0 |
| Nov 14 | 19 | 2 |
| Nov 15 | 0 | 9 |
| Nov 16 | 0 | 23 |
| Nov 17 | 0 | 52 |

The waiting ones publish as those results arrive. The first real club forecasts in the same copy: Nov 20, Athletic
Club–Espanyol, plus one Bundesliga and one Ligue 1 match.

**Activation** adds to §5: `scripts/soccer_live_setup.sh` now also downloads the pinned international history and
backfills ESPN national-team competitions from 2026-10-07. The hourly cycle refreshes them, and the forecast command runs
clubs and national teams; an international failure never stops the clubs and is alerted.
