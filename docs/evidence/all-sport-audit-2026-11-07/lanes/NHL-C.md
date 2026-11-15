# NHL-C — API, UI & operations (NHL-specific) — read-only audit, 2026-11-07

Code: SNAP (`$SP/snap`, paths below relative to repo root). Data: dev API http://127.0.0.1:8871 (PID 69042, uvicorn on SNAP
code, NBA_DATA_DIR=`$SP/work/devapi`), store `$SP/work/devapi/nhl.sqlite` (schema 35) opened with `?immutable=1`.
Saved API JSON: `$SP/work/NHL-C/game_nhl:*.json`. No code edited, no server started, ports 8100/4173 not touched.

Games traced: **nhl:2026020258** (DAL @ CBJ, 2–1 **SO**), **nhl:2026020251** (UTA @ MTL, 5–4 **OT**), **nhl:2026020253**
(CHI @ TBL, 4–1 **REG**), **nhl:2026020267** (NYR @ SEA, **upcoming** 22:00Z), plus nhl:2026020261 (OT, no forecast) and
nhl:2026020268 (upcoming, before the window).

## 1. Findings (most severe first)

### F1 MEDIUM — OT/SO is not shown with NHL final scores; regulation 3-way "Wrong" grades look like grading errors (NEW)
- Files: `backend/app/schemas.py:227-230` (`Outcome` = home_score/away_score/home_won, no decision);
  `frontend/src/pages/GamePage.tsx:159-161,176` (hero/strip "Final: A x – y H"); `frontend/src/pages/TodayPage.tsx:217-218`
  ("FINAL"); `frontend/src/components/Canonical.tsx:147` (History "Result" column: score only, no `result_label`);
  `frontend/src/components/MarketView.tsx:166` (legacy market history). The decision appears only inside the "Regraded…"
  note (`Canonical.tsx:83-87`, shown only when `regraded`). NFL ("FINAL · TIE", `NflPages.tsx:26`) and soccer ("PENS",
  `SoccerPages.tsx:29-32`) label their endings; NHL does not.
- Evidence: `curl -s ':8871/api/games/nhl:2026020251?sport=nhl'` → `outcome {'home_score': 5, 'away_score': 4, 'home_won': True}`,
  reg3 canonical `MTL in regulation 0.466 loss "Tied after regulation"`; DB `game_decisions` nhl:2026020251 = OT/4 periods.
  `/api/canonical/forecasts?sport=nhl&market=reg3` row for nhl:2026020258 carries `outcome` without decision (the SO is only
  in `canonical.grade_history[0].decision`). So the History page shows "MTL in regulation 46.6% | UTA 4 – 5 MTL | Wrong", and
  the slate card "FINAL"; nothing on screen says OT. Same for winner/puck-line/total rows of OT/SO games and for games with
  no forecast (nhl:2026020261, OT).
- Grades themselves are correct (see claim table). Why it matters: NHL users cannot tell regulation from OT/SO results, and
  a correct reg3 loss reads as a wrong grade; the reg3 market only makes sense with the decision visible.
- Fix: add `decision` (REG/OT/SO, nullable) to `Outcome` from `game_decisions`; render "Final (OT)" / "Final (SO)" in
  GameHero, MatchupStrip, Today statusLabel and every Result column; for reg3 rows show `result_label` in the History table.

### F2 MEDIUM — Canonical "Made … min before the start" is the recording time, not when the probability was computed; the winner market comparison uses that later time (NEW, cross-sport code path)
- Files: `backend/app/providers/real.py:892-893` (`_winner_quote_prob(g, c["record"]["generated_at"])`);
  `frontend/src/components/Canonical.tsx:72,101` ("made N min before the start", "Made …"); `Canonical.tsx:179`
  (text: "with a bookmaker moneyline retrieved no later than the forecast"). `game_forecasts.source_generated_at` is stored
  (`coverage.py:357`) but not in the API record.
- Evidence (NHL store): `select … avg/max((generated_at - source_generated_at) minutes) from game_forecasts where
  status='forecast' and source<>'adaptive'` → 967 rows, **avg 9.6 min, max 53.9 min** (records 635–640, nhl:2026020211:
  source 00:07:14Z, recorded 01:01:06Z). nhl:2026020229 winner (record 762): API `generated_at 20:15:13Z,
  minutes_before_start 44.8`, while its own near-tipoff card says `generated_at 19:31:33Z, 88.5 min`; the winner comparison
  therefore uses the moneyline retrieved **20:08:15Z** instead of the 19:31:31Z quote the model saw (3 canonical rows have a
  same-market quote in that gap: 762, 764, 765; 764/765 are line markets compared via their own quote id).
