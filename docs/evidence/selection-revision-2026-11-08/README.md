# Selection revision: weigh price and reliability, judge whole tickets (for independent review; NOT deployed)

Author: Claude Opus 5.5, session 43, Nov 8 evening.

**Revision 4: f7 review fixes (f7 verdict "approve with fixes"; this version needs f7's re-check, then the owner's go).**

- **F1 (must fix):** the per-ticket lines cite leg numbers instead of repeating labels. With long two-team names, unpriced and without records, the message lengths are:

  | Menu | Live code | Fixed |
  |---|---|---|
  | 6 × 2 | 2,807 | 3,299 |
  | 5 × 3 | 2,965 | 3,375 |
  | 4 × 4 | 2,790 | 3,118 |

  Revision 3 refused all three as too long (`42-message-length-probe.txt`, `probe_len.py`). A parametrized test covers it.
- **F2:** soccer picks carry `assessment` in the snapshot, through `attach_assessments`, the same as NBA/NHL/NFL.
- **F3:** a price of 0, or one between -100 and +100, is treated as no price (no ZeroDivisionError).
- **F4:** the text reads "Sportsbook implies a higher chance than the model: legs …".
- **F5:** the missing-`payout_assessment` refusal says "review again", so it is retryable during the switch.
- **Tests:** `41-failing-first-f7-fixes.txt` shows 8 failing first, each for its intended reason (too long, missing soccer assessment, ZeroDivisionError).

| Exact version, revision 4 (sha256 prefix) | |
|---|---|
| `tickets.py.proposed` | `2a053998ea998b9b` |
| `test_tickets.py.proposed` | `0993990a295c18b3` |
| `SKILL.proposed.md` | `d7bd95bfb74eb96f` |
| `automation.proposed.toml` | `1fc034d8791f7d1c` (unchanged) |

- **Base:** release `96ce63e`, clean. The installed skill/automation are still `5219493b54110048` / `061d4716c731166b`.
- **Checks on a fresh `96ce63e` export with the diffs:**
  - focused suites (`test_delivery_release`, `test_soccer_live`, `test_intl_live`, `test_tickets`, `test_telegram`, `test_freshness`, `test_near_tipoff`, `test_nfl_live`): **199 passed** (`43-focused-on-96ce63e-v4.txt`);
  - full suite: `44-full-suite-on-96ce63e-v4.txt`.

**Revision 3 (superseded by revision 4 above).**
- The owner's clarification is in: no live record, untested markets, model lines and large sportsbook disagreements are weighed case by case and never excluded automatically. This is stated in `SKILL.proposed.md` and `automation.proposed.toml`, and guarded by `test_no_record_untested_markets_and_large_sportsbook_gaps_are_never_automatic_exclusions`.
- The demo wording is corrected. Its judgments are "more conservative judgment, not a proven better strategy", and a sportsbook disagreement "warrants investigation", not an assumed cause.

| Exact version (sha256 prefix) | |
|---|---|
| `tickets.py.proposed` | `20b7f37f2e6e78d0` |
| `test_tickets.py.proposed` | `85e8da51f332a00f` |
| `SKILL.proposed.md` | `0e3a26bf8371ca1d` |
| `automation.proposed.toml` | `1fc034d8791f7d1c` |

- **Base:** release HEAD `96ce63e`. Since `3a67089` it changed only `scripts/soccer_live_cycle.sh`; `tickets.py` (`9182a677820f095f`) and `test_tickets.py` (`a07f033c8907f645`) are identical.
- **Installed copies unchanged:** the skill (`5219493b54110048`) and automation (`061d4716c731166b`) still equal `*.current.*`.

**Revision 2 (after review):**
- The ticket-level past-picks range and its below/above/straddles verdict are removed from the assessment and from Telegram. Each leg's own record stays visible in `leg_records` and the per-pick `assessment`.
- Added `demo/`: the proposed process applied to 7 frozen pregame snapshots. Start with `demo/DEMO.md`.

- **Requested by:** the owner, forwarding the reviewer's instructions after the selection audit.
- **Status:** IMPLEMENTED in an isolated copy and TESTED with mocks. NOT reviewed, NOT deployed, NOT active.

| | |
|---|---|
| Base | release `~/nba-predictor-delivery` @ `3a67089`, `backend/app/tickets.py` sha256 prefix `9182a677820f095f` (the live helper) |
| Proposed | `tickets.py.proposed` sha256 prefix `20b7f37f2e6e78d0`; `tickets.py.diff` applies cleanly to the base and gives the same hash |
| Tests | `test_tickets.py.diff` / `test_tickets.py.proposed` |
| Skill / automation text | `SKILL.diff` / `automation.diff` against the installed copies (`*.current.*`) |

## The problem

From the audit (`31-selection-audit.txt`, `32-lock-audit.txt`; scripts included):

- All 15 sent legs today were the highest-probability market in their game.
- The app's own `game_rank` sorts by the lower bound of past win rates of similar picks, i.e. by hit rate.
- The price was never weighed. 12 of 15 legs were quoted at a price whose break-even probability was above the app's own estimate. This is a warning, not proof they would lose.
- The earlier-ticket game lock then narrowed later batches (59 eligible forecasts were blocked).

## What changes (helper)

