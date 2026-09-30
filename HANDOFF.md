# Current handoff

Status: Milestone 4 implemented and reviewed; odds snapshots and local launchd automation configured. Courtside frontend redesign complete. Git baseline d96db86. Free sources only (ESPN; no keyed odds API, user decision 2026-09-30). No deployment, no purchases.
Current owner: User (prospective daily operation). Final test was run once (2026-09-30T14:40:35Z); Elo is the frozen primary model.

## Established (M1–M3, reviewed)
- backend/app: SQLite migrations, raw cache, ESPN adapter, idempotent ingest, append-only ledger (REPLACE-proof), odds, CLI.
- M3: walk-forward backtester (00:00 ET cutoff, availability guard), Elo primary (k15/H50/carry .75/MOV) in the sealed
  models/frozen_decision.json. Final test on 2,643 games: Brier 0.2105 vs home-rate 0.2475. Logistic is descriptive only.
- Ledger: 27,744 backtest rows under dataset 67ea40d5f04f241e (the all-seasons hash). Do not relabel or duplicate them.
- Limits: ESPN is unofficial (terms unverified); availability assumed at tipoff + 4 h; no turnovers; no historical odds.

## M4 changes (Claude)
- Migration 0003 `job_runs`. `app/live.py`: live Elo forecasts for eligible (regular/play-in/postseason) scheduled games
  whose cutoff is within 7 days. The model state uses results with result_available_at <= now; forecasts go to the ledger as run_type=live,
  cutoff = 00:00 ET on the game date (the trigger requires generation before it). The idempotency key includes the dataset
  fingerprint: an unchanged rerun returns the stored row, and a new result creates a new version. Shown and scored = latest version
  before the cutoff. `score` is read-only.
- `app/jobs.py` + CLI `refresh | predict | score | daily | scheduler`: refresh covers ESPN months (yesterday → +8 days; up to 3
  months), with up to 3 attempts per month and backoff; ≥ 30 min between successful refreshes (quota), each run logged. `daily` still predicts if
  the refresh fails (the status stays visible). The scheduler is a bounded loop (max_runs, interval ≥ 30 min). No system cron installed.
- API contract v2 (`schemas.py`): `mode` real|demo; prediction optional with `forecast_note`; prediction `cutoff_at`;
  market provenance (price_label, retrieved_at, nullable quote_time); team stats = record/last-10/points/rest, point-in-time
  at the cutoff (ORtg/DRtg dropped: no possessions); `run_type` filter on history/performance; performance `baselines`.
- `app/providers/{real,demo}.py`; main.py picks by `NBA_API_MODE` (default real; 503 with guidance if there's no store).
  Real: prediction = latest live, else the primary model's backtest (labelled), else none plus a note (preseason, warm-up,
  not yet, missed). Backtest performance = test seasons only, from the ledger. API DB connections are per request
  (`check_same_thread=False`: FastAPI may run the dependency and the endpoint on different threads).
- Frontend: mode-driven header/banner (real vs demo); Live/Backtest badges with explanations; Courtside charcoal/amber redesign, responsive sidebar/top-nav, daily-slate court hero and refreshed matchup cards (Codex). Live/Backtest toggle on
  Performance and History (never mixed); game cards and detail handle missing forecasts; new stats rows; dates with years.
- Tests: tests/test_live.py (13): full lifecycle (forecast → idempotent reruns → new version → cutoff → result → score),
  only-available results, post-cutoff rejection, refresh retry/fail/quota, month window, bounded scheduler, CLI
  failure exit, real-API labels/notes/market, point-in-time stats, separate run types, meta. Demo API tests updated to v2.

## Odds collection (Claude, 2026-09-30; user: free sources only, no historical odds, collect from now on)
- ESPN day scoreboard (`espn.fetch_day`, `ingest.ingest_day`); `jobs.odds_snapshot` + CLI `odds` (days with a game within
  36 h, max 2 requests, ≥10 min apart, retries shared with refresh via `_fetch_each`); `scheduler --job odds` (≥10 min).
