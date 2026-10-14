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

## Run — one command (development app)
```sh
export NBA_DEV_DATA_DIR="$HOME/nba-predictor-dev-copy"   # one development directory; set it the same way every time
sh scripts/dev.sh --check-paths                          # optional: only validates the paths; nothing is written
sh scripts/dev.sh                                        # from the repo root; Ctrl-C stops it
```
Then open **http://127.0.0.1:5173/**. The script copies the live stores (`data/`, read-only backup) into
`$NBA_DEV_DATA_DIR` (default `~/nba-predictor-dev-data` when unset), migrates the copy to this checkout's schema, starts
the API (:8000) and web app (:5173), and waits until both answer (or prints the failing log). Results show the copy's
timestamp. Training status reads the live `data/` folder directly, read-only. An earlier dev server of this checkout on
those ports is replaced; anything else on them is left alone and reported. The installed release app is separate
(http://127.0.0.1:4173/, launchd `com.nba-predictor.web`/`api`) and is never touched by this script.

**Development data directory.** The script deletes and overwrites files in this directory, so it only reuses one that it
created itself. That directory holds a `.nba-dev-data` marker file. An existing directory without the marker is refused
and left exactly as it is, even an empty one. It could be your home folder, a backup or another checkout's `data/`, and
the script cannot tell. The first start at a path that does not exist yet creates and marks the directory. Every later
start, `--check-paths` and refresh must use the same `NBA_DEV_DATA_DIR`, and then reuses that marked directory. Without
the variable, the script checks the default `~/nba-predictor-dev-data` instead. If that folder exists without a marker,
as an older one does, the script refuses. Choose a new, nonexistent path only to start fresh on purpose. An older,
unmarked development folder is neither adopted nor deleted: remove it yourself if you no longer need it.
The development directory itself must not be a symlink, and naming it with a trailing `.` or `..` is also refused.
Symlinks in its parent path (for example macOS `/tmp`) are allowed. The resolved path still has to pass the separation
checks: it must not be inside `data/`, and `data/` must not be inside it. A directory is also refused if it contains a
symlink anywhere, or if a log, store or `SNAPSHOT_AT` in it is hard-linked.

**Snapshot, not live.** The development app shows a copy taken at startup (`SNAPSHOT_AT`), not live data. Every start
replaces the stores and `research/`, `raw/` and `eval/` with fresh copies of `data/`. Refresh by restarting with the
same `NBA_DEV_DATA_DIR`. The live `data/nba.sqlite` is required: without it the script stops before the path checks
and before changing anything. If `data/nhl.sqlite`, `research/`, `raw/` or `eval/` is missing, its earlier development copy is
removed instead of being kept under a new timestamp. Other files in the directory (such as the app's `reports/`) are
not touched.

**Limits.** The marker protects against misconfiguration; it is not a security boundary. Path and ownership validation
runs at three checkpoints, not before every operation: before anything is stopped or written, again just before the
copy (once the directory exists, newly created or reused), and again after the copy, before migrate and the servers.
None of these checks is atomic with the step that follows it. If another process replaces the directory, or one of its
parts, between a check and its use, that is not detected. Do not move or relink these directories while the script
starts.

## Run manually (two terminals, from the repo root)
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
| `GET /api/sources` | Freshness of schedule/results, odds, forecasts, NBA official injury reports, NHL injuries (newest ESPN snapshot), the NBA player box file and the canonical every-game ledger; an input without data is `missing` or `stale`, never left out |
| `GET /api/games?date=YYYY-MM-DD` | Games on an Eastern date: prediction (or a `forecast_note` explaining why none), market, result |
| `GET /api/games/{game_id}` | Detail plus point-in-time team stats as of the game's cutoff (404 if unknown) |
| `GET /api/predictions?run_type=live\|backtest&date_from&date_to&team&min_confidence&limit` | History, latest version per game (422 if `date_from > date_to`). `min_confidence` (0.5–1) keeps picks whose model probability for the predicted winner, max(p_home, 1 − p_home), is at least that value. Each row has `pick` and `confidence`; `record` gives the selected count and the settled win/loss record (unscored games never count) |
| `GET /api/games?date=…&policy=pregame\|near_tipoff&min_confidence` | The slate. `selection` gives the counts, and each game's `slate_pick` is its pick under the chosen policy only (never the other forecast), with `meets_confidence` |
| `…/predictions` and `…/performance` `&policy=pregame\|near_tipoff` | Live forecasts only: the pre-game archive of the discontinued daily job, some made several days before the game (API compatibility default) or the two-hour record (frontend default); never mixed (422 with `run_type=backtest`). Game payloads carry an optional `near_tipoff` object next to `prediction` |
| `GET /api/performance?run_type=live\|backtest` | Live scoring, or the final-test backtest with baselines. Never mixed |
| `GET /api/training-run?sport=` | Adaptive training state: process holding the training lock (via `lsof`, no lock taken), artifacts written by a running run, last run / success / failure, versions trained but not forecasting, and the versions behind the latest live forecasts. Reads `NBA_LIVE_DATA_DIR` (default: the data dir) read-only; no progress percentage or ETA |

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
.venv/bin/python -m app.cli score-near-tipoff         # score near-tipoff forecasts (read-only; separate from the pre-game archive)
.venv/bin/python -m app.cli boxscores    # possession refresh: ESPN summaries for recent finals lacking a complete box (max 30/run)
.venv/bin/python -m app.cli shadow-predict   # archived protocol; recording discontinued
.venv/bin/python -m app.cli shadow-report    # archived shadow protocol report; not the new two-hour research record
.venv/bin/python -m app.cli injuries [--dry-run]   # raw NBA injury-report PDFs for game windows (pilot; needs NBA_INJURY_COLLECTION=1)
.venv/bin/python -m app.cli odds         # pregame odds snapshot: ESPN day scoreboards for games within 36 h; max once per 10 min
.venv/bin/python -m app.cli scheduler --interval-minutes 360 --max-runs 4   # bounded loop (≥30 min interval)
.venv/bin/python -m app.cli scheduler --job odds --interval-minutes 15 --max-runs 96   # 24 h of odds snapshots
```
- **Current policy:** new forecasts are generated only in the two hours before scheduled tipoff, never after start. NBA and NHL use the same rule for all supported markets. Forecasts of the discontinued daily job remain as the pre-game archive (some were made several days before the game); nightly `daily` no longer records predictions. Preseason/exhibition games remain excluded.
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
- **Adaptive system `adaptive-v1` (research; docs/ADAPTIVE_SYSTEM.md).** Both sports. Every learned member (the core walk
  members and every registry candidate, the `mlp` / `torch_mlp` / `keras_mlp` networks included) is refitted within the
  season at the first forecast of each ISO week when new eligible results exist; a learned combination over every
  compatible member is refitted on the same schedule from out-of-sample predictions (walk-forward history of the last two
  completed seasons plus the current one, with settled prospective adaptive forecasts replacing the reconstruction of the
  same game). Training runs only off the forecast path: the scheduled
  `adaptive-train` command (research-environment Python, `scripts/launchd/com.nba-predictor.adaptive-train.plist`, not
  installed) snapshots the store under the job lock, trains from the snapshot under its own training lock and publishes the
  fit records under the job lock again, skipping unless the next game's week differs from the fit in force; `daily` only
  reports whether a run is due; the two-hour cycle (`predict-now`) only loads the verified artifacts, delivers
  results that arrived after their cutoff, and records `adaptive_forecasts` (probability or an explicit `unavailable` row
  with the reason; contributing members, weights, missing members, training cutoff, weight-update time, bookmaker quote at
  the same line or "unavailable", pick / no pick under the research selector's unchanged thresholds). A failed refit keeps
  the previous valid artifact in force and is logged in `adaptive_fits`. Separate identity (`adaptive-v1:<member>:<definition>`),
  separate tables; nothing replaces the primary forecast. CLI: `adaptive-train [--dry-run|--force]`, `adaptive-record`,
  `adaptive-status`, `adaptive-replay` (chronological development-season comparison; reserved test seasons never read).
  Deep networks are fitted only where torch / tensorflow are installed at their pinned versions (the research environment);
  elsewhere they are skipped with the reason. UI: Game page "Adaptive combination" card, Performance → research details →
  "Adaptive system" card. API: `/api/research/adaptive`, `/api/research/adaptive/game/{id}`.
- Odds (free, no key): each snapshot appends DraftKings moneylines with their retrieval time. The last quote confirmed
  before tipoff is our own closing snapshot (`coverage` → `closing_lines`, with minutes before tipoff). It is not a
  bookmaker-published close, and it exists only for games snapshotted while the collector was running.

## Pinned release: scheduled jobs and the always-on app
Everything that runs unattended uses the release worktree `~/nba-predictor-live` (branch `release`), never this development
checkout. It lives outside `~/Desktop` on purpose: macOS asks before a background process may read Desktop, and the web
service's `node` hung waiting for that permission. Only the data folder stays here (`NBA_DATA_DIR`). The release has its own
Python environment (`~/nba-predictor-live/backend/.venv`), built from `backend/requirements-release.txt` (exact versions of this
checkout's venv), so installing packages here never reaches it.

| launchd label | What | Where |
|---|---|---|
| `odds`, `nhl-odds`, `injuries`, `daily`, `nhl-daily` | scheduled jobs (`ProcessType` Standard, not deferred) | `release-check` guarded |
| `api` | read-only API, always on (KeepAlive) | http://127.0.0.1:8100 |
| `web` | the built app (`vite preview`, proxies `/api` to :8100), always on | http://127.0.0.1:4173 |

With `NBA_RELEASE_GUARD=1` every scheduled command checks that the code comes from the clean release checkout and that the
store's schema equals the release's; otherwise it refuses before any request, lock or migration and records a failed job run.
Every job run records its release commit (`job_runs.detail.release`). The dev servers (:8000 API, :5173 Vite) are unaffected.

Move the release only between cycles (no game within 2 h; never mid-evening if an identity-hashed file changed):
```sh
backend/.venv/bin/python scripts/check_project.py --ready                    # 1. in this checkout, after committing
git -C ~/nba-predictor-live merge --ff-only <commit>                         # 2. move the release
uv pip sync --python ~/nba-predictor-live/backend/.venv/bin/python backend/requirements-release.txt   # only if it changed
(cd ~/nba-predictor-live/frontend && npm ci && npm run build)                # 3. rebuild the app (node from fnm v22.23.2)
launchctl kickstart -k gui/$(id -u)/com.nba-predictor.api; launchctl kickstart -k gui/$(id -u)/com.nba-predictor.web
cd ~/nba-predictor-live/backend && NBA_RELEASE_GUARD=1 NBA_RELEASE_ROOT=$HOME/nba-predictor-live \
  NBA_DATA_DIR=/Users/andry/Desktop/nba-predictor-sprint1/data .venv/bin/python -m app.cli --sport nhl release-check   # 4. and nba
```
- A release with a new migration is refused until the store is migrated deliberately from the release checkout (same
  environment as step 4, command `migrate`, each sport), between cycles and after a backup:
  `NBA_DATA_DIR=<data dir> .venv/bin/python -m app.backup --dest <backup dir>` (online SQLite backup of both stores,
  integrity-checked, with a manifest of sha256, schema version and row counts; reads the stores only).
  To undo a migration, with the scheduled jobs and the API stopped (an open connection keeps writing to the replaced file): `… -m app.backup --restore <backup dir>/<stamp> --sport nba --sport nhl`
  (refuses a manifest naming another store, a file that does not match the manifest or carries another store identity, and a store whose job lock is held; the new file is written and synced first, then the replaced files are kept as
  `*.replaced-<UTC>`). Rehearsed on copies of the live stores 2026-10-14: schema 26 → 30 in under 1 s per sport, restore
  logically identical to the original (docs/SMOKE_TEST.md).
- Roll back: `git -C ~/nba-predictor-live checkout -B release <previous commit>`, then steps 3–4. Pre-release launchd files:
  `data/backups/launchd-before-release-20261003/` (dev checkout) and `…-before-release-venv-20261003/` (shared venv, Desktop path).
- The release runs only while this Mac is awake: closing a laptop lid sleeps it. An always-on host is the planned next step.

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
The full suite also tests the optional tuning study and needs its pinned Optuna dependency, even when no research jobs are run. With the backend environment already installed, run `uv pip install --python backend/.venv/bin/python 'optuna==5.0.0'` (or the equivalent pip command) before readiness checks. This installs the existing research dependency; it does not enable live training.

```sh
python3 scripts/check_project.py                           # structure guard
backend/.venv/bin/python scripts/check_project.py --ready  # + typecheck, build, pytest
```

### Explore agreement and confidence together

On **Performance**, use **Explore model agreement + confidence ranges**. Choose the sport and market, then any exact subset of 2–8 available models. Agreement is votes for the same outcome divided by the full subset size; confidence is a separate filter. You can require, for example, at least 80% agreement and 65–70% mean confidence among the agreeing models. Ties and missing members produce no pick. Upper bounds are exclusive; leave the upper bound empty to include 100%.

Historical results use saved out-of-sample forecasts. **Test joint rules on later seasons** searches agreement ranges and confidence thresholds/bands for the selected subset, freezes the choice on earlier seasons, and reports its next-season record against an earlier-selected constituent at matched coverage. This does not search every possible subset automatically, retrain models, or change live recommendations. These reused historical seasons provide exploratory evidence, not a fresh independent test. Historical totals/spreads use model-defined lines, not bookmaker lines.

**Recorded live** derives the same classifications from the stored two-hour cohorts and exact cohort-linked model inputs. Method definitions and member versions have separate records; missing models, voids and pending outcomes remain visible. Use exact-line, date and definition filters to inspect coverage. New filters are retrospective classifications, not retroactively recorded bets or recommendations.
