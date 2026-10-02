# What actually trains — audit (2026-10-02, code at 9fac164; deployed checkout d69e466)

Read-only audit by a separate agent in this session (not independent), spot-checked by Claude. Citations are file:line at
65917e3. "Online" = updates after every delivered result; "refit" = learned weights re-estimated.

## Correction
Claude said on 2026-10-02 that CatBoost "would be the first heavy dependency" and that the backend had "no external
machine-learning library". Wrong: `scikit-learn>=1.5,<2` is declared in `backend/pyproject.toml` and used by `gbt_v1`,
`gbt_points`, `goals_glm` (and numpy/scipy by the score members and stacker). numpy and scipy are imported directly but only
installed transitively (gap 5).

## Models and what they learn
| Member | Sport · markets · role | Learns | Refit | Fixed |
|---|---|---|---|---|
| `elo` | NBA winner · primary | team ratings | online (+ season carry) | k15 h50 c0.75 mov, sealed `frozen_decision.json` |
| `hockey_elo` | NHL winner · primary | team ratings | online | k4 h30 c0.75 mov, sealed `nhl_frozen_decision.json` |
| `points_normal` | NBA lines · primary | off/def ratings, league means, variances | online | eta 0.05, carry 0.5 (`nba_market_decision.json`) |
| `goals_poisson` | NHL reg3/lines · primary; winner member | attack/defence, league means, OT share | online | eta 0.003, carry 0.75 |
| `logistic`, `logistic_v2` | NBA winner · research | L2 logistic weights + scaler | season start, earlier seasons | l2 1, features |
| `efficiency_margin` | NBA winner · research | σ (season start); ratings online from boxes | season start | half-life 30 d, prior 10 |
| `gbt_v1` | NBA winner · research | sklearn HistGBT | season start | SETTINGS, seed 20260930 |
| `availability_two_hour` | NBA winner · research | as `logistic_v2` + absence features | season start | sealed forward facts |
| `hockey_logistic`, `hockey_gbt` | NHL winner · research | logistic / HistGBT | season start | as above |
| `ridge_points`, `gbt_points` | NBA lines · research | margin/total regressions; residual SDs | season start | λ 10 / GBT_SETTINGS |
| `ridge_points@weekly`, `gbt_points@weekly` | NBA lines · **shadow (new, 9fac164)** | same | season start **and first forecast of each ISO week** | same definitions |
| `goals_draw`, `goals_glm` | NHL lines · research | draw weight / Poisson GLMs | season start | grid / α 1 |
| winner + line stackers | both · combiners | log-linear pool weights | every run, on last 2 completed seasons | L2 1 |
| average, agreement | both · combiners | nothing | — | — |
| selector | both · rule choice only | which source/filter/threshold | weekly, Mon 14:00 UTC, settled live cohorts | thresholds, min evidence |
| threshold tracking | both · report | nothing | — | — |

## Do new finished games enter training in live operation?
- Every run rebuilds every model from the sealed config by replaying all results available at `now` (`near_tipoff.py`,
  `market_live.py`, `agreement.py` → `backtest.run(..., deliver_until=now)`). Online parts absorb every final immediately.
- Learned weights (all research members above) re-estimate only at the first game of a new season, so **current-season finals
  reach features but not weights until next season** — except the two new weekly shadow members.
- Line-member state is cached per input digest (`data/research/line-state-*.pkl`); any new result changes the digest.
- Stackers use the last two completed seasons; the selector learns only which rule to follow.

## Schedule (launchd, deployed)
`odds` every 15 min (NBA) / 30 min (NHL) → odds snapshot, weekly selector decision if due, and two-hour forecasts when a
game starts within 2 h (refresh → odds → boxes → injuries → primary → markets → research members → cohorts/selections);
`daily` 22:00 / 22:15 local (refresh and scoring, no forecasts); `injuries` :02/:32. `nba-markets` plist not installed.
Two-hour rule enforced in code and by triggers (migrations 0015, 0016).

## Gaps (status)
1. No trained-artifact identity stored for learned members (only code+config `definition` hashes). → addressed for research
   fits by the fit register (see docs/MODEL_SELECTION.md §11 when built).
2. Training cutoff not recorded (except primary Elo near-tipoff `latest_result_available_at`). → same.
3. `dataset_version` is a hash, not a stored list; past training sets are reproducible only by replay.
4. Learned weights season-start only (see above).
5. numpy/scipy undeclared; versions not in `definition`.
6. NBA market seal has no data-fingerprint check.
7. Unverified: within-season determinism of season-start refits beyond fixed seeds (the cadence study reproduced every member
   exactly on two separate walks of the same data, which supports it).
