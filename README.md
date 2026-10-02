# NBA Prediction App

Responsive NBA prediction app backed by reproducible statistical models. Milestone 4: the app shows real NBA
schedules and results (ESPN), live Elo forecasts recorded before each game, and the held-out backtest. Accuracy is
measured, never promised, and there is no betting-market edge claim.

Start with [PLAN.md](PLAN.md). Before each work session, read [HANDOFF.md](HANDOFF.md).
Shared collaboration rules are in [AGENTS.md](AGENTS.md); Claude's entry point is [CLAUDE.md](CLAUDE.md).

## Layout
- `backend/` — FastAPI service (`app/main.py` entry point, `app/schemas.py` API contract,
  `app/fixtures.py` the only demo dataset, `tests/`).
- `frontend/` — React + TypeScript + Vite app (`src/api/client.ts` is the only API client).
- `docs/SMOKE_TEST.md` — manual/browser verification evidence.

## Requirements
Python 3.10+ (tested 3.12), Node 20.19+ or 22.12+ with npm (tested Node 22 / npm 10).

## Setup (once)
```sh
# Backend (either uv or plain venv)
cd backend
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
#   or: python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cd ..

# Frontend
cd frontend && npm ci && cd ..
```

## Run (two terminals, from the repo root)
First run only: build the data store (see "Data store" below: `migrate`, then `ingest`).
```sh
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev -- --host 127.0.0.1 --strictPort
```
- App: http://127.0.0.1:5173/
- API: http://127.0.0.1:8000/api/health (interactive docs: http://127.0.0.1:8000/docs)

The API serves **real data** by default (`NBA_API_MODE=real`: SQLite store + prediction ledger). For the synthetic demo,
start the backend with `NBA_API_MODE=demo`; every screen is then labelled Demo. Real mode returns 503 with instructions
if the data store is missing or unmigrated.
The Vite dev server proxies `/api` to `http://127.0.0.1:8000`; set `API_PROXY_TARGET` to point elsewhere.
The backend allows CORS from the Vite origin; override with `CORS_ORIGINS` (comma-separated).

## API contract v2 (`mode`: real | demo; prediction `run_type`: live | backtest | demo)
| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Health check and mode |
| `GET /api/meta` | Next game date, available dates, teams, primary model, last successful refresh |
| `GET /api/sources` | Freshness of schedule/results, odds, and forecasts |
| `GET /api/games?date=YYYY-MM-DD` | Games on an Eastern date: prediction (or a `forecast_note` explaining why none), market, result |
| `GET /api/games/{game_id}` | Detail plus point-in-time team stats as of the game's cutoff (404 if unknown) |
| `GET /api/predictions?run_type=live\|backtest&date_from&date_to&team&min_confidence&limit` | History, latest version per game (422 if `date_from > date_to`). `min_confidence` (0.5–1) keeps picks whose model probability for the predicted winner, max(p_home, 1 − p_home), is at least that value. Each row has `pick` and `confidence`; `record` gives the selected count and the settled win/loss record (unscored games never count) |
| `GET /api/games?date=…&policy=pregame\|near_tipoff&min_confidence` | The slate. `selection` gives the counts, and each game's `slate_pick` is its pick under the chosen policy only (never the other forecast), with `meets_confidence` |
| `…/predictions` and `…/performance` `&policy=pregame\|near_tipoff` | Live forecasts only: the night-before archive (API compatibility default) or the two-hour record (frontend default); never mixed (422 with `run_type=backtest`). Game payloads carry an optional `near_tipoff` object next to `prediction` |
| `GET /api/performance?run_type=live\|backtest` | Live scoring, or the final-test backtest with baselines. Never mixed |

## NBA + NHL and markets (contract v3)
Every endpoint takes `sport=nba|nhl` (default `nba`). Each sport has its own store (`data/nba.sqlite`, `data/nhl.sqlite`); a
missing store or a store labelled with the other sport returns 503, never the other sport's data. Demo mode is NBA-only.
| Endpoint | Purpose |
|---|---|
| `GET /api/meta?sport=` | Also lists `sports`, `markets` (title, availability, settlement notes and limitations) and `score_unit` |
| `GET /api/games?sport=&date=&market=&policy=&min_confidence=` | With `market` ≠ winner, each game carries `market_forecast` for that market and policy only |
| `GET /api/games/{id}?sport=` | `market_forecasts`: every market × policy, each at its exact line |
| `GET /api/market-predictions?sport=&market=&policy=…` | Market history; record = W–L–push, voids, pending |
| `GET /api/market-performance?sport=&market=&policy=` | Multi-outcome Brier/log loss, line sources, bookmaker comparison at the same line, NHL one-time test |

