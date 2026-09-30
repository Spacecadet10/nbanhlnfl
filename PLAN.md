# NBA Prediction App — Build Plan

## Outcome
A responsive web app for desktop and phones showing NBA games, pregame win probabilities, prediction history, and honest model performance. Build the frontend from the first milestone. Accuracy is measured, never promised.

## Scope
Four screens:
- Today: date selector, games, start times, win probabilities, source freshness.
- Game: team comparison, model versus market probabilities when available, prediction timestamp, and projected scores only once supported by a validated score model.
- Performance: sample size, date range, accuracy, Brier score, log loss, calibration, and market comparison on matched games.
- History: predictions recorded before tipoff, outcomes, and model versions; filters by date and team.

Include loading, empty, error, missing-data, stale-data, and mobile states. Demo fixtures must carry a visible Demo label. Do not mix demo metrics with actual results. Use plain language; put technical metric definitions in help text.

Defer accounts, payments, native mobile apps, player props, live betting, alerts, LLM explanations, elaborate simulations, and multi-sport support.

## Architecture
- Frontend: React, TypeScript, Vite; responsive styling and a small chart library chosen during setup.
- Backend: Python FastAPI with validated response schemas.
- Storage: SQLite for local MVP; migrations from the beginning. Reassess PostgreSQL for hosted concurrent use.
- Models: Python; historical home-win rate, then Elo and logistic regression.
- Updates: explicit ingestion/prediction/scoring commands first; scheduler once commands are reliable.
- Deployment: choose after local end-to-end acceptance. Keep API credentials on the server.

Planned directories: frontend/, backend/, tests/, docs/, data/. Keep generated data, credentials, and dependencies out of version control.

## Data and evaluation rules
1. Cache original source responses with source, retrieval time, content hash, and importer version. Do not overwrite raw artifacts.
2. Store event time, source publication/availability time where known, and retrieval time separately. Document historical timestamp assumptions; do not invent exact availability.
3. Training labels require results available before the prediction cutoff. Features must exclude the current game's outcome and future information. Pass only approved pregame features into predictors.
4. Store predictions append-only: game, cutoff, actual generation time, model/configuration version, dataset/feature version, probability, and run type (live, backtest, demo). Keep outcomes separately. Retrospective runs must never look like live pregame forecasts.
5. Use chronological walk-forward development. Fit transformations and calibration only on eligible training/validation data. Reserve a final test period; log accesses and do not repeatedly tune against it.
6. Compare models to simple baselines. Compare to de-vigged market probabilities on the same games and report coverage. Use paired loss differences with a seeded date/week block bootstrap and acknowledge small samples.
7. Distinguish odds available at forecast time from later closing-market benchmarks. Record bookmaker, market, quote time, retrieval time, and distance to tipoff. A stale final snapshot is not a verified close.
8. Exclude historical injury features until timestamp quality is established. Do not infer pregame knowledge from who eventually played.
9. Keep predictions available when odds are missing. Missing odds means no market comparison, not fabricated data.
10. During live evaluation, freeze model methodology and any scheduled retraining policy in advance. Version deliberate changes and report their results separately.

## Source strategy and budget
Start with an adapter for NBA schedule/results and team statistics; investigate nba_api during the data milestone. Validate endpoint reliability and permitted use before depending on it for public deployment.

Use a current-odds adapter only after checking provider coverage, quotas, terms, and credentials. Historical odds may require payment. Free local operation is a target, not a hosting or data guarantee. Calculate polling costs before enabling scheduled collection. No purchases without user authorization.

Target at least five historical seasons if reliably available. If blocked, report actual coverage and continue baseline development with explicit limitations. Historical odds acquisition must not block the frontend or basic model evaluation.

## Milestones

### 1 — App shell and API contract
Build the four screens with clearly labeled deterministic demo fixtures. Define API schemas for games, game detail, predictions, performance, and source status. Establish backend health endpoint and frontend API client. Include responsive layout and all data states.
Acceptance: both apps run from documented commands; the UI works at phone and desktop widths; frontend typecheck/build passes; API contract smoke test passes. No claims of real prediction accuracy.

