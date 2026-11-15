# Courtside parlay workflow audit — 2026-11-08 (read-only)

Claude Opus 5.5 lead; 10 lane agents + 1 adversarial verifier. Code under audit: working tree on nfl-dev (HEAD 79ff6ec),
backend/app/tickets.py sha 01f9edd787dd (unchanged during the audit), installed skill/wrapper/automation under ~/.codex.
No repo code, ~/.codex, launchd or data/ file was changed. Telegram mocked in every run; live ledger only read via copies.
Lane scripts/logs: /private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/77ca1c52-38b7-48e4-a3dd-f779b67607f6/scratchpad/a1..a11
(temporary). CONFIRMED = reproduced; PLAUSIBLE = code reading only.

## Findings (most severe first)

### HIGH
1. **No visual parlay ticket is ever sent** (requirement gap, CONFIRMED). tickets.py:274-275 sends `result['text']` via
   telegram.deliver; telegram.py:225 renders an image only for game-card text. `telegram_card.render_ticket_card`
   (telegram_card.py:233) has one caller: backend/tests/test_tickets.py:235. Message 224 was text (key
   `ticket-format-test:2026-11-08:v1`, 647 chars, sendMessage) and its TEST header was hand-edited outside code; no visual
   v3 ticket test was ever sent. The renderer also lacks Ticket numbering, ticket IDs, start date/time/timezone and the
   "combine all legs in one parlay" line. Fix: after the text, send the PNG under its own delivery key linked to the batch,
   caption ≤1024 chars, archive PNG hash; add numbering/IDs/times to the renderer; add a code-level test/preview mode.
2. **`retry` delivery strands the ticket and its budget silently** (CONFIRMED by 4 lanes + verifier). On ConnectError /
   ConnectTimeout / HTTP 429 the row is `retry`; tickets.py:229-231 reuses the archived result and :275 passes the archived
   `as_of`, so telegram.py:230 (`next_attempt_at > now`) returns `already_recorded` forever (exit 0). Reservation kept;
   tickets.py:471 alert ignores `retry`. Fix: pass the current time on retry, alert on `retry`, return an explicit status.
3. **Live Telegram sender and ticket helper run the uncommitted working tree** (CONFIRMED). com.nba-predictor.telegram runs
   sprint1/backend every 60 s without NBA_RELEASE_GUARD; the wrapper's REPO is sprint1 and the venv's editable install maps
   `app` to it regardless. Edits here go live within 60 s / next hourly run. The first live v3 run was 10:12 ET today.

### MEDIUM
4. **Telegram/network failure stops settlement** (CONFIRMED). tickets.py:400-401 `bot.identity()` precedes grading; with
   `--notify` (always used) a DeliveryError aborts before any grade or snapshot (0 rows). The automation thread's sandbox
   network is restricted; the 10:13 run needed a network retry. Contradicts docs/TICKETS.md:15. Fix: grade first, notify
   after, catch DeliveryError per message.
5. **False "DELIVERY REVIEW REQUIRED" alert — sent live today** (CONFIRMED). tickets.py:471-474 counts every
   uncertain/sending/failed delivery incl. game cards, from UTC midnight of the Toronto date, one key per day. The 10:12
   run sent it (message 225 per the Codex entry) for an uncertain HOU@PHX game card from Nov 7 19:28 ET. A real ticket
   failure later the same day would get no alert. Fix: ticket/settlement keys only, Toronto-day bounds, key on counted rows.
6. **Early coverage writes duplicate canonical rows and breaks an existing test** (CONFIRMED; dev-only). jobs.py:413-416
   records before adaptive/research; coverage `_content_key` includes `fallback_reason` (coverage.py:262-269), whose text
   changes, so the final record appends a second row with identical source/probability/line. test_coverage.py:278 fails
   `assert (2 == 2 and 6 == 12)` and passes with HEAD's jobs.py. Once promoted: new forecast_id → likely second Telegram
   card (PLAUSIBLE) and ticket re-review / same-game lock (see 8). Fix: reason-only changes must not create a version.
7. **Ledger ignores NBA_DATA_DIR** (CONFIRMED). telegram.py:70 → REPO_ROOT/.telegram-delivery.sqlite while archives follow
   config.data_dir() (tickets.py:155,267,322). A "copy" run of settle/send/personal writes the live ledger and can message
   the real chat. Fix: derive the ledger path from config / env override.
