# Proposed selection process on 7 frozen pregame snapshots (demonstration, nothing sent)

Author: Claude Opus 5.5 (session 43), Nov 8 evening. These are my judgments as the author of the process, not an independent validation of it. They are more conservative judgment, not a proven better strategy. None of them is a rule: the cautions below (no live record, untested markets, sportsbook disagreement) are weighed case by case and must not become automatic exclusions. A guard test (`test_no_record_untested_markets_and_large_sportsbook_gaps_are_never_automatic_exclusions`) keeps the helper from excluding such picks.

**Inputs.** The snapshot saved with each of today's 7 sent batches, with DraftKings quotes stored by that snapshot's `as_of`. The rules in force then apply: stale sources blocked, earlier-ticket game locks (identical forecast only), moved lines excluded.

**Not used.** No game results were read: no grades, no finals, no ticket_grades. Nothing here was tuned to an outcome.

| File | What it holds |
|---|---|
| `facts.txt` | Every usable forecast per snapshot, as the revised helper shows it |
| `assess-*.json`, `assess-summary.txt` | The new read-only `assess` command run on each frozen snapshot, for the original tickets, my selections and the closest rejected candidates |
| `snapshot-*.json`, `plan-*.json` | The exact inputs |

## How I judged (the proposed process; no new numeric thresholds)

1. **Evidence behind the probability.** A leg needs more than the model's own number: a record of similar picks whose 95% interval is consistent with the app's probability. Weaker evidence:
   - markets the app itself labels untested or no-skill (NBA spreads, totals and team totals: "development research only"; NHL totals: "no skill"; team totals: "not tested");
   - model lines;
   - every NFL market, which has no settled record yet.
2. **Price against both.** Compare the price's break-even probability with the app's probability and with that leg's record interval.
   - Above both the app and the top of the interval: expensive by every available measure, so reject.
   - Inside the interval: fairly priced but uncertain.
   - Below both: favorable, which still isn't proof.
3. **Large disagreement with the sportsbook warrants investigation.** The app says it uses no injury or lineup information (NBA) and no goalies (NHL), so a very large gap may reflect information the app lacks. I have not established how often that is the cause. In this demo I treated such gaps as unresolved uncertainty, case by case, not as a cut-off.
4. **The whole ticket.** Each added leg multiplies the payout by its decimal price and the risk by its probability. A leg is worth adding only when its own evidence holds up at its price. I look at the payout, the break-even and the model estimate. No ticket-level historical range is used.
5. **No ticket** when no pair of distinct-game legs passes 1–3. I never add a market for variety or a leg for size.

## Decisions

### Snapshot 1 (16:14Z: NFL 1 PM slate, NHL FLA @ BOS). **No ticket.**
- **NFL:** every leg is model-only, with no NFL record on the model's first live day. That is an uncertainty, not a ban; here I judged no NFL pair strong enough.
  - The line markets claim 10–14 point disagreements with sportsbook lines: PHI +3 66% vs a 52% break-even, IND +4.5 62.5% vs 51%, KC-game Under 47.5 64.5% vs 52%. For a brand-new model, gaps that size are unexplained, and I did not rely on them (rules 1 and 3).
  - **Original sent tickets, both rejected:**
    - KC win (78% at -520, needs 84%) and DET win (68% at -305, needs 75%) are both priced above the model.
    - KC + DET: model 53.3% vs break-even 63.2%.
    - KC + DEN: 52.0% vs 49.2%, with no record on either leg.
  - DEN win (66% at -142, needs 59%) is the only NFL moneyline priced below the model, but it's model-only too. One leg can't make a ticket, and pairing it with another model-only leg compounds unverified risk.
- **NHL:**
  - BOS win: 56.6% at -142 needs 59%, which is at the top of its record (56–59% over 3,525 picks), so it's priced at or above both.
  - FLA +1.5: needs 69%; its record is 48% of 42, which contradicts the app.

### Snapshot 2 (20:07Z: PHI @ CHI, VGK @ BUF; NFL inactive then). **No ticket.**
- **PHI win:** 67.8% at -449 needs 82%, far above its record (65–71% over 999 picks). Expensive by every measure, and the sportsbook disagrees by 14 points (rule 3). Rejected. It was in the original ticket, PHI + BUF: 48.5% vs a 59.7% break-even.
- **Under 237.5:** the line had moved since the forecast. The revised helper excludes it, so the earlier audit's "positive on price and record" example for this game falls away.
- **CHI +9.5:** record 41% of 59 contradicts the app. **Team totals:** around 50% and unpriced.
- **BUF +1.5:** 71.6% at -270 needs 73%, inside its record (66–86% over 66 picks). A fair leg, but the only one: the other game has no acceptable leg.

### Snapshot 3 (21:55Z: LAL @ DET, TBL @ WPG). **No ticket.**
- **DET win:** 63.9% at -264 needs 72.5%, above its record (59–64% over 1,100 picks). **TBL win:** needs 60%, above its record (56–59%). Both expensive.
- **Original DET + WPG:** 42.2% vs a 48.2% break-even. Rejected.
- **Closest candidate: WPG +1.5 + Over 216.5.** Model 39.0%, pays ×2.81, break-even 35.5%. WPG +1.5 is fair (needs 66.4%, record 59–83%). The Over sits inside its record (46–66% over 87), but the app labels NBA totals development-only with no independent test, so that leg's support is weak. I'd leave it out.
- **LAL +7.5:** record 42% of 38 contradicts the app.

