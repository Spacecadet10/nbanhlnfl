# Smoke test — milestone 1 (demo data)

Date: 2026-09-30. Tester: Claude (Opus 5.5) via Claude-in-Chrome on macOS Chrome.
All data is synthetic (`run_type: "demo"`); nothing here measures real prediction accuracy.

## Environment / commands
- Backend: `backend/.venv` (CPython 3.12.12 via uv), `cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`
- Frontend: Node 22.23 / npm 10.9, `cd frontend && npm ci && npm run dev -- --host 127.0.0.1 --strictPort`
- URLs: app http://127.0.0.1:5173/ · API http://127.0.0.1:8000/api/health

## Automated checks (actual results)
- `backend/.venv/bin/python scripts/check_project.py --ready` → exit 0: typecheck OK, `vite build` OK,
  pytest `12 passed, 1 warning` (third-party Starlette/httpx deprecation warning).
  First run needed `.omc/` moved aside (`FAIL: Unexpected top-level directory: .omc`); at user request `.omc`
  (oh-my-claudecode plugin state) was then added to the guard's SKIP list and .gitignore, and the
  in-place `--ready` run passed (exit 0, 12 passed).
- `npm ci` from lockfile: OK.

## API via curl (actual)
- `GET :8000/api/health` → `{"status":"ok","run_type":"demo","api_version":"0.1.0"}`
- Same through Vite proxy `:5173/api/health` → identical.
- `GET :5173/api/games?date=2026-10-30` → 4 scheduled demo games.
- `GET :5173/api/games/nope` → 404. `GET :5173/history` → 200 (SPA route).

## Browser — desktop (1512×~780 CSS px viewport)
| Flow | Result |
|---|---|
| Today `/` loads demo "today" 2026-10-30: 4 cards, tipoff times, prob bars, market % or "No market odds", Demo badges, global Demo banner | Pass |
| Source status: schedule/results "fresh", odds "stale" (amber) | Pass |
| Click card → Game detail: model vs market, prediction timestamp "(before tipoff)", model version, projected score withheld, team comparison | Pass |
| Back link → Today with `?date=`; "‹" → 2026-10-29 shows Final scores; "Demo today" link returns | Pass |
| Empty state: `/?date=2026-10-25` → "No games on this date. Demo data covers …" | Pass |
| Error state: `/games/does-not-exist` → "404: Game does-not-exist not found" + Try again + back link | Pass |
| Performance: note banner, 32 games, small-sample warning, 3 metric tiles, calibration table with bars, market comparison (24/32, 75% coverage), metric definitions | Pass (calibration bar scaling bug found and fixed, re-verified) |
| History: 36 rows newest first; Team=BOS → URL `?team=BOS`, 5 rows all containing BOS (pre-fixture-tweak data) | Pass |
| Backend stopped → Today and History show "500: API unavailable. Is the backend running?" + Try again; backend restarted → Try again loads 36 rows | Pass |
| Console errors on Performance page | None |

## Browser — phone (390×844)
The Chrome window could not be resized below desktop width (resize_window reported success but the viewport
stayed 1512px), so phone width was emulated with a same-origin 390×844 `<iframe>` of the app. This exercises
CSS media queries and layout, but **not** a real mobile device, touch input, or mobile browser chrome.
| Screen | Result |
|---|---|
| Today: stacked nav tabs, banner wraps, full-width date picker, single-column cards | Pass, `scrollWidth` = 390 (no horizontal scroll) |
| Game detail (`demo-20261028-DAL-PHI`): stacked facts, comparison tables fit | Pass, 390 |
| Performance: tiles stack, calibration table fits | Pass, 390 |
| History: rows render as labelled cards; filters wrap; team+date filter → 2 rows; empty filter (`2026-10-25`) → empty message | Pass, 390 |
| Unknown game → error state | Pass, 390 |

## Not verified
- Real phone hardware / iOS Safari / Android Chrome; touch-only help tooltips (`?` uses `title`, hover only).
- Dark mode (styles exist, not visually checked).
- Production build served by a static host (only `vite build` success was checked).
- Accessibility audit beyond basic roles/labels.


## Codex follow-up — 2026-09-30
- Confirmed existing .omc-only guard exception and ignore entry; no broader guard exclusions added.
- Fresh npm audit found 4 affected packages (3 moderate, 1 high), superseding the earlier count.
- Updated Vite to 7.3.6 and react-router-dom to 7.18.4 using npm; install audit reports 0 vulnerabilities.
- Readiness command passed: TypeScript, Vite production build, 12 backend tests. Existing TestClient deprecation warning remains.
- Replaced Game page title-only help with native details/summary disclosures, accessible names, keyboard focus styling, and 44px targets. In-app browser at 390×844: Market help opened by click and closed with Enter; expanded text fit without horizontal overflow (390px scrollWidth).
- Navigated Today → Game → Performance → History → Today after the router update.
- Visually checked dark palette on Game, Performance, History at 390px and Today at 1280px. For this check only, temporarily forced the existing dark CSS media block on; restored prefers-color-scheme afterward. No OS settings changed. Native controls now declare matching color-scheme.
- Real touch hardware, mobile Safari, and OS-driven automatic theme switching remain unverified. These checks are viewport/browser checks, not physical-device certification.
- README Node requirement updated for Vite 7. Backend/frontend remain demo-only.

# Milestone 4: real data in the app (2026-09-30, Claude)
API in real mode (`NBA_API_MODE` unset), Vite dev server, Chrome via Claude-in-Chrome.

## Local scheduled jobs — 2026-09-30 (Codex)
- Installed `com.nba-predictor.odds` (RunAtLoad + every 900 seconds) and `com.nba-predictor.daily` (22:00 local) as user LaunchAgents.
- `launchctl print gui/501/com.nba-predictor.odds` and `...daily` confirmed both loaded with expected triggers.
- The odds agent's immediate run wrote `data/odds-launchd.log`, exited 0, and skipped because no scheduled game tipped within 36 hours.
- Not a hosted/always-on service: this Mac must be awake and the checkout path must remain `/Users/andry/Desktop/nba-predictor-sprint1`.
- Plists are in `scripts/launchd/`; per-job logs are in `data/`. Daily run awaits the next 22:00 local trigger.

## Real-data job run (actual)
`app.cli migrate` applied 0003_job_runs. `app.cli daily` → exit 0:
- refresh: success, 2 requests (months 202609 and 202610). 155 October games seen; 0 new or updated games; 52 new odds quotes (new DraftKings snapshot).
- predict: success. **0 eligible games**: the 2026-27 regular season starts 2026-10-20, and only preseason games
  (not forecast) fall in the 7-day horizon. No live forecasts exist yet; the first will be recorded about 2026-10-13.
- score: success, 0 live forecasts. All game and prediction rows were unchanged by the run.

## API via the proxy (actual)
- `/api/health` → `{"status":"ok","mode":"real","api_version":"0.2.0"}`. `/api/meta` → default_date 2026-10-03,
  1,575 dates, 30 teams, primary model elo-carry=0.75-home_advantage=50-k=15-mov=True, last refresh 2026-09-30T14:54:23Z.
- 2026-10-20: 3 regular-season games with DraftKings lines and the note "Forecasts are recorded within 7 days…";
  2026-10-03: 1 preseason game with the "outside the model's scope" note.
- 2026-03-15: 7 finals with **Backtest**-labelled predictions. Game detail stats are as of the 00:00 ET cutoff.
- `/api/performance?run_type=backtest` reproduces the final test from the ledger: 2,643 games, accuracy 67.2%,
  Brier 0.21052, log loss 0.60878, home-rate 0.24752, constant 0.25. `run_type=live`: 0 games.
- `/api/predictions?run_type=backtest`: 6,605 (3,962 validation + 2,643 test); `live`: 0.
- Found and fixed: backtest history took 4.0 s (a per-row team-cache check); now 0.09–0.11 s.

## Browser (Chrome, same-origin iframes; the window could not be resized)
Desktop 1280 px: Game day (future, preseason, past backtest, empty date), game detail (backtest + 404), Performance
(Live empty state, Backtest with baselines, calibration, "no market odds"), History (Live empty, Backtest + BOS filter:
491 rows). Phone 390 px: the same 8 routes all at scrollWidth 390 (no horizontal scroll). Screenshots checked: Performance
backtest (desktop), backtest game detail (desktop), game cards and History cards (phone). No console errors.
- Found and fixed: the empty-state date range lacked years ("Oct 4 – Oct 31"); Performance and History dates lacked
  years; the History toggle said "Final test" although backtest history includes validation (now "Backtest (retrospective)").
- Demo mode (`NBA_API_MODE=demo`, 390 px): DEMO WORKSPACE pill, demo banner and Demo badges on every screen, no
  Live/Backtest toggle, no real-data banner.

## Not verified
- A live forecast shown in the browser: none can exist before about 2026-10-13, and timestamps were not faked. The
  lifecycle (forecast → rerun → new version → cutoff → result → score → API) is covered by tests/test_live.py.
- Real devices and mobile Safari; unattended scheduling (no system cron was installed).


## Codex frontend redesign — 2026-09-30
- Updated existing UI in place with Courtside styling: fixed desktop sidebar, responsive top navigation, charcoal/amber palette,
  daily-slate hero with inline court illustration, redesigned team matchup cards, stronger hierarchy across detail/performance/history.
- Preserved real/demo mode banners, forecast status labels, source freshness, loading states, and API-backed page flows.
- Vite production build and TypeScript check passed during the change; backend suite was also run by the required readiness command
  at that point (75 passed; subsequent M4 handoff reports 89 passing after later edits).
- Browser checked real-data Today (1280 and 390 px), Game detail (390 px), Performance (390 px), History (390 px). No horizontal
  overflow at 390px. Real provider correctly displayed preseason game without a forecast; pages kept Live and Backtest separate.
- Mobile screenshots showed responsive menu wrapping, cards/forms, empty live-history state, and game detail tables. Physical device
  and Safari remain unverified.
- Local app relaunched for preview: frontend http://127.0.0.1:5173/ and backend http://127.0.0.1:8000. In-app browser left on Today.

## Confidence filter (2026-10-01, Claude)
Servers: API restarted with the current code (`backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`, real mode); Vite dev
server already running on 5173. Browser: Chrome via the automation extension.

Desktop (window 1512 px wide; the window could not be resized smaller):
- `/history?run=backtest`: default filter 70%+ pressed; record "2027 picks at 70%+ · Settled record 1570–457 (2027 settled) · Observed win
  rate 77.5%"; 500 rows shown, lowest model confidence in the table 70.0%; columns Pick / Model confidence / Result / Outcome of pick; both
  home and away picks present. Switching to 80%+, 60%+ and All updated the URL, the record (653, 4,126 and 6,605 picks) and the rows.
- `/history` (live): "No live picks at 70%+ model confidence." with a "Show all picks" button (no live forecasts exist yet).
- `/?date=2026-03-10`: "4 of 11 games selected at 70%+ model confidence (11 with a forecast)"; 4 cards, each with "Pick: TEAM · model confidence
  X%" (including an away pick, DET 80.0%); Live record line "0 picks … no observed win rate" and a separate Backtest record line.
- No horizontal overflow; no console errors.

Phone width (390 px): checked by loading the app in three 390-px-wide same-origin frames, because the browser window stayed at desktop width.
- Today with a date, History (backtest) and Today on the default date: `innerWidth` 390, no horizontal overflow (scroll width 390), filter
  wraps to two rows of 34-px buttons, record lines wrap, history rows render as stacked cards.
- Default date (2026-10-03, one preseason game without a forecast): "0 of 1 game selected … No picks at 70%+ model confidence on this date.
  None of these games has a forecast yet." with "Show all 1 game".

Not verified in a browser: demo mode (`NBA_API_MODE=demo`) with the filter (covered by an API test only); keyboard-only operation of the filter.

## Near-tipoff forecasts (2026-10-01, Claude)
Real store, real clock: `app.cli predict-now --dry-run` and `app.cli predict-now` both returned status `skipped` (no eligible game tips off
within 24 hours; refresh, odds and boxscores steps were each skipped by their own rules: quota or nothing due). One `near_tipoff` job row,
0 forecasts, ledger unchanged at 27,744. A real refresh-then-forecast on a live game has **not** happened yet (first possible 2026-10-19/20).

Rehearsal on a scratch copy of the database (never the real store), fixed clock 2026-10-20T18:00Z, with a results refresh recorded at 17:50Z:
- 3 forecasts created for the opening-night games (BOS@DET 60 min before tipoff, PHI@NY, OKC@SA), each with the market quote available then;
  at 19:05Z the first game was refused `after_tipoff` and the others `stale_results`. The 43 night-before Elo rows were untouched.
- A made-up final score for the first game (scratch only) gave: scored 1, record 1–0, same-game comparison with the night-before forecast and the market.

Browser (temporary API on :8001 and Vite on :5174 pointed at the scratch copy; window about 514 px wide, plus 300-px frames):
- Today `?date=2026-10-20&conf=all`: 3 cards, each with the night-before forecast and a separate "Near-tipoff" block ("made 60 min before
  tipoff", pick, model confidence, "Correct" on the settled game). At 70%+ only PHI@NY is selected.
- Game page: "Near-tipoff forecast" card with pick, time made, data freshness (refresh time, results used, newest result), market at that
  time, model version and the injuries limitation; the night-before "Win probability" card is unchanged above it.
- History `?policy=near_tipoff`: 3 rows under the near-tipoff model version, record "1–0 (1 settled)"; default History shows only the
  night-before model version (43 rows). Performance `?policy=near_tipoff`: "Near-tipoff live forecasts", 1 scored game, small-sample warning.
- No horizontal overflow at 514 px or in 300-px frames; no console errors. Not verified at full desktop width for these new blocks, in demo
  mode, or with a keyboard only.

## Near-tipoff review fixes (2026-10-01, Claude)
Scratch copy again (temporary API :8001, Vite :5174), desktop window 1280 px, `/?date=2026-10-20`, with one scratch-only near-tipoff row added so
the two policies differ (OKC@SA: night-before SA 57.9%, near-tipoff OKC 80.0%):
- "Night-before forecasts" selected, 70%+: "1 of 3 games selected … (3 with a night-before forecast)"; one card (NY 77.9%), with the line
  "Near-tipoff forecast (not used by this view): NY 77.9%"; Live and Backtest record lines.
- "Near-tipoff forecasts" selected, 70%+: "2 of 3 games selected … (3 with a near-tipoff forecast)"; cards NY 77.9% and OKC 80.0% (an away
  pick that the night-before view does not select), each showing "made N min before tipoff" and the night-before forecast as "not used by
  this view"; only the Live near-tipoff record line (no Backtest line).
- No horizontal overflow, no console errors. Not re-checked at phone width or in demo mode after this change.

## Phone width and demo mode after the near-tipoff fixes (2026-10-01, Claude)
Temporary servers, stopped afterwards: scratch copy on API :8001 / Vite :5174, demo mode (`NBA_API_MODE=demo`) on API :8002 / Vite :5175.
Checked in 390-px-wide same-origin frames (the browser window does not shrink to phone width), `innerWidth` 390 in every frame.

Scratch copy, `/?date=2026-10-20`:
- Night-before, 70%+: "1 of 3 games selected … (3 with a night-before forecast)"; card NY 77.9% with "Near-tipoff forecast (not used by
  this view): NY 77.9%"; Live record "15 picks at 70%+ · no settled games" and Backtest record "2027 picks · 1570–457 · 77.5%". With three
  frames loading at once the record lines appeared a few seconds after the cards.
- `&policy=near_tipoff`, 70%+: "2 of 3 games selected … (3 with a near-tipoff forecast)"; cards NY 77.9% (made 300 min before tipoff) and
  OKC 80.0% (made 60 min before tipoff); only the Live record line. `&conf=80`: 1 of 3 selected (OKC 80.0%).
- Policy toggle 303 px wide on one row; confidence filter wraps to two rows of 34-px buttons.

Demo mode:
- Today (default date 2026-10-30), 70%+: "2 of 4 games selected", picks BOS 77.7% and DEN 73.1%; one Demo record line "13 picks at 70%+ ·
  9–2 (11 settled) · 81.8%" (synthetic data, not a model result); no policy toggle and no near-tipoff blocks.
- `/?conf=all&policy=near_tipoff`: the policy is ignored in demo (4 games, "4 with a night-before forecast", Demo record 20–12 of 32 settled).
- `/history?conf=65`: 15 rows, lowest confidence 67.9%, Demo record 10–3 (13 settled).

No horizontal overflow in any frame (scroll width 390); no console errors. Desktop screenshots of the same scratch views (1512 px) were
taken for the user and are not stored in the repository.

Still not verified: keyboard-only operation; a real phone or real phone browser (frames only); a real near-tipoff forecast on a real
upcoming game (first possible 2026-10-19/20).

## NBA + NHL and markets (2026-10-01 UTC, Claude)
Temporary servers from the `multisport-dev` worktree against the real stores: API `:8011` (`NBA_API_MODE=real`,
`NBA_DATA_DIR=<repo>/data`), Vite `:5181` (`API_PROXY_TARGET=http://127.0.0.1:8011`). Stopped afterwards.
- `curl /api/health` → `{"status":"ok","mode":"real","api_version":"0.3.0"}`; `/api/meta?sport=nhl` → sport nhl, primary model
  `hockey_elo-carry=0.75-home_advantage=30-k=4-mov=True`, all six NHL markets available; `/api/meta` (NBA) unchanged primary Elo.
- `/api/games?sport=nhl&date=2026-10-01&market=spread`: 8 games, 8 forecasts; PHI@NJD at the DraftKings line −1.5 (model NJD −1.5
  23.5%, de-vigged book 36.9%).
- Browser, 1400 px: NHL Today (puck line) 8 cards with line source, sides, pick, bookmaker comparison at the same line and
  limitations; game detail NHL:2026020009 lists 5 market forecasts and "Goals scored / game" stats; NHL Performance backtest =
  2,792 games, 56.2%, Brier 0.243 (home rate 0.248), matching docs/NHL_BASELINE_REPORT.md; market history (home team goals) 47 rows;
  clicking Performance from History kept `sport=nhl&market=team_total_home`; the NBA toggle reset market and filters and showed the
  NBA banner and unchanged NBA winner view.