- Why: the displayed lead time and the "no later than the forecast" claim are off by up to ~54 min; direction is
  conservative for the model (market gets a newer quote), so no look-ahead in the model's favour. Fix: expose
  `source_generated_at` as the forecast time ("computed …, recorded …") and use it for `_winner_quote_prob`.

### F3 MEDIUM — Whole-host job outages left three NHL games with no forecast in any market; nothing alerts (NEW instances; host-asleep pattern noted earlier in HANDOFF:727 for Oct 6–8)
- Evidence: no `job_runs` rows at all in **both** NHL and NBA stores for 2026-11-04T22:09:29Z → 2026-11-05T01:04:45Z and
  2026-11-06T00:46:18Z → 2026-11-06T23:38:48Z (`select count(*) from job_runs where started_at between
  '2026-11-06T00:47' and '2026-11-06T23:38'` → NBA 0; NHL only the 23:38 restart rows). Games nhl:2026020247, 248 (00:30Z
  Nov 5) and nhl:2026020261 (02:30Z Nov 6) have 0 `game_forecasts` rows; API states `missing: "not recorded: no forecast
  cycle ran within two hours of the start"`. The tick plist (`com.nba-predictor.nhl-odds`, StartInterval 60) was the only
  scheduler; cause (sleep/shutdown) not verified (`pmset -g log` returned nothing usable).
- UI is honest after the fact (missing reason, coverage count), and the freshness bar would show "Source data stale" only
  while someone is viewing. Fix: host heartbeat/alert or `caffeinate`/power settings; NBA-C lane owns launchd.

### F4 LOW — NHL injuries source note on the API is wrong whenever the API process lacks the jobs' env (NEW)
- `backend/app/providers/real.py:408-409` reads `NHL_INJURY_CAPTURE` from the **API** process. The live API plist
  (`~/Library/LaunchAgents/com.nba-predictor.api.plist`) has no `NHL_INJURY_CAPTURE`; `com.nba-predictor.nhl-odds` and
  `nhl-daily` set it to 1. Dev API (same absence) shows: `/api/sources?sport=nhl` → "NHL injuries (ESPN page) … fresh,
  last_updated 2026-11-07T20:07:01Z … not captured live: NHL_INJURY_CAPTURE is not 1" — fresh data and "not captured" in the
  same row. Live :8100 not queried (forbidden); inferred from code + plist. Fix: derive capture state from stored capture
  files/job details, not the API env.

### F5 LOW — NHL "Real data" popover says "No goalie, injury or line-up information" (`frontend/src/components/Layout.tsx:122`)
- The game page shows an ESPN goalie card (`GamePage.tsx:311-330`) and the store holds injury snapshots; the statement is
  true only of the models. Also omits that odds/goalies come from ESPN. Fix: "Forecasts use no goalie, injury or line-up
  information; goalie listings are shown for reference."

### F6 LOW — Freshness bar covers only the schedule/results refresh; odds/goalie fetch failures are invisible (partly known, HANDOFF:927)
- `backend/app/freshness.py:136-150` (`last_failed_at`, `failing` from `job='refresh'` only). `/api/freshness?sport=nhl` →
  `last_failed_at 2026-10-27T19:41:12Z`, while job_runs 3636 `odds failed 2026-11-07T07:50:25Z ConnectError`. "Forecast
  made" is store-wide `MAX(game_forecasts.generated_at)`, not the viewed slate's.
- Same moment, two verdicts: `/api/freshness` `source_state: stale` (2×60 s+5 min) vs `/api/sources` "NHL schedule &
  results … fresh" (60 min rule, `real.py:288`). Fix: show odds last success/failure in the bar; one staleness rule.

### F7 LOW — Sport selection: unknown URL values silently become NBA in the UI; NHL game links need ?sport=nhl (NEW)
- API is strict: `sport=NHL|bogus|''` → 422 on `/api/meta`, `/api/games/{id}`, `/api/freshness` (pattern
  `^(nba|nhl)$`, `main.py:26`; `^(nba|nhl|nfl)$`, `main.py:118`); NHL in demo → 404 (`main.py:83-85`); missing NHL store →
  503 (TestClient, empty NBA_DATA_DIR: `/api/meta?sport=nhl 503 "No NHL data store found…"`), no NBA fallback. **Missing**
  sport = NBA by design (`main.py:26`); `GamesResponse`/`GameDetail` carry no `sport` field (`schemas.py:316-339`), so a
  client cannot check which store answered.
