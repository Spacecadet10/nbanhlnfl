# NBA-A — data & storage audit (NBA), 2026-11-07

Reviewer NBA-A, read-only. Code audited: SNAP ($SP/snap, HEAD 79ff6ec + uncommitted diff). Store: $SP/stores/nba.sqlite (schema 31).
Writable work, scripts and outputs: $SP/work/NBA-A/ (scripts/, counts_*.txt, replay*_snap.txt, head_runs.txt, probe_snap.txt,
probe_head.txt, rawgame_401901822.txt). Python = REPO/backend/.venv/bin/python, OMP_NUM_THREADS=2. Raw cache read via a symlink
($W/snapdata/raw -> REPO/data/raw); the replay path only calls raw_cache.load (read-only), nothing was written there.

## 1. Findings (most severe first)

### F1 MEDIUM — A team change on a final whose latest revision predates 0035 appends no revision (NEW)
- File: backend/app/ingest.py:547-549 (`if not with_teams or last["home_team_id"] is None or ...: return`); migration
  backend/app/migrations/0035_result_integrity.sql header item 2. Sports: NBA, NFL, NHL (shared `_record_result_revision`).
- Evidence: after `migrate`, all 9,612 NBA result_revisions have NULL teams (0035 adds the columns without backfill). Probe S7
  (`PYTHONPATH=$SP/snap/backend:$SP/snap/backend/tests python scripts/probe.py $W/probe_snap`): store at 0034, final A at t1,
  migrate to 0035, then same score with away team espn:10 -> espn:11 at t2. Result: games row updated (away espn:11,
  result_available_at moved to t2) but result_revisions still only `[1, None, None, 124, 115, t1]`.
- Why it matters: 0035 claims "a team change on a final with an unchanged score is a recorded correction". That holds only for games
  whose first revision is written after 0035; for every existing final (9,611 NBA), the correction is silent in the append-only log
  while games/availability change. The new test `test_a_team_change_on_a_final_is_a_recorded_correction` only covers a fresh 0035 store.
- Fix (text): have `_upsert_game` return the pre-update teams (or a `teams_changed` flag) and append a revision whenever teams changed,
  regardless of NULL teams on the latest revision; add a failing-first test with `db.migrate(conn, upto=34)` as in S7.

### F2 MEDIUM — One final with a missing/empty score aborts the whole ESPN response (NEW)
- File: backend/app/sources/espn.py:153-154 (`int(sides["home"]["score"]) if final`). Sports: NBA, NFL (shared `_parse_scoreboard`).
- Evidence: probe S6 — a payload with two finals, one with `"score": ""`: `ValueError: invalid literal for int() with base 10: ''`,
  games_written 0, raw_responses 0 (transaction rolled back; the gzip file itself is still written by store_fetch before the insert).
- Good: a missing score never becomes 0 (non-finals store NULL even though ESPN sends "0"; real cache scan: 180,510 non-final
  competitor scores are "0", all stored NULL). Bad: a month request carries ~100-230 games, so one malformed event blocks every
  status/result update in that request until ESPN fixes it, with no record of why. Not observed in the real cache (79,794 final
  competitor scores, all numeric).
- Fix: parse per event inside try/except, record refused events append-only (as 0035 does for soccer with `soccer_rejections`) and
  import the rest; keep refusing to invent a score.

### F3 MEDIUM — Source terms: recorded clauses relevant to current use are not reconciled with the new real-money ticket activity (partly NEW)
- Files: docs/DATA_SOURCES.md:13 vs :112-119; backend/app/sources/espn.py:3; docs/RESEARCH.md:33; backend/app/sources/nba_injury.py:4-5.
- Evidence (primary pages read 2026-11-07):
  - Disney Terms of Use, https://disneytermsofuse.com/english/ ("Last updated: May 24, 2024"): 2.A licence "for your personal,
    noncommercial use only"; 2.A excludes use "in connection with any use, creation, development, modification, prompting, fine-tuning,
    training, testing, benchmarking or validation of any artificial intelligence or machine learning tool"; 2.B.viii no "commercial or
    business-related use"; 2.B.x no access "using a robot, spider, script, or other automated means ... for the purposes of creating or
    developing any AI Tool, data mining or web scraping". (Quotes via WebFetch summariser; wording should be re-read by a human.)
  - NBA.com Terms of Use, https://www.nba.com/termsofuse ("LAST UPDATED: July 13, 2026"): s.1 download "to any single computer only for
    your personal, noncommercial use"; s.9 NBA Statistics "may not be used in connection with any gambling activity (including legal
    gambling activity)"; no clause on automated access or AI found.
  - HANDOFF (Nov 7) records hourly real-money ticket reviews with a $5 daily cap, built on these forecasts.
