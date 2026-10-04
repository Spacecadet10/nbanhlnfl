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
