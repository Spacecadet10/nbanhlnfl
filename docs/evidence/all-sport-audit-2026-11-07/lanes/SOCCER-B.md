# SOCCER-B — forecasting/evaluation + S1 source evidence (audit of $SP/snap2, frozen 20:38Z)

SP=/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad
P1=/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/11c8ef97-c1df-493c-b7b0-edeb8138cb0b/scratchpad (raw S1 bytes, read-only)
EV=snap2 docs/evidence/soccer-s1-sources-2026-11-07. My scripts: $SP/work/SOCCER-B/*.py (all run with `python3 -I`, read cached bytes only).

## Part 1 — Soccer forecasts: none exist. Nothing to audit as a model.

Searches run (snap2 unless stated):
- `grep -ril soccer backend/app` → cli.py, devpaths.py, freshness.py, main.py, migrations/0034, 0035, preview_publish.py, schemas.py, soccer.py, sources/espn.py, sports.py. No model, ledger, market or job file.
- `grep -ilw -E "soccer|uefa|premier|eng\.1|uefa\.champions|draw_prob|p_draw" models/* adaptive*.py combine.py markets.py ledger.py telegram*.py jobs.py` → no hits. (A broader `football|epl` grep hit only false positives such as "replay"/NFL.)
- `grep -ril -E "openfootball|football-data\.org|wikipedia" backend frontend/src scripts` → no hits (no S2 importer).
- main.py:238-288: `/api/soccer/{meta,matches,matches/{id}}` read-only via `db.connect_readonly("soccer")`; comment says never `get_provider` or a forecast path. sports.py:10 `SPORT_IDS = ("nba","nhl")`; sports.py:63 soccer in `DATA_ONLY`; `sports.get()` raises for it.
- soccer.py:13 "There is no soccer model"; soccer.py:276-282 capability rows say forecasts/performance/history/research unavailable.
- frontend/src/pages/SoccerPages.tsx:13,71,155,256,318-328: only "not available" text; no probability rendered.
- Launchd: `grep -il soccer REPO/scripts/launchd/* snap2/scripts/launchd/* ~/Library/LaunchAgents/*` → no hits (11 plists, none soccer).
- Release ~/nba-predictor-live (HEAD a55c874): `grep -ril soccer backend/app frontend/src scripts` → no hits; no soccer store in its data/.
- Store copy $SP/stores/soccer.sqlite: predictions, game_forecasts, market_forecasts, model_fits, candidate/adaptive/recovered/near_tipoff forecasts, adaptive_fits, odds_quotes, market_quotes all 0 rows. soccer_matches 203, soccer_result_revisions 171, raw_responses 119.

SOCCER_PLAN.md S2–S6 status vs reality: S2 "proposal (not started)" (SOCCER_PLAN.md:57) and REPORT §F "not started" are true: no openfootball/football-data.org/Wikipedia code, no new migration beyond 0034/0035. S3 preregistration: none exists. `grep -i prereg|seal|digest` over docs/*.md finds only the S3 heading (SOCCER_PLAN.md:78) and a generic line in ACCURACY_PLAN.md:117. No sealed soccer file in models/ (models/ holds only NBA/NHL/NFL json). S4–S6: no baselines, calibration, sealed method or soccer forecast ledger. The plan does not claim otherwise.

## Findings (most severe first)

1. MEDIUM — The 2 extra-time matches ESPN labels FULL_TIME have no independent check, and one only gives its 90-minute score after duplicates are removed. Files: EV/REPORT.md finding 3; SOCCER_PLAN.md:50-51. Evidence (`c_ninety.py`): 169930 Juventus–Real Madrid 2005-03-09 has final 2-0, raw goal details 76',76',117',117', 2 byte-identical duplicates. After dropping them the ESPN 90-minute score is 1-0. 416637 Ajax–Dnipro 2015-03-19 has final 2-1 and an ESPN 90-minute score of 1-0. Neither has a Wikipedia page cached: the 4 fetched pages are 2024-25 UCL KO, 2024-25 UEL KO, 2014-15 UCL KO and the 2005 final. So these are ESPN-only (internal). They are exactly the cases where the status label is wrong, so they matter most for any future "90-minute = grading target" rule. The REPORT already says they "were not compared" (not new as a gap). New point: 169930's derived score depends on a de-duplication rule. Proposed fix: S3 should treat ET matches without second-source agreement as ungradable, as the plan states. Record that de-duplication changes 169930's 90-minute score.
2. MEDIUM — recheck/wiki_et.py hardcodes the ESPN side of the "independent" agreement. Its TARGETS tuple (6th field, "ESPN 90-min from S1 count_output") is a constant, so the script does not re-derive the ESPN value it reports "AGREE" against. I re-derived the 8 values myself from scoreboard details (non-shoot-out scoring plays, base minute ≤ 90) and from summary keyEvents (period ≤ 2) where cached. All 8 agree with Wikipedia (output below), so the claim holds, but the evidence script alone does not prove it. Fix: have wiki_et.py compute the ESPN 90-minute score from the cached scoreboard. NEW.
3. MEDIUM — The independence of the openfootball checks is not established. EPL ESPN vs openfootball agreement (412 and 380) is between two publications. openfootball states no upstream source, and CC0 §4(c) says the affirmer "disclaims responsibility for clearing rights of other persons". If openfootball were transcribed from ESPN, the agreement would be circular, and the CC0 grant would not settle upstream rights. The REPORT labels Wikipedia's provenance as "not established" but does not say the same of openfootball in finding 4 or §A. Fix: label the EPL comparisons "agreement of two publications; provenance independence unknown". NEW (wording).
4. LOW — No UEFA source was assessed. probes.jsonl, recheck/probes.jsonl and licence_review_requests.jsonl contain no uefa.com host. UCL/UEL results and ET/penalty facts rest on ESPN, openfootball and Wikipedia only. The REPORT's source list does not mention UEFA, so this is a scope gap, not a false claim. NEW.
5. LOW — The raw evidence is temporary. All counts depend on session scratchpads P1/s1-downloads (115), s1-recheck-downloads (76) and s1-docreview-downloads (21). All are present and hash-verified now, but they are not committed. The REPORT acknowledges this (G1); listed so the lead knows reproducibility expires with that scratchpad. Not new.
6. LOW — In count_output.txt §1, "season label" for espn_eng1_dates_2023_limit1000.json is "2026-27 English Premier League". That is the response's leagues[].season (the current season), not the events' season; events carry season.year 2023. It does not affect the counts. The importer (sources/espn.py:636) uses the event season, not the league label. NEW, hygiene only.
7. LOW (compliance, already recorded) — football-data.co.uk received 10 requests (9 in S1 + 1 robots.txt in the recheck) although its robots.txt has `Disallow: /` for Anthropic-AI, Claude-Web and ClaudeBot (verified in cached fd_robots.txt). Wikimedia calls (5 API + 7 policy pages) used the default curl User-Agent. The REPORT records both. For the same reason I did not re-fetch football-data.co.uk or Wikimedia.

No CRITICAL or HIGH finding. Every S1 count I recomputed matched, and no soccer forecast is served or stored anywhere.

## Part 2 — S1 evidence recomputation

a. Probes and hashes (`a_hashes.py`, `a_lic.py`):
- S1: 115 rows, 115 files, 115 sha256+size match, 0 missing, 27,382,716 bytes, 18:20:15Z–18:31:04Z. Status {200:106, 400:1, 403:3, 502:5}. Hosts: ESPN site.api 65, football-data.co.uk 9, GitHub 18 (api 7 + raw 11), ClubElo 6, docs/terms 17. Matches REPORT.
- Recheck: 76 rows, 76 match, 9,277,481 bytes, status {200:68, 403:5, 404:2, 502:1}. ESPN data requests: 1 (dates=2024&limit=1000) plus 3 ESPN-domain terms/robots requests. Wikimedia: 4 en.wikipedia + 1 wikidata = 5. Matches REPORT.
- Licence review: 21 rows, 21 match (5 openfootball LICENSE + README, 7 Wikimedia, 9 Betfair = robots + home (403) + search + 6 KB pages). The REPORT says "Betfair ×8 … 5 FAQ articles". The cached set has 6 KB article pages plus the search page (betfair_kb_access, _dataspec, _download, _jurisdictions, _what_data, plus the devsupport search), so the jsonl shows 9 Betfair rows. Minor count wording: 5 + 7 + 9 = 21 is consistent with the file; "Betfair ×8" is not.
- Re-running count.py, count_recheck.py, season_compare.py and wiki_et.py gives exit 0 and byte-identical outputs (cmp).

b. EPL comparisons (`b_epl.py`, my own pairing by (home, away) within the season, " FC" stripped):
- Calendar year 2023: 412 ESPN events (all STATUS_FULL_TIME, period 2, unique ids, goals in details = FT on 412/412) vs 412 openfootball rows dated 2023. Split 216 (2022-23) + 196 (2023-24). Paired 216/216 and 196/196, FT equal 412/412, same calendar date 412/412.
- Season 2023-24: ESPN CY2023 + recheck CY2024 events dated in the season = 380 vs openfootball 380. Paired 380, FT equal 380, date equal 380, 0 unmatched.
- Fields compared by the evidence scripts: team pair, calendar date and FT score only. I also compared HT, from ESPN goal-detail minutes ≤ 45'+x vs openfootball score.ht. Equal on 206/206 (2022-23 part), 192/192 (2023-24 part) and 369/369 (full season). The gaps are openfootball rows with no ht (23 and 11 per season file). Kickoff time was not compared for equality; only the offset (+1 h for 184, 0 h for 196) is reported.
- Side note: espn_eng1_dates_2024.json without limit returned 100 events (2024-01-01..2024-04-02), confirming silent truncation without &limit=1000.

c. Extra time / penalties in the 62 sampled knockout-month events (UCL 2005-03, 2005-05, 2015-03, 2025-03, 2025-05; UEL 2015-03, 2025-04). Status names: FULL_TIME 54, PEN 5, AET 3. **10 went to ET/penalties** (strong evidence = period ≥ 3, shoot-out fields, or minute > 105):

| id | match (ESPN) | status | period | clock | goal minutes (scoring details) | ESPN 90' (details / summary) | Wikipedia 90' |
|---|---|---|---|---|---|---|---|
| 169930 | Juventus 2-0 Real Madrid 2005-03-09 | FULL_TIME | 4 | 122' | 76',76',117',117' (dupes) | 1-0 / – | not fetched |
| 183634 | Milan 3-3 Liverpool 2005-05-25 | PEN (2-3) | 5 | 127' | 1',39',44',54',56',61' + 5 shoot-out | 3-3 / 3-3 | 3-3 |
| 410544 | Chelsea 2-2 PSG 2015-03-11 | AET | 4 | 120'+3' | 81',86',96',114' | 1-1 / 1-1 | 1-1 |
| 410540 | Atlético 1-0 Leverkusen 2015-03-17 | PEN (3-2) | 5 | 120' | 27' + 5 shoot-out | 1-0 / – | 1-0 |
| 416637 | Ajax 2-1 Dnipro 2015-03-19 (UEL) | FULL_TIME | 4 | 120'+4' | 60',97',117' | 1-0 / – | not fetched |
| 733605 | Liverpool 0-1 PSG 2025-03-11 | PEN (1-4) | 5 | 120' | 12' + 5 shoot-out | 0-1 / 0-1 | 0-1 |
| 733606 | Atlético 1-0 Real Madrid 2025-03-12 | PEN (2-4) | 5 | 120' | 1' + 6 shoot-out | 1-0 / 1-0 | 1-0 |
| 733619 | Inter 4-3 Barcelona 2025-05-06 | AET | 4 | 120'+3' | 21',45'+1',54',60',87',90'+3',99' | 3-3 / – | 3-3 |
| 733642 | Lazio 3-1 Bodø/Glimt 2025-04-17 (UEL) | PEN (2-3) | 5 | 120' | 21',90'+3',99',109' + 5 shoot-out | 2-0 / – | 2-0 |
| 734499 | Man Utd 5-4 Lyon 2025-04-17 (UEL) | AET | 4 | 120'+8' | 10',45'+1',71',77',104',109',114',120',120'+1' | 2-2 / 2-2 | 2-2 |

So the REPORT's current "10 went to extra time (8 labelled AET/PEN + 2 FULL_TIME)" is verified. The older "8 of 62" figure undercounted by exactly the 2 FULL_TIME cases. A naive "clock > 90" rule would wrongly add 9 more KO events: 2005-format clocks 91'–96' with period 2, e.g. 182820 Liverpool 1-0 Chelsea at 96'. Across all 2,125 cached finals:
- only 169930 and 416637 are FULL_TIME with period > 2;
- 10 AET labels have no ET evidence (period 2, clock 0'): esp.1 ×5 (20644, 20647, 20639, 20642, 21181), fra.1 21318, ger.1 21064, ita.1 20628 and 20635, UCL group 21220;
- 18 finals show period 1 (esp.1 Oct 2005 16, UCL Oct 2005 2);
- scoreboard odds are `[null]` on 2,125/2,125.

All of this matches the REPORT. Extra check: the frozen snap2 classifier (`parse_soccer_scoreboard`, run by `c_s0classify.py` on cached bytes) gives regulation 2105, unknown 12, penalties 5, extra_time 3. The 12 unknowns are exactly the 10 false AET labels plus 169930 and 416637. The 8 genuine AET/PEN keep their label, and 182820/169928 stay regulation.

d. Independence labels:
- EPL CY2023 412 and season 2023-24 380: two publications agree. Independent publication, provenance independence unknown (Finding 3).
- 90-minute score after ET, ESPN scoreboard details vs summary keyEvents/linescores: internal consistency only (one provider).
- 8 AET/PEN vs Wikipedia goal minutes: independent publication. Wikipedia's provenance is not established, as the REPORT says.
- 169930 and 416637 ET detection and 90-minute score: ESPN-only, internal.
- 10 false AET labels: ESPN-only. "False" rests on competition structure (league and group matches have no ET), which is interpretation, though strong.
- openfootball UCL/UEL counts (125/119/189; 205/141/141/141/189): header vs line count inside openfootball. Internal consistency.
- openfootball a.e.t. incompleteness (Inter–Barcelona 4-3 with no a.e.t.): cross-source (ESPN 733619 AET plus Wikipedia aet=y).
- ESPN team-id stability and the 285 ids: internal.
- Kickoff offset read as "UK local": interpretation.

e. Licences and terms. Each source is assessed on three separate axes: public access, zero cost, permitted use. A manual download is not a workaround for any restriction, and the results-only path is assessed without odds.

| Source | Public / zero cost | Permitted use | Basis | Verified from |
|---|---|---|---|---|
| openfootball (7 repos) | public, free, no key | CC0: waiver "for any purpose whatsoever, including without limitation commercial" | explicit grant; upstream rights unknown (§4(c)) | 7 cached LICENSE.md with identical sha 36ffd9dc085d529a…; GitHub API spdx CC0-1.0; football.json README "Use as you please with no restrictions whatsoever." Primary page re-fetched: raw.githubusercontent.com/openfootball/england/master/LICENSE.md, 2026-11-07 ~20:45Z, same clauses |
| ESPN site API | public, undocumented, free | Disney ToU (Last Updated May 24, 2024) covers ESPN-branded products: "for your personal, noncommercial use only"; bars "training, testing, benchmarking or validation of any artificial intelligence"; bars access "using a robot, spider, script, or other automated means" | explicit restriction; applying it to the undocumented API is interpretation (ESPN is named among the covered brands) | cached doc_disney_terms.html. Primary page re-fetched: https://disneytermsofuse.com/english/, 2026-11-07 ~20:44Z, same wording. The cached ESPN devcenter terms page is "Page not found"; site.api robots.txt returns Access Denied |
| football-data.co.uk | public, free | "private individuals only, NOT commerical or data training products using automated bots/scrapers/AI"; robots `Disallow: /` for ClaudeBot | explicit restriction | cached fd_data.php, fd_robots.txt (not re-fetched: robots disallow) |
| StatsBomb open data | public on GitHub, free, registration requested | §1.2.1 may not "distribute, reproduce, sell or in any way provide the data"; §1.2.2 may not "commercially exploit the data" | explicit restriction | cached hudl_LICENSE.pdf sha a5462e69…; text "last updated 8 September 2023" |
| Betfair Historical Data | site 403 from region CA; KB: "for purchase & download to registered Betfair customers"; data from April 2015 | terms not read | unknown | cached Betfair KB pages. Whether any tier is free was not found in the cached pages, so it is unverified |
| UEFA (uefa.com) | not probed | not read | unknown | — |
| football-data.org | free plan ("Free Plan is Free Forever", "Scores delayed", 10 calls/min); account and key | T&C (June 1, 2018) §2.3 single Application; §6 no credentials in open-source repos; §7.1 attribution; §9.1 no reference to the data after cancellation | explicit conditions; no ML clause found (absence ≠ permission) | cached fdorg_register.html. The cached fdorg_terms.html is a 404 page; the T&C text is in the register page |
| Others (FBref/SR, Transfermarkt, OddsPortal, The Odds API, Sportmonks) | as in REPORT | quotes found verbatim in cached copies | explicit | e_grep.py on cached files |

Scope actually searched: the REPORT's 11 odds sources and results/terms pages above, 212 cached requests in total. No UEFA source, Understat terms, ClubElo terms or API-Football terms were read. Results-only path, judged on its own: only openfootball is free and explicitly permitted; that has no stats or odds, and UEL covers only 2020-21..2024-25. ESPN is explicitly restricted, which also concerns the existing S0, NBA and NFL ESPN collection (G7, HANDOFF:79).

## Claim table
| Claim (source) | Status | Evidence |
|---|---|---|
| "115 (ESPN site.api 65; football-data.co.uk 9; GitHub 18; ClubElo 6; docs/terms 17)" REPORT evidence table | verified | a_hashes.py: 115/115 sha match |
| Recheck "76 (status 200:68, 403:5, 404:2, 502:1)" | verified | a_hashes.py |
| Licence review "21 … Betfair ×8 … 5 FAQ articles" | partly contradicted (count wording) | jsonl has 9 Betfair rows (robots, home, search, 6 KB pages); total 21 correct |
| Count scripts byte-identical on re-run | verified | cmp on 4 outputs |
| "calendar year 2023 EPL … 216 + 196 = 412 … all scores equal" REPORT finding 4 | verified (FT + date; HT also equal where present) | b_epl.py |
| "season 2023-24 EPL = 380/380 with the same dates and scores" | verified | b_epl.py, season_compare rerun |
| "62 events … 10 went to extra time … 8 labelled AET/PEN, 2 STATUS_FULL_TIME (169930, 416637)" | verified | c_et.py, c_ninety.py |
| "10 false STATUS_FINAL_AET labels … period 2 / clock 0'" | verified | c_et.py |
| "Across all 2,125 cached S1 finals those 2 are the only FULL_TIME events with period > 2" | verified | c_et.py |
| "18 Oct 2005 finals show period 1" | verified | 16 esp.1 + 2 UCL |
| "Wikipedia goal minutes give the same 90-minute score for those 8" | verified (ESPN side re-derived independently) | c_ninety.py; see Finding 2 |
| "odds null on 2,125/2,125 sampled finals" | verified (`[null]`) | inline check |
| openfootball CC0 "identical sha256 36ffd9dc… in all 7" | verified | shasum ×7 |
| Disney ToU clauses (REPORT §C) | verified (cache + primary page) | e_grep.py; WebFetch |
| football-data.co.uk clause + robots disallow | verified (cache) | e_grep.py, fd_robots.txt |
| StatsBomb 1.2.1/1.2.2/§7, 8 Sept 2023 | verified (cache) | pdftxt/sb_license.txt |
| football-data.org 2.3/6.1/7.1/9.1, June 1 2018 | verified (cache, in fdorg_register.html) | e_grep.py |
| "S2 proposal (not started)" SOCCER_PLAN.md:57 | verified | no importer/migration/code |
| "There is no soccer model" soccer.py:13; UI "no validated model exists" | verified | Part 1 searches, empty forecast tables |
| SOCCER_PLAN line 15 "2 extra-time matches labelled FULL_TIME, 10 false AET labels" | verified | c_et.py |

## Checks run
| Command | Expected | Actual | Result |
|---|---|---|---|
| `python3 -I work/SOCCER-B/a_hashes.py s1=EV/probes.jsonl recheck=EV/recheck/probes.jsonl` | 115 and 76 match | 115/115, 76/76, 0 missing | pass |
| `python3 -I work/SOCCER-B/a_lic.py EV/licence_review_requests.jsonl P1/s1-docreview-downloads` | 21 match | 21/21 | pass |
| `python3 -I EV/count.py P1/s1-downloads` + cmp count_output.txt | identical | exit 0, IDENTICAL | pass |
| `python3 -I EV/recheck/{count_recheck,season_compare,wiki_et}.py …` + cmp | identical | exit 0, IDENTICAL ×3 | pass |
| `python3 -I work/SOCCER-B/b_epl.py P1/s1-downloads P1/s1-recheck-downloads` | 412 (216+196) and 380 equal | as expected; HT 206/206, 192/192, 369/369 | pass |
| `KO=… python3 -I work/SOCCER-B/c_et.py P1/s1-downloads` | 10 ET of 62 | 10 strong-evidence ET (19 under naive clock > 90, explained above) | pass |
| `python3 -I work/SOCCER-B/c_ninety.py P1/s1-downloads P1/s1-recheck-downloads` | 8 agree with Wikipedia | 8 AGREE, 2 not fetched | pass |
| `PYTHONPATH=snap2/backend backend/.venv/bin/python c_s0classify.py` (via exec) on cached scoreboards | 12 unknown = 10 false AET + 2 hidden ET | regulation 2105, unknown 12, pen 5, aet 3 | pass |
| `python3 -I work/SOCCER-B/e_grep.py <cached doc> <phrases>` | quoted clauses present | all present (fdorg clauses in register page, not terms page) | pass |
| WebFetch disneytermsofuse.com/english/ and the openfootball england LICENSE.md | same wording | same | pass |
| Part 1 greps (listed above) and forecast-table counts on soccer.sqlite copy | no soccer model | none; all forecast tables 0 rows | pass |

Not run: no re-scrape of any data source (by brief). No fetch of football-data.co.uk (its robots.txt disallows ClaudeBot) or Wikimedia (default UA would breach its UA policy). No fetch of Betfair (geo-blocked) or uefa.com (outside cached scope; reported as a gap). No pytest (not needed for this lane). No HTML/PDF re-extraction of StatsBomb (used existing pdftxt, cross-checked by phrase).

## Open questions / cross-sport
- The soccer store copy carries the full NBA/NHL forecast schema (predictions, game_forecasts, model_fits …, all empty). The guard against a model writing there is code-level (`sports.get` raises, soccer not in SPORT_IDS), not schema-level. SOCCER-A/C may want to confirm that no `db.connect(sport="soccer")` caller reaches forecast writers.
- The ESPN/Disney restriction applies to the existing NBA/NFL (and NHL?) ESPN pipelines too (HANDOFF:79, G7). That is a cross-sport decision for the user, not chased here.
- Before S3 relies on Betfair, Understat or uefa.com, their terms need reading: unassessed.
