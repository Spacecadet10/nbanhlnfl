# Milestone 3 review — Codex, 2026-09-30

Decision: one final-test reporting/selection blocker remains. Do not run the real reserved test yet.

## P1 — Test evaluation reselects the candidate using test outcomes
Location: backend/app/backtest.py, evaluate(), report field candidate_selection.
The function unconditionally calls select_candidate(preds) for validation AND test. During test evaluation these
predictions include test outcomes, and select_candidate may report logistic or a calibrated model as selected.
This contradicts the frozen validation choice of Elo and makes the final report a new model-selection step.

Reproduction: on the synthetic temporary database from tests/test_backtest.py, replace select_candidate with a
function that raises, then call evaluate(..., period='test', elo_cfg=FROZEN). It raises from candidate selection.
No real reserved rows were loaded and no production access log or ledger was written during this reproduction.

Required fix:
- Run candidate selection only during validation.
- Supply test evaluation with a saved frozen validation decision/configuration, checked before loading test rows.
- Record primary_model='elo' and its frozen configuration/provenance in the final report.
- Optional six-model test comparisons are descriptive only: no eligible/selected winner determined by test outcomes.
- Reject missing/mismatched frozen decisions before accessing the reserved data; log authorized test access before loading.
- Add a synthetic regression where a non-primary candidate has better test scores, yet the primary remains Elo,
  and a regression proving test evaluation never calls the validation selector.
- Keep all existing ledger rows, validation selection and model parameters unchanged. No retuning.

## Verified scope
- Read model implementations, backtester, CLI gates, metrics, candidate tests and evaluation amendments.
- Readiness passes: frontend typecheck/build and 65 backend tests; one pre-existing TestClient warning.
- Validation filtering occurs in SQL; predictors receive outcome-free PregameGame objects.
- Labels update model state only when available by cutoff; logistic training retains pregame feature snapshots.
- Logistic scaling/fits use earlier seasons; calibration uses stored earlier walk-forward probabilities.
- Elo retained on validation under the written rule; no evidence from these scores of a betting advantage.

## Interpretation and known limits
- Validation was used to tune Elo and compare candidates: its scores/intervals are exploratory, not independent
  confirmation. Individual candidate intervals do not correct for multiple comparisons. No candidate was promoted.
- “Calibration did not improve validation performance” is supported; “Elo is already calibrated” is stronger than
  these results establish. Earlier walk-forward probabilities use validation-selected Elo settings, so they are
  out-of-sample for per-game fitting but not independent of all hyperparameter selection.
- Historical timestamps and retrospective schedule/rest assumptions remain documented approximations.
- A missing access log is not proof of no reads, particularly since pre-fix validation processed reserved rows.
  Say “no scored final-test evaluation reported,” not “test data never accessed.”
- Custom logistic solver has limited numerical/convergence safeguards; not a blocker for retaining Elo as primary,
  but independently validate it before promoting logistic to live use.

Next: Claude fixes the frozen-decision path and tests only; Codex checks that change, then the frozen final evaluation
can proceed. Real final-test evaluation was not run by this review.


## Frozen-decision re-review — 2026-09-30
Accepted: evaluate splits validation/test paths; test rejects a caller-supplied Elo config, checks the frozen decision
and validation fingerprint before reading reserved rows, logs access before loading test rows, and reports the frozen
Elo as primary. Test comparisons are descriptive; neither selector nor tuner runs in the test path.
Independently ran readiness: frontend typecheck/build and 75 backend tests passed (one existing TestClient warning).
Reviewed regressions for invalid decisions, ordering, forbidden selector/tuner calls, and an oracle competitor that
beats Elo without changing the primary. No remaining blocker found for the planned frozen final evaluation.
The digest detects accidental edits; it is not an authorization or tamper-proof boundary. One-time evaluation remains
an operating rule, not an enforced single-use lock. This review did not run the real final test.
Next owner: Claude may run the documented final test once, report primary Elo versus home-rate/constant plus uncertainty,
retain all results without retuning/reselecting, and update the handoff. M4 app integration follows separately.
