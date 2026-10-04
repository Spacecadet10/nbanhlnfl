# Decision and model-contribution audit — release candidate `rc-20261004`

Claude (Opus 5.5), 2026-10-04. Code: `backend/app/model_audit.py`, `python -m app.cli --sport <nba|nhl> model-audit`
(read-only; writes `data/reports/model-audit-<sport>/report.json`). Run on store copies taken 17:45 UTC
(research definitions NHL `cb194620c0e6a3c6`, NBA `ff7e0e9199ca0cf7`).

**Evidence used, never mixed.** *Historical*: walk-forward forecasts of the live members' own code and settings on seasons
already used to choose the models (NHL 2018–21, 4,877 games; NBA 2022–24, 3,962 games); the stacker is refit as live.
Exploratory, not held-out. *Candidates*: the saved pool screening tables (same seasons; reserved test seasons excluded).
*Deep networks*: the registered deep-v1 study, quoted. *Prospective*: settled two-hour cohorts in the store. Log-loss
differences are paired on the same games with a week-block bootstrap 95% interval; "games to detect" is the sample a test
would need to detect the observed difference (80% power, games treated as independent, so optimistic).

## 1. What the system uses

- **Published probability:** one primary model per market (NHL winner `hockey_elo`, NHL lines `goals_poisson` with the
  empty-net grid; NBA winner `elo`, NBA lines `points_normal`). Nothing else changes it.
- **Research layer, per two-hour forecast:** voting members (NHL winner 4, NBA winner 5 + the injury-aware member,
  lines 3), shadow members that never vote (NHL `hockey_logistic_xg`, NBA weekly refits), the equal average, the
  stacker, 11 NHL / 13 NBA pool candidates (incl. 2 / 3 deep networks), the core selector, the pool selector and the historical
  starting policy. All research; none is the published pick.
- **Live coverage (NHL, last 14 days on the copy):** 19 of 19 eligible games since two-hour recording began have a
  two-hour forecast; no member missing in any cohort; every winner, puck-line and total cohort had a bookmaker quote,
  no 3-way or team-total cohort did. 2 failed `near_tipoff` runs in the window. NBA: no eligible game yet.

## 2. Does combining beat the best single model? (historical, same later games; best single chosen on earlier seasons)

| Market | Average − best single | Stacker − best single | Reading |
|---|---|---|---|
| NHL winner | +0.0007 [−0.0004, +0.0019] | +0.0010 [−0.0001, +0.0020] | no gain; `hockey_logistic` alone is as good |
| NHL 3-way | +0.0024 [−0.0005, +0.0054] | −0.0020 [−0.0042, +0.0001] | no clear gain; stacker beats the average |
| NHL puck line | +0.0007 [−0.0009, +0.0022] | +0.0004 [−0.0007, +0.0015] | no gain (`goals_glm` best) |
| NHL total | **+0.0014 [+0.0002, +0.0026] worse** | −0.0002 [−0.0012, +0.0008] | average hurts (goals_glm overconfident) |
| NHL team total home | +0.0004 [−0.0011, +0.0021] | **+0.0031 [+0.0011, +0.0054] worse** | stacker hurts |
| NHL team total away | −0.0006 [−0.0017, +0.0006] | −0.0005 [−0.0027, +0.0017] | no gain |
| NBA winner | +0.0002 [−0.0018, +0.0021] | **+0.0025 [+0.0007, +0.0043] worse** | no gain; stacker hurts |
| NBA spread | +0.0023 [−0.0006, +0.0052] | +0.0006 [−0.0007, +0.0018] | no gain (`ridge_points` best) |
| NBA total | **−0.0169 [−0.0272, −0.0084] better** | **−0.0239 [−0.0405, −0.0104] better** | combination helps |
| NBA team total home | **−0.0136 [−0.0215, −0.0066]** | **−0.0188 [−0.0311, −0.0084]** | combination helps |
| NBA team total away | **−0.0080 [−0.0128, −0.0037]** | **−0.0107 [−0.0189, −0.0031]** | combination helps |

The NBA total gains are a calibration effect, not new information: at the primary's own model line the primary is ~50/50
by construction, while `ridge_points`/`gbt_points` are overconfident there (mean confidence 0.575/0.592 vs accuracy
0.520/0.522; calibration error 0.055/0.070). Averaging with the ~50/50 primary shrinks them toward a calibrated forecast.

## 3. What each model adds (equal average without it vs with it)

- **NHL winner:** `hockey_elo` and `hockey_logistic` each add (removing worsens log loss by ~0.0005); `goals_poisson` and
  `hockey_gbt` add nothing detectable.