### 2 — Reliable data and ledger
Implement migrations, raw cache, schedule/results/team-stat ingestion, stable game/team identifiers, availability rules, and append-only prediction storage. Add source assessment and coverage report. Investigate historical odds and implement normalization/de-vigging if data is available.
Acceptance: repeat ingestion creates no duplicates; cached input can be replayed; timestamps validate; ledger updates/deletes are rejected; missing data is surfaced. Document actual historical coverage and acquisition gaps.

### 3 — Backtester and baselines
Implement historical home-rate baseline, time-aware Elo, then logistic regression on a small set of rolling team/rest features. Test each independently. Select chronological train/validation/test periods based on actual coverage and record them before experimentation.
Acceptance: reproducible report with dataset version, per-game predictions, Brier/log loss/calibration, sample counts, and market comparison where available. Leakage tests cover same-day unfinished games, current-game labels, future rows, and preprocessing. No ensemble required.

### 4 — Real daily app
Connect the frontend to stored real schedules and model predictions. Add commands for refresh, pregame prediction generation, and postgame scoring. Introduce bounded scheduled jobs with retries, quota controls, and visible last-success timestamps.
Acceptance: one complete game lifecycle works from ingestion through prediction to scoring; reruns are safe; historical forecasts remain unchanged; no demo content is presented as real. Verify an end-to-end fixture and a real-data smoke run, identifying any external blocker.

### 5 — Validation and release
Run prospective shadow evaluation. Add calibration only if validation supports it. Consider a margin/total model and score projection separately; ensemble only if it adds validated value. Choose deployment target and operating budget once requirements are known.
Acceptance: usable mobile/desktop app, documented startup/backup/update procedure, source coverage and limitations, reproducible evaluation, no exposed secrets. Publish only within the user's deployment authorization. A useful release does not require an unsupported claim of beating the market.

## Collaboration and token budget
User intends Claude and Codex to collaborate; user named Claude Opus 5.5. Select the exact available Claude model in the Claude client; this plan does not verify availability or change either client's model.

Default workflow: Claude implements one milestone or bounded task; Codex reviews the diff and relevant checks. Swap roles when useful, but use one writer at a time. Reviewer fixes only a clearly assigned follow-up or returns a short actionable list. Do not ask both agents to independently implement the same feature.

Start each session by reading AGENTS.md and HANDOFF.md, then only the relevant plan section and files. End by updating HANDOFF.md with changed files, checks/results, blockers, and the next concrete task. Keep handoffs under roughly 50 lines. Avoid pasting the old conversation, rescanning the entire repository, or rerunning unchanged checks. Review model changes for leakage and evaluation validity; infrastructure/UI changes need appropriate engineering checks, not improved Brier scores.

Suggested first Claude prompt:
“Read CLAUDE.md and HANDOFF.md. Implement milestone 1 in PLAN.md only. Use labeled demo fixtures, define the API contract, build the four responsive screens, run relevant checks, and update HANDOFF.md for Codex review. Do not start ingestion or model training.”

Suggested Codex review prompt:
“Read AGENTS.md and HANDOFF.md. Review the latest milestone changes against PLAN.md. Inspect the diff and relevant tests. Report only actionable findings with file locations; do not rewrite working code. Update HANDOFF.md with the next bounded task.”

## Today’s delivery target
Complete milestone 1 today: a locally running frontend and FastAPI service, with four connected screens and clearly labeled demo data. This is a usable application foundation; real daily predictions still depend on data and model milestones. Do not promise production accuracy today.

Implementation order:
1. Create the single frontend and backend in the canonical directories. Define the API schema and deterministic backend-owned fixtures.
2. Connect Today and Game screens end to end; then Performance and History using the same API client.
3. Finish phone layout, navigation, demo labeling, empty/error/loading states.
4. Run the structure guard, typecheck, build, backend API tests, and browser/API smoke verification. Document exact install/start commands in README.md and evidence in docs/SMOKE_TEST.md.
5. Update HANDOFF.md for a focused Codex review. Correct blockers in place; do not create a second implementation.

Use npm scripts named typecheck and build, and backend pytest configuration that discovers API tests when run from backend/. The readiness command is `<backend-python> scripts/check_project.py --ready`, run from the repository root. Use the same Python environment that contains backend dependencies.

Do not wait for historical odds, API keys, model training, or hosting to finish today’s deliverable. Do not add further features before the app works end to end. The guard is a lightweight structure/check runner, not a guarantee of correctness.
