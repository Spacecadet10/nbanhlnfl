# Soccer goal and corner markets — evidence (2026-11-09 to 2026-11-10, Claude Opus 5.5, session 5f)

**Branch:** `soccer-markets-20261109`, worktree `~/Desktop/nba-predictor-soccer-markets`, based on `39fadb1`. Everything
is uncommitted; review the working-tree diff against `39fadb1`.

**Not done:**
- nothing is deployed, merged, migrated live or sent;
- the live store and ledger were never written. The research store is a backup-API copy of a 2026-11-09 18:30 ET
  snapshot.

**Plan:** `docs/SOCCER_MARKETS_PLAN.md`.
**Protocol:** `backend/app/soccer_markets_protocol.json`.

## Statuses (separate, per the evidence rule)
| Part | IMPLEMENTED | TESTED | INDEPENDENTLY REVIEWED | ACTIVATED |
|---|---|---|---|---|
| Corner counts + DraftKings prices from ESPN scoreboards (migration 0040) | yes | yes (real payload excerpts) | no | no |
| Month history 2010-08…2026-10, 7 club competitions (research store) | yes | 1,365 requests, 0 failed | no | no |
| Goals model (Poisson, sealed `cfe78bf9`) | yes | development, validation and one-shot reserved test: **serve** | no | no |
| Corners model (negative binomial, sealed `f50224dd`) | yes | development (after amendment 2), validation and one-shot test: **serve** | no | no |
| Double chance / draw no bet from the tested 1X2 | yes | validation 2020-22 (not held out) | no | no |
| Live run, API, app panel + history, tickets, Telegram text/PNG, settlement | yes | unit/integration tests; real forecasts on a scratch copy; desktop 1280 / phone 390 | no | no |

## 1. Data
- **ESPN month history** (`soccer history-months --from 201008 --to 202610`, 7 club competitions):
  - 1,365 requests, 0 failed, research store `~/nba-predictor-research-data/soccer-markets-2026-11-09/data`;
  - every final has `wonCorners` back to 2010.
- **Count-only coverage:** `coverage_counts.json` (script `verify/coverage_counts.py`).
  - Leagues have 380/306 regular-season matches per season. Ligue 1 2019-20 has 279 finals (season stopped).
  - Corner rows are near complete everywhere except EPL 2011-12 (207/380).
- **UEFA group-stage slugs:** amendment 1, written from the listing.
  - UCL: `group-stage` 2010-23, `league-phase` 2024-26.
  - UEL: `20xx-first-round` 2010-15, `201617-group-stage`, `group-stage` 2017-23, `league-phase` 2024-26.
- **Bookmaker prices.** The real DraftKings prices on the ESPN scoreboard (3-way, goals total, goal handicap) are stored
  append-only with their retrieval time; `tests/test_soccer_markets_data.py` uses real payloads.
  - Corner prices: none in the source. The prop list was checked for EPL and La Liga matches of 2026-11-21.
  - BTTS, team totals and double chance in ESPN's prop list carry no side, so they are not used.
- **Data defect found in development:** ESPN lists whole runs of history as 0-0 corners. Examples:
  - EPL 2016-17: 363 of 380;
  - EPL 2022-23: 83;
  - Serie A 2022-23: 38.

  The 2026-27 season has none. Handled by amendment 2 (§3).

## 2. Protocol and logs (written before any fit)
`logs/experiments.jsonl` (copy of `~/nba-predictor-research-data/soccer-markets-eval-logs/experiments.jsonl`), in order:
1. `protocol_written` 23:46:07Z, sha256 `403b7814…`;
2. `protocol_amended` 1 at 00:11:49Z, `47f79fba…` (stage rules; before any fit);
3. goals: development 00:11:57–00:13:36Z → `cfe78bf9…`;
4. corners v1: development 00:13:48–00:15:48Z → `62c3b5e5…`;
5. goals test 00:14:14Z (access logged) → `test_completed` 00:14:41Z;
6. `protocol_amended` 2 at 00:22:53Z, `fa64ec96…`, with the user's words; `candidate_retired` `62c3b5e5` untested;
7. corners v2: development → `f50224dd…`;
8. corners test, access 00:25:05Z.

`logs/test_access.jsonl` has exactly one reserved read per family.

**Goals second use.** The 2023-25 seasons were read once before by the 1X2 Elo test (2026-11-08), so this goals test is
a disclosed second use. Corners is a first use.

## 3. Amendment 2 (after the corners v1 development; user's decision)
- **The question to the user:** "Corners data defect … How should I proceed?"
- **The answer:** "Amend, redevelop, then test (Recommended)".
- **The rule:**
  - a both-zero corner count is "not delivered": no input, never scored, and the 90 % season rule applies;
  - it never settles a leg (unscorable);
  - the live completeness check treats it as delivered, so a genuine 0-0 cannot block a family.
