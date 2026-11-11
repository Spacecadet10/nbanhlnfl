# NBA-C — API, UI & operations (NBA + shared shell) — read-only audit, 2026-11-07

Reviewer: NBA-C. Code audited: SNAP ($SP/snap). Stores: dev API copy $SP/work/devapi/nba.sqlite (schema 35, migrated) and
$SP/stores/nba.sqlite (schema 31, live backup 15:29 ET), both opened `immutable=1`. Dev API http://127.0.0.1:8871 (env:
NBA_DATA_DIR=$SP/work/devapi, NBA_LIVE_DATA_DIR=$SP/stores, no SNAPSHOT_AT). Own scratch: $SP/work/NBA-C/.
Disclosure: I made ONE read-only `GET http://127.0.0.1:4173/api/sources` (release app) before re-reading the "never touch
8100/4173" line. Nothing else touched the release ports; no writes anywhere outside $SP/work/NBA-C (one temp file
/tmp/claude-501/x.json was created by a curl -o and deleted immediately).

## 1. Findings (most severe first)

### HIGH-1 — Telegram delivery job runs the uncommitted dev working tree against live data, with no release guard (NEW in this form)
- Where: scripts/launchd/com.nba-predictor.telegram.plist (installed copy identical: `diff` SAME); README.md:186-190.
- Sports: NBA, NHL, NFL (it delivers all three).
- Evidence: `plutil -convert json ~/Library/LaunchAgents/com.nba-predictor.telegram.plist` → ProgramArguments
  `/Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python -m app.cli telegram run --once`, WorkingDirectory
  `/Users/andry/Desktop/nba-predictor-sprint1/backend`, Env `NBA_DATA_DIR=…/nba-predictor-sprint1/data`, no
  `NBA_RELEASE_GUARD`/`NBA_RELEASE_ROOT`. `launchctl print gui/501/com.nba-predictor.telegram` → runs = 627, last exit 0,
  run interval 60 s. So every minute, picks sent to the user's phone are computed by REPO HEAD 79ff6ec + the uncommitted diff
  (backend/app/freshness.py and db.py are modified in $SP/lead/working.diff; telegram.py → RealProvider/freshness.summary),
  i.e. code another session is editing right now, not the release a55c874. Store access is `db.connect_readonly`
  (telegram.py:267), so no migration, but the content of deliveries follows unreviewed code.
