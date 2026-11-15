# Soccer goal and corner markets: plan, gates and data gaps (2026-11-09)

**Brief (user, 2026-11-09):** expand soccer beyond the 90-minute 1X2:
- match goals over/under;
- home and away team goals over/under;
- both teams to score;
- double chance and draw no bet;
- match corners over/under, team corners over/under, and corner handicaps.

It is built end to end in the existing backend, store, API client and app. Goals come first, then corners. Nothing is
purchased or deployed.

**Test decision (user, 2026-11-09):** asked "may I run the one-shot reserved test myself once the models are sealed?",
the user chose "Run it after sealing (Recommended)".

The machine-readable rules are `backend/app/soccer_markets_protocol.json`. Its sha256 and time were logged before any fit
(`~/nba-predictor-research-data/soccer-markets-eval-logs/experiments.jsonl`). This page summarises them.

## 1. Sources, and the exact gaps
| Need | Source used | Gap |
|---|---|---|
| Results 2010-11 to today | ESPN scoreboards: whole months, `soccer history-months` | none for the 7 club competitions |
| Corner counts | ESPN scoreboard `wonCorners`, every final back to 2010 (checked 2026-11-09) | no second source to cross-check. Counts include extra time, so knockout matches are excluded |
| Bookmaker prices | ESPN scoreboard `odds` (DraftKings): 3-way result, match goals total, goal handicap, each with side, line, open/current price | **forward only:** past matches carry no pregame prices |
| Corner prices | **none.** ESPN's DraftKings listing has no corner market, also not in its prop list (checked for EPL and La Liga matches of 2026-11-21) | corners are model lines only |
| BTTS, team goals, double chance prices | ESPN prop list `propBets` | **not used.** Its entries carry no side (two "Both Teams To Score" prices, two prices per team-total line, both labelled `over`), so the side cannot be read without guessing |
| National teams | — | goals and corners for national teams are out of scope: no goal/corner model is evaluated for them. Double chance and draw no bet are derived from their tested 1X2 |

ESPN's terms were flagged in S1. The user's decision of 2026-11-08 applies here: "forget the terms. Implement the goddamn thing."

## 2. Models (protocol `candidates`)
- **goals:** team-strength Poisson. Grid: half-life × ridge × promoted-team shift. The Dixon-Coles low-score adjustment is
  an alternative, chosen on validation only if clearly better.
- **corners:** the same structure fitted on corner counts. A negative-binomial dispersion is the alternative, chosen on
  validation only if clearly better.
- **baseline:** per-competition empirical count distributions from 2010-19.
- **Corner probabilities come only from corner counts.** The 1X2 Elo is never an input.
- **Double chance and draw no bet** are exact functions of the tested 1X2 forecast:
  - 1X = 1 − P(away);
  - DNB home = P(home) / (P(home) + P(away)), with a draw as a push.
- **Point in time:** a match dated D uses every label available by D 06:00 UTC from earlier dates, as the 1X2 does. The
  frozen model refits from scratch at each cutoff, so live forecasts equal the evaluated ones by construction.

## 3. Evaluation and gates
- Periods:
  - warm-up 2010-12;
  - development: tune 2013-17, fit 2018-19;
  - validation 2020-22;
  - reserved test 2023-25;
  - 2026 stays prospective.
- Goals: the 2023-25 seasons were read once before (the 1X2 Elo test of 2026-11-08), so this goals test is a **disclosed
  second use**. Corners: first use.
- Reported per family, market line, competition and season:
  - sample sizes;
  - joint log loss (primary) with week-block bootstrap intervals;
  - Brier, log loss, ECE and reliability per half line;
  - predicted vs observed totals;
  - paired comparison with the frequency baseline.
- Reading of the one-shot test, mechanical:
  - **R1:** the family's paired interval against the baseline lies below 0;
  - **R2:** every line of a market has ECE ≤ 0.05;
  - **R3:** per competition, n ≥ 300 and an interval below 0;
  - **R4:** descriptive only.
  - Served = R2 markets × R3 competitions, if R1 passes, within the live 1X2 launch scope. UEL stays input only.
