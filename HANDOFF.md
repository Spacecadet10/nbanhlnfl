# HANDOFF

Read only "Current state" below unless the task needs history. Entries dated before 2026-11-22 were moved verbatim on 2026-11-26 to
docs/handoff-archive/HANDOFF-2026-11-before-22.md (nothing deleted). Newest entry first. Each task adds one short entry here.

## Current state (written 2026-11-26 from the newest entries; verify against the checkouts before relying on a commit id)
Which checkout runs what: docs/RELEASE_CHECKOUTS.md. How to move one: skill `release`. Evidence rules: skill `learning-evidence`.
- Live checkout `~/nba-predictor-live` (branch live-release): b15457d. NBA/NHL jobs, API 8100. Release of Nov 25 17:51Z; release-check
  problems [], pin_note null. It lacks the candidate-pool `information_at` freeze that delivery has.
- Delivery checkout `~/nba-predictor-delivery` (branch delivery-release): 3774a73 per the 2026-11-26 audit (ec5a909 NHL box repair was released
  Nov 25; the entries below name later releases). NCAAB, NFL, Soccer, Telegram, weekly report, autoresearch; API 8101, web 4173.
- Backup runner `~/nba-predictor-backup-runner`: a delivery-release commit; nightly backup exits 2 = no mirror drive.
- Forward-bar repair (branch forward-bar, base 3774a73, review fixes merged into 93e26d1): NOT released. `FORWARD_PAIRING["published_bar_from"]`
  is None: nothing is confirmatory yet. Needs the live pool freeze first, then a separate dated commit setting a FUTURE start.
- Colours: rule bucket-record-v1 live since 2026-11-22T17:15:41Z (green-tracking released 2026-11-25T08:07Z; pick_snapshots.sqlite). The NCAAB
  colour gate was removed in 6603296. This Desktop checkout (colour-gate 708a9ff) is STALE.
- Learning audit of 2026-11-26 (~/nba-predictor-research-data/learning-audit-2026-11-26/): published models are sealed; no code path promotes;
  NBA/NHL forward pairs are invalid until the repair above is released and has run. The plan and PROMOTION_CRITERIA are proposals, NOT adopted.

### NHL retrain: PENDING VERIFICATION
Previous-season player boxes were added to data/research/nhl_players/boxes on Nov 26 04:47Z (1,394 boxes), so NHL fits are due; the scheduled 09:30 ET
adaptive-train should refit. NOT verified: that refit (duration with ~4x boxes; NHL took 95 min on Nov 25, cap 300 min) and the first live forecasts
with the member on Nov 27. Do not move live while adaptive-train runs.

### Open items
- nhl_players_injuries@adaptive abstains when an injured-reserve player has no box (partly addressed by the box addition; model-file change is an owner decision).
- Lock-busy odds and partial/skipped adaptive-train still exit 0; box folder not in the nightly backup; forward-candidates.sqlite (230 MB) not backed up.
- First live settled NBA/NHL/NFL pick-colour rows and first live 'sent' row were not observed at release.
- NCAAB neutral-site home/away flip after final; NCAAB total model; quote-selection rules differ by consumer (Nov 22 OPEN list, below).
- Recommended-pick audit (colour/ranking): Telegram Recommended pick follows tickets.card_choice context-card-v1; for NFL it does not apply the frozen 75% rule.
- Budget for autoresearch-agent expires 2026-12-31.

### Owner decisions pending
Promotion constants and colour option (PLAN.md G0); adaptive clock via an adaptive.py edit; bookmaker gate form; one contender per sport; budget renewal;
colour minimum sample (MIN_LIVE_GRADED 30); pointing `published_bar_from` at a future start. Nothing promotes without an owner decision.

---

## Forward bar repair: NBA/NHL compare against the PUBLISHED forecast; same-eligibility counts; per-sport forward status — branch forward-bar (base 3774a73), Nov 26, 2026 — Claude Sonnet 5.5 — NOT released
- Done: (1) NBA/NHL record model `published` (read-only from game_forecasts, `candidates_view.upcoming_published`; market, line, side orientation,
  information time in the new `rf_forecasts.basis` column) and it is THE bar (`FORWARD_PAIRING["bar"]`); research members stay as labelled context
  incumbents; no fallback to a member. (2) `autoresearch.forward_pair_counts` (the evaluator's own pairing) feeds `/api/research/autoresearch`
  `v3.forward.<sport>.pairing`; `paired_graded_items` = valid pairs only, old join kept as `raw_joined_graded_items`. Rows without information time
  (all rows before the live port) are labelled "invalid: no information time (pre-repair)", never deleted, never counted. (3) `forward_status.py`:
  `v3.forward_status` for all five sports. (4) `FORWARD_PAIRING["published_bar_from"] = None` (not established): nothing confirmatory; protocol ids unchanged.
  (5) `full_coverage` criterion untouched. Frontend: status table + new column (typecheck and test:unit pass). Details: docs/AUTORESEARCH.md.
- Changed an existing assertion (spec-driven): test_research_status::test_forward_summary_labels_each_sport_and_counts_paired_graded_items
  (`paired_graded_items` 1 -> 0, plus `raw_joined_graded_items == 1`): the old number was the naive join this task replaces.
- On the Nov 25 backup copy: nba 748 and nhl 462 candidate game-market units invalid (no information time), 0 valid pairs; old join said 127 / 114.
  NCAAB/NFL/Soccer: no candidate. Published rows recorded into the COPY equal coverage.canonical for 75/75 NBA and 96/96 NHL game-markets.
- Review fixes (Opus review of 67e5904): pairing counts cached by store identity and per-sport exception isolation; same line KIND required;
  the orientation check was removed (it could not fail; a/b is fixed by the market definition, stored as documentation); bar labelled
  "published as of start - 60 min, not necessarily the last card"; the `published` model keeps all model_versions.
- Needs: the live-checkout pool freeze (information_at) before any valid pair can exist; then, after live verification, a separate dated commit
  setting `published_bar_from` to a FUTURE start. Open: the as-of rule scores the published run made by start - 60 min, not necessarily the last published one.

## NHL injury model: previous-season player boxes added (owner choice "last season's boxes"), Nov 26, 2026 04:47Z — Claude Opus 5.5 — data only, no code change
- Why: nhl_players_injuries@adaptive abstained on 7 of 8 games on Nov 25 (also Nov 18-19): any injured-reserve player with no box in the live folder
  (current season only) is unresolved and the whole game abstains. The model already reads current + previous season minutes; the folder lacked 2025-26.
- Done: rehearsal on a copy (~/nba-predictor-research-data/nhl-prev-season-2026-11-26): `app.cli --sport nhl nhl-player-inputs --from-season 2026
  --to-season 2026 --kind boxes --sleep 1` -> stored 1,394, failed 0, missing 0 (26 min). Measured with the model's own player_features (scratchpad
  realcheck.py; reproduces the live result 1/8 first): Nov 25 22:00:55Z games usable 1 of 8 -> 6 of 8 (still skipped: NYR@BUF, STL@NYI - rookies /
  no NHL minutes in two seasons). Box load +0.8 s. Then the new files were added to data/research/nhl_players/boxes with `rsync --ignore-existing`
  (excluding last-bulk-run.json): 1,779 boxes; the 436 existing files byte-identical (sha256 list in evidence/).
