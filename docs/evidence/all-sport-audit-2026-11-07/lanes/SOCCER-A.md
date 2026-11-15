# SOCCER-A — soccer data & storage audit (snap2, frozen 20:38Z)

Reviewer: SOCCER-A (read-only). Code audited: `$SP/snap2` (`SP=/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad`).
Work dir (all writes): `$SP/work/SOCCER-A/` (scripts in `scripts/`, outputs `survey.txt`, `survey2.txt`, `scenarios.txt`,
`edge.txt`, `mig35.txt`, stores `d1/`, `rebuild/`, `scen/`, `edge/`, `mig34/`, `mig35/`). No ESPN request made; cached bytes only.
Python: `REPO/backend/.venv/bin/python`, snap2 code on `sys.path` (`-I` for scripts reading cached payloads).

## Verdict on the prior audit's soccer findings (all reproduced against snap2 code)

| Prior finding (HANDOFF "Independent audit of soccer S0/S1") | Status on snap2 | Evidence |
|---|---|---|
| HIGH: FULL_TIME with extra time stored as regulation (169930, 416637) | **Fixed** → `decided_in='unknown'` + `decision_note` | survey.txt; mig35.txt; store copy rows |
| MEDIUM: same-second retrieval → duplicate revisions on every replay | **Fixed** (ordered by `(retrieved_at, fetch_id)`) | scenarios S2; store copy replay ×3 = 0 new revisions |
| MEDIUM: stale qualifier/aggregate/note carried across a result correction | **Fixed** | scenarios S5 (cleared), S5b (kept when same result) |
| MEDIUM: tie-fact change never moves an availability time | **Fixed** via `tie_facts_available_at` (result_available_at unchanged) | scenarios S4, S7 |
| MEDIUM: team change on a final with same score leaves no revision | **Fixed** (revision carries teams; result_available_at moves) | scenarios S3 |
| LOW: home==away event aborts the day | **Fixed** (`soccer_rejections`, rest of day imported) | edge.txt |
| LOW: older retrieval imported late | **Fixed** (skipped as a whole) | scenarios S6 |
| A→B→A replay | **OK** (3 genuine revisions, replay ×2 adds 0) | scenarios S1 |

## 1. Findings (most severe first)

No CRITICAL or HIGH finding in the soccer data/storage lane on snap2. No live soccer store exists, so none of the
issues below reaches served live data today.

### F1 — MEDIUM (NEW) — a migrated store and a store rebuilt from the same log disagree on `tie_facts_available_at`
- Files: `backend/app/migrations/0035_result_integrity.sql:90-93` (backfill = `MAX(result_available_at, updated_at)`) vs
  `backend/app/soccer.py:140` (new insert = `result_available_at`, i.e. kickoff+4 h for historical first sight).
- Evidence: `scripts/rebuild.py` (fresh store, same 123 raw_fetches, snap2 replay) vs the replayed store copy `d1/`:
  `diff d1/m.txt rebuild/m.txt` → 12 rows differ, all 2025 UEFA second legs, e.g. `soccer:uefa.champions:733144`:
  copy `tie_facts_available_at=2026-11-07T17:52:15Z`, rebuild `2025-03-11T21:45:00Z` (result_available_at in both:
  `2025-03-11T21:45:00Z`). Reproduced again on a HEAD-built 0034 store migrated with snap2 (`mig35.txt`: 733144
  tie_facts `2026-11-07T10:00:02Z`, result `2025-03-11T21:45:00Z`); replay does not converge it.
- Why it matters: the same raw log gives two different "when were these tie facts knowable" values depending on whether
  the store was migrated or rebuilt; SOCCER_PLAN S2 acceptance requires a byte-identical replay. The UI shows it
  (`frontend/src/pages/SoccerPages.tsx:194-195` "Tie facts known" when it differs from result availability), so a migrated
  store shows "Tie facts known 7 Nov 2026" for a March 2025 tie. Direction is conservative (later), so no look-ahead.
- Fix (text): pick one rule for both paths. Either backfill with `result_available_at` when
  `result_availability_basis='assumed_tipoff_plus_4h'` and the tie facts were present at first import (same as insert),
  or make the insert path use the retrieval time; add a test that migrate(0034 store)+replay == fresh rebuild.