- **No market has proven skill against the bookmaker.** No free historical prices exist. Prices are shown as information;
  nothing becomes a "best pick".

## 4. Settlement
- Basis: 90 minutes plus stoppage time.
- Whole lines push. Quarter lines split the stake: half win or half loss.
- Draw no bet pushes on a draw.
- Postponed, cancelled or abandoned matches are void.
- A match decided in extra time is unscorable: ESPN gives no 90-minute count. Knockout matches are never forecast.
- A corner count listed as 0-0 is unscorable (amendment 2).
- Ticket return per unit stake:
  - legs return d, (d+1)/2, 1, 1/2 or 0;
  - a ticket with a half result is graded `partial`.

## 5. Code
- **Migration** `0040_soccer_markets.sql`, all append-only:
  - `soccer_match_stats`;
  - `soccer_odds_quotes`;
  - `soccer_market_forecasts` (stores each forecast's whole joint grid);
  - `soccer_market_checks`.
- **Modules:**
  - `app/soccer_markets.py`: settlement and probabilities;
  - `app/soccer_markets_eval.py`: protocol, gates, development, test;
  - `app/soccer_markets_live.py`: live run, reads, grading.
- **CLI:**
  - `soccer history-months --from YYYYMM --to YYYYMM`;
  - `soccer markets-develop | markets-test | markets-forecast | markets-status`.
- **API:** `GET /api/soccer/markets`, `GET /api/soccer/markets/history`.
- **App:** the soccer Game day "Markets" panel (per match, plus a ranking across matches), and a markets history.
- **Tickets and Telegram:**
  - soccer selections beside the 1X2 pick;
  - settlement through `tickets.grade_leg`;
  - text and PNG through the existing renderers.

## 6. Activation (needs the user's go; nothing here is live)
1. Review this branch (`soccer-markets-20261109`), then merge it into the delivery release.
2. Migrate the live soccer store to 0040. This is an explicit release migration.
3. Import the history months the model window needs: `soccer history-months --from 202407 --to <last month>` for the
   seven club competitions, about 15 minutes.
4. Add `soccer markets-forecast` to `scripts/soccer_live_cycle.sh`.
5. Update the ticket reviewer skill. The proposed text is in the evidence folder; the installed skill still says 1X2
   only, and its hash is pinned in every snapshot.

## 7. Status (2026-11-10, 00:35 UTC). Evidence: `docs/evidence/soccer-markets-2026-11-09/README.md`
- **Amendment 1** (stage rules; before any fit): leagues use the regular season = the most frequent ESPN season type.
  UEFA uses its group / league-phase slugs (9 listed).
- **Amendment 2** (after the corners v1 development; the user's decision "Amend, redevelop, then test"):
  - ESPN lists historical 0-0 corner rows where counts are missing (EPL 2016-17: 363 of 380);
  - a both-zero count is "not delivered": no input, never scored, a leg on it is unscorable;
  - the live check still treats it as delivered;
  - the corners v1 seal `62c3b5e5` is retired untested.
- **Goals:** sealed `cfe78bf9` (Poisson).
  - Reserved test 2023-25, n 6,023: −0.127 [−0.140, −0.114] joint log loss vs frequency.
  - R1-R3 pass for every market and competition.
  - Served: EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL.
  - Weak spot: BTTS is no better than the base rate.
- **Corners:** sealed `f50224dd` (negative binomial).
  - Reserved test, n 5,937: −0.121 [−0.136, −0.106].
  - R1 and R2 pass; R3 passes for the six launch competitions (UEL fails; it is input only).
  - R4 disclosure: total over 7.5 is under-predicted by 3.4 pp; corner totals over 7.5 and 8.5 are not better than
    frequency.
- **Pinned** in `app/soccer_markets_live.py` (CANDIDATES), with byte copies of the seals and readings.
- **Real forecasts** were made on a scratch copy for the Nov 20 club matches. They equal the evaluated model exactly.
  Nothing is live.
