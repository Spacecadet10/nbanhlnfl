# NFL-C — API, UI & operations (read-only audit, 2026-11-07)

Scope: SNAP code (`$SP/snap`), dev API 127.0.0.1:8871 (PID 69042, cwd `$SP/snap/backend`, `NBA_DATA_DIR=$SP/work/devapi`,
store `nfl.sqlite` schema 35, activation.json present), launchd `com.nba-predictor.nfl-live` (read-only), release worktree
`/Users/andry/.codex/worktrees/nfl-live-release/nba-predictor-sprint1` (= R; branch `codex/nfl-live-20261102`, 8539513, clean).
No code edits, no browser, no writes outside `$SP/work/NFL-C`.

## 1. Findings (most severe first)

### F1 MEDIUM — A refused or crashed NFL tick does not show as "failing" in /api/freshness (NEW)
- Where: R `backend/app/release.py:25-27` (GUARDED maps `tick` → job `odds`), `:89-104` (record_refusal), R `backend/app/cli.py:582-586`;
  SNAP `backend/app/freshness.py:72-78` and `:141-158` (reads only `job='refresh'`). Forecast outcomes `blocked` / `inactive` /
  `unsupported`: SNAP `backend/app/nfl_live.py:69-77`, R `backend/app/jobs.py:825-827` (no job row, exit 0 because
  `_any_failed` only looks for `"failed"`, R `cli.py:49-56`, `:678-679`).
- Evidence: the NFL store has job rows only for `refresh` (`sqlite3 ... "select job,status,count(*) ... from job_runs group by 1,2"` →
  `refresh|failed|2`, `refresh|success|336`). A guard refusal would be written as job `odds`, which `freshness._runs` never
  reads, so `failing` stays false and `last_error` shows the old refresh error. A crash before the guard (for example a broken
  venv import) writes no row at all. Its only traces are stderr in `REPO/.nfl-live-error.log` (outside `data/`) and
  `launchctl` "last exit code". The UI then shows stale data only after `stale_after`: 7500 s (about 2 h) when idle, 540 s when
  pregame. Capabilities still say "forecasts active" the whole time.
- Why it matters: a guard refusal (for example after the live store is migrated past schema 33, which the dev tree now ships
  as 0035) or a scoreboard block during the T−120 window lets a game pass without a forecast. Nothing operational flags it.
  The only sign is a "missing" row afterwards.
- Fix (text): record the NFL tick refusal under `refresh`, or make `freshness._runs` also count failed rows of the NFL tick.
  Record a job row with status `failed`/`blocked` for `nfl_live.record` results `blocked`/`inactive`, and exit non-zero on
  `blocked` inside a due window. Surface `forecasts.status` in /api/freshness.

### F2 MEDIUM — The UI says the NFL is "data only / no NFL model" while NFL forecasts are active (NEW)
- `frontend/src/components/Layout.tsx:124`: the Real-data popover for NFL says "Data only: there is no NFL model, so no NFL forecast, odds,
  injury or performance record is shown." The same screen shows forecasts, bookmaker lines and the record
  (`curl /api/nfl/meta` → capabilities forecasts/odds/performance/history `available: True`).
- `frontend/src/pages/NflPages.tsx:290`: the Daily Learning page subtitle says "Not available: the NFL is data only for now." Line `:295`
  renders `NflThresholdRecord`, which says "No recorded NFL forecasts to classify by market and threshold" (`:118`), but the store holds
  10 NFL `game_forecasts` rows (ids 1–10). Line `:119` appends the active "Live two-hour NFL forecasts…" reason to the same
  card, which contradicts it.
- NBA wording leaks: SNAP `backend/app/coverage.py:588` gives the reason "…any forecast of this game is in the legacy views (Within 2 hours,
  Pre-game archive)". `NflPages.tsx:314` shows it on every NFL card and matchup before 2026-11-02. The NFL has no such views.
  Evidence: `curl /api/nfl/games/nfl:401872988` → `state: not_recorded`, with that reason.
