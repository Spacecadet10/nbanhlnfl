# NFL-A — data & storage audit (read-only), 2026-11-07

Scope: the NFL data path in SNAP (`$SP/snap`, HEAD 79ff6ec plus the uncommitted diff), the store copy `$SP/stores/nfl.sqlite` (schema 33), and the live raw cache `REPO/data/raw/espn_nfl` (read only).
- Work dir: `$SP/work/NFL-A/`. Scripts: `scan_status.py`, `live_copy_replay.py`, `real_bytes.py`, `fixtures.py`, `fx2.py`; plus `missing_raw_ids.txt`.
- Python is `REPO/backend/.venv/bin/python`, run with `PYTHONPATH=$SP/snap/backend` and `NBA_DATA_DIR` pointing under the work dir.
- Code running live: the NFL launchd job (`com.nba-predictor.nfl-live`) runs `/Users/andry/.codex/worktrees/nfl-live-release/nba-predictor-sprint1` at commit 8539513, not SNAP. `nfl.py` and `nfl_live.py` are identical to SNAP. `ingest.py` differs by 159 diff lines: live has none of the 0035 ordering changes.

## 1. Findings (most severe first)

### F1 HIGH — 295 of 510 NFL raw responses (the whole 2002–2026 backfill) have no cached bytes; `nfl replay` cannot run on the live store (NEW)
- **Where:** live data dir `REPO/data/raw/espn_nfl`; the claim is in `backend/app/ingest.py:1-3` ("Import is a pure function of cached bytes, so `replay` rebuilds the same rows"); the command is documented in `docs/NFL_PLAN.md:17`.
- **Evidence:**
  - A path check of every `raw_responses.path` against `REPO/data/raw`: 295 missing, all with `first_retrieved_at` 2026-11-01 (raw_id 1–295). 215 present, all from 2026-11-02 onwards. List in `$SP/work/NFL-A/missing_raw_ids.txt`.
  - 8,216 games, 8,094 of them finals, have their `last_raw_id` in the missing set.
  - `find /Users/andry /private/tmp -name "f80997a0…*"` (raw 246, month 202301) found nothing. `/tmp/courtside-nfl-eval` no longer exists.
  - HANDOFF:237 says "Initialized first data/nfl.sqlite from checked scratch". The store was copied in, but its raw folder was not.
  - `live_copy_replay.py` on a migrated writable copy (33→35) printed `missing raw files 295`. Both replays then failed with `FileNotFoundError … espn_nfl/3d/3da99446….json.gz`; the store was unchanged, snapshot `(8108, 10776, …)` before and after.
  - Rebuilding from the bytes that do exist (`real_bytes.py`) is not faithful. 11 of 71 rebuilt games differ from live. Example: `nfl:401873026` live `result_available_at 2026-11-02T22:06:49Z observed_retrieval`; rebuild `2026-11-01T22:00:00Z assumed_tipoff_plus_4h`. The rebuild backdates availability by about 24 h, because the 2026-11-01 retrievals that showed those games as scheduled are among the missing bytes.
- **Why it matters:**
  - The store's facts (all historical scores, ties, neutral flags, season types) cannot be re-derived or re-audited from source bytes.
  - Overtime (F4) and any later importer fix cannot be applied to history.
  - A rebuild from partial bytes silently changes point-in-time availability, so it would leak results earlier than they were actually learned.
  - The sealed NFL evaluation fingerprints can be checked only against store rows, not bytes.
- **Fix (text):**
  - Find the scratch raw folder the store was built from and copy it into `data/raw/espn_nfl` with hashes verified. Otherwise re-fetch the 295 month requests into the raw cache only; this is a network job and needs a go. Store them as new `raw_fetches` and do not rewrite the existing rows.
  - Add a `raw_cache.missing_files(conn)` check to `release.check('nfl')` and to `nfl replay`, so it refuses up front instead of failing mid-run.
  - Record the gap in HANDOFF.

### F2 MEDIUM — `replay()` never re-imports NFL line quotes (NEW; also on HEAD)
- **Where:** `backend/app/ingest.py:295-297` (HEAD:294-296).
  - The NFL branch calls only `import_raw`. Live ingestion calls `import_lines_fetch(conn, fetch_id, "nfl")` (`ingest.py:85`); the NBA branch of replay does too (`:293-294`).
