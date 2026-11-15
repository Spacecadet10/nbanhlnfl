# Daily learning implementation and operator handoff

The `/daily-learning` page covers all five NBA and six NHL markets through the existing API client and adaptive system. This change adds code; it does not activate a scheduler or run real-data training. The existing primary predictions, exact-rule registry, weekly artifacts and serving release remain separate.

## Implemented cycle

1. `adaptive-train --cadence daily` uses the existing training lock, consistent SQLite snapshot, artifact publication and fit ledger. Daily periods use US Eastern calendar dates. Eligible newly available results trigger learned-member refits; online models receive eligible results. With no new results it skips. A missing dependency or insufficient training sample remains explicit. The daily deep path does not use the weekly path's 100-current-season-result deferral, but still requires an earlier season for epoch selection.
2. It saves all supported market member vectors at shared per-game lines in the combiner artifact, including source/cutoff/result-availability provenance. Walk-forward histories retain their out-of-sample prediction cutoffs. Settled prospective inputs can replace the corresponding reconstructed row, as in the existing adaptive trainer.
3. Each market uses a chronological two-thirds search / one-third selection split. Search excludes outcomes unavailable at the split boundary (a game whose result arrived after the boundary is in neither period). Only outcomes strictly available before the daily cutoff enter assessment. The later third both picks the rule and reports its record, so the chosen rule's later-period record is a selection statistic — optimistically biased (a winner's-curse) — **not** validation and **not** an untouched holdout. Only the prospective ledger recorded before games is unbiased.
4. Deterministic beam search explores **every size 2 through the eligible roster**, up to 512 evaluated rules per size, beam width 8, seed 17. Starts include ranked members, expansions of the preceding size, and seeded subsets. Mutations change members and their individual thresholds. Stops at the per-size budget or no new mutations. Every size is attempted; not every membership/threshold vector is evaluated. This is not exhaustive or globally optimal. Threshold grid: 50–90% at 5-point steps for two-way markets; 1/3, 35–90% at 5-point steps for regulation three-way.
5. Search ranks on earlier Wilson lower bound with at least 100 decided picks. Earlier-equivalent pick sets collapse for selection (the size-stratified candidates remain inspectable). Selection requires 50 later-period decisions, 5% coverage, a win rate above the earlier-selected strongest single model's matched-count record, at least one later-period season with 20 decisions, and — for two-way markets only — no such season below the 50% break-even. Regulation three-way has no 50% floor (the draw is a third outcome; applying one would have rejected every reg3 rule), so there the baseline-beating gate alone carries stability. The incumbent remains unless a qualified challenger improves its later-period (selection) Wilson lower bound by two percentage points. Otherwise `insufficient_evidence` is legitimate. These are explicit research-policy defaults, not proven-optimal parameters.
6. The chosen subset and unequal thresholds freeze in the content-addressed combiner artifact. `adaptive-record --cadence daily` applies that exact decision before tipoff. Unanimity, all-member availability, individual thresholds and ties are checked; no substitutes. The rule and reason persist under `selection.daily_agreement` in the existing append-only `adaptive_forecasts` table. Its fit ID references the frozen rule artifact. Stable database idempotency keys prevent duplicate retries; a genuinely new fit/version can append a new forecast. Settlement reads final scores and treats moved/canceled games as void. Latest-per-game/market reporting never counts retries as extra games.
7. The opt-in `NBA_DAILY_AGREEMENT=1` lets the existing two-hour jobs additionally record daily forecasts. Default is off. No training runs on the request or forecast path. The original adaptive combination and its selector stay distinct from unanimous daily rules.

## Injury and player-rating member (NBA, added 2026-10-11; retrain pending)

`adaptive.members` registers one more NBA winner member, `players_injuries_v1@adaptive` (`player_ratings.PlayersInjuriesV1`,
docs/PLAYER_RATINGS_STUDY.md §11): `logistic_v2` plus the injury-report features and the expected-roster player-rating
difference after reported absences, with absent minutes from delivered games only. It is a walk member like the others: the
daily walk fits it, the two-hour cycle thaws it, delivers what arrived after the artifact's cutoff and predicts.