- **NHL lines:** `goals_draw` carries the 3-way market (removing it +0.0045); `goals_poisson` *hurts* the 3-way average
  (removing it −0.0023). Puck line: only `goals_glm` adds; the two Poisson members slightly hurt. Totals: `goals_glm`
  hurts (overconfident, calibration error 0.050).
- **NBA winner:** only `logistic_v2` adds detectably; the others are interchangeable. **NBA lines:** the primary
  `points_normal` is what makes the average work on totals (calibration), `ridge_points` on spreads.
- **Overlap:** forecasts are highly correlated. Effective independent members: NHL winner 1.08 of 4, NBA winner 1.04
  of 5, NHL lines 1.07–1.41 of 3; NBA lines 1.8–2.0 of 3. Winner members disagree on the pick in 4–16% of games. Five
  winner models agreeing is close to one model agreeing with itself: **agreement counts are not independent confirmations.**

## 4. Candidates and deep networks

- **Pool candidates (screening seasons):** on the NHL winner, adding almost any candidate to the 4-member average improves
  it by ~0.0005–0.0011 (incl. the shadow `hockey_logistic_xg`, −0.0011 [−0.0015, −0.0007]); on NHL 3-way, adding
  `multinomial_l2`/`poisson_draw` improves it by ~0.003–0.004. On NBA, no candidate improves the winner average; on NBA
  totals/team totals adding several candidates makes the average **worse** (same overconfidence). These are screening-stage
  comparisons, not the sealed pool routes, and were made on seasons the candidates were screened on.
- **Deep networks (deep-v1):** alone, none beats the best earlier-chosen existing model in any market (NHL winner torch
  +0.0007 [−0.0004, +0.0017], keras +0.0009 [−0.0003, +0.0021]; NBA winner torch +0.0001 [−0.0018, +0.0021]; NBA total
  torch +0.0082 [+0.0017, +0.0149] worse). **Whether a network adds anything inside an average or stack was never
  measured** (no per-game deep forecasts saved on the shared walk). Live, they record forecasts but do not vote, are not
  tracked and are not selectable.

## 5. Probability quality and comparable coverage

Calibration error (ECE, 10 bins) of the primary models is small (NHL winner `hockey_elo` 0.009, NBA winner `elo` 0.015).
At matched coverage (most confident 10% / 25% / 50% of games), the best single model is as good as the average or stacker
on winners (NHL 10%: `hockey_logistic` 0.703 vs average 0.682; NBA 10%: `logistic_v2` 0.811 vs average 0.821). High win
rates on the top 10% reflect confident favourites, not skill beyond the stated probability. Full tables per source in the
report (`sources.<source>.coverage`, `calibration`).

## 6. What evidence is missing

- **Prospective:** 13 settled two-hour NHL cohorts under the latest released definition (3 + 3 under earlier ones), 0 NBA.
  No prospective comparison is possible yet (the audit requires ≥30 paired games). Where history shows no clear difference, a
  difference of the observed size would need ~3,000 to several hundred thousand games to confirm (NHL winner average vs
  best single: ~21,000); the clear NBA total gains needed ~300–1,700. A season is ~1,300 games.
- **Bookmaker:** winner/puck line/total quotes exist for every NHL cohort, but 13 games is far too few to compare.
- **Deep networks inside combinations:** not measured (see §4).
- **Inputs that hurt performance:** not measurable prospectively yet; historical diagnostics found goalie inputs ~0
  importance (NHL) and pace dominating NBA totals (`diagnostics` report).

## 7. Recommended next steps (ordered)

1. **Fix the overconfident line members before combining more models.** NBA `ridge_points`/`gbt_points` and NHL `goals_glm`
   (totals) are miscalibrated at the primary's line; a season-start calibration step fitted on earlier seasons only, as a
   new versioned member, is the cheapest likely gain. Test it on the walk before any live change.
2. **Stop treating agreement counts as independent evidence.** Show the effective number of members next to "4 of 4 agree"
   and prefer the average's probability over vote counts in selection rules.
3. **Prune or reweight:** members that add nothing (NHL `goals_poisson`/`hockey_gbt` in the winner average,
   `goals_poisson` in the 3-way average) could be dropped from averages as a registered new version; the stacker should
   not be used where it is worse (NBA winner, NHL team total home).
4. **Measure the deep networks inside combinations** with the research venv on the shared walk, before keeping them live.
5. **Surface the audit in the app** (Codex item 1): per game and market, primary, contributors, missing members and why the
   selector picked or abstained — extending the existing Game page research card, not a new screen.