- Fix: make the Layout popover and the `NflUnavailable` subtitle depend on `capabilities` (as `NflGameDay` already does at
  `:68`). Skip `NflThresholdRecord` when forecasts are active. Give an NFL-specific `not_recorded` reason ("Before NFL live recording began; no
  forecast exists").

### F3 MEDIUM — Display of stored NFL forecasts depends on the serving checkout's code hash, which includes shared NBA/NHL modules (NEW)
- Where: `backend/app/nfl_live.py:18-20` (definition hashes `coverage.py`, `markets.py`, `models/base.py`… shared with NBA/NHL) and
  `:27-37` (`activation()` returns None on mismatch). Readers in the same process gate on it: `Views.attach` `:179-183`,
  `nfl.capabilities` `backend/app/nfl.py:78-87`, `freshness.summary` `backend/app/freshness.py:152-161`.
- Effect (from the code; not reproduced, because that would need a code edit): editing `coverage.py` or `markets.py` in the checkout that
  serves the API or Telegram hides every recorded NFL forecast in that checkout. The dev API runs from REPO or a snapshot, and
  Telegram runs from REPO `backend/` (launchd telegram plist WorkingDirectory). The pinned release keeps recording, but the UI says
  "NFL live forecasts have not been activated." (`NflPages.tsx:309`) and the freshness bar says "Forecasts not supported for NFL"
  (`Freshness.tsx:35`, `freshness.py:161`). Telegram silently stops NFL cards.
- Current state: hashes match (R, SNAP and REPO all `b6074877a7e7ccfe` = live activation `definition`), so nothing is hidden
  today.
- Fix: keep the definition check for the producer (`record`) only. Readers should show stored rows by their own `model_version` and
  label a hash mismatch instead of dropping the rows.

### F4 LOW — "Schedule refreshed / last imported" does not separate manual imports from automatic collection (NEW)
- `backend/app/nfl.py:125-129`: `last_refresh_at` = MAX `raw_fetches.retrieved_at` over every `espn_nfl` fetch. Manual
  `nfl ingest --from/--to` writes these fetches too (`backend/app/ingest.py:64-85`, `store_fetch(source=espn.NFL_SOURCE)`). That value appears as
  "Schedule refreshed… Last ESPN NFL scoreboard import" (`NflPages.tsx:136-137`, `:301-302`) and "last imported" (`Layout.tsx:124`). The
  freshness bar's "Data updated" uses the last successful `refresh` job (`freshness.py:72,149`).
- So a manual backfill of an old month moves "Schedule refreshed" to now even while automatic refresh is failing. Today the two
  agree (meta `19:46:37Z`, freshness `source_fetched_at 19:46:38Z`). Fetch-only updates are handled honestly: retrieval
  time moves even when no game changes. No snapshot-publication time is shown on NFL pages except through the shared bar
  (`Freshness.tsx:32`).
- Fix: drive "Schedule refreshed" from the last successful `refresh` job and show "last manual import" separately.

### F5 LOW — nfl-live runs on the dev worktree venv (already in HANDOFF L29, user decision; new detail)
- Plist (repo template and installed copy are identical; `diff` → SAME): Python
  `/Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python`, WorkingDirectory R `/backend`, `NBA_DATA_DIR=REPO/data`
  (live), `NBA_RELEASE_GUARD=1`, `NBA_RELEASE_ROOT=R`, StartInterval 60, logs `REPO/.nfl-live.log` / `.nfl-live-error.log` (git-ignored,
  not under `data/`). `launchctl print`: runs = 841, last exit code = 0, state not running (between ticks).
- That venv has `torch-2.14.1` and `tensorflow-2.21.0` dist-info dated Nov 7 13:23, installed while the job was running. It also has an editable finder
  mapping `app` → REPO `backend/app`. Today `app` still resolves to the release code: `cd R/backend && python -c "find_spec('app').origin"` →
  `R/backend/app/__init__.py`, because cwd precedes the finder, which is appended to `meta_path`. If that ever falls through, the
  guard's code-root check refuses. The guard does not check the interpreter or the installed packages. The NFL model modules
  import only the standard library (checked), so the risk is limited to import-time breakage, which F1 shows is poorly surfaced.
- Fix: give the release worktree its own venv, or add an interpreter-prefix check to `release.check`.

### F6 LOW — An NFL matchup deep link without `?sport=nfl` opens the NBA page and shows a 404 (NEW)
- `frontend/src/components/competition.ts:26-29`: `leagueFrom` infers only soccer from the path. `/games/nfl:401873037` with no
  sport query becomes the NBA matchup, which calls `/api/games/nfl:401873037?sport=nba` → `404 {"detail":"Game nfl:401873037 not found"}`.
  The error is explicit and no data leaks, but the link is broken. `useSoccerMatchUrlSync` (`Selection.tsx:67-81`) has no NFL
  equivalent.
- Fix: treat the `nfl:` prefix like soccer's.

### F7 LOW — On the audit instance the freshness bar says "Live data" for a frozen copy
- `backend/app/main.py:131-133` returns None when `SNAPSHOT_AT` is absent, so `freshness.py:162-164` reports `mode: live` with a policy
  `next_check_at`. `curl /api/freshness?sport=nfl` on 8871 → `"mode":"live","next_check_at":"2026-11-07T20:46:38Z"`, but
  `$SP/work/devapi` has no SNAPSHOT_AT and no job writes it. This is an artefact of the lead's setup. In general, any copy without the
  marker is labelled live.

### F8 LOW — Odds age is not exposed
- The record carries `market_quote_id` and `line_source` but no quote `retrieved_at` (SNAP API `record` keys). The UI says
  "bookmaker's line" with no time (`frontend/src/components/Canonical.tsx:20,73`). Capability text claims "Available quotes are
  retrieval-stamped" (`nfl.py:83`): they are stamped in the DB but not shown. The bound itself holds: quote must be ≤ cutoff and ≥ cutoff−2 h
  (`nfl_live.py:112-114`), and quotes 6796–6798 were retrieved at 22:52:07Z against a 23:15:00Z cutoff.

### F9 LOW — Hygiene and wording
- Stale "data-only / no forecasts" claims: `backend/app/main.py:118` (LEAGUE description), `:182-183`; `backend/app/nfl.py:1-5`, `:55-57`, `:61`
  (CAPABILITIES says "odds are not collected yet" while odds are collected); `frontend/src/pages/NflPages.tsx:15-17`; `components/Selection.tsx:21-22`; R `jobs.py:815`
  ("NFL never reaches any forecast code", contradicted at `:825-827`).
- `NflPages.tsx:337`: the History/Performance table link text is `dateTime · nfl:401873037` (raw id, no matchup).
- `nfl_live.py:217`: `ranked_picks` hard-codes `threshold=.75` instead of reading the activation's frozen threshold.
- `.nfl-live.log` is 1,248,752 bytes / 63,228 lines after 5 days (pretty-printed JSON every minute, no rotation).
- `backend/app/preview_publish.py:135-145` copies activation.json only when present and never removes it. A live deactivation done by
  renaming (NFL_LIVE.md:50) leaves the dev copy active until it is renamed there too, as the docs say.
- Mac-sleep gaps in the tick log (parsed `as_of`): 22h52m from 2026-11-06 00:47Z to 23:39Z, so the JAX@BAL result was first seen at
  23:39:17Z (observed basis, about 22 h after kickoff). 10h52m from Nov 5 12:00Z, resuming at 22:52Z, 23 min before that game's cutoff.
  Documented ("Mac must be awake", NFL_LIVE.md:15). A missed window would be counted as missing, which is honest, but nothing alerts.

## 2. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "NFL never… through get_provider… no forecast code can see an NFL store" (main.py:180-182) | verified for routing | `sports.SPORT_IDS=("nba","nhl")`; `/api/games`, `/api/meta`, `/api/sources`, `/api/canonical/*`, `/api/predictions` with `sport=nfl` → 422 pattern `^(nba|nhl)$` |
| NFL reads give an explicit error, never another sport's data | verified | missing store → 503 "No NFL data store found"; soccer-labelled store → 503 "holds 'soccer' data, not 'nfl'"; unlabelled → 503; bad id/prefix (`espn:…`, `nhl:…`, `garbage`) → 404; bad date → 422; demo → 404 (main.py:187-189) |
| "NFL is data-only (no forecasts)" (main.py:118, nfl.py:1-5, Layout.tsx:124, NflPages.tsx:290) | contradicted | meta capabilities forecasts/odds/performance/history available; `game_forecasts` ids 1–10 |
| "Stale/failed refreshes and unresolved recent results block forecasts" (NFL_LIVE.md:25) | verified (code) | nfl_live.py:75-77, 96-101 |
| "Book quotes must additionally be within 2h of that cutoff" (NFL_LIVE.md:22) | verified | nfl_live.py:112-114; quote 6796 retrieved 22:52:07Z, cutoff 23:15:00Z |
| "Missing real lines are explicitly model lines" (NFL_LIVE.md:35) | verified | team totals ids 4,5,9,10 `line_source=model`, `market_quote_id` NULL; UI label Canonical.tsx:20 |
| "Winner ties void" (NFL_LIVE.md:26) | verified (code) | markets.py settle `winner`: `void if home == away`; GRADE_LABEL void text names a tie |
| Job "exit0, stderr empty" (HANDOFF:230) | verified now | launchctl last exit code = 0, runs = 841; `.nfl-live-error.log` 0 bytes; log statuses: not_due 5229, success 335, ok 2, no failed/blocked |
| Release guard "refuses dirty code or a mismatched schema" (NFL_LIVE.md:40) | verified (code); not checking interpreter | R release.py:60-86 |
| Guard refusal "is recorded as a failed job run" (R release.py:11) | verified, but invisible to freshness | job name `odds`, while freshness reads `refresh` (F1) |
| "Changes to those serving/model files disable forecasts" (NFL_LIVE.md:39) | verified; also hides display (F3) | nfl_live.py:18-37 |
| Hashes consistent across producer and readers | verified | R / SNAP / REPO definition = `b6074877a7e7ccfe` = live activation.json |
| "Every listed game, including preseason and the Pro Bowl" (NflPages.tsx:300) | partly verified | season_type counts: preseason 1464, other 24 (Pro Bowl identity not checked) |

## 3. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `curl :8871/api/nfl/meta` | NFL meta | 200, 8230 games, default 2026-11-08, capabilities active | pass |
| `curl :8871/api/nfl/games/{espn:401585601, nhl:2024020001, nfl:doesnotexist, garbage}` | 404 | 404 "No NFL game …" ×4 | pass |
| `curl :8871/api/nfl/games?date=2026-13-01` / no date | 422 | 422 / 422 | pass |
| `curl :8871/api/nfl/games?date=1999-01-01` | empty with navigation | 200 `games:[]`, `next_date 2002-08-03` | pass |
| `curl :8871/api/nfl/games?date=2026-11-08&sport=nba` | NFL only | 200 NFL games (extra param ignored) | pass |
| `curl :8871/api/{games,meta,sources,predictions,canonical/forecasts,canonical/ranked}?sport=nfl` | rejected | 422 pattern `^(nba|nhl)$` | pass |
| `curl :8871/api/freshness?sport=nfl` / `sport=xyz` | NFL summary / 422 | 200 (mode live, see F7) / 422 | pass, with caveat |
| `curl :8871/api/nfl/meta?mode=demo` | query ignored (env-mode) | 200 real | pass (mode is env-driven) |
| `curl :8871/api/nfl/record` | report | 200, active, recording began 2026-11-02T22:11:33Z, 2 games per market | pass |
| TestClient (SNAP) with `NBA_DATA_DIR=$SP/work/NFL-C/{missing,wrong(soccer copy),unlabelled}` × 5 NFL endpoints | 503 explicit | all 503 with explicit reason | pass |
| API vs `nfl.sqlite` (immutable) for nfl:401872988 (tie 27–27, 2026-10-11, regular) | match | status/score/winner `tie`/kickoff/result_available_at 21:00Z assumed basis match; forecast `not_recorded` | pass |
| …nfl:401873011 (neutral final NO 10–16 PIT) | match | neutral True, winner away, assumed basis | pass |
| …nfl:401873038 (upcoming neutral CIN@ATL 2026-11-08T14:30Z) | upcoming, no record | `state upcoming` ×5, no record; UI shows window opening at kickoff−2 h | pass |
| …nfl:401873037 (JAX 33 @ BAL 10, forecast ids 6–10) | values and timestamps match DB | p, confidence, line, line_source, quote ids, generated_at 23:15:32Z, 119.5 min, grades (W/L/W/W/W) all match | pass |
| OT game | identify | schema has no period/OT field; not determinable from store | not verified |
| `diff` repo plist vs installed plist; `launchctl print …nfl-live` | — | SAME; last exit 0, runs 841 | recorded |
| `cd R/backend && REPO venv python -c find_spec('app')` | release code | `R/backend/app/__init__.py` | pass |
| Log scan `.nfl-live.log` (statuses, gaps) | — | see F5/F9 | recorded |

Not run: the browser (lead does it); any 390 px measurement; frontend unit tests or build; a reproduction of F3 (needs a code
edit); tailing `REPO/data/*.log` (the nfl-live logs are not under `data/`; the other logs belong to other lanes).

## 4. Open questions and cross-sport notes

- Wide/overflow candidates for the 390 px check: `NflPages.tsx:258` and `:336` are inside `.table-wrap` (styles.css:161, OK).
  Check `Layout.tsx:113` (four-button SportToggle plus pills in the header), `NflPages.tsx:83-95` (date control plus "Next games"),
  `Canonical.tsx:100` `<code className="wrap">` model version of about 40 chars (styles.css:1328 sets only the font size; it relies on
  `.facts dd { overflow-wrap:anywhere }` at ≤580 px, styles.css:290; 581–760 px is a two-column `.facts` inside the two-column
  `.nfl-detail-grid`), and `NflPages.tsx:337` (long id link). `Canonical.tsx:129` (CanonicalHistory table without a wrapper) is not
  used by NFL.
- Telegram launchd runs REPO dev working-tree code (uncommitted, being edited) on live data, without the release guard. Its NFL card
  path depends on F3's hash. This is for NBA-C (shared).
- `Views` (`nfl_live.py:135`) covers only regular/postseason games, so preseason NFL games get `forecasts: []` and "No forecast was
  recorded". Lane B/product: this conflicts with the every-game coverage rule for NBA/NHL.
- The dev store copy is schema 35, but the NFL producer ships 33. Migrating the live NFL store with dev code would make the nfl tick refuse
  (F1 makes that hard to see).