- Separation: public accessibility — ESPN site.api, NBA injury PDFs, sportsdataverse GitHub releases all fetched without key (repo
  evidence). Zero cost — yes for all three (no key/account; repo docs). Permitted use — explicit text: ESPN personal/noncommercial,
  automated extraction for data mining and ML training/validation outside the licence; NBA personal/noncommercial, statistics not for
  gambling. Interpretations (mine, not legal advice): whether a small personal bankroll is "commercial" and whether injury-report PDFs are
  "NBA Statistics" are open; the ML-validation clause in Disney 2.A reads directly on training/validating the forecasting models.
- Docs: espn.py:3 and DATA_SOURCES.md:13 still say ESPN has "no published terms", contradicting DATA_SOURCES.md:112; DATA_SOURCES.md
  does not record the 2.A ML clause (RESEARCH.md:33 does). The user's 2026-09-30 acceptance was for "personal research"; it predates
  stake-based ticket reviews.
- Fix: correct the two "no published terms" lines; add the 2.A ML clause and NBA s.9 gambling clause to DATA_SOURCES.md; ask the user
  whether the 2026-09-30 decision covers staked tickets (user decision, not a code change).

### F4 LOW — Replay is not equal to live for market_quotes: 642 quotes appear only after a replay (NEW, pre-existing on HEAD)
- File: backend/app/ingest.py:618 (`import_lines_fetch`) + migration 0012 (no backfill when applied 2026-10-01T03:06:26Z).
- Evidence: first full replay (SNAP and HEAD alike) `{"responses": 4913, "games_inserted": 0, "games_updated": 0, "line_quotes": 642}`;
  the 642 rows come from fetch_ids 85, 90, 91, 100, 102 (2026-09-30T14:08:52Z..2026-10-01T02:00:36Z), all retrieved before 0012
  existed; live store's earliest market quote is 2026-10-02T02:00:06Z. Second replay adds 0.
- Why: a rebuilt/replayed store has earlier "first seen" quotes for ~26 games than the live store; anything keyed on earliest/opening
  quote or quote counts differs between live and a rebuild. Genuine retrievals with their own times, so not a leak.
- Fix: either run `import_lines_fetch` over pre-0012 fetches once (with a go) or document the difference in replay docs.

### F5 LOW — NBA stores no period count; a 3-quarter preseason game is stored as a normal final (NEW)
- File: backend/app/sources/espn.py:25-29, 140-155; games schema (migration 0001 line 47) has no periods column; status set has
  no 'suspended'/'abandoned'.
- Evidence: `scripts/statusscan.py` over all 4,132 cached ESPN NBA responses: status names seen only STATUS_FINAL 39,897 /
  SCHEDULED 90,143 / IN_PROGRESS 3,496 / HALFTIME 375 / END_PERIOD 147 / POSTPONED 69 / CANCELED 1. Finals by (period, detail):
  (4,"Final") 37,871; (5,"Final/OT") 1,960; (6,"Final/2OT") 56; (7,"Final/3OT") 2; (1,"Final") 6 and (2,"Final/OT") 1 = All-Star
  mini-games (season_type 'other'); (3,"Final") 1 = espn:401365905 MIL@MEM 2021-10-06, 87-77, stored `final`/`preseason`.
- OT: the stored final score includes overtime, which matches NBA market specs "overtime counts" (backend/app/markets.py:33-37), so
  NBA settlement does not need regulation scores. The only risk is shortened games (graded as complete for totals/spreads, and
  preseason is in every-game coverage). Unknown ESPN status names (e.g. a future STATUS_SUSPENDED) map to 'other', never to final.
- Fix: store `status.period` (and detail) per game; treat a franchise final with period < 4 as not gradable for line markets.

### F6 LOW — team_game_stats readiness check does not reflect the possession inputs features use (NEW)
- Files: backend/app/training_inputs.py:123-126 (`nba_team_boxes` uses team_game_stats existence); features read
  team_possession_observations (backend/app/backtest.py:88-108); team_game_stats is update-in-place with no time (ingest.py:443-463).
- Evidence: espn:401909838 (2026-10-22 MEM-UTA 123-103): scoreboard team_game_stats present from 2026-10-22T03:09:38Z; possession box
  only at 2026-11-02T17:59:28Z after 5 failed summary attempts on 10-22 (box_fetch_attempts). The report key would show 0 missing
  while the efficiency member lacked that game for 11.6 days. Report-only (not in `blocking`); `jobs.box_refresh_plan` uses possession
  observations correctly.
