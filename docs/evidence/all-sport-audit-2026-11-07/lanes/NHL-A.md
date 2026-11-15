# NHL-A — NHL data & storage audit (read-only), 2026-11-07

Scope: NHL data path in SNAP (`$SP/snap`, HEAD 79ff6ec + uncommitted diff). Store copy `$SP/stores/nhl.sqlite` (schema 31).
Writable reproductions only under `$SP/work/NHL-A/` (copy migrated to 35 with SNAP code; fixture stores in `fx/`).
Raw bytes read-only from `REPO/data/raw` (passed as `cache_dir`; replay only reads). Python: REPO venv, `PYTHONPATH=$SP/snap/backend`.

## 1. Findings (most severe first)

### F1 MEDIUM — Shot (xG) data for current-season games is stamped available up to 37 h before it was retrieved
- File: `backend/app/nhl_shots.py:27-29` (`_policy`: >24 h after start ⇒ `historical_backfill`) and `:47-48`
  (`available = max(result_available_at, tipoff_at)` for backfill). Sport: NHL.
- Evidence (store copy): `select g.season, i.policy, g.result_availability_basis, count(*), avg/max((retrieved_at-available_at)*24) from nhl_pbp_imports i join games g …`
  → `2027|historical_backfill|observed_retrieval|22|18.6 h avg|36.7 h max`, `2027|historical_backfill|assumed|6|55.5|62.9`, `2027|observed|…|236|0|0`.
  Example fetch 14624 `nhl:2026020006`: result observed 2026-10-01T03:15:15Z, pbp retrieved 2026-10-02T15:54:30Z, available_at 2026-10-01T03:15:15Z.
  Also 16149 (`nhl:2026020139`, 24.2 h), 16727 (23.5 h).
- Why it matters: the result rule (`ingest.py:386-398`) has a "tracked live" clause so a late-observed result of a game the
  store followed live is stamped at its retrieval; the shot rule has no such clause, so any game whose pbp fetch slips past
  24 h (outages like 2026-11-06 02:00–23:38Z, see F6) gets shots dated before the system had them. Live forecasts are not
  affected (rows did not exist yet), but every retrospective consumer that filters `available_at <= cutoff`
  (`agreement.py:134`, `adaptive.py` `_events`, `nhl_eval.py:553`, `tuning_study.py:199`, `expanded_search.py:59`,
  `diagnostics.py:55`) would admit shots the live system could not have used ⇒ live ≠ replay, latent look-ahead for the
  2026-27 season.
- Fix (text): use `historical_backfill` only when the game's result basis is `assumed_tipoff_plus_4h` (truly historical);
  otherwise `available_at = retrieved_at`. Add a failing-first test: observed result, pbp fetched 30 h later ⇒ available_at = retrieval.
  Existing 28 rows are append-only; record them (or add a corrected-availability view) rather than editing.
- NEW (not in HANDOFF).

### F2 MEDIUM — 62 stored NHL decisions disagree with what the current importer derives from the same cached bytes (preseason/other shoot-outs stored as OT); replay never corrects them
- Files: original `nhl_stats.decision` (commit 4d28c3f, `backend/app/sources/nhl_stats.py:65-73`: period>3 and gameType≠2 ⇒ "OT")
  vs SNAP/live `backend/app/sources/nhl_stats.py:79-87` (gameType≠3, period 5 ⇒ "SO"). `ingest.py:410-413` skips every
  retrieval not newer than `last_seen_*`, so the 2015-16…2025-26 season lists (each fetched once, 2026-10-01T03:14-03:15Z) are
  never re-derived.
- Evidence: store copy `select season_type, decision, periods, count(*) …` → `preseason|OT|5|61`, `preseason|SO|5|7` (only
  2026-27 ones are SO), `other|OT|5|1`. Raw bytes sha `1fb6e70d…f42351` (season 2025-26): `{'id': 2025010008, 'gameType': 1,
  'gameStateId': 6, 'period': 5, 'homeScore': 2, 'visitingScore': 3}` → stored `nhl:2025010008 OT 5`.
  Fresh rebuild from the same retrieval log (`work/NHL-A/fresh_rebuild.py` → `fresh.sqlite`) vs migrated+replayed copy:
  `decisions differ 62` (`preseason OT→SO 61`, `other OT→SO 1`), `rev decision differ 62`; same-store replay ×2 left
  `game_decisions` digest unchanged (`4da00d16a8dc`).
