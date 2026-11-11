# Sport × market coverage audit: models → app → AI tickets → Telegram

- **Date:** 2026-11-08, evidence taken 20:15–20:25Z (15:15–15:25 ET).
- **Who:** Claude Opus 5.5, as lead plus 10 read-only lanes. `lanes/*.md` are the lead's condensed notes of each lane's hand-back; subagents could not write files.
- **Read-only.** Nothing was edited except this folder and one HANDOFF entry. Nothing was trained, refreshed, deployed, sent or loaded/unloaded. The reserved soccer test was not touched.
- **Live stores** were read only through `.backup`/`cp` copies in the session scratchpad.
- **HTTP:** only GET requests to 8100/8841/8851, after the handlers were confirmed read-only.
- **Checkout names used below:** L = `~/nba-predictor-live` (a55c874, clean); S1 = this tree (nfl-dev 79ff6ec + about 42 dirty paths); NFL-R = `~/.codex/worktrees/nfl-live-release/nba-predictor-sprint1` (8539513, clean); ST = `~/Desktop/nba-predictor-soccer-train`.

## 0. Bottom line
1. **Markets with live pregame forecasts.** NBA has five, NHL six, NFL five. Soccer has none.
   - NBA and NFL: winner, spread, total, home team total, away team total.
   - NHL: the same five plus regulation 3-way (reg3).
   - Every market for every competitive game in the 2-hour window is forecast by a sealed primary model. No confidence gate applies to generation. Adaptive models are never canonical, because no approval exists (coverage.py:109-140).
2. **There are no props, alternate lines, period/quarter/half markets, double chance, draw-no-bet or BTTS anywhere live.**
   - ESPN prop snapshots are stored raw and never parsed.
   - Soccer has only a sealed research 1X2 model, in ST, whose reserved test has not been run.
3. **What has actually been sent (Nov 5 to 8):** only **winner** legs and **NHL +1.5 puck-line** legs. The helper permits every market, including spreads, totals, team totals and the reg3 draw, but none of those has ever been selected.
4. **LIVE BREAK, ongoing: since 19:04:08Z today NFL is silently missing from Telegram game cards and from AI ticket review.**
   - **Cause:** an uncommitted one-line S1 edit, `coverage.py:269` (`_content_key`), modified 14:04:08 ET.
   - The NFL activation hash covers `coverage.py` (`nfl_live.py:18-20`). S1 now hashes to `aedc35c649ce50dc`, while activation.json stores `b6074877a7e7ccfe`; the lead re-computed this with shasum.
   - **Effect:** S1's `activation()` returns None. Telegram then `continue`s with no log line or alert (`telegram.py:263-266`), and the ticket snapshot marks NFL `inactive` (`tickets.py:30-32`).
   - **The NFL producer (NFL-R) is unaffected.** It recorded every window, so forecasts 51–70 (HOU@LAC, LV@SF, ARI@SEA, GB@NE) exist but were never carded or reviewed. Ticket coverage still "passed 11/11".
   - **Next affected:** TB@CHI (window opens 23:20Z).
   - **HANDOFF wording** ("NFL definition mismatch blocks open windows") implies the forecasts did not exist. They do.
   - **Fixing this is a user decision.** Options: revert or move the coverage.py edit, re-activate, or narrow the hash. This audit made no change.
5. **The live app (8100/4173) shows NBA and NHL only.** Its UI bundle was built Oct 16.
   - NFL returns 422/404. Soccer is absent.
   - `?sport=nfl` or `?sport=soccer` silently renders NBA (`Selection.tsx:12` in L).
6. **Mixed-sport tickets are genuinely supported and reviewed together.** Example: 2026-11-08-f02719e1e628 = NBA #1154 + NHL #1079.
   - Legs must be in the same 2-hour start window.
   - Since 19:04Z NFL cannot take part. Soccer never can.

