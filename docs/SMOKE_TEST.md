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