- **Information time.** One row, one cutoff: the walk's rows have `cutoff_at` = midnight ET of the game date. The member
  uses only the latest injury report published before that cutoff that lists the game — in practice the previous evening's
  5:30 PM report's next-day section — and only rosters/ratings from results delivered before it. The same-day 5:30 PM report
  (the one the research study measured at 17:45 ET) is published after the cutoff and is never used in the walk, so the
  study's effect size does not transfer. A team counts as reported only when it is **positively listed** on that report
  (an entry of any status) and not marked NOT YET SUBMITTED; a team listed nowhere has usually not reported yet, so it is
  the declared `report_missing` state, never a healthy team (review finding A1; it used to count as submitted with zero
  absences). Measured read-only on the training copy over every final regular-season game of 2021-22 … 2025-26 (6,605
  games, corrected parser): 93.8% have a usable prior-evening report, but only **31.8% have both teams positively listed**
  on it (30.5–33.0% by season; the earlier "37% both teams submitted" figure came from a 60-date sample and the looser
  "not marked NOT YET SUBMITTED" rule). The rest are `report_missing`. A game tipping off before 18:00 ET may use the
  prior-evening report (the study's evening-only rule belonged to the 17:45 ET design).
- **Inputs and degradation.** The member reads the availability research cache of the data directory lazily (first forecast
  or delivered result), never from `members()` or an API read. No player box in the data directory → the member is excluded
  with the reason (training proceeds without it). Reports absent or unusable (unreconciled, published after the cutoff,
  missing metadata) → the declared `report_missing` state, never guessed availability; a corrupt box file fails the member
  visibly (training `partial`, retried) while the other members run. What it read is in the member's fit detail (`inputs`:
  box seasons and digest, report count, facts coverage). A result whose game has no box rows updates nothing and is NOT
  recorded as delivered (`result_without_box` counts it), so a later cache extension cannot rebuild rosters the ratings
  never saw.
- **Cache coverage and the two live guards.** The LIVE `data/` cache ends with the **2023-24 box** (`player_box_2020` …
  `player_box_2024.csv`) and **1,002 reports to 2024-06-17**; the training copy used for the runs holds the box through
  2025-26 and reports to 2026-06-13. On a store whose cache stops before the current season the member is **active but
  stale**, not inert: in the walk history its forecasts are `logistic_v2` plus near-constant player/injury features
  (`report_missing` = 1, ratings and rosters frozen at the last cached season), and it can score worse than `logistic_v2`
  over that stretch. In live recording two guards now make it `missing` with the reason instead of forecasting silently:
  (1) player-box staleness — a team's recent finals of the season (`candidates.live.STALE_BOX_GAMES`) without player-box
  rows (absent, empty or truncated current-season CSV) → "stale inputs … without player box rows"; (2) train/serve input
  consistency — the fit records the box digest and seasons it read, and a serving process whose resolved cache differs
  (e.g. a season file added after the fit) → "inputs changed since the fit … retrain". Extend the cache to the current
  season (box + reports) or wire live inputs, then retrain, before reading a rule containing this member as evidence.
- **Pseudo-agreement.** The member is a superset of `logistic_v2`, so their unanimity is not independent evidence. Every
  daily decision carries `member_overlap` — the earlier-period share of same-side picks for every member pair, with a
  note — including the `insufficient_evidence` early return (no pairs when no table could be built). The rule ledger
  (`/api/research/thresholds/ledger`) carries the same overlap for the rule's members, and the `/daily-learning` page and
  the ledger table show it next to each rule, flagging pairs that agree on 90% or more of their games as not independent
  votes. The coverage-matched best-single gate guards this only partly; the overlap is descriptive and changes nothing.

## Website and evidence