- 390-px same-origin frames (`innerWidth` 386): NHL Today reg3 and spread, NHL game detail, NHL history (total), NHL performance
  (spread and backtest), NBA Today 2026-10-20: scroll width = frame width, no error states. Light theme applied in a frame
  (background #f3f8f7). No console errors.
- Found and fixed during the check: `/api/meta?sport=nhl` took 19.8 s (the sealed-decision data check reloaded every validation game
  six times per request); now cached per store state, 0.2 s cold / 6 ms warm. Transient "market not defined" and "Demo predictions"
  texts while metadata loaded are no longer shown. Page title is now "Courtside".
- Not verified: keyboard-only use, a real phone, screen readers; settled market forecasts (none settled yet).

### In-progress verification notes — 2026-10-02
Two readiness attempts encountered stale exact-key assertions in `test_daily_runs_shadow_steps_only_when_enabled`: first the disabled branch expected only refresh/score/policy, then the enabled branch at line 204 also omitted the new `two_hour_score` and `market_score` output keys. Both branch expectations were reviewed and updated; the actual assertions that no old shadow job runs remain. This is a test-contract change, not suppression of scoring errors. Final results below supersede these failed checks.

### Two-hour forecasting, agreement and tracking — Codex, 2026-10-02
- `backend/.venv/bin/python scripts/check_project.py --ready`: exit 0, **321 passed**, TypeScript typecheck and Vite build passed (`/tmp/courtside-acceptance.log`). Existing Starlette deprecation and joblib core-count warnings remain. After correcting two obsolete UI messages, `npm run build` from `frontend/`: exit 0. An initial build invocation from the repo root failed because package.json intentionally lives only in frontend; no scaffold/change was made in response.
- Tests cover same-cohort agreement, missing/untrained models, abstentions, immutable retries, exact two-hour boundary, late calculations, market window, primary isolation, separate definitions, threshold denominators, late/stale injury PDFs, incomplete minutes, unknown players, historical injury cutoffs and unsubmitted reports. Existing lifecycle/model/leakage/market tests remain enabled.
- Real databases: migration 0015 applied. SQLite backups made before the timing change at `/tmp/courtside-before-two-hour-{nba,nhl}.sqlite`. Every old prediction/market row compared field-for-field and unchanged: NBA 27,744 winner / 0 market; NHL 8,668 winner / 480 market. Zero new two-hour records at verification. The earlier 196 NHL research rows are preserved as night-before archive, never relabelled.
- `.venv/bin/python -m app.cli availability-prepare --seed-season 2026`: 1,002 historical facts prepared without evaluation; free prior-season player file downloaded, GitHub SHA-256 `7a4868da67ae7f6ebfba23389e04220be2e3f69d2e56a3942c02aa56f1becc89` matched, raw_id 102/fetch_id 105. Live minute loader finds 591 players. Current-report lookup correctly refuses because no report was actually received within the past hour. No invented injury-aware forecast.
- Real `jobs.forecast_due` for both stores skipped forecasts because no eligible game was within two hours; NHL also hit the existing results-refresh quota. Read-only real-store TestClient calls to `/api/thresholds` and `/api/agreement-performance` for each sport returned 200 with empty two-hour groups.
- launchctl: NBA odds 900 seconds, NHL odds 1,800 seconds; both daily agents and injury agent loaded; all five last exit codes 0. Existing jobs invoke this checkout, so no duplicate agent or installation was added. Must remain awake/logged in.
- Browser at http://127.0.0.1:5173: NBA Today at 1280×900 defaults to Within 2 hours; no horizontal overflow. NHL game `games/nhl:2026020017?sport=nhl` at 390×844 shows explicit no-two-hour-forecast, separate winner-agreement missing state, archived night-before probability. NHL Performance (reg3) shows two-hour empty market/threshold record and separate historical test; dark theme legible. NHL History at 390×844 shows Live + Within 2 hours, working controls and correct empty text; no horizontal overflow. Light theme restored and temporary viewport override reset; original game page restored.
- Current limits: no eligible real game during verification, so no real two-hour forecast-to-final lifecycle or populated two-hour threshold table was browser-verified. Synthetic tests cover storage/settlement, but prospective accuracy is unknown. Demo API regression tests pass; demo mode was not separately browser-checked this turn. No paid source, deployment, new historical evaluation, model promotion or threshold tuning.

## Model agreement, combination and selection (2026-10-02 UTC, Claude)
Worktree `../nba-predictor-selector` (branch `selector-dev`), merged into `shadow-season` only after these checks.
- **Tests:** `backend/.venv/bin/python -m pytest -q` from `backend/` — see HANDOFF for the final count. New: `tests/test_selection.py`
  (candidates' leakage/own-label isolation, readiness, draw inflation equals the Poisson grid at w = 0, coherence, stacker,
  selector minimum evidence / abstention / Bonferroni / class-balance reference / bookmaker reference / hysteresis / reasons,
  study machinery, out-of-fold refusal, regulation draw and integer push), `tests/test_research_live.py` (every market at the
  primary's exact line and quote, retries add nothing, not-ready members missing not substituted, missing primary market,
  two-hour and start-time triggers, append-only, reschedule → void, calculation finishing after the start, frozen weekly decision
  only used after it was written, evidence cutoff, one market per game with reasons, primary forecasts kept when research fails,
  off switch, real API states), `tests/test_api_research.py` (demo contract). `npm run typecheck`, `npm run build` pass.
- **Bug reinjection** (scratch copy of the backend): evidence ignoring its cutoff, stacker trained on its own season, a missing
  member filled from the primary, the cohort two-hour lower bound removed, decisions recomputed within a week → each fails a test.
  Regulation draw scored as a push was **not** caught at first; `test_regulation_draw_is_an_outcome_and_integer_lines_push` was
  added and fails under that reinjection.
- **Rehearsal with real data on scratch copies** (fixed clock, refresh/odds/box/injury network steps stubbed, a refresh row
  inserted only in the copy): NHL `2026-10-02T21:00Z` → 2 games, primary 2 + 10 market rows, 8 winner research rows, 12 complete
  cohorts (puck line and total at DraftKings lines, team totals at model-only lines), 12 selections "insufficient evidence".
  NBA `2026-10-20T21:30Z` → 1 game, 5 cohorts, 169 s total; the injury-aware candidate reported missing (facts file not in the
  scratch dir), never substituted. No real store was written.
- **Browser** (Chrome; isolated servers: demo API :8011 + Vite :5181; real-mode API :8012 on migrated scratch copies + Vite :5182):
  Today shows the research line per game (demo: "Selected: BOS win (Winner, 80.0%)"); Game → Model selection card with each
  market's members, missing models with reasons, equal average, stacker, bookmaker (where quoted), agreement counts, line and
  source, minutes before start, rule in force and an audit section; Performance → forward research (sources, agreement vs
  confidence at matched count, selector record, thresholds, by week, bookmaker comparison, weekly decisions) and the collapsible
  retrospective study labelled exploratory; History → Research recommendations. Real-mode NHL pages show explicit empty states
  ("No research forecast for this game…", "No research selections recorded yet."). No console errors. Phone: the window cannot
  shrink below 595 px, so 390-px layout was measured in a 390-px iframe of each screen: page scroll width 390 on Today, Game,
  History and Performance (wide tables scroll inside their own containers); light theme checked visually at 390 px, dark at
  desktop. Tab closed, window restored to 1280×900, temporary servers stopped.
- **Fixed during the check:** raw rule text in the pick line, clipped missing-model reasons, an unexplained Elo denominator in
  demo data (exact 50% forecasts abstain; now shown as "exact 50% (no pick)").
- **Integration (live checkout, 2026-10-02 ~11:42 UTC):** backups `data/backups/{nba,nhl}-before-0016-20261002.sqlite`, fast-forward
  to 4a3fce4, `app.cli --sport {nba,nhl} migrate` applied 0016; all 18 pre-existing tables compared row by row: identical except
  schema_migrations (+1). First real NBA odds run on the new code (11:50 UTC): success; forecasts skipped, "no eligible game
  within two hours"; 5 selector decisions written with "insufficient evidence". Readiness: `backend/.venv/bin/python
  scripts/check_project.py --ready` exit 0 — structure guard, typecheck, build, **353 passed** (the first attempt failed on
  `.claude/scheduled_tasks.lock`, a Claude Code scheduler lock created by this session; removed, guard unchanged).
- **Not verified live yet:** a real research cohort → settlement → weekly decision lifecycle (first NHL window tonight from
  ~20:30 UTC; first scheduled decision 2026-10-05 14:00 UTC).

### Gap closing 1–4 and 7 (2026-10-02 ~13:30 UTC, Claude)
- **Tests:** `pytest -q` from `backend/` → **361 passed** (was 353). New: line A → B → A keeps three cohorts and three research
  member revisions with the current line latest; quotes retrieved after the information time are never used (trigger + winner
  quote table/id); a slow download ending after the start records nothing (primary, markets, cohorts, selections); definition
  changes reported as separate groups; old decision rows without a settlement version stay unknown; per-market slate entries
  (line, missing inputs, timing); NBA path through the same two-hour trigger (`tests/test_research_nba.py`); frontend type ↔ API
  field contract for every research screen (`tests/test_research_contract.py`). `npm run typecheck` and `npm run build` pass.
- **Supplementary study pass** (scratch copies; user-approved): every run-2 figure reproduced exactly; adds the earlier-seasons
  best-single comparison and reliability tables (docs/COMBINATION_RESULTS.md).
- **Browser:** demo Today winner view shows per-market research lines with timing and missing-input reasons; NHL puck line /
  total views checked against the NHL rehearsal scratch store served with its clock pinned to the rehearsal time (port 8013,
  scratch only) — exact bookmaker line, status, reason, minutes before start. Performance shows average and best-single columns,
  reliability tables (labelled "no calibration is fitted"), settlement-rules version per decision (unknown for older rows),
  retrospective best-single-by-earlier-season line. 390-px frames: page width ≤ 390 on Performance, Today (winner and total
  views) and Game. Tab closed; temporary servers (8011-8013, 5181-5183) stopped.

## Improvement pipeline (weekly shadows, fit register, studies) — Claude, 2026-10-02 ~14:10 UTC
- Live checkout fast-forwarded to 99e6c1c (branch `retrain-cadence`) after backups `data/backups/{nba-before-0017-0018,
  nhl-before-0017,nhl-before-0018}-20261002.sqlite`; migrations 0017 (NHL shots) and 0018 (fit register) applied to both
  stores. `scripts/check_project.py --ready` in the live checkout with the live venv: exit 0 (structure, typecheck, build,
  385 passed). The research worktree venv (same pinned numpy 2.5.3 / scikit-learn 1.9.1 / scipy 1.18.1, plus CatBoost) also passes.
- Real-data member check (a `.backup` copy of the NBA store, `research_live._line_state` at 2026-10-20T17:30Z before the first
  NBA game, 229 s cold, 2 s cached): `ridge_points@weekly` and `gbt_points@weekly` return distributions (margin 2.08 / 2.15,
  total 219.3 / 218.6) and fit identities (8,919 training results, fingerprint 04d04c8fadd461a5, params digests distinct from
  the season members because their residual SDs differ). Season and weekly members are identical in means on opening day, as
  expected (same training data).
- Browser (temporary servers 8021/5191 from the live checkout, real mode): Game day, Performance (Total market, research
  section empty state and weekly decisions table), History render; no console errors; 390-px frames: page width 386 = viewport
  on all three. New member labels render only once NBA cohorts exist (from 2026-10-20) — unverified in the browser until then.
  Servers stopped, tab closed.
- **NHL xG shadow (after merge of 7a674f1):** shot import complete in the live NHL store — 14,524 eligible games 2016–2027
  (1,247,967 unblocked attempts; 8 recent games `observed`, the rest `historical_backfill`; 0 errors, 0 incomplete, 0 pending).
  One run hit the 2-hour background limit and was resumed (imports are per game and idempotent). Dry run (write=False) of
  `agreement.predict` on a `.backup` copy at 2026-10-02T20:45Z for nhl:2026020017 (22:30Z): status success, no errors or skips,
  80 s; forecasts hockey_elo 0.532, goals_poisson 0.470, hockey_logistic 0.588, hockey_gbt 0.656, hockey_logistic_xg 0.620.
  First real cohorts with the shadow member: tonight's window (from 20:30 UTC) — unverified at the time of writing.

## expanded-v1 and the primary-read fix — Claude, 2026-10-02 ~16:30 UTC (live checkout 1d0709c)
- `check_project.py --ready` exit 0 (393 passed). Live checkout fast-forwarded; no migration needed.
- Rehearsal on a `.backup` copy of the live NHL store (a `refresh` job row inserted into the copy only), full code path at
  2026-10-02T20:45Z for nhl:2026020017: primary accepted; agreement success (no errors/skips); research success — 6 core
  cohorts + 1 `expanded-v1` winner cohort; 94 s. Core winner: 4 voters (xG listed, not voting), average 0.561, stacker 0.579.
  Expanded: 5 voters (4 of 5 home), average 0.573, stacker 0.612. `model_fits`: 5 learned members, 14,508 training results
  through 2026-06-15T04:00Z.
- Browser (rehearsal store, API clock pinned at 20:50Z, ports 8022/5192): Game page shows the core research card and a separate
  "Expanded experiment · Recording only" section with all five members, the all-model average and stacker; Performance (NHL,
  winner) shows "Core experiment" and "Expanded experiment (expanded-v1) · Recording only" groups, the latter with the eight
  registered subset averages as sources and no selector section. 390-px frames: 386 = viewport on both pages.
- Found during this check: the Game page logged duplicate React keys because `market_live.latest` returned research member
  rows next to the primary ones; the same query feeds primary market scoring and threshold tracking, so research rows would
  have counted as primary forecasts once they existed (none existed in the live stores yet). Fixed (1d0709c) with a regression
  test that fails without the fix; after the fix the Game page logs no console errors. Servers stopped, tab closed.

## selective-v1 and combination-rule tracking — Claude, 2026-10-02 ~17:50 UTC
- Studies: `app.cli --sport {nhl,nba} selective-study` (final run 17:32–17:33 UTC reused the saved tables; first build
  17:07–17:31 UTC). Registry: `app.cli rules-registry` → `backend/app/models/combination_rules.json`, sha256 4ea80e59…,
  5,365 rules / 11 markets / 19 selected (unqualified research).
- API/browser on the rehearsal copy (branch code, clock pinned at 20:50Z, ports 8023/5193): `/api/research/rules` NHL winner —
  1 canonical cohort, 895 rules, 30 pick on it, the selected rule abstains with "condition 'clear' not met" (that reason was
  missing in the first check: out-of-condition games were not explained — fixed with a test). Performance → "Combination
  rules": selected and all-registered views (900 table rows with all), historical frontier labelled "Backtest · reused
  seasons", prospective record labelled "Live"; 390-px frame 386 = viewport; no console errors. Servers stopped, tab closed.

## Candidate pool pool-v1 — Claude (-6b), 2026-10-02 ~17:00–19:20 UTC (worktree ../nba-predictor-candidates)
- Data: `.backup` copies of both live stores (16:56 UTC) in the session scratch directory via `NBA_DATA_DIR`; live stores untouched.
- Dry runs (warm-up seasons only): `app.cli --sport {nhl,nba} pool-screen --dry-run` exit 0.
- Stage 1: `pool-screen` NHL (started 17:21:32Z, 322 s; shortlist frozen 17:26:54Z) and NBA (17:32:21Z, 1,118 s; frozen
  17:51:00Z) exit 0. Stage 2 (once, access logged): `pool-assess` NHL (17:32:28Z, 699 s) and NBA (17:57:26Z, 952 s) exit 0. Compact evidence docs/evidence/candidate-pool/.
- Rehearsal of `candidates.live.record` on the store copies (fixed clock, 70 min before start):
  NHL nhl:2026020017/18 (2026-10-02 22:30/23:00Z): 26 rows, 0 errors, retry 0 new; goalie listings matched by name
  (expected/confirmed, retrieved 16:36Z); line markets skipped with "no exact line from the core cohort" (no cohorts in the copy).
  NBA espn:401909088 (2026-10-20 19:00Z): 10 winner rows; first run 129 s (trains and stores all season-2027 artifacts), retry 15 s.
- Tests: `pytest tests/test_candidate_pool.py` 12 passed; `backend/.venv/bin/python scripts/check_project.py --ready` exit 0 (414 passed; 2 migration-list expectations updated for 0019).
- Not done here: jobs wiring, roster UI and browser check (integration session); no real prospective candidate rows yet.

## Candidate-pool integration (pool-v1 from session -6b, 2aecf4a) — Claude, 2026-10-02 ~18:50 UTC
- Merged candidates-v3 (fast-forward, rebased on b0ac762); wired `jobs.candidate_pool` into `predict_now` after the research
  cohorts (never fails the run; test with a raising pool), `/api/research/candidates`, Performance → "Candidate pool".
- Rehearsal copy (migration 0019 applied to the copy): `candidate_pool` at 2026-10-02T20:46Z for nhl:2026020017 — success,
  21 candidate forecasts in 37 s (`research-cand:` prefix; puck line at the core cohort's exact −1.5 line). Roster: 9 NHL
  candidates trained; `poisson_draw:nhl_base` held-out gains in reg3 / puck line / away team total, "selectable after 100
  settled prospective picks". Browser (ports 8024/5194, clock pinned): roster card renders, 386 = viewport at 390 px, no console errors.

## Pool selection, integrity fixes (audit P1–P14) — Claude (-b6), 2026-10-02 ~20:15–21:30 UTC (worktree ../nba-predictor-poolsel, branch pool-selector)
All runs on scratch copies of the live stores (SQLite backup API; scratchpad `dA` new code, `dB` audited 9d3e50e code, `dC`
copy of the live store taken 20:53Z with its 9 legacy candidate artifacts). Network steps stubbed (refresh, odds, NHL pbp);
everything else the real `jobs.selector_decisions` + `jobs.predict_now` wiring. Clock fixed.
- **Core identities unchanged:** `research_live.definition` NBA `28b64a0bac80ca86`, NHL `5040a9e7ad2ab51c`, `selector-v1:e00a275e092a`
  before and after; every hashed file (research_live, combine, markets, market_live, selector, models/*, retrain_cadence,
  fit_register, agreement, backtest, availability*, candidates/{features,learners,engine,goalies}, combination_rules.json)
  byte-identical to 9d3e50e (`git diff --quiet`).
- **New vs audited code, same snapshot, now 2026-10-02T21:00Z (nhl:2026020017/18 in the window), two cycles:** identical row
  counts AND rows (`SELECT *` compared) in predictions 8680, near_tipoff_forecasts 2, market_forecasts 510, research_cohorts
  14, selector_decisions 30, research_selections 12, candidate_forecasts 42, model_fits 14. The second cycle wrote nothing
  new in either. New code only: 0020 applied, pool_decisions 3 (reg3, spread, team_total_away: insufficient evidence, 0
  settled cohorts, 210/198/198 rules), pool_selections 6 (no-pick "selector insufficient_evidence"; every registered source
  joined — 9/6/6 vectors — from candidate forecasts whose line_ref names the same cohort at the same line; 0 of 16
  line-market candidate rows lack a matching core-cohort reference).
- **Guards on the copy:** candidate inserts 3 h before, at the start and with a malformed time rejected; a pool selection at
  the start rejected; UPDATE pool_decisions / DELETE pool_selections rejected (append-only).
- **Upgrade of the current live store (copy `dC`, schema 19, 7 real cohorts, 21 real candidate forecasts, 9 artifacts
  without digests), now 22:10Z:** 0020 applied; the 9 legacy artifacts adopted once (digests written, listed in the
  cycle output); 63 candidate forecasts, 9 pool selections, 0 errors; retry 0 new, 0 adopted.
- **NBA copy:** 0020 applied; pool decision team_total_home insufficient evidence; v1 decisions still 30.
- **Browser (API :8031 + Vite :5181 on `dA`, real clock after 21:00Z), desktop:** Performance → NHL → Regulation 3-way:
  "Candidate-pool selector" card (registry 99c3f7cf1a080c6f, decision "insufficient evidence — no rule has 100+ settled
  picks…", 9 sources with route and held-out CI); roster rows show per-market pool sources and "Regulation 3-way pool
  decision: insufficient evidence"; 2 prospective games per candidate. NBA → Home team total: one source
  `avg:points_normal+cand:hetero_linear:nba_base`; roster (same API) "selectable only inside … never alone". Route label
  then changed from "in combination only" to "combination route" (typecheck passed; not re-screenshotted).
  **Phone width: unverified** in this run (the window resize did not change the captured viewport); the card uses the
  existing `table-wrap` pattern.
- **Tests:** `backend/.venv/bin/python scripts/check_project.py --ready` exit 0: structure guard, typecheck, build, 438 passed, 0 skipped (4 min 15 s).

## Review fixes, gap-fill study, selection status — Claude, 2026-10-02 ~21:40–22:30 UTC (worktree ../nba-predictor-poolsel)
- **Scratch rehearsal** (scratchpad `rehA`: SQLite-backup copies of both live stores taken 21:41:56Z + `data/research` copy;
  network steps stubbed; `jobs.selector_decisions` + `jobs.predict_now` with fixed clocks), code a3a597c, NHL:
  - 21:50Z: 0020 applied; core definition `7f9821facdf5b8f9` (was `5040a9e7ad2ab51c`: `markets.py` changed); 6 new core
    decisions + 3 pool decisions (all insufficient evidence; earlier 30 decisions kept); 12 cohorts, 12 selections,
    42 candidate forecasts, 6 pool selections; 9 legacy artifacts superseded with `registered_digest_match = true`.
  - Same clock again: 0 new rows anywhere (idempotent). 22:20Z: refused `stale_results` (refresh stubbed) — correct.
  - Read-only counts on the live stores: 0 of 510 NHL market forecasts and 0 NBA have a moved start, so the new void rule
    changes no recorded settlement.
- **Regression tests proven on old code:** 8 tests fail on e6b6abd and pass on a3a597c; 3 tests for the independent review
  of a3a597c (dry run, clock-independent idempotency, per-game isolation) fail on a3a597c and pass on e0961c7.
- **Browser (API :8041 on `rehA`, Vite :5191), Performance → NHL:** "Selection status by market" card lists all six markets
  (status, core and pool selector state with reasons, 9 / 6 / 6 eligible pool sources for reg3 / puck line / away total,
  decided / evidence-to / next-review dates, versions, last cohort time). Desktop 1538 px. Narrow width by **emulation only**
  (same-origin 390 px iframe; the window resize did not change the captured viewport): page width 388 = viewport (no
  horizontal page scroll), table scrolls inside `table-wrap` (442 vs 314 px); no console errors. **No real phone test.**

## tracking-v2, features-v1, training status, prop snapshots — Claude, 2026-10-03 ~00:00–01:30 UTC (live checkout + worktree ../nba-predictor-features)
- **Post-merge cycle (previous session's code):** NHL run 98 (2026-10-02 23:31Z): 9 legacy candidate artifacts adopted, agreement /
  research / pool / pool selection `success`, 0 errors.
- **Live tracking:** migration 0021 applied by the 00:16Z jobs (backups `data/backups/{nba,nhl}-before-0021-20261003.sqlite`,
  `…-before-0022-…`). Run 103 (00:16Z) tracking `failed`: it imported the new `agreement_tracking.py` (inputs digest) against a
  store migrated to 0021 only — non-fatal, reported; fixed by 0022 at the next start. Run 108 (00:59–01:12Z): 0022 applied,
  tracking `success`, **247 observations** for nhl:2026020021 (winner 83, reg3 48, each line market 29; all `pick`), 2 prop
  snapshots stored (`espn_props`, ~390 KB each). Run 108 status `failed` for a pre-existing reason: nhl:2026020020 started at
  01:00Z inside the 13-minute cycle, so its research steps finished after the start (skipped, correct) and `goals_draw` /
  `goals_glm` raised `AttributeError: 'NoneType' object has no attribute 'market'` for that game (hashed research code, not changed).
- **Scratch rehearsals** (`.backup` copies + `data/research` copies; network steps stubbed; fixed clocks): NHL 2026-10-03T22:00Z —
  research, pool, pool selection and tracking success; same clock again adds nothing. NBA 2026-10-20T22:00Z — 1 forecast, 5 cohorts,
  29 candidate forecasts, **583 observations** (winner 50 pick / 33 incomplete = every group containing `availability_two_hour`,
  which refused: "No injury report actually received in the last 60 minutes" — the injury fetch was stubbed; visible, not
  invented); run status `failed` from that refusal (expected in a rehearsal).
- **Studies:** features-v1 NBA and NHL on scratch copies (docs/CHALLENGER_RESULTS.md "Feature tranche"); tracking historical
  tables rebuilt per market (`data/research/tracking/historical-{sport}-{market}.json.gz`, summaries + chronological checks).
- **API** (uvicorn :8000 on the live stores, restarted on ba6ad92): `/api/research/tracking` NHL winner prospective `recording`
  (0.06 s), historical with a date filter 1.25 s, `/tracking/check` precomputed 0.17 s, `/tracking/game/nhl:2026020021` 6 markets,
  `/api/research/training` 14 fits, 12 skipped/failed update events; API RSS ≈ 475 MB after one historical market load.
- **Browser** (Vite :5173): Performance → NHL winner: Agreement tracking card (filters rule / model / confidence threshold and band /
  exact line / dates / version / sort; prospective vs historical; active recommendation strip "none … exploratory"; leaderboard;
  all groups with "insufficient evidence"); selecting majority + band ≥ 0.80 and ≥ 60% changed the table as expected; Training and
  updates card. Game page nhl:2026020021: tracking card per market with collapsed classifications. Desktop 1366 px by headless
  Chrome screenshots (the extension window could not be resized); 390 px by same-origin iframe emulation: page width 388 =
  viewport, tables scroll inside `table-wrap`. **No real phone test.**
- **Checks:** full suite 469 passed (before review fixes); targeted suites after fixes: tracking 21, features-v1 5, training 1;
  `check_project.py --ready` on ba6ad92: exit 0, 477 passed.

## Discovery tracking — Claude, 2026-10-03 ~02:00 UTC (worktree ../nba-predictor-poolsel)
> **Superseded at reconciliation (2026-10-03):** the Discovery card and `/api/research/discovery` were removed as a duplicate of
> tracking-v2 (`/api/research/tracking`, Performance → Agreement tracking), which stays canonical. The evidence below describes
> the removed card on branch `pool-selector` (backup `backup/pool-selector-20f27fe`); the discovery-v1 study results are kept.
- API :8041 on the scratch copy `rehA` (store copies from 2026-10-02 21:41Z + discovery-v1 outputs), Vite :5191.
- `/api/research/discovery?sport=nhl&market=reg3&method=avg&level=>=0.55`: 0.1 s; historical groups pooled over seasons
  with Wilson intervals, matched best earlier single, log loss/Brier for probability groups; prospective groups from 2
  stored cohorts, all "insufficient evidence". NHL winner registered-only: 1.3 s, 8 groups.
- Browser, desktop 1568 px: Performance → NHL → Regulation 3-way → "Combination discovery and tracking": selects
  (agreement, confidence threshold/band), model checkboxes, "Registered rules only" → 9 registered groups with badges and
  reasons. Fixed during the check: checkbox layout inside `.filters`; registered-only match count.
- 390 px **emulation** (same-origin iframe): page width 388 = viewport, model chips wrap (312 px), table scrolls inside its
  wrapper (460 vs 314 px). No real phone test.

## Joint agreement / confidence exploration — Codex, 2026-10-03

Scope: extended `agreement_tracking`, its API and the existing Performance component; integrated the previously prepared audit branch (merge `838f879`). The new joint view does not write forecasts or modify sealed selection rules.

- `python3 scripts/check_project.py`: passed.
- `backend/.venv/bin/python -m pytest backend/tests/test_joint_agreement.py -q`: 12 passed, including NBA/NHL API contract, 4/5 votes versus mean probability, half-open ranges, NHL three-way votes, missing/tied members, unchanged earlier decisions when later outcomes change, non-unanimous range selection, version separation/pushes, invalid historical goalie sources, exact cohort/current-time filtering and rescheduled voids. Two initial fixture mistakes were corrected (confidence accidentally distinguished vote groups; an invalid winner line was already rejected by the database).
- Scratch read-only matrix: all 5 NBA and 6 NHL markets evaluated successfully from SQLite `mode=ro` + `query_only`. 6,605 historical NBA and 11,870 historical NHL games per market; 3 and 7 next-season folds respectively. One available core-model subset per market, supporters-mean definition; this verifies all market paths, not an exhaustive subset search. Saved `data/reports/joint-1/verification-20261003.json`. Scratch live state had 0 NBA / 5 NHL two-hour cohorts per market, with NHL definitions kept separate.
- Preview API: `NBA_DATA_DIR=/private/tmp/codex-joint-preview .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8032`; frontend: `API_PROXY_TARGET=http://127.0.0.1:8032 npm run dev -- --host 127.0.0.1 --port 5182 --strictPort`. Scratch stores were SQLite backup snapshots; no forecast/retraining job was run for this feature.
- User app API restarted on `127.0.0.1:8000` in real mode; UI verified at `http://127.0.0.1:5173/performance?sport=nba`. Earlier scratch browser flow: NHL pair selection, historical rule evaluation and prospective version separation; actual viewport 390×844 showed document scrollWidth 390. Desktop check at 1280×900. Remaining final browser/readiness results below.
- Historical tracking artifacts preserved at `/private/tmp/codex-joint-tracking-before-calc2` before rebuilding the audit's `calc-2` tables; NBA rebuild completed. This rebuild is classification of stored OOF probabilities, not a rerun of the reserved final test.

### Completion verification — Devin, 2026-10-03

- Preserved Codex's uncommitted implementation. Fixed the joint search's last confidence band: its internal `1 + 1e-9` upper sentinel canceled the boundary tolerance and excluded exactly 100%. The new regression failed before the fix and passes afterward; open-ended search bands now return `confidence_max: null`.
- Browser regression reproduced on the original UI: typing `unknown` into Research definition yielded only `u` and lost focus. Moved that control outside the loading body; the same slow-typing probe now returns `unknown`, focused. Added Reset research filters so a failed query does not strand hidden model/line controls.
- Codex's `/private/tmp/codex-joint-readiness.log` ended with **527 passed, 2 failed**, both `ModuleNotFoundError: No module named 'optuna'`; it was not a passing readiness run. Installed the existing pinned dependency with `uv pip install --python backend/.venv/bin/python 'optuna==5.0.0' --exclude-newer 2026-09-26` (PyPI release 2026-09-07). No dependency declarations, model definitions, selectors or forecast rows changed. README now documents this test prerequisite.
- `python3 scripts/check_project.py`: passed. `backend/.venv/bin/python -m pytest backend/tests/test_joint_agreement.py -q`: **13 passed**. Final `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 backend/.venv/bin/python scripts/check_project.py --ready`: **exit 0**, structure/typecheck/build passed, **530 tests passed**, no skips; one existing Starlette/httpx deprecation warning. The earlier readiness run also passed after Optuna was installed (529 tests, before the new regression was collected).
- Restarted only the app API from `backend/`: `NBA_API_MODE=real .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`; health returns 200, real mode. Existing Vite frontend remains at `http://127.0.0.1:5173`. No ingestion, training, migration, historical rebuild, forecast-generation or selection job was invoked.
- Running API GET matrix: `/api/meta`, `/api/research/tracking/joint?market=...&members=...&evaluate=true`, its `evidence=prospective` variant, and `/api/research/tracking?evidence=historical` for both sports and every available market: **11/11 passed**. Historical games/next-season folds: NBA **6,605 / 3**, NHL **11,870 / 7** per market. Live rows: NBA **0**, NHL **5**, with **2** distinct method/member-version groups per NHL market. Historical tracking reports for both sports return `available`, not stale `calc-2` artifacts. Tested pairs: NBA winner `elo,logistic`; NBA lines `gbt_points,gbt_points@weekly`; NHL winner `hockey_elo,hockey_logistic`; NHL reg3/lines `goals_poisson,goals_glm`. This is market-path coverage, not exhaustive subset/performance validation.
- `node /private/tmp/devin-joint-smoke.cjs`: **exit 0**, existing cached Playwright with installed Chrome, headless. Actual URLs `http://127.0.0.1:5173/performance?sport=nba` and `?sport=nhl`; desktop **1280×900**, phone emulation **390×844**. Both sports/widths passed: research badge and anchor navigation, model selection, next-season folds and matched comparator, switching to recorded live removes historical evaluation, NBA empty state, NHL version separation, definition typing, invalid-range 422/reset, intentionally aborted joint request/error/retry (no stale results), and delayed-response loading. Document widths were exactly **1280 / 390**, no page-level horizontal overflow; **0 JavaScript page errors**. Initial harness-only issues (uppercase badge text and missing request base URL) were corrected without changing app assertions.
- Additional `node /private/tmp/devin-joint-navigation.cjs`: desktop real-mode checks passed for NHL regulation three-way subset selection, switching to total resets the subset, an exact-line query returns only that line, and switching sport resets the market/subset (no overflow or JS page errors). Desktop navigation with the real **demo API** (`NBA_API_MODE=demo .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8034`, browser routing only `/api/` requests to it) reached Game day → Game detail → History → Performance → Game day, showed Demo labels, and the joint card offered no synthetic forecasts.
- **Additional navigation harness did not pass end-to-end:** its first teardown hit `route.fetch: Request context disposed` for the existing demo research slate. Waiting for in-flight routes then exposed `route.fetch: Timeout 30000ms exceeded` on `/api/research/slate?sport=nba&date=2026-10-30`. Independent `curl --max-time 15 --silent --show-error --output /dev/null -w 'HTTP %{http_code} in %{time_total}s\\n' 'http://127.0.0.1:8034/api/research/slate?sport=nba&date=2026-10-30'` returned **exit 28, HTTP 000, 15.005058 s**, with no bytes. That route uses the pre-existing `research_demo.store()` initialization; root cause remains unconfirmed. No app tests were skipped or weakened. The extra harness never reached its phone navigation/market-reset checks; those are **unverified**, separate from the successful four-case joint-feature browser matrix above. Temporary demo API stopped after testing; real API/frontend left running.
- Remaining limitations: phone width is browser emulation, not a physical device; historical seasons are reused exploratory evidence at model-defined lines; no fresh prospective advantage or profitable betting claim. Original ignored verification JSON remained blocked by the file tool despite granted scope; the independent running-API checks above do not rely on reading it. Next: independent Codex review of the final feature diff, then a separate investigation of the demo research initialization timeout and completion of that navigation smoke run.


## Record counts and pre-game archive labels — 2026-10-03 16:20–16:50 UTC (Claude, sole writer)
- **Isolation:** tests ran with `NBA_DATA_DIR` set to an empty scratch dir (it stayed empty); API/browser used a consistent
  snapshot made with SQLite's backup API from `mode=ro` + `query_only` connections at 16:28:50Z (WAL content included;
  `integrity_check` ok), plus copies of `reports/`, `research/`, `eval/`. Temporary API `127.0.0.1:8091`
  (`NBA_DATA_DIR=<snapshot> NBA_API_MODE=real .venv/bin/uvicorn app.main:app --port 8091`) and Vite `127.0.0.1:5191`
  (`API_PROXY_TARGET=http://127.0.0.1:8091`); both stopped afterwards. Live stores and running services untouched.
- **Tests:** `tests/test_record_counts.py` (4) failed on the old code (missing fields; reg3 final without a decision counted as
  pending), pass now; 63 targeted tests pass. `NBA_DATA_DIR=<scratch> OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
  backend/.venv/bin/python scripts/check_project.py --ready`: **exit 0**, typecheck + build passed, **534 passed** (1 existing
  Starlette warning). Existing exact-dict record assertions in test_live.py/test_api.py were extended with the new fields.
- **API identities on the snapshot:** every winner record (2 sports × live pregame/near-tipoff/backtest × All/60/70%) and every
  market history + performance record (both sports, both policies) satisfies its identity: **0 failures**; market history and
  performance records are identical. NHL pre-game archive winner: 57 = 13 settled (4–9) + 44 pending + 0 void + 0 unscorable;
  two-hour: 5 = 5 settled (0–5). NBA live: 0. Populations equal those before the change.
- **Browser:** desktop viewport measured 952 CSS px (window 1280×900; resizing the window did not change it). Phone checked by
  390 px iframe emulation (386 px inner width), not a real device: History winner, History reg3, Today spread, Today winner,
  Performance total (archive), Game page, NBA History (empty state "No pre-game archive forecasts in this view…") and an invalid
  date range (error state "422: date_from must be on or before date_to" with Try again): **0 px horizontal overflow** on all,
  no "night-before" text. Record lines read e.g. "All dates · 57 picks … Settled record 4–9 (13 settled) … 44 pending · 0 void ·
  0 unscorable"; History with filters reads "Oct 2, 2026 – Oct 3, 2026 · VGK · 1 pick … 0 pending". Today shows the all-dates
  record separately from "13 games, 13 with a pre-game archive forecast". Loading state observed. Console errors not captured
  (tracking started after loads): unverified.
- **Limitations:** no void or unscorable case exists in real data (covered by synthetic tests only); the frontend has no test
  runner, so UI behaviour is browser-checked, not unit-tested; the running :8000 app needs a restart to show the change.
- **Self-review fix (16:55–17:10 UTC):** two wording bugs from this batch appeared in demo mode only: the Game page told demo
  users their forecast came from "the discontinued daily job", and the demo Today slate said "with a pre-game archive forecast".
  Fixed in GamePage.tsx (sentence only for `run_type === 'live'`) and TodayPage.tsx (`policyName`: demo slates say "demo").
  Verified on a temporary demo API (`NBA_API_MODE=demo`, scratch `NBA_DATA_DIR`, port 8092, Vite 5192) at 1280 and 390 px iframe
  widths: Today/Game/History contain no "archive"/"discontinued daily job" text; "4 games, 4 with a demo forecast"; empty state
  "No demo picks at 80%+ model confidence on this date"; demo record 36 = 32 settled + 4 pending + 0 + 0; 0 px overflow. Real
  snapshot API re-checked: slate "13 games, 13 with a pre-game archive forecast", game page keeps the archive sentence, no
  "night-before". Readiness rerun (isolated): **exit 0, 534 passed**. Temporary servers stopped.
- **Reviewer checks (Claude Fable 5.1, 16:35–18:10 UTC):** `PYTHONDONTWRITEBYTECODE=1 NBA_DATA_DIR=<scratch> backend/.venv/bin/python
  -m pytest -p no:cacheprovider --basetemp=<scratch> tests/test_record_counts.py tests/test_live.py tests/test_api.py
  tests/test_api_markets.py tests/test_live_markets.py tests/test_near_tipoff.py`: **72 passed**. The same new test file against a
  scratch copy with HEAD `real.py` + `schemas.py`: **4 failed**. Before the restart the live page showed blank counts
  ("· pending · void · unscorable") because :8000 still ran the old code; after the user-requested restart (16:49 UTC) the live
  NHL History and Today record lines show "44 pending · 0 void · 0 unscorable" and match the API. Demo-mode rendering was not
  rechecked by the reviewer (no demo service running).


## API deadlock fix — 2026-10-03 19:25–20:05 UTC (Claude Opus 5.5)
- Reproduced on `6e70a4e` with a temporary API on a store backup copy (`NBA_DATA_DIR=<scratch snapshot> uvicorn ... --port 8093`):
  60 concurrent `/api/meta` -> 60 × timeout (curl exit 28, 40 s), then `/api/health` no answer.
- After the fix, same server setup: 60 concurrent -> 60 × 200 in 1.31 s; 120 -> 120 × 200 in 2.3 s; health 200; 14-request
  Performance burst 0.91–0.99 s; 20 clients hanging up mid-request on joint evaluate, then `/api/meta` 200 in 0.83 s.
- `tests/test_api_concurrency.py`: the 8-request/3-thread case hangs on `6e70a4e` (killed at 90 s, exit 142) and passes now.
  Limitation: on a regression it hangs rather than failing fast (the test client waits for stuck requests on exit).
- Full suite with `NBA_DATA_DIR=<empty scratch>`: 559 passed; `tsc --noEmit` ok. Temporary server stopped.


## Pinned release switch — 2026-10-03 19:45–20:00 UTC (Claude Opus 5.5)
- `release-check` from `../nba-predictor-live/backend` under `env -i` (launchd-like) for nba and nhl: exit 0, problems [],
  app = release checkout, schema 23/23, definitions NBA `1215f3641ad63e4f`, NHL `3e2504c4d655c41b`.
- Locks free and no CLI process before `launchctl bootout`/`bootstrap` of the five jobs (19:51:25 UTC). All five plists'
  WorkingDirectory = release. First runs: NHL run 152 success with release `9fc202c…`; NBA run 417 skipped (quota) with release.
  No guard refusals in either log; no new selector definition rows; release checkout `git status` empty afterwards.


## Release venv, always-on API/app, worktree moved — 2026-10-03 20:20–20:35 UTC (Claude Opus 5.5)
- Release venv vs dev venv: `uv pip freeze` diff empty (43 packages). `release-check` (env -i) nba/nhl: problems [], definitions
  NBA 1215f3641ad63e4f / NHL 3e2504c4d655c41b; release suite with the release venv: 565 passed; checkout clean.