- The v1 seal `62c3b5e5` is retired untested. Its report is kept in `develop_corners_v1_retired/`.
- **Goals is unaffected.** The exact code bytes of the goals seal are kept in `code_at_goals_seal/`; their sha256 equal
  the seal's `code` digests.

## 4. Results
### Goals (sealed `cfe78bf9`, Poisson half-life 365 d, tau 3, D 0.1; tau and D on grid edges; Dixon-Coles rho -0.055 not chosen)
- **Validation 2020-22** (n 6,097), joint log loss: Poisson 2.937, Dixon-Coles 2.936, frequency 3.061.
  - Dixon-Coles − Poisson: −0.0008 [−0.0019, +0.0003], so not chosen.
  - Poisson − frequency: −0.124 [−0.136, −0.112].
- **Reserved test 2023-25** (n 6,023): 2.935 vs frequency 3.062; paired −0.127 [−0.140, −0.114].
  - **Reading:** R1 pass; R2 pass for all 4 markets (max overall ECE 0.035); R3 pass for every competition.
  - **Served:** EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL (UEL input only).

| Test, per competition | n | paired diff to frequency [95 %] | max line ECE |
|---|---|---|---|
| eng.1 | 1,140 | −0.121 [−0.154, −0.087] | 0.060 |
| esp.1 | 1,140 | −0.137 [−0.163, −0.111] | 0.057 |
| fra.1 | 917 | −0.109 [−0.147, −0.072] | 0.039 |
| ger.1 | 918 | −0.144 [−0.177, −0.111] | 0.042 |
| ita.1 | 1,140 | −0.131 [−0.159, −0.104] | 0.039 |
| uefa.champions | 384 | −0.156 [−0.205, −0.106] | 0.098 |
| uefa.europa (input only) | 384 | −0.071 [−0.121, −0.022] | 0.091 |

Per market on the test (predicted vs observed, ECE, Brier vs frequency):
- **Team goals:** clearly better than frequency on every line.
- **Match total:**
  - over 2.5: 52.8 % vs 54.5 %, ECE 0.032, Brier better [−0.0085, −0.0026];
  - over 1.5 and 3.5: better;
  - over 0.5 and 4.5: about equal.
- **Both teams to score:** 52.5 % vs 54.8 %, ECE 0.035, **no better than frequency** [−0.0027, +0.0014].
- **Per competition:** the line ECEs reach 0.06 (EPL) and 0.098 (UCL, n 384). R2 is defined on the pooled test.

### Corners (sealed `f50224dd`, negative binomial k 11.28, half-life 180 d, tau 10, D 0.1; all three on grid edges)
- **Validation 2020-22** (n 5,243 after amendment 2), joint log loss: NB 4.649, Poisson 4.723, frequency 4.769.
  - NB − Poisson: −0.075 [−0.088, −0.062].
  - NB − frequency: −0.120 [−0.134, −0.107]. Every competition is below 0.
- **Not scored (under 90 % genuine counts):** eng.1 2011, 2012, 2016, 2022; ger.1 2021; uefa.europa 2011, 2013.
- **Reserved test 2023-25** (n 5,937): 4.671 vs frequency 4.792; paired −0.121 [−0.136, −0.106].
  - **Reading:** R1 pass; R2 pass for all 4 markets (max overall ECE 0.034); R3 pass except UEL (CI to +0.009).
  - **R4 disclosed:** total over 7.5 is 69.0 % predicted vs 72.4 % observed.
  - **Served:** EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL.

| Test, per competition | n | paired diff to frequency [95 %] | max line ECE |
|---|---|---|---|
| eng.1 | 1,118 | −0.160 [−0.200, −0.123] | 0.069 |
| esp.1 | 1,135 | −0.096 [−0.132, −0.058] | 0.053 |
| fra.1 | 903 | −0.085 [−0.119, −0.051] | 0.036 |
| ger.1 | 916 | −0.101 [−0.132, −0.069] | 0.045 |
| ita.1 | 1,099 | −0.151 [−0.188, −0.113] | 0.052 |
| uefa.champions | 383 | −0.202 [−0.257, −0.149] | 0.080 |
| uefa.europa | 383 | −0.043 [−0.091, +0.009] | 0.098 |

Per market on the test:
- **Team corners and handicaps:** better than frequency on every line.
- **Match total:**
  - over 7.5 and 8.5: not better (intervals reach 0). Low totals are under-predicted by 2–3 pp and high totals
    over-predicted. The two team counts are modelled as independent; their negative correlation is ignored, so the total
    is too wide.
- **Disclosed 0-0 rows treated as missing in the test seasons:** ita.1 2023: 36; eng.1 2023: 10, 2024: 12; fra.1 2024:
  10; and others (see `test_corners/test_report.json` `counts`).

