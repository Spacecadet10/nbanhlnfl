# All-sport independent audit — NBA, NHL, NFL, soccer (7 competitions) — 2026-11-07

Lead: Claude Opus 5.5 (session 2b, read-only). 12 lane reviewers (sport × A data/storage, B forecasting/evaluation,
C API/UI/operations) + 1 cross-sport integration reviewer, all read-only. Lead did all browser checks and the re-checks.
Durable copies: lane, integration, browser and re-check reports are in `lanes/` next to this file; the remediation plan
is in `remediation/`. Session evidence root (scripts, store copies; session-local, may be cleaned): `/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/`
(`audit/<SPORT>-<LANE>.md`, `audit/INTEGRATION.md`, `audit/BROWSER-lead.md`, `audit/LEAD-recheck-snap3.md`, `work/<lane>/`
scripts + outputs, `lead/` snapshots/diffs). Scratchpad is session-local: copy anything needed long-term.

## 0. What was audited, and what this does not show
- Trees (HEAD 79ff6ec + uncommitted diff, branch nfl-dev). Another session (`ce`, sole writer, user-assigned soccer fixes) kept
  editing during the audit, so three frozen copies were used:
  `snap` 20:28:31Z (NBA/NHL/NFL lanes, lead browser checks, dev API :8871/:5271) → `snap2` 20:38:05Z, git-diff sha `afd18db`
  (soccer lanes, integration) → `snap3` 20:55:17Z, git-diff sha `330feed` = the tree under ce's readiness gate (lead re-checked
  ce's post-audit fixes only). snap→snap2 differs only in soccer.py, sources/espn.py, 0035 (index), test_soccer.py, docs.
- Data: read-only SQLite backups (`mode=ro` backup API) of live `data/{nba,nhl,nfl}.sqlite` at 15:29 ET (schemas 31/31/33)
  and ce's scratch soccer store (35; no live soccer store exists). Writes only to copies under `work/`. Raw cache read in place.
- Not shown by this audit: that any model has betting skill (lanes found none claimed with evidence; market Brier beats the
  model on the NBA winner market (0.191 vs 0.218, n=120) and in every quoted NHL market (121 games, point estimates)); correctness of code paths no lane traced; the live API
  :8100 (one read-only GET to the release web app :4173 /api/sources was made by NBA-C by mistake — disclosed, no write).
  Audit hygiene: the lead's rsync snapshots copied `.env.telegram` (bot token) and the delivery ledger into the scratchpad;
  found by BATCH-C, deleted by the lead (no token string remains in the scratchpad; nothing was run from them that sends).
  Passing tests establish only what they assert. A passing readiness gate or an S0/S1 "accept" below is engineering
  acceptance only: it is NOT acceptance of model performance, of a betting edge, or of any source's use (see M17, section 4).

## 1. Actionable findings (deduplicated, most severe first)
Status: CONF = reproduced/verified by lead or integration reviewer; LANE = one lane's evidence, not independently reproduced;
FIXED = fixed in snap3 and re-checked by lead. Paths relative to repo root, snap/snap2 line numbers.