- Fix: base `nba_team_boxes` on complete possession observations (same SQL as jobs.box_refresh_plan).

### F7 LOW — Team names overwritten in place; rename upsert runs before the stale check (pre-existing)
- File: backend/app/ingest.py:325-331 (teams upsert precedes `_upsert_game`'s stale return).
- Evidence: teams table has 59 rows, ids `espn:<id>` stable (franchises 1-30, no relocation in store window); no name history. A late
  older retrieval can rename a team back. Not observed in real data.
- Fix: gate the team upsert by the same retrieval ordering, or keep a team-name history table.

### Not findings (checked, behaving as claimed)
- Replay idempotency on the real NBA store with SNAP code: migrate -> replay -> replay; games, result_revisions (9,612),
  team_game_stats (19,222), team_possession_observations (18,296) and their content hashes unchanged (table in section 3).
- HEAD code on the same real store is also idempotent (no A->B->A pattern in real NBA data), but fixtures reproduce the HEAD bug.
- 0035 backfill: 0 NULL last_seen_at / last_seen_fetch_id; 1,008/9,850 games (835 final, 171 scheduled, 2 postponed) have a
  backfilled last_seen_at earlier than the true last retrieval containing them (max gap 29.1 days; 0 earlier-than-backfill, 0
  same-second fetch mismatches). This is the documented lower bound; the first replay re-applied those retrievals with 0 game updates.
- Box corrections keep their own retrieval time and cannot leak into earlier forecasts via the as-of path (see section 2/Q6).

## 2. Answers to the lane checklist

Q1 Identity. games PK `game_id='espn:<event id>'`, no other uniqueness; raw_responses UNIQUE(source, request_key, content_hash) with
immutability triggers; raw_fetches, result_revisions, team_possession_observations, box_fetch_attempts append-only by trigger;
team_game_stats PK (game_id, team_id) update-in-place (no trigger). Same-day duplicate check (both orientations, <12 h, excluding
postponed/canceled): only 401838141/401838143 (All-Star mini-games, legit). Postponed games keep their own id; the make-up game is a new
ESPN id (e.g. espn:401810499 MIN-GS 2026-01-24 postponed -> espn:401857824 final 2026-01-25). Neutral site: ESPN flag only (88 rows);
the 2020 bubble (181 games, all neutral_site=0 in store) is overridden downstream (backend/app/backtest.py:35, :83; sports.py:21).
Teams: keyed by ESPN id, name/abbr overwritten (F7).

Q2 Regulation/OT. Not stored for NBA (no periods/decision; game_decisions empty in NBA store). Final score = end of play incl. OT,
consistent with NBA market spec. Missing score: non-finals NULL; final with missing score aborts import (F2); never coerced to 0.

Q3 Status. espn.py:25-29: SCHEDULED->scheduled, FINAL->final, POSTPONED->postponed, CANCELED->canceled, IN_PROGRESS/HALFTIME/
END_PERIOD/DELAYED->in_progress, anything else->other. No suspended state. Store copy: final 9,611, scheduled 171, postponed 67,
canceled 1; 0 scheduled rows with tipoff before 2026-11-07. A postponed row (espn:401810507, 2026-01-26 MIL-DAL): status postponed,
scores NULL, result_available_at NULL, tipoff kept at the original time. Fixture S5: same id postponed -> rescheduled updates status
to scheduled and moves tipoff (non-final, so allowed).

Q4 Replay. See section 3. Full-store replay (all 4,913 logged responses, 35 s) used instead of a date window (the CLI has no window
option; full replay is a superset). Fixtures (scripts/probe.py) SNAP vs HEAD:
- A->B->A then replay x2: SNAP revisions 3 -> 3 -> 3, rows equal live; HEAD 3 -> 5 -> 7 (duplicates at t2/t3 re-appended).
- Two different responses, same retrieved_at second, replay x2: SNAP 2 -> 2; HEAD 2 -> 6.
- Older retrieval (t2, different score) imported after t1/t3: SNAP unchanged; HEAD overwrote (games_updated 1).
- Box-only A->A'->A: both end at the live values.
- tests/test_replay_same_store.py: SNAP 20 passed; the same file run against HEAD app code: 20 failed (expected failing-first).

Q5 0035 backfill. Correct as a lower bound on real rows (above). Can the skip rule drop a genuine later correction? Only if a later
correction is stamped at or before an earlier-stamped retrieval already imported. In the CLI every store-writing job holds
`db.store_lock` across fetch and import (backend/app/cli.py:572), and retrieved_at is stamped after the fetch, so stamp order =
commit order within one code base. Remaining exposures (not observed): a clock step backwards; a second writer outside the lock
(e.g. the release checkout's jobs on the same store — release code does not maintain last_seen, so mixed-version writers leave
last_seen stale, which errs on the permissive HEAD-like side); deliberate out-of-order imports of another machine's raw log (none
exist in cli.py — no retrieved_at override flags). Real same-second different responses do occur (month+day pairs, e.g. raw 1516/1515
at 2026-10-17T00:33:16Z); they are ordered by fetch_id.