## 1. Running release / configuration (lane 9; timestamps UTC, Nov 8)
| Job | Code | Notes |
|---|---|---|
| api 8100, web 4173 | L a55c874; dist built Oct 16 10:53 | NBA/NHL only (`sports.py:43` in L; `main.py:25` `^(nba\|nhl)$`) |
| odds (NBA tick 60 s), nhl-odds, daily 22:00, nhl-daily 22:15, nba-markets 22:30, injuries :02/:32 | L, NBA_RELEASE_GUARD=1 | last ticks 20:20Z; stores at schema 31 |
| nfl-live (60 s) | NFL-R 8539513, clean; runs on the S1 venv's Python | 1,899 runs, exit 0, last 20:20:17Z; schema 33 |
| telegram (60 s) | **S1, dirty, no release guard** | last 20:19:53Z |
| Codex automation `courtside-ticket-reviews` (`~/.codex/automations/courtside-ticket-reviews/automation.toml`, hourly at :10) | S1 `tickets.py` (untracked, modified 14:10 ET) + skill `~/.codex/skills/courtside-ticket-review/SKILL.md` (modified 14:09) | preflight --notify, then check/send |
| adaptive-train | **not loaded** (`launchctl print`: could not find service) | last log Nov 2; plist has no guard |
| other processes | S1 dev preview 8841/5198 (since Nov 7, stale process); ST soccer preview 8851/5251; uvicorn 8862 from nba-predictor-batch1 (started 15:20 ET, owner UNVERIFIED) | |

- **Branch divergence.** No `merge-base --is-ancestor` pair holds. L's commits 6d55e00, 7a83e27 and a55c874 are missing from S1 and NFL-R. In particular, the box-score connection fix (`jobs.py:24-31` in L) is missing from S1, so **releasing S1 as-is would regress live**.
- **Code that exists only in S1:** tickets, Telegram, `ranked_picks`, `threshold_overview`, `freshness.summary`, and the NFL/soccer serving routes.

## 2. Sport × market table
Column meanings:
- **Live fc** = canonical `game_forecasts`, last 7 days.
- **App** = the live app at 4173/8100.
- **Card** = Telegram game card.
- **AI review** = included in the ticket snapshot that the AI must account for.
- **Eligible** = the helper permits it as a leg.
- **Sent** = ticket legs actually sent, Nov 5 to 8.

| Sport | Market | Implemented (model, seal) | Live fc | App | Card | AI review | Eligible | Sent |
|---|---|---|---|---|---|---|---|---|
| NBA | Winner (incl. OT) | Elo k15 HA50, `models/frozen_decision.json` | Yes, 62 rows / 45 games | Yes, "Pick:"; Today hides <70% by default | Yes; can be "Recommended pick" | Yes | Yes | 3 legs: OKC, ATL (msg 195); PHI (245) |
| NBA | Spread | points_normal, `nba_market_decision.json` 16af84 | Yes, 95 rows, all bookmaker lines | Yes; no "no proven skill" note in L | Yes; never recommended | Yes | Yes (no market filter) | 0 |
| NBA | Total | same | Yes, 101 rows, bookmaker lines | Yes | Yes | Yes | Yes | 0 |
| NBA | Home / away team total | same | Yes, 64 / 63 rows, **100% model lines** | Yes | Yes | Yes | Yes | 0 |
| NHL | Winner (incl. OT/SO) | HockeyElo, `nhl_frozen_decision.json` d6f178ca | Yes, 53 rows / 47 games | Yes; **no OT/SO marker** | Yes; 16 recommended | Yes | Yes | CAR, DAL (199) |
| NHL | Reg3 (60 min, draw) | GoalsPoisson + empty-net | Yes, 53 rows; draw pickable, 0 draw picks (p_draw max .179) | Yes (3 outcomes); History omits it | Yes; 0 recommended | Yes | Yes, draw allowed | 0 |
| NHL | Puck line ±1.5 | same | Yes, 53 rows; DraftKings ±1.5 only | Yes | Yes | Yes | Yes | OTT+1.5, NYI+1.5 (199); BUF+1.5 (245/246) |
| NHL | Total | same | Yes, 56 rows | Yes | Yes | Yes | Yes | 0 |
| NHL | Home / away team total | same | Yes, 53 rows; model lines | Yes | Yes | Yes | Yes | 0 |
| NFL | Winner (OT counts; tie void) | Elo, seal d0e51f91; test confirmed at 0.75 (126/157) | Yes, 14 rows Nov 2–8 plus all of today's windows | **No** (L has no NFL) | Yes **until 19:04Z, then silently dropped** | Yes **until 19:04Z** | While S1 activation matched | KC×2, DET, DEN (234) |
| NFL | Spread / total | NflPoints; line test not run, `closing_line_skill` false | Yes, 14 each, bookmaker | No | Same as winner | Same | Same | 0 |
| NFL | Home / away team total | same | Yes, 14 each, **model line** (activation.json says `implied_line: true`; never implemented) | No | Same | Same | Same | 0 |
| Soccer | 1X2 at 90 min | ST only: draw-aware Elo, seal 7dbc668c, uncalibrated, **reserved test not run** | **No** | No (S1: data-only screens; ST: "Research backtest" preview ≤2022) | No (no soccer code) | No (`tickets.py:53` hard-coded `forecasts_unavailable`) | No | 0 |
| All | Player props | No; ESPN raw snapshots only (NBA 334, NHL 371; `props_collect.py:1-8`) | No | No | No | No | No | 0 |
| All | Alternate lines, quarter/period/half, first scorer | No (schema allows only the six keys; MARKETS.md:46) | No | No | No | No | No | 0 |
| Soccer | Totals, BTTS, double chance, draw-no-bet | No; ST schema `CHECK (market='1x2_regulation')` (0037:46) | No | No | No | No | No | 0 |

