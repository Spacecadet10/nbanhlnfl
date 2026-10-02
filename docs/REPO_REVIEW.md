# Repository review (2026-10-02)

Inspected by a research agent in this session (read-only clones, throwaway venvs; not an independent review). Probe
scripts and raw outputs: docs/evidence/repo-review/. How the findings were used: docs/CANDIDATE_POOL.md §1 (NGBoost adopted
as an optional research learner with a seeded base tree and chronological early stopping; bivariate Poisson rejected
because our residual goal correlation is −0.05; optuna not used this round because every setting is declared, not searched).

Review date: 2026-10-02. All clones were `git clone --depth 50` in a scratch directory (not kept). Probes ran in throwaway venvs (`.venv`, `.venv_pb`) made with `uv venv -p 3.12`. Probe scripts and their raw outputs are in `docs/evidence/repo-review/`. The project was not modified by the review.

Environment that resolved in `.venv` (one `uv pip install`, no conflicts): Python 3.12.12 (uv-managed, macOS arm64), numpy 2.5.3, scipy 1.18.1, scikit-learn 1.9.1, ngboost 0.5.11, optuna 5.0.0, xgboost 3.4.1, lightgbm 4.7.0, statsmodels 0.15.0, pandas 3.0.6, pyarrow 25.0.1, pbpstats 1.3.11. Separate `.venv_pb`: penaltyblog 1.13.0 with numpy 2.5.3 / scipy 1.18.1 / pandas 3.0.6.

## Summary table