1. **`leg_assessment(sport, pick)`**
   - Attached to every snapshot pick as `pick['assessment']`, and to every validated leg.
   - Puts side by side:
     - the app probability;
     - the stored price, its decimal and `break_even` (vig included);
     - `model_gap` (app probability minus break-even);
     - the record of similar picks (win rate, sample size, 95% Wilson interval via the app's own `threshold_overview.wilson`, history vs season);
     - `record_minus_break_even` (that interval minus break-even; **not** an accuracy measure);
     - the exact-percentage record, `app_rank`, `skill_proven`, line source (bookmaker/model/none);
     - the app's own limitation note for the market.
   - Facts only: no verdict and no new threshold.
2. **`ticket_assessment(legs)`**
   - Payout at stored prices (decimal prices multiplied, as a parlay of distinct games pays), break-even, model estimate (independence assumption) and `model_minus_break_even`.
   - `leg_records`: each leg's own record.
   - Lists: legs priced above the model, unpriced legs, legs without a record.
   - No ticket-level historical range: multiplying past intervals does not give today's ticket a win-probability interval.
3. **`assess --plan plan.json`**: read-only comparison of candidate tickets against the plan's snapshot, with no fresh snapshot, archive, ledger write or send. The reviewer uses it to compare a ticket with its alternatives.
4. **`check()` requires `payout_assessment` on every ticket.** This is a sentence of judgment: why this payout justifies this uncertainty. Zero tickets stays valid.
5. **Ticket text**
   - The menu line adds each option's payout.
   - Each ticket shows "Pays about ×X at stored prices; break-even B%; model estimate M%", "Priced above the model: …" and "No record of similar picks for …".
   - Unpriced tickets say "Payout unknown: no stored price for …".
   - The text uses no "value" or "edge" wording.

The skill and automation text replace the selection bullet with: weigh probability, app recommendation, relevant record, evidence quality and exact price together; read the fields as facts; don't take the highest percentage by default; don't add a heavily priced favorite to keep the hit percentage high; judge whole tickets with `assess`; give every ticket a `payout_assessment`; no variety, size or positive-gap requirement; zero tickets when support is weak. The structural ticket rules are kept word for word.

## Demonstration on frozen pregame snapshots (`demo/DEMO.md`)

- **Inputs:** today's 7 sent-batch snapshots, with quotes stored by each `as_of` and the rules in force then. No results were read.
- **Files:**
  - `facts.txt`: every usable forecast as the revised helper shows it;
  - `assess-*.json`: the new `assess` action run on the original tickets, my selections and the closest rejected candidates.
- **My selections:**
  - snapshot 4: TOR +1.5 + CGY +1.5 (model 52.8%, ×2.08, break-even 48.0%);
  - snapshot 5: MIN win + TOR +1.5 (63.6%, ×1.77, 56.6%), with TOR's third-ticket exposure flagged.
- **No ticket** in the other 5 snapshots.
- **Rejected:** favorites priced above both the model and their record (PHI -449, BOS -790, DET -264, DAL -305, KC -520), model-only NFL legs, moved lines, and a suspicious bargain (LAC +103 against a 15-point sportsbook disagreement) paired with an overpriced leg.
- **No market was forced:** the evidence still favored moneylines and puck lines.
- These are the author's own judgments, from one day of data. They demonstrate how the process applies; they don't validate it.

`before_after.txt` and `sample_text_282.txt` show what the revised helper displays for the original tickets.

## Tests

- **Failing first:** `33-failing-first-selection.txt` (the new tests on the base). `35-failing-first-no-range.txt` covers the revision-2 tests before the range was removed.
- **Final**, with the diff applied to a fresh full export of release `96ce63e` (isolated data, release venv):
  - `38-final-on-96ce63e.txt`: the focused suites, 190 passed.
  - `39-full-suite-on-96ce63e.txt`: the full suite, 1,946 passed / 8 failed in 19m38s. All 8 are in `tests/test_deep_study.py`.
  - `40-deep-study-baseline-on-clean-96ce63e.txt`: the same 8 fail on the unmodified release, because torch/keras are not installed (research-only dependencies).
- **Not run:** `--ready`, the browser, a real scheduled run. On an isolated copy (git archive `3a67089` of `backend`, `scripts` and `docs`), with an empty `NBA_DATA_DIR`, mocked Telegram and the release venv:
  `pytest -q -p no:cacheprovider tests/test_delivery_release.py tests/test_soccer_live.py tests/test_tickets.py tests/test_telegram.py tests/test_freshness.py tests/test_near_tipoff.py tests/test_nfl_live.py`
  gave **189 passed**.
- **Changed existing tests:** the `ticket()` fixture helper and two inline plans gain `payout_assessment`, the new requirement.

## Unresolved limitations

- **The selection stays the reviewer's judgment.** The helper only supplies facts and refuses a ticket without a `payout_assessment`. It cannot tell whether that sentence is sound.
- **Prices**
  - Prices are the latest stored DraftKings "current" quotes at snapshot time, not executable prices.
  - The break-even includes vig. The parlay payout is the product of the legs' decimal prices; a sportsbook's actual parlay price can differ.
  - Team totals (model lines), the 60-minute result and soccer have no stored price, so their payout is unknown.
- **Estimates and records**
  - The model estimate assumes the games are independent.
  - Groups of similar past picks describe the past, not today's probability.
  - Season-only samples are small, and the NFL has no record at all.
- **Not changed**
  - The app's `game_rank` (hit-rate ranking, app code outside the helper).
  - The 6-leg cap and the identical-forecast game lock.
  - The PNG card, which still shows only "Model estimate".
- **Deployment ordering:** `payout_assessment` is a new plan requirement, so the skill/automation text and the code must switch together, after the heartbeat thread logs `task_complete`. Otherwise live reviewer plans would be refused.
- **Overlap with the separate 7-leg / newer-forecast package:** that package (scratch `rel/`, unreviewed) touches the same file and needs sequencing.
- **One day of data.** Nothing here shows that the revised judgment wins more tickets or more money.