- Consequence (expected): NHL fits now "due" (box data of consumed results changed); the scheduled 09:30 ET adaptive-train refits; the member trains on
  2025-26 too. Research percentages move through that refit only; shown percentages, cards, colours unaffected. No NHL game Nov 26; first Nov 27 18:00Z.
- Not verified yet: the 09:30 refit (duration with ~4x boxes: NHL weekly+daily took 95 min on Nov 25; NBA is also due; wall cap 300 min) and live
  forecasts with the member on Nov 27. Pettersson (two in VAN) stays ambiguous by design.
- Rollback: delete the 2,788 added files (1,394 box .json + their bulk-*.raw; exact list: evidence/added-files.txt) and let adaptive-train refit.

## NHL player-box repair, partial failures visible, release pin — live b15457d (branch live-release), delivery ec5a909 (nhl-boxes) RELEASED Nov 25, 2026 17:51-17:54Z — Claude Opus 5.5 (lead)
- Cause: since Nov 18 the NHL stats API returns at most 100 rows whatever `limit`; the collector asked for every missing game at once (42 games =
  1,512 skater rows) and refused the short reply as truncated, 11 runs in a row, so the backlog only grew. Last good collection Nov 17-18
  (343 boxes, last game 2026020343). training_inputs also exited 0 with that step failed.
- Fix: new nhl_box_collect.py (2 games per request, splits on truncation, outage stop, time cap, atomic writes). training-inputs/daily exit 3 =
  done but a required step failed/needs attention (`attention`, `outcome` in daily). Installed plists = templates: adaptive-train, nhl-daily,
  ncaab-daily (3 still trains, job ends 3; a failed odds step is no longer hidden).
- Pin: NBA/NHL jobs run ~/nba-predictor-live by design (NBA_RELEASE_ROOT was right). 43d4b74 (Nov 16) was released directly in that detached checkout,
  on no branch, without the pin step. Now: pushed branch `live-release`; write_pin refuses a commit no branch holds; docs/RELEASE_CHECKOUTS.md. release-check
  nba/nhl: problems [], pin_note null, research definitions unchanged (94844709fc4412a5 / f862a44ddaa1e76f); no pin line in ticks since.
- Catch-up 17:54Z (real data, exit 0): stored 42, failed 0, missing 0, unavailable []; 385 boxes; the 343 old boxes + raw files byte-identical (only
  last-bulk-run.json, the status file, changed).
- TRAINING is separate and was NOT triggered by hand: the scheduled 20:30Z run (job exit 0) refit NHL weekly (20:30-20:45Z, combiner fit 187) and daily
  (20:45-22:04Z, fit 204); nhl_players_injuries@adaptive success, inputs box_games 385. torch/keras members skipped as always. Adaptive RESEARCH percentages
  moved through that normal refit; shown percentages, cards and colours do not read the adaptive ensemble. Definitions b921c537a765c65c / d11d95730c905f88.
- Affected: only nhl_players_injuries@adaptive and, through it, the NHL adaptive combiner.
- History preserved: row-by-row compare of backups 20261125T174959Z vs 20261125T220901Z (predict/forecast/snapshot/adaptive/research/selection/grade/
  result tables of five stores + 1,585 pick_snapshots): 0 old rows changed or removed.
- Live first colours (all saved before start): Soccer 08:07:47Z, NCAAB 13:01:20Z, NBA 20:02:12Z, NHL 22:03:07Z, NFL 23:00:43Z. adds_up/sent_adds_up true everywhere.
  Not observed yet: the first settled NBA, NHL or NFL live row (games not over).
- The 15 picks = observed live ('seen') NCAAB rows of RICE@OHIO, UNI@CLT, SBU@BEL (4 green, 2 red, 9 neutral). CORRECTION of an earlier message: at 16:40Z they were
  all pending, so they did not count yet. Settled now: green 3-1, red 1-1, neutral 6-3. Separate: 355 backfilled colours proven by saved card text. Reconstructed: none.
- Review: one independent Opus reviewer (REQUEST CHANGES 2 high + 3 medium -> fixed; N1, N2 fixed) -> APPROVE of b15457d and ec5a909; recorder EQUIVALENT f9c09c0..ec5a909 (static).
- Tests, 3 chunks per line `python -m pytest -q -p no:cacheprovider tests/<third of the files>`, default temp, exact deployed commits:
  live b15457d 2,410 passed, 9 FAILED (8 test_deep_study: no torch/keras; test_weekly_report::test_review_time_and_recorder_come_from_the_config_unless_given, same
  failure on 43d4b74: it reads the live review config); delivery ec5a909 3,478 passed, 1 skipped, 8 FAILED (the same deep_study). The first full run found test_shadow
  broken by the new daily keys -> fixed test-only, rerun above. Old assertion "exit 0 with a failed collector" changed to 3 on the owner's request. Frontend: unchanged, not run.
- Hand runs (owner request) Nov 25 16:29Z: nhl-daily exit 0 (84 quotes, 118 injury rows, 55/55 settled), ncaab-daily exit 0 (370 quotes, 8 incomplete; 404 forecasts: 380 settled, 9 pending, 15 void).
- OPEN: (1) nhl_players_injuries@adaptive still abstains on 7 of tonight's 8 games for a DIFFERENT, older reason (same on Nov 18-19): one injured-reserve player with no
  NHL box this season (Huberdeau, Graves, Krug, ... 33 of 115 rows, 19 teams) makes the whole game abstain. Used in CAR@TBL with the new fit. Its file is hashed into the model
  definition: a fix is a model change, owner decision. (2) lock-busy odds and partial/skipped adaptive-train still exit 0. (3) `cli scheduler --job daily` exits 0 with
  attention (no plist uses it). (4) box folder not in the nightly backup (refetchable). (5) pin mismatch stays a log line (owner, Nov 11). (6) backups exit 2 = no mirror drive.
  (7) later retrospective replays may use boxes collected late (separate ledger).
- ROLLBACK, reverse order: delivery + runner `git checkout --detach f9c09c0`, pin, kickstart api-delivery; live `git checkout --detach 43d4b74`, kickstart api (its pin: branch
  live-release holds 43d4b74); plists from evidence/before, bootout+bootstrap. Boxes can stay. Evidence: ~/nba-predictor-research-data/nhl-boxes-2026-11-25/.