### HIGH
| # | Finding | Sports | Evidence | Status |
|---|---|---|---|---|
| H1 | [risk, latent defect] A write command run from the dev checkout with NBA_DATA_DIR unset migrates the LIVE stores to 35 (`config.py:43-44` default `REPO/data`; migrate on connect `cli.py:580-582,679,735`); then `release.check` refuses every guarded scheduled job (release.py:80-84 "35 differs from 31"; NFL "35 vs 33"). README "Run manually" (L71-74) starts the dev API on live data/. No guard outside dev.sh. | NBA NHL NFL | INTEGRATION I1 reproduced on copies; NBA-C HIGH-2 | CONF |
| H2 | [confirmed config defect] `com.nba-predictor.telegram` runs every 60 s with `REPO/backend/.venv` and WorkingDirectory `REPO/backend` against `REPO/data`, without `NBA_RELEASE_GUARD` (the only scheduled job without it): user-facing pick cards are computed by the uncommitted working tree, not release a55c874. Contradicts README:186-187. Mitigation verified: it opens stores via `db.connect_readonly` (`query_only`) and dispatches before any migrating command; live schemas still 31/31/33 at 15:5x. nfl-live runs a clean guarded worktree (8539513) but on the dev venv (torch/tensorflow added Nov 7 13:23; numpy/scipy/sklearn identical — I12). | NBA NHL NFL | plist read by lead; NBA-C HIGH-1; INTEGRATION I12 | CONF |
| H3 | [confirmed defect] NFL store is not reproducible and has no supported backup: 295/510 raw responses (the whole Nov 1 16:17–18:24Z backfill; 8,094/8,108 finals) have no bytes (NBA 0/5,555, NHL 0/17,566 missing); `nfl replay` → FileNotFoundError ×2; `app.backup` covers only nba/nhl (`backup.py:23`, `--sport nfl` invalid). Migration 0035 rebuilds tables. Replay also never rebuilds NFL line quotes (`ingest.py:295-297`; 0 vs 10,776). Contradicts ingest.py:1-3, NFL_PLAN.md:17. | NFL | lead count script `lead/missing_bytes.txt`; NFL-A F1/F2; INTEGRATION I2/I9 | CONF |
| H4 | [confirmed evidence defect + policy] Reserved-season evidence is overstated. NBA: `data/eval/test_access.jsonl` has 5 lines; EVALUATION_PLAN.md:104 says "must stay at exactly 1 line" and "never used to … select". The 4 extra (players-v1, Oct 10) are labelled user-requested amendments (PLAYER_RATINGS_STUDY.md §3) — recorded, not hidden — but the rule text was never amended; pool-v1 assess (Oct 2) used 2025/2026 to decide eligibility (`pool_assess_access_nba.jsonl`). Thresholds API/UI show "held up in 2024-25–2025-26" as confirmation, and the seasons choosing the "best" threshold are Elo's tuning seasons. NHL: same pattern — threshold "earlier" seasons 2018-24 are hockey_elo tuning/selection seasons; "later" 2025-26 are test seasons reused by pool stage 2 (all 5 seasons, `pool_assess_access_nhl.jsonl`) and holdout A. "Best 0.70 / held up" and ranked-pick "strong" overstate. | NBA NHL | lead read all 4 access logs; NBA-B F1, NHL-B F1 | CONF (logs) / LANE (UI wording) |
| H5 | [confirmed defect, release risk] The dev line does not contain the live releases: 7a83e27 and a55c874 (NBA/NHL release) and 8539513 (NFL release) are not ancestors of HEAD 79ff6ec (lead: `git merge-base --is-ancestor`). The tree lacks the cycle clock, refusal rate limit and box-score connection-failure retries, and still has the early forecast wake-ups 7a83e27 removed pending a user timing decision. Releasing this tree (e.g. for 0035) would undo reviewed live fixes and change forecast timing. | NBA NHL NFL | BATCH-C N1 (plan/BATCH-C.md) + lead ancestry check | CONF |