### Double chance / draw no bet (frozen 1X2 Elo `7dbc668c`, validation 2020-22, n 6,215; NOT held out)
- **Double chance** (1X, X2, 12):
  - every side beats frequency on Brier;
  - home is over-predicted (1X: 70.4 % vs 67.8 %);
  - its held-out calibration is exactly the 1X2 test's per-outcome calibration.
- **Draw no bet, home:** 60.6 % vs 57.2 % (n 4,679 non-draws), ECE 0.039, Brier 0.198 vs 0.246.

File: `derived_validation_dc_dnb.json`.

## 5. Live path on real data (scratch copy, nothing live)
- **Setup:** a copy of the research store, with ESPN day scoreboards for 2026-11-06…22 fetched into it (7 club
  competitions, 0 failed).
- **First run:** 1X2 and market forecasts were run for every date on or after Nov 20 whose inputs are complete. Nov 21
  and 22 are correctly refused: the Nov 20 matches are not settled yet (`verify/markets_2026-11-21.json`).
- **Recorded, Nov 20:**
  - Athletic Club–Espanyol, goals 1.38–0.86 / corners 6.87–3.30;
  - Troyes–Le Havre, 1.18–1.07 / 5.58–3.28;
  - Bayern–Köln, 3.94–0.82 / 8.20–3.39.
- **Fidelity:** every stored row equals the evaluated frozen model's output for the same inputs and cutoff (identical
  means and joint grids, about 33,000 inputs each; `verify/fidelity.txt`).
- **Bookmaker vs model.** Athletic–Espanyol over 2.5, DraftKings −135:
  - DraftKings implies 53.5 % with the vig removed;
  - the model gives 38.8 %;
  - shown as information under the "no proven skill against the bookmaker" label (`verify/markets_esp1.json`).
- **Browser:** headless Chrome over DevTools, device metrics 1280×900 and 390×844 mobile (`verify/browser2.json`;
  script `verify/markets_browser_check.mjs`).
  - Game day for La Liga and Bundesliga, the soccer History page, and Game day with the markets API blocked.
  - At 390 px, innerWidth = scrollWidth = 390 in all four cases; every expected label is present; 0 JS exceptions;
    0 API errors.
  - Blocked API: "Couldn't load data … Try again" under the Markets heading.
  - Screenshots in `verify/`.

## 6. Checks (exact commands; `NBA_DATA_DIR` = a scratch folder each time)
- `~/nba-predictor-delivery/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_soccer_markets.py
  tests/test_soccer_markets_data.py tests/test_soccer_markets_eval.py tests/test_soccer_markets_live.py`
- The existing ticket/soccer/telegram files: `tests/test_tickets.py tests/test_soccer_live.py tests/test_soccer.py
  tests/test_intl_live.py tests/test_telegram.py tests/test_soccer_forecasts.py`
- **Full suite** (`pytest -q -p no:cacheprovider` in `backend`, 00:32–00:53Z): **2,103 passed / 11 failed**
  (`review/full-suite-before-list-fix.txt`).
  - 8 are the known `tests/test_deep_study.py` failures (torch/keras absent).
  - 3 were the migration-list tests (`test_storage`, `test_live`, `test_nfl_live`), which list every migration by name;
    0040 was added to their expected lists, after which the three files give 57 passed.
- **Final focused run** on the final code (the 4 market files plus `test_tickets`, `test_soccer_live`, `test_soccer`,
  `test_intl_live`, `test_telegram`, `test_soccer_forecasts`, `test_storage`, `test_live`, `test_nfl_live`,
  `test_delivery_release`, `test_freshness`, `test_near_tipoff`): **462 passed** (`review/focused-final.txt`).
- **Not rerun:** the whole suite after the list fix and a settlement-text change.
- **Review patch:** `review/delta.patch` (36 files; new real-payload fixtures listed by hash in
  `review/fixtures.sha256`). Recompute its sha256 after any further change.
- `python3 scripts/check_project.py`: structure guard passed.
- Frontend: `npm run typecheck`, `npm run test:unit` (27 pass) and `npm run build`. Its `node_modules` is a symlink to
  the delivery release's, at the identical lockfile.

## 7. What this does NOT establish
- **No betting edge.** No free historical prices exist for these markets; every market is labelled "no proven skill
  against the bookmaker".
- **No skill on some markets:**
  - BTTS is no better than the base rate;
  - corner totals over 7.5 and 8.5 are not better than frequency.
- **Corner counts** are ESPN's only, with no second source. Historical 0-0 rows are treated as missing.
- **Goals' reserved seasons** were read before by the 1X2 test (disclosed second use).
- **The live store is not migrated, and its history months are not imported.** The hourly cycle change and the reviewer
  skill text (`proposed-skill-soccer-markets.md`) are not applied.
- **No independent review yet.**