### Snapshot 4 (22:28Z: NHL 7 PM games; NBA source stale, blocked; WPG locked to its sent forecast). **Ticket: TOR +1.5 + CGY +1.5.**
- **TOR +1.5:** 73.1% at -192 needs 65.8%, at the bottom of its record (66–86% over 66), so favorable on both. Caution: the app is 7 points above the price's break-even and ignores goalies.
- **CGY +1.5:** 72.1% at -270 needs 73%, inside its record, so fair.
- **Ticket:** model 52.8%, pays ×2.08, break-even 48.0%. This matches the original pair: when the evidence supports moneylines and puck lines, the process keeps them.
- **Rejected:**
  - **The original 3-leg ticket adding WPG +1.5:** 34.9% vs 31.9%. WPG is priced at the model (-0.4) and is already in an earlier ticket, so it adds exposure without an evidence-backed advantage.
  - **TOR win (+136) instead of TOR +1.5:** 36.3% vs 30.9%. Similar support, but TOR's moneyline record is the generic ~51% coin-flip group and the gap to the sportsbook is 8 points. A batch can't use two different forecasts for the same game anyway.
- **NHL totals and team totals:** no-skill or untested, so not used.

### Snapshot 5 (23:28Z: MIN, DAL; NFL CHI; NHL PIT; NBA delayed but usable; TOR and CGY locked to their sent forecasts). **Ticket: MIN win + TOR +1.5, with exposure flagged.**
- **MIN win:** 86.9% at -620 needs 86.1%, inside its record (83–92% over 186), so fair.
- **TOR +1.5** (the identical forecast 1097 already sent): favorable, as in snapshot 4.
- **Ticket:** model 63.6%, pays ×1.77, break-even 56.6%, no leg priced above the model.
- **Exposure:** TOR would sit in a third ticket, so one TOR loss would sink three. The ticket text discloses this. A reviewer could reasonably decline for that reason alone. I'd send it because it's the only pair with both legs supported and neither priced above the model.
- **Rejected:**
  - **Original CHI + PIT:** 48.1% vs 51.8%. CHI is model-only and priced above the model; PIT +1.5 needs 70.4% against a 67.1% model.
  - **Original 4-leg:** 25.4% vs 24.8%, with CHI unrecorded and three legs priced above the model.
  - **MIN + CGY:** 62.7% vs 62.8%. **MIN + PIT:** 58.3% vs 60.6%.
  - **DAL win:** needs 75.3%, above its record (67–74%).
  - **TB +6 and Under 47.5:** NFL model-only with large gaps (rule 3).
  - **Over 221.5 and MIN -12.5:** lines moved, so excluded.

### Snapshot 6 (23:57Z: MIN, BOS, DAL; NHL games locked to forecasts that are no longer current; CHI locked). **No ticket.**
- **MIN win:** needs 87.5%, inside its record (83–92%). A fair leg, but the only supported one.
- **BOS win:** 77.8% at -790 needs 88.8%, far above its record (76–83% over 607). The clearest "expensive favorite" case.
- **DAL win:** needs 75.3%, above its record. **CHI:** model-only and priced above the model.
- **Over 222.5:** same game as MIN, and its 33-pick record is weak.
- **Original tickets rejected:** MIN + BOS (67.6% vs a 77.7% break-even) and MIN + BOS + DAL (47.8% vs 58.5%). These raised the displayed hit percentage while the prices asked for more than the model and the records.

### Snapshot 7 (00:12Z: LAC, MIA @ SAC; BOS and DAL locked to their sent forecasts; CHI locked). **No ticket.**
- **LAC win:** 64.3% at +103 needs 49.3%, below its record (59–64% over 1,100), so favorable on paper. But the sportsbook disagrees by about 15 points on an NBA moneyline, and the app uses no injury or lineup information (rule 3).
- **The record doesn't cover this.** It counts past picks at this probability level, not past picks where the market disagreed this much.
- **The only other usable legs are worse:**
  - DAL needs 75.3%, above its record;
  - BOS needs 88.8%, far above its record;
  - MIA win needs 84% against a 62% model, a 22-point disagreement;
  - SAC +11.5's record of 47% of 30 contradicts the app;
  - CHI is model-only.
- **Original LAC + DAL:** 45.4% vs a 37.1% break-even. It rests on a suspicious bargain plus an overpriced leg. I'd leave it out and flag LAC for a news check.

## Summary

| | Sent today | My demonstration |
|---|---|---|
| Tickets | 11 in 7 batches | 2 in 2 snapshots (TOR+1.5 + CGY+1.5; MIN win + TOR+1.5) |
| No-ticket snapshots | 0 | 5 |
| Markets used | moneylines and +1.5 puck lines | moneylines and +1.5 puck lines (the evidence favored them; no market was forced) |

**Main difference.** The demonstration rejects favorites priced above both the model and their record: PHI -449, BOS -790, DET -264, DAL -305, KC -520. It also rejects model-only NFL legs, and won't pair a suspicious bargain with an overpriced leg.

**Limits**
- I designed this process, so these are not independent decisions.
- Seven snapshots from one day are too few to show the selections are better. No results were used, so they show nothing about winning.
- Rules 1 and 3 are judgment, and a reviewer could weigh them differently. They are not exclusion rules.
- Sending fewer tickets is the result of the evidence, not a goal. On a slate with well-recorded legs at fair prices, the same process would send more.