- `web` (vite preview from ~/Desktop) hung in state U with an empty log; after `git worktree move` to ~/nba-predictor-live:
  :4173 200, /history 200, /api via proxy 200 (57/13/44); :8100 health 200. kill -9 of api and web: restarted in ~2 s.
- Jobs after the switch: NBA 422 and NHL 155 skipped by quota with release 948f1bec; no guard refusals in either log.

## Review fixes (dev checkout, uncommitted) — 2026-10-03 22:40 UTC, Claude (Opus 5.5)
- Preview: `uvicorn app.main:app --port 8010` (real data) + `API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev -- --port 5190`.
- `/?sport=nhl&date=2026-10-03`: 13 cards; picks with one-decimal bars (BUF 70.3%); bookmaker line with American odds
  (CHI +190 / BUF -230); no agreement/research lines until "Show research details" (`details=1`), which shows them.
- `market=spread&details=1`: quotes as prices (BUF -1.5 +110 · CHI +1.5 -130) plus de-vigged %; tonight's release forecasts
  shown as "earlier model version" (preview runs the new NHL market version).
- `/history?sport=nhl&run=backtest`: "Show 500 more (500 of 2792 shown)" -> request `limit=1000`.
- `/performance?run=backtest`: 10 calibration bins (0-10% … 90-100%); research cards collapsed behind the toggle.
- Game page at 390 px wide: near-tipoff card, "Show research details" checkbox; layout fits. `/api/games?date=bad` -> 422 list detail.


## Independent historical-selection review — Codex, 2026-10-04
- Browser (Codex in-app) opened http://localhost:5190/performance?sport=nhl: real API-backed winner record, agreement visible with details off, frozen historical policy "Learned stacker at 65%+", frozen Oct 4 01:57 EDT; 0 post-freeze cohorts. Policy statistics observed only, not independently regenerated.
- Viewport checks: 1280x900 -> document width 1280; 390x844 with details off -> document width 390. Enabling Show research details updated URL to &details=1 and loaded Models in use. At 390px with details on, document width became 428 (horizontal overflow); agreement-vs-confidence tables extend beyond viewport. PyTorch/Keras/TensorFlow entries absent in this preview roster. Viewport override reset.
- CLI curl --max-time 5 to http://localhost:8100/api/research/historical-start?sport=nhl&market=winner returned exit 7 in the shell environment; this does not establish the host service is down (browser preview worked). Current release deployment/deep fits not independently verified.
- python3 scripts/check_project.py: structure passed. npm --prefix frontend run typecheck: passed. Focused pytest result recorded in HANDOFF.md. Full --ready not run in this review; known semantic findings prevent acceptance. No new full navigation/loading/error/demo smoke pass claimed.

## Historical selection, starting policy, agreement visibility — Claude (Opus 5.5), 2026-10-04 ~06:00–07:00 UTC
Preview only, on SQLite backup copies of both live stores taken ~05:45 UTC (scratch `NBA_DATA_DIR`); live :8000/:8100/:4173
untouched. API: `NBA_DATA_DIR=<scratch> NBA_API_MODE=real backend/.venv/bin/uvicorn app.main:app --port 8010`; frontend:
`API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev -- --host 127.0.0.1 --port 5190 --strictPort`.
- Policies frozen on the copies: `python -m app.cli --sport nhl historical-start` (84 s, 4,877 games 2018–21) and `--sport nba`
  (2 min 18 s, 3,962 games 2022–24). `/api/research/historical-start` 200 for both; before building it returned `not_built`.
- Desktop 1280 px: Game day (NHL, Oct 3) shows "Winner agreement … 4 of 4 pick BUF" on every card with research details
  off; Oct 4 cards say "Not recorded for this game". Performance shows the Historical starting policy card by default
  (rule, reason, excluded sources, chronological check with the picks' mean probability vs base rate, "since frozen":
  0 cohorts — no cohort exists under the new definition yet). Details on: "Models in use" table (fit, latest forecast,
  votes/never votes, selectable). NHL puck line: historical test shows "Earlier model version …" note (API
  `matches_live_model: false`). No console errors.
- Phone 390 px (iframe; Chrome window minimum is 500 px): Game day, NBA and NHL Performance: no horizontal overflow after
  one fix (the model-version `<select>` widened the NHL line page to 859 px; now `.version-select { max-width: 100% }`).
- Not verified in a browser: the policy's live record with real picks (needs cohorts under the new definition; covered by
  `test_policy_applies_only_to_its_definition_after_freezing_and_keeps_the_bookmaker_apart`); demo mode in a browser (API in demo mode returns status `demo` with a note, checked with TestClient).
- **After Codex's review (06:30–07:00 UTC):** policies rebuilt as `historical-start-v2` (v1 files kept); API restarted on
  the final code: both sports `matches_current_definition` and `matches_current_policy` true. 390 px iframes with research
  details ON: NHL winner (was 432 px: "Research version" select), NHL puck line (was 449 px: an unbreakable suspended-source
  list in the Candidate-pool selector card), NHL 3-way, NBA, Game page, Game day: all 386 px after `select { max-width:
  100% }` and `.card { overflow-wrap: break-word }`. Desktop: NBA total shows "No rule qualified"; "Models in use" shows
  current-version fits/forecasts with earlier versions listed apart. Preview servers stopped.

## Re-review evidence limits — Codex, 2026-10-04 (262d8fb)
- No new browser pass this turn: Claude records preview servers stopped after final 390px checks; those final layout results remain author-reported here.
- Independent: python3 scripts/check_project.py passed; backend/.venv/bin/python -m pytest backend/tests/test_historical_selection.py -q -> 13 passed in 3.71s. Full --ready not rerun; 594-test result remains author-reported.
- Read-only SQLite + release registry verification confirms both NHL deep fits/forecasts; exact versions/times in HANDOFF.md. No deployment performed. New synthetic historical/live record parity finding is also recorded there.

## Release candidate rc-20261004 — Claude (Opus 5.5), 2026-10-04 ~18:30 UTC
Store copies (snapshots 17:45 UTC + deep artifacts), release venv API (`~/nba-predictor-live/backend/.venv/bin/python -m
uvicorn app.main:app --port 8010`, `NBA_DATA_DIR=<copy>`), Vite :5190. 390 px and 1280 px iframes: Game day NHL (5 agreement
lines), Game page nhl:2026020035, Performance NHL and NBA spread with details: scroll width = viewport, no error states;
historical starting policy card and `torch_mlp:nhl_base` roster row present. API: historical-start NHL frozen, matches the
current definition; NHL torch/keras/sklearn MLP "trained" and forecasting (current version). Servers stopped.

## Calibrated shadow members — Codex, 2026-10-04
- Local implementation on `model-audit`; nine registered pairs, never voters or selector sources. No release or live store changes.
- Preview data: static RC store copies in `/private/tmp/codex-calibrated-shadow-preview` (both `PRAGMA quick_check`: `ok`).
  Source: Claude's stopped rehearsal `scratchpad/rcdata`; original files preserved. The initial SQLite read-only backup attempt
  could not open the source; copying the static files to the writable scratch directory resolved it.
- API: from repo root, `NBA_DATA_DIR=/private/tmp/codex-calibrated-shadow-preview PYTHONPATH=backend /Users/andry/nba-predictor-live/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8016`.
  Frontend: from `frontend/`, `API_PROXY_TARGET=http://127.0.0.1:8016 npm run dev -- --host 127.0.0.1 --port 5196`.
  Sandbox initially refused both localhost binds; approved local-preview execution succeeded.
- Browser: `http://127.0.0.1:5196/performance?details=1` (NBA) and
  `http://127.0.0.1:5196/performance?details=1&sport=nhl`. Six NBA / three NHL shadow rows visible,
  with no selection eligibility. NHL prepared-artifact state reads “frozen fit ready; awaiting first forecast”.
  At 1280×900 and 390×844, document scroll width equals viewport width for both sports with details open;
  NHL details closed also fits at 390. Browser console error list empty. Desktop roster visually inspected.
- First readiness run: typecheck/build passed; 613 tests passed, one existing roster exact-set assertion failed because
  it omitted the three new NHL shadows. Updated expected set explicitly and asserted all three are nonvoting shadows.
- Focused regression checks cover unavailable/current-season results, immutable fits, artifact completion timestamp,
  preserved push mass, append-only/idempotent recording, exact raw-cohort linkage, corruption/future-fit refusal,
  unchanged primary/cohort rows, roster ineligibility, and reporting calibration even if other pool candidates fail.
- Calibration history is exploratory and at model-generated lines. Bookmaker-line transfer remains unverified and is
  recorded in forecast provenance. No real prospective calibration accuracy is claimed; runtime recording verified
  with synthetic fixtures, not a backdated live forecast.

## Calibrated shadows — Devin fixes resumed by Claude (Opus 5.5), 2026-10-04 ~21:15 EDT
- Devin (19:39–19:44 EDT) fixed: no-line markets (NBA winner, NHL reg3) failed ledger insert (`line_source` NULL → stored
  `"none"`, raw value kept in inputs); stricter artifact structure checks; per-member `fit_unavailable` shared by
  record and roster, so incomplete/mismatched artifacts no longer show "fit ready". Its turn was cancelled before readiness.