- Migration 0004 rebuilds `job_runs` to allow job 'odds' (rows kept; tested). `coverage.closing_summary` / `LAST_PREGAME_SQL`:
  last `current` quote confirmed via raw_fetches at or before tipoff; gap reported; a scheduled-status quote after tipoff is excluded.
- Not changed: live performance still compares against the last quote before the forecast cutoff (forecast-time market).
  A closing-snapshot comparison is not in the API yet.
- Checks: `--ready` exit 0, 94 passed, typecheck/build passed. Real: migrate applied 0004; `odds` skipped (no game within 36 h);
  one real `ingest_day 20261020` stored 6 DraftKings quotes.

## Local scheduling (Codex, 2026-09-30)
- Installed user LaunchAgents: odds at load + every 15 min; daily refresh/predict/score at 22:00 local. Source plists:
  `scripts/launchd/`; logs: `data/odds-launchd.log`, `data/daily-launchd.log`.
- Runs only while the Mac is awake and the checkout remains at its configured absolute path. To remove, `launchctl bootout`
  the two labels from `gui/501` and delete their plist files in `~/Library/LaunchAgents`.
- Verified: both agents loaded; odds agent has a 900-second interval and daily has a 22:00 calendar trigger. Initial odds run
  exited 0 and skipped correctly because no game was within 36 hours. Daily first fires at 22:00.

## Verification (actual)
- `--ready` passed: 88 tests, typecheck, build.
- Real `daily` run: refresh success (2 requests, 52 new odds quotes), predict 0 eligible (the season starts 10-20; preseason is excluded),
  score 0. API backtest performance reproduces the final test exactly (2,643; 0.21052; home-rate 0.24752).
- Review recheck: both API health URLs returned real mode; live performance returned 0 games; Oct 20 endpoint returned 3 openers with market lines and no forecast.
  Current SQLite ledger has 0 live rows. The timestamp-tie defect was reproduced with an in-memory SQLite case.
- Browser: 8 routes at 1280 px and 390 px (no horizontal scroll), demo mode checked; details in docs/SMOKE_TEST.md. This review opened a preview but did not independently inspect its rendered UI.
- Fixed during verification: a 4 s history query (now 0.1 s); dates missing years; an inaccurate toggle label.

## Open items / review focus
- Fixed and reviewed (same-second live versions): `LATEST_LIVE_SQL` in app/live.py selects one row per game,
  ordered by generated_at DESC, prediction_id DESC. The regression checks game detail, history, performance and scoring.
  Independent `backend/.venv/bin/python scripts/check_project.py --ready`: exit 0, 89 passed, typecheck/build passed (one Starlette/httpx deprecation warning).
- No live forecast exists yet (the first is expected about 2026-10-13, when openers enter the 7-day window). Not faked.
- Live cutoff = midnight ET before game day, so a forecast needs `daily` run the evening before; later results that
  night are not used (same as the backtest).
- Rest days use fractional tipoff gaps; postponement reschedules are not modelled. Odds have no quote time.
- The real provider does per-game queries (fine locally); revisit if hosted. The team/date caches key on db file + game
  count + latest game updated_at, so they self-refresh on ingest from another process; only a team rename without a game
  change shows a stale label until a game updates (labels only, not forecasts).
- Next: start odds collection (cron/launchd every 15 min) before preseason 10-03; run `daily` each evening from about 10-13, then verify the first real forecast lifecycle (forecast before cutoff → result → score)
  in the browser. Real API recheck returned mode=real and live sample_size=0; no live forecast exists to inspect yet. Consider shadow logistic forecasts later.

Frontend design verification: docs/SMOKE_TEST.md (desktop + 390px browser checks; no horizontal overflow). Local servers are running for preview.

References: PLAN.md; README.md (operation); docs/{EVALUATION_PLAN,BASELINE_REPORT,DATA_SOURCES,SMOKE_TEST,MILESTONE2_REVIEW,MILESTONE3_REVIEW}.md.
