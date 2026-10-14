# Daily-learning training run + live-loop activation — consolidated request

Owner of this document: the session finishing the daily-learning code (Opus 4.8, 2026-10-11). The training run itself
is to be executed by the Claude session the user designates (Opus 5.5), per the user's instruction. This is the single
consolidated authorization request the product spec requires before expensive training or any live operational change.

## What this delivers

The daily-learning system: for NBA and NHL, every day, retrain the participating models, search model-agreement
combinations of **any size (2 through the full eligible roster)** per market with per-member confidence thresholds,
freeze the chosen rule before each day's games, record the result, and show all of it on the website `/daily-learning`
page across all 11 markets. N1/H2 are not privileged here; they are at most two candidates among all discovered ones.

This is NOT the two-rule activation, NOT a pairs/triples-only explorer, NOT a static threshold dashboard.

## State at the time of writing (verified this session)

- Code implements the daily cadence (`adaptive-train --cadence daily`), the all-size subset search
  (`combination_thresholds.search_large_subsets`), per-market/per-member thresholds, the honest earlier/later split,
  frozen append-only rules, and the `/daily-learning` page with per-game drill-down. All 11 markets are represented.
- Focused tests pass: `backend/tests/test_daily_agreement.py` (10) plus the adaptive/API/threshold suites (71).
  Typecheck, production build, and the structure guard pass.
- One bug was fixed this session: the daily path was the only code that opens the previous combiner's artifact file (to
  carry the incumbent rule). An unreadable/missing file would have failed every future daily run. It now drops only the
  carried rule, records the reason on the page (`incumbent_error`), and still trains. Regression test added.
- A real **NHL** daily training run is in progress on an isolated copy (`/tmp/nba-daily-train-run`, migrated to schema
  28), deep venv, to populate the page for real and to measure true cost. Live `data/` is untouched.

## Two honest limitations the user must keep in mind

1. **The historical "validation" numbers are selection-biased.** History is split into an earlier search third and a
   later validation third, but the winning rule is chosen using that same later third, then the later third's record is
   displayed. So those numbers read better than true out-of-sample. Only the prospective (recorded-before-the-game)
   ledger is unbiased, and it is empty until the loop runs for real game-days. (An independent leakage audit of this is
   running; fold its findings in before trusting any displayed historical number.)
2. **Daily retraining is expensive.** Daily cadence refits each model on roughly every game-day instead of every ISO
   week — about 6x more fit points. The weekly NBA pre-training already took ~5 hours; a from-scratch daily NBA history
   build has no cache to reuse (the daily definition hash differs) and could take far longer. NHL is much cheaper. This
   is the compute decision the spec demands be made explicitly, not silently worked around.

## Training run — exact commands (isolated copy only; never `data/`)

Use the deep venv (has torch 2.14.1 / TF 2.21.0) so no model is skipped, and an isolated `NBA_DATA_DIR` copy. The copy
in `/tmp/nba-daily-train-run` is already a read-only backup of live, migrated to schema 28. Rebuild it the same way if a
fresh one is wanted:

```sh
# from the repo root; fresh read-only copy of the live stores, then migrate the copy
python - <<'PY'
import sqlite3; from pathlib import Path
dst = Path("/tmp/nba-daily-train-run"); dst.mkdir(parents=True, exist_ok=True)
for s in ("nhl","nba"):
    src = sqlite3.connect(f"file:data/{s}.sqlite?mode=ro", uri=True)
    out = sqlite3.connect(dst/f"{s}.sqlite"); src.backup(out); out.close(); src.close()
PY
# REQUIRED: also copy the raw feature tree (~210 MB), or every candidate-pool member fails with
# "feature walk unavailable" and the combiner is only partial. Copy eval/ too if it exists.
cp -R data/raw /tmp/nba-daily-train-run/raw
[ -d data/eval ] && cp -R data/eval /tmp/nba-daily-train-run/eval
cd backend && for s in nhl nba; do NBA_DATA_DIR=/tmp/nba-daily-train-run .venv/bin/python -c "from app import db;c=db.connect(sport='$s');db.migrate(c);c.close()"; done
```

Definition-vs-environment note (verified the hard way): the fit definition hashes the set of installed candidate
models, so the DEEP venv (torch/keras present) computes a different definition than a venv without them. Train and
SERVE the preview from the SAME deep venv, or the page recomputes a different definition and shows "awaiting training"
over fits that exist. The live release must likewise carry the same model set as the trainer for its served definition
to match the trained fits (the same constraint the weekly system already has).

Then train (NHL first — it is cheaper and verifies the path; NBA is the long pole):

