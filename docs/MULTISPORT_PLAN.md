# Multi-sport and multi-market plan (NBA + NHL)

Recorded 2026-09-30 (session date), before any NHL data was imported or any NHL or new-market model was run.
Authorisation: the user's multi-phase brief of this date (NHL support, more markets, free sources, operations, UI).
Existing rules still apply: AGENTS.md guards, docs/EVALUATION_PLAN.md, the sealed NBA Elo and shadow decisions.

## Inventory (what exists and is reused)
| Area | Reused as is | NBA-specific today |
|---|---|---|
| Storage | `db.py` forward migrations, append-only triggers incl. INSERT OR REPLACE guards, `raw_cache.py` (content-addressed bytes + one `raw_fetches` row per retrieval) | `games`/`teams` ids `espn:<id>`; `odds_quotes.market = 'moneyline'` only |
| Ledger | `ledger.py` idempotency key (game, run type, model/dataset/feature version, cutoff); retries return the stored row | stores `home_win_prob` only: no market, selection or line |
| Backtest | `backtest.run` walk-forward with LeakageError guards, `dataset_version`, week-block paired bootstraps, `metrics.py` | season periods, bubble dates, Elo grid, `SEASON_TYPES` |
| Live/ops | `live.py` (night-before), `near_tipoff.py` (manual, freshness record, refusals), `jobs.py` (job_runs, quotas, retries), launchd plists | ESPN NBA URLs; jobs read the NBA store only |
| API/UI | one FastAPI app, one API client, four screens, confidence filter, policy toggle, demo provider, light/dark themes | no sport or market parameter; copy says NBA |
| Research | logistic, efficiency, GBT, stacking rounds (docs/EVALUATION_PLAN.md research rounds 2-3) | all NBA; 2024-25 and 2025-26 test seasons spent (1 access) |

## Architecture decision: one schema, one store per sport
- NHL gets its own SQLite file (`data/nhl.sqlite`) created by the **same** migrations and code. NBA stays in `data/nba.sqlite`.
- Why not a `sport` column in the existing tables (the earlier roadmap's idea): every existing NBA query (sealed Elo, shadow,
  near-tipoff, scoring, API) reads `games` without a sport filter. One missed filter would feed hockey results into the sealed
  NBA model. Separate stores make that impossible without touching NBA code paths, and leave the 27,744-row NBA ledger untouched.
- Collision protection: a one-row `store_identity` table (sport) checked on every connect for a sport; ids are namespaced
  (`espn:` NBA, `nhl:` NHL); new market tables carry `sport` and a trigger rejects rows whose sport differs from the store's.
- The API takes an explicit `sport` (default `nba`, so existing clients keep working) and never falls back to another sport.

## Phase checklist
- [x] 1 Inventory and this plan
- [x] 2 Source probes and repository review → docs/DATA_SOURCES.md "NHL and market sources"
- [x] 3 Foundations: sport registry, store identity, market quote and market forecast tables, NHL adapter + ingest + replay
- [x] 4 Market specification and settlement (docs/MARKETS.md) with tests, before any market model
- [x] 5 NHL ingestion, coverage report, preregistered evaluation, baselines (constant, home rate, Elo, goals model)
- [~] 6 Spread/puck line and totals for both sports from score distributions; team totals; period markets only if data passes
- [ ] 7 Bounded candidate research (not started: next would be an NHL draw-dependence candidate) (preregistered; NBA historical work is development only)
- [x] 8 Evaluation reports; NHL final test logged once
- [~] 9 Operations: sport-aware refresh/odds/predict/score/near-tipoff; health; scheduler config prepared for review (not installed)
- [x] 10 Frontend: sport, market and policy selection consistent across Today, Game, History and Performance
- [~] 11 Failure-mode tests, scratch-store rehearsals, final handoff

## Priorities and acceptance criteria
A. **NHL game winner (incl. OT/SO)**: seasons imported with coverage report; replay rebuilds identical rows; walk-forward
   report for constant / home rate / Elo / goals model on preregistered periods; live night-before and near-tipoff forecasts
   recorded in the NHL store; API and UI show them under `sport=nhl` only.
B. **Spreads/puck lines and game totals (both sports)**: every forecast stores the exact line; probabilities come from one
   score distribution per game and pass bound/sum/monotonicity checks; settlement handles win/loss/push/void; ESPN line
   quotes are stored append-only per retrieval; no line is ever invented (model-only probabilities are labelled as such).
C. **Team totals**: modelled from the same distribution; market lines only if a free source actually publishes them.
D. **Period/quarter markets**: only if historical segment scores exist in a season file and are verified; otherwise a
   documented blocker.
E. **Player props**: feasibility note only unless identity, participation, prior-game features and DNP policy are credible.

## Evaluation rules for new work
- NHL periods are fixed in docs/EVALUATION_PLAN.md ("NHL evaluation plan") before any NHL model output is viewed.
- NBA spreads/totals: historical work uses only warm-up + validation seasons (2019-20 … 2023-24) and is development
  research. 2024-25 and 2025-26 are spent and are not used for tuning, selection or confirmation. Independent evidence is
  the prospective 2026-27 record under a rule recorded before the first forecast.
- Confidence thresholds and records are per sport and market; the NBA 70% filter result is never shown for NHL.

## Out of scope for this brief
Paid or keyed sources, deployment, installing new persistent jobs without review, player-prop forecasts.

Status key: [x] done, [~] partly done (see HANDOFF.md), [ ] not started.