- Why it matters: reg3 settlement and regulation goals are unaffected (OT and SO both ⇒ 'mid' / winner−1), and the sealed
  eval sets exclude preseason. But a wrong fact is stored and shown for preseason games (memory: preseason is graded), and the
  module docstring claim "replay rebuilds the same rows" is false for this store. Any fingerprint over all decisions
  (`nhl_eval._decisions_digest`) differs between this store and a rebuild.
- Fix (text): one explicit, logged re-derivation of `game_decisions` from cached bytes for rows where
  `decision(gameType, periods)` ≠ stored, appended as observed revisions with their own time (not backdated); or bump
  IMPORTER_VERSION and have replay re-derive decisions from the latest retrieval regardless of `last_seen`. Test: preseason
  period 5 stored as OT before the fix becomes SO with a revision.
- NEW.

### F3 MEDIUM — One malformed final row aborts the whole NHL schedule/results import
- File: `backend/app/sources/nhl_stats.py:121-122` (`int(g["homeScore"])` / `int(g["visitingScore"])`), inside the single
  transaction of `ingest.py:485-492`.
- Evidence (`work/NHL-A/decision_fixture.py`, S7): final row without `homeScore` → `KeyError 'homeScore'`; with
  `homeScore: null` → `TypeError int() … NoneType`. The transaction rolls back including the `raw_fetches` row (the gz file
  stays in the cache). Not observed in real data: 0 finals with null scores in any cached season list (2015-16…2026-27).
- Why it matters: a single bad game would stop status/result refresh for every NHL game until the source changes. Soccer
  got a rejection table in 0035; NHL did not.
- Fix (text): treat a final without both integer scores as `status='other'`/rejected with a counted reason (append-only
  rejection row like `soccer_rejections`), keep the fetch log. Failing-first fixture as S7.
- NEW.

### F4 LOW — One stored result availability is 20 h earlier than when it was learned (rule changed, row not re-stamped)
- Evidence: `nhl:2026020007` (COL–LAK, tipoff 2026-10-01T02:00Z): live store `result_first_seen_at 2026-10-02T02:15:03Z`,
  `result_available_at 2026-10-01T06:00:00Z`, basis `assumed_tipoff_plus_4h`; fresh rebuild with SNAP code gives
  `2026-10-02T02:15:03Z observed_retrieval` (game first seen 03:15Z in progress ⇒ "tracked", `ingest.py:391-393`).
  Only availability difference in the whole fresh-vs-live comparison.
- Impact checked: neither team played between 2026-10-01T06:00Z and 2026-10-03T12:00Z; 0 rows in game_forecasts /
  recovered_forecasts / adaptive_forecasts / research_cohorts for games in that window. No consequence found; determinism only.
- Fix: re-stamp via an appended revision or document as known exception. NEW.

### F5 LOW — Decision-only correction leaves games.updated_at and games.last_raw_id on the older response
- File: `backend/app/ingest.py:562-577`. Evidence S1: after OT(T1)→SO(T2) same score: `result_available_at=T2`, but
  `updated_at=T1`, `last_raw_id=1` (the OT response); `game_decisions.last_raw_id=2`. Confirms the 0035 comment "NHL
  decision changes never moved updated_at". Consumers keyed on `MAX(updated_at)` (`providers/real.py:1044-1047`
  `_cache_key`; `nhl_eval.py:265` adds a decisions digest, `freshness.revision` adds `MAX(revision_id)`, so those two are covered).
  Provenance of the current availability is only recoverable via game_decisions/result_revisions.
- Also: `_upsert_decision` still orders by `raw_responses.first_retrieved_at` (`ingest.py:565-570`); harmless now because the
  game-level `last_seen` gate runs first (S2/S3/S5 show no flips), but it is the pattern 0035 removed elsewhere.
- Fix: move `updated_at`/`last_raw_id` (or a dedicated `result_raw_id`) when a decision correction moves availability;
  drop the first_retrieved_at comparison. NEW (the first_retrieved_at note matches HANDOFF line ~25 "decisions … still ordered by first_retrieved_at").

### F6 LOW (ops, cross-lane C) — Result collection gaps up to ~22 h; availability is stamped honestly
- Evidence: no nhl_stats fetch between 2026-11-06T02:00Z and 23:38:48Z; the 11 games of 2026-11-06 UTC got results 21.1–23.6 h
  after start (2026-11-04: 11.9–13.9 h). All stamped `observed_retrieval` at the actual retrieval (not backdated). Close to the
  24 h OBSERVED_WINDOW; feeds F1.