Q6 Box corrections/late boxes. Features use team_possession_observations, delivered at max(available_at, result_available_at) <=
cutoff (backtest.run; LeakageError if available before own cutoff). Observed rows: available_at = observed_at = retrieval time (470
rows; 0 backdated). Examples:
- espn:401909095 (2026-10-23 BOS 107-103 NYK): NYK FGA 81 at fetch 2675 (2026-10-24T02:00:06Z, raw 2250 sha 42c0a0c8...), corrected to 80
  at fetch 2899 (2026-10-24T23:14:10Z, raw 2471 sha 19e4a083...); both rows kept with their own times. Scoreboard team_game_stats had
  80 already from raw 2257 (2026-10-24T02:38:35Z), overwritten in place without time.
- espn:401909861 (2026-10-25 UTA 123-132 LAL): FGA/OREB revised (UTA 94->95, 19->20 OREB; LAL 81->82, 4->5) at 2026-10-26T21:56:19Z
  vs first 2026-10-25T23:46:04Z; both kept.
- espn:401909838: first box 11.6 days late (2026-11-02T17:59:28Z), stored with that time.
A corrected box cannot reach a forecast made before its retrieval via this path. Historical caveat (documented, not new): 17,826
sportsdataverse rows (seasons 2020-2026, files retrieved 2026-09-30, upstream rewritten 2026-08-26 per docs/RESEARCH.md:34) are
backdated to tipoff+4 h ('historical_backfill_assumed'), so backtests see upstream-corrected boxes as known 4 h after each game; 0
such rows on games whose result was observed live.

Q7 Raw bytes (scripts/rawcheck.py, rawgame.py; sha256 recomputed from the gz files):
- espn:401909095: raw 2249, source espn, endpoint .../basketball/nba/scoreboard, key dates=202610&limit=1000, sha a6e4cc748a54... OK,
  retrieved 2026-10-24T02:00:05Z (fetch 2674); bytes STATUS_FINAL period 4 home 2 107 away 18 103 = store.
- espn:401909861: raw 2721, key dates=20261025, sha 841e3ae9c58c... OK, fetch 3149 2026-10-25T23:46:04Z; bytes final 123-132 = store.
- espn:401909838: raw 1890, sha 57d2624661b0... OK, 2026-10-22T03:09:38Z; final 123-103 = store.
- Real score correction espn:401901822 (2027 preseason, 2026-10-17): 117-98 in 9 cached responses through raw 1537
  (2026-10-18T02:00:02Z), 116-98 from raw 1538 (2026-10-18T16:13:17Z) onward (182 responses). Store: home_score 116, revisions
  1 (117-98 @02:35:25Z raw 1529) and 2 (116-98 @2026-10-18T16:13:17Z raw 1538), result_available_at moved to 2026-10-18T16:13:17Z,
  result_first_seen_at kept. Correct.

Q8 Sources: see F3. Used for NBA: ESPN scoreboard (schedule/results/box/odds), ESPN summary (possession box), ESPN core odds history
(research), espn_props (319 raws), NBA injury report PDFs (861 raws), sportsdataverse release CSVs (7), nba_player_minutes_live (1).
All free/no key per repo docs. Terms text recorded in docs/DATA_SOURCES.md:112-119 (ESPN/Disney), :165 (NBA.com), sportsdataverse MIT
code licence but ESPN-owned content (docs/RESEARCH.md:34, docs/REPO_REVIEW.md:139).

