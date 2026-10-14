# Sports autoresearch (karpathy/autoresearch adapted to NBA/NHL)

Research only. Nothing here writes a store, a forecast table or a live artifact, and nothing approves or promotes a model.
Status: built and exercised on isolated copies, including real headless-agent runs. **Not activated, not independently
reviewed** (see the end).

## What comes from where
| Part | Source | Location | Changes |
|---|---|---|---|
| Upstream | github.com/karpathy/autoresearch @ `228791fb499afffb54b46200aca536f79142f117` (2026-03-25) | `~/autoresearch-sports/upstream` | None (reference only). Needs one NVIDIA GPU (CUDA 12.8 torch, FA3 via `kernels`), so it cannot run on this M4 Mac |
| Mac fork (smoke test only) | github.com/miolini/autoresearch-macos @ `537c6e6d0ecf7d28f9d70ce20bb05d8c7ed9cfce`. A true Git fork of upstream `2224cd7`: 5 fork commits, 13 upstream commits behind (incl. upstream's NaN fast-fail and empty-shard guards) | `~/autoresearch-sports/fork-macos`, with its own uv venv (Python 3.10.21, torch 2.6.0, MPS) | None by us. The fork's own changes: MPS device detection, PyTorch SDPA instead of FA3, no `torch.compile` on MPS, torch 2.6.0 from PyPI, DEPTH 4, batch 2^16, window "L" |
| Workspace (upstream layout) | Upstream `program.md`, `README.md` and `.gitignore` committed verbatim as `761f95a`, then adapted (`1e16616`, `1087d0d`) | `~/autoresearch-sports/workspace` | `program.md` rewritten for the sports rules; `prepare.py` is a fixed shim; `train.py` is the editable target |
| Integration (ours) | This repo, branch `autoresearch-sports` | `backend/app/autoresearch.py` (evaluator), `backend/app/research_agent.py` (agent loop), `backend/app/research_status.py` + `GET /api/research/autoresearch` + `frontend/src/components/Autoresearch.tsx` (status on the Daily learning page), their tests, this file | New files, plus additive route/types/client entries. No existing module's behaviour changed |

Upstream's text/GPT benchmark is not used for sports. It was run once, through the Mac fork, as an environment smoke test.

## Mapping of the upstream loop
| Upstream | Here |
|---|---|
| `prepare.py`: fixed constants, data, `evaluate_bpb` | `app/autoresearch.py`: `PROTOCOL`, `build_dataset` (`study.existing_tables` + `features.walk`), `walk`, `score_market`. The workspace `prepare.py` only calls it |
| `train.py`: the one file the agent edits | Workspace `train.py`: `candidates(sport)` (`registry.Spec` families/heads), `fit`, `outputs`, `MARKETS` |
| `uv run train.py > run.log`, then `grep "^val_bpb:"` | `python prepare.py trial --sport nba > run.log`, then `grep "^status:\|^market_" run.log` |
| `val_bpb` (lower is better) | Per market: log loss vs the incumbent on a fixed matched cohort (keep rule below) |
| Agent writes `results.tsv` | The evaluator writes `results.tsv` and the append-only `trials.jsonl`. The agent cannot write them (sandbox) |
| Fixed 5-minute budget | Per-trial hard timeout (default and cap 30 min; the process group is killed), trial/time budget per daily run, concurrency 1 per sport |
| Keep the commit or `git reset` | Same, per program.md. The incumbent per market is replayed from the log, not from git |

## Protocol `sports-autoresearch-v2`
Fixed before any trial. The final ids are `-223d3ee95f99` (NBA) and `-d207475e7f43` (NHL); earlier ids from this session's revisions keep their own logs. Each id hashes the protocol, every app source/model file, the library versions, the snapshot (store sha256, raw manifest, derived files) and the upstream commit. Any change gives a new id with empty evidence.

- **Data:** read-only copies (`chmod -R a-w`) in `~/autoresearch-sports/data-snapshot-2026-10-14`:
  - `nba.sqlite`/`nhl.sqlite`, copied 2026-10-14T03:39:55Z, integrity ok, sha256 `8e5ac8da…` / `2858bf4a…`
  - the raw response cache, copied 04:04:42Z
  - the NHL goalie parse cache (`research/candidates/goalies-v1.json.gz`), derived from the raw cache by the first NHL build and now part of the identity
- **Search seasons:** the existing `validation` loaders filter seasons in SQL, so reserved-season rows are never loaded: NBA 2020–2024, NHL 2016–2024. One exception: NHL `goalies.load` parses play-by-play imports of every season, but that feeds only the goalie features, which are stripped. **Scored seasons:** 2022–2024. **Cohort per market:** NBA 3,962 games, NHL 4,201.
- **Isolation of the candidate:**
  - The evaluator runs the existing season-start walk itself, season-major across all of the trial's specs.
  - `train.py` runs in a separate process under `sandbox-exec`:
    - **Reads:** no file under `/Users`, `/Volumes`, `/private/tmp` or `/private/var/folders`, except the Python install, its virtualenv, `backend/app` source and the trial's tmp dir. That rules out every store copy, the experiment root and the workspace.
    - **Writes:** only its tmp dir.
    - **Environment:** no network and no `NBA_DATA_DIR`.
  - For each season, every spec is asked only to fit `engine.training_rows`: rows of earlier seasons whose results were available before the season's first cutoff.
  - The process then forecasts that season one cutoff at a time, in time order, with labels removed. At no point does it hold a label of a game it has yet to forecast, or a feature row from a later cutoff.
  - Its answers are JSON, never unpickled, and are checked against the head's output format.
- **Cutoff:** night before, 00:00 ET (`backtest.cutoff_for`). Results and box/shot events reach the feature walk only when available at the cutoff (`backtest.run` raises `LeakageError` otherwise).
- **Markets:** all NBA markets (5) and all NHL markets (6). Line markets are settled at the primary's own line, because there are no historical bookmaker lines. The primary is therefore close to 50/50 there by construction (NBA: log loss ln 2).
- **Bar (starting incumbent), per market:** the existing app member with a forecast for every cohort game and the lowest cohort log loss.

  | Sport | Market | Bar member | Cohort log loss |
  |---|---|---|---|
  | NBA | winner | `logistic_v2` | 0.62968 |
  | NBA | spread | `ridge_points@weekly` | 0.68643 |
  | NBA | total | `points_normal` | 0.69296 |
  | NBA | team_total_home | `points_normal` | 0.69268 |
  | NBA | team_total_away | `points_normal` | 0.69306 |
  | NHL | winner | `hockey_logistic_xg` | 0.65261 |
  | NHL | reg3 | `goals_draw` | 1.03491 |
  | NHL | spread | `goals_glm` | 0.67613 |
  | NHL | total | `goals_draw` | 0.69041 |
  | NHL | team_total_home | `goals_glm` | 0.68827 |
  | NHL | team_total_away | `goals_draw` | 0.68658 |

  The primary is reported for information only.
- **Inputs excluded:**
  - NHL goalie features: historical rows use the actual starter, which is postgame. They are stripped, and a spec using them is invalid.
  - Injury reports and player ratings: no timestamped history exists for the search seasons (PLAN rule 8), and players-v1 found no information beyond the team model.
- **Keep rule, per market vs the incumbent:**
  - Every cohort game must have a forecast. A missing game is a coverage failure, never dropped.
  - Paired log-loss difference, week-block bootstrap (seed 20260930, 2,000 samples): the 95% upper bound must be below 0.
  - Matched pick accuracy must not be lower. An undefined accuracy is a reject.

  This is the MODEL_SELECTION §6 test applied to validation: an exploratory search, not promotion evidence. Each record states how many train.py versions searched the same seasons.
- **Agreement (descriptive):** reported per market, at the sealed `agreement_tracking.THRESHOLDS`, for four groups: primary alone; candidate+primary unanimous; majority of the existing members; majority of the candidate plus existing members.
  - A group needs every member. A missing member makes the group incomplete, never a smaller group.
  - Each row covers the same game, market and line.
  - Each threshold reports picks, coverage and accuracy.
- **Holdout:** the existing `test` seasons (2025, 2026).
  - These were already used once by earlier studies, so they are protected here but not pristine.
  - The holdout is spent once per protocol and sport, testing every market's kept incumbent together, each with its own trial's train.py.
  - It needs a phrase typed at a terminal. That stops accidental and non-interactive use. It does not stop an agent that deliberately drives a pseudo-terminal with the user's shell; only a separate OS user would. program.md forbids it.
  - A per-sport ledger across all protocol ids in the experiment root means a new protocol is not a fresh holdout. Reuse needs `--reuse-reason`, which is logged.
  - It runs each trial's saved `train.py` after checking it against the logged sha256.
  - The access is logged as `pending` before computing, then `completed`. A run that fails before scoring is logged `failed` and may be repeated (at most 2 failures).
  - It has not been run.
- **Forward record:** the only route to promotion (MODEL_SELECTION §6 and the coverage-v2 rule that no adaptive definition is approved). Unchanged.

## Trial log and failure handling
- Every trial is logged in `trials.jsonl` with these fields:
  - **Commit and code:** `started`/`finished` events, commit, train.py sha256, spec definitions (`registry.definition`).
  - **Data and cutoff:** seed, incumbent state, data identity (snapshot, cache, dataset version), cutoff policy.
  - **Run:** resources (wall time, CPU, peak RSS), timeout.
  - **Results per market:** cohort, coverage, matched metrics (log loss, Brier, accuracy, abstentions), vs incumbent and vs primary with week-block CIs, per-season deltas, decision and reason; agreement tables.
  - **Status:** `keep`, `discard`, `invalid`, `crash`, `timeout` or `interrupted`.
- `results.tsv` is upstream's one-line view, listing only the kept markets' deltas.
- **One evaluation per commit:**
  - `--retry` re-runs only a crashed, timed-out or interrupted attempt (at most 2 retries).
  - A new commit with identical train.py, files and incumbents is logged with `cached_from` instead of re-running. It never becomes an incumbent and never counts as a new result.
  - A `started` record without a finish is logged `interrupted` on the next run.
- **Invalid:**
  - `train.py` failing the static check: imports allowlisted; from `app.candidates` only engine, registry, learners, deep and features, and only names the app defines; no `open`/`eval`/`getattr`/private attributes.
  - A commit that changes any file other than `train.py`, or that lacks a readable `train.py` or protected file.
  - Malformed outputs: wrong keys or types, non-finite values, outside [0,1], not summing to 1, unknown or out-of-scope games, a push where none is possible, wrong output count.
  - Postgame inputs.
- **Refused (nothing logged):**
  - live or unset `NBA_DATA_DIR`
  - writable snapshot
  - experiment root inside the repository, live store or snapshot
  - uncommitted workspace changes
  - drift of the evaluator, app code, libraries or frozen dataset
  - a second concurrent run
- **What the tests check** (`tests/test_autoresearch.py`, 55): the sandbox blocks reading the frozen dataset or any other store copy, and overwriting the log (verified on real data too: snapshot, live store, workspace and experiments are unreadable, and writes fail). A label stashed during any spec's fit is never one of a game later forecast; the previous spec-by-spec schedule leaked 1,000 such forecasts in the same check. Patching the engine from train.py does not change what the fits receive. A timeout leaves no worker processes (also verified with real loky workers). Mutating each key guard makes its test fail.

## Results of this session (all exploratory; nothing promoted)
| Trial (branch `autoresearch/oct14d`, final protocol; identical results under the previous v2 id on `oct14c`) | NBA | NHL |
|---|---|---|
| Baseline `1087d0d`: logit_l2 + ridge / poisson_draw (existing pool families) | No improvement in any market. E.g. spread +0.0004 [−0.0004, +0.0013] vs `ridge_points@weekly` | **Kept:** reg3 −0.0083 [−0.0108, −0.0059] vs `goals_draw`; away team total −0.0027 [−0.0051, −0.0002] (marginal). Other markets rejected |
| torch_mlp NBA winner (existing deep family) | Discard: +0.0013 [−0.0017, +0.0042] vs `logistic_v2` | n/a |
| multinomial_l2 NHL winner + reg3 | n/a | Discard: winner +0.0006 vs `hockey_logistic_xg`; reg3 −0.0013 [−0.0040, +0.0014] vs the kept trial |
| hetero_linear NBA lines (via `daily`) | Discard | Discard (unchanged NHL code vs its own keep) |
| `import os` commit (via `daily`) | invalid | invalid |

- The NHL keeps reproduce the earlier pool study, where `poisson_draw:nhl_base` was better than `goals_draw` on reg3 and on the away team total. That is a known pool candidate, not a new model.
- Under protocol v1, whose bar was the primary, the NBA spread and NHL winner also passed. Against the best existing member they do not. v1 overstated them: its NBA line "keeps" only beat a primary that is 50/50 by construction.

## Agent loop (`prepare.py agent`): proposal → real fit → fixed evaluation → recorded decision
Started by a person, never by the agent; nothing schedules it. One run (`research_agent.run_agent`):
1. **Setup** (upstream's step with the human): refuses on a STOP file, another run (lock), a dirty workspace or a workspace
   master that is not the registered protocol base; checks out a fresh `autoresearch/agent-<run id>` from master.
2. **Agent**: one headless Claude Code session (`claude -p`) in the workspace on program.md. Permissions are NOT bypassed:
   `--restricted` (file tools confined to the workspace and, read-only by rule, the app source), `--tools Read,Edit,Grep,Glob,Bash`,
   an allowlist of fixed command forms (`Edit(./train.py)`, git status/diff/log --oneline/show/add train.py/commit -m/reset
   --hard, `python prepare.py trial|status`), a deny list (`--output`, `--ext-diff`, `-c`, `--config`, `--template`, `-F`,
   Write, web tools), `--permission-prompts none` (anything else is denied without asking), `--max-budget-usd` (one cap shared
   by the launch and its single retry), no MCP servers, no session persistence, and an explicit environment (`AGENT_ENV`).
   The whole process tree runs under `sandbox-exec` with `agent_profile`. Under `$HOME` it writes nothing except the
   workspace, and reads only the workspace, the app source, the Python install, the run's `python` shim and, each one
   required and verified with Claude Code 2.1.299: the Claude Code install (`~/.local/bin`, `~/.local/share/claude`), the
   login keychain (`~/Library/Keychains`: macOS keeps Claude Code's login there, encrypted; items are released by
   securityd, and the agent's shell has no command to request them) and git's user config (commit identity). The user's
   `~/.claude*` (hooks, MCP commands, other sessions' transcripts) is neither readable nor writable; Claude Code runs
   without it. The data dir, experiment root, live stores, repository and other sessions' temp files are denied. Inside
   the workspace, `.git` is writable only where commit/reset need it (objects except `objects/info`,
   `refs/heads/autoresearch/*`, logs, index, HEAD, ORIG_HEAD, AUTO_MERGE, lock files) and `.git/config`, hooks, info,
   alternates, packed-refs, the master ref, every `.gitattributes`, `prepare.py` and `program.md` are read-only. Network is
   denied except TCP 443 (the model API; acceptable only while the agent has no write or execution primitive outside the
   sandbox), DNS and the broker socket.
   Every git call of the driver and evaluator runs with the repository-configurable programs git lets us switch off
   switched off (fsmonitor, hooks, external diff, pager, ssh; no system/global config). Filter drivers cannot be switched
   off generally, so before EVERY git call on a registered workspace the files that configure git (`workspace_git_sha`:
   config, config.worktree, hooks, info, objects/info, every `.gitattributes`) must equal those registered with the
   protocol (`protocol.json` `workspace_git`); a change refuses the call. After the agent ends, a change also writes STOP
   and skips every further git command and the sweep.
3. **Broker**: inside that sandbox the evaluator cannot read the data, so the workspace `prepare.py` hands `trial`/`status`
   to a unix-socket broker in the (unsandboxed) driver. It accepts only `--sport`, `--description`, `--retry`; holdout,
   prepare, daily, agent, stop, `--exp-root`, `--workspace` and `--timeout` are refused. One request at a time.
4. **Budgets outside the agent**: `trial_gate`, called by the evaluator for every trial, refuses past the run's trial budget,
   with under `min_trial_s` left before the deadline, after a STOP, or when the run's driver is gone (an orphaned agent cannot
   keep running trials). A trial's timeout is capped at the time left (and 540 s, inside one agent shell call). At the
   deadline or on `prepare.py stop` the driver kills the agent's process tree and any trial in flight (recorded
   `interrupted`). One trial at a time across sports (`trial.lock`). A launch failure (non-zero exit with no trial, within
   120 s) is retried once. STOP is checked before and after the lock, after the (minutes-long) protocol preparation and
   every second while the agent runs; the stop handler stays installed until the run's records are written.
5. **Afterwards**: interrupted trials get their record; commits the agent made but did not run are evaluated by `daily`
   within what is left of the budget; uncommitted edits are saved as `uncommitted.diff` (never evaluated); the workspace
   returns to master; `agent/runs.jsonl` gets the run's start and end (stop reason, outcome, trials, cost) and
   `agent/runs/<id>/` keeps the prompt, the full stream-json transcript, the sandbox profile and `report.json`.

`python prepare.py stop [--reason ...]` is the kill switch: it writes `agent/STOP` (every trial and new run is refused until a
person runs `stop --clear`) and signals the driver. Status: `GET /api/research/autoresearch` (reads the experiment root named
by `NBA_AUTORESEARCH_DIR`; research only), shown on the Daily learning page.

## Commands
```
cd ~/autoresearch-sports/workspace
python3 prepare.py prepare --sport nba --sport nhl      # once per protocol (NBA ~2.8 min, NHL ~1.5 min)
python3 prepare.py trial --sport nba > run.log 2>&1      # one trial on the committed train.py
python3 prepare.py status
python3 prepare.py daily --sport nba --sport nhl --max-trials 4 --max-minutes 120 --timeout 1800
python3 prepare.py holdout --sport nhl                   # human only, at a terminal; one-shot
python3 prepare.py agent --sport nba --sport nhl --max-trials 3 --max-minutes 30 --max-usd 3   # one bounded agent run
python3 prepare.py stop --reason "..."                   # kill switch; `stop --clear` to allow runs again
```
- `daily` evaluates, oldest first and within the budget, the commits between the protocol base and the workspace HEAD that have no record yet.
- It reads git objects only and never checks anything out.
- Only executed trials count against the budget.
- It writes `experiments/reports/daily-*.json`. "No improvement found" is a valid outcome.
- It never starts an agent.

## Limits
- The sandbox uses macOS `sandbox-exec`, which is deprecated by Apple but functional on this machine (macOS 26.6). The child may still run processes, but they inherit the sandbox.
- Data, protocol and evaluator are protected by hashes and by the sandbox on the candidate. The agent's own shell (outside the sandbox) can read the snapshot. Protection against a deliberately adversarial agent would need a separate OS user.
- Repeated validation search inflates false keeps. The bootstrap is not corrected for the number of trials. Each record states the count, and the holdout and the forward record are the checks.
- Historical lines are model lines (the primary's), not bookmaker lines. Nothing here measures betting value.

## Activation (NOT done)
1. Independent review of this increment (a separate session or Codex).
2. A human starts an agent session in the workspace on a new `autoresearch/<tag>` branch, with a trial budget. For example: `cd ~/autoresearch-sports/workspace && claude`, then "read program.md and start a run; budget 4 trials". Upstream suggests disabling permissions for an unattended agent. That is not recommended here and was not done.
3. Optional scheduling of `prepare.py daily` via launchd, only after steps 1–2. No plist exists yet.

None of these steps promotes anything.
