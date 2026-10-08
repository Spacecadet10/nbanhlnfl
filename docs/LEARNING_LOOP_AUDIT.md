# Learning loop audit — 2026-10-05

Read-only inspection of the working code and local NBA/NHL stores at approximately 22:20 UTC. The running service's code revision was not independently verified. This distinguishes code policy from actual ledger evidence; no fits, forecasts, or deployment were changed.

## Finding

The app does learn from results, but on different schedules. The earlier conversational claim that the feedback loop was simply missing was too broad. Online state updates and weekly research selection already exist. Automatic replacement of the primary model is not implemented by those research selectors, and prospective improvement is not demonstrated.

| Component | What changes | Current evidence |
|---|---|---|
| NHL Elo and Poisson goals | Team state updates from eligible delivered results, replayed for each forecast; Poisson needs known regulation scores | Latest successful cycle began 2026-10-05 21:46:53 UTC: 14,547 primary results used, latest availability 04:24:32 UTC. This includes 39 current-season regular-season results beyond the 14,508 season-start set. |
| Learned NHL core models | Features can evolve during the season; fitted coefficients follow the season-start schedule | Registered logistic, logistic-xG, GBT, goals-draw and goals-GLM fits use 14,508 games, results through June 15. The 39 newer results have not entered their coefficient fits. This is the declared policy, not a failed daily training job. |
| NHL candidate models, including PyTorch/TensorFlow | Season-start fitted weights; predictions consume the shared feature pipeline | All 11 registered candidate fits use 14,508 games through June 15. Both deep models first used their registered fit October 4. Latest pool cycle succeeded, recording 69 market forecasts without reported errors. |
| NBA online and weekly models | Online ratings plus separately named weekly ridge/GBT research variants | Code provides the update paths; the store has no registered live fits or settled research monitoring series yet. No live learning effectiveness can be established. |
| Calibrated shadows | Explicitly prepared, frozen earlier-season corrections | Neither local store contains calibrated candidate forecasts. Completed code and copy-based verification do not establish live activation. |
| Weekly selectors | Reconsider registered source/filter/threshold rules using available settled evidence | October 5 decisions exist. Every NHL market reports insufficient evidence: 18 settled cohorts at decision time; NBA has zero. Required minima are 100 picks, 10% coverage and three weeks, plus statistical gates. |
| Deterioration monitoring | Reports rolling log-loss changes; never changes models | NHL series have 23 settled forecasts each, below the 200 needed for the two complete 100-game windows. No alert is not proof of good performance. |

## Where adaptation stops

1. Most learned NHL coefficients and neural-network weights do not update within the season. More results can change their input features without changing their fitted weights.
2. Weekly research selection is running but correctly abstaining for insufficient evidence. It does not replace the primary forecast model. Candidate admission is constrained by a sealed registry and held-out study evidence, not automatic promotion of whichever recent record looks best.
3. Frozen calibrated shadows require preparation and activation; no live rows currently demonstrate either their execution or benefit.
4. The monitor's online `last_update_input` is the newest final result in the store, not proof that a particular model consumed it; it does not filter preseason in that display query. Prefer actual forecast provenance for consumption evidence. NBA's displayed recent timestamp must not be called a verified NBA model update.

## Recommended next task

Expose per-model learning evidence: last consumed eligible result, last actual fit, scheduled next fit, available results not yet used for fitting, and whether the model influences picks. Keep feature updates distinct from weight refits and from model promotion. Then use the existing cadence-study machinery to evaluate a separately versioned within-season NHL refit schedule against the current season-start baseline, chronologically and on identical games. Do not silently change the live schedule or lower selector gates to manufacture picks.

Relevant code: `near_tipoff.predict_now`, `models/hockey.py`, `models/logistic.py`, `retrain_cadence.Cadenced`, `candidates/engine.training_rows`, `training_monitor.status`, `jobs.selector_decisions`, `selector.py`, and `candidates/selection.py`.


## Both-sport reevaluation following user clarification

The requested product is an adaptive prediction system for both NBA and NHL: models train on available historical games, update using newly available outcomes, and a learned combination adjusts contributions before comparison with available odds. A reporting correction alone does not implement that product.

Concrete restrictions in the existing implementation:
- `research_live._train_seasons` selects only completed seasons (last two); `_winner_stack` and `_line_stacks` fit combinations from those seasons. The combination already learns weights, but current-season outcomes do not feed its training set. This applies to both sports.
- `candidates.live.ensure_fit` filters training rows to `r.season < season`; this also applies to the deep networks. Their architecture is not the reason for frozen within-season weights.
- `research_live.CORE`, `LINE_MEMBERS`, and the candidate registry bound which members enter combinations. Recording all candidate forecasts is not an all-model adaptive ensemble. NBA weekly ridge/GBT variants exist, but are excluded from the core combination; expanded experiments remain research-only.
- `selector.scorable` requires bookmaker quotes for two-way live reference comparisons. This can reduce selection evidence. It is distinct from model training and outcome scoring, which do not inherently require historical odds.
- Both sports have historical training paths. Lack of new NBA regular-season forecasts is not a reason to defer historical NBA training or adaptive replay evaluation.

Revised implementation scope: extend existing training, combination, and ledger components for BOTH sports, with separately versioned scheduled within-season base-model refits and combination-weight refits on predictions made before their outcomes were available. Learn outcome probabilities without requiring odds; use contemporaneous quotes as an optional separately tracked decision/reference input. Expose missing quotes without suppressing outcome learning. Evaluate every compatible registered member on aligned game/market/line records; availability or eligibility must be explicit, and learned contribution may be near zero. Historical validation must replay the complete adaptive schedule and compare against the current system on identical later games. No new learning schedule or deployment was implemented by this reevaluation.
