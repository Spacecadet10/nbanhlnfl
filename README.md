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
| `GET /api/predictions?run_type=live\|backtest&date_from&date_to&team&limit` | History, latest version per game (422 if `date_from > date_to`) |
| `GET /api/performance?run_type=live\|backtest` | Live scoring, or the final-test backtest with baselines. Never mixed |

## Daily operation (milestone 4)
Run from `backend/`:
```sh
.venv/bin/python -m app.cli daily        # refresh → predict → score; exit code 1 if a job failed
.venv/bin/python -m app.cli refresh      # ESPN months around today; retries 3×; max once per 30 min (--force to override)
.venv/bin/python -m app.cli predict      # live Elo forecasts for eligible games whose cutoff is within 7 days
.venv/bin/python -m app.cli score        # score the latest live forecasts against stored results (read-only)
.venv/bin/python -m app.cli boxscores    # possession refresh: ESPN summaries for recent finals lacking a complete box (max 30/run)
.venv/bin/python -m app.cli shadow-predict   # sealed challenger's shadow forecasts; a dry run unless --record
.venv/bin/python -m app.cli shadow-report    # shadow season coverage and scores; --final applies the sealed rule once, after the play-in
.venv/bin/python -m app.cli odds         # pregame odds snapshot: ESPN day scoreboards for games within 36 h; max once per 10 min
.venv/bin/python -m app.cli scheduler --interval-minutes 360 --max-runs 4   # bounded loop (≥30 min interval)
.venv/bin/python -m app.cli scheduler --job odds --interval-minutes 15 --max-runs 96   # 24 h of odds snapshots
```
- A game on Eastern date D has its cutoff at 00:00 ET on D. Live forecasts must be recorded before it, so run `daily` at
  least once in the evening before each game day. Preseason and exhibition games are not forecast.
- Reruns are safe: unchanged data returns the stored forecast. New results create a new forecast version, and the latest
  version before the cutoff is shown and scored. Every job run goes to `job_runs`, and the app shows the last successful refresh.
- On this Mac, user LaunchAgents `com.nba-predictor.odds` and `com.nba-predictor.daily` run odds at load and every
  15 minutes, and run `daily` at 22:00 local time. Their source plists are in `scripts/launchd/`; logs are
  `data/odds-launchd.log` and `data/daily-launchd.log`. They run only while this Mac is awake and this checkout
  remains at the configured path. To stop/remove them, run `launchctl bootout gui/501 ~/Library/LaunchAgents/com.nba-predictor.odds.plist`
  and the same command with `com.nba-predictor.daily.plist`, then remove those two plist files.
- Shadow challenger (`logistic_v2`, sealed in `backend/app/models/shadow_decision.json`): `daily` also runs `boxscores` and records
  shadow forecasts only when `NBA_SHADOW_RECORDING=1` is set for the daily job. It is off by default in code and enabled on this Mac
  since 2026-10-01 through `EnvironmentVariables` in `scripts/launchd/com.nba-predictor.daily.plist` (remove that key and reload the
  agent to turn it off). The Elo forecast, scoring and the app are the same either way. Shadow rows share the ledger (run_type live, their own model_version) and are never shown in the app.
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