- Calibration identity hashes `calibrated.py`, so these edits made the preview fits stale (roster showed all 9 "awaiting
  calibration-shadow-fit"). Refit on the preview copy only, cutoff `2026-10-05T01:13:44Z`:
  `NBA_DATA_DIR=/private/tmp/codex-calibrated-shadow-preview PYTHONPATH=backend <live venv python> -m app.cli --sport {nhl,nba} calibration-shadow-fit --season 2027 --cutoff <ts>`
  → new `nhl-69b2ba7c05e8cd4c-2027.json` (7,515/6,194/6,194 games), `nba-896aa9930176f03e-2027.json` (3,962×5, 5,133);
  same counts as Codex's fits. Older artifacts left in place. Core identities unchanged (NBA `ff7e0e91…`, NHL `cb194620…`).
- API :8017 / Vite :5197 (same commands as above, ports changed). `/api/research/candidates`: NBA 6 / NHL 3 calibration
  rows, all "frozen fit ready; awaiting first forecast", none voting.
- Browser (Playwright headless) `/performance?details=1` NBA and NHL at 1280×900 and 390×844: 6 / 3 ready rows,
  scroll width = viewport, no `role=alert`, no console errors.
- Readiness: `NBA_DATA_DIR=<empty scratch> /Users/andry/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready`
  → exit 0, **624 passed, 1 warning (12 m 42 s)**. Focused `test_calibrated_shadows.py` + `test_training_monitor.py`: 15 passed.
  Servers :8017/:5197 stopped afterwards; Codex's :8016/:5196 left running.

## Claims-focused calibrated-shadow review — Devin, after `9d88432`
- Changes: tests and training-history wording only; no application code, model identity, live data or release change.
- Baseline command from the repository root:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend NBA_DATA_DIR="$(mktemp -d /private/tmp/devin-calibration-review.XXXXXX)" /Users/andry/nba-predictor-live/backend/.venv/bin/python -m pytest backend/tests/test_calibrated_shadows.py backend/tests/test_candidate_pool.py backend/tests/test_training_monitor.py -q`
  → **34 passed (21.46 s)** before test strengthening.
- Mutation reproduction (memory only, no source edits): `unittest.mock.patch.object(cal.study, '_apply', ...)` delegates to the original
  function with `1 / params` for three-way markets, reversing temperature direction to `p**T`. In-process
  `pytest.main(['backend/tests/test_calibrated_shadows.py', '-q'])` still returned 0: **all 14 original tests passed**.
  After adding independent expected values, the same mutation with `-k 'apply_preserves or every_supported_pair' --tb=short`
  returned 1: **2 intentionally failing tests, 1 passed**. For input `(4/9,4/9,1/9)`, T=2, the wrong result was
  `(0.484848,0.484848,0.030303)` instead of `(0.4,0.4,0.2)`. The mutation was removed by exiting its patch context/process.
- Expanded file, normal unmodified application: **29 passed (3.39 s)**. New tests cover actual sealed-registry tracking before/after
  recording all nine synthetic shadows, pool-vector isolation, the `now=None` clock branch and tipoff refusal, unavailable training,
  and preservation of existing pool results when a shadow skips, errors or raises. These are not real-game or network-cycle tests.
- Final command:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend NBA_DATA_DIR="$(mktemp -d /private/tmp/devin-calibration-review.XXXXXX)" /Users/andry/nba-predictor-live/backend/.venv/bin/python -m pytest backend/tests/test_calibrated_shadows.py backend/tests/test_candidate_pool.py backend/tests/test_training_monitor.py backend/tests/test_calibration_study.py backend/tests/test_agreement_tracking.py -q`
  → **73 passed, 1 warning (61.07 s)**; warning is the existing Starlette/httpx TestClient deprecation.
- `python3 scripts/check_project.py` and `git diff --check` passed. No full readiness/build/typecheck/browser rerun: application unchanged.
- Used `db.connect_readonly` and `calibrated.load_artifact` with `NBA_DATA_DIR=/private/tmp/codex-calibrated-shadow-preview` to verify
  all nine artifact digests, identities, ≥300 games, season restrictions and result ≤ training-cutoff ≤ creation timestamps.
  Core identities still NBA `ff7e0e9199ca0cf7`, NHL `cb194620c0e6a3c6`; calibration identities still NBA `896aa9930176f03e`, NHL
  `69b2ba7c05e8cd4c`. Fits include eligible warm-up seasons (NBA efficiency 2021; NHL Poisson 2016–17, GLM 2017), now explicit in
  MODEL_SELECTION. No artifacts rebuilt. No preview servers started, no release interpreter/environment files modified.


## 2026-10-05 Codex learning-status correction
- `python3 scripts/check_project.py`: passed.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_training_monitor.py -q`: 2 passed, existing Starlette/joblib warnings.
- `cd frontend` then `npm run build`: TypeScript and Vite passed. `git diff --check`: passed.
- Read-only `_online` against local stores: NBA latest eligible result June 14, no verified consumption; NHL primary Elo reports saved forecast provenance with 14,547 results. Non-primary consumption explicitly unverified. No data writes.
- Browser URL `http://127.0.0.1:5196/performance?details=1`: CUA tab selection timed out in `Emulation.setFocusEmulationEnabled`. Desktop and phone flows/rendering unverified; no fresh full readiness run or deployment.


## 2026-10-06 adaptive-v1 (NBA + NHL) — Claude (Fable 5.1)
All runs on store copies (`NBA_DATA_DIR=<scratchpad>/data`, snapshots of the live stores taken 2026-10-05 23:10 UTC via an
immutable-mode backup); the live `data/` folder was never opened for writing and is not migrated to 0024.
- `python3 scripts/check_project.py`: passed. `git diff --check`: passed. `cd frontend && npm run typecheck && npm run build`: passed.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_adaptive.py backend/tests/test_storage.py -q`: **36 passed**
  (12 adaptive: end-to-end train→record on the synthetic NHL store, prospective rows entering the next fit and changing weights,
  future/own outcomes never entering a fit, scheduled-walk resume equality, leave-one-out/abstain, failed refit keeps the previous
  artifact, corrupt artifact → missing member, no-combiner → unavailable row, jobs hooks and off switch, two-hour DB guards,
  freeze/thaw of function-local member classes, walk-forward history blind to later and own outcomes).
- Regression: `pytest backend/tests/test_api.py backend/tests/test_training_monitor.py backend/tests/test_research_live.py
  backend/tests/test_candidate_pool.py -q`: **63 passed**.
- Full readiness `NBA_DATA_DIR=<empty> OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready`: **exit 0, 652 passed** (6 min 31 s; typecheck + build ok). With the release venv (`backend/.venv`, no torch/tensorflow) the same command fails only the 8 `test_deep_study.py` tests that need those libraries, as in earlier handoffs; everything else passed (642). Two existing expectations were extended for the new migration and the new `daily` step (`test_live.py`, `test_shadow.py`).
- Replays (research env `~/nba-predictor-deep/backend/.venv/bin/python`, `OMP_NUM_THREADS=4`): `--sport nhl adaptive-replay`
  34 min, `--sport nba adaptive-replay` 63 min; reports in the scratchpad, summaries committed to `docs/evidence/adaptive/`.
  Numbers in docs/ADAPTIVE_SYSTEM.md §8.
- Real-copy training: `--sport nhl adaptive-train` (research env): success, 15 member fits + combiner for `2027:2026-W41`,
  2,831 training rows per market, deep members kept their season-start fit (39 current-season results < 100), ~1 h first run.
  `--sport nba adaptive-train`: first run **partial** — every member fit succeeded except `points_normal` (`KeyError: 'winner'`: the NBA line primary asked for a model line on the winner market); the failure was logged in `adaptive_fits`, the winner combiner still trained (2,643 rows, 17 members) and every line market reported "no combination" with its reason — the designed failure path. Fixed (`walk_member` sets lines for line markets only; regression test `test_line_primary_walk_sets_model_lines_for_line_markets_only`) and rerun: **success**, 20 member fits (deep networks on 8,919 games) + combiner for `2027:2026-W43`, 2,643 rows per market, 18 winner / 9 line members, no exclusions, ~75 min. The NHL real-copy artifacts were made before that one-line fix, so their definition (`9042dd64a7c2e1f9`) differs from the final code's; the NHL logic is unchanged by the fix.
- Real-copy forecast path (release env, no torch): `adaptive.record(now="2026-10-06T22:00:00Z", write=False)` on the NHL copy:
  7 eligible games, winner + reg3 forecasts for every game (12 and 5 contributing members, deep artifacts applied from NumPy
  weights), line markets skipped "no primary two-hour market forecast" (the snapshot predates those cycles), odds available for
  winner, selector "insufficient evidence" everywhere; 80 s under CPU contention. No row written (dry run; the real clock had no
  game within two hours and timestamps are never backdated).
- API (dev server :8018 on the copies): `/api/health` ok; `/api/research/adaptive?sport=nhl` → combiner fit, 15 members, due=false
  ("fit in force already prepared for 2027:2026-W41"); `?sport=nba` before training → due=true "no combiner fit";
  `/api/research/adaptive/game/nhl:2026020043` → `no_adaptive_forecast`.
- Browser (Claude-in-Chrome, desktop 1280-px viewport; the extension pins the viewport so `resize_window` has no effect — phone
  width checked by rendering the same pages in a 390-px iframe and reading `scrollWidth`): Performance → details → "Adaptive system"
  card (NHL) shows definition, schedule, weights per market, training rows (0 prospective / 2,831 walk-forward), selector state,
  member fits in force, empty prospective record, replay tables with green/grey paired intervals; NBA shows the "no combiner fit
  yet" state. Game page `nhl:2026020043` shows the explicit empty state. Populated Game card verified on a synthetic NHL store served
  with a fixed clock (`:8019`/`:5199`, scratch-only harness): reg3/spread/totals probabilities, pick/no-pick reason, bookmaker
  comparison at the same line or "unavailable", training cutoff, weight-update time, per-member probabilities/weights/roles,
  missing members. 390-px iframe: no page overflow; wide tables scroll inside `.table-wrap` as the other research tables. No console
  errors. Servers :8018/:5198/:8019/:5199 stopped afterwards.
- Not verified: a real two-hour cycle writing `adaptive_forecasts` on a live game (needs a release move + the next game day);
  prospective accuracy; the first real nightly `adaptive-train` duration in the release environment.

## 2026-10-06 adaptive-v1 review fixes — Claude (Fable 5.1)
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_adaptive.py backend/tests/test_storage.py -q`: **41 passed**
  (17 adaptive, 4 new for the review findings; the failed-refit test extended with the partial → retry → success cycle).
- `NBA_DATA_DIR=<empty> OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready`: **exit 0, 657 passed**
  (7 min 34 s). `python3 scripts/check_project.py`, `git diff --check`, `npm run typecheck`: passed.
- Real-copy cycles (NHL, research venv): `adaptive-train` success (definition `903207331c701548`, 15 member fits + combiner, content-addressed
  artifact names); forced repeat success with the candidate history reused (same out-of-sample counts, no new periods).
- Browser not re-run after the fixes: the only UI change is the partial-run note and "in force: earlier fit #n" on the Performance
  card (typechecked); desktop/phone rendering of the card itself was verified earlier today.

## 2026-10-06 adaptive-v1 training-lock fix — Claude (Fable 5.1)
- `pytest backend/tests/test_adaptive.py backend/tests/test_shadow.py backend/tests/test_storage.py -q`: **72 passed** (19 adaptive; new:
  store lock free + forecast cycle during a blocked training run, tampered record published as failed, NBA new-week run through
  the scheduled command). Full readiness (research venv): **exit 0, 659 passed** (6 min 39 s; a previous run of the same suite had one failure in `test_train_then_record_end_to_end…` — the winner row came back `unavailable` — that did not reproduce in three reruns of the test, the file or the suite; cause not established, noted as a flake to watch).
- Real NHL copy, release venv (`NBA_DATA_DIR=<copy> backend/.venv/bin/python -m app.cli --sport nhl adaptive-train`): **success**, 16 fit records published (15 members + combiner; the torch/keras members `skipped` — no fit exists for this definition and no library here, they are fitted by the research-venv run), snapshot removed afterwards; `db.store_lock('nhl', wait_seconds=0)` acquired instantly while the training was mid-walk (held by `train.lock` instead). The run before it was refused at publication with "definition changed during training" because the module was edited while it ran — the guard working as designed.

## 2026-10-07 adaptive-v1 final-code real-copy runs — Claude (Fable 5.1)
- Code frozen after the snapshot-ownership / retryable-publication fixes. `pytest backend/tests/test_adaptive.py -q`: **21 passed**.
  Full readiness `NBA_DATA_DIR=<empty> OMP_NUM_THREADS=3 ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready`:
  **exit 0, 661 passed** (8 min 13 s). Structure guard, `git diff --check`, typecheck/build passed.
- Real copies, release venv (`backend/.venv`, no torch/tensorflow), scheduled command path (`run_scheduled`: training lock → brief job lock
  + snapshot → train from the snapshot → brief job lock + verified publication → cleanup):
  - NBA `adaptive-train`: success, 22 fit records published, 0 failed, combiner success, period `2027:2026-W43`; deep members skipped
    (no library, no fit for this definition) as designed.
  - NHL run A (`now=2026-10-01`, forced): success, 16 published, 0 failed, combiner success, period `2027:2026-W41`.
  - NHL run B (`now=2026-10-05`, unforced): success, 16 published, 0 failed; due reason "no combiner fit for this definition yet" because
    run A's rows carry the real clock (Oct 7) and are invisible at Oct 5 — fits are never backdated, so a next-period run on a real copy
    needs real time to pass. The unforced next-period path (new week + settled prospective forecast → new fit with `prospective = 1`) is
    verified on the synthetic NHL and NBA stores (`test_adaptive.py`).
  - After the runs: both copies `due: false` ("fit in force already prepared … weekly schedule"); snapshot folders empty.
- Forecasting during training (real NHL copy, while a trainer held `train.lock`): `db.store_lock("nhl", wait_seconds=0)` acquired in
  0.00 s; `adaptive.training_lock("nhl")` not acquirable (held by the trainer); `adaptive.record(write=False)` at the next game window
  produced winner + reg3 forecasts for 2 eligible games (10 and 5 contributing members) in 38 s with no errors.
- Earlier run refused at publication with "definition changed during training (…→…): nothing published" after the module was edited
  mid-run — the guard working as designed; that run's artifacts are orphaned on disk and unused.
- Not done: no live migration, no deployment, no browser rerun since the UI change for partial runs (typechecked only).

## 2026-10-07 member-record write failure fix — Claude (Fable 5.1)
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_adaptive.py -q`: **22 passed** (new: member row write failure →
  combiner partial → due → unforced retry succeeds). `git diff --check`, `python3 scripts/check_project.py`: passed. Publication-path change
  only; no real-copy rerun.


## 2026-10-07 default threshold display
- Existing threshold card moved above overall performance in live/two-hour views for all selected NBA/NHL markets; research toggle no longer required. No new API or data calculation.
- `cd frontend` then `npm run build`: passed TypeScript + Vite. `python3 scripts/check_project.py` and `git diff --check`: passed.
- Browser inventory: no tabs; `curl --max-time 3 http://127.0.0.1:5196/performance`: connection refused. Desktop/phone navigation and appearance unverified; full readiness not repeated. Not deployed.


## 2026-10-07 missed prediction recovery (local, not activated)
- `pytest backend/tests/test_storage.py -q`: 24 passed; `pytest backend/tests/test_near_tipoff.py backend/tests/test_live.py -q`: 38 passed. Recovery tests exercise NBA winner and all NHL primary markets, own-outcome independence, timestamps, duplicate prevention, separation and periodic-cycle ordering.
- `npm run build` in frontend: TypeScript/Vite passed; `python3 scripts/check_project.py` and `git diff --check`: passed.
- SQLite backup copies only, `/private/tmp/codex-missed-recovery`: NHL 42 rows / 7 games; NBA zero; exports retained as `*-recovery.json`. No live migration or writes.
- Attempted API `NBA_DATA_DIR=/private/tmp/codex-missed-recovery NBA_API_MODE=real PYTHONPATH=backend backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8026`, frontend `API_PROXY_TARGET=http://127.0.0.1:8026 npm run dev -- --host 127.0.0.1 --port 5206`: both failed to bind with EPERM. Browser desktop/phone verification unavailable. Full readiness pending; not release-ready certification.

## 2026-10-08 release move to `a4c466e` (adaptive-v1 + recovery + default thresholds), both sports — Claude (Fable 5.1)
Authorised by the user ("activate the combined release for both NBA and NHL using backups and the documented release procedure").
Started 22:46 EDT after the last NHL game (02:00 UTC) had begun and the 22:15 nightly jobs had run; no scheduled game within 2 h; both job locks free.
- Backups: `data/backups/nba-before-release-20261008.sqlite` (23.5 MB) and `nhl-before-release-20261008.sqlite` (143.6 MB), SQLite online backup from immutable connections, `quick_check` ok, schema 23.
- `git -C ~/nba-predictor-live merge --ff-only a4c466e`: fast-forward from `f1e0181`; worktree clean. Requirements and frontend deps unchanged (no pip sync / npm ci).
- `npm run build` in the release frontend (node v22.23.2): built in 431 ms. `launchctl kickstart -k` api + web: `/api/health` ok, web 200; `/api/research/adaptive` correctly answered 503 "Adaptive tables missing" before migration.
- `release-check` (release venv, guard env) before migration: both sports refused with "store schema 23 differs from this release's schema 25" — expected. `migrate` from the release checkout: applied `0024_adaptive.sql`, `0025_recovery.sql` on both stores. `release-check` after: NHL and NBA `problems: []`, commit `a4c466e4…`.
- First live training (research venv on the release code, `OMP_NUM_THREADS=4`): NHL `adaptive-train` → **success, 16 fit records published, 0 failed, combiner success**, period `2027:2026-W41`, 2,846 training rows per market, 15 members (torch/keras season-start fits kept: 39 current-season results < 100), snapshot removed. While it trained: `db.store_lock("nhl", 0)` acquired instantly, training lock held by the trainer; the 03:08 UTC odds cycle ran normally. NBA `adaptive-train` → **success, 22 fit records published, 0 failed, combiner success**, period `2027:2026-W43`, 2,643 training rows per market, 21 members (18 winner / 9 line members; torch/keras fitted on 8,919 games), no member failures, snapshot removed; `/api/research/adaptive?sport=nba` → combiner success, due false.
- Scheduler: `com.nba-predictor.adaptive-train.plist` copied to `~/Library/LaunchAgents` and bootstrapped (loaded, fires 09:30 local, both sports, research venv on the release checkout).
- Recovery: the 03:08 UTC NHL odds cycle's recovery step reconstructed 3 missed games (18 rows, bounded per cycle); `/api/recovered-forecasts?sport=nhl` shows them as "retrospective" (winner 2–1 at 50%+), while `/api/thresholds` and the live record are unchanged (winner 32 forecasts, 17–14; `predictions` 9,002 and `market_forecasts` 965 exactly as before the move). NBA: 0 missed eligible games.
- Live API after the move: `/api/research/adaptive?sport=nhl` → combiner success, due false ("fit in force already prepared for 2027:2026-W41"); first live `adaptive_forecasts` rows will be written by the first two-hour cycle (NHL games from 23:00 UTC Oct 8); the first ordinary next-week refit is the Monday 09:30 run.


## Missing NGBoost dependency repair — Codex, 2026-10-08
- Installed project-pinned `ngboost==0.5.11` into research AND release environments (training and pickled-model inference require it). uv resolver added dependencies without replacing existing packages; `uv pip check` passes in both. No source/release commit changes.
- Library fingerprint changes affect both sports: due() now reports no current-definition combiner, NBA `3f090596b8efcb6e`, NHL `2c63d35ec5a24519`. Started installed launchd adaptive-train job immediately with kickstart; it runs NHL then NBA. Training completion remains pending; do not describe prior fits as matching these new definitions.
- Stored next eligible games: NHL Oct 8 23:00 UTC (two-hour window opens 17:00 Toronto); NBA Oct 20 19:00 UTC. Zero adaptive rows before these windows is not evidence of a recording defect. Scheduler first execution initiated manually for this repair.
- Checks: structure guard and whitespace pass; no frontend edits/browser rerun or full readiness. Next: inspect `data/adaptive-train-launchd.log`, verify successful current-definition combiners and NGBoost member fit, then first prospective forecast. Mac must remain awake during training.


## 2026-10-09 post-activation refit under the NGBoost definitions — Claude (Fable 5.1)
- `data/adaptive-train-launchd.log` (scheduler job kickstarted by Codex 11:06 UTC Oct 8, research venv, release code): NHL success,
  16 published, 0 failed, combiner success (def `2c63d35ec5a24519`); NBA success, 22 published, 0 failed, combiner success
  (def `3f090596b8efcb6e`, NGBoost member fit: 104 fits). Read-only store checks: `adaptive_fits` new-definition rows NHL 1 combiner +
  15 members, NBA 1 + 21; snapshots removed; job_runs continued every cycle (odds 07:15–07:54 UTC) while training.
- `adaptive_forecasts`: 11 rows, all `unavailable` ("no combiner fit for this definition yet"), game `nhl:2026020065`, generated
  01:11/01:45 UTC Oct 9 before its 02:00 UTC start; `/api/research/adaptive/game/nhl:2026020065` shows them. `recovered_forecasts`:
  96 rows / 16 NHL games; live counts unaffected.
- `requirements-release.txt` + 21 pins (ngboost closure); `git diff --check`, structure guard passed; release fast-forwarded, `release-check`
  clean NHL and NBA.


## Oct 9 — automatic training sleep protection (Codex)
- Installed and reloaded the existing idle adaptive-train LaunchAgent with `/usr/bin/caffeinate -i /bin/sh -c ...`; `launchctl print` confirms both sports remain in the command, same environment, same working directory and 09:30 calendar. No extra training started. Backup of former installed plist: `/private/tmp/adaptive-train-before-sleep-guard.plist`.
- `plutil -lint`, structure guard, `git diff --check` passed. Four shell cases validate success and either/both sport failures; NHL failure cannot be masked by NBA success. No frontend changes/browser checks or full readiness run.
- Both current combiners still successful; NHL 11 unavailable adaptive rows, NBA zero. Genuine live prediction still unverified. This guard covers idle sleep during training only, not lid closure or idle gaps between collection jobs.

## Oct 9 — adaptive recovery implementation (Claude, not deployed)
- Tests: `tests/test_adaptive_recovery.py` 11 passed; deep venv run of deep_study, adaptive, adaptive_recovery, recovery, storage, live: 93 passed. Full suite in `backend/.venv`: 667 passed, 8 failed, all in `test_deep_study.py` (`No module named 'torch'/'keras'`), identical on unmodified HEAD.
- Real-data dry run on a scratch copy of the NHL store (migrated to 0026, raw cache linked read-only, definition pinned to the live `2c63d35ec5a24519` in the script only): 10 missed games, 60 rows reconstructed, 0 unreconstructable, 0 errors; all rows pass combiner training cutoff, latest result and quote time at or before the cutoff; winner rows use 12 members; nine games use primary-reconstruction lines, one a live primary line; live and primary tables unchanged. Cost about 40 s per game.
- API: TestClient on the copy, `/api/recovered-forecasts?sport=nhl` 200 with `adaptive` 60 records in 6 groups; demo mode 200 with empty `adaptive`. Browser not rerun: no frontend change. Structure guard and `git diff --check` pass.
- Follow-up (same day): test clock fix in `tests/test_adaptive.py`. Deep venv: full suite 676 passed (real clock) and 676 passed (probe plugin setting every `app.*.utc_now` to 2027-03-01T12:00:00Z). `~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready` exit 0: structure guard, `tsc --noEmit`, `vite build`, 676 passed. Browser not rerun (no frontend change).


## System status panel — Codex, Oct 9
- Added default Performance status panel for both sports in existing Training component, using existing single API client. Independent loading/error/retry states for training inputs, adaptive fit/due state and recovery; visible-page minute refresh plus manual refresh. Demo explicitly labelled; pending/settled market counts are distinct from fit success and unique games. Optional adaptive recovery payload works with old release API. No backend/model/definition edits.
- Verification: frontend build/typecheck, structure guard and whitespace pass. Browser preview http://127.0.0.1:5197/performance (API proxy :8100): NBA desktop shows 21/21 fits and empty prospective record; NHL 390px shows 15/15 fits, 24 pending pairs and 16 recovered primary games. Document width equals 390px, screenshot inspected. Viewport reset. User navigated preview to History before manual refresh/expanded-error checks, so those remain unverified; error/demo UI implemented but not browser-injected. Full readiness not repeated (prior 676 backend pass predates this frontend-only change).
- Existing endpoint does not expose recovery backlog/deferred jobs or active training process: panel explicitly marks those unavailable instead of claiming completion. Working tree contains Claude's recovery work; preserved unchanged. Panel not committed/deployed. Next: review panel, add backlog telemetry in the recovery owner, and complete recovery review/safe release.

## Model combinations of saved forecasts — 2026-10-10 (Claude, Opus 5.5)
Data: session-scratch copies (`NBA_DATA_DIR=<scratchpad>/data`: immutable snapshots of nba/nhl.sqlite + copies of
`research/candidates/oof-*.json.gz`). Live `data/` only read (snapshot), never written.
- API: `NBA_DATA_DIR=<copy> NBA_API_MODE=real <deep venv>/python -m uvicorn app.main:app --port 8765`; web:
  `API_PROXY_TARGET=http://127.0.0.1:8765 npx vite --port 5199 --strictPort` → http://localhost:5199/performance?sport=nhl.
- `GET /api/research/combinations?sport=nhl&market=winner` → 200, `available`, 11,870 games, earlier 2018–24 (9,078),
  later 2025–26 (2,792, flagged spent reserved-test), 4 goalie models excluded; first call 9.3 s, cached after.
  `sport=nba` winner/spread → `available` (6,605 games); nba `reg3` → `no_stored_forecasts`. Bad `base` → 422 (pytest).
- Browser (Claude in Chrome, viewport 500 px; window resize had no effect, so 1440 and 390 px checked in same-origin
  iframes): card "Model combinations · saved forecasts" opens and loads (spinner) → 6 tables; first rows of the
  leaderboard, pairs, add-a-model and all four holdout rows equal the API values. 1440 px and 390 px: page scrollWidth =
  viewport (no page overflow); at 390 px four tables scroll inside `.table-wrap`. "Compare additions" with Elo + Goals model
  showed loading then "Group: Elo + Goals model — log loss 0.6686" and first addition matching the API (−0.0031
  [−0.0038, −0.0024], 258 corrected / 201 introduced). API stopped → "Couldn't load data. 500: API unavailable…" + Try
  again. NBA spread card shows the model-only-line note.
- Not verified in browser: the `no_stored_forecasts` empty state (no NBA reg3 page exists; covered by API + pytest), demo
  mode (pytest only).

# Smoke test — usable app: one-command startup, results overview, training status (2026-10-10)

Tester: Claude (Opus 5.5) via Claude-in-Chrome (macOS Chrome) and curl. Real data (dev copy of the live stores).

## Startup
- Found before the change: a 7-day-old dev API (pid 6850, :8000, started before many backend commits, no reload, reading
  `data/` read-write) and a 7-day-old Vite (:5173) from this checkout; the dev code expects schema 0027 while the live
  stores are at 0026. The release app (launchd, `~/nba-predictor-live` @ f7ee505) answers on :4173 / :8100. A hung uvicorn
  from this checkout (pid 82323, :8093) was left alone.
- `sh scripts/dev.sh` (from the repo root): stopped the two stale dev servers (cwd inside this checkout only), copied
  `data/nba.sqlite`/`nhl.sqlite` by read-only SQLite backup plus `research/ raw/ eval/` (APFS clones) to
  `~/nba-predictor-dev-data`, migrated the copy, started API + Vite, waited for `:8000/api/health`, `:5173/` and the
  `:5173/api/health` proxy → printed `dev.sh: ready` (every launch this session; copy time not measured).
- Killing the API process → the launcher stopped Vite too, printed the log tails, exited 1, both ports free (verified
  with `lsof`). TaskStop of the launcher → both ports free.
- Release app (:4173/:8100), launchd agents, the pre-training gate (pids 81169/81170) and `data/` were not touched.

## API (curl via the Vite proxy, actual)
- `/api/health` → `{"status":"ok","mode":"real","api_version":"0.3.0","data_snapshot_at":"2026-10-10T19:59:04Z"}`.
- `/api/training-run?sport=nhl` (0.46 s) → running false ("No process holds this sport's training lock"), last run
  2026-10-10T04:23:06Z success 15 models, no failure, serving adaptive `8631cc578d16aa5c` (18 forecasts, latest
  18:36:55Z), primary `hockey_elo-…-policy=near_tipoff` (18:33:24Z); not serving: `abfc617c4309ed33`, 3 models written,
  not published (the 19:17 interrupted pre-training attempt's leftovers).
- `/api/training-run?sport=nba` (0.78 s) → not running; last run 09:23:07Z success 21 models; no live forecast yet;
  `2a0111fd73fc5cfa` "published, no live forecast yet".

## Browser
| Flow | Result |
|---|---|
| Desktop 1440 wide: `/` shows Development copy banner, "Results so far" NBA + NHL cards, Training cards for both | Pass |
| NHL live record: 56.8%, 21 of 37 correct, settled 37, pending 3, settled games Oct 2 – Oct 9; small-sample note; archive 33/57; reconstructed 10/16 (labelled retrospective); backtest 56.2% of 2,792 | Pass (matches release API `/api/predictions` record 21–16, 3 pending) |
| NBA: no live forecasts, notice explaining preseason exclusion; backtest 67.2% of 2,643 labelled retrospective | Pass |
| Training cards: Not running, last run, last failure none, what is forecasting, trained-not-forecasting; Technical details expands (versions, paths, lock note) | Pass |
| Sport toggle NHL keeps the overview for both sports | Pass |
| "Every prediction →" (NHL) → `/history?sport=nhl`, Prediction history, 40 rows, no error | Pass |
| Phone width: app in a 390 px iframe (window resize did not apply to the maximized Chrome window): viewport 388, scrollWidth 388, no element past the right edge; stat tiles 2×2 | Pass |
| API stopped: "API NOT REACHABLE" banner with the start command, error card with Try again | Pass |
| Loading state | Not verified (requests finished before a capture; code path is the existing `AsyncView`/`Loading`) |

## Gaps
- Results and games come from the copy (timestamp shown); restart the launcher to refresh. Training status is live.
- "Expected models" for a running trainer is shown only when its version equals this checkout's; otherwise the last run's
  count is shown for reference (this worktree has uncommitted edits, so its version hash differs from the trainer's).
- A running-trainer state was exercised only in unit tests (no trainer ran during this session).
- Header "REAL DATA" pill is clipped at phone width (existing header layout, not changed here).
- The headless-Chrome phone screenshot is unusable (minimum window ≈ 500 px); the iframe measurement is the evidence.

## Automated checks (actual)
- `python3 scripts/check_project.py` → "Structure guard passed". `git diff --check` → clean. `cd frontend && npm run build` → OK.
- `backend/.venv/bin/python -m pytest -q tests/test_training_run.py tests/test_api.py tests/test_training_monitor.py` → 21 passed
  (test_training_run: 6 passed after adding the not-serving test).
- Full suite, main venv (excluding the concurrent writer's new `tests/test_combination_stored.py`): 696 passed, 8 failed
  (all `tests/test_deep_study.py`: `ModuleNotFoundError: torch` / `keras`; those libraries exist only in
  `~/nba-predictor-deep`). Not caused by this change; not rerun in the deep venv.
- `backend/.venv/bin/python scripts/check_project.py --ready` → pytest 701 passed, 8 failed (same deep-study tests), so
  **--ready FAILED** in the main venv.

## Confidence thresholds & exact model subsets (saved forecasts) — 2026-10-10 (Claude, Opus 5.5)
- Servers (own pair, the other session's :8000/:5173 untouched): `cd backend && NBA_API_MODE=real NBA_DATA_DIR=~/nba-predictor-dev-data
  NBA_LIVE_DATA_DIR=<repo>/data .venv/bin/python -m uvicorn app.main:app --port 8011`; `cd frontend && API_PROXY_TARGET=http://127.0.0.1:8011
  npx vite --port 5183`. Data = the dev copy made by scripts/dev.sh (snapshot 2026-10-10T20:07:37Z); `data/` not read or written.
- Adaptive histories exported on the dev copy with the research venv: `NBA_DATA_DIR=~/nba-predictor-dev-data
  ~/nba-predictor-deep/backend/.venv/bin/python -m app.cli --sport {nhl,nba} combination-export-adaptive` → 3 definitions per sport
  (NHL 4,246–4,260 winner/reg3 games, NBA 3,962 winner games), 8 walk members per definition listed as not stored. Note: dev.sh
  re-copies research/ on restart, so the export must be rerun after it.
- curl via :5183 proxy, all 200: thresholds nba winner 796 KB 0.98 s; nhl reg3 373 KB; nba spread 518 KB; nhl winner
  adaptive:8631cc578d16aa5c 363 KB; subsets 224–245 KB, 0.3–2.2 s uncached.
- Browser (Claude in Chrome), Performance › Research › "Confidence thresholds & model subsets": all five views (Thresholds,
  Agreement × confidence, Add a model, Exact subsets, Check a rule via "Details") rendered real rows; source switch to
  adaptive:2a0111fd73fc5cfa and Add `torch_mlp` rendered; "No saved history" lists the 8 NBA walk members. Widths: 606 px window
  and a 1280 px same-origin iframe: document scrollWidth = viewport, card scrollWidth = clientWidth. Phone: 390 px iframe
  (NHL reg3, every view): document 386 = viewport, card 344 = 344; wide tables scroll inside `.table-wrap`. One overflow found
  and fixed (long source names in the facts list; `min-width: 0` on `#thresholds .facts dd`). Console: no errors.
- Not verified: a real phone device; the release servers (:4173/:8100) — nothing deployed.

### Codex focused threshold review — 2026-10-10
- URL: http://127.0.0.1:5183/performance?sport=nhl (existing in-app browser tab; API backing the preview).
- Opened Confidence thresholds & model subsets: loading then NHL winner pool results, real model names, archive digests, earlier/later labels and cumulative threshold rows rendered.
- Reproduced source recovery bug: Market = Game total goals, open explorer, choose adaptive:8631cc578d16aa5c. Empty message appears and Saved forecast source selector disappears (DOM count 0); cannot directly switch back to pool. Returned Market to Winner afterwards.
- Independent commands: `backend/.venv/bin/python -m pytest backend/tests/test_combination_thresholds.py backend/tests/test_combination_stored.py -q` → 13 passed, one Starlette warning; `npm --prefix frontend run typecheck` passed; `git diff --check` passed. `python3 scripts/check_project.py` failed: unexpected top-level directory `.claude`.
- Not verified this review: dedicated desktop/phone viewport checks, full navigation/error/demo flows, full readiness, training or release behavior. No servers restarted, no deployment or training changes. See HANDOFF for the independently reproduced later-outcome-dependent shortlist bug.

### Codex threshold-fix recheck — 2026-10-10
- Existing in-app browser at http://127.0.0.1:5183/performance?sport=nhl: selected Game total goals, expanded threshold explorer, selected adaptive:8631cc578d16aa5c. Source picker and Back to the candidate pool remained visible during loading and in the empty state; clicked the back control to return to pool.
- Focused backend command `backend/.venv/bin/python -m pytest backend/tests/test_combination_thresholds.py backend/tests/test_combination_stored.py -q`: 16 passed, one Starlette warning, 2.07s. Frontend typecheck, structure guard and whitespace check passed. No full readiness or new phone viewport verification; full-suite completion remains to be recorded by its running session.

## Threshold explorer review fixes — 2026-10-10 ~22:55 UTC (Claude, Opus 5.5)
- Same servers (:8011 API restarted with the fix, :5183 web, dev copy). NHL › total, Performance › Research › thresholds card:
  pool loads (50%+ row 2792 of 2792, 1505–1287); switching the picker to adaptive:8631cc578d16aa5c shows "No saved out-of-sample
  forecasts for this market and source — choose another source above." with the picker still present and value kept;
  "Back to the candidate pool" and the picker each return to the pool table (1 table, value pool).
- Widths (same-origin iframes): 390 px — document 386 = viewport, card 344 = 344, picker 312 px, in pool and empty states;
  1280 px — document 1276 = viewport, card 954 = 954. Before the CSS fix the picker was 492 px and the phone document 529 px.
- Not verified: a real phone device.

## Threshold explorer: rule ledger and comparisons — 2026-10-10 ~23:15 UTC (Claude, Opus 5.5)
- Servers :8011 (restarted with the change) / :5183, dev copy. NHL › Exact subsets: limits panel (Archive, Models searched, Not
  searched, Rules, Selection, Best single model); both rankings; columns earlier/later picks (coverage) · W–L · win rate [95%],
  best single on the same games, best single's most confident picks. "Details & games" on hockey_logistic≥75 & extra_trees≥70 &
  multinomial_l2≥75: rule 44 of 2792, 38–6, 86.4% [73.3–93.6]; (a) same games 38–6 (same side 44/44); (b) 44 most confident
  (≥78.8%) 38–6; constituent, by-season and ledger tables; first ledger row 2024-10-12 CHI @ EDM, members home 81.2/77.3/79.7%,
  5–2, actual away, loss, best single home 82.3% loss. Frozen label shown; after "Check rule" the exploratory label shown.
- CSV via the :5183 proxy: `content-type: text/csv`, `attachment; filename="ledger-nhl-winner-all.csv"`, 172 lines (171 games
  + header). JSON ledger later: 44 games, 38 wins.
- Widths: 1440 px document 1440 = viewport, card 1118 = 1118; 390 px iframe (NBA subsets and rule detail) document 386 =
  viewport, card 344 = 344, ledger 50 rows. Not verified: a real phone.

### Codex rule-ledger focused review — 2026-10-10
- URL http://127.0.0.1:5183/performance?sport=nhl&market=total, existing in-app browser. Exact subsets → Details & games for Goals regression ≥65% & poisson_draw:nhl_base ≥60%: constituent probabilities, two labelled comparisons, season totals, later ledger and both CSV links render.
- Visible later ledger: 2026-04-08 EDM @ SJS, model line 7.5, under 78.1% / under 68.1%, score 5–2, win; best single under 59.5%, win. One-pick sample explicitly warned. Download not independently exercised; no fresh phone viewport/full-app/error-state run.
- `backend/.venv/bin/python -m pytest backend/tests/test_combination_thresholds.py backend/tests/test_combination_stored.py backend/tests/test_api.py -q`: 32 passed, one Starlette warning (2.00s). Structure guard, whitespace and frontend typecheck passed. No training, deployment or live-data writes.

## Research presentation: distinct later pick sets, reasons, coverage — 2026-10-11 (Claude, Opus 5.5; author)
- Browser verification BLOCKED (desktop and 390 px not run). The only dev API, :8011 (pid 68117, started 19:12 with no reload), predates
  this change and the previous coverage increment: `curl -s -m 60 "http://127.0.0.1:8011/api/research/thresholds?sport=nba&market=winner&source=pool"`
  → status available, but no `coverage_definition`, no `split` and no per-period coverage keys. :8000/:5173 are older still (16:07).
  No service was started or restarted (not approved). With a stale API the new sections are absent: every new field is optional
  in the types, so nothing breaks, but this is not verification of the new flows.
- Verified without a browser: `npm --prefix frontend run typecheck` passed; `npm --prefix frontend run build` built (398.55 kB JS).
- To verify: restart the :8011 API on the current code (with approval), then at 1440 px and 390 px check:
  - Combinations and threshold cards: the "Games and forecast coverage" table and its scope sentence;
  - an insufficient-evidence market or source shows the backend reason, with the source picker still usable;
  - NBA winner pool › Exact subsets: "18 of 20 rules (13 distinct later pick sets)"; overlap note "20 rules make 15 distinct later
    pick sets — set 9: rules 9, 12, 14, 15, 16, 18 (278 picks)"; the "Later pick set" column; the two-comparison note;
  - no horizontal overflow.

## Research presentation cleanup: wording, shared-picks test, legacy coverage, plurals — 2026-10-11 (Claude, Fable 5.1; writer)
- Browser verification BLOCKED, same cause as the entry above: `:8011` (pid 68117, started 19:12, no reload) still serves the
  older code; `:5183` (Vite, HMR) already serves the current frontend source. No service was started, stopped or restarted
  (not approved). Exact current processes and the proposed restart are in HANDOFF.md (top entry).
- Verified without a browser: `npm --prefix frontend run typecheck` exit 0; `npm --prefix frontend run build` built (399.58 kB JS);
  focused backend tests 98 passed with `NBA_DATA_DIR=<empty scratch>`.
- To verify after the approved restart, at 1440 px and 390 px on http://127.0.0.1:5183/performance?sport=nba:
  - Exact subsets (NBA winner, pool): headline "18 of 20 rules (13 distinct later pick sets)"; overlap note "these 20 rules make 15
    distinct later pick sets — set 9: rules 9, 12, 14, 15, 16, 18 (278 picks)" followed by the definition that says "made the same
    picks … mean probability and calibration gap can differ"; footer says outcomes are identical by construction, not records;
  - NBA spread, pool: headline "1 of 20 rules (1 distinct later pick set)" (singular);
  - a rule detail where the best single is a member: "(a member: the same picks and outcomes)";
  - "Games and forecast coverage" tables on the combinations and thresholds cards (full shape). The older-API note cannot be seen
    against the restarted server; it is reachable only by pointing the web app at an API without per-period coverage fields;
  - no horizontal overflow, no console errors.

## Browser verification: research presentation cleanup — 2026-10-11 ~03:40–04:20 UTC (Claude, Fable 5.1; the session that wrote the cleanup, so this is the author's verification, not an independent review)
- Setup (user-approved): the stale `:8011` API (pid 68117; verified first: `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8011`,
  cwd `<repo>/backend`, ppid 1, no children) was stopped with `kill 68117` and restarted on the current code as pid 10146 from `<repo>/backend`:
  `NBA_API_MODE=real NBA_DATA_DIR=/Users/andry/nba-predictor-dev-data NBA_LIVE_DATA_DIR=/Users/andry/nba-predictor-dev-data OMP_NUM_THREADS=1
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8011` (both data variables point at the dev copy; `data/` not read).
  Log: a fresh regular file in the session scratchpad (`api-8011-20261011T034146Z.log`, created with `noclobber`); `~/nba-predictor-dev-data/api.log`
  and `web.log` untouched (mtimes 23:07/23:04 local), no marker written, `scripts/dev.sh` not run. Vite `:5183` (pid 43514) left running.
  Startup: `app/main.py` has no startup hook, scheduler or thread; a store needing migration gives a 503 per request. Startup log: "Application
  startup complete" only; `/api/health` → `{"status":"ok","mode":"real","api_version":"0.3.0","data_snapshot_at":"2026-10-10T20:07:37Z"}`;
  after the whole session the API had 0 child processes. Training-status text on the page is snapshot-derived here (both dirs = the copy).
- API fields via the Vite proxy (`curl -s "http://127.0.0.1:5183/api/research/subsets?sport=nba&market=winner&source=pool"`, 200, 336 KB, 1.0 s):
  `later_overlap_definition` starts "Descriptive later-period overlap: … share one pick set. Rules in one set made the same picks …";
  `later_overlap.reliable` = 20 rules, 15 sets, shared set 9 = rules 9, 12, 14, 15, 16, 18 (278 picks), beats_matched {18 rules, 13 sets};
  coverage keys games/settled/pending/used/excluded_missing_member/missing; split earlier 2022–2024, later 2025–2026. Thresholds (797 KB, 0.7 s):
  coverage earlier 3962/3962/0/3962/0, later 2643/2643/0/2643/0. NBA spread subsets: beats 1 rule, 1 set, 17 sets.
- Viewports: the Claude-in-Chrome window could not be resized (`resize_window` reported success but `innerWidth` stayed 511; the window sits
  off-screen at y=982). As in earlier entries, same-origin iframes of exactly 1440 px (NBA, NHL) and 390 px (NBA) were used inside
  `http://127.0.0.1:5183/@vite/client` (a non-React page on the same origin); layout was measured from each iframe document
  (`innerWidth`, `documentElement.scrollWidth`, card `scrollWidth`) and text read from its DOM; screenshots at device resolution.
  Every check below: page `scrollWidth` = viewport width (1440/1440/390); no element outside the viewport except inside `.table-wrap`
  (overflow-x auto; at 390 px the subsets table wrapper is 316 px wide with 492 px of scrollable content). No "undefined"/"NaN" text in the card.
- 1440 px, NBA winner, pool: Facts, "Games and forecast coverage" scope sentence + table (Earlier (2022, 2023, 2024) 3962 3962 0 3962 0 none;
  Later (2025, 2026) 2643 2643 0 2643 0 none) + definition. Exact subsets: limits panel (15 models, 105 pairs, 455 triples, 161,210 rules,
  174 collapsed, best single elastic_net:nba_base); headline "18 of 20 rules (13 distinct later pick sets) in the first list and 3 of 20
  (3 distinct later pick sets) in the second"; overlap note "these 20 rules make 15 distinct later pick sets — set 9: rules 9, 12, 14, 15, 16, 18
  (278 picks)." followed by the new definition; second list "15 distinct later pick sets — set 5: rules 5, 10 (324 picks); set 12: rules 13, 16,
  17, 18, 20 (335 picks)"; "Later pick set" column 1…15 with "9 (shared by 6)" on rules 9, 12, 14, 15, 16, 18; footer "made the same later picks,
  so their win–loss–push outcomes and win rate are identical by construction; their mean probability and calibration gap can still differ…".
  Details & games on rule 9 (mlp:nba_base ≥ 80% & elastic_net:nba_base ≥ 80%): later record 278 of 2643, 237–41, 85.3% [80.6–88.9], mean p 84.8%;
  row "(a) elastic_net:nba_base on the SAME games (a member: the same picks and outcomes)" 237–41, mean p 85.3% (same outcomes, different
  probability, as the wording now says); (b) 278 most confident 236–42; constituent models, by season; ledger "278 games", rows with both
  members' probabilities, shared pick, score, result and best single; CSV links present.
- 1440 px, NBA spread, pool (market change collapses the card, pre-existing): headline "1 of 20 rules (1 distinct later pick set) … 2 of 20
  (2 distinct later pick sets)" (singular correct); overlap "17 distinct later pick sets — set 11: rules 11, 14, 15, 20 (98 picks)";
  second list "18 … set 8: rules 8, 14, 15 (98 picks)". Adaptive sources in spread: "No saved forecasts: no saved forecasts for this market."
  with the source picker still shown; back to pool recovers. NBA winner adaptive sources (3): all available, "0 of 20 rules (0 distinct later
  pick sets)".
- 1440 px, NHL winner, pool: models list + "Excluded: logit_l2:nhl_goalie (used the actual starting goalie…)"; coverage Earlier (2018–2024)
  9078/9078/0/9078/0 none, Later (2025, 2026) 2792/2792/0/2792/0 none; headline "0 of 20 rules (0 distinct later pick sets)" both lists;
  overlap "17 … set 7: rules 7, 8, 12 (57 picks); set 11: rules 13, 16 (80 picks)". NHL winner adaptive:8631…: "18 of 20 rules (9 distinct
  later pick sets) … 14 of 20 (8 distinct later pick sets)"; overlap "10 distinct later pick sets — set 1: rules 1, 3 (15 picks); set 2: rules
  2, 4, 5, 6 (16 picks); set 4: rules 8, 9, 10 (14 picks); set 5: rules 11, 19 (15 picks); set 6: rules 12, 14 (22 picks); set 8: rules 15, 18
  (16 picks); set 10: rules 17, 20 (27 picks)" — all equal to the evidence summaries.
- 390 px, NBA: same coverage table, headline, overlap note and set 9 rows; rule 9 detail with the "(a member: the same picks and outcomes)"
  row and the 278-game ledger; spread headline "1 of 20 rules (1 distinct later pick set)". Screenshots: headline box, overlap note (text
  wraps, no clipping).
- Loading / error / recovery (fetch patched inside the iframe only; no service touched): a 4 s delay shows "Loading…" with a `role=status`
  spinner and the source picker still usable; a rejected `/research/subsets` fetch shows the `role=alert` "Couldn't load data. Cannot reach the
  API. Is the backend running?" with "Try again" and the picker still present; after restoring fetch, "Try again" loads the real result
  ("18 of 20 rules (13 distinct…"). Insufficient evidence (no real sport/market/source returns it — a full sweep of 2 sports × 6 markets × 4
  sources via curl gave only `available` or `no_stored_forecasts`): a mocked `insufficient_evidence` response rendered "Insufficient evidence:
  needs more than 2 seasons for an earlier/later split (2 saved seasons) [mock]. Earlier seasons 2021; later seasons 2022, 2023. Not compared:
  Elo (mock exclusion reason)." with the picker present; switching back to pool recovered the real data.
- Legacy / absent coverage (mocked thresholds responses, 1440 NHL and 390 NBA): coverage reduced to `{games, missing}` per period →
  "This response comes from an older API that does not report settled, pending and every-model-saved counts per period, so the coverage table
  is not shown. Reported only: Earlier (2018, …, 2024): 9078 saved games; missing forecasts Elo 244. Later (2025, 2026): 2792 saved games;
  missing forecasts none. Restart the API on the current code for the full table." — no table, no denominator, no "of undefined"; at 390 px
  "… Elo 244, Ridge points 3 …" with no overflow. Coverage removed entirely → no "Games and forecast coverage" section; the thresholds tables
  still render.
- Console: no errors or exceptions during the whole session, including a reload of the 390 px frame with tracking active. API log: 333 lines,
  328 × 200; one `422` for `market=puckline` (nhl) from another client of `:5183` — "puckline" appears nowhere in this checkout's frontend or
  backend source and was not requested by these frames; the API rejected it as designed. No 5xx, no traceback, no migration message.
- Not verified: a real insufficient-evidence market (none exists in this snapshot); the pending-game wording (every real list has 0 pending);
  browser window at a true 1440/390 OS window (iframes used instead).
- After the check: the browser tab was closed; the restarted `:8011` API (pid 10146) was left running on the current code, as approved.

## Presentation cleanup: focused review + browser verification — 2026-10-11 ~04:00 UTC (Claude, Fable 5.1; reviewer, authored none of the increment)
- Services (verified, NOT restarted): API `:8011` = pid 10146, `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8011`,
  cwd `<repo>/backend`, started 23:41:46 local on 2026-10-10 (after the last code edit, 23:32), env `NBA_API_MODE=real
  NBA_DATA_DIR=/Users/andry/nba-predictor-dev-data NBA_LIVE_DATA_DIR=/Users/andry/nba-predictor-dev-data OMP_NUM_THREADS=1`; its stdout/stderr
  is another session's scratchpad file `api-8011-20261011T034146Z.log` (read only, left in place). This differs from the HANDOFF proposal
  (pid 68117, live `NBA_LIVE_DATA_DIR`) and already matches the requested isolation, so no process was killed or started and no new log was
  created. Web `:5183` = pid 43514 (Vite, `API_PROXY_TARGET=http://127.0.0.1:8011`), untouched. Nothing on :8000/:5173.
  `curl -s http://127.0.0.1:5183/api/health` → `{"status":"ok","mode":"real","api_version":"0.3.0","data_snapshot_at":"2026-10-10T20:07:37Z"}`;
  the page shows the "Development copy" banner. Startup: `app/main.py` has no lifespan/startup hook and migrations only raise a 503 on
  demand; the process log shows "Application startup complete" with no job, training or migration line. Training-status panels read the
  snapshot copy, so they are not evidence of live training. `scripts/dev.sh` not run; `data/` not read.
- API (curl via `:5183`, actual): `GET /api/research/subsets?sport=nba&market=winner&source=pool` → `later_overlap_definition` contains
  "made the same picks … mean probability and calibration gap can differ"; `later_overlap.reliable` = rules 20, 15 distinct sets,
  `beats_matched` {rules 18, distinct_later_pick_sets 13}, shared set 9 = rules 9, 12, 14, 15, 16, 18 (278 picks); `lift` = 3 of 20, sets
  5 and 12 shared. `coverage` carries games/settled/pending/used/excluded_missing_member/missing for both periods on thresholds,
  combinations and subsets (nba winner/spread/total, nhl winner/reg3). A scan of every sport × market × source (42 subsets calls) returned
  only `available` or `no_stored_forecasts`: no `insufficient_evidence` and no pending later pick exists in this data, so those renderings
  were exercised with in-page mocks only.
- Browser: Chrome extension on http://127.0.0.1:5183/performance?sport=nba and ?sport=nhl. The window would not resize past the real
  viewport of 1280 CSS px (`resize_window` 1440×900 reported success; `innerWidth` stayed 1280 on a 1512-px screen), so the interactive
  flow ran at 1280 px and the 1440 px and 390 px checks used same-origin iframes (1440×900 scaled 0.85 for the screenshot, 390×844) of the
  same page driven through the same flow. That exercises CSS layout, not a phone browser.
  | Flow | Result |
  |---|---|
  | NBA winner, pool: open card → Thresholds: facts, full "Games and forecast coverage" table (3962 / 2643 saved, 0 pending, 0 excluded, missing none) + definition | Pass |
  | Per-model thresholds: "Model or combination" → Elo: table changes (50%+ 1776–867 vs 1787–856 for the 15-model average) | Pass |
  | Exact subsets: "18 of 20 rules (13 distinct later pick sets) … 3 of 20 (3 distinct later pick sets)"; note "these 20 rules make 15 distinct later pick sets — set 9: rules 9, 12, 14, 15, 16, 18 (278 picks)" + the new definition; set column "9 (shared by 6)" on rules 9, 12, 14, 15, 16, 18; footer: outcomes and win rate identical by construction, mean probability and calibration gap can differ | Pass |
  | Rule 9 detail (mlp ≥ 80% & elastic_net ≥ 80%): "(a) elastic_net:nba_base on the SAME games (a member: the same picks and outcomes)"; ledger "278 games", 50 rows, "Show more (228 left)" → 250 rows and "Show more (28 left)" | Pass |
  | NBA spread, pool → Exact subsets: "1 of 20 rules (1 distinct later pick set) … 2 of 20 (2 distinct later pick sets)"; "20 rules make 17 distinct later pick sets — set 11: rules 11, 14, 15, 20 (98 picks)" | Pass (singular) |
  | Source adaptive 00911fd3e4b0640b on spread: "No saved forecasts: no saved forecasts for this market."; source select still usable; "Back to the candidate pool" restores the pool list | Pass |
  | NBA winner, adaptive 2a0111fd73fc5cfa, rule 5 detail: "244 games excluded: a member had no saved forecast."; non-member (a) row "same side on 313 of 313" | Pass (plural; see gap 3) |
  | Mock legacy coverage (`window.fetch` patched in the page for /api/research/thresholds only; no service touched) `{games, missing}`: warn note "This response comes from an older API … Reported only: Earlier (2022, 2023, 2024): 3962 saved games; missing forecasts Elo 12, Logistic 3. Later (2025, 2026): 2643 saved games; missing forecasts none. Restart the API on the current code for the full table."; no table, no denominator | Pass |
  | Mock coverage absent: heading and table absent, rest intact. Mock `{earlier:{games,settled}, later:{}}`: "… 3962 saved games; missing forecasts not reported. Later …: saved games not reported; missing forecasts not reported." | Pass |
  | Mock status `insufficient_evidence` + reason/excluded/split: "Insufficient evidence: only 1 eligible model; a subset needs at least 2. Earlier seasons 2022, 2023, 2024; later seasons 2025, 2026. Not compared: Elo (mock: fewer than 100 settled games)."; source select and view buttons usable | Pass |
  | Mock HTTP 500: role=alert "Couldn't load data. 500: mock failure" + "Try again"; mock removed → Try again → full view and coverage table | Pass |
  | Mock 3 s delay: "Loading…" then the view | Pass |
  | Mock subsets without `later_overlap`/`later_pick_set`: "for 18 of 20 rules in the first list and 3 of 20 in the second." (no set counts), no overlap note, "—" in the set column | Pass |
  | Model combinations card (NBA winner): scope sentence + full coverage table | Pass |
  | NHL winner, pool: Excluded / Not searched list the 4 goalie models with the postgame-input reason; coverage 9078 / 2792; Exact subsets "0 of 20 rules (0 distinct later pick sets)" in both lists; "20 rules make 17 … set 7: rules 7, 8, 12 (57 picks); set 11: rules 13, 16 (80 picks)" | Pass |
  | NHL reg3: "5 of 20 rules (3 distinct later pick sets) … 6 of 20 (3 distinct later pick sets)"; rule 2 "(shared by 3)" detail "(a) multinomial_l2:nhl_base … (a member: the same picks and outcomes)", ledger 48 games / 48 rows | Pass |
  | "undefined" / "NaN" in the card text after every step above | None |
  | `scrollWidth` = viewport at 1280 (1280), in the 1440 iframe (1436 = width − borders) and the 390 iframe (386) through thresholds, subsets and rule detail; no element outside `.table-wrap` past the viewport; wide tables scroll inside `.table-wrap` (390: 492 px in a 312 px wrap) | Pass |
  | Console errors (tracking on from the NHL reload; the NBA flows before that are not covered) | None |
- Automated (actual): `cd backend && NBA_DATA_DIR=<empty scratch> OMP_NUM_THREADS=1 .venv/bin/python -m pytest -q -p no:cacheprovider
  tests/test_combination_thresholds.py tests/test_combination_stored.py tests/test_api.py` → 63 passed, 1 warning; scratch dir stayed empty.
  `npm --prefix frontend run typecheck` → exit 0. Not run: build, full suite, pyflakes, check_project.
- Gaps / observations (nothing fixed; none blocks):
  1. The extension's network list shows a 503 entry before each 200 for the same research URL. The API log has 0 lines with 503, the
     Vite log 0, direct page fetches return 200 (4–388 ms) and no error state was ever displayed; cause not established (most likely a
     capture artifact). Outside the four changes.
  2. Changing the market select closes the open thresholds and combinations cards (the section remounts). Pre-existing.
  3. The rule detail's "N games excluded: a member had no saved forecast." is the rule's count over both periods (`(~elig).sum()` in
     `combination_thresholds.py`) under a "· later period" heading; for adaptive 2a0111 rule 5 all 244 are earlier-period games (later:
     0 excluded, eligible 2643). Pre-existing wording; the cleanup only pluralised it.
  4. `Research.tsx` line 183 keeps an inline `d.games === 1 ? 'game' : 'games'` instead of `count()`. Cosmetic.
  5. Not verified: a real phone browser; 1440 px as a real window; a real older server (legacy shapes were mocked in the page).

## exact-rules-v1: registered exact rules tracked prospectively — 2026-10-11 ~05:50 UTC (Claude, Fable 5.1; writer)
- Services untouched: API `:8011` (pid 10146, started 23:41 local, before these edits) and Vite `:5183` were not restarted,
  so the real API does not serve the new `exact_rules` field yet; no store was migrated or registered against (the registry
  file `backend/app/models/exact_rules.json` does not exist; nothing registered on the user's behalf).
- Automated (actual, `NBA_DATA_DIR=<empty scratch>`, dir stayed empty): `tests/test_exact_rules.py` + `tests/test_agreement_tracking.py`
  → 45 passed; regression `tests/test_research_live.py test_api.py test_combination_thresholds.py test_combination_stored.py
  test_selection.py test_research_contract.py test_rule_tracking.py test_pool_selection.py test_joint_agreement.py
  test_audit_pool_tracking.py test_selection_status.py test_release_guard.py test_dev_script.py` → 256 passed;
  `npm --prefix frontend run typecheck` exit 0; `npm --prefix frontend run build` built (402.47 kB JS);
  `python3 scripts/check_project.py` passed; `git diff --check` clean (new files: 0 whitespace problems).
  CLI without a store: `exact-rules-register … --dry-run` prints the validated rule and writes nothing; an adaptive member is
  refused (`exit 1`, reason printed); `exact-rules-list` reports no registry. Migration 0028 was executed only on the
  disposable synthetic test store (test_migration_0028_…).
- Browser (Chrome, http://127.0.0.1:5183/performance?sport=nhl, "Show research details" on): the running API predates the
  change, so the new section was checked with an IN-PAGE `fetch` mock of `/api/research/tracking` (the real payload plus a
  synthetic `exact_rules`); no service was touched. Real viewport 1440×682 this time (`innerWidth` 1440) and a 390×844
  same-origin iframe.
  | Check | Result |
  |---|---|
  | "Registered exact rules · Prospective only" section under the tracking table: rule "Elo ≥ 65% & Goals model ≥ 60%", "core cohort · registry v1 · explorer reliable list, rank 3", reasons "Goals model 20×, Elo 8×", tracked since + first game, eligible 40 with "20 below a member threshold · 8 members disagree", 12 (30%), pending 2, 7–3, 70.0% 39%–89%, mean min. confidence 66.0%, anchor 60.0% (10), "insufficient evidence" | Pass |
  | Historical evidence: only the not-applicable note; no rule rows | Pass |
  | No registered rule: note + "No exact rule is registered for this market." | Pass |
  | No "undefined"/"NaN"; no element outside `.table-wrap` past the viewport at 1440 (table 1070/1070) and 390 (table scrolls: 617 px in a 312 px wrap, page 386 = width − borders) | Pass |
  | Console errors during the mocked renders (tracking on from the first read; the initial load before it is not covered) | None |
- Not verified: the real API serving `exact_rules` (needs an API restart on this code, not authorised here); a registered real
  rule end-to-end in a live two-hour cycle (none registered by design); a real phone browser.

# exact-rules-v1 synthetic end-to-end (2026-10-11, Claude Opus 5.5, verification only; no code changed)
## Isolation
- `exact_rules.REGISTRY_PATH` is fixed to `backend/app/models/exact_rules.json` (no override), so the run used a **disposable checkout**: `rsync` of this working tree (tracked + untracked, excluding `.git`, `data/`, `node_modules`, `.venv`, `dist`) to `<scratchpad>/e2e/checkout`, `node_modules` APFS-cloned into it. Data: `NBA_DATA_DIR=NBA_LIVE_DATA_DIR=<scratchpad>/e2e/data` (also `data-void`, `data-pre28`). `PYTHONDONTWRITEBYTECODE=1`.
- Verified from the copy's cwd: `app.__file__`, `exact_rules.REGISTRY_PATH`, `agreement_tracking.REGISTRY_PATH`, `db.MIGRATIONS_DIR`, `config.REPO_ROOT`, `data_dir()`, `live_data_dir()` all inside the scratchpad.
- Pitfall found and fixed in the harness: a script run from outside the copy imports `app` through the venv's editable install (= the shared checkout's code). The first stage-3 call did so (read-only: registry absent there, no write; code identical to the copy apart from `.omc` folders); the harness then put the copy first on `sys.path` and asserts `app.__file__` is inside it.
- In the copy only: `backend/app/models/nhl_frozen_decision.json` replaced by the synthetic store's sealed decision (from the test fixture), otherwise every NHL request is 503 "Sealed model decision unusable".
- Shared checkout afterwards: no file outside `data/` changed; `backend/app/models` has no `exact_rules*`. In `data/`, `nba.sqlite`, `injuries-launchd.log`, `odds-launchd.log` changed during the run: the scheduled release jobs (`com.nba-predictor.injuries` runs from `~/nba-predictor-live` with `NBA_DATA_DIR=<this repo>/data`; log entry at the same second). The installed `com.nba-predictor.api/web` services were not touched.
## Data and clock
- Stores: `tests/test_e2e_build.py` (copy only) = the existing fixtures `nhl_store` → `sealed` → two upcoming games (`nhl:2026020001` BOS–CHI, `nhl:2026020002`) through the real NHL ingest, an integer total line (o6/u6), then the real two-hour research sequence (`near_tipoff.predict_now` → `market_live.forecast` → `agreement.predict` → `research_live.record`) at fixture time 2026-10-12T21:30Z; copied out by SQLite backup. `data-pre28` built the same way with `db.migrate(upto=27)`.
- Registration: real CLI, real clock: `python -m app.cli --sport nhl exact-rules-register --market winner --rule hockey_elo@0.8,goals_poisson@0.75 --note …` and `--market total --rule goals_poisson@0.5,goals_glm@0.5` → versions 1, 2, `effective_from` 2026-10-11T05:33:01Z (synthetic test rules, copy registry only).
- Harness clock (only in `<scratchpad>/e2e/stage.py`): recording via `jobs.agreement_tracking(conn, game_ids, now=2026-10-12T21:40:00Z)` (before the 23:00Z start; after registration); results via the real NHL schedule ingest (`add_upcoming`, final 4–2, `retrieved_at` 2026-10-13T02:00Z); voids by re-ingesting a moved start. Nothing backdated, nothing after the start.
- Servers: `uvicorn app.main:app --host 127.0.0.1 --port 8765 --app-dir <copy>/backend` (`NBA_API_MODE=real`), `vite --host 127.0.0.1 --port 5875 --strictPort` with `API_PROXY_TARGET=http://127.0.0.1:8765`; ports checked free first; both stopped at the end (PIDs from their own pid files).
## Results (real API via the vite proxy, real UI in Chrome; no fetch mocks)
| # | Scenario | API | UI (desktop 1440 px) |
|---|---|---|---|
| 1 | No registry | `exact_rules.status no_registered_rules`, 83 sealed groups | "No exact rule is registered for this market." |
| 2 | Registered, no observations | `no_observations_yet`, eligible 0 | row "Tracked since Oct 11, 1:33 AM EDT · no game yet" |
| 3 | Recording | `out.exact`: rules 2, created 4 (winner g1 pick, g2 `below_threshold`; total g1 pick, g2 `disagree`), 0 errors | pending: winner 1 pick (50%), pending 1, 0–0 |
| 4 | Identical retry | created 0, already_recorded 4; `agreement_observations` 498 rows before and after | unchanged |
| 5 | Settled / push / void | winner 1–0, win rate 1.0, mean min. confidence 0.879, Brier null; total 1 push; void store (game 1 moved): both rules 1 void, 0 pending | "1–0 · 100.0% (21%–100%) · 87.9%"; total "0–0–1P"; void store "0–0 · 1 void" |
| 6 | Broken (`{not json`) and tampered (threshold edited, seal not updated) registry | tracking 200, 83 sealed groups, `failed` + error (JSON / seal); game endpoints 200, 247 sealed groups (249 − 2 exact), `exact_rules_error`; CLI `exact-rules-list` → JSON `failed`, exit 1, file unchanged | Performance: warn "Exact-rule tracking unavailable…"; Game (populated, game 1): `role=alert` warn above the sealed markets (winner 83 / total 29 groups); Game (no observations, `nhl:2025020036`): warn + "No tracking observations…" |
| 7 | Pre-0028 store | cycle: exact `skipped` "migration 0028 not applied", 494 sealed rows; API `schema_required` + registered ids; schema 27 before and after | warn "Not recorded on this store: … Registered for this market: …"; no "no game yet" |
| 8 | Historical evidence | `exact_rules.status not_applicable`, rules [] (store has no historical table: `no_historical_table`) | note "Registered exact rules have no historical record here…", no rule rows |
- Payload agreement: every UI number above was compared with the same request's JSON. Game card counts equal the endpoint (winner 84 = 83 + 1 exact).
- Navigation: sidebar links clicked (history → game day → performance), routes and sections render. Console: no errors on the checked pages. API log: 5xx only from the startup poll before the synthetic decision was installed and from NBA requests (no NBA store in the scratch folder).
- Phone: **390 × 844 same-origin iframes**, not a real viewport (the window resize left `innerWidth` at 1440). Performance and game page, normal and broken-registry states: document width 388 = viewport, no element outside a `.table-wrap` overflows; tables scroll inside their wrappers; warnings wrap.
## Observations (not fixed; verification only)
- Game card lists the top 12 picks by statistic (`game_observations`, `picks[:12]`): the winner exact pick (0.879) is counted ("29 home") but not named among 29 sealed picks. Presentation only.
- Pre-0028: the game card shows sealed groups with no schema note (does not claim exact tracking).
- `schema_required` text runs the reason and "Registered for this market" together without a full stop.
- The "Historical (exploratory)" button did not toggle on two extension clicks (ref); DOM `click()` did; sidebar ref clicks worked. Not investigated: worth one manual click.
## Not verified
- A real 390 px browser viewport; the NBA sport (no NBA synthetic store); the live/release environment; candidate members (the rules used cohort members only).

## Codex daily-learning preview — 2026-10-11; superseded by subsequent Fable edits
- Startup: `NBA_DATA_DIR=/tmp/nba-daily-learning-preview-20261011 NBA_API_MODE=real backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8021`; `API_PROXY_TARGET=http://127.0.0.1:8021 npm --prefix frontend run dev -- --host 127.0.0.1 --port 5181 --strictPort`. Source: read-only SQLite backups of existing ~/nba-predictor-dev-data snapshots; original SNAPSHOT_AT 2026-10-10T20:07:37Z preserved. Live-backup attempts failed with `sqlite3.OperationalError: unable to open database file` twice; no live-store writes. Port 8011 occupied; untouched, preview used 8021.
- URL http://127.0.0.1:5181/daily-learning: observed NBA 5/NHL 6 markets, supported threshold ranges, fit/history/recording counts separately. All real-data daily results awaiting training. Expanded NHL regulation market, inspected exclusions, submitted exact-rule evidence request: visible `422: No daily fit for this definition` with retry; no fabricated fallback. Navigation between Game day and Daily learning worked. Loading/empty states observed.
- Browser at 1440x900 and 390x844; phone document.scrollWidth = innerWidth = 390. Screenshots visually inspected. Viewport reset. Populated production-training UI unverified because real-data training intentionally belongs to Claude. Synthetic API tests cover populated histories and evidence responses for the same implementation; this is not production acceptance.
- Latest scoped run at that point: 9 daily tests passed in 13.98s (synthetic NBA daily train/record/settle/reassess; six-market NHL recording, duplicate retry, new frozen-rule version, settlement, API and drill-down). Build/typecheck, structure and diff whitespace passed. Earlier full --ready: 878 passed in 651.12s. These checks do not validate subsequent Fable code changes. User reports Fable later stopped preview API 8021.


## Codex repair continuation — 2026-10-12; independent verification assigned to Claude
- Focused command: `backend/.venv/bin/python -m pytest -q backend/tests/test_nhl_player_ratings.py backend/tests/test_adaptive.py -k 'nhl or score_member_without_distribution'` → 12 passed, 27 deselected. Bulk-collector regression first failed (`stored == 0` instead of 1); after repair the valid second game is saved and the incomplete first game is reported.
- `python3 scripts/check_project.py` and `git diff --check` passed.
- `NBA_DATA_DIR=/tmp/codex-injury-readiness OMP_NUM_THREADS=2 LOKY_MAX_CPU_COUNT=2 /Users/andry/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready`: structure, frontend typecheck and build passed; backend suite interrupted (exit 130) after user assigned independent verification to Claude. Not a readiness pass.
- No browser/API flows reverified in this continuation; desktop and phone checks remain Claude's gate. Earlier temporary logs/download cache are unavailable, so collection completion and source coverage remain unverified. No real-data training launched.


## Additional source-integrity repairs — 2026-10-12
- Same focused command above now returns 14 passed, 27 deselected (0.13s). Two new regressions demonstrated that changed derived injury weights were accepted despite an unchanged raw report, and rejected page bodies were discarded; both pass after fixing those paths.
- Structure guard and diff whitespace passed. No new API/browser checks or training; this is targeted implementation verification only. Claude owns the independent full gate.

- Final raw-box integrity repair: same focused command → 16 passed, 27 deselected (0.14s); original and bulk source-corruption regressions both failed before and passed after. Structure and whitespace checks passed. Browser/full readiness remain unverified for this final code.

## Unresolved NBA injuries and incomplete team boxes — 2026-10-12
- Three failing-first regressions: unmatched NBA absent player falsely counted as a complete report; NBA and NHL cached game IDs falsely passed freshness without the team's rows.
- `backend/.venv/bin/python -m pytest -q backend/tests/test_player_ratings.py backend/tests/test_nhl_player_ratings.py` → 47 passed in 0.45s. Structure guard and diff whitespace passed. No new browser/full-suite verification or real-data training.
- Adaptive integration command: `backend/.venv/bin/python -m pytest -q backend/tests/test_adaptive.py -k 'nba_player_injury_member or score_member_without_distribution'` → 4 passed, 24 deselected in 4.79s; one Starlette/httpx deprecation warning. Synthetic fixture fits only.


## Further targeted checks — 2026-10-12
- NHL delayed-result regressions failed before: old late box changed injury minutes from 20/60 to 28/60 and revived a player outside the five most recent games. Both pass with game-time-ordered delivered histories.
- Six invalid probability vectors (nonfinite, negative, non-unit-sum) were accepted before; now rejected. Valid rounding regression passes.
- NBA player minutes NaN, infinity and negative value each reproduced acceptance before; shared parser now raises an explicit error.
- `backend/.venv/bin/python -m pytest -q backend/tests/test_adaptive.py -k 'probability_vector or nba_player_injury_member or score_member_without_distribution or train_then_record_end_to_end'` → 12 passed, 23 deselected in 11.28s (core-count fallback and Starlette deprecation warnings). Synthetic fixtures only, before final minutes guard.
- After minutes guard: `backend/.venv/bin/python -m pytest -q backend/tests/test_availability.py backend/tests/test_player_ratings.py backend/tests/test_nhl_player_ratings.py` → 64 passed in 0.48s. Structure and whitespace checks passed. Full independent/browser verification remains Claude's task.


## Failure handling and source validation — 2026-10-12
- Reproduced a score model retaining earlier market votes after a later market raised; walk and candidate paths now remove partial outputs. Synthetic integration tests cover both.
- NBA duplicate player/game rows previously entered ratings twice; importer now raises. NHL importers now share completeness/global uniqueness checks; malformed `19:99` minutes rejected. Regressions failed before fixes.
- Shared feature check formerly skipped adaptive-suffixed walk members without candidates; when feature calculation failed, dependent walk members did not receive the failure reason. Checks now normalize member names and propagate unusable state; independent recent-result indexing survives feature failure. Integration tests cover with/without candidates.
- `backend/.venv/bin/python -m pytest -q backend/tests/test_nhl_player_ratings.py backend/tests/test_player_ratings.py backend/tests/test_availability.py` → 68 passed in 0.47s.
- Final `LOKY_MAX_CPU_COUNT=2 backend/.venv/bin/python -m pytest -q backend/tests/test_adaptive.py -k 'feature_walk_failure_blocks or failed_score_member or nba_player_injury_member or train_then_record_end_to_end'` → 8 passed, 31 deselected in 27.52s; Starlette/httpx deprecation warning. Synthetic fits only. Earlier broader vector/integration selection (before last feature-walk change) passed 13 tests.
- Structure guard and diff whitespace passed. Full independent readiness, browser, real-data coverage/parity and predictive evaluation remain Claude's gate; no claim of complete correctness.

## Source parity and threshold review — 2026-10-12
- Focused NHL/threshold/daily suites: 100 passed (15.39s); added rule-application checks: 2 passed, 17 deselected (0.15s). Structure and whitespace passed. Commands, real NBA read-only sample, and limitations: `docs/evidence/nhl-injury-sources/cache-review-2026-10-12.md`. No fresh browser/full readiness pass or real-data training.

## NHL team/side identity — 2026-10-12
- Wrong-team box and reordered explicit-side cache tests failed before fixes. NHL suite passed 25 tests in 0.15s. Added swapped-side rejection: `backend/.venv/bin/python -m pytest -q backend/tests/test_nhl_player_ratings.py -k 'wrong_teams or explicit_sides'` → 3 passed, 23 deselected in 0.14s. Structure/whitespace passed. No browser/full-suite or real-data training. Model state rejects mismatched delivered team IDs/sides; new bulk caches use explicit side IDs, old bulk caches keep ordered-key compatibility.

## Systematic bug-hunt verification — 2026-10-12 (Claude, Opus 5.5)
Scope: the audit fixes listed in HANDOFF.md (top entry). No training, deployment, scheduler or live-store change.
- Data: live `data/nba.sqlite`/`nhl.sqlite` copied with `cp` (live files unchanged) into the session scratchpad; the copy was
  migrated (0027, 0028) with `NBA_DATA_DIR=<scratch>/app .venv/bin/python -m app.cli --sport {nba,nhl} migrate`.
  `data/research`, `data/eval`, `data/reports` copied alongside. The release app (8100/4173) was not touched.
- Servers: `cd backend && NBA_DATA_DIR=<scratch>/app NBA_LIVE_DATA_DIR=<scratch>/app .venv/bin/python -m uvicorn app.main:app
  --host 127.0.0.1 --port 8000`; `cd frontend && npx vite --host 127.0.0.1 --port 5173 --strictPort`. Health: API 200, web 200.
- Browser (Claude in Chrome): the window could not be resized (outerWidth stayed 511 px), so pages were also loaded in
  same-origin iframes of 1440×900 and 390×844. `/`, `/daily-learning`, `/history?sport=nhl`, `/performance?sport=nba`,
  `/performance?sport=nhl&market=total&details=1`: API-backed content at 511, 1440 and 390 px, 4 nav links, no `role=alert`,
  no horizontal page overflow, no element outside a table wrapper wider than the viewport. Pass.
- Reproduced before / verified after (Agreement tracking filters): NHL spread, tracking "Exact line" −1.5, switch market to
  total. Before: API log `GET /api/research/tracking?sport=nhl&market=total&evidence=prospective&line=-1.5`, select showing
  "every line" with no line options. After: `…market=total&evidence=prospective` (no line), options 5.5/6.5. Pass.
- Daily learning: both sports show "No daily fit published for this code definition" — expected, the audit changed hashed
  definition files. The new live-record line (95% interval, unscorable, outcome words) is therefore only typechecked/built,
  not seen with real rows.
- Not reproducible here: threshold-explorer adaptive source after a sport switch (no exported adaptive history in the copy:
  sources = `pool` only for both sports); `URLSearchParams.size` (Chrome supports it). Both code-confirmed only.
- Not run: loading/error states with the backend stopped (unchanged code paths this round), real-device phone.
- Automated: see HANDOFF.md top entry for the exact `check_project.py --ready` result.

## Pasted adaptive review follow-up — 2026-10-13
- Inspected current fixes and original scratch repros. No code changes: existing correction/late-result serving guard and pending-feature fix already present.
- `LOKY_MAX_CPU_COUNT=2 backend/.venv/bin/python -m pytest -q backend/tests/test_adaptive.py -k 'training_time_forecast or results_that_changed'` → 3 passed, 39 deselected in 13.98s, same process. Synthetic fixtures only. Original scratch-test KeyError remains undiagnosed; scratch correction tests bypass the serving guard. Full readiness/browser gate not repeated.

## Six-item adaptive follow-up audit — 2026-10-13 (Claude, Opus 5.5)
Synthetic fixtures only (plus a read-only `cp` copy of the players-v1 study cache for item 5); no live `data/` access,
no training. `S` = the session scratchpad; `NBA_DATA_DIR=$S/emptydata` (or `$S/readydata*`), `OMP_NUM_THREADS=4`,
`LOKY_MAX_CPU_COUNT=2`, cwd `backend/` unless noted.
- Fail-before (each new test on the unfixed code, then pass after the fix):
  - `pytest tests/test_adaptive.py -k one_markets` → 2 failed (`'failed' == 'partial'`: the combiner fit and the forecast
    cycle both failed for every market on one market's injected exception) → 2 passed.
  - `-k committed_after_the_training_snapshot` → failed: `hockey_elo` missing "results changed since the fit … cutoff
    2026-10-12T20:00:04Z", winner members `[]`; re-confirmed failing with the fix line reverted → passed with it.
  - `-k backfilled_shot` (real `nhl_shots.import_game`, policy historical_backfill, dated before the fit) → failed (no
    reason recorded for `hockey_logistic_xg@adaptive`) → passed.
  - `tests/test_player_ratings.py -k late_result_of_an_earlier_season` → `assert 2024 == 2025` → passed.
  - `tests/test_player_ratings.py -k research_report_names` → `{'h1': 1.0, 'n1': 1.0} == {'h1': 1.0}` (name resolved
    from a March box row for a January report) → passed.
  - `tests/test_goal_model_fixes.py -k draw_inflated` → GoalsDraw returned a grid at λ=40 → passed.
- Mutation: the two existing `results_that_changed` tests FAIL with `adaptive._results_changed` patched to `None`
  (`$S/mut/test_mut_guard.py`) → the digest guard is load-bearing.
- Scratch repros `/private/tmp/claude-501/adrev/test_repro_correction.py` → 2 failed (8.597 / 9.276): they call `_advance`
  directly, below the serving guard; expected, not a regression. No KeyError seen.
- Together: `pytest tests/test_adaptive.py tests/test_adaptive_recovery.py` → 66 passed (211.7 s). Broader targeted set
  (13 files) → 253 passed / 1 failed — the failure was my mid-run edit of hashed `availability.py`; `test_adaptive_recovery.py`
  alone → 20 passed; player/availability suites → 88 passed; `test_goal_model_fixes.py test_pool_selection.py` → 24 passed.
- Full gate (repo root) `NBA_DATA_DIR=$S/readydata OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python
  scripts/check_project.py --ready` → 1001 passed, 1 failed (`test_committed_registry_is_sealed…`: my first goals guard sat in
  hashed `engine.py`; moved out, engine.py restored). Final rerun (`$S/readydata2`): structure guard passed, frontend typecheck + build OK, **1002 passed, 0 failed** (630 s), exit 0, scratch data dir empty afterwards (run 22:27:12–22:37:51 UTC; only docs edited during it).
- Browser: not rerun — no API/UI code changed in this increment.

## Every-game coverage + review fixes — 2026-10-13 (Claude, Opus 5.5)
Synthetic fixtures for tests; a read-only copy of the live stores (19:00Z) for the Oct 6–12 audit and the browser check
(`$S` = session scratchpad; the copy migrated to 0029 in `$S/uidata`, raw cache linked read-only). No training, no
deployment, no live-store write.
- Fail-before (each regression on the unfixed code): preseason/coverage ledger tests (new functionality, fail by
  construction); start-time edit after the final → `tipoff_at` became 23:07Z; R1/R1b → `due False` (mutation: stubbing
  `inputs_changed_since_fit` to [] fails both); exact rules → '2026-10-1T00:00:00Z' registered; 0028 detection broke once
  0029 existed (2 existing tests failed → presence check); two models → 422 `min() iterable argument is empty`; recovery
  definition (reviewer repro); player box republication → `inputs changed since the fit`; C1 (reviewer repro: all six
  rows `primary_recorded`); C2 (reviewer repro passed = defect, now fails); C3 (reviewer repro: due False after the
  player-box block → now True; regression folded into the B1/B2 test: block → due → retrain → forecasts → growth never
  blocks); NHL 5OT box → `invalid bulk time on ice`;
  daily rule / report / invalid vector by injection.
- Reviewer repros on the fixed tree: review-repros 6 failed / 6 passed (defect-asserting ones fail; test_b_serving fails
  by an accepted semantic difference); recheck: c1 passes, c2 fails (defect gone).
- Targeted (cwd backend, empty NBA_DATA_DIR): `tests/test_coverage.py` 18 passed; `test_daily_agreement.py
  test_player_ratings.py test_shadow.py` 94 passed; `test_adaptive.py test_adaptive_recovery.py` 66 passed (earlier in
  the session); exact rules + agreement tracking 64; research suites 96; storage + live 47.
- Full backend venv (before the C1/C2 round): 1027 passed / 10 failed — 8 `test_deep_study` (torch/keras not in
  backend/.venv; the deep venv gate runs them) + 2 fixed since (`test_shadow` step list, B1 player-box test).
- Oct 6–12 report (`coverage.games_report` on the copy): NHL 46 games, source 46, 0 missing from store, all covered,
  record 155–121 (UTC window) / 154–122 (ET); NBA 34 (UTC) preseason games, 0 forecast, 170 game-markets missing
  ("no pregame forecast in any ledger").
- Browser (Chrome, real API via vite proxy, uvicorn `NBA_DATA_DIR=$S/uidata NBA_API_MODE=real … --port 8010`,
  `API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev -- --port 5180`): Performance → Every-game coverage card, NHL and
  NBA, 1440×900 and 390-wide window (renders at the 500 px minimum): schedule vs source, market line, unfiltered record,
  threshold view, sources, missing reasons, games/ledgers details. Found: not-started games listed as missing → fixed
  (`upcoming`), re-verified ("4 not started yet", 0 missing). Not checked: date-filter interaction by typing (API
  date range covered by the API test), real phone device.
- Final readiness gate (deep venv, repo root, `NBA_DATA_DIR=$S/readydata4`): structure guard passed, frontend typecheck + build OK, **1042 passed, 0 failed** (801 s), exit 0; run 2026-10-14T00:04:41Z–00:18:11Z on the reviewed code (last code edit 00:04:15Z; no non-doc file changed during the run), scratch data dir empty afterwards.

## Canonical NBA winner forecast connected to Today, Game, History, Performance — 2026-10-14 (Claude, Opus 5.5)
`$S` = session scratchpad. No live-store write, no job/launchd/release change, no training.
- Fail-before: `tests/test_canonical.py` (7 tests) on the unfixed code → 7 failed (no canonical policy → 422; no
  `canonical` field; A → B → A recorded `['primary_recorded','primary_direct']`, the third decision returned the first
  row). Re-run of the final file on a scratch copy with the fix reversed → 7 failed, existing coverage tests 18 passed.
- After: `cd backend && NBA_DATA_DIR=$S/empty-data .venv/bin/python -m pytest -q tests/test_canonical.py tests/test_coverage.py
  tests/test_live.py tests/test_near_tipoff.py tests/test_api.py tests/test_api_markets.py tests/test_multisport.py
  tests/test_record_counts.py tests/test_research_contract.py tests/test_api_research.py tests/test_live_markets.py
  tests/test_storage.py` → 151 passed. Gate: `NBA_DATA_DIR=$S/gate-data OMP_NUM_THREADS=4
  ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready` (01:16:29–01:29:17Z) → structure guard
  passed, typecheck + build OK, **1049 passed**, exit 0; scratch dir empty afterwards.
- API, SYNTHETIC FIXTURE (`$S/fixture/build_and_serve.py`, fixed clock 2026-10-23T03:00Z): g1 record #1 identical on
  `/api/games?policy=canonical`, `/api/games/espn:g1`, `/api/canonical/forecasts` (grade loss); g2 missing "no forecast
  cycle ran…" identical on all three; preseason `unvalidated` identical, no probability; performance 2 games / 1 forecast /
  1 missing / coverage 0.5 / 0–1, market matched 1; preseason 1 game, 1 experimental row; legacy near-tipoff sample 1.
- Browser (Chrome, API :8012 + Vite :5182 fixture; API :8011 + Vite :5181 isolated real copy): 1440 px Today card
  (record #1, pick, Wrong, "Fallback: sealed baseline model"), Game page provenance, History rows, Performance canonical
  report; 500 px (window minimum, not 390) Game page and History stacked rows; real copy Oct 13 NBA preseason cards
  "No validated forecast" with the reason; NHL Today unchanged (two-button toggle, Within 2 hours default); no console
  errors. Gap: the fixture server shows the REAL DATA banner (mode real); phone width below 500 px not tested.
- Real isolated check: copy `~/nba-predictor-research-data/canonical-check-20261014/` (guarded `cp` at 01:12:23Z, no WAL,
  sha match), dev cycle steps refresh/odds skipped by quota (0 network requests; data from the live 01:08Z refresh),
  `coverage.record` wrote experimental rows for the 2 real preseason games. Real regular-season pregame→final check
  PENDING (first NBA regular game 2026-10-20T19:00Z).

## Independent review of the canonical NBA winner slice — 2026-10-14 (Claude, Opus 5.5, separate session)
`$S` = this session's scratchpad `rv/`. Review only: no implementation edit, no live write, no job/release change.
- Focused tests: `cd backend && NBA_DATA_DIR=$S/empty-data .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_canonical.py tests/test_coverage.py tests/test_live.py tests/test_near_tipoff.py tests/test_api.py tests/test_api_markets.py tests/test_multisport.py tests/test_record_counts.py tests/test_research_contract.py tests/test_api_research.py tests/test_live_markets.py tests/test_storage.py` → 151 passed (01:36:53–01:38:00Z).
- Mutation (scratch copy of backend/, repo untouched): revision identity removed from `_insert` → only the A → B → A test fails; selection "latest row wins" → only the later-failure test fails; unmutated 7 passed.
- Gate: `NBA_DATA_DIR=$S/gate-data OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready` (01:45:41–01:58:17Z) → structure guard passed, typecheck + build OK, 1049 passed, exit 0, scratch dir empty.
- SYNTHETIC FIXTURE (`$S/fixture.py`, own script, repo test helpers, real `near_tipoff.predict_now` + `coverage.record`; API :8031 pinned to as_of 2026-10-22T03:50Z, this tree's app/main.py; Vite :5191 from this tree): g1 #30 (later missing #44), g3 A→B→A #35, g5 pending #49, g2 no-cycle gap, g6 missing (unfinished earlier game), preseason `pre`. `/api/games?policy=canonical`, `/api/games/{id}`, `/api/canonical/forecasts` identical for all 6; performance 5 games / 3 forecast / 2 missing / 1–1 / market matched 2; preseason 1 game, 1 experimental row; legacy near_tipoff 2, pre-game archive 3. `/api/coverage` on the same store: g1 winner `missing`, `pre` `win` 0.771 (contradiction, see HANDOFF). Fixture artifacts: REAL DATA banner, NHL card served from the NBA store.
- Browser (Chrome): 1440 px Today (default 70% → "0 of 6 selected", no cards; conf=all → 6 cards matching the API, later failure shown), Game page provenance (#30, made 22:40Z, version, Wrong), History rows, canonical Performance, legacy Performance Coverage card (g1 "Missing", pre "Won · 77.1%"); 500 px (window minimum; 390 px not reachable) History stacked rows and preseason Game page. No console errors.
- Isolated real copy (`$S/realcopy`, `cp -p` of data/{nba,nhl}.sqlite at 01:43Z, no WAL, sha match, migrated 26→29 on the copy; API :8032 with NBA_DATA_DIR verified in the process env; no cycle, no network): Oct 13/14 NBA, 12 preseason games all `unvalidated`; Today default shows "None of these games has a canonical forecast yet"; NHL Today two-button toggle, Within 2 hours default, legacy perf sample 61. Real regular-season pregame→final: PENDING (2026-10-20T19:00Z).

## Slice 1 follow-up (review findings 1, 3, 4) — 2026-10-14 (Claude, Opus 5.5)
`$S` = session scratchpad `rv/`. Isolated data only; no live write, no job/release change.
- Fail-before: 4 new tests in `tests/test_canonical.py` on the unfixed code → 4 failed (Coverage g1 `missing` vs canonical `loss`; preseason Coverage `win` vs `unvalidated`; `KeyError: 'upcoming'` on a not-started preseason game; no `below_threshold`). After: `cd backend && NBA_DATA_DIR=$S/empty-data .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_canonical.py tests/test_coverage.py tests/test_live.py tests/test_near_tipoff.py tests/test_api.py tests/test_api_markets.py tests/test_multisport.py tests/test_record_counts.py tests/test_research_contract.py tests/test_api_research.py tests/test_live_markets.py tests/test_storage.py` → 155 passed (two exact-shape legacy selection tests failed on an intermediate version; fixed by omitting the new keys outside the canonical policy).
- Gate: `NBA_DATA_DIR=$S/gate-data OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready` (02:12:11–02:25:10Z) → structure guard passed, typecheck + build OK, 1053 passed, exit 0, scratch dir empty.
- API, SYNTHETIC FIXTURE (same `$S/fixture.py` store, :8031): `/api/coverage` winner cells g1 `loss` #30 + later_failure #44, g3 #35, g5 #49 pending, g2/g6 missing, `pre` `unvalidated` + experimental #11 (win) — all equal to the canonical record; winner counts 3 forecast / 2 missing / 1 unvalidated; spread keeps the old rule (g1 `missing`). Slate 70%: with_forecast 3, selected 0, below_threshold 3, without_forecast {unvalidated 1, missing 2}; legacy slate JSON unchanged.
- Isolated real copy (`$S/realcopy`, :8032): `/api/coverage?sport=nba` default range (Oct 6–13) now 200 (was KeyError): 38 winner cells `unvalidated`; NHL coverage unchanged except labels. Oct 14 slate: without_forecast {unvalidated 7}.
- Browser (Chrome; Vite :5191/:5192 from this tree): 1440 px fixture Today default 70% — count line by group, "No canonical forecast reaches 70%+ (3 below it)", the 3 games without a forecast listed with reasons; fixture Coverage card (#30 Lost + later failure; preseason "No validated forecast … Experimental / unvalidated row #11"); real-copy Coverage card loads ("38 preseason without a validated model"). 390 px: real CSS width via an iframe harness (`$S/narrow/index.html`; the Chrome window minimum is 500 px) — real-copy Today Oct 14 (7 preseason cards with the reason, no overflow seen) and fixture Coverage card record table with the experimental preseason row. Not checked at 390: the Coverage games table (inside its own horizontal scroller). No console errors.

## Independent review of the Slice 1 follow-up (findings 1, 3, 4) — 2026-10-14 (Claude, Opus 5.5, separate session)
`$S` = this session's scratchpad. Review only: no implementation edit, live write, job/release change or commit.
- Diff: pre-fix baseline = the earlier review's scratch copy of backend/ (`diff -rq` vs this tree: only app/coverage.py, app/schemas.py, tests/test_canonical.py differ); frontend read directly (Coverage.tsx, TodayPage.tsx CanonicalSlate).
- Focused tests: `cd backend && NBA_DATA_DIR=$S/empty-data .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_canonical.py tests/test_coverage.py tests/test_live.py tests/test_near_tipoff.py tests/test_api.py tests/test_api_markets.py tests/test_multisport.py tests/test_record_counts.py tests/test_research_contract.py tests/test_api_research.py tests/test_live_markets.py tests/test_storage.py` → 155 passed (02:35–02:36Z). `--ready` gate NOT re-run (unchanged code since the author's run; that 1053-pass result is reported, not observed here).
- Own reproduction (`$S/repro/test_review.py`, repo fixtures, game_forecasts rows inserted directly; 7-game Oct 21 slate: forecast A→B then later missing, missing→forecast, missing-only, no rows, start moved after a forecast then a missing attempt, preseason with an experimental row, not-started preseason): current tree 4 passed; pre-fix baseline 3 failed (`KeyError: 'upcoming'`, no `below_threshold`, no `forecast_id`). After results, pre-fix Coverage showed g1 `missing` (canonical #7 win), moved game `missing` (canonical #8 void), preseason p2 `win` (canonical unvalidated); current: all 7 cells equal canonical state/grade/record id/later_failure. Slate counts at None/0.5/0.6/0.7/0.99: selected + below_threshold = with_forecast; without_forecast equals the per-state count of games without a pick (missing 2, unvalidated 2); legacy policy JSON has neither key.
- Isolated real copy (`$S/realcopy`, sqlite read-only backup of data/{nba,nhl}.sqlite at 02:38Z, quick_check ok, migrated 26→29 on the copy; current API :8041, pre-fix API :8042, no network): `/api/coverage?sport=nba&date_from=2026-10-07&date_to=2026-10-14` pre-fix 500 `KeyError: 'upcoming'`, current 200 (41 winner cells `unvalidated`). NHL `/api/coverage` default and Oct 6–12: identical between pre-fix and current except the new `forecast_id` cell field, `unvalidated: 0`, labels and notes. Copy artifact: source schedule unreadable (raw cache not copied).
- Browser (Chrome; Vite :5195 → own SYNTHETIC fixture API :8043, Vite :5196 → real copy :8041; 390 px CSS width via a same-origin iframe, real clicks/keyboard): Performance → Legacy (within 2 hours) → Coverage "Games" table, fixture (later-failure and experimental preseason rows), real NBA (To date typed to 2026-10-14: 41 preseason without a validated model, no error) and real NHL (62 rows): table 316 px wide in a 316 px scroller, page scrollWidth 390, text wraps and is readable (header 9 px, cells 11 px), date inputs 133×44 px and usable by keyboard, Games disclosure opens by click. Horizontal scrolling: not needed at 390; forced at 280 px the table scroller moved 26.5 px with no page scroll. Today 390 px fixture default 70%: "3 with a canonical forecast (1 … shown, 2 below it hidden); 4 without one (2 missing, 2 preseason…)" and all 4 listed with reasons. Not verified: real mobile touch behaviour; console errors only captured after tracking started (none seen). Aside (not this slice): at 320 px the sidebar "Prediction history" link widens the page to 367 px.

## Fail-closed adaptive selection (coverage-v2) — 2026-10-14 (Claude, Opus 5.5)
`$S` = session scratchpad. Isolated data only; no training, collection, deployment, live write, job change or commit.
- Fail-before: `cd backend && NBA_DATA_DIR=$S/empty-data .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_coverage.py` with the new/changed tests and the unfixed code → 7 failed, 16 passed (adaptive taken as source; `coverage-v1`; 0 missing rows when no baseline; no `_adaptive_rejected`; report counted 6 adaptive "main"). After the fix: 23 passed.
- Gate: `NBA_DATA_DIR=$S/gate-data OMP_NUM_THREADS=4 ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready` (03:01:44Z, 13 min) → structure guard passed, typecheck + build OK, 1058 passed, exit 0, scratch dir empty. One label string (Coverage.tsx `primary_direct`) was edited after the gate's build step: `npm --prefix frontend run build` (tsc --noEmit + vite build) → OK.
- Isolated real copy (`$S/realcopy`, read-only backup 02:38Z, API :8041, no network): `/api/coverage?sport=nhl&date_from=2026-10-06&date_to=2026-10-13` before → 240 cells `adaptive` counted as main; after → `coverage-v2`, those cells come from `primary_recorded`, main 0, fallback 372, no new missing cell.
- Browser (Chrome, 1200 px window): fixture Game page "Sealed baseline model (computed for this record)", plain badge, "adaptive ensemble not used because: …"; fixture Today canonical copy names the sealed baseline and says no adaptive ensemble is approved; real-copy NHL Coverage card "fallback order coverage-v2: Adaptive ensemble (only if approved; none is) → …", "0 adaptive, 62 sealed primary", record row "Adaptive ensemble (research, not approved) 0". No console errors after tracking started. Not checked this time: phone width.

## All-markets canonical integration (NBA + NHL) — 2026-10-14 (Claude, Opus 5.5); awaiting independent review
`$S` = session scratchpad. Isolated data only; no network, training, live write, job change or commit.
- Focused: `cd backend && NBA_DATA_DIR=$S/emptydata OMP_NUM_THREADS=4 .venv/bin/python -m pytest -q tests/test_canonical.py tests/test_coverage.py tests/test_canonical_markets.py` → 42 passed (12 + 23 + 7).
- Fail-before (tests written after the code): scratch worktree of `snapshot-2026-10-13` with the new tests copied in → 7 failed (`KeyError: 'canonical_markets'`, `KeyError: 'games'`, no `coverage.pick_side`, `grade()` has no `decision`, `KeyError: 'market_quote_id'`, reg3 history 422), 1 passed (NHL winner regression: already worked).
- Frontend: `cd frontend && npx tsc --noEmit -p . && npm run build` → OK.
- Structure guard: `backend/.venv/bin/python scripts/check_project.py` → FAIL only on an empty untracked top-level `.claude/` (created 23:25 local by tooling; not removed).
- API on two isolated stores, fixed clock (`$S/export/serve_fixed_clock.py`, which only overrides the provider's "now"): FIXTURE = synthetic NHL test store (repo fixtures `game` + `_preseason` + `_settle` 4-1 REG, its own sealed decision), clock 2026-10-13T04:00Z; COPY = real store snapshot (~03:00Z Oct 14) migrated 26→29 on the copy, `coverage.record` at simulated 22:15Z and 23:05Z Oct 14, clock 23:10Z. FIXTURE: each of 6 markets has one record id across `/api/games?policy=canonical&market=`, `/api/games/{id}` `canonical_markets` and `/api/canonical/forecasts` (#1–#6), one grade on every page, Performance on the market's grid (reg3 0.35–0.70, others 0.50–0.90), bookmaker comparison matched for winner/spread/total, unmatched with reason for reg3 ("no bookmaker quote recorded") and model-line team totals; preseason game `unvalidated` in all 6. COPY: no forecast recorded — every row `missing` with the stale-results guard reason (last refresh 03:08Z/03:14Z; the guard was not relaxed); NBA preseason `unvalidated`.
- Browser (Chrome, Vite :5181 → fixture, :5182 → copy): Today spread card record #3 "BOS -1.5 · bookmaker's line · 72.1% · Correct"; reg3 with `conf=35` then market → spread: `conf` dropped, two-way grid shown; Game page lists canonical #1–#6 with grades matching the API; History spread/team_total_away rows #3/#6; Performance reg3 threshold table 35–70%, three-outcome Brier, unmatched reason; 390 px window (Chrome minimum renders a 500 px frame): Today card and History cards stack without overflow; copy Today reg3: "2 missing (coverage gaps)" with full reasons. No console errors after tracking started. Not verified: touch devices, NBA markets in the browser (API test only).

## Stabilization batch 2 — 2026-10-14 (Claude, Opus 5.5); awaiting independent review
`$S` = session scratchpad; isolated data only; no training, live write, job change or commit. Network: free requests only (NBA injury HEAD probes, one NBA PDF, one ESPN NHL injury page, sportsdataverse release listing and two player-box files into an isolated copy).
- Fail-before → after (each new test run before its fix): result revisions 5 failed (`no such table: result_revisions`) → pass; availability timing 2 failed (backdated 03:30 vs 00:00; decision correction kept 03:00) → pass; player-box refresh 1 failed (`status failed`, 404 for 2027) → pass; review-1 findings 3 failed (`KeyError: 'record_by_market'`, threshold graded 1≠0, reg3 reason) → pass; grade history 2 failed (`KeyError: 'grade_history'`) → pass.
- Focused: `cd backend && NBA_DATA_DIR=$S/emptydata .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_result_revisions.py tests/test_canonical_markets.py` → 19 passed; `tests/test_canonical_markets.py tests/test_canonical.py tests/test_coverage.py` → 45 passed; `tests/test_player_ratings.py` → 44 passed; migration-list tests 2 passed.
- Real-copy checks: migration 0030 on a fresh copy of the store snapshot: NBA 9456 finals → 9456 backfill revisions, NHL 15838 → 15838, 0 null observed_at, integrity ok. `jobs.player_box_refresh(enabled=True)` on an isolated copy against the real source: before = failed (404 player_box_2027.csv); after = downloaded 2025 (sha256:8e34c5b1…) and 2026 (sha256:7a4868da…), digests equal the published ones, `not_published [2027]`; second run skipped with that reason.
- Browser (fixture API with a corrected final 4-1 → 1-4): Game page "Regraded after a score correction: Correct with CHI 1 – 4 BOS (seen Oct 12, 11:00 PM EDT) → Wrong with CHI 4 – 1 BOS (seen Oct 12, 11:30 PM EDT). The forecast itself never changed."; History "· regraded" and "1 forecast was regraded after a score correction". API per market: total and home team total win under both results, away team total loss → win.
- Frontend: `cd frontend && npx tsc --noEmit -p . && npm run build` → OK.
- Full suite on this version: running (deep env, `$S/full-backend3.log`, source recorded in `$S/full3-source.txt`).
- Review-2 fixes: 8 new failing-first tests (7 findings + decision correction) → pass; `tests/test_result_revisions.py tests/test_player_ratings.py tests/test_canonical_markets.py` → 72 passed. Re-review: author tests 154 passed; its own reproductions 9 passed, 1 failed by design (gap cache).
- Final full suite: `cd backend && NBA_DATA_DIR=$S/emptydata4 OMP_NUM_THREADS=4 PYTHONPATH=. ~/nba-predictor-deep/backend/.venv/bin/python -m pytest -q -p no:cacheprovider` (started 2026-10-14T04:38:58Z) → 1088 passed, 0 failed, 15 min 48 s; `git diff snapshot-2026-10-13 -- backend` + untracked backend files hashed before and after: identical. Frontend `npx tsc --noEmit -p . && npm run build` → OK. Structure guard: still FAIL only on the empty untracked `.claude/` (not removed; awaiting the user).

## Stabilization batch 3 (2026-10-14 ~13:40Z, session -ae): void rules, results chip, agreement views
- Scratch dir S = /private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/e9b30806-c9d0-4ec9-950e-cb2b38b193ae/scratchpad; real data = read-only copies `S/rev-copy/{nba,nhl}.sqlite` (migrated copy of the live stores); pre-batch tree = `S/oldtree` (cb29841 + `S/handoff-to-25/tree.patch`).
- Full suite (cwd backend): `NBA_DATA_DIR=$S/emptydata6 OMP_NUM_THREADS=4 PYTHONPATH=. ~/nba-predictor-deep/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests` → 1105 passed, 0 failed (15:55); source hash `fe8fd1ca…` before = after (`find app tests -name "*.py" -o -name "*.sql" -o -name "*.json" | sort | xargs shasum -a 256 | shasum -a 256`).
- Frontend (cwd frontend): `npx tsc --noEmit -p . && npm run build` → OK.
- Old-vs-new on the real copy (run from each tree with `PYTHONPATH=.`):
  - `S/void_report.py S/rev-copy out.json`: Coverage report identical, sha256 596e18e9….
  - `S/thr_cmp.py S/rev-copy`: `/api/thresholds` identical.
  - `S/rec_cmp.py S/rev-copy`: recovered, adaptive-recovered and thresholds identical, 8f4bb4c1….
  - `S/f5_real.py S/rev-copy`: tracking-v2 winner single Elo 37–24 over 67 games (pooled) → 20–15 over 41 (newest definition cb194620).
  - `S/joint_real.py S/rev-copy`: joint default = report default.
- Browser: `NBA_DATA_DIR=$S/browser-data FIXED_NOW=2026-10-14T23:10:00Z .venv/bin/python $S/export/serve_fixed_clock.py 8812` + Vite :5182 (proxy to 8812).
  - Pages checked: Today NHL (results chip "stale · Oct 13, 11:14 PM EDT"); /games/nhl:2026020030?details=1 (tracking "Observed only under research definition 3e2504c4…" note); /performance?sport=nhl&details=1 (definition selector + "One research definition per record").
  - Widths: 1280 and 390 px. No console errors.
- Reviews: fresh-context subagents (same model, not Codex), repros under `S/review-chip`, `S/review-audit`, `S/agree-audit`.

## Store backup/restore rehearsal (2026-10-14 ~14:07Z, session -ae; copies only, never `data/`)
- S as above; copies of the live stores taken 2026-10-13 23:20 local (`S/snap`, schema 26, release f7ee505) → `S/rehearsal2/data`.
- `NBA_DATA_DIR=$S/rehearsal2/data PYTHONPATH=. ~/nba-predictor-deep/backend/.venv/bin/python -m app.backup --dest $S/rehearsal2/backups` → both stores integrity ok, schema 26; backup folder holds only `manifest.json` + the two stores.
- `… -m app.cli --sport nba migrate` and `--sport nhl migrate` (candidate code) → 0027–0030 applied, < 1 s per sport.
  - First rehearsal (`S/rehearsal`): integrity ok, `foreign_key_check` empty, `result_revisions` 9456 = NBA finals, 15838 = NHL finals, `game_forecasts` 0.
- `… -m app.backup --restore $S/rehearsal2/backups/<stamp> --sport nba --sport nhl` → schema 26, integrity ok, identity nba/nhl, sha256 equals the manifest; replaced files kept as `*.replaced-<UTC>`.
  - First rehearsal: restored content equals the original, table-by-table digest over all 33 tables plus the schema (NBA e9efab0c…, NHL 3fd827e2…). Not byte-identical: the backup API rewrites pages.
- Not rehearsed: the release checkout's `release-check`, launchd stop/start, a real backup of `data/` (needs approval at release time).

## Final combined candidate (2026-10-14 ~16:05Z, session -ae)
- Candidate = working tree on `snapshot-2026-10-13` + uncommitted batches 1–3, player/injury fixes, patches C+D from session -25.
  - Hashes: backend (app+tests .py/.sql/.json) `9091e03aefdffb4d…`; frontend/src `39c77d5f303d0f23…`.
- Readiness gate: `NBA_DATA_DIR=$S/emptydata9 OMP_NUM_THREADS=4 PYTHONPATH=backend ~/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready` → guard OK, typecheck OK, build OK, 1251 passed / 0 failed in 19:08; hashes unchanged after.
- Lifecycle: `NBA_DATA_DIR=$S/lifecycle/data … python $S/lifecycle/lifecycle.py <sport> <start> <end> <cycle now> <read now>` on copies of `S/snap` migrated 26→30.
  - nhl `2026-10-13T22:00:00Z 2026-10-13T23:31:00Z 2026-10-13T21:40:00Z 2026-10-14T06:00:00Z` → RESULT OK (outputs `S/lifecycle/nhl.out`).
  - nba `2026-10-14T00:00:00Z 2026-10-14T00:01:00Z 2026-10-13T23:40:00Z 2026-10-14T06:00:00Z` → RESULT OK, preseason unvalidated (`S/lifecycle/nba.out`).

## Readiness evidence per version (session -ae, 2026-10-14) — each result applies ONLY to the hashes listed
| Version | backend app+tests | frontend/src | `check_project.py --ready` (deep env, empty scratch NBA_DATA_DIR) |
|---|---|---|---|
| Combined, before final-delta fixes | 9091e03a… | 39c77d5f… | 1251 passed, 0 failed (19:08) |
| + band/LOW fixes | a68b6122… | bd62570a… | 1251 passed, 0 failed (19:20) |
| + patch E (63b80c34…) | f3273ef1… | bd62570a… | 1258 passed, 0 failed (18:11); hashes identical after |
Later edits (patch F, cosmetic note count) need their own run.
- CORRECTION (found by session -25): the identity hashes above (9091e03a…, a68b6122…, f3273ef1…, and the frontend/src ones) also covered git-ignored editor-plugin state (`.omc/state/*` inside backend/app and frontend/src). They were identical before and after each run, so each result belongs to its tree, but they cannot be reproduced from source alone. From here on the identity is `scratchpad/source_id.sh`: sha256 over (sha256, path) of `git ls-files -co --exclude-standard` under backend/app, backend/tests, backend/pyproject.toml, frontend/src, frontend/package*.json and scripts (ignored files and docs excluded).
| + cosmetic hidden-row count + patch F (54e5009b…) — FINAL CANDIDATE | source_id.sh `9efe26f3f5b545bb4ff3d6d4a31f6a11820ad23598732c9aba7153d04a1b1922` (excludes docs and ignored files) | | 1263 passed, 0 failed (19:46), guard + typecheck + build OK; identity identical after |

## Today screen readability + threshold pass (2026-10-29, session docs-playbook)
Change: Today page now puts the slate first (title, date, policy, market, probability filter, games) and folds the full
results/training block and data sources into two collapsed sections ("How we're doing", "Data sources"). Every market now
defaults to the All-picks filter (the NBA winner's preset 70% is gone), "model confidence" reads "model probability", the
selected band's settled record is shown with a warning under 30 settled picks, and model-agreement research is behind
"Show research details". No model, API or data change.
- Environment: dev API `127.0.0.1:8100` (real data); `cd frontend && API_PROXY_TARGET=http://127.0.0.1:8100 npm run dev -- --port 5175 --strictPort`.
- Automated: `npm run typecheck` exit 0; `npm run build` exit 0 (`dist/assets/index-B1WMoTyY.js`, `index-B-s9RwEK.css`);
  `python3 scripts/check_project.py` → "Structure guard passed".
- Desktop (1280×1900, headless Chrome): Today shows 3 games; the two canonical forecasts (52.9%, 64.4%) are visible at the
  default All filter; band summary "63 of 68 started games have a forecast … Settled: 37–26 (58.7% correct)"; "Data sources"
  and "How we're doing" are collapsed. Opening both renders 4 overview cards + the source list; no console errors.
- Band check (`/?conf=70`): "At 70% or higher: 18–5 on 23 settled (78.3%). Too few settled picks to judge this threshold.";
  empty state "No forecast at 70%+ model probability on this date (2 lower)."
- Phone (390×844 CSS px, CDP `Emulation.setDeviceMetricsOverride`): `innerWidth` 390, `document.scrollWidth` 390 — no
  horizontal overflow; header wraps, filter wraps three per row, cards stack.
- Screenshots (not committed): `/tmp/v3-desktop.png`, `/tmp/v3-band70.png`, `/tmp/v3-expanded.png`, `/tmp/cdp-mobile.png`.
- Not run: `check_project.py --ready` (full backend suite; no backend change in this pass), manual-browser walkthrough
  (headless Chrome only), and no deployment — `127.0.0.1:4173` still serves `~/nba-predictor-live`, not this build.

## Market reposition pass (2026-10-29, session docs-playbook)
Change (frontend only): Today cards now show the market's de-vigged probability beside the model estimate
("Model estimate: ATL · 52.9% model probability · market 51.7%") and the record line carries the benchmark
("Market benchmark: model Brier 0.224 vs market 0.193 over 63 matched games — the market is the better
forecaster"). Performance opens with the record and the "Versus the market" card above the detail tables, with an
explicit sentence on which forecaster is better; system-status diagnostics are folded into a collapsed section.
"Pick:" became "Model estimate:", the filter is "Model probability threshold", and the threshold table warns that
small bands are noise. No model, API or data change.
- Automated: `npm run typecheck` exit 0; `npm run build` exit 0 (`dist/assets/index-DZKHYpx7.js`);
  `python3 scripts/check_project.py` passed.
- Desktop (1280×1400, headless Chrome): Today shows "market 51.7%" / "market 66.4%" and the benchmark line;
  Performance header → tiles → "Versus the market" (model 0.224, market 0.193, "the market's Brier is lower by
  0.031 — the market is the better forecaster on these games. The model is a record, not a market edge.").
- Phone (390×2500 CSS px, CDP): `innerWidth` 390, `document.scrollWidth` 390, no JS errors, market text present.
- Screenshots (not committed): `/tmp/reposition-today.png`, `/tmp/reposition-perf3.png`,
  `/tmp/reposition-mobile.png`.
- Not run: `check_project.py --ready` (full backend suite; no backend change), manual walkthrough, deployment
  (`127.0.0.1:4173` still serves `~/nba-predictor-live`).

## Reference redesign review — Codex, 2026-11-01 (local only)
- Compared the two user attachments (palette and four-screen Courtside reference) with the running implementation. Revised the existing shell, cards, hero, controls and responsive rules; added generated decorative assets, NBA logo aliases, per-sport dashboard history, safe sport switching from game routes, scroll reset and an outage Reconnect button. No backend/model/data contract changes.
- API/demo: `NBA_API_MODE=demo NBA_DATA_DIR=/tmp/courtside-design-review backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8831`; web from frontend/: `API_PROXY_TARGET=http://127.0.0.1:8831 npm run dev -- --host 127.0.0.1 --port 5193 --strictPort`.
- Real-data layout preview: copied the existing **Oct 10 development snapshots**, migrated only `/tmp/courtside-redesign-data/{nba,nhl}.sqlite`, preserved SNAPSHOT_AT `2026-10-10T20:07:37Z`. NBA used SQLite read-only backup; NHL used a file copy after asserting no source WAL, with copied-store integrity_check `ok`. Live store backups failed twice with `sqlite3.OperationalError: unable to open database file`; live stores were not migrated or written.
- API: `NBA_API_MODE=real NBA_DATA_DIR=/tmp/courtside-redesign-data NBA_LIVE_DATA_DIR=/tmp/courtside-redesign-data backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8832`; web from frontend/: `API_PROXY_TARGET=http://127.0.0.1:8832 npm run dev -- --host 127.0.0.1 --port 5194 --strictPort`. Initial sandbox port binds returned EPERM; local servers started with approved escalation.
- Browser: in-app browser at **1440×900** and **390×844**. Inspected NBA Today `/`, dashboard `/games/espn:401909896`, Research Details `?tab=research`, Performance `/performance`; also NHL Today and `/games/nhl:2026020227?sport=nhl`. NBA arena/athlete/ball are absent on NHL. Real snapshot labels remain visible; missing forecasts/odds remain missing. Phone document scrollWidth equals 390 on dashboard, research, performance, Today, History, Daily Learning and Demo Today after fixing the demo selector's intrinsic-width overflow.
- Flows: game-card navigation → dashboard → Research Details → dashboard; Performance, History and Daily Learning links; threshold 70% produces `conf=70` and pressed 70% button; selecting spread resets threshold; previous day updates `date=2026-10-30`; sport switch resets league-specific filters; changing sport from an NHL matchup returns to NBA `/` rather than requesting the NHL identifier from NBA. Empty `/?date=2000-01-01` shows no games and a return button. Loading state observed while real responses arrive.
- Outage: stopped only the test demo API, reloaded :5193, verified visible API error and Try again (no fixture fallback); restored API and confirmed page-level retry loads demo games. Added Reconnect because the shell health warning otherwise stayed stale, then repeated the outage, restored API and verified Reconnect clears alerts and restores Demo workspace/banner. Native calendar popup selection is unverified (automation fill did not commit); previous-day button and date URLs were verified.
- Appearance: inspected light four-screen layout at desktop and phone; dark desktop Performance; high-contrast logo discs on NBA/NHL hero. Screenshots: `/tmp/courtside-redesign-smoke/{dashboard,research,performance}-desktop.jpg`, corresponding `-phone.jpg`, `today-phone.jpg`, `demo-phone.jpg`, `performance-dark.jpg`, `nhl-dashboard-{desktop,phone}.jpg`, `history-phone.jpg`, `daily-phone.jpg`. Some early desktop screenshots precede the snapshot banner and final white logo discs; final captures supersede them where present.
- Automated: frontend typecheck/build passed during implementation; build emits its existing >500 kB JS chunk warning. `python3 scripts/check_project.py` and `git diff --check` passed. Final build and full readiness results will be recorded below when complete.
- Full readiness started: `OMP_NUM_THREADS=2 LOKY_MAX_CPU_COUNT=2 NBA_DATA_DIR=/tmp/courtside-redesign-readiness NBA_LIVE_DATA_DIR=/tmp/courtside-redesign-readiness /Users/andry/nba-predictor-deep/backend/.venv/bin/python scripts/check_project.py --ready > /tmp/courtside-redesign-readiness.log 2>&1`. Frontend gates passed; backend suite still running at this entry. No release/deployment, push or real-data model training requested or performed. Tests may fit synthetic fixtures. Training status in this preview reads the isolated copy, despite the existing development banner's generic live-system wording.

- Final frontend verification: `npm --prefix frontend run build` → exit 0 (typecheck + build, 555 ms on the first final run; later final rebuild also passed); structure guard and whitespace check passed. Follow-up inspected dark dashboard at desktop/phone (390px document width), fixed hero image sizing to cover and mobile ribbon theme color, and captured the final desktop dashboard. Full readiness remains running with backend failures observed; it is not a pass.

### Readiness diagnosis / native date follow-up
- Original full readiness exited 1: **1420 passed, 18 failed, 33 errors, 1 warning in 1608.93s**. Failures/errors are in test_autoresearch, test_research_agent, test_research_status; tracebacks show `sandbox-exec: sandbox_apply: Operation not permitted`, dependent setup assertions (`crash` rather than `keep`), missing sandbox-created output, and a denied `ps` subprocess. Backend source/tests remain unchanged. Raw log: `/tmp/courtside-redesign-readiness.log`. This run is a failure, not a readiness pass.
- Re-running those three modules with approved execution outside Codex's sandbox, retaining their own app sandbox profiles; log `/tmp/courtside-redesign-process-tests.log`. No test assertions, skips or application safety guards changed.
- Browser binding recovered after a tool-session refresh. Native calendar now verified at the default desktop viewport: opened the date-picker popup, Right then Return changed NHL Nov 1 to Nov 2, URL became `/?sport=nhl&date=2026-11-02`, and the date label and API-backed four-game slate updated. Screenshot `/tmp/courtside-redesign-smoke/calendar-selected.jpg`. This supersedes the earlier native-calendar gap.

### Final result
- Approved process-test rerun command (from backend/): `OMP_NUM_THREADS=2 LOKY_MAX_CPU_COUNT=2 NBA_DATA_DIR=/tmp/courtside-redesign-readiness NBA_LIVE_DATA_DIR=/tmp/courtside-redesign-readiness /Users/andry/nba-predictor-deep/backend/.venv/bin/python -m pytest tests/test_autoresearch.py tests/test_research_agent.py tests/test_research_status.py -q` → **107 passed, 1 warning in 182.05s**. Log `/tmp/courtside-redesign-process-tests.log`. All 51 original failed/error cases belong to these modules and are covered by this passing rerun; the first full execution also passed 1420 tests. No tests or safety profiles were changed, deleted or skipped to clear failures. This is combined full-run plus affected-module verification, not a claimed single unrestricted full-suite pass.
- Completed reference audit: palette tokens, compact sidebar/header, three-column desktop slate and forecast metrics, NBA arena/athlete/ball decoration, real team logos, Performance KPI/report hierarchy and Research Details layout inspected in rendered screens. API-backed values, missing forecasts/odds, Demo labels and NBA/NHL separation preserved. Backend diff is empty. No further source changes after the final passing frontend build. Final dashboard screenshot refreshed and browser left on the NBA dashboard; viewport override reset.
- Implementation and local verification complete. Independent review remains the next acceptance step; nothing was pushed or released. Preview data is an older development snapshot, not a current forecast service.

## NFL data-only UI + live-health check + preview refresh — Claude (Opus 5.5, session 2994a49e), 2026-11-01 ~18:15–18:45Z
- **Live health (read-only, `PRAGMA query_only` on `data/{nba,nhl}.sqlite`; launchd jobs from `~/nba-predictor-live`, release 18fa0bf):**
  NBA odds/two-hour cycle every ~15 min, all `success` (runs 5044–5055, 16:14–18:09Z); NHL cycle ~35 min, all `success` (2244–2257).
  The 1 p.m. ET NHL game WPG @ CAR (`nhl:2026020227`, 18:00Z) has two-hour forecasts recorded 16:14–16:29Z (predictions 6,
  near_tipoff 1, market 15, adaptive 12, game_forecasts 6); live API `:8100/api/games?sport=nhl&date=2026-11-01&policy=canonical`
  → `with_forecast: 1`, other 4 `upcoming`. NBA games start 20:30Z; forecasts are two-hour only, none due yet. Not backdated, nothing written.
  The NHL game still showed `scheduled` after the 18:19:53Z refresh because the NHL stats feed itself returned `gameStateId 2` (pre-game),
  period 1, 0–0 for it (raw fetch 17165); status turns `in_progress` only on states 3–5. Source timing, not a job failure.
- **Why :5194 looked stale:** its API (:8832, started inside the Codex sandbox) read `/tmp/courtside-redesign-data`, an Oct 10 copy.
  Codex's two live-backup failures were the sandbox: with the backend venv (SQLite 3.50) a `mode=ro` backup of the live WAL stores works
  (it leaves empty `-wal`/`-shm` side files, main files unchanged: sizes/mtimes 199491584 @13:09, 247685120 @12:45). The system
  `python3` (3.9) fails with `unable to open database file`. New regression test `test_dev_script.py::test_wal_mode_live_store_without_side_files_is_copied_and_left_unchanged`.
- **Preview refreshed:** new dir `/tmp/courtside-preview-20261101` (SNAPSHOT_AT `2026-11-01T18:18:33Z`): read-only backup of both live stores,
  APFS clones of research/raw/eval, `--sport nba|nhl migrate` on the copies only (0032 applied to copies), NFL store copied from the scratch
  import + `nfl ingest --from 202610 --to 202612` into the copy. Stopped only PID 80658 (old :8832) and later my own 94141; API now
  `NBA_API_MODE=real NBA_DATA_DIR=/tmp/courtside-preview-20261101 NBA_LIVE_DATA_DIR=<repo>/data backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8832`.
  Vite :5194 (PID 80672) unchanged. `:5194/api/health` → `data_snapshot_at 2026-11-01T18:18:33Z`; NHL slate shows the 1 p.m. forecast.
- **Browser (preview tab, 1440×900 and 390×844):** NFL Game day (12 games Nov 1: 8 in progress without invented scores, 4 scheduled),
  matchup `nfl:401873024` and `nfl:401873009` (facts, forecast unavailable, earlier meetings, last results; checked against real results,
  e.g. DAL 7–41 at PHI 2024-12-29, PHI 34–6 at DAL 2024-11-10), empty date 2026-11-04, error `/games/nfl:999` (404 + Try again),
  NFL Performance/History/Daily Learning unavailable screens, NBA and NHL Game day, NHL Performance, History. Document overflow 0 px
  on every one at 390 px. Dark theme NFL matchup at desktop. Light theme restored; tab closed. Screenshots under
  `~/.t3/userdata/browser-artifacts/` (game day desktop, matchup dark). Demo mode NFL: covered by API test (404, honest message).
- **Checks:** `pytest tests/test_api.py tests/test_nfl.py tests/test_nfl_api.py tests/test_dev_script.py tests/test_multisport.py tests/test_storage.py tests/test_live.py`
  → 195 passed. First combined run failed 5 NFL API tests: `test_api.py` sets `app.dependency_overrides[api_mode] = demo` for the session;
  fixed in the NFL test fixture as `test_api_research.py` does (no assertion changed). `npx tsc --noEmit` OK; `python3 scripts/check_project.py`
  passed; `git diff --check` clean. `check_project.py --ready`: typecheck + build passed (existing >500 kB chunk warning); backend suite result below.
- **Full readiness result:** `OMP_NUM_THREADS=4 backend/.venv/bin/python scripts/check_project.py --ready` → **exit 1**: structure guard,
  typecheck and build passed; backend **1473 passed, 8 failed, 1 warning in 1168.14s**. All 8 failures are `tests/test_deep_study.py`
  (torch/tensorflow are only in the deep venv; they also fail on the pre-NFL code, see the NFL A0 entry). Rerun of that file with
  `/Users/andry/nba-predictor-deep/backend/.venv/bin/python -m pytest tests/test_deep_study.py -q` → **10 passed**. Log
  `/tmp/courtside-preview-20261101/ready.log`. This is a full run plus an environment-specific rerun, not a single green run.

## Freshness milestone (docs/REFRESH_PLAN.md) — Claude (Opus 5.5), 2026-11-01 ~19:05–19:40Z
- Startup: `NBA_DEV_DATA_DIR=/tmp/courtside-fresh-20261101 API_PORT=8845 WEB_PORT=5195 sh scripts/dev.sh` → ready; publisher started
  (`app.cli preview-publish --live <repo>/data --dev /tmp/courtside-fresh-20261101 --interval 60`). Live data read-only; :5194/:8832 untouched.
- `/api/freshness?sport=nba|nhl` → `auto_snapshot`, NBA `pregame` (120 s), NHL `active` (60 s), both `stale` at 19:07Z (live data 28/18 min
  old: launchd jobs still run at 15/30 min). `sport=nfl` → 503 "No NFL data store found" (no live NFL store).
- No-reload update (headless Chrome over DevTools, `/tmp/claude-cdp/observe.py`, one page open on `?sport=nba`): live NBA refresh at
  19:14 → publisher republished 19:14:31Z (reason: source time lag; 1.0 s) → bar changed within 5 s from "Data updated 1:39 PM … Source data
  stale" to "Data updated 2:14 PM · Copy published 2:14 PM · Next check 2:15 PM", banner "last copy 2:14 PM"; page loads 1. Requests in
  65 s: freshness 4, games 2, meta 4, ranked 4 (dev StrictMode doubles the initial effects); game views were not refetched because the
  data revision did not change (lag-only republish). A revision-change refetch in the browser was not observed this session (covered by
  code path + backend revision tests only).
- Screenshots (headless Chrome): `/tmp/claude-fresh-smoke/{nba-desktop,nhl-desktop,nfl-desktop,nba-phone,nhl-phone500}.png`. Desktop bar
  and banner render; at 500 px the bar wraps without overflow; the 390-px capture is a crop of Chrome's ≥500-px headless layout (not a
  real phone check). NFL shows the 503 error card and a failed-check bar (true: no store).
- Real `tick` on scratch `/tmp/claude-tick-20261101`: NHL dates refresh 1 request / 0.43 s; NBA dates refresh + due odds cycle 230 s.
- Not verified: hidden-tab pause/resume and offline in a real browser (code path only), touch devices, dark theme of the bar.

## Larger text restored, decorative images removed, dark/phone fixes, freshness 404 — Claude (Opus 5.5, session cb8787d2), 2026-11-01 ~19:35–20:00Z
- Started only after the freshness thread had finished (T3 thread eafbd452 idle, its last run 19:35Z). Preview :5194 (vite, current tree) with
  API :8832 on `/tmp/courtside-preview-20261101` (frozen copy, no publisher).
- **Freshness 404:** `/api/freshness` returned 404 on :8832 because that API process (PID 1474, started 13:24 EST) predated the endpoint
  (main.py edited 13:50 EST). Restarted it on the current code with the same environment (`NBA_API_MODE=real NBA_DATA_DIR=/tmp/courtside-preview-20261101
  NBA_LIVE_DATA_DIR=<repo>/data backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8832`, new PID 62324,
  log `/tmp/courtside-opus-check/api-8832.log`); about one minute of downtime. Now `GET /api/freshness?sport=nba|nhl|nfl` → 200 each, mode
  `frozen_snapshot`, NBA/NHL `source_state: stale`, NFL `never` (true for this copy). The page shows "Frozen copy: will not update
  automatically … Source data stale" instead of the 404; the failure is reported, not hidden. The live API :8100 also answers 404 (old
  process, live — not touched; it needs a restart as part of a release).
- **Browser (T3 preview, same-origin iframes, theme set via localStorage and restored afterwards):** 12 screens — NBA Game day, dashboard,
  Research details, Performance, History, Daily learning; NHL Game day, dashboard, Performance; NFL games, NFL matchup, NFL Performance
  (unavailable) — × light/dark × 1440/390 px = 48 loads. Automated per load: document overflow, elements past the viewport, clipped text,
  WCAG contrast of every text node on solid backgrounds, decorative `<img>` count, nav label height, error alerts. Result: **0 problems in
  all 48** (overflow 0, nothing clipped, no contrast below 4.5/3, no decorative images, nav labels one line at 1440, no alerts).
  Text on gradients was checked by eye (dark dashboard, dark phone NBA/NHL/NFL).
- **Before the fixes (same harness, 19:00Z):** dark Game day selected threshold chip contrast 1.07 (invisible), dark Research "?" 2.23,
  phone "Real data" popover from −3 px; rendered sizes nav 16, team names 17, odds 14, tabs 15, chips 14, badges 12, brand line 8 px.
  **After:** chip lime in dark (contrast passes), "?" passes, popover 16–374 px of 390 when opened; nav 19, team names 19, odds 18,
  tabs 18, chips 17, page subtitles 19, KPI labels 19, brand line 10 px.
- Screenshot after: `~/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mvy8jg48-03ac818b.png` (dark: NHL phone with popover
  open, Research details with "?"). Before: `…-mvy77b9l-9e517ddd.png` (dark Game day, invisible chip).
- Not verified: a real phone/touch device; NFL with a live store (none exists).
- Readiness: `backend/.venv/bin/python scripts/check_project.py --ready` → **exit 1**: structure guard, typecheck, build passed (existing >500 kB chunk warning); backend **1505 passed, 8 failed, 1 warning in 1776 s**; all 8 are `tests/test_deep_study.py` (torch/keras only in the deep venv); rerun `~/nba-predictor-deep/backend/.venv/bin/python -m pytest tests/test_deep_study.py -q` → **10 passed**. Log `/tmp/courtside-opus-check/ready.log`. A full run plus an environment rerun, not one green run.

## Codex review fixes P1–P4 — Claude (Opus 5.5, session cb8787d2), 2026-11-01 ~20:15–20:45Z
- Regressions first: `backend/.venv/bin/python -m pytest tests/test_freshness.py -q -k "leads_to_the_live or inside_the_live or odds_only or per_sport"`
  → 5 failed before the fixes (DID NOT RAISE ×3; revision unchanged after an odds-only ingestion; NFL `auto_snapshot`); after → passed.
- Related suites after the fixes: `pytest tests/test_freshness.py tests/test_dev_script.py tests/test_nfl_api.py tests/test_nfl.py tests/test_live.py tests/test_api.py -q`
  → 181 passed, 1 warning. `npx tsc --noEmit` → OK. `sh -n scripts/dev.sh` OK; `dev.sh` test suite 94 passed using `backend/app/devpaths.py`.
- Manual: `python - check-paths LIVE DEV MARK < backend/app/devpaths.py` on scratch dirs → "data directories are separate" exit 0; with
  `dev/nba.sqlite` symlinked → "write target is a symlink" exit 1.
- API :8832 restarted on this code (same env as before) → `/api/freshness?sport=nba|nfl` 200, both `frozen_snapshot`, own publish time.
- Browser P2 (T3 preview on :5194, `window.fetch` patched in the page): forced revision change + `/api/games` 503 → 4 game cards stayed,
  bar showed "Update failed (503: simulated outage); showing the last loaded data, retrying"; outage ended with the revision held fixed →
  `/api/games` re-requested 30 s after the failure (20:16:45 → 20:17:15Z) and the warning cleared. Tab closed; theme untouched.
- Full: `OMP_NUM_THREADS=4 backend/.venv/bin/python scripts/check_project.py --ready` → **exit 1**: structure guard, typecheck, build passed; backend **1511 passed, 8 failed, 1 warning in 1184 s**, all 8 `tests/test_deep_study.py` (torch/keras only in the deep venv); rerun `~/nba-predictor-deep/backend/.venv/bin/python -m pytest tests/test_deep_study.py -q` → **10 passed**. Log `/tmp/courtside-opus-check/ready2.log`. A full run plus an environment rerun, not one green run.

## Focused Codex re-review — P1–P4, 2026-11-01
- Reviewed shared publisher path guards, background error reporting/retry wiring, odds revision invalidation and per-sport publication metadata. No remaining blocking finding in these four fixes.
- Independent: `backend/.venv/bin/python -m pytest backend/tests/test_freshness.py backend/tests/test_dev_script.py backend/tests/test_nfl_api.py backend/tests/test_nfl.py -q` → 144 passed, 1 warning, 29.42s; frontend typecheck, structure guard and whitespace check passed. Regressions preserve the source schema and reject unsafe destinations, change revision on an odds-only update, and distinguish NBA/NFL publication states/errors.
- Browser outage/recovery and visual matrix remain author evidence above; not replayed in this focused code/test review. Full/deep suites not repeated. Read ready2.log: 1511 passed / 8 deep failures, exit 1; separate deep rerun remains author-reported. No claim of a single green full run, release readiness or installed scheduling. Only review documentation edited.


## 2026-11-01 ~21:05Z — missing NBA forecast / preview recovery (Codex)
- Compared `GET http://127.0.0.1:8100/api/games/espn%3A401909903?sport=nba` with :5194: live canonical #779 existed, frozen 13:18 EST preview lacked it. Record timestamp 18:59:17Z = 13:59:17 EST, before 15:30 EST tipoff. No predictions regenerated.
- Started existing launcher with `NBA_DEV_DATA_DIR=/tmp/courtside-active-preview-20261101 API_PORT=8832 WEB_PORT=5194 sh scripts/dev.sh` (detached PID 7934; log `/tmp/courtside-active-preview-20261101.launcher.log`). Preview now serves fresh copies, checked for publication every 60 s. Publisher log confirms initial NBA/NHL publication then successful checks at 21:03:58Z and 21:04:58Z; status last_error null.
- Preserved scratch-only NFL SQLite/raw data from old preview into new guarded development directory; NFL identity and quick_check passed, `/api/nfl/meta` HTTP 200. Live data/ lacks NFL: launcher restart rebuilds scratch stores, so save/restore this NFL scratch data on restart; do not write it into live data/.
- Browser URL `http://127.0.0.1:5194/games/espn%3A401909903?conf=70`: desktop 1440×900 and phone 390×844 display Boston–Orlando in progress, BOS 55.5%, spread and three totals; canonical table populated. Phone document/viewport width both 390. Screenshot `/tmp/courtside-bos-orl-fixed.png`, phone `/tmp/courtside-bos-orl-phone.png`. Temporary viewport reset. NHL Game Day also showed existing forecasts. No navigation/source changes.
- `python3 scripts/check_project.py` passed. Full readiness not rerun; this is an operational preview fix, not overall readiness certification. Live source timestamps still old enough to display Source data stale: old live refresh cadence remains, no tick job installed. No commit/deploy/live migrations or training.


## 2026-11-01 — market/threshold matchup records (Codex)
- Classification by threshold AND market, then all sports. Reused existing canonicalThresholds API/useApi and ThresholdOverview types; added ForecastThresholdRecord in existing component. NBA/NHL each recorded matchup market now shows highest reporting threshold reached, cumulative live win/loss/rate/count, small-sample note, explicit live-season provenance. One request per sport/revision, loading/error/retry/unavailable states. Historical and live evidence remain separate; no new model judgment or ranking/forecast selection changes.
- Renamed matchup-only broad “No proven skill” chip to “Market edge unproven” with explicit bookmaker-edge tooltip; observed threshold records appear independently. Existing Performance market ladders unchanged. NFL matchup and unavailable performance/history/research screens have an explicit “No recorded NFL forecasts to classify by market and threshold” section, using existing API capability reason; no generic NFL model/API activation or invented numbers.
- Files: frontend/src/components/ThresholdOverview.tsx, frontend/src/pages/GamePage.tsx, frontend/src/pages/NflPages.tsx. Preserved pre-existing edits. Actual checks: npm --prefix frontend run typecheck PASS; npm --prefix frontend run build PASS (existing >500kB chunk warning); python3 scripts/check_project.py PASS. No backend changes, migrations, model runs, full readiness rerun, commit or release.
- Browser :5194 Boston–Orlando: winner55%+ 42–27/60.9%/69; spread60%+10–11/47.6%/21 small sample, markets separate. NBA desktop1440×1000 + phone390×844 (scroll width390). NHL Jets–Hurricanes phone shows winner65%+6–2 and puckline55%+50–33, plus reg3/totals separated. NFL401873024 empty state verified desktop/phone; phone scroll width390. Temporary viewport reset. Screenshot /tmp/courtside-market-threshold-desktop.png. Error/empty code paths inspected; network-error UI not forced. Dark-mode-specific check not repeated.
- Next: separate focused review of these UI changes; full readiness still not certified. Source refresh rollout remains outstanding and unrelated. NFL forecast serving remains unavailable.


## 2026-11-01 16:21–16:25 EST — Carolina final recovery / scheduled refresh preparation
- Requested fixing the delayed final. Ran released CLI from /Users/andry/nba-predictor-live/backend with NBA_DATA_DIR pointing to repository data/, NBA_RELEASE_GUARD=1 and NBA_RELEASE_ROOT set: `.../.venv/bin/python -m app.cli --sport nhl refresh --force`. Success run2280, one request, season2027, fetch17182, 1409 games seen, 2 updated; release18fa0bf unchanged. Normal importer wrote actual observation times; no score/forecast manually edited or schema migrated.
- Verified both live :8100 and preview :5194: game nhl:2026020227 final, CAR7/WPG0, original canonical winner#738 win; reg3 win; spread/total/both team totals loss. Automatic publisher carried result into preview; browser existing NHL tab showed final and all grades. Winner65%+ threshold updated7–2 (77.8%,9 settled). Screenshot /tmp/courtside-carolina-final.png. No frontend changes this task. Separate `score` command was queued behind scheduled NHL odds; canceled ONLY our redundant PID21255, before it ran; canonical grades already available from imported outcome, not a successful score-command claim.
- Recurrence fix PREPARED, NOT INSTALLED: scripts/launchd/com.nba-predictor.odds.plist and nhl-odds.plist now call `tick`, StartInterval60, retaining same labels/logs, clean pinned release path, release guard and existing environment. Coordinator applies active/pregame/idle cadence and bounded retries; not forced full-season refresh every minute. No duplicate launchd jobs created or installed.
- Actual checks: plistlib parse/inspection passed, structure guard passed, test_freshness.py + test_release_guard.py 35 passed, 1 existing warning (1.59s). First pytest invocation referenced nonexistent test_release.py and ran zero tests; corrected filename then passed. No full readiness rerun or claim.
- Remaining blocker for lasting fix: pinned release18fa0bf does NOT contain tick, development branch includes schema0032 and unrelated unreviewed snapshot/NFL evaluation/UI work. Installing these plists now would fail; disabling release guard or running dirty development code on live is not a rollout. Next task: prepare scoped reviewed refresh release from pinned base (coordinator + date adapters + necessary tests, preserving model policy/schema compatibility), independent review per project rule, then promote and replace the existing odds jobs with these templates; verify two unattended cycles and rollback. User's fix request supplies rollout intent; no need to ask again merely for authorization. Current game fixed; recurring ingestion rollout remains incomplete. No NFL activation, training, live migration, release promotion, commit or push.


## Independent remaining-check audit — Codex, 2026-11-01 ~20:02–20:04 EST; observation incomplete
- Read-only current state at01:02Z: NBA marked full cycles00:42:55/00:57:56 (901s apart); second near_tipoff5188 still running, so odds row finished does NOT establish complete second tick. NHL only first full cycle00:42:55, second due~01:13Z. Completed compact tick log confirms first full ticks returned; successful focused ticks then run~61s apart. No lock_busy/refusal rows since activation. Both refresh streams have zero failed refreshes in observation.
- Latest broad successes NBA00:31:41 and NHL00:23:17 (prior to activation): an hourly broad verification needs observation through~01:32Z, beyond proposed01:22Z check. No broad refresh by new release yet observed; do not force one and label it unattended.
- New NBA final espn:401909904 observed at00:52:53Z, result98–90 away, observed_retrieval availability timestamp. This proves real final ingestion after activation, NOT actual end-of-game-to-display lag without source finish time. No new NHL final in this interval.
- Pause confirmed: first full cycles start00:42:55; next successful fast refresh NBA00:52:53/NHL00:55:01, gaps~10/~12min. Long same-job forecast work prevents minute refresh ticks until completion. This is not continuous minute-level refresh.
- Preview API01:02:32: NBA source00:57:56/copy00:58:58 fresh; NHL source00:55:02/copy00:55:50 stale despite live NHL refreshing through01:02:06. Publisher healthy/no errors, configured max_lag600s; unchanged NHL revision can leave copied freshness heartbeat10min behind. A stale preview banner here is not live ingestion failure. Changed data revisions publish promptly; no publisher edits during verification.
- Independent preactivation confirmation: NBA near_tipoff5166(00:31:42),5158(00:01:02) already had availability_two_hour Invalid player minutes; current error predates activation. Rollback dry-run passed per activating session's report, not rerun by this audit. No live/source/job changes or tests triggered. Remaining: complete second full ticks, unattended hourly broad refreshes, idle-no-work evidence when actually idle, final preview check. NFL untouched.

## Refresh release 7a83e27 activation (2026-11-02 00:42–01:36Z, live, read-only verification)
- Live checkout `~/nba-predictor-live` fast-forwarded 18fa0bf → 7a83e27 (clean); `release-check` nba/nhl → schema 31/31, problems []; odds/nhl-odds launchd jobs run `tick` every 60 s.
- Observed unattended: NBA cycles every 15 min (4), NHL every 30 min (2); minute date refreshes succeeded throughout (NBA 18, NHL 42); hourly broad refresh (NBA 01:32:30, NHL 01:23:20); no lock_busy, refusal or refresh-failure rows.
- Rollback dry run: 18fa0bf detached worktree `release-check` against live stores → problems []. Backups: `/Users/andry/nba-predictor-launchd-backup-20261101-refresh/` (checksums OK).
- Known: NBA forecast runs `failed` from the research agreement step (`Invalid player minutes`, failing since 2026-10-24T20:11Z: ESPN "-"/"--" minutes for inactive coach's-decision players); minute refreshes pause during a cycle's forecast step (worst ≈14 min); preview copies of unchanged data may lag ~10 min.
- Not run: frontend/browser checks (no frontend change in this release).


## Refresh activation independently verified — Codex, 2026-11-01 21:26 EST
- Live checkout clean at7a83e27. Read-only audit of00:42:00–01:36:59Z confirms FOUR completed NBA tick cycles (00:42:55,00:57:56,01:12:43,01:27:35) and TWO NHL cycles (00:42:55,01:13:12 log / marked odds01:13:13). Verified compact completion logs, not merely odds snapshot marks. Forecast cycle timing~15/30min preserved; NHL second forecast step skipped as not due.
- Successful unattended broad refresh NBA01:32:30Z, NHL01:23:20Z. Window ending01:36:59Z contains19 NBA/43 NHL successful refresh rows plus4/2 skips, zero refresh failures; author's18/42 tally used an earlier endpoint. No busy-lock/refusal rows. At02:26:32Z preview both fresh, no publish errors (NBA source02:24:59/copy02:25:34; NHLsource02:24:17/copy02:24:32).
- ACCEPTED OPERATIONAL LIMITATIONS, not error-free: all four NBA near_tipoff steps failed in the research agreement member with availability_two_hour Invalid player minutes (confirmed predates activation); primary forecasts unaffected per retained records. Completed forecast steps occupy the tick/store job~4–14min, pausing fast refreshes. Unchanged preview heartbeat may lag10min. Actual final-to-display latency and an idle-no-work interval were not independently measured in this active-game window; do not claim continuous1min updates or observed idle behaviour.
- Earlier independent checks confirmed guards/schema31 and backup hashes. Old-release rollback dry-run passed per activating session; not rerun here after their temporary checkout was removed. No live writes, manual jobs, model runs, NFL access/activation or new tests in this verification. Next scoped fix: Invalid player minutes, then remove refresh pauses via separately reviewed design/release. Activation no longer pending; older candidate-status sections below are historical.

## Release A a55c874 (box-score connection-failure retry) activation (2026-11-02 ~17:59Z, live)
- `~/nba-predictor-live` 7a83e27 → a55c874; `release-check` nba/nhl → schema 31/31, problems [], definitions unchanged.
- Guarded `app.cli --sport nba boxscores` → 13/13 previously given-up games fetched, complete lines, basis observed_retrieval, available from 17:59:28–17:59:42Z (no backdating).
- Tick jobs reloaded; idle ticks exit 0. Full suite on a55c874: 1373 passed, 8 failed (test_deep_study only; same 8 fail on 7a83e27). Not run: browser checks (no frontend change).


## Exact displayed-percentage card record — Codex, 2026-11-02
- Scope: descriptive live canonical history only, sport → market → pick direction → displayed 0.1-point percentage, also matching source/model version and bookmaker/model/none line source. No cumulative fallback. Same market may contain different actual lines; labelled line provenance. Current record as of API read, NOT a reconstruction of what was known when an older forecast was made. API comparison excludes this game; card totals add its own canonical win/loss once it settles and explicitly say whether this game's result is included, plus a separate count of other settled examples. Small sample is separate from observed right/wrong/evenly-split tendency.
- API/UI: `percentage_history` added to existing canonical responses and ranked picks. Backend recomputes from the current store per request; no persisted counters, new scheduler, ledger/schema migration, training or forecast changes. Existing 30s visible-page freshness checks refetch views on a data revision; existing publisher checks live copies every60s. Source ingestion and publisher delays still apply. No actual new live final arrived during this browser check; automatic refresh is verified by integration ingestion/revision assertions plus existing UI refresh wiring, not a measured live end-to-end latency.
- Preview startup (repository root): `NBA_API_MODE=real NBA_DATA_DIR=/tmp/courtside-active-preview-20261101 NBA_LIVE_DATA_DIR=/Users/andry/nba-predictor-live/data PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8841`; in frontend/: `API_PROXY_TARGET=http://127.0.0.1:8841 npm run dev -- --host 127.0.0.1 --port 5198 --strictPort`. Uses existing auto-published development copy, no live writes. Normal reproducible startup remains `sh scripts/dev.sh`; restart it to load the new API. Existing :8832/:5194 stack was not restarted or overwritten. Sandbox binds initially refused; approved local startup succeeded.
- Focused checks: `cd backend && OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_threshold_overview.py tests/test_canonical.py tests/test_canonical_markets.py` →47 passed/1 Starlette deprecation warning,28.19s. Covers exact rounding boundaries (also checked against Node toFixed), direction/version/source/line separation, own-game exclusion, future result timestamp/late forecast rejection, empty/equal/right/wrong states, result ingestion changing data revision and card counts across Today/Game/History/Ranked, repeated reads stable.
- Independent current-API audit: `python3 /tmp/audit_percentage_cards.py` reads all11 markets with limit5000, checks returned totals and independently groups/excludes each target →981 histories reconcile (87×5 NBA,91×6 NHL). No duplicate counting or broader threshold substitution. Structure guard passed; whitespace check clean. Full readiness results recorded below when completed.
- Browser: http://127.0.0.1:5198/games/espn%3A401909903?sport=nba and /games/nhl%3A2026020227?sport=nhl at1440×1000 and390×844; document widths equal viewport (1440/390). Correct five NBA and six NHL market names, Over/Under, home/away, model/bookmaker lines, sample counts and timestamp visible; no console warnings/errors before deliberate outage test. Default headlines use exact model percentage; broad ranking evidence remains separately named behind ranking details. Boston55.5% is only the game's own one win; zero OTHER settled matches, never59%.
- Game Day: navigation + previous-day control; selected NHL Game total goals for2026-11-01. Observed more-right(55.4% under1–0), more-wrong(52.3% under0–1), evenly-split(54.4% under1–1), and empty examples visible. Nov2 upcoming games show not forecast yet; no fabricated history. Loading states observed during navigation. API8841 intentionally stopped only for error test →API NOT REACHABLE + Couldn't load data, no fallback numbers; restarted and Reconnect restored API-backed records. Temporary viewport reset. Demo-specific browser check not repeated (demo contract unchanged; canonical reporting not provided in demo).
- Screenshots: /tmp/courtside-percentage-desktop.png, /tmp/courtside-percentage-phone.png, /tmp/courtside-percentage-nhl-phone.png, /tmp/courtside-percentage-nhl-desktop.png, /tmp/courtside-percentage-cards-final.png. Some initial images precede final wording clarification; updated Boston evidence captured separately. No deployment or independent Codex review claimed.
- User follow-up correction: Boston55.5% has one matching win total (the card itself); zero other settled matches. Always display W/L counts including0/0; broader rank-history text removed from default headlines and explicitly named in expanded ranking details. Final card grid fits at390,847,1440px without overflow (narrow layouts use a full-width card). Final evidence: /tmp/courtside-boston-exact-percentage.jpg and /tmp/courtside-boston-exact-percentage-phone.jpg. The earlier cropped PNG was blank and is not evidence. Final `npm run build` passed (582ms Vite phase; existing >500kB bundle warning). Final structure guard and `git diff --check` passed.
- Final follow-up supersedes the intermediate zero-count wording: BOS55.5% now reads **First example won; 1 win, 0 losses, 1 settled pick; includes this game's result; 0 other settled examples**. Before a game settles it adds no win/loss. Rates on the card use the displayed total; API comparison intervals still describe other games and are not shown as total intervals. Confirmed final wording in browser at390/847/1440px; the two final JPGs above were replaced with current evidence. Final frontend build passed (571ms Vite phase; existing bundle warning).
- Final team-total verification after user spotted the inclusion issue: Orlando Over109.5 at50.7% shows1 win/1 loss (evenly split); Boston Over111.5 at51.5% shows0 wins/2 losses (more often wrong). Each includes this game and lists1 other settled example, with markets kept home/away separate. Screenshot /tmp/courtside-team-total-records.jpg.

- Full readiness command completed FAILED (before final opposite-side follow-up):1499 passed,26 failed,33 errors,1 warning in1362.84s. Detailed log /tmp/courtside-percentage-ready.log. Autoresearch/research-agent trial subprocesses report sandbox-exec sandbox_apply Operation not permitted; research-status ps denied; deep-study Torch/Keras dependencies absent; research-agent file/path case also failed. First autoresearch failure reproduced alone →KeyError markets from absent trial output, /tmp/courtside-percentage-readiness-first-failure.log. No checks skipped or assertions weakened, no unrelated fixes. These are NOT passing readiness checks.
- User then requested against-model output. Response-only opposite_pick_label uses existing market labels and unchanged stored line. Two-way opposite swaps a/b; reg3 returns null. The shared renderer names an experimental opposite only when other matching games have more losses than wins. A pending card before recorded start says Against model — experimental; a started/final card says Opposite scenario — retrospective and explicitly no pregame opposite call was recorded. Never triggered by the card's own loss; never promoted to the existing Best Pick ranking. Model lines/current price limitations visible.
- Final focused tests, same command above plus2 new opposite-label tests:49 passed/1 warning in28.78s. One initial new-test failure was a fixture clock before prediction time; setting it to the actual forecast time fixed the test; all original assertions retained. `node /tmp/check-courtside-card-ui.cjs` →6 actual React rendered-card cases pass: pregame vs retrospective, own-loss-only no trigger, model/bookmaker price wording, reg3 no single opposite, tied total no trigger. `npm run build` final passed(561ms Vite phase, existing >500kB warning); structure guard/diff --check passed. Full suite not repeated after small follow-up due known readiness failures; no full readiness claim.
- Final running API restarted on8841 (same command/data), frontend5198. Real NBA browser verified BOS under111.5 retrospective on0–1 other matching evidence, total0–2 including current loss; ORL50.7% total1–1 has no against signal because other record1–0. Screenshot /tmp/courtside-opposite-scenario.jpg (and -phone.jpg); latest text checked390/847/1440px no overflow, viewport reset. Actual pregame signal not observed on real qualifying upcoming picks; pregame branch is verified by rendered component cases only.


## Personal Telegram delivery — Codex, 2026-11-02
- Setup/commands and limitations: docs/TELEGRAM.md. Token/chat/nonce saved in .env.telegram mode600 and git-ignored; receipt DB mode600 and logs ignored; no secrets in committed files or launch agent. Verified official getMe usernameProp51picksbot and no webhook; only private Start with generated nonce paired a destination. No public auto-subscription.
- User requested actual test: `PYTHONPATH=backend backend/.venv/bin/python -m app.cli telegram test` →sent, Telegram message57; `... telegram preview --date 2026-11-01 --game-id espn:401909903` →sent, message58. Preview1158chars uses real stored BOS–ORL records and actual historical time, explicitly TEST PREVIEW—PAST GAME, NOT A LIVE PICK. Own-game results included in displayed totals; only other matches trigger retrospective opposite scenario. No final-game record was changed to pass live eligibility. Telegram accepted messages; recipient-read not verified.
- Read-only actual-source dry run: repo/data is the configured live-job source; ~/nba-predictor-live/data does not contain the stores. NBA/NHL both fresh, failingFalse, zero eligible summaries. Source read transactions/query_only only; no model, fit, forecast, import, score or migration commands. NFL absent from sender by construction.
- Installed personal agent from scripts/launchd/com.nba-predictor.telegram.plist to ~/Library/LaunchAgents using exclusive creation (no overwrite), launchctl bootstrap gui/<uid> succeeded. Three unattended statusok runs at20:17:09/20:18:29/20:19:49Z, launchctl runs3/last exit0, stderr empty. StartInterval60s; actual observed cadence~80s. This is a logged-in Mac service, not cloud delivery: awake/online and source jobs required. /stop pauses, /start resumes; only the paired chat controls it. No naturally eligible pregame message observed yet.
- Verification: initial `OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py tests/test_threshold_overview.py tests/test_canonical.py tests/test_canonical_markets.py` →61 passed/1 existing warning,28.25s. Final expanded sender-only command →18 passed,0.30s. SQLite repeated sends across separate connections deduplicate; new forecast IDs can send; ambiguous acknowledgements are not automatically retried; confirmed connection/429 retries are bounded by time and pregame eligibility. Mismatched sport/market/percentage, stale/failed sources, finished games and unpaired/group controls rejected.
- `npm run typecheck`, `plutil -lint scripts/launchd/com.nba-predictor.telegram.plist`, `python3 scripts/check_project.py`, `git diff --check` passed. Existing full-readiness sandbox/ML failures above remain; full suite not repeated for this scoped sender. No repeated browser pass: no changed screen/flow, actual external delivery verified by Telegram API acknowledgements instead. No release checkout changes, purchases or deployment of the main app.


## Telegram message presentation — Codex, 2026-11-02
- User confirmed Recommended pick wording. Added bold HTML headings, separators, sport/status icons, prominent recommendation, consistent market order, and visually separate experimental/retrospective opposite blocks; forecasts/ranking/counts unchanged. Input text escaped before markup. parse_modeHTML verified by tests and real Telegram acceptance. Live deduplication keys remain unchanged across presentation-only updates; explicit redesigned preview key versioned separately.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py` →19 passed,0.21s; HTML parser safety test and actual send payload HTML-mode assertion added. `python3 scripts/check_project.py` and `git diff --check` passed. No browser pass (Telegram-only text change), no full readiness claim.
- `PYTHONPATH=backend backend/.venv/bin/python -m app.cli telegram preview --date 2026-11-01 --game-id espn:401909903` →preview sent; stored receipt message59,statussent,1290chars, Recommended pick heading present. Actual Telegram client appearance/read status is for user review; API accepted formatted message. No secrets printed, no extra messages beyond requested new preview.


## Native Telegram image card, no icons — Codex, 2026-11-02
- User requested a proper card design without emojis. Existing format_batch records feed telegram_card.py’s Pillow renderer: navy header, lime Recommended pick panel, stacked white market cards, correct original percentages, W–L/settled counts, source labels, historical results, and separately labelled retrospective/experimental opposite. No analytics duplicated or forecast/ranking/count changes. Dynamic source text remains escaped in the underlying message; image is rendered locally, never from a public image URL.
- Task dependency: optional notifications Pillow>=10.4,<14 in backend/pyproject.toml. `backend/.venv/bin/python -m pip install ...` failed because this uv-created venv has no pip; existing `uv pip install --python backend/.venv/bin/python 'Pillow>=10.4,<14'` installed only Pillow13.0.0. No other packages changed.
- Local real-data preview /tmp/prop51-designed-card.png1080×1957 inspected visually: all five NBA markets, correct line sources and record counts, no overlap, no icons, prominent Recommended pick. `... app.cli telegram preview --date 2026-11-01 --game-id espn:401909903` →previewsent using sendPhoto, Telegram message60; stored receiptstatussent and sourceiconcheckfalse. Caption prominently identifies past-game/test. Actual Telegram display/read status not verified independently beyond accepted upload.
- Sender22 tests passed,0.40s; new PNG/image-size/multipart/no-icon/source-immutability and render-before-claim failure checks. Existing at-most-one-claim receipt behavior retained for photo sends; live keys unchanged for style-only updates. `python3 scripts/check_project.py` and `git diff --check` passed. No website UI changed, so no new browser pass; no full app readiness claim.


## NFL live setup — Codex, 2026-11-02 22:14Z

- Commands: `python3 scripts/check_project.py` passed; `git diff --check` clean. `cd frontend && npm run build` passed
  (existing large-chunk warning). NFL/eval/API/Telegram/freshness/release/market focused suite116 passed12.39s;
  NFL live+storage34 passed1.34s (includes populated quote-table migration with referencing forecasts, preserved IDs,
  full foreign-key check, restored enforcement and append-only triggers). Final NFL live10 passed0.60s, including the
  corrected first-window state. Baseline NFL suite66 passed2.65s. Earlier combined suite134 passed27.61s.
- Full required command launched: `OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python scripts/check_project.py --ready`
  → frontend typecheck/build passed; backend suite still running at this recording (`/tmp/nfl-ready.log`, session49660).
  It is NOT a passing whole-app readiness result. Prior unrelated research/sandbox/ML failures are documented above.
- Live source (scratch, then first NFL live-store initialization): guarded NFL refresh imported71 November games,
  updated12, collected96 retrieval-stamped complete market quotes. Actual one-shot frozen test completed, report saved
  separately; no historical predictions inserted in live ledger. NFL schema33; live NBA/NHL remain31.
- Runtime release guard8539513: schema33/33, problems[]; NFL launchd runs22:10:39,22:11:40,22:12:40,22:13:40Z, exit0,
  stderr empty, idle/not_due, zero forecasts. The first run preceded the small first-window display fix; later runs use
  8539513. No natural live forecast/settlement or Telegram delivery yet observed (first window23:15Z).
- Read-only HTTP probe `/tmp/nfl-api-smoke.py`: all200 for NFL meta,Nov2 slate,CHI@SEA detail,record,freshness plus NBA/NHL
  meta; NFL active,fresh,5 upcoming markets,zero live grades. Source and delivered record identities match in tests.
- Browser `http://127.0.0.1:5198/?sport=nfl`:1280px desktop and390×844 phone; actual API-backed game day, matchup,
  performance and history navigation; five markets, honest empty live records, separate frozen-test details, actual
  forecast-window time18:15EST. Scroll width390/viewport390 on phone record and matchup pages;1280/1280 desktop.
  Screenshots `/tmp/prop51-nfl-desktop.png`, `/tmp/prop51-nfl-phone.png`; viewport reset; NFL Game Day left open.
- Active publisher restarted after its old loaded migrator rejected the newly added parent-table rebuild for an NHL
  preview copy. New publisher22:13:43Z: error null,NHL published. NBA/NHL live stores and jobs were not migrated/released.
- Limits: pregame forecasts, not in-play probability updates; injuries/weather and NFL daily research unavailable;
  score markets informational; laptop must be awake/online. Activation/operations/rollback: docs/NFL_LIVE.md.

- Full-readiness follow-up22:17Z: autoresearch failures observed; first reproduced in isolation (not an NFL test):
  `test_the_bar_is_the_best_existing_member_and_a_real_fit_is_kept_only_where_it_beats_it` → KeyError`markets`; trial
  JSONL records candidate exit71,`sandbox-exec: sandbox_apply: Operation not permitted`, matching prior environmental
  failure. `/tmp/nfl-ready-failure-probe.log`; full run continues, no full-suite pass claimed. Telegram unattended
 22:13:07/22:14:18/22:15:27Z reports NFL eligible0/statusok; existing NHL sends remain deduplicated.


## Screenshot warning diagnosis — 2026-11-02 22:22Z
Safari screenshot URL5194/games/espn:401909286 used old API8832, whose same-game/ranked responses omit percentage_history.
Current API8841 returns allfive exact records. Switched existing Safari tab through UI to
http://127.0.0.1:5198/games/espn%3A401909286?sport=nba; AX confirms warning absent and total records including this game:
52.0 winner1–0;53.8 spread0–1;60.2 total1–1;50.1 home3–2;51.4 away1–2. No code/data changes; phone checks not rerun for
navigation-only correction. Screenshot capture unavailable(noWindowsAvailable), desktop AX verification succeeded.

## Typography refresh — 2026-11-03
App5198/game espn:401909286, computed root Avenir Next stack; desktop1280/1280 and phone390/390 scroll width. Font screenshot saved /tmp/prop51-app-font-desktop.png, /tmp/prop51-app-font-phone.png; viewport reset. Frontend npm run build passed, Telegram pytest22 passed0.42s, structure guard and diffcheck passed. Telegram preview /tmp/prop51-card-new-font.png rendered real saved past-game data, no test message sent.

## Barlow + Inter trial — 2026-11-03
Bundled TTF+OFLassets; npm run build passed(607ms), Telegram23passed0.56s, structure/diffcheck passed. On5198/game espn:401909286 browser confirms both fonts loaded; rootInter/h1BarlowCondensed; desktop1280/1280 and phone390/390 scrollwidth,viewportreset. /tmp/prop51-barlow-app.png and /tmp/prop51-barlow-inter.png(card real past-game example) inspected. No data/model changes or test notifications.

## European soccer data milestone — 2026-11-07 (Claude Opus 5.5)
Scope: fixtures, results and matchup pages for eng.1, esp.1, ger.1, ita.1, fra.1, uefa.champions, uefa.europa. Data only:
no soccer forecast, odds, injury or accuracy exists or is shown. Nothing written to `data/`.

Commands (repository root; `S` = a scratch data directory, here the session scratchpad `appdata/`):
- Store + real sample: `cd backend && NBA_DATA_DIR=$S OMP_NUM_THREADS=1 .venv/bin/python -m app.cli soccer migrate`, then
  `... soccer ingest --from 20261025 --to 20261109 --sleep 0.5` (all seven; 112 day requests, 0 failed) and
  `... soccer ingest --competition uefa.champions --from 20250311 --to 20250312`, `--competition uefa.europa` for 20250417
  and 20250521 (knockout days with extra time / penalties). `... soccer coverage` → 179 matches, 116 raw responses.
- Repeat ingestion on real data (`--competition eng.1 --competition uefa.champions --from 20261104 --to 20261105`):
  matches 179→179, result revisions 147→147, raw responses 116→116, raw fetch log 116→120.
- API: `NBA_DATA_DIR=$S NBA_API_MODE=real .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8850`
- Web: `cd frontend && API_PROXY_TARGET=http://127.0.0.1:8850 npx vite --host 127.0.0.1 --port 5210 --strictPort`
- URLs: http://127.0.0.1:5210/?sport=soccer[&competition=uefa.champions][&date=YYYY-MM-DD], match pages
  /games/soccer:<competition>:<ESPN event id>; API /api/soccer/meta|matches|matches/{id}.

Real statuses observed in the sample: STATUS_FULL_TIME, STATUS_FINAL_AET, STATUS_FINAL_PEN, STATUS_SCHEDULED,
STATUS_FIRST_HALF, STATUS_HALFTIME (in progress via ESPN `state`). Postponed/cancelled/abandoned/suspended mappings are
by name and were not observed. ESPN gives no per-half scores and leaves `neutralSite` unset (stored as unknown).

Browser (Claude-in-Chrome, macOS Chrome), desktop 1440 px wide:
| Flow | Result |
|---|---|
| `?sport=soccer&date=2026-11-07`: Premier League cards, Fulham 1–1 Newcastle tagged "FULL TIME · DRAW", Draws metric, "predictions not available" notice, ESPN logos | Pass |
| Match page (Fulham v Newcastle): score, 90-minute result Draw, extra time not played, no shoot-out, venue, neutral site "not reported", same-competition last results with W/D/L | Pass |
| Header competition picker → Champions League from a match page: returns to UCL Game day, default date Nov 4, League Phase tags | Pass |
| Previous-day step (Nov 4 → Nov 3) | Pass |
| UCL 2025-03-12: Atlético 1–0 Real Madrid "AFTER EXTRA TIME · PENS 2–4", Atlético not bolded, "Real Madrid went through the tie (aggregate 2–2)" | Pass |
| That match page: 90-minute result "Not available", shoot-out 2–4 Real Madrid, qualifier "(the tie, not this match)", source note | Pass |
| Performance (Europa League) and History (Serie A): explicit not-available text, stored-match counts, "not full-season coverage" | Pass |
| La Liga 2026-12-25 (never requested): "has not been requested … matches may exist"; previous match-day link | Pass |
| Unknown match id: 404 error state with Try again | Pass |
| League toggle Soccer → NBA → Soccer: competition cleared, NBA shows its 503 "No NBA data store found" error (no NBA store in the scratch dir), back to Premier League | Pass |
| API stopped (own PID): "API NOT REACHABLE" banner + error state; restarted, Try again reloads Ligue 1 | Pass |
| Console errors on soccer pages | None |

Phone: Chrome's minimum window gave a **500 px** CSS viewport, not 390. At 500 px the first check found horizontal overflow
(scrollWidth 634: the 800 px rule `header-tools { nowrap }` plus the new picker). Fixed: soccer header wraps with the
picker on its own row. Rechecked Game day (Europa League) and the extra-time match page (Man Utd 5–4 Lyon, 90-minute result
not available, aggregate 7–6): scrollWidth 500 = clientWidth. 390 px itself: **unverified**.

Not verified / gaps: loading spinner not captured (shared AsyncView); NFL/NBA/NHL screens not exercised with data (copying
the preview NFL store failed twice with "unable to open database file"; not retried); no refresh job, so soccer data is only
as fresh as the last manual `soccer ingest` (in-progress matches stay "IN PROGRESS" until re-imported); `scripts/dev.sh`
does not copy `soccer.sqlite` into its snapshot (scripts/ out of scope), so the dev launcher shows the soccer 503 state;
ESPN date-parameter time zone and historical coverage beyond the requested days not established.