- Both sports and all 11 markets appear together, with trained/usable fit coverage, historical prediction counts and actual recorded-game counts as separate columns.
- Inspect each model, the explicitly labelled equal average, searched subset sizes and member-specific thresholds, baseline comparisons and season records.
- Individual ladders and the exact-subset evidence view expose every configured cumulative threshold and disjoint band, including zero-pick rows. A subset ladder uses **minimum member confidence with unanimity**, not average probability. The selected unequal-threshold rule remains a separate result.
- Enter any supported exact subset (`model@0.6,other@0.7,…`) to inspect saved evidence. Game drill-down paginates by 100; source, exact line, date, member probabilities and settlement are visible. This is read-only and does not register arbitrary rules.
- The history section reports reconstructed vs settled-recorded input counts. Its later-period results are research and selection-biased (the page labels that period "selection — biased"); the separate live ledger reports only decisions actually frozen and recorded before the game. No recovered forecast is silently added to the live ledger.
- Fit IDs, definition, cutoff, publication, failed attempts, exclusions, recording configuration and the required daily schedule are visible. Recording enabled is not claimed to prove operational success; actual recorded counts are separate.

## Claude's operations, after approval

No new migration is needed for this feature; it reuses migration 0024 tables. Any already-pending 0027/0028 release migration remains part of its existing separately approved release procedure.

Changing `adaptive.py` changes the code-derived weekly definition as well as the new daily one. Previously completed pre-training and its artifacts have **not** been overwritten, but those fits must not be relabelled as fits of the new code. Before switching a serving release, choose and approve how the weekly service obtains its new-definition fits. Retain the current release until this is resolved.

Use the pinned research environment with all intended deep dependencies and an explicit isolated `NBA_DATA_DIR` for initial validation. From `backend/`, after approval:

```sh
NBA_DATA_DIR=/absolute/approved/data /absolute/research/venv/bin/python -m app.cli --sport nba adaptive-train --cadence daily
NBA_DATA_DIR=/absolute/approved/data /absolute/research/venv/bin/python -m app.cli --sport nhl adaptive-train --cadence daily
```

Do not use `--dry-run` as a cheap readiness check: it still fits models in memory. Initial chronological **daily** history, especially neural models, may cost considerably more than the completed weekly pre-training. No daily production runtime or compute budget has been measured here. The search evaluation budget is bounded; model training has no wall-clock cap. Benchmark the approved isolated run, inspect each member failure/skip, and decide runtime/compute before scheduling it every day. No annual-fit substitute is presented as daily retraining.

One consolidated future activation approval should name the training data location/environment and compute cap, daily scheduler time and commands for both sports, release revision/build, handling of changed weekly definitions, existing pending migrations, backups, and the `NBA_DAILY_AGREEMENT=1` change to the existing forecast jobs. Existing store/training locks are retained. Do not register N1/H2 or change their treatment as part of this daily system; they remain unprivileged historical examples.

After training, verify all market histories/usable members and actual fit cutoffs, inspect selected/no-pick decisions, then observe the first approved pregame recording, retry and settlement. Preserve the isolated validation output, initial live fit/forecast IDs and the 11-market status table. This is when real-data operation can be accepted; synthetic tests alone do not establish that production training will complete on schedule.

## Local preview used for this implementation

Existing development SQLite snapshots were backed up into `/tmp/nba-daily-learning-preview-20261011`; no live store was migrated. From the repository root:

```sh
NBA_DATA_DIR=/tmp/nba-daily-learning-preview-20261011 NBA_API_MODE=real backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8021
API_PROXY_TARGET=http://127.0.0.1:8021 npm --prefix frontend run dev -- --host 127.0.0.1 --port 5181 --strictPort
```

Open `http://127.0.0.1:5181/daily-learning`. Those snapshots have no fits for the new daily definition; all markets honestly show awaiting training. No source-data completeness or live predictive-performance claim follows from this preview.
