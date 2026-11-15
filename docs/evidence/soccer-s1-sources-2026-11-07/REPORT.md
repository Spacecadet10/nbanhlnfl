# S1 — Soccer source assessment (2026-11-07; rewritten after the independent audit)

Scope: historical results, statistics and odds for EPL (ESPN eng.1), La Liga (esp.1), Bundesliga (ger.1), Serie A (ita.1),
Ligue 1 (fra.1), UEFA Champions League (uefa.champions) and UEFA Europa League (uefa.europa). Target of the eventual model:
the 90-minute result (1X2). Project rule: every source must be **free** (no paid plans, trials, purchases) **and clearly
permitted** for this use; free API keys only after the user agrees to create an account; odds optional; no
manual-download workaround for usage restrictions. No legal determinations are made: explicit clauses are quoted;
interpretation is labelled.

Labels: **[DL]** counted from bytes fetched here (reproduced by the scripts below); **[DOC]** provider's own page (URL,
UTC access time); **[UNV]** unverified. **Internal consistency** = two derivations from the same ESPN payload;
**independent** = agreement with a separately published source.

## Evidence and reproducibility
| Round | Requests | Bytes | Log | Count scripts (all exit 0, 0 hash mismatches, output byte-identical when re-run 2026-11-07 by the lead ~19:42 UTC and by the document reviewer ~20:00–20:10 UTC) |
|---|---|---|---|---|
| S1 probes 18:20–18:31 UTC | 115 (ESPN site.api 65; football-data.co.uk 9; GitHub 18; ClubElo 6; docs/terms 17) | 27,382,716 | probes.jsonl | `python3 -I count.py <s1-downloads>` → count_output.txt |
| Recheck (requests 19:29:16–19:38:21 UTC) | 76 (status 200:68, 403:5, 404:2, 502:1) | 9,277,481 | recheck/probes.jsonl | `python3 -I recheck/count_recheck.py <s1-downloads> <s1-recheck-downloads>`; `recheck/season_compare.py`; `recheck/wiki_et.py` |
| Licence review 20:11:32–20:15:12 UTC | 21 (openfootball LICENSE ×5 + README; Wikimedia policy pages ×7; Betfair ×8: robots.txt and home page 403, developer-support search + 5 FAQ articles 200) | — | licence_review_requests.jsonl (url, time, status, bytes, sha256) | documents only |

Raw bytes are in the session scratchpad (`…/scratchpad/s1-downloads/` 115 files, 26 MB; `…/s1-recheck-downloads/` 76
files, 9.0 MB; `…/s1-docreview-downloads/` 21 files), present and hash-verified on 2026-11-07 ~20:00–20:20 UTC. They are
**temporary and not committed** (third-party content; ESPN terms below). Once deleted, the counts are verifiable only by
re-downloading changing live sources: they are then no longer reproducible from cache.

**Compliance record.** The S1 probes made 9 football-data.co.uk requests (robots.txt + 8 pages, 18:25:18–18:25:41) although
its robots.txt disallows ClaudeBot / Claude-Web / Anthropic-AI; the recheck added 1 robots.txt request (10 in total). These
should not have happened; the source is excluded and no CSV was fetched. The recheck's 5 Wikimedia API calls (and the
licence review's 7 Wikimedia page fetches) used a default curl User-Agent, which does not meet Wikimedia's "meaningful
User-Agent" requirement (section C). The recheck made 1 new ESPN data request (EPL `dates=2024&limit=1000`) and 3 ESPN-domain
terms/robots requests; no ESPN data was collected after that.

## Findings
1. **Only openfootball (CC0) is both free and clearly licensed among the results sources checked.** It has no statistics
   and no odds. ESPN, football-data.co.uk, FBref/Sports Reference, Transfermarkt, OddsPortal and StatsBomb carry explicit
   restrictions (section C).