```sh
cd backend
DEEP=/Users/andry/nba-predictor-deep/backend/.venv/bin/python
NBA_DATA_DIR=/tmp/nba-daily-train-run OMP_NUM_THREADS=4 LOKY_MAX_CPU_COUNT=4 nice -n 10 "$DEEP" -m app.cli --sport nhl adaptive-train --cadence daily
NBA_DATA_DIR=/tmp/nba-daily-train-run OMP_NUM_THREADS=4 LOKY_MAX_CPU_COUNT=4 nice -n 10 "$DEEP" -m app.cli --sport nba adaptive-train --cadence daily
```

`--dry-run` is not a cheap check: it still fits models in memory. Expect NBA to run for many hours; if that is not
acceptable on this machine, run it on the deep box or decide to exclude the heavy neural members from the daily cadence
(a compute decision, not a silent substitution — state it on the page).

## Success criteria (verified read-only, not "a process ran")

For each sport, in the isolated copy's store: one `adaptive_fits` row with `kind='combiner'`, `status='success'`,
`schedule='daily'`, for the daily definition, plus the full member set successful. Then preview the page and confirm
every market shows discovered combinations (or an explicit `insufficient_evidence`/awaiting reason — never a fabricated
number), with the threshold ladder, baselines, and per-game evidence. Preview:

```sh
NBA_DATA_DIR=/tmp/nba-daily-train-run NBA_API_MODE=real /Users/andry/nba-predictor-deep/backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8021  # DEEP venv: must match the trainer's model set so the served definition matches the trained fits
API_PROXY_TARGET=http://127.0.0.1:8021 npm --prefix frontend run dev -- --host 127.0.0.1 --port 5181 --strictPort
# open http://127.0.0.1:5181/daily-learning
```

## Consolidated approval list for the LIVE daily loop (separate, explicit, after a clean trial)

Do none of these until the isolated trial is verified and the leakage audit is clean. Each line says what it unlocks:

1. Install a daily scheduler modeled on `com.nba-predictor.adaptive-train` but invoking `adaptive-train --cadence daily`
   for both sports, at a chosen off-peak time — unlocks automatic daily retrain + reassess. The current weekly agent
   stays; decide whether daily replaces or runs beside it.
2. Set `NBA_DAILY_AGREEMENT=1` on the two-hour forecast job — unlocks recording the frozen daily pick before each game.
   Off by default; nothing is recorded live until this is set.
3. Release/definition handling: editing `adaptive.py` changed both the weekly and the daily code-definition hashes.
   Decide how the serving release obtains fits for the new definition before switching it; do not relabel the existing
   pre-trained weekly fits as fits of the new code. Keep the current release serving until resolved.
4. Migrations 0027 (+0028 if the exact-rule work ships with it) on the live store, with a backup first.
5. First full real cycle verification: new eligible results -> daily refit -> reassessment -> frozen upcoming-game rule
   -> recorded forecast -> settlement, each observed, not assumed.

## What cannot be finished in one session, honestly

The live auto-daily loop depends on the scheduler + compute decision above. The prospective track record — the only
unbiased evidence of whether a combination helps — accumulates one game-day at a time and cannot be compressed. The page
will correctly show "recorded live" counts growing from zero as real days pass.

## Independent audit outcome (2026-10-11) and what was fixed

A separate read-only session audited the daily split and selection. Verdict REVISE. The forward/prospective path is
clean: cutoff earlier/later disjointness, frozen-before-later isolation, apply-time correctness and the walk-forward
history were all verified point-in-time, no leakage or look-ahead. Two issues were confirmed and fixed here:

- MAJOR (display only): the chosen rule's historical "validation" record is selection-biased — it is picked by taking
  the maximum later-period Wilson lower bound over many candidates and then that same later record is shown. Fixed the
  labeling: every "validation" on this path is now "selection", with an explicit winner's-curse caveat on the reason
  strings, the `report()`/`evidence()` notes (plus a `historical_record_bias: "selection"` field), and the website
  ("Later (selection — biased)"). The bias itself is intrinsic to reusing the later period for both choice and display.
- MINOR: the per-season 50% break-even floor was applied to the three-way market, which would have suppressed every
  regulation-three-way rule. Fixed: the floor is two-way only; three-way relies on the overall baseline-beating gate.

Remaining design choice (not yet made): to show an UNBIASED historical number, either reserve a third chronological
hold-out never touched by selection, or display only the prospective ledger as the rule's performance. Until then, the
only unbiased evidence on the page is the "Actually recorded live" section, which is empty until real game-days accrue.