### F2 — LOW (NEW) — classifier re-derivation is stored as a source correction in the append-only revision history
- Files: `backend/app/soccer.py:145-156` (reclassify on replay of the same retrieval) + `soccer.py:110` (`_record_revision`
  is called for `reclassified`).
- Evidence (`mig35.txt`, 0034 store built with HEAD code, then snap2 migrate + replay):
  `R ('soccer:uefa.champions:169930', 1, 'regulation', 2, 0, '2026-11-07T10:00:00Z', None)` and
  `R ('soccer:uefa.champions:169930', 2, 'unknown', 2, 0, '2026-11-07T10:00:00Z', 'soccer:111')`; same for 416637.
  replay1 `{"revisions": 2, "reclassified": 2}`, replay2 0/0.
- Why it matters: revision 1 keeps the old wrong "regulation" label forever, with the same `observed_at` as revision 2,
  and nothing marks it as an importer artifact; `match_detail.result_revisions` / the UI revision list present it as an
  earlier source version. An as-of reader must tie-break on `revision`. result_available_at correctly does not move.
- Fix: record the importer version (or a `reason='reclassified'`) on the revision row, or have `match_detail` label it;
  document that a pre-v2 store must be replayed after 0035 (migration alone leaves old labels; `decision_note` NULL).

### F3 — LOW (NEW) — a "final" with one score missing stores the other score on a non-final row
- File: `backend/app/sources/espn.py:642-645`.
- Evidence (`edge.txt`): home score `None` on 733148 → stored `('733148','other','STATUS_FULL_TIME', None, 2, None)`.
  The 0035 CHECK only ties *both* scores to `status='final'`, so a half score passes. `_match` hides scores on non-finals
  (`soccer.py:318`), so the API does not show it.
- Fix: clear both scores when either is missing (`row.home_score = row.away_score = None`) before setting `status='other'`.

### F4 — LOW (pre-existing, shared `ingest._upsert_team`) — team names updated from stale retrievals
- Files: `backend/app/soccer.py:105-106` (teams upserted before the staleness check in `_upsert_match`),
  `backend/app/ingest.py:354-360` (unconditional rename). An older retrieval imported late renames the team even though
  its match data is skipped. Not reproduced with a name change (cached payloads: 285 team ids, 0 with >1 name/abbr —
  `scripts/teams.py`). Affects NBA/NHL/NFL equally. Fix: only rename when the retrieval is newer than the team's last
  rename (needs a `teams.updated_at`), or upsert teams after `_upsert_match` returns non-stale.

### F5 — LOW (NEW, hygiene) — `soccer_goal_minutes` (dedup of duplicated pre-2010 goals) is test-only dead code
- File: `backend/app/sources/espn.py:511-544`; only callers are `tests/test_soccer.py:528-539,693`. Stored facts never
  use goal lists (scores come from competitor `score`; ET detection `_extra_time_minutes` reads raw minute strings, where
  duplicates do not matter). Survey of 2,306 real finals: goal list unusable for 42, deduplicated for 4
  (`survey2.txt`). Not a defect now; any S3 use must treat these 42 as ungradable. Keep or remove; do not cite it as a
  stored-data guarantee.

### F6 — LOW (NEW) — residual silent paths in the parser
- `espn.py:615-616`: an event without exactly home+away competitors is skipped silently (no rejection row). 0 such events
  in the cache.
- `espn.py:630` `int(season.get("type"))`, `ev["competitions"][0]`, `c['team']['id']`: a malformed event still aborts
  the whole day (only home==away was moved to `soccer_rejections`).
- `espn.py:621-625`: a final name with an unexpected type id or `completed` not True becomes `other` silently;
  STATUS_CANCELED/ABANDONED/SUSPENDED mappings were verified only synthetically (none in the cached bytes).
- Fix: route all per-event parse failures to `soccer_rejections` with the reason.