### MEDIUM
| # | Finding | Sports | Evidence | Status |
|---|---|---|---|---|
| M1 | 0035 claim "a team change on a final with an unchanged score is a recorded correction" failed for every pre-0035 final (NULL team columns; `ingest.py:547-549`, `soccer.py:207`). | all 4 | NBA-A F1; INTEGRATION I3 (all sports) | FIXED in snap3: NBA probe S7 now 2 revisions; live-NHL copy: new retrieval of a 1,394-final season list added only 9 genuine OT→SO decision revisions, 0 spurious (hazard in I3 absent) |
| M2 | Canonical forecast is stamped when `coverage.record` runs, last in the cycle (jobs.py order, `coverage.py:345`), not when computed; `real.py:892` uses that later time for quote eligibility; "made N min before the start" and "retrieved no later than the forecast" (Canonical.tsx:72,101,179) are off. Lag NBA 901 rows avg 11.8 / max 70.4 min; NHL 841 rows avg 9.6 / max 53.9; NFL 0. 11/120 NBA games' canonical is older than the latest Elo row; NHL record 762 compared to a quote 37 min after the model's. Direction favours the market (no look-ahead for the model). | NBA NHL | INTEGRATION I5 quantified; NBA-B F2, NHL-C F2 | CONF |
| M3 | Unattended-operation gaps are silent: no job_runs in NBA or NHL stores 2026-11-04 22:09→11-05 01:04Z and 11-06 00:46→23:38Z (pmset Maintenance Sleep; sleep=1 min; no keep-awake); 3 job rows stuck `running`; 12 NBA + 3 NHL games have no canonical forecast; NFL result 22.4 h late. Freshness reads only `job='refresh'` (`freshness.py:72-78,136-150`), so odds failures and NFL release-guard refusals (recorded as job `odds`) never show "failing"; NFL blocked/inactive exit 0 with no row. | all | NBA-C HIGH-3, NHL-C F3, NFL-C F1, NFL-A F8 | LANE (two lanes agree on windows) |
| M4 | NFL live line forecasts pick the bookmaker quote without `price_label='current'` (`nfl_live.py:112-114`); correct today only because the parser inserts open before current (3,093/5,388 open/current pairs differ). | NFL | lead read code; NFL-B repro `work/NFL-B/scripts/repro.py` (−7.0 open used) | CONF |
| M5 | NFL definition hash covers shared `coverage.py`, `markets.py`, `models/base.py`, `models/elo.py`: a comment-only edit hides every recorded NFL forecast ("not activated") and silently stops NFL Telegram cards; existing test still passes. | NFL ← NBA/NHL | INTEGRATION I4 reproduced; NFL-C F3 | CONF |
| M6 | NFL one-shot test is enforced per seal and per data dir (`nfl_eval.py:660-670`, log under NBA_DATA_DIR); `activate` accepts any hand-written log line and report (`nfl_live.py:40-48`; test fixture does exactly this); original `/tmp/courtside-nfl-eval` log gone. Seal-before-test rests on mtimes/self-reported fields (seal first committed 27 min after the test). | NFL | NFL-B F2/F3 | LANE |
| M7 | One malformed final (empty/missing score) aborts the whole response, unrecorded: `espn._parse_scoreboard` (`espn.py:153-154`, NBA+NFL) and `nhl_stats.py:121-122` (NHL copy); gz written, no fetch logged. NFL unknown status → 'other' can block NFL forecasts up to 7 days (`nfl_live.py:97-101`, code only). Soccer: home==away now goes to `soccer_rejections`; other malformed events still abort the day. Not seen in real data. | NBA NHL NFL soccer | INTEGRATION I6; NBA-A F2, NFL-A F4, NHL-A F3, SOCCER-A F6 | CONF (fixtures) |
| M8 | "Replay rebuilds the same rows" (ingest.py:1-3) behaves three ways: same-store replay never re-applies importer changes (62 NHL preseason/other shoot-outs stored as OT — lead SQL: preseason OT/5 periods 61, other 1; nhl:2026020007 availability 20 h early); soccer re-derives (now labelled `reclassified`, FIXED wording); fresh rebuild re-derives all. A genuinely new retrieval does correct NHL labels (lead: 9 of the 62 fixed by re-ingesting the cached 2025 list). NHL shot/xG availability is back-dated to the result time when pbp is fetched > 24 h later (`nhl_shots.py:27-29,47-48`; 22 games avg 18.6 h, max 36.7 h) — any `available_at ≤ cutoff` rebuild would see data live did not have. | NHL NBA NFL soccer | INTEGRATION I7; NHL-A F1/F2/F4; lead SQL | CONF |
| M9 | DB enforces store sport only for `store_identity`, `market_quotes`, `market_forecasts`, `soccer_*`: plain SQL accepts cross-sport `games`, `teams`, `game_forecasts`, `espn_soccer` raw rows in any store (an `espn_soccer` fetch row changes an NBA store's freshness revision). All code paths guarded (StoreMismatch). Shared fallbacks to 'nba' for unlabelled stores (`db.py:26`, `jobs.py:66/90/818`, `coverage.py:466`) not reachable with today's labelled stores. | all | INTEGRATION I8/I10; NFL-A F3 | CONF |
| M10 | NHL finals never show OT/SO: shared `Outcome` has no decision (`schemas.py:227-230`); GamePage/TodayPage/History show score only, so a correct reg3 loss on an OT game reads "MTL in regulation | UTA 4 – 5 MTL | Wrong". | NHL | NHL-C F1; INTEGRATION I11 | LANE (API JSON shown) |
| M11 | UI says NFL is "data only / no NFL model" (`Layout.tsx:124`, `NflPages.tsx:290,295`) while NFL forecasts/odds/record are active (10 rows); NBA legacy-view wording on NFL games (`coverage.py:588`). | NFL | NFL-C F2; INTEGRATION I11 | LANE |
| M12 | API describes job settings from its own env: "refresh job is off (NBA_PLAYER_BOX_REFRESH is not 1)" (`providers/real.py:363-366,387-388`) and NHL "not captured live: NHL_INJURY_CAPTURE is not 1" (`real.py:408-409`) though the job plists set them to 1. Two freshness verdicts at once (`/api/sources` 60-min "fresh" vs `/api/freshness` 540-s "stale"). | NBA NHL | NBA-C M4/M5, NHL-C F4/F6 | LANE |
| M13 | Versioning gaps: `game_forecasts` stores no dataset/feature version (126 NHL `primary_direct` rows untraceable); `model_version` lacks code/decision digest; NBA coverage-computed fallback rows have no data version/source link; adaptive identity in the tree ≠ stored, so the dev API shows an empty adaptive record; adaptive-train is installed but not loaded (no fit since 2026-11-02) — intent unconfirmed. | NBA NHL | NBA-B F3/F5, NHL-B F2 | LANE |
| M14 | `.table.history` (`Canonical.tsx:135`) has no scroll wrapper and only collapses to cards at ≤580 px: page scrollWidth 694/600 (NBA History), 710/600 and 710/700 (NHL History). 390 px and 1440 px clean everywhere. | NBA NHL | lead browser, `audit/BROWSER-lead.md` | CONF |
| M15 | Soccer stale states shown as current: past-kickoff "IN PROGRESS"/plain kickoff with no "as of last import" (4 in-progress rows); match page never shows last import time. | soccer | SOCCER-C M3 | LANE |
| M16 | Soccer S1 independence: the 2 FULL_TIME-labelled extra-time matches (169930, 416637) have no independent check (169930's 1–0 depends on removing byte-identical duplicate goals); `recheck/wiki_et.py` hardcodes the ESPN 90-minute values it reports as agreeing (re-derived: 8/8 agree); EPL–openfootball agreement is "two publications agree, provenance unknown", not independent validation. | soccer | SOCCER-B 1-3 | LANE (recomputed from cached bytes) |
| M17 | Source terms vs current use (interpretation flagged): Disney ToU (updated 2024-05-24) 2.A personal/noncommercial, excludes AI/ML training/testing/validation; 2.B(x) bars automated access; NBA.com ToU (2026-07-13) §9: statistics "may not be used in connection with any gambling activity"; NHL.com ToS (2025-10-29) bars "unauthorized automated means to compile information". Repo still says ESPN has "no published terms" (`espn.py:3`, `DATA_SOURCES.md:13`, contradicted by DATA_SOURCES.md:112). The user's 2026-09-30 personal-research decision predates the hourly real-money ticket reviews; no recorded NHL decision. | all | NBA-A F3, NHL-A F7, NFL-A, SOCCER-B (pages read 2026-11-07) | user decision |

### LOW (grouped; details in lane files)
- NBA: no period count (3-quarter preseason espn:401365905 stored as normal final); readiness check reads scoreboard box, features use possession box (espn:401909838 11.6 d late); team names overwritten in place, before the stale check (all sports, `ingest._upsert_team`); first replay adds 642 pre-table quotes (also HEAD); `predictions` key returns earlier row on A→B→A; nightly `score` scores 0 rows; 108/238 near_tipoff runs "failed" (`AvailabilityError: Invalid player minutes`). (NBA-A F4–F7, NBA-B F6–F8)
- NHL: decision-only correction leaves `updated_at`/`last_raw_id`; DATA_SOURCES.md:201 "≥30 min apart" contradicted (58 s–3 min); 92 goalie snapshots after start; provider spelled two ways; reg3 draw pick grades win (canonical) vs loss (legacy) — latent; MARKET_EDGE_RESEARCH.md still calls NHL "the only live question", omits holdout A FAIL (−6.4% ROI, 2026-10-16); test asserts tipoff order not availability; "Tipoff/play-in" wording. (NHL-A F5–F8, NHL-B F5–F8, NHL-C F5–F9)
- NFL: OT not stored; all 8,108 revisions 'observed' incl. backfill; `games.season` comment says ending year; historical games show current names; team-total live lines are model 50/50 lines graded W/L unlike evaluation's implied lines; winner "probability" is expected score with tie mass 0; skipped games leave no reason; serving never rechecks dataset version; threshold .75 / min 200 hard-coded; n=2 record shown with no interval; `/games/nfl:<id>` without `?sport=nfl` → NBA-page 404. (NFL-A F5–F7, NFL-B F4–F8, NFL-C F4–F9)
- Soccer: no `competition` → Premier League served (API); PUBLISH_INTERVAL > ~9.3e9 crashes publisher after dev.sh claims republishing; final with one missing score stored as 'other' keeping the other score; `soccer_goal_minutes` dedup used only in tests (42 finals with unusable goal lists); 2001-era FULL_TIME ET with no period/clock evidence would read regulation (none cached); back link drops date; penalties wording assumes ET; S1: Betfair 9 rows not 8, `count_output.txt` "season label" is league's current season, no UEFA source probed, raw S1 bytes only in another session's scratchpad. (SOCCER-A F3–F7, SOCCER-B 4, SOCCER-C LOW)
- Shared UI: `?sport=NHL`/unknown → NBA silently (contradicts Selection.tsx:6-8); only soccer ids infer their league from the path; Daily Learning ignores `?sport`; copies without SNAPSHOT_AT labelled "Live data" (incl. the audit instance); "Real data" popover time stale after fetch-only refresh; dev copy publishes fetch-only refresh after 10 min but marks stale after 7–9 min. (lead browser, NBA-C LOW-7/8/10, M6, NHL-C F7)

### Earlier-audit (15:00 ET) soccer findings — status on the gated tree
FULL_TIME+ET (169930, 416637 → `unknown` with reason), stale tie facts on correction, tie-fact availability, same-second /
populated replay duplicates, team change revision, Canonical 390 px overflow, soccer publish trigger, PUBLISH_INTERVAL<30,
home==away aborting the day: all FIXED and reproduced (SOCCER-A/C on snap2, lead on snap3 for the post-audit fixes). Over all
2,306 cached real finals: FULL_TIME→regulation 2286, AET→ET 3, PEN→penalties 5, unknown 12; no league match labelled ET.

## 2. Per-sport claim table (selected; full tables in lane files)
| Sport | Claim (source) | Status |
|---|---|---|
| NBA | Replay A→B→A / same-second / older-late adds no duplicates (0035) | verified (snap: 3/2/unchanged; HEAD 3→7, 2→6) |
| NBA | Features use only results with result_available_at ≤ forecast time; box corrections at own retrieval time | verified (NBA-A/B; espn:401901822 117-98→116-98 moved availability) |
| NBA | Performance numbers (canonical winner 75-45, Brier 0.218236; Elo backtest 2643, 0.6720) reproduce from rows | verified (NBA-B) |
| NBA | "test_access.jsonl must stay at exactly 1 line" (EVALUATION_PLAN:104) | contradicted (5 lines; amendments recorded) |
| NBA | "Everything unattended uses the release worktree" (README:186-187) | contradicted (telegram) |
| NBA | ESPN "no published terms" (espn.py:3) | contradicted (Disney ToU) |
| NHL | Final score includes the shoot-out goal; reg3 settles OT/SO as draw; puck line/totals count SO goal | verified (raw pbp e5c79066…; 15 hand grades) |
| NHL | Goalie/injury records stamped at retrieval, read as-of | verified |
| NHL | "Import is a pure function of cached bytes, replay rebuilds the same rows" | contradicted (62 decisions, 1 availability, xG times) |
| NHL | Canonical 78-43 Brier 0.22519; near-tipoff 132/210; Wilson CIs | verified (NHL-B) |
| NHL | "with a bookmaker moneyline retrieved no later than the forecast" | contradicted (record 762) |
| NHL | MARKET_EDGE_RESEARCH "NHL tied / only live question" | contradicted (holdout A FAIL in HANDOFF only) |
| NFL | Ties stored as finals with equal scores; season = start year; neutral = SB + international | verified |
| NFL | Activation 2026-11-02 22:11:33Z; no backfill labelled live; 10 rows all post-activation | verified |
| NFL | Inputs stop at kickoff−120 min; quotes within 2 h of cutoff; ties void | verified (positive control) |
| NFL | `nfl replay` rebuilds the store (NFL_PLAN:17) | contradicted (295 missing bytes; quotes not rebuilt) |
| NFL | "NFL is data-only" (main.py:118, Layout.tsx:124, NflPages.tsx:290) | contradicted |
| NFL | "Run the test once" | process only, not enforced (per seal/data dir) |
| NFL | "NFL percentages verified" (HANDOFF) | verified only as display = stored rows, not model quality |
| Soccer | No soccer forecast/model/job exists (backend, API, UI, launchd, release) | verified (absence, searches in SOCCER-B) |
| Soccer | 412 calendar-2023 EPL (216+196) and 380 2023-24 match openfootball on FT score+date | verified (also HT 369/369) — agreement, not independent validation |
| Soccer | 115 S1 probes / 76 recheck / 21 licence requests: sha256+size | verified |
| Soccer | ≥10 of 62 sampled UEFA knockouts went to ET/pens (REPORT current) | verified (10); old "8 of 62" contradicted |
| Soccer | Payload league ≠ requested competition rejected before writing | verified |
| Soccer | Same club same `soccer:<id>` across domestic/UEFA; no id collision with espn:/nhl:/nfl: | verified (64 shared ids) |
| Soccer | "corrections are recorded in HANDOFF.md" (SOCCER_PLAN:26-28) | contradicted at snap2 (ce writes HANDOFF after the gate) |
| Soccer | Canceled/abandoned/suspended mapping | unverified on real data (synthetic only; suspended not tried) |
| All | NBA/NHL/NFL freshness revisions unchanged by the soccer part | verified (HEAD vs snap2, schemas 31/33 and 35) |
| All | No page shows another sport's or demo data on failure | verified (browser + API matrix) |

## 3. Tests, browser and readiness — actual results
- Readiness gate: run by session `ce` (agreed; exactly one active run at a time). Run 1 (`ready-final-audit.log`, started
  20:37:00Z, tree afd18db) ABORTED by ce to fix findings — VOID, no exit code. Run 2: `ready-final-2.log` (ce scratchpad
  11c8ef97…/scratchpad/), START 20:54:59Z, HEAD 79ff6ec, GITDIFF_BEFORE 330feed (= lead snap3), command
  `OMP_NUM_THREADS=4 backend/.venv/bin/python scripts/check_project.py --ready`. RESULT (lead read the log): READY_EXIT_CODE 1 — structure, typecheck, build passed; pytest 1 failed / 1668 passed
  (24:52), END 21:20:04Z, GITDIFF_AFTER f37e239 (ce: only Codex HANDOFF appends during the run). Failure:
  `test_research_agent.py::test_f_low2_registration_refuses_a_workspace_that_configures_programs` assumes `/tmp/x` is absent;
  `/tmp/x` (158-byte soccer API response, created 15:32:51 ET) was almost certainly written by this audit's API reviewers
  (`curl -o /tmp/x`, a breach of their brief) — removed by the lead 16:2x ET. ce changed the test to use tmp_path and will run
  one more gate. RUN 3 (`ready-final-3.log`, lead read the log): START 21:23:59Z, END 21:49:52Z, same command; structure
  guard passed, typecheck + vite build ok, pytest 1669 passed / 0 failed (1 warning, 25:39); READY_EXIT_CODE 0.
  CODE_TREE_BEFORE = CODE_TREE_AFTER = 2275eab21c1a8876582130e70c5ded4fa3c393fc (recipe: `{ git diff -- . ':(exclude)HANDOFF.md';
  git ls-files -o --exclude-standard | sort | xargs shasum; } | shasum`; lead recomputed 2275eab before the run). Gated code =
  snap3 + the tmp_path test fix (lead reviewed: guard refuses on the `include.` key, so an absent include path still exercises
  the intended refusal; control: a JSON include file makes git abort with "bad config line 1", the cause of run 2's failure;
  test passes in isolation). Changes AFTER the gate are documentation only (lead compared every changed file with snap3):
  docs/SMOKE_TEST.md and HANDOFF.md (ce), this report under docs/ and the HANDOFF audit entry (lead). No re-run needed for those.
  The gate shows the automated checks pass on this code; it is not evidence about data correctness beyond what the tests assert,
  model quality, or source permission.
  (fe's earlier run 2, 15:01 ET, covered a tree that changed under it — not evidence for this tree.)
- Focused tests run by reviewers (frozen code, never the full suite): NBA-A test_replay_same_store 20 passed (20 failed on
  HEAD code), test_storage 24; NBA-B backtest/near_tipoff/canonical 25; NHL-A 13; NHL-B 12; NFL-A 32+3; NFL-B 25; SOCCER-A 60;
  SOCCER-C test_soccer 5 + test_dev_script 12, `node --test competition.test.mjs` 3/3; INTEGRATION 51. None covers M2, M4,
  M7 or the H1 guard. ce reported "446 passed (21 suites)" before freezing snap3 — reported, not observed by lead.
- Lead re-checks (snap3): NBA-A probes (S1 3/3/3, S2 2/2, S7 2 revisions); 0034→0035 soccer migration vs rebuild 0/58 diffs
  (20 tie-fact matches incl. 733144); live-NHL copy migrate + replay ×2 (16003 revisions unchanged, 0 updates) and new
  retrieval of cached season list (+9 genuine OT→SO, 0 spurious); missing raw bytes per store.
- Browser (lead, Chrome, same-origin iframe of exact width on snap dev instance): 15 NBA/NHL/NFL pages × 390 and 12 × 1440, 12
  soccer pages × 390 and 9 × 1440: scrollWidth = viewport, content loaded; intermediate widths: History overflow (M14);
  wrong-sport/unknown ids → explicit 404; error → recovery (fetch forced to fail, then Retry) clears every error in all four
  sports. Not run: dev.sh-launched instance, real backend stop/restart, screenshots. Caveat: the audit instance had no
  SNAPSHOT_AT/publisher, so its "Live data" chip is not representative of dev.sh.

## 4. Source cost / licence / coverage gaps (free-only rule)
Superseded in detail by `remediation/PLAN.md` "Source suitability" (explicit text vs interpretation, per use), which
corrects this table after the independent challenge (e.g. NBA.com §9 applicability to injury reports is interpretation).
| Source | Public | Zero cost | Permitted use | Coverage evidence |
|---|---|---|---|---|
| ESPN site APIs (NBA, NHL odds/goalies, NFL, soccer) | yes | yes (no key) | NOT established: Disney ToU 2.A/2.B(x) explicit; user's 2026-09-30 personal-research decision recorded; staked-ticket use not decided | NBA/NHL complete bytes; NFL 295 backfill files missing; soccer S1 probes cached in another session's scratchpad |
| NHL.com / api.nhle.com | yes | yes | NOT established: ToS bars unauthorized automated compiling; no recorded user decision | 17,566 raw, 0 missing |
| NBA.com (injury reports) | yes | yes | §9 bars use "in connection with any gambling activity" — conflicts with ticket reviews if inputs feed them (unverified which do) | — |
| sdv team box (MIT repo, ESPN content) | yes | yes | personal research only (as recorded); NBA-only, refused in NFL store | — |
| openfootball (soccer) | yes | yes | CC0 (re-fetched 2026-11-07) — the only clearly permitted soccer source; has known errors | EPL 2023/2023-24 agreement verified |
| football-data.co.uk | yes | yes | terms + robots exclude automated/AI use; manual download is not a workaround | not fetched |
| StatsBomb open data | yes | yes | explicit restrictions (per cached terms) | not assessed for coverage |
| Betfair | region-blocked | unknown | unknown (terms not read) | — |
| UEFA sources | — | — | not searched | gap |
Scope actually searched: the sources above only; this is not a claim that no free permitted source exists. Odds are optional:
the soccer results-only path (openfootball) is not blocked by the lack of free odds.

## 5. Recommendations
**Engineering readiness — NOT ready to release 0035 or call the tree "ready" until:** the gate result above is a pass on
330feed; H1 has a guard (dev-checkout write commands refuse `REPO/data` unless an explicit release flag) with a failing-first
test; H2 Telegram job is moved to a guarded release worktree/venv (launchd change — needs the user's go); 0035 release plan
covers all three release checkouts together (release.check is exact-match), a manual NFL backup first (backup.py has no NFL),
and replay-after-migrate for soccer; M5 NFL hash decoupled (or re-activation planned) before anyone edits coverage.py/markets.py.
**Data correctness:** NBA and NHL stores reproduce from bytes (verified replay on copies). Fix order: H3 (locate/re-fetch the
295 NFL files — network needs a go; add a missing-bytes preflight to release.check/replay/backup), M4, M7, M8 (re-derive
importer changes on replay explicitly and label them, as soccer now does; correct the 62 NHL decisions via a recorded
re-derivation, not silent edits; xG availability = retrieval time), M9 DB triggers for games/teams/game_forecasts per store.
**Model/evaluation validity:** no lane found leakage in features or settlement; settlement reproduces by hand for NHL SO/OT and
NFL ties/lines. But: treat 2024-25/2025-26 (NBA) and 2025-26 (NHL) as used — label threshold "held up" verdicts and "strong"
picks as in-sample/reused and disclose the contamination next to EVALUATION_PLAN:104 without changing the rule or retroactively validating the reuse; evaluate prospectively from a sealed date (H4); expose computation time and use it for quote eligibility (M2); add
dataset/code versions to canonical rows (M13); make the NFL one-shot guard sport-wide and verify report integrity at
activation (M6). Bookmaker Brier is better than the model on the NBA winner market and every quoted NHL market (live record, point estimates, no intervals); nothing here
supports a betting edge (consistent with the recorded 2026-10-16/11-01 decisions).
**Soccer S0/S1 acceptance:** S0 — the earlier audit's blockers are fixed and reproduced on the gated tree; ACCEPT S0
conditionally on (a) a passing gate — satisfied by run 3 (exit 0, code tree 2275eab), (b) M15 stale-state labels (or recorded as known), (c) 0035 not applied to
any live store without the release plan above. S1 — ACCEPT with corrections: relabel validation independence (M16), replace
hardcoded values in wiki_et.py, Betfair 9 not 8, record "no UEFA source probed", move the S1 raw bytes from another session's
scratchpad into a durable evidence location. Source permission (M17) remains the user's decision and blocks S2 on ESPN, not on
openfootball results-only.

## 6. Narrow fix plan — SUPERSEDED by `remediation/PLAN.md` (three reviewed batches + independent challenge). Kept for history.
Proposed writer: one session assigned by the user (session `ce` has the shared-ingest context); this auditor stays read-only.
0. User decisions (no code): ESPN/NHL/NBA.com terms vs staked tickets (M17); move Telegram (and nfl-live venv) to a release
   worktree/venv (H2); is adaptive-train meant to be unloaded (M13); re-fetch 295 NFL files (H3, network); 0035 release timing.
1. Guards (shared, small): refuse writes to REPO/data from a dev checkout (H1); NFL backup support + missing-bytes preflight
   (H3/I9); NFL definition hash limited to NFL-owned files or a recorded shared-file digest with re-activation path (M5).
2. NFL correctness: `price_label='current'` in nfl_live (M4) + regression test inserting open after current; record skipped
   games with reason; replay rebuilds NFL quotes.
3. Shared parser robustness: per-event rejection rows for malformed finals in espn/nhl_stats parsers (M7), tests per sport.
4. Timing honesty: canonical rows carry computation time; quote eligibility uses it (M2); freshness shows odds/guard failures
   and stuck runs (M3).
5. UI honesty: NHL OT/SO on finals (M10), NFL "data only" text (M11), env-based job notes (M12), `.table-wrap` for
   Canonical.tsx:135 (M14), soccer "as of last import" (M15).
6. Evaluation docs: contamination disclosure (logs preserved, rule unchanged), threshold history labelled reused/in-sample (NBA, NHL), MARKET_EDGE_RESEARCH
   NHL holdout A FAIL, MARKETS.md team totals include SO goal, S1 REPORT corrections (M16).
Out of scope here (per brief): S2 implementation, training/calibration, reserved-test access, job/env changes, deployment.