### F7 LOW — Docs volume claim contradicted; NHL terms restriction unresolved
- `docs/DATA_SOURCES.md:200-202`: "Request volume is kept to one season request per refresh (≥ 30 min apart)". Store:
  day-list requests since 2026-11-02 at min gap 58 s, mean 197 s on 2026-11-07 (374 day lists + 20 season lists that day).
- NHL.com Terms of Service (https://www.nhl.com/info/terms-of-service, "Last Updated: October 29, 2025", read 2026-11-07):
  §2 forbids "unauthorized spidering, scraping, or harvesting of content or information" and "any other unauthorized automated
  means to compile information"; §7 "only for non-commercial, informational, personal use"; §7 "you may not copy, reproduce …
  enter into a database". Gambling/betting not mentioned. Explicit restriction (automated compilation, database) vs
  interpretation (whether undocumented api.nhle.com / api-web are "Services" under these terms; docs say "the stats API has no
  separate terms") vs unknown (any licence for api-web pbp). The repo records a user "personal research decision" for ESPN
  (2026-09-30); I found no recorded equivalent decision for NHL.com. Fix: update DATA_SOURCES volume line; record the NHL
  decision or ask the user.

### F8 LOW — Hygiene
- `nhl_shots.game_id` has no FK to games (`0012+` schema); checked: 0 orphan shots, 0 shot/import game mismatches.
- 92 goalie snapshots retrieved at/after scheduled start (ESPN still "scheduled"); every consumer filters
  `retrieved_at <= as_of`, so no leak; note only.
- Injury archive captures: `captured_at` = Wayback capture time, `retrieved_at` up to 13 days later (e.g. `20261001182502.json`:
  captured 2026-10-01T18:25:02Z, retrieved 2026-10-14T06:04:18Z). Gating uses captured_at (public-before-cutoff rule, documented),
  so for 2026-10-01…10-15 (before live captures began 2026-10-15T21:06Z) a retrospective rebuild can use reports the live
  system did not hold. Disclosed policy; flag only.
- `teams` cache in `providers/real.py:1044-1061` keys on `MAX(games.updated_at)`; an NHL /team rename alone would not refresh
  names (cross-lane C).

## 2. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "Final scores include the single goal credited to a shootout winner" (`nhl_stats.py:5`, DATA_SOURCES.md:188) | verified | pbp raw sha `e5c79066…57ad` (2025020006): non-SO goals 3–3, `lastPeriodType SO`; season list 3–4; store 3–4 SO |
| HockeyResult.regulation removes the deciding OT/SO goal (`models/hockey.py:19-28`) | verified (logic) | 2053 reg OT + 1041 SO all 1-goal margins; 0 ties stored |
| "NHL decision changes never moved updated_at" (0035 comment) | verified | S1: updated_at stays T1 after OT→SO |
| A decision-only correction is a revision and moves availability (`ingest.py:573-577`) | verified | S1 revisions `[(1,'OT',T1),(2,'SO',T2)]`, result_available_at=T2 |
| Replay into the same store adds nothing; A→B→A, same-second ordered by fetch id (0035 §1, `ingest.py:363-374`) | verified | S1–S5 replay×2 identical; real copy replay×2: revisions 16003→16003, decisions/goalies/quotes digests unchanged, games_updated 0 |
| "Import is a pure function of cached bytes, so replay rebuilds the same rows" (`ingest.py:3`) | contradicted for this store | F2 (62 decisions), F4 (1 availability) |
| Older retrieval imported late cannot overwrite (0035 §1) | verified | S4: SO@T2 then OT@T1 → SO, 1 revision |
| Missing vs zero scores | verified | raw unplayed rows carry 0–0; store 1078 scheduled + 117 canceled + 3 postponed all NULL scores; finals never NULL |
| Utah continuity "rating_key {nhl:59, nhl:68 → nhl:53}" (`sports.py:40-42`) | verified (mapping exists) | teams: ARI nhl:53 2016–2024, Utah HC nhl:59 2025, Utah Mammoth nhl:68 2026–27; both Utah abbr `UTA` (no unique on abbr; ids distinct) |
| Preseason distinguishable | verified | `season_type` from gameType 1; 1140 final preseason; eligible types (`sports.py:36`) exclude it |
| Goalie listing time = retrieval time; as-of by retrieved_at (`ingest.py:646-679`) | verified | snapshot 26337…27577 (`nhl:2026020267` home, all `expected` Grubauer, fetches 18327…18806, sha e.g. `606df735…6661`); GOALIE_AS_OF_SQL / `listed_starters` filter `retrieved_at <= ?` |
| Live injury captures stamped at retrieval (`nhl_injuries.py:150-160`) | verified | `live-20261107203645.json` captured_at = retrieved_at = 2026-11-07T20:36:45Z, sha ok; loader enforces equality (`nhl_player_ratings.py:331`) |
| "one season request per refresh (≥ 30 min apart)" (DATA_SOURCES.md:201) | contradicted | F7 |
| NHL.com terms quote "non-commercial, informational, personal use" (DATA_SOURCES.md:200) | verified | ToS §7, page dated 2025-10-29 |
| HANDOFF L70 "inherited NBA/NHL/NFL replay-ordering pattern in ingest._upsert_game (not changed)" | superseded | SNAP `ingest.py:363-414` now uses last_seen; NHL verified above |
| Internet Archive (Wayback) terms | unverified | archive.org/about/terms.php returned no terms text via fetch |

## 3. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `sqlite3 file:$SP/stores/nhl.sqlite?immutable=1` decision × period × season_type counts | consistent with `decision()` | preseason OT/5 = 61, other OT/5 = 1 | fail (F2) |
| Raw season list 2025-26 (`gzip -dc …1fb6e70d….json.gz`, sha matched) for 2025010008/2025010101 | period 5 preseason | `gameType 1, period 5` | evidence F2 |
| `python work/NHL-A/migrate_and_count.py work/NHL-A/nhl.sqlite` (SNAP) | 0032–0035 apply, no NULL last_seen | applied 4; last_seen_null 0, fetch_null 0 | pass |
| `python work/NHL-A/replay_twice.py work/NHL-A/nhl.sqlite REPO/data/raw` | no new revisions/decisions | replay1 & 2: games_updated 0, revisions 16003, decisions/goalies/quotes digests unchanged; games digest changed once (1409 last_seen_* advanced: backfill lower bound), identical after replay2 | pass |
| `python work/NHL-A/fresh_rebuild.py …pristine… work/NHL-A/fresh.sqlite REPO/data/raw` + diff vs replayed copy | identical facts | games status/score/teams identical; 62 decisions differ; 1 availability differs; revisions 16003=16003, observed_at equal | fail (F2, F4) |
| `python work/NHL-A/decision_fixture.py work/NHL-A/fx` S1–S6 | revision per genuine change, replay no-op, fresh=live | all as expected (output `work/NHL-A/decision_fixture.out`) | pass |
| same, S7 (final without score) | rejected/counted | KeyError / TypeError abort | fail (F3) |
| `pytest -q tests/test_replay_same_store.py tests/test_result_revisions.py -k "nhl or decision or regulation"` (SNAP) | pass | 13 passed, 22 deselected | pass |
| pbp availability vs retrieval query (F1) | backfill only for historical | 28 current-season backfills | fail (F1) |
| market/goalie match coverage 2026-09-20…11-08 | regular games matched | 259/267; 8 unmatched all before ESPN collection began 2026-10-01T03:30Z; preseason 0 (ended before collection) | pass |

Not run: dev API / UI checks (lane C); forecast-feature use of goalie/injury snapshots at cutoff vs generation time (lane B);
NHL raw for every SO game (sampled 1 pbp + 1 season list); Wayback terms (page not retrievable as text); no network beyond
two terms pages.

## 4. Open questions / cross-sport
- Shared `ingest._upsert_game`/`is_newer_retrieval` also serve NBA/NFL; the "rule changed, rows not re-derived" pattern (F2/F4)
  likely exists for NBA/NFL stores built before the tracked-live clause — NBA-A/NFL-A should run the same fresh-vs-store diff.
- `nhl_player_ratings` keys `recent_players[tid]` by raw team id (no rating_key), so Utah HC→Mammoth (59→68) resets the
  expected-lineup history at the rename — lane B to confirm.
- Does the user's ESPN personal-research decision extend to NHL.com/api.nhle.com and api-web pbp polling at ~1–3 min? Needs a recorded answer (F7).
- Wayback (Internet Archive) terms for automated CDX + `id_` retrieval not verified.