### F7 — LOW (residual risk, not verified) — FULL_TIME extra time with no period/clock/minute evidence
- `espn.py:550-561,586-594`: in 2001-02-style rows (period 2, clock 0, stoppage written 91'-95') an extra-time second leg
  labelled FULL_TIME with goals ≤105' would still be "regulation". No such row exists in the cache (2001 UEFA cache is
  group stage only), so neither confirmed nor excluded. Recommend: for `leg==2`/final rows before 2005, treat FULL_TIME
  as `unknown` unless the aggregate/note proves 90 minutes, or exclude those seasons from any 90-minute grading.

## 2. Claim table

| Claim (source) | Status | Evidence |
|---|---|---|
| "New classifier over all 2,306 cached real finals: FULL_TIME→regulation 2286, AET→extra_time 3, PEN→penalties 5, unknown 12" (SMOKE_TEST.md:1821-1823) | verified | survey.txt: 998+279+200+191+215+213+190=2286; ET 2+1; PEN 4+1; unknown 5+1+1+2+1+1+1=12 |
| "S1: 2 extra-time matches labelled FULL_TIME, 10 false AET labels" (SOCCER_PLAN.md:15-16) | verified | survey.txt: FULL_TIME→unknown 169930, 416637; AET→unknown 9 league (2001-02) + UCL 21220 |
| 169930: "period 4, clock 122'" → unknown | verified | store copy + snap2 parse: `STATUS_FULL_TIME but period 4, clock 122', event minutes 107', 107', 109'` |
| 416637: "period 4 … goals 97'/117'" → unknown; qualifier Dnipro on away goals | verified | `…period 4, clock 120', event minutes 97', 117', 120'`; agg 2-2, qualified soccer:600 |
| "only a consistent regulation ending shows a 90-minute result" (SOCCER_PLAN.md:17-18) | verified (code) | `soccer.py:304`; no 90-minute/derived score column in 0035 |
| "derived values stay disabled" (espn.py:488, SOCCER_PLAN.md:18-19) | verified | `soccer_goal_minutes` has no production caller (F5) |
| "two different responses in the same second are ordered by fetch id" (0035:4-5, ingest.py:369) | verified | scenarios S2 |
| "replaying an already-imported log adds nothing" (0035:4) | verified | store copy replay ×3: 0 inserted/updated/revisions; 3rd snapshot identical |
| "an older retrieval imported late cannot overwrite newer data" (0035:5) | verified | scenarios S6 |
| "Revisions record the two teams… Earlier revision rows keep NULL teams" (0035:7-8) | verified | mig35: 58/60 NULL-team revisions after migration; new ones carry teams |
| "tie facts … get their own availability time" (0035:10) | verified, but migration/rebuild disagree | F1 |
| "B … 203 matches, 2 unknown endings" (SMOKE_TEST.md:1841) | verified | store copy: 203 rows, 2 unknown |
| "corrections … are recorded in HANDOFF.md" (SOCCER_PLAN.md:26-28) | contradicted | snap2 HANDOFF has no entry for the 0035 fixes; only the audit entry (lines 6-54) and the older S0 entry (line 60, still says "now row.updated_at", superseded by last_seen) |
| "Payload whose league ≠ requested competition is rejected before writing" (HANDOFF:97) | verified | edge.txt: SoccerPayloadMismatch, raw_responses 0 |
| "malformed events are recorded in an append-only rejection table instead of aborting the day" (0035:10-11) | partly | home==away only; other malformed events still abort (F6) |

## 3. Checks run