2. **ESPN's status name does not establish how a match ended** [DL]. In the 62 events of the sampled knockout months,
   **10 went to extra time** by period/clock/event-minute evidence: 8 are labelled AET/PEN (183634, 410544, 410540, 733605,
   733606, 733619, 733642, 734499) and **2 are labelled STATUS_FULL_TIME** — 169930 Juventus 2-0 Real Madrid 2005-03-09
   (period 4, clock 122', goals listed twice 76', 117') and 416637 Ajax 2-1 Dnipro 2015-03-19 UEL (period 4, clock 120'+4',
   goals 60', 97', 117'). In the other direction, the Oct 2001 samples carry **10 false STATUS_FINAL_AET** labels (9 league
   matches + UCL group match 21220), all period 2 / clock 0'. Across all 2,125 cached S1 finals those 2 are the only
   FULL_TIME events with period > 2. Label defects are not confined to early seasons (416637 is from 2015); no period is claimed clean.
3. **A 90-minute score after extra time is derivable from ESPN goal minutes** [DL] ("90'+3'" stoppage vs "99'" extra time),
   but only once extra time has been detected from period/clock/event minutes, never from the status name. For the 8
   ESPN-labelled AET/PEN matches the scoreboard goal minutes give one 90-minute score; for the 5 with a fetched summary
   (183634, 410544, 733605, 733606, 734499) the keyEvents with period ≤ 2 agree, and linescores agree where present
   (**internal consistency only**; linescores are corrupt in 2015 410544 — negative ET periods — and absent in 2005). **Independent**
   check: Wikipedia goal minutes give the same 90-minute score for those 8 (recheck/wiki_et_output.txt); the 2
   FULL_TIME-labelled ET matches were not compared. Wikipedia's provenance independence from ESPN/UEFA is not established
   (interpretation).
4. **ESPN coverage** [DL]: month queries (`dates=YYYYMM`) work; day ranges → HTTP 400; a year query needs `&limit=1000`.
   Non-empty from 2001-02 (eng.1, UCL), 2000-01 (esp.1), 2009-10 (UEL); earlier floors for the other leagues not probed.
   Sample defects: false AET/FULL_TIME labels (above); postponed placeholders scored "0-0" (14 in EPL Oct 2005, 16 in Ligue 1
   Oct 2005); goal details duplicated or not adding up in 2001–2005; one Ligue 1 Oct 2010 final without goal details.
   Complete-period checks: **calendar year 2023 EPL** (halves of two seasons: 216 matches of 2022-23 + 196 of 2023-24) =
   412 = openfootball's 412, all scores equal; **season 2023-24 EPL** = 380/380 with the same dates and scores. Every other
   cell is a one-month sample.
5. **Odds**: none of the 11 sources checked was **verified** to offer free, permitted historical odds (ESPN,
   football-data.co.uk, football-data.org, The Odds API, Sportmonks, API-Football [unassessed: 403], OddsPortal,
   openfootball, StatsBomb, engsoccerdata, FiveThirtyEight); Betfair Historical Data is unassessed (section C). The Odds
   API's free tier is forward-only (account + key). Missing odds do not block results-only model development.

## A. Coverage (one-month samples unless stated)
ESPN scoreboard, finals per sampled month [DL]; `+Np` postponed placeholder rows (to exclude); `!` defect.

| comp | Oct 2000 | Oct 2001 | Oct 2002 | Oct 2005 | Oct 2009 | Oct 2010 | Oct 2015 | Oct 2020 | other samples |
|---|---|---|---|---|---|---|---|---|---|
| eng.1 | 0 | 33 | 31 +1p | 40 +14p | – | 39 | 37 | 34 | Oct 2024 30; Dec 2024 62; CY2023 412; season 2023-24 380 |
| esp.1 | 40 (no goal details!) | 50 (5 false AET!) | 31 | 50 | – | 39 | 36 | 38 | – |
| ger.1 | – | 27 (1 false AET!) | 26 +7p | 36 +8p (2 details≠score!) | – | 36 | 34 | 33 | – |
| ita.1 | – | 27 +9p (2 false AET!) | 27 | 50 +1p | – | 40 | 42 | 31 | – |
| fra.1 | – | 27 (1 false AET!) | 30 | 39 +16p | – | 38 (1 no details) | 36 | 31 | – |
| uefa.champions | 0 | 56 (1 false AET: 21220; 3 no details, 1 ≠) | 47 +1p | 16 | – | 16 | 16 | 32 | Mar 2005 8 (ET labelled FULL_TIME: 169930; 2 ≠); May 2005 3; Mar 2015 8; Mar 2025 16; May 2025 3 |
| uefa.europa | – | – | – | 0 | 48 | 24 | 48 | 48 | Mar 2015 16 (ET labelled FULL_TIME: 416637); Apr 2025 8 |