- **Evidence:** `real_bytes.py` built a fresh store from the 215 present responses (336 fetches) and replayed it 3 times. Result: `"line_quotes": 0` each time and `market_quotes after replay 0`, against 10,776 in the live store.
- **Why it matters:**
  - `game_forecasts.market_quote_id` (no foreign key, 0029:24) points at these rows; the forecasts' `line_source='bookmaker'` depends on them.
  - A rebuilt NFL store would have forecasts whose quotes do not exist, and the fixed-cutoff quote lookup in `nfl_live.record` (`nfl_live.py:112-114`) would fall back to model lines.
  - `tests/test_nfl.py::test_replay_rebuilds_identical_nfl_games` compares only `games` rows, so it cannot catch this.
- **Fix:** in the NFL replay branch, when `fetch_id is not None`, add `totals["line_quotes"] += import_lines_fetch(conn, f["fetch_id"], "nfl", cache_dir)["inserted"]`. Extend the test to compare `market_quotes` as well.

### F3 MEDIUM — store identity is enforced in code but not in the DB for games, teams, result_revisions and game_forecasts; several shared paths default to NBA (NEW)
- **Where:**
  - `migrations/0001` `games` table: no sport or prefix guard. `0029_game_forecasts.sql`: `sport TEXT NOT NULL`, no CHECK, no store trigger.
  - Defaults to NBA: `db.py:26` (`config.db_path(sport or "nba")`), `jobs.py:66,90,818` (`store_sport(conn) or "nba"`), `coverage.py:466` (same).
- **Evidence (`fixtures.py`, `fx2.py`, scratch stores):**
  - Second identity row, UPDATE and DELETE on `store_identity`: all raise.
  - `claim_store(nfl_store,'nba')`, NBA `ingest_month`, `ingest_nhl_teams` and `ingest_team_box` (sdv) into an NFL store: all raise `StoreMismatch`.
  - NFL ingest into an NBA store raises.
  - `market_quotes` with sport='nba' in an NFL store raises `market quote sport does not match this store`.
  - BUT direct SQL `INSERT INTO games … 'espn:999' … 'espn:1'` into the NFL store succeeded, and `INSERT INTO game_forecasts … sport='nba'` into the NFL store succeeded.
  - `jobs.sport(unclaimed_store) = nba`; `db.connect()` with no arguments opens `…/nba.sqlite`.
- **Falls through to NBA/NHL logic? Not found on the NFL path.**
  - Every NFL writer claims 'nfl' first (`ingest.py:79`). `jobs.refresh`/`tick` dispatch on `name == "nfl"` explicitly and refuse unknown names (`jobs.py:100,819-828`).
  - `ingest._match_game` treats nba/nfl by id and everything else as NHL (`ingest.py:605-615`). It is reached only with literal "nfl" from NFL paths.
  - The `else` → NHL there and the `or "nba"` defaults are latent traps for a future sport or an unclaimed store, not a live defect.