Markets and settlement: [docs/MARKETS.md](docs/MARKETS.md). Plan and status: [docs/MULTISPORT_PLAN.md](docs/MULTISPORT_PLAN.md).
NHL results: [docs/NHL_BASELINE_REPORT.md](docs/NHL_BASELINE_REPORT.md). Run from `backend/`:
```sh
.venv/bin/python -m app.cli --sport nhl ingest-nhl --from-season 2016 --to-season 2027   # NHL stats season lists (13 requests)
.venv/bin/python -m app.cli --sport nhl daily      # refresh (1 request) → Elo winner forecasts → market forecasts → score
.venv/bin/python -m app.cli --sport nhl odds       # ESPN NHL day scoreboards: moneyline/puck line/total quotes, games within 36 h
.venv/bin/python -m app.cli --sport nhl predict-now [--dry-run]   # near-tipoff winner + market forecasts (manual)
.venv/bin/python -m app.cli --sport nhl score-markets              # settle and score market forecasts (read-only)
.venv/bin/python -m app.cli markets [--dry-run]    # NBA market forecasts (spread, total, team totals); also --sport nhl
```
Run `odds` before `daily`/`markets` so forecasts use the bookmaker's line where one is quoted; otherwise the line is model-only and labelled.
NHL and NBA-market jobs are **not scheduled**: plists are prepared in `scripts/launchd/` (`nhl-daily`, `nhl-odds`, `nba-markets`) for review.

## Daily operation (milestone 4)
Run from `backend/`:
```sh
.venv/bin/python -m app.cli daily        # refresh → predict → score; exit code 1 if a job failed
.venv/bin/python -m app.cli refresh      # ESPN months around today; retries 3×; max once per 30 min (--force to override)
.venv/bin/python -m app.cli predict      # guarded two-hour forecasts; also records supported markets and research candidates
.venv/bin/python -m app.cli score        # score the latest live forecasts against stored results (read-only)
.venv/bin/python -m app.cli predict-now [--dry-run]   # near-tipoff forecasts for games tipping off within 2 h (refreshes data first)
.venv/bin/python -m app.cli score-near-tipoff         # score near-tipoff forecasts (read-only; separate from the night-before record)
.venv/bin/python -m app.cli boxscores    # possession refresh: ESPN summaries for recent finals lacking a complete box (max 30/run)
.venv/bin/python -m app.cli shadow-predict   # archived protocol; recording discontinued
.venv/bin/python -m app.cli shadow-report    # archived shadow protocol report; not the new two-hour research record
.venv/bin/python -m app.cli injuries [--dry-run]   # raw NBA injury-report PDFs for game windows (pilot; needs NBA_INJURY_COLLECTION=1)
.venv/bin/python -m app.cli odds         # pregame odds snapshot: ESPN day scoreboards for games within 36 h; max once per 10 min
.venv/bin/python -m app.cli scheduler --interval-minutes 360 --max-runs 4   # bounded loop (≥30 min interval)
.venv/bin/python -m app.cli scheduler --job odds --interval-minutes 15 --max-runs 96   # 24 h of odds snapshots
```
- **Current policy:** new forecasts are generated only in the two hours before scheduled tipoff, never after start. NBA and NHL use the same rule for all supported markets. Night-before forecasts remain an archive; nightly `daily` no longer records predictions. Preseason/exhibition games remain excluded.
- Existing periodic `odds` jobs now refresh and record winner, supported market and research-model forecasts when games enter that window (NBA every 15 minutes; NHL every 30). The Mac must be awake, logged in, and the checkout must stay at its configured path. The daily jobs maintain schedules/results and score records. No extra scheduler is needed.
- `predict-now` (also `predict`, `markets`, `agreement`) performs the same guarded refresh-and-forecast cycle manually. Use `--sport nhl` before the command for NHL. Stale results, unresolved earlier results, started games, and calculations finishing after tipoff are refused. Retries retain the original prediction and quote; changed inputs/lines receive separate immutable records.
- Frozen primary Elo settings remain unchanged. Research agreement compares six NBA candidates (including a separately defined injury-aware candidate) or four NHL candidates. Missing/untrained/stale candidates are explicit, never replaced with an Elo vote; a partial cohort does not qualify for the agreement performance record. Winner agreement does not apply to spread/total picks.
- The previous midnight `logistic_v2` shadow protocol is discontinued by this timing change. Its sealed files/history stay intact; `shadow-predict --record` now skips. The newer two-hour research records cannot be promoted under the old rule.
- NBA availability: `availability-prepare --seed-season 2026` prepares checksum-sealed facts from the existing development report cache and downloads one free, digest-verified prior-season player file. Current player minutes come from cached finished-game summaries. The two-hour cycle refreshes injury PDFs through the existing budgeted collector. A fresh, reconciled report and complete minute history are required; otherwise the injury-aware candidate skips while other models can run. This new timing variant has no claimed accuracy improvement. `pypdf` and scikit-learn are runtime dependencies.
- Injury collection remains installed at :02/:32; source plists are in `scripts/launchd/`. Forecast cycles reuse the same request budgets and raw cache. Raw documents and every forecast version are retained. Source access is for the existing personal-research scope.
- Performance → **Confidence thresholds · Two-hour record** shows per-market/model/version results at 50–90% thresholds, with samples, coverage, pending, pushes/voids and descriptive intervals. Read-only CLI: `.venv/bin/python -m app.cli thresholds` (or `--sport nhl thresholds`). Selecting a promising threshold is research, not proof; freeze a rule before assessing future games.
- **Model agreement, combination and selection (research/shadow; docs/MODEL_SELECTION.md, results docs/COMBINATION_RESULTS.md).**
  In the same two-hour cycle, every market gets a research cohort: the primary forecast plus two extra candidates per line /
  3-way market (NBA `ridge_points`, `gbt_points`; NHL `goals_draw`, `goals_glm`) at exactly the primary's line and quote, the
  winner research models, an equal average, an out-of-fold stacker and agreement counts (`research_cohorts`; members in the
  existing ledgers). A frozen weekly selector (Monday 14:00 UTC, written by the first odds cycle after it) chooses a source /
  agreement filter / threshold per market from settled prospective cohorts only, or abstains; one market at most is recommended
  per game (`selector_decisions`, `research_selections`; all append-only and two-hour guarded). Recommendations are research
  only: never the primary forecast, never betting advice, no profitability claim. Off switch: `RESEARCH_SELECTION=0` in the job
  environment (primary forecasts unaffected). CLI: `selector-decide`, `research-report --market total`, `combination-study`
  (the one-time retrospective study; already run, do not rerun for evaluation). The UI shows it on Today (research line for the
  selected market: exact line, status, missing inputs, minutes before start),
  Game (Model selection card), History (Research recommendations) and Performance (forward research + retrospective study).
