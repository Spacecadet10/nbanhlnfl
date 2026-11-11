# SOCCER-C — API, UI & operations (read-only audit, snap2 frozen 20:38Z)

Code audited: `$SP/snap2` (paths below relative to repo root). The API checks used the shared dev API at
http://127.0.0.1:8871 (it runs SNAP code). The read path is identical in snap2: `main.py` is unchanged, and the `soccer.py`
diff only touches import and replay. Its store is `$SP/work/devapi/soccer.sqlite`, byte-identical to `$SP/stores/soccer.sqlite`
(`cmp`). The snap2 error paths were exercised in-process (FastAPI TestClient, `PYTHONPATH=$SP/snap2/backend`) against
private copies in `$SP/work/SOCCER-C/{empty,wrong,good}`. No uvicorn was started, no browser was used and nothing outside
`$SP/work/SOCCER-C` was written.

## 1. Findings (most severe first)

### MEDIUM-1 (NEW, snap2-specific): the revision list says "the source delivered N versions" when a newer importer re-derived the same retrieval
- Files: `frontend/src/pages/SoccerPages.tsx:230-237` (wording). `backend/app/soccer.py:146-156` (snap2 reclassify branch)
  followed by `soccer.py:110` → `_record_revision` (`soccer.py:204-213`).
- Evidence: I re-ran the scenario from snap2's own test `test_replaying_the_last_retrieval_reclassifies…` and then read
  `soccer.match_detail` (my file `$SP/work/SOCCER-C/test_reclass_ui.py`):
  `REVS [(1, 'regulation', '2026-11-07T20:00:00Z'), (2, 'unknown', '2026-11-07T20:00:00Z')]`,
  `UPDATED_AT before/after 2026-11-07T20:00:00Z 2026-11-07T20:00:00Z fetches 1`.
  So one retrieval produces two revisions with the same `observed_at`. The match page would render: "The source delivered 2
  versions of this result … 2–0 (seen T); 2–0 (ending unclear) (seen T)". The source delivered one version. The importer
  changed its reading of it.
- The reclassify UPDATE (`soccer.py:154-155`) also leaves `updated_at` unchanged. "Last updated" on the card and in Match
  facts therefore does not move, although `decided_in` changed.
- Why it matters: this is a false provenance claim on screen. Downstream, an "as-of" reader of `soccer_result_revisions`
  sees two different endings with the same `observed_at`, and nothing in the row says which one is a re-derivation. This is
  cross-lane (SOCCER-A/B).
- Not visible with real data yet: no match in either soccer store has more than one revision (`… having count(*)>1` → no
  rows).
- Proposed fix (text only): give revisions a provenance field (e.g. `basis = 'source' | 'reclassified'` plus
  importer_version). The UI would then say "N recorded versions; k re-derived from the same ESPN response by a newer
  importer". Alternatively, record the reclassification outside the result-revision history. Set `updated_at` (or a
  dedicated `reclassified_at`) on reclassification.