**What actually went out on Telegram, Nov 5 20:15Z to Nov 8 20:16Z (lane 6).** Each card lists every market.

| Sport | Cards sent | Other |
|---|---|---|
| NBA | 50 (+3 uncertain) | — |
| NHL | 33 (+1 uncertain) | — |
| NFL | 10 | Cards 227–235; none after 19:03Z |
| Soccer | 0 | — |
| Ticket messages | — | 195 and 199 (Nov 7), 234 and 245, plus PNG 246 (Nov 8) |

## 3. Settlement rules and line provenance
**Settlement code:** S1 `markets.py:58-83` and `coverage.py:424-441`. L is identical except that S1 adds "winner tie → void" (`markets.py:72`), which only matters for NFL.

| Market | Rule |
|---|---|
| Winner | Final score including OT. NHL shootout winner gets +1 goal in the stored api.nhle.com score (`nhl_stats.py:79-87, 121-122`). NFL tie → void. |
| NHL reg3 | `decision ≠ REG` (OT/SO) → draw ("mid"); NULL decision → unscorable (`markets.py:68-73`). A draw is a real outcome; a non-draw pick loses. Note: legacy `market_live.pick` / `result_for_pick` (137-147, used by daily `market_score`) never pick a draw and count a draw as a loss. |
| Spread / puck line | home − away + line: above 0 home covers, below 0 away covers, 0 is a push; OT counts. |
| Total | home + away − line, OT counts (NHL: the shootout goal is included in the score; whether DraftKings counts it is UNVERIFIED). |
| Team totals | team score − line. |
| All markets | Void if postponed, canceled, or the start time changed at all (`markets.py:58-65`). Unresolved 48 h after start with no final. Pending until `result_available_at`. A push is excluded from rates. |
| Tickets | Each leg is graded from its **original forecast_id row and line** (`tickets.py:397-486`). Any loss → ticket loss. Any pending, unscorable or unresolved leg → ticket pending, **never resolves**. All push/void → void. Otherwise win. Profit is never computed. |

**Line provenance:**