- Odds (free, no key): each snapshot appends DraftKings moneylines with their retrieval time. The last quote confirmed
  before tipoff is our own closing snapshot (`coverage` → `closing_lines`, with minutes before tipoff). It is not a
  bookmaker-published close, and it exists only for games snapshotted while the collector was running.

## Data store
SQLite at `data/nba.sqlite`, raw responses in `data/raw/` (both gitignored; override with `NBA_DATA_DIR`).
Run from `backend/`:
```sh
.venv/bin/python -m app.cli migrate                            # create/upgrade schema
.venv/bin/python -m app.cli ingest --from 201910 --to 202610   # ESPN months, 1 req/s, ~2 min
.venv/bin/python -m app.cli replay                             # rebuild rows from cache, no network
.venv/bin/python -m app.cli import-team-box --from-season 2020 --to-season 2026 --historical-backfill   # possession inputs, completed seasons
.venv/bin/python -m app.cli coverage                           # JSON coverage report
.venv/bin/python -m app.cli backtest --period validation       # baselines + candidates (validation only)
.venv/bin/python -m app.cli research-gbt                        # research only: tree candidate vs Elo and logistic_v2 (dev extra)
.venv/bin/python -m app.cli research-stacking                   # research only: learned stackers and accuracy at fixed coverage
```
Evaluation rules: [docs/EVALUATION_PLAN.md](docs/EVALUATION_PLAN.md); results: [docs/BASELINE_REPORT.md](docs/BASELINE_REPORT.md).
The reserved final test has been run once (2026-09-30), and the primary model is fixed in the sealed
`backend/app/models/frozen_decision.json`. Sources, timestamp rules, odds findings and coverage: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).
The prediction ledger (`app/ledger.py`) is append-only.

## Checks
```sh
python3 scripts/check_project.py                           # structure guard
backend/.venv/bin/python scripts/check_project.py --ready  # + typecheck, build, pytest
```