### MEDIUM-2 (NEW): the UI and 503 import instructions, run as shown, write to the live `data/` directory
- Files: `frontend/src/pages/SoccerPages.tsx:94` ("Import days with `python -m app.cli soccer ingest --competition … from
  backend/`") and `backend/app/main.py:253-254` (503 detail: "Run `python -m app.cli soccer migrate` and `… soccer ingest
  …` from backend/").
- Evidence: `config.data_dir()` defaults to `REPO_ROOT/data` when `NBA_DATA_DIR` is unset (`backend/app/config.py:43-44`).
  `db.connect` creates the parent directory and the file (`db.py:26-29`), and `cli._soccer` migrates and claims it
  (`cli.py:734-736`). So following the on-screen instruction creates a live `data/soccer.sqlite` and fetches ESPN. With
  `dev.sh` the publisher then picks it up as "no served copy".
- Why it matters: CLAUDE.md says "never … write against data/ unless the task says so". HANDOFF says live data has no
  soccer store, and the ESPN-terms decision (HANDOFF S1 blocker 1) is still with the user. The app should not instruct a
  live collection that has not been approved. `docs/SMOKE_TEST.md:1701` itself uses `NBA_DATA_DIR=$S`.
- Proposed fix: name `NBA_DATA_DIR=<scratch dir>` in both messages. Or replace them with "No soccer store is available;
  soccer collection is not enabled", and point to the docs.

### MEDIUM-3 (NEW): states from the last import are shown as current, with no "as of" label
- Files: `frontend/src/pages/SoccerPages.tsx:36-37` (card label: kickoff time for `scheduled`, "IN PROGRESS"), `:157-159`,
  `:169`, `:222`.
- Evidence: the store, last imported 17:51–18:25Z, holds four `in_progress` rows (e.g. `soccer:esp.1:401882811`, kickoff
  17:30Z, `STATUS_FIRST_HALF`, last_seen 17:51:19Z). It also holds `scheduled` rows whose kickoff had passed when the
  snapshot was taken (e.g. `soccer:fra.1:401876411`, kickoff 18:00Z, last_seen 17:51:51Z). A past-kickoff `scheduled` card
  shows only its kickoff time, which reads as upcoming. "IN PROGRESS" shows hours later. The match page does not show the
  competition's last import time at all.
- Partly mitigated: Game Day has a "Last imported" metric with the hint "Not refreshed automatically", and the in-progress
  card says "Live scores are not collected; the result follows the next import".
- Proposed fix: when `now > kickoff` and the status is not final, label the card "Scheduled at last import (time)" or "In
  progress at last import (time)". Show `last_refresh_at` on the match page.

### LOW-1: a request without `competition` silently gets Premier League data
- File: `backend/app/main.py:240` (`Query("eng.1", …)`). The comment at `main.py:239` says "Every read names one
  competition".
- Evidence: `curl '/api/soccer/matches?date=2026-11-01'` → 200, `competition_id: "eng.1"`. `curl /api/soccer/meta` → 200
  eng.1. The frontend always sends it (`api/client.ts:57-59`, competition is never empty), so no UI path is affected.
- Proposed fix: make `competition` required (no default) so that a client bug returns 422 instead of another competition's
  data.

### LOW-2: very large PUBLISH_INTERVAL values pass dev.sh, then crash the publisher
- File: `scripts/dev.sh:81-86`.
- Evidence: in `/bin/sh` (bash 3.2), 30, 030 and 25-digit "…030" pass. "1e3" and " 30" are refused as non-digit.
  20-digit values are refused only through the `[` error "integer expression expected", and the message then says "must be
  at least 30 seconds" (wrong reason). A value between about 9.3e9 and 9.2e18 passes the shell check. Then
  `time.sleep(int(v))` raises `OverflowError: timestamp too large to convert to C _PyTime_t` (checked in the backend Python
  for 2 values), so the publisher dies after its first check.
- Meanwhile dev.sh line 195 still prints "Copies are republished …". This is an absurd input; the <30 case is fixed and
  tested.
- Proposed fix: add an upper bound (e.g. ≤ 86400) in both dev.sh and `cli.py:349-350`.

### LOW-3: wording carried over from the forecast sports
- The navigation says "Prediction Dashboard" and "Prediction History" on soccer (`Layout.tsx:21-23, 95-103`), while the
  pages themselves say not available.
- The date control's "Next games" button (`frontend/src/pages/NflPages.tsx:92-94`, reused) goes backwards for UCL/UEL,
  whose `default_date` is the latest past date (2026-11-04 / 2026-11-05).
- With soccer selected, the dev banner (`Layout.tsx:143`) shows `data_snapshot_at` from a single `/api/health` fetch
  (`Layout.tsx:51`, deps `[]`), so "last copied" goes stale while the publisher republishes.

### LOW-4: the back link from a match drops the date the user came from
- `SoccerPages.tsx:208` links to `/${carried}`. `useCarriedSearch` (`Selection.tsx:119-128`) never keeps `date`, and
  `useSoccerMatchUrlSync` (`Selection.tsx:78`) deletes it.
- So Game Day 2025-03-11 → match → "‹ Soccer matches" lands on `default_date` (e.g. 2026-11-04 for UCL). Browser Back
  works. NBA/NHL/NFL behave the same way, but soccer's past-only competitions make the jump larger.

### LOW-5: penalties wording assumes extra time was played
- `SoccerPages.tsx:32, 175, 183, 221` render 'penalties' as "AFTER EXTRA TIME · PENS" / "Extra time: Played".
- That holds for UCL/UEL knockouts, the only competitions with shoot-outs in scope. It is an assumption, not a stored
  fact; a future competition with direct 90-minute shoot-outs would be mislabelled.

### Cross-lane / not mine (recorded, not chased)
- launchd: `com.nba-predictor.telegram` runs `python -m app.cli telegram run --once` with WorkingDirectory
  `/Users/andry/Desktop/nba-predictor-sprint1/backend`, i.e. the code of the working tree being edited, against
  `NBA_DATA_DIR=…/sprint1/data` (live). `com.nba-predictor.nfl-live` uses the sprint1 venv python with WorkingDirectory
  `~/.codex/worktrees/nfl-live-release/…`. Neither touches soccer. They matter for NBA-C/NFL-C (code under edit runs
  live every 60 s).
- `cli._soccer` and every sport migrate before `claim_store` (`cli.py:734-736`). When an NFL store sits at
  `soccer.sqlite`, my copy was migrated before the `StoreMismatch: This store holds 'nfl' data, not 'soccer'` refusal.
  dev.sh then fails loudly ("migrating the soccer copy failed"), which is correct. The pre-claim migration is a shared
  pattern.
- All `raw_responses.importer_version` values in both soccer stores are `espn-soccer-scoreboard-1`, yet the rows already
  carry the classifier's `unknown` endings. The version string did not track the parser change at import time (SOCCER-A).

## 2. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "Every read names one competition" (main.py:239) | contradicted (API), verified (UI) | No competition → eng.1 served (LOW-1); the client always sends one |
| Invalid competition / wrong sport → explicit error | verified | usa.1, nba, ENG.1, `eng.1\n` → 422 pattern mismatch; `/api/{meta,games,freshness,performance}?sport=soccer` → 422 |
| Match id names its competition; a wrong competition prefix → 404 | verified | `soccer:eng.1:401882821` (really esp.1) → 404; `nba:…`, bare `401882821`, `soccer:usa.1:1` → 404; the detail route takes no competition param |
| Missing store → 503, demo → 404, wrong-sport store → 503 (snap2) | verified | TestClient: empty dir 503 "No soccer data store found…"; nfl.sqlite renamed soccer.sqlite → 503 "holds 'nfl' data, not 'soccer'"; demo → 404 on all 3 routes |
| API rows equal store rows, one competition only | verified | compare.py: 7 competitions, 2+ matches each (incl. 733605 PSG pens, 733642 Bodø pens, 734499 Man Utd ET, 169930 / 416637 unknown); 16 fields DIFF {} for each; detail match == list match; 0 foreign-competition rows in h2h/recent; revision counts API = store |
| Draw / ET / penalties / aggregate kept apart, no "winner" for a draw | verified (code + API) | FCU 1–1 KOE: regulation_result=draw, card "FULL TIME · DRAW", sideText "Draw", no is-winner; penalties: regulation_result null, shootout_winner, "went through the tie (the tie, not this match)"; unknown: "Not established… Nothing is guessed" |
| Deep links: the match page picks its own competition (competition.test.mjs) | verified | `node --test --experimental-strip-types src/components/competition.test.mjs` → 3 pass, 0 fail; code: `Selection.tsx:67-81` rewrites the URL |
| "dev.sh copies + migrates soccer.sqlite (`app.cli soccer migrate`)" (HANDOFF:65) | verified (test with fake CLI) + command works | test_dev_script -k "soccer or publish_interval" 12 passed; real `app.cli soccer migrate` on my copy → `"applied": []`, exit 0; `--sport soccer` refused by argparse |
| "devpaths guards soccer files" (HANDOFF:65) | verified (code) | devpaths.py:23 adds soccer to FILES; WRITE_TARGETS in the test includes the soccer names |
| "preview_publish republishes soccer" incl. fetch-only imports (fixes the earlier audit's MEDIUM) | verified | preview_publish.py:28; freshness.py:124-132 adds MAX(fetch_id) of espn_soccer; raw_cache.store_fetch adds a raw_fetches row for identical bytes (4 such raw_ids in the store); tests `…republishes_a_soccer_copy…`, `…fetch_only…` pass |
| "NBA/NHL/NFL revisions unchanged" (HANDOFF:66) | verified | rev.py: nba/nhl/nfl old == new on both the unmigrated backups (no soccer table → `_one` None) and the migrated dev copies (0 rows → "0\|\|0\|0" skipped); soccer differs as intended |
| "PUBLISH_INTERVAL<30 leaves launcher without publisher" (earlier audit LOW) | fixed in snap2 | dev.sh:81-86 + tests (10, 29, abc, 60s, -60, 3.5 refused; skipped when NBA_DEV_PUBLISH=0); upper bound missing (LOW-2) |
| "no job enabled" / no soccer recurring job (HANDOFF:67, SOCCER_PLAN:28) | verified | `grep -il soccer ~/Library/LaunchAgents/*` → none; `grep -il soccer scripts/launchd/*` → none; `launchctl list` shows no soccer label |
| UI "source delivered N versions" (SoccerPages.tsx:231) | contradicted under snap2 replay | MEDIUM-1 |

## 3. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `curl /api/soccer/meta?competition=<each of 7>` | 200, one competition | 200 ×7; counts eng 25, esp 25, ger 20, ita 35, fra 25, ucl 34, uel 39 | pass |
| `python -I $SP/work/SOCCER-C/compare.py $SP` (API vs store, 7 competitions) | no field diffs, no cross-competition rows | all DIFF {}, foreign [] | pass |
| curl error matrix (14 URLs, see claim table) | 422/404, never other data | as listed; missing competition → eng.1 | pass, except LOW-1 |
| `python $SP/work/SOCCER-C/errs.py` (snap2 TestClient: empty / wrong / demo / good) | 503 / 503 / 404 / 200 | as expected | pass |
| `pytest -q -p no:cacheprovider tests/test_soccer.py -k "api or publisher or republished or revision_part"` (snap2) | pass | 5 passed | pass |
| `pytest -q -p no:cacheprovider tests/test_dev_script.py -k "soccer or publish_interval"` (snap2) | pass | 12 passed | pass |
| `node --test --experimental-strip-types src/components/competition.test.mjs` (snap2/frontend, node v22.23.2) | pass | 3/3 pass | pass |
| `python $SP/work/SOCCER-C/rev.py $SP` (revision old vs new, 4 stores × 2 copies) | nba/nhl/nfl same | same ×6, soccer DIFF ×2 | pass |
| `NBA_DATA_DIR=$W/good python -m app.cli soccer migrate`; same on `$W/wrong` | applied [] / refusal | `{"applied": []}` / StoreMismatch traceback | pass |
| `pytest … $SP/work/SOCCER-C/test_reclass_ui.py` (reclassify then match_detail) | show what the UI gets | 2 revisions, same observed_at, updated_at unchanged | MEDIUM-1 |
| PUBLISH_INTERVAL edge values in /bin/sh + Python sleep | refuse invalid | huge values pass the shell, then OverflowError | LOW-2 |
| launchd plist grep + `launchctl list` | no soccer job | none | pass |

Not run:
- Browser checks (lead's lane by instruction): 390 px, visual states, console.
- A private uvicorn: not needed, since TestClient covered the snap2 behaviour.
- A real `scripts/dev.sh` end-to-end start: it would stop and start servers on shared ports.
- Typecheck/build: not in scope; the lead runs the gate.

## 4. 390 px list for the lead
- Soccer wide tables lacking a scroll wrapper: **none**. The only soccer table, `ResultTable`
  (`frontend/src/pages/SoccerPages.tsx:294`), is inside `.table-wrap` (`styles.css:161`).
- Elements worth checking at 390 px:
  - Header with SportToggle + CompetitionSelect (`Layout.tsx:112-114`; wrap rules `styles.css:1076` nowrap vs `1365-1367`).
  - Date control (`NflPages.tsx:83-95`).
  - Match hero with two 104 px logos (`SoccerPages.tsx:214-225`; `styles.css:1217`).
  - Card bottom row with the long `learned()` text plus nowrap "Matchup" (`SoccerPages.tsx:156-161`; `styles.css:138`).
  - Card status line for knockout penalties, e.g. "AFTER EXTRA TIME · PENS 1–4 · Round of 16 · Leg 2"
    (`SoccerPages.tsx:142`).
  - The long `<code>` ingest command in the empty state (`SoccerPages.tsx:94`).

## 5. Open questions / cross-sport
- Should a re-derivation by a newer importer be a result revision at all? This is SOCCER-A/B, and it needs a decision
  before any evaluation reads `soccer_result_revisions` as-of.
- Is any live soccer collection approved? If not, MEDIUM-2's messages should not suggest one.
- The Telegram launchd job runs working-tree code against live data (see cross-lane note) — for NBA-C/NFL-C.