8. **A used game is locked for the day** (CONFIRMED, design). tickets.py:133-140 (within a plan) and 251-257 (across
   batches) key on (sport, game_id): a newer forecast version or a different market of that game cannot be used later.
   Real NBA games re-versioned >10 min after first publication in 15/48 winner and 28/48 spread/total game-markets since
   Nov 1. Fix: key on (sport, game_id, market); conflict only on opposite side/different line; disclose exposure.
9. **All legs must start within 2 h of the check** (CONFIRMED, design). eligible() tickets.py:59-64 + forecasts only exist
   inside near_tipoff.WINDOW=2h. Cross-window tickets (7 pm + 10 pm) are impossible; once $5 is reserved, later windows are
   analysed but cannot receive a stake. Nothing forces the $3 main early. This is the main structural limit on "strongest
   combinations across all sports"; changing it is a product decision.
10. **"Best supported combinations" is presence-only** (CONFIRMED). check accepts `combination_comparison`, notes and
    every reason set to "." or "x" (tickets.py:79-103). Code enforces structure, budget and coverage accounting only;
    ranking quality is AI judgment and unverified. Descriptive records are not validated future accuracy.
11. **Missing-forecast alert races publication** (data CONFIRMED, firing PLAUSIBLE). Due at T-110 (tickets.py:44-48) vs
    Nov 7 first canonical rows at T-104..T-108; per-sport/day key without game ids masks later real gaps; preseason excluded
    although coverage includes it.
12. **Bot token in plaintext in Codex history** (CONFIRMED, counts only): ~/.codex thread_history_1.sqlite and
    state_5.sqlite (mode 644), from the Nov 2 pairing. Not in repo/logs. Rotation is the user's decision.
13. **Personal slips** (CONFIRMED). CLI-only JSON import; no API route or screen. A pending slip cannot later receive its
    return (tickets.py:450-452 refuses changed content); a "correction" is a new unrelated row (double counts); bool stake
    accepted; odds/leg results ignored; no immutability triggers; no consumer reads personal_tickets.

### LOW (CONFIRMED unless marked)
- An errored sport section drops out of coverage counts (tickets.py:83-101); error kept in archive/alerts, legs refused.
- Games starting just after Toronto midnight: not in ranked_picks(today) but in the due query → false alert (rare).
- check() does not cap $1 count and accepts `True` as a stake (tickets.py:149 vs 238-241); send rejects before sending.
- deliver(photo=…) with non-card text raises IndexError after the claim → row stuck `sending` (telegram.py:242); relevant
  to fixing #1.
- Archive writes not atomic (tickets.py:158-161, 270-273); legacy scan parses every day-*.json incl. hand-written notes;
  two identical concurrent checks → FileExistsError (no double send).
- Shared-exposure line prints internal keys (`nba:1088`); check text ≠ sent text when a prior batch exists.
- Result notice terse, labels unescaped under HTML (tickets.py:428, PLAUSIBLE); score-only correction re-notifies same
  outcome; unresolved/unscorable legs stay pending without alert; missing TELEGRAM_CHAT_ID settles nothing silently;
  A→B→A grade revert partial (real corrections move result_available_at).
- RealProvider replays at a past as_of show finals not yet available (providers/real.py:846-878); live tickets unaffected.
- Ticket text rounds half-even (`:.1%`), cards half-up (0.7125 → 71.2 vs 71.3); no real stored ties.
- Test archive uses a production-format ticket ID; SKILL example path /tmp/ticket-snapshot.json fails on reuse.
- Game cards interleave with tickets (Nov 7 evening: 37 cards vs 2 tickets; 3–4 cards per game), each with its own pick.
- Mac sleep: ~40 sleep/dark-wake cycles/h overnight, 21 "connection unavailable" since Nov 7, no Codex catch-up.
- UI (pre-existing): preview API :8841 is stale (no /api/soccer routes → soccer 404); soccer header overflows at
  1201–1306 px; NFL game page overflows at 1024 px; "last game" link stores failed paths.

## Area verdicts
| # | Area | Verdict |
|---|---|---|
| 1 | Flow and ownership | FAIL — wrapper genuinely thin; retry/settle gaps; dev tree live |
| 2 | Selection and coverage | FAIL — per-forecast accounting works; game lock, 2 h window, presence-only comparison |
| 3 | Records and timestamps | PASS on the live path (grouping, own-result exclusion, IDs/lines/times unchanged); LOW issues |
| 4 | Telegram UI/UX | FAIL — no visual ticket; false alert sent live |
| 5 | Storage, retry, budget | FAIL — $5 cap held in 4-process races and crash points; `retry` strands budget |
| 6 | Settlement, personal slips | FAIL — grading correct at original lines; blocked by Telegram failure; personal CLI-only |
| 7 | Automation and operations | FAIL — dev tree live, restricted network, false alerts, sleep dependency |
| 8 | App UI/UX | PASS NBA/NHL/NFL at 1280/390 (iframes); soccer UNVERIFIED (stale API); no ticket UI exists |
| 9 | Tests and verification | FAIL — structure 2 fails; backend 2 failed / 1693 passed |