| Sport / market | Line source |
|---|---|
| NBA/NHL spread and total | Newest `market_quotes` row with `price_label='current'` (ESPN "close") and `retrieved_at ≤ now`. **No maximum age and no provider filter** (`market_live.py:47-50`). In practice it is always DraftKings. One NBA case (#4340/4341) used a 23-hour-old quote after an outage. With no quote, the model's own x.5 line is used, labelled "model" (`markets.py:261-269`). |
| NFL spread and total | Latest quote in [cutoff − 2 h, cutoff], **with no `price_label='current'` filter** (`nfl_live.py:112-121`). Latent bug; harmless today, since all 42 quotes used were 'current'. Forecast 57 used a spread quote 67.7 min older than that game's ML/total quotes. |
| Team totals (all sports) and NHL reg3 | Always model line or no price. ESPN supplies only ML, spread and total (`espn.py:283-286`). |
| Winner | No line. DraftKings ML is stored for comparison only; NFL snapshot `market_prob` is hard-coded None. |

## 4. Each layer and what it actually does
| Layer | What it does |
|---|---|
| **Models calculate** | Every market above for every competitive game whose start is ≤ 2 h away. NBA cycle every 15 min, NHL every 30 min, so first forecast at T−120 to T−105 / T−90. NFL cutoff is exactly T−120. Refused when results data is >60 min stale or a team's prior game is not final (`near_tipoff.py:20-23, 85-135`). Preseason is "unvalidated". |
| **Live app recommends** | Nothing, today. "Pick:" on every market is just the likelier side at any confidence (`Canonical.tsx:63`). The only real recommendation is the research selector, hidden unless `?details=1` (`selector.py:28-33, 188-214`): ≥100 settled, Bonferroni lower bound on lift >0. Today every NBA/NHL market is `insufficient_evidence`. NBA winner picks below 70% are hidden by default; that hid PHI #1154 (67.8%), the leg the AI picked. Dev-only, not live: NFL "Recommended pick" (winner ≥ 0.75), paper rules, the no-skill note. |
| **Telegram card "Recommended pick"** | NBA/NHL: only a market with proven skill and Wilson lower bound >0.5 (`threshold_overview.py:35-42, 299-308`), so only winner or reg3. NFL: winner ≥ 0.75. Every market is still listed on the card, with "Against model — EXPERIMENTAL" where losses exceed wins. |
| **AI reviews** | Every eligible canonical forecast in the snapshot. Eligible = start in (now, now + 2 h], generated ≥ start − 2 h, scheduled, pending (`tickets.py:59-64`). Covers all markets for NBA/NHL/NFL; each needs candidate/excluded plus a reason. The skill mandates no confidence, sample, price or label gate. Quality is presence-only. |
| **Helper permits** | 1–3 tickets of 2–3 legs. Distinct games within a ticket, so no same-game multi-market. Mixed sports allowed. Any market, including reg3 draw and team totals. Stakes $1 or $3; batch ≤ $5; daily ≤ $5, ≤ 1×$3 and ≤ 2×$1, enforced only in `send`. A game used once that day is locked to that exact forecast_id (`tickets.py:254-260`). Snapshot ≤ 10 min old; six fields re-checked at send. |
| **Actually sent** | Winner legs (NBA, NHL, NFL) and NHL +1.5 legs only. Text for every batch. PNG only for the 20:09Z batch (msg 246); PNG goes only after the text is confirmed sent. |

## 5. Traced example (lane 8): NBA #1154 PHI win + NHL #1079 BUF +1.5
- **The probability is identical at every hop:** 0.677621204 and 0.71611061 (only display rounding differs).

| Hop | NBA #1154 | NHL #1079 |
|---|---|---|
| 1. Model (L) | `predictions` #29241, Elo, computed 18:34:49Z | `market_forecasts` #4290, quote 71609 (DraftKings −285/+230, 18:34:54Z) |
| 2. Ledger | published 19:22:58Z; the run took 48 min | published 19:00:04Z, `line_source` market → bookmaker |
| 3. API | `/api/canonical/forecasts` at 20:17Z | same endpoint |
| 4. App | `Canonical.tsx:60-65`; visual UNVERIFIED | same |

- **Snapshot:** `/tmp/ticket-snapshot-20261108-1507.json` (sha 226445…, matches the review). The line survives only inside `pick_label`; there is no line field.
- **AI review:** archive `data/reports/tickets/2026-11-08-358d48…json`, 11/11 reviewed. Compared alternatives: 1162+1079 45.8%, 1154+1077 35.9%, 1154+1082 40.3%, 1154+1078 30.6%.
- **Check and send:** checks at 20:08:40, 20:09:01 and 20:09:06Z.
- **Telegram:** text msg 245 and PNG msg 246, both 20:09:06Z, sent text equal to the archive byte for byte. Settlement pending.
- **Defects seen on this path:**
  - (a) Send appends a "Shared core exposure nfl:46 $4" block that was not in the reviewed text. The ticket has no NFL leg (`tickets.py:175-183, 248-261`).
  - (b) Market titles ("Winner", "Puck line") are dropped in the text and PNG.
  - (c) The NHL card for this game said "No strong pick" while the ticket rationale said "app rank 1".
  - (d) Published time is 25–48 min after compute time. This caused the 19:08Z "missing due" and stale alerts (msgs 239/240).
- **NFL #46 KC** (tickets 6326d052a0f6, f836c8f90122; msg 234, text only) follows the same chain except hops 3–4: the live API/app has no NFL.
- **Soccer** stops at hop 1.

## 6. Hidden exclusions, fallbacks, stale sources, windows, gates, early exits
1. **NFL definition drift** (§0.4): a whole sport is dropped with no Telegram alert. The ticket helper alerts once (msg 241) and still reports full coverage. `tests/test_tickets.py` monkeypatches `snapshot()`, so no test covers it.
2. **Whole-sport skip when a source is stale.** The freshness rule is 2×interval + 5 min, i.e. 9 min pregame (`freshness.py:33,150`). Telegram skips the sport (`telegram.py:274-276`, log only); tickets exclude and alert.
   - Long forecast runs hold the store, so the sport looks stale. NBA run 7600 took 48 min, and refresh stopped 18:33–19:24Z.
   - NHL 7–25 min runs → `source_not_fresh` at 20:12Z.
3. **Hung runs.** NBA job_runs 6126 and 6465 are still `running`, and the NBA store had no writes for about 23 h (Nov 6 00:40–23:38Z). launchd will not start another instance meanwhile.
   - Missed: 7 NBA games, plus 3 NHL games during host gaps. No `missing` row is written; the gap only appears on read.
   - Since 10-17: 12 NBA + 21 NHL regular-season finals have quotes but no canonical forecast. Root cause UNVERIFIED.
4. **Silent coverage omissions in the ticket review:**
   - a whole sport errors or is inactive;
   - a market without a canonical row only goes to `without_forecast`, and `missing_due` is satisfied by any market of the game;
   - markets with no picks need no note;
   - preseason is skipped;
   - games after Toronto midnight are outside `ranked_picks(day)` (UNVERIFIED in practice);
   - a forecast published and started between two hourly runs is never reviewed.
5. **Window and lock limits:**
   - Legs must start within 2 h of the check, so there are no cross-window tickets.
   - A game used once that day is locked to its forecast_id, across markets and versions.
6. **Budget.** After the $5 cap, `check` still demands full coverage. An empty plan makes `send` return `no_ticket`. `check` passes 3×$1, but `send` rejects it.
7. **No confidence gate anywhere in the ticket path.** No-skill line markets are eligible. The model probability is presented beside an unverified bookmaker price.
8. **Sport fallback.** The live UI maps unknown sports to NBA (`Selection.tsx:12` in L). The backend has no cross-sport store fallback: store identity is checked at `tickets.py:36-38` and `telegram.py:270-272`, and only NBA may be untagged.
9. **Other Telegram issues:**
   - Duplicate cards on every re-forecast: 20 of 54 games got 2–5 cards; PHI@CHI's total flipped 238.5/237.5.
   - 7 uncertain card receipts were never retried and raise no card alert.
   - No result messages for the legacy Nov 7 tickets.
10. **NHL line markets.** `market_live.py:32` hard-codes the empty-net adjustment (commit 262d8fb, Oct 4), which the Oct 1 seal does not cover.
11. **NFL team totals** use the model line, while the activation record says `implied_line: true`.

## 7. Soccer, specifically (lane 4)
- **Sealed candidate.** ST `docs/evidence/soccer-training-2026-11-08/s4/sealed_candidate.json`, hash `7dbc668c…`, sealed 17:03:48Z.
  - Model: draw-aware Elo, market `1x2_regulation` only, calibration rejected.
  - The seal's code hashes still match at 20:16Z.
  - Validation log loss 0.9913 vs 1.0748 (from the report; not recomputed).
- **Reserved test (2023-24 to 2025-26): NOT run.** `soccer_test_access.jsonl` and `reserved-test-run/` are absent. The guard is in ST `soccer_eval.py:163-174, 941-961`; it is procedural, because the logs are editable.
- **Research preview (8851).** 9 backtest runs, 55,356 rows, all labelled "Research backtest", seasons ≤2022, 0 published. Backtest rows cannot be published (0037:30-34).
- **Live generation: none.** No `soccer.sqlite` in `data/`, no launchd job, no soccer code in L. S1 soccer is ESPN data-only and not scheduled (`soccer.py:13`).
- **Tickets and Telegram: excluded by code.** The helper loops only `('nba','nhl','nfl')` (`tickets.py:28`) and hard-codes soccer as `forecasts_unavailable` (`:53`). Skill lines :22 and :41 say the same. There is no Telegram soccer code.
- **Sources and odds.** openfootball (CC0, pinned) for research. ESPN soccer is "not clearly permitted" (SOCCER_PLAN.md). 0 soccer odds rows. All soccer training work is uncommitted, in ST only.

## 8. Unsupported or disconnected markets, with the reason
| Market | Reason |
|---|---|
| Player props | Policy: props only as a new app (`~/props-collector` exists separately). Here, raw ESPN snapshots are never parsed. |
| Alternate lines, period/quarter/half, first scorer | No model or schema. Six market keys only (`types.ts:15`, `schemas.py:18`). |
| Soccer totals, BTTS, double chance, draw-no-bet | Not built; the schema forbids them. |
| Soccer 1X2 | Research only; reserved test not run; no live job or provider. |
| NFL in the live app | L has no NFL code. The S1/NFL-R UI is dev-only. |
| NHL reg3 / team-total prices | No bookmaker quotes collected (`coverage.py:692`). Paper trading cannot cover them (`paper_trading.py:34-41`). |
| Spread, total, team totals as recommendations | "No proven skill" (`threshold_overview.py:36-42`). NFL `closing_line_skill: false`. Still card-listed and ticket-eligible. |

## 9. Gaps preventing complete all-sport, all-market review
1. NFL absent from cards and tickets because of S1 definition drift: the hash covers the shared `coverage.py`, and there is no alert or test.
2. Three disagreeing code lines: L (forecasts/app), NFL-R (NFL forecasts), dirty S1 (Telegram/tickets). S1 lacks L's fixes, and L lacks NFL, tickets and Telegram.
3. The live app has no NFL or soccer, and its UI bundle is from Oct 16.
4. Soccer has no live model, provider, odds or ticket path; its reserved test is not run.
5. Hung or long forecast runs block a whole sport and leave no `missing` row. Stale-source skips are log-only.
6. Ticket review is limited to the same 2-hour window and one forecast_id per game per day. Coverage is presence-only and can pass with a sport missing.
7. Odds feed only ML, spread and total. Team totals and reg3 have no bookmaker line or price. Spread/total lookup has no maximum quote age; NFL lookup has no 'current' filter.
8. NHL OT/SO is not shown in the app. NHL reg3 draw is pickable in canonical code but never in the legacy scorer.
9. No line field or market title in the snapshot, ticket text or PNG. Exposure text at send differs from what was reviewed.
10. Markets never offered anywhere: props, alternate lines, periods, soccer derivatives.

## 10. Checks
| Check | Result |
|---|---|
| `cd backend && NBA_DATA_DIR=<scratch>/testdata NBA_LIVE_DATA_DIR=<scratch>/livedata OMP_NUM_THREADS=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tickets.py` (lane 5; tmp dirs, mocked Telegram) | **36 passed** |
| NFL definition re-computed by the lead: `cat` of the 7 files `\| shasum -a 256` in S1 | `aedc35c649ce50dc`; activation.json `b6074877a7e7ccfe`; `git diff --stat HEAD -- coverage.py` 1 line; mtime Nov 8 14:04:08 |
| Full backend suite, `check_project.py --ready`, ticket helper (snapshot/check/send/settle), Telegram job, browser | **Not run** |
| Read-only `.backup` of nba/nhl stores | Failed in some lanes (SQLITE_CANTOPEN under sandbox). Those lanes used `cp` / `immutable=1` copies; `quick_check` ok. |

**UNVERIFIED:**
- Visual rendering in the app.
- Whether the 7 uncertain card photos were seen.
- Root cause of the hung runs and of the 33 unforecast finals.
- Owner of the 8862 server.
- The props-collector app's sources and terms.
- Codex automation run history (inferred from file times).
- Whether DraftKings counts the shootout goal toward totals.