## Green/red/neutral pick tracking — branch green-tracking a40309d, RELEASED to delivery (2026-11-25T08:06:54Z) — Claude Opus 5.5 (lead)
- What: each prediction's colour is saved before its start (exact %, W/L behind it, rule version, forecast id, time) in the append-only
  `data/pick_snapshots.sqlite`, graded automatically, shown on every Performance page (`GET /api/pick-colours`; counting rules:
  docs/PICK_COLOURS.md). Colour rule (bucket-record-v1), cards, recommendations and message keys unchanged. It measures the colours;
  it does not show that green picks are profitable.
- Tracking start: 2026-11-25T08:07:47Z (first rows saved live: 72 Soccer, none settled). NBA, NHL, NFL, NCAAB: NO live row yet - no pregame
  forecast existed at release; their first live rows were not observed. Everything settled below is backfilled from saved card text.
- Backfill (proof = the sent card's own text; dry run, --apply, recheck): 1,220 of 2,922 saved card lines (105 NBA, 66 NHL, 20 NFL, 755
  NCAAB, 274 Soccer), from 2026-11-22; recheck wrote 0. Not proven: 1,607 before the rule went live (2026-11-22T17:15:41Z), 95 lines with
  no confirmed delivery in the ledger (52 of those count as sent through the Telegram export, colour unknown). No colour was reconstructed.