| Command | Expected | Actual | Result |
|---|---|---|---|
| `sqlite3 "file:$SP/stores/soccer.sqlite?immutable=1" "select competition_id,status,decided_in,count(*) …group by 1,2,3"` | per-competition counts | eng.1 19 final/1 in_progress/5 sched; esp.1 18/1/6; fra.1 17/0/8; ger.1 16/1/3; ita.1 28/1/6; UCL 31 reg, 2 pen, 1 unknown; UEL 36 reg, 1 ET, 1 pen, 1 unknown | info |
| per-competition example (store copy) | one parsed final each | eng.1 Crystal Palace 0-2 Newcastle; esp.1 Athletic 4-0 Getafe; fra.1 Angers 0-0 Lorient; ger.1 Hoffenheim 3-0 Leverkusen; ita.1 Inter 2-1 Fiorentina; UCL Arsenal 2-2 PSV (agg 9-3, Arsenal); UEL Man Utd 5-4 Lyon extra_time (agg 7-6) | pass |
| `python -I scripts/survey.py` (snap2 parser over all cached ESPN scoreboards) | no false labels | see claim table; 0 parse errors; 0 league ET labels; 0 league FULL_TIME→unknown | pass |
| `python -I scripts/survey2.py` | no ET hidden in regulation | regulation max clock: eng 90, esp 98, ger 91, UCL 96, others 90; level-aggregate 2nd legs labelled regulation only with "away goals" notes (182821, 410538); no regulation row with ET/penalty note | pass |
| `NBA_DATA_DIR=$SP/work/SOCCER-A/d1 python -m app.cli soccer replay` ×3 on store copy | 0 changes | `{"responses":123,…0,0,0,0}` ×3; first run only moved `last_seen_*` on 12 rows (backfill lower bound, documented) | pass |
| `python -I scripts/rebuild.py` (fresh store from same log) vs replayed copy | identical | 12 rows differ in tie_facts_available_at; revisions differ only in NULL teams (documented) | **fail** (F1) |
| `python -I scripts/scenarios.py` S1–S7 | see verdict table | as expected for all 7 | pass |
| `python -I scripts/edge.py` | mismatch rejected; home==away logged; statuses | mismatch rejected (0 raw rows); rejection row; abandoned/canceled mapped; half score stored on 'other' | pass except F3 |
| HEAD 0034 store (`git archive 79ff6ec backend/app`, `mig_phase1.py`) → snap2 `mig_phase2.py` | triggers kept, FK clean | triggers 98→101 (none removed; +3 soccer_rejections), index +raw_fetches_raw_time, `foreign_key_check` [], integrity ok, foreign_keys restored 1; HEAD labelled 169930/416637 "regulation", replay reclassified both | pass (+F2) |
| `PRAGMA foreign_key_check` on immutable nba/nhl/nfl/soccer copies | 0 | 0/0/0/0 | pass |
| `python -m pytest -q -p no:cacheprovider tests/test_soccer.py tests/test_replay_same_store.py` (snap2/backend) | pass | 60 passed, 1 warning, 2.37 s | pass |
| `python -I scripts/teams.py` | ids shared across competitions; no cross-store collision | 285 ids, 64 in both domestic and UEFA (same club = same `soccer:<id>`), 0 with >1 name; other stores use `espn:`/`nhl:`/`nfl:` ids, games `espn:`/`nhl:`/`nfl:`; 0 event ids in >1 competition | pass |
| `python -I scripts/statuses.py` | status inventory | observed: FULL_TIME(28) 2286, FINAL_AET(45) 15, FINAL_PEN(47) 11, POSTPONED 57, SCHEDULED 28 (score "0" string), FIRST_HALF 3, HALFTIME 2 | info |

Not run: any ESPN fetch (forbidden); full suite / `--ready` (lead's gate); API/UI checks (lane C); NBA/NHL/NFL effects of
0035's `games` backfill (other A lanes).

Side effect to report: running `python -m app.cli` and pytest from `$SP/snap2/backend` wrote `__pycache__/*.pyc` into
snap2 (55 .pyc newer than my first survey; may include other lanes). No source file was changed; exclude `__pycache__`
if the snapshot is hashed.

## 4. Open questions / cross-sport concerns
- 0035 runs in every store (adds `games.last_seen_*`, backfills, rebuilds empty soccer tables, runs a whole-database
  `PRAGMA foreign_key_check` before commit). The four store copies have 0 FK violations today, but any future dangling
  reference anywhere in NBA/NHL makes 0035 refuse to apply. NBA/NHL/NFL lanes should check the `games` backfill.
- `ingest._upsert_team` stale-rename (F4) is shared by all sports.
- Docs: snap2 HANDOFF lacks the entry SOCCER_PLAN.md:26-28 points to; HANDOFF line 60 ("now row.updated_at") is superseded.
- A store built before importer v2 needs `soccer replay` after migrating to 0035 to get the new endings (F2); not stated
  in the migration or docs.