- Contradicts README.md:186-187 "Everything that runs unattended uses the release worktree `~/nba-predictor-live` …, never
  this development checkout." Also com.nba-predictor.nfl-live uses the dev venv (`…sprint1/backend/.venv/bin/python`) with
  WD `~/.codex/worktrees/nfl-live-release/…/backend` (guarded, worktree clean at 8539513; `find_spec('app')` from that WD →
  the worktree's app, not the editable install).
- HANDOFF already notes only "deep extra sits in the venv used by nfl-live/telegram launchd jobs (user decision)"; the fact
  that telegram executes working-tree code unguarded is not recorded.
- Fix (text): point telegram at the release checkout (WD + venv of ~/nba-predictor-live) with NBA_RELEASE_GUARD=1 and
  NBA_RELEASE_ROOT, or gate `telegram run` behind `release.check()`; update README table to list nfl-live/telegram.

### HIGH-2 — Nothing stops a dev-checkout write command from migrating the live stores (NEW)
- Where: backend/app/config.py:43-44 (`NBA_DATA_DIR` default = REPO/data); backend/app/cli.py:568-582 (every non-read-only
  command: `db.connect(sport=…)` then `db.migrate(conn)`); backend/app/db.py:99-121 (no target check); README.md:71-74
  ("Run manually": uvicorn from backend/ without NBA_DATA_DIR → serves live data/ with dev code).
- Sports: NBA, NHL (NFL/soccer via their own migrate paths, same default dir).
- Evidence: SNAP ships migrations through 0035 (`sqlite3 … devapi/nba.sqlite "SELECT MAX(version) FROM schema_migrations"`
  → 35); live backup is 31 (`… stores/nba.sqlite` → 31). Any of `refresh|odds|tick|predict|daily|…` run from REPO/backend
  without NBA_DATA_DIR opens REPO/data/nba.sqlite read-write and applies 0032-0035 (0035 is a PARENT_REBUILD, db.py:11).
  The release guard then refuses every scheduled job (release.py:80-84 "store schema … differs from this release's schema")
  → live collection and forecasting stop. The only protections are prose (CLAUDE.md "never migrate … data/") and
  dev.sh/devpaths (which protect the launcher only). Not reproduced (would write live data); code path read only.
- Fix (text): in the dev checkout refuse write commands when `config.data_dir()` resolves to the directory named by the
  release plists / REPO/data unless an explicit `--allow-live-store` (or NBA_RELEASE_GUARD) is set; make `db.migrate` refuse
  a store whose identity row is marked live; make README "Run manually" set NBA_DATA_DIR to a copy.

### HIGH-3 — Host sleep / stuck ticks: orphaned `running` job rows and multi-hour NBA gaps → 12 canonical forecasts missing (NEW; cross-lane NBA-A/B)
- Where: scripts/launchd/com.nba-predictor.odds.plist (no caffeinate/keep-awake; only adaptive-train uses caffeinate);
  backend/app/coverage.py:589-594 (reason text); job_runs.
- Sports: NBA (NHL likely same; not checked).
- Evidence (dev copy, immutable):
  - `SELECT run_id,job,status,started_at FROM job_runs WHERE status='running'` → 6126 near_tipoff 2026-11-04T22:27:50Z,
    6465 near_tipoff 2026-11-06T00:40:12Z, 6933 near_tipoff 2026-11-07T20:10:15Z (6933 was in progress at backup; the log
    shows the next tick at 20:34:49Z).
  - After 6126 the next job row of any kind is 2026-11-05T01:04:45Z (2 h 37 min gap); after 6465 the next is
    2026-11-06T23:38:48Z (23 h gap).
  - Games with no canonical row at all (since recording began 2026-10-16T21:01:14Z): espn:401909088, 401909090, 401909877,
    401909878, 401909879, 401909924–401909929 (Nov 5 00:30–01:00Z), 401909937 (Nov 6 03:00Z) = 12; API
    `/api/canonical/forecasts` record games 132 / forecast 120 / missing 12, all reason "not recorded: no forecast cycle ran
    within two hours of the start".
  - `pmset -g log` shows Maintenance Sleep/DarkWake cycles at 2026-11-04 17:00-17:25 ET and 2026-11-05 19:40+ ET;
    `pmset -g` → `sleep 1`.
  - Today: tick 20:10:15Z ran ~24 min (no sleep events 15:10-15:34 ET); during it no NBA refresh ran (ticks skip silently on
    the lock), live freshness went stale and the Telegram log shows NBA "source_not_fresh" 20:26–20:34Z.
- Why it matters: every-game coverage is a product rule; orphaned `running` rows are never closed, and the API reason says
  no cycle ran when one started and died.
- Fix (text): keep-awake for the tick jobs (caffeinate wrapper or pmset schedule) or document the coverage loss; close stale
  `running` rows on the next tick (status `abandoned` with the gap); record a row when a tick is skipped on the lock after
  N minutes; distinguish "cycle started but never finished" in coverage reasons. Ask lane B why near_tipoff takes 12–24 min.

### MEDIUM-4 — API describes the scheduled jobs' configuration from its own process environment (NEW)
- Where: backend/app/providers/real.py:363-366 (injuries), 387-388 (player box); release api.plist env has neither flag.
- Sport: NBA.
- Evidence: `curl :8871/api/sources` → player box note "not refreshed: the refresh job is off (NBA_PLAYER_BOX_REFRESH is not
  1)"; the one GET on :4173 returned the same text in the release app. But com.nba-predictor.daily (and adaptive-train) set
  `NBA_PLAYER_BOX_REFRESH=1`, and com.nba-predictor.injuries sets `NBA_INJURY_COLLECTION=1` while the API says "collected by
  the two-hour cycle". False operational status shown to the user.
- Fix (text): derive these notes from job_runs (last player-box refresh run, injuries job rows) rather than the API's env.

### MEDIUM-5 — Two contradictory freshness verdicts for the same source on one screen (NEW)
- Where: backend/app/providers/real.py:280-286 (sources: ≤ near_tipoff.MAX_DATA_AGE = 60 min while a game is within 2 h)
  vs backend/app/freshness.py:150-151 (bar: 2×interval + 5 min = 540 s pregame). UI: TodayPage.tsx:118-122 (SourceList)
  and Freshness.tsx:29-31,45.
- Evidence (same moment, 8871): `/api/freshness` → `source_fetched_at 20:10:09Z, source_state "stale", stale_after_seconds
  540`; `/api/sources` → "ESPN schedule & results … last_updated 20:10:09Z, freshness fresh".
- Fix (text): one freshness function, or label them differently ("fresh enough to forecast" vs "on schedule").