| # | Repo | HEAD (date) | License | Py3.12 / numpy 2.x | Maintenance | Reusable component | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | catboost/catboost | eb5a7d1 (2026-10-02) | Apache-2.0 | not re-installed (already rejected) | very active; 738 open issues+PRs; PyPI 1.2.10 (2026-02-18) | `RMSEWithUncertainty` = Gaussian NLL with diagonal-Newton leaves | **Reject** (retest only under a specific hypothesis, see §1) |
| 2 | stanfordmlgroup/ngboost | 6aadc1f (2026-09-01) | Apache-2.0 | **yes**: 0.5.11 runs with sklearn 1.9.1, numpy 2.5.3 | active; 50 open; PyPI 0.5.11 (2026-06-26) | `NGBRegressor(Dist=Normal)`, a heteroscedastic Normal for margin/total | **Adopt (as an experiment)** with a seeded `Base` and a chronological `X_val` |
| 3 | optuna/optuna | 5c8e50d (2026-09-30) | MIT | yes, 5.0.0 | very active; 19 open | TPESampler(seed) + MedianPruner + sqlite RDB storage | **Adopt** for bounded, logged searches |
| 4 | dblackrun/pbpstats | e7ccf2f (2024-04-16) | MIT | installs (only dep is requests) | stale since 2024-04; 16 open | possession/lineup parser (live S3 pbp source still works) | **Reference only** (lineups are postgame; the stats.nba.com `playbyplayv2` source returned an empty `{}`) |
| 5 | sportsdataverse/fastRhockey | 5ce9fda (2026-09-30) | MIT (LICENSE.md) | R, not run | active; 0 open | loaders → sportsdataverse-data releases; XGBoost xG that has no training code | **Reference only** |
| 6 | sportsdataverse/sportsdataverse-data | d15f769 (2026-10-01) | MIT (code); data terms not stated | parquet reads with pandas 3 / pyarrow 25 | very active (releases updated 2026-10-01) | `nhl_goalie_boxscores`, `nba_stats_player_boxscores`, `nba_player_impact`, `nhl_pbp_*` parquet | **Adapt** (backfill/cross-check only; season-aggregate tables leak) |
| 7 | shufinskiy/nba_data | e829d46 (2026-07-17) | Apache-2.0 | tar.xz CSV | active | archived stats.nba.com / pbpstats / cdn pbp dumps, 1996–2025 | **Adapt** (historical pbp source if (c) is pursued) |
| 8 | shufinskiy/nba-on-court | 6791ac6 (2024-12-20) | MIT | **no**: uses `np.in1d` (removed in numpy 2.5.3) and pulls pandas 1.5.3 | stale | players-on-court reconstruction from pbp | **Reference only** (it also has a bug, §8) |
| 9 | HarryShomer/xG-Model | 4477d4e (2018-05-05) | none declared | old sklearn API | dead | xG features (rebound, angle change, off-wing) | **Reference only** (random 80/20 split) |
| 10 | HarryShomer/Hockey-Scraper | 983ffc4 (2024-06-26) | GPL-3.0 | n/a | README says "no longer maintained" | html shifts/rosters scraper | **Reject** (GPL, unmaintained, and our api-web pipeline already exists) |
| 11 | martineastwood/penaltyblog | 72de651 (2026-10-01) | MIT | **yes**: 1.13.0 wheel installs on 3.12/numpy 2.5.3 | very active; 1 open | Dixon–Coles and bivariate Poisson likelihoods (Cython), `dixon_coles_weights` | **Adapt** (re-implement the ~50-line scipy likelihood; the package's dependency tree is heavy) |
| 12 | kyleskom/NBA-Machine-Learning-Sports-Betting | 8e36b0b (2026-01-08) | none declared | n/a | active-ish | none | **Reject** (cautionary example: same-day stats leakage, and an earlier version cherry-picked the best of 300 random splits) |
| 13 | rd11490/NBA_Tutorials (RAPM) | e5cd532 (2022-09-09) | MIT | trivial (pandas/sklearn) | stale | weighted RidgeCV RAPM on stints | **Reference only** |
| + | xgboost 3.4.1 / lightgbm 4.7.0 (PyPI) | — | Apache-2.0 / MIT | **import fails without libomp** (no Homebrew on this machine) | — | point GBMs | Not needed; if adopted, a libomp install is a new system dependency |
| + | statsmodels 0.15.0 (PyPI) | — | BSD-3 | yes | — | `NegativeBinomial`, `GLM(family=NegativeBinomial)` | **Adopt** if an NB goals model is wanted |

---

## 1. catboost/catboost

- HEAD `eb5a7d1ad5a58d2d60a1c8559b5af6864052b5d2`, 2026-10-02. Apache-2.0. 738 open issues+PRs. Latest PyPI release is 1.2.10 (2026-02-18). I did not reinstall it, because the family was already rejected.
- **Implementation:** `catboost/private/libs/algo_helpers/error_functions.h:280-313`, `TRMSEWithUncertaintyError`. Approx is 2-dimensional: `a0 = mean` and `a1 = log sigma`. The code comment says it "computes ngboost 'natural gradient'". In practice it uses `der0 = w*(y-a0)` and `der1 = w*((y-a0)^2 * exp(-2 a1) - 1)`, with a *diagonal observed* Hessian of `der2[0] = -w` and `der2[1] = -2 w (y-a0)^2 exp(-2a1)`.
- `catboost/private/libs/options/catboost_options.cpp:77-81` forces the defaults `leaf_estimation_method=Newton` and 1 Newton iteration. Lines 980-990 force the `L2` score function for Lossguide.
- Documentation: `catboost/docs/en/concepts/loss-functions-regression.md:85-95` gives the NLL formula. `catboost/docs/en/references/uncertainty.md` covers data versus knowledge uncertainty through virtual ensembles. **I found no documented calibration caveat** (grep for "calibrat" in both files returned nothing).
- **Why its sigma can be badly calibrated (my reading of the code, not a documented claim):** the scale leaf value is the Newton step `der1/der2`. That denominator is proportional to the squared residual and goes to 0 when a leaf's residuals are small, so the steps are noisy and unbounded. NGBoost instead uses the Fisher metric (constant 2 for log-scale, `ngboost/distns/normal.py:21-25`) plus a line search.
- **What would justify a retest:** only a specific hypothesis. One example is "with `leaf_estimation_method='Gradient'`, a small `learning_rate` and chronological early stopping on validation NLL, CatBoost's sigma matches NGBoost's PIT coverage." Without a hypothesis like that, NGBoost covers the same niche with a cleaner optimizer.

## 2. stanfordmlgroup/ngboost

- HEAD `6aadc1fc49311606c9abc808c2a1f100880120c5`, 2026-09-01. Apache-2.0. 50 open issues+PRs. Recent commits are steady: n_jobs (2026-09), a py3.14 fix, a validation_fraction API fix, and the criterion deprecation fix. PyPI 0.5.11 was uploaded 2026-06-26, so it predates the HEAD n_jobs commit.
- **Dependency metadata conflict:** the repo `pyproject.toml:18-19` pins `scikit-learn >=1.6,<1.7` for Python <3.14. The **published 0.5.11 wheel** declares `scikit-learn (>=1.6,<2.0)` (from the installed METADATA). As a result, `uv` resolved **ngboost 0.5.11 + scikit-learn 1.9.1 + numpy 2.5.3 + scipy 1.18.1** with no conflict. Other dependencies it pulls in: lifelines 0.30.0, matplotlib 3.11.2, sympy 1.14.0, tqdm, joblib.
- Files inspected (repo HEAD line numbers):
  - `ngboost/ngboost.py:112`: `self.random_state = check_random_state(random_state)`. This RNG is used only for minibatch and column subsampling (`sample()` at 163-185, `random_state.choice` at 169/178) and for the auto validation split.
  - `ngboost/ngboost.py:396-430`: if `early_stopping_rounds` is set and no `X_val` is passed, it calls **`train_test_split` (a random, non-chronological split)** with `validation_fraction` (405/423). For our use, always pass a chronological `X_val, Y_val`.
  - `ngboost/ngboost.py:492-553`: the boosting loop. It computes the score, computes `grads = D.grad(Y_batch, natural=True)` (502), fits one base learner per parameter (`fit_base`, 206-248, optionally threaded via `n_jobs`), runs `line_search` (250+), and takes a `params -= lr*scale*pred` step. Validation loss is tracked, and the loop breaks once the best loss is older than `early_stopping_rounds` (527-537).
  - **The extra trees fitted after the best iteration are kept.** You must call `pred_dist(X, max_iter=best_val_loss_itr+1)` (pred_param 151-158, pred_dist 662+).
  - `ngboost/scores.py:8-77`: natural gradient = `solve(metric, grad)`, falling back to pinv per sample.
  - `ngboost/distns/normal.py`: the parameters are (loc, log scale). LogScore gradient and Fisher are at lines 11-25 (FI = diag(1/var, 2)). CRPScore is at 28-53.
  - `ngboost/learners.py:4-12`: the default base is `DecisionTreeRegressor(max_depth=3, criterion="squared_error", random_state=None)`.
- **Determinism finding (important):** `random_state=42` alone is **not reproducible**. The default base tree has `random_state=None`, and sklearn trees permute features even when `max_features=None`, so ties break randomly.
  - Probe 1 (`docs/evidence/repo-review/probe_ngboost.out`): two fits with the same seed gave max|Δloc| = 5.05 and max|Δscale| = 1.95.
  - Probe 2 passed `Base=DecisionTreeRegressor(max_depth=3, criterion="squared_error", random_state=seed)` and the difference was **exactly 0.0**.
- **Probe** `docs/evidence/repo-review/probe_ngboost.py` and `docs/evidence/repo-review/probe_ngboost2.py` used synthetic data with 6,000 rows × 20 features. The mean was `3x0 - 2x1 + x2*x3` and the true sigma was `exp(2.3 + 0.35x4 - 0.25x5)`, so mean sigma ≈ 11.2, close to the NBA margin scale. The split was train 0–3999, validation 4000–4499, test 4500–5999.

  | config | fit time | corr(pred σ, true σ) | mean pred σ (true 11.23) | 80% PI coverage | test NLL |
  |---|---|---|---|---|---|
  | 600 iters, lr .03, no early stopping, default Base | 16.7 s | 0.881 (Spearman 0.942) | 9.01 | **0.685** (overconfident) | 3.9216 |
  | seeded Base, early stopping 50 on chronological val (best itr 76), predict at best itr | 3.1 s | 0.938 | 10.73 | **0.775** | 3.7970 |
  | same mean, constant SD from val residuals (12.50) | — | — | 12.50 | 0.838 | 3.9731 |
  | oracle (true μ, σ) | — | — | — | — | 3.7484 |

  Takeaways from the probe:
  - Running without early stopping it overfits sigma. Sigma shrinks, the PIT histogram is U-shaped (`[265,147,…,207]`), and coverage drops to 0.685.
  - With chronological early stopping it tracks the true sigma well (r = 0.94) and beats a constant SD by 0.176 nats per game on this synthetic data.
  - The heteroscedasticity here is strong and built in (log σ spans about ±0.8). **The real NBA gain depends on whether game-level sigma actually varies**, and that is unverified.
- **Pregame knowability / leakage:** the library itself is neutral. The leakage risks are (i) the random auto validation split (405/423) and (ii) predicting with every iteration after early stopping.
- **Verdict: adopt as an experiment** for family (a). Pin `ngboost==0.5.11` and `scikit-learn==1.9.1`. Always pass a seeded `Base`, an explicit chronological `X_val/Y_val`, and `max_iter=best_val_loss_itr+1`. The baseline to beat is the current constant-residual-SD ridge/GBT on validation NLL/CRPS and PIT coverage.

## 3. optuna/optuna

- HEAD `5c8e50d85b77dd5a1fd7e26e21f63debc81d6016`, 2026-09-30. MIT. 19 open. PyPI 5.0.0 (2026-09-07). Dependencies (`pyproject.toml:33-41`): alembic, colorlog, numpy, packaging, sqlalchemy>=1.4.2, tqdm, PyYAML. All installed on 3.12 with numpy 2.5.3.
- Files inspected:
  - `optuna/samplers/_tpe/sampler.py:337-391`: `seed` feeds `LazyRandomState(seed)` and `RandomSampler(seed=seed)` (383-384). `n_startup_trials=10` random trials come first.
  - In 5.0, the internal Parzen `multivariate` flag is hard-set to True (`sampler.py:373-375`, comment "remains only for historical reasons"). `constant_liar=True` is now the default (345); it only matters for parallel workers.
  - `optuna/study/_optimize.py:190-268`: every trial is created with `study.ask()` *before* the objective runs. A `TrialPruned` exception sets PRUNED (213) and any other exception sets FAIL (216). The result is always told back to storage, and exceptions in `catch` are recorded instead of raised.
  - Pruners: `optuna/pruners/` contains median, percentile, successive_halving, hyperband, threshold, patient, wilcoxon and nop.
- **Probe** `docs/evidence/repo-review/probe_optuna.py` ran 40 trials per study with sqlite storage, `TPESampler(seed=123)`, `MedianPruner`, and forced failures on trials 3 and 17.
  - Result: `PRUNED 22, COMPLETE 16, FAIL 2`. **The sqlite `trials` table holds all 40 rows per study across every state**, plus 160 `trial_params` rows (failed trials' params included) and 204 intermediate-value rows.
  - Two runs with the same seed gave an **identical parameter sequence** (sequential, `n_jobs=1`).
- Caveat: determinism holds only for sequential runs. With parallel workers, ordering and constant_liar break it.
- **Verdict: adopt** for a bounded, logged search. Pin `optuna==5.0.0` and use the sqlite storage file as the "every attempted configuration" ledger. Use a fixed `n_trials` and a fixed seed. The objective must be a chronological-validation metric, with test seasons never touched.

## 4. dblackrun/pbpstats

- HEAD `e7ccf2fb6326da630a3cf1665aac964a1e108f4c`, 2024-04-16 (last push 2024-07-03). MIT. 16 open. PyPI 1.3.11 (2024-04-17). Its only runtime dependency is `requests` (`pyproject.toml:34-36`). It installs on 3.12.
- Sources:
  - `pbpstats/__init__.py:1-19` holds hard-coded browser-like `HEADERS` with a Referer of stats.nba.com and a 30 s timeout.
  - `pbpstats/data_loader/stats_nba/web_loader.py:16-30` issues a GET and **writes the response to the file cache before any parse**.
  - The stats endpoint is `https://stats.nba.com/stats/playbyplayv2` (`data_loader/stats_nba/pbp/web.py:28`).
  - The live endpoint is `https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/playbyplay/playbyplay_{gid}.json` (`data_loader/live/pbp/web.py:23`).
  - The schedule comes from `cdn.nba.com` (`live/schedule/web.py:32`).
- Lineups:
  - `resources/enhanced_pbp/start_of_period.py:48` `get_period_starters` infers the starters from the period's events (307+). Its fallback requests `boxscoretraditionalv2` filtered to the start of the period (92-97).
  - `enhanced_pbp_item.py:72-114` `current_players` / `lineup_ids` carry the lineup forward through substitutions.
  - **This is entirely postgame information** (who actually played and when). It is not knowable pregame.
- **Probe** `docs/evidence/repo-review/probe_pbpstats.py` loaded game `0022300001` through the library's own loaders with default headers.
  - The first run returned HTTP 200 on both endpoints, but the save failed (`FileNotFoundError .../pbp_cache/pbp/stats_0022300001.json`) because the cache subdirectory has to exist first.
  - The second run, after `mkdir`:
    - **stats.nba.com playbyplayv2 → `KeyError: 'resultSets'`**. The saved file is 2 bytes (`{}`), so the endpoint answers but returns no data.
    - **Live S3 → OK, 576 events, 0.3 s**, with x/y and `personId` fields.
  - Each endpoint was requested twice in total. No rotation or spoofing beyond the library's defaults.
- **Verdict: reference only.** It is useful as a parser design reference if NBA lineup or possession work (c) is pursued. Its stats.nba.com path is broken for v2 pbp, and all lineup outputs are postgame. They could only feed *lagged* player-impact ratings computed from games strictly before the forecast date.

## 5. sportsdataverse/fastRhockey (R; not executed)

- HEAD `5ce9fdac984a7a1b0d2717acecd4be10dea3909b`, 2026-09-30. MIT (`LICENSE.md`; the GitHub API reports NOASSERTION because of the R-style `LICENSE` stub). 0 open. Version 1.0.0 (`DESCRIPTION:4`).
- Loaders: `R/nhl_loaders.R:87-104` `.nhl_release_loader` reads `https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{tag}/{prefix}_{season}.rds`. Tags include `nhl_pbp_full` (242), `nhl_pbp_lite` (300), `nhl_goalie_boxscores` (697), `nhl_game_rosters` (837) and `nhl_schedules` (750).
- xG:
  - `R/zzz.R:16-19, 41-92` downloads three XGBoost JSON boosters from `raw.githubusercontent.com/sportsdataverse/fastRhockey-nhl-data/main/models/` on load.
  - `R/helpers_nhl.R:1-10` says the models were "trained on NHL PBP data from 2010-2024" and uses era dummies (`R/nhl_xg.R:27-31`).
  - **No training code or evaluation is in this repo**, and the release notes say "no publishing code exists in any public repository."
  - The feature list (`docs/evidence/repo-review/sdv_dl/feats_5v5.txt`, 36 features) has era dummies, distance, angle, rebound, rush, time and distance since the last event, cross-ice, empty net, shot types and last-event dummies.
- Goalie fields: `R/nhl_game_feed.R:73-108` provides home/away goalie on ice, goalie ids and `goalie_in` flags. Lines 592-617 and 656-666 derive `event_goalie_id` from `details.goalieInNetId`, which is the same field available from api-web.nhle.com play-by-play.
- **Leakage note:** the published xG boosters were fit on 2010–2024 shots, so using them to build features for any backtest season in 2010–2024 is in-sample. Our own xG model, refit chronologically, avoids this.
- **Verdict: reference only.** The xG feature list (cross-ice, rush, last-event types) is a checklist for extending our xG model. Do not use their boosters in backtests.

## 6. sportsdataverse/sportsdataverse-data

- HEAD `d15f7693a2d4d2cd9570e619c9d6df4fdea87c3c`, 2026-10-01. MIT (`LICENSE`, covering the R package code). 1 open. The repo itself is an R package. The data live in **377 GitHub releases**, listed through the unauthenticated API because `gh` is not logged in. Raw JSON is in `docs/evidence/repo-review/sdv_releases*.json`.
- Relevant tags, with dates published or last written:
  - `nhl_goalie_boxscores`: 2010–2027, updated 2026-10-01.
  - `nhl_pbp_full`: 2010–2027, 8.8 GB.
  - `nhl_pbp_lite`: 2024–2027.
  - `nhl_game_rosters`, `nhl_shifts`, `nhl_scratches`, `nhl_xg_models`.
  - `espn_nhl_injuries`: only 2026 and 2027.
  - `nba_stats_player_boxscores`, `nba_stats_game_lineups`, `nba_stats_pbp`, `nba_stats_possessions`: 1997–2026.
  - `nba_player_impact`: 1997–2026.
  - `espn_nba_injuries` and `espn_nba_depthcharts`: 2027 only.
- **Probe** `docs/evidence/repo-review/probe_sdv_assets.py`. Downloads all returned HTTP 200 (`docs/evidence/repo-review/sdv_dl/`):
  - `goalie_box_2025.parquet` (78 KB), 5,592 × 24. Columns include `player_id, team_id, home_away, starter, decision, toi, shots_against, saves, goals_against, even_strength_/power_play_/shorthanded_ shots_against ("25/26" strings), save_pctg, game_id, game_date` covering 2024-10-04 → 2025-06-17. **Goalie identity: yes. Shot coordinates: no** (those come from `nhl_pbp_*`, which I did not download because it is 19–42 MB per season). The `starter` flag is the **actual** starter (postgame).
  - `player_boxscores_2025.parquet` (641 KB), 35,114 × 37. Has **`minutes` ("35:56") and `plus_minus_points`: yes**, plus person_id, comment (DNP reasons) and season_type_id 2–6.
  - `nba_player_impact_2025.parquet` (119 KB), 788 × 28. Columns are `o_rapm, d_rapm, rapm, adj_rapm, bpm, war, darko_*` and so on. These are **full-season aggregates**: joining them to games in the same season leaks future games. They are only usable as a prior-season input. The values look odd (for example LeBron James 2025 `rapm=-1.25` while `bpm=7.3`), and the method is undocumented ("scored through the hoopR model stack"; the build code is in another repo I did not inspect).
- Data license/terms: **no data license is stated** in the release notes. The notes say the files are "public, versioned data asset[s]" from upstream sources (`api-web.nhle.com`, stats.nba.com, ESPN). The repo MIT license covers code. Upstream league terms of use still apply.
- **Verdict: adapt.** Use it as a free, keyless historical backfill and cross-check for NHL goalie boxscores (starter identity per game, for training family (b)) and for NBA player minutes and plus-minus. Do not use `nba_player_impact` or any season aggregate with same-season games.

## 7. shufinskiy/nba_data

- HEAD `e829d4678be1e075f99e5d41a1c5f97089be446b`, 2026-07-17. Apache-2.0. 4 open. The data are committed in `datasets/` (1.1 GB in the shallow clone).
- `list_data.txt` lists 58 `nbastats_*`, 60 `nbastatsv3_*`, 50 `pbpstats_*`, 60 `shotdetail_*`, 18 `datanba_*`, 12 `cdnnba_*` and 18 `matchups_*` archives.
- Loaders: `function_for_download/load_nba_data.py:68` reads `list_data.txt`. The R collectors in `loading/utils.R:334-372` hit stats.nba.com `leaguegamelog` and data.nba.com.
- **Probe** `docs/evidence/repo-review/probe_nba_data.py` read the local `pbpstats_2024.tar.xz`: 483,126 × 19 possession rows over 1,230 games. Columns are `GAMEID, GAMEDATE, PERIOD, STARTTIME, ENDTIME, STARTSCOREDIFFERENTIAL, FG2A..., TURNOVERS, EVENTS, DESCRIPTION, URL`. **There are no lineup or player-id columns** in this dump, so lineups would have to come from `nbastats_*` plus a players-on-court reconstruction.
- All of this is postgame data. It is only usable for lagged player-impact ratings.
- **Verdict: adapt (data source only)** for family (c), if it is pursued.

## 8. shufinskiy/nba-on-court

- HEAD `6791ac6264e6f3f7ef061d7812a87d011cf2803b`, 2024-12-20. MIT. 6 open. Dependencies (`pyproject.toml:28-34`): `numpy = "^1.0.0"`, which means <2, plus nba-api and polyleven.
- A `uv` dry-run resolved nba-on-court 0.1.1, but it **downgrades pandas to 1.5.3**.
- `nba_on_court/nba_on_court.py` calls `np.in1d` in 4 places (e.g. lines 63-65). **`np.in1d` does not exist in numpy 2.5.3** (verified: `hasattr(np,'in1d') == False`).
- **Bug:** `nba_on_court.py:56` builds `pl2_id` from `"PLAYER1_ID"` under a PLAYER2 filter, where it should use PLAYER2_ID.
- The players-on-court logic (40-80) is postgame.
- **Verdict: reference only.**

## 9. HarryShomer/xG-Model

- HEAD `4477d4ef8141d55008b9122017b740097e8ade3b`, 2018-05-05. No license file. Data covers 2007–2016.
- `xg_model.py:141` uses **`train_test_split(test_size=.2, random_state=42)`, a random shot-level split** across seasons (within-game and within-season leakage of conditions). GridSearchCV is at 41-113 and log loss/AUC at 152-163.
- `clean_data.py` features: empty net (69-80), angle change for rebounds (83-115), off-wing (284), previous event (307-365, rebound <2 s at 346), and shooter handedness and position (221-253).
- **Verdict: reference only.** It confirms the feature set we already have. Off-wing and handedness are possible additions; don't copy its evaluation.

## 10. HarryShomer/Hockey-Scraper

- HEAD `983ffc4640fa29b62eb9af93fcf753c15b4cf471`, 2024-06-26. **GPL-3.0**. `README.rst:1` reads "This repository is no longer maintained."
- Sources: api-web.nhle.com schedule and pbp (`nhl/json_schedule.py:25`, `nhl/pbp/json_pbp.py:21`), plus html reports for rosters and shifts (`nhl/playing_roster.py:21`, `nhl/pbp/html_pbp.py:45`).
- **Verdict: reject.** It is GPL (copying code would contaminate the project) and unmaintained, and we already pull api-web pbp.

## 11. martineastwood/penaltyblog

- HEAD `72de6519e0c9a357b8b1aa2a6441a454b18ff54e`, 2026-10-01. MIT (`LICENCE`). 1 open. PyPI 1.13.0 (2026-10-01). Heavy dependencies (`pyproject.toml:23-46`): bs4, cython, plotly, pulp, statsbombpy, `wrapper-tls-requests`, `socks` and others. The prebuilt wheel installed cleanly on 3.12 / numpy 2.5.3 / scipy 1.18.1.
- Files:
  - `penaltyblog/models/loss.pyx:87-150` holds `dixon_coles_loss_function`, with the Dixon–Coles τ for 0-0/0-1/1-0/1-1 at 133-141 and time weights applied at 143.
  - `loss.pyx:284-366` holds `compute_bivariate_poisson_loss`. It uses λ3 = exp(correlation) as a constant shared component and sums over k ≤ min(x,y) (Karlis–Ntzoufras).
  - `models/dixon_coles.py:78,127-139` has ρ init −0.1. `models/bivariate_poisson.py:68,297-312` has λ3 = exp(log-param).
  - `models/utils.py:6-26` `dixon_coles_weights(dates, xi=0.0018)` gives exponential time decay.
  - Also present: `negative_binomial.py`, `zero_inf_poisson.py`, `weibull_copula.py`.
  - Team parameterisation is attack/defence plus HFA only, with **no covariate hook**.
- **Probe** `docs/evidence/repo-review/probe_bivariate.py`. My standalone scipy implementation (~50 lines: `nll_bp`, `nll_dc`) ran on synthetic NHL-like data (32 teams, 2,624 games, marginal mean ≈ 2.8, true λ3 = 0.20, HFA 0.05, so corr(yh,ya) = 0.073), cross-checked against penaltyblog 1.13.0:

  | model | λ3 / ρ | HFA | fit time |
  |---|---|---|---|
  | own scipy bivariate Poisson (L-BFGS-B) | λ3 = 0.260 | 0.037 | 2.6 s |
  | penaltyblog BivariatePoissonGoalModel | λ3 = 0.257 | 0.041 | 0.4 s |
  | own scipy Dixon–Coles | ρ = −0.037 | 0.037 | 0.2 s |
  | penaltyblog DixonColesGoalModel | ρ = −0.037 | 0.037 | 0.1 s |

  - The likelihood-ratio statistic versus independent Poisson was 20.56 for BP and 1.54 for DC.
  - My scipy code agrees with penaltyblog. With one season of games, λ3 ≈ 0.2 is detectable, but the estimate is noisy (0.26 against a true 0.20).
  - **DC's τ barely registers when the dependence is a shared additive component spread across all scores.**
  - The real NHL dependence structure is untested here.
- **Verdict: adapt.** Re-implement the BP (and optionally DC) likelihood in scipy with *our* covariate log-link (Elo, xG share, goalie GSAx). Do not add penaltyblog as a dependency: its tree is heavy and it has no covariate hook. Its `loss.pyx` lines above are the reference.

## 12. kyleskom/NBA-Machine-Learning-Sports-Betting (leakage case study)

- HEAD `8e36b0b72ecc5082aa8976dff742cdf910b3aa36`, 2026-01-08 ("Major Overhaul #502"). No license file. 6 open.
- **Current split:** chronological. `src/Train-Models/XGBoost_Model_ML.py:44` sorts by date, and lines 50-63 do a tail split for test and calibration. The NN and LogReg files likewise (`NN_Model_ML.py:51-60`, `Logistic_Regression_ML.py:10` with TimeSeriesSplit).
- **Feature leakage (still present):**
  - `config.toml:1` `data_url` is `leaguedashteamstats?...DateFrom=10/01/{start}&DateTo={month}/{day}/{year}...`.
  - `src/Process-Data/Get_Data.py:56-60, 86-92` fetches with `DateTo = date_pointer` and stores the result as table `date_pointer`.
  - `src/Process-Data/Create_Games.py:139-140` joins each game to the table **for its own game date** (`fetch_team_table(teams_con, date_str)`).
  - When backfilled after the fact, the season-to-date per-game averages therefore include that day's game. stats.nba.com DateTo filters are inclusive; that is my understanding of the endpoint, and I did not re-verify it with a request.
- **History:** at `cf6ad37` (2024-05-02), `XGBoost_Model_ML.py:23-49` looped `for x in range(300): train_test_split(..., test_size=.1)` and saved the model whenever `acc == max(acc_results)`. That is a **random split plus best-of-300 cherry-picked accuracy**, and the accuracy was written into the model filename. The current version still puts test accuracy in the filename (line 224).
- **Verdict: reject.** Nothing reusable. It is a useful negative example for reviewers.

## 13. rd11490/NBA_Tutorials (RAPM)

- HEAD `e5cd532329cb0e78fdf255b0d1cee7f8e1816506`, 2022-09-09. MIT.
- `rapm/rapm.py:13-182` builds a stint-level design (offense/defense player dummies, 85-104) with a target of points per 100 possessions. It fits `RidgeCV` with lambda↔alpha conversion (111-125), weighted by possessions.
- It fits on a whole season, and RidgeCV's default efficient LOO is not chronological.
- **For us:** a pregame-valid version must refit on stints strictly before each forecast date (or at least the prior season). It would also need the injury/availability model to say *who plays*. Building lineup stints requires the postgame pbp from §4 and §7.
- **Verdict: reference only.**

## Additional library probes (scope addition)

### xgboost 3.4.1 / lightgbm 4.7.0
- **Without libomp: both fail to import** on this machine (no Homebrew, `brew: command not found`). The full traceback is in `docs/evidence/repo-review/probe_gbm.out`. The xgboost error reads: `Library not loaded: @rpath/libomp.dylib ... Run 'brew install libomp'`. LightGBM fails the same way (`OSError: dlopen(... lib_lightgbm.dylib) Library not loaded: @rpath/libomp.dylib`).
- As a compatibility check only, `DYLD_LIBRARY_PATH` pointed at the libomp bundled inside the sklearn 1.9.1 wheel (`sklearn/.dylibs/libomp.dylib`); output is in `docs/evidence/repo-review/probe_gbm_with_sklearn_libomp.out`. **This is a test workaround, not a recommended setup** (two OpenMP runtimes can conflict).
  - xgboost: fit 0.25 s, test RMSE 13.046. Same seed gave max|Δ| = 0.0; a different seed gave 9.45 (subsample/colsample 0.8).
  - lightgbm (`deterministic=True, force_row_wise=True`): fit 0.12 s, RMSE 13.057. Same seed gave max|Δ| = 0.0.
- Neither has a native heteroscedastic Normal head. They are point models like our HistGBT.
- **Verdict:** no gain over sklearn HistGBT for us, and adopting them adds a system dependency (libomp).

### statsmodels 0.15.0
- `docs/evidence/repo-review/probe_statsmodels_nb.py` simulated NB data (4,000 rows, true α = 0.05, coefficients [1.05, 0.10, −0.08, 0.05]).
  - `sm.NegativeBinomial`: converged, params [1.057, 0.109, −0.088, 0.038], α = 0.057, 0.02 s.
  - `GLM(family=NegativeBinomial(alpha=.05))`: same coefficients.
  - Poisson GLM: Pearson χ²/df = 1.164.
- Installs and fits fine on 3.12 / numpy 2.5.3 / scipy 1.18.1.
- **Verdict: adopt** if an NB goals model is wanted, though NHL goal overdispersion is typically small.

### Bivariate Poisson / Dixon–Coles implementations found
- penaltyblog is the only inspected repo that implements them: `loss.pyx:87-150` (DC) and `loss.pyx:284-366` (BP). None of the others do.
- The standalone scipy version (`docs/evidence/repo-review/probe_bivariate.py`, functions `nll_bp` and `nll_dc`, ~50 lines including the team parameterisation) reproduces penaltyblog's estimates (table in §11).

## Papers referenced (not fetched or re-read in this session; summarized from prior knowledge)
- **Dixon & Coles (1997)**, JRSS-C 46(2). Independent Poisson with a τ correction on the four lowest scorelines and exponential time decay (ξ). For NHL regulation scores, τ only touches 0-0/1-0/0-1/1-1, which matter less in a sport with ~3 goals per team. Expect a small effect (the probe's LR was 1.54 under a BP truth).
- **Karlis & Ntzoufras (2003)**, The Statistician 52(3). Bivariate Poisson with a shared component λ3, which can only produce **non-negative** correlation. If NHL regulation goal counts turn out to be slightly negatively correlated, BP will collapse to λ3 → 0 and a copula or DC (ρ can take either sign) is needed. Test the empirical correlation on our data first.
- **Duan et al. (2019)**, arXiv:1910.03225 / ICML 2020 (NGBoost). The natural gradient makes the boosting step invariant to the parameterisation. The paper's reported benchmarks are UCI regressions, not sports.

## Notes on runtime and footprint
- About 40 minutes total.
- Scratch footprint is about 6.5 GB, mostly the catboost clone and nba_data's committed archives. The extracted 132 MB CSV and the optuna sqlite file were deleted after the probes.