## 3. Claim table
| Claim (source) | Status | Evidence |
|---|---|---|
| 0035: "replaying an already-imported log adds nothing" (0035 header L4-5) | verified (NBA real store + fixtures) | counts_02/03 identical to counts_01; probe S1/S2 |
| 0035: "two different responses in the same second are ordered by fetch id" | verified | probe S2 SNAP 2->2 (HEAD 2->6) |
| 0035: "an older retrieval imported late cannot overwrite newer data" | verified | probe S4 SNAP unchanged, HEAD overwritten |
| 0035: "a team change on a final with an unchanged score is a recorded correction" | contradicted for pre-0035 finals | F1, probe S7 |
| 0035: backfill "nothing is invented ... lower bound" | verified | 0 NULL; 1,008 lower-bound rows, none later than truth |
| HANDOFF L28: "HEAD reproduces 3->7 revisions for ... NBA" | verified | probe_head S1: 3->5->7 |
| HANDOFF L18: same-second replay 2->4->6 on HEAD | verified (NBA) | probe_head S2: 2 -> 6 after two replays |
| HANDOFF L64: "Unresolved: inherited NBA/NHL/NFL replay-ordering pattern in ingest._upsert_game (not changed)" | stale | SNAP changed ingest.py (is_newer_retrieval, last_seen) |
| espn.py:3 / DATA_SOURCES.md:13 "no published terms" | contradicted | DATA_SOURCES.md:112; Disney ToU page read 2026-11-07 |
| nba_injury.py:4 "NBA.com terms allow downloading for personal, non-commercial use" | verified (text), incomplete | NBA ToU s.1; s.9 gambling clause not mentioned there |
| ingest_box_summary docstring: observed/available = retrieval time, never backdated | verified | 470 observed rows, 0 with available_at <> observed_at |
| test_replay_same_store asserts duplicates prevented | verified for games/revisions/decisions only | test does not assert team_game_stats or market_quotes |

## 4. Checks run
| Command (cwd/paths as noted) | Expected | Actual | Result |
|---|---|---|---|
| `python -I scripts/counts.py snapdata/nba.sqlite` (base copy) | baseline | schema 31; games 9850, rev 9612, tgs 19222, tpo 18296, mq 132551 | recorded |
| `NBA_DATA_DIR=$W/snapdata PYTHONPATH=$SP/snap/backend python -m app.cli --sport nba migrate` | 0032-0035 applied | applied 0032..0035; core hashes unchanged; null fetch ids 0 | pass |
| same, `replay` (1st) | no game/revision change | `{"responses":4913,"games_inserted":0,"games_updated":0,"line_quotes":642}`; rev/tgs/tpo hashes unchanged | pass (F4 noted) |
| same, `replay` (2nd) | no change at all | line_quotes 0; all counts/hashes equal 1st | pass |
| HEAD export (`git archive HEAD backend/app backend/tests`) migrate + replay x2 on separate copy | compare | identical counts; line_quotes 642 then 0 | recorded |
| `python scripts/probe.py $W/probe_snap` (SNAP) | fixes hold | S1 3/3/3, S2 2/2, S4 unchanged, S7 no team revision, S6 ValueError | pass except F1/F2 |
| same with HEAD PYTHONPATH | bug reproduces | S1 3/5/7, S2 2->6, S4 overwritten | reproduced |
| `pytest -q tests/test_replay_same_store.py` (SNAP) | pass | 20 passed | pass |
| same file against HEAD app code | fail | 20 failed | failing-first confirmed |
| `pytest -q tests/test_storage.py` (SNAP) | pass | 24 passed | pass |
| scripts/statusscan.py, oddfinals.py, rawcheck.py, rawgame.py over cache | facts | as quoted in section 2 | recorded |
| WebFetch disneytermsofuse.com/english/, nba.com/termsofuse (2026-11-07) | read terms | quoted in F3 | done |

Not run: test_live/test_nfl_live (other lanes, and migration-list asserts known to change with 0035); full suite (lead gate);
a live-forecast-path check that live NBA forecasts call backtest.run with deliver_until = generation time (lane B).

## 5. Open questions / cross-sport
- F1 and F2 are in shared code (NHL/NFL lanes should confirm their own stores: all their revisions also get NULL teams).
- espn:401901822 was a preseason final corrected 117-98 -> 116-98 after ~37.6 h; any total/spread forecast graded in that window
  against 215 vs 214 points — does grading re-settle on result_revisions? (lane B/C).
- Does the NBA injury-report data feed forecasts used for staked tickets (availability_live.py)? If so, NBA ToU s.9 applies (F3, user).
- 0035 must not be applied to the live store while ~/nba-predictor-live (a55c874) jobs write it: that code does not maintain
  last_seen_at, so last_seen goes stale (permissive) — needs the user's go and a release plan.
- odds_quotes (legacy) dedupe by raw_id (ingest.py:339-343), so a repeat of identical bytes is not a new observation there, unlike
  market_quotes (by fetch_id). Pre-existing, LOW, not chased.