- UI: `leagueFrom` (`frontend/src/components/competition.ts:29-32`) maps anything but exact `nhl|nfl|soccer` to NBA, so
  `?sport=NHL` shows NBA data under the NBA label — contrary to the comment "An unknown value is never silently replaced by
  data of another sport" (`Selection.tsx:6-8`). `/games/nhl:2026020258` without `?sport=nhl` → NBA store →
  `404 "Game nhl:2026020258 not found"` under the NBA header; soccer ids infer their league, `nhl:` ids do not.
  Carried links (`useCarriedSearch`, Layout nav, Today/History/Game links, date chip and setDate) keep `sport=nhl`.
- Fix: infer NHL from the `nhl:` id prefix like soccer; show an "unknown sport" notice instead of the NBA default; add
  `sport` to games/game responses.

### F8 LOW — NBA wording on NHL screens
- "Tipoff"/"tip-off"/"Near-tipoff": `GamePage.tsx:161,176,395`, `TodayPage.tsx:37,259`, `Badges.tsx:17-18`,
  `HistoryPage.tsx:77`. "play-in": `Canonical.tsx:210` and `/api/sources?sport=nhl` canonical note ("regular-season,
  play-in or playoff games"). `forecast_note` "No pre-game archive forecast was recorded before the cutoff" on an upcoming
  NHL game (nhl:2026020267, `real.py:136`). No quarter/4-period or "point spread" text found in NHL paths; markets use
  API titles ("Puck line", "Winner (incl. OT and shootout)").

### F9 LOW — latent/hygiene
- `freshness.revision()` (`freshness.py:118-124`) omits `goalie_snapshots`, `market_forecasts`, `predictions`: a goalie
  change without a new quote does not refresh the open game page (in practice the same odds fetch appends market_quotes).
- Research endpoints outside `get_provider` accept markets the sport lacks: `/api/research/combinations?market=reg3`
  (no sport) → 200 `sport: nba … status no_stored_forecasts` (`main.py:727,746,763,786`), unlike `/api/canonical/*` (404).
- Today canonical block shows "market X%" beside the model probability using the latest pre-start quote (may be newer than
  the forecast), with no time (`TodayPage.tsx:249` → `Canonical.tsx:65-72`).

## 2. Claim table
| Claim | Status | Evidence |
|---|---|---|
| "Which sport's data store to read. Never falls back to another." (`main.py:26`) | verified for invalid values; missing = NBA by design | curl matrix (§3) |
| "An unknown value is never silently replaced by data of another sport" (`Selection.tsx:6-8`) | contradicted | `leagueFrom` code: `?sport=NHL` → nba |
| reg3 "Settles on the final score (regulation only; OT/SO games are a draw)" (meta note) | verified | 258 SO → `result_side mid`; 251 OT → mid; 253 REG → TBL win (`markets.py:73-77`) |
| Puck line/total "overtime and shootout count (shootout winner +1 goal)" | verified on samples | 258: 2–1 → CBJ +1.5 win, Under 5.5; 253: TBL −1.5 win (4–1), Over 6.5 loss; 251: Over 6.5 win (9) |
| API scores = DB | verified | 258 2–1/SO, 253 4–1/REG, 251 5–4/OT, 261 2–3/OT; `result_revisions` 15997 (258 rev 1 SO) |
| Canonical probabilities = DB rows | verified | 258 ids 936–941, 253, 267: p_a/p_mid/p_b, lines, line_source, quote ids match `game_forecasts` |
| Goalies: "As listed by ESPN when we last checked before now" | verified | API = latest `goalie_snapshots` ≤ now (258: confirmed DeSmith/Talbot 23:52:20Z; 267: SEA expected Grubauer, NYR confirmed Korpisalo 20:07:00Z) |
| Moneyline shown = latest quote ≤ start | verified | `real.py:139-144`; 258 retrieved 23:52:20Z < 00:00Z |
| Model-only lines labelled | verified | team totals `line_source model`, quote null → "Line x · model-only", "No bookmaker comparison (model-only line)" |
| "with a bookmaker moneyline retrieved no later than the forecast" (`Canonical.tsx:179`) | contradicted (1 NHL record) | F2, record 762 |
| "Made N min before the start" | contradicted as computation time | F2 |
| NHL injuries "not captured live: NHL_INJURY_CAPTURE is not 1" | contradicted for live jobs | F4 |
| "No goalie, injury or line-up information" (Layout) | contradicted | F5 |
| Upcoming states honest | verified | 268: all markets `upcoming`, "recorded within two hours of the start"; 267: 6 canonical rows 20:20:18Z, pending |

## 3. Checks run
| Command | Expected | Actual | Result |
|---|---|---|---|
| `curl ':8871/api/meta?sport=nhl|NHL|bogus|'` and no sport | nhl 200; others 422; missing → nba | 200 nhl; 422 ×3; missing 200 `sport: nba` | pass (missing=NBA by design) |
| `curl ':8871/api/games/nhl:2026020258'` / `?sport=nba` / `?sport=NHL` | no NBA data | 404 / 404 / 422 | pass |
| `curl ':8871/api/freshness?sport=NHL'`, `?sport=soccer` | 422 | 422, 422 | pass |
| `curl ':8871/api/canonical/forecasts?market=reg3'` (no sport) | 404 | 404 "not defined for NBA" | pass |
| TestClient, `NBA_DATA_DIR=$SP/work/NHL-C/emptydata`, `/api/meta|games|freshness|games/{id}?sport=nhl` | 503, no fallback | 503 ×4 | pass |
| `curl ':8871/api/games/<5 ids>?sport=nhl'` vs `sqlite3 'file:…/nhl.sqlite?immutable=1'` (games, game_decisions, game_forecasts, result_revisions, goalie_snapshots, market_quotes) | match | match (except F1/F2 display gaps) | pass |
| gap query `game_forecasts.generated_at - source_generated_at` | ≈0 | avg 9.6, max 53.9 min | fail (F2) |
| job_runs gap queries NHL+NBA | continuous ticks | two outages (F3) | fail |
| `curl ':8871/api/sources?sport=nhl'`, `/api/freshness?sport=nhl` | consistent | stale vs fresh; injury note contradiction | fail (F4, F6) |
| `curl ':8871/api/canonical/ranked?sport=nhl&date=2026-11-07'` | no-skill markets not "strong" | strong False for all; rule text says line markets excluded | pass |
| Python scan of `<table` without `table-wrap` in NHL screens | — | list in §5 | done |
Not run: browser rendering (lead's lane); live API :8100 (forbidden); focused pytest (no test covers F1/F2 — no fix to test);
`pmset` root cause of outages (no usable output).

## 4. Open questions / cross-sport
- Lead's dev instance has **no `SNAPSHOT_AT`** in `$SP/work/devapi`, so `/api/freshness` says `mode: live`, `next_check_at`
  in the past, and the UI on :5271 will show "Live data" without the DEVELOPMENT COPY banner — not what `scripts/dev.sh:154`
  produces; account for it in browser checks.
- F2 is in shared code (`real.py` canonical comparison); NBA likely has the same recording-vs-computation gap — not checked.
- F3 outages hit NBA too (NBA store has no job_runs in the same windows) — for NBA-C/launchd.
- 25 NHL canonical "missing" rows from 2026-10-20 cite "stale results … last: 2026-10-20T04:42:58Z" — not chased (NHL-A/B).

## 5. NHL wide tables without a horizontal-scroll wrapper (for the 390 px check)
- `frontend/src/pages/PerformancePage.tsx:154` — baselines, 4 cols (legacy winner record).
- `frontend/src/pages/PerformancePage.tsx:170` — calibration, 4 cols with inline bars (most likely to overflow).
- `frontend/src/components/MarketView.tsx:211` — historical test, 5 cols (renders only when `reference` is set; NHL
  `reference` is null today for reg3/spread/total).
- `frontend/src/pages/GamePage.tsx:341` — archive model vs market, 3 cols (narrow).
- 2-col compare tables (low risk): `Canonical.tsx:185,198`, `MarketView.tsx:197`, `PerformancePage.tsx:198`.
- `.table.history` tables `Canonical.tsx:135` (7 cols), `MarketView.tsx:155` (8), `HistoryPage.tsx:176` (8) are unwrapped
  but collapse to cards at ≤580 px (`styles.css:292-295`); between 581–800 px they have no scroll wrapper.
- All tables in Tracking, RankedPicks, ThresholdOverview, PaperRules, Coverage, ModelAgreement, Skill, Training, Adaptive,
  Research, CandidateForecasts, Overview are inside `.table-wrap`.
