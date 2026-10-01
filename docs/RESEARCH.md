# Research: can this NBA model get more accurate with free data? (2026-09-30)

This combines four read-only reviews: published methods, free data sources and their terms, and two rounds of GitHub prior art.
Sources are linked. "[snippet]" means the source was seen only in a search excerpt (fivethirtyeight.com now redirects).
"[unverified]" means it was not confirmed at a primary source. Nothing here is evidence that our model improves. It only decides what is worth testing.

## 1. Bottom line
- **Our Elo's numbers are in the range reported for Elo-class models.** One FiveThirtyEight-style replication reported 67.1% / Brier 0.211
  on 2014-15 (garrrychan.github.io/blog/nba_elo.html). Ours: 67.2% / 0.2105 on 2024-26. These are different games and seasons, so this is
  context, not a matched comparison or evidence of parity. The market favourite wins about **68%** of NBA games (32% underdog win rate since 2021:
  covers.com/guides/which-sport-do-betting-underdogs-win-most-often).
- **Unvalidated working estimate with free data (a research agent's inference from the sources, not a measured result or a ceiling):**
  67–69% accuracy, Brier 0.204–0.208. Only prospective shadow forecasts can show what this project actually achieves. The closing market sits around 0.200–0.207 (hobby walk-forward: github.com/roni-altshuler/nba_predictor [unverified]).
- **Accuracy is noisy:** with about 2,600 games, ±1.8 points is a tie. Improvement must be judged on paired per-game Brier, as our plan already does.
- Unusually high reported accuracy deserves scrutiny of *how* it was measured; leakage must be shown from the method, not assumed from a
  number. One documented example: 72% using first-half stats and 93% using full-game box scores (pmc.ncbi.nlm.nih.gov/articles/PMC11265715).

## 2. What is likely to help (ranked; expected Brier gains are estimates, none measured by the sources)
| # | Idea | Evidence | Expected gain | Free and point-in-time for us? |
|---|---|---|---|---|
| 1 | **Player availability** (who is out × how good they are) | 538 blended about 65% player talent with 35% Elo [snippet]. Markets mostly win on injury news | 0.002–0.006 | **Partly.** Official NBA injury-report PDFs carry the publication time in the file name and go back to at least 2019-12 (status codes checked). Player value must be built by us from past box scores (DARKO history is recomputed by today's model, so it leaks). Large build |
| 2 | **Efficiency/margin model**: rolling net rating from possessions; spread → Φ(spread/σ), σ ≈ 11.6–12.8 | Bookmaker spread residual SD 11.6 (pts-in-the-paint.blogspot.com, 2012). nba-forecast-lab: Elo plus rolling net rating logistic beat its Elo by about 3% Brier (reproduced, but on our spent test season, so context only) | 0.001–0.003 | **Yes**, using the team box parquets (§3) |
| 3 | **Schedule effects**: back-to-back, non-linear rest, eastward time-zone shift, altitude | Away team on a back-to-back about −1.8 pts (Entine and Small); eastward jet lag −1.3 pts (PMC9245584); 538: back-to-back −46 Elo, Denver +47 [snippet]. One project found travel "inside the noise floor" | 0.0005–0.0015 | **Yes**: schedule plus static arena coordinates |
| 4 | **Better season-start prior** (roster continuity instead of a flat 75% carry-over) | Preseason win totals are worth about 56 games of results [snippet] | 0.0005–0.0015, concentrated in Oct–Dec | Partly (needs player data) |
| 5 | **Rolling home advantage, Platt calibration, simple average of models** | 538 used a rolling 10-year home advantage [snippet]; home win rate about 54% recently (Yahoo). Averages add about 0.5 points (nicidob.github.io/nba_elo) | 0.0005–0.001 | Yes |

Notes: 538's classic Elo is K 20, home 100, carry 0.75 toward 1505, margin multiplier (MOV+3)^0.8/(7.5+0.006·diff) (ergosum.co).
Our K 15 / home 50 were chosen on validation 2021-24, not on the test, and fit the lower modern home advantage.

## 3. Data sources (what we may use)
| Source | Has | Terms (quoted in the reviews) | Verdict |
|---|---|---|---|
| ESPN public JSON (current) | schedule, results, box, odds, current injuries | Disney ToU: personal non-commercial only; no automated "data mining or web scraping"; no use to train or validate ML | **Conflicts even with personal automated use.** The user accepted this for personal research (2026-09-30). Minimise requests |
| **sportsdataverse `espn_nba_team_boxscores` release files** (repo sportsdataverse/sportsdataverse-data) | team box 2002–2026 incl. TOV/OREB/FGA/FTA; CSV about 1.2 MB per season | repo license MIT (GitHub API, 2026-09-30); hoopR-nba-data is a different repo (NOASSERTION). The data is ESPN-derived, and no repo license can grant ESPN's rights | **Use for personal research.** About 8 downloads replace about 9,400 ESPN summary calls; that reduces our own requests but does not resolve data rights. Files get rewritten (2021-24 repaired 2026-08-26), so verify the published SHA-256. Drop All-Star rows. 2024-25 check: 1,321/1,321 of our final games covered, FGA and points equal on 2,642/2,642 team rows |
| **Official NBA injury report PDFs** (ak-static.cms.nba.com) | player status per team, timestamped by file name, since ≥2019-12 | nba.com terms: private non-commercial only; no gambling use | **Best free availability source, personal only.** Two file-name formats: `_HHPM` (older) and `_HH_MMPM` (15-min, 2026) |
| stats.nba.com / nba_api, cdn.nba.com | everything | NBA ToU §9: private non-commercial only | Blocked or 403 from this machine |
| Basketball-Reference | everything | forbids use to support ML that predicts | **Do not use** |
| balldontlie | games free; box and injuries $10–40/mo | key required; allows derived commercial use (upstream rights unverified) | The only plausible cheap commercial path |
| Sportradar / SportsDataIO | official data | paid (quote / about $99+/mo) [unverified] | Needed for any commercial product |
| Odds history | SBR archive 2007–2023; Kaggle CC0 set | no timestamps, provenance unverified | Descriptive only; never a "closing line" |

**Commercial reality:** every free source here forbids or does not grant commercial use. Selling requires a licensed feed plus
legal review (NBA §9 excludes gambling uses).

## 4. GitHub prior art
| Repo | Verdict | Why |
|---|---|---|
| seungminnam/nba-forecast-lab | **study only** (no license) | Sound point-in-time features (shift-then-rolling, per season), pipeline scaler, 213/216 tests pass. But the test season was viewed before selection, and its "immutable" registry is append-only by convention only. Metrics reproduced exactly |
| s9b/NBA-Predictor | avoid (no license) | Feature selection, scaler and isotonic fit on all data; full-season stats; today's injuries on past games; rest misaligned |
| zostaff/nba-predictor | avoid | Trained on synthetic games; "market odds" computed from its own Elo |
| dblackrun/pbpstats | tool only (MIT, stale) | Exact possessions, but needs stats.nba.com |
| sportsdataverse hoopR/-py/-data | **use the data releases** | See §3 |
| dckp1000/NBA-96-26-DATASET | avoid | Unattributed copy of shufinskiy/nba_data; ends 2024-25; play-by-play only |
| fivethirtyeight/data nba-elo | benchmark (CC BY 4.0) | Historical Elo to cross-check ours; live feed is dead |

None of the reviewed projects shows a better result under testing comparable to ours. That is not proof that no better system exists.

## 5. Decisions proposed for the accuracy plan
**Build, in order (each step checked, then Codex review):**
1. Step 2 revised: load possession stats from the sportsdataverse team box files (hash-pinned, All-Star rows filtered, matched by
   ESPN game id), **instead of** about 9,400 ESPN summary requests. Coverage is reported per season.
2. Candidate (a) efficiency margin model: σ fitted walk-forward.
3. Candidate (c) logistic v2: the registered features only (elo_diff, net-rating diff, rest_diff, b2b_home, b2b_away, neutral).
   Non-linear rest, time-zone shift, altitude and rolling home advantage are deferred (Codex step 3 review): they need verified venue
   and schedule data and a written definition, as a separately specified candidate.
4. Screen on validation, then seal the at-most-2 shadow candidates before opening night.
5. After sealing: collect official injury PDFs forward (personal research), and assess historical PDFs plus our own player-value
   ratings as a *separate, later* candidate. It cannot enter this season's shadow; it would be a new version reported separately.

**Skip:** gradient boosting / model zoos / big tuning sweeps, isotonic calibration, DARKO historical values (leak), any
Basketball-Reference data, copying code from unlicensed repos, and treating any repo's reported score as evidence.

**Open questions:**
- Resolved: the release repo is MIT. That still doesn't change ESPN's underlying terms.
- Whether the live season continues on ESPN scoreboard calls. The user chose personal research; the requests are kept minimal.