- **Why it matters:** the guard relies on every writer calling `claim_store`. Any new writer or manual repair can mix leagues without a DB error.
- **Fix:**
  - Add `BEFORE INSERT` triggers on `games` (`NEW.game_id` prefix must match the store sport's `id_prefix`) and on `game_forecasts`/`teams` (`NEW.sport IS (SELECT sport FROM store_identity)` / prefix), in the same way as `market_quotes_store_sport`.
  - Make `jobs.sport` and `coverage.canonical` refuse an unlabelled store instead of defaulting to NBA.

### F4 MEDIUM — a malformed final aborts the whole NFL import, and an unknown ESPN status becomes 'other', which blocks NFL forecasts for up to 7 days (NEW; latent)
- **Where:** `sources/espn.py:140,153-154` (`int(sides[...]["score"])` when final; unmapped status → `"other"`), `ingest.py:76-86` (one transaction per response), `nfl_live.py:97-101`.
- **Evidence:**
  - `fixtures.py` §6: a month containing one final with score "" raised `ValueError: invalid literal for int() with base 10: ''`. The other game in the same response was not imported either (count stayed 5).
  - Synthetic `STATUS_FINAL_OT` and `STATUS_SUSPENDED` were both stored as `('other', None, None)`.
  - In real cached bytes (`scan_status.py` over 217 files) only STATUS_SCHEDULED, FINAL, IN_PROGRESS, HALFTIME and END_PERIOD occur, so there is no real occurrence yet.
- **Why it matters:**
  - A failed refresh makes freshness 'failing', and `nfl_live.record` returns `blocked`.
  - A game left in status 'other' (e.g. a suspended game) satisfies the "missing recent result" query (status IN scheduled/in_progress/other, kickoff between cutoff−7 d and cutoff−6 h). That blocks every NFL forecast for 7 days.
  - Soccer already records rejections (`soccer_rejections`, 0035); NFL does not.
- **Fix:**
  - Treat a final without two integer scores as `other` and count it, the same as soccer does.
  - Add an append-only rejection row per event instead of aborting the response.
  - Lane B/C should decide whether status 'other' should block forecasts or only be shown.

### F5 LOW — overtime is not recorded for the NFL (NEW)
- **Evidence:**
  - Real bytes (raw 296, month 202611): `nfl:401873024` and `nfl:401873031` are `STATUS_FINAL`, shortDetail `Final/OT`, period 5.
  - Stored rows carry only status and scores. `result_revisions.decision` is NULL for all NFL rows (`ingest.py:534-559`; there is no `game_decisions` row for the NFL).
- **Why it matters:** no current NFL market depends on it, since winner, spread and total all include OT. But "tie after OT" vs "regulation tie" cannot be told apart, and with F1 it cannot be recovered for history.
- **Fix:** if needed, store `periods` (or decided_in) from `status.period`/shortDetail in a decision table. Not urgent.

### F6 LOW — every revision has basis 'observed', including the 8,094 backfilled historical results whose games are `assumed_tipoff_plus_4h` (NEW)
- **Where:** `ingest.py:552-558` hard-codes `'observed'`.
- **Evidence:**
  - `select basis,count(*) from result_revisions` → `observed|8108`.
  - Example: `nfl:401872988` (2026-10-11 tie, 27–27) is `assumed_tipoff_plus_4h` in `games`.
- `observed_at` is the true retrieval time, so this is not a timestamp lie. But `grade_history` exposes `basis`, and the 'backfill' value that the schema allows is never used.
- **Fix:** write `'backfill'` when the game's availability basis is assumed.

### F7 LOW — schema and display hygiene (NEW)
- `games.season` comment says "ending year: 2024 = 2023-24" (0001). For the NFL it is the start year (docs/NFL_PLAN.md:25; real rows: Super Bowl `nfl:401772988` 2026-02-08 is season 2025, postseason).
- `teams` keeps only the latest abbr/name (`ingest.py:327-331`), so the 2019 London game shows as `CHI@LV` (Oakland at the time).
- Neutral flag limitation: in-USA relocated home games before 2014 (e.g. the 2010 Giants–Vikings game at Detroit) and the 2020 49ers "home" games in Glendale get home advantage. Not checked row by row; this is a model note for lane B.

### F8 INFO (cross-lane C) — 23 h ingestion gap across JAX@BAL; the result is stored honestly
- `raw_fetches`: the last day poll was `2026-11-06T00:47:27Z` (kickoff 01:15Z); the next retrieval was `2026-11-06T23:39:17Z`.
- `job_runs` has no row in between. The 23:39 run shows `next_due_at 2026-11-06T00:52:41Z`, so the job did not run; the policy did not skip it. `.nfl-live.log` has no `as_of` between those times.
- The result was stored as `observed_retrieval 2026-11-06T23:39:17Z`: correct, not backdated.
- The 2026-11-01 slate likewise first appeared final at `2026-11-02T22:06:49Z`, before the job was activated, and is stamped observed at that time. Correct.

## 2. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "Import is a pure function of cached bytes, so `replay` rebuilds the same rows" (ingest.py:3) | **contradicted** for the live NFL store | F1 (295 missing; replay FileNotFoundError ×2); F2 (quotes not rebuilt) |
| "Own commands … `nfl {migrate,ingest,replay,coverage}`" (NFL_PLAN.md:17) | replay **contradicted** on live data | F1 |
| "Ties are final results with equal scores" (espn.py:111) | verified | 16 regular + 10 preseason ties stored with both scores; matches known NFL ties 2002–2026 (e.g. `nfl:401772921` DAL 40–40 GB). Non-final rows with scores: 0. Synthetic 0–0 final stored as (0,0); scheduled as (NULL,NULL) |
| "international games and the Super Bowl carry neutralSite: true" / parser rule (NFL_PLAN.md:24,67-70) | verified (sample) | every postseason season 2002–2025 has exactly 1 neutral game (the SB); 2019–2026 list includes London/Munich/Mexico/Brazil/Madrid/Melbourne games and HOF games; venue countries in cached bytes: USA, Spain, Germany, Mexico |
| "Season = ESPN season year = the year the season starts" (NFL_PLAN.md:25) | verified | 2025 regular `nfl:401772966` 2026-01-04 is season 2025; SB `nfl:401772988` 2026-02-08 is 2025 postseason; 240 regular games in the following calendar year carry the prior season |
| "Games dated outside August … March are `other`" (NFL_PLAN.md:70) | verified | `nfl:241106007` (ESPN season 1988, dated 2004-11-06) is `other` |
| Pro Bowl → `other` (NFL_PLAN.md:24,55) | verified | 23 AFC/NFC/IRV/RIC rows are `other` |
| Store identity "set once / immutable" (0032) | verified | F3 fixture: insert/update/delete all raise |
| "market/research tables still allow only nba and nhl" (NFL_PLAN.md:16) | stale | 0033 widened `market_quotes` to nfl (documented later at NFL_PLAN.md:37-40) |
| 0035: replay adds nothing; same-second ordered by fetch id; older late import never overwrites; team change on final is a revision (0035 header) | verified (fixtures, SNAP code) | `fixtures.py` §2–5; real-data replay ×3 on 336 present fetches: 2nd/3rd `games_updated 0`, revisions 14, duplicates 0 |
| 0035 backfill "lower bound … nothing invented" | verified | live copy after migrate: last_seen NULL 0; `nfl:401873037` last_seen_fetch_id 611 = its updating fetch (later identical fetches up to 631 not counted, as documented) |
| "NFL live setup activated — 2026-11-02 22:14Z", activated 22:11:33Z (HANDOFF:226,241) | verified | `data/research/nfl_live/activation.json` activated_at `2026-11-02T22:11:33Z`; all 10 `game_forecasts` have generated_at ≥ 23:16:03Z, order_version `nfl-live-v1`, 2 games; no row has generated_at ≥ tipoff |
| "No historical forecast backfill" (NFL_LIVE.md) | verified | game_forecasts min generated_at 23:16:03Z > activation; predictions / market_forecasts 0 rows |
| "Initialized first data/nfl.sqlite from checked scratch; schema33, 96 quotes" (HANDOFF:237) | partially verified | schema 33 verified; raw bytes were not carried over (F1). 96 quotes not checked (store now has 10,776) |
| sdv_team_box: "Repo license MIT … content is ESPN's: personal, non-commercial research only" (sdv_team_box.py:3-4) | as recorded; not in NFL path | NBA-only (`espn_nba_team_boxscores`); `ingest_team_box` refused on an NFL store (F3 fixture) |
| ESPN NFL source "free, no key" (NFL_PLAN.md:21) | public + zero cost: verified by code (no key in `_get`). Permitted use: **not established** | Disney Terms of Use https://disneytermsofuse.com/english/ (accessed 2026-11-07, "Last updated May 24, 2024"): 2.A "for your personal, noncommercial use only"; 2.B(x) bars access "using a robot, spider, script, or other automated means" incl. data mining/web scraping. Already in DATA_SOURCES.md:112-122 and HANDOFF:79-80 (user decision); NFL_PLAN.md:21 omits the caveat and DATA_SOURCES.md has no NFL entry |

## 3. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `sqlite3 "file:$SP/stores/nfl.sqlite?immutable=1"` counts by season_type/status | sane split | other 24, post 276, pre 1463+1 postponed, regular 6345 final + 2 postponed + 119 scheduled | pass |
| tie / 0–0 / non-final-with-score queries | ties only on finals | ties: pre 10, reg 16; 0–0: 0; non-final with score: 0 | pass |
| postponed duplicates (2014 BUF–NYJ `nfl:400554331` → `nfl:400607990` neutral=1 at Detroit; 2017 MIA–TB `nfl:400951581` → `nfl:400981391`) | placeholder kept as postponed | as expected | pass |
| 2022 BUF@CIN canceled game | present as canceled, or absent | absent from the store (2022 regular = 271) | pass (absent; not checked against bytes, since they are missing) |
| raw path existence for all 510 `raw_responses` vs `REPO/data/raw` | all present | 295 missing (2026-11-01), 215 present | **fail** (F1) |
| `real_bytes.py`: sha256 + size of raw 490/437/296, parsed vs stored for `nfl:401873037` (BAL 10–33 JAX), `nfl:401873036` (SEA 17–34 CHI), `nfl:401873024` (DAL 30–33 NYG, Final/OT) | match | sha/size ok True ×3; status, scores, teams match True ×3 | pass |
| `live_copy_replay.py` (copy migrated 33→35, replay ×2) | replay unchanged | both replays FileNotFoundError; store unchanged | **fail** (F1) |
| `real_bytes.py` fresh replay ×3 of the present bytes | 2nd/3rd no-op; quotes rebuilt | no-op True; quotes 0 vs 10,776 | **fail** (F2) |
| `fixtures.py` §1 identity triggers/claims | all refuse | all refuse except direct SQL games insert | partial (F3) |
| `fx2.py` direct game_forecasts sport='nba' in NFL store | refused | inserted | **fail** (F3) |
| `fixtures.py` §2 A→B→A then replay ×2 | 3 revisions, unchanged | 3 revisions, unchanged True | pass |
| `fixtures.py` §3 two responses in the same second, replay ×2 | 2 revisions ordered by fetch, stable | (17 then 20; fetch 1,2), stable True | pass |
| `fixtures.py` §4 older retrieval imported late | ignored | kept 20, 1 revision | pass |
| `fixtures.py` §5 team change on final with the same score | new revision, availability moved | rev 2 with nfl:21; result_available_at → 04:00 observed; replay stable | pass |
| `fixtures.py` §6 blank score on a final | event rejected, others kept | whole month aborted (ValueError) | **fail** (F4) |
| `scan_status.py` over the cached NFL bytes | status names known | only 5 known names; OT finals = STATUS_FINAL/"Final/OT"/period 5 | pass (F5 note) |
| `pytest -q tests/test_nfl.py tests/test_replay_same_store.py` (SNAP) | pass | 32 passed | pass |
| `pytest -q tests/test_nfl_live.py -k "quote_migration or price_ingestion or wrong_store"` | pass | 3 passed | pass |
| `job_runs`/`raw_fetches` 2026-11-05T22:00Z–11-07T00:00Z | regular polling | gap 00:47:27Z → 23:39:17Z | info (F8) |

Not run:
- The full `test_nfl_live.py`: migration-list asserts are known to be stale after 0035, and the lead runs the gate.
- Any network re-fetch of the missing bytes: forbidden.
- Row-by-row neutral check of the pre-2014 in-USA relocations: no bytes for 2002–2026 (F1).
- Verifying the "96 quotes" HANDOFF figure.
- The live code (8539513) A→B→A behaviour: live never replays, and its old ordering is the HEAD pattern already reproduced in the soccer audit (HANDOFF:41).

## 4. Open questions / cross-sport

- **Lead:** do the NBA/NHL live raw caches have the same gap, i.e. stores initialised from scratch copies without `raw/`? Run `raw_cache.missing_files` on `$SP/stores/{nba,nhl}.sqlite` against `REPO/data/raw`.
- **Lane B (NFL):**
  - `nfl_eval.load_rows` reads each game's *current* score with its *current* `result_available_at`. After a correction, the game drops out of the model state until the correction time, instead of using revision 1 before it.
  - Neutral handling downstream is in place (`models/elo.py:35`, `models/points.py:54,80`, `nfl_eval.py:137`), so F7's relocations are the only home-field risk.
  - The 10 live forecasts were generated between T−119 min and T−120 min; their inputs are filtered at the fixed cutoff.
- **Lane C (NFL):**
  - F8 job gap (Mac asleep or offline?).
  - F4: does status 'other' block forecasts?
  - The live job runs 8539513 without 0035; migrating the live NFL store to 35 needs review and a go.
- **Shared code:** the `or "nba"` defaults (`db.py:26`, `jobs.py:66,90,818`, `coverage.py:466`) and the `_match_game` else-branch → NHL (`ingest.py:608`) affect any future sport. Soccer already has its own tables and triggers.