openfootball (CC0) [DL repo trees; contents only where stated]:

| Competition | Season files present | Match contents counted | Gaps |
|---|---|---|---|
| EPL | txt 35 (1992-93..2026-27); football.json 17 (2010-11..) | 2022-23, 2023-24, 2024-25: 380 each; 2023-24 = ESPN 380/380 | other seasons not counted |
| La Liga | txt 15 (2012-13..2026-27) | none counted | nothing before 2012-13 |
| Bundesliga | txt 17 (2010-11..2026-27) | none counted | nothing before 2010-11 |
| Serie A | txt 14 (2013-14..2026-27) | none counted | nothing before 2013-14 |
| Ligue 1 | europe/france 13 (2014-15..2026-27) | none counted | nothing before 2014-15 |
| UCL | cl.txt 15 (2011-12..2025-26) | 125 × 12 seasons; 119 in 2019-20; 189 in 2024-25 and 2025-26 (each equals its header and the format count) | no qualifiers before 2024-25; no 2026-27 file at 18:26 UTC |
| UEL | el.txt 5 (2020-21..2024-25) | 205 / 141 / 141 / 141 / 189; 2021-22 has 2 "[cancelled]" lines | **none before 2020-21 or from 2025-26** |

openfootball known errors [DL]: football.json `p` [4,4] on both 2025 shoot-outs; empty 2025 UCL final score; Inter–Barcelona
2025-05-06 stored 4-3 without "a.e.t." in json and txt, so a.e.t. flags are incomplete (24 a.e.t. lines UCL and 22 UEL are a
lower bound). EPL kickoff times are local: openfootball minus ESPN UTC = +1 h for **184** and 0 h for **196** of the 380
2023-24 matches, split at the DST changes (consistent with UK local time; interpretation). UEFA times (18:45/21:00) look
like CET [UNV].

Other datasets: football-data.co.uk lists 34 season CSVs per league for E0/SP1/D1/I1/F1 (1993/94..2026/27) and no UEFA
files [DL index pages only; no CSV fetched]. StatsBomb open data covers few seasons of these competitions [DL
competitions.json]. engsoccerdata champs.csv: 6,556 rows 1955–2017, only the final for 2016 and 2017, no UEL [DL]. ClubElo:
HTTP 502 / "Fixtures API deactivated" [DL]. FiveThirtyEight's documented spi_matches.csv URL now returns an ABC News HTML
page [DL].

## B. Fields
| field | ESPN scoreboard | ESPN summary | openfootball | football-data.org v4 [DOC, keyed] |
|---|---|---|---|---|
| how it ended | status name **unreliable both ways**; `status.period` 3–4 = ET, 5 = shoot-out (agreed with the evidence on all sampled finals except the 10 false AET, which show period 2; 18 Oct 2005 finals show period 1); clock; event minutes | linescores (periods 3–4 ET, 5 shoot-out; corrupt in 2015, absent in 2005) | txt "a.e.t." / "pen." (incomplete) | `duration`, `extraTime`, `penalties` |
| 90-min score | = final only when status FULL_TIME **and** period ≤ 2 **and** no extra-time event minute; after ET derivable from goal minutes | keyEvents with `period.number` ≤ 2 | `score.ft` / txt "(90-min, HT)" (errors above) | `score.regularTime` |
| aggregate / leg | `aggregateScore`, `leg`, `series` | same | round name only | stage enums |
| kickoff / zone | ISO UTC | same | local, zone not stated | UTC [UNV] |
| stable ids | team id (285 ids stable across seasons and competitions; 1 club with two ids) and event id | same | names only | ids (keyed) |
| statistics | competitor `statistics` | boxscore team stats (2005 possession "0") | none | paid add-on |
| odds | null on 2,125/2,125 sampled finals | Bet365 `preMatch*` (2024-25 only, no quote time) | none | paid add-on |

