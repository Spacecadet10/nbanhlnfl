# NCAAB colour-gated Telegram selection — policy ncaab-colour-gate-v1 (prospective)

Written before any card was sent under this policy and before any outcome under it was read. Code: backend/app/ncaab_gate.py
(POLICY, VALIDATION_BLOCKED, MAX_PRICE_AGE); colour decision: backend/app/card_threshold.py (DECIDE_VERSION
card-colour-decide-v2, fitting rule card-threshold-v1). A separate policy from ncaab-top4-policy-eval-2026-11-18.

## Start
Counting starts at the first Telegram run on the released code (the delivery commit and time are recorded in HANDOFF.md).
Nothing before that moment counts: not recovered Telegram history, not the top-4 period, not backtests.

## Rule (every condition must hold for a pick to be sent)
1. Market winner, spread or total, and not in VALIDATION_BLOCKED. At v1, spread and total are BLOCKED: on Nov 17-21 settled
   games they scored Brier 0.2602 / 0.2595 and log loss 0.7154 / 0.7153 (n=218), worse than a coin flip and than DraftKings
   no-vig (0.2500 / 0.2508). Only winner can qualify.
2. Green under the card's own displayed decision (card_threshold.decide: qualifies only with a set / seed / capped line, or a
   line held for too few new picks; never with a stale, missing, failed or unqualified line).
3. Valid pregame forecast; the card's forecast-time sportsbook quote exists (same game, market, line; at or before the forecast,
   before tip-off); the newest quote for the same side from the same provider is on the same line, before tip-off, and at most
   60 minutes old at the run.
4. Expected value at that offered price > 0: p_win x (decimal - 1) - p_loss, pushes refunded (p_mid from the stored forecast).

## Ranking and exclusions
- Recommended pick of a card = the qualifying pick with the highest expected value. (The other sports' context ranking,
  tickets.card_choice, orders warning-free supported picks by the SPORTSBOOK probability, i.e. the book's biggest favourite;
  NCAAB gated cards do not use that order.)
- Other markets on a sent card are printed as "Context only · not a recommendation: <reason>".
- No quota: zero qualifying picks = zero cards. A game with an earlier sent card stays in the run so a lean that stops
  qualifying becomes the existing single "Pick withdrawn" update. Deduplication, uncertain-delivery handling and the
  published-picks ledger are unchanged.
- Every evaluated game's verdicts (each market, colour state, price, EV, reasons) are saved append-only in
  ncaab_gate_decisions in the delivery ledger. All NCAAB forecasts keep being recorded and graded, sent or not.

## Evaluation (no claim before these are computed on the counted sample)
- Report separately: sent picks; all qualifying-but-unsent (none expected); all evaluated picks (coverage = sent / evaluated).
- Per group: n, calibration (reliability by decile), Brier and log loss vs DraftKings no-vig on the same games, hit rate, and
  return at the executable price recorded in the verdict (1 unit flat, pushes 0). Never a synthetic or no-vig price as a return.
- Keep live forecasts, recovered Telegram history, backtests and each model version apart; never relabel a forecast's model.
- Known data issue to exclude and report: neutral-site games whose home/away ESPN flipped after the final (5 games through
  Nov 21: 401916125, 401917538, 401918959, 401919826, 401927396) are graded against flipped scores until ingest is fixed.

## Changes
Any change to a threshold rule, block, price limit, EV rule or ranking after this file is committed is a new policy version
(ncaab-colour-gate-v2, ...) with its own start time. Lifting the spread/total block needs new chronological out-of-sample
evidence recorded before the change.