- Live totals 08:12:45Z, all sports (after/table.txt, after/api/*.json; adds_up and sent_adds_up true for every sport): 3,650 live forecasts (355 known
  colour, 3,295 unknown: 2,496 before the rule, 708 later forecast not saved, 91 before tracking) + 973 recovered-only, counted once = 4,623.
  Against the 4,157 of the earlier report: +456 Soccer picks in its three other markets, +10 NCAAB (two newer games); other sports identical.
- GREEN, live record, W-L (priced picks only for units; small samples marked on the page):
  - All forecasts 21-14, 2 void, 1 unsettled (-0.08u on 4): NBA 6-4, NHL 1-1, NFL none, NCAAB 2-3, Soccer 12-6.
  - Recommended 1-2, 1 void (-1.00u on 1): NBA 0-1, NCAAB void, Soccer 1-1.
  - Confirmed sends 79-48, 6 void, 1 unsettled (-1.40u on 67): NBA 6-6, NHL 1-1, NFL none, NCAAB 60-35, Soccer 12-6.
  - Recovered-history support (separate): all 11-5 (NBA 6-3, NHL 5-2); sends 14-7 (NBA 8-5, NHL 6-2).
  - Baseline, same periods: sends red 118-108, neutral 334-302. Overlapping markets of one game are not independent.
- Review: one independent Opus reviewer. Round 1 REQUEST CHANGES (3 high, 4 medium, lows) -> fixed test-first (two mediums documented only: M4
  a failed sent-row write needs a manual backfill; soccer price coverage is low and biased) -> APPROVE of e51f4b24 -> N1 (lock delay) fixed
  -> final APPROVE of a40309d. Recorder EQUIVALENT 5ded396..a40309d (static; 47-delivery replay byte-identical at e51f4b24, not replayed at
  a40309d). weekly_report_review.json: see its last entry.
- Tests at a40309d: full backend `pytest tests/test_*.py` (default chunks failed on an awk slip, so five whole copies ran in parallel under
  --basetemp in a scratch folder): each 3,408 passed, 1 skipped, 25 FAILED, the same 25 = 8 test_deep_study (torch/keras not installed) + 16
  test_research_agent + 1 test_soccer_live. test_research_agent is the --basetemp: default temp 46 passed at a40309d and at 2749f91; the same
  --basetemp on 2749f91 fails the same 16. test_soccer_live passes alone. Not fully green (deep_study). Frontend: build OK; 77 unit tests
  passed before e51f4b24, not rerun (no frontend change since).
- Live checks: Telegram runs 08:07:47Z, 08:10:05Z, 08:12:23Z ok, no `pick_snapshots: failed`, error log unchanged, cadence unchanged (~137 s);
  /performance for all five sports at 1440 and 390: no overflow, no console error, numbers = API. First uncached Soccer build 10 s (then 60 s cache).
- Backups: before 20261125T080550Z; after (runner -> a40309d) pick_snapshots.sqlite 1,292 rows, integrity ok. Both exit 2 = no mirror drive.
- OPEN: (1) observe the first live NBA/NHL/NFL/NCAAB rows and the first live 'sent' row. (2) Telegram paused, stale pages or the Mac off ->
  colour not saved -> unknown. (3) Unpriced: 3,536 of 3,650 forecasts have no matching stored price. (4) M4. (5) soccer-live exit 1 is its own
  refusal (matches still to be played), seen before this release too.
- ROLLBACK: delivery `git checkout --detach 2749f91`, frontend build, `app.release pin`, kickstart api-delivery + web; runner -> 787d861.
  pick_snapshots.sqlite can stay (nothing else reads it). Evidence: ~/nba-predictor-research-data/pick-colours-2026-11-25/.

## NBA daily job run by hand (owner request), Nov 25, 2026 04:36Z — Claude Opus 5.5 — no code change
- Why: the Mac restarted ~21:59 local (login 23:34), so the 22:00 run never fired. Ran `launchctl kickstart -p gui/$(id -u)/com.nba-predictor.daily`
  -> runs 1, last exit code 0 (data/daily-launchd.log, 04:36:05Z-04:36:14Z). Refresh skipped (odds cycle had refreshed 04:35:51Z, next due 04:36:51Z);
  box scores 3/3; player_box 2027 downloaded; score/two-hour/market scoring ran (72 of 73 two-hour forecasts settled, 1 pending).
- Log's 18 "outcome": "failed" = availability_two_hour in earlier odds cycles (22:31Z-02:46Z), not this run: 10 unresolved/no-history Out players
  (Larrea, Mitchell, Beekman, ...), 8 "Invalid player minutes". Not investigated. adaptive_train still "due".
- Not an issue: com.nba-predictor.card-thresholds is absent on purpose (retired Nov 22, bucket-colours release afdb239). This checkout
  (colour-gate 708a9ff) is STALE; the current handoff is delivery 2749f91 / origin/delivery-release.
- OPEN: nhl-daily and ncaab-daily showed runs 0 since login at 04:35Z (not run by hand; only the NBA daily was requested).
- Not run: direct integrity check of data/nba.sqlite after the restart (command denied).

## Resumable research runs + five-sport completion — branch research-resume 0097440, RELEASED to delivery (2026-11-24 ~03:00Z) — Claude Fable 5.2 (lead)
- RESULT (verified 2026-11-24 07:30Z, `python3 -I <evidence>/verify_results.py` -> PROBLEMS: 0; output in evidence/after/verify_results.txt):
  all five v3 studies `evaluated`, queue.remaining 0, no failed item, no item scored twice; pinned code in every run (runner-runs.jsonl:
  app-23df530, files fac8c6988092dff4, rc 0, no `problem`). No candidate kept in any sport; nothing promoted.
  - NBA (label "recovery of 2026-11-23 06:00", 07:14Z, 49 s): v3 8732ea3dca99 20/20 (14 new, the 6 of Nov 22 kept), 10 candidates, none kept.
    v2 e49eae253e6a: queue empty, 0 new trials (its one train.py version was tried Nov 22: discard) = no improvement found.
  - NHL (same label, 07:15Z, 64 s): v3 7caea41a88ae 12/12 (all new), 5 candidates, none kept. v2 ba9372fa1e0f registered by this run (the id
    PIN.json already named): 1 trial, `keep` on reg3 only (log loss 1.0315 vs goals_draw 1.0349; exploratory, one version searched, NOT a
    promotion). The invalid Nov 23 trial (9cb21e25c613) was not reused.
  - NCAAB (label "resume of 2026-11-23 06:00 budget-limited runs", 07:16Z, 122 s): v3 df7a114fb452 7/7 (4 new, the 3 of Nov 23 kept), 3
    candidates, none kept; 12 "reference-line beat(s) need human review" (3 candidates x spread/total/team totals, graded at the market-free
    reference line because no bookmaker history exists: never a bookmaker result, never auto-kept). Winner market: no keep.
  - Soccer (same label, 07:18Z, 697 s): v3 36f6ce89df68 12/12 (10 new, the 2 of Nov 23 kept; corners_nb_sealed finished from its stored
    partial in 11 s), 4 candidates, none kept.
  - NFL: unchanged since 2026-11-23 11:15Z (6/6, 2 candidates, none kept); not rerun.
  - Unchanged (sha256 before = after): both study-cache files, audit.jsonl (12 entries) + audit.head, PIN.json, workspace/prepare.py;
    app-23df530 clean at 23df530; the earlier items.jsonl lines are a byte-identical prefix of the new files (NBA 6, NCAAB 3, NFL 6, Soccer 2).
- Why NCAAB/Soccer never finished before (verified in the wrapper): it passed `--max-trials 3` and recorded the evaluator's budget stop
  (`skipped: item budget reached` / `time budget reached`) and `evaluated ...; N queued` as done for the day. Evaluator untouched (it already
  scores each item once and resumes); no study identity, pin, cache or audit entry changed.
- Change (scripts/autoresearch_daily.sh only, + template, tests, docs, one docstring): outcome `pending` when the report says work remains
  (never done_today, never last_success); no item cap (AUTORESEARCH_TRIALS 100000) and no daily attempt cap (column 6 is a chunk counter);
  round-robin guarded chunks (soft AUTORESEARCH_MINUTES 15; the guard is asked again before every chunk); stuck-process limit = soft + 30 min
  (NBA/NHL: twice the soft budget + the v2 trial timeout) - a kill limit, not a work limit; per-run window 170 min, nothing starts
  08:55-16:30 local (adaptive-train 09:30/15:30; malformed times exit 78); full chunk without progress -> `failed` with the item named,
  short chunk -> stays pending; unreadable named report -> `failed`. Memory limit (6 GB), heavy lock, game windows and live-forecast
  priority are unchanged and still enforced by app.learning_run.
- Schedule INSTALLED 2026-11-24 ~07:35Z: 04:00, 05:00, 06:00, 07:30 local (was 06:00, 11:10, 13:10). `plutil -p` diff installed vs template
  showed only the start times; installed file == delivery template; bootout + bootstrap of that one job; launchctl print lists the four
  slots. Before/after plists in evidence. First run on the new slots not yet observed when this entry was written.
- Recovery loop: evidence recovery2.sh (asked the guard first, then the released wrapper); guard refused 03:04Z-07:14Z (games), then both
  phases ran once; `all four final 2026-11-24T07:30:21Z`. Log: ~/nba-predictor-research-data/pin-repair-2026-11-23/recovery.log.
- Review: one independent Opus review of e8b9250+96554ec: REQUEST CHANGES (high: an item failure retried by the evaluator was marked
  `failed`; medium: NBA/NHL budget used twice, a late start ran into 09:30, a short last chunk failed a healthy sport; low: unreadable
  report, gate text, weak assertions, docs) -> all fixed test-first in 26d3201 -> re-review APPROVE. 0097440 (the reviewer's last low item,
  one line) was not re-reviewed.
- Tests: targeted `pytest tests/test_autoresearch_scheduled.py tests/test_research_status.py -q` (worktree backend): 228 passed. Full
  backend suite once at 0097440 (6 chunks, evidence/test-output): 3,353 = 3,344 passed + 1 skipped + 8 FAILED (test_deep_study, the known
  torch/keras environment failures of the last release; NOT rerun in the nba-predictor-deep env this time). Not fully green. Frontend
  not rerun (no frontend change).
- Live: every sport finished inside its first chunk, so the multi-chunk `pending` path ran in tests only (three-chunk run, round-robin,
  item-failure retry, soccer partial growth, guard refusal mid-run), not on real data.
- NOT DONE: no entry in data/reports/weekly_report_review.json for 0097440 or for this notes commit: no independent recorder review was made.
  Static facts for whoever writes it: diff 5ded396..0097440 = the 5 files above; backend/app/research_status.py is AST-identical apart from
  one docstring; best_pick_record.py sha256 7cc3e6e1 unchanged. Until an entry exists, rows recorded at these commits do not count.
- OPEN: (1) 20 other scripts/launchd templates still point at nba-predictor-sprint1 - separate, unresolved; do not install without a diff.
  (2) NCAAB 12 reference-line flags need a human look. (3) A soccer walk that rewrites its partial forever would stay pending (bounded by the
  run window, no verdict). (4) `today` is fixed at run start (a run crossing local midnight counts as the old day). (5) probe-helper failure
  reports "report file could not be read". (6) The heavy lock is likely, not guaranteed, free at 09:30 (one atomic item can overrun).
  (7) Two wrappers at once (a manual run during a scheduled slot) are serialised only by the heavy lock: rows may alternate
  refused/pending; no single-instance lock.
- ROLLBACK: schedule: copy evidence/before/com.nba-predictor.autoresearch-daily.plist back, bootout + bootstrap. Wrapper: checkout 5ded396 +
  pin (old caps return; study progress is unaffected either way). Evidence: ~/nba-predictor-research-data/research-resume-2026-11-23/.

## Research pin repair + audit + research-6 — branch pin-repair 16de9b8, RELEASED to delivery (2026-11-23 ~15:50Z) — Claude Opus 5.5 (lead)
- What broke (verified): 2026-11-23 06:00 the research run loaded the LIVE checkout (dd11fb6, app 63922174) not the pin (23df530, fac8c698):
  prepare.py sets PYTHONPATH, `python -m` puts the cwd (delivery/backend) first. NBA/NHL v3 refused (cache keyed by the app), v2 ran one trial
  each under unintended ids (NBA aa1be0f64744 discard, NHL 9cb21e25c613 KEEP reg3), the state file said "evaluated"; NCAAB/NFL/soccer v3
  ran on live code unnoticed. The 2026-11-22 history release triggered it; HANDOFF of that release did not name the risk.
- Repair: scripts/research_runner.py (outside the fingerprinted app; prepare.py is hash-registered in running protocols and is UNCHANGED),
  ~/autoresearch-sports/PIN.json (records the existing pin; nothing re-pinned, no cache or fingerprint rebuilt/renamed), refuse_unpinned in
  every study command, app/research_audit.py (append-only, chained, fail-closed; enforced in baseline selection, reuse, daily queue,
  holdouts, v3 runs, forward tracking, promotion, status). Wrapper reads v3= itself; rc 0 without an outcome line = failed.
- Audit written (12 entries; 68 original files byte-identical before/after): invalid = both 06:00 v2 protocols + both trials by id + the
  two VOID protocols of Nov 18; NCAAB/NFL/soccer 06:00 = unverified, then verified by recomputation with the pinned code (NCAAB 3 items +
  evaluation, NFL 6 items + evaluation, soccer 2 items identical; soccer unfinished item 297,755 records equal after the evaluator's own
  push->mid rename). Live check: usable NHL records 0, reg3 model to beat = goals_draw, forward candidates none.
- State: nba/nhl rows of daily-state.tsv corrected evaluated -> refused (original in evidence/before). Agents installed: autoresearch-daily
  (06:00 + same-day retries 11:10/13:10), autoresearch-agent (AUTORESEARCH_PYTHON = runner; gate sha unchanged). api-delivery plist:
  NBA_AUTORESEARCH_DIR added; web proxies only /api/research/autoresearch to delivery, so the audit reasons are visible.
- RECOVERY (labelled "recovery of 2026-11-23 06:00", through the wrapper and all guards): NOT RUN YET at this commit. Guard refused: adaptive
  trainer held the heavy lock, then games are on until late. evidence/recovery.sh retries every 15 min until 05:40; result in
  evidence/recovery.log, runner-runs.jsonl (label) and the next HANDOFF entry. manual agent script now refuses unless AUTORESEARCH_PYTHON = runner.
- Also: earlier Telegram cards whose printed line note differs are no longer called "same" (2 rows; file rebuilt, otherwise identical).
- Review: independent Opus REQUEST CHANGES (2 high, 11 medium, 8 low) -> all fixed test-first -> re-review APPROVE of c1080b6. Not re-reviewed:
  ae93dc2 (the reviewer's own LOW items), ee88710/16de9b8 (one proxy line, one plist template). research-6 up to 6e86d2c reviewed SHIP elsewhere.
- Tests: full suite at c1080b6, 167 files, 3,310 = 3,301 passed + 1 skipped + 8 test_deep_study (torch/keras; all 10 of that file pass in the
  nba-predictor-deep env). ae93dc2: 270 passed on the affected files; launchd tests 234-327 passed; frontend 73; typecheck; build.
- Mistake: at ~15:46Z I installed the repo's STALE api-delivery template (retired data folder); delivery API answered 503 for under 2 minutes;
  restored from the saved plist, stray folder removed, that template now equals the installed file. Telegram runs continued; nothing written.
- Found: delivery git index.lock, empty, from 01:48, no holder; moved to evidence (checkout clean, fsck ok). Cause unknown.
- OPEN: 20 other scripts/launchd templates still point at nba-predictor-sprint1 (do not install them without a diff); audit tolerates one valid
  chained line beyond the head (crash rule); NHL v2 has no protocol on the 5-sport snapshot yet (first recovery run registers it: ba9372fa1e0f).
- ROLLBACK: research: put evidence/before plists back; keep audit.jsonl and PIN.json (older code ignores them). App: checkout dd11fb6 + build +
  pin + kickstart, but then also boot out autoresearch-daily (dd11fb6 has the cwd bug). Evidence: ~/nba-predictor-research-data/pin-repair-2026-11-23/.

## Recovered Telegram predictions in Performance and Prediction History — branch recovered-history 4224965, RELEASED to delivery (2026-11-23 ~04:50Z) — Claude Opus 5.5 (lead)
- Owner: the Nov 2-16 original predictions (Desktop JSON, sha256 ae9cfd15..., same file as docs/telegram_record.json) must be in
  the historical views, not only in card summaries. New GET /api/telegram-history (NBA/NHL/NCAAB), /api/nfl/telegram-history,
  /api/soccer/telegram-history (app/telegram_history.py; read-only; no model code, distinct from /api/recovered-forecasts).
- Pages: NBA/NHL History merges the rows into the canonical table (dates, team, market); NFL/Soccer History has one dated list with
  market (and competition) filters; Performance of all four has totals per market, exact-% records, individual rows, reconciliation.
- COUNTING RULE (on the page): one prediction per (sport, game, market). The last card before the start is the prediction;
  earlier cards are listed as versions, not counted; a game+market also in the live record is counted once, by the live forecast
  of record; the card stays listed (same / earlier live version / different). Sent at or after the start = excluded, listed.
  Unsettled stays unsettled; every row is unpriced; model version unknown.
- Reconciliation (live API): card rows 905 NBA / 576 NHL / 110 NFL / 126 Soccer = 1,717; last card per game+market 460/498/105/126 =
  1,189 (all imported, 0 excluded, 0 unmatched, 0 unsettled, 0 unaccounted); earlier cards 440/66/5/0 = 511; sent after start
  5/12/0/0 = 17. Duplicates of live 80/42/25/69 = 216: 191 same, 25 earlier live version (24 NBA, 1 NHL), 0 different; counted only
  in recovered 380/456/80/57 = 973. 1,123 graded by the export, 66 from the stored final score, 0 disagree; 8 of the 25 earlier-version
  rows graded differently from the live forecast that is counted (7 NBA, 1 NHL), both grades shown.
  Combined = union of game+market keys: NBA 680, NHL 738, NFL 200, Soccer 420. No Nov 17 and no NCAAB row.
- Data: research/telegram_history.json rebuilt from the Desktop JSON (old file: evidence telegram_history.before.json); bucket fields
  identical in all 1,189 rows, so cards/colours/best_pick_record do not change. Rebuild: `python scripts/build_telegram_history.py --data-dir <data>`.
  A pre-release file reads as 'old_format' (unavailable with a reason), never half-shown.
- Review: independent Opus REQUEST CHANGES (old-file page crash, filters lost, NCAAB wording, soccer scope, malformed file) -> fixed
  test-first -> re-review APPROVE with one finding (filters following a league switch), fixed (010a10c). 4224965 (rule text, rows open)
  was added after the re-review. NOT done: weekly_report_review.json entry (reviewer: recorder inputs unchanged).
- Tests: backend suite at 010a10c, 164 files in 6 chunks, 3,171 collected = 3,161 passed + 1 skipped + 9 failed: 8 test_deep_study
  (torch/keras absent) + test_adaptive ...publishes_briefly (60 s in-test limit; failed the same on unmodified 787d861 at load ~55; passed alone
  on 4224965 at load ~20). 4224965 (rule text + UI): 154 passed, 1 skipped on 7 affected files. Frontend 72 unit passed, typecheck, build.
  check_project.py: FAILs only on pre-existing docs files.
- Verified live: verify.py on :8101 and :4173: 1,189/1,189 rows equal the source, BAD 0, per-day sums equal totals; dom.mjs: 2,378
  rendered rows (both pages) checked against the source, 0 missing; headless Chrome 1440 and 390: no horizontal page overflow, no errors.
  Chrome extension was not connected (headless Chrome over CDP instead).
- Live steps: delivery 787d861 -> 4224965 ff + npm build + `app.release pin`; api-delivery and web kickstarted; no plist, migration, venv or
  backup-runner change (runner stays 787d861). research-6 (another session) rebases on this; it deploys after the 07:47 check Nov 23.
- OPEN: earlier cards marked "same" ignore the printed line text (2 cards); while a History request fails its recovered rows hide too;
  if NFL live is re-activated later, Nov 15-16 NFL overlap labels can drop (still counted once); all-market payload ~1.1 MB.
- ROLLBACK: cd ~/nba-predictor-delivery && git checkout --detach 787d861 && (cd frontend && npm run build) && pin; the new data file is
  safe for the old code; kickstart api-delivery and web. Evidence: ~/nba-predictor-research-data/recovered-history-2026-11-22/.

## Five-sport research (sports-research-v3) — branch research-5 23df530, RELEASED to delivery (2026-11-22 ~21:28Z) — Claude Opus 5.5 (lead)
- Built by 4 agents (core evaluator; NCAAB+NFL adapters; Soccer adapter + readiness fix; scheduling/snapshot/forward driver),
  integrated in research-5; independent Opus review x2 (REQUEST CHANGES -> fixed; round-2 must-fix 131423a and minors 23df530
  fixed by the lead). No new files; no live-hashed or SEALED file changed (fingerprint tests pin nfl_live.definition
  b6074877a7e7ccfe, ncaab fit_code_version 24ec6d25, selector-v1:d472eb466923, RECORDER_SHA256; soccer_markets_eval.py byte-identical).
- Adapters: NBA/NHL built into app/autoresearch.py; NCAAB `ncaab_cards.research_adapter` (research imports lazy: an eager import
  broke telegram's ncaab_card_selections creation in the full suite); NFL `nfl_challengers.research_adapter`; Soccer
  `soccer_challengers.research_adapter` (moved out of SEALED soccer_markets_eval.py).
- Evaluator: Brier/log loss/ECE/score errors, paired week-block bootstrap, MODEL_SELECTION §6 keep rule with a 1e-9 tolerance
  (dated amendment in MODEL_SELECTION), reference/model-line beats -> needs_human_review (never kept or forward-tracked),
  no-vig only on non-synthetic quotes <= cutoff at the same line, returns only at executable prices; protocol id includes
  the snapshot identity; per-sport outcomes evaluated|no_candidates|refused|failed|skipped; bounded resumable queue.
- Soccer readiness fix: future fixtures were reported "kicked off" (cutoff compared, not now); now one pending line per
  competition, real unsettled matches still listed (405,570 spam lines in the 58 MB log).
- Live steps: backup 20261122T212632Z; delivery 1179494 -> 23df530 + npm build + `app.release pin`; api-delivery/web
  kickstarted; backup runner -> 23df530; snapshot ~/autoresearch-sports/data-snapshot-2026-11-22-5sport (app.backup
  --snapshot, soccer backfill 1,169 free ESPN requests, 0 failed, --verify ok, read-only); app pin worktree
  ~/autoresearch-sports/app-23df530; workspace prepare.py pin commit 7a6d35e (BACKEND app-23df530, SNAPSHOT 5sport; old
  app-ca44760 + data-snapshot-2026-11-18 kept); NEW launchd jobs com.nba-predictor.forward-record (30 min) and forward-grade
  (60 min) under the light guard; NBA/NHL study cache built once (see below). Daily plist unchanged (06:00 Toronto).
- Study cache (NBA/NHL v3 needs it; the daily step refuses without it): build with the evaluator directly (the shim adds
  --workspace, which `v3` rejects): PYTHONPATH=$AR/app-23df530/backend NBA_DATA_DIR=$AR/data-snapshot-2026-11-22-5sport
  /Users/andry/nba-predictor-deep/backend/.venv/bin/python -m app.autoresearch v3 prepare-cache --exp-root $AR/experiments
  --sport nba (then nhl), under app.learning_run (heavy lock). Rebuild after every app-pin change.
- Results on the snapshot (search/validation; holdout untouched): NBA, NHL, NFL, Soccer: no candidate kept; NCAAB: 12
  reference-line beats need human review (not bookmaker skill; DraftKings sample since Nov 17 still fails spread/total).
  No historical bookmaker quotes exist for any sport, so no sportsbook comparison or returns.
- Tests: full backend suite at 535460e: 3,131 passed / 9 failed = 8 test_deep_study (torch/keras absent) + the wrapper test
  fixed in 131423a; research + delivery sets on 23df530: 371 passed; frontend 62 unit, typecheck, build ok.
- Observed: forward-record run 21:28Z recorded NCAAB 605 / NFL 90 / Soccer 2,268 rows (NBA/NHL nothing upcoming); the full
  wrapper at 21:35Z refused all five for real game windows (learning-runs.jsonl reasons); manual guarded runs with the game
  window relaxed: NFL evaluated (2/2 candidates, none kept), NBA evaluated (v2 queue scored; v3 item budget reached, resumes).
- OPEN: first scheduled five-sport run is 06:00 Toronto Nov 23 (unverified until it runs); forward grading starts as games
  finish; NCAAB spread/total still fail validation; NBA/NHL line markets only at model lines; Soccer national-team 1X2
  research walk not built; `v3` CLI via the shim rejects --workspace (use the direct command above).
- ROLLBACK: delivery `git checkout --detach 1179494` + npm build + re-pin; workspace `git revert 7a6d35e`; bootout
  forward-record/forward-grade and move their plists aside; snapshot/forward store/app-23df530 are additive.

## Bucket-record colouring release — branch bucket-colours afdb239, RELEASED to delivery (2026-11-22 ~17:05Z) — Claude Opus 5.5 (lead)
- Owner: colour = each exact-% bucket's own settled W/L (green ≥1 win and ≥75%; red losses > wins; else neutral; pushes/voids
  shown apart), no probability cutoff, no NCAAB colour gate, all NCAAB/Soccer recommendations shown, recovered Nov history shown.
- Built by 4 agents in separate worktrees (bc-colour backend+Telegram, bc-web, bc-ops, bc-quotes), merged in bucket-colours;
  independent Opus review REQUEST CHANGES -> fixed -> re-review APPROVE (+ stale-odds PNG label, pool-copy warning: afdb239).
- Live steps: backup 20261122T162946Z (ledger 319 deliveries, integrity ok) + plists/history/review copies in
  ~/nba-predictor-research-data/bucket-release-2026-11-22/; delivery 7d125ca -> afdb239 + npm build; `app.release pin`
  (stale 23ab1aa pin ended); card-thresholds job booted out, plist moved to the evidence folder (card-thresholds.plist.removed);
  NBA_CARD_COLOURS removed from telegram + api-delivery plists; autoresearch-daily 11:00 -> 06:00 local (it read a fixed
  Nov 18 snapshot; it collided with adaptive-train 09:30, 75-min cap ends 07:15); web kickstarted; backup runner -> afdb239.
- Live data: `app.cli ncaab repair-orientation --apply` repaired 7 games (401918959 401919826 401927396 401916125 401917538
  401917539 401928998; recheck 0; result_revisions appended); `scripts/build_telegram_history.py` rebuilt
  research/telegram_history.json: 1189 rows, 0 excluded, overlap-with-live 216 kept and flagged, period 11-02..11-16 (old file kept).
- Weekly recorder review appended for afdb239 (EQUIVALENT; best_pick_record.py byte-identical).
- Tests: full backend suite at 6ba785f 3011 passed / 9 failed (8 test_deep_study torch/keras absent; test_soccer_live
  two-hour-window passes alone) + 138 passed on afdb239-touched files; frontend 61 unit, typecheck, build ok.
- OPEN: off-machine mirror needs the owner's external drive (until then backup-nightly exits 2 and warns); first 06:00
  autoresearch-daily run (Nov 23) unobserved; NCAAB spread/total still fail validation (shrinkage variant also fails: Brier
  0.2554 vs coin 0.25 / DK 0.2508); NCAAB team totals + soccer BTTS/corners/team goals have no bookmaker prices
  (additional-odds/rundown-odds not loaded, paid); market_live.latest_quote and research selectors keep their own quote rules;
  adaptive pickle pool has no retention yet; ~50 NCAAB cards/day now go out (kill switch NBA_NCAAB_CARDS=0).
- ROLLBACK: cd ~/nba-predictor-delivery && git checkout --detach 7d125ca && (cd frontend && npm run build); copy
  plists-before/*.plist back to ~/Library/LaunchAgents and bootout/bootstrap; restore telegram_history.before.json.

## Quote consistency + backup retention/coverage — branch bc-quotes, Nov 22
- One forecast-time quote rule, `odds.forecast_quote` (market_quotes): latest 'current' quote of the same game, market and exact line retrieved at or before the forecast and before the start; ties on the retrieval time: the quote recorded with the forecast, DraftKings, provider key order, newest quote_id. Used by: card/ticket `tickets.card_market_prob`, scorecard `scorecard._book`, sportsbook comparison `sportsbook._line_quote` (before: card = any book, no recorded-id preference, quote_id tie-break; scorecard = only the quote recorded with the forecast; sportsbook = recorded quote else the card rule). Send-time freshness is `odds.send_time_quote` (used by `tickets.quote_check`, same provider as the forecast's quote, any line so a move shows). Winner moneylines stay `odds.winner_quote` (NBA odds_quotes; others market_quotes winner); soccer 1X2 stays `soccer_book.latest_1x2` (Bet365, as of the forecast). Closing-price analysis (scripts/closing_value.py) is separate and labelled. Stored rows are only read.
- Behaviour change to know: a quote that arrived between the model run and `generated_at` is now what the scorecard shows even if the forecast recorded an older id; `coverage.forecast_quote`/`matched_quote` (research evaluation) still use the recorded id and are unchanged. Not unified: `market_live.latest_quote` (forecast-time input, quote_id tie-break), `research_live._winner_quote`, `near_tipoff._market`, `paper_trading._quote`, `nfl_live` (2-hour window), `tickets` snapshot quote (line ~236, prefers the pick's own book). Listed in the consumer map in the final report.
- Backups: `scripts/backup_nightly.py` now also copies adaptive model pickles + their .sha256 (research/adaptive, no locks/snapshots), scorecard DBs and card_thresholds.sqlite (SQLite backup API, integrity + row counts under `databases/`), still allowlist-only, no .env. Retention `prune()` (14 days all, newest per ISO week 8 weeks, newest per month 12 months, never the newest/only copy/newest verified; folders not named as a UTC stamp ignored). Report-only by default (`prune` in the run's JSON); deletes only with `--prune`. The owner decides whether to enable it; the launchd plist is unchanged. backup_nightly.py/journal.py and their tests were brought from colour-gate (aac81a0) unchanged; this branch's base 7d125ca lacks them.
## Bucket-record colouring, backend + Telegram — branch bc-colour (not pushed), Nov 22
- Colour is now ONE pure rule, `threshold_overview.bucket_colour(wins, losses)` (rule id `bucket-record-v1`: green = at least one win and 4*W >= 3*(W+L); red = losses > wins; else neutral), applied to each exact-% bucket's own live record. `percentage_record` gains `colour`, `colour_rule`, `voids`; the `history` and recovered `telegram` sub-records get their own `colour`/`voids` and are never merged into the headline. Shared wording: `threshold_overview.record_line`.
- Removed: `card_threshold.py`, `ncaab_gate.py`, `/api/card-thresholds`, CLI `card-thresholds`, `NBA_CARD_COLOURS`, the card-threshold store path in config, their tests. The `card_thresholds` and `ncaab_gate_decisions` tables in data are untouched (no DROP); nothing writes them any more.
- NCAAB cards go out again by the normal path (`NBA_NCAAB_CARDS=0` stays the kill switch). Every supported market is shown; NCAAB spread/total (`ncaab_app.FAILED_VALIDATION`) are labelled "Failed validation - not recommended" and `card_choice` never recommends them; a market with no forecast shows "No forecast for this market"; missing or older-than-20-minutes odds show "Odds unavailable". Colour plays no part in ranking or hiding. Soccer cards use the same labels.
- best_pick_record.py not edited; it reads only wins/losses/rank of `percentage_history`, which are unchanged (new fields are additive).
- Recovered Telegram history (Nov 22): the cards showed little of it because build_telegram_history.py DROPPED every game the live record also counts (216 of 1189 rows: NBA 80, NHL 42, NFL 25, Soccer 69 - all of Nov 15-16 and half of Soccer, so Soccer's period read 11-14..11-14), the period was a UTC start date, a missing/unreadable file or a sport without rows was silently omitted, and a bucket with no rows read as plain "none settled". Now: all 1189 rows import (0 excluded/unmatched; 66 ungraded in the export are graded from the store final), overlap rows are kept with `also_in_live` and reported (`overlap_with_live`, `by_sport_date`), the period/`dates` come from the rows in Toronto time, `telegram_record` always returns a record with `available`, `reason`, `period`, `bucket_period`, `win_rate`, `voids`, `overlap_live`, and cards say "recovered history unavailable" / "no recovered history at this exact %", never 0-0. Rebuild the live file after a backup: `python scripts/build_telegram_history.py --data-dir "$NBA_DATA_DIR"`.
- Review fixes (Nov 22): neutral % pill is always grey (never the team colour); `percentage_history.telegram.line` and `.history.line` are exactly the text Telegram prints (one function each in threshold_overview, web renders verbatim; history.line null when Telegram hides it); red has no strike-through (mark + red pill; old struck messages still parse; delivery key unchanged); stale-price label reads "Current odds unavailable (latest price older than 20 min) — the price above is the one the forecast saw" and "Odds unavailable" is not repeated under a sportsbook reason line; Soccer placeholders name the team and unserved goal/corner families become one "Not offered for this competition: ..." line; small-sample text prints once; dead GREEN_MIN_PERCENT, ncaab_cards.annotate and the ncaab_selection branch of card_choice removed.
- NCAAB VOLUME WARNING: without the gate every NCAAB game with a due forecast gets a card, about 50 cards a day (forecast games Nov 17-21: 55 / 61 / 27 / 51 / 51). This follows the owner's "show all available NCAAB recommendations"; NBA_NCAAB_CARDS=0 is the kill switch if the chat becomes too noisy.
- Next: frontend twin of the rule (frontend agent), review, then a guarded release.

## Colour release + NCAAB colour gate — branch colour-gate a7d3be1, RELEASED to delivery 7d125ca (2026-11-22T01:34:46Z) — Claude Opus 5.5 (lead)
- Built (test-first): card_threshold DECIDE card-colour-decide-v2 / RULE card-threshold-v1.1: colour decided on the PRINTED %
  (displayed_percentage / toFixed(1)); only set/seed/capped lines colour (capped from max(T, requested)); stale, missing,
  unreadable, held or unqualified lines never colour and print a grey status ("No qualifying threshold · no line beat the
  price (n=317 priced, checked Nov 21) · not coloured"). Web reads green_from from /api/card-thresholds. app/ncaab_gate.py
  (policy ncaab-colour-gate-v1, docs/preregistration/ncaab-colour-gate-2026-11-22.md) replaces the NCAAB off switch and the
  top-4 rule: green under the card's own decision + validated market (spread/total BLOCKED: Brier 0.2602/0.2595 vs coin 0.25,
  n=218, Nov 17-21) + card's forecast-time quote + same-line price <= 20 min old + EV > 0 at that price (pushes refunded);
  recommended = max EV; others "Context only"; zero qualifiers = zero cards; verdicts append-only in ncaab_gate_decisions.
  NBA_NCAAB_CARDS=0 is the kill switch. Soccer Bet365/derived DC price as of the forecast (odds-consistency 0ef5d99).
  Nightly backup adds the Telegram ledger + rule/history/config files (backup-coverage 0636f64). Top-4 status note records
  the Nov 21 interim look and end of counting (frozen prereg untouched).
- Review: independent Opus REQUEST CHANGES (display/decision rounding split, held chain, red-block withdrawal parse,
  length/unreadable-store failure, ledger growth, lows) -> fixed test-first (8ca96c0) -> re-review APPROVE; recorder path
  EQUIVALENT-EXCEPT (best_pick_record.py byte-identical; residual: flag on + card already at the compact length limit).
  Entry appended to data/reports/weekly_report_review.json (7d125ca, 05:02:02Z); 62c3592 listed under not_reviewed.
- Release method: 7d125ca = 62c3592 + this change 3-way applied (deployed-only code kept: learning pages, 390 px CSS).
  Tests: ship tree, delivery venv: full suite 2996 passed / 9 failed = 8 test_deep_study (torch/keras absent, known) +
  test_soccer_live two-hour-window (module NOW frozen at import, fails only in long runs; passes alone and as a file on
  both trees; files untouched). Affected suites 518 passed; frontend unit 64 passed, typecheck + build ok.
- Live steps: delivery checkout 62c3592 -> 7d125ca + npm run build; NBA_CARD_COLOURS=1 added to telegram + api-delivery
  plists (bootout/bootstrap); web kickstarted; NEW job com.nba-predictor.card-thresholds (00:20 + 06:00 local,
  `app.cli card-thresholds run` from delivery under the release guard; first run exit 0: W 317 / L 400 priced, none beat
  the price; R/D 0); backup runner f859bc0 -> a7d3be1 (37 backup tests pass there; first scheduled run 09:45Z unobserved).
- Observed: 83 Telegram runs 01:34:55Z-04:59:47Z all ok, 0 sent/failed/uncertain; NCAAB gated every run, 0 qualifying.
  No NBA/NHL/NFL/Soccer card fell due after the switch, so a SENT coloured card is not observed yet.
- Backup: pre-release snapshot ~/nba-predictor-backups/stores/20261122T000756Z (journal verify ok, ledger 287 deliveries
  integrity ok, 105 files, no .env); restored to scratch: 5 stores integrity ok, counts >= earlier copy, sample forecast and
  graded rows identical. No retention/pruning exists (~0.9 GB/night); no off-machine mirror (exit 2).
- Evidence: ~/nba-predictor-research-data/colour-release-2026-11-22/ (plists-before, screenshots deployed/demo 390+1440,
  Telegram PNG/text real/demo/flag-off; demo uses a scratch store W 55.0 / L 60.0 labelled DEMO, never production).
- ROLLBACK: cd ~/nba-predictor-delivery && git checkout --detach 62c3592 && (cd frontend && npm run build); copy
  plists-before/*.plist back, bootout/bootstrap telegram + api-delivery, kickstart -k web; bootout + rm
  com.nba-predictor.card-thresholds.plist. Runner: git -C ~/nba-predictor-backup-runner checkout --detach f859bc0.
- OPEN: (1) NCAAB neutral-site home/away flip after the final grades 5 games against flipped scores (401916125, 401917538,
  401918959, 401919826, 401927396; shared ingest.py _upsert_game) - not fixed, needs a data-behaviour decision; (2) NCAAB
  total model (raw running averages, no prior/shrink) is a model change, not made; (3) quote-selection rules still differ
  by consumer (card any book <= forecast; quote_check same provider <= send; scorecard pick's provider) - listed, not
  unified; (4) recommended pick for NBA/NHL/NFL/Soccer may still be red (colour is display-only there); (5) owner: `sudo pmset
  -b sleep 0`, a --mirror target, a pruning policy, adaptive pickles/scorecard DBs in backups; (6) Odds API: 0 credits spent
  this task (3 of the approved 5 spent earlier per SPEND.json; remaining quota not verifiable from local headers).


