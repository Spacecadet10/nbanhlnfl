# Reading plan for the one-shot soccer reserved test (written BEFORE the run)

- Written: 2026-11-08, by the integration writer (session f7). The run has not started, and no reserved-season result,
  outcome share or metric has been computed or seen by me. The access log
  `~/nba-predictor-research-data/soccer-eval-logs/soccer_test_access.jsonl` does not exist at writing time.
- Why this file exists: the user's instruction (2026-11-08):
  > Before opening results, verify that interpretation/acceptance rules are documented. If they are missing, resolve
  > that specific gap first; never invent passing criteria afterward.
- What I checked first:
  - The protocol (`backend/app/soccer_protocol.json` v2, sha256 `f3794639…`; `PROTOCOL.md`) defines model choice and
    calibration on validation. It defines **no** reading or acceptance rule for the test.
  - The soccer report's open decision M1 (§11.1) says the same, with option (a): "accept as is and write a dated
    reading plan before the run".
  - This plan takes option (a). The runner is used unchanged, so there is no re-seal, and the sealed candidate stays
    `7dbc668c214f33862541156c0630913194e8a3421c0e7c3495f4de296ecd9c47`.

## What the run produces (fixed code, `soccer_eval.run_test`)
`test_report.json` holds the frozen Elo's metrics for test seasons 2023-24, 2024-25 and 2025-26:
- `overall`: n, log loss, its 95 % week-block CI, Brier, accuracy, mean predicted and observed home/draw/away shares,
  ECE per outcome;
- `by_competition`: the same, including the CI;
- `by_season`: the same, without the CI.

It holds no per-match rows and no paired comparison.

## Baseline (fixed before any test data, not refitted)
The frequency baseline uses each competition's home/draw/away shares from 2010–2019. UEL uses UCL's shares. Source:
`development_validation_report.json` (sha256 `1dd1717f…`), key `frequency.frequencies`. Its test log loss per
competition is exact from the report, because the forecast is constant within a competition:
`LL_freq(c) = −Σ_k observed_k(c) · ln f_k(c)`. The pooled value is `Σ_c n_c · LL_freq(c) / Σ_c n_c`. A per-season
baseline is **not** computable (seasons mix competitions), so no rule uses one.

## Acceptance rules (all decided now)
- **R1. Skill over the baseline, pooled.** PASS only if
  - Elo `overall.log_loss` < pooled `LL_freq`, **and**
  - the upper end of Elo's `overall.log_loss_ci95` < pooled `LL_freq`.

  The paired interval of the protocol's choice rule is not produced by the frozen runner, so R1 uses this stricter
  substitute: the whole interval of Elo's own test log loss must lie below the fixed baseline's value.
- **R2. Calibration gross-failure guard.** PASS only if each outcome's overall ECE is ≤ 0.05. (Validation, known
  before this plan: home 0.034, draw 0.0057, away 0.031.)
- **R3. Launch scope per competition.** A competition may be served live only if R1 and R2 pass overall **and**, for
  that competition:
  - Elo `log_loss` < `LL_freq(c)`, **and**
  - the upper end of its `log_loss_ci95` < `LL_freq(c)`, **and**
  - n ≥ 300.

  Competitions that fail are reported and not launched.
- **R4. Descriptive only, never a gate:**
  - per-season log loss, Brier and accuracy;
  - predicted vs observed shares;
  - reliability.

  An overall home over-prediction larger than 0.03 is disclosed as a limitation.

**Verdict wording.**
- If R1 and R2 pass, the reserved test supports serving the frozen uncalibrated Elo's 90-minute 1X2 probabilities for
  the R3 competitions, as model quality only.
- If either fails, the evidence does not support launch, and the result stands.

## Fixed in advance regardless of outcome
- The result is recorded unchanged and copied into `docs/evidence/` in full. No refit, tuning, recalibration, re-seal
  or second run follows from it. The runner refuses a second run.
- Passing does not establish a betting edge. Log loss, Brier and accuracy are not compared with bookmaker prices: no
  free, permitted odds source exists (S1, G5).
- Live serving additionally requires inputs equivalent to the evaluated point in time. Cutoff is 06:00 UTC on match
  day; only results available by then are used, never same-day results. Any live result source must be free and
  clearly permitted. These are pipeline conditions, not test criteria.
- Deciding UEFA knockout matches stay ungraded, and other markets (totals, BTTS, double chance, qualification) stay
  unsupported.

## Amendment 1, before the run (2026-11-08, after an independent read-only review; still no result exists)
The review (APPROVE WITH FIXES) found no contradiction with the protocol. These fixes make the wording exact; none
changes how hard a rule is to pass.

- **F1. Exact field paths.**
  - R1: `test.overall.log_loss < B` and `test.overall.log_loss_ci95[1] < B`, where B is the pooled baseline.
  - R2: `test.overall.ece[k] <= 0.05` for k = 0, 1, 2 (home, draw, away).
  - R3: `test.by_competition[c].n`, `.log_loss` and `.log_loss_ci95[1]`.
  - R4 disclosure: `test.overall.mean_predicted[0] - test.overall.observed[0] > 0.03`.
- **F2. Arithmetic.**
  - `LL_freq(c)` uses `test.by_competition[c].observed` and `frequency.frequencies[c]` as stored, in float64 with
    `math.log`.
  - The pooled weights are `n_c = test.by_competition[c].n`.
  - All comparisons use unrounded floats. A tie fails `<` and passes `<=`. Rounding is for display only.
- **F3. Candidate competitions** are the 7 protocol codes. A competition missing from `by_competition` counts as n = 0
  and is not launched. UEL's test covers 2023-24 and 2024-25 only, and it uses UCL's frequencies.
- **F4.** If R1 and R2 pass but no competition passes R3, nothing is launched.
- **F5. Consistency check.** If `sum_c n_c != test.overall.n`, stop and report. The result is not interpreted.
- **F6. R1 wording correction.** R1 is "this unpaired substitute (Elo's own log-loss interval against the baseline's
  test value), not the protocol's paired interval". It is not claimed to be stricter.
- **F7.** The protocol's choice-rule requirement of a lower log loss in at least 2 of 3 seasons is **not applied**,
  because a per-season baseline cannot be computed. This is disclosed as a difference from the choice rule.