## Checks run (actual results)
- Focused: `OMP_NUM_THREADS=4 PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_tickets.py backend/tests/test_telegram.py backend/tests/test_near_tipoff.py backend/tests/test_freshness.py -q -p no:cacheprovider` — PASS 93 (also with network blocked + empty NBA_DATA_DIR).
- `python3 scripts/check_project.py` — FAIL. Initially 3 items; one (`.claude/scheduled_tasks.lock`) was created by this
  audit session's own timer and was removed by the lead; rerun FAIL on the 2 known batch1-final filenames only.
- `OMP_NUM_THREADS=4 backend/.venv/bin/python scripts/check_project.py --ready` — FAIL at structure (exit 1); suite not run by it.
- Its parts run individually: `npm run typecheck` PASS; `tsc --noEmit && vite build --outDir <scratch>` PASS;
  `npm run test:unit` PASS 3/3; backend `pytest backend/tests` (from a scratch cwd, empty NBA_DATA_DIR, network blocked)
  FAIL 2 failed / 1693 passed, 2405.7 s: test_coverage.py::test_the_two_hour_cycle_records_every_game_even_when_the_primary_step_fails
  (repeatable; passes with HEAD jobs.py) and test_research_agent.py::test_the_deadline_kills_the_agent_tree_and_the_trial_in_flight
  (passed alone; load flake, PLAUSIBLE).
- tests/test_coverage+tickets+near_tipoff+freshness+canonical: 1 failed / 107 passed (same coverage test).
- Lane harnesses (mocked Telegram): uniqueness, crash points, 4-process races, retry, settlement cases, personal slips,
  cross-midnight, early-coverage probe — outputs in lane dirs.
- Browser: :5198 at 1280 and 390 via same-origin iframes (top viewport fixed at 1200); console 0 errors.

## Live vs development
- Working tree (live effect): Telegram notifier (launchd, 60 s), ticket helper via Codex automation (hourly :10, ACTIVE),
  dev preview :5198/:8841 (preview data copy).
- Release ~/nba-predictor-live a55c874 (clean, not an ancestor of HEAD): forecast jobs, API :8100, web :4173.
  NFL tick: ~/.codex/worktrees/nfl-live-release 8539513 (guarded). jobs.py early coverage: dev-only.
- 10:12 ET first live v3 run: created v3 ledger tables, graded the 3 legacy Nov 7 tickets win (no notice), sent the false
  delivery alert, archived a 0/0 review (reviews/2026-11-08-cb41bb45….json). No batch or ticket send.

## Missing tests
retry resend + alert; settle when identity fails; delivery alert scope; early record creates one row per game/market (real
predict_now); check/send stake-cap parity incl. bool; ledger path isolation; ticket PNG + text delivery and caption limit;
deliver(photo) failure state; same-game other-market/new-version; errored sport; post-midnight game; real snapshot() on a
store (all ticket tests mock it); snapshot expiry/date rollover; hash mismatch; HTML escaping of ticket/result text;
personal pending→result and correction; `pytest.raises` without `match` in test_guards.

## Next bounded batch (one writer, failing-first tests, no live sends)
1. Retry: current time on retry, `retry` in alert, explicit CLI status (#2).
2. Settle first: grade before bot identity; per-message DeliveryError handling; printed cause (#4).
3. Delivery alert scoped to ticket/settlement keys, Toronto-day bounds, counted keys in dedup key (#5).
4. Early coverage: reason-only change must not create a version; restore test_coverage.py:278 by code, not assertion (#6).
5. Ledger path from config/env (#7).
6. Visual ticket: PNG after text under its own key, caption ≤1024, renderer numbering/IDs/times, deliver marks failed on
   any post-claim exception, code-level test mode (#1, LOW deliver).
Later, needing decisions: same-game rule per market (#8), cross-window stakes (#9), personal-slip revisions + read-only
API/UI (#13), running the sender/helper from a guarded release (#3), token rotation (#12), batch1-final filenames (owner).