### MEDIUM-6 — Dev copy republishes fetch-only refreshes after 10 min, but marks data stale after 7–9 min (NEW)
- Where: backend/app/preview_publish.py:59-71 (max_lag 10 min default, cli `--max-lag-minutes`), scripts/dev.sh:8-11;
  freshness.py:24,33,150.
- Sports: all served by dev.sh.
- Evidence (focused reproduction on SNAP code, $SP/work/NBA-C): `needs_publish(live{rev r1, fetched +7/+9/+10 min},
  published{rev r1}, max_lag=10 min)` → None, None, None; +11 → "source time lags live by more than 10 min";
  stale_after_s active=420, pregame=540. So during pregame/active, a dev app whose live refreshes change no row shows
  "Source data stale" while live is fresh. (HANDOFF's soccer note covers a different case — soccer revision.)
- Fix (text): max_lag ≤ min stale_after − publish interval (e.g. 5 min), or republish on any newer successful refresh.

### LOW-7 — "Real data" popover refresh time goes stale after fetch-only refreshes (NEW)
- Layout.tsx:47-50,127: meta (`last_refresh_at`) refetches only when `fresh.changes` (revision) changes; revision excludes
  refresh timestamps (freshness.py:113-123). After a refresh that changes no row the popover keeps the old time while the
  bar's "Data updated" advances. Fix: take the time from the freshness poll (`source_fetched_at`).

### LOW-8 — Any copy served without SNAPSHOT_AT is labelled "Live data"
- main.py:118-129 + freshness.py:162-164. Observed on 8871 (copies, no publisher): `/api/freshness` → mode "live",
  next_check_at 2026-11-07T20:12:09Z (in the past; nothing will run). Manual imports (`cli refresh`) and scheduled refreshes
  are both `job='refresh'` and are not distinguished in API/UI; snapshot publication is distinguished ("Copy published").
  Fix: mark copies explicitly (store_identity flag or required SNAPSHOT_AT for NBA_DATA_DIR ≠ live dir); expose run trigger.

### LOW-9 — Canonical "upcoming" text inside the window
- coverage.py:589-590: espn:401909938 (start 22:00Z) at 20:35Z, near_tipoff generated 20:10:15Z, canonical attempts 0 →
  "Canonical forecasts are recorded within two hours of the start." Should say due/overdue once inside the window.

### LOW-10 — Deep links / defaults
- /games/nhl:2026020263 without ?sport → NBA store → `404 Game nhl:2026020263 not found` (curl); NBA/NHL/NFL ids are
  prefixed (sports.py:28,35,49) but only soccer infers league from the id (competition.ts:12-29). Unknown `?sport=xyz`
  silently shows NBA (competition.ts:26-29; breadcrumb shows NBA). API side correct: `?sport=mlb|nfl|soccer` on /api/games
  → 422; NBA id with sport=nhl → 404. No cross-sport or demo fallback found in client.ts/useApi.ts/freshness.ts (deps change
  → loading/error, never old data; quiet-refetch failure keeps same-sport data with a visible "Update failed … retrying").
  Invalid `?date=` → ErrorState with a 422 message whose "Try again" cannot succeed (TodayPage.tsx:43, States.tsx:8-16).

### LOW-11 — Wording
- Layout.tsx:127 NBA popover "Forecasts come from an Elo model" (canonical rows are primary_recorded/adaptive plus the
  points_normal market model). Layout.tsx:138 tells release-app users to run `sh scripts/dev.sh`. TodayPage.tsx:64 subtitle
  "Live forecasts and best picks." also in demo mode (Demo badge/banner present). docs/SMOKE_TEST.md:20 shows a historical
  /api/health body (`run_type`, 0.1.0) not today's (`mode`, 0.3.0).

### LOW-12 — Dev venv drifted from the release pins (partly known)
- `freeze.py` via importlib.metadata: REPO/backend/.venv 80 dists vs release 64. Release-only: pandas 2.3.3, lifelines,
  ngboost, matplotlib, python-dateutil, pytz, tzdata, contourpy, cycler, kiwisolver, formulaic, autograd(-gamma),
  interface_meta, pyparsing, fonttools. Dev-only: torch 2.14.1, tensorflow 2.21.0, keras 3.15.1 (+deps), hypothesis, pillow
  13.0.0 (release 12.3.0). Shared core identical (fastapi 0.142.2, pydantic 2.13.5, numpy 2.5.3, scipy 1.18.1, sklearn 1.9.1,
  uvicorn 0.54.0, httpx 0.28.1). README.md:189-190 "requirements-release.txt (exact versions of this checkout's venv)" is
  contradicted; release venv matches requirements-release.txt except hypothesis/sortedcontainers listed but not installed.
  Jobs on the dev venv: telegram, nfl-live. adaptive-train plist (installed, NOT loaded: `launchctl print` → not found) uses
  ~/nba-predictor-deep venv with WD ~/nba-predictor-live.

### Scheduled jobs (read-only)
| job | loaded | python/venv | checkout (WD) | NBA_DATA_DIR | schedule | guard |
|---|---|---|---|---|---|---|
| api | yes (pid 942) | ~/nba-predictor-live/.venv | ~/nba-predictor-live | REPO/data | KeepAlive :8100 | n/a |
| web | yes (pid 953) | node 22.23.2 vite preview | ~/nba-predictor-live/frontend | – | KeepAlive :4173 | n/a |
| odds (`tick`) | yes | live venv | live | REPO/data | every 60 s | yes |
| nhl-odds (`--sport nhl tick`) | yes | live venv | live | REPO/data | every 60 s | yes |
| injuries | yes | live venv | live | REPO/data | :02, :32 | yes |
| daily | yes | live venv | live | REPO/data | 22:00 | yes |
| nhl-daily | yes | live venv | live | REPO/data | 22:15 | yes |
| nba-markets | yes | live venv | live | REPO/data | 22:30 | yes |
| nfl-live | yes | **REPO/backend/.venv** | ~/.codex/worktrees/nfl-live-release (8539513, clean) | REPO/data | every 60 s | yes |
| telegram | yes | **REPO/backend/.venv** | **REPO/backend (dirty working tree)** | REPO/data | every 60 s | **no** |
| adaptive-train | **no** | ~/nba-predictor-deep/.venv | live | REPO/data | 09:30, 15:30 | no |
All installed plists are byte-identical to scripts/launchd/*. No secret values in any plist (telegram token lives in
.env.telegram, mode 600, gitignored).

### 390 px candidates for the lead's browser check (tables NOT inside `.table-wrap`; `.table.history` becomes cards ≤580 px, styles.css:292-295, so excluded)
- components/MarketView.tsx:211 — 5 columns (Line, Games, Log loss, League-average model, Pick accuracy) — most likely.
- pages/PerformancePage.tsx:170 — calibration table, 4 long headers (Predicted home win / Games / Avg predicted / Actual home wins).
- pages/PerformancePage.tsx:154 — 4 columns (blank, Picked winner, Brier score, Log loss).
- pages/GamePage.tsx:341 — 3 cols (ArchiveCard odds), components/MarketView.tsx:197, components/Canonical.tsx:185,198,
  pages/PerformancePage.tsx:198 — 2 cols (low risk).
- The HANDOFF item "Canonical.tsx:242 Calibration table outside .table-wrap" is wrapped in SNAP (Canonical.tsx:232,242).

## 2. Claim table
| claim (source) | status | evidence |
|---|---|---|
| "Everything that runs unattended uses the release worktree … never this development checkout" (README.md:186-187) | contradicted | telegram + nfl-live plists (above) |
| "release venv … built from requirements-release.txt (exact versions of this checkout's venv)" (README.md:189-190) | contradicted (second half) | venv diff LOW-12 |
| "Training status reads $LIVE directly (read-only)" (dev.sh:194) | verified | main.py:622-624 `?mode=ro` |
| "The live data/ folder is never migrated or written" (dev.sh:5) | verified for dev.sh only | copies via mode=ro backup (dev.sh:124), migrate with NBA_DATA_DIR=$DEV (dev.sh:150); no guard outside dev.sh (HIGH-2) |
| "PUBLISH_INTERVAL < 30 leaves the launcher without publisher but says it republishes" (HANDOFF L29, LOW) | resolved in SNAP | dev.sh:81-86; isolated snippet: abc/29/-5/60s/" 60" → FAIL "Nothing was started"; ""/030/60 → OK |
| Demo "always labelled Demo" (main.py:1-2, README:80-81) | verified (API) | TestClient NBA_API_MODE=demo on empty data dir: every 200 carries mode/run_type demo; canonical/NHL/NFL → 404 with explanation; research_demo uses in-memory synthetic store (research_demo.py:1-3). UI: Layout.tsx:115,144 pill+banner on meta.mode demo. Browser not checked |
| Demo never served in real mode | verified (code) | DemoProvider only in `_provider` when mode=="demo" (main.py:84-87); research_demo only behind `p.mode != "real"` |
| "a refresh failure never counts as fresh data: only successful refresh job runs move the source time" (freshness.py:4-5) | verified (code) | freshness.py:72 |
| "A recently published copy is not shown as proof that its source data is recent" (Freshness.tsx:14-15) | verified | separate "Data updated" and "Copy published" items (Freshness.tsx:29-32) |
| API secrets server-side only | verified | `git ls-files` grep for key/token patterns → none; frontend src has no keys/VITE_ vars; .gitignore covers .env, .env.*, data/, *.sqlite, .telegram-delivery*; `git check-ignore` confirms |
| Final game API = stored rows (espn:401909294) | verified | canonical ids 1068-1072 + 1078 = latest forecast rows; market quote = last pre-start 02:58:12Z (quotes at 03:11/03:12Z after start excluded, real.py:145-148); near_tipoff 02:29:55Z |
| Upcoming game API = stored rows (espn:401909938) | verified | market = quote 42519 (20:10:09Z); no game_forecasts rows → state upcoming, attempts 0; team totals line_source "model" (no bookmaker line) |
| Missing-data game (espn:401909924) | verified, reason incomplete | no game_forecasts rows, no near_tipoff rows; state missing "no forecast cycle ran…" while run 6126 started 22:27:50Z and never finished |
| SNAP API on an unmigrated (31) store | no defect seen | TestClient on a copy ($SP/work/NBA-C/s31): /api/meta, freshness, games (pregame/canonical), game detail, canonical/forecasts, sources all 200; game detail identical to schema-35 copy except percentage_history.as_of |

## 3. Checks run
| command | expected | actual | result |
|---|---|---|---|
| `curl :8871/api/{health,meta,freshness,sources,games…,games/espn:401909294,…938,…924}` | real-mode JSON | as quoted above | pass |
| `sqlite3 "file:$SP/work/devapi/nba.sqlite?immutable=1"` queries (games, game_forecasts, odds_quotes, job_runs, injury_report_attempts) | rows match API | match (above) | pass |
| cross-sport probes `curl :8871/api/games/nhl:2026020263[?sport=nhl]`, `espn:401909294?sport=nhl|mlb|nfl`, `games?date=2026-13-01`, `sport=soccer` | 404/422, never other store | 404,200,404,422,422,422,422 | pass |
| TestClient demo mode (SNAP, NBA_DATA_DIR=$SP/work/NBA-C/emptydata) | demo-labelled or 404 | as claim table | pass |
| TestClient real mode on schema-31 copy $SP/work/NBA-C/s31 | 200, same as migrated | same except as_of | pass |
| `needs_publish` / stale_after reproduction (SNAP) | lag ≤ stale | lag 10 min > 7/9 min | fail (MEDIUM-6) |
| dev.sh:81-86 snippet with 9 PUBLISH_INTERVAL values | invalid → visible fail | as claim table | pass |
| launchd: `launchctl list | grep nba-predictor`, `launchctl print gui/501/com.nba-predictor.{telegram,nfl-live,odds,adaptive-train,daily,api}`, `plutil` of each plist, `diff` vs scripts/launchd | – | table above | – |
| venv diff: `<venv>/bin/python -I freeze.py` (importlib.metadata) for both venvs | identical pins per README | differ (LOW-12) | fail |
| `pmset -g log`, `pmset -g` | – | sleep cycles at gap times; sleep 1 | – |
| secrets grep over `git ls-files`, frontend src; `git check-ignore -v` | none / ignored | none / ignored | pass |
| table-wrap scan of frontend/src/**/*.tsx | – | list above | – |
Not run: browser checks (lead's), dev.sh (forbidden), any test suite (no focused test fits these ops findings), live-store
reads (REPO/data not opened; only odds-launchd.log and .telegram-delivery.log tails read), NHL/NFL job-gap analysis.

## 4. Open questions / cross-sport
- Lane NBA-B: why does near_tipoff take 12–24 min per cycle (runs 6444, 6453, 6473, 6933) while holding the store lock that
  blocks the 60 s refresh? Why is `availability_two_hour` missing at 20:16Z for espn:401909938 although the 20:00 injury
  report was stored 20:02:01Z (agreement lists the name with no reason)?
- Lane NBA-A / NHL-C: same orphaned-`running` + sleep-gap pattern likely in nhl.sqlite (not checked).
- Lead: decide whether telegram/nfl-live on the dev venv/working tree is an accepted user decision (HANDOFF L29 calls the
  venv part a user decision) — the unguarded working-tree code part is not covered by that note.
- Shared code: providers/real.py env-based status notes (MEDIUM-4) likely have NHL equivalents (NHL_INJURY_CAPTURE,
  NHL_PLAYER_BOX_REFRESH) — not checked.