ESPN `details` entries have no id or period [DL]; they carry team, clock, type, flags and athletesInvolved. Duplicates occur
in a few pre-2010 events (byte-identical in 169930, 169934, 182820, ger.1 21244; near-duplicates — same minute and team, not
byte-identical, sometimes the same athlete id and sometimes not — in ger.1 190019/190147). Two different goals by one team in the same minute exist (ita.1 63966: an own goal and
a goal at 86'), so de-duplication must keep entries that differ in type or player and require every team's goals to add up.

## C. Terms, licences and cost (cost and usage separated)
| Source | Cost | Usage (quoted clause) | Verdict for this project |
|---|---|---|---|
| openfootball (england, espana, deutschland, italy, europe, football.json, champions-league) | free, no key | CC0 1.0 (LICENSE.md, identical sha256 36ffd9dc… in all 7; fetched 19:31–20:11 UTC) §2: waives rights "for any purpose whatsoever, including without limitation commercial"; README: "Use as you please with no restrictions whatsoever." §4(c): the affirmer "disclaims responsibility for clearing rights of other persons" (upstream provenance not stated; interpretation) | **free and clearly permitted** |
| Wikipedia text (validation of individual facts) | free | CC BY-SA 4.0 §2(a)(1): no purpose limit; attribution and ShareAlike apply "If You Share"; WMF Terms of Use §7: "facts you contribute to the projects may be reused freely without attribution" (fetched 20:12 UTC). Automated access must follow the User-Agent Policy ("informative User-Agent string with contact information, or they may be blocked") and API:Etiquette (serial requests, maxlag) | **free; permitted for facts, with access conditions** (interpretation that goal minutes are facts) |
| football-data.org free tier | free ("Free Plan is Free Forever"; UCL + 5 leagues, not UEL; "Scores delayed"; 10 calls/min); account + key required | T&C (2018-06-01): 2.3 key "applies to a single Application"; 6.1 credentials not in open-source repositories; 7.1 attribution "Football data provided by the Football-Data.org API"; 9.1 no referencing of data after cancellation (interpretation: a cache cannot outlive the account); no AI/ML clause found. Free-tier season depth **[UNV]** | **free; no restricting clause found; needs the user's account decision** |
| ESPN site API | free, undocumented | Disney Terms of Use (Last Updated May 24, 2024; fetched 19:32 UTC): "personal, noncommercial use only … no right to … transform any Disney Product, including in connection with any … training, testing, benchmarking or validation of any artificial intelligence or machine learning tool…"; clause x: no access "using a robot, spider, script, or other automated means, including … data mining or web scraping or otherwise compiling … any collection of data". No ESPN API-specific terms found (devcenter terms 404) | **not clearly permitted** (explicit clauses) |
| football-data.co.uk | free | data.php: "private individuals only, NOT commerical or data training products using automated bots/scrapers/AI"; robots.txt `Disallow: /` for Anthropic-AI, Claude-Web, ClaudeBot | **not permitted; excluded** |
| StatsBomb / Hudl open data | free; registration requested (2.2) | User Agreement (8 Sept 2023; cached PDF sha256 a5462e69…, fetched 19:35:18 UTC; clauses re-read from it on 2026-11-07): 1.2.1 not "distribute, reproduce, sell or in any way provide the data to any external or third party"; 1.2.2 not "commercially exploit the data or any analysis derived…"; the data "is the property of StatsBomb" (§7) | **not clearly permitted**; also sparse |
| FBref / Sports Reference | free | Terms (May 19, 2023): no use for "training, fine-tuning, prompting, or instructing artificial intelligence models … or (ii) supporting machine learning methods used to predict, classify, label, or score" | **not permitted** |
| Transfermarkt (and datasets built from it) | free | §11.1: no access "using bots, spiders, screen scraping or other automated processes"; no use "for the training or development of artificial intelligence (AI), including … machine learning" | **not permitted** (derived CC0 datasets inherit the upstream question; interpretation) |
| OddsPortal | free | no "embedding, aggregating, scraping or recreating it without our express consent"; "for your personal use only" | **not permitted** |
| The Odds API | free 500 credits/month (live/upcoming only; historical "only available on paid usage plans") | permits "Using our data to train statistical and machine learning models"; bars reselling "as a standalone data product" | **free tier forward-only; needs an account**; no historical odds |
| Sportmonks | free plan covers only Danish Superliga and Scottish Premiership | "Reselling … without approval is not allowed" | **none of the 7 competitions free** |
| Betfair Historical Data | **unassessed**: site returned 403 with "accessing the Betfair website from a country that Betfair does not accept bets from or the traffic from your network was detected as being unusual", Region: CA (a geo-restriction: interpretation); FAQ: data "for purchase & download to registered Betfair customers", needs a Betfair.com account, from April 2015 | terms not read | **unassessed** (account) |
| API-Football, ClubElo, Understat | unknown (403 / 502 / maintenance page) | not read | **unassessed** |
| engsoccerdata | free | DESCRIPTION "GPL (>= 2)" vs README "Free to use for non-commerical use"; sources include espn.co.uk and football-data.co.uk | **not clearly licensed** (interpretation) |

## D. Team identity, promoted teams, cross-league
ESPN team ids are stable in the samples (Man Utd 360 in eng.1 2001→2024, UCL and UEL; Ipswich 373 in 2001-02 and 2024-25)
[DL]. openfootball has names only: EPL maps to ESPN names with one rule (strip " FC": 23/23, 20/20); UCL 2024-25 needs an
explicit table (10 of 16 sampled names). No in-scope free source has second divisions, so promoted clubs return with stale
strength. Cross-league strength links only through UCL/UEL matches; opponents from other leagues are cold-start teams.

## E. Open gaps
G1 raw bytes temporary (above). G2 football-data.org free-tier season depth unverified (account needed). G3 openfootball
match contents counted only for EPL 2022-23..2024-25, UCL and UEL; La Liga/Bundesliga/Serie A/Ligue 1 files not counted.
G4 ESPN month-bucket time zone and rate limits [UNV]. G5 no free, permitted historical odds verified; Betfair and API-Football
unassessed. G6 Understat and ClubElo unassessed. G7 the existing ESPN collection (soccer S0, NBA/NFL pipelines) does not meet
the "clearly permitted" criterion; it is flagged, not changed. G8 openfootball's update delay for current results is
unmeasured.

## F. S2 recommendation (proposal; not started)
1. **Results-only development on openfootball (CC0)**: EPL, Bundesliga, La Liga, Serie A, Ligue 1 (season files from EPL
   1992-93, Bundesliga 2010-11, La Liga 2012-13, Serie A 2013-14, Ligue 1 2014-15; contents to be counted per season at
   ingest) and UCL main stage 2011-12..2025-26;
   UEL only 2020-21..2024-25 (**blocked** before and after). Statistics and odds labelled unavailable.
2. **90-minute grading after extra time**: openfootball "(90-min, HT)" is graded only when an independent second source
   agrees (Wikipedia goal minutes, accessed with a descriptive User-Agent, if the user accepts it); otherwise ungradable.
   Because openfootball a.e.t. flags are incomplete and ESPN's status is unreliable, every UEFA knockout match needs this
   check; a draw is never assumed.
3. **Ingest validation**: row counts vs file header and format count; cancelled lines flagged; versioned team-name map;
   matches outside the format rejected and recorded.
4. **Questions for the user** (account decisions only): a free football-data.org account for current UCL + 5-league results
   (UEL not included; conditions 2.3/6.1/7.1/9.1); optionally a free The Odds API account for forward-only odds; Wikipedia as
   a validation source; whether to model UEL on 5 seasons. **Withdrawn**: football-data.co.uk in any form; any paid source.
